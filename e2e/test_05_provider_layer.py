"""Provider layer in isolation (docs/failure-modes.md P1–P9): cache, ledger, budget caps, retries,
replay and concurrency, exercised through the real hub with a scripted stand-in provider."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from spriteguru.cache import Cache
from spriteguru.ledger import Ledger
from spriteguru.providers.base import (BudgetExceeded, CallResult, Candidate, ProviderError, ProviderRequest,
                                      ReplayMiss)
from spriteguru.providers.hub import ProviderHub

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class Scripted:
    """Stand-in for a provider: returns queued outcomes and records concurrency."""

    id = "openai"

    def __init__(self, outcomes=None, delay=0.0):
        self.outcomes = list(outcomes or [])
        self.calls = 0
        self.delay = delay
        self.active = 0
        self.peak = 0

    async def run(self, req):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.outcomes:
                o = self.outcomes.pop(0)
                if isinstance(o, Exception):
                    raise o
            return CallResult([Candidate(PNG, "image/png", {})], request_id=f"req-{self.calls}",
                              usage={"input_tokens_details": {"text_tokens": 100, "image_tokens": 0},
                                     "output_tokens_details": {"image_tokens": 1000}})
        finally:
            self.active -= 1


def hub(tmp: Path, prov, **kw) -> ProviderHub:
    return ProviderHub(Cache(tmp / "cache"), Ledger(tmp / "ledger.jsonl"), mode="live",
                       providers={"openai": prov}, **kw)


def req(**kw) -> ProviderRequest:
    base = dict(op="edit", model="gpt-image-2.5-flare", prompt="draw", params={"quality": "high", "n": 1},
                images=[PNG], size=(1024, 1024), seed=1)
    base.update(kw)
    return ProviderRequest(**base)


def test_provider_layer(rec, work, monkeypatch):
    S = "provider-layer"
    import spriteguru.providers.hub as hub_mod

    monkeypatch.setattr(hub_mod, "BACKOFF_BASE", 1.05)
    tmp = work / "provider"

    async def main():
        # P1: the same request twice pays once
        p = Scripted()
        h = hub(tmp / "p1", p)
        await h.call(req(), job="j")
        c2 = await h.call(req(), job="j")
        lines = list(h.ledger.entries())
        rec.check(S, "second identical call is a cache hit", p.calls == 1 and c2[0].meta["cached"] and
                  lines[-1]["status"] == "cache_hit" and lines[-1]["cost"] == 0.0,
                  [l["status"] for l in lines], ["P1"])
        # P2: every input moves the key; dict order does not
        k = req().cache_key("openai")
        variants = {"image": req(images=[PNG + b"x"]), "seed": req(seed=2), "prompt": req(prompt="draw!"),
                    "param": req(params={"quality": "low", "n": 1}), "mask": req(mask=PNG),
                    "model": req(model="gpt-image-2.5-sunburst")}
        moved = {n: r.cache_key("openai") != k for n, r in variants.items()}
        same = req(params={"n": 1, "quality": "high"}).cache_key("openai") == k
        rec.check(S, "cache key covers every input, ignores key order", all(moved.values()) and same,
                  {**moved, "reordered_same": same}, ["P2"])
        # P3: a 'sent' line precedes every outcome
        p = Scripted([RuntimeError("socket closed")])
        h = hub(tmp / "p3", p)
        try:
            await h.call(req(), job="j")
        except RuntimeError:
            pass
        st = [l["status"] for l in h.ledger.entries()]
        rec.check(S, "sent line written before the outcome", st == ["sent", "error"], st, ["P3"])
        # P4: budget caps ask before crossing; approval lets it through
        p = Scripted()
        h = hub(tmp / "p4", p, session_cap=0.01)
        try:
            await h.call(req(), job="j")
            blocked = False
        except BudgetExceeded:
            blocked = True
        rec.check(S, "call over the session cap blocked before sending", blocked and p.calls == 0, p.calls, ["P4"])
        asked = []

        async def yes(a):
            asked.append(a.reason)
            return True

        h = hub(tmp / "p4b", p, session_cap=0.01, approve=yes)
        await h.call(req(), job="j")
        rec.check(S, "approved call proceeds", p.calls == 1 and asked, asked, ["P4"])
        p = Scripted()
        h = hub(tmp / "p4c", p, job_cap=0.01)
        try:
            await h.call(req(), job="j")
            blocked = False
        except BudgetExceeded as e:
            blocked = e.scope == "job"
        rec.check(S, "call over the job cap blocked", blocked and p.calls == 0, covers=["P4"])
        vp = Scripted()
        vh = ProviderHub(Cache(tmp / "p4d" / "cache"), Ledger(tmp / "p4d" / "l.jsonl"), mode="live",
                         providers={"fal": vp})
        try:
            await vh.call(ProviderRequest(op="video", model="minimax/h3-max/image-to-video", prompt="walk",
                                          params={"duration": 5}, images=[PNG]), job="v")
            asked_video = False
        except BudgetExceeded as e:
            asked_video = "video" in str(e)
        rec.check(S, "video calls always ask", asked_video and vp.calls == 0, covers=["P4"])
        # P4: the session cap survives an engine restart (it counts the last 24 hours from the ledger)
        led = Ledger(tmp / "p4e" / "ledger.jsonl")
        led.append({"status": "ok", "call_id": "x", "cost": 9.9, "job": "old"})
        p = Scripted()
        h = ProviderHub(Cache(tmp / "p4e" / "cache"), Ledger(tmp / "p4e" / "ledger.jsonl"), mode="live",
                        providers={"openai": p}, session_cap=10.0)
        try:
            await h.call(req(prompt="after restart"), job="new")
            blocked = False
        except BudgetExceeded:
            blocked = True
        rec.check(S, "session cap survives a restart", blocked and p.calls == 0, covers=["P4"])
        # P4: one job-cap approval covers one more cap's worth, not the job forever
        asks = []

        async def yes2(a):
            asks.append(a.reason)
            return True

        p = Scripted()
        h = hub(tmp / "p4f", p, job_cap=0.01, approve=yes2)
        for i in range(3):
            await h.call(req(prompt=f"repair {i}"), job="j")
        rec.check(S, "job cap asks again after the approved amount", len(asks) == 3, len(asks), ["P4"])
        # P5: 429 and 5xx retry with backoff
        p = Scripted([ProviderError("rate", retryable=True, status=429),
                      ProviderError("oops", retryable=True, status=503)])
        h = hub(tmp / "p5", p)
        t0 = time.monotonic()
        await h.call(req(), job="j")
        retries = [l for l in h.ledger.entries() if l["status"] == "ok"]
        rec.check(S, "429/5xx retried until success", p.calls == 3 and len(retries) == 1, p.calls, ["P5"])
        # P6: non-retryable errors fail at once
        p = Scripted([ProviderError("bad request", retryable=False, status=400)])
        h = hub(tmp / "p6", p)
        try:
            await h.call(req(), job="j")
            failed = False
        except ProviderError:
            failed = True
        rec.check(S, "4xx fails immediately", failed and p.calls == 1, p.calls, ["P6"])
        # P8: replay never calls a provider
        p = Scripted()
        h = hub(tmp / "p8", p, replay=True)
        try:
            await h.call(req(prompt="uncached"), job="j")
            miss = False
        except ReplayMiss:
            miss = True
        rec.check(S, "replay mode raises on a cache miss without calling", miss and p.calls == 0, covers=["P8"])
        # P9: at most 4 concurrent calls per provider
        p = Scripted(delay=0.15)
        h = hub(tmp / "p9", p)
        await asyncio.gather(*[h.call(req(prompt=f"p{i}"), job="j") for i in range(10)])
        rec.check(S, "per-provider concurrency capped at 4", p.peak == 4, p.peak, ["P9"])

    asyncio.run(main())

    # P3 (hard crash): a process killed mid-call leaves an unknown outcome, not silent spend
    crash = tmp / "crash"
    code = f"""
