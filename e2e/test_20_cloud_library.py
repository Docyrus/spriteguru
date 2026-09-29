"""Cloud membership, phase 6 (docs/cloud-integration-plan.md scenario 10): the asset library. A
synced animation is published server-side (the fake records `from-project`, no uploads); an
unsynced character is uploaded; importing places items by kind and renames a clashing character
with its paths; plans gate it; and the studio's Library screen and "Add to library" dialog work."""

from __future__ import annotations

import json
from pathlib import Path

from cloudhelp import Engine, browser_sign_in, cli, cli_login, machine_env
from conftest import free_port
from fakecloud import FakeCloud
from test_18_cloud_sync import _make_project, _tree


def test_cloud_library(rec, work, run_dir):
    S = "cloud-library"
    from playwright.sync_api import expect, sync_playwright

    fc = FakeCloud().start()
    user = fc.add_user("lib@example.com", "Library Test", pro=True)
    team = fc.add_team("Aurora Studio", {user.id: "admin"})
    lib_a, lib_b = work / S / "machine-a", work / S / "machine-b"
    env_a, env_b = machine_env(fc.url, lib_a), machine_env(fc.url, lib_b)
    eng = Engine(free_port(), env_a, work / S / "engine.log")
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)

    def shot(page, name):
        p = shots / f"{name}.png"
        page.screenshot(path=str(p))
        rec.files.append({"scenario": S, "path": p.relative_to(run_dir).as_posix(), "sha256": None, "in_digest": False})

    try:
        with eng.client() as c:
            browser_sign_in(c)
            proj = _make_project(c, "Library Quest")
            pid = c.post("/api/project/sync/link", json={"owner": "me"}).json()["project"]["id"]

            uploads = len(fc.events("upload"))
            r = c.post("/api/project/library/publish", json={"prefix": "animations/knight-walk-E/final/",
                                                               "name": "Knight walk", "kind": "animation"})
            walk = r.json()
            fp = fc.events("library_from_project")
            rec.check(S, "publishing a synced animation references the project server-side; nothing is uploaded",
                      r.status_code == 200 and walk["from_project"] and fp and fp[-1]["prefix"] == "animations/knight-walk-E/final/"
                      and fp[-1]["project"] == pid and len(fc.events("upload")) == uploads,
                      [walk.get("from_project"), len(fc.events("upload")) - uploads], [])

            # an unlinked project: its character is uploaded, then published
            proj2 = _make_project(c, "Loose Ends")
            (proj2 / "characters" / "knight" / "notes.txt").write_text("only in this project")
            before = len(fc.events("upload"))
            r = c.post("/api/project/library/publish", json={"prefix": "characters/knight/", "name": "Knight",
                                                               "kind": "character"})
            loose = r.json()
            item = fc.library[loose["item"]["id"]]
            files = {f["path"] for f in fc.library_files[item["id"]]}
            local = {p[len("characters/knight/"):] for p in _tree(proj2) if p.startswith("characters/knight/")}
            rec.check(S, "publishing from an unsynced project uploads the folder's files, then creates the item",
                      r.status_code == 200 and not loose["from_project"] and len(fc.events("upload")) > before
                      and files == local and "character.json" in files and item["thumbnailSha"],
                      {"files": len(files), "uploaded": len(fc.events("upload")) - before}, [])

            # import both into the linked project: the clashing knight becomes knight-2, paths and all
            c.post("/api/library/open", json={"path": str(proj)})
            r = c.post(f"/api/cloud/library/{loose['item']['id']}/import")
            imp = r.json()
            rec_k2 = c.get("/api/characters/knight-2").json()
            views_ok = all((proj / v).is_file() and v.startswith("characters/knight-2/") for v in rec_k2["views"].values())
            rec.check(S, "a character item lands in characters/<slug>/, renamed with its paths when the name is taken",
                      r.status_code == 200 and imp["path"] == "characters/knight-2/" and rec_k2["name"] == "knight-2"
                      and rec_k2["views"] and views_ok, [imp.get("path"), rec_k2.get("name")], [])
            r = c.post(f"/api/cloud/library/{walk['item']['id']}/import")
            anim = r.json()
            got = {p for p in _tree(proj) if p.startswith(anim["path"])}
            rec.check(S, "an exported animation (no spec.json) lands in animations/<id>/final/",
                      r.status_code == 200 and anim["path"] == "animations/knight-walk/final/"
                      and any(p.endswith("animation.json") for p in got) and len(got) == anim["files"], anim.get("path"), [])
            uploads = len(fc.events("upload"))
            res = c.post("/api/project/sync/now").json()
            import hashlib

            renamed = hashlib.sha256((proj / "characters/knight-2/character.json").read_bytes()).hexdigest()
            sent = [e["sha256"] for e in fc.events("upload")[uploads:]]
            rec.check(S, "the next push of imported files uploads only the renamed record: the workspace holds the rest",
                      any(p.startswith("characters/knight-2/") for p in res["pushed"]) and sent == [renamed],
                      len(sent), [])

            # to the team's library: blobs copied server-side from the Personal project
            r = c.post("/api/project/library/publish", json={"prefix": "characters/knight/", "name": "Team knight",
                                                               "kind": "character", "owner": team.id})
            rec.check(S, "a synced folder published to a team's library is copied server-side",
                      r.status_code == 200 and r.json()["from_project"] and r.json()["item"]["ownerId"] == team.id,
                      r.json().get("from_project"), [])
            listing = c.get("/api/cloud/library", params={"owner": "me", "kind": "animation"}).json()["items"]
            search = c.get("/api/cloud/library", params={"owner": "me", "q": "walk"}).json()["items"]
            rec.check(S, "the library lists by workspace, kind and search",
                      [i["name"] for i in listing] == ["Knight walk"] and [i["name"] for i in search] == ["Knight walk"],
                      [len(listing), len(search)], [])

            # machine B, from the CLI
            cli_login(env_b)
            code, lst, _ = cli(env_b, "library", "list", "--owner", team.id)
            code2, got_b, _ = cli(env_b, "init", str(lib_b / "b.sprites"), "--style", "hd-cartoon", "--mode", "synthetic")
            code3, imp_b, _ = cli(env_b, "library", "import", lst["items"][0]["id"], "--project", str(lib_b / "b.sprites"))
            rec.check(S, "the CLI lists a team's library and imports an item into a project",
                      code == 0 and [i["name"] for i in lst["items"]] == ["Team knight"] and code3 == 0
                      and (lib_b / "b.sprites" / "characters" / "team-knight" / "character.json").is_file(),
                      imp_b.get("path") if imp_b else None, [])

            # a trial account (no team seat) has cloud sync but not the library
            user.pro = False
            user.trial_ends = fc.now() + 86400
            role = team.members.pop(user.id)
            c.post("/api/cloud/access/refresh")
            r = c.post("/api/project/library/publish", json={"prefix": "characters/knight/", "name": "Trial knight",
                                                               "kind": "character", "owner": "me"})
            rec.check(S, "without Pro the personal library refuses with plan_required and the server's message",
                      r.status_code == 402 and r.json()["error"]["code"] == "plan_required"
                      and "library is part of Pro" in r.json()["detail"], r.json().get("error", {}).get("code"), [])
            user.pro = True
            team.members[user.id] = role
            c.post("/api/cloud/access/refresh")

        # the studio: Library screen, import, and Add to library
        with eng.client() as c, sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{eng.url()}/?token=t0k#/library")
            grid = page.get_by_test_id("library-grid")
            expect(grid.get_by_test_id("library-item")).to_have_count(2, timeout=15000)
            thumbs = grid.get_by_test_id("library-item-thumb")
            page.wait_for_function("(els) => els.every((i) => i.complete && i.naturalWidth > 0)", arg=thumbs.element_handles())
            n_thumbs = thumbs.count()
            page.get_by_test_id("library-kind").get_by_role("radio", name="Animations").click()
            expect(grid.get_by_test_id("library-item")).to_have_count(1, timeout=10000)
            page.get_by_test_id("library-kind").get_by_role("radio", name="All").click()
            page.get_by_test_id("library-workspace").get_by_role("radio", name="Aurora Studio").click()
            expect(grid.locator("[data-testid=library-item][data-name='Team knight']")).to_be_visible(timeout=10000)
            rec.check(S, "the Library screen shows each workspace's items with thumbnails, filtered by kind",
                      n_thumbs == 2 and grid.get_by_test_id("library-item").count() == 1, n_thumbs, [])
            shot(page, "01-library")
            grid.get_by_test_id("library-item-import").first.click()
            expect(page.get_by_test_id("toast-ok")).to_contain_text("Imported Team knight", timeout=15000)
            page.goto(f"{eng.url()}/?token=t0k#/characters")
            expect(page.locator("[data-testid=character-row][data-name='team-knight']").or_(
                page.get_by_test_id("character-card").filter(has_text="team-knight"))).to_have_count(1, timeout=15000)
            rec.check(S, "Import from the Library screen adds the character to the open project",
                      (proj / "characters" / "team-knight" / "character.json").is_file(), None, [])

            page.locator("[data-testid=character-row]").first.get_by_test_id("character-publish").click()
            dialog = page.get_by_test_id("publish-dialog")
            expect(dialog).to_be_visible()
            dialog.get_by_test_id("publish-name").fill("Studio knight")
            dialog.get_by_test_id("publish-submit").click()
            expect(dialog).to_have_count(0, timeout=15000)
            names = [i["name"] for i in c.get("/api/cloud/library", params={"owner": "me"}).json()["items"]]
            rec.check(S, "Add to library on a character publishes it from the dialog", "Studio knight" in names, names, [])
            rec.check(S, "no page errors", not errors, errors[:3])
            browser.close()
    finally:
        eng.stop()
        fc.stop()
