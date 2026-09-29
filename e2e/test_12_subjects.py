"""Subjects beyond characters and image references (docs/failure-modes.md, I1–I8, K1–K13, T9, Q2,
P12, R12): a tank, a machine and an additive fireball through the guided route in every style, a
character's fireball throw, and characters seeded from an uploaded image, through the CLI and the API.
"""

from __future__ import annotations

import base64
import io
import json
import time

import httpx
import numpy as np
from PIL import Image

from conftest import free_port, sk
from test_06_jobs import _serve, _stop
from validate import check_final

TANK = "An olive green battle tank with a long cannon and wide treads."
GENERATOR = "A rusty yellow steam generator with a big gear and a red warning light."
FIREBALL = "A blue glowing energy ball with a streaming tail."
HERO = "A young knight in blue armor with a red cape."


def _final(proj, anim):
    return proj / "animations" / anim / "final"


def _job(proj, anim) -> dict:
    d = sorted((proj / "animations" / anim / "jobs").iterdir())[-1]
    return json.loads((d / "job.json").read_text())


def _frames(final) -> list[np.ndarray]:
    return [np.asarray(Image.open(p).convert("RGBA")) for p in sorted((final / "frames").glob("*.png"))]


def _centroid(f: np.ndarray) -> tuple[float, float]:
    a = f[..., 3].astype(np.float64)
    ys, xs = np.mgrid[: a.shape[0], : a.shape[1]]
    return float((xs * a).sum() / a.sum()), float((ys * a).sum() / a.sum())


def _gen(proj, who, action, *extra, env=None):
    r = sk("gen", who, action, "--project", str(proj), *extra, env=env)
    return r["json"]


def _subjects(work, sub: str, subjects) -> tuple:
    """A synthetic project holding the given (name, kind, description, extra args) subjects."""
    proj = work / sub / "K.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "phaser", "--mode", "synthetic")
    for name, kind, desc, extra in subjects:
        sk("character", "new", name, "--kind", kind, "--describe", desc, *extra, "--approve", "--project", str(proj))
    chars = {n: json.loads((proj / "characters" / n / "character.json").read_text()) for n, *_ in subjects}
    return proj, chars


def test_subject_vehicles(rec, work):
    S = "subjects"
    proj, chars = _subjects(work, "subjects-vehicles", (("tank", "vehicle", TANK, []),
                                                        ("generator", "machine", GENERATOR, [])))
    rec.check(S, "vehicles and machines carry their kind and blend",
              [(c["kind"], c["blend"]) for c in chars.values()] == [("vehicle", "normal"), ("machine", "normal")],
              {n: c["kind"] for n, c in chars.items()}, ["K5"])
    meta = json.loads((proj / "characters" / "tank" / "ref" / "turnaround-c1.meta.json").read_text())
    tprompt = json.dumps(meta)
    rec.check(S, "object turnaround uses the object template, no body wording",
              "turnaround_object" in tprompt and "arms" not in tprompt.lower(), meta.get("template"), ["K6"])
    rec.check(S, "a long vehicle's four views are cut cleanly", set(chars["tank"]["views"]) ==
              {"front", "side-e", "side-w", "back"} and not chars["tank"]["view_warnings"],
              chars["tank"]["view_warnings"], ["T9"])
    # --- tank fire: rigid guide, recoil preserved ------------------------------------------------
    j = _gen(proj, "tank", "fire")
    comp = j["steps"][0]["info"]
    rec.check(S, "tank fire: object sheet prompt and rigid guide",
              comp["prompt"]["template"] == "guided_object" and comp["choreo"] == "vehicle.fire.side.6"
              and "arms" not in comp["prompt"]["prompt"].lower(), comp["choreo"], ["K1", "K6"])
    guide = json.loads((proj / j["dir"] / "guide.json").read_text())
    rec.check(S, "tank fire: guide is the vehicle's own silhouette (rigid mode)", guide["mode"] == "rigid",
              guide["mode"], ["K1"])
    rec.check(S, "tank fire accepted", j["result"]["accepted"], j["result"]["score"])
    fr = _frames(_final(proj, "tank-fire-E"))
    xs = [_centroid(f)[0] for f in fr]
    h = max(int((f[..., 3] > 0).any(1).sum()) for f in fr)
    back = (xs[0] - xs[2]) / h  # recoil moves the tank back (left, it faces right) by ~5 % of its height
    rec.check(S, "tank fire: recoil kept by rigid registration, not corrected away", 0.02 < back < 0.09,
              round(back, 4), ["K9"])
    rec.check(S, "tank fire: returns to rest", abs(xs[-1] - xs[0]) / h < 0.015, round((xs[-1] - xs[0]) / h, 4), ["K9"])
    v = check_final(_final(proj, "tank-fire-E"), "phaser")
    rec.check(S, "tank fire export agrees with its sheet", v["ok"], v["errors"], ["E2"])
    rec.keep(S, _final(proj, "tank-fire-E") / "sheet.png", "tank-fire.png")

    j = _gen(proj, "tank", "move")
    rec.check(S, "tank move (driving loop) accepted", j["result"]["accepted"], j["result"]["score"], ["K3"])
    j = _gen(proj, "tank", "idle")
    idle_rep = json.loads((_final(proj, "tank-idle-E") / "report.json").read_text())
    rec.check(S, "tank idle: no character breathing checks on a vehicle",
              j["result"]["accepted"] and not any(f["metric"] == "idle_motion" for f in idle_rep["findings"])
              and "idle_head_travel" not in json.dumps(idle_rep["temporal"]), j["result"]["score"], ["K3", "K15"])
    j = _gen(proj, "generator", "work")
    rec.check(S, "generator work loop accepted", j["result"]["accepted"], j["result"]["score"], ["K3"])
    rec.keep(S, _final(proj, "generator-work-E") / "sheet.png", "generator-work.png")



