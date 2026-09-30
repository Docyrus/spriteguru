# SpriteGuru command reference

Every command takes `--project/-p <folder>` (except `init`, `keys`, `analyze`, `eval fixtures`,
`eval pixel-bench`) and finds the project from the working directory if you omit it.

`--mode live|synthetic` (overrides the project's default for that one command) exists only on `character
new`, `gen`, `repair`, `inbetween`, `jobs resume`, `serve` and `studio`; `character approve`, `export`,
`ledger`, `analyze`, `models` and the listing commands reject it with exit 2 because they never call a provider.
`init --mode` instead sets the project's default. `--yes/-y` (on `character new`, `gen`, `repair`,
`inbetween`, `jobs resume`) auto-approves every approval prompt, including video calls and cap crossings.

Contents: [init](#init) · [character new](#character-new) · [character approve / list](#character-approve--list)
· [gen](#gen) · [repair](#repair) · [inbetween](#inbetween) · [export](#export) · [analyze](#analyze)
· [jobs](#jobs) · [models](#models) · [ledger](#ledger) · [keys](#keys) · [eval](#eval-maintainers-only)
· [studio / serve](#studio--serve)

## init

`spriteguru init <path> [--style S] [--engine E] [--pixel-height N] [--palette-size N] [--fps N] [--asset-folder DIR] [--mode M]`

Creates `<path>` (conventionally `Name.sprites`) with `project.json`, `characters/`, `animations/`,
`cache/`. Prints only a human line on stderr; nothing on stdout, so you already know the path.

| Option | Default | Notes |
| --- | --- | --- |
| `--style` | `hd-cartoon` | `pixel`, `hd-cartoon`, `painted`, `vector`. Fixed per project. |
| `--engine` | `godot` | `phaser`, `pixi`, `godot`, `unity`, `gamemaker`. Changeable per animation with `export --engine`. |
| `--pixel-height` | 48 | Logical character height; pixel style only. |
| `--palette-size` | 16 | |
| `--fps` | 12 | |
| `--asset-folder` | none | Every export is also copied to `<folder>/<animation-name>/`. |
| `--mode` | **`live`** | The project's default provider mode. Use `synthetic` for offline. |

`project.json` also holds `settings.session_cap_usd` (10.0, spend in the last 24 h),
`settings.job_cap_usd` (3.0), `provider_mode` and `settings.image_models` (see [models](#models)). Editing
caps or image models is the user's decision, not yours.

## character new

`spriteguru character new <name> [--describe TEXT] [--image FILE] [--use-image-as-view] [--kind K] [--blend add|normal] [--mirrorable|--asymmetric] [--candidates N] [--approve] [--seed N]`

Creates the subject and generates its turnaround (an effect gets one design view). Needs `--describe`,
`--image`, or both. Names must be unique in the project (duplicates are refused).

- stdout JSON: `{"character": {name, kind, description, palette, views, view_warnings, approved,
  turnaround, turnaround_candidates, ...}, "cost": <usd>}`.
- `--kind` `character` (default), `vehicle`, `machine`, `effect`. Vehicles and machines move rigidly from
  their approved silhouette; effects come from soft shape guides and `--blend add` gives glow on black
  (exported with `blend: add`).
- `--image` accepts anything Pillow reads, up to 20 MB. With `--use-image-as-view` the image is the side
  view, no turnaround is generated (cheaper), and the description is derived from the image.
- `--asymmetric` when the west-facing version can't be a mirror of the east-facing one.
- `--candidates N` generates N turnarounds so the user can choose.
- `--approve` approves the first candidate immediately.

## character approve / list

`spriteguru character approve <name> [--candidate K]`: marks the subject usable (K is 1-based; picks that
turnaround first). Prints view warnings, which are worth reading (for example "side-w was cut apart from
a neighbour; mirrored side-e instead": the turnaround's views touched and one was replaced by a mirror).

`spriteguru character list`: table only (name, kind, style, approved, views, palette count).

## gen

`spriteguru gen <character> <action> [--facing E|W|N|S] [--frames N] [--loop|--no-loop] [--view side|top-down] [--motion in-place|root-motion] [--seed N] [--replay] [--yes]`

Compile the spec, generate candidates, analyse, repair, decide, export. Creates or overwrites the spec for
`<character>-<action>-<facing>`, so re-running the same triple replaces that animation's spec (earlier job
folders remain in `jobs/`).

- The character must be approved (else exit 2).
- Defaults for frames/loop come from the action (walk loops, attack-melee doesn't). Override only for a reason.
- **Route** depends on style and action: pixel loops use Retro Diffusion when a key exists, HD/painted loops
  use MiniMax video, one-shots use a guided sheet on the project's sprite-sheet model (GPT Image 2.5 unless
  `models` says otherwise), vector uses Quiver or GPT-6 Sol. The chosen route is in the JSON (`route`); the
  image models the job used are in the compile step (`steps[0].info.models`).
- `--seed` changes the request, so it bypasses the cache and produces a new candidate.
- `--replay` uses cached candidates only.
- stdout JSON (the job state): `id`, `anim_id`, `state` (`done`|`failed`), `error`, `route`, `winner`,
  `candidates[]` (`id`, `score`, `accepted`, `top[]` findings, `html` report path), `decisions[]`,
  `attempts` (repairs, rerolls, ...), `spend`, and `result` (`final`, `score`, `accepted`, `files`,
  `spend`, `copied_to`) when done. Paths are relative to the project root.

## repair

`spriteguru repair <anim_id> --frame N [--kind pose|identity|frame] [--note TEXT]`

Repair of frame N (**1-based**) on the latest job's winner. Guided-sheet routes only: on `video-loop`,
`rd-loop` or vector routes it exits 1 ("... jobs repair by re-roll; run `spriteguru gen` again"). `pose` fixes a frame that doesn't follow
its guide pose, `identity` pulls a drifted look back to the reference, `frame` is for other defects and takes
`--note` describing what to fix. Re-analyses and re-exports; the repair is kept only if it doesn't score
worse. JSON: `{job, candidate, before, after, kept, export}`. Bills in live mode on the project's current
repair model (GPT Image 2.5 by default, a masked edit; the fal models take no mask, so they redraw the canvas
and only the target frame is kept).

## inbetween

`spriteguru inbetween <anim_id> --after N`

Generates one extra frame between frame N and N+1 (**1-based**) and re-exports with `frames + 1`. It also
resets `durations` in `animation.json` to a flat value (observed: 120/120/80/60/100/120 ms became 86 ms x 7). Guided-sheet
routes only ("... jobs cannot insert generated in-betweens" otherwise). JSON:
`{job, candidate, frames, score, export}`. Bills in live mode on the project's current in-between model.

## export

`spriteguru export <anim_id> [--engine E]`

Re-runs the export from the final frame set, optionally switching the engine first (the choice is saved
in the animation's spec). No provider call. JSON has the absolute `dir`, `files`, and `copied_to`.
Files from a previously selected engine stay in `final/`, so tell the user which engine's files to use.

## analyze

`spriteguru analyze <sheet.png> [--action A --frames N | --spec spec.json] [--grid COLSxROWS] [--out DIR] [--style S]`

Scores any sheet (not only SpriteGuru's): matte, layout, registration, temporal checks. No provider call,
no project needed. Give `--action` and `--frames` (or `--spec`) so checks know what the sheet should
contain: without them the analyser guesses the frame order and can add spurious "frames reordered" and
"motion gap" findings (the same sheet scored Q 55 bare and Q 80 with `--action walk --frames 8`). Give
`--grid` when the layout is a known uniform grid. Writes `frames/`, `preview.gif` and
`report.html` to `--out` (default: a folder named after the sheet, next to it). JSON:
`{score, accepted, frames, report, findings[]}`.

## jobs

`spriteguru jobs list`: table of every job (job id, state, route, winner, score, spend).
`spriteguru jobs resume [--yes] [--mode M]`: continues jobs left `queued`, `running` or
`awaiting_approval` (a crash or a killed process) from the last completed step; provider calls already made
replay from the cache. It ignores jobs that ended `failed` (including a refused approval): for those, fix
the cause and run `gen` again.

## models

`spriteguru models [--set TASK=MODEL ...] [--reset]`

The image model for each task: `guided_sheet` (sprite sheets), `turnaround`, `repair`, `inbetween`. With no
options it only lists them; stderr has one line per task with the model, maker and price, stdout the JSON
`{"models": {task: model}, "available": [...]}`. No provider call.

| Model | Provider | Price |
| --- | --- | --- |
| `gpt-image-2.5-flare` (sheets' default), `gpt-image-2.5-sunburst` (the others' default) | OpenAI | token-billed, about $0.05 per sheet-sized image |
| `seedream-5.0-flash` | fal | $0.027 per image |
| `seedream-5.0-pro` | fal | $0.0675 per image |
| `seedream-5.0-lite` | fal | $0.035 per image (draws at 3.7 MP or more, resized back) |
| `flux-2-max` | fal | $0.07 first MP + $0.03 per extra MP, inputs included: about $0.25 per candidate on a 2560x1024 sheet |

`--set` (repeatable) stores a choice; picking a task's default removes the entry. `--reset` returns every task
to its default. An unknown task or model exits 2 and lists the valid ones. Changing models changes what the
user pays and what the art looks like, so only do it when they ask. A job keeps the models it compiled with,
even through `jobs resume`; `repair` and `inbetween` use the current setting. A fal model needs the fal key.

## ledger

`spriteguru ledger`: JSON summary of `ledger.jsonl`: `total_usd`, `calls`, `cache_hits`, `by_model`,
`session_usd` (last 24 h) and `unknown_outcomes` (calls sent whose result was never recorded; if this is
non-zero, tell the user). Run it before and after a live session to report actual spend.

## keys

`spriteguru keys status` shows which providers (`openai`, `fal`, `retrodiffusion`, `quiver`) have a key
and where it comes from, never the value. `keys set <provider>` (prompts, hidden input) and
`keys delete <provider>` change the OS keychain; leave those to the user. Environment variables win over the
keychain: `OPENAI_API_KEY`, `FAL_KEY`, `RD_API_KEY`, `QUIVERAI_API_KEY` (also `AI_PROVIDER_KEY_OPENAI`,
`_FAL`, `_QUIVER`, `_RETRODIFFUSION`). A `.env` found upward from the working directory (or
`SPRITEGURU_ENV_FILE`) seeds missing variables.

## eval (maintainers only)

`eval fixtures | run | pixel-bench | judge | gen`: the regression harness for the analyzer and generation
routes. Offline by default, but `eval gen --mode live --yes` spends. Use only when the task is about
developing SpriteGuru.

## studio / serve

`spriteguru studio [--browser]` opens the GUI; `spriteguru serve [--port N --token T]` runs the engine
API on 127.0.0.1 and prints `{port, token}` as JSON on startup, then blocks. Run either only when the
user asks for the GUI or API, and in the background if you must, then stop it.
