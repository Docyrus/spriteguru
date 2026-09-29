"""Provider interface (4.2): one request type in, a list of candidates out."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

OPS = ("generate", "edit", "video", "svg_generate", "svg_animate", "respond", "rd_animate", "rd_fix")


@dataclass
class ProviderRequest:
    op: str
    model: str
    prompt: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    images: list[bytes] = field(default_factory=list)  # for edit, images[0] is the guide canvas
    mask: bytes | None = None
    seed: int | None = None
    n: int = 1
    size: tuple[int, int] | None = None
    schema: dict[str, Any] | None = None  # JSON schema for structured output (respond)
    instructions: str | None = None
    purpose: str = ""  # human label only; not part of the cache key

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError(f"unknown op {self.op!r}")

    def cache_key(self, provider: str) -> str:
        body = {
            "provider": provider,
            "model": self.model,
            "op": self.op,
            "params": self.params,
            "prompt": self.prompt,
            "instructions": self.instructions,
            "schema": self.schema,
            "images": [hashlib.sha256(b).hexdigest() for b in self.images],
            "mask": hashlib.sha256(self.mask).hexdigest() if self.mask else None,
            "seed": self.seed,
            "n": self.n,
            "size": list(self.size) if self.size else None,
        }
        canon = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canon.encode()).hexdigest()


@dataclass
class Candidate:
    data: bytes
    media_type: str  # image/png, video/mp4, image/svg+xml, application/json
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ext(self) -> str:
        return {"image/png": "png", "video/mp4": "mp4", "image/svg+xml": "svg",
                "application/json": "json", "image/gif": "gif", "image/webp": "webp"}.get(self.media_type, "bin")

    def json(self) -> Any:
        return json.loads(self.data)


@dataclass
class CallResult:
    candidates: list[Candidate]
    request_id: str | None = None
    usage: dict[str, Any] | None = None
    cost: float | None = None  # provider-reported cost in USD, when the API returns one


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None,
                 code: str | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.code = code
        self.retry_after = retry_after


class BudgetExceeded(RuntimeError):
    def __init__(self, message: str, *, estimate: float, scope: str):
        super().__init__(message)
        self.estimate = estimate
        self.scope = scope


class ReplayMiss(RuntimeError):
    pass


class Provider(Protocol):
    id: str

    async def run(self, req: ProviderRequest) -> CallResult: ...


def retryable_status(status: int | None) -> bool:
    return status is not None and (status == 429 or status >= 500)