def test_subject_effects(rec, work):
    S = "subjects"
    proj, chars = _subjects(work, "subjects-effects", (("fireball", "effect", FIREBALL, ["--blend", "add"]),))
    rec.check(S, "an additive effect carries its kind and blend",
              (chars["fireball"]["kind"], chars["fireball"]["blend"]) == ("effect", "add"), chars["fireball"]["kind"],
              ["K5"])
    rec.check(S, "an effect has one design view", list(chars["fireball"]["views"]) == ["key"],
              list(chars["fireball"]["views"]), ["K1"])
    # --- additive fireball ----------------------------------------------------------------------
    j = _gen(proj, "fireball", "projectile")
    comp = j["steps"][0]["info"]
    rec.check(S, "fireball: generated on black and matted by luminance",
              comp["key"] == "#000000" and comp["prompt"]["template"] == "guided_effect", comp["key"], ["K4"])
    rec.check(S, "fireball projectile accepted", j["result"]["accepted"], j["result"]["score"])
    fin = _final(proj, "fireball-projectile-E")
    anim = json.loads((fin / "animation.json").read_text())
    sheet = json.loads((fin / "sheet.json").read_text())
    anims = json.loads((fin / "anims.json").read_text())
    rec.check(S, "fireball: export declares additive blending", anim.get("blend") == "add"
              and str(sheet["meta"].get("blendMode")).lower() == "add" and "add" in json.dumps(anims).lower(),
              [anim.get("blend"), sheet["meta"].get("blendMode")], ["K8"])
    fr = _frames(fin)
    soft = np.concatenate([f[(f[..., 3] > 10) & (f[..., 3] < 128)][:, :3] for f in fr])
    brightness = float(soft.max(1).mean()) if len(soft) else 0.0
    rec.check(S, "fireball: soft glow edges are unpremultiplied colour, no dark fringe", brightness > 150,
              round(brightness, 1), ["K4"])
    blue = np.concatenate([f[f[..., 3] > 128][:, :3] for f in fr]).astype(int)
    rec.check(S, "fireball: keeps its blue design", float((blue[:, 2] > blue[:, 0] + 20).mean()) > 0.2,
              round(float((blue[:, 2] > blue[:, 0] + 20).mean()), 3))
    gif = Image.open(fin / "preview.gif").convert("RGB")
    corner = np.asarray(gif)[:4, :4].reshape(-1, 3)
    rec.check(S, "fireball: preview.gif shows the additive effect on black, not on a checker",
              int(corner.max()) < 16, corner.max(), ["K8"])
    rec.keep(S, fin / "sheet.png", "fireball-projectile.png")

    j = _gen(proj, "fireball", "impact")
    fin = _final(proj, "fireball-impact-E")
    fr = _frames(fin)
    rec.check(S, "fireball impact: all six frames found, faint sparks included", j["result"]["accepted"]
              and len(fr) == 6, [j["result"]["score"], len(fr)], ["K10", "K11", "K12"])
    cs = [_centroid(f) for f in fr if f[..., 3].sum() > 0]
    H = fr[0].shape[0]
    spread = max(c[1] for c in cs) - min(c[1] for c in cs)
    rec.check(S, "fireball impact: centred, never ground-locked", spread / H < 0.08, round(spread / H, 4), ["K2"])
    rec.keep(S, fin / "sheet.png", "fireball-impact.png")



