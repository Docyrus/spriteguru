"""AI-coded SVG animation (6.9): GPT-6 Sol writes a declarative motion spec, the engine evaluates it.

Four primitives: rigid rotation (world matrices down the hierarchy), path morph (rubberhose limbs
whose d keys share one command structure), attachment (child follows the end of a host path and
aligns to its tangent) and drawing swap (exactly one variant visible per phase).
"""

from __future__ import annotations

import copy
import io
import json
import math
import re
from dataclasses import dataclass, field

import numpy as np
from lxml import etree
from PIL import Image, ImageDraw

from ..spec import Finding
from .vector import RIG_PARTS, RUBBERHOSE, render_svgs, to_rgba

RIG = {
    "name": "rig.side.v2",
    "draw_order": RIG_PARTS,
    "rubberhose": RUBBERHOSE,
    "rigid": {"torso": {"parent": "root"}, "head": {"parent": "torso"},
              "hand_f": {"attach": {"to": "arm_f", "at": "end", "align": "tangent"}},
              "hand_n": {"attach": {"to": "arm_n", "at": "end", "align": "tangent"}},
              "foot_f": {"attach": {"to": "leg_f", "at": "end", "align": "tangent"}},
              "foot_n": {"attach": {"to": "leg_n", "at": "end", "align": "tangent"}}},
}

KEYS_SCHEMA = {"type": "array", "items": {"type": "object", "properties": {"t": {"type": "number"},
                                                                           "v": {"type": "number"}},
                                          "required": ["t", "v"], "additionalProperties": False}}
SCHEMA = {
    "type": "object",
    "properties": {
        "motion": {"type": "string"},
        "loop": {"type": "boolean"},
        "parts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "ease": {"type": "string", "enum": ["linear", "sine.inOut", "quad.inOut", "cubic.inOut"]},
                "morph": {"anyOf": [{"type": "null"}, {"type": "array", "items": {
                    "type": "object", "properties": {"t": {"type": "number"}, "d": {"type": "string"}},
                    "required": ["t", "d"], "additionalProperties": False}}]},
                "rotate": {"anyOf": [{"type": "null"}, {"type": "object", "properties": {
                    "pivot": {"anyOf": [{"type": "null"}, {"type": "array", "items": {"type": "number"}}]},
                    "keys": KEYS_SCHEMA}, "required": ["pivot", "keys"], "additionalProperties": False}]},
                "attach": {"anyOf": [{"type": "null"}, {"type": "object", "properties": {
                    "to": {"type": "string"}, "at": {"type": "string", "enum": ["start", "end"]},
                    "align": {"type": "string", "enum": ["tangent", "none"]}},
                    "required": ["to", "at", "align"], "additionalProperties": False}]},
                "swap": {"anyOf": [{"type": "null"}, {"type": "array", "items": {
                    "type": "object", "properties": {"variant": {"type": "string"}, "from": {"type": "number"},
                                                     "to": {"type": "number"}},
                    "required": ["variant", "from", "to"], "additionalProperties": False}}]},
            },
            "required": ["id", "ease", "morph", "rotate", "attach", "swap"],
            "additionalProperties": False,
        }},
        "root": {"type": "object", "properties": {"x": KEYS_SCHEMA, "y": KEYS_SCHEMA,
                                                  "ease": {"type": "string",
                                                           "enum": ["linear", "sine.inOut", "quad.inOut",
                                                                    "cubic.inOut"]}},
                 "required": ["x", "y", "ease"], "additionalProperties": False},
    },
    "required": ["motion", "loop", "parts", "root"],
    "additionalProperties": False,
}

EASES = {
    "linear": lambda u: u,
    "sine.inOut": lambda u: 0.5 - 0.5 * math.cos(math.pi * u),
    "quad.inOut": lambda u: 2 * u * u if u < 0.5 else 1 - (-2 * u + 2) ** 2 / 2,
    "cubic.inOut": lambda u: 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2,
}

# ---------------------------------------------------------------------------
# Path parsing and morphing


_TOK = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def parse_path(d: str) -> tuple[tuple, np.ndarray]:
    """(command structure, numbers). Arc flags are part of the structure, never interpolated."""
    toks = _TOK.findall(d)
    cmds, nums = [], []
    i, cur = 0, None
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cur = t
            i += 1
            if cur.upper() == "Z":
                cmds.append((cur, ()))
                continue
        if cur is None:
            raise ValueError(f"path does not start with a command: {d[:40]}")
        k = _ARGS[cur.upper()]
        chunk = toks[i:i + k]
        if len(chunk) < k:
            raise ValueError(f"truncated path segment in {d[:40]}")
        vals = [float(x) for x in chunk]
        flags = (int(vals[3]), int(vals[4])) if cur.upper() == "A" else ()
        cmds.append((cur, flags))
        nums += vals
        i += k
        if cur == "M":
            cur = "L"
        elif cur == "m":
            cur = "l"
    return tuple(cmds), np.array(nums, float)


