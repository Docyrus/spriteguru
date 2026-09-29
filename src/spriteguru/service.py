"""Operations shared by the CLI and the API, so no logic lives in the transport layer."""

from __future__ import annotations

from . import choreo as choreo_lib
from . import generate as gen
from . import registry
from .project import Project, write_json
from .spec import SpriteSpec


def create_animation(project: Project, character: str, action: str, *, facing: str = "E", frames: int | None = None,
                     loop: bool | None = None, view: str = "side", fps: int | None = None,
                     engine: str | None = None, motion: str = "in-place") -> tuple[str, SpriteSpec]:
    rec = project.character(character)
    if action not in choreo_lib.actions(rec.kind):
        raise ValueError(f"unknown {rec.kind} action {action!r}; known: {', '.join(choreo_lib.actions(rec.kind))}")
    spec = gen.spec_for_character(rec, action, facing=facing, frames=frames, loop=loop, view=view,
                                  fps=fps or project.config.fps, engine=engine or project.config.engine,
                                  motion=motion)
    anim_id = project.animation_id(rec.name, action, facing)
    project.save_spec(anim_id, spec)
    write_json(project.animation_dir(anim_id) / "meta.json", {"character": rec.name})
    return anim_id, spec


def estimate(project: Project, anim_id: str, *, mode: str | None = None) -> dict:
    """Route, estimated cost and (for guided sheets) the guide canvas, before anything is sent."""
    mode = mode or project.config.settings.provider_mode
    spec = project.spec(anim_id)
    import json

    meta = json.loads((project.animation_dir(anim_id) / "meta.json").read_text())
    rec = project.character(meta["character"])
    c = gen.compile_spec(project, spec, rec, mode=mode)
    cost = 0.0
    lines = []
    if c.route in ("guided", "guided-pixel"):
        m = registry.model_for("guided_sheet")
        est = registry.estimate_cost(m, "edit", {}, prompt=c.prompt.text,
                                     input_sizes=[(c.plan.width, c.plan.height), (512, 512)], n=2,
                                     size=(c.plan.width, c.plan.height))
        lines.append({"model": m, "what": "guided sheet, best of 2", "usd": round(est, 4)})
        cost += est
    elif c.route == "rd-loop":
        est = registry.estimate_cost(registry.model_for("pixel_loop"), "rd_animate", {})
        lines.append({"model": "rd-advanced-animation", "what": "pixel loop", "usd": est})
        cost += est
    elif c.route == "vector-anim":
        if not rec.svg:
            m = registry.model_for("vector_character")
            est = registry.estimate_cost(m, "svg_generate", {})
            lines.append({"model": m, "what": f"vector {rec.kind}", "usd": round(est, 4)})
            cost += est
        m = registry.model_for("vector_idle")
        est = registry.estimate_cost(m, "svg_animate", {})
        lines.append({"model": m, "what": "animation", "usd": round(est, 4)})
        cost += est
    elif c.route == "video-loop":
        m = registry.model_for("hd_loop")
        est = registry.estimate_cost(m, "video", registry.model(m)["params"])
        lines.append({"model": m, "what": "5 s video", "usd": round(est, 4)})
        cost += est
    else:
        if not rec.svg:
            m = registry.model_for("vector_character")
            est = registry.estimate_cost(m, "svg_generate", {})
            lines.append({"model": m, "what": "vector character", "usd": round(est, 4)})
            cost += est
        m = registry.model_for("vector_idle" if c.route == "vector-idle" else "vector_motion")
        est = registry.estimate_cost(m, "respond", {}) * (1 if c.route == "vector-idle" else 3)
        lines.append({"model": m, "what": "idle animation" if c.route == "vector-idle" else "motion spec, up to 3 rounds",
                      "usd": round(est, 4)})
        cost += est
    if project.config.settings.judge_enabled:
        j = registry.estimate_cost(registry.model_for("judge"), "respond", {}) * (2 if c.route.startswith("guided") else 1)
        lines.append({"model": registry.model_for("judge"), "what": "QA judge", "usd": round(j, 5)})
        cost += j
    if mode == "synthetic":
        cost = 0.0
        lines = [{**l, "usd": 0.0} for l in lines]
    return {"anim_id": anim_id, "compiled": c.summary(), "estimate_usd": round(cost, 4), "lines": lines,
            "guide": c.guide, "spec": spec.model_dump(mode="json")}