def test_subject_character_throw(rec, work):
    S = "subjects"
    proj, _ = _subjects(work, "subjects-hero", (("hero", "character", HERO, []),))
    j = _gen(proj, "hero", "fireball")
    rec.check(S, "character fireball throw accepted", j["result"]["accepted"]
              and j["steps"][0]["info"]["choreo"] == "fireball.side.6", [j["result"]["score"], j["steps"][0]["info"]["choreo"]])
    rec.keep(S, _final(proj, "hero-fireball-E") / "sheet.png", "hero-fireball.png")

    # --- a whole-animation recolour is caught (Q2) --------------------------------------------------
    # a one-shot guided action (the hd walk is a video loop, which this defect does not reach)
    j = _gen(proj, "hero", "cast", "--seed", "7", env={"SPRITEGURU_SYNTH_DEFECTS": "c1=recolor;c2=recolor"})
    flagged = [c for c in j["candidates"] if any(t["metric"] == "identity" and not t["frames"] for t in c["top"])]
    rec.check(S, "uniform recolour fails identity against the reference", len(flagged) >= 1 and
              not j["result"]["accepted"], [c["id"] for c in flagged], ["Q2"])
    rec.check(S, "uniform recolour is re-rolled, not repaired frame by frame",
              any(d["action"] == "reroll" for d in j["decisions"]), [d["action"] for d in j["decisions"]], ["Q2"])


