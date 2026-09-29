"""Choreography library (7.5): frame lines for the prompt and poses for the guide.

Each action is authored once at its classic key-pose count. Other frame counts are derived by
resampling the key poses (cyclically for loops), so prompt text and guide never disagree.

Three frame modes: "skeleton" (characters: joint angles for the mannequins), "rigid" (vehicles and
machines: one transform of the whole silhouette) and "effect" (effects: shape keyframes).
Keys are `<action>.side.<K>` for characters and `<kind>.<action>.side.<K>` for other kinds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

import yaml

from ..figure import POSE_KEYS


KINDS = ("character", "vehicle", "machine", "effect")
RIGID_KEYS = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "sx": 1.0, "sy": 1.0}


@dataclass
class FrameChoreo:
    line: str
    pose: dict[str, float]
    airborne: bool = False
    key: float = 0.0  # position in key-pose space
    shapes: list[dict] | None = None  # effect mode


@dataclass
class Choreo:
    name: str
    action: str
    loop: bool
    frames: list[FrameChoreo]
    harmonics: list[int] = field(default_factory=lambda: [2, 4])
    timing_ms: list[int] | None = None
    props: list[str] = field(default_factory=list)
    amplitude: str | None = None
    impact: int | None = None  # 0-based frame index of the attack impact
    apex: int | None = None  # 0-based frame index of the jump apex
    returns_to_start: bool = False
    kind: str = "character"
    mode: str = "skeleton"  # skeleton, rigid, effect

    @property
    def airborne(self) -> list[int]:
        return [i for i, f in enumerate(self.frames) if f.airborne]


@lru_cache(maxsize=1)
def library() -> dict[str, dict]:
    lib: dict[str, dict] = {}
    for entry in resources.files("spriteguru.choreo").iterdir():
        if entry.name.endswith(".yaml"):
            lib.update(yaml.safe_load(entry.read_text()))
    return lib


def _split(key: str) -> tuple[str, str]:
    parts = key.split(".")
    kind = parts[0] if parts[0] in KINDS[1:] else "character"
    start = 1 if kind != "character" else 0
    return kind, "-".join(parts[start: parts.index("side")])


def _action_of(key: str) -> str:
    return _split(key)[1]


def actions(kind: str = "character") -> list[str]:
    """A kind's actions in library order (the order the choreography files declare them)."""
    return list(dict.fromkeys(a for k in library() for kk, a in [_split(k)] if kk == kind))


def _entry(action: str, kind: str = "character") -> tuple[str, dict]:
    for key, value in library().items():
        if _split(key) == (kind, action):
            return key, value
    raise KeyError(f"no {kind} choreography for action {action!r}; known: {actions(kind)}")


def _mode(kind: str, entry: dict) -> str:
    if kind == "effect":
        return "effect"
    return entry.get("mode", "rigid" if kind in ("vehicle", "machine") else "skeleton")


def _lerp_rigid(a: dict, b: dict, u: float) -> dict:
    return {k: float(a.get(k, d)) + (float(b.get(k, d)) - float(a.get(k, d))) * u for k, d in RIGID_KEYS.items()}


def _lerp_shapes(a: list[dict], b: list[dict], u: float) -> list[dict]:
    if len(a) != len(b) or any(x.get("type") != y.get("type") for x, y in zip(a, b)):
        return [dict(x) for x in (a if u < 0.5 else b)]
    out = []
    for x, y in zip(a, b):
        s = {}
        for k, v in x.items():
            s[k] = v + (y.get(k, v) - v) * u if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "n" else v
        out.append(s)
    return out


def _lerp_pose(a: dict, b: dict, u: float) -> dict:
    out = {}
    for k in POSE_KEYS:
        va, vb = float(a.get(k, 0.0)), float(b.get(k, 0.0))
        out[k] = va + (vb - va) * u
    return out


def _swap_sides(text: str) -> str:
    return (text.replace("right", "\0").replace("left", "right").replace("\0", "left")
            .replace("Right", "\0").replace("Left", "Right").replace("\0", "Left"))


