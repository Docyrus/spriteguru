"""Ingestion, background colour field and matte (section 9).

The background is a smooth colour field estimated from the image itself (a tile plate); the
matte is a two-threshold, connectivity-aware key against that plate. ML matting is a fallback.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageCms
from scipy import ndimage
from skimage.filters import apply_hysteresis_threshold

from ..color import from_oklab, linear_to_srgb, rgb_to_hex, srgb_to_linear, to_oklab
from ..env import get as app_env

ANALYSIS_EDGE = 1024


@dataclass
class Matte:
    rgba: np.ndarray  # straight alpha, despilled, uint8 (H, W, 4)
    mask: np.ndarray  # bool foreground
    path: str  # "key", "alpha" or "ml"
    key_rgb: tuple[int, int, int] | None = None
    key_lab: np.ndarray | None = None
    plate_lab: np.ndarray | None = None  # full-resolution plate, OKLab
    dist: np.ndarray | None = None  # D(x, y)
    t_low: float = 0.0
    t_high: float = 0.0
    shadow: np.ndarray | None = None
    signals: dict = field(default_factory=dict)

    @property
    def key_hex(self) -> str | None:
        return rgb_to_hex(self.key_rgb) if self.key_rgb else None


# ---------------------------------------------------------------------------
# 9.1 Ingestion


def ingest(src) -> np.ndarray:
    """Decode to RGBA8 in sRGB, converting any embedded ICC profile."""
    if isinstance(src, np.ndarray):
        arr = src
        if arr.ndim == 2:
            arr = np.stack([arr] * 3, -1)
        if arr.shape[2] == 3:
            arr = np.concatenate([arr, np.full(arr.shape[:2] + (1,), 255, np.uint8)], -1)
        return np.ascontiguousarray(arr.astype(np.uint8))
    if isinstance(src, (bytes, bytearray)):
        img = Image.open(io.BytesIO(src))
    elif isinstance(src, Image.Image):
        img = src
    else:
        img = Image.open(Path(src))
    icc = img.info.get("icc_profile")
    if icc:
        try:
            src_prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst_prof = ImageCms.createProfile("sRGB")
            mode = "RGBA" if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info else "RGB"
            img = ImageCms.profileToProfile(img.convert(mode), src_prof, dst_prof, outputMode=mode)
        except Exception:
            pass
    return np.ascontiguousarray(np.asarray(img.convert("RGBA"), dtype=np.uint8))


def has_alpha(rgba: np.ndarray) -> bool:
    return float((rgba[..., 3] < 250).mean()) >= 0.05


# ---------------------------------------------------------------------------
# 9.2 Background colour field


def border_seeds(shape: tuple[int, int], frac: float = 0.02) -> np.ndarray:
    h, w = shape
    by, bx = max(2, int(round(h * frac))), max(2, int(round(w * frac)))
    m = np.zeros((h, w), dtype=bool)
    m[:by], m[-by:], m[:, :bx], m[:, -bx:] = True, True, True, True
    return m


def hist_mode(samples: np.ndarray, bins: int = 32) -> tuple[np.ndarray, float]:
    """Mode of OKLab samples in a bins^3 histogram; returns (key, share near the mode)."""
    rng = [(0.0, 1.0), (-0.5, 0.5), (-0.5, 0.5)]
    hist, edges = np.histogramdd(samples, bins=bins, range=rng)
    idx = np.unravel_index(np.argmax(hist), hist.shape)
    sl = tuple(slice(max(0, i - 1), i + 2) for i in idx)
    share = float(hist[sl].sum() / max(1, len(samples)))
    lo = np.array([edges[d][idx[d]] for d in range(3)])
    hi = np.array([edges[d][idx[d] + 1] for d in range(3)])
    inside = np.all((samples >= lo) & (samples < hi), axis=1)
    key = samples[inside].mean(0) if inside.any() else (lo + hi) / 2
    return key.astype(np.float32), share


def tile_plate(lab: np.ndarray, ref: np.ndarray, tile: int = 16, near: float = 0.08,
               min_frac: float = 0.25) -> np.ndarray:
    """Per-tile medians of pixels within `near` of the reference (key or previous plate),
    holes inpainted from neighbours, bilinearly upsampled to the input size."""
    h, w = lab.shape[:2]
    gh, gw = max(1, h // tile), max(1, w // tile)
    crop = lab[: gh * tile, : gw * tile]
    ref_c = ref if ref.ndim == 1 else ref[: gh * tile, : gw * tile]
    d = np.linalg.norm(crop - ref_c, axis=-1)
    ok = d < near
    t = crop.reshape(gh, tile, gw, tile, 3).transpose(0, 2, 1, 3, 4).reshape(gh, gw, tile * tile, 3)
    okt = ok.reshape(gh, tile, gw, tile).transpose(0, 2, 1, 3).reshape(gh, gw, tile * tile)
    grid = np.zeros((gh, gw, 3), np.float32)
    cnt = okt.sum(-1)
    holes = cnt < min_frac * tile * tile
    for c in range(3):
        v = np.where(okt, t[..., c], np.nan)
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(v, axis=-1)
        grid[..., c] = np.nan_to_num(med, nan=0.0)
    if holes.all():
        base = ref if ref.ndim == 1 else np.median(ref.reshape(-1, 3), 0)
        grid[:] = base
    elif holes.any():
        grid = _fill_holes(grid, holes)
    return cv2.resize(grid, (w, h), interpolation=cv2.INTER_LINEAR)


def _fill_holes(grid: np.ndarray, holes: np.ndarray, iters: int = 40) -> np.ndarray:
    """Fill tiles covered by the character from their neighbours: nearest valid tile, then
    diffusion (only hole tiles change), which interpolates smooth background drift."""
    _, (iy, ix) = ndimage.distance_transform_edt(holes, return_indices=True)
    out = grid[iy, ix].copy()
    k = np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]], np.float32) / 4.0
    for _ in range(iters):
        sm = np.stack([ndimage.convolve(out[..., c], k, mode="nearest") for c in range(3)], -1)
        out[holes] = sm[holes]
    return out


# ---------------------------------------------------------------------------
# 9.3 Matte


def min_hole_area(char_h: float | None, shape: tuple[int, int]) -> float:
    if char_h:
        return max(25.0, (0.04 * char_h) ** 2)
    return max(25.0, 1e-4 * shape[0] * shape[1])


def key_matte(rgba: np.ndarray, *, seeds: np.ndarray | None = None, char_h: float | None = None,
              pixel: bool = False, ref_palette: list[str] | None = None, untint_smoke: bool = False) -> Matte:
    rgba = ingest(rgba)
    if has_alpha(rgba):
        return alpha_matte(rgba, pixel=pixel)
    rgb = rgba[..., :3]
    H, W = rgb.shape[:2]
    lab = to_oklab(rgb)

    # analysis copy for global statistics
    s = min(1.0, ANALYSIS_EDGE / max(H, W))
    small = cv2.resize(lab, (max(1, int(W * s)), max(1, int(H * s))), interpolation=cv2.INTER_AREA) if s < 1 else lab
    seed_full = border_seeds((H, W))
    if seeds is not None:
        seed_full = seed_full | seeds
    seed_small = cv2.resize(seed_full.astype(np.uint8), (small.shape[1], small.shape[0]),
                            interpolation=cv2.INTER_NEAREST).astype(bool)
    key, share = hist_mode(small[seed_small])
    if key[0] < 0.12 and float(np.hypot(key[1], key[2])) < 0.03:
        # black background: an additive effect (K4); alpha comes from luminance, not from a key
        return luma_matte(rgba, pixel=pixel)
    border_only = cv2.resize(border_seeds((H, W)).astype(np.uint8), (small.shape[1], small.shape[0]),
                             interpolation=cv2.INTER_NEAREST).astype(bool)
    _, border_share = hist_mode(small[border_only])

    tile = 16
    plate_small = tile_plate(small, key, tile=tile)
    plate_small = tile_plate(small, plate_small, tile=tile, near=0.06)
    plate = cv2.resize(plate_small, (W, H), interpolation=cv2.INTER_LINEAR) if s < 1 else plate_small
    D = np.linalg.norm(lab - plate, axis=-1)

    d = D[seed_full]
    sigma = float(1.4826 * np.median(np.abs(d - np.median(d))))
    t_low, t_high = max(3 * sigma, 0.03), max(6 * sigma, 0.08)
    fg = apply_hysteresis_threshold(D, t_low, t_high)

    # cast shadows: the key darkened (same chroma direction, 20–80% lower lightness)
    shadow = _shadow_mask(lab, key) & fg
    if shadow.any():
        # a shadow lies on the background: it must touch key-coloured pixels outside the character,
        # never sit enclosed inside the silhouette (a key-hued but darker costume is not a shadow)
        outside = ndimage.binary_dilation(~fg, iterations=2)
        lbl, n = ndimage.label(shadow)
        if n:
            ids = np.arange(1, n + 1)
            sizes = ndimage.sum(np.ones_like(D), lbl, ids)
            touch = ndimage.maximum(outside.astype(np.uint8), lbl, ids)
            filled_wo = ndimage.binary_fill_holes(fg & ~shadow)
            enclosed = ndimage.mean(filled_wo.astype(np.float32), lbl, ids)
            keep_ids = ids[(sizes >= 20) & (touch > 0) & (enclosed < 0.5)]
            shadow = np.isin(lbl, keep_ids)
        fg &= ~shadow

    filled = ndimage.binary_fill_holes(fg)
    pockets, n = ndimage.label(filled & ~fg & (D < t_low))
    cleared = 0
    if n:
        ids = np.arange(1, n + 1)
        area = ndimage.sum(np.ones_like(D), pockets, ids)
        mean_d = ndimage.mean(D, pockets, ids)
        clear = ids[(area > min_hole_area(char_h, (H, W))) | (mean_d < t_low / 2)]
        cleared = int(len(clear))
        filled &= ~np.isin(pockets, clear)
    # M14: background seen through small enclosed gaps (a muzzle brake's ports, a chain link, a
    # trigger guard) comes back as shaded key: key-hued, just above t_low, so hysteresis joins it to
    # the outline. The key is never on the subject by contract, so an enclosed region of *shaded key*
    # is a hole whatever its size. Shading scales linear RGB, which scales OKLab L, a, b alike, so a
    # shaded key keeps the key's hue and C/L exactly; a costume mixed toward grey near the key (M9:
    # (60, 224, 60) on (0, 255, 0), D 0.089) loses chroma faster than lightness and stays opaque, as
    # does a key-hued costume much darker than the key. Rims touching the exterior go to the band.
    near_lbl, n_near = ndimage.label(filled & (D < max(0.12, t_high)))
    if n_near:
        ids = np.arange(1, n_near + 1)
        touches = ndimage.maximum(ndimage.binary_dilation(~filled).astype(np.uint8), near_lbl, ids)
        size = ndimage.sum(np.ones_like(D), near_lbl, ids)
        k_c = float(np.linalg.norm(key[1:])) + 1e-6
        c_px = np.linalg.norm(lab[..., 1:], axis=-1)
        shade_err = np.abs(c_px / k_c - lab[..., 0] / max(float(key[0]), 1e-6))
        hue_cos = (lab[..., 1:] @ key[1:]) / (c_px * k_c + 1e-6)
        err = ndimage.mean(shade_err, near_lbl, ids)
        hue = ndimage.mean(hue_cos, near_lbl, ids)
        gaps = ids[(touches == 0) & (size >= 4) & (err < 0.08) & (hue > 0.97)]
        cleared += int(len(gaps))
        filled &= ~np.isin(near_lbl, gaps)
    fg = filled

    key_rgb = tuple(int(v) for v in from_oklab(key))
    matte = Matte(rgba=rgba, mask=fg, path="key", key_rgb=key_rgb, key_lab=key, plate_lab=plate, dist=D,
                  t_low=t_low, t_high=t_high, shadow=shadow)
    _refine_edges(matte, pixel=pixel, ref_palette=ref_palette)
    changed = 0
    if untint_smoke and not pixel:
        matte.rgba, changed = untint(matte.rgba)
    plate_dev = float(np.percentile(np.linalg.norm(plate_small - key, axis=-1), 95))
    interior = ndimage.binary_erosion(fg, iterations=2)
    key_d = np.linalg.norm(lab - key, axis=-1)
    contamination = float((key_d[interior] < t_high + 0.07).mean()) if interior.any() else 0.0
    matte.signals = {
        "path": "key", "key": rgb_to_hex(key_rgb), "uniformity": round(border_share, 4),
        "seed_share": round(share, 4), "plate_deviation": round(plate_dev, 4), "noise_sigma": round(sigma, 5),
        "t_low": round(t_low, 4), "t_high": round(t_high, 4), "key_contamination": round(contamination, 5),
        "shadow_px": int(shadow.sum()), "pockets_cleared": cleared,
        "spill": round(_spill(lab, fg, key, ref_palette), 4), "untinted_px": changed,
    }
    return matte


def _shadow_mask(lab: np.ndarray, key: np.ndarray) -> np.ndarray:
    Lk = float(key[0])
    ck = float(np.hypot(key[1], key[2]))
    if ck < 0.08 or Lk < 0.1:  # achromatic key: shadows are indistinguishable from dark fills
        return np.zeros(lab.shape[:2], bool)
    L = lab[..., 0]
    ratio = L / Lk
    rel = lab[..., 1:] / np.maximum(L, 1e-3)[..., None]
    rel_k = key[1:] / Lk
    dev = np.linalg.norm(rel - rel_k, axis=-1)
    return (ratio >= 0.2) & (ratio <= 0.8) & (dev < 0.12 * np.linalg.norm(rel_k) + 0.02)


def _spill(lab: np.ndarray, fg: np.ndarray, key: np.ndarray, ref_palette: list[str] | None) -> float:
    """Mean lean of character chroma toward the key hue, minus the reference palette's lean."""
    kd = key[1:] / (np.linalg.norm(key[1:]) + 1e-6)
    inner = ndimage.binary_erosion(fg, iterations=3)
    if not inner.any():
        return 0.0
    lean = float((lab[inner][:, 1:] @ kd).mean())
    if ref_palette:
        from ..color import hex_to_rgb

        pal = to_oklab(np.array([hex_to_rgb(h) for h in ref_palette], np.uint8))
        lean -= float((pal[:, 1:] @ kd).mean())
    return lean


