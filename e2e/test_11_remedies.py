"""Remedies beyond frame repair: a generated in-between inserted into a finished guided animation
(13.6), and judge calibration from hand labels switching off an imprecise issue type (14.2)."""

from __future__ import annotations

import json

import httpx

from conftest import free_port, sk
from test_06_jobs import _serve, _stop
from validate import check_final


def test_inbetween_and_judge_calibration(rec, work):
    S = "remedies"
    proj = work / "remedies" / "R.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "phaser", "--mode", "synthetic")
    sk("character", "new", "knight", "--describe", "An armoured knight with a blue tabard and a round shield.",
       "--approve", "--project", str(proj))
    sk("gen", "knight", "cast", "--project", str(proj))
    before = json.loads((proj / "animations" / "knight-cast-E" / "final" / "animation.json").read_text())
    r = sk("inbetween", "knight-cast-E", "--after", "3", "--project", str(proj))["json"]
    after = json.loads((proj / "animations" / "knight-cast-E" / "final" / "animation.json").read_text())
    rec.check(S, "in-between inserted and exported", after["frames"] == before["frames"] + 1 == r["frames"],
              [before["frames"], after["frames"]], ["T4"])
    v = check_final(proj / "animations" / "knight-cast-E" / "final", "phaser")
    rec.check(S, "export with the in-between agrees with its sheet", v["ok"], v["errors"], ["E2"])
    rec.keep(S, proj / "animations" / "knight-cast-E" / "final" / "sheet.png", "cast-with-inbetween.png")

    # judge calibration: label judge findings through the API, then calibrate from the CLI
    port = free_port()
    env = {"SPRITEGURU_SYNTH_DEFECTS": "judge=stray_marks:1"}
    srv = _serve(proj, port, env)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"X-SpriteKit-Token": "t0k"}, timeout=120) as c:
            c.post("/api/animations", json={"character": "knight", "action": "hurt"}).raise_for_status()
            job = c.post("/api/animations/knight-hurt-E/jobs", json={"seed": 5}).json()
            for _ in range(600):
                st = c.get(f"/api/jobs/{job['id']}").json()
                if st["state"] in ("done", "failed"):
                    break
                import time

                time.sleep(0.1)
            labeled = 0
            for cand in st["candidates"]:
                rep = c.get(f"/api/jobs/{job['id']}/candidates/{cand['id']}/report").json()
                for i, f in enumerate(rep["findings"]):
                    if f["source"] == "judge":
                        for _ in range(3):  # three reviewers agree the judge was wrong
                            c.post("/api/findings/label", json={"job": job["id"], "candidate": cand["id"],
                                                                "index": i, "correct": False}).raise_for_status()
                            labeled += 1
            rec.check(S, "judge findings labelled through the API", labeled >= 3, labeled)
    finally:
        _stop(srv)
    res = sk("eval", "judge", "--project", str(proj))["json"]
    rec.check(S, "imprecise judge issue type switched off", "stray_marks" in res["disabled"], res)
    cfg = json.loads((proj / "project.json").read_text())
    rec.check(S, "disabled types saved to project settings", "stray_marks" in cfg["settings"]["judge_disabled"])
