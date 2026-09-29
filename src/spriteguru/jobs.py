"""Jobs (section 5): a state machine run as one asyncio task, checkpointing job.json before and
after every step. On restart, unfinished jobs resume from their last completed step; provider
steps go through the cache, so resuming never pays twice for a finished call.
"""

from __future__ import annotations

import asyncio
import collections
import datetime as dt
import json
import os
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np
from PIL import Image
from pydantic import BaseModel

from . import character as char_mod
from . import export as export_mod
from . import generate as gen
from . import report as report_mod
from .env import get as app_env
from .project import Project, read_json, write_json
from .providers.base import BudgetExceeded
from .providers.hub import ApprovalRequest, ProviderHub
from .qa import judge as judge_mod
from .qa import repair as repair_mod
from .qa.score import score as q_score
from .qa.score import top_findings
from .spec import Finding, SpriteSpec

MAX_CONCURRENT_JOBS = 3


class JobState(BaseModel):
    id: str
    kind: str  # animation, turnaround
    anim_id: str | None = None
    character: str | None = None
    state: str = "queued"  # queued, running, awaiting_approval, done, failed, cancelled
    step: str | None = None
    steps: list[dict] = []
    route: str | None = None
    key: str | None = None
    candidates: list[dict] = []
    winner: str | None = None
    attempts: dict = {}
    decisions: list[dict] = []
    spend: float = 0.0
    error: str | None = None
    dir: str = ""
    created: str = ""
    updated: str = ""
    result: dict = {}
    approval: dict | None = None
    seed: int = 0


class EventBus:
    """Engine events for the studio and the sync scheduler. `publish` may be called from worker
    threads (cloud calls and sync rounds run there): each queue is fed on its own event loop."""

    def __init__(self, keep: int = 1000):
        self._subs: dict[asyncio.Queue, asyncio.AbstractEventLoop | None] = {}
        self.history: collections.deque = collections.deque(maxlen=keep)

    @staticmethod
    def _put(q: asyncio.Queue, event: dict) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def publish(self, event: dict[str, Any]) -> None:
        event = {"ts": round(dt.datetime.now().timestamp(), 3), **event}
        self.history.append(event)
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        for q, loop in list(self._subs.items()):
            if loop is None or loop is here:
                self._put(q, event)
            elif not loop.is_closed():
                loop.call_soon_threadsafe(self._put, q, event)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        self._subs[q] = loop
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.pop(q, None)

    @property
    def listeners(self) -> int:
        return len(self._subs)


class Cancelled(Exception):
    pass


