"""Model registry: ids, fixed parameters, prices and routes (registry.yaml)."""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from .spec import SpriteSpec


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    text = resources.files("spriteguru").joinpath("registry.yaml").read_text()
    return yaml.safe_load(text)


def model(model_id: str) -> dict[str, Any]:
    models = load()["models"]
    if model_id not in models:
        raise KeyError(f"unknown model {model_id!r}; known: {sorted(models)}")
    return models[model_id]


def model_for(role: str) -> str:
    return load()["roles"][role]


def provider_config(provider: str) -> dict[str, Any]:
    return load()["providers"][provider]


def aspect_target(action: str) -> float:
    targets = load()["aspect_targets"]
    return float(targets.get(action, targets.get(action.split("-")[0], 1.0)))


def route_for(spec: SpriteSpec) -> str:
    """The route follows from the subject kind, the style and the loop flag (6.5); nothing is chosen
    per job. Vehicles, machines and effects always use guided sheets (their placement and timing
    come from the guide), or Quiver animation in vector style (the AI-coded rig is humanoid, K7)."""
    kind = spec.character.kind
    if kind != "character":
        if spec.style.kind == "vector":
            return "vector-anim"
        return "guided-pixel" if spec.style.kind == "pixel" else "guided"
    routes = load()["routes"][spec.style.kind]
    if spec.style.kind == "vector" and spec.loop and spec.action == "idle":
        return routes["idle"]
    return routes["loop" if spec.loop else "oneshot"]


# ---------------------------------------------------------------------------
# Cost estimates (4.4)


def _image_tokens(w: int, h: int, per_mpx: float) -> float:
    return w * h / 1e6 * per_mpx


def estimate_cost(model_id: str, op: str, params: dict[str, Any], *, prompt: str = "",
                  input_sizes: list[tuple[int, int]] | None = None, n: int = 1,
                  size: tuple[int, int] | None = None, today: dt.date | None = None) -> float:
    m = model(model_id)
    price = m.get("price", {})
    if "usd_per_mtok_image_out" in price:
        w, h = size or (1024, 1024)
        text_in = len(prompt) / 4
        image_in = sum(_image_tokens(iw, ih, price.get("in_tokens_per_mpx", 1300)) for iw, ih in (input_sizes or []))
        image_out = n * _image_tokens(w, h, price.get("out_tokens_per_mpx_high", 4000))
        return (text_in * price["usd_per_mtok_text_in"] + image_in * price["usd_per_mtok_image_in"]
                + image_out * price["usd_per_mtok_image_out"]) / 1e6
    if "usd_per_second" in price:
        today = today or dt.date.today()
        rate = price["usd_per_second"]
        until = price.get("promo_until")
        if until and today <= dt.date.fromisoformat(str(until)):
            rate = price.get("promo_usd_per_second", rate)
        return rate * float(params.get("duration", 5)) * n
    if "usd_per_call" in price:
        if params.get("prompt_style", "").endswith("custom_action") and "usd_per_call_custom_action" in price:
            return price["usd_per_call_custom_action"] * n
        return price["usd_per_call"] * n
    if "usd_per_mtok_in" in price:
        tin = price.get("est_tokens_in", 4000) + len(prompt) / 4
        tout = price.get("est_tokens_out", 4000)
        return (tin * price["usd_per_mtok_in"] + tout * price["usd_per_mtok_out"]) / 1e6 * n
    return 0.0


def actual_cost(model_id: str, usage: dict[str, Any] | None, fallback: float) -> float:
    """Cost from provider-reported usage when available, else the estimate."""
    if not usage:
        return fallback
    price = model(model_id).get("price", {})
    if "usd_per_mtok_image_out" in price:
        itd = usage.get("input_tokens_details") or {}
        otd = usage.get("output_tokens_details") or {}
        text_in = itd.get("text_tokens", 0)
        image_in = itd.get("image_tokens", max(0, usage.get("input_tokens", 0) - text_in))
        image_out = otd.get("image_tokens", usage.get("output_tokens", 0))
        return (text_in * price["usd_per_mtok_text_in"] + image_in * price["usd_per_mtok_image_in"]
                + image_out * price["usd_per_mtok_image_out"]) / 1e6
    if "usd_per_mtok_in" in price:
        return (usage.get("input_tokens", 0) * price["usd_per_mtok_in"]
                + usage.get("output_tokens", 0) * price["usd_per_mtok_out"]) / 1e6
    if "balance_cost" in usage:
        return float(usage["balance_cost"])
    return fallback
