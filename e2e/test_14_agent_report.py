"""Issues found by an agent that used the CLI for a showcase (docs/failure-modes.md V6, M16, V7, K16,
K17): key halos on the video route, smoke tinted by the magenta reference, pale rig limbs, a repaired
effect frame in the wrong phase, and exhaust added to machines that have none."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
from PIL import Image

from conftest import ROOT, sk

GEN_NO_EXHAUST = "A brass drilling rig on an iron base with a big spiral drill bit and a glowing orange core."
GEN_SMOKE = "A brass mining drill machine with a big steel drill bit and a small smoking chimney."


def _job(proj: Path, anim: str) -> dict:
    return json.loads(sorted((proj / "animations" / anim / "jobs").glob("*/job.json"))[-1].read_text())


def _frames(proj: Path, anim: str) -> list[np.ndarray]:
    return [np.asarray(Image.open(p).convert("RGBA"))
            for p in sorted((proj / "animations" / anim / "final" / "frames").glob("*.png"))]


def _tinted(frames) -> int:
    from spriteguru.pipeline.matte import untint

    return sum(untint(f)[1] for f in frames)  # pixels that still look like grey seen through magenta


def test_machine_exhaust_and_tinted_smoke(rec, work):
    S = "agent-report"
    proj = work / "agent-report" / "M.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "phaser", "--mode", "synthetic")
    smoke = {"SPRITEGURU_SYNTH_DEFECTS": "turnaround=tintsmoke;c1=tintsmoke;c2=tintsmoke"}
    sk("character", "new", "rig", "--kind", "machine", "--describe", GEN_NO_EXHAUST, "--approve",
       "--project", str(proj), env=smoke)
    sk("character", "new", "drill", "--kind", "machine", "--describe", GEN_SMOKE, "--approve",
       "--project", str(proj), env=smoke)
    meta = json.loads((proj / "characters" / "drill" / "ref" / "turnaround-c1.meta.json").read_text())
    rec.check(S, "object turnarounds ask for no smoke or halos around the subject",
              "No smoke, steam, sparks, light halos" in json.dumps(meta), meta.get("version"), ["M16"])

    def motion_lines(job):
        text = job["steps"][0]["info"]["prompt"]["prompt"].lower()
        return " ".join(l for l in text.splitlines() if l.startswith("frame "))

    j = sk("gen", "rig", "work", "--project", str(proj), env=smoke)["json"]
    prompt = motion_lines(j)
    rec.check(S, "a machine whose description names no exhaust gets no steam or smoke in its motion",
              "steam" not in prompt and "smoke" not in prompt, None, ["K17"])
    j2 = sk("gen", "drill", "work", "--project", str(proj), env=smoke)["json"]
    prompt2 = motion_lines(j2)
    rec.check(S, "a machine with a smoking chimney gets smoke, in the description's word",
              "puff of smoke" in prompt2 and "steam" not in prompt2, None, ["K17"])

    # M16: the smoking drill's lilac smoke is untinted in the approved view and in every frame
    view = np.asarray(Image.open(proj / "characters" / "drill" / "ref" / "side-e.png").convert("RGBA"))
    drill = json.loads((proj / "characters" / "drill" / "character.json").read_text())
    from spriteguru.color import hex_to_rgb
    from spriteguru.pipeline.matte import tinted_grey

    rec.check(S, "smoke tinted by the magenta reference is neutral in the approved view",
              _tinted([view]) == 0 and not any(tinted_grey(hex_to_rgb(h)) for h in drill["palette"]),
              _tinted([view]), ["M16"])
    rec.check(S, "and in every frame of the animation", j2["result"]["accepted"] and _tinted(_frames(proj, "drill-work-E")) == 0,
              [j2["result"]["score"], _tinted(_frames(proj, "drill-work-E"))], ["M16"])
    # a subject without smoke in its description keeps its colours: the untint is scoped
    rec.check(S, "a subject without smoke in its description is left as drawn",
              _tinted(_frames(proj, "rig-work-E")) > 0, _tinted(_frames(proj, "rig-work-E")) > 0, ["M16"])
    rec.keep(S, proj / "animations" / "drill-work-E" / "final" / "sheet.png", "drill-work.png")


def test_effect_burst_in_fading_frame(rec, work):
    S = "agent-report"
    proj = work / "agent-report" / "E.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "phaser", "--mode", "synthetic")
    sk("character", "new", "blast", "--kind", "effect", "--blend", "add", "--describe",
       "A blue glowing energy explosion with sparks.", "--approve", "--project", str(proj))
    # the impact's fifth frame is the ring breaking into wisps: a fading phase, drawn as a full burst
    env = {"SPRITEGURU_SYNTH_DEFECTS": "c1=burst:4;c2=burst:4;judge=wrong_pose.2:4"}
    j = sk("gen", "blast", "impact", "--project", str(proj), env=env)["json"]
    first = next(c for c in j["candidates"] if c["id"] == "c1")
    rep = json.loads((proj / first["report"]).read_text())
    size = [f for f in rep["findings"] if f["metric"] == "effect_size"]
    judged = [f for f in rep["findings"] if f["source"] == "judge" and 4 in f["frames"]]
    rec.check(S, "a full burst in a fading frame is flagged by size, on that frame",
              bool(size) and 4 in size[0]["frames"], rep["temporal"].get("effect_size"), ["K16"])
    rec.check(S, "the judge's wrong-pose on the same frame is corroborated and becomes a failure",
              bool(judged) and judged[0]["corroborated"] and judged[0]["level"] == "fail",
              [(f["level"], f["corroborated"]) for f in judged], ["K16"])
    acts = [d["action"] for d in j["decisions"]]
    rec.check(S, "the burst frame is repaired and the repaired sheet wins",
              "repair" in acts and j["result"]["accepted"] and j["winner"] != "c1", [acts, j["winner"]], ["K16"])
    rep_c = [c for c in j["candidates"] if c["kind"] == "repair"]
    meta = json.loads((proj / rep_c[0]["meta"]).read_text()) if rep_c else {}
    rec.check(S, "the repair prompt states the frame's phase", "the frame shows:" in meta.get("prompt", ""),
              None, ["K16"])


def test_video_key_halo(rec, work):
    S = "agent-report"
    proj = work / "agent-report" / "V.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "phaser", "--mode", "synthetic")
    sk("character", "new", "robo", "--describe", "A friendly round white robot with a red scarf and a glowing cyan antenna.",
       "--approve", "--project", str(proj))
    j = sk("gen", "robo", "walk", "--project", str(proj), env={"SPRITEGURU_SYNTH_DEFECTS": "video=keyhalo"})["json"]
    rep = json.loads((proj / "animations" / "robo-walk-E" / "final" / "report.json").read_text())
    remn = (rep["temporal"].get("video") or {}).get("key_remnants")
    rec.check(S, "video route: a glow drawn over the key is unmixed with the approved palette",
              j["route"] == "video-loop" and remn is not None and remn < 0.003
              and not any(f["metric"] == "key_contamination" for f in rep["findings"]), remn, ["V6"])


def test_vector_rig_limbs(rec, work):
    S = "agent-report"
    from spriteguru.pipeline import motion

    fx = ROOT / "tests" / "fixtures" / "vector-rig"
    original, rig = (fx / "robot.original.svg").read_text(), (fx / "robot.rig.svg").read_text()
    fixed, notes = asyncio.run(motion.fix_limb_looks(rig, original))
    r = motion.load_rig(fixed)
    rec.check(S, "a pale rig leg takes the drawing's colour and the outlined leg gets its outline",
              any("leg_f recoloured #D2D2D2" in n for n in notes) and any("leg_n outlined" in n for n in notes), notes,
              ["V7"])
    rec.check(S, "the fixed rig is still a valid rig", motion.validate_rig(r) == [], motion.validate_rig(r), ["V7"])
    spec = {"loop": True, "root": {}, "parts": [
        {"id": "leg_n", "ease": "linear", "rotate": None, "attach": None, "swap": None, "morph": [
            {"t": 0.0, "d": r.rest_paths["leg_n"]},
            {"t": 0.5, "d": r.rest_paths["leg_n"].replace("451", "431")}]}]}
    from lxml import etree

    svg = motion.evaluate(spec, r, 0.5, True)
    group = next(el for el in etree.fromstring(svg.encode()).iter() if isinstance(el.tag, str) and el.get("id") == "leg_n")
    ds = {p.get("d") for p in group.iter() if isinstance(p.tag, str) and p.tag.endswith("path")}
    rec.check(S, "the leg and its outline copy move together", len(ds) == 1, len(ds), ["V7"])
