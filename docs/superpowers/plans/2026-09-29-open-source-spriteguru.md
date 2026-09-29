# Open-Source SpriteGuru Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the desktop app into a fully local, Apache-2.0-licensed SpriteGuru project, migrate existing SpritePlay local data, remove every SpritePlay Cloud surface, and publish the verified result to `Docyrus/spriteguru`.

**Architecture:** Rename the Python package and executable first so every subsequent change targets the final namespace. Add a local-only, idempotent compatibility layer for old paths, preferences, environment variables, and provider keys; then remove the backend and frontend cloud subsystems rather than disabling them. Rebuild the brand and packaging from the approved Split Cells geometry, document a clean-checkout workflow, run the full offline verification suite, and push `main` to the already-created public repository.

**Tech Stack:** Python 3.12, uv/uv-build, FastAPI, Typer, Pydantic, keyring, React 19, TypeScript 7, Vite 8, Playwright, Pillow, PyInstaller, pytest/xdist.

**Spec:** `docs/superpowers/specs/2026-09-28-open-source-spriteguru-design.md`

## Global Constraints

- The product, distribution, import package, executable, desktop bundle, and normal UI identity are `SpriteGuru` / `spriteguru`.
- The desktop application must never contact SpritePlay Cloud; direct calls to user-configured AI providers remain supported.
- Existing local projects, assets, exports, local preferences, and provider credentials must survive migration.
- Legacy `SPRITEKIT_*`, `.spriteplay`, and `spriteplay.*` names are read-only migration inputs; new output uses SpriteGuru names.
- Cloud routes and CLI commands are deleted, not replaced with compatibility stubs.
- The approved logo is the original Split Cells geometry with the heavier center, light `#F5F6F8` tile, black `#15181D` S, and blue `#2C6BD0` terminal cell.
- The license is Apache License 2.0 and the repository is public at `https://github.com/Docyrus/spriteguru`.
- Do not commit `.env`, API keys, keychain data, `MyGame.sprites`, `.superpowers`, test artifacts, caches, browser downloads, or distributable build output.

## Review Focus

- **Both default library roots exist:** SpriteGuru remains canonical while projects in SpritePlay remain visible and neither tree is overwritten; covered in Task 2 migration tests.
- **A project contains a cloud link and sync metadata:** opening it removes only the link/active sync state while every authored asset remains byte-identical; covered in Task 2 migration tests.
- **Provider keys were stored under the old `spritekit` keychain service:** the first read migrates them to `spriteguru`, while obsolete cloud refresh tokens are deleted; covered in Task 2 credential tests.
- **Retired routes or UI leak back during refactoring:** cloud URLs return 404 and the built studio contains no account, sync, plan, update, or remote-library surface; covered in Tasks 3 and 4.
- **A new contributor has no keys or sibling repositories:** README-only setup builds and starts a synthetic project from a clean clone; covered in Task 6 clean-checkout validation.

---

### Task 1: Rename the Python Package, CLI, and Runtime Namespace

**Files:**
- Create: `e2e/test_25_open_source.py`
- Create: `src/spriteguru/env.py`
- Move: `src/spritekit/` → `src/spriteguru/`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `packaging/engine_entry.py`
- Modify: `packaging/launcher_entry.py`
- Modify: `packaging/engine.spec`
- Modify: `packaging/launcher.spec`
- Modify: `packaging/build_app.sh`
- Modify: `studio/vite.config.ts`
- Modify: `studio/package.json`
- Modify: `studio/package-lock.json`
- Modify: `e2e/conftest.py`
- Modify: `e2e/support/filekeyring.py`
- Modify: every active Python/test/notebook import that names `spritekit`

**Interfaces:**
- Produces: import package `spriteguru`; executable `spriteguru`; module entry `python -m spriteguru.cli`; engine binary `spriteguru-engine`; `spriteguru.env.get(name, default=None)` and `spriteguru.env.present(name)`.
- Consumes: legacy `SPRITEKIT_<NAME>` values only when `SPRITEGURU_<NAME>` is absent.

- [ ] **Step 1: Write the failing identity and environment tests**

Add these tests to `e2e/test_25_open_source.py`:

```python
from __future__ import annotations

import os
import subprocess
import sys

from conftest import ROOT


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, env={**os.environ, **(env or {})}, capture_output=True, text=True)


def test_spriteguru_is_the_only_python_and_cli_identity():
    imported = _run(sys.executable, "-c", "import spriteguru; print(spriteguru.__version__)")
    help_out = _run(sys.executable, "-m", "spriteguru.cli", "--help")
    legacy = _run(sys.executable, "-c", "import spritekit")

    assert imported.returncode == 0, imported.stderr
    assert help_out.returncode == 0, help_out.stderr
    assert "SpriteGuru" in help_out.stdout
    assert "SpritePlay" not in help_out.stdout
    assert legacy.returncode != 0


def test_spriteguru_environment_name_wins_and_spritekit_is_a_fallback():
    code = "from spriteguru.env import get; print(get('PROJECT', 'missing'))"
    canonical = _run(sys.executable, "-c", code, env={"SPRITEGURU_PROJECT": "new", "SPRITEKIT_PROJECT": "old"})
    legacy = _run(sys.executable, "-c", code, env={"SPRITEGURU_PROJECT": "", "SPRITEKIT_PROJECT": "old"})

    assert canonical.stdout.strip() == "new"
    assert legacy.stdout.strip() == "old"
```

