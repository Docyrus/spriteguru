"""The repair loop (14.4) with injected defects: key contamination forces a re-roll on the next key,
identity drift gets one masked frame repair, and the job ends accepted."""

from __future__ import annotations

import json

from conftest import sk


def test_repair_loop(rec, work):
    S = "repair-loop"
    proj = work / "repair" / "R.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--mode", "synthetic")
    sk("character", "new", "knight", "--describe", "An armoured knight with a blue tabard and a round shield.",
       "--approve", "--project", str(proj))
    env = {"SPRITEGURU_SYNTH_DEFECTS": "c1=tint:2;c2=drop:4;repair=none"}
    st = sk("gen", "knight", "attack-melee", "--seed", "7", "--project", str(proj), env=env)["json"]
    acts = [d["action"] for d in st["decisions"]]
    rec.check(S, "decision sequence", acts == ["reroll", "repair", "accept"], acts)
    first = st["decisions"][0]
    rec.check(S, "contamination re-roll switches the chroma key", bool(first["key_exclude"]) and
              st["candidates"][-1]["key"] not in first["key_exclude"], first["key_exclude"], ["M9"])
    dropped = next(c for c in st["candidates"] if c["id"] == "c2")
    rec.check(S, "dropped frame caught as a frame-count failure",
              any(t["metric"] == "frame_count" for t in dropped["top"]), [t["metric"] for t in dropped["top"]], ["L8"])
    rep = next(d for d in st["decisions"] if d["action"] == "repair")
    rec.check(S, "identity drift repaired on the right frame", rep["frames"] == [2] and
              rep["finding"]["metric"] == "identity", rep["frames"])
    rec.check(S, "repaired sheet accepted", st["result"]["accepted"] and st["result"]["score"] >= 85,
              st["result"]["score"])
    jdir = proj / st["dir"]
    repairs = sorted((jdir / "repairs").glob("*.png"))
    rec.check(S, "repair output kept", len(repairs) == 1, [p.name for p in repairs])
    for p in repairs:
        rec.keep(S, p)
    rec.keep(S, jdir / "candidates" / "r1-c1.png", "before-repair.png")
    rec.keep(S, proj / "animations" / "knight-attack-melee-E" / "final" / "sheet.png", "after-repair-sheet.png")
    # a manual one-frame repair through the CLI re-analyzes and keeps the better result
    r = sk("repair", "knight-attack-melee-E", "--frame", "4", "--kind", "pose", "--project", str(proj))["json"]
    rec.check(S, "manual repair re-analyzed and exported", r["export"]["files"] and r["after"] >= 0,
              {"before": r["before"], "after": r["after"], "kept": r["kept"]})


def test_black_repair_retry(rec, work):
    S = "repair-loop"
    # a repair whose masked cell comes back as a black block (seen live) is retried without the mask
    proj2 = work / "repair" / "R2.sprites"
    sk("init", str(proj2), "--style", "hd-cartoon", "--mode", "synthetic")
    sk("character", "new", "knight", "--describe", "An armoured knight with a blue tabard and a round shield.",
       "--approve", "--project", str(proj2))
    st2 = sk("gen", "knight", "attack-melee", "--seed", "8", "--project", str(proj2),
             env={"SPRITEGURU_SYNTH_DEFECTS": "c1=wrongpose:2;c2=drop:4;repair=black"})["json"]
    rep_c = [c for c in st2["candidates"] if c["kind"] == "repair"]
    meta = json.loads((proj2 / rep_c[0]["meta"]).read_text()) if rep_c else {}
    rec.check(S, "black masked repair detected and retried without the mask",
              meta.get("retried_without_mask") is True and st2["result"]["accepted"],
              {"retried": meta.get("retried_without_mask"), "score": st2["result"]["score"]})
