"""SpriteGuru is now SpritePlay (docs/failure-modes.md N1–N13). An install the SpriteGuru build left
behind carries over with no action from the user: the same library folder, gallery, machine id and
sign-in, and a linked project whose next sync sends only the `.gitignore` line for the new folder,
which a second upgraded machine then matches without a conflict. A new install gets only the new
names, and nothing the user sees says SpriteGuru.

The SpriteGuru layout is made from a real install: a machine signs in, links and syncs a project
with this build, then every name this build wrote is put back under its SpriteGuru name (the only
difference between the two builds' files)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from cloudhelp import KEY_ENV, Engine, browser_sign_in, cli, cli_login, machine_env
from conftest import ROOT, free_port, sk
from fakecloud import FakeCloud

OLD_LOCKS = {".spriteplay-library.lock": ".spriteguru-library.lock", ".spriteplay-cloud.lock": ".spriteguru-cloud.lock"}


def _keyring(lib: Path) -> dict:
    p = lib / ".test-keyring.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def _write_keyring(lib: Path, data: dict) -> None:
    (lib / ".test-keyring.json").write_text(json.dumps(data))


def _state(lib: Path, name: str = ".spriteplay-library.json") -> dict:
    return json.loads((lib / name).read_text())


def _to_spriteguru(lib: Path, proj: Path) -> None:
    """What the SpriteGuru build left on disk: the same files under the old names."""
    os.rename(lib / ".spriteplay-library.json", lib / ".spriteguru-library.json")
    for new, old in OLD_LOCKS.items():
        if (lib / new).exists():
            os.rename(lib / new, lib / old)
    if (lib / ".spriteplay-cache").exists():
        os.rename(lib / ".spriteplay-cache", lib / ".spriteguru-cache")
    kr = _keyring(lib)
    kr["spriteguru-cloud"] = kr.pop("spriteplay-cloud")
    _write_keyring(lib, kr)
    os.rename(proj / ".spriteplay", proj / ".spriteguru")
    gi = proj / ".gitignore"
    gi.write_text(gi.read_text().replace(".spriteplay/", ".spriteguru/"))


def _names(root: Path, word: str) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if word in p.name)


def _home_env(home: Path) -> dict:
    """A machine whose library is the default one under ~/Documents (no SPRITEGURU_LIBRARY), with no
    keychain and no provider keys."""
    env = {k: v for k, v in os.environ.items() if k not in KEY_ENV and k != "SPRITEGURU_LIBRARY"}
    env.update({"HOME": str(home), "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
                "SPRITEGURU_ENV_FILE": str(home / "no-such.env"), "SPRITEGURU_SYNTH_DELAY": "0"})
    return env


def _character(c: httpx.Client) -> None:
    c.post("/api/characters", json={"name": "knight", "description": "An armoured knight with a blue tabard."})
    for _ in range(300):
        if c.get("/api/characters/knight").json().get("views"):
            break
        time.sleep(0.1)
    c.post("/api/characters/knight/approve")


def test_rename(rec, work, run_dir):
    S = "rename"
    from playwright.sync_api import expect, sync_playwright

    base = work / S
    fc = FakeCloud().start()
    fc.add_user("rename@example.com", "Rename Test", pro=True)
    lib, lib_b = base / "machine", base / "machine-b"
    env, env_b = machine_env(fc.url, lib), machine_env(fc.url, lib_b)
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)
    try:
        # an install as this build leaves it: signed in, a linked project synced, an active character
        eng = Engine(free_port(), env, base / "engine-1.log")
        try:
            with eng.client() as c:
                browser_sign_in(c)
                proj = Path(c.post("/api/library/projects", json={"name": "Carry Over", "style": {"kind": "hd-cartoon"},
                                                                  "engine": "phaser"}).json()["project"]["path"])
                _character(c)
                c.put("/api/project/active-character", json={"name": "knight"})
                pid = c.post("/api/project/sync/link", json={"owner": "me"}).json()["project"]["id"]
                c.post("/api/project/sync/now")
                sync_before = c.get("/api/project/sync").json()
        finally:
            eng.stop()
        # a second machine with the same project
        cli_login(env_b)
        _, got, _ = cli(env_b, "cloud", "download", pid)
        b_proj = Path(got["path"])
        machine = _state(lib)["machine_id"]
        devices = sorted(d.machine_id for d in fc.devices.values())
        commits = len(fc.events("commit"))

        _to_spriteguru(lib, proj)
        _to_spriteguru(lib_b, b_proj)
        rec.check(S, "the SpriteGuru layout has no SpritePlay name left: state file, locks, keychain entry, project folder",
                  not _names(lib, "spriteplay") and "spriteplay-cloud" not in _keyring(lib)
                  and (proj / ".spriteguru" / "sync" / "state.json").is_file(), _names(lib, "spriteplay"), [])

        # the new build, started with the SpriteGuru-era cloud setting only
        legacy_env = {k: v for k, v in env.items() if k != "SPRITEPLAY_CLOUD_URL"} | {"SPRITEGURU_CLOUD_URL": fc.url}
        eng = Engine(free_port(), legacy_env, base / "engine-2.log")
        try:
            with eng.client() as c:
                listing = c.get("/api/library").json()
                card = next((p for p in listing["projects"] if Path(p["path"]) == proj), None)
                state = _state(lib)
                rec.check(S, "the library state carries over under its new name: the same gallery and machine id; the "
                             "old state file and locks are gone",
                          card is not None and state["machine_id"] == machine
                          and not (lib / ".spriteguru-library.json").exists()
                          and not any((lib / old).exists() for old in OLD_LOCKS.values()),
                          {"machine": state.get("machine_id") == machine, "card": card is not None}, ["N2", "N3"])
                rec.check(S, "the gallery reads the project's old folder where it is, without renaming it",
                          card is not None and (card.get("cloud") or {}).get("project_id") == pid
                          and (proj / ".spriteguru").is_dir() and not (proj / ".spriteplay").exists(),
                          {"linked": bool((card or {}).get("cloud")), "old_folder": (proj / ".spriteguru").is_dir()}, ["N5"])

                st = c.get("/api/cloud/status", params={"refresh": "true"}).json()
                kr = _keyring(lib)
                rec.check(S, "the sign-in carries over: the old keychain entry moves to the new service and is deleted; "
                             "the older SPRITEGURU_CLOUD_URL still points the app",
                          st["signed_in"] and st["user"]["email"] == "rename@example.com"
                          and kr.get("spriteplay-cloud", {}).get("refresh_token") and not kr.get("spriteguru-cloud"),
                          {"signed_in": st["signed_in"], "services": sorted(k for k, v in kr.items() if v)}, ["N4", "N8"])
                rec.check(S, "the website sees the same machines: no new device, the same machine id",
                          sorted(d.machine_id for d in fc.devices.values()) == devices and machine in devices
                          and len(devices) == 2, len(fc.devices), ["N3", "N9"])

                c.post("/api/library/open", json={"path": str(proj)})
                project = c.get("/api/project").json()
                ignore = (proj / ".gitignore").read_text().split()
                rec.check(S, "opening the project renames its folder: the active character is kept and the new folder "
                             "is ignored by git",
                          (proj / ".spriteplay" / "sync" / "state.json").is_file() and not (proj / ".spriteguru").exists()
                          and project["active_character"] == "knight" and ".spriteplay/" in ignore,
                          {"active": project.get("active_character"), "ignored": ".spriteplay/" in ignore}, ["N5", "N7"])
                res = c.post("/api/project/sync/now").json()
                sync_after = c.get("/api/project/sync").json()
                # the project's own sync starts when it opens, so either round may send the edit
                sent = [p for e in fc.events("commit")[commits:] for p in e["paths"]]
                rec.check(S, "syncing after the upgrade sends only .gitignore's new line: no pull, conflict or "
                             "'copied?' question",
                          sent == [".gitignore"] and res["pushed"] in ([], [".gitignore"]) and res["pulled"] == []
                          and res["conflicts"] == 0 and not sync_after["conflicts"] and not sync_after["copied"]
                          and sync_after["rev"] == sync_before["rev"] + 1,
                          {"sent": sent, "pulled": res.get("pulled"), "conflicts": res.get("conflicts")}, ["N5", "N7"])
                # the second machine upgrades too: its .gitignore edit matches the one already sent
                legacy_b = {k: v for k, v in env_b.items() if k != "SPRITEPLAY_CLOUD_URL"} | {"SPRITEGURU_CLOUD_URL": fc.url}
                code, res_b, _ = cli(legacy_b, "sync", "--project", str(b_proj))
                rec.check(S, "a second machine upgrading the same project matches that edit: no conflict, nothing sent",
                          code == 0 and res_b["conflicts"] == 0 and res_b["pushed"] == []
                          and (b_proj / ".gitignore").read_text() == (proj / ".gitignore").read_text()
                          and (b_proj / ".spriteplay").is_dir() and not (b_proj / ".spriteguru").exists()
                          and len(fc.events("commit")) == commits + 1,
                          {k: (res_b or {}).get(k) for k in ("pushed", "pulled", "conflicts")}, ["N5", "N7"])

                # a stray old folder next to the new one: never uploaded, and the new one wins
                stray = proj / ".spriteguru"
                stray.mkdir()
                (stray / "local.json").write_text(json.dumps({"active_character": "ghost"}))
                (stray / "sync.txt").write_text("machine-local")
                (proj / "notes.txt").write_text("written after the rename")
                res = c.post("/api/project/sync/now").json()
                sent = [p for e in fc.events("commit")[commits:] for p in e["paths"]]
                c.post("/api/library/open", json={"path": str(proj)})
                rec.check(S, "a leftover .spriteguru/ is never uploaded, and the new folder's settings win",
                          "notes.txt" in res["pushed"] and not any(p.startswith((".spriteguru/", ".spriteplay/")) for p in sent)
                          and c.get("/api/project").json()["active_character"] == "knight", sent, ["N6", "N5"])

                # a folder that can't be renamed (a read-only project folder) stays in use under its old name
                locked = Path(c.post("/api/library/projects", json={"name": "Locked", "style": {"kind": "hd-cartoon"},
                                                                    "engine": "phaser"}).json()["project"]["path"])
                c.post("/api/library/close")
                os.rename(locked / ".spriteplay", locked / ".spriteguru")
                (locked / ".spriteguru" / "local.json").write_text(json.dumps({"active_character": None,
                                                                               "asset_folder": "game/sprites"}))
                locked.chmod(0o555)
                try:
                    r = c.post("/api/library/open", json={"path": str(locked)})
                    opened = r.status_code == 200 and c.get("/api/project").json().get("name") == "Locked"
                    alive = c.get("/api/library").status_code == 200
                finally:
                    locked.chmod(0o755)
                logged = "couldn't rename" in (base / "engine-2.log").read_text()
                rec.check(S, "when the old folder can't be renamed, the project still opens from it and the engine "
                             "logs why",
                          opened and alive and logged and (locked / ".spriteguru" / "local.json").is_file()
                          and not (locked / ".spriteplay").exists(),
                          {"status": r.status_code, "logged": logged}, ["N13"])

                agents = sorted(a for a in fc.user_agents if a.startswith("Sprite"))
                rec.check(S, "the app introduces itself to the website as SpritePlay",
                          bool(agents) and all(a.startswith("SpritePlay/") for a in agents), agents, ["N11"])

                # the studio: the saved theme carries over, and nothing on screen says SpriteGuru
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    page = browser.new_page(viewport={"width": 1400, "height": 900})
                    page.goto(f"{eng.url()}/?token=t0k#/projects")
                    page.get_by_test_id("topbar").wait_for()
                    page.evaluate("() => { localStorage.setItem('spriteguru.theme', 'dark'); "
                                  "localStorage.removeItem('spriteplay.theme'); }")
                    page.reload()
                    expect(page.locator("html")).to_have_attribute("data-theme", "dark", timeout=15000)
                    moved = page.evaluate("() => [localStorage.getItem('spriteplay.theme'), "
                                          "localStorage.getItem('spriteguru.theme')]")
                    rec.check(S, "a theme saved under the old storage key carries over once", moved == ["dark", None],
                              moved, ["N10"])
                    texts = [page.title(), page.locator(".brand-name").text_content() or "",
                             page.get_by_test_id("topbar-brand-mark").get_attribute("alt") or "", page.inner_text("body")]
                    page.screenshot(path=str(shots / "01-gallery.png"))
                    page.get_by_test_id("account-chip").click()
                    page.get_by_test_id("account-menu").wait_for()
                    texts.append(page.inner_text("body"))
                    page.screenshot(path=str(shots / "02-account-menu.png"))
                    page.keyboard.press("Escape")
                    page.goto(f"{eng.url()}/?token=t0k#/settings")
                    page.wait_for_timeout(500)
                    texts.append(page.inner_text("body"))
                    rec.check(S, "the tab, header, mark, account menu and settings say SpritePlay, never SpriteGuru",
                              texts[:3] == ["SpritePlay Studio", "SpritePlay", "SpritePlay"]
                              and not any("SpriteGuru" in t for t in texts),
                              [texts[:3], [t.count("SpriteGuru") for t in texts]], ["N11"])
                    browser.close()
                for name in ("01-gallery.png", "02-account-menu.png"):
                    rec.files.append({"scenario": S, "path": (shots / name).relative_to(run_dir).as_posix(),
                                      "sha256": None, "in_digest": False})

                bundle = [p.relative_to(ROOT).as_posix() for p in (ROOT / "src" / "spriteguru" / "studio_dist").rglob("*")
                          if p.is_file() and (b"SpriteGuru" in p.read_bytes() or b"spriteguru.com" in p.read_bytes())]
                rec.check(S, "no file of the built studio contains the old name or the old website address", not bundle,
                          bundle, ["N11", "N14"])
                page_html = httpx.get(eng.url("/auth/callback"), params={"state": "no-such-state", "code": "x"}).text
                rec.check(S, "an expired sign-in link's page is titled SpritePlay",
                          "<title>SpritePlay</title>" in page_html and "SpriteGuru" not in page_html, page_html[:120], ["N11"])

                # a sign-in the new server doesn't know (renewed on the old site after the copy)
                for d in fc.devices.values():
                    if d.machine_id == machine:
                        d.refresh = "0" * 64
                fc.expire_access_tokens()
                refused = not c.get("/api/cloud/status", params={"refresh": "true"}).json()["signed_in"]
                browser_sign_in(c)
                again = c.get("/api/cloud/status", params={"refresh": "true"}).json()["signed_in"]
                mine = [d for d in fc.devices.values() if d.machine_id == machine]
                same = len(mine) == 1 and len(fc.devices) == 2 and not mine[0].revoked and mine[0].refresh != "0" * 64
                rec.check(S, "a sign-in the new server refuses signs the machine out; signing in again is the same "
                             "machine to the server, not a new device",
                          refused and again and same, {"refused": refused, "again": again, "same_machine": same}, ["N15"])

                # signing out clears the sign-in under both keychain names
                kr = _keyring(lib)
                kr["spriteguru-cloud"] = {"refresh_token": "left-by-an-old-build"}
                _write_keyring(lib, kr)
                c.post("/api/cloud/sign-out")
                kr = _keyring(lib)
                rec.check(S, "signing out deletes the sign-in under the new and the old keychain names",
                          not kr.get("spriteplay-cloud", {}).get("refresh_token")
                          and not kr.get("spriteguru-cloud", {}).get("refresh_token"),
                          sorted(k for k, v in kr.items() if v), ["N4"])
        finally:
            eng.stop()

        # the new cloud setting wins when both are set; the CLI speaks SpritePlay
        both = legacy_env | {"SPRITEPLAY_CLOUD_URL": fc.url, "SPRITEGURU_CLOUD_URL": "http://127.0.0.1:9"}
        code, out, _ = cli(both, "cloud", "status")
        rec.check(S, "with both cloud settings, SPRITEPLAY_CLOUD_URL wins",
                  code == 0 and out is not None and out.get("cloud_url") == fc.url, (out or {}).get("cloud_url") == fc.url,
                  ["N8"])
        helps = [cli(env, "--help")[2], cli(env, "cloud", "--help")[2]]
        rec.check(S, "the CLI's help says SpritePlay, never SpriteGuru",
                  all("SpritePlay" in h and "SpriteGuru" not in h for h in helps), [h.count("SpritePlay") for h in helps],
                  ["N11"])

        # with no cloud setting the app uses spriteplay.com; every connection goes to a closed proxy, so
        # the check never reaches the real website
        quiet = {k: v for k, v in machine_env("http://127.0.0.1:9", base / "machine-default").items()
                 if k not in ("SPRITEPLAY_CLOUD_URL", "SPRITEGURU_CLOUD_URL", "NO_PROXY", "no_proxy")}
        quiet |= {"HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9"}
        code, out, _ = cli(quiet, "cloud", "status")
        rec.check(S, "with no cloud setting, the app's server is https://spriteplay.com",
                  code == 0 and (out or {}).get("cloud_url") == "https://spriteplay.com", (out or {}).get("cloud_url"), ["N14"])

        # an update check cached from one server isn't offered after moving to another
        fc2 = FakeCloud().start()
        try:
            for plat in ("macos-arm64", "macos-x64", "windows-x64", "linux-x64"):
                fc2.add_release("9.9.0", fc2.now() - 60, platform=plat)
            lib_u = base / "machine-updates"
            code1, first, _ = cli(machine_env(fc.url, lib_u), "cloud", "status")
            code2, moved, _ = cli(machine_env(fc2.url, lib_u), "cloud", "status")
            up = (moved or {}).get("update") or {}
            rec.check(S, "after moving to another server, the update check asks the new server instead of reusing the old answer",
                      code1 == 0 and not ((first or {}).get("update") or {}).get("available") and code2 == 0
                      and up.get("version") == "9.9.0" and str(up.get("url", "")).startswith(fc2.url),
                      up.get("version"), ["N16"])
        finally:
            fc2.stop()
    finally:
        fc.stop()

    # the default library folder, from HOME (no SPRITEGURU_LIBRARY)
    scratch = {"SPRITEGURU_LIBRARY": str(base / "scratch-library")}
    home = base / "home-spriteguru"
    old = home / "Documents" / "SpriteGuru"
    sk("init", str(old / "Old.sprites"), "--style", "hd-cartoon", "--mode", "synthetic", env=scratch)
    (old / ".spriteguru-library.json").write_text(json.dumps({
        "recent": [{"path": str((old / "Old.sprites").resolve()), "last_opened": "2026-09-01T10:00:00"}],
        "hidden": [], "machine_id": "legacy-home-machine"}))
    eng = Engine(free_port(), _home_env(home), base / "engine-home.log")
    try:
        with eng.client() as c:
            listing = c.get("/api/library").json()
        rec.check(S, "an existing ~/Documents/SpriteGuru stays the library: its projects are listed, nothing moves, "
                     "no SpritePlay folder is made",
                  Path(listing["root"]) == old.resolve()
                  and [Path(p["path"]).name for p in listing["projects"]] == ["Old.sprites"]
                  and not (home / "Documents" / "SpritePlay").exists()
                  and _state(old).get("machine_id") == "legacy-home-machine"
                  and not (old / ".spriteguru-library.json").exists(),
                  {"root": Path(listing["root"]).name, "projects": len(listing["projects"])}, ["N1", "N2"])
    finally:
        eng.stop()

    fresh = base / "home-fresh"
    (fresh / "Documents").mkdir(parents=True)
    eng = Engine(free_port(), _home_env(fresh), base / "engine-fresh.log")
    try:
        with eng.client() as c:
            root = Path(c.get("/api/library").json()["root"])
            made = Path(c.post("/api/library/projects", json={"name": "Fresh Start", "style": {"kind": "hd-cartoon"},
                                                              "engine": "phaser"}).json()["project"]["path"])
        rec.check(S, "a new install uses ~/Documents/SpritePlay and writes no SpriteGuru name anywhere",
                  root == (fresh / "Documents" / "SpritePlay").resolve() and made.parent == root
                  and (made / ".spriteplay" / "local.json").is_file() and (root / ".spriteplay-library.json").is_file()
                  and not _names(fresh, "spriteguru") and not _names(fresh, "SpriteGuru"),
                  {"root": root.relative_to(fresh.resolve()).as_posix(), "old_names": _names(fresh, "spriteguru")},
                  ["N1", "N7"])
    finally:
        eng.stop()

    (old.parent / "SpritePlay").mkdir()
    eng = Engine(free_port(), _home_env(home), base / "engine-both.log")
    try:
        with eng.client() as c:
            root = Path(c.get("/api/library").json()["root"])
        rec.check(S, "once ~/Documents/SpritePlay exists, it is the library", root == (old.parent / "SpritePlay").resolve(),
                  root.name, ["N1"])
    finally:
        eng.stop()