@dataclass
class _Handle:
    state: JobState
    task: asyncio.Task | None = None
    cancel: bool = False
    approval: asyncio.Future | None = None
    memo: dict = field(default_factory=dict)


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _save_frames(d: Path, frames: list[np.ndarray]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.png"):
        old.unlink()
    for i, f in enumerate(frames):
        Image.fromarray(f, "RGBA").save(d / f"{i:03d}.png")


def _load_frames(d: Path) -> list[np.ndarray]:
    return [np.asarray(Image.open(p).convert("RGBA")).copy() for p in sorted(d.glob("*.png"))]


class JobRunner:
    def __init__(self, project: Project, hub: ProviderHub, bus: EventBus | None = None, *,
                 interactive_approve: Callable[[ApprovalRequest], Awaitable[bool]] | None = None):
        self.project = project
        self.hub = hub
        self.bus = bus or EventBus()
        self.jobs: dict[str, _Handle] = {}
        self._sem: asyncio.Semaphore | None = None
        self._interactive = interactive_approve
        hub.approve = self._approve
        prev = hub.events
        hub.events = lambda e: (self.bus.publish(e), prev(e) if prev else None)

    @property
    def sem(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        return self._sem

    # -- records ------------------------------------------------------------------

    def _path(self, st: JobState) -> Path:
        return self.project.abs(st.dir) / "job.json"

    def save(self, st: JobState) -> None:
        st.updated = _now()
        st.spend = round(self.hub.ledger.job_spend(st.id), 5)
        write_json(self._path(st), st)

    def load(self, job_dir: Path) -> JobState:
        return JobState.model_validate(read_json(job_dir / "job.json"))

    def get(self, job_id: str) -> JobState | None:
        h = self.jobs.get(job_id)
        if h:
            return h.state
        for p in self.project.root.glob("animations/*/jobs/*/job.json"):
            st = JobState.model_validate(read_json(p))
            if st.id == job_id:
                return st
        return None

    def all_jobs(self) -> list[JobState]:
        seen = {}
        for p in sorted(self.project.root.glob("animations/*/jobs/*/job.json")):
            try:
                st = JobState.model_validate(read_json(p))
            except Exception:
                continue
            seen[st.id] = st
        for jid, h in self.jobs.items():
            seen[jid] = h.state
        return sorted(seen.values(), key=lambda s: s.created, reverse=True)

    def create_animation_job(self, anim_id: str, *, seed: int = 0) -> JobState:
        d = self.project.new_job_dir(anim_id)
        st = JobState(id=f"{anim_id}@{d.name}", kind="animation", anim_id=anim_id,
                      character=None, dir=self.project.rel(d), created=_now(), seed=seed,
                      attempts=repair_mod.Attempts().to_dict())
        self.save(st)
        self.jobs[st.id] = _Handle(st)
        self.bus.publish({"type": "job_created", "job": st.id, "anim": anim_id})
        return st

    # -- approvals and cancellation -----------------------------------------------------

    async def _approve(self, req: ApprovalRequest) -> bool:
        h = self.jobs.get(req.job or "")
        if h is None:
            return await self._interactive(req) if self._interactive else False
        if self._interactive is not None:
            return await self._interactive(req)
        loop = asyncio.get_running_loop()
        h.approval = loop.create_future()
        h.state.state = "awaiting_approval"
        h.state.approval = {"model": req.model, "op": req.op, "estimate": round(req.estimate, 4),
                            "reason": req.reason, "session_spend": round(req.session_spend, 4),
                            "job_spend": round(req.job_spend, 4)}
        self.save(h.state)
        self.bus.publish({"type": "approval_required", "job": h.state.id, **h.state.approval})
        try:
            ok = await h.approval
        finally:
            h.approval = None
            h.state.approval = None
            h.state.state = "running"
            self.save(h.state)
        return bool(ok)

    def approve(self, job_id: str, ok: bool) -> bool:
        h = self.jobs.get(job_id)
        if h and h.approval and not h.approval.done():
            h.approval.set_result(ok)
            return True
        return False

    def cancel(self, job_id: str) -> bool:
        h = self.jobs.get(job_id)
        if not h:
            return False
        h.cancel = True  # cooperative: checked between steps; in-flight calls finish into the cache
        if h.approval and not h.approval.done():
            h.approval.set_result(False)
        return True

    # -- running ---------------------------------------------------------------------

    def start(self, job_id: str) -> asyncio.Task:
        h = self.jobs[job_id]
        h.task = asyncio.create_task(self.run(job_id))
        return h.task

    async def run(self, job_id: str) -> JobState:
        h = self.jobs[job_id]
        st = h.state
        async with self.sem:
            st.state = "running"
            st.error = None
            self.save(st)
            try:
                if st.kind == "animation":
                    await self._animation(h)
                st.state = "done"
                st.step = None
                self.bus.publish({"type": "job_done", "job": st.id, "result": st.result})
            except Cancelled:
                st.state = "cancelled"
                self.bus.publish({"type": "job_cancelled", "job": st.id})
            except BudgetExceeded as e:
                st.state = "failed"
                st.error = f"budget: {e}"
                self.bus.publish({"type": "job_failed", "job": st.id, "error": st.error})
            except Exception as e:
                st.state = "failed"
                st.error = f"{type(e).__name__}: {e}"
                (self.project.abs(st.dir) / "error.txt").write_text(traceback.format_exc())
                self.bus.publish({"type": "job_failed", "job": st.id, "error": st.error})
            finally:
                self.save(st)
        return st

    async def resume_unfinished(self) -> list[str]:
        started = []
        for p in sorted(self.project.root.glob("animations/*/jobs/*/job.json")):
            st = JobState.model_validate(read_json(p))
            if st.state in ("queued", "running", "awaiting_approval"):
                st.state = "queued"
                self.jobs[st.id] = _Handle(st)
                self.start(st.id)
                started.append(st.id)
        return started

    async def _step(self, h: _Handle, name: str, fn: Callable[[], Awaitable[dict]]) -> dict:
        """Run a step once: checkpoint before and after; a completed step is replayed from job.json."""
        st = h.state
        done = next((s for s in st.steps if s["name"] == name and s["status"] == "done"), None)
        if done is not None:
            return done.get("info", {})
        if h.cancel:
            raise Cancelled()
        rec = next((s for s in st.steps if s["name"] == name), None)
        if rec is None:
            rec = {"name": name, "status": "running", "started": _now()}
            st.steps.append(rec)
        rec["status"] = "running"
        st.step = name
        self.save(st)
        self.bus.publish({"type": "step_started", "job": st.id, "step": name})
        info = await fn()
        rec.update(status="done", finished=_now(), info=info)
        self.save(st)
        self.bus.publish({"type": "step_done", "job": st.id, "step": name})
        if app_env("CRASH_AFTER") == name:  # fault injection for the resume E2E (J1)
            os._exit(3)
        return info

    # -- the animation state machine ---------------------------------------------------

    def _compiled(self, h: _Handle, key_exclude: set[str] | None = None) -> gen.Compiled:
        memo_key = ("compiled", tuple(sorted(key_exclude or ())))
        if memo_key not in h.memo:
            spec = self.project.spec(h.state.anim_id)
            meta_p = self.project.animation_dir(h.state.anim_id) / "meta.json"
            name = read_json(meta_p)["character"] if meta_p.is_file() else h.state.anim_id.rsplit("-", 2)[0]
            rec = self.project.character(h.state.character or name)
            h.state.character = rec.name
            c = gen.compile_spec(self.project, spec, rec, key_exclude=key_exclude, mode=self.hub.mode)
            c.__dict__["sol_effort"] = self.project.config.settings.sol_effort
            h.memo[memo_key] = (c, rec)
        return h.memo[memo_key]

    async def _animation(self, h: _Handle) -> None:
        st = h.state
        jdir = self.project.abs(st.dir)
        key_exclude: set[str] = set()

        async def compile_step():
            c, rec = self._compiled(h)
            if c.guide is not None:
                c.guide.image.save(jdir / "guide.png")
                c.guide.mask.save(jdir / "mask.png")
                write_json(jdir / "guide.json", c.guide.to_dict())
            if c.prompt is not None:
                (jdir / "prompt.txt").write_text(c.prompt.text + "\n")
            write_json(jdir / "compile.json", c.summary())
            return c.summary()

        info = await self._step(h, "compile", compile_step)
        st.route, st.key = info["route"], info["key"]
        attempts = repair_mod.Attempts()  # re-derived by replaying decisions on resume
        round_no = 0
        pending = [("generate", 0, None)]
        while pending:
            kind, rnd, step_obj = pending.pop(0)
            if kind in ("generate", "reroll"):
                excl = set(step_obj.key_exclude) if step_obj else set()
                key_exclude |= excl
                ids = await self._step(h, f"{kind}-{rnd}", lambda: self._generate(h, rnd, key_exclude))
            elif kind == "repair":
                ids = await self._step(h, f"repair-{rnd}", lambda: self._repair(h, rnd, step_obj, key_exclude))
            elif kind == "inbetween":
                ids = await self._step(h, f"inbetween-{rnd}", lambda: self._inbetween(h, rnd, step_obj, key_exclude))
            else:
                break
            ana = await self._step(h, f"analyze-{rnd}", lambda: self._analyze(h, ids["candidates"], key_exclude))
            best = self._best_candidate(st)
            decision = await self._step(h, f"decide-{rnd}", lambda: self._decide(h, best, attempts, key_exclude))
            st.attempts = attempts.to_dict()
            act = decision["action"]
            if act in ("accept", "best_effort"):
                break
            round_no = rnd + 1
            if act == "reroll":
                attempts.rerolls += 1
            elif act == "repair":
                attempts.repairs += 1
            elif act == "inbetween":
                attempts.inbetweens += 1
                attempts.repairs += 1
            st.attempts = attempts.to_dict()
            self.save(st)
            pending.append((act, round_no, repair_mod.Step(act, decision.get("frames", []),
                                                           Finding.model_validate(decision["finding"])
                                                           if decision.get("finding") else None,
                                                           decision.get("reason", ""),
                                                           set(decision.get("key_exclude", [])))))
        await self._step(h, "export", lambda: self._export(h))

    async def _generate(self, h: _Handle, rnd: int, key_exclude: set[str]) -> dict:
        st = h.state
        h.memo["key_exclude"] = set(key_exclude)
        c, rec = self._compiled(h, key_exclude)
        seed = st.seed + rnd * 1000
        outs = await gen.generate(self.hub, c, job=st.id, seed=seed, rec=rec, project=self.project)
        return {"candidates": self._store_candidates(h, outs, rnd, "generate" if rnd == 0 else "reroll",
                                                     key=c.key)}

    def _store_candidates(self, h: _Handle, outs: list[gen.CandidateOut], rnd: int, kind: str,
                          parent: str | None = None, key: str | None = None) -> list[str]:
        st = h.state
        cdir = self.project.abs(st.dir) / "candidates"
        cdir.mkdir(exist_ok=True)
        ids = []
        for o in outs:
            cid = o.id if rnd == 0 else f"r{rnd}-{o.id}"
            meta = {k: v for k, v in o.meta.items() if k not in ("svg",)}
            entry = {"id": cid, "round": rnd, "kind": kind, "parent": parent, "key": key,
                     "key_exclude": sorted(h.memo.get("key_exclude", set()))}
            if o.image is not None:
                p = cdir / f"{cid}.png"
                p.write_bytes(o.image)
                entry["sheet"] = self.project.rel(p)
            if o.frames is not None:
                fd = cdir / f"{cid}.source"
                _save_frames(fd, o.frames)
                entry["source_frames"] = self.project.rel(fd)
            if o.meta.get("svg"):
                (cdir / f"{cid}.svg").write_text(o.meta["svg"])
                entry["svg"] = self.project.rel(cdir / f"{cid}.svg")
            if o.extra.get("video_bytes"):
                (cdir / f"{cid}.mp4").write_bytes(o.extra["video_bytes"])
                entry["video"] = self.project.rel(cdir / f"{cid}.mp4")
            extra = {k: v for k, v in o.extra.items() if k not in ("video_bytes", "findings")}
            if o.extra.get("findings"):
                extra["findings"] = [f.model_dump() for f in o.extra["findings"]]
            (cdir / f"{cid}.meta.json").write_text(json.dumps({**meta, "extra": extra}, indent=2, default=str))
            entry["meta"] = self.project.rel(cdir / f"{cid}.meta.json")
            st.candidates = [x for x in st.candidates if x["id"] != cid] + [entry]
            ids.append(cid)
            self.bus.publish({"type": "candidate_ready", "job": st.id, "candidate": cid})
        self.save(st)
        return ids

    def _candidate_out(self, h: _Handle, entry: dict) -> gen.CandidateOut:
        meta = json.loads(self.project.abs(entry["meta"]).read_text())
        extra = meta.pop("extra", {})
        if extra.get("findings"):
            extra["findings"] = [Finding.model_validate(f) for f in extra["findings"]]
        img = self.project.abs(entry["sheet"]).read_bytes() if entry.get("sheet") else None
        frames = _load_frames(self.project.abs(entry["source_frames"])) if entry.get("source_frames") else None
        return gen.CandidateOut(entry["id"], img, "image/png", meta, frames=frames, extra=extra)

    async def _analyze(self, h: _Handle, ids: list[str], key_exclude: set[str]) -> dict:
        st = h.state
        cdir = self.project.abs(st.dir) / "candidates"
        settings = self.project.config.settings

        async def one(cid: str) -> dict:
            entry = next(x for x in st.candidates if x["id"] == cid)
            c, rec = self._compiled(h, set(entry.get("key_exclude", [])))
            out = self._candidate_out(h, entry)
            if entry.get("kind") == "inbetween":
                from .pipeline.analyze import analyze_frames

                spec = c.spec.model_copy(update={"frames": len(out.frames)})
                an = await asyncio.to_thread(analyze_frames, out.frames, spec, reference=c.reference,
                                             register_frames=True)
            else:
                an = await asyncio.to_thread(gen.analyze_candidate, c, out)
            # the VLM judge adds semantic findings to candidates worth judging
            if settings.judge_enabled and an.frames and an.report.score >= 40:
                try:
                    ch_frames = c.choreo.frames if getattr(c, "choreo", None) is not None else []
                    order = an.report.order or list(range(len(an.frames)))
                    lines = [ch_frames[i].line for i in order] \
                        if len(ch_frames) == len(an.frames) and sorted(order) == list(range(len(ch_frames))) else None
                    jf, jinfo = await judge_mod.judge(self.hub, an.frames, c.reference, c.spec.action,
                                                      effort=settings.luna_effort, job=st.id,
                                                      disabled=set(settings.judge_disabled),
                                                      kind=c.spec.character.kind, lines=lines)
                    judge_mod.corroborate(jf, an.report.findings)
                    an.report.findings += jf
                    an.report.score, an.report.accepted = q_score(an.report.findings)
                    an.report.temporal["judge"] = jinfo
                except BudgetExceeded:
                    raise
                except Exception as e:
                    an.report.temporal["judge_error"] = str(e)[:300]
            _save_frames(cdir / f"{cid}.frames", an.frames)
            if entry.get("sheet") and an.matte is not None and an.matte.rgba.ndim == 3 and an.layout.labels.size > 1:
                Image.fromarray(an.matte.rgba, "RGBA").save(cdir / f"{cid}.matte.png")
                removed = an.matte.mask & ~an.layout.clean_mask
                Image.fromarray((removed * 255).astype(np.uint8), "L").save(cdir / f"{cid}.removed.png")
                entry["matte"] = self.project.rel(cdir / f"{cid}.matte.png")
                entry["removed"] = self.project.rel(cdir / f"{cid}.removed.png")
            sheet_rgb = np.asarray(Image.open(self.project.abs(entry["sheet"])).convert("RGB")) if entry.get("sheet") \
                else (an.frames[0][..., :3] if an.frames else np.zeros((8, 8, 3), np.uint8))
            html_path = await asyncio.to_thread(report_mod.write_analysis, cdir / f"{cid}.report", an, sheet_rgb,
                                                title=f"{st.anim_id} · {cid}", pixel=c.spec.style.kind == "pixel")
            (cdir / f"{cid}.report.json").write_text(json.dumps(an.report.model_dump(mode="json"), indent=2))
            entry.update(score=an.report.score, accepted=an.report.accepted,
                         frames=self.project.rel(cdir / f"{cid}.frames"),
                         report=self.project.rel(cdir / f"{cid}.report.json"),
                         html=self.project.rel(html_path),
                         top=[{"metric": f.metric, "level": f.level, "message": f.message, "frames": f.frames,
                               "remedy": f.remedy} for f in top_findings(an.report.findings)])
            self.bus.publish({"type": "candidate_scored", "job": st.id, "candidate": cid,
                              "score": an.report.score, "accepted": an.report.accepted,
                              "top": entry["top"]})
            self.save(st)
            return {"score": an.report.score, "accepted": an.report.accepted}

        # one candidate at a time: running two analyses in threads at once aborted the process at exit
        # (libc++abi recursive_mutex in a native library's teardown), so the analyses stay sequential
        results = {}
        for cid in ids:
            results[cid] = await one(cid)
        best = self._best_candidate(st)
        st.winner = best["id"] if best else None
        self.save(st)
        return {"results": results, "winner": st.winner}

    @staticmethod
    def _best_candidate(st: JobState) -> dict | None:
        scored = [c for c in st.candidates if "score" in c]
        if not scored:
            return None
        # a hand-picked winner outranks every automatic choice
        # a repair, re-roll or in-between must beat what it replaces: on a tie the earlier candidate stays
        # (K16: an equal-scoring repair that drew a full burst into a fading frame was exported)
        return max(scored, key=lambda c: (bool(c.get("chosen")), bool(c.get("accepted")), c["score"], -c["round"]))

    async def _decide(self, h: _Handle, best: dict | None, attempts: repair_mod.Attempts, key_exclude) -> dict:
        st = h.state
        if best is None:
            return {"action": "best_effort", "reason": "no candidate"}
        report = json.loads(self.project.abs(best["report"]).read_text())
        findings = [Finding.model_validate(f) for f in report["findings"]]
        c, _ = self._compiled(h, key_exclude)
        n = len(report.get("frames", [])) or c.spec.frames
        step = repair_mod.plan_next(findings, score=report["score"], accepted=report["accepted"], route=c.route,
                                    n_frames=n, attempts=attempts, key=best.get("key") or c.key)
        d = {**step.to_dict(), "candidate": best["id"], "score": report["score"]}
        st.decisions.append(d)
        self.bus.publish({"type": "decision", "job": st.id, **d})
        return d

    async def _repair(self, h: _Handle, rnd: int, step: repair_mod.Step, key_exclude) -> dict:
        st = h.state
        c, _ = self._compiled(h, key_exclude)
        best = self._best_candidate(st)
        sheet = self.project.abs(best["sheet"]).read_bytes()
        idx = step.frames[0]
        rep = json.loads(self.project.abs(best["report"]).read_text()) if best.get("report") else {}
        frames_info = rep.get("frames") or []
        old_box = tuple(frames_info[idx]["box"]) if idx < len(frames_info) else None
        new_png, meta = await repair_mod.repair_frame(self.hub, c, sheet, idx, step.finding, job=st.id,
                                                      seed=st.seed + 7000 + rnd, old_box=old_box)
        rdir = self.project.abs(st.dir) / "repairs"
        rdir.mkdir(exist_ok=True)
        (rdir / f"f{idx + 1}-r{rnd}.png").write_bytes(new_png)
        out = gen.CandidateOut(f"c{best['id'].split('-')[-1].lstrip('c')}", new_png, "image/png",
                               {**meta, "repaired_from": best["id"]})
        return {"candidates": self._store_candidates(h, [out], rnd, "repair", parent=best["id"], key=best.get("key"))}

    async def _inbetween(self, h: _Handle, rnd: int, step: repair_mod.Step, key_exclude) -> dict:
        st = h.state
        c, _ = self._compiled(h, key_exclude)
        best = self._best_candidate(st)
        frames = _load_frames(self.project.abs(best["frames"]))
        report = json.loads(self.project.abs(best["report"]).read_text())
        order = report.get("order") or list(range(len(frames)))
        n = len(frames)
        b = step.frames[0] if step.frames else 0
        pos = order.index(b) if b in order else 0
        a_pos = (pos - 1) % n
        a_frame, b_frame = order[a_pos], order[pos]
        sheet = self.project.abs(best["sheet"]).read_bytes() if best.get("sheet") else None
        if sheet is None or c.guide is None:
            return {"candidates": []}
        mid, meta = await repair_mod.inbetween(self.hub, c, sheet, a_frame, b_frame, job=st.id,
                                               seed=st.seed + 9000 + rnd)
        pix = report.get("pixel") or {}
        if c.spec.style.kind == "pixel" and pix.get("pitch"):
            # R12: the other frames are already on the logical lattice; the new cell is at source size
            from .pipeline import pixel as pixel_mod

            mid = pixel_mod.to_logical(mid, float(pix["pitch"]), pix.get("palette"))
        seq = frames[: pos] + [mid] + frames[pos:]
        out = gen.CandidateOut("c1", None, "image/png", {**meta, "inserted_at": pos}, frames=seq)
        return {"candidates": self._store_candidates(h, [out], rnd, "inbetween", parent=best["id"],
                                                     key=best.get("key"))}

    async def _export(self, h: _Handle) -> dict:
        st = h.state
        best = self._best_candidate(st)
        if best is None:
            raise RuntimeError("no candidate to export")
        c, _ = self._compiled(h)
        spec: SpriteSpec = self.project.spec(st.anim_id)
        frames = _load_frames(self.project.abs(best["frames"]))
        report = json.loads(self.project.abs(best["report"]).read_text())
        durations = [f.get("duration_ms", 83) for f in report.get("frames", [])] or [83] * len(frames)
        if len(durations) != len(frames):
            durations = [int(round(1000 / max(1, spec.fps)))] * len(frames)
        pivot = tuple(report.get("pivot", (0.5, 0.94)))
        if c.flip_output:
            frames = [np.ascontiguousarray(f[:, ::-1]) for f in frames]
            pivot = (1.0 - pivot[0], pivot[1])
        fs = export_mod.FrameSet(name=st.anim_id, action=spec.action, frames=frames, durations=durations,
                                 pivot=pivot, loop=spec.loop, fps=spec.fps, pixel=spec.style.kind == "pixel",
                                 root_motion=report.get("temporal", {}).get("registration", {}).get("root_motion"),
                                 blend=spec.character.blend)
        final = self.project.animation_dir(st.anim_id) / "final"
        asset = Path(self.project.config.asset_folder) if self.project.config.asset_folder else None
        res = await asyncio.to_thread(export_mod.export, fs, final, spec.engine, asset_folder=asset,
                                      report=report)
        html_src = self.project.abs(best["html"]) if best.get("html") else None
        if html_src and html_src.is_file():
            (final / "report.html").write_text(html_src.read_text())
        st.result = {"winner": best["id"], "score": best.get("score"), "accepted": best.get("accepted"),
                     "final": self.project.rel(final), "files": res["files"], "copied_to": res["copied_to"],
                     "spend": round(self.hub.ledger.job_spend(st.id), 5)}
        return st.result
