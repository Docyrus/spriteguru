"""Live scenarios against the real providers (opt-in: `pytest e2e --live`; spends roughly $1–2).

Every route that has a key runs once through the CLI. Checks assert that the pipeline worked and
the outputs are valid; model-dependent values (scores, periods) are recorded as unstable so they
never enter the digest.
"""

from __future__ import annotations

import json

import pytest

from conftest import sk
from validate import check_final

pytestmark = pytest.mark.live


def _ledger(proj) -> dict:
    return sk("ledger", "--project", str(proj))["json"]


def test_live_hd(rec, work):
    S = "live-hd"
    from spriteguru import keys

    if not (keys.get("openai") and keys.get("fal")):
        pytest.skip("needs OpenAI and fal keys")
    proj = work / "live-hd" / "Knight.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "godot", "--mode", "live")
    ch = sk("character", "new", "knight", "--describe", "An armoured knight with a blue tabard, a round wooden "
            "shield on the left arm and a short sword on the belt.", "--approve", "--yes", "--project", str(proj),
            timeout=1200)["json"]["character"]
    rec.check(S, "GPT Image 2.5 turnaround cropped into four views",
              sorted(ch["views"]) == ["back", "front", "side-e", "side-w"], sorted(ch["views"]))
    rec.keep(S, proj / "characters" / "knight" / "ref" / "turnaround.png", digest=False)
    st = sk("gen", "knight", "attack-melee", "--yes", "--project", str(proj), timeout=2400)["json"]
    rec.check(S, "guided sheet: best of 2 from GPT Image 2.5 flare",
              sum(1 for c in st["candidates"] if c["round"] == 0) == 2, [c["id"] for c in st["candidates"]])
    meta = json.loads((proj / st["candidates"][0]["meta"]).read_text())
    rec.check(S, "provenance carries the provider request id and reported cost",
              bool(meta.get("request_id")) and meta.get("cost", 0) > 0, {"cost": meta.get("cost")})
    rec.check(S, "attack exported", st["state"] == "done", st["result"].get("score"), stable=False)
    final = proj / "animations" / "knight-attack-melee-E" / "final"
    v = check_final(final, "godot")
    rec.check(S, "attack export agrees with its sheet", v["ok"], v["errors"], ["E2"])
    rec.keep(S, proj / st["dir"] / "candidates" / "c1.png", "attack-c1.png", digest=False)
    rec.keep(S, final / "sheet.png", "attack-sheet.png", digest=False)
    rec.keep(S, final / "preview.gif", "attack-preview.gif", digest=False)
    rec.keep(S, final / "report.html", "attack-report.html", digest=False)
    r = sk("repair", "knight-attack-melee-E", "--frame", "3", "--kind", "pose", "--yes", "--project", str(proj),
           timeout=1200)["json"]
    rec.check(S, "masked frame repair on GPT Image 2.5 sunburst", "export" in r,
              {"before": r["before"], "after": r["after"], "kept": r["kept"]}, stable=False)
    st = sk("gen", "knight", "walk", "--yes", "--project", str(proj), timeout=2400)["json"]
    rec.check(S, "video loop on MiniMax H3 Max cut into frames", st["route"] == "video-loop" and st["state"] == "done",
              st["result"].get("score"), stable=False)
    rep = json.loads((proj / "animations" / "knight-walk-E" / "final" / "report.json").read_text())
    rec.note(S, walk_video=rep["temporal"].get("video"), walk_score=rep["score"],
             walk_findings=[f["message"] for f in rep["findings"]])
    rec.keep(S, proj / "animations" / "knight-walk-E" / "final" / "sheet.png", "walk-sheet.png", digest=False)
    rec.keep(S, proj / "animations" / "knight-walk-E" / "final" / "preview.gif", "walk-preview.gif", digest=False)
    led = _ledger(proj)
    rec.check(S, "judge ran on GPT-6 Luna", led["by_model"].get("gpt-6-luna", 0) > 0, led["by_model"], stable=False)
    rec.check(S, "no unknown outcomes in the ledger", led["unknown_outcomes"] == 0, led["unknown_outcomes"])
    rec.note(S, ledger=led)


def test_live_vector(rec, work):
    S = "live-vector"
    from spriteguru import keys

    if not (keys.get("openai") and keys.get("quiver")):
        pytest.skip("needs OpenAI and Quiver keys")
    proj = work / "live-vector" / "Robo.sprites"
    sk("init", str(proj), "--style", "vector", "--engine", "phaser", "--mode", "live")
    sk("character", "new", "robo", "--describe", "A friendly round robot with a red scarf, stubby arms and legs.",
       "--approve", "--yes", "--project", str(proj), timeout=1200)
    st = sk("gen", "robo", "idle", "--yes", "--project", str(proj), timeout=3000)["json"]
    rec.check(S, "Quiver Arrow 2 idle loop sampled in Chromium", st["state"] == "done" and st["route"] == "vector-idle",
              st.get("error") or st["result"].get("score"), stable=False)
    if st["state"] == "done":
        rec.keep(S, proj / "animations" / "robo-idle-E" / "final" / "sheet.png", "idle-sheet.png", digest=False)
    st = sk("gen", "robo", "walk", "--yes", "--project", str(proj), timeout=3000)["json"]
    rec.check(S, "GPT-6 Sol prepared the rig and authored a walk", st["state"] == "done",
              st.get("error") or st["result"].get("score"), stable=False)
    if st["state"] == "done":
        rec.keep(S, proj / "animations" / "robo-walk-E" / "final" / "sheet.png", "walk-sheet.png", digest=False)
        rep = json.loads((proj / "animations" / "robo-walk-E" / "final" / "report.json").read_text())
        rec.note(S, motion=rep["temporal"].get("motion"), walk_findings=[f["message"] for f in rep["findings"]])
    rec.note(S, ledger=_ledger(proj))
