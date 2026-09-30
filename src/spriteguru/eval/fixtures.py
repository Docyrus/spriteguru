"""Golden fixtures: deterministic sheets with ground truth, one per failure mode (docs/failure-modes.md).

Each case renders coloured figures from the choreography onto an imperfect chroma background and
records every frame's true mask, box, torso point, ground contact and playback index.
"""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw

from .. import choreo as choreo_lib
from ..color import hex_to_rgb
from ..figure import Proportions, Props, bounds, character_skin, render, skeleton
from ..spec import Character, SpriteSpec, Style
from ..synth import Degrade, background, draw_grid_lines, draw_labels, draw_shadow, draw_strays, finish

GROUND = 0.92


@dataclass
class Case:
    id: str
    covers: list[str]
    action: str = "walk"
    frames: int = 8
    cols: int = 4
    rows: int = 2
    cell: tuple[int, int] = (320, 352)
    style: str = "hd-cartoon"
    key: str = "#00FF00"
    strip: bool = True
    seed: int = 1
    degrade: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)
    expect: dict = field(default_factory=dict)  # findings: [metrics], no_fail: bool, count: int
    guided: bool = True  # analyzer gets the grid prior (and the strip exclusion)
    pixel_height: int = 40


CASES: list[Case] = [
    Case("clean-walk8", ["R1", "R2", "R3", "T2", "L12"], expect={"no_fail": True, "accept": True}),
    Case("gradient-noise", ["M1", "M2"], degrade={"gradient": 34, "vignette": 0.22, "noise": 6},
         expect={"no_fail": True}),
    Case("soft-spill", ["M3", "M6"], action="run", frames=6, cols=3, degrade={"blur": 1.3, "spill": 0.45, "noise": 2},
         expect={"no_fail": True}),
    Case("arm-gap-pockets", ["M4", "M5"], action="cast", frames=6, cols=3, degrade={"noise": 3},
         expect={"no_fail": True, "pockets": True}),
    Case("grid-lines-labels", ["L1", "L2"], action="jump", frames=6, cols=3, extras={"grid_lines": True,
         "labels": True, "strays": True}, expect={"findings": ["extraneous_marks"]}),
    Case("detached-parts", ["L3"], action="idle", frames=4, cols=2, extras={"halo": True},
         expect={"no_fail": True}),
    Case("sword-cross", ["L4"], action="attack-melee", frames=6, cols=3, cell=(260, 352), extras={"reach": 1.25},
         guided=True),
    Case("touching", ["L5"], action="run", frames=6, cols=6, rows=1, cell=(170, 360), strip=False, guided=False,
         extras={"squeeze": True}),
    Case("offset-rows", ["L6"], strip=False, guided=False, extras={"row_offset": 0.3}),
    Case("irregular-spacing", ["L7"], strip=False, guided=False, degrade={"jitter": 0.0},
         extras={"spacing_jitter": 0.14}),
    Case("missing-frame", ["L8"], extras={"missing": 5}, expect={"findings": ["frame_count"]}),
    Case("clipped-frame", ["L10"], strip=False, guided=False, extras={"clip": 0}, expect={"findings": ["clipping"]}),
    Case("cast-shadows", ["L11"], extras={"shadows": True}, key="#FF00FF", expect={"no_fail": True, "shadow": True}),
    Case("baseline-jitter", ["R1", "R2", "R6"], degrade={"jitter": 0.03}, expect={"no_fail": True}),
    Case("jump-airborne", ["R4"], action="jump", frames=6, cols=3, expect={"no_fail": True, "lift": True}),
    Case("scale-drift", ["R7", "R9"], extras={"scale": (3, 0.07)}, expect={"findings": ["scale"]}),
    # a leap intro drawn with a shield and bulky gauntlets: raised fists, a tuck and a kneel fake scale drift, and a
    # grounded frame a pixel off the line fakes a second rise
    Case("leap-gauntlets", ["R13", "T10"], action="intro-leap", frames=8, cols=4, rows=2,
         extras={"shield": True, "gauntlets": 3.0}, expect={"no_fail": True, "lift": True, "absent": ["scale", "pose"]}),
    Case("flipped-frame", ["R8"], action="attack-melee", frames=6, cols=3, cell=(360, 352), extras={"flip": 2},
         expect={"findings": ["facing"]}),
    Case("shuffled", ["T1"], action="crouch", frames=6, cols=3, strip=False, guided=False,
         extras={"shuffle": [0, 3, 1, 4, 2, 5]}, expect={"findings": ["order"]}),
    Case("shuffled-walk", ["T1", "T2", "T6"], strip=False, guided=False, extras={"shuffle": [0, 3, 1, 6, 2, 5, 4, 7]},
         expect={"findings": ["gait"], "order_ambiguous": True}),
    Case("duplicate", ["T3"], strip=False, guided=False, extras={"duplicate": (4, 3)},
         expect={"findings": ["duplicates"], "order_ambiguous": True}),
    Case("half-cycle", ["T5", "T6"], strip=False, guided=False, frames=4, cols=4, rows=1, extras={"half": True},
         expect={"findings": ["gait"], "order_ambiguous": True}),
    Case("broken-loop", ["T4", "T5"], action="idle", frames=4, cols=4, rows=1, strip=False, guided=False,
         extras={"replace_last": ("jump", 0)}, expect={"findings": ["loop_seam"], "order_ambiguous": True,
                                                       "wrong_pose": True}),
    Case("same-leg-twice", ["T6"], strip=False, guided=False, extras={"same_leg": True},
         expect={"findings": ["gait"], "order_ambiguous": True}),
    Case("idle-exaggerated", ["T7"], action="idle", frames=4, cols=4, rows=1, extras={"breath_scale": 9.0},
         expect={"findings": ["idle_motion"]}),
    Case("pixel-clean", ["X1", "X2", "X5"], style="pixel", strip=False, guided=True,
         extras={"pitch": 6.3}, degrade={"noise": 2}, expect={"pixel_match": 0.85}),
    Case("pixel-drift", ["X3", "X4", "X6", "X7", "X8"], style="pixel", strip=False, guided=True,
         extras={"pitch": 7.1, "drift": 0.22, "per_frame_pitch": 0.08, "cell_gradient": 10, "aa": 0.6},
         degrade={"noise": 3}, expect={"pixel_match": 0.75}),
    Case("alpha-input", ["M8"], strip=False, guided=False, extras={"alpha": True}, expect={"no_fail": True}),
    Case("icc-profile", ["M11"], extras={"icc": True}, expect={"no_fail": True}),
    Case("non-uniform-bg", ["M7"], strip=False, guided=True, extras={"two_tone": True},
         expect={"findings": ["background"]}),
    Case("key-near-palette", ["M9"], key="#00FF00", extras={"tint_green": True},
         expect={"findings": ["key_contamination"]}),
]