- [ ] **Step 2: Run the tests and verify the package test fails for the right reason**

Run:

```bash
uv run pytest e2e/test_25_open_source.py::test_spriteguru_is_the_only_python_and_cli_identity -n 0 -q
```

Expected: FAIL because `spriteguru` does not exist and `spritekit` still imports.

- [ ] **Step 3: Move the package and perform the mechanical namespace rewrite**

Move `src/spritekit` to `src/spriteguru`. Update Python imports, resource package names, executable names, test subprocess modules, notebook imports, PyInstaller collection names, Vite output path, and package metadata. The essential `pyproject.toml` result is:

```toml
[project]
name = "spriteguru"
description = "SpriteGuru: local-first sprite studio"

[project.scripts]
spriteguru = "spriteguru.cli:main"
```

Set `studio/package.json` and both lockfile package records to `spriteguru-studio`. Rename `spritekit-engine` to `spriteguru-engine` throughout packaging and launcher discovery.

- [ ] **Step 4: Add the canonical environment helper and use it for every app-specific environment read**

Create `src/spriteguru/env.py`:

```python
from __future__ import annotations

import os


def get(name: str, default: str | None = None) -> str | None:
    canonical = os.environ.get(f"SPRITEGURU_{name}")
    if canonical:
        return canonical
    legacy = os.environ.get(f"SPRITEKIT_{name}")
    return legacy if legacy else default


def present(name: str) -> bool:
    return bool(get(name))
```

Replace direct application reads of `SPRITEKIT_PROJECT`, `LIBRARY`, `ENV_FILE`, `DEV`, `VECTOR_BROWSER`, `MODELS`, `OFFLINE`, `MATTE_MODEL`, `LOG`, `BACKOFF_BASE`, `CRASH_AFTER`, `SYNTH_DEFECTS`, and `SYNTH_DELAY` with this helper. Tests emit canonical `SPRITEGURU_*` names except the explicit fallback test.

- [ ] **Step 5: Update the E2E harness and packaging entries**

Change the E2E runner to invoke `[sys.executable, "-m", "spriteguru.cli", ...]`, name its worker field `spriteguru_run_id`, use `SPRITEGURU_LIBRARY`/`SPRITEGURU_LOG`, and update the file-backed keyring root lookup. Update `packaging/engine_entry.py` and `launcher_entry.py` to import `spriteguru`, and update all hidden imports and collected packages.

- [ ] **Step 6: Refresh locks and verify the rename**

Run:

```bash
uv lock
uv run pytest e2e/test_25_open_source.py -n 0 -q
uv run spriteguru --help
uv run python -c "import spriteguru; print(spriteguru.__version__)"
```

Expected: both tests PASS; help identifies SpriteGuru; the import prints `0.1.0`.

- [ ] **Step 7: Commit the namespace rename**

```bash
git add pyproject.toml uv.lock src/spriteguru packaging studio/package.json studio/package-lock.json studio/vite.config.ts e2e notebooks
git commit -m "refactor: rename the application to SpriteGuru"
```

---

### Task 2: Migrate SpritePlay Local Data and Preserve Provider Credentials

**Files:**
- Rewrite: `e2e/test_24_rename.py`
- Modify: `src/spriteguru/project.py`
- Modify: `src/spriteguru/library.py`
- Modify: `src/spriteguru/spec.py`
- Modify: `src/spriteguru/keys.py`
- Modify: `studio/src/main.tsx`
- Modify: `.gitignore`

**Interfaces:**
- Produces: canonical `.spriteguru/local.json`, `.spriteguru-library.json`, `.spriteguru-library.lock`, `.spriteguru-cache`, `~/Documents/SpriteGuru`, `spriteguru.*` browser keys, and keyring service `spriteguru`.
- Consumes: `.spriteplay/local.json`, `.spriteplay-library.json`, `.spriteplay-cache`, `~/Documents/SpritePlay`, `spriteplay.*` browser keys, provider service `spritekit`, and cloud services `spriteplay-cloud`/`spriteguru-cloud`.
- Preserves: `Project.open(root, migrate=True)`, `library.root()`, `library.listing()`, and `keys.get/set/delete/status()` public behavior.

- [ ] **Step 1: Replace the old forward-rename scenario with failing reverse-migration tests**

Rewrite `e2e/test_24_rename.py` around local fixtures only. Include these assertions as separate tests:

