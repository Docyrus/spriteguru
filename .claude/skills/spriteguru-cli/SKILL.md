---
name: spriteguru-cli
description: Drive the SpriteGuru command line to turn a text description or reference image of a character, vehicle, machine or effect into game-ready 2D sprite animations (sprite sheet, per-frame PNGs, engine atlas for Godot/Phaser/Pixi/Unity/GameMaker, GIF preview), to repair or extend them, and to score any existing sprite sheet. Use this whenever a task involves the `spriteguru` command, a `*.sprites` project folder, generating walk/run/idle/attack cycles or character-select intros, exporting sprite sheets for a game engine, or checking a sprite sheet's quality, even if the user only says "make me a sprite of..." and never names the tool. Read it before running any spriteguru command, because live runs bill the user's OpenAI/fal/Quiver accounts, often without asking.
---

# SpriteGuru CLI

SpriteGuru turns a description (or a sketch) of a character, vehicle, machine or effect into game-ready
animation assets: `sheet.png`, per-frame PNGs, an engine atlas and a GIF preview. It runs locally; the
only outbound calls are to AI providers, and only in **live** mode. Drive it through the CLI. The studio
GUI (`studio`, `serve`) is for humans and blocks forever.

## Money comes first

Live mode spends the user's real provider credit. The CLI pauses for approval only on video calls and on
calls that would cross a spend cap (default $3 per job, $10 per rolling 24 h). A turnaround or a
guided-sheet animation under those caps runs and bills with no prompt. So "it didn't ask" does not mean
"it was free", and the repo's `.env` may already hold working keys, which makes a live run succeed
silently. Treat every live command as a purchase:

- **Rehearse offline first.** `--mode synthetic` is a deterministic simulator: free, no keys, placeholder
  art. It exercises every command, flag and output file. Use a throwaway project for it
  (`spriteguru init Scratch.sprites --mode synthetic`) and never move its characters into a live project;
  they are placeholders and would be approved and animated as if real.
- **`init` defaults to `--mode live`.** Pass `--mode synthetic` yourself unless the user has said to spend.
- **Run `spriteguru keys status` first.** It shows which providers have a key (never the value). With none
  configured a live run can only fail; with keys configured, live runs will really bill.
- **Get the user's go-ahead before the first live command**, naming what you will run and roughly what it
  costs. Their approval covers that command, not retries with new seeds or a batch of extra animations.
- **Don't pre-stage live work.** If real generation needs the user's OK, hand them the exact commands
  instead of creating a live-mode project or running `character new` "ready to go": those calls bill.
- **A blocked or failing live call is a stop sign, not an obstacle.** If a live command fails (no key,
  connection error, proxy, sandbox), do not work around it by changing network, proxy or sandbox settings,
  overriding environment variables, or retrying in a loop; report it and offer synthetic mode.
- **Never add `--yes` on your own to get past an approval.** The approval line
  (`needs approval <model> ~$0.xxx: <reason>`) is the price tag: show it to the user and let them decide.
- **Cost preview for video loops (free):** run `gen` without `--yes` and with no terminal on stdin
  (`</dev/null`). A video route stops at the approval, prints the estimate, exits 1 and spends nothing.
  Other routes have no preview; expect cents per animation and check `spriteguru ledger` afterwards.
- `gen --replay` never calls a provider; it rebuilds from cached candidates and fails with `ReplayMiss`
  if there are none. To get a genuinely new result, change `--seed` (this bills again in live mode);
  do not delete `cache/`.

## Running it

