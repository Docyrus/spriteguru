# SpriteGuru

SpriteGuru is a local-first, open-source sprite studio. A Python engine and a React studio turn a
character description (or a sketch) into game-ready animation assets: a clean sprite sheet,
per-frame PNGs, an engine atlas and an animated preview.

Everything runs on your machine. There is no account, no hosted service and no telemetry: the app
talks only to the AI providers you give it keys for (see [Privacy and network](#privacy-and-network)).
Without keys, a built-in synthetic mode runs every route offline, for free and deterministically.

The core idea: every model call is shaped by a machine-readable `SpriteSpec` (grid, cell size,
ground line, chroma key, pose per frame), so the analyzer knows what the image should contain.
Everything after generation is a pure function of pixels plus spec: deterministic, testable and
free to re-run. The design is in [docs/implementation-plan.md](docs/implementation-plan.md).

## Requirements

SpriteGuru is developed and tested on macOS. The engine and studio are cross-platform Python and
web code, the desktop build also targets Windows, and Linux runs from source.

| Tool | Version | Install |
| --- | --- | --- |
| Git | any recent | <https://git-scm.com> |
| Python | Python 3.12 (uv installs it for you from `.python-version`) | via uv |
| uv | 0.12 or newer | `brew install uv`, or <https://docs.astral.sh/uv/getting-started/installation/> |
| Node.js and npm | Node.js 20.19+ or 22.12+ (current LTS recommended), with its bundled npm | <https://nodejs.org> or `brew install node` |

Platform notes:

- **macOS**: nothing else. The native window uses the system WebKit through pywebview.
- **Windows**: the native window uses Microsoft Edge WebView2, which ships with Windows 11 and
  current Windows 10. If it is missing, install the "Evergreen" runtime from Microsoft.
- **Linux**: pywebview needs a GTK or Qt backend for the native window, which `uv sync` does not
  install. Either open the studio in your browser (`uv run spriteguru studio --browser`) or add the
  Qt backend for one run: `uv run --with "pywebview[qt]" spriteguru studio`. Playwright's browser
  also needs system libraries: install them with `uv run playwright install --with-deps chromium`.

## Install from a clean checkout

```bash
git clone https://github.com/Docyrus/spriteguru.git
cd spriteguru
uv sync                              # Python 3.12, the engine and its dependencies, in .venv/
cd studio
npm ci                               # the studio's exact dependency versions from package-lock.json
npm run build                        # type-checks and builds the studio into src/spriteguru/studio_dist/
cd ..
uv run playwright install chromium   # headless browser for vector rendering and the E2E tests
```

The engine serves the built studio from `src/spriteguru/studio_dist/`. If you skip `npm run build`,
the app opens a page saying "The studio UI is not built".

## First launch without API keys (synthetic mode)

```bash
uv run spriteguru init Demo.sprites --style hd-cartoon --engine godot --mode synthetic
uv run spriteguru studio --project Demo.sprites
```

The first command creates the project folder `Demo.sprites/` in the current directory, with the
synthetic simulator as its provider mode. The second opens the SpriteGuru window on the project
gallery with Demo already open: its card says **Open now**, and the header shows the SpriteGuru
mark, **Demo** and **Synthetic**. Click **Characters** in the left rail to see "No characters yet"
and the **New character** button. Nothing asks for a key or an account. (On Linux, add
`--browser`.)

The same project works from the command line, still with no keys:

```bash
cd Demo.sprites
uv run spriteguru character new knight --describe "An armoured knight with a blue tabard and a round shield."
uv run spriteguru character approve knight
uv run spriteguru gen knight attack-melee        # writes animations/knight-attack-melee-E/final/
cd ..
```

Synthetic results are placeholders drawn by a deterministic simulator: good for learning the
workflow and for tests, not for your game.

## Provider keys

Real generation uses your own accounts with these providers:

| Provider | Used for | Environment variable |
| --- | --- | --- |
| OpenAI | turnarounds, guided sheets, repairs, in-betweens (GPT Image 2.5); the QA judge and vector motion (GPT-6 Luna, Sol) | `OPENAI_API_KEY` |
| fal | HD looping animation through MiniMax H3 Max video | `FAL_KEY` |
| Retro Diffusion | pixel-art looping animation (optional: without it, pixel loops fall back to a guided sheet) | `RD_API_KEY` |
| Quiver | vector characters and idle loops (Quiver Arrow 2) | `QUIVERAI_API_KEY` |

Store keys in the OS keychain (macOS Keychain, Windows Credential Locker, Linux Secret Service):

```bash
uv run spriteguru keys set openai      # prompts for the key; also: fal, retrodiffusion, quiver
uv run spriteguru keys status          # which keys are configured, and where from; never the values
uv run spriteguru keys delete openai
```

Or use environment variables. For development, copy `.env.example` to `.env` in the repo root (or
your working directory) and fill in the keys you have; `.env` is ignored by git, and variables
already set in the environment win. `SPRITEGURU_ENV_FILE=<path>` points at another file. The
`AI_PROVIDER_KEY_OPENAI`, `_FAL`, `_QUIVER` and `_RETRODIFFUSION` names are accepted too.

Key values are never printed, logged, cached or returned by the API. The Settings screen in the
studio manages the same keychain entries.

## Using SpriteGuru

### Command line

```bash
uv run spriteguru init MyGame.sprites --style hd-cartoon --engine godot    # --mode live is the default
cd MyGame.sprites
uv run spriteguru character new knight --describe "An armoured knight with a blue tabard and a round shield."
uv run spriteguru character approve knight
uv run spriteguru gen knight attack-melee          # guided sheet, best of 2, repair loop, export
uv run spriteguru gen knight walk --yes            # looping HD walk via video (video calls always ask)
uv run spriteguru repair knight-attack-melee-E --frame 3 --kind pose
uv run spriteguru inbetween knight-attack-melee-E --after 3
uv run spriteguru export knight-attack-melee-E --engine phaser
uv run spriteguru ledger                           # spend
uv run spriteguru studio                           # native window (or --browser)
```

Styles are `pixel`, `hd-cartoon`, `painted` and `vector`; engines are `phaser`, `pixi`, `godot`,
`unity` and `gamemaker`. Add `--mode synthetic` to `init` (or to any command) to run offline.

Spend control: every provider call is written to `ledger.jsonl` before it is sent. A session cap
(spend in the last 24 hours, so restarts never reset it) and a per-job cap ask before any call that
would cross them; an approval covers one more cap's worth. Video calls always ask.

Every job writes `animations/<id>/jobs/<time>/` (guide canvas, mask, prompt, candidates with
provenance sidecars, per-candidate HTML reports) and `animations/<id>/final/` (sheet, frames,
engine files, Aseprite JSON, GIF, report). Each export is also copied into the project's game asset
folder when one is set.

### Subjects: characters, vehicles, machines, effects

A subject has a kind. Characters are posed from joint-angle choreography on a mannequin guide.
Vehicles and machines use their own approved silhouette, moved rigidly per frame (recoil, bob,
tilt). Effects are drawn from soft shape guides (orb, tail, ring, burst, sparks); additive effects
are generated on black, matted by luminance and exported with `blend: add`.

| Kind | Actions |
| --- | --- |
| character | walk, run, idle, jump, attack-melee, cast, hurt, death, crouch, climb, push, fireball, intro, intro-salute, intro-weapon, intro-taunt, intro-leap, intro-powerup |
| vehicle | idle, move, fire, destroyed |
| machine | work, activate, break |
| effect | projectile, charge, impact, explosion, aura |

```bash
uv run spriteguru character new tank --kind vehicle --describe "An olive green battle tank with a long cannon."
uv run spriteguru character new hadouken --kind effect --blend add --describe "A blue glowing energy ball with a streaming tail."
uv run spriteguru gen tank fire
uv run spriteguru gen hadouken projectile
```

### Image references

Start from an image instead of (or as well as) a description: a sketch, concept art or an existing
sprite. Any format Pillow reads is accepted, up to 20 MB.

```bash
uv run spriteguru character new knight2 --image concept.png                       # turnaround drawn from the image
uv run spriteguru character new knight3 --image sprite.png --use-image-as-view    # the image is the side view
```

In the studio, drop an image on the character form.

### Routes

| Style | Looping | One-shot |
| --- | --- | --- |
| Pixel | Retro Diffusion animation (falls back to a guided sheet without an RD key) | Guided canvas edit on GPT Image 2.5, then pixel reconstruction |
| HD cartoon, painted | MiniMax H3 Max image-to-video, cut into a loop | Guided canvas edit on GPT Image 2.5 |
| Vector | Idle: Quiver Arrow 2 micro-animation; other loops: GPT-6 Sol authored motion | GPT-6 Sol authored motion |

Model ids, fixed parameters and prices live in `src/spriteguru/registry.yaml`.

### The studio

`spriteguru studio` starts the engine (`spriteguru serve`, bound to 127.0.0.1 with a per-launch
token) and opens the studio in a native window, on the **project gallery** unless `--project` opens
one. Each project is a folder, `~/Documents/SpriteGuru/<name>.sprites` by default
(`SPRITEGURU_LIBRARY=<dir>` moves the library), and its characters, sheets, exports, cache and
ledger all live inside it. Projects opened from elsewhere show up as recent projects.

The header holds the project switcher and the active-character switcher. Every tab works on the
active character: Characters (with its finished animations playing in place), Animation builder
(route, cost and guide canvas before sending), Candidates, Sheet review, Frame editor, Findings
(one-click remedies with costs), Export and Settings (provider keys).

### Coming from SpritePlay

A SpritePlay-era install carries over by itself, locally: `~/Documents/SpritePlay` becomes
`~/Documents/SpriteGuru` (or both are listed if both exist), recent projects, project-local
preferences, browser preferences and provider keys move to their SpriteGuru names, and cloud links
and old sign-in tokens are removed from this machine. Project assets are never changed. The details
are in [docs/failure-modes.md](docs/failure-modes.md) (MG1–MG10). `SPRITEKIT_*` environment
variables still work as fallbacks for their `SPRITEGURU_*` names.

## Development

### Running from source

`uv run spriteguru ...` always runs the code in `src/spriteguru/`. Python changes take effect on
the next command; studio changes need `npm run build` again, or the dev server below.

### Frontend development

Run the engine on the fixed development port with development CORS, then Vite:

```bash
SPRITEGURU_DEV=1 uv run spriteguru serve --port 8777 --token dev --mode synthetic   # add --project <p> to open one
cd studio && npm run dev
```

Open <http://127.0.0.1:5173/?token=dev>. Vite serves the studio with hot reload on port 5173 and
proxies `/api` (HTTP and WebSocket) to the engine on 127.0.0.1:8777.

Other settings: `SPRITEGURU_LIBRARY` (project library folder), `SPRITEGURU_PROJECT` (project to
open), `SPRITEGURU_VECTOR_BROWSER=auto|managed|chrome` (vector renderer source),
`SPRITEGURU_MODELS` (model cache, default `~/.cache/spriteguru/models`), `SPRITEGURU_OFFLINE=1`
(never download models), `SPRITEGURU_MATTE_MODEL` (rembg model, default `birefnet-general`) and
`SPRITEGURU_LOG` (engine log level).

### Tests

End-to-end scenarios are the only tests. Each one drives the real CLI, API or studio and records
checks, and each run writes one artifact folder under `e2e/artifacts/<run>/`: `summary.json`,
`index.html`, the outputs, and `digest.txt`, a hash of the normalized results. Offline runs are
deterministic, so the digest repeats run to run.

```bash
uv run pytest e2e                 # offline, all scenarios, 6 in parallel (free, ~7 min on 10 cores)
uv run pytest e2e --quick         # skips the evaluation matrices (~4 min)
uv run pytest e2e -n 0 -k brand   # one process, one scenario, for debugging
cd studio && npm run typecheck    # the studio's TypeScript
```

Live scenarios call real providers and **spend money** (about $1–2 for the whole set); they run only
when asked, with keys configured:

```bash
uv run pytest e2e --live
uv run spriteguru eval gen --mode live --yes   # the generation set against real providers
```

The subsystems tested in isolation list their failure modes first in
[docs/failure-modes.md](docs/failure-modes.md); every check in the artifact cites the IDs it covers.
The golden fixtures in `tests/golden/` regenerate byte-identically with `spriteguru eval fixtures`.

### Desktop build

```bash
packaging/build_app.sh
```

PyInstaller builds the engine (`spriteguru-engine`) and the launcher. On macOS the result is
`dist/SpriteGuru.app` (bundle id `com.spriteguru.studio`, engine in `Contents/MacOS/engine/`), and
the script checks the bundle's name, id and icon; elsewhere the launcher and its `engine/` folder
are in `dist/SpriteGuru/`, and Windows builds use `brand/SpriteGuru.ico`.
The app doesn't bundle Chromium: vector rendering uses Playwright's headless browser when it's
installed, else the installed Google Chrome, else it downloads the headless browser once (about
96 MB) on the first vector job.

### Committing generated assets (optional Git LFS)

SpriteGuru writes PNG sheets, frames and GIFs into your game's asset folder. If you keep that game
in git, [Git LFS](https://git-lfs.com) keeps the repository small:

```bash
git lfs install
git lfs track "*.png" "*.gif" "*.webp"
git add .gitattributes
```

Each project writes its own `.gitignore` for `cache/`, job folders and the machine-local
`.spriteguru/` folder.

## Troubleshooting

- **"Executable doesn't exist" or a vector job waiting on a download**: install the headless browser
  with `uv run playwright install chromium` (Linux: `--with-deps`). Offline, vector jobs fail with a
  plain message until it is installed.
- **The window doesn't open** (Linux: no GTK/Qt backend; Windows: no WebView2): run
  `uv run spriteguru studio --browser`, or see the platform notes under [Requirements](#requirements).
- **"The studio UI is not built"**: run `npm ci && npm run build` in `studio/`.
- **"no keychain backend available"** (headless Linux, containers): set the provider environment
  variables or a `.env` file instead of `keys set`.
- **Port 8777 or 5173 is already in use** (development only; the app itself picks a free port):
  stop the other process (`lsof -i :8777`), or start the engine on another port and change the proxy
  target in `studio/vite.config.ts`.
- **A live command says a provider key is missing**: `uv run spriteguru keys status` shows what the
  engine sees. Keys set in a shell after the studio started need a restart. Without keys, use
  `--mode synthetic`.

## Project layout

```text
src/spriteguru/
  spec.py registry.yaml registry.py keys.py env.py project.py library.py cache.py ledger.py
  planner.py guide.py figure.py choreo/ prompts/        generation inputs
  providers/  openai fal retrodiffusion quiver synthetic hub
  pipeline/   matte layout register pixel temporal video vector motion analyze browser
  qa/         metrics judge score repair
  export/     engine formats
  eval/       fixtures golden store
  jobs.py generate.py character.py service.py edits.py manual.py
  api.py cli.py launcher.py report.py filelock.py
studio/       Vite + React studio (built into src/spriteguru/studio_dist/)
e2e/          E2E scenarios and the artifact writer
tests/        golden analyzer fixtures with ground truth, and scenario fixtures
packaging/    PyInstaller specs and the desktop build script
brand/        logo, marks and app icons (see brand/README.md)
notebooks/    one per pipeline module, for visual debugging
docs/         implementation plan and failure modes
```

## Privacy and network

SpriteGuru has no account, server, telemetry or update check. The engine listens only on
127.0.0.1, behind a per-launch token. It makes outbound connections only for:

- **the AI providers you configured**, with your prompts and images, when you run a live job
  (OpenAI, fal, Retro Diffusion, Quiver). Synthetic mode makes no provider calls;
- **one-time downloads of open models and tools**: the DINOv2-small identity model from Hugging Face
  (skipped with `SPRITEGURU_OFFLINE=1`; a built-in descriptor stands in), the rembg matte model on
  first use, and Playwright's headless browser for vector rendering when neither it nor Chrome is
  installed.

onnxruntime's own telemetry is switched off before it loads. Projects, keys, ledgers and exports
stay on your disk.

Please don't describe a security issue in a public issue: open one that asks for a private
contact, without details, and a maintainer will reach out.

## Contributing

Contributions are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers the workflow: setup (this
README), tests first, offline before live, and never committing keys or other people's assets.

## License

SpriteGuru is released under the Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
