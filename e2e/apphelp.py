"""Local engine process helper shared by browser and API scenarios."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from conftest import ROOT


class Engine:
    def __init__(self, port: int, library: Path, log: Path, *, mode: str = "synthetic",
                 project: Path | None = None, extra_env: dict[str, str] | None = None):
        self.port = port
        library.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "SPRITEGURU_LIBRARY": str(library),
               "SPRITEGURU_ENV_FILE": str(library / "no-such.env"),
               "SPRITEGURU_SYNTH_DELAY": "0", **(extra_env or {})}
        log.parent.mkdir(parents=True, exist_ok=True)
        self._out = open(log, "a")
        args = [sys.executable, "-m", "spriteguru.cli", "serve", "--port", str(port),
                "--token", "t0k", "--mode", mode]
        if project:
            args += ["--project", str(project)]
        self.proc = subprocess.Popen(args, env=env, cwd=ROOT, stdout=self._out,
                                     stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                if self.client().get("/api/library").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"engine did not start: {log.read_text()[-2000:]}")

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url(), headers={"X-SpriteGuru-Token": "t0k"}, timeout=120)

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)
        self._out.close()
