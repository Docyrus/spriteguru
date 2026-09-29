# Failure modes

Written before the code. Each subsystem that is tested in isolation (golden fixtures,
replayed jobs) lists every way it can go wrong. Each ID is covered by a fixture case in
`tests/golden/` or a scenario in `e2e/`, and the checks in the E2E artifact cite these IDs.

## Provider layer (cache, ledger, budget, retries)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| P1 | Same request sent twice pays twice | Second call is a cache hit; the ledger gets a `cache_hit` line with cost 0 |
| P2 | Cache key ignores an input (image bytes, seed, prompt, param order) | Key is SHA-256 over canonical JSON + input image hashes; changing any input changes the key, reordering dict keys does not |
| P3 | Crash mid-call leaves spend invisible | Ledger line with status `sent` is written before the request; the outcome line follows |
| P4 | Call would cross the session or job cap | Call raises `BudgetExceeded` before sending unless the caller approved it |
| P5 | 429 or 5xx aborts the job | Exponential backoff with jitter, bounded by a 10-minute total timeout |
| P6 | 4xx (bad request, auth) retried forever | Non-retryable errors fail immediately with the provider's message |
| P7 | Key leaks into logs, ledger, cache or API responses | Keys are never serialized; the settings API returns only `configured: true/false` |
| P8 | `--replay` makes a network call | Replay mode raises `ReplayMiss` on a cache miss instead of calling |
| P9 | Too many parallel calls to one provider | Per-provider semaphore of 4 |

## Matte (section 9)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| M1 | Background gradient or vignette leaves a halo or keeps background as foreground | Tile plate models the drift; D is measured against the plate, not a flat key |
| M2 | Noise speckle becomes foreground | Hysteresis with σ-scaled thresholds removes isolated noise |
| M3 | Soft anti-aliased edges eaten or kept as fringes | Low threshold keeps the edge, then alpha by projection and despill |
| M4 | Gap between arm and torso stays filled with key | Enclosed key-like pockets above min area or very close to the key are cleared |
| M5 | Dark detail enclosed by the character (eye, belt) cleared as a hole | Only key-like pockets (D < t_low) are candidates for clearing |
| M6 | Key-coloured spill on the character edge (magenta fringe) | Despill: C' = (C − (1−α)B)/α near the edge |
| M7 | Non-uniform background (scenery, two colours) keyed badly | Dominant-bin share < 0.6 flags non-uniform and routes to the ML matte fallback |
| M8 | Input already has alpha (Retro Diffusion) | Alpha path: keep alpha and clean it, no keying |
| M9 | Character colour close to the key | Key contamination metric counts character pixels within t_low of the key |
| M10 | Pixel style gets partial alpha | Pixel styles binarize at α ≥ 0.5 |
| M11 | Embedded ICC profile or palette / greyscale PNG mode | Everything is decoded to RGBA8 sRGB first |

## Layout and segmentation (section 10)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| L1 | Grid lines or borders drawn by the model split or join frames | Long thin structures removed by morphological opening; positions kept as grid priors |
| L2 | Text labels or frame numbers counted as frames | Clusters of small same-height components removed, "labels present" finding |
| L3 | Detached parts (sword, hair, effect) become their own frame | Component grouping attaches small components to the nearest anchor |
| L4 | Weapon reaches into the neighbour column; straight cut splits it | Seam cut routes around foreground |
| L5 | Frames touch; no clean cut | Cheapest seam anyway, "frames touching" finding |
| L6 | Rows offset horizontally from each other | Column cuts are fitted per row band |
| L7 | Irregular spacing (model drifts from the guide) | ±35% DP window around expected cuts |
| L8 | Wrong number of frames (model drew 7 of 8) | Frame-count finding, re-roll remedy; never silently padded |
| L9 | Frame order wrong because rows are uneven in height | Rows clustered by bottoms, then left to right |
| L10 | Limb clipped by the cell or image edge | Clipping finding when the mask runs straight along a border > 3% of height |
| L11 | Cast shadow on the key merged into the character | Shadow classification (same chroma direction, 20–80% darker) splits it off |
| L12 | Reference strip in guided sheets counted as a frame | The strip region is excluded from detection using the spec's grid, by component (L14) |
| L13 | Low-confidence layout accepted silently | Confidence below 0.15 or count mismatch raises a layout-confidence finding |

