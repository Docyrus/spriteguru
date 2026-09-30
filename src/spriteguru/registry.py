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


# ---------------------------------------------------------------------------
# Selectable image models (IM1-IM10)


def image_tasks() -> dict[str, str]:
    """Task -> label for the image tasks a project can point at another model."""
    return dict(load()["image_tasks"])


def image_models() -> list[str]:
    return list(load()["image_models"])


def check_image_choice(task: str, model_id: str) -> None:
    """IM2: refuse an unknown task or model, naming the allowed values."""
    if task not in image_tasks():
        raise ValueError(f"unknown image task {task!r}; tasks: {', '.join(image_tasks())}")
    if model_id not in image_models():
        raise ValueError(f"unknown image model {model_id!r} for {task}; models: {', '.join(image_models())}")


def image_model(task: str, chosen: dict[str, str] | None = None) -> str:
    """The model for an image task: the project's choice, else the task's default role (IM2: a stale
    name falls back to the default)."""
    m = (chosen or {}).get(task)
    return m if m in image_models() else model_for(task)


def resolve_image_models(chosen: dict[str, str] | None = None) -> dict[str, str]:
    return {t: image_model(t, chosen) for t in image_tasks()}


def stale_image_choices(chosen: dict[str, str] | None) -> list[str]:
    return [f"{t}: {m}" for t, m in (chosen or {}).items() if t in image_tasks() and m not in image_models()]


def supports_mask(model_id: str) -> bool:
    return bool(model(model_id).get("image", {}).get("mask", False))


def fal_image_size(size: tuple[int, int], caps: dict[str, Any]) -> tuple[int, int]:
    """IM3: the canvas aspect scaled into the model's pixel range, each side a multiple of 16."""
    w, h = size
    lo, hi = float(caps.get("min_pixels", 0)), float(caps.get("max_pixels", 4096 * 4096))
    px = float(w * h)
    s = (lo / px) ** 0.5 if px < lo else (hi / px) ** 0.5 if px > hi else 1.0

    def snap(v: float, up: bool) -> int:
        q = v / 16
        return int(16 * (int(q) + (1 if up and q != int(q) else 0))) or 16

    up = px * s * s <= lo * 1.0001  # rounding must not drop below the minimum or rise above the maximum
    W, H = snap(w * s, up), snap(h * s, up)
    while W * H > hi:
        W, H = W - 16, max(16, round(H * (W - 16) / W / 16) * 16)
    return W, H


def image_model_info(model_id: str) -> dict[str, Any]:
    """What Settings shows for a selectable model."""
    m = model(model_id)
    price = m.get("price", {})
    if "usd_per_image" in price:
        price_text = f"${price['usd_per_image']:g} per image"
    elif "usd_first_mp" in price:
        price_text = f"${price['usd_first_mp']:g} first MP + ${price['usd_per_extra_mp']:g} per extra MP, inputs included"
    else:
        price_text = "token-billed, about $0.05 per sheet-sized image"
    return {"id": model_id, "label": m.get("label", model_id), "maker": m.get("maker", ""),
            "provider": m["provider"], "mask": supports_mask(model_id), "price": price_text}


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
    if "usd_per_image" in price:
        return price["usd_per_image"] * n
    if "usd_first_mp" in price:
        # one call per image; each processes its inputs and its output (IM6)
        import math

        out_mp = math.ceil((size or (1024, 1024))[0] * (size or (1024, 1024))[1] / 1e6)
        in_mp = sum(math.ceil(iw * ih / 1e6) for iw, ih in (input_sizes or []))
        return n * (price["usd_first_mp"] + price["usd_per_extra_mp"] * max(0, out_mp + in_mp - 1))
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
