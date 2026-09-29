"""Temporal analysis (section 13): the aligned frames as a very short video.

One pairwise distance matrix yields frame order, duplicates, loop closure, gait validity and
where in-betweens are needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numba
import numpy as np

from ..spec import Finding


@dataclass
class Temporal:
    D: np.ndarray
    order: list[int]
    reordered: bool
    duplicates: list[tuple[int, int]]
    gaps: list[tuple[int, int]]
    smoothness: float
    seam_ratio: float | None
    gait: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"order": self.order, "reordered": self.reordered, "duplicates": self.duplicates, "gaps": self.gaps,
                "smoothness": round(self.smoothness, 4),
                "seam_ratio": None if self.seam_ratio is None else round(self.seam_ratio, 4),
                "gait": self.gait, "checks": self.checks, "D": np.round(self.D, 4).tolist()}


# ---------------------------------------------------------------------------
# 13.1 Distance matrix


def _edge_map(rgba: np.ndarray, height: int = 64) -> tuple[np.ndarray, np.ndarray]:
    h, w = rgba.shape[:2]
    s = height / h
    small = cv2.resize(rgba, (max(1, int(round(w * s))), height), interpolation=cv2.INTER_AREA)
    a = small[..., 3].astype(np.float32) / 255.0
    g = cv2.cvtColor(small[..., :3], cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0 * a
    e = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    return e, a > 0.5


def _small_premul(rgba: np.ndarray, height: int = 64) -> np.ndarray:
    h, w = rgba.shape[:2]
    s = height / h
    sm = cv2.resize(rgba, (max(1, int(round(w * s))), height), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    return np.concatenate([sm[..., :3] * sm[..., 3:4], sm[..., 3:4]], -1)


def distance_matrix(frames: list[np.ndarray]) -> np.ndarray:
    """1 − IoU of aligned masks (silhouette), mean |Δ| of Sobel edge maps (interior) and mean |Δ| of
    premultiplied colour (which limb is in front), all on 64 px tall copies."""
    n = len(frames)
    masks = [f[..., 3] >= 128 for f in frames]
    maps = [_edge_map(f)[0] for f in frames]
    cols = [_small_premul(f) for f in frames]
    scale = max(float(max(m.max() for m in maps)), 1e-6)
    D = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            inter = np.logical_and(masks[i], masks[j]).sum()
            union = np.logical_or(masks[i], masks[j]).sum()
            iou = inter / union if union else 1.0
            edge = float(np.abs(maps[i] - maps[j]).mean() / scale)
            u = (cols[i][..., 3] > 0.5) | (cols[j][..., 3] > 0.5)
            col = float(np.abs(cols[i][..., :3] - cols[j][..., :3]).sum(-1)[u].mean()) if u.any() else 0.0
            D[i, j] = D[j, i] = (1 - iou) / 3 + edge * 4.0 / 3 + col / 3
    return D


# ---------------------------------------------------------------------------
# 13.2 Held–Karp


@numba.njit(cache=True)
def _held_karp(D: np.ndarray, cycle: bool) -> tuple[float, np.ndarray]:
    n = D.shape[0]
    full = 1 << n
    INF = 1e18
    dp = np.full((full, n), INF)
    parent = np.full((full, n), -1, np.int64)
    dp[1, 0] = 0.0
    for mask in range(1, full):
        if not (mask & 1):
            continue
        for j in range(n):
            if not (mask & (1 << j)):
                continue
            cur = dp[mask, j]
            if cur >= INF:
                continue
            for k in range(n):
                if mask & (1 << k):
                    continue
                nm = mask | (1 << k)
                v = cur + D[j, k]
                if v < dp[nm, k]:
                    dp[nm, k] = v
                    parent[nm, k] = j
    last = full - 1
    best, bj = INF, 0
    for j in range(1, n):
        v = dp[last, j] + (D[j, 0] if cycle else 0.0)
        if v < best:
            best, bj = v, j
    path = np.empty(n, np.int64)
    mask = last
    j = bj
    for idx in range(n - 1, -1, -1):
        path[idx] = j
        pj = parent[mask, j]
        mask = mask ^ (1 << j)
        j = pj
        if j < 0:
            break
    return best, path


def best_order(D: np.ndarray, cycle: bool) -> tuple[list[int], float]:
    n = len(D)
    if n <= 2:
        return list(range(n)), float(D[0, 1] * (2 if cycle else 1)) if n == 2 else 0.0
    cost, path = _held_karp(np.ascontiguousarray(D, dtype=np.float64), cycle)
    return [int(p) for p in path], float(cost)


def _min_matching(D: np.ndarray) -> list[tuple[int, int]]:
    """Exact minimum-weight perfect matching over an even number of frames (bitmask DP)."""
    n = len(D)
    full = (1 << n) - 1
    memo: dict[int, tuple[float, list]] = {0: (0.0, [])}

    def solve(mask: int) -> tuple[float, list]:
        if mask in memo:
            return memo[mask]
        i = (mask & -mask).bit_length() - 1
        best = (float("inf"), [])
        rest = mask & ~(1 << i)
        m = rest
        while m:
            j = (m & -m).bit_length() - 1
            m &= m - 1
            c, pairs = solve(rest & ~(1 << j))
            if c + D[i, j] < best[0]:
                best = (c + D[i, j], pairs + [(i, j)])
        memo[mask] = best
        return best

    return solve(full)[1]


def gait_order(D: np.ndarray) -> tuple[list[int], float]:
    """Walk and run cycles: the two halves are near-mirrors (twins half a cycle apart). Match twins,
    then order the pairs with Held–Karp over (pair, orientation); the full cycle is the pair order
    traversed twice, once per leading leg."""
    n = len(D)
    pairs = _min_matching(D)
    pairs.sort(key=lambda p: min(p))
    m = len(pairs)
    INF = float("inf")
    # dp[mask][j][o]: pair 0 fixed at orientation 0; o picks which twin goes in the first half
    dp = np.full((1 << m, m, 2), INF)
    par = {}
    dp[1, 0, 0] = 0.0

    def fs(j, o):
        a, b = pairs[j]
        return (a, b) if o == 0 else (b, a)

    for mask in range(1, 1 << m):
        if not mask & 1:
            continue
        for j in range(m):
            if not mask & (1 << j):
                continue
            for o in range(2):
                cur = dp[mask, j, o]
                if cur == INF:
                    continue
                f1, s1 = fs(j, o)
                for k in range(m):
                    if mask & (1 << k):
                        continue
                    for o2 in range(2):
                        f2, s2 = fs(k, o2)
                        v = cur + D[f1, f2] + D[s1, s2]
                        nm = mask | (1 << k)
                        if v < dp[nm, k, o2]:
                            dp[nm, k, o2] = v
                            par[(nm, k, o2)] = (j, o)
    full = (1 << m) - 1
    f0, s0 = fs(0, 0)
    best, bj, bo = INF, 0, 0
    for j in range(m):
        for o in range(2):
            if dp[full, j, o] == INF:
                continue
            fj, sj = fs(j, o)
            v = dp[full, j, o] + D[fj, s0] + D[sj, f0]
            if v < best:
                best, bj, bo = v, j, o
    seq = []
    mask, j, o = full, bj, bo
    while True:
        seq.append((j, o))
        if (mask, j, o) not in par:
            break
        pj, po = par[(mask, j, o)]
        mask &= ~(1 << j)
        j, o = pj, po
    seq = seq[::-1]
    first = [fs(j, o)[0] for j, o in seq]
    second = [fs(j, o)[1] for j, o in seq]
    order = first + second
    return order, float(best)


def order_cost(D: np.ndarray, order: list[int], cycle: bool) -> float:
    c = sum(D[order[i], order[i + 1]] for i in range(len(order) - 1))
    if cycle and len(order) > 1:
        c += D[order[-1], order[0]]
    return float(c)


def _orient(order: list[int], cycle: bool) -> list[int]:
    """Traversal direction that best agrees with reading order (the choreography's direction)."""
    if not cycle:
        return order
    alt = [order[0]] + order[1:][::-1]

    def agree(o):
        pos = {f: i for i, f in enumerate(o)}
        return sum(1 for f in range(len(o) - 1) if (pos[f + 1] - pos[f]) % len(o) == 1)

    return alt if agree(alt) > agree(order) else order


# ---------------------------------------------------------------------------
# Analysis


def _mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    ma, mb = a[..., 3] > 32, b[..., 3] > 32
    if ma.shape != mb.shape:
        return 0.0
    union = int((ma | mb).sum())
    return int((ma & mb).sum()) / union if union else 1.0


def analyze(frames: list[np.ndarray], *, loop: bool, action: str, sprite_h: float,
            lift: list[float] | None = None, impact: int | None = None, apex: int | None = None,
            returns_to_start: bool = False, check_order: bool = True, frontal: bool = False,
            subtle: bool = False, kind: str = "character") -> Temporal:
    """`subtle` (idle breathing): frames are near-identical by design, so ratios against the median
    step are noise; order and duplicates are not judged and gaps or seams must be large outright."""
    n = len(frames)
    findings: list[Finding] = []
    D = distance_matrix(frames)
    reading = list(range(n))
    order, reordered = reading, False
    if check_order and n >= 3 and not subtle:
        gait = action in ("walk", "run") and loop and n % 2 == 0 and n >= 4
        opt, cost = gait_order(D) if gait else best_order(D, loop)
        if loop:
            k = opt.index(0)
            opt = opt[k:] + opt[:k]
        opt = _orient(opt, loop)
        base = order_cost(D, reading, loop)
        thresh = 0.70 if action in ("walk", "run") else 0.85
        if cost < thresh * base and opt != reading:
            order, reordered = opt, True
            findings.append(Finding(metric="order", level="warn", severity=1, value=round(cost / max(base, 1e-9), 4),
                                    threshold=thresh, message=f"frames reordered to {[i + 1 for i in order]}",
                                    remedy="auto_fixed", auto_fixed=True))
    seq = [D[order[i], order[i + 1]] for i in range(n - 1)]
    if loop and n > 2:
        seq_all = seq + [D[order[-1], order[0]]]
    else:
        seq_all = seq
    step = float(np.median(seq_all)) if seq_all else 0.0
    dups, gaps = [], []
    for i in range(n - 1):
        a, b = order[i], order[i + 1]
        if step > 0 and D[a, b] < 0.25 * step and not subtle and (kind != "effect" or _mask_iou(frames[a], frames[b]) > 0.8):
            dups.append((a, b))  # effects: a fading tail has tiny steps next to the burst; only true copies count
        if step > 0 and D[a, b] > 2.5 * step and (not subtle or D[a, b] > 0.15):
            gaps.append((a, b))
    if dups:
        findings.append(Finding(metric="duplicates", level="warn", severity=2, frames=sorted({b for _, b in dups}),
                                message=f"near-duplicate frames {', '.join(f'{a + 1}/{b + 1}' for a, b in dups)}",
                                remedy="regenerate_frame"))
    if gaps:
        findings.append(Finding(metric="gaps", level="warn", severity=2, frames=sorted({b for _, b in gaps}),
                                message=f"motion gap between {', '.join(f'{a + 1}->{b + 1}' for a, b in gaps)}",
                                remedy="inbetween"))
    arr = np.array(seq_all) if seq_all else np.array([0.0])
    smooth = float(1 - arr.std() / max(arr.mean(), 1e-9))
    seam = None
    if loop and n > 2:
        med = float(np.median(seq)) if seq else 0.0
        seam = float(D[order[-1], order[0]] / max(med, 1e-9))
        if subtle and D[order[-1], order[0]] <= 0.15:
            pass  # a subtle loop's seam is judged outright, not against its tiny median step
        elif seam > 2.5:
            findings.append(Finding(metric="loop_seam", level="fail", severity=3, value=round(seam, 3), threshold=2.5,
                                    message=f"seam ratio {seam:.2f}: not a loop", remedy="reroll"))
        elif seam > 1.5:
            findings.append(Finding(metric="loop_seam", level="warn", severity=1 if seam <= 2.0 else 2,
                                    value=round(seam, 3), threshold=1.5,
                                    message=f"seam ratio {seam:.2f}: one in-between closes the loop",
                                    remedy="inbetween"))

    gait, checks = {}, {}
    fr = [frames[i] for i in order]
    masks = [f[..., 3] >= 128 for f in fr]
    if kind != "character":
        gait = {}  # gait and body checks are for characters only (K3)
    elif action in ("walk", "run") and n >= 4 and frontal:
        gait = {"skipped": "front/back view: leg spread is not horizontal"}
    elif action in ("walk", "run") and n >= 4:
        gait = gait_check(masks, fr)
        if gait.get("k_lead") == 2 and gait["k_spread"] == 2:
            findings.append(Finding(metric="gait", level="fail", severity=3, value=2, threshold=1,
                                    message="the same leg leads both steps", remedy="reroll"))
        elif gait["k_spread"] != 2:
            findings.append(Finding(metric="gait", level="fail", severity=3, value=gait["k_spread"], threshold=2,
                                    message=f"leg spread repeats {gait['k_spread']}x per loop, expected 2 steps"
                                            + (" (same leg leads twice or limp)" if gait["k_spread"] == 1 else ""),
                                    remedy="reroll"))
        if gait["strength"] < 0.02:
            findings.append(Finding(metric="gait", level="warn", severity=2, value=round(gait["strength"], 4),
                                    message="legs barely move", remedy="reroll"))
    if action == "idle" and kind == "character":  # K15: an object's moving parts legitimately move its top
        tops = np.array([np.nonzero(m.any(1))[0].min() for m in masks if m.any()], float)
        travel = float((tops.max() - tops.min()) / max(sprite_h, 1)) if len(tops) else 0.0
        checks["idle_head_travel"] = round(travel, 4)
        if travel > 0.03:
            findings.append(Finding(metric="idle_motion", level="warn", severity=2, value=round(travel, 4),
                                    threshold=0.03, message=f"idle head travels {travel:.1%} of height; too much",
                                    remedy="reroll"))
    # the reach test is for strikes; a vehicle's muzzle smoke keeps billowing forward after the flash
    if impact is not None and n >= 3 and kind == "character":
        reach = []
        for m in masks:
            xs = np.nonzero(m.any(0))[0]
            reach.append(float(xs.max()) if len(xs) else 0.0)
        peak = int(np.argmax(reach))
        checks["reach_peak"] = peak
        if abs(peak - impact) > 1:
            findings.append(Finding(metric="pose", level="warn", severity=2, frames=[peak], value=peak + 1,
                                    threshold=impact + 1, message=f"forward reach peaks at frame {peak + 1}, "
                                                                  f"impact is frame {impact + 1}", remedy="pose_fix"))
        if returns_to_start:
            ratio = float(D[order[-1], order[0]] / max(step, 1e-9)) if step else 0.0
            checks["return_ratio"] = round(ratio, 4)
    if apex is not None and lift is not None and n >= 3:
        lf = np.array([lift[i] for i in order])
        peak = int(np.argmax(lf))
        rises = int(np.sum(np.diff(np.sign(np.diff(lf))) < 0))
        checks["apex"] = peak
        if abs(peak - apex) > 1 or rises > 1:
            findings.append(Finding(metric="pose", level="warn", severity=2, frames=[peak], value=peak + 1,
                                    threshold=apex + 1, message=f"jump apex at frame {peak + 1}, expected {apex + 1}",
                                    remedy="pose_fix"))
    return Temporal(D, order, reordered, dups, gaps, smooth, seam, gait, checks, findings)


def _lead(frame: np.ndarray, mask: np.ndarray) -> float:
    """Signed leading-leg cue: lightness of the front foot region minus the back one. Near limbs are
    drawn lighter (the depth cue the guide asks for), so the sign flips when the other leg leads."""
    ys = np.nonzero(mask.any(1))[0]
    if len(ys) == 0:
        return 0.0
    top, bot = ys.min(), ys.max()
    band = slice(int(bot - 0.22 * (bot - top)), bot + 1)
    m = mask[band]
    xs = np.nonzero(m.any(0))[0]
    if len(xs) < 4:
        return 0.0
    cols = xs.min(), xs.max()
    width = cols[1] - cols[0] + 1
    front = m.copy()
    back = m.copy()
    front[:, : cols[0] + int(0.6 * width)] = False
    back[:, cols[0] + int(0.4 * width):] = False
    g = frame[band][..., :3].astype(np.float32).mean(-1)
    if not front.any() or not back.any():
        return 0.0
    return float(g[front].mean() - g[back].mean())


def gait_check(masks: list[np.ndarray], frames: list[np.ndarray] | None = None) -> dict:
    spreads, heads, leads = [], [], []
    for idx, m in enumerate(masks):
        if frames is not None:
            leads.append(_lead(frames[idx], m))
        ys = np.nonzero(m.any(1))[0]
        if len(ys) == 0:
            spreads.append(0.0)
            heads.append(0.0)
            continue
        top, bot = ys.min(), ys.max()
        h = bot - top + 1
        low = m[int(bot - 0.25 * h):bot + 1]
        xs = np.nonzero(low.any(0))[0]
        spreads.append((xs.max() - xs.min() + 1) / h if len(xs) else 0.0)
        heads.append(float(top))
    s = np.array(spreads) - np.mean(spreads)
    S = np.abs(np.fft.rfft(s))
    k = int(np.argmax(S[1:]) + 1) if len(S) > 1 else 0
    strength = float(S[k] / len(s)) if len(S) > 1 else 0.0
    out = {"spread": [round(float(v), 4) for v in spreads], "spectrum": [round(float(v), 4) for v in S],
           "k_spread": k, "strength": strength, "head": heads}
    if leads:
        la = np.array(leads) - np.mean(leads)
        L = np.abs(np.fft.rfft(la))
        if len(L) > 2 and float(np.abs(la).max()) > 6.0:  # only when the depth cue is clearly present
            out["lead"] = [round(float(v), 2) for v in leads]
            out["lead_spectrum"] = [round(float(v), 2) for v in L]
            # same leg twice: the lead pattern repeats exactly, so the once-per-loop harmonic vanishes
            out["k_lead"] = 2 if L[1] < 0.25 * L[2] else 1
    return out
