"""The browser that renders vector sprites (docs/failure-modes.md BR1–BR7).

The app doesn't bundle Chromium (it would add ~195 MB). In order:
1. Playwright's own headless browser, when it's installed (development machines, tests, and any
   earlier download), so rendering doesn't depend on the installed Chrome's version (BR6);
2. the installed Google Chrome (BR5: one that won't start is skipped);
3. otherwise Playwright's headless browser is downloaded once, under a lock so concurrent jobs make
   one download (BR1, BR3), and only an install with Playwright's completion marker counts (BR4).

`SPRITEGURU_VECTOR_BROWSER` picks one source: `auto` (the order above), `managed` (Playwright's own,
downloaded when missing) or `chrome`.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from ..env import get as app_env

INSTALL_TIMEOUT = 600
MODES = ("auto", "managed", "chrome")
OFFLINE_MESSAGE = ("Vector sprites need a one-time download of a small browser (about 100 MB), and it couldn't be "
                   "downloaded. Connect to the internet and try again.")


class RendererUnavailable(RuntimeError):
    pass


def _mode() -> str:
    m = (app_env("VECTOR_BROWSER", "auto") or "auto").strip().lower()
    return m if m in MODES else "auto"


def browsers_dir() -> Path:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ms-playwright"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ms-playwright"


def _revision() -> str | None:
    import playwright

    try:
        data = json.loads((Path(playwright.__file__).parent / "driver" / "package" / "browsers.json").read_text())
        return next(b["revision"] for b in data["browsers"] if b["name"] == "chromium-headless-shell")
    except (OSError, ValueError, StopIteration, KeyError):
        return None


def installed() -> bool:
    """Playwright's headless browser is fully installed (its completion marker exists, BR4)."""
    rev = _revision()
    return bool(rev) and (browsers_dir() / f"chromium_headless_shell-{rev}" / "INSTALLATION_COMPLETE").is_file()


def install(force: bool = False) -> None:
    """Download Playwright's headless browser once; other processes wait on the lock, then reuse it."""
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    from ..filelock import FileLock

    with FileLock(browsers_dir() / ".spriteplay-install.lock"):
        if installed() and not force:  # another job or window just installed it (BR3)
            return
        print("spriteguru: downloading the vector renderer (one time, about 100 MB)", file=sys.stderr, flush=True)
        node, cli = compute_driver_executable()
        args = [node, cli, "install", "chromium-headless-shell", *(["--force"] if force else [])]
        try:
            p = subprocess.run(args, env=get_driver_env(), capture_output=True, text=True, timeout=INSTALL_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RendererUnavailable(OFFLINE_MESSAGE) from e
        tail = (p.stdout + p.stderr).strip().splitlines()[-5:]
        for line in tail:  # the installer's own account goes to the engine log (BR7)
            print(f"spriteguru: installer: {line}", file=sys.stderr, flush=True)
        # the installer also fetches helpers rendering doesn't use (ffmpeg, for video recording); the
        # headless browser's completion marker decides, not the exit code
        if not installed():
            raise RendererUnavailable(OFFLINE_MESSAGE)


async def launch(pw):
    """A headless browser for rendering, from the first source that works."""
    from playwright.async_api import Error as PlaywrightError

    mode = _mode()
    if mode in ("auto", "managed") and installed():
        try:
            return await pw.chromium.launch()
        except PlaywrightError:
            pass  # damaged: reinstalled below (BR4)
    if mode in ("auto", "chrome"):
        try:
            return await pw.chromium.launch(channel="chrome")
        except PlaywrightError:
            if mode == "chrome":
                raise RendererUnavailable("Google Chrome couldn't be started for vector rendering.")
    await asyncio.to_thread(install, installed())  # a damaged install is replaced (--force)
    try:
        return await pw.chromium.launch()
    except PlaywrightError as e:
        raise RendererUnavailable(f"The vector renderer couldn't start: {str(e).splitlines()[0]}") from e