# K17: exhaust (steam, smoke) in object motion is decoration the description has to ask for
_EXHAUST = [(r"\bsteam\w*", "steam"), (r"\bsmok\w*|\bchimney", "smoke"), (r"\bexhaust\w*", "exhaust"),
            (r"\bvapou?r\w*", "vapour"), (r"\bfumes?\b", "fumes")]


def exhaust_word(description: str | None) -> str | None:
    import re

    text = (description or "").lower()
    for pattern, word in _EXHAUST:
        if re.search(pattern, text):
            return word
    return None


def render_line(line: str, description: str | None) -> str:
    """Keep a line's optional `[[ ... {exhaust} ... ]]` clause only when the description names exhaust."""
    import re

    word = exhaust_word(description)
    if word:
        return re.sub(r"\[\[(.*?)\]\]", lambda m: m.group(1).replace("{exhaust}", word), line)
    return re.sub(r"\[\[.*?\]\]", "", line)


def choreography(action: str, frames: int, *, facing: str = "E", kind: str = "character",
                 description: str | None = None) -> Choreo:
    key, e = _entry(action, kind)
    mode = _mode(kind, e)
    keys = e["frames"]
    K = len(keys)
    loop = bool(e.get("loop", False))
    air_keys = {i - 1 for i in e.get("airborne", [])}
    out: list[FrameChoreo] = []
    for j in range(frames):
        if frames == K:
            pos = float(j)
        elif loop:
            pos = j * K / frames
        else:
            pos = j * (K - 1) / max(1, frames - 1)
        i0 = int(pos) % K
        i1 = (i0 + 1) % K if loop else min(i0 + 1, K - 1)
        u = pos - int(pos)
        shapes = None
        if mode == "effect":
            shapes = _lerp_shapes(keys[i0]["shapes"], keys[i1]["shapes"], u)
            pose = {}
        elif mode == "rigid":
            pose = _lerp_rigid(keys[i0].get("pose", {}), keys[i1].get("pose", {}), u)
        else:
            pose = _lerp_pose(keys[i0]["pose"], keys[i1]["pose"], u)
        nearest = int(round(pos)) % K if loop else min(int(round(pos)), K - 1)
        line = keys[nearest]["line"]
        if abs(u) > 0.2 and abs(u) < 0.8:
            nxt = keys[i1]["line"].split(":")[0]
            line = f"Between {keys[i0]['line'].split(':')[0].lower()} and {nxt.lower()}: " + line.split(": ", 1)[-1]
        airborne = pose.get("air", 0.0) > 0.02 or (nearest in air_keys and pose.get("air", 0.0) > 0.0)
        if facing == "W":
            line = _swap_sides(line)
        if mode != "skeleton":
            airborne = False
        out.append(FrameChoreo(line=render_line(line, description), pose=pose, airborne=airborne, key=pos,
                               shapes=shapes))

    timing = None
    if e.get("timing_ms"):
        t = e["timing_ms"]
        if frames == K:
            timing = list(t)
        else:
            total = sum(t)
            timing = [max(40, int(round(total / frames)))] * frames
    impact = e.get("impact")
    apex = e.get("apex")

    def remap(idx):
        if idx is None:
            return None
        k = idx - 1
        return int(round(k * (frames - 1) / max(1, K - 1))) if not loop else int(round(k * frames / K)) % frames

    return Choreo(name=key if frames == K else f"{key}->{frames}", action=action, loop=loop, frames=out,
                  harmonics=list(e.get("harmonics", [2, 4])), timing_ms=timing, props=list(e.get("props", [])),
                  amplitude=e.get("amplitude"), impact=remap(impact), apex=remap(apex),
                  returns_to_start=bool(e.get("returns_to_start", False)), kind=kind, mode=mode)


def default_frames(action: str, kind: str = "character") -> int:
    key, e = _entry(action, kind)
    return len(e["frames"])


def default_loop(action: str, kind: str = "character") -> bool:
    return bool(_entry(action, kind)[1].get("loop", False))