## Registration and normalization (section 11)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| R1 | Bounding-box alignment makes feet slide and head shake | Anchor on the ground line + torso core, not boxes |
| R2 | Stray pixel or pointed toe sets the ground line | Robust ground row: lowest row with width ≥ 15% of median row width |
| R3 | Arms swing the horizontal anchor | Torso band pixels with DT depth ≥ 30% of the band max |
| R4 | Jump height flattened | Airborne frames keep their offset from the shared ground line |
| R5 | Intended bob removed | Loops keep a truncated Fourier fit of y (harmonics 2, 4 walk/run; 1 idle) |
| R6 | A wrong frame is "corrected" by a large shift | Corrections clamped to 4% of height; larger ones become findings |
| R7 | Scale drift across frames | Median of three pose-invariant ratios; corrected only above 3% and when measures agree |
| R8 | Frame faces the wrong way | Mirror comparison with embeddings; flip when mirrorable, repair otherwise |
| R9 | Resampling creates dark halos | Resample only in premultiplied linear light |
| R10 | Output canvas crops a limb | Common canvas is the union of aligned boxes plus padding |

## Pixel reconstruction (section 12)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| X1 | Non-integer pitch (e.g. 5.7 px) | Comb score over 0.05 px steps |
| X2 | Harmonic error (2× or 0.5× pitch) | Smallest pitch within 90% of best; search restricted to 0.6–1.6 × p0 |
| X3 | Grid drifts across the frame | Lattice boundaries fitted by DP with a spacing penalty |
| X4 | Different pitch per frame | Lattice fitted per frame |
| X5 | Anti-aliased boundaries between fake pixels | Sample only the central 50% of each cell |
| X6 | Gradients or noise inside a fake pixel | Median of the largest colour group |
| X7 | Hundreds of near-duplicate colours; flicker across frames | Joint k-means++ palette over all frames with pinned reference colours |
| X8 | Orphan pixels, doubled outline corners | Orphan removal, pixel-perfect L-corner rule |
| X9 | Source was not pixel art | Grid fidelity residual reported high |

## Temporal analysis (section 13)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| T1 | Frames shuffled | Held–Karp optimal cycle/path; reorder when below the reading-order threshold. Walk and run cycles are ordered over twin pairs (the halves are near-mirrors). A shuffle that is cheaper than the true order (passing-left and passing-right are near-identical) cannot be recovered by any distance; the gait check fails it instead |
| T2 | Walk cycle contact poses near-mirror, reorder triggered falsely | Stricter 70% threshold for walks |
| T3 | Duplicate frame (stutter) | Pair distance < 0.25 m flagged |
| T4 | Missing in-between (jump in motion) | Pair distance > 2.5 m flagged |
| T5 | Half cycle delivered as a loop | Seam ratio > 2.5 flagged as not a loop |
| T6 | Same leg leads twice | Leg-spread DFT dominant bin ≠ 2 catches limps; a signed leading-leg cue (near limbs lighter) must alternate once per loop, so a dominant k = 2 there means the same leg leads both steps |
| T7 | Idle motion exaggerated | Idle head travel > 3% of height flagged |
| V1 | Video loop period misdetected | Self-similarity search over [T_min, T_max] |
| V2 | Motion-blurred frames chosen | Sharpest within ±1 source frame by Laplacian variance |
| V3 | Video background drifts over time | Each video frame matted against its own plate |

## Vector and motion (sections 6.8, 6.9)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| S1 | SVG contains `<script>`, `on*` handlers, `foreignObject`, external `href` | Removed by the allowlist sanitizer; sanitizer report lists removals |
| S2 | XML bombs / external entities | defusedxml parse rejects them |
| S3 | Root width/height missing or wrong; frames render at the wrong size | Sanitizer sets width/height to the frame size |
| S4 | Animation sampled during its opening | Sampler skips `opening_animation_ms` |
| S5 | Rendering reaches the network | Browser context is offline |
| S6 | Morph keys with different command structures | Validator rejects with a precise message |
| S7 | Loop pose at t = 0 differs from t = 1 | Validator rejects for loops |
| S8 | Missing rig group or rubberhose part not a single path | Rig validator rejects with the failing check |
| S9 | Attachment to a non-path host | Validator rejects |

## Jobs (section 5)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| J1 | Crash mid-job loses work or pays again on resume | `job.json` checkpoint before and after each step; provider calls replay from the cache |
| J2 | Cancel interrupts an in-flight call so its output is lost | Cancellation is checked only between steps |
| J3 | Too many jobs at once | Global semaphore of 3 |

## Export (section 15)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| E1 | Bilinear filtering bleeds neighbours | 2 px padding and 1 px edge extrusion |
| E2 | Engine metadata disagrees with the sheet (frame rects, pivot, durations) | Every format is written from one canonical frame set and re-parsed in the E2E check |
| E3 | Pixel art blurred on upscale | 4× copy uses nearest neighbour |
| E4 | GIF dithering on pixel art | GIF palette per animation, no dithering for pixel art |