# ---------------------------------------------------------------------------
# 9.4 Edge alpha and despill


def _refine_edges(m: Matte, *, pixel: bool, ref_palette: list[str] | None = None) -> None:
    rgb = m.rgba[..., :3]
    fg = m.mask
    lin = srgb_to_linear(rgb)
    B = srgb_to_linear(from_oklab(m.plate_lab))
    fg8 = fg.astype(np.uint8)
    depth = cv2.distanceTransform(fg8, cv2.DIST_L2, 3)
    outside = cv2.distanceTransform(1 - fg8, cv2.DIST_L2, 3)
    interior = depth >= 2
    band = (fg & ~interior) | (~fg & (outside <= 2))

    def local_mean(win):
        k = (win, win)
        wsum = cv2.boxFilter(interior.astype(np.float32), -1, k, normalize=False)
        csum = cv2.boxFilter(lin * interior[..., None], -1, k, normalize=False)
        return csum / np.maximum(wsum, 1e-6)[..., None], wsum

    F, wsum = local_mean(5)
    F9, wsum9 = local_mean(9)
    F = np.where((wsum > 0)[..., None], F, F9)
    has_f = (wsum > 0) | (wsum9 > 0)
    diff = F - B
    denom = (diff ** 2).sum(-1)
    alpha = np.clip(((lin - B) * diff).sum(-1) / np.maximum(denom, 1e-6), 0.0, 1.0)
    weak = (denom < 0.0025) | ~has_f
    alpha = np.where(weak, fg.astype(np.float32), alpha)
    a = np.where(interior, 1.0, np.where(band, alpha, 0.0)).astype(np.float32)
    a = np.where(fg & ~band, 1.0, a)
    # never grow the silhouette far beyond the keyed mask: outside pixels need clear evidence
    a = np.where(~fg & (a < 0.15), 0.0, a)

    if pixel:
        a_bin = a >= 0.5
        out = np.where((band & a_bin)[..., None], F, lin)
        m.mask = a_bin
        rgba = np.dstack([np.round(linear_to_srgb(out) * 255).astype(np.uint8), (a_bin * 255).astype(np.uint8)])
    else:
        with np.errstate(all="ignore"):
            clean = (lin - (1 - a)[..., None] * B) / np.maximum(a, 1e-3)[..., None]
        clean = np.where((band & (a > 0.05))[..., None], np.clip(clean, 0, 1), lin)
        deep = depth >= 5
        k21 = (21, 21)
        wd = cv2.boxFilter(deep.astype(np.float32), -1, k21, normalize=False)
        Fd = cv2.boxFilter(lin * deep[..., None], -1, k21, normalize=False) / np.maximum(wd, 1e-6)[..., None]
        if deep.any():
            Fd = np.where((wd > 0)[..., None], Fd, lin[deep].mean(0))
        else:
            Fd = F
        if m.key_lab is not None and m.plate_lab is not None and ref_palette:
            a, clean = _translucent(rgb, lin, B, a, clean, fg, m.key_lab, m.plate_lab, ref_palette)
        clean = _despill(clean, Fd, a, depth, m.key_lab)
        rgba = np.dstack([np.round(linear_to_srgb(clean) * 255).astype(np.uint8),
                          np.round(a * 255).astype(np.uint8)])
        m.mask = a >= 0.5
    rgba[a <= 0] = 0
    m.rgba = rgba


