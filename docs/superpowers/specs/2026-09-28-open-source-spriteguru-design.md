# Open-Source SpriteGuru Desktop Design

**Date:** 2026-09-28  
**Status:** Approved in conversation  
**Repository target:** Public GitHub repository `Docyrus/spriteguru`  
**License:** Apache License 2.0

## 1. Intent

SpriteGuru becomes a fully local-first, open-source desktop sprite studio that has no relationship with SpritePlay Cloud. The application may contact only the AI providers a user configures directly and any local loopback server used by the desktop shell. It must not authenticate with, authorize through, synchronize with, fetch updates from, or expose remote library content from SpritePlay.

The product, installed command, Python distribution, and Python import package are all named SpriteGuru. Existing local SpritePlay projects and preferences migrate automatically without losing assets, exports, project settings, or recent-project history.

## 2. Goals

- Remove all SpritePlay Cloud authentication, account, access-entitlement, remote-library, synchronization, team, release-checking, and cloud-download behavior.
- Delete the cloud implementation rather than disable it or leave compatibility endpoints.
- Preserve the local Projects gallery, local project files, AI-provider key management, generation pipeline, editing, evaluation, exports, and spend controls.
- Rename the Python source package from `spritekit` to `spriteguru`, the distribution to `spriteguru`, and the installed command to `spriteguru`.
- Rename every active user-facing SpritePlay surface to SpriteGuru.
- Migrate SpritePlay-era local data into canonical SpriteGuru locations without overwriting conflicting data.
- Replace the existing mark with the approved original **Split Cells** symbol: a light rounded tile, a black modular S, and a blue lower-right cell.
- Publish the verified project as an Apache-2.0-licensed public repository at `Docyrus/spriteguru`.

## 3. Non-goals

- No replacement hosted service, self-hosted sync protocol, account system, updater, marketplace, or remote asset library.
- No compatibility stubs for retired cloud HTTP routes or cloud CLI commands; they disappear and normal route/command-not-found behavior applies.
- No redesign of the generation, QA, editing, provider, export, or evaluation pipelines beyond package/name changes required by this work.
- No migration or deletion of remote SpritePlay data. This project changes only the desktop repository and local machine state.
- No renaming of existing user project folders ending in `.sprites` or generated asset identifiers.

## 4. Target Architecture

### 4.1 Local-only engine

The engine continues to bind to loopback and serve the React studio behind a per-launch token. Its state contains the open project, provider hub, ledger, job runner, and event bus, but no cloud client, account cache, or sync scheduler.

Generation authorization comes only from local conditions: a valid open project, configured provider keys, the selected synthetic/live mode, job state, and spend approvals. There is no trial, plan, license, team seat, offline grace period, or account lock.

### 4.2 Deleted cloud subsystem

Delete `src/spritekit/cloud/` as part of the package rename. No equivalent `src/spriteguru/cloud/` package is created. Remove:

- OAuth/PKCE sign-in and callback handling;
- keychain refresh-token storage and token refresh;
- account, device, access, team, and billing status;
- cloud project listing and download;
- sync scope, index, scheduler, push/pull, conflicts, linking, and copied-folder handling;
- remote asset-library browse, import, and publish;
- release/update checks;
- cloud-specific error translation, events, and caches.

The provider clients remain. Their direct HTTP/API traffic is not SpritePlay Cloud behavior.

### 4.3 API and CLI

Remove `/auth/callback`, `/api/cloud/*`, and every project-sync route. These paths receive FastAPI's ordinary 404 response after removal.

Remove the `cloud`, `sync`, and remote `library` CLI command groups and every account-access guard used by generation commands. Retain local project creation/opening, provider keys, generation, repair, export, ledger, evaluation, server, and studio commands.

The only installed executable is `spriteguru`. Help, errors, examples, process titles, and launcher commands use that name.

### 4.4 Studio

Remove the cloud context/provider, account menu/chip, sync pill/panel, cloud banners, access-lock UI, cloud-project row, team/storage/plan presentation, cloud settings, update banner, and remote Library screen/navigation entry.

Keep the local Projects gallery and all project-local workflows. The word "library" may remain only where it means the local project collection or a generic internal collection, never the retired remote asset library.

## 5. Package and Identifier Rename

- Move `src/spritekit/` to `src/spriteguru/`.
- Rewrite imports and module invocations from `spritekit` to `spriteguru` throughout source, tests, notebooks, packaging, and documentation.
- Set the Python distribution name to `spriteguru` and entry point to `spriteguru = "spriteguru.cli:main"`.
- Move the bundled studio output under `src/spriteguru/studio_dist/` and update Vite and PyInstaller paths.
- Make `SPRITEGURU_*` the canonical environment-variable namespace, including project, library, development, vector-browser, and test configuration.
- Read legacy `SPRITEKIT_*` variables only as compatibility fallbacks where doing so preserves existing local workflows. New documentation and emitted messages use only `SPRITEGURU_*`.
- References to `spritekit` are allowed only in migration/compatibility code and tests that prove that migration.

## 6. Local Data Migration

Migration is local, idempotent, and never contacts a remote server.

### 6.1 Library root

The canonical default library root is `~/Documents/SpriteGuru`.

- If `~/Documents/SpritePlay` exists and `~/Documents/SpriteGuru` does not, atomically rename the SpritePlay directory to SpriteGuru and rewrite stored recent/hidden paths whose prefix changed.
- If both roots exist, keep SpriteGuru as the canonical root. Merge safe recent-project history and include valid projects from both roots in the local gallery without moving or overwriting either folder.
- New projects default to the SpriteGuru root.
- An explicit `SPRITEGURU_LIBRARY` continues to override the default. `SPRITEKIT_LIBRARY` is a fallback only when the canonical variable is unset.

