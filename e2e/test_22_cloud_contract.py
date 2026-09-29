"""The cloud contract against a real spriteguru-web dev server (docs/cloud-integration-plan.md section
12, "contract check"), so the fake cloud can't drift from the server it imitates. It runs scenarios
1–5, 10 and the trial part of 13 with a real browser session approving on the real consent endpoint.

    e2e/support/contract_server.sh            # an isolated `wrangler dev` with its own database
    uv run pytest e2e -k contract --cloud-url http://localhost:5189 --cloud-log <the log it prints>

With `--cloud-log`, each run signs up a fresh account and verifies it from the email the dev server
prints; otherwise SPRITEPLAY_CONTRACT_EMAIL and SPRITEPLAY_CONTRACT_PASSWORD name a verified one.
Every run uses a fresh machine id, so each gets its own 7-day trial. Never run against production.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from cloudhelp import Engine, browser_sign_in, cli, cli_login, machine_env
from conftest import free_port
from test_18_cloud_sync import _edit_json, _export, _make_project, _tree

pytestmark = pytest.mark.contract


class Web:
    """The website in a browser: an account with a session, approving the desktop's sign-in."""

    def __init__(self, base: str, log: str | None):
        if not base.startswith(("http://localhost", "http://127.0.0.1")):
            raise pytest.UsageError("the contract run only talks to a local dev server")
        self.base = base
        self.http = httpx.Client(base_url=base, timeout=60, headers={"Origin": base})  # as the site's own pages send
        email = os.environ.get("SPRITEPLAY_CONTRACT_EMAIL")
        password = os.environ.get("SPRITEPLAY_CONTRACT_PASSWORD")
        if not (email and password):
            if not log:
                pytest.skip("needs --cloud-log (fresh accounts) or SPRITEPLAY_CONTRACT_EMAIL/_PASSWORD")
            email, password = f"contract-{secrets.token_hex(5)}@example.com", "Contract-" + secrets.token_urlsafe(12)
            r = self.http.post("/api/auth/sign-up/email", json={"email": email, "password": password, "name": "Contract Run"})
            assert r.status_code == 200, r.text[:300]
            link = None
            for _ in range(50):
                hits = re.findall(r"(\S+/api/auth/verify-email\?token=\S+)", Path(log).read_text())
                link = next((h for h in reversed(hits) if email.split("@")[0] in _token_email(h)), None)
                if link:
                    break
                time.sleep(0.2)
            assert link, "the dev server printed no verification email"
            self.http.get(link, follow_redirects=True)
        r = self.http.post("/api/auth/sign-in/email", json={"email": email, "password": password})
        assert r.status_code == 200, r.text[:300]
        self.email = email

    def approve(self, authorize_url: str) -> httpx.Response:
        """The consent page's work: the site's /authorize, then allow, then the app's loopback callback."""
        r = self.http.get(authorize_url, follow_redirects=False)
        assert r.status_code == 302, r.text[:300]
        params = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
        a = self.http.post("/api/oauth/approve", json={**params, "decision": "allow"})
        assert a.status_code == 200, a.text[:300]
        return httpx.get(a.json()["redirect"], follow_redirects=True, timeout=60)

    def create_team(self, name: str) -> dict:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + "-" + secrets.token_hex(3)
        r = self.http.post("/api/auth/organization/create", json={"name": name, "slug": slug})
        assert r.status_code == 200, r.text[:300]
        return r.json()


def _token_email(link: str) -> str:
    """The email inside a verification link's JWT payload (to pick this run's link from the log)."""
    import base64

    try:
        tok = parse_qs(urlsplit(link).query)["token"][0].split(".")[1]
        return json.loads(base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4))).get("email", "")
    except Exception:
        return ""


