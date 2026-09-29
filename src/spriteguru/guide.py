"""Guide canvas (6.3): reference strip plus posed grey mannequins on a known grid over the key.

The guide carries no text, numbers or lines, because models copy them.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from . import choreo as choreo_lib
from .color import hex_to_rgb
from .figure import MANNEQUIN, Proportions, Props, bounds, render, skeleton
from .planner import GridPlan
from .spec import SpriteSpec

GROUND = 0.92  # ground line as a fraction of cell height
MARGIN_TOP = 0.04
MARGIN_X = 0.04
MAX_CHAR = 0.80  # standing height as a fraction of cell height, at most


@dataclass
class GuideFrame:
    index: int
    rect: tuple[int, int, int, int]
    origin: tuple[float, float]  # ground point under the hip, sheet coordinates
    box: tuple[int, int, int, int]  # mannequin bounding box, sheet coordinates
    airborne: bool
    line: str


@dataclass
class Guide:
    plan: GridPlan
    key: str
    char_h: int  # standing character height in px
    ground_y: int  # ground line inside a cell (cell coordinates)
    frames: list[GuideFrame]
    image: Image.Image  # RGB canvas
    mask: Image.Image  # RGBA; alpha 0 marks the frame cells the model may paint
    figure_mask: np.ndarray  # bool, mannequin pixels (pose conformance)
    ref_box: tuple[int, int, int, int] | None = None
    choreo_name: str = ""
    meta: dict = field(default_factory=dict)

    def png(self) -> bytes:
        return _png(self.image)

    def mask_png(self) -> bytes:
        return _png(self.mask)

    def to_dict(self) -> dict:
        return {"plan": self.plan.to_dict(), "key": self.key, "char_h": self.char_h, "ground_y": self.ground_y,
                "ref_box": self.ref_box, "choreo": self.choreo_name, "mode": (self.meta or {}).get("mode", "character"),
                "frames": [{"index": f.index, "rect": f.rect, "origin": f.origin, "box": f.box,
                            "airborne": f.airborne, "line": f.line} for f in self.frames]}

    def background_seeds(self, clearance: float = 0.06) -> np.ndarray:
        """Pixels that are background in the guide and far from any figure (matte seed samples)."""
        import cv2

        occupied = self.figure_mask.copy()
        if self.ref_box:
            x0, y0, x1, y1 = self.ref_box
            occupied[y0:y1, x0:x1] = True
        dist = cv2.distanceTransform((~occupied).astype(np.uint8), cv2.DIST_L2, 5)
        return dist > clearance * self.char_h


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def proportions(spec: SpriteSpec) -> Proportions:
    if spec.character.heads_tall:
        return Proportions(float(spec.character.heads_tall))
    return Proportions({"pixel": 4.0, "hd-cartoon": 5.0, "painted": 6.0, "vector": 5.0}[spec.style.kind])


def props_for(ch: choreo_lib.Choreo) -> Props:
    return Props(weapon="weapon" in ch.props, shield="shield" in ch.props, cape="cape" in ch.props)


def fit_height(parts_list, plan: GridPlan) -> tuple[int, float]:
    """Largest standing height (px) that fits every pose inside a cell, and the shared x shift."""
    tops, xmins, xmaxs = [], [], []
    for parts in parts_list:
        x0, y0, x1, y1 = bounds(parts)
        tops.append(y1)
        xmins.append(x0)
        xmaxs.append(x1)
    top = max(tops)
    xmin, xmax = min(xmins), max(xmaxs)
    h_by_top = (GROUND - MARGIN_TOP) * plan.cell_h / top
    h_by_w = (1 - 2 * MARGIN_X) * plan.cell_w / (xmax - xmin)
    h = int(min(h_by_top, h_by_w, MAX_CHAR * plan.cell_h))
    shift = -(xmin + xmax) / 2  # centre the union of all poses in the cell
    return h, shift


def build(spec: SpriteSpec, plan: GridPlan, key: str, reference: Image.Image | None = None,
          ch: choreo_lib.Choreo | None = None) -> Guide:
    kind = spec.character.kind
    ch = ch or choreo_lib.choreography(spec.action, spec.frames, facing=spec.facing, kind=kind)
    if ch.mode == "rigid":
        return _build_rigid(spec, plan, key, reference, ch)
    if ch.mode == "effect":
        return _build_effect(spec, plan, key, reference, ch)
    prop = proportions(spec)
    props = props_for(ch)
    parts_list = [skeleton(f.pose, prop, spec.view, spec.facing, props) for f in ch.frames]
    char_h, shift = fit_height(parts_list, plan)
    ground_y = int(round(GROUND * plan.cell_h))
    key_rgb = hex_to_rgb(key)
    canvas = Image.new("RGBA", (plan.width, plan.height), key_rgb + (255,))
    figure_mask = np.zeros((plan.height, plan.width), dtype=bool)
    frames: list[GuideFrame] = []
    flip = spec.facing == "W"
    for i, (f, parts) in enumerate(zip(ch.frames, parts_list)):
        img, (ox, oy) = render(parts, char_h, MANNEQUIN, facing=spec.facing, view=spec.view)
        x0, y0, x1, y1 = plan.cell_rect(i)
        sx = shift * char_h * (-1 if flip else 1)
        gx = x0 + plan.cell_w / 2 + sx
        gy = y0 + ground_y
        px, py = int(round(gx - ox)), int(round(gy - oy))
        canvas.alpha_composite(img, (px, py))
        a = np.asarray(img)[..., 3] > 127
        ys, xs = np.nonzero(a)
        sub = figure_mask[py:py + img.height, px:px + img.width]
        sub |= a[: sub.shape[0], : sub.shape[1]]
        box = (int(px + xs.min()), int(py + ys.min()), int(px + xs.max()) + 1, int(py + ys.max()) + 1)
        frames.append(GuideFrame(i, (x0, y0, x1, y1), (gx, gy), box, f.airborne, f.line))

    ref_box = None
    if reference is not None and plan.strip:
        ref = _fit_reference(reference, char_h)
        rx0, ry0, rx1, ry1 = plan.ref_rect
        px = int(round(rx0 + plan.cell_w / 2 - ref.width / 2))
        py = int(round(ry0 + ground_y - ref.height))
        canvas.alpha_composite(ref, (px, py))
        ref_box = (px, py, px + ref.width, py + ref.height)

    mask = Image.new("RGBA", (plan.width, plan.height), (0, 0, 0, 255))
    hole = Image.new("RGBA", (plan.cell_w, plan.cell_h), (0, 0, 0, 0))
    for i in range(plan.frames):
        x0, y0, _, _ = plan.cell_rect(i)
        mask.paste(hole, (x0, y0))
    return Guide(plan, key, char_h, ground_y, frames, canvas.convert("RGB"), mask, figure_mask, ref_box,
                 ch.name, {"shift": shift, "facing": spec.facing, "view": spec.view})


def _mask_image(plan: GridPlan) -> Image.Image:
    mask = Image.new("RGBA", (plan.width, plan.height), (0, 0, 0, 255))
    hole = Image.new("RGBA", (plan.cell_w, plan.cell_h), (0, 0, 0, 0))
    for i in range(plan.frames):
        x0, y0, _, _ = plan.cell_rect(i)
        mask.paste(hole, (x0, y0))
    return mask


def _place_reference(canvas: Image.Image, plan: GridPlan, reference: Image.Image | None, height: int,
                     ground_y: int, centered: bool = False):
    if reference is None or not plan.strip:
        return None
    ref = _fit_reference(reference, height, max_w=int(0.92 * plan.cell_w))
    rx0, ry0, rx1, ry1 = plan.ref_rect
    px = int(round(rx0 + plan.cell_w / 2 - ref.width / 2))
    py = int(round((ry0 + plan.cell_h / 2 - ref.height / 2) if centered else (ry0 + ground_y - ref.height)))
    canvas.alpha_composite(ref, (px, max(0, py)))
    return (px, py, px + ref.width, py + ref.height)


# ---------------------------------------------------------------------------
# Rigid subjects (vehicles, machines): the object's own silhouette, transformed per frame


def _grey_version(ref: Image.Image) -> Image.Image:
    """A grey stand-in that keeps the object's shape and internal structure: luminance compressed into
    a mid-grey band, so the model reads it as a figure to replace and still sees the parts."""
    arr = np.asarray(ref.convert("RGBA")).astype(np.float32)
    lum = arr[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)
    g = 110 + (lum / 255.0) * 90
    out = np.dstack([g, g, g, arr[..., 3]]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _rigid_render(sil: Image.Image, height: int, pose: dict, facing: str) -> tuple[Image.Image, tuple[float, float]]:
    """Scale the silhouette to `height`, then apply the pose about its ground contact (bottom centre).
    Returns the image and the ground point in it."""
    s = height / sil.height
    base = sil.resize((max(1, int(round(sil.width * s * pose.get("sx", 1.0)))),
                       max(1, int(round(height * pose.get("sy", 1.0))))), Image.Resampling.LANCZOS)
    pad = int(0.3 * max(base.size))
    big = Image.new("RGBA", (base.width + 2 * pad, base.height + 2 * pad), (0, 0, 0, 0))
    big.paste(base, (pad, pad))
    pivot = (pad + base.width / 2, pad + base.height)
    rot = pose.get("rot", 0.0) * (1 if facing != "W" else -1)
    big = big.rotate(rot, resample=Image.Resampling.BICUBIC, center=pivot)
    sign = -1 if facing == "W" else 1
    dx = sign * pose.get("dx", 0.0) * height
    dy = -pose.get("dy", 0.0) * height
    return big, (pivot[0] - dx, pivot[1] - dy)


def _build_rigid(spec: SpriteSpec, plan: GridPlan, key: str, reference: Image.Image | None,
                 ch: choreo_lib.Choreo) -> Guide:
    if reference is not None:
        ref = reference.convert("RGBA")
        a = np.asarray(ref)[..., 3]
        ys, xs = np.nonzero(a > 16)
        if len(ys):
            ref = ref.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    else:  # no approved view yet: a plain rounded block stands in
        ref = Image.new("RGBA", (320, 200), (0, 0, 0, 0))
        from PIL import ImageDraw

        ImageDraw.Draw(ref).rounded_rectangle([0, 0, 319, 199], radius=30, fill=(160, 160, 160, 255))
    sil = _grey_version(ref)
    aspect = ref.width / ref.height
    max_dx = max(abs(f.pose.get("dx", 0.0)) for f in ch.frames)
    max_up = max(max(0.0, f.pose.get("dy", 0.0)) for f in ch.frames)
    rot = max(abs(f.pose.get("rot", 0.0)) for f in ch.frames)
    h_by_top = (GROUND - MARGIN_TOP) * plan.cell_h / (1 + max_up + 0.02 * rot)
    h_by_w = (1 - 2 * MARGIN_X) * plan.cell_w / (aspect * (1 + 2 * max_dx / max(aspect, 1e-6)) + 0.03 * rot)
    height = int(min(h_by_top, h_by_w, 0.72 * plan.cell_h))
    ground_y = int(round(GROUND * plan.cell_h))
    canvas = Image.new("RGBA", (plan.width, plan.height), hex_to_rgb(key) + (255,))
    figure_mask = np.zeros((plan.height, plan.width), dtype=bool)
    frames: list[GuideFrame] = []
    for i, f in enumerate(ch.frames):
        img, (ox, oy) = _rigid_render(sil, height, f.pose, spec.facing)
        x0, y0, x1, y1 = plan.cell_rect(i)
        gx, gy = x0 + plan.cell_w / 2, y0 + ground_y
        px, py = int(round(gx - ox)), int(round(gy - oy))
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.alpha_composite(img, (max(0, px), max(0, py)), (max(0, -px), max(0, -py)))
        cell = layer.crop((x0, y0, x1, y1))  # never spill into a neighbouring cell
        canvas.alpha_composite(cell, (x0, y0))
        a = np.asarray(cell)[..., 3] > 127
        figure_mask[y0:y1, x0:x1] |= a
        ys, xs = np.nonzero(a)
        box = (x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1) if len(xs) \
            else (x0, y0, x1, y1)
        frames.append(GuideFrame(i, (x0, y0, x1, y1), (gx, gy), box, False, f.line))
    ref_box = _place_reference(canvas, plan, reference, height, ground_y)
    return Guide(plan, key, height, ground_y, frames, canvas.convert("RGB"), _mask_image(plan), figure_mask,
                 ref_box, ch.name, {"shift": 0.0, "facing": spec.facing, "view": spec.view, "mode": "rigid",
                                    "expected": [(f.pose.get("dx", 0.0), f.pose.get("dy", 0.0)) for f in ch.frames]})


# ---------------------------------------------------------------------------
# Effects: soft grey shapes, on black for additive effects


def effect_size(plan: GridPlan) -> int:
    return int(min(0.8 * plan.cell_h, 0.9 * plan.cell_w))


def _draw_shapes(draw, shapes: list[dict], cx: float, cy: float, unit: float, mirror: bool, fill,
                 min_px: float = 1.0) -> None:
    """`min_px`: the thinnest stroke and smallest spark diameter; pixel style passes ~1.6 logical
    pixels so rings and sparks survive the lattice (K13)."""
    import math

    def P(x, y):
        return (cx + (-x if mirror else x) * unit, cy - y * unit)

    for sh in shapes:
        t = sh["type"]
        if t == "orb":
            x, y = P(sh["x"], sh["y"])
            r = sh["r"] * unit
            draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)
        elif t == "tail":
            w = sh["w"] * unit
            (xa, ya), (xb, yb) = P(sh["x0"], sh["y"]), P(sh["x1"], sh["y"])
            draw.polygon([(xa, ya - 0.1 * w), (xb, yb - w / 2), (xb, yb + w / 2), (xa, ya + 0.1 * w)], fill=fill)
        elif t == "ring":
            x, y = P(sh["x"], sh["y"])
            r, th = sh["r"] * unit, max(min_px, sh["t"] * unit)
            draw.ellipse([x - r, y - r, x + r, y + r], outline=fill, width=int(round(th)))
        elif t == "burst":
            x, y = P(sh["x"], sh["y"])
            n, r = int(sh.get("n", 8)), sh["r"] * unit
            pts = []
            for k in range(2 * n):
                ang = math.pi * k / n
                rr = r if k % 2 == 0 else 0.45 * r
                pts.append((x + rr * math.cos(ang), y + rr * math.sin(ang)))
            draw.polygon(pts, fill=fill)
        elif t == "sparks":
            x, y = P(sh["x"], sh["y"])
            n, r, sz = int(sh.get("n", 8)), sh["r"] * unit, max(min_px / 2, sh.get("size", 0.03) * unit)
            for k in range(n):
                ang = 2 * math.pi * k / n + 0.3
                sx, sy = x + r * math.cos(ang), y + r * math.sin(ang)
                draw.ellipse([sx - sz, sy - sz, sx + sz, sy + sz], fill=fill)


def effect_background(spec: SpriteSpec, key: str) -> tuple[int, int, int]:
    return (0, 0, 0) if spec.character.blend == "add" else hex_to_rgb(key)


def _build_effect(spec: SpriteSpec, plan: GridPlan, key: str, reference: Image.Image | None,
                  ch: choreo_lib.Choreo) -> Guide:
    from PIL import ImageDraw

    size = effect_size(plan)
    bg = effect_background(spec, key)
    canvas = Image.new("RGBA", (plan.width, plan.height), bg + (255,))
    figure_mask = np.zeros((plan.height, plan.width), dtype=bool)
    frames: list[GuideFrame] = []
    ss = 4
    logical = size / (spec.style.pixel_height or 48) if spec.style.kind == "pixel" else 0.0
    for i, f in enumerate(ch.frames):
        x0, y0, x1, y1 = plan.cell_rect(i)
        layer = Image.new("RGBA", (plan.cell_w * ss, plan.cell_h * ss), (0, 0, 0, 0))
        _draw_shapes(ImageDraw.Draw(layer), f.shapes or [], plan.cell_w * ss / 2, plan.cell_h * ss / 2, size * ss,
                     spec.facing == "W", (200, 200, 200, 255), min_px=max(1.0, 1.6 * logical) * ss)
        layer = layer.resize((plan.cell_w, plan.cell_h), Image.Resampling.BOX)
        canvas.alpha_composite(layer, (x0, y0))
        a = np.asarray(layer)[..., 3] > 127
        figure_mask[y0:y1, x0:x1] |= a
        ys, xs = np.nonzero(a)
        box = (x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1) if len(xs) \
            else (x0, y0, x1, y1)
        frames.append(GuideFrame(i, (x0, y0, x1, y1), (x0 + plan.cell_w / 2, y0 + plan.cell_h / 2), box, False, f.line))
    ref_box = _place_reference(canvas, plan, reference, int(0.8 * size), plan.cell_h // 2, centered=True)
    return Guide(plan, key if spec.character.blend != "add" else "#000000", size, plan.cell_h // 2, frames,
                 canvas.convert("RGB"), _mask_image(plan), figure_mask, ref_box, ch.name,
                 {"shift": 0.0, "facing": spec.facing, "view": spec.view, "mode": "effect",
                  "expected": [(0.0, 0.0)] * len(ch.frames)})


def _fit_reference(ref: Image.Image, char_h: int, max_w: int | None = None) -> Image.Image:
    """Crop the reference to its alpha box and scale it to the standing height, narrowed to `max_w`
    when it would spill out of its strip (wide effects and vehicles)."""
    ref = ref.convert("RGBA")
    a = np.asarray(ref)[..., 3]
    ys, xs = np.nonzero(a > 16)
    if len(ys):
        ref = ref.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    scale = char_h / ref.height
    if max_w and ref.width * scale > max_w:
        scale = max_w / ref.width
    return ref.resize((max(1, int(round(ref.width * scale))), max(1, int(round(ref.height * scale)))),
                      Image.Resampling.LANCZOS)
