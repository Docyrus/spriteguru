# Outputs, layout and troubleshooting

Contents: [Project layout](#project-layout) · [Scores and findings](#scores-and-findings) ·
[Environment variables](#environment-variables) · [Errors and what to do](#errors-and-what-to-do)

## Project layout

```text
Game.sprites/
  project.json                     style, engine, fps, caps, provider_mode (shared config)
  .spriteguru/local.json           machine-local (active character, asset folder); gitignored
  ledger.jsonl                     every provider call, written before it is sent
  cache/                           provider outputs keyed by request; enables --replay and resume
  characters/<name>/
    character.json                 record: description, palette, approved, views, warnings
    ref/turnaround.png, front|side-e|side-w|back.png, turnaround-cN.png (candidates), measure.json
  animations/<character>-<action>-<facing>/
    spec.json, meta.json           what was asked for
    jobs/<time>/                   guide canvas, mask, prompt, candidates + provenance, per-candidate reports
    final/                         the deliverable (below)
```

`final/` always has `sheet.png`, `frames/000.png...`, `preview.gif`, `animation.json`, `report.html`,
`report.json`, `sheet.aseprite.json`, `export.zip`, plus engine files:

| Engine | Extra files |
| --- | --- |
| godot | `<anim-id>.tres` (SpriteFrames; texture path is relative to the asset folder when one is set) |
| phaser | `sheet.json` (TexturePacker atlas) and `anims.json` |
| pixi | `sheet.json` |
| unity | `sheet.spriteguru.json` and `SpriteKitImporter.cs` (editor script that slices and builds the clip) |
| gamemaker | `<name>_strip<N>.png` (equal-width strip, no padding) |

Pixel style also writes `sheet@4x.png` and its JSON. `animation.json` carries `fps`, per-frame `durations`,
`pivot`, `loop`, `size`, `rects`, `root_motion` and `blend` (`add` for glow effects, which the engine must
draw additively).

## Scores and findings

`Q = 100 - weighted findings`, clamped to 0-100. **Accepted = Q >= 85 and no `fail`-level finding.**
A `fail` blocks acceptance whatever Q is. Each finding has `metric`, `level` (`fail`|`warn`|`info`),
`severity` 1-3, `frames` (**0-based**; empty means the whole sheet), `message` and `remedy`.

| Remedy | Meaning / command |
| --- | --- |
| `auto_fixed` | the engine already fixed it; informational |
| `frame_repair`, `pose_fix`, `identity_fix`, `regenerate_frame` | `repair <id> --frame N --kind frame\|pose\|identity` (N = finding frame + 1) |
| `inbetween` | `inbetween <id> --after N` |
| `reroll`, `reroll_next_key`, `reroll_1080p`, `fix_round` | new candidate: `gen ... --seed <new>` (bills again) |
| `pixel_refit`, `ml_matte` | already handled by the pipeline's rounds; if it persists, report it |
| `confirm_layout` | needs a human to confirm the cell layout in the studio's Sheet review |

The engine already ran its own repair loop before returning (`attempts` and `decisions` in the job JSON
say what it tried), so a remaining `fail` usually needs your judgement or the user's, not a blind retry.

## Environment variables

`SPRITEGURU_PROJECT` (default project), `SPRITEGURU_LIBRARY` (studio project library),
`SPRITEGURU_ENV_FILE` (.env path), `SPRITEGURU_OFFLINE=1` (never download models; a built-in identity
descriptor stands in), `SPRITEGURU_MODELS` (model cache, default `~/.cache/spriteguru/models`),
`SPRITEGURU_MATTE_MODEL` (default `birefnet-general`), `SPRITEGURU_VECTOR_BROWSER=auto|managed|chrome`,
`SPRITEGURU_LOG` (log level). The old `SPRITEKIT_*` names still work as fallbacks.

On a fresh machine the first analysis downloads open models (the DINOv2-small identity model, and the
rembg matte model when needed) into `SPRITEGURU_MODELS`, so the first command can be slow and needs
network access. `SPRITEGURU_OFFLINE=1` skips the download and uses a built-in descriptor instead.

## Errors and what to do

| What you see | Meaning | Do this |
| --- | --- | --- |
| exit 2 `no project found; pass --project or run spriteguru init` | not inside a project | pass `-p <Name.sprites>` or `cd` into it |
| exit 2 `character 'x' is not approved` | gen needs an approved subject | look at the turnaround, then `character approve x` |
| exit 1, traceback ending `ValueError: unknown character action 'x'; known: ...` | typo, or wrong kind for the subject | pick from the `known:` list (each kind has its own actions) |
| exit 1, `state: failed`, `budget: call to <model> (~$X) needs approval: <reason>` | approval wasn't given (non-interactive, no `--yes`) | show the user the model, estimate and reason; re-run the same `gen` with `--yes` only if they agree (paid calls already made replay from the cache; `jobs resume` won't pick up a `failed` job) |
| `ProviderError: no OpenAI key: run spriteguru keys set openai` (or fal / Quiver / Retro Diffusion) | live mode, key missing | tell the user; or rehearse with `--mode synthetic` |
| `ReplayMiss: replay mode: no cached output ...` | `--replay` with nothing cached | drop `--replay` (this will call providers, so ask first) |
| `state: done`, `accepted: false` | best-effort export | read findings, then repair, inbetween, or a new seed |
| Pixel loop falls back to a guided sheet | no Retro Diffusion key (`RD_API_KEY`) | expected; mention it to the user |
| Vector job waits on a download or `Executable doesn't exist` | no headless browser | `uv run playwright install chromium` (Linux: `--with-deps`); it also downloads once by itself |
| Linux/Windows: studio window won't open | no GTK/Qt / WebView2 | `studio --browser`; not needed for CLI work |
| `objc[...] Class AVF... is implemented in both` on stderr | duplicate video libraries on macOS | harmless, ignore |
| `unknown_outcomes > 0` in `ledger` | a call was sent and its result never recorded (crash mid-call) | tell the user; it may have billed |

If a live command is killed mid-run (crash, timeout, closed terminal), `jobs list` shows the job as
`running`, `queued` or `awaiting_approval`, and `jobs resume` continues it from the last completed step,
replaying paid calls from the cache instead of paying twice. A job that ended `failed` is finished:
fix the cause and run `gen` again.
