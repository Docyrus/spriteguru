"""The studio UI end to end in headless Chromium: token gate, characters, builder with estimate and
guide, a generation job with live progress, candidates, sheet review, frame editor hand fixes that
re-export on disk, findings, export and settings. Screenshots join the artifact."""

from __future__ import annotations

import json
import time

from conftest import free_port, sk
from test_06_jobs import _serve, _stop


def test_studio(rec, work, run_dir):
    S = "studio"
    from playwright.sync_api import expect, sync_playwright

    proj = work / "studio" / "S.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "godot", "--mode", "synthetic")
    sk("character", "new", "knight", "--describe", "An armoured knight with a blue tabard and a round shield.",
       "--approve", "--project", str(proj))
    port = free_port()
    srv = _serve(proj, port, {})
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)

    def shot(page, name):
        p = shots / f"{name}.png"
        page.screenshot(path=str(p), full_page=False)
        rec.files.append({"scenario": S, "path": p.relative_to(run_dir).as_posix(), "sha256": None,
                          "in_digest": False})

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/")
            ok = page.get_by_test_id("gate-no-token").is_visible(timeout=10000)
            rec.check(S, "without a token the studio shows the gate", ok)
            page.goto(f"http://127.0.0.1:{port}/?token=t0k")
            expect(page.get_by_test_id("topbar")).to_be_visible(timeout=15000)
            rec.check(S, "token stripped from the address bar", "token=" not in page.url, page.url.split("/", 3)[-1])
            expect(page.get_by_test_id("topbar-provider-mode")).to_contain_text("synthetic", ignore_case=True)
            # characters
            page.get_by_test_id("nav-characters").click()
            expect(page.get_by_test_id("characters-screen")).to_be_visible()
            row = page.get_by_test_id("character-row").first
            expect(row).to_be_visible()
            expect(page.get_by_test_id("character-view-side-e").first).to_be_visible()
            rec.check(S, "characters screen lists the approved knight",
                      page.get_by_test_id("character-approved-badge").count() >= 1)
            shot(page, "01-characters")
            # builder: estimate and guide before sending
            page.get_by_test_id("nav-builder").click()
            page.get_by_test_id("builder-action").select_option("attack-melee")
            page.get_by_test_id("builder-plan").click()
            expect(page.get_by_test_id("builder-guide-image")).to_be_visible(timeout=30000)
            route = page.get_by_test_id("builder-route").inner_text()
            rec.check(S, "builder shows route, cost and guide before sending", "guided" in route.lower(), route)
            shot(page, "02-builder")
            page.get_by_test_id("builder-generate").click()
            prog = page.get_by_test_id("job-progress")
            expect(prog).to_be_visible(timeout=15000)
            t0 = time.time()
            state = None
            while time.time() - t0 < 300:
                state = prog.get_attribute("data-state")
                if state in ("done", "failed", "cancelled"):
                    break
                time.sleep(0.5)
            rec.check(S, "job ran to completion with live progress", state == "done", state)
            shot(page, "03-progress")
            # candidates
            page.get_by_test_id("nav-candidates").click()
            cards = page.get_by_test_id("candidate-card")
            expect(cards.first).to_be_visible(timeout=15000)
            rec.check(S, "candidates side by side with scores", cards.count() == 2 and
                      page.get_by_test_id("candidate-score").count() == 2, cards.count())
            shot(page, "04-candidates")
            page.locator("[data-testid=candidate-card][data-winner=false] [data-testid=candidate-use]").first.click()
            expect(page.get_by_test_id("candidate-chosen-badge")).to_be_visible(timeout=30000)
            rec.check(S, "hand-picked winner marked as chosen", page.get_by_test_id("candidate-chosen-badge").count() == 1)
            # sheet review
            page.get_by_test_id("nav-sheet").click()
            expect(page.get_by_test_id("sheet-image")).to_be_visible(timeout=15000)
            rec.check(S, "sheet review overlays the detected grid", page.get_by_test_id("sheet-cell").count() == 6,
                      page.get_by_test_id("sheet-cell").count())
            page.get_by_test_id("sheet-toggle-matte").click(force=True)
            shot(page, "05-sheet")
            # frame editor: duration + nudge, apply -> re-export on disk
            anim = proj / "animations" / "knight-attack-melee-E" / "final" / "animation.json"
            before = json.loads(anim.read_text())
            page.get_by_test_id("nav-frames").click()
            expect(page.get_by_test_id("frames-canvas")).to_be_visible(timeout=15000)
            page.get_by_test_id("frames-timeline-cell").nth(1).click()
            dur = page.get_by_test_id("frames-duration")
            dur.fill("250")
            dur.press("Enter")
            page.get_by_test_id("frames-nudge-right").click()
            page.get_by_test_id("frames-apply").click()
            t0 = time.time()
            after = before
            while time.time() - t0 < 30:
                after = json.loads(anim.read_text())
                if after["durations"] != before["durations"]:
                    break
                time.sleep(0.3)
            rec.check(S, "frame editor edits re-export on disk", after["durations"][1] == 250,
                      {"before": before["durations"], "after": after["durations"]})
            shot(page, "06-frames")
            n_before = json.loads(anim.read_text())["frames"]
            page.get_by_test_id("frames-timeline-cell").nth(2).click()
            page.get_by_test_id("frames-inbetween").click()
            t0 = time.time()
            n_after = n_before
            while time.time() - t0 < 60:
                n_after = json.loads(anim.read_text())["frames"]
                if n_after != n_before:
                    break
                time.sleep(0.3)
            rec.check(S, "in-between inserted from the frame editor", n_after == n_before + 1, [n_before, n_after],
                      ["T4"])
            # findings, export, settings
            page.get_by_test_id("nav-findings").click()
            expect(page.get_by_test_id("findings-q")).to_be_visible(timeout=15000)
            rec.check(S, "findings shows the score", page.get_by_test_id("findings-q").inner_text().strip() != "")
            shot(page, "07-findings")
            page.get_by_test_id("nav-export").click()
            page.get_by_test_id("export-run").click()
            expect(page.get_by_test_id("export-file").first).to_be_visible(timeout=30000)
            rec.check(S, "export lists produced files", page.get_by_test_id("export-file").count() >= 5,
                      page.get_by_test_id("export-file").count())
            shot(page, "08-export")
            page.get_by_test_id("nav-settings").click()
            expect(page.get_by_test_id("settings-key-row").first).to_be_visible(timeout=15000)
            body = page.content()
            from spriteguru import keys

            leaked = [p for p in keys.ENV if keys.get(p) and keys.get(p) in body]
            rec.check(S, "settings never shows key values", not leaked, leaked, ["P7"])
            shot(page, "09-settings")
            page.set_viewport_size({"width": 420, "height": 800})
            page.get_by_test_id("nav-candidates").click()
            wide = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            rec.check(S, "no horizontal page scroll at phone width", not wide)
            shot(page, "10-narrow")
            rec.check(S, "no uncaught page errors", not errors, errors[:3])
            browser.close()
    finally:
        _stop(srv)