## Found in live runs (added after the first real-provider tests)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| M12 | Video chroma subsampling smears the key into the outline, deeper than the alpha band (magenta rim) | Hue-projection despill within 5 px of the edge, against the local deep interior (≥ 5 px inside) |
| G1 | The image model places figures up to ~70% of body height away from their mannequins and draws them bulkier | Pose conformance normalizes placement and height, uses an 8% tolerance band, and judges reach per direction as a within-sheet outlier |
| G2 | The model draws a different pose than the guide asks for (a thrust instead of an overhead swing) | Reach outlier flags the frame; POSE FIX repair |
| Q1 | DINOv2 embeddings move with extreme poses (tucked jump), faking identity drift | Embedding-only outliers are warnings; palette drift is the failing identity signal |
| R11 | Scale measures assume an upright body; lying frames (death) read as 30% scale drift | Frames wider than tall are left out of scale analysis |
| T8 | Idle breathing frames are near-identical, so median-relative order, duplicate and seam ratios are noise | Subtle actions skip reordering and duplicates; gaps and seams must be large outright |
| P10 | A provider call never completes (Quiver animation at high reasoning effort ran 15+ minutes) | Streaming with per-provider timeouts; the ledger shows an unknown outcome if the process is stopped; animation uses medium effort |
| P11 | Provider rejects a parameter combination (Quiver: low effort animation, "not supported yet") | Non-retryable 400 surfaces the provider's message immediately |
| V4 | Top-down front/back walks move the feet vertically, so horizontal leg spread reads as "legs barely move" | Gait spectrum applies to side-like views only |

## Image references (written before the code)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| I1 | The uploaded image has a busy background (photo, scene) | Used as a reference for the turnaround edit, the model isolates the subject; used directly as a view, the key matte falls back to the ML matte |
| I2 | The image is huge (6000 px) or tiny (48 px pixel art) | Normalized: long edge capped at 1536 for provider input; tiny images upscaled with nearest neighbour for the edit, kept as-is for pixel views |
| I3 | The image already has transparency (a sprite PNG) | Alpha path: alpha kept and cleaned |
| I4 | Not an image, an animated GIF, or an unsupported format | Decoded with Pillow (first frame); anything undecodable is rejected with a clear 400 |
| I5 | No description given and the describe call fails or is offline | Falls back to "the character shown in the reference image"; the simulator describes deterministically |
| I6 | Several subjects in the image | Used as a view: the largest component, with a warning |
| I7 | The subject faces left | The describe step reports the facing; a left-facing view is flipped before becoming side-e |
| I8 | Oversized or hostile upload through the API | Size limit, decoded and re-encoded as PNG, written only inside the project |

## Subject kinds: vehicle, machine, effect (written before the code)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| K1 | Humanoid mannequins drawn for a tank, a machine or a fireball | Kind-specific guides: the object's own silhouette, rigidly transformed per frame; effect shapes for effects |
| K2 | Ground-locking pulls a floating effect to the floor | Effects are anchored on their centroid, never ground-locked |
| K3 | Body-specific checks (gait, torso anchor, upright scale measures, identity by pose) raise false findings on objects | Checks are kind-aware: gait only for characters; scale and facing skipped for effects |
| K4 | A glowing effect keyed on a chroma background keeps coloured halos | Additive effects are generated on black and matted by luminance (alpha = max channel, colour unpremultiplied) |
| K5 | Luminance matting erases dark effects (smoke, shadows) | Effects declare their blend: `add` on black, `normal` on the chroma key |
| K6 | Character-only prompt text ("arms slightly away from the body") in object prompts | Kind-specific turnaround and sheet templates |
| K7 | AI-coded motion needs a humanoid rig; objects have none | Vector objects and effects animate through Quiver with the action's motion prompt |
| K8 | The game engine blends an additive effect normally (black box around the glow) | Export metadata carries `blend: add` (animation.json, TexturePacker meta, Phaser anims) |
| K9 | Rigid objects jitter between frames, or intended recoil and tilt get "corrected" away | Rigid registration: ground contact and centroid x; offsets in the choreography (recoil, bob) are preserved |

## Found while verifying subject kinds and image references (phase 2)