def format_path(cmds: tuple, nums: np.ndarray) -> str:
    out, j = [], 0
    for c, flags in cmds:
        k = _ARGS[c.upper()]
        vals = nums[j:j + k]
        j += k
        if c.upper() == "A":
            vals = list(vals)
            vals[3], vals[4] = flags
        out.append(c + (" " + " ".join(f"{v:.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
                                        for v in vals) if k else ""))
    return " ".join(out)


def endpoints(cmds: tuple, nums: np.ndarray) -> list[tuple[float, float]]:
    """Absolute points in drawing order (anchor points and control points)."""
    pts = []
    x = y = 0.0
    j = 0
    start = (0.0, 0.0)
    for c, _ in cmds:
        k = _ARGS[c.upper()]
        v = nums[j:j + k]
        j += k
        rel = c.islower()
        C = c.upper()
        if C == "Z":
            x, y = start
            continue
        if C == "H":
            x = x + v[0] if rel else v[0]
            pts.append((x, y))
            continue
        if C == "V":
            y = y + v[0] if rel else v[0]
            pts.append((x, y))
            continue
        if C == "A":
            x, y = (x + v[5], y + v[6]) if rel else (v[5], v[6])
            pts.append((x, y))
            continue
        for m in range(0, k, 2):
            px, py = (x + v[m], y + v[m + 1]) if rel else (v[m], v[m + 1])
            pts.append((px, py))
        x, y = pts[-1]
        if C == "M":
            start = (x, y)
    return pts


def _segment(keys: list[float], t: float, loop: bool) -> tuple[int, int, float]:
    n = len(keys)
    if n == 1:
        return 0, 0, 0.0
    if loop:
        t = t % 1.0
        i = max([j for j, k in enumerate(keys) if k <= t + 1e-9], default=n - 1)
        j = (i + 1) % n
        span = (keys[j] - keys[i]) % 1.0 or 1.0
        u = ((t - keys[i]) % 1.0) / span
    else:
        if t <= keys[0]:
            return 0, 0, 0.0
        if t >= keys[-1]:
            return n - 1, n - 1, 0.0
        i = max(j for j, k in enumerate(keys) if k <= t)
        j = i + 1
        u = (t - keys[i]) / max(keys[j] - keys[i], 1e-9)
    return i, j, min(max(u, 0.0), 1.0)


def morph(keys: list[tuple[float, str]], t: float, ease, loop: bool = True) -> str:
    parsed = [parse_path(d) for _, d in keys]
    if len({cmds for cmds, _ in parsed}) != 1:
        raise ValueError("morph keys must share one command structure")
    times = [k for k, _ in keys]
    i, j, u = _segment(times, t, loop)
    u = ease(u)
    return format_path(parsed[0][0], (1 - u) * parsed[i][1] + u * parsed[j][1])


def interp(keys: list[dict], t: float, ease, loop: bool) -> float:
    if not keys:
        return 0.0
    ks = sorted(keys, key=lambda k: k["t"])
    if loop:
        ks = [k for k in ks if k["t"] < 1.0 - 1e-9] or ks[:1]
    i, j, u = _segment([k["t"] for k in ks], t, loop)
    u = ease(u)
    return (1 - u) * ks[i]["v"] + u * ks[j]["v"]


# ---------------------------------------------------------------------------
# Matrices


def T(x, y):
    return np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], float)


def R(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]], float)


def mstr(M) -> str:
    return "matrix(%.5f %.5f %.5f %.5f %.3f %.3f)" % (M[0, 0], M[1, 0], M[0, 1], M[1, 1], M[0, 2], M[1, 2])


# ---------------------------------------------------------------------------
# Rig


@dataclass
class Rig:
    svg: str
    width: int
    height: int
    ids: dict
    rest_paths: dict  # rubberhose id -> d
    pivots: dict  # rigid id -> (x, y)
    boxes: dict


