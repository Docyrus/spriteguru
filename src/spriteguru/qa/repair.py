"""Repair planning and execution (14.4): the cheapest fix first.

Deterministic fixes already happened inside the analyzer (re-key, marks, flips, reorder, scale,
baseline, palette, retiming). Guided sheets then get one masked edit per frame on GPT Image 2.5
sunburst; video and Retro Diffusion jobs repair by re-roll; vector jobs through fix rounds.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .. import prompts, registry
from ..color import hex_to_rgb, key_name
from ..figure import MANNEQUIN, render, skeleton
from ..providers.base import ProviderRequest
from ..spec import Finding
from .score import ACCEPT_Q, finding_cost

MAX_REPAIRS, MAX_REROLLS, MAX_FIX_ROUNDS = 4, 1, 2
GLOBAL_FAILS = {"frame_count", "loop_seam", "gait", "background", "key_contamination"}
FRAME_FAILS = {"clipping", "pose", "identity", "facing", "scale", "semantics", "frames_touching", "duplicates",
               "sharpness", "effect_size"}


@dataclass
class Attempts:
    repairs: int = 0
    rerolls: int = 0
    fix_rounds: int = 0
    inbetweens: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Step:
    action: str  # accept, repair, inbetween, reroll, best_effort
    frames: list[int] = field(default_factory=list)
    finding: Finding | None = None
    reason: str = ""
    key_exclude: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {"action": self.action, "frames": self.frames, "reason": self.reason,
                "finding": self.finding.model_dump() if self.finding else None,
                "key_exclude": sorted(self.key_exclude)}


def _blocking(f: Finding) -> bool:
    return f.level == "fail" and not f.auto_fixed and (f.source == "cv" or f.corroborated or f.severity >= 3)


def plan_next(findings: list[Finding], *, score: float, accepted: bool, route: str, n_frames: int,
              attempts: Attempts, key: str | None = None) -> Step:
    if accepted:
        return Step("accept", reason=f"Q {score:.0f} with no blocking findings")
    blocking = [f for f in findings if _blocking(f)]
    guided = route in ("guided", "guided-pixel")
    frame_scoped = [f for f in findings if f.metric in FRAME_FAILS and f.frames and
                    (f.level == "fail" or (f.metric in ("duplicates", "sharpness") and f.level == "warn"))
                    and not f.auto_fixed and (f.source == "cv" or f.corroborated or f.severity >= 3)]
    bad_frames = sorted({i for f in frame_scoped for i in f.frames})
    # a whole-animation identity failure (Q2: recoloured throughout) cannot be fixed frame by frame
    global_hits = [f for f in blocking if f.metric in GLOBAL_FAILS or (f.metric == "identity" and not f.frames)]
    too_many = n_frames and len(bad_frames) > 0.3 * n_frames
    if global_hits or (too_many and guided):
        if attempts.rerolls < MAX_REROLLS:
            excl = {key} if key and any(f.metric == "key_contamination" for f in global_hits) else set()
            why = global_hits[0].message if global_hits else f"{len(bad_frames)} of {n_frames} frames need repair"
            return Step("reroll", reason=why, key_exclude=excl)
        return Step("best_effort", reason="re-roll budget spent")
    if route.startswith("vector"):
        return Step("best_effort", reason="vector jobs repair inside their fix rounds")
    if frame_scoped and guided:
        if attempts.repairs < MAX_REPAIRS:
            worst = max(frame_scoped, key=finding_cost)
            return Step("repair", frames=[worst.frames[0]], finding=worst, reason=worst.message)
        return Step("best_effort", reason="frame repair budget spent")
    if frame_scoped and attempts.rerolls < MAX_REROLLS:
        return Step("reroll", reason=f"{route} jobs repair by re-roll: {frame_scoped[0].message}")
    gap = next((f for f in findings if f.metric in ("gaps", "loop_seam") and f.level == "warn"), None)
    if gap is not None and guided and attempts.inbetweens < 1 and attempts.repairs < MAX_REPAIRS:
        return Step("inbetween", frames=gap.frames or [n_frames - 1], finding=gap, reason=gap.message)
    if score < ACCEPT_Q and not blocking and attempts.rerolls < MAX_REROLLS and not guided:
        return Step("reroll", reason=f"Q {score:.0f} below {ACCEPT_Q:.0f}")
    return Step("best_effort", reason=f"Q {score:.0f}; nothing cheaper left to try")


# ---------------------------------------------------------------------------
# Execution: masked frame repair on guided sheets


def fix_text(f: Finding, facing: str) -> tuple[str, dict]:
    """Template name and variables for a finding (7.7)."""
    m = f.metric
    if m == "pose" or m == "duplicates":
        return "repair_pose", {}
    if m == "identity" or (m == "semantics" and f.issue in ("missing_item", "changed_item", "color_drift",
                                                               "style_drift")):
        detail = f.message.split("—", 1)[-1].strip() if m == "semantics" else "outfit, colours and accessories"
        return "repair_identity", {"detail": detail[:120]}
    if m == "facing":
        want = "right" if facing == "E" else "left"
        other = "left" if want == "right" else "right"
        return "repair_frame", {"fix": f"the character faces {other}; make it face {want}"}
    if m == "clipping":
        return "repair_frame", {"fix": "the character is cut off at the edge of its area; draw the full body from "
                                       "head to feet inside the area with empty space around it"}
    if m == "scale":
        return "repair_frame", {"fix": "the character is drawn at a different size; draw it at the same height "
                                       "as the other frames"}
    if m == "sharpness":
        return "repair_frame", {"fix": "the character is blurry; redraw it with the same crisp edges as the "
                                       "other frames"}
    if m == "semantics":
        return "repair_frame", {"fix": f.message.split("—", 1)[-1].strip()[:160]}
    return "repair_frame", {"fix": f.message[:160]}


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def repair_canvas(sheet: Image.Image, guide, idx: int, key: str, grow: int = 8) -> tuple[Image.Image, Image.Image,
                                                                                      tuple]:
    """The current sheet with the target cell re-guided (key + mannequin), and a mask exposing
    the dilated cell."""
    x0, y0, x1, y1 = guide.frames[idx].rect
    canvas = sheet.convert("RGB").copy()
    canvas.paste(guide.image.crop((x0, y0, x1, y1)), (x0, y0))
    W, H = canvas.size
    gx0, gy0, gx1, gy1 = max(0, x0 - grow), max(0, y0 - grow), min(W, x1 + grow), min(H, y1 + grow)
    mask = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    mask.paste(Image.new("RGBA", (gx1 - gx0, gy1 - gy0), (0, 0, 0, 0)), (gx0, gy0))
    return canvas, mask, (x0, y0, x1, y1)


def _subject_words(compiled) -> dict:
    kind = compiled.spec.character.kind
    return {"subject": kind, "shape": "shape" if kind == "effect" else ("figure" if kind == "character" else kind)}


def _bg_name(key: str) -> str:
    return "black" if key.upper() == "#000000" else key_name(key)


def _fix_sentence(finding: Finding, compiled, idx: int) -> str:
    template, extra = fix_text(finding, compiled.spec.facing)
    if template == "repair_pose":
        return f"the frame must show: {compiled.choreo.frames[idx].line}"
    line = compiled.choreo.frames[idx].line if 0 <= idx < len(compiled.choreo.frames) else ""
    if template == "repair_identity":
        return f"the {extra['detail']} must match the reference exactly" + (f"; the frame shows: {line}" if line else "")
    # K16: without the frame's phase a "fixed" fading frame came back as a full burst
    return extra.get("fix", finding.message) + (f"; the frame shows: {line}" if line else "")


MIN_PIXELS = 655_360  # the image API rejects smaller sizes


def quad_canvas(ref: Image.Image, top_right: Image.Image, target: Image.Image, bottom_right: Image.Image,
                key: str) -> tuple[Image.Image, Image.Image, tuple, float]:
    """2x2 guided canvas: [reference | neighbour] over [target mannequin | neighbour]. A row of four
    cells breaks the API's 3:1 aspect limit; small cells are scaled up to its minimum size."""
    cw, ch = target.size
    canvas = Image.new("RGB", (2 * cw, 2 * ch), hex_to_rgb(key))
    canvas.paste(ref, (0, 0))
    canvas.paste(top_right, (cw, 0))
    canvas.paste(target, (0, ch))
    canvas.paste(bottom_right, (cw, ch))
    s = 1.0
    if canvas.width * canvas.height < MIN_PIXELS:
        s = (MIN_PIXELS / (canvas.width * canvas.height)) ** 0.5
    W = int(np.ceil(canvas.width * s / 16) * 16)
    H = int(np.ceil(canvas.height * s / 16) * 16)
    s = W / canvas.width
    if (W, H) != canvas.size:
        canvas = canvas.resize((W, H), Image.Resampling.LANCZOS)
    tx0, ty0, tx1, ty1 = 0, int(round(ch * s)), int(round(cw * s)), H
    mask = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    mask.paste(Image.new("RGBA", (tx1 - tx0, ty1 - ty0), (0, 0, 0, 0)), (tx0, ty0))
    return canvas, mask, (tx0, ty0, tx1, ty1), s


