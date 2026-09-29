from __future__ import annotations

import os
import subprocess
import sys

from conftest import ROOT


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, env={**os.environ, **(env or {})}, capture_output=True, text=True)


def test_spriteguru_is_the_only_python_and_cli_identity():
    imported = _run(sys.executable, "-c", "import spriteguru; print(spriteguru.__version__)")
    help_out = _run(sys.executable, "-m", "spriteguru.cli", "--help")
    legacy = _run(sys.executable, "-c", "import spritekit")

    assert imported.returncode == 0, imported.stderr
    assert help_out.returncode == 0, help_out.stderr
    assert "SpriteGuru" in help_out.stdout
    assert "SpritePlay" not in help_out.stdout
    assert legacy.returncode != 0


def test_spriteguru_environment_name_wins_and_spritekit_is_a_fallback():
    code = "from spriteguru.env import get; print(get('PROJECT', 'missing'))"
    canonical = _run(sys.executable, "-c", code, env={"SPRITEGURU_PROJECT": "new", "SPRITEKIT_PROJECT": "old"})
    legacy = _run(sys.executable, "-c", code, env={"SPRITEGURU_PROJECT": "", "SPRITEKIT_PROJECT": "old"})

    assert canonical.stdout.strip() == "new"
    assert legacy.stdout.strip() == "old"


def test_closing_the_studio_stops_the_engine_promptly_and_quietly(tmp_path):
    """What a new contributor sees when they close the window: the launcher stops the engine while
    a studio socket is open (and another has already gone away). It must exit within its grace
    period, without an ASGI traceback."""
    import json
    import signal
    import time

    from websockets.sync.client import connect

    env = {**os.environ, "SPRITEGURU_LIBRARY": str(tmp_path / "library"),
           "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring"}
    proc = subprocess.Popen([sys.executable, "-m", "spriteguru.cli", "serve", "--port", "0", "--token", "t0k",
                             "--mode", "synthetic"], cwd=tmp_path, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        port = json.loads(proc.stdout.readline())["port"]
        url = f"ws://127.0.0.1:{port}/api/events?token=t0k"
        with connect(url):
            pass  # a window that was closed
        studio = connect(url)  # the window being closed now
        time.sleep(1.5)
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        took = time.monotonic() - started
        studio.close()
    finally:
        if proc.poll() is None:
            proc.kill()
    stderr = proc.stderr.read()

    assert took < 2.0, (took, stderr[-2000:])
    assert "Traceback" not in stderr and "Exception in ASGI application" not in stderr, stderr[-2000:]
