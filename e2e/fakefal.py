"""An in-process fake of fal's queue REST API for the image-model scenarios (IM1-IM10).

Submissions are recorded (inline images replaced by their sizes) and answered with the offline
simulator's art, resized to the `image_size` the request asked for, so the engine's resize back to
the canvas runs for real. Knobs on the object inject failures: `fail_next` (status, detail) pairs for
the next submits, `nsfw_next` flagged results, and `floor_next` images drawn the way live FLUX.2 [max]
broke a sheet (a full-width floor line under each row of figures, plus single-pixel specks; R14)."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from conftest import free_port


def _decode(uri: str) -> bytes:
    return base64.b64decode(uri.split(",", 1)[1])


def _floor_lines(img: Image.Image) -> Image.Image:
    """A grey band across the whole canvas under each row of figures, and a few 1 px specks."""
    import numpy as np

    a = np.asarray(img).copy()
    key = a[2, 2].astype(int)
    fg = np.abs(a.astype(int) - key).sum(-1) > 60
    rows = fg.any(1)
    y, H, W = 0, a.shape[0], a.shape[1]
    while y < H:
        if rows[y]:
            end = y
            while end + 1 < H and rows[end + 1]:
                end += 1
            if end - y > 40:
                a[max(0, end - 4): min(H, end + 8), :] = (200, 198, 190)
            y = end + 1
        else:
            y += 1
    for i in range(6):
        sy, sx = (37 + 131 * i) % H, (211 + 397 * i) % W
        if not fg[sy, sx]:
            a[sy, sx] = (255 - key).clip(0, 255)
    return Image.fromarray(a)


class FakeFal:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.fail_next: list[tuple[int, str]] = []
        self.nsfw_next = 0
        self.floor_next = 0
        self._results: dict[str, dict] = {}
        self._files: dict[str, bytes] = {}
        self._polls: dict[str, int] = {}
        self._lock = threading.Lock()
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _send(self, status: int, body, ctype: str = "application/json") -> None:
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                args = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                fake._send_submit(self, self.path.lstrip("/"), args, self.headers.get("Authorization", ""))

            def do_GET(self):
                fake._send_get(self, self.path)

        self._handler = Handler
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeFal":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    def for_endpoint(self, fragment: str) -> list[dict]:
        return [r for r in self.requests if fragment in r["endpoint"]]

    # -- routes ------------------------------------------------------------------------

    def _send_submit(self, h, endpoint: str, args: dict, auth: str) -> None:
        images = [Image.open(io.BytesIO(_decode(u))) for u in args.get("image_urls", [])]
        record = {"endpoint": endpoint, "auth": auth.startswith("Key "), "keys": sorted(args),
                  "image_size": args.get("image_size"), "num_images": args.get("num_images"),
                  "seed": args.get("seed"), "output_format": args.get("output_format"),
                  "image_urls": [{"data_uri": u.startswith("data:image/png;base64,"), "size": list(im.size)}
                                 for u, im in zip(args.get("image_urls", []), images)],
                  "prompt": args.get("prompt", "")}
        with self._lock:
            self.requests.append(record)
            fail = self.fail_next.pop(0) if self.fail_next else None
            nsfw = self.nsfw_next > 0
            if nsfw:
                self.nsfw_next -= 1
        if fail:
            h._send(fail[0], {"detail": fail[1]})
            return
        rid = uuid.uuid4().hex
        app = "/".join(endpoint.split("/")[:2])  # fal's queue URLs use the app id, not the full endpoint
        result = self._render(endpoint, args, images, nsfw)
        with self._lock:
            self._results[rid] = result
            self._polls[rid] = 0
        h._send(200, {"request_id": rid, "status_url": f"{self.url}/{app}/requests/{rid}/status",
                      "response_url": f"{self.url}/{app}/requests/{rid}"})

    def _send_get(self, h, path: str) -> None:
        m = re.match(r"^/files/([\w.-]+)$", path)
        if m and m.group(1) in self._files:
            h._send(200, self._files[m.group(1)], "image/png")
            return
        m = re.match(r"^/(.+)/requests/(\w+)/status$", path)
        if m and m.group(2) in self._results:
            with self._lock:
                self._polls[m.group(2)] += 1
                done = self._polls[m.group(2)] > 1  # one IN_QUEUE answer first, so the engine polls
            h._send(200, {"status": "COMPLETED" if done else "IN_QUEUE"})
            return
        m = re.match(r"^/(.+)/requests/(\w+)$", path)
        if m and m.group(2) in self._results:
            h._send(200, self._results[m.group(2)])
            return
        h._send(404, {"detail": f"no route {path}"})

    # -- art -----------------------------------------------------------------------------

    def _render(self, endpoint: str, args: dict, images: list[Image.Image], nsfw: bool) -> dict:
        from spriteguru.providers.base import ProviderRequest
        from spriteguru.providers.synthetic import SyntheticProvider

        size = args.get("image_size") or {"width": 1024, "height": 1024}
        W, H = int(size["width"]), int(size["height"])
        n = int(args.get("num_images") or 1)
        pngs = []
        for im in images:
            buf = io.BytesIO()
            im.save(buf, "PNG")
            pngs.append(buf.getvalue())
        op = "edit" if pngs else "generate"
        # the simulator draws at the guide's size (or the requested one for a text-only request); the model's
        # own size is then imposed, as a real model draws at image_size
        req = ProviderRequest(op=op, model="fake-fal", prompt=args.get("prompt", ""), images=pngs, n=n,
                              size=images[0].size if images else (W, H), seed=args.get("seed"))
        res = asyncio.run(SyntheticProvider().run(req))
        out = []
        for cand in res.candidates[:n]:
            img = Image.open(io.BytesIO(cand.data)).convert("RGB")
            with self._lock:
                floor = self.floor_next > 0
                if floor:
                    self.floor_next -= 1
            if floor:
                img = _floor_lines(img)
            if img.size != (W, H):
                img = img.resize((W, H), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            name = f"{uuid.uuid4().hex}.png"
            self._files[name] = buf.getvalue()
            out.append({"url": f"{self.url}/files/{name}", "width": W, "height": H, "content_type": "image/png"})
        return {"images": out, "has_nsfw_concepts": [nsfw] * len(out), "seed": args.get("seed") or 0}
