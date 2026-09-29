from __future__ import annotations

import subprocess
import sys

from fastapi.testclient import TestClient

from conftest import ROOT
from spriteguru.api import create_app


def test_retired_cloud_routes_are_normal_404s():
    client = TestClient(create_app(None, "test-token", mode="synthetic"), base_url="http://127.0.0.1")
    for method, path in (
        ("get", "/api/cloud/status"),
        ("post", "/api/cloud/sign-in"),
        ("get", "/api/cloud/projects"),
        ("get", "/api/project/sync"),
        ("post", "/api/project/sync/now"),
        ("get", "/auth/callback?state=x&code=y"),
    ):
        response = getattr(client, method)(path, headers={"x-spriteguru-token": "test-token"})
        assert response.status_code == 404, (method, path, response.status_code)


def test_cli_has_no_cloud_sync_remote_library_or_access_gate():
    result = subprocess.run(
        [sys.executable, "-m", "spriteguru.cli", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    from spriteguru import cli

    assert result.returncode == 0
    assert "cloud" not in result.stdout.lower()
    assert "sync" not in result.stdout.lower()
    assert "library" not in result.stdout.lower()
    assert not hasattr(cli, "_require_access")
