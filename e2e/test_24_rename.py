"""Local-only migration from SpritePlay-era paths and preferences to SpriteGuru."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import ROOT, free_port


def _project(root: Path, name: str) -> Path:
    project = root / f"{name.lower().replace(' ', '-')}.sprites"
    for rel in ("characters", "animations", "cache"):
        (project / rel).mkdir(parents=True, exist_ok=True)
    (project / "project.json").write_text(json.dumps({
        "name": name,
        "style": {"kind": "hd-cartoon"},
        "engine": "godot",
        "fps": 12,
        "settings": {"provider_mode": "synthetic"},
    }))
    return project


def _default_home(monkeypatch, home: Path) -> None:
    (home / "Documents").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SPRITEGURU_LIBRARY", raising=False)
    monkeypatch.delenv("SPRITEKIT_LIBRARY", raising=False)


def test_spriteplay_library_and_project_migrate_without_asset_loss(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _default_home(monkeypatch, home)
    old_root = home / "Documents" / "SpritePlay"
    project = _project(old_root, "Carry Over")
    asset = project / "characters" / "knight" / "ref" / "side-e.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"authored-asset")
    (project / ".spriteplay" / "sync").mkdir(parents=True)
    (project / ".spriteplay" / "local.json").write_text(
        json.dumps({"active_character": "knight", "asset_folder": "game/sprites"})
    )
    (project / ".spriteplay" / "sync" / "state.json").write_text('{"rev":9}')
    raw = json.loads((project / "project.json").read_text())
    raw["cloud"] = {"project_id": "remote", "owner_id": "owner"}
    (project / "project.json").write_text(json.dumps(raw))
    (old_root / ".spriteplay-library.json").write_text(json.dumps({
        "recent": [{"path": str(project), "last_opened": "2026-09-28T12:00:00"}],
        "hidden": [],
        "auto_sync": True,
        "machine_id": "cloud-machine",
    }))

    from spriteguru import library
    from spriteguru.project import Project

    library._reset_for_tests()
    migrated_root = library.root()
    migrated = Project.open(migrated_root / "carry-over.sprites")
    saved = json.loads((migrated.root / "project.json").read_text())

    assert migrated_root == (home / "Documents" / "SpriteGuru").resolve()
    assert (migrated.root / "characters" / "knight" / "ref" / "side-e.png").read_bytes() == b"authored-asset"
    assert migrated.config.active_character == "knight"
    assert "cloud" not in saved
    assert (migrated.root / ".spriteguru" / "local.json").is_file()
    assert not (migrated.root / ".spriteguru" / "sync").exists()


def test_migrated_local_preferences_survive_the_next_open(tmp_path):
    project = _project(tmp_path, "Reopen")
    (project / ".spriteplay").mkdir()
    (project / ".spriteplay" / "local.json").write_text(json.dumps({"active_character": "knight"}))

    from spriteguru.project import Project

    first = Project.open(project)
    first.config.active_character = "wizard"
    first.save()
    again = Project.open(project)

    assert first.config.active_character == "wizard"
    assert again.config.active_character == "wizard"
    assert not (project / ".spriteplay" / "local.json").exists()


def test_both_default_roots_remain_visible_without_overwrite(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _default_home(monkeypatch, home)
    new = _project(home / "Documents" / "SpriteGuru", "New Project")
    old = _project(home / "Documents" / "SpritePlay", "Old Project")

    from spriteguru import library

    library._reset_for_tests()
    listing = library.listing()
    paths = {Path(card["path"]) for card in listing["projects"]}

    assert Path(listing["root"]) == new.parent.resolve()
    assert paths == {new.resolve(), old.resolve()}
    assert new.is_dir() and old.is_dir()


def test_spriteplay_state_merges_recents_and_drops_cloud_keys(tmp_path, monkeypatch):
    root = tmp_path / "library"
    monkeypatch.setenv("SPRITEGURU_LIBRARY", str(root))
    canonical_project = _project(root, "Canonical")
    legacy_project = _project(tmp_path / "elsewhere", "Legacy")
    (root / ".spriteguru-library.json").write_text(json.dumps({
        "recent": [{"path": str(canonical_project), "last_opened": "2026-09-27T10:00:00"}],
        "hidden": [],
        "theme": "light",
    }))
    (root / ".spriteplay-library.json").write_text(json.dumps({
        "recent": [{"path": str(legacy_project), "last_opened": "2026-09-28T10:00:00"}],
        "hidden": [],
        "theme": "dark",
        "machine_id": "remote-machine",
        "auto_sync": True,
        "access_cache": {"allowed": True},
    }))

    from spriteguru import library

    library._reset_for_tests()
    library.root()
    state = json.loads((root / ".spriteguru-library.json").read_text())

    assert [entry["path"] for entry in state["recent"]] == [str(legacy_project), str(canonical_project)]
    assert state["theme"] == "dark"
    assert "machine_id" not in state
    assert "auto_sync" not in state
    assert "access_cache" not in state


def test_failed_default_root_rename_uses_readable_source_and_logs(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    _default_home(monkeypatch, home)
    old_root = home / "Documents" / "SpritePlay"
    _project(old_root, "Still Here")

    from spriteguru import library

    real_rename = library.os.rename

    def fail_root(old, new):
        if Path(old) == old_root:
            raise OSError("read only")
        return real_rename(old, new)

    monkeypatch.setattr(library.os, "rename", fail_root)
    library._reset_for_tests()

    assert library.root() == old_root.resolve()
    assert {card["name"] for card in library.listing()["projects"]} == {"Still Here"}
    assert "SpriteGuru" in capsys.readouterr().err


def test_provider_key_migrates_and_cloud_tokens_are_deleted(tmp_path, monkeypatch):
    root = tmp_path / "library"
    monkeypatch.setenv("SPRITEGURU_LIBRARY", str(root))
    for name in ("OPENAI_API_KEY", "AI_PROVIDER_KEY_OPENAI"):
        monkeypatch.delenv(name, raising=False)
    root.mkdir()

    import keyring
    from spriteguru import keys, library

    keyring.set_password("spritekit", "openai", "provider-secret")
    for service in ("spriteplay-cloud", "spriteguru-cloud"):
        keyring.set_password(service, "refresh_token", "obsolete")
        keyring.set_password(service, "account", "{}")

    library._reset_for_tests()
    library.root()

    assert keys.get("openai") == "provider-secret"
    assert keyring.get_password("spriteguru", "openai") == "provider-secret"
    assert keyring.get_password("spritekit", "openai") is None
    for service in ("spriteplay-cloud", "spriteguru-cloud"):
        assert keyring.get_password(service, "refresh_token") is None
        assert keyring.get_password(service, "account") is None


def test_browser_preferences_migrate_to_spriteguru_and_canonical_wins(tmp_path):
    from playwright.sync_api import sync_playwright

    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory",
         str(ROOT / "src" / "spriteguru" / "studio_dist")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            for _ in range(50):
                try:
                    page.goto(f"http://127.0.0.1:{port}/#/projects")
                    break
                except Exception:
                    time.sleep(0.05)
            page.evaluate("""() => {
                localStorage.setItem('spriteplay.theme', 'dark');
                localStorage.removeItem('spriteguru.theme');
                localStorage.setItem('spriteplay.currentAnim', 'legacy-animation');
                localStorage.setItem('spriteguru.currentAnim', 'canonical-animation');
            }""")
            page.reload()
            values = page.evaluate("""() => ({
                theme: localStorage.getItem('spriteguru.theme'),
                legacyTheme: localStorage.getItem('spriteplay.theme'),
                animation: localStorage.getItem('spriteguru.currentAnim'),
                legacyAnimation: localStorage.getItem('spriteplay.currentAnim'),
            })""")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)

    assert values == {
        "theme": "dark",
        "legacyTheme": None,
        "animation": "canonical-animation",
        "legacyAnimation": None,
    }
