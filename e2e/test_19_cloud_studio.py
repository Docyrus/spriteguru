"""Cloud membership in the studio (docs/cloud-integration-plan.md scenario 12): the sync pill, the Sync
panel's conflict view (images side by side, a JSON field diff, keep one side), the "In the cloud"
gallery row with its download, linking from Settings, and card badges. Machine B works through the
CLI while machine A's studio runs in headless Chromium against the fake cloud."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from PIL import Image, ImageOps

from cloudhelp import Engine, browser_sign_in, cli, cli_login, machine_env
from conftest import free_port
from fakecloud import FakeCloud
from test_18_cloud_sync import _make_project, _tree


def test_cloud_studio(rec, work, run_dir):
    S = "cloud-studio-sync"
    from playwright.sync_api import expect, sync_playwright

    fc = FakeCloud().start()
    user = fc.add_user("studio@example.com", "Studio Sync", pro=True)
    fc.add_team("Lapsed Studio", {user.id: "member"}, active=False)
    lib_a, lib_b = work / S / "machine-a", work / S / "machine-b"
    env_a, env_b = machine_env(fc.url, lib_a), machine_env(fc.url, lib_b)
    eng = Engine(free_port(), env_a, work / S / "engine-a.log")
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)

    def shot(page, name):
        p = shots / f"{name}.png"
        page.screenshot(path=str(p))
        rec.files.append({"scenario": S, "path": p.relative_to(run_dir).as_posix(), "sha256": None, "in_digest": False})

    try:
        with eng.client() as c, sync_playwright() as pw:
            browser_sign_in(c)
            proj = _make_project(c, "Studio Quest")
            pid = c.post("/api/project/sync/link", json={"owner": "me"}).json()["project"]["id"]
            cli_login(env_b)
            b_proj = Path(cli(env_b, "cloud", "download", pid)[1]["path"])

            # both machines change the knight's description and side view
            cj, img = "characters/knight/character.json", "characters/knight/ref/side-e.png"
            for root, who in ((b_proj, "B"), (proj, "A")):
                d = json.loads((root / cj).read_text())
                d["description"] = f"A knight redrawn on machine {who}."
                (root / cj).write_text(json.dumps(d, indent=2) + "\n")
                im = Image.open(root / img).convert("RGBA")
                r, g, b, a = im.split()
                shifted = Image.merge("RGBA", (b, r, g, a)) if who == "B" else Image.merge("RGBA", (g, b, r, a))
                shifted.save(root / img)
                if who == "B":
                    cli(env_b, "sync", "--project", str(b_proj))
            c.post("/api/project/sync/now")

            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 950})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{eng.url()}/?token=t0k#/characters")
            pill = page.get_by_test_id("sync-pill")
            expect(pill).to_have_attribute("data-state", "conflicts", timeout=15000)
            rec.check(S, "the header's sync pill counts the conflicts", pill.inner_text().strip() == "2 conflicts",
                      pill.inner_text().strip(), ["C8"])
            pill.click()
            panel = page.get_by_test_id("sync-panel")
            group = panel.get_by_test_id("conflict-group")
            expect(group).to_have_count(1, timeout=10000)
            files = group.get_by_test_id("conflict-file")
            png_row = group.locator(f"[data-testid=conflict-file][data-path='{img}']")
            imgs = png_row.locator("img")
            expect(imgs).to_have_count(2)
            page.wait_for_function("(els) => els.every((i) => i.complete && i.naturalWidth > 0)", arg=imgs.element_handles())
            diff = group.get_by_test_id("conflict-json-diff")
            expect(diff).to_contain_text("description", timeout=10000)
            rec.check(S, "the conflict view groups the knight's files; images show both versions, JSON a field diff",
                      group.get_attribute("data-group") == "characters/knight/" and files.count() == 2
                      and "machine A" in diff.inner_text() and "machine B" in diff.inner_text(),
                      [group.get_attribute("data-group"), files.count()], ["C8"])
            shot(page, "01-conflict-view")

            group.get_by_test_id("conflict-group-theirs").click()
            expect(pill).to_have_attribute("data-state", "synced", timeout=15000)
            page.get_by_test_id("sync-panel-close").click()
            expect(page.locator("body")).to_contain_text("A knight redrawn on machine B.", timeout=15000)
            rec.check(S, "keeping the cloud's takes machine B's files, and the studio reloads the character from them",
                      _tree(proj)[cj] == _tree(b_proj)[cj] and _tree(proj)[img] == _tree(b_proj)[img], None, ["C30"])

            # "In the cloud": a project machine B made that isn't on this machine yet
            code, _, _ = cli(env_b, "init", str(lib_b / "space.sprites"), "--style", "pixel", "--mode", "synthetic")
            code, linked, _ = cli(env_b, "sync", "--link", "me", "--project", str(lib_b / "space.sprites"))
            page.goto(f"{eng.url()}/?token=t0k#/projects")
            row = page.get_by_test_id("cloud-row")
            card = row.locator("[data-testid=cloud-card][data-name='space']")
            expect(card).to_be_visible(timeout=15000)
            mine = page.locator("[data-testid=project-card][data-name='Studio Quest']")
            rec.check(S, "the gallery lists cloud projects not on this machine; local ones show their sync state",
                      row.get_by_test_id("cloud-card").count() == 1
                      and card.get_by_test_id("cloud-card-workspace").inner_text() == "Personal"
                      and mine.get_by_test_id("project-card-sync").inner_text() == "Synced",
                      row.get_by_test_id("cloud-card").count(), [])
            shot(page, "02-in-the-cloud")
            card.get_by_test_id("cloud-card-download").click()
            expect(page.get_by_test_id("characters-screen")).to_be_visible(timeout=30000)
            opened = Path(c.get("/api/project").json()["path"])
            rec.check(S, "Download puts the project in the library, opens it, and it matches the cloud",
                      opened.parent == lib_a.resolve() and opened.name == "space.sprites"
                      and _tree(opened) == _tree(lib_b / "space.sprites"), opened.name, ["C24"])

            # linking from Settings: Personal, and an inactive team offered but disabled
            c.post("/api/library/projects", json={"name": "Fresh", "style": {"kind": "pixel"}, "engine": "godot"})
            c.post("/api/library/open", json={"path": str(opened)})
            page.goto(f"{eng.url()}/?token=t0k#/projects")
            fresh = page.locator("[data-testid=project-card][data-name='Fresh']")
            fresh.get_by_test_id("project-card-menu").click()
            page.get_by_test_id("project-card-sync-menu").click()
            picker = page.get_by_test_id("sync-link-picker")
            expect(picker).to_be_visible(timeout=15000)
            options = picker.get_by_test_id("sync-link-workspace")
            lapsed = picker.locator("[data-testid=sync-link-workspace] input").nth(1)
            rec.check(S, "Sync to cloud… on a gallery card opens that project's link picker, which offers Personal and "
                         "shows an inactive team disabled with the reason",
                      c.get("/api/project").json()["name"] == "Fresh" and options.count() == 2 and lapsed.is_disabled() and "no active Team plan" in options.nth(1).inner_text(),
                      options.count(), ["C15"])
            shot(page, "03-link-picker")
            picker.get_by_test_id("sync-link-submit").click()
            expect(page.get_by_test_id("sync-pill")).to_have_attribute("data-state", "synced", timeout=20000)
            rec.check(S, "Sync to cloud links the project and pushes it", json.loads(
                (Path(c.get("/api/project").json()["path"]) / "project.json").read_text()).get("cloud") is not None,
                      None, [])
            rec.check(S, "no page errors", not errors, errors[:3])
            browser.close()
    finally:
        eng.stop()
        fc.stop()
