"""Vector mode (6.8): Quiver Arrow 2 characters and idle loops, sanitized and sampled in Chromium.

Frames arrive with true alpha and exact geometry, so they skip matte, layout and registration
and enter at temporal analysis and QA.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field

import numpy as np
from lxml import etree
from PIL import Image

SVG_NS = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
RIG_PARTS = ["arm_f", "hand_f", "leg_f", "foot_f", "torso", "head", "leg_n", "foot_n", "arm_n", "hand_n"]
RUBBERHOSE = ["arm_f", "leg_f", "leg_n", "arm_n"]

ALLOWED = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "defs", "linearGradient",
           "radialGradient", "stop", "clipPath", "mask", "style", "title", "desc", "animate", "animateTransform",
           "animateMotion", "set", "use", "symbol", "mpath", "text", "tspan", "filter", "feGaussianBlur", "feOffset",
           "feBlend", "feColorMatrix", "feComposite", "feFlood", "feMerge", "feMergeNode", "pattern", "metadata"}
URL_RE = re.compile(r"url\(\s*['\"]?([^)'\"]*)['\"]?\s*\)", re.I)


@dataclass
class Sanitized:
    svg: str
    removed: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0


def _local(tag) -> str:
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else str(tag)


def sanitize(svg: str, width: int, height: int) -> Sanitized:
    """defusedxml parse (rejects entities and DTD tricks), then an element and attribute allowlist."""
    import defusedxml.ElementTree as DET

    DET.fromstring(svg.encode() if isinstance(svg, str) else svg)  # raises on XML bombs / external entities
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=True,
                             remove_pis=True)
    root = etree.fromstring(svg.encode() if isinstance(svg, str) else svg, parser)
    removed: list[str] = []
    if _local(root.tag) != "svg":
        raise ValueError("root element is not <svg>")
    for el in list(root.iter()):
        if el is root or not isinstance(el.tag, str):
            continue
        name = _local(el.tag)
        if name not in ALLOWED:
            parent = el.getparent()
            if parent is not None:
                removed.append(f"<{name}>")
                parent.remove(el)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            local = _local(attr)
            val = el.attrib[attr]
            if local.lower().startswith("on"):
                removed.append(f"@{local} on <{_local(el.tag)}>")
                del el.attrib[attr]
            elif local == "href":
                if not val.startswith("#"):
                    removed.append(f"@href={val[:40]}")
                    del el.attrib[attr]
            elif "javascript:" in val.lower():
                removed.append(f"@{local} javascript")
                del el.attrib[attr]
            elif "url(" in val.lower():
                for u in URL_RE.findall(val):
                    if not u.startswith("#"):
                        removed.append(f"@{local} url({u[:40]})")
                        del el.attrib[attr]
                        break
        if _local(el.tag) == "style" and el.text:
            text = el.text
            if "@import" in text or re.search(r"url\(\s*['\"]?(?!#)", text):
                text = re.sub(r"@import[^;]*;", "", text)
                text = URL_RE.sub(lambda m: m.group(0) if m.group(1).startswith("#") else "none", text)
                removed.append("<style> external reference")
                el.text = text
    root.set("width", str(width))
    root.set("height", str(height))
    if "viewBox" not in root.attrib:
        root.set("viewBox", f"0 0 {width} {height}")
    out = etree.tostring(root, encoding="unicode")
    return Sanitized(out, removed, width, height)


# ---------------------------------------------------------------------------
# Chromium rendering and sampling


PAUSE_JS = """t => {
  document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; });
  document.querySelectorAll('svg').forEach(s => { s.pauseAnimations?.(); s.setCurrentTime?.(t / 1000); });
}"""


async def sample_svg(svg: str, n: int, period_ms: float, opening_ms: float | None, w: int, h: int) -> list[bytes]:
    from playwright.async_api import async_playwright

    from .browser import launch as launch_browser

    async with async_playwright() as pw:
        browser = await launch_browser(pw)
        ctx = await browser.new_context(viewport={"width": w, "height": h}, offline=True)
        page = await ctx.new_page()
        await page.set_content(f"<body style='margin:0;background:transparent'>{svg}</body>")
        frames = []
        for k in range(n):
            t = (opening_ms or 0) + k * period_ms / n
            await page.evaluate(PAUSE_JS, t)
            frames.append(await page.screenshot(omit_background=True))
        await browser.close()
        return frames


async def render_svgs(svgs: list[str], w: int, h: int) -> list[bytes]:
    from playwright.async_api import async_playwright

    from .browser import launch as launch_browser

    async with async_playwright() as pw:
        browser = await launch_browser(pw)
        ctx = await browser.new_context(viewport={"width": w, "height": h}, offline=True)
        page = await ctx.new_page()
        out = []
        for s in svgs:
            await page.set_content(f"<body style='margin:0;background:transparent'>{s}</body>")
            out.append(await page.screenshot(omit_background=True))
        await browser.close()
        return out


def to_rgba(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA")).copy()


# ---------------------------------------------------------------------------
# Rig checks (6.9)


def rig_report(svg: str) -> dict:
    root = etree.fromstring(svg.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    ids = {el.get("id"): el for el in root.iter() if isinstance(el.tag, str) and el.get("id")}
    missing = [p for p in RIG_PARTS if p not in ids]
    empty = [p for p in RIG_PARTS if p in ids and len(list(ids[p].iter())) <= 1 and _local(ids[p].tag) == "g"]
    not_single = []
    for p in RUBBERHOSE:
        if p not in ids:
            continue
        el = ids[p]
        paths = [e for e in el.iter() if isinstance(e.tag, str) and _local(e.tag) == "path"]
        if _local(el.tag) == "path":
            paths = [el]
        if len(paths) != 1:
            not_single.append(p)
    return {"missing": missing, "empty": empty, "not_single_path": not_single,
            "ok": not missing and not empty and not not_single}


# ---------------------------------------------------------------------------
# Synthetic SVGs (offline simulator)


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in c[:3])


def synthetic_character_svg(skin, W: int, H: int, props=None) -> str:
    """A rigged side-view character: rubberhose limb paths plus rigid parts, ids per rig.side.v2."""
    from ..figure import Proportions, skeleton

    pr = Proportions(5.0)
    parts = {p.name: p for p in skeleton({"hip_n": 5, "hip_f": -5, "shoulder_n": -6, "shoulder_f": 6,
                                          "elbow_n": 12, "elbow_f": 12, "knee_n": 4, "knee_f": 4}, pr)}
    s = 0.8 * H
    ox, oy = W / 2, 0.92 * H

    def P(pt):
        return ox + pt[0] * s, oy - pt[1] * s

    def hose(upper, lower):
        a, b = P(parts[upper].points[0]), P(parts[upper].points[1])
        c = P(parts[lower].points[1])
        # one cubic from joint to extremity through the middle joint
        c1 = (a[0] + (b[0] - a[0]) * 0.9, a[1] + (b[1] - a[1]) * 0.9)
        c2 = (b[0] + (c[0] - b[0]) * 0.1, b[1] + (c[1] - b[1]) * 0.1)
        return f"M {a[0]:.1f},{a[1]:.1f} C {c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {c[0]:.1f},{c[1]:.1f}"

    far, near, torso, head = (_hex(skin.colors[k]) for k in ("far", "near", "torso", "head"))
    boots = _hex(skin.boots or skin.colors["far"])
    ol = _hex(skin.outline or (30, 30, 30))
    lw_leg, lw_arm = pr.w_thigh * s, pr.w_arm * s
    hip, sh = P(parts["torso"].points[0]), P(parts["torso"].points[1])
    hc = P(parts["head"].points[0])
    hr = pr.head * s / 2
    tw = pr.w_torso * s

    def foot(name):
        a, b = P(parts[name].points[0]), P(parts[name].points[1])
        return (f'<ellipse cx="{(a[0] + b[0]) / 2:.1f}" cy="{(a[1] + b[1]) / 2:.1f}" rx="{pr.foot * s * 0.6:.1f}" '
                f'ry="{pr.w_shin * s * 0.45:.1f}" fill="{boots}" stroke="{ol}" stroke-width="2"/>')

    def hand(name, col):
        c = P(parts[name].points[0])
        return f'<circle cx="{c[0]:.1f}" cy="{c[1]:.1f}" r="{0.035 * s:.1f}" fill="{col}" stroke="{ol}" stroke-width="2"/>'

    def limb(pid, d, col, w):
        return (f'<path id="{pid}" d="{d}" fill="none" stroke="{col}" stroke-width="{w:.1f}" '
                f'stroke-linecap="round" stroke-linejoin="round"/>')

    torso_d = (f"M {hip[0] - tw / 2:.1f},{hip[1]:.1f} L {sh[0] - tw / 2:.1f},{sh[1]:.1f} "
               f"L {sh[0] + tw / 2:.1f},{sh[1]:.1f} L {hip[0] + tw / 2:.1f},{hip[1]:.1f} Z")
    body = [
        limb("arm_f", hose("arm_f_upper", "arm_f_lower"), far, lw_arm),
        f'<g id="hand_f">{hand("hand_f", _hex(skin.colors["head"]))}</g>',
        limb("leg_f", hose("leg_f_upper", "leg_f_lower"), far, lw_leg),
        f'<g id="foot_f">{foot("foot_f")}</g>',
        (f'<g id="torso"><path d="M {sh[0]:.1f},{sh[1] + 4:.1f} L {hc[0]:.1f},{hc[1] + hr * 0.4:.1f}" stroke="{head}" '
         f'stroke-width="{lw_arm:.1f}" stroke-linecap="round"/><path d="{torso_d}" fill="{torso}" stroke="{ol}" '
         f'stroke-width="2.5" stroke-linejoin="round"/></g>'),
        (f'<g id="head"><circle cx="{hc[0]:.1f}" cy="{hc[1]:.1f}" r="{hr:.1f}" fill="{head}" stroke="{ol}" '
         f'stroke-width="2.5"/><path d="M {hc[0] - hr:.1f},{hc[1]:.1f} A {hr:.1f},{hr:.1f} 0 0 1 {hc[0] + hr * 0.4:.1f},'
         f'{hc[1] - hr * 0.92:.1f} L {hc[0] - hr * 0.2:.1f},{hc[1] - hr * 0.2:.1f} Z" fill="{_hex(skin.hair)}"/>'
         f'<circle cx="{hc[0] + hr * 0.45:.1f}" cy="{hc[1] - hr * 0.1:.1f}" r="{max(2, hr * 0.13):.1f}" '
         f'fill="{_hex(skin.eye)}"/></g>'),
        limb("leg_n", hose("leg_n_upper", "leg_n_lower"), near, lw_leg),
        f'<g id="foot_n">{foot("foot_n")}</g>',
        limb("arm_n", hose("arm_n_upper", "arm_n_lower"), near, lw_arm),
        f'<g id="hand_n">{hand("hand_n", _hex(skin.colors["head"]))}</g>',
    ]
    return (f'<svg xmlns="{SVG_NS}" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            f'<g id="root">{"".join(body)}</g></svg>')


def synthetic_breathing(svg: str, period_ms: int = 2000, opening_ms: int = 400) -> str:
    style = (f"<style>@keyframes intro{{from{{opacity:.4}}to{{opacity:1}}}}"
             f"@keyframes breathe{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-4px)}}}}"
             f"@keyframes breathe2{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-2.5px)}}}}"
             f"#root{{animation:intro {opening_ms}ms linear both}}"
             f"#head,#hand_n,#hand_f{{animation:breathe {period_ms}ms ease-in-out {opening_ms}ms infinite}}"
             f"#torso,#arm_n,#arm_f{{animation:breathe2 {period_ms}ms ease-in-out {opening_ms}ms infinite}}</style>")
    return svg.replace(">", ">" + style, 1) if svg.startswith("<svg") else svg


# ---------------------------------------------------------------------------
# Route


FRAME = 512


async def character_svg(hub, project, rec, *, job=None, seed=0) -> tuple[str, list[str]]:
    """The vector character: Quiver generation from the approved side view, sanitized."""
    from pathlib import Path

    from .. import prompts, registry
    from ..character import png_bytes
    from ..providers.base import ProviderRequest

    if rec.svg:
        return project.abs(rec.svg).read_text(), []
    if rec.kind == "character":
        p = prompts.render("vector_character", character=rec.description.rstrip("."), part_ids=", ".join(RIG_PARTS))
    else:  # objects and effects have no rig: give each moving part its own group instead
        from ..prompts import Prompt

        what = {"vehicle": "side view facing right, at rest, wheels or treads and any turret as separate groups",
                "machine": "side view, at rest, each moving part (gears, pistons, lights, doors) as its own group",
                "effect": "centred, at its most intense moment, each layer (core, glow, particles) as its own group"}
        p = Prompt(f"vector_{rec.kind}", 1, f"VECTOR {rec.kind.upper()}: {rec.description.rstrip('.')}, "
                                              f"{what[rec.kind]}.")
    refs = []
    side = rec.views.get("side-e")
    if side:
        refs.append(project.abs(side).read_bytes())
    model = registry.model_for("vector_character")
    params = {k: v for k, v in registry.model(model)["params"].items() if k not in ("stream", "animate_reasoning_effort")}
    req = ProviderRequest(op="svg_generate", model=model, prompt=p.text, params=params, images=refs,
                          instructions=prompts.style_block(rec.style), size=(FRAME, FRAME), seed=seed,
                          purpose="vector_character")
    cands = await hub.call(req, job=job)
    san = sanitize(cands[0].data.decode(), FRAME, FRAME)
    path = project.character_dir(rec.name) / "ref" / "character.svg"
    path.write_text(san.svg)
    rec.svg = project.rel(path)
    project.save_character(rec)
    return san.svg, san.removed


async def generate(hub, c, *, project, rec, job=None, seed=0):
    from .. import registry
    from ..generate import CandidateOut
    from ..providers.base import ProviderRequest
    from ..spec import Finding

    svg, removed = await character_svg(hub, project, rec, job=job, seed=seed)
    findings = []
    if removed:
        findings.append(Finding(metric="vector_rig", level="info", message=f"sanitizer removed {len(removed)} items",
                                remedy="auto_fixed", auto_fixed=True))
    if c.route in ("vector-idle", "vector-anim"):
        model = registry.model_for("vector_idle")
        mp = registry.model(model)["params"]
        if c.route == "vector-idle":
            motion_prompt = "breathe gently and bob in place, feet fixed"
        else:
            from .. import prompts as P

            beats = " ".join(f"({i + 1}) {f.line}" for i, f in enumerate(c.choreo.frames))
            motion_prompt = (f"Animate {P.action_title(c.spec.action, c.spec.character.kind)}: {beats} "
                             + ("Loop seamlessly." if c.spec.loop else "Play once, then hold the final state.")
                             + " Keep the overall position and size unchanged.")
        req = ProviderRequest(op="svg_animate", model=model, prompt=motion_prompt,
                              params={"svg": svg, "reasoning_effort": mp.get("animate_reasoning_effort",
                                                                             mp.get("reasoning_effort", "medium"))},
                              seed=seed, purpose="vector_idle")
        cands = await hub.call(req, job=job)
        cd = cands[0]
        san = sanitize(cd.data.decode(), FRAME, FRAME)
        period = float(cd.meta.get("loop_period_ms") or 2000)
        opening = cd.meta.get("opening_animation_ms")
        pngs = await sample_svg(san.svg, c.spec.frames, period, opening, FRAME, FRAME)
        frames = [to_rgba(p) for p in pngs]
        if san.removed:
            findings.append(Finding(metric="vector_rig", level="warn", severity=1,
                                    message=f"sanitizer removed {', '.join(san.removed[:5])}", remedy="auto_fixed",
                                    auto_fixed=True))
        return [CandidateOut("c1", None, "image/svg+xml", {**cd.meta, "svg": san.svg}, frames=frames,
                             extra={"findings": findings, "vector": {"period_ms": period, "opening_ms": opening,
                                                                     "sanitizer_removed": san.removed}})]
    from . import motion

    result = await motion.author_loop(hub, c, svg, job=job, seed=seed)
    rig_svg = result.info.pop("rig_svg", svg)
    if rig_svg != svg:  # keep the prepared rig next to the original character
        rp = project.character_dir(rec.name) / "ref" / "character.rig.svg"
        rp.write_text(rig_svg)
    return [CandidateOut("c1", None, "image/svg+xml", {"motion_spec": result.spec, "svg": rig_svg},
                         frames=result.frames, extra={"findings": findings + result.findings,
                                                      "motion": result.info})]
