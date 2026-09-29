"""Frame registration and normalization (section 11).

Frames are aligned on the parts that should not move (ground contact and torso core), never
on bounding boxes; the anchor trajectory is then split into intended motion plus jitter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import embed
from ..color import linear_to_srgb, srgb_to_linear
from ..spec import Finding


@dataclass
class Crop:
    """One extracted frame: its RGBA crop and where it sat on the sheet."""

    rgba: np.ndarray
    offset: tuple[int, int]  # crop top-left in sheet coordinates
    region: tuple[int, int, int, int]
    airborne: bool = False
    squash: bool = False


@dataclass
class Aligned:
    frames: list[np.ndarray]  # RGBA, all the same size
    canvas: tuple[int, int]  # (W, H)
    pivot: tuple[float, float]  # normalized
    placements: list[tuple[int, int]]  # crop top-left on the canvas
    anchors: list[tuple[float, float]]  # crop-local (torso x, ground y)
    scales: list[float]
    flipped: list[bool]
    airborne: list[bool]
    sprite_h: float
    root_motion: list[float] | None = None
    findings: list[Finding] = field(default_factory=list)
    info: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 11.1 Anchors


def anchor_of(mask: np.ndarray, top_down: bool = False) -> tuple[float, int]:
    """Torso x (depth-filtered core band) and robust ground line of one frame mask."""
    rows = mask.sum(axis=1)
    nz = rows[rows > 0]
    if len(nz) == 0:
        h, w = mask.shape
        return w / 2, h - 1
    med = np.median(nz)
    yg = int(np.nonzero(rows >= 0.15 * med)[0].max())
    top = int(np.nonzero(rows)[0].min())
    h = max(1, yg - top)
    dt = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    if top_down:
        ys, xs = np.nonzero(dt >= 0.3 * dt.max())
        wts = dt[ys, xs]
        return float((xs * wts).sum() / wts.sum()), yg
    y0, y1 = int(top + 0.15 * h), max(int(top + 0.55 * h), int(top + 0.15 * h) + 1)
    band = dt[y0:y1]
    if band.max() <= 0:
        xs = np.nonzero(mask[y0:y1].any(0))[0]
        return float(np.median(xs)) if len(xs) else mask.shape[1] / 2, yg
    _, xs = np.nonzero(band >= 0.3 * band.max())
    return float(np.median(xs)), yg


def core_band(mask: np.ndarray) -> tuple[int, int]:
    rows = mask.sum(axis=1)
    ys = np.nonzero(rows)[0]
    if len(ys) == 0:
        return 0, mask.shape[0]
    top, bot = int(ys.min()), int(ys.max())
    h = bot - top
    return int(top + 0.0 * h), int(top + 0.55 * h) + 1


# ---------------------------------------------------------------------------
# Resampling in premultiplied linear light (R9)


def resize_rgba(rgba: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    w, h = size
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    lin = srgb_to_linear(rgba[..., :3]) * a
    pre = np.concatenate([lin, a], -1)
    out = cv2.resize(pre, (w, h), interpolation=cv2.INTER_LANCZOS4)
    out = np.clip(out, 0, 1)
    oa = out[..., 3:4]
    rgb = np.where(oa > 1e-4, out[..., :3] / np.maximum(oa, 1e-4), 0)
    res = np.concatenate([linear_to_srgb(rgb) * 255, oa * 255], -1)
    res = np.round(res).astype(np.uint8)
    res[res[..., 3] == 0] = 0
    return res


# ---------------------------------------------------------------------------
# 11.2 Registration refinement


def _edges(gray: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return np.hypot(gx, gy)


def _subpixel(v: np.ndarray, i: int) -> float:
    if 0 < i < len(v) - 1:
        a, b, c = v[i - 1], v[i], v[i + 1]
        d = a - 2 * b + c
        if abs(d) > 1e-9:
            return float(i + 0.5 * (a - c) / d)
    return float(i)


def match_pairs(maps: list[np.ndarray], bands: list[tuple[int, int]], pairs: list[tuple[int, int]],
                search: int) -> list[tuple[int, int, float, float, float]]:
    """Translation of frame j's core band relative to frame i by NCC of edge maps (2× downscaled)."""
    out = []
    s = 2
    for i, j in pairs:
        y0, y1 = bands[i]
        tmpl = maps[i][y0:y1]
        if tmpl.size == 0 or tmpl.std() < 1e-6:
            continue
        tsm = cv2.resize(tmpl, (max(1, tmpl.shape[1] // s), max(1, tmpl.shape[0] // s)), interpolation=cv2.INTER_AREA)
        pad = search
        big = cv2.copyMakeBorder(maps[j], pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
        region = big[y0:y1 + 2 * pad]
        rsm = cv2.resize(region, (max(1, region.shape[1] // s), max(1, region.shape[0] // s)), interpolation=cv2.INTER_AREA)
        if rsm.shape[0] < tsm.shape[0] or rsm.shape[1] < tsm.shape[1]:
            continue
        res = cv2.matchTemplate(rsm, tsm, cv2.TM_CCOEFF_NORMED)
        _, peak, _, (px, py) = cv2.minMaxLoc(res)
        sx = _subpixel(res[py], px)
        sy = _subpixel(res[:, px], py)
        dx = sx * s - pad
        dy = sy * s - pad
        out.append((i, j, dx, dy, max(0.05, float(peak))))
    return out


def solve_offsets(n: int, matches, fixed: int = 0) -> np.ndarray:
    """Weighted least squares for all offsets jointly, with frame `fixed` at zero."""
    t = np.zeros((n, 2))
    if not matches:
        return t
    rows, rhs, w = [], [], []
    for i, j, dx, dy, wt in matches:
        r = np.zeros(n)
        r[j], r[i] = 1, -1
        rows.append(r)
        rhs.append((dx, dy))
        w.append(wt)
    r = np.zeros(n)
    r[fixed] = 1
    rows.append(r)
    rhs.append((0.0, 0.0))
    w.append(100.0)
    A = np.array(rows) * np.sqrt(np.array(w))[:, None]
    B = np.array(rhs) * np.sqrt(np.array(w))[:, None]
    sol, *_ = np.linalg.lstsq(A, B, rcond=None)
    return sol


# ---------------------------------------------------------------------------
# 11.3 Trajectory smoothing


def fourier_fit(y: np.ndarray, harmonics: list[int]) -> np.ndarray:
    n = len(y)
    i = np.arange(n)
    cols = [np.ones(n)]
    for h in harmonics:
        if 2 * h < n or (2 * h == n):
            cols += [np.cos(2 * np.pi * h * i / n), np.sin(2 * np.pi * h * i / n)]
    X = np.stack(cols, 1)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return X @ coef


def smooth_nonloop(x: np.ndarray) -> np.ndarray:
    if len(x) >= 5:
        from scipy.signal import savgol_filter  # imported here: scipy.signal adds ~0.35 s to every CLI start

        return savgol_filter(x, 5, 2, mode="interp")
    return x.copy()


# ---------------------------------------------------------------------------
# 11.4 Scale


def scale_measures(mask: np.ndarray) -> tuple[float, float, float]:
    rows = mask.sum(1)
    ys = np.nonzero(rows)[0]
    if len(ys) < 4:
        return 0.0, 0.0, 0.0
    top, bot = int(ys.min()), int(ys.max())
    h = bot - top + 1
    head_rows = rows[top: top + max(1, int(0.12 * h))]
    head_w = float(head_rows.max())
    dt = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    y0, y1 = int(top + 0.15 * h), int(top + 0.65 * h) + 1
    band_max = dt[y0:y1].max(1)
    torso = 2.0 * float(band_max.max()) if len(band_max) else 0.0
    thick = np.nonzero(band_max >= 0.5 * band_max.max())[0] if len(band_max) else []
    hip = y0 + int(thick.max()) if len(thick) else y1
    head_hip = float(hip - top)
    return head_w, torso, head_hip


# ---------------------------------------------------------------------------
# Registration driver


def register(crops: list[Crop], *, loop: bool, harmonics: list[int] | None = None, pixel: bool = False,
             top_down: bool = False, reference: np.ndarray | None = None, mirrorable: bool = True,
             root_motion: bool = False, correct_scale: bool = True, mode: str = "character",
             expected: list[tuple[float, float]] | None = None) -> Aligned:
    if mode in ("rigid", "effect"):
        return register_rigid(crops, mode=mode, expected=expected, pixel=pixel, reference=reference,
                              mirrorable=mirrorable, loop=loop)
    n = len(crops)
    findings: list[Finding] = []
    masks = [c.rgba[..., 3] >= 128 for c in crops]
    anchors = [anchor_of(m, top_down) for m in masks]
    tops = [int(np.nonzero(m.any(1))[0].min()) if m.any() else 0 for m in masks]
    heights = [a[1] - t + 1 for a, t in zip(anchors, tops)]
    grounded_idx = [i for i in range(n) if not crops[i].airborne] or list(range(n))
    sprite_h = float(np.median([heights[i] for i in grounded_idx]))

    # sheet-space ground line per row band (median of grounded frames sharing the band)
    sheet_ground = [crops[i].offset[1] + anchors[i][1] for i in range(n)]
    row_of = _row_bands([c.region for c in crops])
    row_ground = {}
    for r in set(row_of):
        g = [sheet_ground[i] - crops[i].region[1] for i in range(n) if row_of[i] == r and not crops[i].airborne]
        row_ground[r] = float(np.median(g)) if g else None
    all_g = [v for v in row_ground.values() if v is not None]
    for r in row_ground:
        if row_ground[r] is None:
            row_ground[r] = float(np.median(all_g)) if all_g else 0.0
    # vertical offset of each frame's ground above the row's shared ground line (preserved for airborne)
    lift = [(crops[i].region[1] + row_ground[row_of[i]]) - sheet_ground[i] for i in range(n)]
    raw_baseline = np.array([lift[i] for i in range(n) if not crops[i].airborne])

    # 11.5 facing: compare with the reference and with neighbouring frames, and with their mirrors
    flipped = [False] * n
    facing_scores = _facing_scores(crops, reference, loop)
    for i, s in enumerate(facing_scores):
        if s > 0.05:
            if mirrorable:
                flipped[i] = True
            else:
                findings.append(Finding(metric="facing", level="fail", severity=3, frames=[i], value=round(s, 3),
                                        threshold=0.05, message=f"frame {i + 1} faces the wrong way",
                                        remedy="frame_repair"))
    if any(flipped):
        for i in range(n):
            if flipped[i]:
                c = crops[i]
                crops[i] = Crop(np.ascontiguousarray(c.rgba[:, ::-1]), c.offset, c.region, c.airborne, c.squash)
                masks[i] = masks[i][:, ::-1]
                anchors[i] = (c.rgba.shape[1] - 1 - anchors[i][0], anchors[i][1])
        findings.append(Finding(metric="facing", level="warn", severity=1, frames=[i for i in range(n) if flipped[i]],
                                message="flipped frames that faced the wrong way", remedy="auto_fixed",
                                auto_fixed=True))

    # 11.4 scale (HD only; pixel frames normalize through the lattice fit)
    scales = [1.0] * n
    meas = np.array([scale_measures(m) for m in masks])
    # the measures assume an upright body: frames wider than tall (lying, sprawled) are left out
    upright = np.array([(lambda ys, xs: (xs.max() - xs.min()) <= (ys.max() - ys.min()) * 1.1)
                        (*np.nonzero(m)) if m.any() else False for m in masks])
    ref_meas = np.median(meas[upright], 0) if upright.sum() >= 2 else np.median(meas, 0)
    ratios = meas / np.maximum(ref_meas, 1e-6)
    est = np.median(ratios, 1)
    est_u = est[upright] if upright.sum() >= 2 else est
    scale_cv = float(np.std(est_u) / max(np.mean(est_u), 1e-6))
    if not pixel and correct_scale:
        for i in range(n):
            spread = float(ratios[i].max() - ratios[i].min())
            same_side = bool(np.all(ratios[i] > 1.02) or np.all(ratios[i] < 0.98))
            if upright[i] and abs(est[i] - 1) > 0.03 and spread < 0.04 and same_side and not crops[i].squash:
                s = float(est[i])
                c = crops[i]
                h, w = c.rgba.shape[:2]
                nw, nh = max(1, int(round(w / s))), max(1, int(round(h / s)))
                crops[i] = Crop(resize_rgba(c.rgba, (nw, nh)), c.offset, c.region, c.airborne, c.squash)
                masks[i] = crops[i].rgba[..., 3] >= 128
                anchors[i] = anchor_of(masks[i], top_down)
                scales[i] = s
        if any(s != 1.0 for s in scales):
            findings.append(Finding(metric="scale", level="warn", severity=1,
                                    frames=[i for i in range(n) if scales[i] != 1.0], value=round(scale_cv, 4),
                                    message="corrected per-frame scale drift", remedy="auto_fixed", auto_fixed=True))
    if scale_cv > 0.08:
        findings.append(Finding(metric="scale", level="fail", severity=2, value=round(scale_cv, 4), threshold=0.08,
                                message=f"frame scale varies by {scale_cv:.1%}", remedy="frame_repair"))

    # heuristic placement: torso x at 0, ground at 0 (airborne frames keep their lift)
    place = np.array([[-anchors[i][0], -anchors[i][1] - (lift[i] if crops[i].airborne else 0.0)]
                      for i in range(n)])

    # 11.2 refinement by edge-map NCC on the core band
    S = int(np.ceil(max(max(c.rgba.shape[:2]) for c in crops) * 1.6)) + 8
    origin = np.array([S / 2, S * 0.8])
    maps, bands = [], []
    for i in range(n):
        g = np.zeros((S, S), np.float32)
        gray = cv2.cvtColor(crops[i].rgba[..., :3], cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        alpha = crops[i].rgba[..., 3].astype(np.float32) / 255.0
        img = gray * alpha
        x, y = (origin + place[i]).round().astype(int)
        h, w = img.shape
        xa, ya = max(0, x), max(0, y)
        xb, yb = min(S, x + w), min(S, y + h)
        if xb > xa and yb > ya:
            g[ya:yb, xa:xb] = img[ya - y:yb - y, xa - x:xb - x]
        e = _edges(g)
        maps.append(e)
        m = np.zeros((S, S), bool)
        if xb > xa and yb > ya:
            m[ya:yb, xa:xb] = masks[i][ya - y:yb - y, xa - x:xb - x]
        y0, y1 = core_band(m)
        bands.append((y0, y1))
    pairs = [(i, i + 1) for i in range(n - 1)] + [(i, i + 2) for i in range(n - 2)]
    if loop and n > 2:
        pairs.append((n - 1, 0))
    search = max(2, int(0.10 * sprite_h))
    matches = match_pairs(maps, bands, pairs, search)
    t = solve_offsets(n, matches)

    # 11.3 horizontal trajectory: in-place loops hold x constant; one-shots keep smoothed arcs
    tx = t[:, 0]
    if loop:
        target = np.full(n, tx.mean())
    else:
        target = smooth_nonloop(tx)
    corr = target - tx
    limit = 0.04 * sprite_h
    big = [i for i in range(n) if abs(corr[i]) > limit]
    corr = np.clip(corr, -limit, limit)
    if big:
        findings.append(Finding(metric="registration", level="warn", severity=2, frames=big,
                                value=round(float(np.max(np.abs(target - tx)) / sprite_h), 4), threshold=0.04,
                                message="frames need a horizontal correction above 4% of height; likely a wrong pose",
                                remedy="frame_repair"))
    place[:, 0] += corr
    # vertical: grounded frames are ground-locked; bob comes from the drawing itself
    ty = t[:, 1]
    bob_fit = fourier_fit(ty, harmonics or [2, 4]) if loop and n >= 4 else smooth_nonloop(ty)
    resid = ty - bob_fit
    jitter = [i for i in range(n) if abs(resid[i]) > 0.04 * sprite_h and not crops[i].airborne]
    if jitter:
        findings.append(Finding(metric="registration", level="warn", severity=1, frames=jitter,
                                value=round(float(np.max(np.abs(resid)) / sprite_h), 4), threshold=0.04,
                                message="body height jumps between frames beyond the smooth bob", remedy="frame_repair"))

    # baseline: raw ground placement jitter before alignment
    base_std = float(raw_baseline.std() / sprite_h) if len(raw_baseline) > 1 else 0.0
    if base_std > 0.015:
        findings.append(Finding(metric="baseline", level="warn", severity=1, value=round(base_std, 4), threshold=0.015,
                                message=f"ground line varied by {base_std:.1%} of height; realigned", remedy="auto_fixed",
                                auto_fixed=True))
    elif base_std > 0.005:
        findings.append(Finding(metric="baseline", level="info", severity=1, value=round(base_std, 4), threshold=0.005,
                                message=f"ground line varied by {base_std:.1%} of height; realigned",
                                remedy="auto_fixed", auto_fixed=True))

    # 11.6 common canvas and pivot
    pad = 2 if pixel else int(np.ceil(0.04 * sprite_h))
    boxes = []
    for i in range(n):
        h, w = crops[i].rgba.shape[:2]
        x0, y0 = place[i]
        boxes.append((x0, y0, x0 + w, y0 + h))
    bx = np.array(boxes)
    minx, miny = np.floor(bx[:, 0].min()) - pad, np.floor(bx[:, 1].min()) - pad
    maxx, maxy = np.ceil(bx[:, 2].max()) + pad, np.ceil(bx[:, 3].max()) + pad
    W = int(np.ceil((maxx - minx) / 4) * 4)
    H = int(np.ceil((maxy - miny) / 4) * 4)
    frames, placements = [], []
    for i in range(n):
        px, py = int(round(place[i][0] - minx)), int(round(place[i][1] - miny))
        canvas = np.zeros((H, W, 4), np.uint8)
        h, w = crops[i].rgba.shape[:2]
        canvas[py:py + h, px:px + w] = crops[i].rgba[: H - py, : W - px]
        frames.append(canvas)
        placements.append((px, py))
    pivot = (float(-minx / W), float(-miny / H))

    # post-alignment checks
    post_ground = []
    torso_x = []
    for i in range(n):
        m = frames[i][..., 3] >= 128
        ax, ay = anchor_of(m, top_down)
        if not crops[i].airborne:
            post_ground.append(ay)
        torso_x.append(ax)
    post_std = float(np.std(post_ground) / sprite_h) if len(post_ground) > 1 else 0.0
    torso_std = float(np.std(torso_x) / sprite_h)
    rm = None
    if root_motion:
        rm = [float(crops[i].offset[0] + anchors[i][0] - crops[i].region[0]) for i in range(n)]
        rm = [v - rm[0] for v in rm]
    info = {"sprite_h": round(sprite_h, 2), "baseline_raw_std": round(base_std, 5),
            "baseline_post_std": round(post_std, 5), "torso_x_std": round(torso_std, 5), "scale_cv": round(scale_cv, 5),
            "facing_scores": [round(s, 4) for s in facing_scores], "matches": len(matches),
            "tx": [round(float(v), 2) for v in tx], "ty": [round(float(v), 2) for v in ty],
            "bob_fit": [round(float(v), 2) for v in bob_fit], "lift": [round(float(v), 2) for v in lift]}
    return Aligned(frames, (W, H), pivot, placements, anchors, scales, flipped, [c.airborne for c in crops],
                   sprite_h, rm, findings, info)


def register_rigid(crops: list[Crop], *, mode: str, expected: list[tuple[float, float]] | None = None,
                   pixel: bool = False, reference: np.ndarray | None = None, mirrorable: bool = True,
                   loop: bool = False) -> Aligned:
    """Vehicles and machines (mode "rigid"): ground contact and centroid x, with the choreography's
    intended offsets (recoil, jolts) re-applied, so jitter goes and motion stays (K9). Effects (mode
    "effect"): alpha-weighted centroid, never ground-locked (K2). No body-specific scale correction."""
    n = len(crops)
    findings: list[Finding] = []
    alphas = [c.rgba[..., 3].astype(np.float32) / 255.0 for c in crops]
    masks = [a >= 0.5 for a in alphas]
    anchors: list[tuple[float, float]] = []
    heights = []
    for a, m in zip(alphas, masks):
        ys, xs = np.nonzero(a > 0.05)
        if len(ys) == 0:
            anchors.append((a.shape[1] / 2, a.shape[0] / 2))
            heights.append(1)
            continue
        w = a[ys, xs]
        cx = float((xs * w).sum() / w.sum())
        if mode == "effect":
            anchors.append((cx, float((ys * w).sum() / w.sum())))
        else:
            anchors.append((cx, float(anchor_of(m)[1]) if m.any() else float(ys.max())))
        heights.append(int(ys.max() - ys.min() + 1))
    sprite_h = float(np.median(heights)) if heights else 1.0
    flipped = [False] * n
    if mode == "rigid":
        for i, sc in enumerate(_facing_scores(crops, reference, loop)):
            if sc > 0.05 and mirrorable:
                c = crops[i]
                crops[i] = Crop(np.ascontiguousarray(c.rgba[:, ::-1]), c.offset, c.region, c.airborne, c.squash)
                anchors[i] = (c.rgba.shape[1] - 1 - anchors[i][0], anchors[i][1])
                flipped[i] = True
        if any(flipped):
            findings.append(Finding(metric="facing", level="warn", severity=1, frames=[i for i in range(n) if flipped[i]],
                                    message="flipped frames that faced the wrong way", remedy="auto_fixed",
                                    auto_fixed=True))
    exp = expected or [(0.0, 0.0)] * n  # fractions of the subject's height, forward = +x
    ex = [(a * sprite_h, b * sprite_h) for a, b in exp]
    place = np.array([[-anchors[i][0] + ex[i][0], -anchors[i][1] - (ex[i][1] if mode == "effect" else 0.0)]
                      for i in range(n)])
    # raw placement jitter relative to each frame's own cell, for the baseline finding
    base = np.array([crops[i].offset[1] + anchors[i][1] - crops[i].region[1] for i in range(n)])
    base_std = float(base.std() / max(sprite_h, 1.0)) if n > 1 and mode == "rigid" else 0.0
    if base_std > 0.015:
        findings.append(Finding(metric="baseline", level="warn", severity=1, value=round(base_std, 4), threshold=0.015,
                                message=f"ground line varied by {base_std:.1%} of height; realigned",
                                remedy="auto_fixed", auto_fixed=True))
    pad = 2 if pixel else int(np.ceil(0.04 * sprite_h))
    bx = np.array([(place[i][0], place[i][1], place[i][0] + crops[i].rgba.shape[1],
                    place[i][1] + crops[i].rgba.shape[0]) for i in range(n)])
    minx, miny = np.floor(bx[:, 0].min()) - pad, np.floor(bx[:, 1].min()) - pad
    maxx, maxy = np.ceil(bx[:, 2].max()) + pad, np.ceil(bx[:, 3].max()) + pad
    W = int(np.ceil((maxx - minx) / 4) * 4)
    H = int(np.ceil((maxy - miny) / 4) * 4)
    frames, placements = [], []
    for i in range(n):
        px, py = int(round(place[i][0] - minx)), int(round(place[i][1] - miny))
        canvas = np.zeros((H, W, 4), np.uint8)
        h, w = crops[i].rgba.shape[:2]
        canvas[py:py + h, px:px + w] = crops[i].rgba[: H - py, : W - px]
        frames.append(canvas)
        placements.append((px, py))
    pivot = (float(-minx / W), float(-miny / H))
    lift = [0.0] * n
    info = {"sprite_h": round(sprite_h, 2), "mode": mode, "baseline_raw_std": round(base_std, 5),
            "baseline_post_std": 0.0, "torso_x_std": 0.0, "scale_cv": 0.0, "lift": lift,
            "expected": [[round(a, 2), round(b, 2)] for a, b in exp]}
    return Aligned(frames, (W, H), pivot, placements, anchors, [1.0] * n, flipped, [False] * n, sprite_h, None,
                   findings, info)


def _row_bands(regions: list[tuple[int, int, int, int]]) -> list[int]:
    centers = [(r[1] + r[3]) / 2 for r in regions]
    heights = [r[3] - r[1] for r in regions]
    order = np.argsort(centers)
    rows = [0] * len(regions)
    cur = 0
    for a, b in zip(order[:-1], order[1:]):
        rows[a] = cur
        if centers[b] - centers[a] > 0.5 * np.median(heights):
            cur += 1
    if len(order):
        rows[order[-1]] = cur
    return rows


def _facing_scores(crops: list[Crop], reference: np.ndarray | None, loop: bool) -> list[float]:
    """Positive score: the frame's mirror image matches the reference and neighbours better."""
    n = len(crops)
    if n == 0:
        return []
    E = [embed.embed_spatial(_square(c.rgba)) for c in crops]
    M = [embed.embed_spatial(_square(c.rgba[:, ::-1])) for c in crops]
    R = embed.embed_spatial(_square(reference)) if reference is not None else None
    scores = []
    for i in range(n):
        terms = []
        if R is not None:
            terms.append(embed.cosine(M[i], R) - embed.cosine(E[i], R))
        js = [(i - 1) % n, (i + 1) % n] if loop else [j for j in (i - 1, i + 1) if 0 <= j < n]
        js = [j for j in dict.fromkeys(js) if j != i]
        if js:
            terms.append(float(np.mean([embed.cosine(M[i], E[j]) - embed.cosine(E[i], E[j]) for j in js])))
        scores.append(float(np.mean(terms)) if terms else 0.0)
    return scores


def _square(rgba: np.ndarray) -> np.ndarray:
    h, w = rgba.shape[:2]
    s = max(h, w)
    out = np.zeros((s, s, 4), np.uint8)
    out[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = rgba
    return out
