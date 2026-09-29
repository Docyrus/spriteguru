"""Quiver adapter: SVG generation and animation with Arrow 2, streamed over SSE."""

from __future__ import annotations

import base64
import json

import httpx

from .. import keys
from .base import CallResult, Candidate, ProviderError, ProviderRequest, retryable_status

BASE = "https://api.quiver.ai/v1"


class QuiverProvider:
    id = "quiver"

    def _headers(self) -> dict[str, str]:
        key = keys.get("quiver")
        if not key:
            raise ProviderError("no Quiver key: run `spriteguru keys set quiver` or set QUIVERAI_API_KEY")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async def run(self, req: ProviderRequest) -> CallResult:
        if req.op == "svg_generate":
            body = {"model": req.model, "prompt": req.prompt, "n": req.n, "stream": True,
                    "reasoning_effort": req.params.get("reasoning_effort", "high")}
            if req.instructions:
                body["instructions"] = req.instructions
            if req.images:
                body["references"] = [{"base64": base64.b64encode(b).decode()} for b in req.images]
            if req.size:
                body["attributes"] = {"viewBox": {"minX": 0, "minY": 0, "width": req.size[0], "height": req.size[1]}}
            path = "/svgs/generations"
        elif req.op == "svg_animate":
            svg = req.params["svg"]
            body = {"model": req.model, "prompt": req.prompt, "stream": True,
                    "reasoning_effort": req.params.get("reasoning_effort", "high"),
                    "svg_source": {"base64": base64.b64encode(svg.encode()).decode()}}
            path = "/svgs/animations"
        else:
            raise ProviderError(f"Quiver adapter does not support op {req.op!r}")
        # Streamed (SSE) so long reasoning keeps the connection alive; only the final `content`
        # events count, `data: [DONE]` merely ends the transport.
        timeout = httpx.Timeout(connect=30, read=300, write=60, pool=30)
        contents, usage, request_id, error = [], None, None, None
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                async with http.stream("POST", BASE + path, headers={**self._headers(), "Accept": "text/event-stream"},
                                       json=body) as r:
                    request_id = r.headers.get("x-request-id")
                    if r.status_code >= 400:
                        raw = await r.aread()
                        try:
                            err = json.loads(raw)
                        except Exception:
                            err = {"message": raw[:300].decode(errors="replace")}
                        raise ProviderError(f"Quiver {r.status_code}: {err.get('message', err)}",
                                            retryable=retryable_status(r.status_code), status=r.status_code,
                                            code=err.get("code"), retry_after=err.get("retry_after"))
                    event, data_lines = None, []
                    async for line in r.aiter_lines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].strip())
                        elif line == "":
                            if data_lines:
                                raw = "\n".join(data_lines)
                                data_lines = []
                                if raw == "[DONE]":
                                    break
                                try:
                                    d = json.loads(raw)
                                except json.JSONDecodeError:
                                    continue
                                kind = event or d.get("type")
                                if kind == "content" and d.get("svg"):
                                    contents.append(d)
                                elif kind == "error":
                                    error = d
                                if d.get("usage"):
                                    usage = d["usage"]
                            event = None
        except httpx.HTTPError as e:
            raise ProviderError(f"Quiver connection error: {e}", retryable=True) from e
        if error and not contents:
            code = error.get("code")
            raise ProviderError(f"Quiver stream error: {error.get('message', error)}",
                                retryable=code in ("rate_limit_exceeded", "server_error", "model_unavailable",
                                                   "request_timeout", "model_error"), code=code)
        cands = []
        for item in sorted(contents, key=lambda d: d.get("index", 0)):
            meta = {k: item.get(k) for k in ("loop_period_ms", "opening_animation_ms", "svg_score") if k in item}
            cands.append(Candidate(item["svg"].encode(), "image/svg+xml", meta))
        if not cands:
            raise ProviderError("Quiver returned no SVG", retryable=True)
        return CallResult(cands, request_id=request_id, usage=usage)
