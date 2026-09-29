"""Analyzer golden set through the CLI: fixtures regenerate byte-identically, every case passes,
and every failure mode in docs/failure-modes.md for sections 9–13 is exercised."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from conftest import ROOT, sk

ANALYZER_MODES = ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M11", "L1", "L2", "L3", "L4", "L5", "L6",
                  "L7", "L8", "L10", "L11", "L12", "R1", "R2", "R3", "R4", "R6", "R7", "R8", "R9", "X1", "X2", "X3",
                  "X4", "X5", "X6", "X7", "X8", "T1", "T2", "T3", "T4", "T5", "T6", "T7"]


def _tree_hash(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file()}


def test_golden_set(rec, work, run_dir):
    S = "golden"
    regen = work / "golden"
    sk("eval", "fixtures", "--out", str(regen))
    committed, fresh = _tree_hash(ROOT / "tests" / "golden"), _tree_hash(regen)
    same = committed == fresh
    rec.check(S, "fixtures regenerate byte-identically", same,
              None if same else sorted(set(committed.items()) ^ set(fresh.items()))[:6])
    out = run_dir / "outputs" / "golden"
    r = sk("eval", "run", str(regen), "--out", str(out), "--workers", "4",
           env={"SPRITEGURU_MATTE_MODEL": "birefnet-general-lite"},
           timeout=3600)
    doc = json.loads((out / "results.json").read_text())
    s = doc["summary"]
    rec.check(S, "all golden cases pass", s["passed"] == s["cases"], f"{s['passed']}/{s['cases']}",
              covers=s["failure_modes_passing"])
    rec.check(S, "frame IoU mean >= 0.9 (D1 exit)", (s["frame_iou_mean"] or 0) >= 0.9, s["frame_iou_mean"], ["L12"])
    rec.check(S, "frame IoU min >= 0.9", (s["frame_iou_min"] or 0) >= 0.9, s["frame_iou_min"])
    missing = [m for m in ANALYZER_MODES if m not in s["failure_modes_passing"]]
    rec.check(S, "every analyzer failure mode has a passing case", not missing, missing)
    for res in doc["results"]:
        rec.check(S, f"case {res['case']}", res["passed"],
                  [c["check"] for c in res["checks"] if not c["pass"]] or res["score"], covers=res["covers"])
    rec.check(S, "golden results digest", True, doc["digest"])
    rec.files.append({"scenario": S, "path": "outputs/golden/index.html", "sha256": None, "in_digest": False})
    assert s["passed"] == s["cases"]