### 6.2 Library state and browser preferences

The canonical state names are `.spriteguru-library.json`, `.spriteguru-library.lock`, and `.spriteguru-cache`. Migrate the corresponding `.spriteplay-*` names.

When both state files exist, merge recent and hidden project lists without duplicates, prefer current SpritePlay-era values for shared local preference fields, and discard cloud-only keys such as machine identity, access caches, auto-sync, or release-check state.

Copy `spriteplay.*` browser preferences to `spriteguru.*` when the canonical key is absent, then remove the migrated legacy preference key. Existing SpriteGuru values win.

### 6.3 Project-local state

The canonical machine-local directory is `.spriteguru/`; it contains `local.json` for fields such as the active character and game asset folder.

- Migrate `.spriteplay/local.json` to `.spriteguru/local.json` without copying sync indexes, baselines, conflicts, locks, or scheduler state.
- When both local files exist, merge known local fields and prefer the newer SpritePlay-era values.
- Keep `.spriteplay/` and `.spriteguru/` ignored by Git during the transition.
- Remove the obsolete `cloud` field from `project.json` on a normal writable project open/save.
- Never modify characters, animations, cache content, exports, ledger entries, or user-authored project assets as part of migration.

### 6.4 Credential cleanup

On first local-state migration, delete obsolete refresh-token credentials stored under the SpritePlay and legacy SpriteGuru cloud keychain service names. This cleanup performs no sign-out request and contains no authentication, refresh, or network logic. AI-provider credentials remain untouched.

### 6.5 Failure behavior

Use atomic rename/write operations where possible. A failed migration logs a SpriteGuru-named warning and falls back to the readable source path for that run. Failure to rename or clean obsolete metadata must not prevent opening a valid local project. Never overwrite a conflicting destination or delete project assets.

## 7. Brand and Packaging

The approved mark is the original **Split Cells** option shown during design review:

- a light `#F5F6F8` rounded-square tile;
- a black `#15181D` compact modular S;
- a blue `#2C6BD0` lower-right terminal cell;
- the original heavier center geometry, not the later one-level-center revision.

Implement the mark from a single vector/geometry source and generate:

- light/dark header and favicon SVG variants;
- PNG sizes used by the launcher and documentation;
- a macOS `.icns` app icon;
- a Windows `.ico` app icon;
- horizontal wordmark assets reading SpriteGuru.

Rename the desktop artifacts to `SpriteGuru.app` and `SpriteGuru.exe`, set the bundle identifier to `com.spriteguru.studio`, and update window title, Dock/taskbar identity, installer/build checks, and asset filenames.

## 8. Open-Source Repository

Add the standard Apache License 2.0 text in `LICENSE` and a concise `NOTICE` identifying SpriteGuru and copyright 2026 Anil Beyazoglu. Update the README for an independent open-source desktop project: local architecture, installation, provider keys, CLI usage, development, packaging, testing, contribution expectations, license, and privacy/network behavior.

Delete the obsolete cloud integration plan and remove cloud sections from other documentation. Historical old-name strings remain only where required to explain or test migration.

After verification, create the public GitHub repository `Docyrus/spriteguru`, configure it as `origin`, and push the `main` branch. Do not publish generated test artifacts, local visual-companion files, credentials, environment files, caches, or build output.

## 9. Testing Strategy

Development follows red-green-refactor. New behavior is first pinned by tests that fail for the expected pre-change reason.

### 9.1 Migration tests

Cover:

- a SpritePlay-only default library root;
- simultaneous SpriteGuru and SpritePlay roots;
- recent/hidden state merge and path-prefix rewrite;
- project-local preference migration when one or both local directories exist;
- removal of project cloud links without asset changes;
- cloud credential cleanup without provider-key cleanup;
- read-only or failed rename/write behavior;
- browser preference migration.

### 9.2 Removal and rename tests

Verify:

- `import spriteguru` succeeds and application imports resolve from the renamed package;
- the `spriteguru` executable and help text work;
- active source, packaging, and built UI contain no non-migration `spritekit` or SpritePlay product identity;
- cloud and sync routes return 404;
- cloud/sync/library command groups are absent;
- real generation is not account-gated;
- no account, sync, remote Library, plan, trial, or update controls render in the studio;
- the local Projects gallery still creates, opens, lists, hides, and switches projects;
- all brand assets match the Split Cells geometry and remain legible at 16 and 32 pixels;
- package/build metadata names SpriteGuru and uses `com.spriteguru.studio`.

### 9.3 Verification sequence

Run targeted migration, CLI, API, studio, and brand scenarios first. Then run frontend type checking/building and the complete offline E2E suite. Finish with static scans that allow SpritePlay, SpriteGuru Cloud service names, `.spriteplay`, `spriteplay.*`, `spritekit`, and `SPRITEKIT_*` only in explicit migration code/tests or archived compatibility explanations—not in active product identity, network destinations, or cloud logic.

## 10. Acceptance Criteria

The work is complete when:

1. No cloud implementation package, auth callback, cloud endpoint, sync scheduler, remote library, updater, or account/access gate remains.
2. The desktop app starts, creates/opens local projects, generates through user-configured providers, edits, and exports without a SpritePlay account.
3. Existing SpritePlay local projects and preferences appear in SpriteGuru after migration with their assets and settings intact.
4. Python users import `spriteguru`, terminal users run `spriteguru`, and packaging imports only the renamed package.
5. Every normal user-facing surface and distributable says SpriteGuru and uses the approved original Split Cells mark.
6. Apache 2.0 licensing and open-source documentation are present.
7. Targeted tests, frontend build/typecheck, and the complete offline E2E suite pass, with any environmental exception reported explicitly.
8. The verified `main` branch is pushed to the public `Docyrus/spriteguru` GitHub repository.