def cell_canvas(sheet: Image.Image, compiled, idx: int):
    g = compiled.guide
    n = len(g.frames)
    return quad_canvas(g.image.crop(g.plan.ref_rect), sheet.crop(g.frames[(idx - 1) % n].rect),
                       g.image.crop(g.frames[idx].rect), sheet.crop(g.frames[(idx + 1) % n].rect), compiled.key)


async def _cell_edit(hub, compiled, canvas, mask, crop, prompt_text, *, purpose, job, seed):
    """One guided cell edit; a masked cell that comes back empty (seen live) is retried without the mask."""
    from ..generate import reference_on_key

    model = registry.model_for(purpose)
    params = {k: v for k, v in registry.model(model)["params"].items() if k != "n"}
    images = [_png(canvas)]
    if compiled.reference is not None:
        images.append(reference_on_key(compiled.reference, compiled.key, (512, 512)))
    retried, out, cands = False, None, None
    for use_mask in (True, False):
        req = ProviderRequest(op="edit", model=model, prompt=prompt_text, params=params, images=images,
                              mask=_png(mask) if use_mask else None, n=1, size=canvas.size, seed=seed,
                              purpose=purpose)
        cands = await hub.call(req, job=job)
        out = Image.open(io.BytesIO(cands[0].data)).convert("RGB")
        if out.size != canvas.size:
            out = out.resize(canvas.size, Image.Resampling.LANCZOS)
        if not degenerate(out, crop):
            break
        retried = True
    return out, cands[0].meta, model, retried