```python
def test_spriteplay_library_and_project_migrate_without_asset_loss(tmp_path, monkeypatch):
    home = tmp_path / "home"
    old_root = home / "Documents" / "SpritePlay"
    project = old_root / "carry-over.sprites"
    asset = project / "characters" / "knight" / "ref" / "side-e.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"authored-asset")
    (project / "animations").mkdir()
    (project / "cache").mkdir()
    (project / ".spriteplay").mkdir()
    (project / ".spriteplay" / "local.json").write_text(
        '{"active_character":"knight","asset_folder":"game/sprites"}'
    )
    (project / ".spriteplay" / "sync").mkdir()
    (project / ".spriteplay" / "sync" / "state.json").write_text('{"rev":9}')
    (project / "project.json").write_text(
        '{"name":"Carry Over","style":{"kind":"hd-cartoon"},"engine":"godot",'
        '"fps":12,"settings":{"provider_mode":"synthetic"},'
        '"cloud":{"project_id":"remote","owner_id":"owner"}}'
    )
    (old_root / ".spriteplay-library.json").write_text(
        '{"recent":[{"path":"' + str(project) + '","last_opened":"2026-09-28T12:00:00"}],'
        '"hidden":[],"auto_sync":true,"machine_id":"cloud-machine"}'
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SPRITEGURU_LIBRARY", raising=False)
    monkeypatch.delenv("SPRITEKIT_LIBRARY", raising=False)
    from spriteguru import library
    from spriteguru.project import Project

    library._reset_for_tests()
    migrated_root = library.root()
    migrated = Project.open(migrated_root / "carry-over.sprites")
    raw = json.loads((migrated.root / "project.json").read_text())

    assert migrated_root == home / "Documents" / "SpriteGuru"
    assert asset.name == "side-e.png"
    assert (migrated.root / "characters" / "knight" / "ref" / "side-e.png").read_bytes() == b"authored-asset"
    assert migrated.config.active_character == "knight"
    assert "cloud" not in raw
    assert (migrated.root / ".spriteguru" / "local.json").is_file()
    assert not (migrated.root / ".spriteguru" / "sync").exists()
```

Also add:

- `test_both_default_roots_remain_visible_without_overwrite` with one uniquely named project in each root;
- `test_newer_spriteplay_state_merges_recents_and_drops_cloud_keys`;
- `test_failed_rename_uses_readable_source_and_logs_a_spriteguru_warning`;
- `test_provider_key_moves_from_spritekit_service_and_cloud_tokens_are_deleted` using `e2e/support/filekeyring.py`;
- a Playwright check that `spriteplay.theme` migrates only when `spriteguru.theme` is absent.

- [ ] **Step 2: Run the migration scenario and verify the direction fails**

Run:

```bash
uv run pytest e2e/test_24_rename.py -n 0 -q
```

Expected: FAIL because the app still treats SpritePlay names as canonical, retains the cloud link/sync state, and uses the old provider keyring service.

- [ ] **Step 3: Reverse the project-local migration and remove cloud fields on save**

In `project.py`, set:

```python
LOCAL_DIR = ".spriteguru"
LEGACY_LOCAL_DIR = ".spriteplay"
LOCAL_FIELDS = ("active_character", "asset_folder")
```

Make `local_dir()` merge only known fields from the legacy `local.json`, preferring legacy values when it is newer/current, write the canonical file atomically, and never carry `sync/`. Keep both directory names in `.gitignore`. Remove the `cloud` argument and disk-preservation branch from `Project.save()`. Make `Project.open(..., migrate=True)` save when `cloud` is present in raw JSON so Pydantic's ignored extra is removed from disk.

Delete `CloudLink` and the `ProjectConfig.cloud` field from `spec.py` in this step. That makes the legacy JSON field an ignored migration input rather than active project state, so the subsequent save drops it.

- [ ] **Step 4: Reverse and harden the library-root migration**

Use canonical constants:

```python
STATE = ".spriteguru-library.json"
LOCK = ".spriteguru-library.lock"
FOLDER = "SpriteGuru"
LEGACY_FOLDER = "SpritePlay"
LEGACY_NAMES = {
    ".spriteplay-library.json": STATE,
    ".spriteplay-cache": ".spriteguru-cache",
}
```

Add `all_roots() -> tuple[Path, ...]`: the canonical root first and an existing SpritePlay root second only when both exist. `_candidates()` scans both roots plus merged recents. If only SpritePlay exists, rename it atomically to SpriteGuru and rewrite path prefixes in `recent` and `hidden`. If the rename fails, use SpritePlay for that run and log the failure. Merge state lists by normalized path, remove cloud-only keys (`machine_id`, `access`, `access_cache`, `auto_sync`, `release_check`, `release_server`), and expose `_reset_for_tests()` to clear the process migration cache safely between isolated test homes.

- [ ] **Step 5: Migrate provider keys and delete cloud credentials locally**

In `keys.py`, use:

```python
SERVICE = "spriteguru"
LEGACY_SERVICE = "spritekit"
CLOUD_SERVICES = ("spriteplay-cloud", "spriteguru-cloud")
```

When `get(provider)` finds a legacy provider value, write it to `spriteguru`, delete only that legacy provider entry, and return it. `delete(provider)` removes both provider-service entries. Add an idempotent `cleanup_cloud_credentials()` that attempts to delete the known refresh-token usernames from both cloud services and ignores missing/backend errors; call it from the once-per-root library migration. Never delete provider keys.

- [ ] **Step 6: Reverse browser preference migration**

In `studio/src/main.tsx`, enumerate the existing preference suffixes. Copy `spriteplay.<suffix>` to `spriteguru.<suffix>` only when the canonical value is absent, then remove the legacy key. Update every active local-storage read/write to `spriteguru.*`.

