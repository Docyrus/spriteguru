"""Colour science: sRGB/linear/OKLab conversion, palette extraction and chroma key choice (6.6)."""

from __future__ import annotations

import numpy as np

KEYS = {"magenta": "#FF00FF", "green": "#00FF00", "cyan": "#00FFFF", "blue": "#0000FF"}
KEY_NAMES = {v: k for k, v in KEYS.items()}

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]], dtype=np.float32)
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]], dtype=np.float32)
_M1i = np.linalg.inv(_M1).astype(np.float32)
_M2i = np.linalg.inv(_M2).astype(np.float32)


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """sRGB uint8, or float in [0, 1], to linear light float32."""
    c = np.asarray(c)
    c = c.astype(np.float32) / 255.0 if c.dtype == np.uint8 else c.astype(np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055).astype(np.float32)


def to_oklab(rgb: np.ndarray) -> np.ndarray:
    """sRGB in 0..255 (..., 3) -> OKLab float32 (..., 3)."""
    lin = srgb_to_linear(np.asarray(rgb, dtype=np.float32) / 255.0)
    lms = lin @ _M1.T
    return (np.cbrt(np.maximum(lms, 0.0)) @ _M2.T).astype(np.float32)


def from_oklab(lab: np.ndarray) -> np.ndarray:
    """OKLab (..., 3) -> sRGB uint8."""
    lms = (np.asarray(lab, dtype=np.float32) @ _M2i.T) ** 3
    lin = lms @ _M1i.T
    return np.clip(np.round(linear_to_srgb(lin) * 255.0), 0, 255).astype(np.uint8)


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb) -> str:
    r, g, b = (int(x) for x in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def key_name(hex_code: str) -> str:
    return KEY_NAMES.get(hex_code.upper(), "key colour")


def palette(rgb: np.ndarray, mask: np.ndarray, k: int = 12, iters: int = 12,
            seed: int = 0) -> list[tuple[str, float]]:
    """Dominant colours of the masked pixels as (hex, coverage), by k-means in OKLab."""
    px = rgb[mask.astype(bool)]
    if len(px) == 0:
        return []
    lab = to_oklab(px.reshape(-1, 3))
    rng = np.random.default_rng(seed)
    if len(lab) > 60000:
        lab = lab[rng.choice(len(lab), 60000, replace=False)]
    k = min(k, len(np.unique(np.round(lab, 3), axis=0)))
    centers = kmeans_pp(lab, k, rng)
    for _ in range(iters):
        d = ((lab[:, None, :] - centers[None]) ** 2).sum(-1)
        lbl = d.argmin(1)
        for j in range(k):
            sel = lab[lbl == j]
            if len(sel):
                centers[j] = sel.mean(0)
    d = ((lab[:, None, :] - centers[None]) ** 2).sum(-1)
    lbl = d.argmin(1)
    cov = np.bincount(lbl, minlength=k) / len(lab)
    order = np.argsort(-cov)
    return [(rgb_to_hex(from_oklab(centers[j])), float(cov[j])) for j in order if cov[j] > 0]


def kmeans_pp(x: np.ndarray, k: int, rng: np.random.Generator, weights: np.ndarray | None = None) -> np.ndarray:
    w = np.ones(len(x)) if weights is None else weights.astype(np.float64)
    first = rng.choice(len(x), p=w / w.sum())
    centers = [x[first]]
    d2 = ((x - centers[0]) ** 2).sum(-1)
    for _ in range(1, k):
        p = d2 * w
        if p.sum() <= 0:
            break
        idx = rng.choice(len(x), p=p / p.sum())
        centers.append(x[idx])
        d2 = np.minimum(d2, ((x - x[idx]) ** 2).sum(-1))
    return np.array(centers, dtype=np.float32)


def pick_key(pal: list[tuple[str, float]], min_coverage: float = 0.01) -> tuple[str, float]:
    """The key with the largest minimum OKLab distance to any palette cluster over 1% coverage."""
    clusters = [to_oklab(np.array(hex_to_rgb(h), dtype=np.uint8)) for h, c in pal if c > min_coverage]
    best, best_d = KEYS["magenta"], -1.0
    for name, hx in KEYS.items():
        kl = to_oklab(np.array(hex_to_rgb(hx), dtype=np.uint8))
        d = min((float(np.linalg.norm(kl - c)) for c in clusters), default=1.0)
        if d > best_d:
            best, best_d = hx, d
    return best, best_d
