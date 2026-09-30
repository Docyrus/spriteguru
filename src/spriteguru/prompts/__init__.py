"""Prompt compiler (section 7): versioned Jinja2 templates compiled from the SpriteSpec."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

import jinja2

from ..color import key_name
from ..spec import SpriteSpec, Style

_env = jinja2.Environment(loader=jinja2.PackageLoader("spriteguru", "prompts"), undefined=jinja2.StrictUndefined,
                          keep_trailing_newline=False, trim_blocks=True, lstrip_blocks=True)


@dataclass
class Prompt:
    template: str
    version: int
    text: str

    @property
    def id(self) -> str:
        return f"{self.template}.v{self.version}"

    def provenance(self) -> dict:
        return {"template": self.template, "version": self.version, "prompt": self.text}


def _latest(name: str) -> int:
    versions = [int(p.name.split(".v")[1].split(".")[0]) for p in resources.files("spriteguru.prompts").iterdir()
                if p.name.startswith(name + ".v") and p.name.endswith(".j2")]
    if not versions:
        raise KeyError(f"no template {name!r}")
    return max(versions)


def render(name: str, version: int | None = None, **ctx) -> Prompt:
    v = version or _latest(name)
    text = _env.get_template(f"{name}.v{v}.j2").render(**ctx).strip()
    return Prompt(name, v, text)


OUTLINES = {"dark": "a 1-pixel dark outline around the whole character",
            "selective": "selective outlines, darker than the fill", "none": "no outline"}
DIRS = {"E": "right", "W": "left", "N": "away from the camera", "S": "toward the camera"}


def style_block(style: Style, *, height: int | None = None, subject: str = "character") -> str:
    noun = {"character": "character", "vehicle": "vehicle", "machine": "machine", "effect": "effect"}[subject]
    if style.kind == "pixel":
        h = height or style.pixel_height or 48
        size = f"about {h} pixels tall" if subject != "effect" else f"about {h} pixels across"
        outline = OUTLINES[style.outline] if subject != "effect" else "no outline"
        return (f"Pixel art. The {noun} is {size} on a coarse pixel grid; every pixel is a crisp "
                f"square of identical size, aligned to one grid across the whole image. Limited palette of about "
                f"{style.palette_size} colours, flat shading with one shadow and one highlight tone per material, "
                f"{outline.replace('character', noun)}. Hard pixel edges, no anti-aliasing, gradients, blur or dithering noise.")
    if subject == "effect":
        return {"hd-cartoon": "Clean 2D cartoon game effect art: bold stylized shapes, vivid saturated colours, crisp edges.",
                "painted": "Hand-painted 2D game effect: painterly energy with soft luminous edges.",
                "vector": "Flat vector game effect: geometric shapes, solid fills, no texture."}[style.kind]
    if style.kind == "hd-cartoon":
        return ("Clean 2D cartoon game art: bold, consistent line weight, flat cel shading with one shadow tone, "
                "crisp edges and a silhouette that reads at small size.")
    if style.kind == "painted":
        return ("Hand-painted 2D game art: soft painterly shading inside the character, crisp clean silhouette "
                "edges against the background.")
    return "Flat vector game art: geometric shapes, solid fills, uniform strokes, no texture."


def view_block(view: str, facing: str) -> str:
    if view == "side":
        return (f"Strict side view (orthographic profile), facing {DIRS[facing]}, camera at mid-height, "
                "no perspective foreshortening.")
    return (f"Top-down three-quarter view as in classic 16-bit RPGs, camera above and in front, character facing "
            f"{DIRS[facing]}.")


def action_title(action: str, kind: str = "character") -> str:
    titles = {
        "character": {"walk": "a walk cycle", "run": "a run cycle", "idle": "an idle breathing loop", "jump": "a jump",
                      "attack-melee": "a melee sword attack", "cast": "a spell cast", "hurt": "a hurt reaction",
                      "death": "a death", "crouch": "a crouch", "climb": "a climbing cycle",
                      "push": "a pushing cycle", "fireball": "a two-handed energy projectile throw",
                      "intro": "a character-select intro: a heroic flourish that settles into a confident stance",
                      "intro-salute": "a character-select intro: an honourable salute and bow that rises into a "
                                      "confident stance",
                      "intro-weapon": "a character-select intro: brandishing the weapon and levelling it at the opponent",
                      "intro-taunt": "a character-select intro: a cocky beckoning taunt",
                      "intro-leap": "a character-select intro: a leap into a hero landing that rises into a "
                                    "confident stance",
                      "intro-powerup": "a character-select intro: tensing up and bursting with power in a roar"},
        "vehicle": {"idle": "an idling loop", "move": "a driving loop in place", "fire": "firing the main gun",
                    "destroyed": "being destroyed"},
        "machine": {"work": "a working loop", "activate": "powering up", "break": "breaking down"},
        "effect": {"projectile": "a projectile flying loop", "charge": "charging up", "impact": "an impact burst",
                   "explosion": "an explosion", "aura": "a flickering aura loop"},
    }
    return titles.get(kind, {}).get(action, action.replace("-", " "))


BG_NAMES = {"#000000": "black"}


def guided_sheet(spec: SpriteSpec, plan, key: str, choreo) -> Prompt:
    kind = spec.character.kind
    if kind in ("vehicle", "machine"):
        lines = [f"Frame {i + 1}: {f.line}" for i, f in enumerate(choreo.frames)]
        return render("guided_object", kind=kind, character=spec.character.description.rstrip("."), n=spec.frames,
                      cols=plan.cols, rows=plan.rows, key_name=key_name(key), key_hex=key.upper(),
                      view_block=view_block(spec.view, spec.facing).replace("camera at mid-height", "camera level"),
                      action_title=action_title(spec.action, kind), frame_lines="\n".join(lines),
                      style_block=style_block(spec.style, subject=kind), grounded=True)
    if kind == "effect":
        lines = [f"Frame {i + 1}: {f.line}" for i, f in enumerate(choreo.frames)]
        additive = spec.character.blend == "add"
        bg = "#000000" if additive else key.upper()
        return render("guided_effect", character=spec.character.description.rstrip("."), n=spec.frames,
                      cols=plan.cols, rows=plan.rows, bg_name=BG_NAMES.get(bg, key_name(bg)), bg_hex=bg,
                      action_title=action_title(spec.action, kind), frame_lines="\n".join(lines),
                      style_block=style_block(spec.style, subject="effect"), additive=additive)
    lines = [f"Frame {i + 1}: {f.line}" for i, f in enumerate(choreo.frames)]
    if choreo.amplitude == "subtle" or spec.action == "idle":
        lines.append("The movement is subtle, only a few pixels.")
    air = [str(i + 1) for i in choreo.airborne]
    air_list = (", ".join(air[:-1]) + " and " + air[-1]) if len(air) > 1 else (air[0] if air else "")
    return render("guided_sheet", character=spec.character.description.rstrip("."), n=spec.frames,
                  cols=plan.cols, rows=plan.rows, key_name=key_name(key), key_hex=key.upper(),
                  view_block=view_block(spec.view, spec.facing), action_title=action_title(spec.action),
                  frame_lines="\n".join(lines), style_block=style_block(spec.style), air_list=air_list)


def turnaround(description: str, style: Style, *, kind: str = "character", from_image: bool = False,
               blend: str = "normal") -> Prompt:
    """The character lock sheet (6.4); objects get a turnaround too, effects a single design view."""
    desc = description.rstrip(".") or f"the {kind} shown in image 1"
    if kind == "effect":
        additive = blend == "add"
        bg = "#000000" if additive else "#FF00FF"
        return render("effect_design", character=desc, style_block=style_block(style, subject="effect"),
                      additive=additive, bg_name="black" if additive else "magenta", bg_hex=bg, from_image=from_image)
    if kind in ("vehicle", "machine"):
        return render("turnaround_object", kind=kind, character=desc, style_block=style_block(style, subject=kind),
                      from_image=from_image)
    return render("turnaround", character=desc, style_block=style_block(style), from_image=from_image)


def describe(kind: str) -> Prompt:
    return render("describe", kind=kind)


def video(spec: SpriteSpec, key: str) -> Prompt:
    short = spec.character.description.split(".")[0].rstrip(".")
    return render("video", character_short=short[0].upper() + short[1:] if short else "The character",
                  action_title=action_title(spec.action, spec.character.kind),
                  view_block=view_block(spec.view, spec.facing),
                  key_name=key_name(key), key_hex=key.upper(), idle=spec.action == "idle")


def judge(n: int, action: str, kind: str = "character", lines: list[str] | None = None) -> Prompt:
    """`lines`: the choreography's per-frame intent, so intended motion (recoil tilt, smoke) is not
    reported as a defect; only passed when the frames still map one-to-one onto the choreography."""
    text = "\n".join(f"Frame {i + 1}: {l}" for i, l in enumerate(lines)) if lines and len(lines) == n else ""
    return render("judge", n=n, action=action_title(action, kind), subject=kind, lines=text)
