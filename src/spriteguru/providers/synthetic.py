"""Offline simulator provider: deterministic stand-ins for every model role.

It lets whole jobs run without keys and without spend (provider mode "synthetic"), and mimics
the defects real models produce so the analyzer, repair loop and studio see realistic input.
Guided edits repaint the grey mannequins of the guide as a coloured character; turnarounds,
videos, pixel loops, SVGs and LLM answers are rendered from the same figure model.

Defects can be forced for E2E scenarios with SPRITEGURU_SYNTH_DEFECTS, e.g.
"c1=flip:2;c2=drop:4" (candidate 1 flips frame 3, candidate 2 drops frame 5), or "repair=none".
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import zlib

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

from ..color import hex_to_rgb
from ..env import get as app_env, present as app_env_present
from ..figure import GREY, Proportions, Props, Skin, character_skin, render, skeleton
from ..synth import Degrade, background, draw_grid_lines, draw_labels, finish
from .base import CallResult, Candidate, ProviderError, ProviderRequest

GREY_LEVELS = {k: np.array(v, np.float32) for k, v in GREY.items()}


def character_seed(description: str) -> int:
    first = description.strip().split(".")[0].strip().lower()
    return zlib.crc32(first.encode()) & 0x7FFFFFFF


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _props_from_text(text: str) -> Props:
    t = text.lower()
    return Props(weapon=any(w in t for w in ("sword", "blade", "axe", "spear", "weapon")),
                 shield="shield" in t, cape="cape" in t or "cloak" in t)


def _key_from_prompt(prompt: str, default: str = "#FF00FF") -> str:
    m = re.search(r"\((#[0-9A-Fa-f]{6})\)", prompt)
    return m.group(1).upper() if m else default


def _character_from_prompt(prompt: str) -> str:
    for pat in (r"CHARACTER \(identical in every frame\): (.+?)\. Same design", r"CHARACTER: (.+?)\. Identical",
                r"(?:VEHICLE|MACHINE|EFFECT) \(identical in every frame\): (.+?)\. Same design",
                r"(?:VEHICLE|MACHINE): (.+?)\. Identical", r"EFFECT \(consistent in every frame\): (.+?)\. Same",
                r"EFFECT: (.+?)\.\n", r"(?:VEHICLE|MACHINE|EFFECT) \(identical to the top-left reference and to image 2\): (.+?)\. Same",
                r"CHARACTER \(identical to the top-left reference and to image 2\): (.+?)\. Same design",
                r"Image 2 shows the character: (.+?)\.$",
                r"VIDEO: (.+?) performs", r"VECTOR CHARACTER: (.+?), full body"):
        m = re.search(pat, prompt, re.S)
        if m:
            return m.group(1)
    return prompt[:80]


def _defects(tag: str) -> list[tuple[str, int]]:
    spec = app_env("SYNTH_DEFECTS", "") or ""
    out = []
    for part in spec.split(";"):
        if "=" not in part:
            continue
        who, what = part.split("=", 1)
        if who.strip() != tag:
            continue
        for item in what.split(","):
            item = item.strip()
            if not item or item == "none":
                continue
            name, _, idx = item.partition(":")
            out.append((name, int(idx) if idx else -1))
    return out


class SyntheticProvider:
    id = "synthetic"

    async def run(self, req: ProviderRequest) -> CallResult:
        delay = float(app_env("SYNTH_DELAY", "0") or 0)
        if delay:  # simulated provider latency (cancellation and concurrency E2E)
            import asyncio

            await asyncio.sleep(delay)
        if req.op == "generate":
            return self._turnaround(req)
        if req.op == "edit":
            return self._edit(req)
        if req.op == "video":
            return self._video(req)
        if req.op == "rd_animate":
            return self._rd(req)
        if req.op == "rd_fix":
            return CallResult([Candidate(req.images[0], "image/png", {})], cost=0.0)
        if req.op == "svg_generate":
            return self._svg(req)
        if req.op == "svg_animate":
            return self._svg_animate(req)
        if req.op == "respond":
            return self._respond(req)
        raise ProviderError(f"synthetic provider does not support {req.op}")

    # -- turnaround ---------------------------------------------------------------

    def _turnaround(self, req: ProviderRequest) -> CallResult:
        from . import synth_objects as so

        desc = _character_from_prompt(req.prompt)
        seed = character_seed(desc)
        W, H = req.size or (2048, 768)
        kind = so.subject_kind(req.prompt)
        source = Image.open(io.BytesIO(req.images[0])) if req.op == "edit" and req.images else None
        if kind != "character":
            cands = []
            for c in range(req.n):
                rng = np.random.default_rng((req.seed or 0) * 31 + c + seed)
                if kind == "effect":
                    glow = so.dominant_color(source) if source is not None else \
                        so.color_from_text(desc, so.GLOW_WORDS, seed, (80, 170, 255))
                    bg = (0, 0, 0) if "(#000000)" in req.prompt else (255, 0, 255)
                    img = so.effect_design(glow, W, H, bg)
                else:
                    col = so.dominant_color(source) if source is not None else so.color_from_text(desc, so.COLOR_WORDS, seed)
                    img = so.object_views(kind, col, W, H, smoke=("tintsmoke", -1) in _defects("turnaround"))
                arr = np.asarray(img.convert("RGB")).astype(np.float32) + rng.normal(0, 2.0, (H, W, 3))
                out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").filter(ImageFilter.GaussianBlur(0.5))
                cands.append(Candidate(_png(out), "image/png", {"synthetic_seed": seed, "kind": kind}))
            return CallResult(cands, request_id=f"synth-{kind}-{seed}", cost=0.0)
        skin = _skin_from_image(source.convert("RGBA"), seed) if source is not None else character_skin(seed)
        props = _props_from_text(desc)
        pixel = "Pixel art" in req.prompt
        cands = []
        for c in range(req.n):
            rng = np.random.default_rng((req.seed or 0) * 31 + c + seed)
            d = Degrade(gradient=10, noise=2.5, seed=int(rng.integers(1 << 30)))
            bg = background((W, H), "#FF00FF", d, rng)
            canvas = Image.fromarray(finish(bg, d, rng), "RGB").convert("RGBA")
            char_h = 0.74 * H
            prop = Proportions(4.0 if pixel else 5.0)
            stand = {"hip_n": 4, "hip_f": -4, "shoulder_n": -10, "shoulder_f": 10, "elbow_n": 10, "elbow_f": 10}
            views = [("top-down", "S"), ("side", "E"), ("side", "W"), ("top-down", "N")]
            for i, (view, facing) in enumerate(views):
                parts = skeleton(stand, prop, view, facing, props)
                img, origin = render(parts, char_h, skin, facing=facing, view=view)
                gx = W * (i + 0.5) / 4
                gy = H * 0.9
                canvas.alpha_composite(img, (int(gx - origin[0]), int(gy - origin[1])))
            out = canvas.convert("RGB").filter(ImageFilter.GaussianBlur(0.5))
            cands.append(Candidate(_png(out), "image/png", {"synthetic_seed": seed}))
        return CallResult(cands, request_id=f"synth-{seed}", cost=0.0)

    # -- guided canvas edit ----------------------------------------------------------

    def _edit(self, req: ProviderRequest) -> CallResult:
        from . import synth_objects as so

        if "turnaround sheet" in req.prompt or "design sheet" in req.prompt:
            return self._turnaround(req)  # a turnaround seeded by an uploaded reference image
        guide = np.asarray(Image.open(io.BytesIO(req.images[0])).convert("RGB"))
        kind = so.subject_kind(req.prompt)
        if kind != "character":
            return self._edit_subject(req, guide, kind)
        mask = np.asarray(Image.open(io.BytesIO(req.mask)).convert("RGBA"))[..., 3] if req.mask else None
        editable = mask < 128 if mask is not None else np.ones(guide.shape[:2], bool)
        desc = _character_from_prompt(req.prompt)
        seed = character_seed(desc)
        key = _key_from_prompt(req.prompt)
        facing = "W" if "facing left" in req.prompt else "E"
        is_repair = req.prompt.startswith(("FRAME FIX", "IDENTITY FIX", "POSE FIX")) or "FIX:" in req.prompt
        cands = []
        for c in range(req.n):
            tag = "repair" if is_repair else f"c{c + 1}"
            defects = _defects(tag)
            if not app_env_present("SYNTH_DEFECTS") and not is_repair and c == 1:
                defects = [("flip", 2), ("scale", 4)]  # the default alternate is visibly worse
            rng = np.random.default_rng((req.seed or 0) * 7919 + c * 104729 + seed)
            if ("black", -1) in defects and req.mask is not None:
                # mimic the live failure: the masked region comes back as a black block
                out = guide.copy()
                out[editable] = 0
                cands.append(Candidate(_png(Image.fromarray(out, "RGB")), "image/png", {"synthetic_defects": defects}))
                continue
            m = re.search(r"about (\d+) pixels tall", req.prompt)
            img = paint(guide, editable, key, character_skin(seed), facing, rng, defects,
                        pixel_height=int(m.group(1)) if m else None)
            cands.append(Candidate(_png(img), "image/png", {"synthetic_defects": defects}))
        return CallResult(cands, request_id=f"synth-edit-{seed}", cost=0.0)

    def _edit_subject(self, req: ProviderRequest, guide: np.ndarray, kind: str) -> CallResult:
        from . import synth_objects as so

        mask = np.asarray(Image.open(io.BytesIO(req.mask)).convert("RGBA"))[..., 3] if req.mask else None
        editable = mask < 128 if mask is not None else np.ones(guide.shape[:2], bool)
        desc = _character_from_prompt(req.prompt)
        seed = character_seed(desc)
        key = _key_from_prompt(req.prompt)
        H, W = guide.shape[:2]
        is_repair = "FIX:" in req.prompt
        cands = []
        for c in range(req.n):
            defects = _defects("repair" if is_repair else f"c{c + 1}")
            if not app_env_present("SYNTH_DEFECTS") and not is_repair and c == 1:
                defects = [("drop", 2)]  # the default alternate misses a frame
            rng = np.random.default_rng((req.seed or 0) * 7919 + c * 104729 + seed)
            if ("black", -1) in defects and req.mask is not None:
                out = guide.copy()
                out[editable] = 0
                cands.append(Candidate(_png(Image.fromarray(out, "RGB")), "image/png", {"synthetic_defects": defects}))
                continue
            if kind == "effect":
                additive = key == "#000000"
                d = Degrade(noise=1.0, seed=int(rng.integers(1 << 30)))
                bg = np.zeros((H, W, 3), np.float32) if additive else background((W, H), key, d, rng)
                ref = so.reference_rgba(req.images[1]) if len(req.images) > 1 else None
                glow = so.dominant_color(Image.fromarray(ref, "RGBA")) if ref is not None else \
                    so.color_from_text(desc, so.GLOW_WORDS, seed, (80, 170, 255))
                img = so.paint_effect(guide, editable, glow, additive, bg, rng, defects)
            else:
                d = Degrade(gradient=10, noise=2.0, seed=int(rng.integers(1 << 30)))
                bg = finish(background((W, H), key, d, rng), d, rng)
                ref = so.reference_rgba(req.images[1]) if len(req.images) > 1 else \
                    np.asarray((so.draw_vehicle if kind == "vehicle" else so.draw_machine)("side", 200, (120, 120, 120)))
                img = so.paint_objects(guide, editable, bg, ref, rng, defects)
            px = re.search(r"about (\d+) pixels (?:tall|across)", req.prompt) if "Pixel art" in req.prompt else None
            if px:  # pixel style: flat cells on a lattice, pitch from the grey figures' size
                g = guide.astype(int)
                grey = editable & ((g.max(-1) - g.min(-1)) < 20) & (g.mean(-1) > 90)
                lbl, n = ndimage.label(ndimage.binary_dilation(grey, iterations=8))
                ext = max((max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start) if kind == "effect"
                           else sl[0].stop - sl[0].start) for sl in ndimage.find_objects(lbl)) if n else 0
                if ext:
                    rgba = img.convert("RGBA")
                    img = _pixelate(rgba, max(2.0, ext / int(px.group(1))), rng).convert("RGB")
            cands.append(Candidate(_png(img), "image/png", {"synthetic_defects": defects, "kind": kind}))
        return CallResult(cands, request_id=f"synth-{kind}-edit-{seed}", cost=0.0)

    # -- video ---------------------------------------------------------------------

    def _video(self, req: ProviderRequest) -> CallResult:
        import av

        from .. import choreo

        desc = _character_from_prompt(req.prompt)
        seed = character_seed(desc)
        key = _key_from_prompt(req.prompt, "#00FF00")
        m = re.search(r"performs (?:a |an )?(.+?) in place", req.prompt)
        title = m.group(1) if m else "walk cycle"
        action = next((a for a in ("walk", "run", "idle", "climb", "push") if a in title), "walk")
        start = Image.open(io.BytesIO(req.images[0])).convert("RGB")
        W, H = start.size
        W, H = W - W % 2, H - H % 2
        fps = 24
        dur = float(req.params.get("duration", 5))
        period = {"walk": 1.0, "run": 0.66, "idle": 2.0}.get(action, 1.0)
        ch = choreo.choreography(action, 8)
        # the real model animates the start frame, so the character keeps the start frame's colours
        arr = np.asarray(start).astype(int)
        kr = np.array([int(key[i:i + 2], 16) for i in (1, 3, 5)])
        fg = np.abs(arr - kr).sum(-1) > 90
        skin = _skin_from_image(Image.fromarray(np.dstack([arr, fg * 255]).astype(np.uint8), "RGBA"), seed)
        props = _props_from_text(desc)
        rng = np.random.default_rng(seed + (req.seed or 0))
        char_h = 0.6 * H
        buf = io.BytesIO()
        container = av.open(buf, mode="w", format="mp4")
        stream = container.add_stream("libx264", rate=fps)
        stream.width, stream.height = W, H
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "18"}
        prop = Proportions(5.0)
        view = "top-down" if "Top-down" in req.prompt else "side"
        facing = ("S" if "toward the camera" in req.prompt else "N" if "away from the camera" in req.prompt
                  else "W" if "facing left" in req.prompt else "E")
        n = int(dur * fps)
        for k in range(n):
            t = (k / fps) / period
            phase = t % 1.0
            pos = phase * len(ch.frames)
            i0 = int(pos) % len(ch.frames)
            i1 = (i0 + 1) % len(ch.frames)
            u = pos - int(pos)
            pose = {kk: ch.frames[i0].pose.get(kk, 0) * (1 - u) + ch.frames[i1].pose.get(kk, 0) * u
                    for kk in set(ch.frames[i0].pose) | set(ch.frames[i1].pose)}
            d = Degrade(gradient=8 + 4 * math.sin(k / 30), noise=2.0, seed=seed + k)
            bg = background((W, H), key, d, np.random.default_rng(seed + k))
            frame = Image.fromarray(finish(bg, d, np.random.default_rng(seed + k)), "RGB").convert("RGBA")
            img, origin = render(skeleton(pose, prop, view, facing, props), char_h, skin, facing=facing, view=view)
            gx, gy = W / 2 + rng.normal(0, 0.6), H * 0.8
            frame.alpha_composite(img, (int(gx - origin[0]), int(gy - origin[1])))
            if ("keyhalo", -1) in _defects("video"):  # V6: a cyan glow ring drawn half into the key
                kr = np.array([int(key[i:i + 2], 16) for i in (1, 3, 5)])
                ring = tuple(int(v) for v in (0.5 * np.array([90, 230, 240]) + 0.5 * kr)) + (255,)
                hx, hy = gx, gy - origin[1] + 0.08 * char_h
                rr = 0.07 * char_h
                ImageDraw.Draw(frame).ellipse([hx - rr, hy - rr, hx + rr, hy + rr], outline=ring,
                                              width=max(3, int(0.012 * char_h)))
            vf = av.VideoFrame.from_ndarray(np.asarray(frame.convert("RGB")), format="rgb24")
            for packet in stream.encode(vf):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        container.close()
        return CallResult([Candidate(buf.getvalue(), "video/mp4", {"period_s": period, "fps": fps})],
                          request_id=f"synth-video-{seed}", cost=0.0)

    # -- Retro Diffusion advanced animation --------------------------------------------

    def _rd(self, req: ProviderRequest) -> CallResult:
        from .. import choreo

        start = Image.open(io.BytesIO(req.images[0])).convert("RGBA")
        W, H = req.size or start.size
        frames = int(req.params.get("frames_duration", 8))
        style = req.params.get("prompt_style", "")
        action = "walk" if style.endswith("walking") else "idle" if style.endswith("idle") else "walk"
        seed = zlib.crc32(req.images[0][:4096]) & 0x7FFFFFFF
        skin = _skin_from_image(start, seed)
        ch = choreo.choreography(action, frames)
        prop = Proportions(4.0)
        sheet = Image.new("RGBA", (W * frames, H), (0, 0, 0, 0))
        char_h = int(H / 1.33 * 0.95)
        skin.outline_w = 1.0 / char_h
        for i, f in enumerate(ch.frames):
            img, origin = render(skeleton(f.pose, prop, "side", "E"), char_h, skin, crisp=True, ss=8, margin_px=1)
            gx, gy = i * W + W / 2, H - (H - char_h) / 2
            sheet.alpha_composite(img, (int(gx - origin[0]), int(round(gy - origin[1]))))
        return CallResult([Candidate(_png(sheet), "image/png", {"layout": [frames, 1]})],
                          request_id=f"synth-rd-{seed}", cost=0.0)

    # -- Quiver ------------------------------------------------------------------------

    def _svg(self, req: ProviderRequest) -> CallResult:
        from ..pipeline import vector

        desc = _character_from_prompt(req.prompt)
        seed = character_seed(desc)
        W, H = req.size or (512, 512)
        m = re.match(r"VECTOR (VEHICLE|MACHINE|EFFECT):", req.prompt)
        if m and req.images:  # objects and effects: vectorize the approved view
            from . import synth_objects as so

            ref = np.asarray(Image.open(io.BytesIO(req.images[0])).convert("RGBA"))
            svg = so.trace_svg(ref, W, H)
            return CallResult([Candidate(svg.encode(), "image/svg+xml", {})], request_id=f"synth-svg-{seed}", cost=0.0)
        svg = vector.synthetic_character_svg(character_skin(seed), W, H, props=_props_from_text(desc))
        return CallResult([Candidate(svg.encode(), "image/svg+xml", {})], request_id=f"synth-svg-{seed}", cost=0.0)

    def _svg_animate(self, req: ProviderRequest) -> CallResult:
        from ..pipeline import vector

        svg = vector.synthetic_breathing(req.params["svg"], period_ms=2000, opening_ms=400)
        return CallResult([Candidate(svg.encode(), "image/svg+xml",
                                     {"loop_period_ms": 2000, "opening_animation_ms": 400})],
                          request_id="synth-anim", cost=0.0)

    # -- LLM roles -----------------------------------------------------------------------

    def _respond(self, req: ProviderRequest) -> CallResult:
        name = req.params.get("schema_name", "")
        if name == "describe":
            from . import synth_objects as so

            img = Image.open(io.BytesIO(req.images[0])) if req.images else Image.new("RGB", (8, 8), (128, 128, 128))
            data = so.describe(img, req.params.get("kind", "character"))
        elif name == "judge":
            n = int(req.params.get("frames", 0))
            data = {"frames": [], "summary": f"synthetic judge: no defects in {n} frames"}
            forced = _defects("judge")
            for kind, idx in forced:
                kind, _, sev = kind.partition(".")  # "wrong_pose.2": the issue at severity 2
                data["frames"].append({"frame": idx + 1, "issue_type": kind, "severity": int(sev or 3),
                                       "description": f"synthetic {kind} issue"})
        elif name == "motion_spec":
            from ..pipeline import motion

            data = motion.synthetic_author(req)
        else:
            data = {}
        return CallResult([Candidate(json.dumps(data).encode(), "application/json", {})],
                          request_id="synth-respond", usage={"input_tokens": 0, "output_tokens": 0}, cost=0.0)


def _skin_from_image(img: Image.Image, seed: int) -> Skin:
    skin = character_skin(seed)
    arr = np.asarray(img)
    px = arr[arr[..., 3] > 127][:, :3]
    if len(px) > 20:
        cols, counts = np.unique((px // 16) * 16 + 8, axis=0, return_counts=True)
        order = np.argsort(-counts)
        main = tuple(int(v) for v in cols[order[0]])
        if sum(main) > 90:
            skin.colors["torso"] = main
            skin.colors["near"] = tuple(int(v * 0.85) for v in main)
            skin.colors["far"] = tuple(int(v * 0.65) for v in main)
    return skin


# ---------------------------------------------------------------------------
# The guide painter


def _pixelate(img: Image.Image, pitch: float, rng: np.random.Generator) -> Image.Image:
    """Mimic AI pixel art: one flat colour per coarse cell on a slightly drifting, non-integer grid,
    softened at cell borders."""
    arr = np.asarray(img).astype(np.float32)
    h, w = arr.shape[:2]
    lw, lh = max(1, int(round(w / pitch))), max(1, int(round(h / pitch)))
    small = cv2.resize(arr, (lw, lh), interpolation=cv2.INTER_NEAREST)
    small[..., 3] = np.where(small[..., 3] >= 128, 255, 0)

    def edges(n, size):
        k = np.arange(n + 1, dtype=float)
        e = k * size / n + 0.12 * pitch * np.sin(2 * math.pi * k / max(8.0, n / 2.0) + rng.uniform(0, 6.28))
        e[0], e[-1] = 0, size
        return e

    bx, by = edges(lw, w), edges(lh, h)
    xs = np.clip(np.searchsorted(bx, np.arange(w) + 0.5) - 1, 0, lw - 1)
    ys = np.clip(np.searchsorted(by, np.arange(h) + 0.5) - 1, 0, lh - 1)
    big = small[ys[:, None], xs[None, :]]
    pre = np.concatenate([big[..., :3] * big[..., 3:4] / 255.0, big[..., 3:4] / 255.0], -1)
    pre = cv2.GaussianBlur(pre, (0, 0), 0.35 * pitch / 4)
    a = pre[..., 3:4]
    rgb = np.where(a > 1e-3, pre[..., :3] / np.maximum(a, 1e-3), 0)
    return Image.fromarray(np.clip(np.concatenate([rgb, a * 255], -1), 0, 255).astype(np.uint8), "RGBA")


def paint(guide: np.ndarray, editable: np.ndarray, key: str, skin: Skin, facing: str,
          rng: np.random.Generator, defects: list[tuple[str, int]], pixel_height: int | None = None) -> Image.Image:
    H, W = guide.shape[:2]
    g = guide.astype(np.float32)
    chroma = g.max(-1) - g.min(-1)
    levels = np.stack(list(GREY_LEVELS.values()))
    names = list(GREY_LEVELS.keys())
    d = np.abs(g[..., None, :] - levels[None, None]).max(-1)
    cls = d.argmin(-1)
    grey = editable & (chroma < 10) & (d.min(-1) < 14)
    # figures: connected mannequin blobs in the editable area
    lbl, n = ndimage.label(ndimage.binary_closing(grey, iterations=2))
    objs = ndimage.find_objects(lbl)
    figs = []
    for i, sl in enumerate(objs, start=1):
        if sl is None:
            continue
        area = int((lbl[sl] == i).sum())
        if area < 200:
            continue
        figs.append((i, sl))
    # reading order: rows by the figures' feet (all stand on one ground line per row), then left to right
    figs.sort(key=lambda t: (round(t[1][0].stop / max(1, H / 4)), t[1][1].start))
    char_h = float(np.median([sl[0].stop - sl[0].start for _, sl in figs])) if figs else 100.0
    char_h_stand = float(np.percentile([sl[0].stop - sl[0].start for _, sl in figs], 75)) if figs else 100.0
    ow = max(1, int(round(0.012 * char_h)))

    base = guide.copy()
    # background regenerated where the model paints; untouched pixels (the strip) are preserved
    d_bg = Degrade(gradient=12, vignette=0.06, noise=2.5, blur=0.0, seed=int(rng.integers(1 << 30)))
    bg = finish(background((W, H), key, d_bg, rng), d_bg, rng)
    base[editable] = bg[editable]
    layer = np.zeros((H, W, 4), np.uint8)
    drop = {i for k, i in defects if k == "drop"}
    wrong = {i for k, i in defects if k == "wrongpose"}
    painted: dict[int, tuple] = {}
    for fi, (i, sl) in enumerate(figs):
        m = (lbl[sl] == i) & grey[sl]
        sub_cls = cls[sl]
        colored = np.zeros(m.shape + (4,), np.uint8)
        for ci, name in enumerate(names):
            sel = m & (sub_cls == ci)
            col = skin.colors.get(name, (128, 128, 128)) if name != "prop" else skin.blade
            colored[sel, :3] = col
            colored[sel, 3] = 255
        if ("skip", fi) in defects:
            colored[m, :3] = guide[sl][m]
        if ("tint", fi) in defects or ("recolor", -1) in defects:  # recolor: every frame (Q2)
            t = colored[..., :3].astype(np.int32)
            colored[..., :3] = np.clip(t[..., ::-1] * 1.0, 0, 255).astype(np.uint8)
        # outlines on the silhouette and on class boundaries
        edge = m & ~ndimage.binary_erosion(m, iterations=ow)
        diff = np.zeros_like(m)
        diff[:, 1:] |= (sub_cls[:, 1:] != sub_cls[:, :-1]) & m[:, 1:] & m[:, :-1]
        diff[1:, :] |= (sub_cls[1:, :] != sub_cls[:-1, :]) & m[1:, :] & m[:-1, :]
        diff = ndimage.binary_dilation(diff, iterations=max(0, ow - 1)) & m
        line = edge | diff
        colored[line, :3] = skin.outline or (30, 30, 30)
        # head details: hair at the back, an eye toward the front
        head = m & (sub_cls == names.index("head")) & ~line
        if head.any():
            ys, xs = np.nonzero(head)
            cy, cx = ys.mean(), xs.mean()
            r = max(2.0, np.sqrt(head.sum() / np.pi))
            pil = Image.fromarray(colored, "RGBA")
            dr = ImageDraw.Draw(pil)
            front = 1 if facing == "E" else -1
            start, end = (150, 320) if front == 1 else (220, 30)
            dr.pieslice([cx - r, cy - r, cx + r, cy + r], start, end, fill=skin.hair + (255,))
            ex, ey = cx + front * 0.45 * r, cy - 0.1 * r
            er = max(1.0, 0.13 * r)
            dr.ellipse([ex - er, ey - er, ex + er, ey + er], fill=skin.eye + (255,))
            colored = np.asarray(pil).copy()
            colored[~ndimage.binary_dilation(m, iterations=1)] = 0
        painted[fi] = (colored, m, sl)
    for fi, (i, sl) in enumerate(figs):
        if fi in drop or fi not in painted:
            continue
        colored, m, sl = painted[fi]
        pm = m  # placement follows this cell's figure
        if fi in wrong and len(painted) > 1:
            # the model drew the pose of another frame in this cell
            other = (fi + max(2, len(painted) // 2)) % len(painted)
            colored, m, _ = painted[other]
        # per-figure placement jitter (the model never lands exactly on the guide)
        jitter = 0.012 * char_h
        dx, dy = rng.normal(0, jitter / 2), rng.normal(0, jitter / 3)
        s = 1.0 + rng.normal(0, 0.01)
        if ("shift", fi) in defects:
            dx += 0.12 * char_h
        if ("scale", fi) in defects:
            s *= 1.12
        img = Image.fromarray(colored, "RGBA")
        if pixel_height:
            img = _pixelate(img, char_h_stand / pixel_height, rng)
        if ("flip", fi) in defects:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if abs(s - 1) > 1e-3:
            nw, nh = max(1, int(img.width * s)), max(1, int(img.height * s))
            img = img.resize((nw, nh), Image.Resampling.LANCZOS)
        y0, x0 = sl[0].start, sl[1].start
        bottom = y0 + pm.shape[0]
        px = int(round(x0 + dx - (img.width - pm.shape[1]) / 2))
        py = int(round(bottom + dy - img.height))
        tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        tmp.alpha_composite(img, (max(0, px), max(0, py)), (max(0, -px), max(0, -py)))
        t = np.asarray(tmp)
        a = t[..., 3:4].astype(np.float32) / 255.0
        layer = (layer.astype(np.float32) * (1 - a) + t.astype(np.float32) * a).astype(np.uint8)
    # soft edges: models anti-alias against the background
    lf = layer.astype(np.float32)
    a0 = lf[..., 3:4] / 255.0
    pre = cv2.GaussianBlur(np.concatenate([lf[..., :3] * a0, a0], -1), (0, 0), 0.7)
    a = pre[..., 3:4]
    rgb = np.where(a > 1e-3, pre[..., :3] / np.maximum(a, 1e-3), 0)
    out = base.astype(np.float32) * (1 - a) + np.clip(rgb, 0, 255) * a
    out = np.clip(out + rng.normal(0, 1.5, out.shape) * editable[..., None], 0, 255).astype(np.uint8)
    img = Image.fromarray(out, "RGB")
    if any(k == "lines" for k, _ in defects):
        xs = sorted({sl[1].start - 20 for _, sl in figs})[1:]
        draw_grid_lines(img, xs, [H // 2])
    if any(k == "labels" for k, _ in defects):
        draw_labels(img, [(sl[1].start, max(0, sl[0].start - 30)) for _, sl in figs], size=18)
    return img