- [ ] **Step 7: Verify migration tests and local gallery regression**

Run:

```bash
uv run pytest e2e/test_24_rename.py e2e/test_13_projects.py -n 0 -q
```

Expected: PASS with no network server and no missing project assets.

- [ ] **Step 8: Commit local migration**

```bash
git add src/spriteguru/project.py src/spriteguru/library.py src/spriteguru/spec.py src/spriteguru/keys.py studio/src/main.tsx e2e/test_24_rename.py .gitignore
git commit -m "feat: migrate local SpritePlay data to SpriteGuru"
```

---

### Task 3: Delete the Backend Cloud, Sync, Remote Library, and Access Gate

**Files:**
- Create: `e2e/apphelp.py`
- Create: `e2e/test_17_local_only.py`
- Modify: `src/spriteguru/api.py`
- Modify: `src/spriteguru/cli.py`
- Modify: `src/spriteguru/library.py`
- Modify: `src/spriteguru/spec.py`
- Modify: `src/spriteguru/service.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Delete: `src/spriteguru/cloud/`
- Delete: `e2e/cloudhelp.py`
- Delete: `e2e/fakecloud.py`
- Delete: `e2e/test_17_cloud_account.py`
- Delete: `e2e/test_18_cloud_sync.py`
- Delete: `e2e/test_19_cloud_studio.py`
- Delete: `e2e/test_20_cloud_library.py`
- Delete: `e2e/test_22_cloud_contract.py`
- Delete: `e2e/support/contract_server.sh`

**Interfaces:**
- Preserves: local `/api/library`, `/api/project`, generation, jobs, keys, ledger, export, events, and static-studio routes.
- Removes: `/auth/callback`, `/api/cloud/*`, `/api/project/sync*`, `cloud`, `sync`, and remote `library` CLI commands, cloud events, and `_require_access`.
- Produces: `e2e.apphelp.Engine`, a cloud-agnostic loopback test-engine helper.

- [ ] **Step 1: Write failing backend-removal tests**

Create `e2e/test_17_local_only.py`:

```python
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import ROOT
from spriteguru.api import create_app


def test_retired_cloud_routes_are_normal_404s():
    client = TestClient(create_app(None, "test-token", mode="synthetic"))
    for method, path in (
        ("get", "/api/cloud/status"),
        ("post", "/api/cloud/sign-in"),
        ("get", "/api/cloud/projects"),
        ("get", "/api/project/sync"),
        ("post", "/api/project/sync/now"),
        ("get", "/auth/callback?state=x&code=y"),
    ):
        response = getattr(client, method)(path, headers={"x-spriteguru-token": "test-token"})
        assert response.status_code == 404, (method, path, response.status_code)


def test_cli_has_no_cloud_sync_remote_library_or_access_gate():
    result = subprocess.run(
        [sys.executable, "-m", "spriteguru.cli", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    from spriteguru import cli

    assert result.returncode == 0
    assert "cloud" not in result.stdout.lower()
    assert "sync" not in result.stdout.lower()
    assert "library" not in result.stdout.lower()
    assert not hasattr(cli, "_require_access")


def test_active_python_source_has_no_spriteplay_cloud_package_or_destination():
    package = ROOT / "src" / "spriteguru"
    assert not (package / "cloud").exists()
    hits = []
    for path in package.rglob("*.py"):
        text = path.read_text()
        if "spriteplay.com" in text or "from .cloud" in text or "from spriteguru.cloud" in text:
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []
```

- [ ] **Step 2: Run the removal tests and verify they fail on active cloud surfaces**

Run:

```bash
uv run pytest e2e/test_17_local_only.py -n 0 -q
```

Expected: FAIL because routes, commands, `_require_access`, the cloud package, and the SpritePlay destination still exist.

- [ ] **Step 3: Remove CLI cloud behavior and access gates**

Delete `cloud_app`, the remote `library_app`, their registrations and commands, `sync`, `_require_access`, and every `_require_access(...)` call. Keep provider approval/spend prompts. Replace all CLI guidance with `spriteguru ...` and keep synthetic/live provider selection unchanged.

- [ ] **Step 4: Remove cloud state, lifecycle, exception handling, and routes from FastAPI**

Delete cloud imports and the `State.cloud`/`State.sync` fields. Remove sync scheduler start/stop/switch logic, access exceptions, cloud event handling, cloud open-prefix exceptions, all cloud/sync/library-asset routes, the auth callback, blob cache, cloud download state, and remote publish/import endpoints. Change the FastAPI title and token header to `spriteguru` / `x-spriteguru-token`. Keep `/api/library` because it is the local Projects gallery.

- [ ] **Step 5: Remove cloud schema/card remnants and dependencies**

Confirm `CloudLink` and `ProjectConfig.cloud` were removed by Task 2, then delete `_cloud_card()`, `cloud_links()`, cloud fields in local project cards, and any service-layer account checks. Delete `src/spriteguru/cloud/`. Remove `pathspec` if `rg 'pathspec' src/spriteguru` finds no remaining use. Keep or move `httpx` to the dev group if it is needed only by E2E clients. Refresh `uv.lock`.

- [ ] **Step 6: Replace cloud test helpers and delete cloud scenarios**

Create `e2e/apphelp.py` by retaining only the generic `Engine` process/client lifecycle from `cloudhelp.py`, invoking `spriteguru.cli`, using `SPRITEGURU_LIBRARY`, and never setting a cloud URL. Update remaining tests that need an engine helper. Delete fake cloud, cloud-account/sync/studio/library/contract scenarios, and the contract server script.

- [ ] **Step 7: Verify backend removal and core API/CLI behavior**

Run:

```bash
uv lock
uv run pytest e2e/test_17_local_only.py e2e/test_07_api.py e2e/test_06_jobs.py -n 0 -q
```

Expected: PASS; the API and job tests use only local/synthetic behavior.

- [ ] **Step 8: Commit backend removal**

```bash
git add pyproject.toml uv.lock src/spriteguru e2e
git commit -m "feat: remove SpritePlay cloud services"
```

---

### Task 4: Remove Cloud UI and Restore an Unconditionally Local Studio

**Files:**
- Create: `e2e/test_19_local_studio.py`
- Modify: `studio/src/App.tsx`
- Modify: `studio/src/lib/api.ts`
- Modify: `studio/src/lib/types.ts`
- Modify: `studio/src/lib/router.ts`
- Modify: `studio/src/main.tsx`
- Modify: `studio/src/screens/Builder.tsx`
- Modify: `studio/src/screens/Characters.tsx`
- Modify: `studio/src/screens/Export.tsx`
- Modify: `studio/src/screens/Projects.tsx`
- Modify: `studio/src/screens/Settings.tsx`
- Modify: `studio/src/shell/NavRail.tsx`
- Modify: `studio/src/shell/TopBar.tsx`
- Modify: `studio/src/styles/app.css`
- Modify: `studio/src/styles/screens.css`
- Modify: `studio/src/ui/Icon.tsx`
- Delete: `studio/src/lib/cloud.tsx`
- Delete: `studio/src/screens/Library.tsx`
- Delete: `studio/src/shell/Account.tsx`
- Delete: `studio/src/shell/Sync.tsx`

**Interfaces:**
- Preserves: project gallery, project switcher, character switcher, provider-key settings, spend approvals, builder, review/edit/findings/export screens.
- Removes: cloud context, account menu, sync UI, remote library route/tab, access lock, publish/import actions, update banners, and cloud project cards.

- [ ] **Step 1: Write a failing local-studio Playwright scenario**

Create `e2e/test_19_local_studio.py` using `e2e.apphelp.Engine`. Start a synthetic engine, create/open one local project through `/api/library/projects`, and assert:

```python
expect(page).to_have_title("SpriteGuru Studio")
expect(page.get_by_test_id("topbar-brand-mark")).to_have_attribute("alt", "SpriteGuru")
assert page.get_by_test_id("account-chip").count() == 0
assert page.get_by_test_id("sync-pill").count() == 0
assert page.get_by_test_id("cloud-row").count() == 0
assert page.get_by_role("link", name="Library").count() == 0

page.goto(f"{engine.url()}/?token=t0k#/settings")
expect(page.get_by_role("heading", name="Provider keys")).to_be_visible()
assert page.get_by_text("Cloud sync").count() == 0
assert page.get_by_text("SpritePlay account").count() == 0
```

Also navigate to Characters and Builder in synthetic mode and assert their create/generate actions are enabled without account state.

- [ ] **Step 2: Run the studio scenario and verify cloud controls are found**

Run:

```bash
uv run pytest e2e/test_19_local_studio.py -n 0 -q
```

Expected: FAIL because the account chip, sync pill, cloud row, remote Library tab, settings sections, and generation lock still render.

- [ ] **Step 3: Remove cloud providers, routes, API methods, and types**

Delete `CloudProvider` wrapping in `App.tsx` and delete `lib/cloud.tsx`. Remove cloud/sync API methods, `SyncChoice`, `cloudBlobUrl`, cloud project/status/access/update/sync types, and `ProjectCard.cloud`. Remove the remote `library` screen from router/app navigation while retaining the local `Library` TypeScript interface used by `/api/library`.

- [ ] **Step 4: Simplify the visible screens**

Remove `AccountChip` and `SyncPill` from `TopBar`; remove the remote Library item from `NavRail`; remove `CloudProjects`, sync badges/menus, update banner, and cloud downloads from `Projects`; remove account and cloud-sync sections from `Settings`; remove remote publish/import actions from Characters/Export. Delete `Account.tsx`, `Sync.tsx`, and `screens/Library.tsx`.

Replace `useGenerationLock()` checks with only existing local constraints (project/character/job readiness). Do not add a new entitlement abstraction.

- [ ] **Step 5: Remove orphaned styles/icons and build the studio**

Use `rg` to identify selectors referenced only by deleted components, then remove those selectors from `app.css` and `screens.css`. Remove remote-library-only icon names when no active component imports them. Run:

```bash
cd studio
npm run typecheck
npm run build
```

Expected: both commands exit 0 and Vite writes `src/spriteguru/studio_dist`.

- [ ] **Step 6: Verify the UI scenario and existing studio workflow**

Run:

```bash
uv run pytest e2e/test_19_local_studio.py e2e/test_09_studio.py e2e/test_13_projects.py -n 0 -q
```

Expected: PASS; no cloud UI appears and the local project/studio workflow remains functional.

- [ ] **Step 7: Commit the local-only UI**

```bash
git add studio e2e/test_19_local_studio.py
git commit -m "feat: make the studio entirely local"
```

---

### Task 5: Build the Split Cells Brand, Desktop Packaging, and Apache License

**Files:**
- Modify: `e2e/test_16_brand.py`
- Modify: `brand/build_brand.py`
- Modify: `brand/build_wordmark.py`
- Modify: `brand/render_logos.py`
- Modify: `brand/README.md`
- Replace: `brand/svg/*.svg`
- Replace: `brand/png/*.png`
- Create: `brand/SpriteGuru.icns`
- Create: `brand/SpriteGuru.ico`
- Delete: `brand/SpritePlay.icns`
- Delete: `brand/SpritePlay.ico`
- Modify: `packaging/engine.spec`
- Modify: `packaging/launcher.spec`
- Modify: `packaging/build_app.sh`
- Modify: `src/spriteguru/launcher.py`
- Modify: `studio/index.html`
- Modify: `studio/src/shell/TopBar.tsx`
- Modify: `studio/src/ui/Icon.tsx`
- Create: `LICENSE`
- Create: `NOTICE`

**Interfaces:**
- Produces: one Split Cells geometry source, SVG/PNG/ICNS/ICO variants, `SpriteGuru.app`/`SpriteGuru.exe`, bundle ID `com.spriteguru.studio`, Apache-2.0 license files.

- [ ] **Step 1: Change brand tests first**

Update `e2e/test_16_brand.py` so the SVG master assertions require:

```python
SPLIT_CELLS = "M24 22h49v16H41v11h31v25H23V58h33V47H24z"

assert 'viewBox="0 0 96 96"' in mark_svg
assert '<rect x="3" y="3" width="90" height="90" rx="22" fill="#F5F6F8"' in mark_svg
assert f'd="{SPLIT_CELLS}" fill="#15181D"' in mark_svg
assert '<rect x="57" y="58" width="15" height="16" fill="#2C6BD0"' in mark_svg
assert "SpriteGuru" in mark_svg
assert "FFD20A" not in mark_svg
```

Update launcher/package expectations to `spriteguru.launcher`, `SpriteGuru.icns`, `SpriteGuru.ico`, `SpriteGuru.app`, `SpriteGuru.exe`, and `com.spriteguru.studio`. Keep the existing pixel-diff, 16/32 px, favicon, and launcher fallback checks.

- [ ] **Step 2: Run the brand scenario and verify old geometry/name failures**

Run:

```bash
uv run pytest e2e/test_16_brand.py -n 0 -q
```

Expected: FAIL because the mark is still the old black/yellow 5×5 S and packaging still names SpritePlay.

- [ ] **Step 3: Implement Split Cells from one 96-unit source**

In `build_brand.py`, define:

```python
GRID = 96
TILE = "#F5F6F8"
INK = "#15181D"
BLUE = "#2C6BD0"
S_PATH = "M24 22h49v16H41v11h31v25H23V58h33V47H24z"
S_POLYGON = [(24, 22), (73, 22), (73, 38), (41, 38), (41, 49), (72, 49),
             (72, 74), (23, 74), (23, 58), (56, 58), (56, 47), (24, 47)]
ACCENT_BOX = (57, 58, 72, 74)
```

Generate both mark SVG variants from that exact geometry and render PNG/ICNS/ICO with supersampled Pillow polygons. Keep the approved heavier center. Make the on-dark asset preserve the light tile so the selected mark looks identical on both shells.

- [ ] **Step 4: Regenerate SpriteGuru wordmarks reproducibly**

Set `TEXT = "SpriteGuru"` and update all SVG accessibility labels. Make `build_wordmark.py` resolve Unbounded from `studio/node_modules/@fontsource-variable/unbounded`; add that package as a development dependency and refresh `studio/package-lock.json`. Regenerate horizontal and wordmark SVGs, then run `brand/build_brand.py`.

- [ ] **Step 5: Rename desktop packaging and launcher identity**

Set engine output to `spriteguru-engine`, launcher/app names to `SpriteGuru`, bundle ID to `com.spriteguru.studio`, icon paths to `brand/SpriteGuru.icns`/`.ico`, pywebview title to `SpriteGuru`, and `argparse` program name to `SpriteGuru`. Update `build_app.sh` checks to compare the new bundle name, ID, and icon.

- [ ] **Step 6: Add Apache 2.0 license files**

Create `LICENSE` with the unmodified Apache License, Version 2.0 text from January 2004. Create `NOTICE` exactly as:

```text
SpriteGuru
Copyright 2026 Anil Beyazoglu

This product includes software developed for the SpriteGuru project.
```

- [ ] **Step 7: Generate and inspect the mark, then run brand tests**

Run:

```bash
cd studio && npm install --save-dev @fontsource-variable/unbounded && cd ..
uv run python brand/build_wordmark.py
uv run python brand/build_brand.py
uv run pytest e2e/test_16_brand.py -n 0 -q
```

Open `brand/png/app-icon-macos-512.png`, `brand/png/mark-32.png`, and `brand/png/mark-16.png` for visual inspection. Expected: the original Split Cells shape remains identifiable at both small sizes; test passes.

- [ ] **Step 8: Build and verify the native bundle**

Run:

```bash
packaging/build_app.sh
```

Expected on macOS: `dist/SpriteGuru.app`, `CFBundleName=SpriteGuru`, `CFBundleIdentifier=com.spriteguru.studio`, and the bundled icon byte-matches `brand/SpriteGuru.icns`.

- [ ] **Step 9: Commit branding, packaging, and licensing**

```bash
git add brand packaging src/spriteguru/launcher.py studio LICENSE NOTICE e2e/test_16_brand.py
git commit -m "feat: add the SpriteGuru Split Cells brand"
```

---

### Task 6: Write the Clean-Checkout README and Remove Cloud Documentation

**Files:**
- Create: `e2e/test_26_open_source_docs.py`
- Rewrite: `README.md`
- Modify: `.env.example`
- Modify: `.gitignore`
- Create: `CONTRIBUTING.md`
- Modify: `docs/implementation-plan.md`
- Modify: `docs/failure-modes.md`
- Modify: `notebooks/README.md`
- Delete: `docs/cloud-integration-plan.md`

**Interfaces:**
- Produces: a README-only path from clone to a running synthetic SpriteGuru studio, complete provider setup, development/testing/build commands, privacy statement, contribution guidance, and Apache license link.

- [ ] **Step 1: Add failing documentation-contract tests**

Create `e2e/test_26_open_source_docs.py`:

```python
from pathlib import Path

from conftest import ROOT


def test_readme_is_a_complete_clean_checkout_runbook():
    readme = (ROOT / "README.md").read_text()
    required = (
        "git clone https://github.com/Docyrus/spriteguru.git",
        "cd spriteguru",
        "Python 3.12",
        "Node.js",
        "uv sync",
        "npm ci",
        "npm run build",
        "uv run spriteguru init",
        "--mode synthetic",
        "uv run spriteguru studio",
        "SPRITEGURU_DEV",
        "packaging/build_app.sh",
        "Apache License 2.0",
    )
    assert [item for item in required if item not in readme] == []


def test_active_docs_have_no_cloud_setup_or_old_commands():
    assert not (ROOT / "docs" / "cloud-integration-plan.md").exists()
    active = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / ".env.example"]
    forbidden = ("spriteplay.com", "uv run spritekit", "cloud login", "sync --link")
    hits = [(str(path.relative_to(ROOT)), token) for path in active for token in forbidden if token in path.read_text()]
    assert hits == []


def test_env_example_matches_documented_provider_names():
    text = (ROOT / ".env.example").read_text()
    assert text.splitlines() == [
        "OPENAI_API_KEY=",
        "FAL_KEY=",
        "RD_API_KEY=",
        "QUIVERAI_API_KEY=",
    ]
```

- [ ] **Step 2: Run the docs contract and verify current README failures**

Run:

```bash
uv run pytest e2e/test_26_open_source_docs.py -n 0 -q
```

Expected: FAIL because clone/prerequisites/synthetic smoke setup are incomplete, old commands/cloud docs remain, and `.env.example` differs.

- [ ] **Step 3: Rewrite README as the authoritative first-run guide**

Use this order:

1. What SpriteGuru is and its local-only network/privacy promise.
2. Supported macOS/Windows/Linux prerequisites with Python 3.12, uv, current Node LTS/npm, pywebview native packages, and Playwright installation (`playwright install --with-deps chromium` on Linux).
3. Exact clone, `uv sync`, `npm ci`, `npm run build` commands.
4. Synthetic smoke path: create `Demo.sprites --mode synthetic`, launch `uv run spriteguru studio --project Demo.sprites`, and state that the Projects/Characters UI should open without keys.
5. Provider key setup and exact `.env` names.
6. Normal CLI/studio usage.
7. Frontend development with `SPRITEGURU_DEV=1`, loopback ports, and Vite.
8. Offline tests, optional paid live tests, native packaging, and optional Git LFS.
9. Troubleshooting for browser binaries, webview libraries, keychain backends, port 8777, and missing keys.
10. Contributing, license, project layout, and privacy/security.

Do not mention account plans, remote sync, cloud library, website login, or updates.

- [ ] **Step 4: Align environment example and contribution docs**

Set `.env.example` to the four canonical provider variables in the test. Add `CONTRIBUTING.md` with local setup linking back to README, red-green-refactor expectations, offline-before-live testing, formatting/typecheck commands, no-credential rules, and a statement that bug reports must not include prompts/assets unless the reporter intends to share them.

- [ ] **Step 5: Clean architecture and failure-mode documentation**

Delete `docs/cloud-integration-plan.md`. Rename package/product references in `docs/implementation-plan.md` and `notebooks/README.md`. Remove cloud failure modes C1–C12, C14–C40 and the SpritePlay-rename narrative from `docs/failure-modes.md`; preserve unrelated local/provider/pipeline failure modes and add a concise migration section matching Task 2.

- [ ] **Step 6: Protect local/generated data from publication**

Add these entries to `.gitignore`:

```gitignore
.superpowers/
*.sprites/
src/spriteguru/studio_dist/
```

Confirm `MyGame.sprites`, visual-companion files, E2E artifacts, `.env`, `dist`, and `build` stay untracked.

- [ ] **Step 7: Run documentation tests and a clean-checkout smoke validation**

Run:

```bash
uv run pytest e2e/test_26_open_source_docs.py -n 0 -q
tmpdir=$(mktemp -d)
rsync -a --exclude .git --exclude .venv --exclude node_modules --exclude .superpowers \
  --exclude '*.sprites' --exclude e2e/artifacts --exclude e2e/work ./ "$tmpdir/spriteguru/"
cd "$tmpdir/spriteguru"
uv sync --locked
cd studio && npm ci && npm run build && cd ..
uv run spriteguru init Demo.sprites --style hd-cartoon --engine godot --mode synthetic
uv run spriteguru --help
```

Expected: all commands exit 0 without an API key. Remove the explicit temporary clone path after validation.

- [ ] **Step 8: Commit documentation**

```bash
git add README.md CONTRIBUTING.md .env.example .gitignore docs notebooks e2e/test_26_open_source_docs.py
git commit -m "docs: publish the local-first SpriteGuru guide"
```

---

### Task 7: Static Cleanup, Full Verification, and Final GitHub Push

**Files:**
- Modify: any active file identified by verification scans
- Modify: `docs/superpowers/plans/2026-09-29-open-source-spriteguru.md` (checkbox progress only)

**Interfaces:**
- Produces: a clean `main` branch on `origin`, containing the verified open-source app and no local artifacts.

- [ ] **Step 1: Scan active source for forbidden cloud and old-identity residue**

Run:

```bash
rg -n -i 'spriteplay\.com|/api/cloud|/auth/callback|project/sync|cloud login|sync --link' \
  src/spriteguru studio packaging README.md CONTRIBUTING.md pyproject.toml
rg -n 'from spritekit|import spritekit|python -m spritekit|uv run spritekit' \
  src/spriteguru studio packaging README.md CONTRIBUTING.md e2e pyproject.toml
find src/spriteguru -maxdepth 2 -type d -name cloud -print
```

Expected: no output. Migration-only old-name strings remain confined to `project.py`, `library.py`, `keys.py`, `env.py`, `main.tsx`, and their migration tests.

- [ ] **Step 2: Verify no cloud dependency or UI residue remains**

Run:

```bash
rg -n -i 'CloudProvider|useCloud|CloudStatus|SyncStatus|AccountChip|SyncPill|spriteplay-cloud' src/spriteguru studio/src
uv run spriteguru --help
```

Expected: the scan prints only the keychain cleanup constant for `spriteplay-cloud`; CLI help contains no cloud, sync, account, or remote-library command.

- [ ] **Step 3: Run frontend and targeted suites**

Run:

```bash
cd studio && npm ci && npm run typecheck && npm run build && cd ..
uv run pytest e2e/test_24_rename.py e2e/test_17_local_only.py e2e/test_19_local_studio.py \
  e2e/test_16_brand.py e2e/test_25_open_source.py e2e/test_26_open_source_docs.py -n 0 -q
```

Expected: all commands and tests PASS with no warnings introduced by this work.

- [ ] **Step 4: Run the complete offline E2E suite**

Run:

```bash
uv run pytest e2e
```

Expected: PASS, with live/provider-spend tests skipped unless `--live` is explicitly supplied. Record any pre-existing/environmental failure by exact test name before making a completion claim.

- [ ] **Step 5: Rebuild the native app and verify repository contents**

Run:

```bash
packaging/build_app.sh
git status --short
git ls-files | rg '(^|/)(\.env|MyGame\.sprites|\.superpowers|e2e/artifacts|dist|build)(/|$)' || true
```

Expected: native build succeeds; the tracked-file scan is empty; `git status` shows only the plan checkbox updates or intentional final fixes.

- [ ] **Step 6: Apply the verification-before-completion checklist and commit final fixes**

Read and execute `superpowers:verification-before-completion`. Re-run any command affected by a fix. Then:

```bash
git add -u
git add README.md CONTRIBUTING.md LICENSE NOTICE .env.example .gitignore brand docs e2e notebooks packaging pyproject.toml src studio tests uv.lock
git commit -m "chore: finalize open-source SpriteGuru"
```

If there is nothing new to commit, keep the already-verified task commits.

- [ ] **Step 7: Push the implementation to the public repository**

Run:

```bash
git status --short --branch
git log --oneline --decorate -10
git remote -v
git push origin main
```

Expected: `main` is clean and synchronized with `https://github.com/Docyrus/spriteguru.git`.

- [ ] **Step 8: Verify the published repository**

Run:

```bash
gh repo view Docyrus/spriteguru --json nameWithOwner,visibility,url,defaultBranchRef
git ls-remote --heads origin main
```

Expected: `nameWithOwner` is `Docyrus/spriteguru`, visibility is `PUBLIC`, the default branch is `main`, and the remote SHA equals local `HEAD`.
