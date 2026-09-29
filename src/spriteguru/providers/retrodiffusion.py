"""Retro Diffusion adapter: POST /v2/inferences, then poll the task (advanced animations, Pixel Fixer)."""

from __future__ import annotations

import asyncio
import base64

import httpx

from .. import keys
from .base import CallResult, Candidate, ProviderError, ProviderRequest, retryable_status

BASE = "https://api.retrodiffusion.ai/v2"
FRAME_COUNTS = (4, 6, 8, 10, 12, 16)


def _err(r: httpx.Response) -> ProviderError:
    try:
        body = r.json()
        err = body.get("error") or body.get("detail") or body
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        code = err.get("code") if isinstance(err, dict) else None
    except Exception:
        msg, code = r.text[:300], None
    ra = r.headers.get("retry-after")
    return ProviderError(f"Retro Diffusion {r.status_code}: {msg}", retryable=retryable_status(r.status_code),
                         status=r.status_code, code=code, retry_after=float(ra) if ra and ra.isdigit() else None)


class RetroDiffusionProvider:
    id = "retrodiffusion"

    def _headers(self) -> dict[str, str]:
        key = keys.get("retrodiffusion")
        if not key:
            raise ProviderError("no Retro Diffusion key: run `spriteguru keys set retrodiffusion` or set RD_API_KEY")
        return {"X-RD-Token": key, "Content-Type": "application/json"}

    async def check_cost(self, payload: dict) -> float:
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.post(f"{BASE}/inferences", headers=self._headers(), json={**payload, "check_cost": True})
            if r.status_code >= 400:
                raise _err(r)
            return float(r.json().get("balance_cost", 0.0))

    async def run(self, req: ProviderRequest) -> CallResult:
        if req.op == "rd_fix":
            return await self._pixel_fixer(req)
        if req.op != "rd_animate":
            raise ProviderError(f"Retro Diffusion adapter does not support op {req.op!r}")
        frames = int(req.params.get("frames_duration", 8))
        if frames not in FRAME_COUNTS:
            raise ProviderError(f"frames_duration must be one of {FRAME_COUNTS}, got {frames}")
        w, h = req.size or (64, 64)
        if not (32 <= w <= 256 and 32 <= h <= 256):
            raise ProviderError(f"advanced animation frames must be 32–256 px, got {w}x{h}")
        payload = {"prompt": req.prompt, "prompt_style": req.params["prompt_style"], "width": w, "height": h,
                   "num_images": 1, "frames_duration": frames,
                   "input_image": base64.b64encode(req.images[0]).decode(),
                   "return_spritesheet": bool(req.params.get("return_spritesheet", True))}
        if req.params.get("input_palette"):
            payload["input_palette"] = req.params["input_palette"]
        if req.seed is not None:
            payload["seed"] = int(req.seed)
        cost = await self.check_cost(payload)
        try:
            return await self._submit_and_poll(payload, cost)
        except ProviderError as e:
            if e.retryable:
                raise
            # failures are refunded, so one automatic retry is free (4.2)
            return await self._submit_and_poll(payload, cost)

    async def _submit_and_poll(self, payload: dict, cost: float) -> CallResult:
        async with httpx.AsyncClient(timeout=120) as http:
            try:
                r = await http.post(f"{BASE}/inferences", headers=self._headers(), json=payload)
            except httpx.HTTPError as e:
                raise ProviderError(f"Retro Diffusion connection error: {e}", retryable=True) from e
            if r.status_code >= 400:
                raise _err(r)
            body = r.json()
            if body.get("base64_images"):
                result = body
            else:
                task_id = body.get("task_id")
                if not task_id:
                    raise ProviderError(f"Retro Diffusion returned no task: {str(body)[:300]}")
                result = await self._poll(http, task_id)
        imgs = result.get("base64_images") or []
        if not imgs:
            raise ProviderError("Retro Diffusion returned no images", retryable=True)
        cands = [Candidate(base64.b64decode(b), "image/png", {}) for b in imgs]
        actual = result.get("balance_cost")
        return CallResult(cands, request_id=result.get("request_id"),
                          cost=float(actual) if actual is not None else cost)

    async def _poll(self, http: httpx.AsyncClient, task_id: str) -> dict:
        delay = 2.0
        for _ in range(300):
            await asyncio.sleep(delay)
            delay = min(delay * 1.3, 8.0)
            try:
                r = await http.get(f"{BASE}/inferences/tasks/{task_id}", headers=self._headers())
            except httpx.HTTPError:
                continue
            if r.status_code >= 400:
                raise _err(r)
            task = r.json()
            status = task.get("status")
            if status == "succeeded":
                return {**(task.get("result") or {}), "request_id": task_id}
            if status == "failed":
                err = task.get("error") or {}
                raise ProviderError(f"Retro Diffusion task failed: {err.get('message', err)}", retryable=False,
                                    code=err.get("code"))
        raise ProviderError(f"Retro Diffusion task {task_id} did not finish", retryable=False)

    async def _pixel_fixer(self, req: ProviderRequest) -> CallResult:
        engine = req.params.get("engine", "standard")
        payload = {"input_image": base64.b64encode(req.images[0]).decode()}
        async with httpx.AsyncClient(timeout=120) as http:
            r = await http.post(f"{BASE}/pixel-fixer/{engine}", headers=self._headers(), json=payload)
        if r.status_code >= 400:
            raise _err(r)
        imgs = r.json().get("base64_images") or []
        return CallResult([Candidate(base64.b64decode(b), "image/png", {}) for b in imgs], cost=0.0)
