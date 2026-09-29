"""Synthetic sheet composer: coloured figures on imperfect chroma backgrounds, with ground truth.

Used by the golden fixtures (known boxes, anchors and order) and by the offline simulator
provider, which mimics the defects AI image models produce: drifting "flat" backgrounds, noise,
soft edges with key spill, placement jitter, stray marks, grid lines and labels.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .color import hex_to_rgb
from .figure import Proportions, Props, Skin, render, skeleton


@dataclass
class Degrade:
    gradient: float = 0.0  # RGB units of linear drift across the sheet
    vignette: float = 0.0  # fraction of darkening in the corners
    noise: float = 0.0  # Gaussian sigma, RGB units
    blur: float = 0.0  # Gaussian radius on the whole sheet (soft AI edges)
    spill: float = 0.0  # tint of edge pixels toward the key (0..1)
    jitter: float = 0.0  # placement jitter, fraction of character height
    scale_jitter: float = 0.0  # per-frame scale jitter (fraction)
    seed: int = 0


@dataclass
class Placed:
    index: int
    origin: tuple[float, float]  # ground point under the hip, sheet coordinates
    torso: tuple[float, float]  # torso mid point, sheet coordinates
    box: tuple[int, int, int, int]
    airborne: bool
    scale: float = 1.0
    flipped: bool = False


@dataclass
class Sheet:
    image: Image.Image  # RGB (or RGBA for alpha outputs)
    placed: list[Placed]
    masks: list[np.ndarray]  # per-frame bool masks, sheet coordinates (ground truth)
    meta: dict = field(default_factory=dict)

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.image.save(buf, format="PNG")
        return buf.getvalue()


def background(size: tuple[int, int], key: str, d: Degrade, rng: np.random.Generator) -> np.ndarray:
    W, H = size
    base = np.array(hex_to_rgb(key), np.float32)
    img = np.broadcast_to(base, (H, W, 3)).copy()
    if d.gradient:
        ang = rng.uniform(0, 2 * math.pi)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        t = ((xx / W - 0.5) * math.cos(ang) + (yy / H - 0.5) * math.sin(ang))
        drift = rng.uniform(-1, 1, 3).astype(np.float32)
        drift /= np.linalg.norm(drift) + 1e-6
        img += t[..., None] * 2 * d.gradient * drift
    if d.vignette:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
        img *= (1 - d.vignette * r ** 2)[..., None]
    return img


def finish(img: np.ndarray, d: Degrade, rng: np.random.Generator, fg_alpha: np.ndarray | None = None,
           key: str | None = None) -> np.ndarray:
    out = img.astype(np.float32)
    if d.spill and fg_alpha is not None and key is not None:
        edge = (fg_alpha > 0.02) & (fg_alpha < 0.98)
        k = np.array(hex_to_rgb(key), np.float32)
        out[edge] = out[edge] * (1 - d.spill) + k * d.spill
    if d.noise:
        out += rng.normal(0, d.noise, out.shape).astype(np.float32)
    out = np.clip(out, 0, 255).astype(np.uint8)
    if d.blur:
        out = np.asarray(Image.fromarray(out).filter(ImageFilter.GaussianBlur(d.blur)))
    return out


def figure(pose: dict, prop: Proportions, props: Props, char_h: float, skin: Skin, *, view: str = "side",
           facing: str = "E") -> tuple[Image.Image, tuple[float, float], tuple[float, float]]:
    parts = skeleton(pose, prop, view, facing, props)
    img, origin = render(parts, char_h, skin, facing=facing, view=view)
    torso = next(p for p in parts if p.name == "torso")
    mid = (torso.points[0] + torso.points[1]) / 2
    sign = -1 if facing == "W" else 1
    torso_off = (sign * mid[0] * char_h, -mid[1] * char_h)  # relative to origin, image axes
    return img, origin, torso_off


def compose(size: tuple[int, int], key: str, items: list[dict], d: Degrade, *, alpha_out: bool = False) -> Sheet:
    """items: dicts with image, origin (in image), target (sheet ground point), torso_off, index, airborne."""
    rng = np.random.default_rng(d.seed)
    W, H = size
    bg = background(size, key, d, rng)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    placed, masks = [], []
    for it in items:
        img: Image.Image = it["image"]
        s = 1.0 + (rng.uniform(-d.scale_jitter, d.scale_jitter) if d.scale_jitter else 0.0) + it.get("scale", 0.0)
        ox, oy = it["origin"]
        if abs(s - 1.0) > 1e-3:
            img = img.resize((max(1, int(round(img.width * s))), max(1, int(round(img.height * s)))),
                             Image.Resampling.LANCZOS)
            ox, oy = ox * s, oy * s
        jx = rng.uniform(-d.jitter, d.jitter) * it.get("char_h", 100) if d.jitter else 0.0
        jy = rng.uniform(-d.jitter, d.jitter) * it.get("char_h", 100) if d.jitter else 0.0
        tx, ty = it["target"]
        tx, ty = tx + jx + it.get("dx", 0.0), ty + jy + it.get("dy", 0.0)
        px, py = int(round(tx - ox)), int(round(ty - oy))
        single = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        single.alpha_composite(img, (max(0, px), max(0, py)), (max(0, -px), max(0, -py)))
        layer.alpha_composite(single)
        a = np.asarray(single)[..., 3] > 127
        ys, xs = np.nonzero(a)
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else (0, 0, 0, 0)
        tox, toy = it.get("torso_off", (0.0, 0.0))
        placed.append(Placed(it["index"], (px + ox, py + oy), (px + ox + tox * s, py + oy + toy * s), box,
                             it.get("airborne", False), s, it.get("flipped", False)))
        masks.append(a)
    la = np.asarray(layer).astype(np.float32)
    alpha = la[..., 3:4] / 255.0
    if alpha_out:
        out = np.asarray(layer).copy()
        return Sheet(Image.fromarray(out, "RGBA"), placed, masks, {"key": None})
    comp = bg * (1 - alpha) + la[..., :3] * alpha
    final = finish(comp, d, rng, alpha[..., 0], key)
    return Sheet(Image.fromarray(final, "RGB"), placed, masks, {"key": key})


def draw_grid_lines(img: Image.Image, xs: list[int], ys: list[int], color=(30, 30, 30), width: int = 3) -> None:
    draw = ImageDraw.Draw(img)
    for x in xs:
        draw.line([(x, 0), (x, img.height)], fill=color, width=width)
    for y in ys:
        draw.line([(0, y), (img.width, y)], fill=color, width=width)


def draw_labels(img: Image.Image, spots: list[tuple[int, int]], size: int = 22, color=(20, 20, 20)) -> None:
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:
        font = ImageFont.load_default()
    for i, (x, y) in enumerate(spots):
        draw.text((x, y), f"FRAME {i + 1}", fill=color, font=font)


def draw_strays(img: Image.Image, rng: np.random.Generator, count: int = 3, r: int = 3, color=(40, 40, 40)) -> None:
    draw = ImageDraw.Draw(img)
    W, H = img.size
    for _ in range(count):
        cx = int(rng.choice([rng.uniform(0.005, 0.03), rng.uniform(0.97, 0.995)]) * W)
        cy = int(rng.choice([rng.uniform(0.005, 0.04), rng.uniform(0.96, 0.995)]) * H)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def draw_shadow(img: Image.Image, key: str, x: float, y: float, w: float, h: float, darken: float = 0.5) -> None:
    k = hex_to_rgb(key)
    col = tuple(int(c * darken) for c in k)
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(ov).ellipse([x - w / 2, y - h / 2, x + w / 2, y + h / 2], fill=col + (255,))
    base = img.convert("RGBA")
    # shadows sit under the character: only paint on background-coloured pixels
    arr = np.asarray(base).copy()
    o = np.asarray(ov)
    d = np.abs(arr[..., :3].astype(int) - np.array(k)).sum(-1) < 90
    sel = (o[..., 3] > 0) & d
    arr[sel, :3] = o[sel, :3]
    img.paste(Image.fromarray(arr[..., :3]))
