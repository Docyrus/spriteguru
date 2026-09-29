"""Video-to-sprite (13.7): decode, find the loop period by self-similarity, cut where the clip best
matches itself (the video-textures idea), resample the cycle to N sharp frames."""

from __future__ import annotations

import io

import av
import cv2
import numpy as np

from ..color import hex_to_rgb
from ..spec import Finding
from .matte import key_matte
from .register import anchor_of


def decode(data: bytes, max_side: int = 1024) -> tuple[list[np.ndarray], float]:
    frames = []
    with av.open(io.BytesIO(data)) as c:
        s = c.streams.video[0]
        fps = float(s.average_rate or s.guessed_rate or 24)
        for fr in c.decode(s):
            img = fr.to_ndarray(format="rgb24")
            h, w = img.shape[:2]
            k = min(1.0, max_side / max(h, w))
            if k < 1:
                img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
            frames.append(img)
    return frames, fps


def _quick_mask(img: np.ndarray) -> np.ndarray:
    """Cheap foreground estimate against the frame's own border colour (for the period search)."""
    h, w = img.shape[:2]
    b = max(2, min(h, w) // 50)
    border = np.concatenate([img[:b].reshape(-1, 3), img[-b:].reshape(-1, 3), img[:, :b].reshape(-1, 3),
                             img[:, -b:].reshape(-1, 3)])
    key = np.median(border, 0)
    d = np.abs(img.astype(np.int16) - key).sum(-1)
    return d > 60


def _signature(img: np.ndarray, mask: np.ndarray, height: int = 64) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return np.zeros((height, height), np.float32), np.zeros((height, height), bool)
    ax, ay = anchor_of(mask)
    top = ys.min()
    hh = max(8, ay - top + 1)
    s = height / hh
    M = np.float32([[s, 0, height / 2 - ax * s], [0, s, height * 0.95 - ay * s]])
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0 * mask
    g = cv2.warpAffine(gray, M, (height, height))
    m = cv2.warpAffine(mask.astype(np.uint8), M, (height, height)) > 0
    e = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return e, m


def distance_matrix(sigs) -> np.ndarray:
    n = len(sigs)
    E = np.stack([s[0] for s in sigs]).reshape(n, -1)
    M = np.stack([s[1] for s in sigs]).reshape(n, -1).astype(np.float32)
    inter = M @ M.T
    area = M.sum(1)
    union = area[:, None] + area[None] - inter
    iou = np.where(union > 0, inter / np.maximum(union, 1), 1.0)
    scale = max(float(E.max()), 1e-6)
    En = E / scale
    sq = (En ** 2).sum(1)
    # mean absolute difference approximated by RMS for speed
    rms = np.sqrt(np.maximum(sq[:, None] + sq[None] - 2 * En @ En.T, 0) / En.shape[1])
    return 0.5 * (1 - iou) + 0.5 * rms * 4


def find_period(D: np.ndarray, t_min: int, t_max: int) -> tuple[int, float]:
    L = len(D)
    best, best_c = t_min, np.inf
    for T in range(t_min, min(t_max, L - 2) + 1):
        c = float(np.mean([D[i, i + T] for i in range(L - T)]))
        if c < best_c - 1e-9:
            best, best_c = T, c
    # prefer the fundamental: a multiple of a nearly-as-good shorter period is not the period
    for T in range(t_min, best):
        if best % T == 0 or abs(best / T - round(best / T)) < 0.08:
            c = float(np.mean([D[i, i + T] for i in range(L - T)]))
            if c <= best_c * 1.1:
                return T, c
    return best, best_c


def blockiness(img: np.ndarray) -> float:
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32)
    dx = np.abs(np.diff(g, axis=1))
    at = dx[:, 7::8].mean() if dx.shape[1] > 8 else 0.0
    inside = np.delete(dx, np.s_[7::8], axis=1).mean() if dx.shape[1] > 8 else 1.0
    return float(at / max(inside, 1e-6))


def key_remnant_share(rgba: np.ndarray, key_rgb, tau: float = 0.2) -> float:
    """V6: share of opaque pixels still within `tau` (OKLab) of the key after matting. Key selection
    keeps design colours at least ~0.3 away, so what remains this close is background, not design."""
    from ..color import to_oklab

    op = rgba[..., 3] >= 128
    if not op.any() or key_rgb is None:
        return 0.0
    k = to_oklab(np.array([key_rgb], np.uint8))[0]
    d = np.sqrt(((to_oklab(rgba[..., :3][op]) - k) ** 2).sum(-1))
    return float((d < tau).mean())


def video_to_frames(data: bytes, n: int, *, key: str | None = None, loop: bool = True,
                    action: str = "walk", palette: list[str] | None = None,
                    untint_smoke: bool = False) -> tuple[list[np.ndarray], dict]:
    raw, fps = decode(data)
    L = len(raw)
    if L < 4:
        raise ValueError(f"video has only {L} frames")
    masks = [_quick_mask(f) for f in raw]
    sigs = [_signature(f, m) for f, m in zip(raw, masks)]
    D = distance_matrix(sigs)
    t_min = max(2, int(round(0.25 * fps)))
    t_max = max(t_min + 1, L // 2)
    T, cost = find_period(D, t_min, t_max)
    starts = range(0, L - T)
    s = int(min(starts, key=lambda i: D[i, i + T])) if len(starts) else 0
    picks = []
    sharp_all = []
    for k in range(n):
        t = s + k * T / n
        cands = [int(np.clip(round(t) + d, 0, L - 1)) for d in (-1, 0, 1)]
        best = None
        for ci in dict.fromkeys(cands):
            g = cv2.cvtColor(raw[ci], cv2.COLOR_RGB2GRAY)
            m = masks[ci]
            v = float(cv2.Laplacian(g, cv2.CV_64F)[m].var()) if m.any() else 0.0
            # stay close to the ideal phase: sharpness wins only among neighbours
            score = v * (1.0 - 0.15 * abs(ci - t))
            if best is None or score > best[0]:
                best = (score, ci, v)
        picks.append(best[1])
        sharp_all.append(best[2])
    frames, remnants = [], []
    for ci in picks:
        # each frame against its own plate (video backgrounds drift), with the approved palette so glow
        # and smoke over the key are unmixed as on sheets (V6)
        m = key_matte(raw[ci], ref_palette=palette, untint_smoke=untint_smoke)
        frames.append(m.rgba)
        remnants.append(key_remnant_share(m.rgba, m.key_rgb))
    block = float(np.median([blockiness(raw[ci]) for ci in picks]))
    info = {"fps": fps, "frames_decoded": L, "period_frames": T, "period_s": round(T / fps, 4),
            "start": s, "picks": picks, "seam_cost": round(float(D[s, s + T]) if s + T < L else 0.0, 4),
            "period_cost": round(cost, 4), "blockiness": round(block, 3)}
    findings = []
    worst = max(remnants) if remnants else 0.0
    info["key_remnants"] = round(worst, 5)
    if worst > 0.003:
        findings.append(Finding(metric="key_contamination", level="fail" if worst > 0.01 else "warn",
                                severity=2 if worst > 0.01 else 1, value=round(worst, 5), threshold=0.003,
                                message=f"{worst:.1%} of the figure is still key-coloured", remedy="reroll"))
    if block > 1.35:
        findings.append(Finding(metric="video_compression", level="warn", severity=1, value=round(block, 3),
                                threshold=1.35, message="visible compression blocks", remedy="reroll_1080p"))
    info["findings"] = [f.model_dump() for f in findings]
    return frames, info
