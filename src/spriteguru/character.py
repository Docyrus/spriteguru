"""Character lock (6.4): generate a turnaround once, approve it, reuse it for every animation.

Subjects can be characters, vehicles, machines or effects, and can start from text, from an
uploaded reference image (which seeds the turnaround edit), or use that image directly as the view.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import prompts, registry
from .color import palette as extract_palette
from .pipeline import layout as layout_mod
from .pipeline import matte as matte_mod
from .project import Project, write_json
from .providers.base import ProviderRequest
from .providers.hub import ProviderHub
from .spec import CharacterRecord, Style

VIEWS = ("front", "side-e", "side-w", "back")
TURNAROUND_SIZE = (2048, 768)
EFFECT_SIZE = (1024, 1024)
SOURCE_MAX = 1536
MAX_UPLOAD = 20 * 1024 * 1024

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "facing": {"type": "string", "enum": ["left", "right", "front"]},
        "mirrorable": {"type": "boolean"},
        "additive": {"type": "boolean"},
    },
    "required": ["description", "facing", "mirrorable", "additive"],
    "additionalProperties": False,
}


class ImageError(ValueError):
    pass


def normalize_image(data: bytes) -> Image.Image:
    """Decode any upload to RGBA (first frame of animations), long edge capped at 1536 (I2, I4, I8)."""
    if len(data) > MAX_UPLOAD:
        raise ImageError(f"image is larger than {MAX_UPLOAD // (1024 * 1024)} MB")
    try:
        img = Image.open(io.BytesIO(data))
        img.seek(0)
        img = img.convert("RGBA")
    except Exception as e:
        raise ImageError("not a readable image (PNG, JPEG, WebP or GIF expected)") from e
    if max(img.size) > SOURCE_MAX:
        s = SOURCE_MAX / max(img.size)
        img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.Resampling.LANCZOS)
    return img


def provider_image(img: Image.Image) -> bytes:
    """The reference as the edit's input image: tiny pixel art upscaled with nearest neighbour (I2)."""
    if max(img.size) < 512:
        k = int(np.ceil(512 / max(img.size)))
        img = img.resize((img.width * k, img.height * k), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class Turnaround:
    record: CharacterRecord
    sheet: Path
    views: dict[str, Path]
    cost: float


def new_record(project: Project, name: str, description: str, style: Style | None = None,
               mirrorable: bool = True, *, kind: str = "character", blend: str | None = None,
               image: bytes | None = None, overwrite: bool = False) -> CharacterRecord:
    if not overwrite and (project.character_dir(name) / "character.json").is_file():
        raise ValueError(f"a subject named {name!r} already exists; choose another name or delete it first")
    rec = CharacterRecord(name=name, description=(description or "").strip(), style=style or project.config.style,
                          mirrorable=mirrorable, kind=kind,
                          blend=blend or ("add" if kind == "effect" else "normal"))
    if image is not None:
        img = normalize_image(image)
        d = project.character_dir(rec.name) / "ref"
        d.mkdir(parents=True, exist_ok=True)
        img.save(d / "source.png")
        rec.source_image = project.rel(d / "source.png")
    if not rec.description:
        if image is None:
            raise ValueError("a subject needs a description or a reference image")
        rec.description_source = "image"
    project.save_character(rec)
    return rec


async def describe_from_image(project: Project, hub: ProviderHub, name: str, *, job: str | None = None,
                              keep_mirrorable: bool = False) -> CharacterRecord:
    """Visual-facts description, facing and blend from the reference image (GPT-6 Luna, vision)."""
    rec = project.character(name)
    if not rec.source_image:
        return rec
    p = prompts.describe(rec.kind)
    model = registry.model_for("judge")
    img = normalize_image(project.abs(rec.source_image).read_bytes())
    req = ProviderRequest(op="respond", model=model, prompt=p.text, images=[provider_image(img)],
                          schema=DESCRIBE_SCHEMA, params={"reasoning_effort": "low", "schema_name": "describe",
                                                          "kind": rec.kind}, purpose="describe")
    try:
        data = json.loads((await hub.call(req, job=job or f"describe:{rec.name}"))[0].data)
    except Exception:  # I5: offline or refused; keep going with a neutral description
        data = {"description": f"the {rec.kind} shown in the reference image", "facing": "right",
                "mirrorable": rec.mirrorable, "additive": rec.blend == "add"}
    if rec.description_source == "image" or not rec.description:
        rec.description = data["description"].strip()
    rec.facing_in_source = data.get("facing", "unknown")
    if not keep_mirrorable:
        rec.mirrorable = bool(data.get("mirrorable", rec.mirrorable))
    if rec.kind == "effect":
        rec.blend = "add" if data.get("additive", True) else "normal"
    project.save_character(rec)
    return rec


def measure_views(sheet_rgba: np.ndarray, n: int = 4, touching: list[int] | None = None,
                  untint_smoke: bool = False) -> tuple[list[np.ndarray], matte_mod.Matte]:
    """Crop the views from a turnaround sheet (magenta key, one row), or the single effect view.
    Indices of views that touched a neighbour (and were cut apart by a seam) go into `touching`."""
    m = matte_mod.key_matte(sheet_rgba, untint_smoke=untint_smoke)  # M16: no magenta tint in the view
    if n == 1:
        return [_largest_crop(m)], m
    lay = layout_mod.detect(m.mask, layout_mod.Prior(n=n, rows=1, cols=n))
    if touching is not None:
        touching.extend(f.index for f in lay.frames if f.touching or f.clipped)
    crops = []
    for f in lay.frames:
        x0, y0, x1, y1 = f.box
        pad = 2
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(sheet_rgba.shape[1], x1 + pad), min(sheet_rgba.shape[0], y1 + pad)
        c = m.rgba[y0:y1, x0:x1].copy()
        c[~(lay.labels[y0:y1, x0:x1] == f.label)] = 0
        crops.append(c)
    return crops, m


def _largest_crop(m: matte_mod.Matte, pad: int = 4) -> np.ndarray:
    """The subject: the largest connected region (plus parts close to it); effects keep all glow."""
    from scipy import ndimage

    mask = m.mask
    if m.path == "luma":
        ys, xs = np.nonzero(m.rgba[..., 3] > 8)
    else:
        lbl, n = ndimage.label(ndimage.binary_dilation(mask, iterations=6))
        if n == 0:
            return m.rgba
        sizes = ndimage.sum(mask, lbl, np.arange(1, n + 1))
        keep = lbl == (int(np.argmax(sizes)) + 1)
        ys, xs = np.nonzero(keep & (m.rgba[..., 3] > 0))
    if len(ys) == 0:
        return m.rgba
    H, W = mask.shape
    y0, y1 = max(0, ys.min() - pad), min(H, ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(W, xs.max() + 1 + pad)
    return m.rgba[y0:y1, x0:x1].copy()


def heads_tall(mask: np.ndarray) -> float | None:
    rows = mask.sum(1)
    ys = np.nonzero(rows)[0]
    if len(ys) < 10:
        return None
    top, bot = ys.min(), ys.max()
    h = bot - top + 1
    upper = rows[top: top + int(0.4 * h)]
    if len(upper) < 5:
        return None
    # the neck is the narrowest row below the widest head row
    head_w_row = int(np.argmax(upper[: max(2, len(upper) // 2)]))
    below = upper[head_w_row:]
    neck = head_w_row + int(np.argmin(below)) if len(below) else len(upper) // 2
    head_h = max(1, neck)
    return round(float(h / head_h), 2)


async def generate_turnaround(project: Project, hub: ProviderHub, name: str, *, n: int = 1,
                              job: str | None = None, seed: int | None = None) -> Turnaround:
    rec = project.character(name)
    job = job or f"turnaround:{rec.name}"
    if rec.source_image and (rec.description_source == "image" or rec.facing_in_source == "unknown"):
        rec = await describe_from_image(project, hub, name, job=job, keep_mirrorable=True)
    from_image = bool(rec.source_image)
    prompt = prompts.turnaround(rec.description, rec.style, kind=rec.kind, from_image=from_image, blend=rec.blend)
    model = registry.image_model("turnaround", project.config.settings.image_models)
    params = dict(registry.model(model)["params"])
    params.pop("n", None)
    size = EFFECT_SIZE if rec.kind == "effect" else TURNAROUND_SIZE
    if from_image:
        img = normalize_image(project.abs(rec.source_image).read_bytes())
        req = ProviderRequest(op="edit", model=model, prompt=prompt.text, params=params, n=n, size=size, seed=seed,
                              images=[provider_image(img)], purpose="turnaround")
    else:
        req = ProviderRequest(op="generate", model=model, prompt=prompt.text, params=params, n=n, size=size,
                              seed=seed, purpose="turnaround")
    cands = await hub.call(req, job=job)
    d = project.character_dir(rec.name) / "ref"
    d.mkdir(parents=True, exist_ok=True)
    rec.turnaround_candidates = []
    cost = 0.0
    for i, c in enumerate(cands):
        p = d / f"turnaround-c{i + 1}.png"
        p.write_bytes(c.data)
        meta = {**prompt.provenance(), "model": model, "params": params, "size": size, "from_image": from_image,
                "provider": c.meta.get("provider"), "request_id": c.meta.get("request_id"),
                "cost": c.meta.get("cost", 0.0), "cached": c.meta.get("cached", False)}
        (d / f"turnaround-c{i + 1}.meta.json").write_text(json.dumps(meta, indent=2))
        rec.turnaround_candidates.append(project.rel(p))
        cost += float(c.meta.get("cost", 0.0))
    project.save_character(rec)
    t = choose_turnaround(project, rec.name, 0)
    return Turnaround(t, project.abs(t.turnaround), {k: project.abs(v) for k, v in t.views.items()}, cost)


def use_image_as_view(project: Project, name: str) -> CharacterRecord:
    """Skip the turnaround: the uploaded image (matted, largest subject, facing right) becomes the side
    view; mirrorable subjects get side-w by flipping. Without front and back views, top-down
    animations are unavailable for this subject."""
    rec = project.character(name)
    if not rec.source_image:
        raise ValueError(f"{name!r} has no reference image")
    rgba = matte_mod.ingest(project.abs(rec.source_image))
    m = matte_mod.key_matte(rgba, pixel=rec.style.kind == "pixel")
    notes = []
    if m.path == "key" and m.signals.get("uniformity", 1.0) < 0.6 and matte_mod.ml_available():
        try:
            m = matte_mod.ml_matte(rgba, [(0, 0, rgba.shape[1], rgba.shape[0])], key=m)  # I1
            notes.append("busy background: ML matte")
        except Exception as e:
            notes.append(f"ML matte unavailable: {e}")
    crop = _largest_crop(m)
    d = project.character_dir(rec.name) / "ref"
    if rec.facing_in_source == "left" and rec.kind != "effect":
        crop = np.ascontiguousarray(crop[:, ::-1])  # I7: side-e always faces right
        notes.append("flipped a left-facing image")
    rec.view_warnings = []
    rec.views = {}
    if rec.kind == "effect":
        Image.fromarray(crop, "RGBA").save(d / "key.png")
        rec.views["key"] = project.rel(d / "key.png")
    else:
        Image.fromarray(crop, "RGBA").save(d / "side-e.png")
        rec.views["side-e"] = project.rel(d / "side-e.png")
        if rec.mirrorable:
            Image.fromarray(np.ascontiguousarray(crop[:, ::-1]), "RGBA").save(d / "side-w.png")
            rec.views["side-w"] = project.rel(d / "side-w.png")
    rec.turnaround = rec.source_image
    _measure(rec, crop)
    rec.approved = False
    project.save_character(rec)
    write_json(d / "measure.json", {"source": "image", "notes": notes, "signals": m.signals})
    return rec


def _measure(rec: CharacterRecord, view: np.ndarray) -> None:
    mask = view[..., 3] >= 128
    pal = extract_palette(view[..., :3], mask, k=min(16, max(4, rec.style.palette_size)))
    rec.palette = [h for h, cov in pal if cov > 0.01]
    rows = np.nonzero(mask.any(1))[0]
    rec.height_px = int(rows.max() - rows.min() + 1) if len(rows) else None
    rec.heads_tall = heads_tall(mask) if rec.kind == "character" else None


def choose_turnaround(project: Project, name: str, index: int) -> CharacterRecord:
    """Crop views from turnaround candidate `index`, measure palette and proportions."""
    rec = project.character(name)
    if not 0 <= index < len(rec.turnaround_candidates):
        raise ValueError(f"turnaround candidate {index} does not exist ({len(rec.turnaround_candidates)} available)")
    src = project.abs(rec.turnaround_candidates[index])
    d = project.character_dir(rec.name) / "ref"
    sheet = matte_mod.ingest(src)
    touching: list[int] = []
    crops, m = measure_views(sheet, n=1 if rec.kind == "effect" else 4, touching=touching,
                             untint_smoke=matte_mod.expects_translucency(rec.description, rec.kind, rec.blend))
    rec.view_warnings = []
    if len(crops) == 4 and touching:
        # T9: a view that touched its neighbour was cut along a seam and may be clipped. A mirrorable
        # subject's side views are mirror images, so a clean one replaces a cut one.
        names = [VIEWS[i] for i in sorted(set(touching))]
        if rec.mirrorable and (1 in touching) != (2 in touching):
            good = 2 if 1 in touching else 1
            crops[3 - good] = np.ascontiguousarray(crops[good][:, ::-1])
            names = [v for v in names if v not in ("side-e", "side-w")]
            rec.view_warnings.append(f"{VIEWS[3 - good]} was cut apart from a neighbour; mirrored {VIEWS[good]} instead")
        if names:
            rec.view_warnings.append(f"views touching on the turnaround: {', '.join(names)}; they may be clipped, "
                                     "reroll the turnaround or pick another candidate")
    target = d / "turnaround.png"
    Image.open(src).save(target)
    rec.turnaround = project.rel(target)
    rec.turnaround_index = index
    rec.views = {}
    for view, crop in zip(("key",) if rec.kind == "effect" else VIEWS, crops):
        p = d / f"{view}.png"
        Image.fromarray(crop, "RGBA").save(p)
        rec.views[view] = project.rel(p)
    side = crops[1] if len(crops) > 1 else (crops[0] if crops else None)
    if side is not None:
        _measure(rec, side)
    rec.approved = False
    project.save_character(rec)
    write_json(d / "measure.json", {"views_found": len(crops), "key": m.key_hex, "touching": sorted(set(touching)),
                                    "warnings": rec.view_warnings, "signals": m.signals})
    return rec


def approve(project: Project, name: str) -> CharacterRecord:
    rec = project.character(name)
    if not rec.views:
        raise ValueError(f"character {name!r} has no turnaround views to approve")
    rec.approved = True
    project.save_character(rec)
    return rec


def reference_view(project: Project, rec: CharacterRecord, facing: str) -> np.ndarray | None:
    """The approved view that seeds a job: side views for E/W, back for N, front for S (6.4); an
    effect's single design view for every facing."""
    view = "key" if rec.kind == "effect" else {"E": "side-e", "W": "side-w", "N": "back", "S": "front"}[facing]
    if view not in rec.views:
        return None
    return np.asarray(Image.open(project.abs(rec.views[view])).convert("RGBA"))


def png_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA" if arr.shape[-1] == 4 else "RGB").save(buf, format="PNG")
    return buf.getvalue()