import asyncio, os, sys
sys.path.insert(0, {str(Path(__file__).parent)!r})
from test_05_provider_layer import hub, req
class Dies:
    id = 'openai'
    async def run(self, r):
        os._exit(9)
asyncio.run(hub(__import__('pathlib').Path({str(crash)!r}), Dies()).call(req(), job='j'))
"""
    subprocess.run([sys.executable, "-c", code], capture_output=True)
    unknown = Ledger(crash / "ledger.jsonl").unknown_outcomes()
    rec.check(S, "crash mid-call shows up as an unknown outcome", len(unknown) == 1,
              [u["status"] for u in unknown], ["P3"])


def test_keys_never_leak(rec, work):
    """P7: key values never reach the ledger, the cache, job files or API responses."""
    S = "provider-layer"
    from spriteguru import keys

    secrets = [v for v in (keys.get(p) for p in keys.ENV) if v]
    hits = []
    for base in (work,):
        for f in base.rglob("*"):
            if f.is_file() and f.suffix in (".json", ".jsonl", ".txt", ".html", ".svg"):
                data = f.read_bytes()
                if any(s.encode() in data for s in secrets):
                    hits.append(str(f.relative_to(work)))
    rec.check(S, "no key value in any project file", not hits, hits[:5], ["P7"])
    st = keys.status()
    rec.check(S, "key status exposes only configured/source", all(set(v) == {"configured", "source", "env"}
                                                                  for v in st.values()), covers=["P7"])
