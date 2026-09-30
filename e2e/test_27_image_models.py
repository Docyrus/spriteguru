"""Selectable image models (docs/failure-modes.md IM1-IM10): the per-task choice through the CLI, the
API and the studio's Settings panel, and Seedream 5.0 / FLUX.2 [max] calls in live mode against a
local fake of fal's queue (sizes, masks, batching, costs, the cache, errors, a missing key)."""

from __future__ import annotations

import json
import math

import httpx
from PIL import Image

from conftest import free_port, sk
from fakefal import FakeFal
from test_06_jobs import _serve, _stop

HERO = "A young knight in blue armor with a red cape."
MODELS = ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "seedream-5.0-flash", "seedream-5.0-pro",
          "seedream-5.0-lite", "flux-2-max"]


def _env(fake: FakeFal, fal_key: str = "fake-fal-key") -> dict:
    """Live mode pointed at the fake: fal goes to it, and OpenAI has no key, so a stray call fails, never spends."""
    return {"SPRITEGURU_FAL_QUEUE_URL": fake.url, "FAL_KEY": fal_key, "AI_PROVIDER_KEY_FAL": "",
            "OPENAI_API_KEY": "", "AI_PROVIDER_KEY_OPENAI": "", "SPRITEGURU_BACKOFF_BASE": "1.2"}


def _live_project(work, sub: str):
    proj = work / sub / "M.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--mode", "live")
    cfg = json.loads((proj / "project.json").read_text())
    cfg["settings"]["judge_enabled"] = False  # the judge is an OpenAI call
    (proj / "project.json").write_text(json.dumps(cfg, indent=2))
    return proj


def _ledger(proj) -> list[dict]:
    return [json.loads(line) for line in (proj / "ledger.jsonl").read_text().splitlines() if line.strip()]


def _job(proj, j) -> dict:
    return json.loads((proj / j["dir"] / "job.json").read_text())


def _flux_cost(n: int, out: tuple[int, int], inputs: list[tuple[int, int]]) -> float:
    mp = math.ceil(out[0] * out[1] / 1e6) + sum(math.ceil(w * h / 1e6) for w, h in inputs)
    return n * (0.07 + 0.03 * (mp - 1))