def clear_remnants(merged: Image.Image, rect: tuple, old_box: tuple | None, key: str) -> int:
    """The old frame may have overhung its cell; after the cell is replaced, what is left of it
    outside the cell (touching the cell border, inside the old frame's box) is painted over with the
    key so it cannot become a stray part of a neighbour. Returns the pixels cleared."""
    if not old_box:
        return 0
    from scipy import ndimage

    from ..pipeline.matte import key_matte

    arr = np.asarray(merged).copy()
    m = key_matte(arr).mask
    x0, y0, x1, y1 = rect
    outside = m.copy()
    outside[y0:y1, x0:x1] = False
    lbl, n = ndimage.label(outside)
    if not n:
        return 0
    ring = np.zeros_like(m)
    ring[max(0, y0 - 1):y1 + 1, max(0, x0 - 1):x1 + 1] = True
    ring[y0:y1, x0:x1] = False
    bx0, by0, bx1, by1 = old_box
    inbox = np.zeros_like(m)
    inbox[by0:by1, bx0:bx1] = True
    cleared = 0
    k = np.array(hex_to_rgb(key), np.uint8)
    for i in range(1, n + 1):
        comp = lbl == i
        if (comp & ring).any() and (comp & inbox).sum() >= 0.9 * comp.sum():
            grown = ndimage.binary_dilation(comp, iterations=2) & ~(m & ~comp)  # soft edge, not neighbours
            arr[grown & inbox] = k
            cleared += int(comp.sum())
    merged.paste(Image.fromarray(arr, "RGB"))
    return cleared


async def repair_frame(hub, compiled, sheet_png: bytes, idx: int, finding: Finding, *, job=None, seed=0,
                       old_box: tuple | None = None) -> tuple[bytes, dict]:
    """Single-frame repair (14.4) on GPT Image 2.5 sunburst through a compact guided canvas whose only
    grey figure is the frame to redraw. Only the target cell of the sheet changes."""
    sheet = Image.open(io.BytesIO(sheet_png)).convert("RGB")
    canvas, mask, crop, s = cell_canvas(sheet, compiled, idx)
    rect = compiled.guide.frames[idx].rect
    p = prompts.render("repair_cell", character=compiled.spec.character.description.rstrip("."),
                       fix=_fix_sentence(finding, compiled, idx), key_name=_bg_name(compiled.key),
                       key_hex=compiled.key, **_subject_words(compiled))
    out, meta, model, retried = await _cell_edit(hub, compiled, canvas, mask, crop, p.text, purpose="repair",
                                                 job=job, seed=seed)
    cell = out.crop(crop).resize((rect[2] - rect[0], rect[3] - rect[1]), Image.Resampling.LANCZOS)
    merged = sheet.copy()
    merged.paste(cell, rect[:2])
    cleared = clear_remnants(merged, rect, old_box, compiled.key)
    return _png(merged), {**p.provenance(), "model": model, "frame": idx, "finding": finding.metric,
                          "cost": meta.get("cost", 0.0), "cached": meta.get("cached", False),
                          "retried_without_mask": retried, "remnant_px_cleared": cleared}


