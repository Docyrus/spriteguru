"""Generation routes (6.5): compile a SpriteSpec into model calls and candidates.

guided / guided-pixel  guided canvas edit on GPT Image 2.5 flare (best-of-2)
rd-loop                Retro Diffusion advanced animation from a pixel start frame
video-loop             MiniMax H3 Max image-to-video, cut into a loop (13.7)
vector-idle            Quiver micro-animation sampled in Chromium (6.8)
vector-motion          GPT-6 Sol authored motion spec, evaluated deterministically (6.9)
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import choreo as choreo_lib
from . import guide as guide_mod
from . import keys, planner, prompts, registry
from .character import png_bytes, reference_view
from .color import KEYS, hex_to_rgb
from .pipeline.analyze import Analysis, GuideInfo, analyze
from .project import Project
from .providers.base import ProviderRequest
from .providers.hub import ProviderHub
from .spec import CharacterRecord, Character, Finding, SpriteSpec


@dataclass
class Compiled:
    spec: SpriteSpec  # the spec actually generated (W mirrorable jobs generate E)
    route: str
    key: str
    key_distance: float
    choreo: choreo_lib.Choreo
    reference: np.ndarray | None
    plan: planner.GridPlan | None = None
    guide: guide_mod.Guide | None = None
    prompt: prompts.Prompt | None = None
    flip_output: bool = False
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"route": self.route, "key": self.key, "key_distance": round(self.key_distance, 4),
                "facing_generated": self.spec.facing, "flip_output": self.flip_output,
                "plan": self.plan.to_dict() if self.plan else None, "choreo": self.choreo.name,
                "prompt": self.prompt.provenance() if self.prompt else None, "notes": self.notes}


def spec_for_character(rec: CharacterRecord, action: str, *, facing: str = "E", frames: int | None = None,
                       loop: bool | None = None, view: str = "side", fps: int = 12, engine: str = "godot",
                       motion: str = "in-place") -> SpriteSpec:
    kind = rec.kind
    return SpriteSpec(
        character=Character(description=rec.description, refs=list(rec.views.values()), palette=rec.palette,
                            heads_tall=rec.heads_tall, mirrorable=rec.mirrorable, kind=kind, blend=rec.blend),
        view=view, facing=facing, action=action, frames=frames or choreo_lib.default_frames(action, kind),
        loop=choreo_lib.default_loop(action, kind) if loop is None else loop, motion=motion, style=rec.style,
        fps=fps, engine=engine)


def choose_key(spec: SpriteSpec, exclude: set[str] | None = None) -> tuple[str, float]:
    """Chroma key (6.6): the candidate farthest from every palette colour; `exclude` skips keys
    that already failed (a contamination re-roll moves to the next key)."""
    if spec.key and not (exclude and spec.key.upper() in exclude):
        return spec.key.upper(), _key_distance(spec.key.upper(), spec.character.palette)
    options = [(hx, _key_distance(hx, spec.character.palette)) for hx in KEYS.values()
               if not exclude or hx not in exclude]
    if not options:
        return KEYS["green"], 0.0
    return max(options, key=lambda t: t[1])


def _key_distance(hx: str, palette: list[str]) -> float:
    from .color import to_oklab

    kl = to_oklab(np.array(hex_to_rgb(hx), np.uint8))
    ds = [float(np.linalg.norm(kl - to_oklab(np.array(hex_to_rgb(h), np.uint8)))) for h in palette]
    return min(ds) if ds else 1.0


def compile_spec(project: Project, spec: SpriteSpec, rec: CharacterRecord, *, key_exclude: set[str] | None = None,
                 mode: str = "live") -> Compiled:
    notes = []
    route = registry.route_for(spec)
    if route == "rd-loop" and mode == "live" and not keys.get("retrodiffusion"):
        route = "guided-pixel"
        notes.append("no Retro Diffusion key: pixel loop generated as a guided sheet instead")
    if route == "guided" and spec.style.kind == "pixel":
        route = "guided-pixel"
    flip = False
    gen_spec = spec
    kind = spec.character.kind
    if spec.view == "side" and spec.facing == "W" and spec.character.mirrorable:
        gen_spec = spec.model_copy(update={"facing": "E"})
        flip = True
        notes.append(f"mirrorable {kind}: west-facing frames are the flipped east-facing job")
    if kind != "character" and spec.view == "top-down" and kind != "effect" and "front" not in rec.views:
        raise ValueError(f"top-down {kind} animations need front and back views; generate a turnaround")
    ch = choreo_lib.choreography(gen_spec.action, gen_spec.frames, facing=gen_spec.facing, kind=kind,
                                 description=gen_spec.character.description)
    if kind == "effect" and spec.character.blend == "add":
        key, dist = "#000000", 1.0  # additive effects are generated on black and matted by luminance (K4)
    else:
        key, dist = choose_key(gen_spec, key_exclude)
    if dist < 0.15:
        notes.append(f"best key {key} is only {dist:.2f} from the palette (want 0.15)")
    ref = reference_view(project, rec, gen_spec.facing)
    c = Compiled(gen_spec, route, key, dist, ch, ref, flip_output=flip, notes=notes)
    if route in ("guided", "guided-pixel"):
        aspect = None
        if kind in ("vehicle", "machine") and ref is not None:
            ys, xs = np.nonzero(ref[..., 3] > 16)
            if len(ys):
                aspect = 1.1 * (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
        c.plan = planner.plan(gen_spec, aspect=aspect)
        c.guide = guide_mod.build(gen_spec, c.plan, key, Image.fromarray(ref) if ref is not None else None, ch)
        c.prompt = prompts.guided_sheet(gen_spec, c.plan, key, ch)
    elif route == "video-loop":
        c.prompt = prompts.video(gen_spec, key)
    return c


def reference_on_key(ref: np.ndarray, key: str, size: tuple[int, int] | None = None) -> bytes:
    """The approved view composited on the key, as the edit's second image."""
    k = hex_to_rgb(key)
    img = Image.fromarray(ref, "RGBA")
    if size:
        canvas = Image.new("RGBA", size, k + (255,))
        s = min(size[0] * 0.8 / img.width, size[1] * 0.8 / img.height)
        img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.Resampling.LANCZOS)
        canvas.alpha_composite(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    else:
        canvas = Image.new("RGBA", img.size, k + (255,))
        canvas.alpha_composite(img)
    return png_bytes(np.asarray(canvas.convert("RGB")))


@dataclass
class CandidateOut:
    id: str
    image: bytes | None  # the sheet (raster routes)
    media: str
    meta: dict
    analysis: Analysis | None = None
    frames: list[np.ndarray] | None = None  # frame routes (vector, video)
    extra: dict = field(default_factory=dict)


async def generate(hub: ProviderHub, c: Compiled, *, job: str | None = None, seed: int = 0,
                   n: int | None = None, rec: CharacterRecord | None = None,
                   project: Project | None = None) -> list[CandidateOut]:
    if c.route in ("guided", "guided-pixel"):
        return await _guided(hub, c, job=job, seed=seed, n=n)
    if c.route == "rd-loop":
        return await _rd_loop(hub, c, job=job, seed=seed)
    if c.route == "video-loop":
        return await _video_loop(hub, c, job=job, seed=seed)
    if c.route in ("vector-idle", "vector-motion", "vector-anim"):
        from .pipeline import vector

        return await vector.generate(hub, c, project=project, rec=rec, job=job, seed=seed)
    raise ValueError(f"unknown route {c.route}")


async def _guided(hub: ProviderHub, c: Compiled, *, job, seed, n) -> list[CandidateOut]:
    model = registry.model_for("guided_sheet")
    params = dict(registry.model(model)["params"])
    count = n or int(params.pop("n", 2))
    params.pop("n", None)
    images = [c.guide.png()]
    if c.reference is not None:
        images.append(reference_on_key(c.reference, c.key, (512, 512)))
    req = ProviderRequest(op="edit", model=model, prompt=c.prompt.text, params=params, images=images,
                          mask=c.guide.mask_png(), n=count, size=(c.plan.width, c.plan.height), seed=seed,
                          purpose="guided_sheet")
    cands = await hub.call(req, job=job)
    return [CandidateOut(f"c{i + 1}", cd.data, "image/png", {**cd.meta, **c.prompt.provenance(), "model": model,
                                                               "params": params, "size": [c.plan.width, c.plan.height],
                                                               "seed": seed})
            for i, cd in enumerate(cands)]


def analyze_candidate(c: Compiled, out: CandidateOut) -> Analysis:
    if out.frames is not None:
        from .pipeline.analyze import analyze_frames

        return analyze_frames(out.frames, c.spec, reference=c.reference, extra=out.extra)
    if c.route in ("guided", "guided-pixel"):
        return analyze(out.image, c.spec, guide=GuideInfo.from_guide(c.guide), reference=c.reference)
    if c.route == "rd-loop":
        cols, rows = out.meta.get("layout", [c.spec.frames, 1])
        return analyze(out.image, c.spec, grid=(cols, rows), reference=c.reference)
    return analyze(out.image, c.spec, reference=c.reference)


# ---------------------------------------------------------------------------
# Retro Diffusion loops


RD_STYLES = {"walk": "rd_advanced_animation__walking", "idle": "rd_advanced_animation__idle",
             "jump": "rd_advanced_animation__jump", "crouch": "rd_advanced_animation__crouch",
             "attack-melee": "rd_advanced_animation__attack"}


def pixel_start_frame(ref: np.ndarray, pixel_height: int) -> np.ndarray:
    """True-resolution start frame: the side view through pixel reconstruction (12)."""
    from .pipeline import pixel as pixel_mod
    from .pipeline.register import Crop
    from .spec import Style

    rows = np.nonzero((ref[..., 3] >= 128).any(1))[0]
    h = rows.max() - rows.min() + 1 if len(rows) else ref.shape[0]
    if h <= pixel_height * 1.2:
        return ref
    spec = SpriteSpec(character=Character(description="x"), action="idle", frames=2, loop=True,
                      style=Style(kind="pixel", pixel_height=pixel_height))
    out, _, _ = pixel_mod.reconstruct([Crop(ref, (0, 0), (0, 0, ref.shape[1], ref.shape[0]))], spec)
    return out[0].rgba


async def _rd_loop(hub: ProviderHub, c: Compiled, *, job, seed) -> list[CandidateOut]:
    ph = c.spec.style.pixel_height or 48
    start = pixel_start_frame(c.reference, ph) if c.reference is not None else None
    if start is None:
        raise ValueError("pixel loops need an approved side view")
    h, w = start.shape[:2]
    side = int(np.clip(np.ceil(max(h, w) * 1.33 / 8) * 8, 32, 256))
    canvas = np.zeros((side, side, 4), np.uint8)
    y0, x0 = (side - h) // 2, (side - w) // 2
    canvas[y0:y0 + h, x0:x0 + w] = start
    style = RD_STYLES.get(c.spec.action, "rd_advanced_animation__custom_action")
    frames = min((4, 6, 8, 10, 12, 16), key=lambda v: abs(v - c.spec.frames))
    params = {"prompt_style": style, "frames_duration": frames, "return_spritesheet": True}
    if c.spec.character.palette:
        pal = np.array([hex_to_rgb(x) for x in c.spec.character.palette[:32]], np.uint8)[None]
        params["input_palette"] = base64.b64encode(png_bytes(pal)).decode()
    prompt = {"walk": "steady walking pace", "idle": "gentle breathing, subtle movement"}.get(
        c.spec.action, c.spec.action.replace("-", " "))
    req = ProviderRequest(op="rd_animate", model=registry.model_for("pixel_loop"), prompt=prompt, params=params,
                          images=[png_bytes(canvas)], size=(side, side), seed=seed, purpose="pixel_loop")
    cands = await hub.call(req, job=job)
    out = []
    for i, cd in enumerate(cands):
        img = Image.open(io.BytesIO(cd.data))
        cols = max(1, img.width // side)
        rows = max(1, img.height // side)
        out.append(CandidateOut(f"c{i + 1}", cd.data, "image/png",
                                {**cd.meta, "prompt": prompt, "params": {k: v for k, v in params.items()
                                                                         if k != "input_palette"},
                                 "layout": [cols, rows], "frame_size": side}))
    return out


# ---------------------------------------------------------------------------
# Video loops (13.7)


def matte_expects(spec) -> bool:
    from .pipeline.matte import expects_translucency

    ch = spec.character
    return expects_translucency(ch.description, ch.kind, ch.blend)


async def _video_loop(hub: ProviderHub, c: Compiled, *, job, seed) -> list[CandidateOut]:
    from .pipeline import video

    if c.reference is None:
        raise ValueError("video loops need an approved view")
    size = 1024
    k = hex_to_rgb(c.key)
    canvas = Image.new("RGBA", (size, size), k + (255,))
    ref = Image.fromarray(c.reference, "RGBA")
    s = 0.6 * size / ref.height
    ref = ref.resize((max(1, int(ref.width * s)), int(0.6 * size)), Image.Resampling.LANCZOS)
    canvas.alpha_composite(ref, ((size - ref.width) // 2, (size - ref.height) // 2))
    start = png_bytes(np.asarray(canvas.convert("RGB")))
    model = registry.model_for("hd_loop")
    params = dict(registry.model(model)["params"])
    req = ProviderRequest(op="video", model=model, prompt=c.prompt.text, params=params, images=[start, start],
                          seed=seed, purpose="hd_loop")
    cands = await hub.call(req, job=job)
    out = []
    for i, cd in enumerate(cands):
        frames, info = video.video_to_frames(cd.data, c.spec.frames, key=c.key, loop=c.spec.loop,
                                             action=c.spec.action, palette=c.spec.character.palette or None,
                                             untint_smoke=matte_expects(c.spec))
        vf = [Finding.model_validate(f) for f in info.pop("findings", [])]
        out.append(CandidateOut(f"c{i + 1}", None, "video/mp4", {**cd.meta, **c.prompt.provenance(),
                                                                 "params": params}, frames=frames,
                                extra={"video": info, "findings": vf, "video_bytes": cd.data}))
    return out
