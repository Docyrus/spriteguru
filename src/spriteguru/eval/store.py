"""eval.sqlite results store and the regression gate (16.3)."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path


def record(db: Path, doc: dict, *, label: str = "", kind: str = "analysis") -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    try:
        con.execute("""create table if not exists runs (id integer primary key, time text, kind text, label text,
                       digest text, cases int, passed int, iou_mean real, summary text)""")
        con.execute("""create table if not exists cases (run int, name text, passed int, score real,
                       accepted int, frames_detected int, frames_true int, iou_min real, checks text)""")
        s = doc["summary"]
        cur = con.execute("insert into runs (time, kind, label, digest, cases, passed, iou_mean, summary) values "
                          "(?,?,?,?,?,?,?,?)", (dt.datetime.now().isoformat(timespec="seconds"), kind, label,
                                                doc.get("digest"), s.get("cases"), s.get("passed"),
                                                s.get("frame_iou_mean"), json.dumps(s)))
        run = cur.lastrowid
        for r in doc.get("results", []):
            con.execute("insert into cases values (?,?,?,?,?,?,?,?,?)",
                        (run, r.get("case") or r.get("name"), int(bool(r.get("passed"))), r.get("score"),
                         int(bool(r.get("accepted"))), r.get("frames_detected"), r.get("frames_true"),
                         min(r["frame_iou"]) if r.get("frame_iou") else None, json.dumps(r.get("checks", []))))
        con.commit()
    finally:
        con.close()


def _rates(doc: dict) -> dict:
    """Per-route auto-accept rate and cost per accepted sheet (generation runs), or pass rate (analysis)."""
    routes: dict[str, dict] = {}
    for r in doc.get("results", []):
        route = r.get("route", "analysis")
        d = routes.setdefault(route, {"n": 0, "accepted": 0, "cost": 0.0})
        d["n"] += 1
        d["accepted"] += int(bool(r.get("accepted") if "accepted" in r else r.get("passed")))
        d["cost"] += float(r.get("cost", 0.0))
    return {k: {"accept_rate": v["accepted"] / max(1, v["n"]),
                "cost_per_accepted": v["cost"] / max(1, v["accepted"]) if v["accepted"] else float("inf")}
            for k, v in routes.items()}


def gate(baseline: dict, current: dict) -> dict:
    """Pass when, on every route, auto-accept drops by at most 2 points and cost per accepted
    sheet rises by at most 5%."""
    b, c = _rates(baseline), _rates(current)
    problems = []
    for route, cb in b.items():
        cc = c.get(route)
        if cc is None:
            problems.append(f"{route}: missing from current run")
            continue
        if cc["accept_rate"] < cb["accept_rate"] - 0.02:
            problems.append(f"{route}: accept rate {cb['accept_rate']:.3f} -> {cc['accept_rate']:.3f}")
        if cb["cost_per_accepted"] not in (0.0, float("inf")) and cc["cost_per_accepted"] > cb["cost_per_accepted"] * 1.05:
            problems.append(f"{route}: cost/accepted {cb['cost_per_accepted']:.4f} -> {cc['cost_per_accepted']:.4f}")
    return {"pass": not problems, "problems": problems, "baseline": b, "current": c}
