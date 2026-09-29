"""OpenAI adapter: images.generate / images.edit (GPT Image 2.5) and responses.create (GPT-6)."""

from __future__ import annotations

import base64
import json
from typing import Any

from .. import keys
from .base import CallResult, Candidate, ProviderError, ProviderRequest, retryable_status

IMAGE_PARAMS = ("quality", "background", "output_format", "input_fidelity", "moderation")


class OpenAIProvider:
    id = "openai"

    def __init__(self) -> None:
        self._client = None

    def client(self):
        if self._client is None:
            key = keys.get("openai")
            if not key:
                raise ProviderError("no OpenAI key: run `spriteguru keys set openai` or set OPENAI_API_KEY")
            from openai import AsyncOpenAI

            # retries are handled by the hub so the ledger sees every attempt's outcome
            self._client = AsyncOpenAI(api_key=key, max_retries=0, timeout=180.0)
        return self._client

    async def run(self, req: ProviderRequest) -> CallResult:
        import openai

        try:
            if req.op in ("generate", "edit"):
                return await self._image(req)
            if req.op == "respond":
                return await self._respond(req)
        except openai.APIStatusError as e:
            body = getattr(e, "body", None) or {}
            err = body.get("error", body) if isinstance(body, dict) else {}
            etype = err.get("type") if isinstance(err, dict) else None
            code = err.get("code") if isinstance(err, dict) else None
            retry = retryable_status(e.status_code) and etype != "image_generation_user_error"
            ra = e.response.headers.get("retry-after") if e.response is not None else None
            raise ProviderError(f"OpenAI {e.status_code}: {e.message}", retryable=retry,
                                status=e.status_code, code=code,
                                retry_after=float(ra) if ra and ra.replace('.', '', 1).isdigit() else None) from e
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise ProviderError(f"OpenAI connection error: {e}", retryable=True) from e
        raise ProviderError(f"OpenAI adapter does not support op {req.op!r}")

    async def _image(self, req: ProviderRequest) -> CallResult:
        c = self.client()
        kwargs: dict[str, Any] = {"model": req.model, "prompt": req.prompt, "n": req.n}
        if req.size:
            kwargs["size"] = f"{req.size[0]}x{req.size[1]}"
        extra = {}
        for k in IMAGE_PARAMS:
            if k in req.params:
                if req.op == "edit" and k == "moderation":
                    continue
                if k == "input_fidelity":
                    extra[k] = req.params[k]
                else:
                    kwargs[k] = req.params[k]
        if extra:
            kwargs["extra_body"] = extra
        if req.op == "generate":
            resp = await c.images.generate(**kwargs)
        else:
            files = [(f"image_{i}.png", b, "image/png") for i, b in enumerate(req.images)]
            kwargs["image"] = files if len(files) > 1 else files[0]
            if req.mask is not None:
                kwargs["mask"] = ("mask.png", req.mask, "image/png")
            resp = await c.images.edit(**kwargs)
        cands = []
        for item in resp.data or []:
            if not getattr(item, "b64_json", None):
                continue
            cands.append(Candidate(base64.b64decode(item.b64_json), "image/png", {}))
        if not cands:
            raise ProviderError("OpenAI returned no images", retryable=True)
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else None
        rid = getattr(resp, "_request_id", None)
        return CallResult(cands, request_id=rid, usage=usage)

    async def _respond(self, req: ProviderRequest) -> CallResult:
        c = self.client()
        content: list[dict[str, Any]] = [{"type": "input_text", "text": req.prompt}]
        for b in req.images:
            content.append({"type": "input_image", "detail": "high",
                            "image_url": "data:image/png;base64," + base64.b64encode(b).decode()})
        kwargs: dict[str, Any] = {"model": req.model, "input": [{"role": "user", "content": content}],
                                  "store": False,
                                  "max_output_tokens": int(req.params.get("max_output_tokens", 32000))}
        if req.instructions:
            kwargs["instructions"] = req.instructions
        effort = req.params.get("reasoning_effort")
        if effort:
            kwargs["reasoning"] = {"effort": effort}
        if req.schema is not None:
            kwargs["text"] = {"format": {"type": "json_schema",
                                         "name": req.params.get("schema_name", "result"),
                                         "schema": req.schema, "strict": True}}
        resp = await c.responses.create(**kwargs)
        if getattr(resp, "status", "completed") != "completed":
            detail = getattr(resp, "incomplete_details", None)
            raise ProviderError(f"OpenAI response {resp.status}: {detail}", retryable=False)
        text = resp.output_text or ""
        for item in getattr(resp, "output", []) or []:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", "") == "refusal":
                    raise ProviderError(f"model refused: {part.refusal}", retryable=False)
        if req.schema is not None:
            try:
                json.loads(text)
            except json.JSONDecodeError as e:
                raise ProviderError(f"structured output was not JSON: {text[:200]}", retryable=True) from e
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else None
        return CallResult([Candidate(text.encode(), "application/json" if req.schema else "text/plain", {})],
                          request_id=getattr(resp, "id", None), usage=usage)
