"""Every provider call goes through the hub: cache, ledger, budget caps, semaphores, retries."""

from __future__ import annotations

import asyncio
import os
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .. import registry
from ..cache import Cache
from ..env import get as app_env
from ..ledger import Ledger
from .base import BudgetExceeded, Candidate, Provider, ProviderError, ProviderRequest, ReplayMiss


@dataclass
class ApprovalRequest:
    job: str | None
    model: str
    op: str
    estimate: float
    session_spend: float
    job_spend: float
    session_cap: float
    job_cap: float
    reason: str


Approver = Callable[[ApprovalRequest], Awaitable[bool]]
EventSink = Callable[[dict[str, Any]], None]

TOTAL_TIMEOUT_S = 600.0
BACKOFF_BASE = float(app_env("BACKOFF_BASE", "2.0"))


def _png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None


class ProviderHub:
    def __init__(self, cache: Cache, ledger: Ledger, *, mode: str = "live", replay: bool = False,
                 session_cap: float = 10.0, job_cap: float = 3.0, approve: Approver | None = None,
                 events: EventSink | None = None, providers: dict[str, Provider] | None = None):
        self.cache = cache
        self.ledger = ledger
        self.mode = mode
        self.replay = replay
        self.session_cap = session_cap
        self.job_cap = job_cap
        self.approve = approve
        self.events = events
        self._providers = providers or {}
        self._sems: dict[tuple[int, str], asyncio.Semaphore] = {}
        self._approved_jobs: dict[str, float] = {}  # job -> spend it was approved up to

    # -- provider resolution -------------------------------------------------

    def provider_id(self, model_id: str) -> str:
        return "synthetic" if self.mode == "synthetic" else registry.model(model_id)["provider"]

    def provider(self, pid: str) -> Provider:
        if pid not in self._providers:
            self._providers[pid] = _build(pid)
        return self._providers[pid]

    def _sem(self, pid: str) -> asyncio.Semaphore:
        key = (id(asyncio.get_running_loop()), pid)
        if key not in self._sems:
            conc = registry.provider_config(pid).get("concurrency", 4)
            self._sems[key] = asyncio.Semaphore(conc)
        return self._sems[key]

    # -- cost ------------------------------------------------------------------

    def estimate(self, req: ProviderRequest) -> float:
        if self.mode == "synthetic":
            return 0.0
        sizes = [s for s in (_png_size(b) for b in req.images) if s]
        return registry.estimate_cost(req.model, req.op, {**req.params}, prompt=req.prompt,
                                      input_sizes=sizes, n=req.n, size=req.size)

    async def _check_budget(self, req: ProviderRequest, est: float, job: str | None) -> None:
        session = self.ledger.session_spend()
        job_spent = self.ledger.job_spend(job)
        reasons = []
        if session + est > self.session_cap:
            reasons.append(f"session cap ${self.session_cap:.2f} (spent ${session:.2f})")
        if job and job_spent + est > max(self.job_cap, self._approved_jobs.get(job, 0.0)):
            reasons.append(f"job cap ${self.job_cap:.2f} (spent ${job_spent:.2f})")
        if req.op == "video" and est > 0:
            reasons.append("video jobs always ask")
        if not reasons:
            return
        ask = ApprovalRequest(job, req.model, req.op, est, session, job_spent, self.session_cap,
                              self.job_cap, "; ".join(reasons))
        if self.approve is not None and await self.approve(ask):
            if job:
                # an approval covers this call plus one more cap's worth; beyond that, ask again
                self._approved_jobs[job] = job_spent + est + self.job_cap
            return
        raise BudgetExceeded(f"call to {req.model} (~${est:.3f}) needs approval: {ask.reason}",
                             estimate=est, scope="session" if "session" in ask.reason else "job")

    # -- the call ------------------------------------------------------------------

    async def call(self, req: ProviderRequest, *, job: str | None = None) -> list[Candidate]:
        pid = self.provider_id(req.model)
        key = req.cache_key(pid)
        base = {"job": job, "provider": pid, "model": req.model, "op": req.op, "digest": key[:16],
                "n": req.n, "purpose": req.purpose}
        hit = self.cache.get(key)
        if hit is not None:
            self.ledger.append({**base, "status": "cache_hit", "cost": 0.0})
            for c in hit:
                c.meta.update(cached=True, cache_key=key)
            self._emit({"type": "spend", "job": job, "model": req.model, "cost": 0.0, "cached": True})
            return hit
        if self.replay:
            raise ReplayMiss(f"replay mode: no cached output for {req.model} {req.op} ({key[:12]})")

        est = self.estimate(req)
        await self._check_budget(req, est, job)

        call_id = uuid.uuid4().hex
        self.ledger.append({**base, "status": "sent", "call_id": call_id, "est_cost": round(est, 5)})
        t0 = time.monotonic()
        try:
            async with self._sem(pid):
                result = await self._with_retries(pid, req)
        except Exception as e:
            self.ledger.append({**base, "status": "error", "call_id": call_id, "cost": 0.0,
                                "latency_ms": round((time.monotonic() - t0) * 1000),
                                "error": f"{type(e).__name__}: {e}"[:500]})
            raise
        latency = round((time.monotonic() - t0) * 1000)
        cost = result.cost if result.cost is not None else registry.actual_cost(req.model, result.usage, est)
        if self.mode == "synthetic":
            cost = 0.0
        self.ledger.append({**base, "status": "ok", "call_id": call_id, "cost": round(cost, 5),
                            "est_cost": round(est, 5), "latency_ms": latency,
                            "request_id": result.request_id})
        per = cost / max(1, len(result.candidates))
        for c in result.candidates:
            c.meta.update(provider=pid, model=req.model, request_id=result.request_id,
                          cost=round(per, 5), cache_key=key, cached=False)
        self.cache.put(key, result.candidates, {"provider": pid, "model": req.model, "op": req.op,
                                                "params": req.params, "prompt": req.prompt,
                                                "seed": req.seed, "usage": result.usage,
                                                "cost": cost, "request_id": result.request_id})
        self._emit({"type": "spend", "job": job, "model": req.model, "cost": cost, "cached": False})
        return result.candidates

    async def _with_retries(self, pid: str, req: ProviderRequest):
        prov = self.provider(pid)
        timeout = registry.provider_config(pid).get("timeout_s", 180)
        deadline = time.monotonic() + max(TOTAL_TIMEOUT_S, 1.5 * timeout)
        attempt = 0
        while True:
            attempt += 1
            try:
                return await asyncio.wait_for(prov.run(req), timeout=timeout)
            except asyncio.TimeoutError as e:
                err: Exception = ProviderError(f"{pid} timed out after {timeout}s", retryable=True)
                err.__cause__ = e
            except ProviderError as e:
                err = e
            if not getattr(err, "retryable", False):
                raise err
            delay = min(60.0, BACKOFF_BASE ** attempt + random.uniform(0, 1) * (BACKOFF_BASE - 1))
            ra = getattr(err, "retry_after", None)
            if ra:
                delay = max(delay, float(ra))
            if time.monotonic() + delay > deadline:
                raise err
            self._emit({"type": "retry", "provider": pid, "attempt": attempt, "delay": delay,
                        "error": str(err)[:200]})
            await asyncio.sleep(delay)

    def _emit(self, event: dict[str, Any]) -> None:
        if self.events:
            try:
                self.events(event)
            except Exception:
                pass


def _build(pid: str) -> Provider:
    if pid == "openai":
        from .openai import OpenAIProvider

        return OpenAIProvider()
    if pid == "fal":
        from .fal import FalProvider

        return FalProvider()
    if pid == "retrodiffusion":
        from .retrodiffusion import RetroDiffusionProvider

        return RetroDiffusionProvider()
    if pid == "quiver":
        from .quiver import QuiverProvider

        return QuiverProvider()
    if pid == "synthetic":
        from .synthetic import SyntheticProvider

        return SyntheticProvider()
    raise KeyError(f"unknown provider {pid!r}")