def _bbox(el) -> tuple[float, float, float, float] | None:
    pts = []
    for e in el.iter():
        if not isinstance(e.tag, str):
            continue
        tag = e.tag.split("}")[-1]
        try:
            if tag == "path" and e.get("d"):
                cmds, nums = parse_path(e.get("d"))
                pts += endpoints(cmds, nums)
            elif tag == "circle":
                cx, cy, r = float(e.get("cx", 0)), float(e.get("cy", 0)), float(e.get("r", 0))
                pts += [(cx - r, cy - r), (cx + r, cy + r)]
            elif tag == "ellipse":
                cx, cy = float(e.get("cx", 0)), float(e.get("cy", 0))
                rx, ry = float(e.get("rx", 0)), float(e.get("ry", 0))
                pts += [(cx - rx, cy - ry), (cx + rx, cy + ry)]
            elif tag == "rect":
                x, y = float(e.get("x", 0)), float(e.get("y", 0))
                pts += [(x, y), (x + float(e.get("width", 0)), y + float(e.get("height", 0)))]
            elif tag in ("polygon", "polyline"):
                v = [float(a) for a in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", e.get("points", ""))]
                pts += list(zip(v[::2], v[1::2]))
        except ValueError:
            continue
    if not pts:
        return None
    a = np.array(pts)
    return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())


def load_rig(svg: str) -> Rig:
    root = etree.fromstring(svg.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    vb = [float(v) for v in (root.get("viewBox") or f"0 0 {root.get('width', 512)} {root.get('height', 512)}").split()]
    ids = {el.get("id"): el for el in root.iter() if isinstance(el.tag, str) and el.get("id")}
    rest, boxes = {}, {}
    for pid in RIG_PARTS:
        el = ids.get(pid)
        if el is None:
            continue
        boxes[pid] = _bbox(el)
        if pid in RUBBERHOSE:
            path = el if el.tag.endswith("path") else next((e for e in el.iter() if isinstance(e.tag, str)
                                                            and e.tag.endswith("path")), None)
            if path is not None:
                rest[pid] = path.get("d")
    pivots = {}
    # pivot = centroid of the overlap of a part with its parent (bbox approximation of the 4x render)
    if boxes.get("torso"):
        x0, y0, x1, y1 = boxes["torso"]
        pivots["torso"] = ((x0 + x1) / 2, y1)
    if boxes.get("head") and boxes.get("torso"):
        hx0, hy0, hx1, hy1 = boxes["head"]
        tx0, ty0, tx1, ty1 = boxes["torso"]
        ox0, oy0, ox1, oy1 = max(hx0, tx0), max(hy0, ty0), min(hx1, tx1), min(hy1, ty1)
        pivots["head"] = ((ox0 + ox1) / 2, (oy0 + oy1) / 2) if ox1 > ox0 and oy1 > oy0 else ((hx0 + hx1) / 2, hy1)
    return Rig(svg, int(vb[2]), int(vb[3]), ids, rest, pivots, boxes)


def validate_rig(rig: Rig) -> list[str]:
    errs = []
    for pid in RIG_PARTS:
        if pid not in rig.ids:
            errs.append(f"missing group {pid}")
        elif rig.boxes.get(pid) is None:
            errs.append(f"group {pid} is empty")
    for pid in RUBBERHOSE:
        el = rig.ids.get(pid)
        if el is None:
            continue
        paths = [e for e in el.iter() if isinstance(e.tag, str) and e.tag.endswith("path")]
        # one path, or an outline copy under it: every path of the limb has the same geometry
        if not paths or len({pe.get("d") for pe in paths}) != 1:
            errs.append(f"rubberhose part {pid} must be a single path (found {len(paths)})")
    for child, parent in (("head", "torso"),):
        a, b = rig.boxes.get(child), rig.boxes.get(parent)
        if a and b and (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]):
            errs.append(f"rigid part {child} does not overlap its parent {parent}")
    return errs


# ---------------------------------------------------------------------------
# Spec validation and evaluation


def validate_spec(spec: dict, rig: Rig, loop: bool) -> list[str]:
    errs = []
    parts = {p["id"]: p for p in spec.get("parts", [])}
    for pid, p in parts.items():
        if pid not in rig.ids:
            errs.append(f"part {pid} does not exist in the SVG")
            continue
        if p.get("morph"):
            structs = set()
            for k in p["morph"]:
                try:
                    structs.add(parse_path(k["d"])[0])
                except ValueError as e:
                    errs.append(f"{pid}: {e}")
            if pid in rig.rest_paths:
                structs.add(parse_path(rig.rest_paths[pid])[0])
            if len(structs) > 1:
                errs.append(f"{pid}: morph keys must share the rest path's command structure")
            if loop:
                t0 = [k for k in p["morph"] if abs(k["t"]) < 1e-6]
                t1 = [k for k in p["morph"] if abs(k["t"] - 1.0) < 1e-6]
                if t0 and t1 and not np.allclose(parse_path(t0[0]["d"])[1], parse_path(t1[0]["d"])[1], atol=0.5):
                    errs.append(f"{pid}: pose at t = 0 differs from t = 1")
        if p.get("attach"):
            host = p["attach"]["to"]
            if host not in rig.rest_paths and not (host in parts and parts[host].get("morph")):
                errs.append(f"{pid}: attachment host {host} is not a path")
        if p.get("rotate") and loop:
            ks = p["rotate"]["keys"]
            k0 = [k["v"] for k in ks if abs(k["t"]) < 1e-6]
            k1 = [k["v"] for k in ks if abs(k["t"] - 1.0) < 1e-6]
            if k0 and k1 and abs(k0[0] - k1[0]) > 0.5:
                errs.append(f"{pid}: rotation at t = 0 differs from t = 1")
    return errs


