"""Live subject kinds and image references (opt-in: `pytest e2e --live`; roughly $0.45).

A tank and an additive fireball through GPT Image 2.5, and a character turnaround drawn from an
uploaded image and described by GPT-6 Luna. Model-dependent values are recorded as unstable.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from conftest import sk
from validate import check_final

pytestmark = pytest.mark.live


def _frames(final):
    return [np.asarray(Image.open(p).convert("RGBA")) for p in sorted((final / "frames").glob("*.png"))]


def _key_remnants(frames, key=(255, 0, 255)) -> int:
    """Visible key-coloured pixels left in exported frames (M14 gaps, M15 smoke)."""
    n = 0
    for f in frames:
        c = f[..., :3].astype(int)
        near = (np.abs(c - np.array(key)).sum(-1) < 120) & (c[..., 1] < 90) & (f[..., 3] > 128)
        n += int(near.sum())
    return n


def test_live_subjects(rec, work):
    S = "live-subjects"
    from spriteguru import keys

    if not keys.get("openai"):
        pytest.skip("needs an OpenAI key")
    proj = work / "live-subjects" / "L.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "phaser", "--mode", "live")

    tank = sk("character", "new", "tank", "--kind", "vehicle", "--describe",
              "An olive green battle tank with a long cannon and wide treads.", "--approve", "--yes",
              "--project", str(proj), timeout=1200)["json"]["character"]
    rec.check(S, "vehicle turnaround cut into four clean views", sorted(tank["views"]) ==
              ["back", "front", "side-e", "side-w"] and not tank["view_warnings"],
              [sorted(tank["views"]), tank["view_warnings"]], ["T9"], stable=False)
    rec.keep(S, proj / "characters" / "tank" / "ref" / "turnaround.png", "tank-turnaround.png", digest=False)
    st = sk("gen", "tank", "fire", "--yes", "--project", str(proj), timeout=2400)["json"]
    comp = st["steps"][0]["info"]
    rec.check(S, "tank fire: object sheet from the rigid guide", comp["prompt"]["template"] == "guided_object"
              and st["route"] == "guided", comp["prompt"]["template"], ["K1", "K6"])
    rec.check(S, "tank fire: score", st["result"]["accepted"], st["result"]["score"], ["K3", "K14"], stable=False)
    fin = proj / "animations" / "tank-fire-E" / "final"
    v = check_final(fin, "phaser")
    rec.check(S, "tank fire export agrees with its sheet", v["ok"], v["errors"], ["E2"])
    key = comp["key"]
    left = _key_remnants(_frames(fin), tuple(int(key[i:i + 2], 16) for i in (1, 3, 5)))
    rec.check(S, "tank fire: no key colour left in gaps or smoke", left < 60, left, ["M14", "M15"], stable=False)
    rec.keep(S, fin / "sheet.png", "tank-fire.png", digest=False)

    sk("character", "new", "fireball", "--kind", "effect", "--blend", "add", "--describe",
       "A blue glowing energy ball with a white-hot core and a streaming tail, like a fighting-game energy wave.",
       "--approve", "--yes", "--project", str(proj), timeout=1200)
    st = sk("gen", "fireball", "projectile", "--yes", "--project", str(proj), timeout=2400)["json"]
    fin = proj / "animations" / "fireball-projectile-E" / "final"
    anim = json.loads((fin / "animation.json").read_text())
    rec.check(S, "fireball: drawn on black, matted by luminance, exported additive",
              st["steps"][0]["info"]["key"] == "#000000" and anim["blend"] == "add", anim["blend"], ["K4", "K8"])
    rec.check(S, "fireball: score", st["result"]["accepted"], st["result"]["score"], ["K2"], stable=False)
    rec.keep(S, fin / "sheet.png", "fireball-projectile.png", digest=False)

    # a crude offline mannequin as the "sketch"; the model draws a real character from it
    sketch_proj = work / "live-subjects" / "sketch" / "S.sprites"
    sk("init", str(sketch_proj), "--style", "hd-cartoon", "--mode", "synthetic")
    sk("character", "new", "sketch", "--describe", "A young knight in blue armor with a red cape.",
       "--approve", "--project", str(sketch_proj))
    sketch = sketch_proj / "characters" / "sketch" / "ref" / "side-e.png"
    ch = sk("character", "new", "fromsketch", "--image", str(sketch), "--approve", "--yes", "--project", str(proj),
            timeout=1200)["json"]["character"]
    rec.check(S, "image reference: GPT-6 Luna describes it and the turnaround follows it",
              ch["description_source"] == "image" and len(ch["description"]) > 20 and len(ch["views"]) == 4,
              ch["description"][:160], ["I5"], stable=False)
    rec.keep(S, sketch, "sketch.png", digest=False)
    rec.keep(S, proj / "characters" / "fromsketch" / "ref" / "turnaround.png", "fromsketch-turnaround.png",
             digest=False)
    led = sk("ledger", "--project", str(proj))["json"]
    rec.note(S, spend_usd=led.get("session_usd"))
    rec.check(S, "spend stays small", (led.get("session_usd") or 0) < 1.5, led.get("session_usd"), stable=False)
