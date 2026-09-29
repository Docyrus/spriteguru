"""Metric catalogue (14.1): findings computed from the pipeline's outputs."""

from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage

from .. import embed
from ..color import hex_to_rgb, to_oklab
from ..spec import Finding


def background_findings(signals: dict) -> list[Finding]:
    out = []
    u = signals.get("uniformity")
    if u is not None and signals.get("path") == "key":
        if u < 0.6:
            out.append(Finding(metric="background", level="fail", severity=2, value=u, threshold=0.6,
                               message=f"background is not uniform (dominant key share {u:.2f})", remedy="ml_matte"))
        elif u < 0.8:
            out.append(Finding(metric="background", level="warn", severity=1, value=u, threshold=0.8,
                               message=f"background drifts (dominant key share {u:.2f})", remedy="ml_matte"))
    c = signals.get("key_contamination")
    if c is not None:
        if c > 0.02:
            out.append(Finding(metric="key_contamination", level="fail", severity=2, value=c, threshold=0.02,
                               message=f"{c:.1%} of character pixels are close to the key", remedy="reroll_next_key"))
        elif c > 0.005:
            out.append(Finding(metric="key_contamination", level="warn", severity=1, value=c, threshold=0.005,
                               message=f"{c:.1%} of character pixels are close to the key", remedy="reroll_next_key"))
    return out


