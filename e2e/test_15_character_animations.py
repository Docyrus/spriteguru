"""The active character's finished animations on the Characters page (docs/failure-modes.md A1–A8): only
that character's exported animations are listed, each plays in place at its exported durations, and
an Open menu takes it to its Sheet, Candidates, Frames, Findings and Export tabs."""

from __future__ import annotations

import json
import time

from conftest import free_port, sk
from test_06_jobs import _serve, _stop
from test_13_projects import _client

# records every data-frame change of a player for `ms` milliseconds: [[frame, t_ms], ...]
WATCH = """(el, ms) => new Promise((res) => {
  const log = [[+el.dataset.frame, 0]];
  const t0 = performance.now();
  const mo = new MutationObserver(() => log.push([+el.dataset.frame, performance.now() - t0]));
  mo.observe(el, { attributes: true, attributeFilter: ['data-frame'] });
  setTimeout(() => { mo.disconnect(); res(log); }, ms);
})"""


def _dwell(log: list) -> dict[int, float]:
    """Median time each frame stays up, from complete visits only (not the first or the unfinished last)."""
    seen: dict[int, list[float]] = {}
    for (f, t), (_, t2) in zip(log[1:], log[2:]):
        seen.setdefault(int(f), []).append(t2 - t)
    return {f: sorted(v)[len(v) // 2] for f, v in seen.items()}


def _jobs_done(c, anims: list[str]) -> dict[str, str]:
    ids = {a: c.post(f"/api/animations/{a}/jobs", json={"seed": 0}).json()["id"] for a in anims}
    states: dict[str, str] = {}
    for _ in range(1500):
        states = {a: c.get(f"/api/jobs/{j}").json()["state"] for a, j in ids.items()}
        if all(s in ("done", "failed", "cancelled") for s in states.values()):
            break
        time.sleep(0.2)
    return states


def test_character_animations(rec, work, run_dir):
    S = "character-animations"
    from playwright.sync_api import expect, sync_playwright

    proj = work / "character-animations" / "C.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "phaser", "--mode", "synthetic")
    sk("character", "new", "knight", "--describe", "An armoured knight with a blue tabard and a round shield.",
       "--approve", "--project", str(proj))
    sk("character", "new", "blast", "--kind", "effect", "--blend", "add", "--describe",
       "A blue glowing energy explosion with sparks.", "--approve", "--project", str(proj))
    port = free_port()
    srv = _serve(proj, port, {})
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)

    def shot(page, name):
        p = shots / f"{name}.png"
        page.screenshot(path=str(p), full_page=False)
        rec.files.append({"scenario": S, "path": p.relative_to(run_dir).as_posix(), "sha256": None, "in_digest": False})

    def final(anim):
        return json.loads((proj / "animations" / anim / "final" / "animation.json").read_text())

    try:
        with sync_playwright() as pw, _client(port) as c:
            for ch, act in (("knight", "walk"), ("knight", "attack-melee"), ("blast", "impact"), ("knight", "idle")):
                c.post("/api/animations", json={"character": ch, "action": act}).raise_for_status()
            # idle is planned but never run: it has no export (A2)
            states = _jobs_done(c, ["knight-walk-E", "knight-attack-melee-E", "blast-impact-E"])
            rec.check(S, "setup: three animations exported", set(states.values()) == {"done"}, states)
            c.put("/api/project/active-character", json={"name": "knight"}).raise_for_status()

            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            def cards():
                return sorted(page.get_by_test_id("character-animation").evaluate_all("els => els.map(e => e.dataset.anim)"))

            def card(anim):
                return page.locator(f"[data-testid=character-animation][data-anim='{anim}']")

            def player(anim):
                return card(anim).get_by_test_id("character-animation-player")

            def watch(anim, ms):
                return player(anim).evaluate(WATCH, ms)

            def pick_character(name):
                page.get_by_test_id("header-character").click()
                page.locator(f"[data-testid=header-character-item][data-name='{name}']").click()
                expect(page.get_by_test_id("header-character")).to_have_attribute("data-name", name, timeout=10000)

            page.goto(f"http://127.0.0.1:{port}/?token=t0k")
            expect(page.get_by_test_id("topbar")).to_be_visible(timeout=15000)
            page.get_by_test_id("nav-characters").click()
            shelf = page.get_by_test_id("character-animations")
            expect(shelf).to_have_attribute("data-character", "knight", timeout=15000)
            expect(page.get_by_test_id("character-animation")).to_have_count(2, timeout=15000)
            rec.check(S, "the active character's exported animations are listed, and only those",
                      cards() == ["knight-attack-melee-E", "knight-walk-E"], cards(), ["A1", "A2"])
            count = page.get_by_test_id("character-animations-count").inner_text()
            rec.check(S, "an animation without an export is counted, not shown as a player",
                      "2 finished" in count and "1 not exported yet" in count, count, ["A2"])

            # A5: a card loads only its first frame until played
            walk_n = final("knight-walk-E")["frames"]
            imgs = player("knight-walk-E").locator("img")
            expect(imgs.first).to_have_js_property("complete", True, timeout=15000)
            first_ok = imgs.first.evaluate("i => i.naturalWidth > 0")
            rec.check(S, "before playing, a card shows its first frame and loads no other",
                      imgs.count() == 1 and first_ok and walk_n > 1, [imgs.count(), first_ok, walk_n], ["A5"])
            shot(page, "01-animations")

            # play, then pause (A8)
            card("knight-walk-E").get_by_test_id("character-animation-play").click()
            expect(player("knight-walk-E")).to_have_attribute("data-ready", "true", timeout=15000)
            log = watch("knight-walk-E", 1500)
            visited = sorted({f for f, _ in log})
            rec.check(S, "play runs through every frame of the export", visited == list(range(walk_n)),
                      [walk_n, visited], ["A4"])
            rec.check(S, "and every frame has loaded once playing", imgs.count() == walk_n, imgs.count(), ["A5"])
            shot(page, "02-playing")
            card("knight-walk-E").get_by_test_id("character-animation-play").click()
            expect(player("knight-walk-E")).to_have_attribute("data-playing", "false")
            paused = watch("knight-walk-E", 900)
            rec.check(S, "pause stops on the current frame", len(paused) == 1, len(paused), ["A8"])

            # A3 + A4: a duration set in the frame editor reaches the player without a reload
            edited = c.post("/api/animations/knight-walk-E/edits",
                            json={"edits": [{"op": "duration", "frame": 1, "ms": 420}]})
            rec.check(S, "setup: frame 1 of the walk now lasts 420 ms", edited.status_code == 200
                      and final("knight-walk-E")["durations"][1] == 420, edited.status_code)
            before = player("knight-walk-E").locator("img").first.get_attribute("src")
            expect(player("knight-walk-E").locator("img").first).not_to_have_attribute("src", before, timeout=15000)
            after = player("knight-walk-E").locator("img").first.get_attribute("src")
            card("knight-walk-E").get_by_test_id("character-animation-play").click()
            expect(player("knight-walk-E")).to_have_attribute("data-ready", "true", timeout=15000)
            dw = _dwell(watch("knight-walk-E", 3000))
            base = final("knight-walk-E")["durations"][0]
            others = sorted(v for f, v in dw.items() if f not in (1,))
            v = lambda u: u.split("&v=")[-1]  # noqa: E731
            rec.check(S, "a rewritten export reloads the card's frames (new version in the URL)",
                      before.split("&v=")[0] == after.split("&v=")[0] and v(before) != v(after), None, ["A3"])
            rec.check(S, "each frame stays up for its exported duration",
                      1 in dw and dw[1] >= 330 and others and others[len(others) // 2] < 0.6 * dw[1]
                      and others[len(others) // 2] >= base * 0.7,
                      {k: round(v) for k, v in sorted(dw.items())}, ["A4"], stable=False)
            card("knight-walk-E").get_by_test_id("character-animation-play").click()

            # A3: an in-between adds a frame; the card follows the new count
            atk = "knight-attack-melee-E"
            n0 = final(atk)["frames"]
            r = c.post(f"/api/animations/{atk}/inbetween", json={"after": 1})
            expect(player(atk)).to_have_attribute("data-frames", str(n0 + 1), timeout=30000)
            rec.check(S, "an in-between from anywhere updates the card's frame count",
                      r.status_code == 200 and final(atk)["frames"] == n0 + 1, [n0, final(atk)["frames"]], ["A3"])

            # A4: a one-shot holds its last frame before it starts again; a loop does not
            card(atk).get_by_test_id("character-animation-play").click()
            expect(player(atk)).to_have_attribute("data-ready", "true", timeout=15000)
            fa = final(atk)
            da = _dwell(watch(atk, 4500))
            last = fa["frames"] - 1
            rec.check(S, "a one-shot holds its last frame before restarting",
                      card(atk).get_attribute("data-loop") == "false" and last in da
                      and da[last] >= fa["durations"][last] + 450,
                      {"last": round(da.get(last, 0)), "duration": fa["durations"][last]}, ["A4"], stable=False)
            wl = final("knight-walk-E")
            rec.check(S, "a loop goes straight from its last frame to its first",
                      card("knight-walk-E").get_attribute("data-loop") == "true"
                      and dw.get(walk_n - 1, 1e9) < wl["durations"][walk_n - 1] + 300,
                      round(dw.get(walk_n - 1, -1)), ["A4"], stable=False)
            card(atk).get_by_test_id("character-animation-play").click()

            # play all / pause all
            page.get_by_test_id("character-animations-play-all").click()
            playing = page.locator("[data-testid=character-animation-player][data-playing=true]")
            expect(playing).to_have_count(2, timeout=5000)
            for a in ("knight-walk-E", atk):
                expect(player(a)).to_have_attribute("data-ready", "true", timeout=15000)
            moved = [len(watch(a, 1200)) > 1 for a in ("knight-walk-E", atk)]
            rec.check(S, "Play all plays every card", all(moved), moved)
            page.get_by_test_id("character-animations-play-all").click()
            expect(playing).to_have_count(0, timeout=5000)
            rec.check(S, "Pause all stops them", True)

            # A1, A6, A8: switch to the effect while a card plays; the list follows, the glow is on the dark stage
            card("knight-walk-E").get_by_test_id("character-animation-play").click()
            page.locator("[data-testid=character-card][data-name=blast]").click()
            expect(shelf).to_have_attribute("data-character", "blast", timeout=10000)
            expect(page.get_by_test_id("character-animation")).to_have_count(1, timeout=10000)
            stage = card("blast-impact-E").get_by_test_id("character-animation-play")
            rows = page.get_by_test_id("character-row").evaluate_all("els => els.map(e => e.dataset.name)")
            rec.check(S, "switching the character switches the list, and the page shows only that character",
                      cards() == ["blast-impact-E"] and rows == ["blast"], [cards(), rows], ["A1"])
            rec.check(S, "an additive effect plays on the dark, screen-blended stage",
                      card("blast-impact-E").get_attribute("data-blend") == "add"
                      and "additive" in (stage.get_attribute("class") or ""), stage.get_attribute("class"), ["A6"])
            stage.click()
            expect(player("blast-impact-E")).to_have_attribute("data-ready", "true", timeout=15000)
            rec.check(S, "the effect plays", len(watch("blast-impact-E", 1200)) > 1, None, ["A6"])
            shot(page, "03-effect")

            # A7: every Open entry opens that tab on the picked animation, never the newest one
            pick_character("knight")
            expect(page.get_by_test_id("character-animation")).to_have_count(2, timeout=10000)
            page.locator(f"[data-testid=character-animation][data-anim='{atk}'] [data-testid=character-animation-open]").click()
            labels = page.get_by_test_id("character-animation-menu-item").all_inner_texts()
            rec.check(S, "the Open menu lists the five animation tabs",
                      [l.strip() for l in labels] == ["Candidates", "Sheet", "Frames", "Findings", "Export"], labels, ["A7"])
            shot(page, "04-open-menu")
            page.keyboard.press("Escape")
            opened = []
            for i, screen in enumerate(("sheet", "candidates", "frames", "findings", "export")):
                target = ("knight-walk-E", atk)[i % 2]
                page.get_by_test_id("nav-characters").click()
                expect(page.get_by_test_id("character-animation")).to_have_count(2, timeout=10000)
                page.locator(f"[data-testid=character-animation][data-anim='{target}'] "
                             "[data-testid=character-animation-open]").click()
                page.locator(f"[data-testid=character-animation-menu-item][data-screen={screen}]").click()
                expect(page.get_by_test_id(f"{screen}-screen")).to_be_visible(timeout=15000)
                sel = page.get_by_test_id("context-animation-select")
                expect(sel).to_have_value(target, timeout=15000)
                rail = page.get_by_test_id("nav-findings" if screen != "findings" else "nav-export").get_attribute("href")
                opened.append([screen, page.url.split("#")[-1], rail])
            rec.check(S, "each entry opens its tab on the picked animation, and the rail's tabs follow it",
                      all(url == f"/{s}/{t}" and rail.endswith(f"/{t}")
                          for (s, url, rail), t in zip(opened, ["knight-walk-E", atk] * 3)), opened, ["A7"])
            rec.check(S, "no page errors", not errors, errors[:3], ["A8"])
            browser.close()
    finally:
        _stop(srv)
