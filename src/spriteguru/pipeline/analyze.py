"""The analyzer: a pure function of pixels plus spec (sections 9–14).

matte → layout → (pixel reconstruction) → registration → temporal → metrics → score.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .. import choreo as choreo_lib
from ..qa import metrics as qa_metrics
from ..qa.score import score as q_score
from ..spec import Finding, FrameInfo, Report, SpriteSpec
from . import layout as layout_mod
from . import matte as matte_mod
from . import register as reg_mod
from . import temporal as temporal_mod


@dataclass
class GuideInfo:
    """What the analyzer needs from a guide canvas (6.3), serializable."""

    cells: list[tuple[int, int, int, int]]
    exclude: list[tuple[int, int, int, int]]
    char_h: float
    rows: int
    cols: int
    figure_mask: np.ndarray | None = None
    seeds: np.ndarray | None = None
    mode: str = "skeleton"
    expected: list[tuple[float, float]] | None = None  # intended offsets, fractions of height, forward = +x

    @classmethod
    def from_guide(cls, guide) -> "GuideInfo":
        sign = -1.0 if guide.meta.get("facing") == "W" else 1.0
        exp = [(sign * a, b) for a, b in guide.meta.get("expected", [])] or None
        return cls(cells=[f.rect for f in guide.frames], exclude=[guide.plan.strip_rect] if guide.plan.strip else [],
                   char_h=float(guide.char_h), rows=guide.plan.rows, cols=guide.plan.cols,
                   figure_mask=guide.figure_mask, seeds=guide.background_seeds(),
                   mode=guide.meta.get("mode", "skeleton"), expected=exp)


@dataclass
class Analysis:
    report: Report
    frames: list[np.ndarray]  # final aligned frames, playback order
    durations: list[int]
    matte: matte_mod.Matte
    layout: layout_mod.Layout
    aligned: reg_mod.Aligned | None
    temporal: temporal_mod.Temporal | None
    crops: list[reg_mod.Crop] = field(default_factory=list)
    pixel: dict = field(default_factory=dict)

    @property
    def findings(self) -> list[Finding]:
        return self.report.findings


def durations_for(spec: SpriteSpec | None, n: int, ch: choreo_lib.Choreo | None) -> list[int]:
    if spec is None:
        return [83] * n
    if not spec.loop and ch is not None and ch.timing_ms and len(ch.timing_ms) == n:
        return list(ch.timing_ms)
    return [int(round(1000 / max(1, spec.fps)))] * n


def scale_measurable(spec: SpriteSpec | None, ch: choreo_lib.Choreo | None) -> list[bool] | None:
    """Per choreography frame, whether its pose is one the scale measures can read (R13)."""
    if spec is None or ch is None or ch.mode != "skeleton":
        return None
    from ..figure import bounds, skeleton
    from ..figure import scale_measurable as measurable
    from ..guide import proportions, props_for

    prop, props = proportions(spec), props_for(ch)
    standing = bounds(skeleton({}, prop, spec.view, spec.facing, props))[3]
    return [measurable(skeleton(f.pose, prop, spec.view, spec.facing, props), f.pose, standing) for f in ch.frames]


def analyze(image, spec: SpriteSpec | None = None, *, guide: GuideInfo | None = None,
            reference: np.ndarray | None = None, prior: layout_mod.Prior | None = None,
            grid: tuple[int, int] | None = None) -> Analysis:
    timings = {}
    t0 = time.perf_counter()
    rgba = matte_mod.ingest(image)
    pixel = spec is not None and spec.style.kind == "pixel"
    ch = None
    if spec is not None:
        try:
            ch = choreo_lib.choreography(spec.action, spec.frames, facing=spec.facing, kind=spec.character.kind)
        except KeyError:
            ch = None
    char_h = guide.char_h if guide else None
    kind = spec.character.kind if spec else "character"
    reg_mode = ch.mode if ch is not None and ch.mode != "skeleton" else "character"
    palette = spec.character.palette if spec else None
    untint = bool(spec) and matte_mod.expects_translucency(spec.character.description, spec.character.kind,
                                                             spec.character.blend)
    matte = matte_mod.key_matte(rgba, seeds=guide.seeds if guide else None, char_h=char_h, pixel=pixel,
                                ref_palette=palette or None, untint_smoke=untint)
    timings["matte"] = time.perf_counter() - t0

    # layout prior from the guide, the spec, or an explicit grid (e.g. a Retro Diffusion sheet)
    if prior is None:
        if guide is not None:
            prior = layout_mod.Prior(n=len(guide.cells), rows=guide.rows, cols=guide.cols, cells=guide.cells,
                                     exclude=guide.exclude, detached=reg_mode == "effect")
        elif grid is not None:
            cols, rows = grid
            H, W = rgba.shape[:2]
            cw, chh = W // cols, H // rows
            n = spec.frames if spec else cols * rows
            cells = [((i % cols) * cw, (i // cols) * chh, (i % cols + 1) * cw, (i // cols + 1) * chh) for i in range(n)]
            prior = layout_mod.Prior(n=n, rows=rows, cols=cols, cells=cells)
        else:
            prior = layout_mod.Prior(n=spec.frames if spec else None)
    t1 = time.perf_counter()
    findings: list[Finding] = qa_metrics.background_findings(matte.signals)

    # ML fallback when the key cannot model the background
    if matte.path == "key" and matte.signals.get("uniformity", 1.0) < 0.6 and matte_mod.ml_available():
        rough = layout_mod.detect(matte.mask, prior)
        boxes = [_pad_box(f.region, rgba.shape, 0.05) for f in rough.frames]
        try:
            matte = matte_mod.ml_matte(rgba, boxes, key=matte, pixel=pixel)
            for f in findings:
                if f.metric == "background":
                    f.auto_fixed = True
                    f.message += "; ML matte applied"
        except Exception as e:  # model download failed or offline
            findings.append(Finding(metric="background", level="info", message=f"ML matte unavailable: {e}"))
    lay = layout_mod.detect(matte.mask, prior)
    timings["layout"] = time.perf_counter() - t1
    findings += lay.findings

    removed = matte.mask & ~lay.clean_mask
    if removed.any():
        from scipy import ndimage as _nd

        removed = _nd.binary_dilation(removed, iterations=2) & ~_nd.binary_erosion(lay.clean_mask, iterations=1)
    crops = []
    poses_measurable = scale_measurable(spec, ch) if ch and len(ch.frames) == len(lay.frames) else None
    measurable = [poses_measurable[f.index] for f in lay.frames] if poses_measurable else None
    for f in lay.frames:
        region_mask = (lay.labels == f.label) & ~removed
        x0, y0, x1, y1 = f.box
        x0, y0 = max(0, x0 - 1), max(0, y0 - 1)
        x1, y1 = min(rgba.shape[1], x1 + 1), min(rgba.shape[0], y1 + 1)
        crop = matte.rgba[y0:y1, x0:x1].copy()
        crop[~region_mask[y0:y1, x0:x1]] = 0
        air = bool(ch and len(ch.frames) == len(lay.frames) and ch.frames[f.index].airborne)
        crops.append(reg_mod.Crop(crop, (x0, y0), f.region, airborne=air))

    report = Report(spec=spec, layout=lay.to_dict(), matte=matte.signals)
    if not crops:
        report.findings = findings + [Finding(metric="frame_count", level="fail", severity=3, value=0,
                                              message="no frames found", remedy="reroll")]
        report.score, report.accepted = q_score(report.findings)
        return Analysis(report, [], [], matte, lay, None, None)

    pixel_info: dict = {}
    if pixel:
        from . import pixel as pixel_mod

        t2 = time.perf_counter()
        crops, pixel_info, pfind = pixel_mod.reconstruct(crops, spec, reference_palette=palette or None)
        findings += pfind
        timings["pixel"] = time.perf_counter() - t2

    t3 = time.perf_counter()
    ref_for_facing = reference
    if reference is not None and pixel and pixel_info.get("pitch"):
        ref_for_facing = None  # reference is at source resolution; neighbours carry the facing check
    aligned = reg_mod.register(crops, loop=bool(spec.loop) if spec else False,
                               harmonics=ch.harmonics if ch else None, pixel=pixel,
                               top_down=bool(spec and spec.view == "top-down"), reference=ref_for_facing,
                               mirrorable=spec.character.mirrorable if spec else True,
                               root_motion=bool(spec and spec.motion == "root-motion"), mode=reg_mode,
                               expected=guide.expected if guide is not None else None, measurable=measurable)
    timings["register"] = time.perf_counter() - t3
    findings += aligned.findings

    t4 = time.perf_counter()
    tmp = temporal_mod.analyze(aligned.frames, loop=bool(spec.loop) if spec else False,
                               action=spec.action if spec else "", sprite_h=aligned.sprite_h,
                               lift=aligned.info.get("lift"), impact=ch.impact if ch else None,
                               apex=ch.apex if ch else None, returns_to_start=bool(ch and ch.returns_to_start),
                               check_order=guide is None,
                               frontal=bool(spec and spec.view == "top-down" and spec.facing in ("N", "S")),
                               subtle=bool(spec and (spec.action == "idle" or (ch and ch.amplitude == "subtle"))),
                               kind=kind)
    timings["temporal"] = time.perf_counter() - t4
    findings += tmp.findings
    order = tmp.order

    # metrics that compare against the guide and the reference
    extra: dict = {}
    if guide is not None and guide.figure_mask is not None and len(lay.frames) == len(guide.cells):
        fmasks = [(lay.labels == f.label) & lay.clean_mask for f in lay.frames]
        gmasks = []
        for cell in guide.cells:
            g = np.zeros_like(guide.figure_mask)
            x0, y0, x1, y1 = cell
            g[y0:y1, x0:x1] = guide.figure_mask[y0:y1, x0:x1]
            gmasks.append(g)
        ious, pf, reach = qa_metrics.pose_conformance(fmasks, gmasks, guide.char_h,
                                               band=0.15 if kind == "effect" else 0.08, reach=kind != "effect")
        extra["pose_iou"] = [round(v, 4) for v in ious]
        if kind == "effect":  # K16: scale-normalized conformance cannot tell a burst from a fading wisp
            ratios = [float(fm.sum()) / max(float(gm.sum()), 1.0) for fm, gm in zip(fmasks, gmasks)]
            garea = [float(gm.sum()) for gm in gmasks]
            extra["effect_size"] = [round(r, 3) for r in ratios]
            med = float(np.median(ratios)) if ratios else 0.0
            # a jump against the previous frame counts only where the guide shrinks or holds (fading):
            # while an effect grows, painted glow legitimately scales differently from one shape to the next
            big = [i for i, r in enumerate(ratios) if med > 0 and
                   (r > 2.5 * med or (i > 0 and ratios[i - 1] > 0 and garea[i] <= garea[i - 1] * 1.05
                                      and r > 1.45 * ratios[i - 1]))]
            if big:
                pf.append(Finding(metric="effect_size", level="warn", severity=2, frames=big,
                                  value=round(max(ratios[i] for i in big), 3), threshold=1.45,
                                  message=f"frames {', '.join(str(i + 1) for i in big)} are much larger than their "
                                          "phase of the effect", remedy="frame_repair"))
        extra["pose_reach"] = reach
        findings += pf
    if kind != "effect":  # effects flicker in colour and glow by design (K3)
        id_info, idf = qa_metrics.identity(aligned.frames, reference if not pixel else None, palette or None)
        if reference is not None and not pixel:
            cov_info, cov_f = qa_metrics.palette_coverage(aligned.frames, reference)
            id_info.update(cov_info)
            idf += cov_f
        extra["identity"] = id_info
        findings += idf
        sharp, sf = qa_metrics.sharpness(aligned.frames)
        extra["sharpness"] = [round(v, 2) for v in sharp]
        if not pixel:
            findings += sf

    final = [aligned.frames[i] for i in order]
    durs = durations_for(spec, len(final), ch)
    frames_info = []
    for new_i, i in enumerate(order):
        f = lay.frames[i]
        c = crops[i]
        ax, ay = aligned.anchors[i]
        frames_info.append(FrameInfo(index=new_i, box=f.box, region=f.region,
                                     anchor=(c.offset[0] + ax, c.offset[1] + ay),
                                     offset=tuple(float(v) for v in aligned.placements[i]),
                                     airborne=aligned.airborne[i], clipped=f.clipped, scale=aligned.scales[i],
                                     flipped=aligned.flipped[i], duration_ms=durs[new_i]))
    report.findings = findings
    report.frames = frames_info
    report.canvas = aligned.canvas
    report.pivot = aligned.pivot
    report.order = order
    report.temporal = {**tmp.to_dict(), "registration": aligned.info, **extra}
    report.pixel = pixel_info
    report.score, report.accepted = q_score(findings)
    timings["total"] = time.perf_counter() - t0
    report.timings_ms = {k: round(v * 1000, 1) for k, v in timings.items()}
    return Analysis(report, final, durs, matte, lay, aligned, tmp, crops, pixel_info)


def _pad_box(box, shape, frac):
    x0, y0, x1, y1 = box
    px, py = int((x1 - x0) * frac), int((y1 - y0) * frac)
    return max(0, x0 - px), max(0, y0 - py), min(shape[1], x1 + px), min(shape[0], y1 + py)


def _union_crop(frames: list[np.ndarray], pad: int) -> list[np.ndarray]:
    alpha = np.zeros(frames[0].shape[:2], bool)
    for f in frames:
        alpha |= f[..., 3] > 0
    ys, xs = np.nonzero(alpha)
    if len(ys) == 0:
        return frames
    H, W = alpha.shape
    y0, y1 = max(0, ys.min() - pad), min(H, ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(W, xs.max() + 1 + pad)
    h = int(np.ceil((y1 - y0) / 4) * 4)
    w = int(np.ceil((x1 - x0) / 4) * 4)
    out = []
    for f in frames:
        c = np.zeros((h, w, 4), np.uint8)
        sub = f[y0:y1, x0:x1]
        c[: sub.shape[0], : sub.shape[1]] = sub
        out.append(c)
    return out


def analyze_frames(frames: list[np.ndarray], spec: SpriteSpec, *, reference: np.ndarray | None = None,
                   extra: dict | None = None, register_frames: bool | None = None) -> Analysis:
    """Frame routes (vector renders, video-derived frames): frames arrive with true alpha, so
    matte and layout are skipped. Vector frames share one exact canvas; video frames are
    registered like any sheet (13.7)."""
    t0 = time.perf_counter()
    extra = extra or {}
    findings: list[Finding] = list(extra.get("findings", []))
    ch = None
    try:
        ch = choreo_lib.choreography(spec.action, spec.frames, facing=spec.facing, kind=spec.character.kind)
    except KeyError:
        pass
    is_video = register_frames if register_frames is not None else "video" in extra
    crops = []
    for i, f in enumerate(frames):
        a = f[..., 3] > 0
        ys, xs = np.nonzero(a)
        if len(ys) == 0:
            crops.append(reg_mod.Crop(f, (0, 0), (0, 0, f.shape[1], f.shape[0])))
            continue
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        air = bool(ch and len(ch.frames) == len(frames) and ch.frames[i].airborne)
        crops.append(reg_mod.Crop(f[y0:y1, x0:x1].copy() if is_video else f, (int(x0), int(y0)) if is_video else (0, 0),
                                  (0, 0, f.shape[1], f.shape[0]), airborne=air))
    pixel = spec.style.kind == "pixel"
    if is_video:
        aligned = reg_mod.register(crops, loop=spec.loop, harmonics=ch.harmonics if ch else None, pixel=pixel,
                                   reference=reference, mirrorable=spec.character.mirrorable)
        final_frames = aligned.frames
        findings += aligned.findings
        sprite_h = aligned.sprite_h
        pivot, canvas = aligned.pivot, aligned.canvas
        lift = aligned.info.get("lift")
        reg_info = aligned.info
    else:
        final_frames = _union_crop(frames, pad=max(2, frames[0].shape[0] // 50))
        masks = [f[..., 3] >= 128 for f in final_frames]
        rows = [np.nonzero(m.any(1))[0] for m in masks]
        sprite_h = float(np.median([r.max() - r.min() + 1 for r in rows if len(r)])) if rows else 1.0
        grounds = [int(r.max()) for r in rows if len(r)]
        H, W = final_frames[0].shape[:2]
        xs = [reg_mod.anchor_of(m)[0] for m in masks]
        pivot = (float(np.median(xs) / W), float(np.max(grounds) / H) if grounds else 0.94)
        canvas = (W, H)
        lift = [float(max(grounds) - g) for g in grounds] if grounds else None
        reg_info = {"sprite_h": sprite_h}
        aligned = None
    tmp = temporal_mod.analyze(final_frames, loop=spec.loop, action=spec.action, sprite_h=sprite_h, lift=lift,
                               impact=ch.impact if ch else None, apex=ch.apex if ch else None,
                               returns_to_start=bool(ch and ch.returns_to_start),
                               check_order=False, frontal=spec.view == "top-down" and spec.facing in ("N", "S"),
                               subtle=spec.action == "idle" or bool(ch and ch.amplitude == "subtle"),
                               kind=spec.character.kind)
    findings += tmp.findings
    id_info, idf = ({}, []) if spec.character.kind == "effect" else \
        qa_metrics.identity(final_frames, None, spec.character.palette or None)
    if reference is not None and spec.character.kind != "effect" and spec.style.kind != "pixel":
        cov_info, cov_f = qa_metrics.palette_coverage(final_frames, reference)
        id_info.update(cov_info)
        idf += cov_f
    findings += idf
    durs = durations_for(spec, len(final_frames), ch)
    frames_info = [FrameInfo(index=i, box=(0, 0, canvas[0], canvas[1]), region=(0, 0, canvas[0], canvas[1]),
                             anchor=(pivot[0] * canvas[0], pivot[1] * canvas[1]), duration_ms=durs[i])
                   for i in range(len(final_frames))]
    report = Report(spec=spec, findings=findings, frames=frames_info, canvas=canvas, pivot=pivot,
                    order=list(range(len(final_frames))),
                    matte={"path": "alpha" if not is_video else "video"},
                    layout={"hypothesis": "frames", "confidence": 1.0},
                    temporal={**tmp.to_dict(), "registration": reg_info, "identity": id_info,
                              **{k: v for k, v in extra.items() if k not in ("findings", "video_bytes")}})
    report.score, report.accepted = q_score(findings)
    report.timings_ms = {"total": round((time.perf_counter() - t0) * 1000, 1)}
    dummy_matte = matte_mod.Matte(rgba=final_frames[0], mask=final_frames[0][..., 3] > 0, path="alpha")
    dummy_layout = layout_mod.Layout([], np.zeros((1, 1), np.int32), "frames", 1.0, {}, np.zeros((1, 1), bool),
                                     {"h": [], "v": []}, 0, 0, [])
    return Analysis(report, final_frames, durs, dummy_matte, dummy_layout, aligned, tmp, crops)
