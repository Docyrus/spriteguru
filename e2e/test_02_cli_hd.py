"""HD cartoon project through the CLI (offline simulator): character lock, guided best-of-2 sheet,
video-loop walk, and every engine's export re-parsed against its sheet."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import sk
from validate import check_final

ENGINES = ["godot", "phaser", "pixi", "unity", "gamemaker"]


def test_hd_project(rec, work):
    S = "cli-hd"
    proj = work / "hd" / "Knight.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "godot", "--mode", "synthetic",
       "--asset-folder", str(work / "hd" / "game-assets"))
    (work / "hd" / "game-assets").mkdir(exist_ok=True)
    r = sk("character", "new", "knight", "--describe", "An armoured knight with a blue tabard and a round shield.",
           "--approve", "--project", str(proj))
    ch = r["json"]["character"]
    rec.check(S, "turnaround yields four views", sorted(ch["views"]) == ["back", "front", "side-e", "side-w"],
              sorted(ch["views"]))
    rec.check(S, "palette and proportions measured", len(ch["palette"]) >= 4 and (ch["heads_tall"] or 0) > 2,
              {"palette": len(ch["palette"]), "heads_tall": ch["heads_tall"]})
    rec.keep(S, proj / "characters" / "knight" / "ref" / "turnaround.png")

    # guided one-shot: best of 2
    r = sk("gen", "knight", "attack-melee", "--project", str(proj))
    st = r["json"]
    rec.check(S, "attack: route is the guided canvas edit", st["route"] == "guided", st["route"])
    rec.check(S, "attack: two candidates analyzed (best of 2)", sum(1 for c in st["candidates"] if c["round"] == 0) == 2,
              [c["id"] for c in st["candidates"]])
    rec.check(S, "attack: winner accepted (Q >= 85, no fails)", st["result"]["accepted"], st["result"]["score"])
    alt = [c for c in st["candidates"] if c["id"] != st["winner"]]
    rec.check(S, "attack: the flawed alternate scores lower", all(c["score"] < st["result"]["score"] for c in alt),
              {c["id"]: c["score"] for c in st["candidates"]})
    jdir = proj / st["dir"]
    for f in ("guide.png", "mask.png", "prompt.txt", "guide.json", "compile.json"):
        rec.check(S, f"attack: job keeps {f}", (jdir / f).is_file())
    prompt = (jdir / "prompt.txt").read_text()
    rec.check(S, "attack: master template blocks in order",
              [prompt.find(b) for b in ("LAYOUT", "CHARACTER", "VIEW", "MOTION", "STYLE", "BACKGROUND")] ==
              sorted(prompt.find(b) for b in ("LAYOUT", "CHARACTER", "VIEW", "MOTION", "STYLE", "BACKGROUND")))
    meta = json.loads((jdir / "candidates" / "c1.meta.json").read_text())
    rec.check(S, "attack: provenance sidecar", all(k in meta for k in ("template", "version", "prompt", "model",
                                                                        "params", "request_id", "cost")),
              sorted(meta)[:12])
    rec.keep(S, jdir / "guide.png", "attack-guide.png")
    rec.keep(S, jdir / "candidates" / "c1.png", "attack-c1.png")
    final = proj / "animations" / "knight-attack-melee-E" / "final"
    rec.keep(S, final / "sheet.png", "attack-sheet.png")
    rec.keep(S, final / "preview.gif", "attack-preview.gif", digest=False)
    rec.keep(S, final / "report.html", "attack-report.html", digest=False)
    rec.check(S, "attack: one-shot timing_ms carried to export",
              json.loads((final / "animation.json").read_text())["durations"] == [120, 120, 80, 60, 100, 120])
    rec.check(S, "attack: exported copy lands in the game asset folder",
              (work / "hd" / "game-assets" / "knight-attack-melee-E" / "sheet.png").is_file(), covers=["E2"])

    # west-facing mirrorable job is the flipped east job
    r = sk("gen", "knight", "attack-melee", "--facing", "W", "--project", str(proj))
    east = json.loads((final / "animation.json").read_text())
    west = json.loads((proj / "animations" / "knight-attack-melee-W" / "final" / "animation.json").read_text())
    rec.check(S, "west: pivot mirrored from east", abs(west["pivot"][0] - (1 - east["pivot"][0])) < 1e-6,
              [east["pivot"][0], west["pivot"][0]])

    # every engine format re-parsed against the sheet
    for eng in ENGINES:
        sk("export", "knight-attack-melee-E", "--engine", eng, "--project", str(proj))
        v = check_final(final, eng)
        rec.check(S, f"export {eng}: metadata agrees with the sheet", v["ok"], v["errors"] or v["size"], ["E1", "E2"])
    led = sk("ledger", "--project", str(proj))["json"]
    rec.check(S, "simulator spends nothing", led["total_usd"] == 0.0, led["total_usd"])
    rec.note(S, ledger=led)


def test_hd_walk_video(rec, work):
    S = "cli-hd"
    proj = work / "hd-walk" / "Knight.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "godot", "--mode", "synthetic")
    sk("character", "new", "knight", "--describe", "An armoured knight with a blue tabard and a round shield.",
       "--approve", "--project", str(proj))
    # looping HD walk: video-to-sprite
    r = sk("gen", "knight", "walk", "--project", str(proj))
    st = r["json"]
    rec.check(S, "walk: route is video-to-sprite", st["route"] == "video-loop", st["route"])
    rec.check(S, "walk: accepted", st["result"]["accepted"], st["result"]["score"])
    rep = json.loads((proj / "animations" / "knight-walk-E" / "final" / "report.json").read_text())
    v = rep["temporal"].get("video", {})
    rec.check(S, "walk: loop period found near 1 s", abs(v.get("period_s", 0) - 1.0) < 0.1, v.get("period_s"),
              ["V1"])
    rec.check(S, "walk: seam ratio <= 1.5", (rep["temporal"]["seam_ratio"] or 9) <= 1.5, rep["temporal"]["seam_ratio"],
              ["T5"])
    rec.check(S, "walk: gait has two steps per loop", rep["temporal"]["gait"].get("k_spread") == 2,
              rep["temporal"]["gait"].get("k_spread"), ["T6"])
    wfinal = proj / "animations" / "knight-walk-E" / "final"
    rec.keep(S, wfinal / "sheet.png", "walk-sheet.png")
    rec.keep(S, wfinal / "preview.gif", "walk-preview.gif", digest=False)
