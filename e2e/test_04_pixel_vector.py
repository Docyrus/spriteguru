"""Pixel and vector projects through the CLI (offline): guided pixel sheet with lattice recovery,
Retro Diffusion loop, Quiver idle loop, AI-coded rubberhose walk, and the SVG sanitizer."""

from __future__ import annotations

import json

from conftest import sk
from validate import check_final


def test_pixel_project(rec, work):
    S = "pixel"
    proj = work / "pixel" / "Px.sprites"
    sk("init", str(proj), "--style", "pixel", "--pixel-height", "48", "--engine", "gamemaker", "--mode", "synthetic")
    sk("character", "new", "hero", "--describe", "A small adventurer in a green hood carrying a sword.", "--approve",
       "--project", str(proj))
    st = sk("gen", "hero", "jump", "--seed", "11", "--project", str(proj))["json"]
    rec.check(S, "jump: guided pixel route", st["route"] == "guided-pixel", st["route"])
    rep = json.loads((proj / "animations" / "hero-jump-E" / "final" / "report.json").read_text())
    px = rep["pixel"]
    rec.check(S, "jump: lattice recovered (pitch CV <= 10%)", px["pitch_cv"] <= 0.10, px["pitch_cv"], ["X1", "X3", "X4"])
    rec.check(S, "jump: joint palette within the palette size", px["colors_after"] <= 16,
              f"{px['colors_before']} -> {px['colors_after']}", ["X7"])
    rec.check(S, "jump: accepted", st["result"]["accepted"], st["result"]["score"])
    air = [f["airborne"] for f in rep["frames"]]
    rec.check(S, "jump: airborne frames keep their lift", air == [False, False, True, True, True, False] and
              max(rep["temporal"]["registration"]["lift"][2:5]) > 5, rep["temporal"]["registration"]["lift"], ["R4"])
    final = proj / "animations" / "hero-jump-E" / "final"
    v = check_final(final, "gamemaker")
    rec.check(S, "jump: GameMaker strip, 4x nearest copy, formats agree", v["ok"], v["errors"], ["E2", "E3", "E4"])
    rec.keep(S, final / "sheet@4x.png", "jump-sheet@4x.png")
    st = sk("gen", "hero", "walk", "--seed", "3", "--project", str(proj))["json"]
    rec.check(S, "walk: Retro Diffusion loop route (simulated)", st["route"] == "rd-loop", st["route"])
    rep = json.loads((proj / "animations" / "hero-walk-E" / "final" / "report.json").read_text())
    rec.check(S, "walk: native-resolution frames kept at pitch 1", rep["pixel"]["pitch"] == 1.0, rep["pixel"]["pitch"],
              ["M8"])
    rec.check(S, "walk: accepted", st["result"]["accepted"], st["result"]["score"])
    rec.keep(S, proj / "animations" / "hero-walk-E" / "final" / "sheet@4x.png", "walk-sheet@4x.png")


def test_vector_project(rec, work):
    S = "vector"
    proj = work / "vector" / "Vec.sprites"
    sk("init", str(proj), "--style", "vector", "--engine", "phaser", "--mode", "synthetic")
    sk("character", "new", "robo", "--describe", "A friendly round robot with a red scarf.", "--approve",
       "--project", str(proj))
    st = sk("gen", "robo", "idle", "--project", str(proj))["json"]
    rec.check(S, "idle: Quiver micro-animation route", st["route"] == "vector-idle", st["route"])
    rec.check(S, "idle: accepted", st["result"]["accepted"], st["result"]["score"])
    rep = json.loads((proj / "animations" / "robo-idle-E" / "final" / "report.json").read_text())
    vec = rep["temporal"].get("vector", {})
    rec.check(S, "idle: sampler skipped the opening and used the loop period",
              vec.get("opening_ms") == 400 and vec.get("period_ms") == 2000, vec, ["S4"])
    rec.check(S, "idle: head travel stays subtle", rep["temporal"]["checks"].get("idle_head_travel", 1) <= 0.03,
              rep["temporal"]["checks"].get("idle_head_travel"), ["T7"])
    st = sk("gen", "robo", "walk", "--project", str(proj))["json"]
    rec.check(S, "walk: AI-coded motion route", st["route"] == "vector-motion", st["route"])
    rec.check(S, "walk: accepted", st["result"]["accepted"], st["result"]["score"])
    rep = json.loads((proj / "animations" / "robo-walk-E" / "final" / "report.json").read_text())
    rec.check(S, "walk: rubberhose gait has two steps", rep["temporal"]["gait"].get("k_spread") == 2,
              rep["temporal"]["gait"].get("k_spread"))
    rec.check(S, "walk: loop closes (seam <= 1.5)", (rep["temporal"]["seam_ratio"] or 9) <= 1.5,
              rep["temporal"]["seam_ratio"], ["S7"])
    svg = (proj / "characters" / "robo" / "ref" / "character.svg").read_text()
    rec.check(S, "character SVG carries every rig group",
              all(f'id="{p}"' in svg for p in ("arm_f", "hand_f", "leg_f", "foot_f", "torso", "head", "leg_n",
                                               "foot_n", "arm_n", "hand_n")), covers=["S8"])
    for n in ("robo-idle-E", "robo-walk-E"):
        rec.keep(S, proj / "animations" / n / "final" / "sheet.png", f"{n}-sheet.png")
        rec.keep(S, proj / "animations" / n / "final" / "preview.gif", f"{n}.gif", digest=False)