Find a working invocation once and reuse it: `spriteguru` if it is on PATH, else `uv run spriteguru`
inside the SpriteGuru checkout, else `uv run --project /path/to/spriteguru spriteguru` from anywhere
(that `--project` belongs to uv; SpriteGuru's own `--project/-p` is the `.sprites` folder).

A project stores its default mode in `project.json`, so in a synthetic project plain commands are already
synthetic; `--mode` is only for overriding that on one command (and only `character new`, `gen`, `repair`,
`inbetween` and `jobs resume` accept it).

The project is found from `--project/-p`, then `SPRITEGURU_PROJECT`, then upward from the working
directory (a `project.json`, or exactly one `*.sprites` folder in the cwd). Passing `-p` explicitly is the
least surprising. With no project the command exits 2 with `no project found`.

**Reading output.** Machine output and human output are split, so keep them apart:

- **stdout** is one JSON line, always the last line. Parse this; it has the paths and numbers.
- **stderr** is progress for humans (Rich text that wraps long paths at 80 columns, so never parse it),
  plus harmless macOS `objc[...] Class AVF... is implemented in both` warnings you can ignore.
- `character list`, `jobs list` and `keys status` print tables only, with no JSON. Read the files instead
  (`characters/<name>/character.json`, `animations/<id>/`), or run `keys status` only to see which
  providers are configured (it never shows key values).
- Exit codes: `0` ok; `1` the job or command failed (a finished job's JSON is still on stdout with
  `state` and `error`; early errors such as an unknown action are a plain traceback whose last line says
  what was wrong); `2` a precondition failed (no project, character not approved, missing option).

```bash
sg() { uv run spriteguru "$@"; }          # adapt to your invocation
sg gen knight walk -p Game.sprites 2>run.log | tail -1 > result.json
python3 - <<'EOF'
import json; d = json.load(open("result.json")); r = d.get("result") or {}
print(d["state"], d["error"], r.get("final"), r.get("score"), r.get("accepted"), r.get("spend"))
EOF
```

## The workflow

A project holds subjects; a subject must be **approved** before it can be animated; each animation
lands in its own folder.

```bash
spriteguru init Game.sprites --style hd-cartoon --engine godot --mode synthetic   # styles: pixel hd-cartoon painted vector
cd Game.sprites                                    # engines: phaser pixi godot unity gamemaker
spriteguru character new knight --describe "An armoured knight with a blue tabard and a round shield."
spriteguru character approve knight                # gen refuses (exit 2) until this is done
spriteguru gen knight walk                         # -> animations/knight-walk-E/final/
spriteguru gen knight attack-melee --frames 6 --facing W
```

- **Describe visual facts, not story**: silhouette, colours, props, materials. The description is embedded
  in every later prompt.
- **`character new` generates the turnaround**, which is the identity every animation must match. Look at
  `characters/<name>/ref/turnaround.png` (and `view_warnings` in the JSON) before approving. `--candidates N`
  makes N turnarounds; `character approve <name> --candidate K` picks one. `--approve` skips the look, so
  use it only for throwaway or synthetic runs.
- **Kinds and actions**: `character` (walk run idle jump attack-melee cast hurt death crouch climb push
  fireball intro intro-salute intro-weapon intro-taunt intro-leap intro-powerup), `vehicle` (idle move fire
  destroyed), `machine` (work activate break), `effect` (projectile charge impact explosion aura). Create
  non-characters with `--kind` (effects add `--blend add` for glow on black). A wrong action prints the valid list.
- **From an image**: `--image concept.png` draws the turnaround from it; add `--use-image-as-view` to treat
  it as the side view and skip generation. `--describe` becomes optional.
- **Facing and layout**: `--facing E|W|N|S` (side view uses E and W), `--view side|top-down`,
  `--frames N`, `--loop/--no-loop`, `--motion in-place|root-motion`. Use `--asymmetric` when a subject
  can't be mirrored for the other facing (text, one-sided props).
- Details on every command and option: `references/commands.md`.

## Reading a result and iterating

`gen` prints `Q <score> accepted|best effort`. Q runs 0-100; **accepted means Q >= 85 with no fail-level
finding**. A non-accepted result is still exported as "best effort", so check `accepted`, not just
`state == "done"`. Look at `sheet.png` / `preview.gif` yourself when you can view images: the score is
a checker, not an art director.

`candidates[].top[]` and `final/report.json` list findings (`metric`, `level`, `message`, `frames`,
`remedy`). Pick the fix by remedy:

| Problem | Fix |
| --- | --- |
| One or two bad frames (pose, identity, artefact) | `repair <anim_id> --frame N --kind pose\|identity\|frame [--note "what to fix"]` |
| Motion pops between two frames | `inbetween <anim_id> --after N` |
| Wrong engine files | `export <anim_id> --engine phaser` (no provider call, free) |
| Whole sheet is off, or the animation is a video/vector loop | `gen ... --seed <new>` for a fresh candidate (bills again) |

`repair` and `inbetween` work on the latest job's winner, re-analyse and re-export, and keep the result
only if it doesn't score worse (`kept: false` means it was discarded). **They only work on guided-sheet
animations** (`route` is `guided` or `guided-pixel` in the `gen` JSON; typically one-shot actions such as
attack-melee, cast, hurt, death). On a looping video, Retro Diffusion or vector animation they fail with a
traceback ending "jobs repair by re-roll; run `spriteguru gen` again": re-roll with a new `--seed` instead.

**Timing gets reset by `inbetween`.** It replaces the animation's per-frame `durations` (for example a
hand-tuned 120/120/80/60/100/120 ms attack) with one flat value, and the frame size can shift by a few
pixels after any repair. Read `final/animation.json` before and after, and tell the user if timing
mattered; they will need to re-apply their hit-frame durations in the engine or the JSON. `repair` keeps
the durations.

**Frame numbering trap:** `findings[].frames` is 0-based, while `repair --frame` and `inbetween --after` are
1-based. A finding with `frames: [2]` (message says "frames 3") is `repair --frame 3`.

## Where the files are

`gen` reports `result.final`, relative to the project root: `animations/<character>-<action>-<facing>/final/`.
Inside: `sheet.png`, `frames/000.png...`, `preview.gif`, `animation.json` (fps, durations, pivot, loop),
`report.html`/`report.json`, `sheet.aseprite.json`, the engine files (godot `<id>.tres`; phaser
`sheet.json`+`anims.json`; pixi `sheet.json`; unity `sheet.spriteguru.json` + importer script; gamemaker
`*_stripN.png`) and `export.zip`. If the project set an `--asset-folder`, each export is also copied there.
Spend is in `ledger.jsonl` (`spriteguru ledger` sums it).

## Leave these alone

- `cache/`, `ledger.jsonl`, `animations/*/jobs/`, `.spriteguru/`: engine state. Hand edits corrupt
  replay and spend accounting.
- `.env` and keychain entries: never print or echo them, and don't run `keys set --value` (it puts the key
  in shell history). If a provider key is missing (`no OpenAI key: run spriteguru keys set openai`), tell the
  user; setting keys is theirs to do.
- `spriteguru eval ...` is the maintainers' regression harness; `eval gen --mode live` spends. Only use
  `eval` when asked to work on SpriteGuru itself.
- `studio` and `serve` open a window or a server that never exits; skip them unless the user wants the GUI.

For failures (approval errors, `ReplayMiss`, vector renderer, Linux/Windows quirks) and the full project
layout, see `references/outputs-and-troubleshooting.md`.
