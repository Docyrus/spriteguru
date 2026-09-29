"""Where the cloud is and who this machine is (cloud plan 2 and 4.2)."""

from __future__ import annotations

import base64
import hashlib
import os
import platform as _platform
import re
import secrets
import socket
import subprocess
import sys
from urllib.parse import urlsplit

from .. import library

DEFAULT_URL = "https://spriteplay.com"  # N14: the website moved from spriteguru.com
CLIENT_ID = "spriteguru-desktop"  # registered on the server under its SpriteGuru name (N9)
URL_ENV = ("SPRITEPLAY_CLOUD_URL", "SPRITEGURU_CLOUD_URL")  # the SpriteGuru-era name still works (N8)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")


class ConfigError(ValueError):
    pass


def base_url() -> str:
    """SPRITEPLAY_CLOUD_URL (or SPRITEGURU_CLOUD_URL) or spriteplay.com. Plain HTTP only for a
    development server on this machine (plan 10)."""
    name = next((n for n in URL_ENV if os.environ.get(n)), URL_ENV[0])
    url = (os.environ.get(name) or DEFAULT_URL).strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS):
        return url
    raise ConfigError(f"{name} must be https (or http on localhost), not {url!r}")


def _os_machine_id() -> str | None:
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True,
                                 text=True, timeout=5).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            return m.group(1) if m else None
        if os.name == "nt":
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                return str(winreg.QueryValueEx(k, "MachineGuid")[0])
        for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            if os.path.isfile(p):
                v = open(p).read().strip()
                if v:
                    return v
    except Exception:
        return None
    return None


def machine_id() -> str:
    """Stable across reinstalls, since the server allows one trial per machine id: base64url of
    sha256("spriteguru:" + OS machine id), cached in the library state; a random id when the OS id
    cannot be read. Hashing keeps the OS id private. The prefix keeps its SpriteGuru name: changing it
    would give every machine a new id, a duplicate device and a second trial (N3)."""
    cached = library.get_state("machine_id")
    if isinstance(cached, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", cached):
        return cached
    raw = _os_machine_id()
    if raw:
        mid = base64.urlsafe_b64encode(hashlib.sha256(f"spriteguru:{raw}".encode()).digest()).decode().rstrip("=")
    else:
        mid = secrets.token_urlsafe(18)  # 24 characters
    library.set_state(machine_id=mid)
    return mid


def default_device_name() -> str:
    try:
        if sys.platform == "darwin":
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True,
                                  timeout=5).stdout.strip()
            if name:
                return name
        if os.name == "nt" and os.environ.get("COMPUTERNAME"):
            return os.environ["COMPUTERNAME"]
    except Exception:
        pass
    return socket.gethostname().removesuffix(".local") or "SpritePlay machine"


def device_name() -> str:
    """This machine's name on the Machines page: the one it was renamed to, else the OS name."""
    name = library.get_state("device_name")
    return name if isinstance(name, str) and name.strip() else default_device_name()


def platform_name() -> str:
    return "macos" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"


def release_platform() -> str:
    """The download channel for this build (plan 9)."""
    if sys.platform == "darwin":
        return "macos-arm64" if _platform.machine().lower() in ("arm64", "aarch64") else "macos-x64"
    return "windows-x64" if os.name == "nt" else "linux-x64"