TURNAROUND_KEY = (255, 0, 255)  # reference views are always drawn on magenta


def tinted_grey(rgb, key=TURNAROUND_KEY, tol: float = 0.05) -> bool:
    """M16: is this colour a neutral grey seen through the reference view's magenta background? Unmixed
    against the key channel by channel, a tinted grey lands on one grey (the per-channel tint ratios
    agree); a real colour does not (red would need a negative blue)."""
    c = np.asarray(rgb, np.float64) / 255.0
    k = np.asarray(key, np.float64) / 255.0
    lo_ch = [i for i in range(3) if k[i] < 0.5]    # channels the key lacks: they carry the grey level
    hi_ch = [i for i in range(3) if k[i] >= 0.5]   # channels the key fills: the tint raises them
    if not lo_ch or not hi_ch:
        return False
    g = float(np.mean(c[lo_ch])) if len(lo_ch) else 0.0
    if max(abs(c[i] - g) for i in lo_ch) > tol:
        return False
    # grey g over key k with tint t: c = (1-t)*g' + t*k for the key-filled channels, g = (1-t)*g'
    ts = []
    for i in hi_ch:
        if c[i] <= g + 1e-6:
            return False
        ts.append((c[i] - g) / max(k[i] - g, 1e-6) if k[i] > g else 1.0)
    t = float(np.mean(ts))
    return 0.03 <= t <= 0.6 and max(ts) - min(ts) <= 0.1


