"""Typed specs shared by every stage, the CLI, the API and (through OpenAPI) the studio."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

StyleKind = Literal["pixel", "hd-cartoon", "painted", "vector"]
EngineName = Literal["phaser", "pixi", "godot", "unity", "gamemaker"]
Facing = Literal["E", "W", "N", "S"]
View = Literal["side", "top-down"]
SubjectKind = Literal["character", "vehicle", "machine", "effect"]
Blend = Literal["normal", "add"]


class Character(BaseModel):
    """The animated subject. Historically a character; `kind` extends it to vehicles, machines and
    visual effects, which get their own guides, choreography, prompts and checks."""

    description: str  # 1–3 sentences, visual facts only
    refs: list[str] = []  # approved turnaround views, project-relative paths
    palette: list[str] = []  # hex, extracted from the side view
    heads_tall: float | None = None  # measured from the side view
    mirrorable: bool = True  # symmetric design: left = flip(right)
    kind: SubjectKind = "character"
    blend: Blend = "normal"  # effects: "add" means generated on black and blended additively


class Style(BaseModel):
    kind: StyleKind
    pixel_height: int | None = None  # logical px of the character, pixel style only
    palette_size: int = 16
    outline: Literal["dark", "selective", "none"] = "dark"


class SpriteSpec(BaseModel):
    character: Character
    view: View = "side"
    facing: Facing = "E"  # side view uses E and W
    action: str  # walk, run, idle, jump, attack-melee, cast, hurt, death …
    frames: int = Field(ge=2, le=16)
    loop: bool
    motion: Literal["in-place", "root-motion"] = "in-place"
    style: Style
    key: str | None = None  # chroma key, auto-picked when empty (6.6)
    fps: int = 12
    engine: EngineName = "godot"


# ---------------------------------------------------------------------------
# Project folder records (section 5)


class Settings(BaseModel):
    session_cap_usd: float = 10.0
    job_cap_usd: float = 3.0
    sol_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "medium"
    luna_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "xhigh"
    provider_mode: Literal["live", "synthetic"] = "live"
    judge_enabled: bool = True
    judge_disabled: list[str] = []  # judge issue types switched off by calibration (precision < 0.8)
    # image task -> model; unset tasks use the registry default. Checked where it is written (API, CLI), not
    # on load, so a model that has left the registry falls back to the default instead of failing (IM2)
    image_models: dict[str, str] = {}


class ProjectConfig(BaseModel):
    name: str
    style: Style
    engine: EngineName = "godot"
    fps: int = 12
    asset_folder: str | None = None  # game asset folder; export copies final/ there
    settings: Settings = Settings()
    active_character: str | None = None  # the subject every studio tab follows (P8)


class CharacterRecord(BaseModel):
    name: str
    description: str
    style: Style
    mirrorable: bool = True
    kind: SubjectKind = "character"
    blend: Blend = "normal"
    source_image: str | None = None  # uploaded reference image, project-relative
    description_source: Literal["text", "image"] = "text"
    facing_in_source: Literal["left", "right", "front", "unknown"] = "unknown"
    palette: list[str] = []
    height_px: int | None = None
    heads_tall: float | None = None
    views: dict[str, str] = {}  # "front", "side-e", "side-w", "back" -> project-relative path
    turnaround: str | None = None
    turnaround_index: int = 0  # which turnaround candidate the views were cropped from
    view_warnings: list[str] = []  # e.g. views that touched on the turnaround and were cut apart (T9)
    svg: str | None = None  # vector characters only
    approved: bool = False
    turnaround_candidates: list[str] = []


# ---------------------------------------------------------------------------
# Analysis output (sections 10–14)

Level = Literal["fail", "warn", "info"]


class Finding(BaseModel):
    metric: str
    level: Level
    severity: int = Field(1, ge=1, le=3)
    frames: list[int] = []  # 0-based frame indices; empty means the whole sheet
    value: float | None = None
    threshold: float | None = None
    message: str
    remedy: str = "none"  # reroll, frame_repair, identity_fix, pose_fix, confirm_layout, ml_matte, ...
    auto_fixed: bool = False
    source: Literal["cv", "judge"] = "cv"
    corroborated: bool = False
    issue: str | None = None  # judge issue type


class FrameInfo(BaseModel):
    index: int
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 in sheet coordinates (tight fg box)
    region: tuple[int, int, int, int]  # region the frame was cut from
    anchor: tuple[float, float]  # torso x, ground y in sheet coordinates
    offset: tuple[float, float] = (0.0, 0.0)  # placement on the common canvas
    airborne: bool = False
    clipped: bool = False
    scale: float = 1.0
    flipped: bool = False
    duration_ms: int = 83
    source: str | None = None  # frame PNG, project-relative


class Report(BaseModel):
    spec: SpriteSpec | None = None
    source: str | None = None
    score: float = 0.0
    accepted: bool = False
    findings: list[Finding] = []
    frames: list[FrameInfo] = []
    canvas: tuple[int, int] = (0, 0)
    pivot: tuple[float, float] = (0.5, 0.94)
    layout: dict = {}
    matte: dict = {}
    temporal: dict = {}
    pixel: dict = {}
    order: list[int] = []
    timings_ms: dict[str, float] = {}