- **T9 — turnaround views touch.** A long subject (a tank is ~2.5× as wide as tall) drawn four times in a row at the height the prompt implies overlaps its neighbour; the layout seam then cuts through the pair and the side-e reference is clipped, which clips every frame guided from it. Detection: the turnaround layout marks touching/clipped views. Response: a mirrorable subject's clean side view replaces the cut one (flipped); otherwise `view_warnings` on the record, printed by the CLI and shown in the studio, ask for a reroll or another candidate. Prevention: the object turnaround prompt (v2) says views never touch and to shrink long subjects.
- **K10 — faint frames vanish on black.** The luma matte's noise floor was the 99th percentile of the sheet border; an effect whose glow reaches the border (a reference strip placed flush left, a wide burst) raised it to ~35 and the last "faint sparks" frame fell below it (detected 5 of 6 frames). Fix: median + 6·MAD floor, capped by the 99th percentile; the guide also narrows a reference that would spill out of its strip.
- **K11 — sparks treated as strays.** Layout drops small marks far from every large component as strays; an effect frame made only of sparks has no large component, so its sparks were dropped and the frame failed pose conformance. Fix: in effect mode, marks wholly inside an expected guide cell are never strays.
- **K12 — fade-outs read as duplicates.** The duplicate test is relative to the median step; in a burst the early steps are huge, so two dim late frames (thin ring, scattered sparks) fell under 25 % of it. Fix: for effects a duplicate also needs alpha-mask IoU > 0.8.
- **Q2 — uniform recolour passes identity.** The identity test flags outlier frames (z-scores against the other frames), so an animation recoloured as a whole — a video that drifted from its start frame, a model that ignored the reference image — scored 96–100. Detection: palette coverage, the histogram intersection of each frame's colour mass with the reference view's palette coverage in OKLab (τ = 0.09). Calibration over 707 exported animations: correct non-pixel outputs ≥ 0.60 (live 0.81–0.89), recolours 0.44–0.49; fail below 0.55, whole animation, remedy re-roll. Pixel style is exempt (its reduced palette scores 0.36–0.47 against the full-resolution view).
- **K13 — pixel effects lose their dim parts.** The luma matte cut pixel-mode alpha at 0.5, so a dark-blue ring (max channel < 128) vanished, and choreography strokes thinner than one logical pixel (a 0.025 ring, 0.015 sparks at 48 px) never survive the lattice. Fix: pixel luma alpha cuts at 0.12 and keeps the painted colour (additive display adds it as is); the guide draws strokes and sparks at least 1.6 logical pixels wide.
- **P12 — scattered per-frame pitch on flat subjects.** Pitch was estimated per frame; a machine's large flat panels give a weak lattice signal and the estimates scattered 7.9–11.7 (pitch_cv 12 %, a false pixel_fidelity fail). Fix: one joint pitch from the summed per-frame score curves; a frame keeps its own pitch only when its score at the joint pitch is under 60 % of its own best (a frame truly drawn at another pitch loses phase within a few cells and scores 0.3–0.5 there; flat panels make false peaks at other pitches but still score 0.73–0.87 at the true one), so a sheet really drawn at mixed pitches is still caught (X4).
- **R12 — pixel in-betweens at the wrong resolution.** The in-between cell was inserted at source resolution among frames already reduced to the logical lattice (a 91.6 % scale fail); snapping it with the drift DP inflated it 1.25× instead, because the resampled cell has soft edges and the DP packs boundaries at its minimum spacing. Fix: the cell is reduced at the sheet's known pitch on a regular lattice (phase fitted) and snapped to the sheet palette.
- **V5 — vector subjects need the approved view.** The vector route sends the approved side view to Quiver; a generation from text alone can draw a different object (caught by Q2's palette coverage, 0.15).
- **M14 — background seen through small gaps stays opaque.** Live GPT Image 2.5: the tank's muzzle-brake ports showed the magenta background painted slightly shaded ((227, 13, 217) against the key (250, 3, 250), D 0.03–0.064). That is above t_low, so hysteresis joined the ports to the outline and they were exported as opaque magenta. Fix: an enclosed region of *shaded key* (at least 4 px, not touching the exterior, D < 0.12) is a hole whatever its size. Shading scales linear RGB, and so scales OKLab L, a and b alike: the region must keep the key's hue (cos > 0.97) and its C/L ratio (|C/C_key − L/L_key| < 0.08). A costume mixed toward grey near the key (the M9 fixture, (60, 224, 60) on (0, 255, 0), D 0.089) loses chroma faster than lightness and stays opaque. A raw D threshold could not separate that fixture from the live ports. The synthetic tank now has a muzzle brake whose ports are painted as shaded key.
- **K14 — the strike-reach test fires on vehicles.** Live tank fire: the impact frame (the muzzle flash, frame 2) is not where the silhouette reaches furthest forward, because the smoke cloud keeps expanding for two more frames, so "forward reach peaks at frame 4" cost 20 points on a correct sheet. Fix: the reach-at-impact test applies to characters only (K3).
- **K15 — the idle head-travel test fires on vehicles and machines.** The user's Prism Tank idle: its launcher rises and settles as intended, and the silhouette's top moved 8.9 % of its height, so "idle head travels too much" warned on a correct sheet. The test guards a character's breathing idle; an object's moving parts (turrets, launchers, antennas) legitimately move its top. Fix: the idle head-travel test applies to characters only.
- **M15 — smoke over the key exports opaque and key-tinted.** Live tank fire: the fading muzzle-smoke wisps were painted as grey blended with magenta, (204, 140, 212). They were keyed as opaque foreground and exported pink. Thinness is not the discriminator, because puffs thicker than 10 px have their own deep interior. Colour alone is not safe either: a red costume on a magenta key leans toward the key as much, with a lean ratio of about 0.37. Fix: a foreground pixel whose chroma leans toward the key, with lean ratio r > 0.15 against the plate, and whose colour is more than 0.08 OKLab from every colour of the approved subject's palette is a translucent neutral over the key: α = 1 − r, colour unmixed from the plate. Smoke is never in the approved reference; a costume colour is. Skipped without a reference palette.
- **Q3 — the judge reports intended motion as defects.** In live tank runs GPT-6 Luna saw only the action title. It reported the recoil tilt as "wrong pose", the firing frames as "enlarged", and the smoke as "stray marks". The warnings added up to Q 79.5 on a sheet whose CV checks passed. Fix: the judge prompt (v2) lists what each frame is meant to show, taken from the choreography in playback order, and says the motion, tilt, recoil, flashes, smoke and glow described there are intended. The list is passed only while frames map one-to-one onto the choreography. Re-judging the same live candidate: Q 92, accepted.

## Layout: the reference strip and confirmed cells (written before the code, from a user report)

The user's live tank sheet (prism-tank-move-W): GPT Image 2.5 drew every tank about 150 px left of its guide cell. The left-column tanks crossed into the reference strip, and the layout erased the strip by rectangle, which cut 40 px off their rear in frames 1, 3, 5 and 7. The user widened those cells in Sheet review and confirmed. Every confirmed candidate came back with the automatic regions, because the cells were only a hint and the components hypothesis still won.

| ID | Failure | Required behaviour |
| --- | --- | --- |
| L14 | A frame the model drew partly inside the reference strip is cut at the strip edge | The strip is excluded by component: a connected region mostly inside the strip rectangle is the reference and is dropped; a region mostly outside it is a frame and is kept whole, even where it crosses the strip |
| L15 | The reference drawing touches a frame, so both form one component | That merged component alone falls back to the rectangle cut, and the frame is flagged as clipped so the user sees it |
| L16 | Confirmed cells are ignored; the automatic layout is re-detected | A confirmed layout is forced: frame *i* is exactly the foreground inside cell *i*, the report's regions equal the confirmed cells, and the studio shows them again after navigating away |
| L17 | A confirmed cell reaches into the reference strip | Inside a confirmed cell nothing is excluded; the user's cell wins over the strip rectangle |
| L18 | Confirmed cells overlap | Each pixel in an overlap goes to the cell whose centre is nearest; nothing is counted twice |
| L19 | A confirmed cell still cuts through the subject | The frame is flagged as clipped where its foreground runs along any edge of its cell, the sheet edge included |
| L20 | A malformed confirmation (out of bounds, zero area, fewer than 2 cells, an unknown candidate) | Rejected with a 400 (404 for the candidate) and a clear message; cells overhanging the sheet by up to 8 px are clamped. The frame count follows the confirmed cells, since the user may add or remove one |
| L21 | Re-analysis on confirmation loses the guide: rigid recoil offsets and pose conformance vanish | The confirmation re-analyzes with the job's guide information, with the cells overriding only the layout |
| L22 | A confirmed layout is re-derived from its parent on a later confirmation | The confirmation is stored on the candidate (`layout_confirmed`), and a later confirmation starts from that candidate's own cells |
| L23 | The confirmed layout is saved but not used: returning to Sheet review shows the old winner, and the export keeps the cut frames | Confirming makes the confirmed candidate the exported one and rewrites the export (the API's `use: false` only adds it for comparison); Sheet review then shows it, and offers "Use this one" for any other candidate it displays |

## Project library and the active character (written before the code, from a user request)

The studio opens on a gallery of projects; nothing can be made outside a project. The header holds a project switcher and an active-character switcher, and every tab follows the active character.

| ID | Failure | Required behaviour |
| --- | --- | --- |
| P1 | The app opens straight into a project, or into nothing, and work is made outside any project | The engine starts without a project; every project-scoped endpoint answers 409 "no project open"; the studio shows the gallery until a project is opened or created |
| P2 | Two projects get the same folder, or a name makes an unusable folder (slashes, dots, empty, reserved) | The folder is `<library>/<slug>.sprites`; an existing folder is refused (409); names that give no slug are refused (400); the display name keeps the user's spelling |
| P3 | Opening an arbitrary path through the API reads or writes outside projects | Only folders holding a readable `project.json` open (400 otherwise). Library thumbnails are served by project id from the listing, never by a path in the request |
| P4 | Switching projects while a job runs orphans it, or keeps spending from the old project | Switching is refused (409, naming the running job) until the job finishes or is cancelled |
| P5 | After a switch the old project's runner, cache or ledger leaks into the new one | A switch builds a new runner, hub and ledger for the new project; old tasks are cancelled; a `project_changed` event tells every open studio to reload |
| P6 | A project in the recent list was moved or deleted | The listing marks it missing and it cannot be opened; "Remove from list" drops it without touching any files |
| P7 | A broken `project.json` in the library folder crashes the listing | The card shows the error; the rest of the gallery still lists |
| P8 | The active character is lost on restart or leaks to another project | It is stored in that project's `project.json`, restored when the project opens, and never shared between projects |
| P9 | The active character names a character that no longer exists | It reads as none; the studio asks for a character instead of failing |
| P10 | Setting an unknown active character | Rejected with 404; the previous choice is kept |
| P11 | The legacy single-project launcher state (`last_project`) and projects outside the library folder disappear from the app | They show in the gallery as recent projects |

## Runtime

- **RT1 — onnxruntime telemetry aborts the process at exit and contacts Microsoft.** Intermittent `libc++abi: terminating due to uncaught exception of type std::__1::system_error: recursive_mutex lock failed` right after a job finished, more often under load, which failed CLI commands with exit code −6. The macOS crash reports put the faulting thread in onnxruntime's bundled telemetry (`Microsoft::Applications::Events::HttpClientManager::onHttpResponse` → `LogManagerImpl::DispatchEvent`). An upload response arrived after the process had begun tearing down static objects. It also meant every model load sent telemetry, although SpriteGuru promises to talk only to the providers the user has keys for. The telemetry system starts when onnxruntime is imported, so `onnxruntime.disable_telemetry_events()` alone did not help: crashes continued, with the main thread in `~PosixEnv` → `PosixTelemetry::Shutdown` → `cancelAllRequests` while the HTTP worker handled a response. Fix: `ORT_DISABLE_TELEMETRY=1` is set in `spriteguru/__init__.py`, before anything imports onnxruntime. Importing onnxruntime then starts 1 thread instead of 3; the two telemetry workers never exist. `disable_telemetry_events()` is still called before sessions are created.
- **RT2 — closing the studio stalls the engine and prints a traceback.** Found in the clean-checkout validation: the events socket only ever sent, so it noticed neither a closed window nor the server shutting down, and every quit waited out uvicorn's 3-second grace period and then logged `Exception in ASGI application` with a `CancelledError`. Fix: the handler waits on the socket's own receive (which ends on a disconnect, including the close uvicorn sends at shutdown) while a separate task sends the events. The engine now exits at once with no traceback (e2e/test_25_open_source.py).

## Found by an agent using the CLI for a showcase (written before the code)

| ID | Failure | Required behaviour |
| --- | --- | --- |
| V6 | Video-route frames keep key-coloured halos: a cyan antenna glow drawn over a blue key stays opaque blue-cyan (190–460 px a frame on the robot). The video frames were matted without the character's palette, so the smoke/glow unmixing (M15) never ran, and no key check ran on them | Video frames are matted with the approved palette (M15 applies), and analyze_frames reports key colour left in the frames the way the sheet route does |
| M16 | A key-tinted translucency in the approved view (smoke or steam over the magenta turnaround) is baked into the design: its lilac colour enters the palette and the reference, the model copies it into every frame, and unmixing against the sheet's own key (M15) cannot remove a magenta tint | Turnaround prompts (v2, v3) ask for no smoke, steam, sparks or light halos in reference views. A palette colour that is a grey seen through magenta no longer protects pixels from M15. (A rule that also dropped palette colours leaning toward the sheet's key was tried and removed: a green-tunic character on a green key lost its tunic, since key selection does not always keep design colours far from the key.) For subjects whose description names smoke, steam, exhaust, mist or a chimney (and normal-blend effects), pixels that are a grey seen through magenta become that grey with the matching transparency, in the approved views and in every frame. A plain view warning was measured and dropped: red designs lean toward magenta as much as tinted smoke does |
| V7 | Vector walks on the rig: the rig preparation redraws legs as pale strokes (#F1F1F0, #D2D2D2) with no outline, so legs read as thin and a far leg vanishes on light backgrounds (the foot looks detached) | After rig preparation, each rubberhose limb takes its fill colour from the original drawing along its rest path (outline pixels ignored), and gets an outline copy underneath when the drawing is dark just outside the limb there. The copy shares the limb's geometry and moves with it. The changes are reported on the job |
| K16 | An effect frame repaired into the wrong phase (a full fireball where the choreography says the smoke fades) passes, because effect pose conformance normalizes scale | Effect frames are also checked for size against their guide shape: an area far above or below the guide's (a factor of 2.2) fails for that frame; the repair prompt states the frame's intended phase |
| K17 | Machine "work" (and vehicle) lines add steam or smoke even when the description names none | Exhaust in object choreography is optional: a line's steam/smoke clause is kept only when the description mentions steam, smoke, exhaust or a chimney, with that word |

## Animations on the Characters page (written before the code, from a user request)

The active character's card lists its finished animations. Each one plays in place and has an Open menu for its Sheet, Candidates, Frames, Findings and Export tabs.

| ID | Failure | Required behaviour |
| --- | --- | --- |
| A1 | The list shows other characters' animations, or keeps the previous character's after the header switcher changes it | Only the active character's animations are listed, and the list follows the switcher |
| A2 | Animations with no export (planned but never run, failed, first job still running) show up as players with broken images | Only animations with an export are listed; with none, the panel says so and links to the builder |
| A3 | A card plays stale frames after an edit, repair, in-between or new job rewrites the export (browser cache), or keeps the old frame count | Frame URLs carry a version taken from the export event and the export's contents; the frame count and durations follow the new export |
| A4 | Playback ignores per-frame durations set in the frame editor, or a one-shot (attack, death, explosion) loops seamlessly as if it were a cycle | Frames are shown for their exported durations; a one-shot holds its last frame before it starts again |
| A5 | Every card loads every frame up front and a character with many animations stalls the page | A card loads only its first frame until it is played |
| A6 | An additive effect is shown flattened on a light checker (preview.gif) so its glow cannot be judged | Additive animations play on the dark, screen-blended stage that Export uses |
| A7 | An Open menu entry opens another animation (the tab's last one, or the character's newest) instead of the one picked | Each entry opens that tab on the picked animation, which becomes the current one, so the rail's animation tabs open it too |
| A8 | Pausing leaves the timer running, or a card keeps a timer after it is gone (character switched) | Pause stops on the current frame; timers end when a card pauses or unmounts |

## Brand: header mark and app icons (written before the code, from a user request)

The studio header shows the SpriteGuru mark from `brand/svg/`, and the app uses the icons from `brand/`.

| ID | Failure | Required behaviour |
| --- | --- | --- |
| B1 | The header shows the old placeholder or a broken image: the SVG lives outside studio/, so the build drops it or the dev server refuses to serve it | The header mark is `brand/svg/logo-mark.svg` (same drawing), loaded in the built studio the engine serves |
| B2 | On the dark theme the mark disappears into the dark header, or the mark stays on the dark variant after switching back | The dark theme shows `logo-mark-on-dark.svg` (the same light tile, so the mark looks identical on both shells) and the light theme `logo-mark.svg`; the theme toggle switches it both ways |
| B3 | The mark is stretched, drawn at a fractional position (blurry S edges), or crowded by the rail edge and header; the 16 and 32 px icon rasters are blurred grey | The header mark is a 32 px square on whole-pixel coordinates, pixel for pixel like the same browser's raster of the SVG, in the header's left corner with at least a quarter of its width clear on every side, at full and narrow widths. The icon rasters snap the S cells to whole pixels (every cell keeps at least one) and contain the exact ink and blue colours at 16 and 32 px |
| B4 | The browser tab still shows the old placeholder favicon | The favicon is the brand mark |
| B5 | The built .app shows PyInstaller's default icon, or the Windows exe has none | The launcher bundle's Info.plist names an icon that is byte-identical to `brand/SpriteGuru.icns`; the Windows exe uses `brand/SpriteGuru.ico`. build_app.sh checks the bundle after building |
| B6 | Run from source (`spriteguru studio`), the Dock and window show Python's icon | The launcher hands pywebview the brand icon: the macOS app icon PNG on macOS, the mark elsewhere |
| B7 | A missing brand folder (an installed package, a frozen build without it) stops the window from opening | No brand file means no custom icon, never an error |

## The vector renderer's browser (written before the code, from a user decision)

The app doesn't bundle Chromium (it would add ~195 MB). Vector rendering uses Playwright's own headless
browser when it's installed, else the installed Google Chrome, else downloads the headless browser
once (about 96 MB) the first time a vector sprite is made.

| ID | Failure | Required behaviour |
| --- | --- | --- |
| BR1 | A clean machine (no Playwright browser, no Chrome): the first vector job dies with "Executable doesn't exist" | The headless browser is downloaded once, then the job renders |
| BR2 | The download is attempted offline or the host fails: a stack trace, or a job stuck forever | The job fails with a plain reason ("needs a one-time download … connect to the internet") within the installer's time limit, and the next job tries again |
| BR3 | Two vector jobs (or the studio and a CLI command) start on a clean machine at once and download into the same folder together | Installs take a lock; the second waits and then uses the first one's browser: one download |
| BR4 | An interrupted download leaves a folder that looks installed | Only an install with Playwright's completion marker counts; a failed launch after an install reinstalls once |
| BR5 | Google Chrome is installed but won't start (damaged, blocked) | It's skipped like a missing one, and the download path takes over |
| BR6 | Rendering depends on whichever Chrome version is installed, so offline test digests change when Chrome updates | Playwright's own browser, when present (development machines, tests, earlier downloads), is always tried first |
| BR7 | The download takes a while with no sign of life, or hangs | The engine log says the renderer is downloading and records the installer's result; the job stays in its render step, and the install gives up after 10 minutes |

## Local-only SpriteGuru (written before the code, from the open-source design)

SpriteGuru is open source and entirely local: the SpritePlay Cloud account, access gate, sync,
remote library and update check are deleted, not disabled. The app contacts only the AI providers the
user has keys for, plus the one-time model and browser downloads named in the README.

| ID | Failure | Required behaviour |
| --- | --- | --- |
| OS1 | A retired cloud route still answers, or answers with a stub that invites a client to retry | `/auth/callback`, `/api/cloud/*` and `/api/project/sync*` are FastAPI's ordinary 404 |
| OS2 | The CLI still offers `cloud`, `sync` or the remote `library` commands, or a generating command still checks an account | None of them exist; generation depends only on the project, provider keys, the mode, job state and spend approvals |
| OS3 | The studio still shows an account chip, sync pill, cloud row, remote Library tab, cloud settings, plan or update banner | None of them render; Characters and Builder create and generate in synthetic mode with no account |
| OS4 | `project.json` holds machine-local fields (`asset_folder`, `active_character`) that differ per machine | They live in `.spriteguru/local.json`, ignored by git; `project.json` keeps shared settings only |
| OS5 | Work in worker threads publishes events through asyncio queues that belong to another thread, so the studio's socket never wakes | The event bus hands each event to the queue's own event loop |
| OS6 | The package, command, bundle or UI still says spritekit or SpritePlay | The import package, distribution and command are `spriteguru`; `SpriteGuru.app`, `com.spriteguru.studio`, the window title and all UI say SpriteGuru. Old names appear only in the migration below |

## Migrating SpritePlay-era local data (written before the code, from the open-source design)

Migration is local, idempotent and never contacts a server. It never modifies characters,
animations, cache content, exports, ledger entries or other authored project assets.

| ID | Failure | Required behaviour |
| --- | --- | --- |
| MG1 | Only `~/Documents/SpritePlay` exists, so the gallery opens empty | It is renamed atomically to `~/Documents/SpriteGuru`, and stored recent and hidden paths under the old prefix are rewritten |
| MG2 | Both `SpriteGuru` and `SpritePlay` roots exist, and one overwrites the other or its projects disappear | SpriteGuru stays the canonical root (new projects go there); projects in both roots are listed; neither folder is moved or overwritten |
| MG3 | The library state under its SpritePlay name (`.spriteplay-library.json`) is ignored, or cloud-only keys survive in it | It merges into `.spriteguru-library.json` under the library lock: recent and hidden lists without duplicates, SpritePlay-era values for shared preference fields, and `machine_id`, access caches, auto-sync and release-check keys dropped. `.spriteplay-cache` becomes `.spriteguru-cache` |
| MG4 | Renaming the root fails (permissions, a busy folder), so the app can't open anything | A `spriteguru:`-prefixed warning goes to stderr and the readable SpritePlay folder is the library for that run |
| MG5 | A project's `.spriteplay/local.json` (active character, asset folder) is lost, its sync baselines, conflicts and locks are copied along, or the old file keeps winning, so a character chosen in SpriteGuru reverts on the next open | Only the known local fields move to `.spriteguru/local.json`, once (SpritePlay-era values win when both files exist), and the old `local.json` is then removed; `sync/` is never copied. Both folder names stay in the project's `.gitignore` |
| MG6 | A project's `cloud` link survives in `project.json` | A writable open saves the project without it; every authored asset stays byte-identical |
| MG7 | Provider keys stored under the old keychain service `spritekit` aren't found | The first read copies each key to the `spriteguru` service and deletes only the old entry; `keys delete` removes both |
| MG8 | Obsolete cloud refresh tokens stay in the keychain | The first library migration deletes the `refresh_token` and `account` entries of `spriteplay-cloud` and `spriteguru-cloud`, with no sign-out request; provider keys are never touched |
| MG9 | Studio preferences under `spriteplay.*` browser-storage keys (theme, open animation) are lost | Each is copied to `spriteguru.*` when the canonical key is absent, then removed; existing SpriteGuru values win |
| MG10 | An old environment variable (`SPRITEKIT_LIBRARY`, `SPRITEKIT_DEV`, …) stops working | `SPRITEGURU_<NAME>` is read first and `SPRITEKIT_<NAME>` is the fallback when it is unset or empty |
