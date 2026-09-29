"""Simulator drawings for non-character subjects: vehicles, machines and visual effects.

Deterministic stand-ins for what an image model returns, so every route for every subject kind can
run offline: turnarounds (four views on magenta), effect design sheets, guided sheets painted over
the guide's grey stand-ins, and colours read back from an uploaded reference image.
"""

from __future__ import annotations

import io
import re
import zlib

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

COLOR_WORDS = {
    "olive": (104, 116, 64), "green": (84, 128, 70), "sand": (192, 164, 112), "desert": (192, 164, 112),
    "tan": (186, 150, 104), "grey": (128, 134, 140), "gray": (128, 134, 140), "steel": (140, 148, 158),
    "blue": (62, 96, 162), "red": (170, 56, 44), "yellow": (212, 176, 48), "orange": (220, 126, 40),
    "purple": (130, 76, 170), "white": (220, 222, 226), "black": (52, 54, 60), "brown": (122, 86, 56),
}
GLOW_WORDS = {"blue": (80, 170, 255), "cyan": (70, 230, 255), "red": (255, 70, 40), "fire": (255, 130, 30),
              "orange": (255, 140, 40), "green": (90, 255, 120), "purple": (190, 100, 255),
              "yellow": (255, 230, 90), "pink": (255, 110, 200), "white": (230, 240, 255)}


def subject_kind(prompt: str) -> str:
    p = prompt.lower()
    if "visual effect" in p or "grey shape" in p or "effect (identical" in p:
        return "effect"
    if "2d game vehicle" in p or "of the vehicle" in p or "grey vehicle" in p or "vehicle (identical" in p:
        return "vehicle"
    if "2d game machine" in p or "of the machine" in p or "grey machine" in p or "machine (identical" in p:
        return "machine"
    return "character"


def color_from_text(text: str, table: dict, seed: int, default=None):
    t = text.lower()
    hits = [(t.find(w), c) for w, c in table.items() if re.search(rf"\b{w}\b", t)]
    if hits:
        return min(hits)[1]
    if default is not None:
        return default
    vals = list(table.values())
    return vals[seed % len(vals)]