def test_image_references(rec, work):
    S = "image-refs"
    proj = work / "imagerefs" / "I.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "godot", "--mode", "synthetic")
    sk("character", "new", "hero", "--describe", HERO, "--approve", "--project", str(proj))
    side = proj / "characters" / "hero" / "ref" / "side-e.png"
    ref = work / "imagerefs" / "hero-side.png"
    Image.open(side).save(ref)

    # turnaround seeded from an image, description from the image (I3: it is a transparent sprite)
    r = sk("character", "new", "knight2", "--image", str(ref), "--approve", "--project", str(proj))["json"]
    c = r["character"]
    tmeta = json.dumps(json.loads((proj / "characters" / "knight2" / "ref" / "turnaround-c1.meta.json").read_text()))
    rec.check(S, "image seeds the turnaround and the description", c["description_source"] == "image"
              and c["source_image"] and "image 1" in tmeta and len(c["views"]) == 4,
              [c["description"], c["description_source"]], ["I3", "I5"])
    j = sk("gen", "knight2", "walk", "--project", str(proj))["json"]
    rep = json.loads((_final(proj, "knight2-walk-E") / "report.json").read_text())
    cov = rep["temporal"]["identity"].get("palette_coverage", [])
    rec.check(S, "image-seeded walk keeps the reference's colours", j["result"]["accepted"] and cov
              and float(np.median(cov)) >= 0.55, [j["result"]["score"], round(float(np.median(cov)), 3) if cov else None],
              ["Q2"])
    rec.keep(S, _final(proj, "knight2-walk-E") / "sheet.png", "knight2-walk.png")

    # the image used directly as the side view
    r = sk("character", "new", "knight3", "--image", str(ref), "--use-image-as-view", "--approve",
           "--project", str(proj))["json"]
    c = r["character"]
    se = np.asarray(Image.open(proj / c["views"]["side-e"]).convert("RGBA"))
    sw = np.asarray(Image.open(proj / c["views"]["side-w"]).convert("RGBA"))
    rec.check(S, "image as view: side-e from the image, side-w its mirror", set(c["views"]) == {"side-e", "side-w"}
              and se.shape == sw.shape and np.array_equal(se[:, ::-1], sw), sorted(c["views"]), ["I3"])
    j = sk("gen", "knight3", "idle", "--project", str(proj))["json"]
    rec.check(S, "image-as-view idle accepted", j["result"]["accepted"], j["result"]["score"])
    rec.keep(S, _final(proj, "knight3-idle-E") / "sheet.png", "knight3-idle.png")

    # I2: a tiny reference (48 px tall) and a huge one are normalized
    tiny = work / "imagerefs" / "tiny.png"
    im = Image.open(ref)
    im.resize((max(1, im.width * 48 // im.height), 48), Image.Resampling.NEAREST).save(tiny)
    huge = work / "imagerefs" / "huge.png"
    im.resize((im.width * 6000 // im.height, 6000), Image.Resampling.NEAREST).save(huge)
    ok = []
    for n, p in (("tiny", tiny), ("huge", huge)):
        r = sk("character", "new", n, "--image", str(p), "--approve", "--project", str(proj))["json"]
        src = Image.open(proj / r["character"]["source_image"])
        ok.append((n, max(src.size), len(r["character"]["views"])))
    rec.check(S, "tiny and huge references normalized and usable",
              all(v == 4 for _, _, v in ok) and ok[1][1] <= 1536, ok, ["I2"])

    # I4: not an image
    bad = work / "imagerefs" / "notes.png"
    bad.write_text("this is not an image")
    r = sk("character", "new", "bad", "--image", str(bad), "--project", str(proj), check=False)
    out = r["stdout"] + r["stderr"]
    rec.check(S, "an undecodable file is rejected with a clean message", r["code"] == 2 and "not a readable image"
              in out and "BytesIO" not in out, r["code"], ["I4"])
    r = sk("character", "new", "hero", "--describe", "Someone else.", "--project", str(proj), check=False)
    rec.check(S, "creating a subject with an existing name is refused", r["code"] == 2 and "already exists"
              in (r["stdout"] + r["stderr"]), r["code"])


def test_image_references_api(rec, work):
    S = "image-refs"
    proj = work / "imagerefs-api" / "I.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "godot", "--mode", "synthetic")
    sk("character", "new", "hero", "--describe", HERO, "--approve", "--project", str(proj))
    side = proj / "characters" / "hero" / "ref" / "side-e.png"
    ref = work / "imagerefs-api" / "hero-side.png"
    Image.open(side).save(ref)

    # through the API: kind-filtered actions, image upload, replacement, hostile uploads
    port = free_port()
    srv = _serve(proj, port, {})
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"X-SpriteGuru-Token": "t0k"}, timeout=120) as cl:
            names = [a["action"] for a in cl.get("/api/actions", params={"kind": "vehicle"}).json()]
            rec.check(S, "API lists the actions of a kind in library order", names == ["idle", "move", "fire", "destroyed"],
                      names, ["K1"])
            buf = io.BytesIO()
            Image.open(ref).save(buf, format="PNG")
            url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            cl.post("/api/characters", json={"name": "api-knight", "image": url, "use_image_as_view": True}
                    ).raise_for_status()
            for _ in range(300):
                ch = cl.get("/api/characters/api-knight").json()
                if ch.get("views"):
                    break
                time.sleep(0.1)
            rec.check(S, "API: create from an uploaded image as the view", set(ch.get("views", {})) ==
                      {"side-e", "side-w"} and ch["description_source"] == "image", sorted(ch.get("views", {})))
            r1 = cl.put("/api/characters/api-knight/image", json={"image": "bm90IGFuIGltYWdl"})
            r2 = cl.put("/api/characters/api-knight/image", json={"image": "data:image/png;base64,@@@"})
            big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * (21 * 1024 * 1024)).decode()
            r3 = cl.put("/api/characters/api-knight/image", json={"image": big})
            r4 = cl.put("/api/characters/api-knight/image", json={"image": url})
            rec.check(S, "API: garbage, broken base64 and oversized uploads are 400s; a real one is accepted",
                      [r1.status_code, r2.status_code, r3.status_code, r4.status_code] == [400, 400, 400, 200],
                      [r1.status_code, r2.status_code, r3.status_code, r4.status_code], ["I4", "I8"])
            dup = cl.post("/api/characters", json={"name": "api-knight", "description": "An effect.", "kind": "effect"})
            after = cl.get("/api/characters/api-knight").json()
            rec.check(S, "API: a duplicate name is refused, the existing subject kept",
                      dup.status_code == 409 and after["kind"] == "character", [dup.status_code, after["kind"]], ["I8"])
            cl.post("/api/characters/api-knight/approve").raise_for_status()
            measure = proj / "characters" / "api-knight" / "ref" / "measure.json"
            before = measure.stat().st_mtime_ns
            r5 = cl.put("/api/characters/api-knight/image", json={"image": url, "use_image_as_view": True})
            r6 = cl.put("/api/characters/nobody/image", json={"image": url})
            for _ in range(300):  # the re-cut writes measure.json last
                if measure.stat().st_mtime_ns != before:
                    break
                time.sleep(0.1)
            rec.check(S, "API: replacing the image withdraws approval and can re-cut the view; unknown name 404",
                      r5.status_code == 200 and r5.json()["approved"] is False and r6.status_code == 404,
                      [r5.status_code, r5.json().get("approved"), r6.status_code], ["I8"])
            files = sorted(p.name for p in (proj / "characters" / "api-knight" / "ref").iterdir())
            rec.check(S, "API: uploads are re-encoded as PNG inside the project only", "source.png" in files
                      and all(not f.endswith((".gif", ".jpg", ".bin")) for f in files), files, ["I8"])
    finally:
        _stop(srv)


def test_subject_styles(rec, work):
    """The same subjects in vector (Quiver animation from the approved view) and pixel style."""
    S = "subject-styles"
    out = {}
    for style in ("vector", "pixel"):
        proj = work / f"subjects-{style}" / "K.sprites"
        sk("init", str(proj), "--style", style, "--engine", "godot", "--mode", "synthetic")
        for name, kind, desc in (("tank", "vehicle", TANK), ("fireball", "effect", FIREBALL),
                                 ("generator", "machine", GENERATOR)):
            extra = ["--blend", "add"] if kind == "effect" else []
            sk("character", "new", name, "--kind", kind, "--describe", desc, *extra, "--approve", "--project", str(proj))
        for who, action in (("tank", "fire"), ("tank", "move"), ("generator", "work"), ("fireball", "impact")):
            j = _gen(proj, who, action)
            out[f"{style}:{who}-{action}"] = (j["route"], j["result"]["accepted"], j["result"]["score"])
            v = check_final(_final(proj, f"{who}-{action}-E"), "godot")
            rec.check(S, f"{style} {who} {action}: accepted, export agrees with its sheet",
                      j["result"]["accepted"] and v["ok"], [j["route"], j["result"]["score"], v["errors"]],
                      {"vector": ["K7", "V5"], "pixel": ["K13", "P12", "R12"]}[style])
        rec.keep(S, _final(proj, "tank-fire-E") / "sheet.png", f"{style}-tank-fire.png")
        rec.keep(S, _final(proj, "fireball-impact-E") / "sheet.png", f"{style}-fireball-impact.png")
    rec.check(S, "vector subjects use Quiver animation, pixel subjects the guided pixel route",
              all(v[0] == "vector-anim" for k, v in out.items() if k.startswith("vector"))
              and all(v[0] == "guided-pixel" for k, v in out.items() if k.startswith("pixel")),
              {k: v[0] for k, v in out.items()}, ["K7"])
    rec.note(S, results=out)


def test_studio_subjects(rec, work, run_dir):
    """The studio's subject UI in headless Chromium: kind selector, image upload used as the view,
    kind badges, and a builder whose actions follow the selected subject's kind."""
    S = "studio-subjects"
    from playwright.sync_api import expect, sync_playwright

    proj = work / "studio-subjects" / "S.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "godot", "--mode", "synthetic")
    sk("character", "new", "hero", "--describe", HERO, "--approve", "--project", str(proj))
    sk("character", "new", "tank", "--kind", "vehicle", "--describe", TANK, "--approve", "--project", str(proj))
    upload = work / "studio-subjects" / "hero-side.png"
    Image.open(proj / "characters" / "hero" / "ref" / "side-e.png").save(upload)
    port = free_port()
    srv = _serve(proj, port, {})
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)

    def shot(page, name):
        p = shots / f"{name}.png"
        page.screenshot(path=str(p), full_page=False)
        rec.files.append({"scenario": S, "path": p.relative_to(run_dir).as_posix(), "sha256": None, "in_digest": False})

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/?token=t0k")
            expect(page.get_by_test_id("topbar")).to_be_visible(timeout=15000)
            page.get_by_test_id("nav-characters").click()
            expect(page.get_by_test_id("characters-screen")).to_be_visible()
            kinds = sorted(page.get_by_test_id("character-kind-badge").evaluate_all(
                "els => els.map(e => e.dataset.kind)"))
            rec.check(S, "subjects show their kind badges", kinds == ["character", "vehicle"], kinds, ["K1"])

            # create a subject from an uploaded image, used directly as the side view
            page.get_by_test_id("characters-new").click()
            expect(page.get_by_test_id("character-form")).to_be_visible()
            page.get_by_test_id("character-form-name").fill("uploaded")
            page.get_by_test_id("character-kind-character").click()
            page.get_by_test_id("character-image-input").set_input_files(str(upload))
            expect(page.get_by_test_id("character-image-preview")).to_be_visible()
            page.get_by_test_id("character-use-image-as-view").click(force=True)
            shot(page, "01-create-from-image")
            page.get_by_test_id("character-form-submit").click()
            view = page.locator("[data-testid=character-row]", has_text="uploaded").get_by_test_id("character-view-side-e")
            expect(view).to_be_visible(timeout=30000)
            ch = json.loads((proj / "characters" / "uploaded" / "character.json").read_text())
            rec.check(S, "studio: image uploaded and used as the side view",
                      ch["description_source"] == "image" and set(ch["views"]) == {"side-e", "side-w"}
                      and bool(ch["source_image"]), sorted(ch["views"]), ["I3", "I8"])
            shot(page, "02-uploaded-subject")

            # the builder lists the active subject's actions; the subject is picked in the header
            def pick(name):
                page.get_by_test_id("header-character").click()
                page.locator(f"[data-testid=header-character-item][data-name='{name}']").click()

            page.get_by_test_id("nav-builder").click()
            pick("tank")
            expect(page.get_by_test_id("builder-action")).to_have_attribute("data-kind", "vehicle", timeout=10000)
            expect(page.locator("[data-testid=builder-action] option[value='fire']")).to_have_count(1, timeout=10000)
            opts = page.get_by_test_id("builder-action").locator("option").evaluate_all("els => els.map(e => e.value)")
            rec.check(S, "studio builder: a vehicle's actions only", opts == ["idle", "move", "fire", "destroyed"],
                      opts, ["K1"])
            pick("hero")
            expect(page.get_by_test_id("builder-action")).to_have_attribute("data-kind", "character", timeout=10000)
            expect(page.locator("[data-testid=builder-action] option[value='fireball']")).to_have_count(1, timeout=10000)
            opts = page.get_by_test_id("builder-action").locator("option").evaluate_all("els => els.map(e => e.value)")
            rec.check(S, "studio builder: a character's actions include the fireball throw",
                      "fireball" in opts and "fire" not in opts, opts)
            shot(page, "03-builder-kinds")
            rec.check(S, "no page errors", not errors, errors[:3])
            browser.close()
    finally:
        _stop(srv)


def test_layout_strip_and_confirm(rec, work):
    """A user report (L14-L22): the model drew every frame into the reference strip, the strip cut
    the left column's frames, and hand-confirmed cells were ignored and lost on reload."""
    S = "layout-confirm"
    proj = work / "layout" / "L.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "godot", "--mode", "synthetic")
    sk("character", "new", "tank", "--kind", "vehicle", "--describe", TANK, "--approve", "--project", str(proj))

    # L14: every frame drawn ~0.3 widths left of its guide cell, the left column across the strip
    j = _gen(proj, "tank", "move", "--facing", "W", env={"SPRITEGURU_SYNTH_DEFECTS": "c1=drift;c2=drift"})
    guide = json.loads((proj / j["dir"] / "guide.json").read_text())
    strip_w = guide["plan"]["cell_w"]
    rep = json.loads((proj / j["candidates"][0]["report"]).read_text())
    crossed = [f["region"][0] < strip_w for f in rep["frames"][::2]]
    widths = []
    for f in _frames(_final(proj, "tank-move-W")):
        cols = np.nonzero((f[..., 3] > 128).any(0))[0]
        widths.append(int(cols.max() - cols.min() + 1))
    rec.check(S, "frames drawn across the reference strip are kept whole", all(crossed)
              and max(widths) / min(widths) < 1.01, [crossed, widths], ["L14"])

    port = free_port()
    srv = _serve(proj, port, {})
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"X-SpriteGuru-Token": "t0k"}, timeout=180) as cl:
            jid, cid = j["id"], j["candidates"][0]["id"]
            cells = [list(f["region"]) for f in rep["frames"]]
            wide = [[max(0, x0 - 24), y0, x1, y1] if i % 2 == 0 else [x0, y0, x1, y1]
                    for i, (x0, y0, x1, y1) in enumerate(cells)]
            ent = cl.post(f"/api/jobs/{jid}/layout", json={"candidate": cid, "cells": wide}).json()
            crep = cl.get(f"/api/files/{ent['report']}").json()
            rec.check(S, "confirmed cells are the layout, not a hint",
                      crep["layout"]["hypothesis"] == "confirmed" and [f["region"] for f in crep["frames"]] == wide,
                      crep["layout"]["hypothesis"], ["L16", "L17"])
            again = cl.get(f"/api/jobs/{jid}").json()
            stored = next(c for c in again["candidates"] if c["id"] == ent["id"])
            fin = json.loads((_final(proj, "tank-move-W") / "report.json").read_text())
            rec.check(S, "confirming makes the confirmed layout the exported one",
                      again["winner"] == ent["id"] and [f["region"] for f in fin["frames"]] == wide,
                      again["winner"], ["L23"])
            srep = cl.get(f"/api/files/{stored['report']}").json()
            rec.check(S, "confirmed cells survive a reload of the job",
                      [f["region"] for f in srep["layout"]["frames"]] == wide, stored["id"], ["L16", "L22"])
            rec.check(S, "confirmation keeps the guide: rigid registration and pose conformance",
                      srep["temporal"]["registration"].get("mode") == "rigid" and "pose_iou" in srep["temporal"],
                      srep["temporal"]["registration"].get("mode"), ["L21"])
            ent2 = cl.post(f"/api/jobs/{jid}/layout", json={"candidate": ent["id"], "cells": wide}).json()
            rep3 = cl.get(f"/api/files/{ent2['report']}").json()
            rec.check(S, "confirming again from the confirmed candidate keeps its cells",
                      [f["region"] for f in rep3["frames"]] == wide, ent2["id"], ["L22"])

            # L18: overlapping cells; each pixel belongs to the nearest cell, boxes stay in their cells
            over = [[x0, y0, x1 + 90, y1] if i % 2 == 0 else [x0 - 90, y0, x1, y1]
                    for i, (x0, y0, x1, y1) in enumerate(wide)]
            ent3 = cl.post(f"/api/jobs/{jid}/layout", json={"candidate": cid, "cells": over, "use": False}).json()
            rep4 = cl.get(f"/api/files/{ent3['report']}").json()
            inside = all(f["box"][0] >= f["region"][0] and f["box"][2] <= f["region"][2] for f in rep4["frames"])
            rec.check(S, "overlapping confirmed cells: every frame found, each within its own cell",
                      len(rep4["frames"]) == len(over) and inside, len(rep4["frames"]), ["L18"])

            # L19: cells that cut through the subject are reported as clipping
            tight = [[f["box"][0] + 40, y0, x1, y1] for f, (x0, y0, x1, y1) in zip(rep["frames"], wide)]
            ent4 = cl.post(f"/api/jobs/{jid}/layout", json={"candidate": cid, "cells": tight, "use": False}).json()
            rep5 = cl.get(f"/api/files/{ent4['report']}").json()
            clipped = next((f["frames"] for f in rep5["findings"] if f["metric"] == "clipping"), [])
            rec.check(S, "confirmed cells that cut the subject are flagged as clipping",
                      sorted(clipped) == list(range(len(tight))), clipped, ["L19"])

            # L20: malformed confirmations
            codes = [cl.post(f"/api/jobs/{jid}/layout", json={"candidate": cid, "cells": b, "use": False}).status_code
                     for b in ([wide[0]], [[0, 0, 99999, 10]] + wide[1:], [[10, 10, 11, 11]] + wide[1:])]
            codes.append(cl.post(f"/api/jobs/{jid}/layout", json={"candidate": "nope", "cells": wide, "use": False}).status_code)
            rec.check(S, "malformed confirmations are rejected", codes == [400, 400, 400, 404], codes, ["L20"])
    finally:
        _stop(srv)


