# Contributing to SpriteGuru

Thanks for helping. SpriteGuru is a local-first sprite studio; changes should keep it working
offline, without an account, and talking only to the providers a user configures.

## Setup

Follow [README.md](README.md) from "Install from a clean checkout" through "First launch without
API keys". You need no API keys to develop: synthetic mode runs every route offline.

## How changes are made

- **Tests first.** Write or extend an E2E scenario in `e2e/` that fails for the reason you expect,
  make it pass, then clean up. Scenarios drive the real CLI, API or studio; there are no unit tests.
- **Failure modes before isolated code.** A subsystem tested on its own (golden fixtures, replayed
  jobs) first gets its list of ways to fail in [docs/failure-modes.md](docs/failure-modes.md), and
  its checks cite those IDs.
- **Repeatable artifacts.** Each run writes `e2e/artifacts/<run>/` with `summary.json`,
  `index.html` and a `digest.txt` that repeats run to run offline. A change that moves the digest
  should explain why.
- **Offline before live.** Everything must pass offline (`--mode synthetic`, the default in tests).
  Live scenarios (`--live`) spend real money: run them only when a change touches a provider route,
  and never make them required.
- **Match the surrounding code.** There is no Python formatter configured; follow the style, naming
  and comment density of the file you're in.

Before opening a pull request:

```bash
uv run pytest e2e                            # the full offline suite (~7 min); --quick while iterating
cd studio && npm run typecheck && npm run build
```

Include the run's digest, or the names of any failing scenarios, in the pull request.

## Keys, credentials and other people's work

- Never commit `.env`, API keys, keychain exports, ledgers from paid runs, `e2e/artifacts/`,
  `e2e/work/`, `dist/` or `build/`. The E2E harness uses a file-backed keyring and a temporary
  library, so tests never read your real keychain or projects.
- `uv run spriteguru keys status` shows where keys come from without their values; it is safe to
  paste.
- Only add images you made or have the right to redistribute under the Apache License 2.0.

## Reporting bugs

Open a GitHub issue with the command or studio steps, what you expected and what happened, your OS,
and the `summary.json` of a failing E2E run if you have one. Prompts, character descriptions,
reference images and generated sprites can be private work: include them only if you intend to share
them publicly. Security issues: see "Privacy and network" in the README.

## License

By contributing, you agree that your contributions are licensed under the Apache License 2.0, as
the rest of the project is (see [LICENSE](LICENSE)).