def pose_conformance(frame_masks: list[np.ndarray], guide_masks: list[np.ndarray], char_h: float,
                     band: float = 0.08, reach: bool = True) -> tuple[list[float], list[Finding]]:
    """Shape agreement of each frame silhouette with its guide mannequin.

    Placement and overall size are registration's job, so the frame is first moved onto the
    mannequin (centroids, anywhere within its cell) and scaled to the mannequin's height. The score
    is then a boundary-tolerant F1: frame pixels inside the dilated mannequin (precision) and
    mannequin pixels covered by the dilated frame (recall), with a band of 8% of the character height
    that absorbs armour, capes and clothing bulkier than the mannequin; a wrong pose does not fit."""
    ious, reach_dev = [], []
    r = max(1, int(round(band * char_h)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    for fm, gm in zip(frame_masks, guide_masks):
        if not fm.any() or not gm.any():
            ious.append(0.0)
            reach_dev.append(1.0)
            continue
        fy, fx = np.argwhere(fm).mean(0)
        gy, gx = np.argwhere(gm).mean(0)
        fys = np.nonzero(fm.any(1))[0]
        gys = np.nonzero(gm.any(1))[0]
        s = float(np.clip((gys.max() - gys.min() + 1) / max(1, fys.max() - fys.min() + 1), 0.75, 1.33))
        M = np.float32([[s, 0, gx - s * fx], [0, s, gy - s * fy]])
        fm = cv2.warpAffine(fm.astype(np.uint8), M, (fm.shape[1], fm.shape[0]), flags=cv2.INTER_NEAREST).astype(bool)
        if not fm.any():
            ious.append(0.0)
            reach_dev.append(1.0)
            continue
        g = cv2.dilate(gm.astype(np.uint8), k).astype(bool)
        f = cv2.dilate(fm.astype(np.uint8), k).astype(bool)
        prec = float(np.logical_and(fm, g).sum() / fm.sum())
        rec = float(np.logical_and(gm, f).sum() / gm.sum())
        ious.append(2 * prec * rec / max(prec + rec, 1e-9))
        reach_dev.append(float(np.percentile(np.abs(_radial(fm, (gy, gx)) - _radial(gm, (gy, gx))), 90) / char_h))
    # Reach: the farthest extent per direction from the centroid (where limbs, weapons and feet go).
    # Costume bulk shifts every frame alike, so a wrong pose shows as a frame-specific outlier.
    base = float(np.median(reach_dev)) if reach_dev else 0.0
    reach_fail = {i for i, v in enumerate(reach_dev) if v - base > 0.3 and v > 2.0 * base} if reach else set()
    reach_warn = ({i for i, v in enumerate(reach_dev) if v - base > 0.2 and v > 1.6 * base} - reach_fail) if reach else set()
    out = []
    fails = sorted({i for i, v in enumerate(ious) if v < 0.5} | reach_fail)
    warns = sorted(({i for i, v in enumerate(ious) if 0.5 <= v < 0.65} | reach_warn) - set(fails))
    if fails:
        out.append(Finding(metric="pose", level="fail", severity=2, frames=fails,
                           value=round(min(ious[i] for i in fails), 3), threshold=0.5,
                           message=f"frames {', '.join(str(i + 1) for i in fails)} do not follow their guide pose",
                           remedy="pose_fix"))
    if warns:
        out.append(Finding(metric="pose", level="warn", severity=1, frames=warns,
                           value=round(min(ious[i] for i in warns), 3), threshold=0.65,
                           message=f"frames {', '.join(str(i + 1) for i in warns)} drift from their guide pose",
                           remedy="pose_fix"))
    return ious, out, [round(v, 4) for v in reach_dev]  # reach returned, not stashed: analyses run concurrently


def _radial(m: np.ndarray, c: tuple[float, float], bins: int = 48) -> np.ndarray:
    ys, xs = np.nonzero(m)
    dy, dx = ys - c[0], xs - c[1]
    ang = ((np.arctan2(dy, dx) + np.pi) / (2 * np.pi) * bins).astype(int) % bins
    out = np.zeros(bins)
    np.maximum.at(out, ang, np.hypot(dy, dx))
    return out


def identity(frames: list[np.ndarray], reference: np.ndarray | None, palette: list[str] | None) -> tuple[dict, list[Finding]]:
    """DINOv2 cosine to the reference and palette distance; outliers by z-score below −2.5."""
    n = len(frames)
    if n < 3:
        return {}, []
    embs = [embed.embed(f) for f in frames]
    ref = embed.embed(reference) if reference is not None else np.mean(embs, 0)
    cos = np.array([embed.cosine(e, ref) for e in embs])
    pal_lab = None
    if palette:
        pal_lab = to_oklab(np.array([hex_to_rgb(h) for h in palette], np.uint8))
    elif reference is not None:
        pal_lab = _palette_lab(reference)
    pdist = np.zeros(n)
    if pal_lab is not None and len(pal_lab):
        for i, f in enumerate(frames):
            px = f[f[..., 3] >= 128][:, :3]
            if len(px) == 0:
                continue
            if len(px) > 4000:
                px = px[np.linspace(0, len(px) - 1, 4000).astype(int)]
            lab = to_oklab(px)
            d = np.sqrt(((lab[:, None, :] - pal_lab[None]) ** 2).sum(-1)).min(1)
            pdist[i] = float(np.mean(d))

    def z(v, invert=False):
        s = v.std()
        if s < 1e-6:
            return np.zeros_like(v)
        zz = (v - np.median(v)) / (1.4826 * np.median(np.abs(v - np.median(v))) + 1e-6)
        return -zz if invert else zz

    zc = z(cos)
    zp = z(pdist, invert=True)
    # Palette drift is pose-invariant and fails; an embedding-only outlier is a warning, because
    # DINOv2 embeddings also move with extreme poses (tucked jumps, lying down).
    pal_bad = [i for i in range(n) if zp[i] < -2.5 and pdist[i] > np.median(pdist) + 0.025]
    emb_bad = [i for i in range(n) if zc[i] < -2.5 and cos[i] < np.median(cos) - 0.10 and i not in pal_bad]
    info = {"cosine": [round(float(c), 4) for c in cos], "palette_dist": [round(float(p), 4) for p in pdist]}
    out = []
    if pal_bad:
        out.append(Finding(metric="identity", level="fail", severity=2, frames=pal_bad,
                           value=round(float(max(pdist[i] for i in pal_bad)), 4), threshold=-2.5,
                           message=f"frames {', '.join(str(i + 1) for i in pal_bad)} drift from the character's colours",
                           remedy="identity_fix"))
    if emb_bad:
        out.append(Finding(metric="identity", level="warn", severity=1, frames=emb_bad,
                           value=round(float(min(cos[i] for i in emb_bad)), 4), threshold=-2.5,
                           message=f"frames {', '.join(str(i + 1) for i in emb_bad)} look less like the character",
                           remedy="identity_fix"))
    return info, out


COVERAGE_FAIL = 0.55  # calibrated: correct non-pixel outputs >= 0.60 (live 0.81-0.89), recolours 0.44-0.49


def palette_coverage(frames: list[np.ndarray], reference: np.ndarray, tau: float = 0.09) -> tuple[dict, list[Finding]]:
    """Q2: the outlier test above is relative, so an animation that is recoloured as a whole passes it.
    This compares each frame's colour mass with the reference view's palette coverage (histogram
    intersection in OKLab); a low median means the whole animation is not the approved subject."""
    from ..color import palette

    pal = palette(reference[..., :3], reference[..., 3] >= 128, k=12)
    if not pal or len(frames) < 2:
        return {}, []
    lab = to_oklab(np.array([hex_to_rgb(h) for h, _ in pal], np.uint8))
    cov = np.array([c for _, c in pal])
    vals = []
    for f in frames:
        px = f[f[..., 3] >= 128][:, :3]
        if len(px) == 0:
            continue
        if len(px) > 6000:
            px = px[np.linspace(0, len(px) - 1, 6000).astype(int)]
        d = np.sqrt(((to_oklab(px)[:, None, :] - lab[None]) ** 2).sum(-1))
        near = d.min(1) < tau
        h = np.bincount(d.argmin(1)[near], minlength=len(lab)) / len(px)
        vals.append(float(np.minimum(h, cov).sum()))
    if not vals:
        return {}, []
    med = float(np.median(vals))
    info = {"palette_coverage": [round(v, 4) for v in vals]}
    if med >= COVERAGE_FAIL:
        return info, []
    return info, [Finding(metric="identity", level="fail", severity=3, value=round(med, 4), threshold=COVERAGE_FAIL,
                          message=f"the animation's colours do not match the approved reference "
                                  f"(palette coverage {med:.2f})", remedy="reroll")]


def _palette_lab(rgba: np.ndarray, k: int = 12) -> np.ndarray:
    from ..color import palette

    pal = palette(rgba[..., :3], rgba[..., 3] >= 128, k=k)
    return to_oklab(np.array([hex_to_rgb(h) for h, c in pal if c > 0.01], np.uint8)) if pal else np.zeros((0, 3))


def sharpness(frames: list[np.ndarray]) -> tuple[list[float], list[Finding]]:
    vals = []
    for f in frames:
        g = cv2.cvtColor(f[..., :3], cv2.COLOR_RGB2GRAY).astype(np.float64)
        m = ndimage.binary_erosion(f[..., 3] >= 128, iterations=2)
        lap = cv2.Laplacian(g, cv2.CV_64F)
        vals.append(float(lap[m].var()) if m.any() else 0.0)
    med = float(np.median(vals)) if vals else 0.0
    soft = [i for i, v in enumerate(vals) if med > 0 and v < 0.6 * med]
    out = []
    if soft:
        out.append(Finding(metric="sharpness", level="warn", severity=1, frames=soft,
                           value=round(min(vals[i] for i in soft) / med, 3), threshold=0.6,
                           message=f"frames {', '.join(str(i + 1) for i in soft)} are blurrier than the rest",
                           remedy="frame_repair"))
    return vals, out