def test_layout_fused_reference(rec, work):
    S = "layout-confirm"
    proj = work / "layout-fuse" / "L.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "godot", "--mode", "synthetic")
    sk("character", "new", "tank", "--kind", "vehicle", "--describe", TANK, "--approve", "--project", str(proj))
    # L15: a frame fused with the reference drawing is cut there and flagged, then repaired
    j2 = _gen(proj, "tank", "fire", "--seed", "4", env={"SPRITEGURU_SYNTH_DEFECTS": "c1=fuse:0;c2=fuse:0"})
    rep2 = json.loads((proj / j2["candidates"][0]["report"]).read_text())
    clip = [f for f in rep2["findings"] if f["metric"] == "clipping"]
    rec.check(S, "a frame fused with the reference is flagged as clipped and repaired",
              bool(clip) and 0 in clip[0]["frames"] and any(d["action"] == "repair" for d in j2["decisions"])
              and j2["result"]["accepted"], [[c["frames"] for c in clip], [d["action"] for d in j2["decisions"]]],
              ["L15"])


def test_studio_layout_confirm(rec, work, run_dir):
    """The reported studio flow: drag cell edges in Sheet review, confirm, go to another screen and
    come back. The cells and the export must follow the confirmation (L16, L23)."""
    S = "studio-layout"
    from playwright.sync_api import expect, sync_playwright

    proj = work / "studio-layout" / "L.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "godot", "--mode", "synthetic")
    sk("character", "new", "tank", "--kind", "vehicle", "--describe", TANK, "--approve", "--project", str(proj))
    _gen(proj, "tank", "move", "--facing", "W", env={"SPRITEGURU_SYNTH_DEFECTS": "c1=drift;c2=drift"})
    port = free_port()
    srv = _serve(proj, port, {})
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)

    def cell_x0(page) -> list[float]:
        return page.get_by_test_id("sheet-cell").locator("rect.ov-cell-body").evaluate_all(
            "els => els.map(e => Math.round(parseFloat(e.getAttribute('x'))))")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/?token=t0k#/sheet/tank-move-W")
            expect(page.get_by_test_id("sheet-cell")).to_have_count(8, timeout=20000)
            before = cell_x0(page)
            edge = page.get_by_test_id("sheet-cell").nth(0).get_by_test_id("sheet-cell-edge-l")
            bb = edge.bounding_box()
            page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            page.mouse.down()
            for k in range(1, 9):
                page.mouse.move(bb["x"] + bb["width"] / 2 - 3 * k, bb["y"] + bb["height"] / 2)
            page.mouse.up()
            dragged = cell_x0(page)
            moved = [i for i in range(8) if dragged[i] < before[i]]
            rec.check(S, "dragging a left edge moves the whole column's cut", moved == [0, 2, 4, 6],
                      [before, dragged], ["L16"])
            page.screenshot(path=str(shots / "01-dragged.png"))
            page.get_by_test_id("sheet-confirm-layout").click()
            page.wait_for_url("**cand=*", timeout=30000)
            expect(page.get_by_test_id("sheet-cell")).to_have_count(8, timeout=20000)
            page.wait_for_timeout(500)
            rec.check(S, "after confirming, the confirmed candidate shows your cells", cell_x0(page) == dragged,
                      cell_x0(page), ["L16"])
            page.get_by_test_id("nav-characters").click()
            expect(page.get_by_test_id("characters-screen")).to_be_visible()
            page.get_by_test_id("nav-sheet").click()
            expect(page.get_by_test_id("sheet-cell")).to_have_count(8, timeout=20000)
            page.wait_for_timeout(500)
            back = cell_x0(page)
            rec.check(S, "leaving Sheet review and coming back keeps the confirmed cells", back == dragged,
                      back, ["L16", "L23"])
            page.screenshot(path=str(shots / "02-after-return.png"))
            rec.files += [{"scenario": S, "path": (shots / n).relative_to(run_dir).as_posix(), "sha256": None,
                           "in_digest": False} for n in ("01-dragged.png", "02-after-return.png")]
            fin = json.loads((_final(proj, "tank-move-W") / "report.json").read_text())
            rec.check(S, "the export was re-cut along the confirmed cells",
                      [round(f["region"][0]) for f in fin["frames"]] == dragged, [f["region"][0] for f in fin["frames"]],
                      ["L23"])
            rec.check(S, "no page errors", not errors, errors[:3])
            browser.close()
    finally:
        _stop(srv)
