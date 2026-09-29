"""Grid planner (6.2): N frame cells plus a one-cell reference strip on GPT Image 2.5's size rules."""

from __future__ import annotations

import math

import numpy as np
from dataclasses import asdict, dataclass

from . import registry
from .spec import SpriteSpec

MAX_PIXELS = 2560 * 1440  # above this the API calls sizes experimental
MIN_PIXELS = 655_360
MAX_EDGE = 2560  # the API allows 3840; the plan keeps sheets inside 2560 x 1440
CELL_PREF = 512
LAMBDA_EMPTY, LAMBDA_ROWS, LAMBDA_HMIN, LAMBDA_PREF = 0.3, 0.1, 2.0, 0.5


@dataclass
class GridPlan:
    frames: int
    cols: int
    rows: int
    cell_w: int
    cell_h: int
    width: int
    height: int
    cost: float
    strip: bool = True

    def cell_rect(self, i: int) -> tuple[int, int, int, int]:
        r, c = divmod(i, self.cols)
        x0 = (c + (1 if self.strip else 0)) * self.cell_w
        y0 = r * self.cell_h
        return x0, y0, x0 + self.cell_w, y0 + self.cell_h

    def cell_of(self, i: int) -> tuple[int, int]:
        """(row, column), 1-based as the prompt names them."""
        r, c = divmod(i, self.cols)
        return r + 1, c + 1

    @property
    def strip_rect(self) -> tuple[int, int, int, int]:
        return 0, 0, self.cell_w, self.height

    @property
    def ref_rect(self) -> tuple[int, int, int, int]:
        return 0, 0, self.cell_w, self.cell_h

    def to_dict(self) -> dict:
        return asdict(self)


def h_min(spec: SpriteSpec) -> int:
    if spec.style.kind == "pixel":
        ph = spec.style.pixel_height or 48
        return int(math.ceil(4 * ph / 0.95 / 16) * 16)
    return 320


def _round16(x: float) -> int:
    return max(16, int(round(x / 16)) * 16)


def plan(spec: SpriteSpec, *, strip: bool = True, aspect: float | None = None) -> GridPlan:
    """`aspect` overrides the action's cell aspect target, e.g. with a vehicle's own proportions."""
    n = spec.frames
    a_target = float(np.clip(aspect, 0.5, 2.6)) if aspect else registry.aspect_target(spec.action)
    hmin = h_min(spec)
    best: GridPlan | None = None
    best_key = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        if (cols - 1) * rows >= n and cols > 1:
            continue
        empty = cols * rows - n
        ncols = cols + (1 if strip else 0)
        for ch in range(CELL_PREF, 127, -16):
            for a in [a_target * f for f in (0.7, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.3)]:
                cw = _round16(ch * a)
                W, H = ncols * cw, rows * ch
                if W % 16 or H % 16:
                    continue
                if not (1 / 3 <= W / H <= 3) or W * H > MAX_PIXELS or max(W, H) > MAX_EDGE:
                    continue
                if W * H < MIN_PIXELS:
                    continue
                cost = (abs(math.log((cw / ch) / a_target)) + LAMBDA_EMPTY * empty + LAMBDA_ROWS * (rows - 1)
                        + LAMBDA_HMIN * max(0.0, 1 - ch / hmin) + LAMBDA_PREF * max(0.0, 1 - ch / CELL_PREF))
                key = (round(cost, 6), -cw * ch)
                if best_key is None or key < best_key:
                    best_key = key
                    best = GridPlan(n, cols, rows, cw, ch, W, H, round(cost, 4), strip)
    if best is None:
        raise ValueError(f"no feasible grid for {n} frames")
    return best