def test_image_models_fal(rec, work):
    S = "image-models"
    proj = _live_project(work, "image-models")
    with FakeFal() as fake:
        env = _env(fake)
        r = sk("models", "--set", "turnaround=seedream-5.0-flash", "--set", "guided_sheet=flux-2-max",
               "--set", "repair=seedream-5.0-pro", "--project", str(proj))["json"]
        rec.check(S, "CLI sets a model per task; unset tasks keep their default", r["models"] == {
            "guided_sheet": "flux-2-max", "turnaround": "seedream-5.0-flash", "repair": "seedream-5.0-pro",
            "inbetween": "gpt-image-2.5-sunburst"} and r["available"] == MODELS, r["models"], ["IM1"])
        bad = sk("models", "--set", "guided_sheet=dall-e-3", "--project", str(proj), check=False)
        rec.check(S, "CLI refuses an unknown model and lists the allowed ones",
                  bad["code"] == 2 and "seedream-5.0-lite" in bad["stderr"], bad["code"], ["IM2"])

        # --- turnaround: Seedream 5.0 Flash text-to-image, two candidates in one call -------------------
        sk("character", "new", "hero", "--describe", HERO, "--candidates", "2", "--approve", "--project", str(proj),
           env=env)
        t = fake.for_endpoint("seedream/v5/flash/text-to-image")
        size = t[0]["image_size"] if t else {}
        px = size.get("width", 0) * size.get("height", 0)
        rec.check(S, "turnaround drawn by Seedream 5.0 Flash: one call for both candidates, PNG, a size in range",
                  len(t) == 1 and t[0]["num_images"] == 2 and t[0]["output_format"] == "png" and t[0]["auth"]
                  and 1024 * 1024 <= px <= 2048 * 2048 and size["width"] % 16 == 0 and size["height"] % 16 == 0,
                  [len(t), t[0]["num_images"] if t else None, size], ["IM1", "IM3", "IM5"])

        # --- sprite sheet: FLUX.2 [max], one call per candidate, no mask ---------------------------------
        j = sk("gen", "hero", "intro-salute", "--yes", "--project", str(proj), env=env)["json"]
        st = _job(proj, j)
        plan = st["steps"][0]["info"]["plan"]
        W, H = plan["width"], plan["height"]
        f = fake.for_endpoint("flux-2-max/edit")
        rec.check(S, "sheet: FLUX.2 [max] gets one call per candidate with distinct seeds",
                  len(f) == 2 and len({x["seed"] for x in f}) == 2 and all(x["num_images"] is None for x in f),
                  [(x["seed"], x["num_images"]) for x in f], ["IM5"])
        rec.check(S, "sheet: guide canvas and reference go inline, no mask, the canvas size asked for",
                  all(len(x["image_urls"]) == 2 and all(u["data_uri"] for u in x["image_urls"])
                      and x["image_urls"][0]["size"] == [W, H] and "mask_url" not in x["keys"]
                      and x["image_size"] == {"width": W, "height": H} for x in f),
                  [(x["keys"], x["image_size"]) for x in f][:1], ["IM3", "IM4"])
        cands = [c for c in st["candidates"] if c["kind"] == "generate"]
        sizes = [Image.open(proj / c["sheet"]).size for c in cands]
        metas = [json.loads((proj / c["meta"]).read_text()) for c in cands]
        rec.check(S, "sheet: the job records its models; candidates name FLUX.2 [max] and have the canvas size",
                  st["models"]["guided_sheet"] == "flux-2-max"
                  and st["steps"][0]["info"]["models"]["guided_sheet"] == "flux-2-max"
                  and all(m.get("model") == "flux-2-max" for m in metas) and all(s == (W, H) for s in sizes),
                  [st["models"], sizes], ["IM1", "IM3"])
        led = [e for e in _ledger(proj) if e.get("job") == j["id"] and e.get("model") == "flux-2-max"]
        paid = sum(e.get("cost", 0) for e in led if e["status"] == "ok")
        want = _flux_cost(2, (W, H), [(W, H), (512, 512)])
        rec.check(S, "ledger: the FLUX.2 [max] call costs $0.07 first MP + $0.03 per extra MP, inputs included",
                  abs(paid - want) < 1e-6, [round(paid, 4), round(want, 4)], ["IM6"])
        rec.check(S, "sheet job finished and exported", j["state"] == "done" and bool(j["result"].get("final")),
                  [j["state"], j["result"].get("score")])

        # --- a manual repair: the current repair model, straight to one maskless edit --------------------
        before = len(fake.requests)
        r = sk("repair", "hero-intro-salute-E", "--frame", "3", "--kind", "pose", "--yes", "--project", str(proj),
               env=env)["json"]
        rp = fake.requests[before:]
        rec.check(S, "repair: Seedream 5.0 Pro, one maskless edit, no wasted masked attempt",
                  len(rp) == 1 and "seedream/v5/pro/edit" in rp[0]["endpoint"] and "mask_url" not in rp[0]["keys"]
                  and rp[0]["num_images"] == 1, [(x["endpoint"], x["keys"]) for x in rp], ["IM1", "IM4"])
        rec.check(S, "repair re-analyzed and exported", bool(r["export"]["files"]), [r["before"], r["after"]])

        # --- switch the sheet model: Seedream 5.0 Lite, a fresh call, upscaled into its range ------------
        sk("models", "--set", "guided_sheet=seedream-5.0-lite", "--project", str(proj))
        j2 = sk("gen", "hero", "intro-salute", "--yes", "--project", str(proj), env=env)["json"]
        lite = fake.for_endpoint("seedream/v5/lite/edit")
        ls = lite[0]["image_size"] if lite else {}
        rec.check(S, "switching models makes a fresh call, never a cache hit of the old model's output",
                  len(lite) == 1 and not any(e["status"] == "cache_hit" for e in _ledger(proj)
                                             if e.get("job") == j2["id"] and e.get("purpose") == "guided_sheet"),
                  len(lite), ["IM10"])
        st2 = _job(proj, j2)
        sizes2 = [Image.open(proj / c["sheet"]).size for c in st2["candidates"] if c["kind"] == "generate"]
        rec.check(S, "Seedream 5.0 Lite draws at least 2560x1440 in one batched call; the sheet comes back at the canvas",
                  lite and lite[0]["num_images"] == 2 and ls["width"] * ls["height"] >= 2560 * 1440
                  and abs(ls["width"] / ls["height"] - W / H) < 0.03 and all(s == (W, H) for s in sizes2),
                  [ls, sizes2], ["IM3", "IM5"])
        paid2 = sum(e.get("cost", 0) for e in _ledger(proj) if e.get("job") == j2["id"] and e["status"] == "ok"
                    and e.get("model") == "seedream-5.0-lite")
        rec.check(S, "ledger: Seedream 5.0 Lite costs $0.035 per image", abs(paid2 - 0.07) < 1e-6, round(paid2, 4),
                  ["IM6"])
        rec.keep(S, proj / "animations" / "hero-intro-salute-E" / "final" / "sheet.png", "seedream-lite-salute.png")
        rec.note(S, requests=[{k: v for k, v in x.items() if k != "prompt"} for x in fake.requests])