def case_by_id(cid: str) -> Case:
    for c in CASES:
        if c.id == cid:
            return c
    raise KeyError(cid)


def spec_for(case: Case) -> SpriteSpec:
    loop = choreo_lib.default_loop(case.action)
    style = Style(kind="pixel", pixel_height=case.pixel_height, palette_size=16) if case.style == "pixel" \
        else Style(kind=case.style)
    return SpriteSpec(character=Character(description="synthetic fixture character", mirrorable=True,
                                          heads_tall=5.0 if case.style != "pixel" else 4.0),
                      action=case.action, frames=case.frames, loop=loop, style=style, key=case.key)


def build(case: Case) -> tuple[Image.Image, dict]:
    """Render the sheet and its ground truth."""
    rng = np.random.default_rng(case.seed)
    ex = case.extras
    ch = choreo_lib.choreography(case.action, case.frames)
    prop = Proportions(4.0 if case.style == "pixel" else 5.0)
    props = Props(weapon="weapon" in ch.props, shield=case.id == "flipped-frame" or bool(ex.get("shield")))
    skin = character_skin(case.seed + 7)
    if case.style == "pixel":
        skin.outline_w = 1.0 / case.pixel_height  # one logical pixel
    if ex.get("tint_green"):
        skin.colors["torso"] = (60, 225, 60)
        skin.colors["near"] = (50, 205, 50)
    cw, chh = case.cell
    ncols = case.cols + (1 if case.strip else 0)
    W, H = ncols * cw, case.rows * chh
    poses = [dict(f.pose) for f in ch.frames]
    order = list(range(case.frames))  # playback index drawn in each sheet slot
    if ex.get("breath_scale"):
        for p in poses:
            p["breath"] = p.get("breath", 0.0) * ex["breath_scale"]
            p["lean"] = p.get("lean", 0.0) + 4 * p["breath"] * 10
    if ex.get("half"):
        full = choreo_lib.choreography(case.action, 8)
        poses = [dict(full.frames[i].pose) for i in range(4)]
    if ex.get("same_leg"):
        poses = poses[:4] + poses[:4]
    if ex.get("replace_last"):
        act, idx = ex["replace_last"]
        other = choreo_lib.choreography(act, choreo_lib.default_frames(act))
        poses[-1] = dict(other.frames[idx].pose)
    if ex.get("shuffle"):
        order = list(ex["shuffle"])
    if ex.get("duplicate"):
        a, b = ex["duplicate"]
        poses[a] = dict(poses[b])
    parts_all = [skeleton(p, prop, "side", "E", props) for p in poses]
    if ex.get("gauntlets"):  # bulky armoured hands, as image models draw them (R13)
        for pp in parts_all:
            for part in pp:
                if part.name.startswith("hand"):
                    part.width *= ex["gauntlets"]
    tops = [bounds(pp)[3] for pp in parts_all]
    xmin = min(bounds(pp)[0] for pp in parts_all)
    xmax = max(bounds(pp)[2] for pp in parts_all)
    reach = ex.get("reach", 1.0)
    char_h = min((GROUND - 0.05) * chh / max(tops), 0.9 * cw / (xmax - xmin) * reach, 0.78 * chh)
    if case.style == "pixel":
        char_h = case.pixel_height
    shift = -(xmin + xmax) / 2

    d = Degrade(seed=case.seed, **case.degrade)
    bg = background((W, H), case.key, d, rng)
    if ex.get("two_tone"):
        yy, xx = np.mgrid[0:H, 0:W]
        split = (xx + 0.6 * yy) > W * 0.55
        bg[split] = np.array([214, 196, 150], np.float32) + rng.normal(0, 4, (int(split.sum()), 3))
    layer = np.zeros((H, W, 4), np.float32)
    truth_frames = []
    masks = []
    slots = [s for s in range(case.frames) if s != ex.get("missing")]
    for slot in slots:
        pidx = order[slot]
        pose = poses[pidx]
        parts = parts_all[pidx]
        flip = ex.get("flip") == slot
        row, col = divmod(slot, case.cols)
        x0 = (col + (1 if case.strip else 0)) * cw
        y0 = row * chh
        gx = x0 + cw / 2 + shift * char_h
        gy = y0 + GROUND * chh
        if ex.get("row_offset") and row == 1:
            gx += ex["row_offset"] * cw
        if ex.get("spacing_jitter"):
            gx += rng.uniform(-1, 1) * ex["spacing_jitter"] * cw
        if ex.get("squeeze"):
            gx += (col - (case.cols - 1) / 2) * -0.0
        if d.jitter:
            gy += rng.uniform(-1, 1) * d.jitter * char_h
            gx += rng.uniform(-1, 1) * d.jitter * char_h
        if ex.get("clip") == slot:
            gx = 0.18 * char_h
        scale = 1.0
        if ex.get("scale") and ex["scale"][0] == slot:
            scale = 1.0 + ex["scale"][1]
        if case.style == "pixel":
            img, origin, tmask = _pixel_figure(parts, char_h, skin, ex, rng, slot)
        else:
            img, origin = render(parts, char_h * scale, skin)
            tmask = None
        if flip:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            origin = (img.width - origin[0], origin[1])
        arr = np.asarray(img).astype(np.float32)
        px, py = int(round(gx - origin[0])), int(round(gy - origin[1]))
        sx0, sy0 = max(0, -px), max(0, -py)
        dx0, dy0 = max(0, px), max(0, py)
        w = min(arr.shape[1] - sx0, W - dx0)
        h = min(arr.shape[0] - sy0, H - dy0)
        src = arr[sy0:sy0 + h, sx0:sx0 + w]
        a = src[..., 3:4] / 255.0
        dst = layer[dy0:dy0 + h, dx0:dx0 + w]
        dst[..., :3] = src[..., :3] * a + dst[..., :3] * (1 - a)
        dst[..., 3:4] = a * 255 + dst[..., 3:4] * (1 - a)
        m = np.zeros((H, W), bool)
        m[dy0:dy0 + h, dx0:dx0 + w] = src[..., 3] > 127
        masks.append(m)
        ys, xs = np.nonzero(m)
        torso = next(p for p in parts if p.name == "torso")
        mid = (torso.points[0] + torso.points[1]) / 2
        tx = gx + (-mid[0] if flip else mid[0]) * char_h * scale
        low = min(pt[1] for p in parts for pt in p.points if p.name.startswith(("foot", "leg")))
        truth_frames.append({
            "slot": slot, "playback": pidx, "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
            if len(xs) else [0, 0, 0, 0],
            "origin": [gx, gy], "torso_x": float(tx), "ground_y": float(ys.max() + 1) if len(ys) else gy,
            "airborne": bool(ch.frames[pidx].airborne) if pidx < len(ch.frames) else False,
            "lift": float(pose.get("air", 0.0) * char_h), "flipped": flip, "scale": scale,
            "logical": tmask,
        })
        if ex.get("shadows") and not ch.frames[pidx].airborne:
            pass

    alpha = layer[..., 3:4] / 255.0
    comp = bg * (1 - alpha) + layer[..., :3] * alpha
    if ex.get("alpha"):
        out = np.dstack([np.clip(layer[..., :3], 0, 255), layer[..., 3]]).astype(np.uint8)
        out[out[..., 3] == 0] = 0
        img = Image.fromarray(out, "RGBA")
    else:
        final = finish(comp, d, rng, alpha[..., 0], case.key)
        img = Image.fromarray(final, "RGB")
    if case.strip and not ex.get("alpha"):
        ref_img, ro = render(skeleton({"hip_n": 5, "hip_f": -5, "shoulder_n": -4, "shoulder_f": 5}, prop, "side", "E",
                                      props), char_h, skin)
        img.paste(ref_img, (int(cw / 2 - ro[0]), int(GROUND * chh - ro[1])), ref_img)
    if ex.get("shadows"):
        for t in truth_frames:
            if not t["airborne"]:
                bw = (t["box"][2] - t["box"][0]) * 0.9
                draw_shadow(img, case.key, t["origin"][0], t["ground_y"] - 1, bw, 0.06 * char_h)
    if ex.get("grid_lines"):
        xs = [(c + (1 if case.strip else 0)) * cw for c in range(1, case.cols)]
        ys = [r * chh for r in range(1, case.rows)]
        draw_grid_lines(img, xs, ys)
    if ex.get("labels"):
        spots = [((s % case.cols + (1 if case.strip else 0)) * cw + 12, (s // case.cols) * chh + 8)
                 for s in range(case.frames)]
        draw_labels(img, spots, size=16)
    if ex.get("strays"):
        draw_strays(img, rng, count=3)
    if ex.get("halo"):
        draw = ImageDraw.Draw(img)
        for t in truth_frames:
            bx0, by0, bx1, by1 = t["box"]
            cx = (bx0 + bx1) / 2
            r = 0.1 * char_h
            draw.ellipse([cx - r, by0 - 0.09 * char_h - r * 0.5, cx + r, by0 - 0.09 * char_h + r * 0.5],
                         fill=(250, 210, 60))
            m = np.zeros((H, W), bool)
            yy, xx = np.mgrid[0:H, 0:W]
            m = (((xx - cx) / r) ** 2 + ((yy - (by0 - 0.09 * char_h)) / (r * 0.5)) ** 2) <= 1
            masks[truth_frames.index(t)] |= m
            t["box"][1] = int(min(t["box"][1], by0 - 0.09 * char_h - r * 0.5))
    truth = {"case": case.id, "covers": case.covers, "size": [W, H], "cell": [cw, chh], "strip": case.strip,
             "cols": case.cols, "rows": case.rows, "char_h": char_h, "frames": truth_frames,
             "expect": case.expect, "guided": case.guided}
    return img, {"truth": truth, "masks": masks}


def _pixel_figure(parts, height: int, skin, ex: dict, rng: np.random.Generator, slot: int):
    """Render true pixel art at logical resolution, then paint it at a drifting, non-integer pitch."""
    logical, origin = render(parts, height, skin, crisp=True, ss=8, margin_px=2)
    L = np.asarray(logical)
    p = ex.get("pitch", 6.0) * (1 + (rng.uniform(-1, 1) * ex.get("per_frame_pitch", 0.0)))
    drift = ex.get("drift", 0.0)
    h, w = L.shape[:2]

    def bounds_for(n):
        k = np.arange(n + 1, dtype=float)
        wob = drift * p * np.sin(2 * math.pi * k / max(6.0, n / 2.3) + rng.uniform(0, 6.28)) if drift else 0.0
        return k * p + wob

    bx, by = bounds_for(w), bounds_for(h)
    bx -= bx[0]
    by -= by[0]
    SW, SH = int(math.ceil(bx[-1])), int(math.ceil(by[-1]))
    xs = np.clip(np.searchsorted(bx, np.arange(SW) + 0.5) - 1, 0, w - 1)
    ys = np.clip(np.searchsorted(by, np.arange(SH) + 0.5) - 1, 0, h - 1)
    src = L[ys[:, None], xs[None, :]].astype(np.float32)
    if ex.get("cell_gradient"):
        fy = (np.arange(SH)[:, None] - by[ys][:, None]) / p
        src[..., :3] += (fy - 0.5)[..., None] * ex["cell_gradient"]
    if ex.get("aa"):
        import cv2

        a = src[..., 3:4] / 255.0
        pre = np.concatenate([src[..., :3] * a, a], -1)
        pre = cv2.GaussianBlur(pre, (0, 0), ex["aa"])
        a2 = pre[..., 3:4]
        src = np.concatenate([np.where(a2 > 1e-3, pre[..., :3] / np.maximum(a2, 1e-3), 0), a2 * 255], -1)
    src = np.clip(src, 0, 255).astype(np.uint8)
    img = Image.fromarray(src, "RGBA")
    o = (origin[0] * p, origin[1] * p)
    return img, o, L


def write(case: Case, root: Path) -> Path:
    d = root / case.id
    d.mkdir(parents=True, exist_ok=True)
    img, data = build(case)
    truth = data["truth"]
    save_kwargs = {}
    if case.extras.get("icc"):
        prof = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
        prof[24:36] = bytes(12)  # ICC header creation date: zeroed so fixtures regenerate byte-identically
        prof[84:100] = bytes(16)  # profile ID (MD5 over the header) likewise
        save_kwargs["icc_profile"] = bytes(prof)
    img.save(d / "sheet.png", **save_kwargs)
    np.savez_compressed(d / "masks.npz", *data["masks"])
    logical = []
    for i, f in enumerate(truth["frames"]):
        if f.get("logical") is not None:
            Image.fromarray(f["logical"], "RGBA").save(d / f"logical_{i}.png")
            logical.append(f"logical_{i}.png")
        f.pop("logical", None)
    truth["logical"] = logical
    truth["spec"] = spec_for(case).model_dump(mode="json")
    (d / "truth.json").write_text(json.dumps(truth, indent=2))
    return d


def _write_id(cid: str, root: Path) -> Path:
    return write(case_by_id(cid), root)


def write_all(root: Path, ids: list[str] | None = None, workers: int | None = None) -> list[Path]:
    from ..parallel import pmap

    todo = [c.id for c in CASES if ids is None or c.id in ids]
    return pmap(_write_id, todo, [root] * len(todo), workers=workers)
