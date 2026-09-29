"""Pixel-art reconstruction (section 12).

AI pixel art is a high-resolution painting of pixels on a drifting, irregular grid. Recover the
true lattice per frame with a beat-tracking style DP, resample each cell by its dominant colour,
then quantize all frames jointly to one palette.
"""

from __future__ import annotations

import numpy as np

from ..color import from_oklab, hex_to_rgb, kmeans_pp, rgb_to_hex, to_oklab
from ..spec import Finding, SpriteSpec
from .register import Crop


# ---------------------------------------------------------------------------
# 12.1 Pitch estimation


def edge_profiles(rgba: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lab = to_oklab(rgba[..., :3])
    a = rgba[..., 3].astype(np.float32) / 255.0
    lab = lab * a[..., None]
    f = np.concatenate([lab, a[..., None] * 0.5], -1)
    opaque = a > 0.5
    dx = np.linalg.norm(f[:, 1:] - f[:, :-1], axis=-1) * (opaque[:, 1:] | opaque[:, :-1])
    dy = np.linalg.norm(f[1:] - f[:-1], axis=-1) * (opaque[1:] | opaque[:-1])
    return dx.sum(0), dy.sum(1)  # E_x(x): boundary between x and x+1


def pitch_curve(E: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Best-phase lattice score of each candidate pitch in `grid`, relative to the mean edge energy."""
    mean, n = E.mean(), len(E)
    out = np.zeros(len(grid))
    if mean <= 0 or n < 4:
        return out
    for k, p in enumerate(grid):
        best = 0.0
        for phi in np.arange(0.0, p, 0.5):
            idx = np.round(np.arange(phi, n - 1, p)).astype(int)
            idx = idx[idx < n]
            if len(idx):
                best = max(best, float(E[idx].mean()))
        out[k] = best / mean
    return out


def _near_best(grid: np.ndarray, curve: np.ndarray) -> float:
    top = float(curve.max())
    return float(grid[int(np.nonzero(curve >= 0.9 * top)[0][0])]) if top > 0 else float(grid[len(grid) // 2])


# ---------------------------------------------------------------------------
# 12.2 Lattice fit with drift


def fit_lattice(E: np.ndarray, p: float, lam: float = 4.0, tol: float = 0.3) -> list[float]:
    """Boundary positions maximizing edge energy with a spacing penalty (beat tracking DP).

    Boundary b means the edge between source pixels b-1 and b (E index b-1)."""
    n = len(E) + 1  # boundary positions 0..len(E)
    e = np.zeros(n)
    e[1:n] = E / max(E.mean(), 1e-9)
    lo, hi = max(1, int(np.floor((1 - tol) * p))), max(2, int(np.ceil((1 + tol) * p)))
    score = np.full(n, -np.inf)
    back = np.full(n, -1, int)
    start_span = int(np.ceil(hi))
    score[:start_span] = e[:start_span]
    for x in range(1, n):
        a, b = max(0, x - hi), x - lo
        if b < a:
            continue
        prev = np.arange(a, b + 1)
        cand = score[prev] - lam * ((x - prev - p) / p) ** 2
        k = int(np.argmax(cand))
        v = cand[k] + e[x]
        if v > score[x]:
            score[x] = v
            back[x] = prev[k]
    tail = np.arange(max(0, n - hi), n)
    x = int(tail[np.argmax(score[tail])])
    path = [x]
    while back[x] >= 0:
        x = int(back[x])
        path.append(x)
    path = path[::-1]
    # extend with the fitted pitch to cover partial cells at both ends
    local = float(np.median(np.diff(path))) if len(path) > 1 else p
    while path[0] - local > -0.5 * local and path[0] > 0:
        path.insert(0, path[0] - local)
    while path[-1] + local < n - 1 + 0.5 * local and path[-1] < n - 1:
        path.append(path[-1] + local)
    return [float(v) for v in path]


# ---------------------------------------------------------------------------
# 12.3 Cell colour


def cell_colors(rgba: np.ndarray, bx: list[float], by: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """Logical image (rows, cols, 4) from the lattice; colour = median of the largest tight group
    among the central 50% of each cell."""
    H, W = rgba.shape[:2]
    lab = to_oklab(rgba[..., :3])
    rows, cols = len(by) - 1, len(bx) - 1
    out = np.zeros((rows, cols, 4), np.uint8)
    out_lab = np.zeros((rows, cols, 3), np.float32)
    for r in range(rows):
        y0, y1 = by[r], by[r + 1]
        iy0 = int(np.clip(np.floor(y0 + 0.25 * (y1 - y0)), 0, H - 1))
        iy1 = int(np.clip(np.ceil(y1 - 0.25 * (y1 - y0)), iy0 + 1, H))
        for c in range(cols):
            x0, x1 = bx[c], bx[c + 1]
            ix0 = int(np.clip(np.floor(x0 + 0.25 * (x1 - x0)), 0, W - 1))
            ix1 = int(np.clip(np.ceil(x1 - 0.25 * (x1 - x0)), ix0 + 1, W))
            a = rgba[iy0:iy1, ix0:ix1, 3]
            if a.size == 0 or (a >= 128).mean() < 0.5:
                continue
            sel = a >= 128
            px = lab[iy0:iy1, ix0:ix1][sel]
            rgbp = rgba[iy0:iy1, ix0:ix1, :3][sel]
            if len(px) > 2:
                d = np.linalg.norm(px[:, None] - px[None], axis=-1)
                cnt = (d < 0.03).sum(1)
                k = int(np.argmax(cnt))
                grp = d[k] < 0.03
                col = np.median(rgbp[grp], 0)
                out_lab[r, c] = np.median(px[grp], 0)
            else:
                col = np.median(rgbp, 0)
                out_lab[r, c] = np.median(px, 0)
            out[r, c, :3] = np.round(col).astype(np.uint8)
            out[r, c, 3] = 255
    return out, out_lab


def redraw(logical: np.ndarray, bx: list[float], by: list[float], shape: tuple[int, int]) -> np.ndarray:
    H, W = shape
    xs = np.clip(np.searchsorted(np.array(bx), np.arange(W) + 0.5) - 1, 0, logical.shape[1] - 1)
    ys = np.clip(np.searchsorted(np.array(by), np.arange(H) + 0.5) - 1, 0, logical.shape[0] - 1)
    return logical[ys[:, None], xs[None, :]]


# ---------------------------------------------------------------------------
# 12.4 Joint palette with pinned centres


def joint_palette(pixels_lab: np.ndarray, weights: np.ndarray, k: int, pinned: np.ndarray | None,
                  iters: int = 20, seed: int = 0, merge: float = 0.02) -> np.ndarray:
    rng = np.random.default_rng(seed)
    pinned = pinned if pinned is not None and len(pinned) else np.zeros((0, 3), np.float32)
    free_k = max(0, k - len(pinned))
    if free_k and len(pixels_lab):
        if len(pinned):
            d2 = ((pixels_lab[:, None] - pinned[None]) ** 2).sum(-1).min(1)
            w = weights * np.maximum(d2, 1e-6)
            free = kmeans_pp(pixels_lab, min(free_k, len(pixels_lab)), rng, weights=w)
        else:
            free = kmeans_pp(pixels_lab, min(free_k, len(pixels_lab)), rng, weights=weights)
        centers = np.concatenate([pinned, free]).astype(np.float32)
    else:
        centers = pinned.astype(np.float32).copy()
    npin = len(pinned)
    for _ in range(iters):
        d = ((pixels_lab[:, None] - centers[None]) ** 2).sum(-1)
        lbl = d.argmin(1)
        moved = False
        for j in range(npin, len(centers)):
            sel = lbl == j
            if sel.any():
                c = (pixels_lab[sel] * weights[sel, None]).sum(0) / weights[sel].sum()
                if np.linalg.norm(c - centers[j]) > 1e-5:
                    moved = True
                centers[j] = c
        if not moved:
            break
    # merge centres closer than `merge` (never merging two pinned colours)
    keep = []
    for j in range(len(centers)):
        if any(np.linalg.norm(centers[j] - centers[i]) < merge and not (j < npin and i < npin) for i in keep):
            continue
        keep.append(j)
    return centers[keep]


def quantize(logical_lab: np.ndarray, alpha: np.ndarray, centers: np.ndarray) -> np.ndarray:
    d = ((logical_lab[..., None, :] - centers[None, None]) ** 2).sum(-1)
    idx = d.argmin(-1)
    return np.where(alpha, idx, -1)


# ---------------------------------------------------------------------------
# 12.5 Cleanup


_N8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def cleanup(idx: np.ndarray, centers: np.ndarray, outline: str) -> np.ndarray:
    idx = idx.copy()
    H, W = idx.shape
    pad = np.pad(idx, 1, constant_values=-1)
    # orphan pixels: all eight neighbours differ -> majority neighbour colour
    for y in range(H):
        for x in range(W):
            v = idx[y, x]
            if v < 0:
                continue
            nb = [pad[y + 1 + dy, x + 1 + dx] for dy, dx in _N8]
            if all(n != v for n in nb):
                opaque = [n for n in nb if n >= 0]
                if len(opaque) >= 6:
                    vals, cnt = np.unique(opaque, return_counts=True)
                    idx[y, x] = vals[np.argmax(cnt)]
    dark = int(np.argmin(centers[:, 0])) if len(centers) else -1
    if outline == "dark" and dark >= 0:
        opaque = idx >= 0
        p = np.pad(opaque, 1, constant_values=False)
        ring = opaque & ~(p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:])
        idx[ring] = dark
        idx = _pixel_perfect(idx, dark)
    elif outline == "selective" and len(centers):
        opaque = idx >= 0
        p = np.pad(opaque, 1, constant_values=False)
        ring = opaque & ~(p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:])
        for y, x in zip(*np.nonzero(ring)):
            c = centers[idx[y, x]].copy()
            c[0] *= 0.6
            idx[y, x] = int(((centers - c) ** 2).sum(-1).argmin())
    return idx


def _pixel_perfect(idx: np.ndarray, color: int) -> np.ndarray:
    """Remove the corner pixel of every three-pixel L in 1-px outlines (Aseprite's pixel-perfect)."""
    out = idx.copy()
    H, W = idx.shape
    o = np.pad(idx == color, 1, constant_values=False)
    for y in range(H):
        for x in range(W):
            if not o[y + 1, x + 1]:
                continue
            up, dn, lf, rt = o[y, x + 1], o[y + 2, x + 1], o[y + 1, x], o[y + 1, x + 2]
            for a, b, diag in ((up, rt, o[y, x + 2]), (up, lf, o[y, x]), (dn, rt, o[y + 2, x + 2]),
                               (dn, lf, o[y + 2, x])):
                others = int(up) + int(dn) + int(lf) + int(rt)
                if a and b and not diag and others == 2:
                    # an elbow: keep the stroke diagonal-connected, fill with an inner neighbour
                    nb = [idx[yy, xx] for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
                          if 0 <= yy < H and 0 <= xx < W and idx[yy, xx] != color]
                    inner = [v for v in nb if v >= 0]
                    out[y, x] = max(set(inner), key=inner.count) if len(inner) >= 1 and len(nb) >= 2 else out[y, x]
                    break
    return out


# ---------------------------------------------------------------------------
# Driver


def reconstruct(crops: list[Crop], spec: SpriteSpec, reference_palette: list[str] | None = None,
                ) -> tuple[list[Crop], dict, list[Finding]]:
    logicals, labs, info_frames, origins = [], [], [], []
    target_h = spec.style.pixel_height or 48
    # the logical height names the standing character; crouched frames are shorter, so the pitch
    # prior comes from the tall end of the frame heights, not each frame's own height
    heights = [max(1, int((c.rgba[..., 3] >= 128).any(1).sum())) for c in crops]
    stand_h = float(np.percentile(heights, 75)) if heights else 1.0
    p0 = stand_h / target_h
    profiles = [edge_profiles(c.rgba) for c in crops]
    joint, curves, grid = None, [], None
    if p0 >= 1.3:
        # P12: one sheet is drawn at one pitch. Frames with large flat areas (machines, vehicles) give
        # weak, scattered per-frame estimates, so the pitch is chosen jointly; a frame keeps its own
        # pitch only when it clearly prefers it: its score at the joint pitch under 60 % of its best. A
        # frame truly drawn at another pitch loses phase within a few cells and scores ~0.3-0.5 there;
        # flat panels make false peaks elsewhere but still score 0.7-0.9 at the true pitch.
        grid = np.arange(max(1.5, 0.6 * p0), max(0.6 * p0 + 0.05, 1.6 * p0), 0.05)
        for ex, ey in profiles:
            cx, cy = pitch_curve(ex, grid), pitch_curve(ey, grid)
            curves.append((cx, cy))
        total = sum(((cx / max(cx.max(), 1e-9)) + (cy / max(cy.max(), 1e-9))) for cx, cy in curves)
        joint = _near_best(grid, total)
    for i, c in enumerate(crops):
        rgba = c.rgba
        H, W = rgba.shape[:2]
        ex, ey = profiles[i]
        if p0 < 1.3:
            # already at native resolution (Retro Diffusion, true pixel art): one cell per pixel
            p, px, py = 1.0, 1.0, 1.0
            bx = [float(v) for v in range(W + 1)]
            by = [float(v) for v in range(H + 1)]
        else:
            cx, cy = curves[i]
            px, py = _near_best(grid, cx), _near_best(grid, cy)
            both = cx + cy
            kj = int(np.argmin(np.abs(grid - joint)))
            p = joint if both[kj] >= 0.6 * both.max() else _near_best(grid, both)
            bx = fit_lattice(ex, p)
            by = fit_lattice(ey, p)
        logical, lab = cell_colors(rgba, bx, by)
        re = redraw(logical, bx, by, (H, W))
        opaque = rgba[..., 3] >= 128
        err = float(np.linalg.norm(to_oklab(re[..., :3])[opaque] - to_oklab(rgba[..., :3])[opaque], axis=-1).mean()) \
            if opaque.any() else 0.0
        logicals.append(logical)
        labs.append(lab)
        origins.append((c.offset[0] + bx[0], c.offset[1] + by[0]))
        info_frames.append({"pitch": round(p, 3), "pitch_x": round(px, 3), "pitch_y": round(py, 3),
                            "cols": logical.shape[1], "rows": logical.shape[0], "grid_error": round(err, 4),
                            "colors_before": int(len(np.unique(rgba[opaque][:, :3], axis=0))) if opaque.any() else 0})

    # 12.4 joint palette over all frames
    all_lab = np.concatenate([l[g[..., 3] > 0] for l, g in zip(labs, logicals)]) if logicals else np.zeros((0, 3))
    uniq, inv, counts = np.unique(np.round(all_lab, 3), axis=0, return_inverse=True, return_counts=True)
    pinned = None
    if reference_palette:
        pinned = to_oklab(np.array([hex_to_rgb(h) for h in reference_palette], np.uint8))[: spec.style.palette_size]
    centers = joint_palette(uniq.astype(np.float32), counts.astype(np.float64), spec.style.palette_size, pinned)
    pal_rgb = from_oklab(centers)

    pitches = np.array([f["pitch"] for f in info_frames])
    p_mean = float(pitches.mean()) if len(pitches) else 1.0
    out, idx_frames = [], []
    for c, logical, lab, org in zip(crops, logicals, labs, origins):
        idx = quantize(lab, logical[..., 3] > 0, centers)
        idx = cleanup(idx, centers, spec.style.outline)
        img = np.zeros(idx.shape + (4,), np.uint8)
        m = idx >= 0
        img[m, :3] = pal_rgb[idx[m]]
        img[m, 3] = 255
        idx_frames.append(idx)
        # registration works in logical pixels from here on: convert sheet positions too
        off = (int(round(org[0] / p_mean)), int(round(org[1] / p_mean)))
        region = tuple(int(round(v / p_mean)) for v in c.region)
        out.append(Crop(img, off, region, c.airborne, c.squash))
    cv = float(pitches.std() / max(pitches.mean(), 1e-9)) if len(pitches) else 0.0
    errs = [f["grid_error"] for f in info_frames]
    info = {"frames": info_frames, "pitch": round(float(np.mean(pitches)), 3) if len(pitches) else None,
            "pitch_cv": round(cv, 4), "grid_error": round(float(np.mean(errs)), 4) if errs else None,
            "palette": [rgb_to_hex(c) for c in pal_rgb], "colors_after": int(len(centers)),
            "colors_before": int(max((f["colors_before"] for f in info_frames), default=0))}
    findings = []
    if cv > 0.10:
        findings.append(Finding(metric="pixel_fidelity", level="fail", severity=2, value=round(cv, 4), threshold=0.10,
                                message=f"pixel pitch varies {cv:.1%} across frames", remedy="pixel_refit"))
    elif cv > 0.05:
        findings.append(Finding(metric="pixel_fidelity", level="warn", severity=1, value=round(cv, 4), threshold=0.05,
                                message=f"pixel pitch varies {cv:.1%} across frames", remedy="pixel_refit"))
    if info["grid_error"] and info["grid_error"] > 0.06:
        findings.append(Finding(metric="pixel_fidelity", level="warn", severity=1, value=info["grid_error"],
                                threshold=0.06, message="source does not sit on a clean pixel grid",
                                remedy="pixel_refit"))
    return out, info, findings


def _regular_lattice(E: np.ndarray, p: float) -> list[float]:
    n = len(E)
    best, phi = -1.0, 0.0
    for ph in np.arange(0.0, p, 0.25):
        idx = np.round(np.arange(ph, n - 1, p)).astype(int)
        idx = idx[(idx >= 0) & (idx < n)]
        s = float(E[idx].mean()) if len(idx) else 0.0
        if s > best:
            best, phi = s, ph
    b = [float(v) + 1 for v in np.arange(phi, n, p)]  # boundary after source pixel idx
    b = [v for v in b if 0 < v < n + 1]
    return [0.0] + b + ([float(n + 1)] if not b or b[-1] < n + 1 else [])


def to_logical(rgba: np.ndarray, pitch: float, palette_hex: list[str] | None = None) -> np.ndarray:
    """A single source-resolution frame (an in-between, R12) brought onto the sheet's lattice: fitted
    at the sheet's known pitch and snapped to its palette, so it matches the reconstructed frames."""
    if pitch < 1.3:
        return rgba
    ex, ey = edge_profiles(rgba)
    # a regular lattice at the known pitch, only the phase fitted: the cell was resampled, and on soft
    # edges the drift DP packs boundaries at its minimum spacing and inflates the frame
    logical, lab = cell_colors(rgba, _regular_lattice(ex, pitch), _regular_lattice(ey, pitch))
    if palette_hex:
        centers = to_oklab(np.array([hex_to_rgb(h) for h in palette_hex], np.uint8))
        on = logical[..., 3] > 0
        idx = quantize(lab, on, centers)
        rgb = np.array([hex_to_rgb(h) for h in palette_hex], np.uint8)
        logical[on, :3] = rgb[idx[on]]
    return logical