def degenerate(img: Image.Image, rect: tuple[int, int, int, int]) -> bool:
    """A masked region the model left empty: mostly near-black pixels."""
    a = np.asarray(img.crop(rect)).astype(np.int32)
    return bool((a.sum(-1) < 60).mean() > 0.4)


async def inbetween(hub, compiled, sheet_png: bytes, a: int, b: int, *, job=None, seed=0) -> tuple[np.ndarray, dict]:
    """Generative in-between (13.6): both neighbours beside an interpolated mannequin."""
    from ..choreo import _lerp_pose
    from ..guide import GROUND, props_for, proportions
    from ..pipeline.matte import key_matte

    g = compiled.guide
    sheet = Image.open(io.BytesIO(sheet_png)).convert("RGB")
    cw, ch = g.plan.cell_w, g.plan.cell_h
    target = Image.new("RGBA", (cw, ch), hex_to_rgb(compiled.key) + (255,))
    mode = compiled.choreo.mode
    fa, fb = compiled.choreo.frames[a], compiled.choreo.frames[b]
    if mode == "skeleton":
        pose = _lerp_pose(fa.pose, fb.pose, 0.5)
        parts = skeleton(pose, proportions(compiled.spec), compiled.spec.view, compiled.spec.facing,
                         props_for(compiled.choreo))
        img, (ox, oy) = render(parts, g.char_h, MANNEQUIN, facing=compiled.spec.facing, view=compiled.spec.view)
        shift = g.meta.get("shift", 0.0) * g.char_h * (-1 if compiled.spec.facing == "W" else 1)
        target.alpha_composite(img, (int(round(cw / 2 + shift - ox)), int(round(GROUND * ch - oy))))
    elif mode == "rigid":
        from ..choreo import _lerp_rigid
        from ..guide import _grey_version, _rigid_render

        ref = Image.fromarray(compiled.reference, "RGBA") if compiled.reference is not None else None
        if ref is not None:
            sil = _grey_version(ref.crop(ref.getbbox()))
            img, (ox, oy) = _rigid_render(sil, g.char_h, _lerp_rigid(fa.pose, fb.pose, 0.5), compiled.spec.facing)
            target.alpha_composite(img, (int(round(cw / 2 - ox)), int(round(GROUND * ch - oy))))
    else:
        from PIL import ImageDraw

        from ..choreo import _lerp_shapes
        from ..guide import _draw_shapes, effect_size

        layer = Image.new("RGBA", (cw * 4, ch * 4), (0, 0, 0, 0))
        _draw_shapes(ImageDraw.Draw(layer), _lerp_shapes(fa.shapes or [], fb.shapes or [], 0.5), cw * 2, ch * 2,
                     effect_size(g.plan) * 4, compiled.spec.facing == "W", (200, 200, 200, 255))
        target.alpha_composite(layer.resize((cw, ch), Image.Resampling.BOX))
    canvas, mask, crop, s = quad_canvas(g.image.crop(g.plan.ref_rect), sheet.crop(g.frames[a].rect),
                                        target.convert("RGB"), sheet.crop(g.frames[b].rect), compiled.key)
    fix = (f"this is the in-between halfway from the top-right frame "
           f"({compiled.choreo.frames[a].line.split(':')[0].lower()}) to the bottom-right frame "
           f"({compiled.choreo.frames[b].line.split(':')[0].lower()}); follow the grey figure's pose")
    p = prompts.render("repair_cell", character=compiled.spec.character.description.rstrip("."), fix=fix,
                       key_name=_bg_name(compiled.key), key_hex=compiled.key, **_subject_words(compiled))
    out, meta, model, retried = await _cell_edit(hub, compiled, canvas, mask, crop, p.text, purpose="inbetween",
                                                 job=job, seed=seed)
    cell = np.asarray(out.crop(crop).resize((cw, ch), Image.Resampling.LANCZOS))
    m = key_matte(cell)
    return m.rgba, {**p.provenance(), "model": model, "between": [a, b], "cost": meta.get("cost", 0.0),
                    "retried_without_mask": retried}