def evaluate(spec: dict, rig: Rig, t: float, loop: bool) -> str:
    root = etree.fromstring(rig.svg.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    ids = {el.get("id"): el for el in root.iter() if isinstance(el.tag, str) and el.get("id")}
    parts = {p["id"]: p for p in spec.get("parts", [])}
    r_ease = EASES.get(spec.get("root", {}).get("ease", "sine.inOut"), EASES["linear"])
    rx = interp(spec.get("root", {}).get("x", []), t, r_ease, loop)
    ry = interp(spec.get("root", {}).get("y", []), t, r_ease, loop)
    M_root = T(rx, ry)
    world = {"root": M_root}
    paths_now = dict(rig.rest_paths)

    # path morphs (in root space)
    for pid in RUBBERHOSE:
        p = parts.get(pid)
        if p and p.get("morph"):
            keys = [(k["t"], k["d"]) for k in p["morph"] if not (loop and abs(k["t"] - 1.0) < 1e-6)]
            keys.sort(key=lambda k: k[0])
            paths_now[pid] = morph(keys, t, EASES.get(p.get("ease"), EASES["linear"]), loop)
        el = ids.get(pid)
        if el is not None and pid in paths_now:
            for path in ([el] if el.tag.endswith("path") else
                         [e for e in el.iter() if isinstance(e.tag, str) and e.tag.endswith("path")]):
                path.set("d", paths_now[pid])  # the limb and its outline copy move together
            el.set("transform", mstr(M_root))

    # rigid rotations down the hierarchy
    for pid in ("torso", "head"):
        p = parts.get(pid) or {}
        parent = RIG["rigid"][pid]["parent"]
        Mp = world.get(parent, M_root)
        rot = p.get("rotate")
        ang = interp(rot["keys"], t, EASES.get(p.get("ease"), EASES["linear"]), loop) if rot else 0.0
        piv = tuple(rot["pivot"]) if rot and rot.get("pivot") else rig.pivots.get(pid, (0.0, 0.0))
        world[pid] = Mp @ T(*piv) @ R(ang) @ T(-piv[0], -piv[1])
        if pid in ids:
            ids[pid].set("transform", mstr(world[pid]))

    # attachments: follow the end of the host path, aligned to its tangent
    for pid in ("hand_f", "hand_n", "foot_f", "foot_n"):
        p = parts.get(pid) or {}
        att = p.get("attach") or RIG["rigid"][pid]["attach"]
        host = att["to"]
        if host not in paths_now or pid not in ids:
            continue
        rest_pts = endpoints(*parse_path(rig.rest_paths[host]))
        now_pts = endpoints(*parse_path(paths_now[host]))
        idx = -1 if att.get("at", "end") == "end" else 0
        nb = -2 if idx == -1 else 1
        A0, A = np.array(rest_pts[idx]), np.array(now_pts[idx])
        ang0 = math.degrees(math.atan2(*(np.array(rest_pts[idx]) - np.array(rest_pts[nb]))[::-1]))
        ang1 = math.degrees(math.atan2(*(np.array(now_pts[idx]) - np.array(now_pts[nb]))[::-1]))
        rot = (ang1 - ang0) if att.get("align", "tangent") == "tangent" else 0.0
        M = M_root @ T(*A) @ R(rot) @ T(-A0[0], -A0[1])
        ids[pid].set("transform", mstr(M))

    # drawing swaps: exactly one variant visible
    for pid, p in parts.items():
        if not p.get("swap") or pid not in ids:
            continue
        phase = t % 1.0 if loop else t
        active = next((s["variant"] for s in p["swap"] if s["from"] <= phase < s["to"]), p["swap"][0]["variant"])
        for child in ids[pid]:
            if isinstance(child.tag, str) and child.get("id", "").startswith(pid + "."):
                child.set("display", "inline" if child.get("id") == f"{pid}.{active}" else "none")
    return etree.tostring(root, encoding="unicode")


def frame_times(n: int, loop: bool) -> list[float]:
    return [k / n for k in range(n)] if loop else [k / max(1, n - 1) for k in range(n)]


async def render_spec(spec: dict, rig: Rig, n: int, loop: bool) -> list[np.ndarray]:
    svgs = [evaluate(spec, rig, t, loop) for t in frame_times(n, loop)]
    return [to_rgba(p) for p in await render_svgs(svgs, rig.width, rig.height)]


def set_of_mark(svg_png: bytes, rig: Rig) -> bytes:
    img = Image.open(io.BytesIO(svg_png)).convert("RGBA")
    base = Image.new("RGBA", img.size, (255, 255, 255, 255))
    base.alpha_composite(img)
    d = ImageDraw.Draw(base)
    for pid, b in rig.boxes.items():
        if b is None:
            continue
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        d.rectangle(b, outline=(255, 0, 0))
        d.text((cx, cy), pid, fill=(200, 0, 0))
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Authoring loop


@dataclass
class AuthorResult:
    spec: dict
    frames: list[np.ndarray]
    findings: list[Finding] = field(default_factory=list)
    info: dict = field(default_factory=dict)


EXAMPLES = json.dumps({
    "motion": "walk.side.8", "loop": True,
    "parts": [
        {"id": "leg_f", "ease": "sine.inOut", "rotate": None, "attach": None, "swap": None,
         "morph": [{"t": 0.0, "d": "M 470,600 C 470,700 430,800 440,958"},
                   {"t": 0.25, "d": "M 470,600 C 480,700 470,820 470,940"},
                   {"t": 0.5, "d": "M 470,600 C 490,700 520,800 520,958"},
                   {"t": 0.75, "d": "M 470,600 C 480,700 470,820 470,940"}]},
        {"id": "foot_f", "ease": "linear", "morph": None, "rotate": None, "swap": None,
         "attach": {"to": "leg_f", "at": "end", "align": "tangent"}},
        {"id": "torso", "ease": "sine.inOut", "morph": None, "attach": None, "swap": None,
         "rotate": {"pivot": None, "keys": [{"t": 0.0, "v": 3}, {"t": 0.5, "v": 3}]}},
    ],
    "root": {"x": [{"t": 0.0, "v": 0}], "y": [{"t": 0.0, "v": 0}, {"t": 0.25, "v": -8}, {"t": 0.5, "v": 0},
                                               {"t": 0.75, "v": -8}], "ease": "sine.inOut"},
}, indent=1)


RIG_SCHEMA = {"type": "object", "properties": {"svg": {"type": "string"}, "notes": {"type": "string"}},
              "required": ["svg", "notes"], "additionalProperties": False}


async def prepare_rig(hub, svg: str, *, effort: str = "medium", job=None, seed=0, max_rounds: int = 2
                      ) -> tuple[str, list[str], int]:
    """Rig preparation (6.9): when the character SVG does not follow the rig convention, GPT-6 Sol
    regroups and renames its parts; the validator checks every attempt and feeds failures back.
    Returns (svg, remaining errors, rounds used)."""
    from .. import prompts, registry
    from ..providers.base import ProviderRequest
    from .vector import sanitize

    rig = load_rig(svg)
    errs = validate_rig(rig)
    if not errs:
        return svg, [], 0
    model = registry.model_for("vector_motion")
    current, rounds = svg, 0
    for rnd in range(max_rounds):
        rounds = rnd + 1
        png = (await render_svgs([current], rig.width, rig.height))[0]
        p = prompts.render("rig_prep", rig_name=RIG["name"], draw_order=", ".join(RIG["draw_order"]),
                           rubberhose=", ".join(RIG["rubberhose"]), width=rig.width, height=rig.height,
                           errors="\n".join(f"- {e}" for e in errs) if rnd else "", svg=current)
        req = ProviderRequest(op="respond", model=model, prompt=p.text, images=[png], schema=RIG_SCHEMA,
                              params={"reasoning_effort": effort, "schema_name": "rig_prep", "round": rnd,
                                      "max_output_tokens": 64000}, seed=seed, purpose="rig_prep")
        cands = await hub.call(req, job=job)
        out = json.loads(cands[0].data)
        try:
            cand = sanitize(out["svg"], rig.width, rig.height).svg
            errs = validate_rig(load_rig(cand))
        except Exception as e:  # unparseable SVG counts as a failed round
            errs = [f"SVG did not parse: {e}"[:200]]
            continue
        current = cand
        if not errs:
            break
    return current, errs, rounds


def _sample_path(d: str, n: int = 24) -> list[tuple[float, float]]:
    """Points along a path's M/L/C segments (absolute or relative): enough to sample a limb's colour."""
    cmds, nums = parse_path(d)
    pts: list[tuple[float, float]] = []
    cur = np.zeros(2)
    k = 0
    for letter, _flags in cmds:
        cnt = _ARGS[letter.upper()]
        vals = np.array(nums[k:k + cnt], float)
        k += cnt
        rel = letter.islower()
        up = letter.upper()
        if up == "M":
            cur = cur + vals[:2] if rel else vals[:2].copy()
        elif up == "L":
            nxt = cur + vals[:2] if rel else vals[:2]
            for t in np.linspace(0, 1, max(2, n // 4)):
                pts.append(tuple(cur + (nxt - cur) * t))
            cur = nxt
        elif up == "C":
            p1, p2, p3 = (cur + vals[0:2], cur + vals[2:4], cur + vals[4:6]) if rel else \
                (vals[0:2], vals[2:4], vals[4:6])
            for t in np.linspace(0, 1, n):
                q = (1 - t) ** 3 * cur + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3
                pts.append((float(q[0]), float(q[1])))
            cur = np.array(p3, float)
    return pts


async def fix_limb_looks(rig_svg: str, original_svg: str) -> tuple[str, list[str]]:
    """V7: rig preparation redraws limbs as strokes and can pick the wrong colour (pale legs on a dark-
    legged design) and drop the outline. Each rubberhose limb takes its colour from the original drawing
    along its rest path, and gets an outline copy underneath when the drawing outlines it there."""
    from ..color import hex_to_rgb, rgb_to_hex, to_oklab

    rig = load_rig(rig_svg)
    orig = to_rgba((await render_svgs([original_svg], rig.width, rig.height))[0])
    H, W = orig.shape[:2]
    root = etree.fromstring(rig_svg.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    ids = {el.get("id"): el for el in root.iter() if isinstance(el.tag, str) and el.get("id")}
    notes = []

    def px(x, y):
        xi, yi = int(round(x)), int(round(y))
        return orig[yi, xi] if 0 <= xi < W and 0 <= yi < H else None

    for pid in RUBBERHOSE:
        el = ids.get(pid)
        if el is None or pid not in rig.rest_paths:
            continue
        paths = [el] if el.tag.endswith("path") else [e for e in el.iter() if isinstance(e.tag, str)
                                                      and e.tag.endswith("path")]
        main = paths[-1]
        try:
            width = float(main.get("stroke-width") or 0)
        except ValueError:
            width = 0.0
        if width <= 0:
            continue
        pts = _sample_path(rig.rest_paths[pid])
        if len(pts) < 6:
            continue
        inner = pts[len(pts) // 8: len(pts) - len(pts) // 8] or pts
        cols = [c for c in (px(x, y) for x, y in inner) if c is not None and c[3] > 200]
        if len(cols) < 4:
            continue
        arr = np.array(cols)[:, :3].astype(np.uint8)
        light = to_oklab(arr)[:, 0] >= 0.3  # outline pixels are not the limb's fill
        fill = arr[light] if light.mean() >= 0.3 else arr
        limb = np.median(fill, axis=0).astype(np.uint8)
        stroke = main.get("stroke") or ""
        try:
            cur = np.array(hex_to_rgb(stroke), np.uint8) if stroke.startswith("#") else None
        except Exception:
            cur = None
        if cur is None or float(np.linalg.norm(to_oklab(limb[None])[0] - to_oklab(cur[None])[0])) > 0.1:
            main.set("stroke", rgb_to_hex(tuple(int(v) for v in limb)))
            notes.append(f"{pid} recoloured {stroke or 'none'} -> {rgb_to_hex(tuple(int(v) for v in limb))}")
        # outline: dark pixels just outside the limb's width in the original drawing
        if len(paths) == 1:
            dark = []
            for (x0, y0), (x1, y1) in zip(inner[:-1], inner[1:]):
                tx, ty = x1 - x0, y1 - y0
                norm = math.hypot(tx, ty) or 1.0
                nx, ny = -ty / norm, tx / norm
                for sgn in (1, -1):
                    c = px(x0 + sgn * nx * (width / 2 + 1.5), y0 + sgn * ny * (width / 2 + 1.5))
                    if c is not None and c[3] > 200:
                        dark.append(c[:3])
            if dark:
                lab = to_oklab(np.array(dark, np.uint8))
                share = float((lab[:, 0] < 0.4).mean())
                if share > 0.5:
                    ol = np.median(np.array(dark)[lab[:, 0] < 0.4], axis=0).astype(np.uint8)
                    copy_el = copy.deepcopy(main)
                    copy_el.set("stroke", rgb_to_hex(tuple(int(v) for v in ol)))
                    copy_el.set("stroke-width", f"{width + max(3.0, 0.14 * width):.1f}")
                    copy_el.attrib.pop("id", None)
                    main.addprevious(copy_el)
                    notes.append(f"{pid} outlined")
    return etree.tostring(root, encoding="unicode"), notes


async def author_loop(hub, c, svg: str, *, job=None, seed=0, max_rounds: int = 2) -> AuthorResult:
    from .. import prompts, registry
    from ..providers.base import ProviderRequest
    from .analyze import analyze_frames

    effort = c.__dict__.get("sol_effort", "medium")
    findings: list[Finding] = []
    original = svg
    svg, rig_errs, prep_rounds = await prepare_rig(hub, svg, effort=effort, job=job, seed=seed)
    if not rig_errs:
        try:
            svg, looks = await fix_limb_looks(svg, original)
        except Exception as e:  # a failed look check must not stop the job; it is reported
            looks = [f"limb look check skipped: {type(e).__name__}"]
        if looks:
            findings.append(Finding(metric="vector_rig", level="info", message="; ".join(looks)[:300],
                                    remedy="auto_fixed", auto_fixed=True))
    rig = load_rig(svg)
    if rig_errs:
        findings.append(Finding(metric="vector_rig", level="fail", severity=2, message="; ".join(rig_errs)[:300],
                                remedy="fix_round"))
    elif prep_rounds:
        findings.append(Finding(metric="vector_rig", level="info", message=f"rig prepared by GPT-6 Sol in "
                                                                           f"{prep_rounds} round(s)",
                                remedy="auto_fixed", auto_fixed=True))
    rest_png = (await render_svgs([svg], rig.width, rig.height))[0]
    som = set_of_mark(rest_png, rig)
    model = registry.model_for("vector_motion")
    ground = rig.boxes.get("foot_n", (0, 0, 0, rig.height * 0.92))[3]
    history = []
    best: AuthorResult | None = None
    problems = ""
    for rnd in range(max_rounds + 1):
        p = prompts.render("motion_author", action_title=prompts.action_title(c.spec.action), n=c.spec.frames,
                           loop=c.spec.loop,
                           frame_lines="\n".join(f"Frame {i + 1}: {f.line}" for i, f in enumerate(c.choreo.frames)),
                           rig_name=RIG["name"], draw_order=", ".join(RIG["draw_order"]),
                           rubberhose=", ".join(RIG["rubberhose"]), rigid="torso (parent root), head (parent torso)",
                           ground=round(ground, 1), width=rig.width, height=rig.height, findings=problems,
                           round=rnd, examples=EXAMPLES, svg=svg)
        images = [som]
        if history:
            images.append(history[-1]["contact"])
        req = ProviderRequest(op="respond", model=model, prompt=p.text, images=images, schema=SCHEMA,
                              params={"reasoning_effort": effort, "schema_name": "motion_spec",
                                      "action": c.spec.action, "frames": c.spec.frames, "width": rig.width,
                                      "height": rig.height, "round": rnd},
                              seed=seed, purpose="vector_motion")
        cands = await hub.call(req, job=job)
        spec = json.loads(cands[0].data)
        errs = validate_spec(spec, rig, c.spec.loop)
        if errs:
            problems = "\n".join(f"- {e}" for e in errs)
            history.append({"round": rnd, "errors": errs, "contact": som})
            continue
        frames = await render_spec(spec, rig, c.spec.frames, c.spec.loop)
        an = analyze_frames(frames, c.spec, extra={"motion": {}})
        fails = [f for f in an.report.findings if f.level == "fail"]
        from ..qa.judge import contact_sheet

        contact = contact_sheet(frames)
        history.append({"round": rnd, "errors": [], "score": an.report.score,
                        "findings": [f.message for f in an.report.findings], "contact": contact})
        result = AuthorResult(spec, frames, list(findings), {"rounds": rnd + 1, "score": an.report.score})
        if best is None or an.report.score > best.info["score"]:
            best = result
        if not fails and an.report.score >= 85:
            break
        problems = "\n".join(f"- frame {', '.join(str(i + 1) for i in f.frames) or 'all'}: {f.message}"
                             for f in an.report.findings if f.level in ("fail", "warn"))
    if best is None:
        findings.append(Finding(metric="motion_spec", level="fail", severity=3,
                                message=f"no valid motion spec after {max_rounds} fix rounds: {problems[:200]}",
                                remedy="fix_round"))
        frames = await render_spec({"parts": [], "root": {"x": [], "y": [], "ease": "linear"}}, rig,
                                   c.spec.frames, c.spec.loop)
        return AuthorResult({}, frames, findings, {"rounds": max_rounds + 1, "rig_svg": svg,
                                                  "history": [{k: v for k, v in h.items() if k != "contact"}
                                                              for h in history]})
    best.info["history"] = [{k: v for k, v in h.items() if k != "contact"} for h in history]
    best.info["rig_svg"] = svg
    return best


# ---------------------------------------------------------------------------
# Synthetic author (offline simulator): motion from the choreography's joint angles


def synthetic_author(req) -> dict:
    from .. import choreo
    from ..figure import Proportions, skeleton

    action = req.params.get("action", "walk")
    n = int(req.params.get("frames", 8))
    W, H = int(req.params.get("width", 512)), int(req.params.get("height", 512))
    ch = choreo.choreography(action, n)
    pr = Proportions(5.0)
    s = 0.8 * H
    ox, oy = W / 2, 0.92 * H
    loop = ch.loop

    def P(pt):
        return ox + pt[0] * s, oy - pt[1] * s

    def hose(parts, upper, lower):
        a, b = P(parts[upper].points[0]), P(parts[upper].points[1])
        cc = P(parts[lower].points[1])
        c1 = (a[0] + (b[0] - a[0]) * 0.9, a[1] + (b[1] - a[1]) * 0.9)
        c2 = (b[0] + (cc[0] - b[0]) * 0.1, b[1] + (cc[1] - b[1]) * 0.1)
        return f"M {a[0]:.1f},{a[1]:.1f} C {c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {cc[0]:.1f},{cc[1]:.1f}"

    rest = {p.name: p for p in skeleton({"hip_n": 5, "hip_f": -5, "shoulder_n": -6, "shoulder_f": 6,
                                         "elbow_n": 12, "elbow_f": 12, "knee_n": 4, "knee_f": 4}, pr)}
    rest_hip = P(rest["torso"].points[0])
    times = frame_times(n, loop)
    morphs = {k: [] for k in RUBBERHOSE}
    lean, head, ry = [], [], []
    for t, f in zip(times, ch.frames):
        parts = {p.name: p for p in skeleton(f.pose, pr)}
        # keep the torso's hip fixed in root space; bob goes to the root offset
        hip = P(parts["torso"].points[0])
        dy = hip[1] - rest_hip[1]
        for name, (u, lo) in {"arm_f": ("arm_f_upper", "arm_f_lower"), "leg_f": ("leg_f_upper", "leg_f_lower"),
                              "leg_n": ("leg_n_upper", "leg_n_lower"), "arm_n": ("arm_n_upper", "arm_n_lower")}.items():
            d = hose(parts, u, lo)
            cmds, nums = parse_path(d)
            nums = nums.copy()
            nums[1::2] -= dy
            morphs[name].append({"t": round(t, 4), "d": format_path(cmds, nums)})
        lean.append({"t": round(t, 4), "v": round(f.pose.get("lean", 0.0), 2)})
        head.append({"t": round(t, 4), "v": round(f.pose.get("head", 0.0), 2)})
        ry.append({"t": round(t, 4), "v": round(dy, 2)})
    parts_out = [{"id": k, "ease": "linear", "morph": v, "rotate": None, "attach": None, "swap": None}
                 for k, v in morphs.items()]
    parts_out.append({"id": "torso", "ease": "linear", "morph": None, "attach": None, "swap": None,
                      "rotate": {"pivot": [round(rest_hip[0], 2), round(rest_hip[1], 2)], "keys": lean}})
    parts_out.append({"id": "head", "ease": "linear", "morph": None, "attach": None, "swap": None,
                      "rotate": {"pivot": None, "keys": head}})
    for foot, host in (("foot_f", "leg_f"), ("foot_n", "leg_n"), ("hand_f", "arm_f"), ("hand_n", "arm_n")):
        parts_out.append({"id": foot, "ease": "linear", "morph": None, "rotate": None, "swap": None,
                          "attach": {"to": host, "at": "end", "align": "tangent"}})
    return {"motion": ch.name, "loop": loop, "parts": parts_out,
            "root": {"x": [{"t": 0.0, "v": 0.0}], "y": ry, "ease": "linear"}}
