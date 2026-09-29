from __future__ import annotations

import os


def get(name: str, default: str | None = None) -> str | None:
    canonical = os.environ.get(f"SPRITEGURU_{name}")
    if canonical:
        return canonical
    legacy = os.environ.get(f"SPRITEKIT_{name}")
    return legacy if legacy else default


def present(name: str) -> bool:
    return bool(get(name))
