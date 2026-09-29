"""The vector renderer's browser (docs/failure-modes.md BR1–BR7): the app bundles no Chromium, so a
clean machine downloads Playwright's headless browser once, concurrent jobs share one download, an
offline download fails with a plain reason, a damaged install is replaced, and an installed Google
Chrome is used without downloading anything.

Real vector jobs run from the CLI (synthetic mode) with an empty browsers folder. The download comes
from a local mirror of Playwright's CDN (PLAYWRIGHT_DOWNLOAD_HOST) serving a zip of the headless
browser already installed on this machine, so the run stays offline and repeatable."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import ROOT, sk

OFFLINE = "Vector sprites need a one-time download of a small browser"


def _pw_browser() -> dict:
    import playwright

    data = json.loads((Path(playwright.__file__).parent / "driver" / "package" / "browsers.json").read_text())
    return next(b for b in data["browsers"] if b["name"] == "chromium-headless-shell")


def _installed_shell() -> Path | None:
    arch = "mac-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "mac-x64"
    b = _pw_browser()
    d = Path.home() / "Library" / "Caches" / "ms-playwright" / f"chromium_headless_shell-{b['revision']}" / \
        f"chrome-headless-shell-{arch}"
    return d if d.is_dir() else None


class Mirror:
    """Playwright's CDN, locally: the headless browser (builds/cft/<version>/<arch>/...) and the ffmpeg
    helper its installer fetches alongside (builds/ffmpeg/<revision>/...). `hits` counts browser downloads."""

    def __init__(self, zip_path: Path, url_path: str):
        self.hits = 0
        mirror = self
        extra = _ffmpeg_zip()

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path == url_path:
                    mirror.hits += 1
                    data = zip_path.read_bytes()
                elif extra and self.path == extra[1]:
                    data = extra[0].read_bytes()
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _ffmpeg_zip() -> tuple[Path, str] | None:
    import playwright

    data = json.loads((Path(playwright.__file__).parent / "driver" / "package" / "browsers.json").read_text())
    rev = next(b["revision"] for b in data["browsers"] if b["name"] == "ffmpeg")
    src = Path.home() / "Library" / "Caches" / "ms-playwright" / f"ffmpeg-{rev}" / "ffmpeg-mac"
    if not src.is_file():
        return None
    arch = "mac-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "mac"
    zp = ROOT / "e2e" / ".mirror" / f"ffmpeg-{rev}-{arch}.zip"
    if not zp.is_file():
        zp.parent.mkdir(exist_ok=True)
        subprocess.run(["ditto", "-c", "-k", str(src), str(zp)], check=True)
    return zp, f"/builds/ffmpeg/{rev}/ffmpeg-{arch}.zip"


def _mirror_zip(shell: Path) -> tuple[Path, str]:
    b = _pw_browser()
    arch = shell.name.removeprefix("chrome-headless-shell-")
    cache = ROOT / "e2e" / ".mirror"
    cache.mkdir(exist_ok=True)
    zp = cache / f"{b['browserVersion']}-{shell.name}.zip"
    if not zp.is_file():  # ditto keeps the executable bits and symlinks the installer expects
        tmp = zp.with_suffix(".part")
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(shell), str(tmp)], check=True)
        tmp.rename(zp)
    return zp, f"/builds/cft/{b['browserVersion']}/{arch}/{shell.name}.zip"


def _project(work: Path, name: str, env: dict) -> Path:
    proj = work / f"{name}.sprites"
    sk("init", str(proj), "--style", "vector", "--engine", "godot", "--mode", "synthetic", env=env)
    return proj


def _vector_job(proj: Path, env: dict, check: bool = True) -> dict:
    """A vector subject and its idle loop: the synthetic Quiver route renders through Chromium."""
    r = sk("character", "new", "knight", "--describe", "A knight in blue armour with a round shield.", "--approve",
           "--mode", "synthetic", "--project", str(proj), env=env, check=False)
    if r["code"] != 0:
        return r
    return sk("gen", "knight", "idle", "--mode", "synthetic", "--project", str(proj), env=env, check=False)


def test_vector_browser(rec, work):
    S = "vector-browser"
    if sys.platform != "darwin":
        pytest.skip("the local CDN mirror is built from the macOS headless browser")
    shell = _installed_shell()
    if shell is None:
        pytest.skip("Playwright's headless browser isn't installed here to mirror")
    zp, url_path = _mirror_zip(shell)
    base = work / S
    base.mkdir(parents=True, exist_ok=True)
    rev = _pw_browser()["revision"]

    def env_for(browsers: Path, host: str, mode: str) -> dict:
        return {"PLAYWRIGHT_BROWSERS_PATH": str(browsers), "PLAYWRIGHT_DOWNLOAD_HOST": host,
                "SPRITEGURU_VECTOR_BROWSER": mode, "SPRITEGURU_SYNTH_DELAY": "0"}

    def marker(browsers: Path) -> Path:
        return browsers / f"chromium_headless_shell-{rev}" / "INSTALLATION_COMPLETE"

    mirror = Mirror(zp, url_path)
    try:
        # BR1: a clean machine downloads the headless browser once, then renders
        b1 = base / "browsers-clean"
        env = env_for(b1, mirror.url, "managed")
        r = _vector_job(_project(base, "clean", env), env)
        rec.check(S, "on a clean machine the first vector job downloads the renderer once and renders",
                  r["code"] == 0 and r["json"]["state"] == "done" and marker(b1).is_file() and mirror.hits == 1,
                  [r["code"], mirror.hits], ["BR1"])
        r2 = _vector_job(_project(base, "clean-again", env), env)
        rec.check(S, "later jobs reuse the downloaded renderer", r2["code"] == 0 and mirror.hits == 1, mirror.hits,
                  ["BR1"])

        # BR4: an install whose files are damaged is replaced (the same folder, to keep disk use low)
        binary = next(b1.rglob("chrome-headless-shell"))
        binary.write_bytes(b"not a browser")
        hits = mirror.hits
        r = _vector_job(_project(base, "damaged", env), env)
        rec.check(S, "a damaged install is downloaded again and the job renders",
                  r["code"] == 0 and mirror.hits - hits == 1 and binary.stat().st_size > 1000, mirror.hits - hits, ["BR4"])
        shutil.rmtree(b1)

        # BR3: two jobs start together on a clean machine: one download
        hits = mirror.hits
        b2 = base / "browsers-race"
        env = env_for(b2, mirror.url, "managed")
        projs = [_project(base, f"race-{i}", env) for i in range(2)]
        with ThreadPoolExecutor(2) as ex:
            results = list(ex.map(lambda p: _vector_job(p, env), projs))
        rec.check(S, "two vector jobs starting together on a clean machine share one download",
                  all(x["code"] == 0 for x in results) and mirror.hits - hits == 1 and marker(b2).is_file(),
                  [[x["code"] for x in results], mirror.hits - hits], ["BR3"])
        shutil.rmtree(b2)
    finally:
        mirror.stop()

    # BR2: offline (nothing answers at the download host): a plain reason, and nothing half-installed
    b3 = base / "browsers-offline"
    env = env_for(b3, "http://127.0.0.1:9", "managed")
    t0 = time.time()
    r = _vector_job(_project(base, "offline", env), env)
    text = r["stdout"] + r["stderr"]
    rec.check(S, "offline, the vector job fails with a plain reason and leaves no install behind",
              r["code"] != 0 and OFFLINE in text and not marker(b3).is_file() and time.time() - t0 < 300,
              [r["code"], OFFLINE in text], ["BR2"])

    # BR5/BR6: with Google Chrome installed, auto uses it and downloads nothing
    chrome = Path("/Applications/Google Chrome.app")
    if chrome.is_dir():
        b5 = base / "browsers-chrome"
        env = env_for(b5, "http://127.0.0.1:9", "auto")
        r = _vector_job(_project(base, "chrome", env), env)
        rec.check(S, "with no renderer downloaded, the installed Google Chrome renders; nothing is downloaded",
                  r["code"] == 0 and not marker(b5).is_file(), r["code"], ["BR5", "BR6"])