def test_image_models_resume_and_errors(rec, work):
    S = "image-models"
    proj = _live_project(work, "image-models-errors")
    with FakeFal() as fake:
        env = _env(fake)
        # every image task on fal: this environment has no OpenAI key, so a repair on GPT Image would fail
        sk("models", "--set", "turnaround=seedream-5.0-flash", "--set", "guided_sheet=flux-2-max", "--set",
           "repair=seedream-5.0-flash", "--set", "inbetween=seedream-5.0-flash", "--project", str(proj))
        sk("character", "new", "hero", "--describe", HERO, "--approve", "--project", str(proj), env=env)

        # a job keeps its models across a settings change and a resume
        crashed = sk("gen", "hero", "intro-taunt", "--yes", "--project", str(proj),
                     env={**env, "SPRITEGURU_CRASH_AFTER": "compile"}, check=False)
        sk("models", "--set", "guided_sheet=seedream-5.0-pro", "--project", str(proj))
        before = len(fake.requests)
        sk("jobs", "resume", "--yes", "--project", str(proj), env=env)
        resumed = fake.requests[before:]
        rec.check(S, "a resumed job keeps the model it compiled with, whatever Settings say now",
                  crashed["code"] != 0 and resumed and all("flux-2-max/edit" in x["endpoint"] for x in resumed),
                  [crashed["code"], [x["endpoint"] for x in resumed]], ["IM1"])

        # a bad request is final; a busy service is retried
        fake.fail_next = [(422, "image_size is out of range")]
        before = len(fake.requests)
        bad = sk("gen", "hero", "intro-powerup", "--yes", "--project", str(proj), env=env, check=False)
        rec.check(S, "a fal 422 fails the job with fal's detail and is not retried",
                  bad["code"] != 0 and "image_size is out of range" in (bad["stderr"] + bad["stdout"])
                  and len(fake.requests) - before == 1, [bad["code"], len(fake.requests) - before], ["IM9"])
        fake.fail_next = [(503, "busy")]
        before = len(fake.requests)
        ok = sk("gen", "hero", "intro-powerup", "--seed", "3", "--yes", "--project", str(proj), env=env)["json"]
        pro = [x for x in fake.requests[before:] if "seedream/v5/pro/edit" in x["endpoint"]]  # repairs go to Flash
        rec.check(S, "a fal 503 is retried and the job finishes",
                  ok["state"] == "done" and len(pro) == 2, [ok["state"], len(pro)], ["IM9"])

        # a candidate the model broke (floor lines joining the figures, specks) loses; the job goes on (R14)
        fake.floor_next = 1
        broken = sk("gen", "hero", "intro-powerup", "--seed", "4", "--yes", "--project", str(proj), env=env, check=False)
        bj = broken["json"] or {}
        bst = _job(proj, bj) if bj.get("dir") else {}
        c1 = next((c for c in bst.get("candidates", []) if c["id"] == "c1"), {})
        rec.check(S, "a sheet with floor lines and specks scores low instead of crashing the job; the other candidate wins",
                  broken["code"] == 0 and bj.get("state") == "done" and bst.get("winner") != "c1"
                  and c1.get("score", 100) < 60, [broken["code"], bj.get("state"), bst.get("winner"), c1.get("score"),
                                                  (bj.get("error") or "")[:120]], ["R14"])

        # the safety checker's flag is final
        fake.nsfw_next = 1
        before = len(fake.requests)
        flagged = sk("gen", "hero", "intro-powerup", "--seed", "5", "--yes", "--project", str(proj), env=env, check=False)
        rec.check(S, "an output flagged by fal's safety checker fails the job, naming the model, without retries",
                  flagged["code"] != 0 and "Seedream 5.0 Pro flagged" in (flagged["stderr"] + flagged["stdout"])
                  and len(fake.requests) - before == 1, [flagged["code"], len(fake.requests) - before], ["IM7"])

        # no fal key: fails before anything is sent or spent
        before = len(fake.requests)
        nokey = sk("gen", "hero", "intro-powerup", "--seed", "9", "--yes", "--project", str(proj),
                   env=_env(fake, fal_key=""), check=False)
        errs = [e for e in _ledger(proj) if e["status"] == "error" and "no fal key" in e.get("error", "")]
        rec.check(S, "a fal model without a fal key fails at once with 'no fal key', nothing sent, nothing spent",
                  nokey["code"] != 0 and len(fake.requests) == before and errs and all(e["cost"] == 0 for e in errs),
                  [nokey["code"], len(fake.requests) - before, len(errs)], ["IM8"])

        # a model name that left the registry falls back to the default and says so
        cfg = json.loads((proj / "project.json").read_text())
        cfg["settings"]["image_models"]["guided_sheet"] = "retired-model"
        cfg["settings"]["provider_mode"] = "synthetic"
        (proj / "project.json").write_text(json.dumps(cfg, indent=2))
        stale = sk("gen", "hero", "intro", "--project", str(proj), "--mode", "synthetic")["json"]
        info = stale["steps"][0]["info"]
        rec.check(S, "a stale model name in project.json falls back to the task default with a note",
                  stale["state"] == "done" and info["models"]["guided_sheet"] == "gpt-image-2.5-flare"
                  and any("retired-model" in n for n in info["notes"]), [info["models"]["guided_sheet"], info["notes"]],
                  ["IM2"])


