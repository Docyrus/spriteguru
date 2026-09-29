"""API keys: OS keychain through keyring, environment variables override (4.1).

Keys never leave this module except as the value handed to a provider client; nothing
here logs them, and the API reports only whether a key is configured.
"""

from __future__ import annotations

import os
from pathlib import Path

from .env import get as app_env

SERVICE = "spriteguru"
ENV = {
    "openai": "OPENAI_API_KEY",
    "fal": "FAL_KEY",
    "retrodiffusion": "RD_API_KEY",
    "quiver": "QUIVERAI_API_KEY",
}
# Alternative variable names accepted as well (checked after the canonical name).
ALIASES = {
    "openai": ("AI_PROVIDER_KEY_OPENAI",),
    "fal": ("AI_PROVIDER_KEY_FAL",),
    "retrodiffusion": ("AI_PROVIDER_KEY_RETRODIFFUSION", "AI_PROVIDER_KEY_RD"),
    "quiver": ("AI_PROVIDER_KEY_QUIVER", "QUIVER_API_KEY"),
}


def _env_names(provider: str) -> tuple[str, ...]:
    return (ENV[provider], *ALIASES.get(provider, ()))


def _dotenv_path() -> Path | None:
    explicit = app_env("ENV_FILE")
    if explicit:
        return Path(explicit)
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / ".env").is_file():
            return d / ".env"
    repo = Path(__file__).resolve().parents[2]
    return repo / ".env" if (repo / ".env").is_file() else None


def _load_dotenv() -> None:
    """Development convenience: a .env (working directory upward, or the repo root) seeds missing env vars."""
    path = _dotenv_path()
    if path is None or not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("'\"")
        if name and value and name not in os.environ:
            os.environ[name] = value


_load_dotenv()


def _keyring():
    try:
        import keyring

        return keyring
    except Exception:  # keyring backend missing (headless CI)
        return None


def get(provider: str) -> str | None:
    if provider in ENV:
        for name in _env_names(provider):
            if os.environ.get(name):
                return os.environ[name]
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(SERVICE, provider)
    except Exception:
        return None


def set(provider: str, value: str) -> None:  # noqa: A001 - mirrors `spriteguru keys set`
    if provider not in ENV:
        raise ValueError(f"unknown provider {provider!r}; expected one of {sorted(ENV)}")
    kr = _keyring()
    if kr is None:
        raise RuntimeError("no keychain backend available; set the environment variable instead")
    kr.set_password(SERVICE, provider, value.strip())


def delete(provider: str) -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(SERVICE, provider)
        except Exception:
            pass


def status() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for provider, env in ENV.items():
        hit = next((n for n in _env_names(provider) if os.environ.get(n)), None)
        source = f"env:{hit}" if hit else None
        if source is None:
            kr = _keyring()
            try:
                if kr is not None and kr.get_password(SERVICE, provider):
                    source = "keychain"
            except Exception:
                pass
        out[provider] = {"configured": source is not None, "source": source, "env": env}
    return out
