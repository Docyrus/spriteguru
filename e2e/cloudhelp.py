"""Shared plumbing for the cloud scenarios: engines pointed at the fake cloud with no provider keys
(a live-mode job can then never spend), browser sign-in through the real loopback callback, and the
CLI with the same machine's environment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from conftest import ROOT

KEY_ENV = ("OPENAI_API_KEY", "FAL_KEY", "RD_API_KEY", "QUIVERAI_API_KEY", "QUIVER_API_KEY", "AI_PROVIDER_KEY_OPENAI",
           "AI_PROVIDER_KEY_FAL", "AI_PROVIDER_KEY_QUIVER", "AI_PROVIDER_KEY_RETRODIFFUSION", "AI_PROVIDER_KEY_RD",
           "AI_PROVIDER_KEY_GOOGLE")


def machine_env(cloud_url: str, library: Path, machine_id: str | None = None, **extra) -> dict:
    """One simulated machine: its own library root (so its own machine id and keychain file), the
    fake cloud, and no provider keys at all (no .env either), so nothing can reach a real provider."""
    library.mkdir(parents=True, exist_ok=True)
    state = library / ".spriteplay-library.json"
    if not state.exists():
        # every simulated machine on this one computer needs its own machine id (the OS id is shared)
        mid = machine_id or "test-" + "".join(ch if ch.isalnum() else "-" for ch in f"{library.parent.name}-{library.name}")[-60:]
        state.write_text(json.dumps({"machine_id": mid}))
    env = {k: v for k, v in os.environ.items() if k not in KEY_ENV}
    env.update({"SPRITEPLAY_CLOUD_URL": cloud_url, "SPRITEGURU_LIBRARY": str(library),
                "SPRITEGURU_ENV_FILE": str(library / "no-such.env"), "SPRITEGURU_SYNTH_DELAY": "0", **extra})
    return env


class Engine:
    """`spriteguru serve` for one machine, with its output kept for the token-leak checks."""

    def __init__(self, port: int, env: dict, log: Path, mode: str = "synthetic", project: Path | None = None):
        self.port, self.env, self.log = port, env, log
        log.parent.mkdir(parents=True, exist_ok=True)
        self._out = open(log, "a")
        args = [sys.executable, "-m", "spriteguru.cli", "serve", "--port", str(port), "--token", "t0k", "--mode", mode]
        if project:
            args += ["--project", str(project)]
        self.proc = subprocess.Popen(args, env=env, cwd=ROOT, stdout=self._out, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                if httpx.get(self.url("/api/library"), headers={"X-SpriteKit-Token": "t0k"}).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"engine did not start: {log.read_text()[-2000:]}")

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.url(), headers={"X-SpriteKit-Token": "t0k"}, timeout=120)

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)
        self._out.close()


def follow(url: str) -> httpx.Response:
    """The fake's browser: its authorize page approves at once, so following redirects is enough."""
    return httpx.get(url, follow_redirects=True, timeout=60)


def browser_sign_in(c: httpx.Client, browser=follow) -> httpx.Response:
    """What the studio does: start a sign-in, then let a browser follow the authorize URL through the
    approval, the engine's loopback callback and the website's result page."""
    url = c.post("/api/cloud/sign-in").json()["authorize_url"]
    return browser(url)


def cli(env: dict, *args: str, timeout: int = 120) -> tuple[int, dict | None, str]:
    p = subprocess.run([sys.executable, "-m", "spriteguru.cli", *args], env=env, cwd=ROOT, capture_output=True,
                       text=True, timeout=timeout)
    data = None
    for line in reversed(p.stdout.strip().splitlines()):
        try:
            data = json.loads(line)
            break
        except ValueError:
            continue
    return p.returncode, data, p.stdout + p.stderr


def cli_login(env: dict, timeout: int = 60, browser=follow) -> tuple[int, dict | None, str]:
    """`spriteguru cloud login --no-browser`, with a browser following the printed link (C3)."""
    p = subprocess.Popen([sys.executable, "-m", "spriteguru.cli", "cloud", "login", "--no-browser"], env=env, cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first = p.stdout.readline()
    url = json.loads(first)["authorize_url"]
    browser(url)
    out, err = p.communicate(timeout=timeout)
    data = None
    for line in reversed(out.strip().splitlines()):
        try:
            data = json.loads(line)
            break
        except ValueError:
            continue
    return p.returncode, data, first + out + err


def leaks(tokens: list[str], texts: list[str]) -> int:
    """How many issued tokens appear in any of the texts (must be 0: P7, plan 10)."""
    return sum(1 for t in tokens if any(t in x for x in texts))


def files_text(root: Path, skip: tuple[str, ...] = (".test-keyring.json",)) -> list[str]:
    out = []
    for p in root.rglob("*"):
        if p.is_file() and p.name not in skip and p.stat().st_size < 5_000_000:
            try:
                out.append(p.read_text(errors="ignore"))
            except OSError:
                pass
    return out
