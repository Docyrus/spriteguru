"""Regression gate (16.3): a change passes when, on every route, the auto-accept rate drops by at
most 2 points and cost per accepted sheet rises by at most 5%."""

from __future__ import annotations

import copy
import json

from conftest import ROOT, sk


def test_gate(rec, work, run_dir):
    S = "eval-gate"
    out = work / "gate" / "a"
    sk("eval", "run", str(ROOT / "tests" / "golden"), "--out", str(out), "--case", "clean-walk8", "--case",
       "gradient-noise", "--case", "shuffled")
    base = json.loads((out / "results.json").read_text())
    r = sk("eval", "run", str(ROOT / "tests" / "golden"), "--out", str(work / "gate" / "b"), "--case", "clean-walk8",
           "--case", "gradient-noise", "--case", "shuffled", "--gate", str(out / "results.json"), check=False)
    rec.check(S, "unchanged analyzer passes the gate", r["code"] == 0 and r["json"]["gate"]["pass"], r["json"]["gate"])
    rec.check(S, "replayed analysis is deterministic", r["json"]["digest"] == base["digest"], r["json"]["digest"][:16])
    worse = copy.deepcopy(base)
    worse["results"][0]["passed"] = False
    worse["results"][0]["accepted"] = False
    (work / "gate" / "worse.json").write_text(json.dumps(worse))
    from spriteguru.eval.store import gate

    v = gate(base, worse)
    rec.check(S, "a regression fails the gate", not v["pass"], v["problems"])
    import sqlite3

    con = sqlite3.connect(work / "gate" / "b" / "eval.sqlite")
    n = con.execute("select count(*) from cases").fetchone()[0]
    rec.check(S, "results stored in eval.sqlite", n == 3, n)
