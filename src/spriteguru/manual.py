"""Manual one-frame repair (`spriteguru repair`, studio "regenerate one frame")."""

from __future__ import annotations

import asyncio
import json

import json

from . import generate as gen
from .jobs import JobRunner, _Handle, _load_frames
from .qa import repair as repair_mod
from .spec import Finding


async def manual_repair(runner: JobRunner, anim_id: str, frame: int, *, kind: str = "pose",
                        note: str | None = None, job_id: str | None = None) -> dict:
    """Repair one frame (0-based, in sheet order) of a finished job's winner; defaults to the newest job."""
    project = runner.project
    jobs = [s for s in runner.all_jobs() if s.anim_id == anim_id and s.state == "done"
            and (job_id is None or s.id == job_id)]
    if not jobs:
        raise ValueError(f"no finished job {job_id or ''} for {anim_id}")
    st = jobs[0]
    h = _Handle(st)
    runner.jobs[st.id] = h
    c, _ = runner._compiled(h)
    if c.route not in ("guided", "guided-pixel"):
        raise ValueError(f"{c.route} jobs repair by re-roll; run `spriteguru gen` again")
    best = runner._best_candidate(st)
    metric = {"pose": "pose", "identity": "identity"}.get(kind, "semantics")
    finding = Finding(metric=metric, level="fail", severity=2, frames=[frame],
                      message=f"manual: — {note}" if note else f"manual {kind} repair", remedy=f"{kind}_fix",
                      issue="changed_item" if kind == "identity" else None)
    rnd = max((cd["round"] for cd in st.candidates), default=0) + 1
    rep = json.loads(project.abs(best["report"]).read_text()) if best.get("report") else {}
    frames_info = rep.get("frames") or []
    old_box = tuple(frames_info[frame]["box"]) if frame < len(frames_info) else None
    new_png, meta = await repair_mod.repair_frame(runner.hub, c, project.abs(best["sheet"]).read_bytes(), frame,
                                                  finding, job=st.id, seed=st.seed + 11000 + rnd, old_box=old_box)
    out = gen.CandidateOut("c1", new_png, "image/png", {**meta, "repaired_from": best["id"], "manual": True})
    ids = runner._store_candidates(h, [out], rnd, "repair", parent=best["id"], key=best.get("key"))
    before = best.get("score", 0.0)
    await runner._analyze(h, ids, set(best.get("key_exclude", [])))
    new = next(cd for cd in st.candidates if cd["id"] == ids[0])
    kept = new.get("score", 0) >= before
    for cd in st.candidates:  # the better of the two becomes the explicit choice
        cd["chosen"] = cd["id"] == (new["id"] if kept else best["id"])
    st.winner = runner._best_candidate(st)["id"]
    res = await runner._export(h)
    runner.save(st)
    return {"job": st.id, "candidate": ids[0], "before": before, "after": new.get("score", 0), "kept": kept,
            "export": res}


async def manual_inbetween(runner: JobRunner, anim_id: str, after: int, *, job_id: str | None = None) -> dict:
    """Insert a generated in-between after frame `after` (0-based, playback order) of a finished job's
    winner (13.6): neighbours on both sides, an interpolated mannequin in between."""
    jobs = [s for s in runner.all_jobs() if s.anim_id == anim_id and s.state == "done"
            and (job_id is None or s.id == job_id)]
    if not jobs:
        raise ValueError(f"no finished job {job_id or ''} for {anim_id}")
    st = jobs[0]
    h = _Handle(st)
    runner.jobs[st.id] = h
    c, _ = runner._compiled(h)
    if c.route not in ("guided", "guided-pixel"):
        raise ValueError(f"{c.route} jobs cannot insert generated in-betweens")
    best = runner._best_candidate(st)
    report = json.loads(runner.project.abs(best["report"]).read_text())
    order = report.get("order") or list(range(len(report.get("frames", []))))
    n = len(order)
    if not 0 <= after < n:
        raise ValueError(f"frame {after + 1} does not exist ({n} frames)")
    nxt = order[(after + 1) % n]
    rnd = max((cd["round"] for cd in st.candidates), default=0) + 1
    step = repair_mod.Step("inbetween", frames=[nxt], reason="manual in-between")
    ids = (await runner._inbetween(h, rnd, step, set(best.get("key_exclude", []))))["candidates"]
    if not ids:
        raise ValueError("in-between needs the guided sheet of the winner")
    await runner._analyze(h, ids, set(best.get("key_exclude", [])))
    new = next(cd for cd in st.candidates if cd["id"] == ids[0])
    for cd in st.candidates:
        cd["chosen"] = cd["id"] == new["id"]
    st.winner = new["id"]
    res = await runner._export(h)
    runner.save(st)
    return {"job": st.id, "candidate": new["id"], "frames": n + 1, "score": new.get("score"), "export": res}
