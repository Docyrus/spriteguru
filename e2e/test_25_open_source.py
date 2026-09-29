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