def test_studio_image_models(rec, work, run_dir):
    """Settings > Image models in headless Chromium, and the settings API."""
    S = "studio-image-models"
    from playwright.sync_api import expect, sync_playwright

    proj = work / "studio-image-models" / "S.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--mode", "synthetic")
    sk("character", "new", "hero", "--describe", HERO, "--approve", "--project", str(proj))
    port = free_port()
    srv = _serve(proj, port, {"FAL_KEY": "", "AI_PROVIDER_KEY_FAL": ""})
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)
    api = f"http://127.0.0.1:{port}"
    H = {"X-SpriteGuru-Token": "t0k"}

    def shot(page, name):
        p = shots / f"{name}.png"
        page.screenshot(path=str(p), full_page=True)
        rec.files.append({"scenario": S, "path": p.relative_to(run_dir).as_posix(), "sha256": None, "in_digest": False})

    def select(page, task: str):
        return page.locator(f"[data-testid=settings-image-model][data-task={task}] [data-testid=settings-image-model-select]")

    try:
        bad = httpx.patch(f"{api}/api/settings", headers=H, json={"image_models": {"guided_sheet": "dall-e-3"}})
        bad2 = httpx.patch(f"{api}/api/settings", headers=H, json={"image_models": {"lipsync": "flux-2-max"}})
        rec.check(S, "API: an unknown model or task is refused with 422 naming the allowed values",
                  bad.status_code == 422 and "flux-2-max" in bad.text and bad2.status_code == 422
                  and "guided_sheet" in bad2.text, [bad.status_code, bad2.status_code], ["IM2"])
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{api}/?token=t0k")
            expect(page.get_by_test_id("topbar")).to_be_visible(timeout=15000)
            page.get_by_test_id("nav-settings").click()
            expect(page.get_by_test_id("settings-image-models")).to_be_visible()
            rows = page.get_by_test_id("settings-image-model")
            expect(rows).to_have_count(4)
            tasks = rows.evaluate_all("els => els.map(e => e.dataset.task)")
            values = [select(page, t).input_value() for t in tasks]
            opts = select(page, "guided_sheet").locator("option").evaluate_all("els => els.map(e => e.value)")
            rec.check(S, "panel: four tasks, GPT Image by default, all six models offered",
                      tasks == ["guided_sheet", "turnaround", "repair", "inbetween"]
                      and values == ["gpt-image-2.5-flare"] + ["gpt-image-2.5-sunburst"] * 3 and opts == MODELS,
                      [tasks, values, opts], ["IM1"])
            select(page, "guided_sheet").select_option("flux-2-max")
            select(page, "repair").select_option("seedream-5.0-pro")
            meta = page.locator("[data-testid=settings-image-model][data-task=guided_sheet] "
                                "[data-testid=settings-image-model-meta]").inner_text()
            needs = page.get_by_test_id("settings-image-model-needs-key").count()
            rec.check(S, "panel: a fal model shows its price, that it takes no mask, and that the fal key is missing",
                      "$0.07 first MP" in meta and "no mask" in meta and needs == 2, [meta, needs], ["IM6", "IM8"])
            expect(page.get_by_test_id("settings-dirty")).to_have_text("2 unsaved")
            shot(page, "01-image-models-picked")
            page.get_by_test_id("settings-save").click()
            expect(page.get_by_test_id("settings-dirty")).to_have_count(0, timeout=10000)
            saved = json.loads((proj / "project.json").read_text())["settings"]["image_models"]
            rec.check(S, "save stores only the tasks that differ from their defaults",
                      saved == {"guided_sheet": "flux-2-max", "repair": "seedream-5.0-pro"}, saved, ["IM1"])
            page.reload()
            page.get_by_test_id("nav-settings").click()
            expect(select(page, "guided_sheet")).to_have_value("flux-2-max", timeout=10000)
            select(page, "repair").select_option("gpt-image-2.5-sunburst")
            page.get_by_test_id("settings-save").click()
            expect(page.get_by_test_id("settings-dirty")).to_have_count(0, timeout=10000)
            saved2 = json.loads((proj / "project.json").read_text())["settings"]["image_models"]
            rec.check(S, "the choice survives a reload; picking the default again removes it",
                      select(page, "guided_sheet").input_value() == "flux-2-max" and saved2 == {"guided_sheet": "flux-2-max"},
                      saved2, ["IM1"])
            shot(page, "02-image-models-saved")
            rec.check(S, "no page errors", not errors, errors[:3])
            browser.close()
        # the builder's estimate follows the chosen sheet model
        a = httpx.post(f"{api}/api/animations", headers=H, json={"character": "hero", "action": "intro"}).json()
        est = httpx.get(f"{api}/api/animations/{a['id']}/estimate", headers=H).json()
        lines = [x["model"] for x in est.get("lines", [])]
        rec.check(S, "the builder's estimate uses the chosen sheet model", "flux-2-max" in lines, lines, ["IM6"])
    finally:
        _stop(srv)