def test_sanitizer_and_motion_validator(rec):
    """Isolated checks of the SVG sanitizer and motion-spec validator against docs/failure-modes.md S1–S9."""
    from spriteguru.pipeline import motion, vector

    S = "vector-safety"
    evil = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 64 64">'
            '<script>alert(1)</script><g id="torso" onclick="steal()"><rect width="10" height="10" '
            'style="fill:url(http://evil/x.png)"/></g><foreignObject><div>x</div></foreignObject>'
            '<use xlink:href="http://evil/sprite.svg#a"/><use href="#torso"/><image href="data:image/png;base64,AAAA"/>'
            '<style>@import url(http://evil/a.css); rect{fill:url(#ok)}</style></svg>')
    san = vector.sanitize(evil, 128, 128)
    out = san.svg
    rec.check(S, "script, handlers, foreignObject, external hrefs removed",
              "<script" not in out and "onclick" not in out and "foreignObject" not in out and "http://evil" not in out
              and 'href="#torso"' in out, san.removed, ["S1"])
    rec.check(S, "root width/height forced to the frame size", 'width="128"' in out and 'height="128"' in out,
              covers=["S3"])
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
            '<svg xmlns="http://www.w3.org/2000/svg">&lol2;</svg>')
    try:
        vector.sanitize(bomb, 64, 64)
        rejected = False
    except Exception:
        rejected = True
    rec.check(S, "entity expansion rejected by defusedxml", rejected, covers=["S2"])
    from spriteguru.figure import character_skin

    svg = vector.synthetic_character_svg(character_skin(1), 256, 256)
    rig = motion.load_rig(svg)
    rec.check(S, "synthetic rig validates", motion.validate_rig(rig) == [], motion.validate_rig(rig), ["S8"])
    bad_rig = motion.load_rig(svg.replace('id="leg_n"', 'id="leg_x"'))
    rec.check(S, "missing rubberhose group rejected", any("leg_n" in e for e in motion.validate_rig(bad_rig)),
              motion.validate_rig(bad_rig), ["S8"])
    d0 = rig.rest_paths["leg_f"]
    spec = {"parts": [{"id": "leg_f", "ease": "linear", "rotate": None, "attach": None, "swap": None,
                       "morph": [{"t": 0.0, "d": d0}, {"t": 0.5, "d": "M 1,2 L 3,4"}]}],
            "root": {"x": [], "y": [], "ease": "linear"}}
    errs = motion.validate_spec(spec, rig, loop=True)
    rec.check(S, "morph keys with different command structures rejected", any("command structure" in e for e in errs),
              errs, ["S6"])
    spec["parts"][0]["morph"] = [{"t": 0.0, "d": d0}, {"t": 1.0, "d": d0.replace("M ", "M 1")}]
    errs = motion.validate_spec(spec, rig, loop=True)
    rec.check(S, "loop pose at t=0 differing from t=1 rejected", any("t = 0" in e for e in errs), errs, ["S7"])
    spec["parts"] = [{"id": "hand_n", "ease": "linear", "morph": None, "rotate": None, "swap": None,
                      "attach": {"to": "head", "at": "end", "align": "tangent"}}]
    errs = motion.validate_spec(spec, rig, loop=True)
    rec.check(S, "attachment to a non-path host rejected", any("not a path" in e for e in errs), errs, ["S9"])