def test_cloud_contract(rec, work, request):
    S = "cloud-contract"
    base = request.config.getoption("--cloud-url").rstrip("/")
    web = Web(base, request.config.getoption("--cloud-log"))
    run = secrets.token_hex(4)
    lib_a, lib_b = work / S / "machine-a", work / S / "machine-b"
    env_a = machine_env(base, lib_a, machine_id=f"contract-a-{run}")
    env_b = machine_env(base, lib_b, machine_id=f"contract-b-{run}")
    eng = Engine(free_port(), env_a, work / S / "engine-a.log")
    try:
        with eng.client() as c:
            # scenario 1 and the trial part of 13: sign-in through the real consent, a fresh trial
            r = browser_sign_in(c, web.approve)
            st = c.get("/api/cloud/status", params={"refresh": "true"}).json()
            a = st["access"]
            rec.check(S, "the real server signs the machine in and starts a 7-day trial with cloud sync",
                      "result=signed-in" in str(r.url) and st["signed_in"] and a["status"] == "trial" and a["allowed"]
                      and a["trial_days_left"] == 7 and a["cloud"] and st["user"]["email"] == web.email
                      and st["personal"]["limits"]["cloudProjects"] == 3, [a["status"], a["trial_days_left"]], ["C1"], stable=False)

            # scenario 2: link and first push
            proj = _make_project(c, "Contract Quest")
            r = c.post("/api/project/sync/link", json={"owner": "me"})
            pid = r.json()["project"]["id"]
            again = c.post("/api/project/sync/now").json()
            rec.check(S, "linking creates the project on the server; the first push commits, the second is a no-op",
                      r.status_code == 200 and r.json()["sync"]["pending"]["push"] == 0 and again["pushed"] == []
                      and again["hashed"] == 0, r.status_code, ["C21"], stable=False)

            # scenario 3: machine B downloads an identical copy
            code, st_b, _ = cli_login(env_b, browser=web.approve)
            code, got, _ = cli(env_b, "cloud", "download", pid)
            b_proj = Path(got["path"])
            rec.check(S, "machine B signs in from the CLI and downloads an identical copy",
                      st_b["signed_in"] and _tree(b_proj) == _tree(proj), len(_tree(b_proj)), ["C3"], stable=False)

            # scenario 4: an export travels A to B; a deletion travels B to A
            anim = _export(c, "idle")
            c.post("/api/project/sync/now")
            code, res_b, _ = cli(env_b, "sync", "--project", str(b_proj))
            gone = f"animations/{anim}/final/preview.gif"
            (b_proj / gone).unlink()
            cli(env_b, "sync", "--project", str(b_proj))
            res_a = c.post("/api/project/sync/now").json()
            rec.check(S, "an export reaches B and a deletion on B reaches A",
                      code == 0 and any(p.startswith(f"animations/{anim}/") for p in res_b["pulled"])
                      and gone in res_a["pulled"] and _tree(b_proj) == _tree(proj), gone, [], stable=False)

            # scenario 5: a conflict, kept from A
            cj = "characters/knight/character.json"
            _edit_json(b_proj / cj, description="Contract change on B.")
            cli(env_b, "sync", "--project", str(b_proj))
            _edit_json(proj / cj, description="Contract change on A.")
            res_a = c.post("/api/project/sync/now").json()
            c.post("/api/project/sync/resolve", json={"choices": [{"group": "characters/knight/", "keep": "mine"}]})
            code, res_b, _ = cli(env_b, "sync", "--project", str(b_proj))
            rec.check(S, "both machines change character.json: one conflict; keeping A's reaches B",
                      res_a["conflicts"] == 1 and json.loads((b_proj / cj).read_text())["description"] == "Contract change on A.",
                      res_a["conflicts"], ["C8"], stable=False)

            # scenario 10: the library, through a team (the dev bypass makes teams active)
            team = web.create_team("Contract Studio")
            st = c.get("/api/cloud/status", params={"refresh": "true"}).json()
            r = c.post("/api/project/library/publish", json={"prefix": f"animations/{anim}/final/", "name": "Contract idle",
                                                               "kind": "animation"})
            item = r.json().get("item", {})
            code, imp, _ = cli(env_b, "library", "import", item.get("id", "none"), "--project", str(b_proj))
            rec.check(S, "a team seat unlocks the library: a synced animation publishes server-side and imports on B",
                      any(t["id"] == team["id"] and t["active"] for t in st["teams"]) and r.status_code == 200
                      and r.json()["from_project"] and code == 0
                      and (b_proj / imp["path"] / "animation.json").is_file(), r.status_code, [], stable=False)

            # sign-out revokes: the machine's next call is refused by the server
            c.post("/api/cloud/sign-out")
            st = c.get("/api/cloud/status", params={"refresh": "true"}).json()
            rec.check(S, "sign-out revokes on the server and leaves this machine signed out", not st["signed_in"],
                      st["signed_in"], [], stable=False)
    finally:
        eng.stop()
