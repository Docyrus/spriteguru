"""Native window (D8): start the engine as its own process, then show the studio in pywebview.

Rules from the plan: the engine is a standalone server (`spriteguru serve --port 0 --token …`) that
prints its port and token as JSON and never imports webview; the studio talks to it only over
HTTP and WebSocket; native file dialogs, reveal-in-folder and external links are the only things
exposed to the page, through `window.pywebview.api` behind the studio's host adapter.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
from pathlib import Path


def engine_command(project: Path | None, token: str, mode: str | None) -> list[str]:
    """The engine binary next to a frozen launcher, else the Python module. Without a project the
    engine starts on the gallery (P1)."""
    args = ["serve", "--port", "0", "--token", token, "--exit-with-parent"] + (["--project", str(project)] if project else [])
    if mode:
        args += ["--mode", mode]
    if getattr(sys, "frozen", False):
        name = "spriteguru-engine" + (".exe" if os.name == "nt" else "")
        here = Path(sys.executable).parent
        # macOS bundle: Contents/Resources/engine (RT5); elsewhere an engine/ folder next to the launcher
        places = (here.parent / "Resources" / "engine" / name, here / "engine" / name, here / name)
        exe = next((p for p in places if p.is_file()), places[0] if sys.platform == "darwin" else places[1])
        return [str(exe), *args]
    return [sys.executable, "-m", "spriteguru.cli", *args]


def start_engine(project: Path | None, mode: str | None = None) -> tuple[subprocess.Popen, dict]:
    token = secrets.token_urlsafe(24)
    # RT4: the engine watches this pipe and stops when it closes, so quitting the app (which skips the
    # `finally` in launch) or a crash never leaves it running
    proc = subprocess.Popen(engine_command(project, token, mode), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=None, text=True)
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError(f"engine exited before announcing its port (code {proc.poll()})")
        line = line.strip()
        if line.startswith("{"):
            info = json.loads(line)
            if info.get("token") == token:
                return proc, info


def app_icon() -> Path | None:
    """The brand icon for the window and the Dock when the launcher runs from source (B6): the macOS
    app icon on macOS, the mark elsewhere. The repo's brand/ kit, or the copy a frozen build carries;
    none found means no custom icon, never an error (B7)."""
    name = "app-icon-macos-512.png" if sys.platform == "darwin" else "mark-256.png"
    roots = [Path(getattr(sys, "_MEIPASS", "")) / "brand"] if getattr(sys, "frozen", False) else []
    roots.append(Path(__file__).resolve().parents[2] / "brand")
    return next((r / "png" / name for r in roots if (r / "png" / name).is_file()), None)


class HostApi:
    """What the page may ask of the host: native dialogs and the OS shell, nothing else."""

    def __init__(self):
        self.window = None

    def open_folder_dialog(self, start: str | None = None):
        import webview

        # pywebview 5+ names the dialog kinds FileDialog.*; FOLDER_DIALOG is deprecated there
        kind = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
        res = self.window.create_file_dialog(kind, directory=start or str(Path.home()))
        return res[0] if res else None

    def reveal(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(p)])
        elif os.name == "nt":
            subprocess.run(["explorer", "/select,", str(p)])
        else:
            subprocess.run(["xdg-open", str(p if p.is_dir() else p.parent)])
        return True

    def open_external(self, url: str) -> bool:
        if not url.startswith(("https://", "http://")):
            return False
        import webbrowser

        webbrowser.open(url)
        return True


def launch(project: Path | None, mode: str | None = None) -> None:
    import webview

    proc, info = start_engine(project, mode)
    api = HostApi()
    try:
        window = webview.create_window("SpriteGuru", info["url"], js_api=api, width=1440, height=900,
                                       min_size=(960, 640))
        api.window = window
        icon = app_icon()
        webview.start(icon=str(icon) if icon else None)
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    """Double-click entry point: the studio opens on the project gallery, where projects are created
    and opened (P1); a project given on the command line opens directly."""
    import argparse

    ap = argparse.ArgumentParser(prog="SpriteGuru")
    ap.add_argument("project", nargs="?")
    ap.add_argument("--mode", default=None)
    a = ap.parse_args()
    project = Path(a.project) if a.project and (Path(a.project) / "project.json").is_file() else None
    launch(project, a.mode)


if __name__ == "__main__":
    main()