def dominant_color(img: Image.Image) -> tuple[int, int, int]:
    """Most common saturated-enough colour of an uploaded reference (ignoring near-uniform borders)."""
    a = np.asarray(img.convert("RGBA"))
    px = a[a[..., 3] > 127][:, :3].astype(int)
    if len(px) == 0:
        return (128, 128, 128)
    border = np.concatenate([a[0, :, :3], a[-1, :, :3], a[:, 0, :3], a[:, -1, :3]]).astype(int)
    bg = np.median(border, 0)
    px = px[np.abs(px - bg).sum(1) > 60] if len(px) > 50 else px
    if len(px) == 0:
        return tuple(int(v) for v in bg)
    chroma = px.max(1) - px.min(1)
    if (chroma > 40).sum() > 0.1 * len(px):  # outlines and shading are grey; the design colour is not
        px = px[chroma > 40]
    q = (px // 24) * 24 + 12
    vals, counts = np.unique(q, axis=0, return_counts=True)
    return tuple(int(v) for v in vals[int(np.argmax(counts))])


def _shade(c, k: float):
    return tuple(int(np.clip(v * k, 0, 255)) for v in c)


# ---------------------------------------------------------------------------
# Object drawings


def draw_vehicle(view: str, size: int, color) -> Image.Image:
    """A tank. view: side (facing right), front or back. Returns RGBA of height `size`."""
    H = size
    W = int(size * (1.9 if view == "side" else 1.05))
    ss = 4
    img = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    S = lambda *v: [x * ss for x in v]  # noqa: E731
    dark, darker, light = _shade(color, 0.7), (46, 46, 50), _shade(color, 1.2)
    ol = (26, 26, 30, 255)
    if view == "side":
        d.rounded_rectangle(S(0.04 * W, 0.66 * H, 0.96 * W, 0.98 * H), radius=int(0.14 * H * ss), fill=darker + (255,),
                            outline=ol, width=2 * ss)
        for k in range(6):
            cx = (0.14 + k * 0.144) * W
            d.ellipse(S(cx - 0.07 * H, 0.75 * H, cx + 0.07 * H, 0.89 * H), fill=(90, 92, 96, 255), outline=ol, width=ss)
        d.polygon(S(0.06 * W, 0.66 * H, 0.10 * W, 0.46 * H, 0.80 * W, 0.46 * H, 0.95 * W, 0.62 * H, 0.95 * W, 0.66 * H),
                  fill=color + (255,), outline=ol)
        d.rounded_rectangle(S(0.30 * W, 0.26 * H, 0.66 * W, 0.48 * H), radius=int(0.08 * H * ss), fill=light + (255,),
                            outline=ol, width=2 * ss)
        d.rectangle(S(0.64 * W, 0.32 * H, 0.99 * W, 0.38 * H), fill=dark + (255,), outline=ol, width=ss)
        # a muzzle brake with two ports the background shows through (M14)
        d.rectangle(S(0.89 * W, 0.295 * H, 0.99 * W, 0.405 * H), fill=dark + (255,), outline=ol, width=ss)
        for hx in (0.905, 0.945):
            d.rectangle(S(hx * W, 0.325 * H, (hx + 0.025) * W, 0.375 * H), fill=(0, 0, 0, 0))
        d.ellipse(S(0.40 * W, 0.18 * H, 0.46 * W, 0.28 * H), fill=dark + (255,), outline=ol, width=ss)
    else:
        d.rectangle(S(0.02 * W, 0.48 * H, 0.26 * W, 0.98 * H), fill=darker + (255,), outline=ol, width=2 * ss)
        d.rectangle(S(0.74 * W, 0.48 * H, 0.98 * W, 0.98 * H), fill=darker + (255,), outline=ol, width=2 * ss)
        d.rectangle(S(0.18 * W, 0.46 * H, 0.82 * W, 0.86 * H), fill=color + (255,), outline=ol, width=2 * ss)
        d.rounded_rectangle(S(0.28 * W, 0.24 * H, 0.72 * W, 0.50 * H), radius=int(0.08 * H * ss), fill=light + (255,),
                            outline=ol, width=2 * ss)
        if view == "front":
            d.ellipse(S(0.44 * W, 0.31 * H, 0.56 * W, 0.43 * H), fill=(30, 30, 34, 255), outline=ol, width=ss)
        else:
            d.rectangle(S(0.40 * W, 0.62 * H, 0.60 * W, 0.70 * H), fill=dark + (255,), outline=ol, width=ss)
    return img.resize((W, H), Image.Resampling.BOX)


SMOKE = (192, 160, 192)  # grey seen through magenta: smoke a model draws over the magenta reference (M16)


def smoke_puffs(img: Image.Image, box) -> None:
    """Three overlapping lilac smoke puffs over the top of an object's box, in place (tintsmoke)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = x0 + 0.3 * w, y0 + 0.14 * h
    for dx, dy, r in ((0.0, 0.0, 0.08), (0.06, -0.06, 0.1), (0.13, -0.1, 0.07)):
        rr = r * h
        d.ellipse([cx + dx * w - rr, cy + dy * h - rr, cx + dx * w + rr, cy + dy * h + rr], fill=SMOKE + (230,))
    img.alpha_composite(layer)


def shade_holes(obj: Image.Image, bg) -> Image.Image:
    """Image models paint the background seen through a gap as shaded key, not the flat key: fill
    the object's enclosed transparent pixels with a slightly darker background colour (M14)."""
    arr = np.asarray(obj.convert("RGBA")).copy()
    solid = arr[..., 3] > 0
    holes = ndimage.binary_fill_holes(solid) & ~solid
    if holes.any():
        arr[holes, :3] = (np.asarray(bg[:3], np.float32) * 0.88).astype(np.uint8)
        arr[holes, 3] = 255
    return Image.fromarray(arr, "RGBA")


def draw_machine(view: str, size: int, color) -> Image.Image:
    H = size
    W = int(size * (1.1 if view == "side" else 0.9))
    ss = 4
    img = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    S = lambda *v: [x * ss for x in v]  # noqa: E731
    ol = (26, 26, 30, 255)
    dark, light = _shade(color, 0.7), _shade(color, 1.25)
    d.rectangle(S(0.06 * W, 0.30 * H, 0.94 * W, 0.98 * H), fill=color + (255,), outline=ol, width=2 * ss)
    d.rectangle(S(0.18 * W, 0.10 * H, 0.34 * W, 0.32 * H), fill=dark + (255,), outline=ol, width=2 * ss)  # chimney
    d.rectangle(S(0.58 * W, 0.18 * H, 0.70 * W, 0.32 * H), fill=light + (255,), outline=ol, width=ss)  # piston
    cx, cy, r = 0.62 * W, 0.62 * H, 0.18 * H
    teeth = []
    for k in range(20):
        ang = np.pi * k / 10
        rr = r if k % 2 == 0 else 0.8 * r
        teeth.append(((cx + rr * np.cos(ang)) * ss, (cy + rr * np.sin(ang)) * ss))
    d.polygon(teeth, fill=(170, 170, 176, 255), outline=ol)
    d.ellipse(S(cx - 0.05 * H, cy - 0.05 * H, cx + 0.05 * H, cy + 0.05 * H), fill=(60, 60, 66, 255))
    d.ellipse(S(0.18 * W, 0.44 * H, 0.28 * W, 0.54 * H), fill=(240, 70, 60, 255), outline=ol, width=ss)  # light
    d.rectangle(S(0.14 * W, 0.66 * H, 0.40 * W, 0.90 * H), fill=dark + (255,), outline=ol, width=ss)  # panel
    return img.resize((W, H), Image.Resampling.BOX)


def draw_effect(size: int, glow) -> Image.Image:
    """An energy ball with a tail, glowing, as RGBA (alpha from intensity)."""
    W, H = int(size * 1.4), size
    shape = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(shape)
    cx, cy, r = 0.62 * W, 0.5 * H, 0.3 * H
    d.polygon([(0.05 * W, cy - 0.04 * H), (cx, cy - 0.34 * H), (cx, cy + 0.34 * H), (0.05 * W, cy + 0.04 * H)], fill=150)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    return glow_image(np.asarray(shape).astype(np.float32) / 255.0, glow, size)


def glow_image(intensity: np.ndarray, glow, size: float) -> Image.Image:
    """Bloom: a coloured halo from the blurred shape and a near-white core; alpha from luminance."""
    halo = np.maximum(cv2.GaussianBlur(intensity, (0, 0), max(1.0, 0.06 * size)), 0.6 * intensity)
    core = cv2.GaussianBlur(np.clip(intensity - 0.55, 0, 1) / 0.45, (0, 0), max(1.0, 0.015 * size))
    col = np.array(glow, np.float32)[None, None] * np.clip(halo * 1.4, 0, 1)[..., None]
    col = col + (255 - col) * np.clip(core, 0, 1)[..., None] * 0.85
    a = np.clip(np.maximum(halo * 1.4, core), 0, 1)
    rgb = np.where(a[..., None] > 1e-3, col / np.maximum(a, 1e-3)[..., None], 0)
    return Image.fromarray(np.dstack([np.clip(rgb, 0, 255), a * 255]).astype(np.uint8), "RGBA")


def object_views(kind: str, color, W: int, H: int, bg=(255, 0, 255), rng=None, smoke: bool = False) -> Image.Image:
    canvas = Image.new("RGBA", (W, H), bg + (255,))
    size = int(0.5 * H)
    draw = draw_vehicle if kind == "vehicle" else draw_machine
    side = draw("side", size, color)
    if side.width > 0.85 * W / 4:  # long subjects: shrink every view so the four never touch
        size = int(size * 0.85 * W / 4 / side.width)
        side = draw("side", size, color)
    views = [draw("front", size, color), side, side.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
             draw("back", size, color)]
    for i, v in enumerate(views):
        gx = W * (i + 0.5) / 4
        x, y = int(gx - v.width / 2), int(0.9 * H - v.height)
        canvas.alpha_composite(shade_holes(v, bg), (x, y))
        if smoke:
            smoke_puffs(canvas, (x, y, x + v.width, y + v.height))
    return canvas


def effect_design(glow, W: int, H: int, bg=(0, 0, 0)) -> Image.Image:
    canvas = Image.new("RGBA", (W, H), bg + (255,))
    fx = draw_effect(int(0.5 * H), glow)
    canvas.alpha_composite(fx, ((W - fx.width) // 2, (H - fx.height) // 2))
    return canvas


# ---------------------------------------------------------------------------
# Guided painters


def _grey_blobs(guide: np.ndarray, editable: np.ndarray, min_area: int = 150):
    g = guide.astype(np.int32)
    grey = editable & ((g.max(-1) - g.min(-1)) < 14) & (g.mean(-1) > 60)
    lbl, n = ndimage.label(ndimage.binary_closing(grey, iterations=2))
    out = []
    for i, sl in enumerate(ndimage.find_objects(lbl), start=1):
        if sl is None:
            continue
        m = lbl[sl] == i
        if m.sum() >= min_area:
            out.append((sl, m))
    return out


def reference_rgba(ref_png: bytes) -> np.ndarray:
    """The subject from the edit's second image (the reference composited on the key)."""
    from ..pipeline.matte import key_matte

    arr = np.asarray(Image.open(io.BytesIO(ref_png)).convert("RGB"))
    m = key_matte(arr)
    ys, xs = np.nonzero(m.rgba[..., 3] > 16)
    return m.rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(ys) else m.rgba


def paint_objects(guide: np.ndarray, editable: np.ndarray, bg_rgb: np.ndarray, ref: np.ndarray,
                  rng: np.random.Generator, defects: list) -> Image.Image:
    """Each grey stand-in becomes the reference object, fitted to its box (the stand-in is the
    object's own silhouette, so position, size and tilt carry over)."""
    H, W = guide.shape[:2]
    out = guide.copy()
    out[editable] = bg_rgb[editable]
    base = Image.fromarray(out, "RGB").convert("RGBA")
    blobs = sorted(_grey_blobs(guide, editable), key=lambda t: (round(t[0][0].stop / max(1, H / 4)), t[0][1].start))
    drop = {i for k, i in defects if k == "drop"}
    refimg = Image.fromarray(ref, "RGBA")
    for fi, (sl, m) in enumerate(blobs):
        if fi in drop:
            continue
        ys, xs = np.nonzero(m)
        bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        obj = refimg.resize((max(1, int(bw)), max(1, int(bh))), Image.Resampling.LANCZOS)
        if ("flip", fi) in defects:
            obj = obj.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        obj = shade_holes(obj, np.median(bg_rgb[editable], 0) if editable.any() else (255, 0, 255))
        jx, jy = rng.normal(0, 0.01 * bh), rng.normal(0, 0.006 * bh)
        if ("drift", -1) in defects:  # L14: the live model drew every frame ~0.3 widths left of its guide
            jx -= 0.3 * bw
        if ("fuse", fi) in defects:  # L15: a frame pushed left until it touches the reference drawing
            strip_w = int(np.nonzero(editable.any(0))[0].min()) if editable.any() else 0
            jx -= sl[1].start + xs.min() - (strip_w - 0.35 * bw)
        ox, oy = int(sl[1].start + xs.min() + jx), max(0, int(sl[0].start + ys.min() + jy))
        base.alpha_composite(obj, (ox, oy))
        if ("tintsmoke", -1) in defects:
            smoke_puffs(base, (ox, oy, ox + obj.width, oy + obj.height))
    img = base.convert("RGB").filter(ImageFilter.GaussianBlur(0.5))
    arr = np.asarray(img).astype(np.float32) + rng.normal(0, 1.5, (H, W, 3)) * editable[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def paint_effect(guide: np.ndarray, editable: np.ndarray, glow, additive: bool, bg_rgb: np.ndarray,
                 rng: np.random.Generator, defects: list) -> Image.Image:
    """Each grey effect shape becomes glowing energy (on black), or a coloured puff (normal blend)."""
    H, W = guide.shape[:2]
    g = guide.astype(np.float32)
    shape = editable & ((g.max(-1) - g.min(-1)) < 20) & (g.mean(-1) > 90)
    bursts = [i for k, i in defects if k == "burst"]
    if bursts:  # K16: frame i drawn as a full burst whatever its phase (seen live after a repair)
        lbl, n = ndimage.label(ndimage.binary_dilation(shape, iterations=24))
        objs = [(sl, i + 1) for i, sl in enumerate(ndimage.find_objects(lbl)) if sl is not None]
        objs.sort(key=lambda t: (round(t[0][0].start / max(1, H / 6)), t[0][1].start))
        ext = max((max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start) for sl, _ in objs), default=0)
        yy, xx = np.mgrid[0:H, 0:W]
        for fi in bursts:
            if 0 <= fi < len(objs):
                sl, lab = objs[fi]
                cy, cx = (sl[0].start + sl[0].stop) / 2, (sl[1].start + sl[1].stop) / 2
                shape = shape & (lbl != lab)
                shape |= editable & ((yy - cy) ** 2 + (xx - cx) ** 2 <= (0.34 * ext) ** 2)
    # thin parts (tails, rings, sparks) and rims glow in the colour; only thick interiors burn white
    dist = cv2.distanceTransform(shape.astype(np.uint8), cv2.DIST_L2, 5)
    dmax = max(1.0, float(dist.max()))
    depth = np.clip((dist - 0.2 * dmax) / (0.5 * dmax), 0, 1)
    intensity = cv2.GaussianBlur(shape * (0.58 + 0.42 * depth), (0, 0), 1.2).astype(np.float32)
    size = float(np.sqrt(max(1, shape.sum())) * 1.5)
    fx = np.asarray(glow_image(intensity, glow, max(20.0, size / 3))).astype(np.float32)
    a = fx[..., 3:4] / 255.0
    base = guide.astype(np.float32).copy()
    base[editable] = bg_rgb[editable]
    if additive:
        out = base + fx[..., :3] * a
    else:  # a normal-blend effect is painted as chunky opaque shapes with a narrow soft rim
        a = np.clip(a * 2.2, 0, 1) ** 0.6
        out = base * (1 - a) + fx[..., :3] * a
    out = out + rng.normal(0, 1.0, out.shape) * editable[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def describe(img: Image.Image, kind: str) -> dict:
    c = dominant_color(img)
    names = {name: col for name, col in COLOR_WORDS.items()}
    name = min(names, key=lambda k: sum((a - b) ** 2 for a, b in zip(names[k], c)))
    what = {"character": "character in simple clothes", "vehicle": "armoured vehicle with a turret and treads",
            "machine": "industrial machine with a gear and a chimney", "effect": "glowing energy ball with a tail"}[kind]
    return {"description": f"A {name} {what}.", "facing": "right", "mirrorable": True, "additive": kind == "effect"}


def seed_of(text: str) -> int:
    return zlib.crc32(text.strip().lower().encode()) & 0x7FFFFFFF


def trace_svg(ref: np.ndarray, W: int, H: int, k: int = 8) -> str:
    """Stand-in for Quiver's image-to-vector on objects and effects: the reference view quantized to
    `k` flat colours, each colour region traced to paths, fitted into a W×H canvas with one group
    per colour layer (the real model returns one group per moving part)."""
    from ..color import palette, rgb_to_hex, hex_to_rgb, to_oklab

    a = ref[..., 3] >= 64
    ys, xs = np.nonzero(a)
    if len(ys) == 0:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"></svg>'
    ref = ref[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    s = min(0.8 * W / ref.shape[1], 0.8 * H / ref.shape[0])
    ox, oy = (W - ref.shape[1] * s) / 2, (H - ref.shape[0] * s) / 2
    pal = palette(ref[..., :3], a, k=k)
    lab = to_oklab(np.array([hex_to_rgb(h) for h, _ in pal], np.uint8))
    lbl = np.full(a.shape, -1)
    lbl[a] = np.sqrt(((to_oklab(ref[..., :3][a])[:, None] - lab[None]) ** 2).sum(-1)).argmin(1)
    groups = []
    for j, (hx, _) in enumerate(pal):
        m = (lbl == j).astype(np.uint8)
        cs, _ = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        d = []
        for c in cs:
            if cv2.contourArea(c) < 4:
                continue
            c = cv2.approxPolyDP(c, 0.8, True)[:, 0]
            d.append("M " + " L ".join(f"{ox + x * s:.1f},{oy + y * s:.1f}" for x, y in c) + " Z")
        if d:
            groups.append(f'<g id="part-{j}"><path fill="{hx}" fill-rule="evenodd" d="{" ".join(d)}"/></g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            + "".join(groups) + "</svg>")
