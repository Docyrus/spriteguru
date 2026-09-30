"""fal adapter: MiniMax H3 Max image-to-video through fal_client, and image generate/edit (Seedream 5.0,
FLUX.2 [max]) through fal's queue REST API.

Image calls send their inputs inline as data URIs and read SPRITEGURU_FAL_QUEUE_URL, so tests can point
them at a local fake (IM1-IM10 in docs/failure-modes.md)."""

from __future__ import annotations

import asyncio
import base64
import io

import httpx

from .. import keys, registry
from ..env import get as app_env
from .base import CallResult, Candidate, ProviderError, ProviderRequest, retryable_status

VIDEO_PARAMS = ("duration", "resolution", "prompt_expansion_mode", "enable_safety_checker")
IMAGE_PARAMS = ("safety_tolerance", "enable_safety_checker")


def _queue_url() -> str:
    return (app_env("FAL_QUEUE_URL") or "https://queue.fal.run").rstrip("/")


def _data_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def _raise_for(r: httpx.Response, what: str) -> None:
    """IM9: 429 and 5xx are retried by the hub; other client errors carry fal's detail and are final."""
    if r.status_code < 400:
        return
    try:
        detail = r.json().get("detail", r.text)
    except ValueError:
        detail = r.text
    raise ProviderError(f"fal {r.status_code} ({what}): {str(detail)[:400]}", retryable=retryable_status(r.status_code),
                        status=r.status_code)


class FalProvider:
    id = "fal"
    poll_s = 0.5

    def __init__(self) -> None:
        self._client = None

    def key(self) -> str:
        key = keys.get("fal")
        if not key:  # IM8: before anything is sent
            raise ProviderError("no fal key: run `spriteguru keys set fal` or set FAL_KEY")
        return key

    def client(self):
        if self._client is None:
            key = self.key()
            import fal_client

            self._client = fal_client.AsyncClient(key=key, default_timeout=600.0)
        return self._client

    async def run(self, req: ProviderRequest) -> CallResult:
        if req.op in ("generate", "edit"):
            return await self._image(req)
        if req.op != "video":
            raise ProviderError(f"fal adapter does not support op {req.op!r}")
        return await self._video(req)

    # -- images ---------------------------------------------------------------------

    async def _image(self, req: ProviderRequest) -> CallResult:
        m = registry.model(req.model)
        endpoint = m.get("endpoints", {}).get(req.op)
        if not endpoint:
            raise ProviderError(f"{req.model} has no fal endpoint for {req.op}")
        key = self.key()
        caps = m.get("image", {})
        W, H = req.size or (1024, 1024)
        ow, oh = registry.fal_image_size((W, H), caps)  # IM3
        base = {"prompt": req.prompt, "image_size": {"width": ow, "height": oh}, "output_format": "png"}
        if req.op == "edit":
            base["image_urls"] = [_data_uri(b) for b in req.images[: caps.get("max_inputs", 10)]]
        base.update({k: req.params[k] for k in IMAGE_PARAMS if k in req.params})
        per = max(1, int(caps.get("per_call", 1)))
        counts = [min(per, req.n - i) for i in range(0, req.n, per)]  # IM5: one call per image when per == 1
        calls = []
        for i, count in enumerate(counts):
            args = dict(base)
            if per > 1:
                args["num_images"] = count
            if caps.get("seed") and req.seed is not None:
                args["seed"] = int(req.seed) + i
            calls.append(self._queue(endpoint, args, key))
        results = await asyncio.gather(*calls)
        cands, rids = [], []
        async with httpx.AsyncClient(timeout=120) as http:
            for rid, res in results:
                rids.append(rid)
                if any(res.get("has_nsfw_concepts") or []):  # IM7
                    raise ProviderError(f"{m.get('label', req.model)} flagged the output as unsafe ({req.purpose or req.op}); "
                                        "change the description or pick another model", retryable=False)
                for img in res.get("images") or []:
                    cands.append(Candidate(await self._fetch_png(http, img["url"], (W, H)), "image/png",
                                           {"fal_request_id": rid, "fal_size": [ow, oh], "fal_endpoint": endpoint}))
        if not cands:
            raise ProviderError(f"fal returned no images from {endpoint}", retryable=True)
        return CallResult(cands[: req.n], request_id=",".join(rids))

    async def _queue(self, endpoint: str, args: dict, key: str) -> tuple[str, dict]:
        headers = {"Authorization": f"Key {key}"}
        base = _queue_url()
        try:
            async with httpx.AsyncClient(timeout=60) as http:
                r = await http.post(f"{base}/{endpoint}", json=args, headers=headers)
                _raise_for(r, "submit")
                sub = r.json()
                rid = sub["request_id"]
                status_url = sub.get("status_url") or f"{base}/{endpoint}/requests/{rid}/status"
                response_url = sub.get("response_url") or f"{base}/{endpoint}/requests/{rid}"
                delay = self.poll_s
                while True:
                    r = await http.get(status_url, headers=headers)
                    _raise_for(r, "status")
                    status = r.json().get("status")
                    if status == "COMPLETED":
                        break
                    if status not in ("IN_QUEUE", "IN_PROGRESS"):
                        raise ProviderError(f"fal request {rid} ended {status}", retryable=True)
                    await asyncio.sleep(delay)
                    delay = min(2.0, delay * 1.5)
                r = await http.get(response_url, headers=headers)
                _raise_for(r, "result")
                return rid, r.json()
        except httpx.HTTPError as e:
            raise ProviderError(f"fal connection error: {e}", retryable=True) from e

    @staticmethod
    async def _fetch_png(http: httpx.AsyncClient, url: str, size: tuple[int, int]) -> bytes:
        """The output as a PNG at exactly `size` (IM3)."""
        from PIL import Image

        if url.startswith("data:"):
            data = base64.b64decode(url.split(",", 1)[1])
        else:
            r = await http.get(url)
            _raise_for(r, "download")
            data = r.content
        img = Image.open(io.BytesIO(data)).convert("RGB")
        if img.size != tuple(size):
            img = img.resize(tuple(size), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    # -- video ----------------------------------------------------------------------

    async def _video(self, req: ProviderRequest) -> CallResult:
        import fal_client

        c = self.client()
        try:
            start = await c.upload(req.images[0], "image/png", "start.png")
            end = await c.upload(req.images[1], "image/png", "end.png") if len(req.images) > 1 else start
            args = {"prompt": req.prompt, "image_url": start, "end_image_url": end}
            args.update({k: req.params[k] for k in VIDEO_PARAMS if k in req.params})
            args.setdefault("prompt_expansion_mode", "disabled")
            if req.seed is not None:
                args["seed"] = int(req.seed)
            request_ids: list[str] = []
            result = await c.subscribe(req.model, arguments=args, with_logs=False,
                                       on_enqueue=request_ids.append, client_timeout=600)
        except fal_client.FalClientHTTPError as e:
            raise ProviderError(f"fal {e.status_code}: {e.message}", retryable=retryable_status(e.status_code),
                                status=e.status_code) from e
        except fal_client.FalClientTimeoutError as e:
            raise ProviderError(f"fal timeout: {e}", retryable=True) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"fal connection error: {e}", retryable=True) from e
        url = (result or {}).get("video", {}).get("url")
        if not url:
            raise ProviderError(f"fal returned no video: {str(result)[:300]}", retryable=True)
        async with httpx.AsyncClient(timeout=120) as http:
            r = await http.get(url)
            r.raise_for_status()
        return CallResult([Candidate(r.content, "video/mp4", {"video_url": url})],
                          request_id=request_ids[0] if request_ids else None)
