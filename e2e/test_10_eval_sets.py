"""Evaluation harness (16): the generation golden set per route with its regression gate, and the
pixel reconstruction benchmark against unfake (D5 exit criterion)."""

from __future__ import annotations

import copy
import json
import shutil

import pytest

from conftest import sk


@pytest.mark.full
def test_generation_set(rec, work):
    S = "eval-generation"
    r = sk("eval", "gen", "--root", str(work / "genset"), "--out", str(work / "genset-out"), "--workers", "4",
           timeout=5400)["json"]
    routes = r["summary"]["routes"]
    want = {"video-loop", "vector-idle", "rd-loop", "guided", "guided-pixel", "vector-motion"}
    rec.check(S, "every route in 6.5 exercised", want <= set(routes), sorted(routes))
    for route, v in sorted(routes.items()):
        rec.check(S, f"{route}: auto-accept rate", v["auto_accept_rate"] >= 0.5, v["auto_accept_rate"])
    base = json.loads((work / "genset-out" / "results.json").read_text())
    rows = base["results"]

    def accepted(pred):
        sel = [x for x in rows if pred(x["case"])]
        return all(x["accepted"] for x in sel) and bool(sel), {x["case"]: x["score"] for x in sel}

    for name, pred, covers in [("idle loops (subtle motion)", lambda c: "-idle-" in c, ["T8"]),
                               ("jumps (tucked poses)", lambda c: "-jump-" in c, ["Q1", "R4"]),
                               ("deaths (lying poses)", lambda c: "-death-" in c, ["R11"]),
                               ("top-down front/back walks", lambda c: c.endswith(("-walk-S", "-walk-N")), ["V4"])]:
        ok, val = accepted(pred)
        rec.check(S, f"all {name} accepted", ok, val, covers)
    rec.check(S, "all 60 specs generated", len(rows) == 60 and all(x["state"] == "done" for x in rows), len(rows))
    # the gate run: the first 12 specs again, gated against the same 12 from the full run
    base12 = copy.deepcopy(base)
    base12["results"] = rows[:12]
    (work / "genset-base12.json").write_text(json.dumps(base12))
    again = sk("eval", "gen", "--limit", "12", "--root", str(work / "genset3"), "--out", str(work / "genset3-out"),
               "--gate", str(work / "genset-base12.json"), "--workers", "4", timeout=3600, check=False)
    rec.check(S, "same engine passes the per-route gate", again["code"] == 0 and again["json"]["gate"]["pass"],
              again["json"]["gate"]["problems"] if again["json"] else again["stderr"][-300:])
    sub = json.loads((work / "genset3-out" / "results.json").read_text())["results"]
    strip = lambda r: {k: v for k, v in r.items() if k != "latency_s"}  # noqa: E731
    rec.check(S, "generation runs are deterministic offline (a separate run reproduces every row)",
              [strip(r) for r in sub] == [strip(r) for r in rows[:12]], len(sub))
    from spriteguru.eval.store import gate

    worse = copy.deepcopy(base)
    for row in worse["results"]:
        if row["route"] == "guided":
            row["accepted"] = row["passed"] = False
    v = gate(base, worse)
    rec.check(S, "an accept-rate drop on one route fails the gate", not v["pass"], v["problems"])
    rec.note(S, routes=routes)


@pytest.mark.full
def test_pixel_benchmark(rec, work):
    S = "pixel-benchmark"
    if shutil.which("uvx") is None:
        pytest.skip("uvx is needed to run unfake in isolation")
    r = sk("eval", "pixel-bench", "--out", str(work / "pixel-bench"), timeout=3600)["json"]
    doc = json.loads((work / "pixel-bench" / "results.json").read_text())
    # unfake is not deterministic run to run, so only our own score enters the digest
    rec.check(S, "beats unfake on exact logical-pixel match (D5)", r["beats_unfake"], {"ours": r["ours"]},
              ["X1", "X3", "X4", "X5", "X6", "X7"])
    rec.note(S, unfake=r["unfake"])
    for row in doc["results"]:
        if row["case"] != "pitch9.3-heavy":
            rec.check(S, f"{row['case']}: >= 0.95 exact match", row["ours"] >= 0.95, row["ours"])
    rec.note(S, results=[{k: v for k, v in row.items() if not k.endswith("_frames")} for row in doc["results"]])
