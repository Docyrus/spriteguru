"""Cost ledger and budget caps (4.4).

Every provider call appends a `sent` line before the request and an outcome line after it,
so a crash mid-call shows up as an unknown outcome instead of silent spend.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator


SESSION_WINDOW_S = 24 * 3600


class Ledger:
    """The session cap counts spend in the last 24 hours, seeded from ledger.jsonl, so restarting the
    engine (or a crash-and-resume loop) can never reset it."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.session_start = time.time()
        self._jobs: dict[str, float] = defaultdict(float)
        self._pending: dict[str, float] = {}
        since = self.session_start - SESSION_WINDOW_S
        self._session = sum(float(e.get("cost", 0.0)) for e in self.entries()
                            if e.get("status") in ("ok", "error") and float(e.get("time", 0)) >= since)

    def append(self, entry: dict[str, Any]) -> None:
        entry = {"time": round(time.time(), 3), **entry}
        line = json.dumps(entry, sort_keys=True, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(line + "\n")
            status = entry.get("status")
            call_id = entry.get("call_id")
            if status == "sent" and call_id:
                self._pending[call_id] = float(entry.get("est_cost", 0.0))
            elif status in ("ok", "error") and call_id:
                self._pending.pop(call_id, None)
                cost = float(entry.get("cost", 0.0))
                self._session += cost
                if entry.get("job"):
                    self._jobs[entry["job"]] += cost

    def session_spend(self) -> float:
        with self._lock:
            return self._session + sum(self._pending.values())

    def job_spend(self, job: str | None) -> float:
        if not job:
            return 0.0
        with self._lock:
            spent = self._jobs.get(job, 0.0)
        if spent == 0.0:
            spent = sum(float(e.get("cost", 0.0)) for e in self.entries()
                        if e.get("job") == job and e.get("status") in ("ok", "error"))
            with self._lock:
                self._jobs[job] = spent
        return spent

    def entries(self) -> Iterator[dict[str, Any]]:
        if not self.path.is_file():
            return iter(())
        with self.path.open() as f:
            lines = f.readlines()
        return (json.loads(line) for line in lines if line.strip())

    def unknown_outcomes(self) -> list[dict[str, Any]]:
        """Calls that were sent but never resolved (crash mid-call)."""
        sent: dict[str, dict[str, Any]] = {}
        for e in self.entries():
            cid = e.get("call_id")
            if not cid:
                continue
            if e.get("status") == "sent":
                sent[cid] = e
            elif e.get("status") in ("ok", "error"):
                sent.pop(cid, None)
        return list(sent.values())

    def summary(self) -> dict[str, Any]:
        total = 0.0
        by_model: dict[str, float] = defaultdict(float)
        calls = hits = 0
        for e in self.entries():
            if e.get("status") in ("ok", "error"):
                total += float(e.get("cost", 0.0))
                by_model[e.get("model", "?")] += float(e.get("cost", 0.0))
                calls += 1
            elif e.get("status") == "cache_hit":
                hits += 1
        return {"total_usd": round(total, 4), "calls": calls, "cache_hits": hits,
                "by_model": {k: round(v, 4) for k, v in sorted(by_model.items())},
                "session_usd": round(self.session_spend(), 4),
                "unknown_outcomes": len(self.unknown_outcomes())}
