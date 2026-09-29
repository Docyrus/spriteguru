"""Hand fixes from the frame editor (section 17): nudge, pivot, duration, flip, reorder, delete.

Edits apply to the exported canonical frame set and re-export immediately, so the studio stays
instant without porting any algorithm to TypeScript.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import export as export_mod
from .project import Project


def load_final(project: Project, anim_id: str) -> tuple[export_mod.FrameSet, dict]:
    final = project.animation_dir(anim_id) / "final"
    meta = json.loads((final / "animation.json").read_text())
    frames = [np.asarray(Image.open(p).convert("RGBA")).copy() for p in sorted((final / "frames").glob("*.png"))]
    fs = export_mod.FrameSet(name=meta["name"], action=meta["action"], frames=frames, durations=meta["durations"],
                             pivot=tuple(meta["pivot"]), loop=meta["loop"], fps=meta["fps"], pixel=meta["pixel"],
                             root_motion=meta.get("root_motion"), blend=meta.get("blend", "normal"))
    return fs, meta


def _shift(f: np.ndarray, dx: int, dy: int) -> np.ndarray:
    out = np.zeros_like(f)
    h, w = f.shape[:2]
    xs0, xd0 = max(0, -dx), max(0, dx)
    ys0, yd0 = max(0, -dy), max(0, dy)
    ww, hh = w - abs(dx), h - abs(dy)
    if ww > 0 and hh > 0:
        out[yd0:yd0 + hh, xd0:xd0 + ww] = f[ys0:ys0 + hh, xs0:xs0 + ww]
    return out


def apply(project: Project, anim_id: str, edits: list[dict]) -> dict:
    """edits: [{op: nudge, frame, dx, dy} | {op: flip, frame} | {op: duration, frame, ms} |
    {op: pivot, x, y} | {op: reorder, order: [...]} | {op: delete, frame} | {op: loop, value}]"""
    fs, meta = load_final(project, anim_id)
    frames, durs = list(fs.frames), list(fs.durations)
    pivot, loop = fs.pivot, fs.loop
    for e in edits:
        op = e["op"]
        if op == "nudge":
            frames[e["frame"]] = _shift(frames[e["frame"]], int(e.get("dx", 0)), int(e.get("dy", 0)))
        elif op == "flip":
            frames[e["frame"]] = np.ascontiguousarray(frames[e["frame"]][:, ::-1])
        elif op == "duration":
            durs[e["frame"]] = max(10, int(e["ms"]))
        elif op == "pivot":
            pivot = (float(e["x"]), float(e["y"]))
        elif op == "reorder":
            order = [int(i) for i in e["order"]]
            if sorted(order) != list(range(len(frames))):
                raise ValueError("reorder must be a permutation of all frames")
            frames = [frames[i] for i in order]
            durs = [durs[i] for i in order]
        elif op == "delete":
            if len(frames) <= 2:
                raise ValueError("an animation needs at least two frames")
            del frames[e["frame"]]
            del durs[e["frame"]]
        elif op == "loop":
            loop = bool(e["value"])
        else:
            raise ValueError(f"unknown edit {op!r}")
    fs = export_mod.FrameSet(fs.name, fs.action, frames, durs, pivot, loop, fs.fps, fs.pixel, fs.root_motion, fs.blend)
    final = project.animation_dir(anim_id) / "final"
    report = json.loads((final / "report.json").read_text()) if (final / "report.json").is_file() else None
    spec = project.spec(anim_id)
    asset = Path(project.config.asset_folder) if project.config.asset_folder else None
    res = export_mod.export(fs, final, spec.engine, asset_folder=asset, report=report)
    log = final / "edits.json"
    history = json.loads(log.read_text()) if log.is_file() else []
    history.append({"edits": edits})
    log.write_text(json.dumps(history, indent=2))
    return res
