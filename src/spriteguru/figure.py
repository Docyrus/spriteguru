"""2D capsule-limb figures posed from choreography joint angles.

One skeleton model draws three things: grey guide mannequins (6.3), coloured characters for
turnarounds and the offline simulator, and golden fixtures with known ground truth.

Figure coordinates: x forward (toward the facing direction), y up, unit = standing height.
Angles are degrees. Hip and shoulder angles rotate a limb forward from hanging straight down;
knee bend rotates the shin backward; elbow bend rotates the forearm forward; lean tilts the
torso forward; weapon is an absolute angle from straight up, positive toward the facing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

POSE_KEYS = ("hip_n", "knee_n", "ankle_n", "hip_f", "knee_f", "ankle_f", "shoulder_n", "elbow_n",
             "shoulder_f", "elbow_f", "lean", "head", "weapon", "air", "breath", "root_x")

# Mannequin greys: far limbs darker, near limbs lighter (depth cue), distinct head/torso/prop.
GREY = {"far": (106, 106, 106), "torso": (138, 138, 138), "head": (160, 160, 160),
        "near": (189, 189, 189), "prop": (214, 214, 214)}


@dataclass
class Proportions:
    heads_tall: float = 5.0

    @property
    def head(self) -> float:
        return 1.0 / self.heads_tall

    @property
    def neck(self) -> float:
        return 0.025

    @property
    def rest(self) -> float:
        return 1.0 - self.head - self.neck

    @property
    def legs(self) -> float:  # hip to sole
        return 0.60 * self.rest

    @property
    def torso(self) -> float:
        return 0.40 * self.rest

    @property
    def ankle_h(self) -> float:
        return 0.035

    @property
    def thigh(self) -> float:
        return 0.5 * (self.legs - self.ankle_h)

    @property
    def shin(self) -> float:
        return 0.5 * (self.legs - self.ankle_h)

    @property
    def upper_arm(self) -> float:
        return 0.58 * self.torso + 0.02

    @property
    def forearm(self) -> float:
        return 0.52 * self.torso + 0.02

    @property
    def foot(self) -> float:
        return 0.11

    # widths (unit height)
    @property
    def w_thigh(self) -> float:
        return 0.09

    @property
    def w_shin(self) -> float:
        return 0.074

    @property
    def w_arm(self) -> float:
        return 0.062

    @property
    def w_torso(self) -> float:
        return 0.2


@dataclass
class Skin:
    """How to paint each part class; mannequins use flat greys with no outline."""

    colors: dict[str, tuple[int, int, int]] = field(default_factory=lambda: dict(GREY))
    outline: tuple[int, int, int] | None = None
    outline_w: float = 0.0  # unit height
    features: bool = False  # eye, hair, belt, boots
    hair: tuple[int, int, int] = (60, 40, 30)
    eye: tuple[int, int, int] = (20, 20, 30)
    boots: tuple[int, int, int] | None = None
    belt: tuple[int, int, int] | None = None
    shield: tuple[int, int, int] = (150, 150, 160)
    cape: tuple[int, int, int] = (110, 30, 40)
    blade: tuple[int, int, int] = (205, 212, 222)


MANNEQUIN = Skin()


def _dir(angle_deg: float) -> np.ndarray:
    """Unit vector for a limb rotated forward by angle from straight down."""
    a = math.radians(angle_deg)
    return np.array([math.sin(a), -math.cos(a)])


def _up(angle_deg: float) -> np.ndarray:
    a = math.radians(angle_deg)
    return np.array([math.sin(a), math.cos(a)])


@dataclass
class Part:
    name: str
    cls: str  # far, near, torso, head, prop
    points: list[np.ndarray]
    width: float
    kind: str = "capsule"  # capsule, circle, polygon


def pose_full(pose: dict) -> dict:
    out = {k: 0.0 for k in POSE_KEYS}
    out.update({k: float(v) for k, v in pose.items() if k in POSE_KEYS})
    return out


@dataclass
class Props:
    weapon: bool = False
    shield: bool = False
    cape: bool = False


def skeleton(pose: dict, prop: Proportions, view: str = "side", facing: str = "E",
             props: Props | None = None) -> list[Part]:
    """Parts back to front, in figure coordinates with the ground at y = 0."""
    p = pose_full(pose)
    props = props or Props()
    front = view == "top-down" and facing in ("N", "S")
    parts = _front_parts(p, prop, facing, props) if front else _side_parts(p, prop, props)
    # ground lock: the lowest sole touches y = air
    lo = min(min(pt[1] for pt in part.points) - (part.width / 2 if part.kind != "polygon" else 0)
             for part in parts if part.name.startswith(("foot", "shin", "leg")))
    dy = p["air"] - lo
    dx = p["root_x"]
    for part in parts:
        part.points = [pt + np.array([dx, dy]) for pt in part.points]
    return parts


def _side_parts(p: dict, pr: Proportions, props: Props) -> list[Part]:
    hip = np.array([0.0, pr.legs])
    lean = p["lean"]
    shoulder = hip + pr.torso * _up(lean)
    shoulder = shoulder + np.array([0.0, p["breath"]])
    head_c = shoulder + (pr.neck + pr.head / 2) * _up(lean + p["head"]) + np.array([0.0, p["breath"] * 0.5])

    def leg(side: str):
        a, k, b = p[f"hip_{side}"], p[f"knee_{side}"], p[f"ankle_{side}"]
        knee = hip + pr.thigh * _dir(a)
        ankle = knee + pr.shin * _dir(a - k)
        phi = math.radians(a - k + b)
        toe = ankle + pr.foot * np.array([math.cos(phi), math.sin(phi)])
        return knee, ankle, toe

    def arm(side: str):
        s, e = p[f"shoulder_{side}"], p[f"elbow_{side}"]
        elbow = shoulder + pr.upper_arm * _dir(s + lean)
        wrist = elbow + pr.forearm * _dir(s + lean + e)
        return elbow, wrist

    kf, af, tf = leg("f")
    kn, an, tn = leg("n")
    ef, wf = arm("f")
    en, wn = arm("n")
    hand_r = 0.032
    parts: list[Part] = []
    if props.cape:
        tail = hip + np.array([-0.16 - 0.05 * math.sin(math.radians(lean)), -0.12])
        parts.append(Part("cape", "far", [shoulder + np.array([0.02, 0.0]), shoulder + np.array([-0.07, -0.02]),
                                          tail, hip + np.array([-0.02, -0.08])], 0.0, "polygon"))
    parts += [
        Part("arm_f_upper", "far", [shoulder, ef], pr.w_arm),
        Part("arm_f_lower", "far", [ef, wf], pr.w_arm * 0.9),
        Part("hand_f", "far", [wf], hand_r * 2, "circle"),
    ]
    if props.shield:
        parts.append(Part("shield", "prop", [(ef + wf) / 2 + np.array([0.03, 0.0])], 0.20, "circle"))
    parts += [
        Part("leg_f_upper", "far", [hip, kf], pr.w_thigh),
        Part("leg_f_lower", "far", [kf, af], pr.w_shin),
        Part("foot_f", "far", [af, tf], pr.w_shin * 0.8),
        Part("torso", "torso", [hip, shoulder], pr.w_torso),
        Part("neck", "torso", [shoulder, head_c], pr.w_arm),
        Part("head", "head", [head_c], pr.head, "circle"),
        Part("leg_n_upper", "near", [hip, kn], pr.w_thigh),
        Part("leg_n_lower", "near", [kn, an], pr.w_shin),
        Part("foot_n", "near", [an, tn], pr.w_shin * 0.8),
    ]
    weapon_parts = []
    if props.weapon:
        wa = math.radians(p["weapon"])
        d = np.array([math.sin(wa), math.cos(wa)])
        hilt = wn - 0.05 * d
        tip = wn + 0.36 * d
        weapon_parts.append(Part("weapon", "prop", [hilt, tip], 0.028))
    parts += weapon_parts
    parts += [
        Part("arm_n_upper", "near", [shoulder, en], pr.w_arm),
        Part("arm_n_lower", "near", [en, wn], pr.w_arm * 0.9),
        Part("hand_n", "near", [wn], hand_r * 2, "circle"),
    ]
    return parts


def _front_parts(p: dict, pr: Proportions, facing: str, props: Props) -> list[Part]:
    toward = 1.0 if facing == "S" else -1.0  # forward swing moves the foot down the screen for S
    hip_c = np.array([0.0, pr.legs])
    shoulder_c = hip_c + np.array([0.0, pr.torso + p["breath"]])
    head_c = shoulder_c + np.array([0.0, pr.neck + pr.head / 2])
    hx, sx = 0.05, 0.10

    def leg(side: str, sign: float):
        a, k = p[f"hip_{side}"], p[f"knee_{side}"]
        hip = hip_c + np.array([sign * hx, 0.0])
        vert = pr.thigh * math.cos(math.radians(a)) + pr.shin * math.cos(math.radians(a - k))
        fwd = pr.thigh * math.sin(math.radians(a)) + pr.shin * math.sin(math.radians(a - k))
        knee = hip + np.array([sign * 0.01, -pr.thigh * math.cos(math.radians(a)) - toward * 0.25 * pr.thigh * math.sin(math.radians(a))])
        ankle = hip + np.array([sign * 0.015, -vert - toward * 0.25 * fwd])
        toe = ankle + np.array([sign * 0.01, -0.02 * toward])
        return hip, knee, ankle, toe

    def arm(side: str, sign: float):
        s, e = p[f"shoulder_{side}"], p[f"elbow_{side}"]
        sh = shoulder_c + np.array([sign * sx, -0.01])
        up = pr.upper_arm * math.cos(math.radians(s))
        elbow = sh + np.array([sign * 0.03, -up - toward * 0.2 * pr.upper_arm * math.sin(math.radians(s))])
        fl = pr.forearm * math.cos(math.radians(s + e))
        wrist = elbow + np.array([sign * 0.01, -fl - toward * 0.2 * pr.forearm * math.sin(math.radians(s + e))])
        return sh, elbow, wrist

    # character's right side is screen-left when facing S, screen-right when facing N
    rs = -1.0 if facing == "S" else 1.0
    hn, kn, an, tn = leg("n", rs)
    hf, kf, af, tf = leg("f", -rs)
    sn, en, wn = arm("n", rs)
    sf, ef, wf = arm("f", -rs)
    parts = [
        Part("leg_f_upper", "far", [hf, kf], pr.w_thigh),
        Part("leg_f_lower", "far", [kf, af], pr.w_shin),
        Part("foot_f", "far", [af, tf], pr.w_shin),
        Part("leg_n_upper", "near", [hn, kn], pr.w_thigh),
        Part("leg_n_lower", "near", [kn, an], pr.w_shin),
        Part("foot_n", "near", [an, tn], pr.w_shin),
        Part("torso", "torso", [hip_c, shoulder_c], pr.w_torso * 1.35),
        Part("neck", "torso", [shoulder_c, head_c], pr.w_arm),
        Part("head", "head", [head_c], pr.head, "circle"),
        Part("arm_f_upper", "far", [sf, ef], pr.w_arm),
        Part("arm_f_lower", "far", [ef, wf], pr.w_arm * 0.9),
        Part("hand_f", "far", [wf], 0.064, "circle"),
        Part("arm_n_upper", "near", [sn, en], pr.w_arm),
        Part("arm_n_lower", "near", [en, wn], pr.w_arm * 0.9),
        Part("hand_n", "near", [wn], 0.064, "circle"),
    ]
    if props.weapon:
        parts.append(Part("weapon", "prop", [wn + np.array([0, -0.03]), wn + np.array([rs * 0.05, 0.30])], 0.028))
    return parts


def bounds(parts: list[Part]) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for part in parts:
        r = part.width / 2 if part.kind != "polygon" else 0.0
        for pt in part.points:
            xs += [pt[0] - r, pt[0] + r]
            ys += [pt[1] - r, pt[1] + r]
    return min(xs), min(ys), max(xs), max(ys)


def render(parts: list[Part], height_px: float, skin: Skin = MANNEQUIN, *, facing: str = "E",
           ss: int = 4, margin_px: int = 4, view: str = "side",
           crisp: bool = False) -> tuple[Image.Image, tuple[float, float]]:
    """Render parts to an RGBA image; returns the image and the figure origin (ground under hip).

    Side views are drawn facing +x; `facing` W mirrors the result. `crisp` renders true pixel art:
    nearest-neighbour downsampling and binary alpha, one flat colour per pixel.
    """
    x0, y0, x1, y1 = bounds(parts)
    ow = skin.outline_w if skin.outline else 0.0
    x0, y0, x1, y1 = x0 - ow, y0 - ow, x1 + ow, y1 + ow
    s = height_px * ss
    m = margin_px * ss
    W = int(math.ceil((x1 - x0) * s)) + 2 * m
    Hh = int(math.ceil((y1 - y0) * s)) + 2 * m
    img = Image.new("RGBA", (W, Hh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def P(pt):
        return ((pt[0] - x0) * s + m, (y1 - pt[1]) * s + m)

    def capsule(a, b, w, color):
        pa, pb = P(a), P(b)
        r = w * s / 2
        draw.line([pa, pb], fill=color, width=max(1, int(round(w * s))))
        for q in (pa, pb):
            draw.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=color)

    def circle(c, d, color):
        q = P(c)
        r = d * s / 2
        draw.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=color)

    def shape(part: Part, color, grow: float = 0.0):
        if part.kind == "capsule":
            capsule(part.points[0], part.points[1], part.width + 2 * grow, color)
        elif part.kind == "circle":
            circle(part.points[0], part.width + 2 * grow, color)
        else:
            pts = [P(pt) for pt in part.points]
            draw.polygon(pts, fill=color)
            if grow > 0:
                draw.line(pts + [pts[0]], fill=color, width=max(1, int(2 * grow * s)))

    for part in parts:
        color = _part_color(part, skin)
        if skin.outline:
            shape(part, skin.outline + (255,), grow=skin.outline_w)
        shape(part, color + (255,))
        if skin.features:
            _features(draw, part, skin, P, s)

    # downsample with box filtering for clean anti-aliased edges (or nearest for crisp pixel art)
    if crisp:
        arr = np.asarray(img)[ss // 2::ss, ss // 2::ss].copy()
        arr[..., 3] = np.where(arr[..., 3] >= 128, 255, 0)
        arr[arr[..., 3] == 0] = 0
        out = Image.fromarray(arr, "RGBA")
    else:
        out = img.resize((max(1, W // ss), max(1, Hh // ss)), Image.Resampling.BOX)
    origin = ((0 - x0) * height_px + margin_px, (y1 - 0) * height_px + margin_px)
    if facing == "W":
        out = out.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        origin = (out.width - origin[0], origin[1])
    return out, origin


def _part_color(part: Part, skin: Skin) -> tuple[int, int, int]:
    if skin.features:
        if part.name == "weapon":
            return skin.blade
        if part.name.startswith("foot") and skin.boots:
            return skin.boots
        if part.name == "shield":
            return skin.shield
        if part.name == "cape":
            return skin.cape
    return skin.colors.get(part.cls, skin.colors.get("torso", (128, 128, 128)))


def _features(draw: ImageDraw.ImageDraw, part: Part, skin: Skin, P, s: float) -> None:
    if part.name == "head" and part.kind == "circle":
        c = part.points[0]
        r = part.width / 2
        q = P(c)
        rr = r * s
        # hair: the back and top of the head (asymmetric, so facing is visible)
        draw.pieslice([q[0] - rr, q[1] - rr, q[0] + rr, q[1] + rr], 150, 320, fill=skin.hair + (255,))
        # eye toward the front
        e = P(c + np.array([0.45 * r, 0.1 * r]))
        er = max(1.0, 0.13 * r * s)
        draw.ellipse([e[0] - er, e[1] - er, e[0] + er, e[1] + er], fill=skin.eye + (255,))
    if part.name == "torso" and skin.belt:
        a, b = part.points
        mid = a + 0.18 * (b - a)
        w = part.width * 0.5
        d = (b - a) / (np.linalg.norm(b - a) + 1e-9)
        n = np.array([d[1], -d[0]])
        p1, p2 = P(mid + n * w), P(mid - n * w)
        draw.line([p1, p2], fill=skin.belt + (255,), width=max(1, int(0.03 * s)))


def character_skin(seed: int, *, outline: bool = True) -> Skin:
    """A deterministic coloured character for turnarounds, the simulator and fixtures.

    Colours stay away from the chroma keys (saturated magenta, green, cyan, blue).
    """
    rng = np.random.default_rng(seed)
    tunics = [(178, 52, 48), (52, 92, 168), (196, 140, 42), (88, 128, 60), (150, 90, 60), (60, 120, 130)]
    pants = [(70, 62, 58), (58, 64, 88), (96, 80, 60), (48, 48, 56)]
    skins = [(236, 196, 160), (198, 148, 108), (150, 102, 72), (244, 214, 186)]
    hairs = [(60, 40, 30), (150, 96, 40), (30, 28, 34), (210, 180, 110)]
    tunic = tunics[rng.integers(len(tunics))]
    limb = tuple(int(c * 0.82) for c in tunic)
    pant = pants[rng.integers(len(pants))]
    far = tuple(int(c * 0.72) for c in limb)
    return Skin(
        colors={"far": far, "near": limb, "torso": tunic, "head": skins[rng.integers(len(skins))],
                "prop": (205, 212, 222)},
        outline=(28, 24, 30) if outline else None, outline_w=0.012 if outline else 0.0, features=True,
        hair=hairs[rng.integers(len(hairs))], boots=pant, belt=(60, 40, 28))