def untint(rgba: np.ndarray, key=TURNAROUND_KEY) -> tuple[np.ndarray, int]:
    """M16: smoke or steam drawn over the magenta reference view carries a magenta tint, and every frame
    copies it. Pixels that are a grey seen through magenta (see `tinted_grey`) become that grey with
    the matching transparency: alpha *= 1 - tint. Returns the image and the pixels changed."""
    out = rgba.copy()
    c = out[..., :3].astype(np.float32) / 255.0
    k = np.asarray(key, np.float32) / 255.0
    lo = [i for i in range(3) if k[i] < 0.5]
    hi = [i for i in range(3) if k[i] >= 0.5]
    if not lo or not hi:
        return out, 0
    g = c[..., lo].mean(-1)
    flat = np.abs(c[..., lo] - g[..., None]).max(-1) <= 0.05
    ts = np.stack([(c[..., i] - g) / np.maximum(k[i] - g, 1e-3) for i in hi], -1)
    t = ts.mean(-1)
    sel = (out[..., 3] > 0) & flat & (ts.min(-1) > 0) & (t >= 0.03) & (t <= 0.6) & (ts.max(-1) - ts.min(-1) <= 0.1)
    if not sel.any():
        return out, 0
    grey = np.clip(np.round(g * 255), 0, 255).astype(np.uint8)
    for i in range(3):
        out[..., i] = np.where(sel, grey, out[..., i])
    out[..., 3] = np.where(sel, np.round(out[..., 3] * (1 - t)).astype(np.uint8), out[..., 3])
    return out, int(sel.sum())


