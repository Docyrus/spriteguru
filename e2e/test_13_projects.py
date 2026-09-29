"""The project library and the active character (docs/failure-modes.md P1–P11), through the API of
an engine started with no project open, the way the app now starts."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from conftest import ROOT, free_port, sk


def _start(port: int, env: dict, unset: tuple[str, ...] = ()) -> subprocess.Popen:
    e = {k: v for k, v in {**os.environ, **env}.items() if k not in unset}
    p = subprocess.Popen([sys.executable, "-m", "spriteguru.cli", "serve", "--port", str(port), "--token", "t0k",
                          "--mode", "synthetic"], env=e, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True)
    deadline = time.time() + 90  # a loaded machine (parallel scenarios) can take a while to start a server
    while time.time() < deadline:
        if p.poll() is not None:
            break
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/library", headers={"X-SpriteGuru-Token": "t0k"}).status_code == 200:
                return p
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    p.kill()
    raise RuntimeError("engine did not start")


def _stop(p: subprocess.Popen) -> None:
    p.terminate()
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        p.kill()


def _client(port: int) -> httpx.Client:
    return httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"X-SpriteGuru-Token": "t0k"}, timeout=120)


def _wait_views(c: httpx.Client, name: str) -> dict:
    for _ in range(300):
        ch = c.get(f"/api/characters/{name}").json()
        if ch.get("views"):
            return ch
        time.sleep(0.1)
    return ch


def _first_event(port: int, kind: str) -> dict | None:
    import websockets

    async def run():
        async with websockets.connect(f"ws://127.0.0.1:{port}/api/events?token=t0k") as ws:
            end = time.time() + 5
            while time.time() < end:
                e = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if e.get("type") == kind:
                    return e
        return None

    return asyncio.run(run())


def test_project_library(rec, work):
    S = "projects"
    lib = work / "projects" / "library"
    lib.mkdir(parents=True)
    port = free_port()
    env = {"SPRITEGURU_LIBRARY": str(lib), "SPRITEGURU_SYNTH_DELAY": "0"}
    srv = _start(port, env)
    try:
        with _client(port) as c:
            r = c.get("/api/project")
            blocked = [c.get(u).status_code for u in ("/api/characters", "/api/animations", "/api/ledger")]
            rec.check(S, "with no project open, project endpoints answer 409 and the gallery lists",
                      r.status_code == 409 and r.json()["detail"] == "no project open" and blocked == [409] * 3
                      and c.get("/api/library").json()["projects"] == [], [r.status_code, blocked], ["P1"])

            r = c.post("/api/library/projects", json={"name": "Space Shooter!", "style": {"kind": "hd-cartoon"},
                                                       "engine": "phaser"})
            ss = r.json()
            folder = Path(ss["project"]["path"])
            rec.check(S, "a new project gets its own folder in the library and opens",
                      r.status_code == 200 and folder == (lib / "space-shooter.sprites").resolve()
                      and (folder / "project.json").is_file() and ss["project"]["name"] == "Space Shooter!",
                      [folder.name, ss["project"]["name"]], ["P2"])
            codes = [c.post("/api/library/projects", json=b).status_code for b in (
                {"name": "space shooter", "style": {"kind": "pixel"}},
                {"name": "///", "style": {"kind": "pixel"}},
                {"name": "", "style": {"kind": "pixel"}},
                {"name": "x", "style": {"kind": "pixel"}, "engine": "unreal"},
                {"name": "y", "style": {"kind": "pixel"}, "location": str(lib / "nowhere")})]
            rec.check(S, "clashing and unusable project names are refused", codes == [409, 400, 400, 400, 400],
                      codes, ["P2"])

            # everything a project makes lives in its folder
            c.post("/api/characters", json={"name": "knight", "description": "An armoured knight with a blue tabard."}
                   ).raise_for_status()
            _wait_views(c, "knight")
            rec.check(S, "characters are saved inside the project's folder, and a new one becomes active",
                      (folder / "characters" / "knight" / "character.json").is_file()
                      and c.get("/api/project").json()["active_character"] == "knight", None, ["P8"])
            bad = c.put("/api/project/active-character", json={"name": "nobody"})
            rec.check(S, "an unknown active character is refused and the choice kept",
                      bad.status_code == 404 and c.get("/api/project").json()["active_character"] == "knight",
                      bad.status_code, ["P10"])

            td = c.post("/api/library/projects", json={"name": "Tower Defense", "style": {"kind": "pixel",
                                                       "pixel_height": 32}, "engine": "godot"}).json()
            rec.check(S, "switching gives the other project's characters and its own active character",
                      c.get("/api/characters").json() == [] and td["project"]["active_character"] is None
                      and c.get("/api/ledger").json()["summary"]["calls"] == 0, td["project"]["name"], ["P5", "P8"])
            ev = _first_event(port, "project_changed")
            rec.check(S, "a switch is announced to open studios", ev is not None and ev["name"] == "Tower Defense",
                      ev and ev["name"], ["P5"])

            listing = c.get("/api/library").json()
            cards = {p["name"]: p for p in listing["projects"]}
            ss_card = cards["Space Shooter!"]
            thumb = c.get(ss_card["thumbnail"]) if ss_card["thumbnail"] else None
            rec.check(S, "the gallery lists both projects, newest first, with counts and a thumbnail",
                      [p["name"] for p in listing["projects"]] == ["Tower Defense", "Space Shooter!"]
                      and ss_card["characters"] == 1 and cards["Tower Defense"]["current"]
                      and thumb is not None and thumb.status_code == 200 and thumb.headers["content-type"] == "image/png",
                      [(p["name"], p["characters"]) for p in listing["projects"]], ["P3"])
            codes = [c.post("/api/library/open", json={"path": str(ROOT / "src")}).status_code,
                     c.post("/api/library/open", json={"id": "000000000000"}).status_code,
                     c.get("/api/library/projects/000000000000/thumbnail").status_code]
            rec.check(S, "only project folders open; unknown ids are 404", codes == [400, 404, 404], codes, ["P3"])

            r = c.post("/api/library/open", json={"id": ss_card["id"]})
            rec.check(S, "reopening restores that project's active character",
                      r.status_code == 200 and r.json()["active_character"] == "knight", r.json().get("active_character"),
                      ["P8"])

            # P4: a running job blocks the switch
            c.post("/api/animations", json={"character": "knight", "action": "attack-melee"}).raise_for_status()
    finally:
        _stop(srv)

    # restart with a slow simulator so a job is still running when the switch is asked for
    srv = _start(port, {**env, "SPRITEGURU_SYNTH_DELAY": "3"})
    try:
        with _client(port) as c:
            c.post("/api/library/open", json={"id": ss_card["id"]}).raise_for_status()
            rec.check(S, "the active character survives an engine restart",
                      c.get("/api/project").json()["active_character"] == "knight", None, ["P8"])
            job = c.post("/api/animations/knight-attack-melee-E/jobs", json={"seed": 1}).json()
            time.sleep(0.5)
            busy = c.post("/api/library/open", json={"id": cards["Tower Defense"]["id"]})
            rec.check(S, "switching while a job runs is refused, naming the job",
                      busy.status_code == 409 and job["id"] in busy.json()["detail"],
                      busy.json().get("detail", "").replace(job["id"], "<job>"), ["P4"])
            for _ in range(600):
                if c.get(f"/api/jobs/{job['id']}").json()["state"] in ("done", "failed"):
                    break
                time.sleep(0.2)
            ok = c.post("/api/library/open", json={"id": cards["Tower Defense"]["id"]})
            rec.check(S, "after the job finishes the switch goes through", ok.status_code == 200, ok.status_code, ["P4"])

            # P9: the active character's folder removed on disk
            shutil.rmtree(folder / "characters" / "knight")
            c.post("/api/library/open", json={"id": ss_card["id"]}).raise_for_status()
            rec.check(S, "an active character that no longer exists reads as none",
                      c.get("/api/project").json()["active_character"] is None, None, ["P9"])

            # P6: moved projects are marked missing; removing a card never deletes files
            moved = lib.parent / "moved-td.sprites"
            c.post("/api/library/open", json={"id": ss_card["id"]})
            shutil.move(str(lib / "tower-defense.sprites"), str(moved))
            listing = c.get("/api/library").json()
            ext = work / "projects" / "elsewhere"
            ext.mkdir()
            outside = c.post("/api/library/projects", json={"name": "Outside", "style": {"kind": "vector"},
                                                            "location": str(ext)}).json()
            miss = next(p for p in listing["projects"] if p["name"] == "tower-defense")
            code = c.post("/api/library/open", json={"id": miss["id"]}).status_code
            rec.check(S, "a moved project shows as missing and cannot open", miss["missing"] and code == 409,
                      [miss["missing"], code], ["P6"])
            out_id = outside["card"]["id"]
            c.post("/api/library/open", json={"id": ss_card["id"]}).raise_for_status()
            c.delete(f"/api/library/projects/{out_id}").raise_for_status()
            names = [p["name"] for p in c.get("/api/library").json()["projects"]]
            rec.check(S, "a project outside the library is listed, and removing it from the list keeps its files",
                      "Outside" not in names and (ext / "outside.sprites" / "project.json").is_file(), names, ["P6"])

            # P7: a broken project.json does not break the gallery
            broken = lib / "broken.sprites"
            broken.mkdir()
            (broken / "project.json").write_text("{not json")
            listing = c.get("/api/library")
            card = next((p for p in listing.json()["projects"] if p["path"].endswith("broken.sprites")), {})
            rec.check(S, "a broken project shows its error and the gallery still lists",
                      listing.status_code == 200 and bool(card.get("error")) and len(listing.json()["projects"]) >= 2,
                      card.get("error"), ["P7"])
    finally:
        _stop(srv)

    # P11: the old single-project launcher's last project shows as a recent project
    home = work / "projects" / "home"
    (home / ".config" / "spriteguru").mkdir(parents=True)
    legacy = work / "projects" / "Legacy.sprites"
    sk("init", str(legacy), "--mode", "synthetic")
    (home / ".config" / "spriteguru" / "last_project").write_text(str(legacy))
    srv = _start(port, {"HOME": str(home)}, unset=("SPRITEGURU_LIBRARY",))
    try:
        with _client(port) as c:
            listing = c.get("/api/library").json()
            rec.check(S, "the previous launcher's project appears in the gallery",
                      any(Path(p["path"]) == legacy.resolve() for p in listing["projects"])
                      and Path(listing["root"]).is_relative_to(home),
                      Path(listing["root"]).relative_to(home).as_posix() if Path(listing["root"]).is_relative_to(home)
                      else listing["root"], ["P11"])
    finally:
        _stop(srv)


def _job_done(c: httpx.Client, anim: str) -> None:
    job = c.post(f"/api/animations/{anim}/jobs", json={"seed": 0}).json()
    for _ in range(900):
        if c.get(f"/api/jobs/{job['id']}").json()["state"] in ("done", "failed"):
            return
        time.sleep(0.2)


def test_studio_projects(rec, work, run_dir):
    """The requested studio flow in headless Chromium: the app opens on the gallery, a project is
    created from it, the header switches projects and the active character, and every tab follows
    the active character (P1, P5, P8)."""
    S = "studio-projects"
    from playwright.sync_api import expect, sync_playwright

    lib = work / "studio-projects" / "library"
    lib.mkdir(parents=True)
    port = free_port()
    srv = _start(port, {"SPRITEGURU_LIBRARY": str(lib)})
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)

    def shot(page, name):
        p = shots / f"{name}.png"
        page.screenshot(path=str(p), full_page=False)
        rec.files.append({"scenario": S, "path": p.relative_to(run_dir).as_posix(), "sha256": None, "in_digest": False})

    try:
        with sync_playwright() as pw, _client(port) as c:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            def pick_character(name):
                page.get_by_test_id("header-character").click()
                page.locator(f"[data-testid=header-character-item][data-name='{name}']").click()
                expect(page.get_by_test_id("header-character")).to_have_attribute("data-name", name, timeout=10000)

            page.goto(f"http://127.0.0.1:{port}/?token=t0k")
            expect(page.get_by_test_id("projects-screen")).to_be_visible(timeout=15000)
            rec.check(S, "the app opens on the project gallery, empty on first run",
                      page.get_by_test_id("project-card").count() == 0, page.url.split("#")[-1], ["P1"])
            shot(page, "01-empty-gallery")

            # create the first project from the gallery
            page.get_by_test_id("projects-new").click()
            page.get_by_test_id("project-form-name").fill("Robot Wars")
            page.get_by_test_id("project-form-style-hd-cartoon").click()
            page.get_by_test_id("project-form-engine").select_option("phaser")
            page.get_by_test_id("project-form-submit").click()
            expect(page.get_by_test_id("characters-screen")).to_be_visible(timeout=15000)
            expect(page.get_by_test_id("header-project")).to_contain_text("Robot Wars")
            cfg = json.loads((lib / "robot-wars.sprites" / "project.json").read_text())
            form = page.get_by_test_id("character-form")
            try:
                expect(form).to_be_visible(timeout=15000)  # the form opens once the empty project has loaded
            except AssertionError:
                pass
            rec.check(S, "a project created in the gallery opens on Characters with the create form",
                      cfg["engine"] == "phaser" and form.is_visible(), [cfg["name"], cfg["engine"]], ["P1", "P2"])

            # the first character, through the form; it becomes the active character
            page.get_by_test_id("character-form-name").fill("mech")
            page.get_by_test_id("character-form-description").fill("A bulky red battle robot with a cannon arm.")
            page.get_by_test_id("character-form-submit").click()
            expect(page.get_by_test_id("header-character")).to_have_attribute("data-name", "mech", timeout=30000)
            # a second subject arrives from elsewhere (API); open studios follow the new active subject
            c.post("/api/characters", json={"name": "drone", "kind": "vehicle",
                                            "description": "A small grey hover drone with twin rotors."}).raise_for_status()
            expect(page.get_by_test_id("header-character")).to_have_attribute("data-name", "drone", timeout=30000)
            rec.check(S, "the header shows the active character and follows changes from the engine", True,
                      None, ["P8"])
            for n in ("mech", "drone"):
                for _ in range(300):
                    if c.get(f"/api/characters/{n}").json().get("views"):
                        break
                    time.sleep(0.1)
                c.post(f"/api/characters/{n}/approve").raise_for_status()
            c.post("/api/animations", json={"character": "mech", "action": "attack-melee"}).raise_for_status()
            c.post("/api/animations", json={"character": "drone", "action": "fire"}).raise_for_status()
            _job_done(c, "mech-attack-melee-E")
            _job_done(c, "drone-fire-E")

            # every tab follows the active character; switching it keeps the tab
            page.get_by_test_id("nav-builder").click()
            pick_character("mech")
            expect(page.get_by_test_id("builder-character")).to_have_attribute("data-name", "mech", timeout=10000)
            pick_character("drone")
            expect(page.get_by_test_id("builder-action")).to_have_attribute("data-kind", "vehicle", timeout=10000)
            rec.check(S, "the builder works on the active character and stays open when it changes",
                      page.get_by_test_id("builder-character").get_attribute("data-name") == "drone"
                      and "#/builder" in page.url, page.url.split("#")[-1])
            page.get_by_test_id("nav-sheet").click()
            sel = page.get_by_test_id("context-animation-select")
            expect(sel).to_have_value("drone-fire-E", timeout=15000)
            pick_character("mech")
            expect(sel).to_have_value("mech-attack-melee-E", timeout=15000)
            opts = sel.locator("option").evaluate_all("els => els.map(e => e.value)")
            rec.check(S, "animation tabs list only the active character's animations and follow a switch",
                      opts == ["mech-attack-melee-E"] and "#/sheet" in page.url, opts)
            shot(page, "02-sheet-follows-character")
            rec.check(S, "the active character is saved in the project",
                      c.get("/api/project").json()["active_character"] == "mech", None, ["P8"])

            # a second project from the header's project switcher
            page.get_by_test_id("header-project").click()
            page.get_by_test_id("header-project-new").click()
            page.get_by_test_id("project-form-name").fill("Castle Siege")
            page.get_by_test_id("project-form-style-pixel").click()
            page.get_by_test_id("project-form-submit").click()
            expect(page.get_by_test_id("header-project")).to_contain_text("Castle Siege", timeout=15000)
            rec.check(S, "a new project from the header opens with no character of the old one",
                      page.get_by_test_id("header-character").get_attribute("data-name") in (None, "")
                      and c.get("/api/characters").json() == [], None, ["P5"])
            # back to the first project; its active character comes back
            page.get_by_test_id("header-project").click()
            page.locator("[data-testid=header-project-item]", has_text="Robot Wars").click()
            expect(page.get_by_test_id("header-project")).to_contain_text("Robot Wars", timeout=15000)
            expect(page.get_by_test_id("header-character")).to_have_attribute("data-name", "mech", timeout=15000)
            rec.check(S, "switching back restores that project's active character", True, None, ["P8"])
            page.get_by_test_id("header-project").click()
            page.get_by_test_id("header-project-home").click()
            expect(page.get_by_test_id("projects-screen")).to_be_visible(timeout=10000)
            names = sorted(page.get_by_test_id("project-card").evaluate_all("els => els.map(e => e.dataset.name)"))
            rec.check(S, "All projects shows both projects as cards", names == ["Castle Siege", "Robot Wars"], names)
            shot(page, "03-gallery")
            rec.check(S, "no page errors", not errors, errors[:3])
            browser.close()
    finally:
        _stop(srv)
