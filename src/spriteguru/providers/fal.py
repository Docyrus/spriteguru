"""fal adapter: upload inputs, then subscribe to the queue (MiniMax H3 Max image-to-video)."""

from __future__ import annotations

import httpx

from .. import keys
from .base import CallResult, Candidate, ProviderError, ProviderRequest, retryable_status

VIDEO_PARAMS = ("duration", "resolution", "prompt_expansion_mode", "enable_safety_checker")


class FalProvider:
    id = "fal"

    def __init__(self) -> None:
        self._client = None

    def client(self):
        if self._client is None:
            key = keys.get("fal")
            if not key:
                raise ProviderError("no fal key: run `spriteguru keys set fal` or set FAL_KEY")
            import fal_client

            self._client = fal_client.AsyncClient(key=key, default_timeout=600.0)
        return self._client

    async def run(self, req: ProviderRequest) -> CallResult:
        if req.op != "video":
            raise ProviderError(f"fal adapter does not support op {req.op!r}")
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
