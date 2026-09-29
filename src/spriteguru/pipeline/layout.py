"""Layout detection and frame segmentation (section 10).

Three detectors (grid fit, seam cuts, component grouping) each propose frame regions; the
hypothesis that best explains the mask under the expected frame count wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy import ndimage

from ..spec import Finding

W_COUNT, W_CUT, W_AREA, W_HEIGHT, W_CONTAIN, W_ORPHAN = 3.0, 1.0, 0.5, 0.5, 1.0, 2.0


@dataclass
class Prior:
    """What the spec and guide say the sheet should contain."""

    n: int | None = None
    rows: int | None = None
    cols: int | None = None
    cells: list[tuple[int, int, int, int]] | None = None  # expected frame cells, reading order
    exclude: list[tuple[int, int, int, int]] = field(default_factory=list)  # e.g. the reference strip
    detached: bool = False  # effects: small detached marks inside a cell are sparks, not strays (K4)
    forced: bool = False  # confirmed by the user: `cells` are the layout, not a hint (L16)


@dataclass
class FrameCut:
    index: int
    region: tuple[int, int, int, int]  # bbox of the region the frame was cut from
    box: tuple[int, int, int, int]  # tight foreground bbox, sheet coordinates
    label: int  # id in Layout.labels
    clipped: bool = False
    touching: bool = False


@dataclass
class Hypothesis:
    name: str
    labels: np.ndarray  # int32 region ids (reading order, 1-based); 0 = none
    count: int
    cuts: list[tuple[np.ndarray, np.ndarray]] = field(default_factory=list)  # (ys, xs) of each cut path
    orphan: np.ndarray | None = None
    score: float = 0.0
    terms: dict = field(default_factory=dict)


@dataclass
class Layout:
    frames: list[FrameCut]
    labels: np.ndarray
    hypothesis: str
    confidence: float
    scores: dict
    clean_mask: np.ndarray
    lines: dict
    labels_removed: int
    strays: int
    findings: list[Finding]

    def to_dict(self) -> dict:
        return {"hypothesis": self.hypothesis, "confidence": round(self.confidence, 4), "scores": self.scores,
                "lines": self.lines, "labels_removed": self.labels_removed, "strays": self.strays,
                "frames": [{"index": f.index, "region": f.region, "box": f.box, "clipped": f.clipped,
                            "touching": f.touching} for f in self.frames]}


# ---------------------------------------------------------------------------
# 10.1 Remove non-sprite elements


def find_lines(mask: np.ndarray) -> tuple[np.ndarray, dict]:
    H, W = mask.shape
    m8 = mask.astype(np.uint8)
    lines = np.zeros_like(mask)
    found = {"h": [], "v": []}
    Lh, Lv = max(3, int(0.25 * W)), max(3, int(0.25 * H))
    horiz = cv2.morphologyEx(m8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (Lh, 1))).astype(bool)
    vert = cv2.morphologyEx(m8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, Lv))).astype(bool)
    for arr, axis, span, thick_max, key in ((horiz, 1, W, 0.01 * H, "h"), (vert, 0, H, 0.01 * W, "v")):
        lbl, n = ndimage.label(arr)
        for sl_i, sl in enumerate(ndimage.find_objects(lbl), start=1):
            if sl is None:
                continue
            ys, xs = sl
            length = (xs.stop - xs.start) if axis == 1 else (ys.stop - ys.start)
            thick = (ys.stop - ys.start) if axis == 1 else (xs.stop - xs.start)
            if length > 0.6 * span and thick <= max(2, thick_max):
                lines[sl] |= lbl[sl] == sl_i
                found[key].append(int((ys.start + ys.stop) // 2) if axis == 1 else int((xs.start + xs.stop) // 2))
    # grow slightly to catch anti-aliased line edges
    if lines.any():
        lines = ndimage.binary_dilation(lines, iterations=1) & mask
    return lines, found


def _components(mask: np.ndarray):
    n, lbl, stats, cent = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    return n, lbl, stats, cent


def _anchors(stats: np.ndarray, n_expected: int | None) -> np.ndarray:
    """Component ids (1-based) that are big enough to be characters."""
    areas = stats[1:, cv2.CC_STAT_AREA].astype(float)
    if len(areas) == 0:
        return np.array([], int)
    k = n_expected or max(1, int((areas > 0.1 * areas.max()).sum()))
    top = np.sort(areas)[::-1][: max(1, k)]
    thr = 0.25 * float(np.median(top))
    return np.nonzero(areas >= thr)[0] + 1


def find_labels(mask: np.ndarray, n_expected: int | None,
                keep_in: list[tuple[int, int, int, int]] | None = None) -> tuple[np.ndarray, int, np.ndarray, int]:
    """Text/label clusters and far stray marks. Returns (labels mask, n clusters, strays mask, n strays).
    Marks wholly inside a `keep_in` box are never strays (an effect's sparks may be all a cell holds)."""
    n, lbl, stats, _ = _components(mask)
    if n <= 1:
        return np.zeros_like(mask), 0, np.zeros_like(mask), 0
    anchors = _anchors(stats, n_expected)
    if len(anchors) == 0:
        return np.zeros_like(mask), 0, np.zeros_like(mask), 0
    hs = float(np.median(stats[anchors, cv2.CC_STAT_HEIGHT]))
    small = [i for i in range(1, n) if i not in set(anchors) and stats[i, cv2.CC_STAT_HEIGHT] < 0.2 * hs]
    boxes = {i: (stats[i, 0], stats[i, 1], stats[i, 0] + stats[i, 2], stats[i, 1] + stats[i, 3]) for i in range(1, n)}
    anchor_boxes = [boxes[a] for a in anchors]

    def inside_anchor(b, grow=0.05):
        for ax0, ay0, ax1, ay1 in anchor_boxes:
            g = grow * hs
            if b[0] >= ax0 - g and b[2] <= ax1 + g and b[1] >= ay0 - g and b[3] <= ay1 + g:
                return True
        return False

    # cluster small components that share a baseline, have similar heights and tight spacing
    cand = [i for i in small if not inside_anchor(boxes[i])]
    cand.sort(key=lambda i: boxes[i][0])
    used, clusters = set(), []
    for i in cand:
        if i in used:
            continue
        group = [i]
        for j in cand:
            if j in used or j in group:
                continue
            gi = boxes[group[-1]]
            bj = boxes[j]
            h1, h2 = gi[3] - gi[1], bj[3] - bj[1]
            if max(h1, h2) > 1.6 * max(1, min(h1, h2)):
                continue
            if abs(gi[3] - bj[3]) > 0.35 * max(h1, h2):
                continue
            gap = bj[0] - gi[2]
            if -0.5 * max(h1, h2) <= gap <= 1.5 * max(h1, h2):
                group.append(j)
        if len(group) >= 3:
            clusters.append(group)
            used.update(group)
    lab_mask = np.isin(lbl, [i for g in clusters for i in g]) if clusters else np.zeros_like(mask)

    # strays: small marks far from every anchor
    strays = []
    for i in range(1, n):
        if i in set(anchors) or i in used:
            continue
        b = boxes[i]
        if stats[i, cv2.CC_STAT_AREA] > 0.02 * hs * hs:
            continue
        near = False
        for ax0, ay0, ax1, ay1 in anchor_boxes:
            dx = max(ax0 - b[2], b[0] - ax1, 0)
            dy = max(ay0 - b[3], b[1] - ay1, 0)
            if max(dx, dy) < 0.5 * hs:
                near = True
                break
        if not near and keep_in and any(b[0] >= x0 and b[2] <= x1 and b[1] >= y0 and b[3] <= y1
                                        for x0, y0, x1, y1 in keep_in):
            near = True
        if not near:
            strays.append(i)
    stray_mask = np.isin(lbl, strays) if strays else np.zeros_like(mask)
    return lab_mask, len(clusters), stray_mask, len(strays)


# ---------------------------------------------------------------------------
# 10.2 Detector A: grid fit


def fit_cuts(P: np.ndarray, expected: list[float], lo: float, hi: float, lam: float = 4.0,
             win_frac: float = 0.35) -> list[int]:
    """Choose one cut near each expected position, on low occupancy, keeping the spacing even.

    Dynamic programming over a ±35% window around each expected cut (exact, O(k · w²)).
    """
    if not expected:
        return []
    bounds = [lo] + list(expected) + [hi]
    L = len(P)
    prev_pos = np.array([lo], float)
    prev_cost = np.array([0.0])
    back: list[np.ndarray] = []
    positions: list[np.ndarray] = []
    for i in range(1, len(bounds) - 1):
        pitch = max(1.0, bounds[i] - bounds[i - 1])
        win = max(1, int(round(win_frac * min(pitch, bounds[i + 1] - bounds[i]))))
        mid = int(round(bounds[i]))
        xs = np.arange(max(1, mid - win), min(L - 1, mid + win) + 1)
        trans = prev_cost[None, :] + lam * ((xs[:, None] - prev_pos[None, :] - pitch) / pitch) ** 2
        arg = trans.argmin(1)
        cost = trans[np.arange(len(xs)), arg] + P[xs]
        back.append(arg)
        positions.append(xs)
        prev_pos, prev_cost = xs.astype(float), cost
    pitch = max(1.0, bounds[-1] - bounds[-2])
    final = prev_cost + lam * ((hi - prev_pos - pitch) / pitch) ** 2
    j = int(final.argmin())
    cuts = []
    for k in range(len(positions) - 1, -1, -1):
        cuts.append(int(positions[k][j]))
        j = int(back[k][j])
    return cuts[::-1]


def _bands(mask: np.ndarray, prior: Prior) -> list[tuple[int, int]]:
    H, W = mask.shape
    if prior.cells:
        ys = sorted({(c[1], c[3]) for c in prior.cells})
        # expected row boundaries; refine each internal boundary on low occupancy
        tops = sorted({c[1] for c in prior.cells})
        bots = sorted({c[3] for c in prior.cells})
        if len(tops) == 1:
            return [(0, H)]
        Py = mask.mean(1)
        cuts = fit_cuts(Py, tops[1:], 0, H)
        edges = [0] + cuts + [H]
        return list(zip(edges[:-1], edges[1:]))
    Py = mask.mean(1)
    occupied = Py > 0.002
    if not occupied.any():
        return [(0, H)]
    # recursive XY-cut: split at wide near-zero runs between content
    runs, y = [], 0
    while y < H:
        if not occupied[y]:
            s = y
            while y < H and not occupied[y]:
                y += 1
            runs.append((s, y))
        else:
            y += 1
    content = np.nonzero(occupied)[0]
    top, bot = int(content.min()), int(content.max()) + 1
    inner = [(s, e) for s, e in runs if s > top and e < bot and (e - s) >= max(2, 0.01 * H)]
    if prior.rows and prior.rows > 1:
        inner = sorted(inner, key=lambda r: r[1] - r[0], reverse=True)[: prior.rows - 1]
        if len(inner) < prior.rows - 1:  # rows touch: fall back to even splitting of the content
            step = (bot - top) / prior.rows
            Pc = Py
            cuts = fit_cuts(Pc, [top + step * i for i in range(1, prior.rows)], 0, H)
            edges = [0] + cuts + [H]
            return list(zip(edges[:-1], edges[1:]))
    if prior.rows == 1:
        inner = []
    cuts = sorted((s + e) // 2 for s, e in inner)
    edges = [0] + cuts + [H]
    return list(zip(edges[:-1], edges[1:]))


def detector_grid(mask: np.ndarray, prior: Prior, lines: dict) -> Hypothesis:
    H, W = mask.shape
    bands = _bands(mask, prior)
    labels = np.zeros((H, W), np.int32)
    cuts_out = []
    rid = 0
    n_total = prior.n
    per_band = _frames_per_band(mask, bands, prior)
    for b, (y0, y1) in enumerate(bands):
        k = per_band[b]
        if k <= 0:
            continue
        band = mask[y0:y1]
        Px = band.mean(0)
        if prior.cells:
            row_cells = [c for c in prior.cells if abs((c[1] + c[3]) / 2 - (y0 + y1) / 2) < (y1 - y0) / 2]
            row_cells.sort(key=lambda c: c[0])
            lo, hi = float(row_cells[0][0]), float(row_cells[-1][2])
            expected = [float(c[0]) for c in row_cells[1:]]
        else:
            xs = np.nonzero(Px > 0)[0]
            if len(xs) == 0:
                continue
            lo, hi = max(0.0, xs.min() - 2.0), min(float(W), xs.max() + 3.0)
            expected = [lo + (hi - lo) * i / k for i in range(1, k)]
        cuts = fit_cuts(Px, expected, lo, hi)
        # grid lines detected in 10.1 override fitted cuts
        for i, c in enumerate(cuts):
            near = [v for v in lines.get("v", []) if abs(v - c) < 0.35 * (hi - lo) / max(1, k)]
            if near:
                cuts[i] = int(min(near, key=lambda v: abs(v - c)))
        edges = [int(lo)] + cuts + [int(hi)]
        for j in range(len(edges) - 1):
            rid += 1
            labels[y0:y1, edges[j]:edges[j + 1]] = rid
        for c in cuts:
            ys = np.arange(y0, y1)
            cuts_out.append((ys, np.full_like(ys, c)))
    return Hypothesis("grid", labels, rid, cuts_out)


def _frames_per_band(mask: np.ndarray, bands, prior: Prior) -> list[int]:
    if prior.cells:
        out = []
        for y0, y1 in bands:
            out.append(sum(1 for c in prior.cells if y0 <= (c[1] + c[3]) / 2 < y1))
        return out
    if prior.n and prior.cols:
        return [min(prior.cols, max(0, prior.n - prior.cols * b)) for b in range(len(bands))]
    if prior.n:
        cols = int(np.ceil(prior.n / len(bands)))
        return [min(cols, max(0, prior.n - cols * b)) for b in range(len(bands))]
    # unknown: count content segments separated by empty columns
    out = []
    for y0, y1 in bands:
        occ = mask[y0:y1].any(0)
        segs = np.diff(np.concatenate([[0], occ.astype(int), [0]]))
        out.append(max(1, int((segs == 1).sum())))
    return out


# ---------------------------------------------------------------------------
# 10.3 Detector B: seam cuts


def _seam(cost: np.ndarray) -> np.ndarray:
    """Minimum-cost top-to-bottom seam (classic seam-carving recurrence); returns x per row."""
    h, w = cost.shape
    S = cost.astype(np.float64).copy()
    back = np.zeros((h, w), np.int8)
    for y in range(1, h):
        prev = S[y - 1]
        left = np.concatenate([[np.inf], prev[:-1]])
        right = np.concatenate([prev[1:], [np.inf]])
        stack = np.stack([left, prev, right])
        arg = stack.argmin(0)
        S[y] += stack[arg, np.arange(w)]
        back[y] = arg - 1
    xs = np.zeros(h, int)
    xs[-1] = int(S[-1].argmin())
    for y in range(h - 1, 0, -1):
        xs[y - 1] = xs[y] + back[y, xs[y]]
    return np.clip(xs, 0, w - 1)


def detector_seam(mask: np.ndarray, grid: Hypothesis, beta: float = 1000.0) -> Hypothesis:
    H, W = mask.shape
    dt = cv2.distanceTransform((~mask).astype(np.uint8), cv2.DIST_L2, 5)
    labels = grid.labels.copy()
    new_cuts = []
    # rebuild each band: group grid cuts by their row span
    bands: dict[tuple[int, int], list[int]] = {}
    for ys, xs in grid.cuts:
        bands.setdefault((int(ys[0]), int(ys[-1]) + 1), []).append(int(xs[0]))
    for (y0, y1), cuts in bands.items():
        cuts = sorted(cuts)
        row_ids = np.unique(grid.labels[y0:y1][grid.labels[y0:y1] > 0])
        if len(row_ids) == 0:
            continue
        band_lab = grid.labels[y0:y1]
        lo = int(np.nonzero((band_lab > 0).any(0))[0].min())
        hi = int(np.nonzero((band_lab > 0).any(0))[0].max()) + 1
        edges = [lo] + cuts + [hi]
        seams = []
        for i, c in enumerate(cuts):
            pitch_l, pitch_r = c - edges[i], edges[i + 2] - c
            win = max(2, int(0.35 * min(pitch_l, pitch_r)))
            xa, xb = max(lo + 1, c - win), min(hi - 1, c + win + 1)
            straight_hits = mask[y0:y1, c].mean()
            if straight_hits == 0:
                xs = np.full(y1 - y0, c)
            else:
                cost = beta * mask[y0:y1, xa:xb] + 1.0 / (1.0 + dt[y0:y1, xa:xb])
                xs = _seam(cost) + xa
            seams.append(xs)
            new_cuts.append((np.arange(y0, y1), xs))
        first = int(row_ids.min())
        xx = np.arange(W)[None, :]
        region = np.zeros((y1 - y0, W), np.int32)
        inside = (xx >= lo) & (xx < hi)
        idx = np.zeros((y1 - y0, W), np.int32)
        for s in seams:
            idx += (xx >= s[:, None]).astype(np.int32)
        region = np.where(inside, first + idx, 0)
        labels[y0:y1] = region
    return Hypothesis("seam", labels, grid.count, new_cuts)


# ---------------------------------------------------------------------------
# 10.4 Detector C: component grouping


def detector_components(mask: np.ndarray, prior: Prior) -> Hypothesis:
    H, W = mask.shape
    n, lbl, stats, cent = _components(mask)
    if n <= 1:
        return Hypothesis("components", np.zeros((H, W), np.int32), 0)
    anchors = list(_anchors(stats, prior.n))
    groups: dict[int, list[int]] = {a: [a] for a in anchors}
    box = {i: [stats[i, 0], stats[i, 1], stats[i, 0] + stats[i, 2], stats[i, 1] + stats[i, 3]] for i in range(1, n)}
    orphans = []
    for i in range(1, n):
        if i in groups:
            continue
        best, best_gap = None, 1e9
        for a in anchors:
            ab = box[a]
            ah = max(1, ab[3] - ab[1])
            aw = max(1, ab[2] - ab[0])
            dx = max(ab[0] - box[i][2], box[i][0] - ab[2], 0)
            dy = max(ab[1] - box[i][3], box[i][1] - ab[3], 0)
            gap = np.hypot(dx, dy) / ah
            cx = (box[i][0] + box[i][2]) / 2
            in_band = ab[0] - 0.3 * aw <= cx <= ab[2] + 0.3 * aw
            if gap < 0.35 and in_band and gap < best_gap:
                best, best_gap = a, gap
        if best is None:
            orphans.append(i)
        else:
            groups[best].append(i)

    def gbox(g):
        bs = np.array([box[i] for i in g])
        return [bs[:, 0].min(), bs[:, 1].min(), bs[:, 2].max(), bs[:, 3].max()]

    frames = [list(g) for g in groups.values()]
    # reconcile the count with N: merge near-touching small pairs, split over-wide anchors
    if prior.n:
        while len(frames) > prior.n:
            bxs = [gbox(g) for g in frames]
            areas = [sum(stats[i, cv2.CC_STAT_AREA] for i in g) for g in frames]
            med_h = float(np.median([b[3] - b[1] for b in bxs]))
            best = None
            for a in range(len(frames)):
                for b in range(a + 1, len(frames)):
                    A, B = bxs[a], bxs[b]
                    dx = max(A[0] - B[2], B[0] - A[2], 0)
                    dy = max(A[1] - B[3], B[1] - A[3], 0)
                    gap = np.hypot(dx, dy)
                    key = (gap, areas[a] + areas[b])
                    if gap < 0.1 * med_h and (best is None or key < best[0]):
                        best = (key, a, b)
            if best is None:
                break
            _, a, b = best
            frames[a] = frames[a] + frames[b]
            del frames[b]
    labels = np.zeros((H, W), np.int32)
    for fi, g in enumerate(frames, start=1):
        labels[np.isin(lbl, g)] = fi
    if prior.n and len(frames) < prior.n:
        labels, frames_n = _split_wide(labels, mask, len(frames), prior.n)
    else:
        frames_n = len(frames)
    labels = _reading_order(labels, frames_n)
    # extend each frame to pixels within 4 px (soft alpha edges belong to their frame)
    if frames_n:
        dist, (iy, ix) = ndimage.distance_transform_edt(labels == 0, return_indices=True)
        grown = labels[iy, ix]
        labels = np.where(dist <= 4, grown, 0).astype(np.int32)
    orphan = np.isin(lbl, orphans) if orphans else None
    return Hypothesis("components", labels, frames_n, [], orphan)


def _split_wide(labels: np.ndarray, mask: np.ndarray, count: int, n: int) -> tuple[np.ndarray, int]:
    widths = []
    for i in range(1, count + 1):
        xs = np.nonzero((labels == i).any(0))[0]
        widths.append(xs.max() - xs.min() + 1 if len(xs) else 0)
    med = float(np.median(widths))
    out = labels.copy()
    nxt = count
    for i in np.argsort(widths)[::-1]:
        if nxt >= n or widths[i] <= 1.6 * med:
            break
        fid = i + 1
        ys, xs = np.nonzero(labels == fid)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        sub = mask[y0:y1, x0:x1] & (labels[y0:y1, x0:x1] == fid)
        mid = (x1 - x0) // 2
        win = max(2, int(0.35 * (x1 - x0) / 2))
        dt = cv2.distanceTransform((~sub).astype(np.uint8), cv2.DIST_L2, 5)
        cost = 1000.0 * sub[:, mid - win:mid + win] + 1.0 / (1.0 + dt[:, mid - win:mid + win])
        seam = _seam(cost) + mid - win
        xx = np.arange(x1 - x0)[None, :]
        right = (xx >= seam[:, None]) & (labels[y0:y1, x0:x1] == fid)
        nxt += 1
        out[y0:y1, x0:x1][right] = nxt
    return out, nxt


def _reading_order(labels: np.ndarray, count: int) -> np.ndarray:
    if count == 0:
        return labels
    objs = ndimage.find_objects(labels)
    info = []
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        info.append((i, sl[0].stop, (sl[1].start + sl[1].stop) / 2, sl[0].stop - sl[0].start))
    if not info:
        return labels
    med_h = float(np.median([t[3] for t in info]))
    info.sort(key=lambda t: t[1])
    rows, cur = [], [info[0]]
    for t in info[1:]:
        if t[1] - cur[-1][1] > 0.5 * med_h:
            rows.append(cur)
            cur = [t]
        else:
            cur.append(t)
    rows.append(cur)
    order = [t[0] for row in rows for t in sorted(row, key=lambda t: t[2])]
    remap = np.zeros(labels.max() + 1, np.int32)
    for new, old in enumerate(order, start=1):
        remap[old] = new
    return remap[labels]


# ---------------------------------------------------------------------------
# 10.5 Hypothesis selection


def score(h: Hypothesis, mask: np.ndarray, n: int | None) -> Hypothesis:
    lab = np.where(mask, h.labels, 0)
    ids = [i for i in range(1, h.labels.max() + 1) if (lab == i).any()]
    count = len(ids)
    if count == 0:
        h.score, h.terms = -1e9, {"count": 0}
        return h
    areas = np.array([(lab == i).sum() for i in ids], float)
    heights = []
    contained = 0
    for i in ids:
        ys, xs = np.nonzero(lab == i)
        heights.append(ys.max() - ys.min() + 1)
        region = h.labels == i
        edge = region & ~ndimage.binary_erosion(region, iterations=1, border_value=1)
        if not (edge & mask).any():
            contained += 1
    heights = np.array(heights, float)
    cvA = float(areas.std() / max(areas.mean(), 1e-6))
    cvH = float(heights.std() / max(heights.mean(), 1e-6))
    fcut = float(np.mean([mask[ys, xs].mean() for ys, xs in h.cuts])) if h.cuts else 0.0
    total = float(mask.sum())
    assigned = float((lab > 0).sum())
    orphan = max(0.0, (total - assigned) / max(total, 1.0))
    ok = 1.0 if (n is None or count == n) else 0.0
    s = (W_COUNT * ok + W_CUT * (1 - fcut) + W_AREA * (1 - min(cvA, 1)) + W_HEIGHT * (1 - min(cvH, 1))
         + W_CONTAIN * contained / count - W_ORPHAN * orphan)
    h.count = count
    h.score = float(s)
    h.terms = {"count": count, "f_cut": round(fcut, 4), "cv_area": round(cvA, 4), "cv_height": round(cvH, 4),
               "contain": round(contained / count, 4), "orphan": round(orphan, 4)}
    return h


def _agreement(a: Hypothesis, b: Hypothesis, mask: np.ndarray) -> float:
    la, lb = a.labels[mask], b.labels[mask]
    if len(la) == 0:
        return 1.0
    return float((la == lb).mean())


def _rect_mask(shape, rects) -> np.ndarray:
    m = np.zeros(shape, bool)
    for x0, y0, x1, y1 in rects:
        m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = True
    return m


def exclude_regions(mask: np.ndarray, rects, keep: np.ndarray | None = None) -> tuple[np.ndarray, list]:
    """Drop the reference strip by component, not by rectangle (L14). A connected region mostly
    inside the rectangles is the reference; one mostly outside is a frame the model drew across the
    strip edge, kept whole. A region that is both (the reference touching a frame) is cut at the
    rectangle and returned as a cut box so its frame is flagged as clipped (L15). Pixels in `keep`
    (confirmed cells, L17) are never excluded."""
    work = mask.copy()
    if not rects:
        return work, []
    inside = _rect_mask(mask.shape, rects)
    if keep is not None:
        inside &= ~keep
    lbl, n = ndimage.label(work)
    if n == 0:
        return work, []
    ids = np.arange(1, n + 1)
    area = ndimage.sum(np.ones(mask.shape), lbl, ids)
    frac = ndimage.sum(inside, lbl, ids) / np.maximum(area, 1)
    work &= ~np.isin(lbl, ids[frac >= 0.8])
    mixed = ids[(frac > 0.2) & (frac < 0.8)]
    cuts = []
    for i in mixed:
        comp = lbl == i
        work &= ~(comp & inside)
        ys, xs = np.nonzero(comp & ~inside)
        if len(ys):
            cuts.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    return work, cuts


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def detect(mask: np.ndarray, prior: Prior) -> Layout:
    H, W = mask.shape
    findings: list[Finding] = []
    if prior.forced and prior.cells:
        return _forced(mask, prior)
    work, strip_cuts = exclude_regions(mask, prior.exclude)
    lines_mask, lines = find_lines(work)
    work &= ~lines_mask
    if lines["h"] or lines["v"]:
        findings.append(Finding(metric="extraneous_marks", level="warn", severity=1,
                                value=len(lines["h"]) + len(lines["v"]),
                                message=f"removed {len(lines['h'])} horizontal and {len(lines['v'])} vertical lines",
                                remedy="auto_fixed", auto_fixed=True))
    lab_mask, n_labels, stray_mask, n_strays = find_labels(work, prior.n,
                                                           prior.cells if prior.detached else None)
    work &= ~lab_mask
    work &= ~stray_mask
    if n_labels:
        findings.append(Finding(metric="extraneous_marks", level="warn", severity=1, value=n_labels,
                                message=f"labels present: removed {n_labels} text-like clusters",
                                remedy="auto_fixed", auto_fixed=True))
    if n_strays:
        findings.append(Finding(metric="extraneous_marks", level="info", severity=1, value=n_strays,
                                message=f"excluded {n_strays} stray marks", remedy="auto_fixed", auto_fixed=True))

    hyps = []
    grid = score(detector_grid(work, prior, lines), work, prior.n)
    hyps.append(grid)
    if grid.cuts:
        hyps.append(score(detector_seam(work, grid), work, prior.n))
    hyps.append(score(detector_components(work, prior), work, prior.n))
    hyps.sort(key=lambda h: h.score, reverse=True)
    best = hyps[0]
    rivals = [h for h in hyps[1:] if _agreement(best, h, work) < 0.98]
    conf = 1.0 if not rivals else float(min(1.0, max(0.0, best.score - rivals[0].score)))
    scores = {h.name: {"score": round(h.score, 4), **h.terms} for h in hyps}

    labels = best.labels
    frames = _extract(labels, work, best)
    if best.name == "seam":
        touching = []
        for ys, xs in best.cuts:
            if work[ys, xs].any():
                ids = sorted({int(labels[y, max(0, x - 1)]) for y, x in zip(ys, xs) if work[y, x]} |
                             {int(labels[y, min(W - 1, x + 1)]) for y, x in zip(ys, xs) if work[y, x]})
                touching += [i for i in ids if i > 0]
        for f in frames:
            if f.label in touching:
                f.touching = True
        if touching:
            findings.append(Finding(metric="frames_touching", level="warn", severity=2,
                                    frames=sorted({f.index for f in frames if f.touching}),
                                    message="frames touch; split along the cheapest seam", remedy="frame_repair"))
    n = prior.n
    if n is not None and len(frames) != n:
        findings.append(Finding(metric="frame_count", level="fail", severity=3, value=len(frames), threshold=n,
                                message=f"detected {len(frames)} frames, expected {n}", remedy="reroll"))
    if conf < 0.15 or (n is not None and len(frames) != n):
        findings.append(Finding(metric="layout_confidence", level="warn", severity=1, value=round(conf, 3),
                                threshold=0.15, message=f"layout confidence {conf:.2f}; confirm the grid",
                                remedy="confirm_layout"))
    for f in frames:  # L15: a frame fused with the reference drawing was cut at the strip
        if any(_overlaps(f.box, c) for c in strip_cuts):
            f.clipped = True
    clipped = [f.index for f in frames if f.clipped]
    if clipped:
        findings.append(Finding(metric="clipping", level="fail", severity=2, frames=clipped,
                                message=f"frames {', '.join(str(i + 1) for i in clipped)} run along a region edge",
                                remedy="frame_repair"))
    return Layout(frames, labels, best.name, conf, scores, work, lines, n_labels, n_strays, findings)


def _forced(mask: np.ndarray, prior: Prior) -> Layout:
    """A layout confirmed by hand (L16-L19): frame i is the foreground inside cell i. Overlaps go to
    the nearest cell centre; the strip is excluded only outside the cells; text labels and grid
    lines are still removed, but nothing inside a cell is dropped as a stray (the user framed it)."""
    H, W = mask.shape
    findings: list[Finding] = []
    cells = [(max(0, x0), max(0, y0), min(W, x1), min(H, y1)) for x0, y0, x1, y1 in prior.cells]
    in_cells = _rect_mask(mask.shape, cells)
    work, _ = exclude_regions(mask, prior.exclude, keep=in_cells)
    lines_mask, lines = find_lines(work)
    work &= ~lines_mask
    lab_mask, n_labels, _, _ = find_labels(work, len(cells))
    work &= ~lab_mask
    if n_labels:
        findings.append(Finding(metric="extraneous_marks", level="warn", severity=1, value=n_labels,
                                message=f"labels present: removed {n_labels} text-like clusters",
                                remedy="auto_fixed", auto_fixed=True))
    work &= in_cells
    labels = np.zeros(mask.shape, np.int32)
    best = np.full(mask.shape, np.inf, np.float32)
    for i, (x0, y0, x1, y1) in enumerate(cells, start=1):
        if x1 <= x0 or y1 <= y0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d = ((xx - (x0 + x1) / 2) ** 2 + (yy - (y0 + y1) / 2) ** 2).astype(np.float32)
        closer = d < best[y0:y1, x0:x1]
        labels[y0:y1, x0:x1][closer] = i
        best[y0:y1, x0:x1][closer] = d[closer]
    frames, heights = [], []
    for i, cell in enumerate(cells, start=1):
        fm = (labels == i) & work
        if not fm.any():
            continue
        ys, xs = np.nonzero(fm)
        frames.append(FrameCut(len(frames), cell, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), i))
        heights.append(int(ys.max() - ys.min() + 1))
    sprite_h = float(np.median(heights)) if heights else 1.0
    for f in frames:
        f.clipped = _clipped(labels, work, f.label, f.region, sprite_h, straight=True)
    if len(frames) != len(cells):
        findings.append(Finding(metric="frame_count", level="fail", severity=3, value=len(frames),
                                threshold=len(cells), message=f"{len(cells) - len(frames)} confirmed cells are empty",
                                remedy="confirm_layout"))
    clipped = [f.index for f in frames if f.clipped]
    if clipped:
        findings.append(Finding(metric="clipping", level="fail", severity=2, frames=clipped,
                                message=f"frames {', '.join(str(i + 1) for i in clipped)} run along a cell edge",
                                remedy="confirm_layout"))
    return Layout(frames, labels, "confirmed", 1.0, {}, work, lines, n_labels, 0, findings)


def _extract(labels: np.ndarray, mask: np.ndarray, h: Hypothesis) -> list[FrameCut]:
    H, W = mask.shape
    out = []
    objs = ndimage.find_objects(labels)
    heights = []
    tmp = []
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        fm = (labels[sl] == i) & mask[sl]
        if not fm.any():
            continue
        ys, xs = np.nonzero(fm)
        box = (sl[1].start + int(xs.min()), sl[0].start + int(ys.min()),
               sl[1].start + int(xs.max()) + 1, sl[0].start + int(ys.max()) + 1)
        region = (sl[1].start, sl[0].start, sl[1].stop, sl[0].stop)
        heights.append(box[3] - box[1])
        tmp.append((i, region, box))
    if not tmp:
        return out
    sprite_h = float(np.median(heights))
    for idx, (i, region, box) in enumerate(tmp):
        clipped = _clipped(labels, mask, i, region, sprite_h, straight=h.name != "components")
        out.append(FrameCut(idx, region, box, i, clipped=clipped))
    return out


def _longest_run(v: np.ndarray) -> int:
    if not v.any():
        return 0
    d = np.diff(np.concatenate([[0], v.astype(np.int8), [0]]))
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    return int((ends - starts).max())


def _clipped(labels, mask, i, region, sprite_h, straight: bool) -> bool:
    H, W = mask.shape
    x0, y0, x1, y1 = region
    fm = (labels == i) & mask
    limit = 0.03 * sprite_h
    sides = []
    if straight or x0 == 0:
        sides.append(fm[y0:y1, x0])
    if straight or x1 == W:
        sides.append(fm[y0:y1, x1 - 1])
    if straight or y0 == 0:
        sides.append(fm[y0, x0:x1])
    if straight or y1 == H:
        sides.append(fm[y1 - 1, x0:x1])
    return any(_longest_run(s) > limit for s in sides)