def expects_translucency(description: str | None, kind: str = "character", blend: str = "normal") -> bool:
    """Only subjects the description gives smoke, steam or mist (or normal-blend effects) are untinted:
    a pale lavender costume is a tinted grey too, and must keep its colour."""
    import re

    if kind == "effect" and blend != "add":
        return True
    return bool(re.search(r"\b(steam\w*|smok\w*|chimney|exhaust\w*|mist\w*|fog\w*|vapou?r\w*|fumes?|cloud\w*)\b",
                          (description or "").lower()))


def _translucent(rgb: np.ndarray, lin: np.ndarray, B: np.ndarray, a: np.ndarray, clean: np.ndarray,
                 fg: np.ndarray, key_lab: np.ndarray, plate_lab: np.ndarray,
                 ref_palette: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """M15: smoke, steam and dust painted over the key come back key-tinted and opaque. A pixel that
    leans toward the key (relative to the plate) and matches none of the approved subject's colours
    is a translucent neutral with the background showing through: 1 - alpha = lean ratio, colour
    unmixed from the plate. The palette test keeps a red costume on a magenta key solid (it leans as
    much, but it is the subject's own colour); smoke is never in the approved reference."""
    from ..color import hex_to_rgb

    kab = key_lab[1:]
    kd = kab / (np.linalg.norm(kab) + 1e-6)
    lab = to_oklab(rgb)
    plate_lean = np.maximum(plate_lab[..., 1:] @ kd, 1e-3)
    r = np.clip((lab[..., 1:] @ kd) / plate_lean, 0.0, 1.0)
    cand = fg & (r > 0.15)
    if not cand.any():
        return a, clean
    # M16: a palette colour that is a grey tinted by the reference view's magenta background came from
    # smoke or steam over the key, not from the design; it must not protect the same tint in frames
    ref_palette = [h for h in ref_palette if not tinted_grey(hex_to_rgb(h))]
    if not ref_palette:
        ref_palette = ["#000000"]
    pal = to_oklab(np.array([hex_to_rgb(h) for h in ref_palette], np.uint8))
    d = np.full(r.shape, np.inf, np.float32)
    ys, xs = np.nonzero(cand)
    if len(pal):
        d[ys, xs] = np.sqrt(((lab[ys, xs][:, None, :] - pal[None]) ** 2).sum(-1)).min(1)
    sel = cand & (d > 0.08)
    if not sel.any():
        return a, clean
    at = 1.0 - r
    with np.errstate(all="ignore"):
        unmixed = np.clip((lin - r[..., None] * B) / np.maximum(at, 1e-3)[..., None], 0, 1)
    a = np.where(sel, np.minimum(a, at), a).astype(np.float32)
    clean = np.where(sel[..., None], unmixed, clean)
    return a, clean


def _despill(lin: np.ndarray, F: np.ndarray, a: np.ndarray, depth: np.ndarray, key_lab: np.ndarray,
             width: float = 5.0) -> np.ndarray:
    """Hue-projection despill near the silhouette: remove the chroma an edge pixel leans toward the
    key beyond what the local deep interior (F, at least 5 px inside) leans. Video chroma
    subsampling and soft model edges smear the key into the outline, deeper than the alpha band,
    which shows as a coloured fringe."""
    kab = key_lab[1:]
    kn = float(np.linalg.norm(kab))
    if kn < 0.05:
        return lin
    kd = (kab / kn).astype(np.float32)
    near = (a > 0) & (depth < width)
    if not near.any():
        return lin
    px = to_oklab(np.clip(linear_to_srgb(lin[near]) * 255, 0, 255))
    ref = to_oklab(np.clip(linear_to_srgb(np.clip(F[near], 0, 1)) * 255, 0, 255))
    lean = px[:, 1:] @ kd
    base = ref[:, 1:] @ kd
    excess = np.maximum(0.0, lean - np.minimum(base, lean) - 0.005)
    px[:, 1:] -= excess[:, None] * kd[None, :]
    out = lin.copy()
    out[near] = srgb_to_linear(from_oklab(px))
    return out


# ---------------------------------------------------------------------------
# Luminance path (additive effects on black)


def luma_matte(rgba: np.ndarray, *, pixel: bool = False, mask_threshold: float = 0.15) -> Matte:
    """Glowing effects drawn on black: alpha = brightest channel above the background noise floor,
    colour unpremultiplied against black, so the sprite composites identically with additive or
    normal blending and never keeps a dark box or coloured halo."""
    rgb = rgba[..., :3].astype(np.float32)
    mx = rgb.max(-1)
    border = mx[border_seeds(mx.shape)]
    # robust floor: glow that happens to reach the border (a reference strip, a wide burst) must not
    # raise it, or faint frames vanish (K10); sensor-like noise sets it through the MAD
    med = float(np.median(border))
    mad = float(np.median(np.abs(border - med)))
    floor = min(float(np.percentile(border, 99)), med + 6.0 * 1.4826 * mad + 4.0) + 2.0
    a = np.clip((mx - floor) / max(1.0, 255.0 - floor), 0.0, 1.0)
    if pixel:
        # K13: pixel effects are drawn in flat colours, dim ones included; a 0.5 cut drops a dark-blue
        # ring. Hard alpha from a low cut, colour kept as painted (additive display adds it as is).
        on = a >= 0.12
        out = np.dstack([np.where(on[..., None], rgb, 0), on * 255.0]).astype(np.uint8)
        return Matte(rgba=out, mask=on, path="luma", key_rgb=(0, 0, 0),
                     signals={"path": "luma", "key": "#000000", "uniformity": 1.0, "noise_floor": round(floor, 2),
                              "key_contamination": 0.0})
    color = np.where(a[..., None] > 1e-3, np.clip(rgb / np.maximum(mx, 1e-3)[..., None] * 255.0 *
                                                  np.minimum(1.0, mx / 255.0 / np.maximum(a, 1e-3))[..., None], 0, 255),
                     0)
    out = np.dstack([np.round(color), np.round(a * 255)]).astype(np.uint8)
    out[out[..., 3] == 0] = 0
    mask = a >= mask_threshold
    return Matte(rgba=out, mask=mask, path="luma", key_rgb=(0, 0, 0),
                 signals={"path": "luma", "key": "#000000", "uniformity": 1.0, "noise_floor": round(floor, 2),
                          "key_contamination": 0.0})


# ---------------------------------------------------------------------------
# Alpha path (Retro Diffusion, vector renders)


def alpha_matte(rgba: np.ndarray, *, pixel: bool = False) -> Matte:
    a = rgba[..., 3]
    mask = a >= 128
    lbl, n = ndimage.label(mask)
    if n:
        sizes = ndimage.sum(np.ones(mask.shape), lbl, np.arange(1, n + 1))
        mask &= ~np.isin(lbl, np.arange(1, n + 1)[sizes < 4])
    out = rgba.copy()
    if pixel:
        out[..., 3] = np.where(mask, 255, 0)
    out[~(out[..., 3] > 0)] = 0
    return Matte(rgba=out, mask=mask, path="alpha", signals={"path": "alpha", "uniformity": 1.0})


# ---------------------------------------------------------------------------
# 9.5 ML matting fallback (BiRefNet through rembg), per cell crop


_SESSION = None


def ml_available() -> bool:
    try:
        import rembg  # noqa: F401

        return True
    except Exception:
        return False


def ml_alpha(rgb_crop: np.ndarray, model: str | None = None) -> np.ndarray:
    """Alpha (float 0..1) for one crop from BiRefNet on the ONNX CPU provider."""
    global _SESSION
    import os

    from ..ortenv import prepare

    prepare()  # before rembg creates its onnxruntime session (RT1)
    from rembg import new_session, remove

    name = model or app_env("MATTE_MODEL", "birefnet-general")
    if _SESSION is None or getattr(_SESSION, "_sk_name", None) != name:
        _SESSION = new_session(name, providers=["CPUExecutionProvider"])
        _SESSION._sk_name = name
    out = remove(Image.fromarray(rgb_crop), session=_SESSION, only_mask=True)
    return np.asarray(out, dtype=np.float32) / 255.0


def ml_matte(rgba: np.ndarray, boxes: list[tuple[int, int, int, int]], key: Matte | None = None,
             pixel: bool = False) -> Matte:
    """Matte each region crop with the ML model; where a partial key exists, intersect them:
    the model decides the silhouette and the key refines edge alpha."""
    H, W = rgba.shape[:2]
    alpha = np.zeros((H, W), np.float32)
    for x0, y0, x1, y1 in boxes:
        crop = np.ascontiguousarray(rgba[y0:y1, x0:x1, :3])
        alpha[y0:y1, x0:x1] = np.maximum(alpha[y0:y1, x0:x1], ml_alpha(crop))
    mask = alpha >= 0.5
    out = rgba.copy()
    if key is not None and key.path == "key":
        edge = ndimage.binary_dilation(mask, iterations=2) & ~ndimage.binary_erosion(mask, iterations=2)
        ka = key.rgba[..., 3].astype(np.float32) / 255.0
        a = np.where(edge, np.minimum(alpha, np.maximum(ka, 0.0)), alpha)
        out[..., :3] = np.where(edge[..., None], key.rgba[..., :3], rgba[..., :3])
    else:
        a = alpha
    if pixel:
        a = (a >= 0.5).astype(np.float32)
    out[..., 3] = np.round(a * 255).astype(np.uint8)
    out[out[..., 3] == 0] = 0
    return Matte(rgba=out, mask=a >= 0.5, path="ml", key_rgb=key.key_rgb if key else None,
                 signals={"path": "ml", "uniformity": key.signals.get("uniformity") if key else None})
