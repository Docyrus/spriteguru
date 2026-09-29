"""Cloud membership, phase 1 (docs/cloud-integration-plan.md scenarios 1 and 13): browser sign-in
through the engine's real loopback callback, the account chip and access banner in the studio, the
access gate on everything that calls a provider, the offline grace, and the token rules (C1–C6,
C26, C31–C38). Every engine talks to the fake cloud and has no provider keys, so a live-mode job can
never reach a real provider or spend."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from cloudhelp import Engine, browser_sign_in, cli, cli_login, files_text, leaks, machine_env
from conftest import ROOT, free_port
from fakecloud import FakeCloud


def _state_file(lib: Path) -> Path:
    return lib / ".spriteplay-library.json"


def _shift_access(lib: Path, *, local: float = 0.0, server: float = 0.0) -> None:
    """Move the cached access check in time, as if days passed (or the clock went back)."""
    p = _state_file(lib)
    d = json.loads(p.read_text())
    d["cloud_access"]["checked_local"] += local
    d["cloud_access"]["checked_server"] += server
    p.write_text(json.dumps(d))


def _keyring(lib: Path) -> dict:
    p = lib / ".test-keyring.json"
    return json.loads(p.read_text()).get("spriteplay-cloud", {}) if p.is_file() else {}


def _tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".spriteplay" not in p.parts:
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _prepare_project(c: httpx.Client) -> tuple[Path, str]:
    """A project with an approved knight and a planned walk, made in synthetic mode (which needs no
    access), then switched to live mode with a zero session cap: a live job can only wait for approval."""
    proj = Path(c.post("/api/library/projects", json={"name": "Quest", "style": {"kind": "hd-cartoon"},
                                                      "engine": "phaser"}).json()["project"]["path"])
    c.post("/api/characters", json={"name": "knight", "description": "An armoured knight with a blue tabard."})
    for _ in range(300):
        if c.get("/api/characters/knight").json().get("views"):
            break
        time.sleep(0.1)
    c.post("/api/characters/knight/approve")
    anim = c.post("/api/animations", json={"character": "knight", "action": "walk", "facing": "E"}).json()["id"]
    c.patch("/api/settings", json={"provider_mode": "live", "session_cap_usd": 0})
    return proj, anim


def _sent_calls(proj: Path) -> int:
    """Calls sent to a real provider (the synthetic turnaround made before the switch to live doesn't count)."""
    p = proj / "ledger.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []
    return sum(1 for r in rows if r.get("status") == "sent" and r.get("provider") != "synthetic")


def _wait_state(c: httpx.Client, job: str, states: tuple[str, ...], timeout: float = 60) -> str:
    st = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = c.get(f"/api/jobs/{job}").json()["state"]
        if st in states:
            return st
        time.sleep(0.2)
    return st


def test_cloud_account_studio(rec, work, run_dir):
    """Sign in from the studio's Sign in button: the browser opens the website, which approves and
    returns to the engine's callback; the chip and banners follow the access through a trial, its last
    days, its end, a purchase and a version lock, then sign-out (scenario 1, 13; C33, C35, C36)."""
    S = "cloud-studio"
    from playwright.sync_api import expect, sync_playwright

    fc = FakeCloud().start()
    fc.add_user("anil@example.com", "Anil Test")
    lib = work / S / "machine-a"
    eng = Engine(free_port(), machine_env(fc.url, lib), work / S / "engine.log", mode="live")
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)
    seen: list[str] = []

    def shot(page, name):
        p = shots / f"{name}.png"
        page.screenshot(path=str(p))
        rec.files.append({"scenario": S, "path": p.relative_to(run_dir).as_posix(), "sha256": None, "in_digest": False})
        seen.append(page.content())

    try:
        with eng.client() as c, sync_playwright() as pw:
            st = c.get("/api/cloud/status").json()
            seen.append(json.dumps(st))
            rec.check(S, "signed out: no access, and only the public update check reaches the cloud",
                      not st["signed_in"] and st["access"]["reason"] == "signed_out" and not st["access"]["allowed"]
                      and [e["kind"] for e in fc.log] == ["releases_latest"], [e["kind"] for e in fc.log], ["C38"])

            c.post("/api/library/projects", json={"name": "Quest", "style": {"kind": "hd-cartoon"}, "engine": "phaser"})
            r = c.post("/api/characters", json={"name": "knight", "description": "An armoured knight."})
            proj = Path(c.get("/api/project").json()["path"])
            rec.check(S, "signed out, a live-mode subject is refused with 402 access_required and nothing is made",
                      r.status_code == 402 and r.json()["error"]["code"] == "access_required"
                      and r.json()["error"]["status"] == "signed_out" and not (proj / "characters" / "knight").exists(),
                      [r.status_code, r.json().get("error", {}).get("status")])

            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{eng.url()}/?token=t0k#/projects")
            banner = page.get_by_test_id("access-banner")
            expect(banner).to_have_attribute("data-reason", "signed_out", timeout=15000)
            rec.check(S, "the gallery says why: sign in to start the 7-day trial, with a Sign in button",
                      "7-day trial" in banner.inner_text() and page.get_by_test_id("access-banner-sign-in").is_visible()
                      and page.get_by_test_id("account-sign-in").is_visible(), banner.inner_text(), [])
            shot(page, "01-signed-out")

            with page.expect_popup() as pop:
                page.get_by_test_id("account-sign-in").click()
            tab = pop.value
            tab.wait_for_url(re.compile(r"/authorize\?result=signed-in"), timeout=30000)
            chip = page.get_by_test_id("account-chip")
            expect(chip).to_have_attribute("data-status", "trial", timeout=15000)
            expect(banner).to_have_count(0, timeout=10000)
            label = page.get_by_test_id("account-access").inner_text()
            blocked = page.get_by_test_id("toast-error").count()
            st = c.get("/api/cloud/status").json()
            seen.append(json.dumps(st))
            auth = fc.events("authorize")[-1]
            rec.check(S, "the browser returns through the engine's callback to the website's result page; the chip shows "
                         "the trial", "result=signed-in" in tab.url and label == "Trial, 7 days" and st["signed_in"]
                      and blocked == 0, [label, blocked], ["C1"])
            rec.check(S, "the sign-in names this machine: its id, name, platform and app version",
                      auth["machine_id"] == st["machine"]["id"] and auth["device_name"] == st["machine"]["name"]
                      and auth["platform"] in ("macos", "windows", "linux") and auth["app_version"] == "0.1.0",
                      {"platform": auth["platform"], "version": auth["app_version"]}, [])
            tab.close()

            chip.click()
            menu = page.get_by_test_id("account-menu")
            expect(menu).to_be_visible()
            rec.check(S, "the account menu shows the name, email, plan with storage, and the website links",
                      page.get_by_test_id("account-email").inner_text() == "anil@example.com"
                      and page.get_by_test_id("account-storage").is_visible()
                      and page.get_by_test_id("account-manage").is_visible()
                      and page.get_by_test_id("account-machines").is_visible(),
                      page.get_by_test_id("account-plan").inner_text(), [])
            shot(page, "02-account-menu")
            page.keyboard.press("Escape")

            # the last two days of the trial show a reminder, not a lock
            user = next(iter(fc.users.values()))
            user.trial_ends = fc.now() + 1.5 * 86400
            c.post("/api/cloud/access/refresh")
            expect(banner).to_have_attribute("data-reason", "trial_ending", timeout=10000)
            rec.check(S, "in the trial's last two days a reminder offers the License and plans; creating still works",
                      "2 days left" in banner.inner_text() and page.get_by_test_id("access-banner-buy").is_visible()
                      and chip.get_attribute("data-allowed") == "true", banner.inner_text(), [])
            shot(page, "03-trial-ending")

            # C35: the trial ends while the studio is open, even offline
            user.trial_ends = fc.now() + 2
            c.post("/api/cloud/access/refresh")
            fc.stop()
            time.sleep(4)  # the server's clock is known to the whole second (the Date header)
            page.evaluate("window.dispatchEvent(new Event('focus'))")
            expect(banner).to_have_attribute("data-status", "trial_ended", timeout=10000)
            rec.check(S, "a trial that ends while the app is open locks it without a restart or a connection",
                      "Your trial has ended" in banner.inner_text() and chip.get_attribute("data-allowed") == "false"
                      and page.get_by_test_id("access-banner-buy").is_visible(), banner.inner_text(), ["C35"])
            shot(page, "04-trial-ended")

            # C36: bought in the browser; "check again" unlocks without signing in again
            fc.start()
            user.license_until = fc.now() + 365 * 86400
            grants = len(fc.events("authorize"))
            page.get_by_test_id("access-banner-check").click()
            expect(chip).to_have_attribute("data-status", "license", timeout=10000)
            expect(banner).to_have_count(0, timeout=10000)
            rec.check(S, "after buying the License in the browser, check again clears the lock without a new sign-in",
                      page.get_by_test_id("account-access").inner_text() == "License"
                      and len(fc.events("authorize")) == grants, page.get_by_test_id("account-access").inner_text(),
                      ["C36"])

            # C33: a License whose updates ended before this build was released
            fc.add_release("0.1.0", fc.now())
            user.license_until = fc.now() - 86400
            c.post("/api/cloud/access/refresh")
            expect(banner).to_have_attribute("data-reason", "version", timeout=10000)
            rec.check(S, "a build newer than the License's updates is locked with the end date and Renew updates",
                      "released after your updates ended on" in banner.inner_text()
                      and page.get_by_test_id("access-banner-renew").is_visible(), banner.inner_text(), ["C33"])
            shot(page, "05-version-lock")

            chip.click()
            page.get_by_test_id("account-sign-out").click()
            expect(page.get_by_test_id("account-sign-in")).to_be_visible(timeout=10000)
            rec.check(S, "sign-out revokes on the server and forgets the sign-in here",
                      len(fc.events("revoke")) == 1 and "refresh_token" not in _keyring(lib)
                      and not c.get("/api/cloud/status").json()["signed_in"], len(fc.events("revoke")), [])
            rec.check(S, "no page errors", not errors, errors[:3])
            browser.close()
            texts = seen + [eng.log.read_text()] + files_text(lib)
            rec.check(S, "no token appears in the engine's output, API answers, the studio or any file but the keychain",
                      leaks(fc.issued, texts) == 0 and len(fc.issued) >= 2, leaks(fc.issued, texts), ["P7"])
    finally:
        eng.stop()
        fc.stop()


def test_cloud_access_gate(rec, work):
    """The gate on everything that calls a provider, in live mode, from the API and the CLI: signed
    out, a running trial, a job running when access ends, the offline grace and a clock moved back
    (C31, C32, C34; scenario 13). Synthetic mode needs no access: every other scenario of the suite
    generates signed out."""
    S = "cloud-gate"
    fc = FakeCloud().start()
    user = fc.add_user("gate@example.com", "Gate Test")
    lib = work / S / "machine"
    env = machine_env(fc.url, lib)
    eng = Engine(free_port(), env, work / S / "engine.log", mode="synthetic")
    try:
        with eng.client() as c:
            proj, anim = _prepare_project(c)
            r = c.post(f"/api/animations/{anim}/jobs", json={"seed": 0})
            code, out, text = cli(env, "gen", "knight", "attack-melee", "--mode", "live", "--project", str(proj))
            rec.check(S, "signed out, a live job is refused by the API (402) and the CLI (exit 3), with the reason",
                      r.status_code == 402 and r.json()["error"]["status"] == "signed_out" and code == 3
                      and "Sign in to start your 7-day trial" in text
                      and not (proj / "animations" / "knight-attack-melee-E").exists(), [r.status_code, code], [])

            browser_sign_in(c)
            r = c.post(f"/api/animations/{anim}/jobs", json={"seed": 0})
            job = r.json().get("id", "")
            state = _wait_state(c, job, ("awaiting_approval", "failed", "done", "cancelled"))
            rec.check(S, "during the trial a live job starts (and, with a zero cap and no keys, waits without spending)",
                      r.status_code == 200 and state == "awaiting_approval" and _sent_calls(proj) == 0, state, [])

            # C34: access ends while that job waits; it keeps its place, the next one is refused
            user.trial_ends = fc.now() - 1
            acc = c.post("/api/cloud/access/refresh").json()
            r2 = c.post(f"/api/animations/{anim}/jobs", json={"seed": 1})
            code, out, text = cli(env, "gen", "knight", "idle", "--mode", "live", "--project", str(proj))
            rec.check(S, "when access ends, the running job carries on and the next job is refused (API and CLI)",
                      not acc["allowed"] and acc["status"] == "trial_ended"
                      and c.get(f"/api/jobs/{job}").json()["state"] == "awaiting_approval"
                      and r2.status_code == 402 and r2.json()["error"]["status"] == "trial_ended" and code == 3
                      and "trial has ended" in text, [acc["status"], r2.status_code, code], ["C34"])
            c.post(f"/api/jobs/{job}/cancel")

            # C31: paid access holds offline for 7 days after the last check, then asks to go online
            user.pro = True
            acc = c.post("/api/cloud/access/refresh").json()
            fc.stop()
            held = c.get("/api/cloud/status").json()["access"]
            _shift_access(lib, local=-8 * 86400, server=-8 * 86400)
            late = c.get("/api/cloud/status").json()["access"]
            r3 = c.post(f"/api/animations/{anim}/jobs", json={"seed": 2})
            rec.check(S, "offline, Pro stays unlocked within the grace; past 7 days generation asks to go online",
                      acc["status"] == "pro" and held["allowed"] and not late["allowed"] and late["reason"] == "offline"
                      and late["message"] == "Connect to the internet to check your access." and r3.status_code == 402,
                      [held["allowed"], late["reason"], r3.status_code], ["C31"])
            fc.start()
            back = c.post("/api/cloud/access/refresh").json()
            rec.check(S, "the lock lifts on the next successful check", back["allowed"] and back["status"] == "pro",
                      back["status"], ["C31"])

            # C32: the local clock moved back since the last check can't stretch access
            _shift_access(lib, local=+7200)
            fc.stop()
            moved = c.get("/api/cloud/status").json()["access"]
            fc.start()
            again = c.get("/api/cloud/status").json()["access"]
            rec.check(S, "a clock moved back makes the cached answer stale: locked offline, checked again online",
                      not moved["allowed"] and moved["reason"] == "offline" and again["allowed"],
                      [moved["reason"], again["allowed"]], ["C32"])
            rec.check(S, "no live job ever reached a provider", _sent_calls(proj) == 0, _sent_calls(proj), [])
    finally:
        eng.stop()
        fc.stop()


def test_cloud_tokens(rec, work):
    """Sign-in from the CLI without an engine, refresh and rotation between processes, a lost rotation,
    a machine signed out on the website, bad callbacks, a cancelled consent, renaming, and a machine
    with no keychain (C1–C6, C26, C37)."""
    S = "cloud-tokens"
    fc = FakeCloud().start()
    fc.add_user("tok@example.com", "Token Test")
    lib = work / S / "machine-b"
    env = machine_env(fc.url, lib)
    outputs: list[str] = []
    try:
        # C3: the CLI with no engine running listens once on 127.0.0.1:0
        code, st, text = cli_login(env)
        outputs.append(text)
        redirect = parse_qs(urlsplit(json.loads(text.splitlines()[0])["authorize_url"]).query)["redirect_uri"][0]
        port = urlsplit(redirect).port
        try:
            httpx.get(redirect, timeout=2)
            closed = False
        except httpx.HTTPError:
            closed = True
        rec.check(S, "the CLI signs in through a one-shot loopback listener that closes afterwards",
                  code == 0 and st["signed_in"] and st["access"]["status"] == "trial" and closed and port,
                  [code, st["access"]["status"], closed], ["C3"])

        eng = Engine(free_port(), env, work / S / "engine.log")
        try:
            with eng.client() as c:
                st = c.get("/api/cloud/status").json()
                rec.check(S, "the engine sees the CLI's sign-in: one keychain per machine",
                          st["signed_in"] and st["user"]["email"] == "tok@example.com", st["user"]["email"], [])

                # C37: the engine and CLI commands refresh at once; nobody spends a rotated token
                fc.expire_access_tokens()
                procs = [subprocess.Popen([sys.executable, "-m", "spriteguru.cli", "cloud", "status", "--refresh"],
                                          env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                         for _ in range(5)]
                mine = c.get("/api/cloud/status", params={"refresh": "true"}).json()
                results = [p.communicate(timeout=60) for p in procs]
                outputs += [o + e for o, e in results]
                ok = [json.loads(o.strip().splitlines()[-1])["signed_in"] for o, _ in results]
                rec.check(S, "five CLI commands and the engine refresh together without signing the machine out",
                          all(ok) and mine["signed_in"] and not fc.events("refresh_refused")
                          and c.get("/api/cloud/status").json()["signed_in"], [ok, len(fc.events("refresh_refused"))],
                          ["C37"])

                # C5: an expired access token mid-use is refreshed once and the call retried
                fc.expire_access_tokens()
                before = len([e for e in fc.events("token") if e["grant"] == "refresh_token"])
                st = c.get("/api/cloud/status", params={"refresh": "true"}).json()
                after = len([e for e in fc.events("token") if e["grant"] == "refresh_token"])
                rec.check(S, "an access token that expired mid-use is refreshed and the call retried",
                          st["signed_in"] and st["personal"] is not None and after == before + 1, after - before, ["C5"])

                # C4: the rotation's answer was lost, so the stored refresh token is dead
                keep = lib / "keep.sprites"
                cli(env, "init", str(keep), "--style", "pixel", "--mode", "synthetic")
                digest = _tree_digest(keep)
                dev = next(iter(fc.devices.values()))
                dev.refresh = "0" * 64
                fc.expire_access_tokens()
                st = c.get("/api/cloud/status", params={"refresh": "true"}).json()
                rec.check(S, "a refresh token the server refuses signs this machine out, asking to sign in again",
                          not st["signed_in"] and "refresh_token" not in _keyring(lib), st["signed_in"], ["C4"])

                # C6: signed out from the website's Machines page
                browser_sign_in(c)
                fc.revoke_machine(c.get("/api/cloud/status").json()["machine"]["id"])
                st = c.get("/api/cloud/status", params={"refresh": "true"}).json()
                rec.check(S, "a machine signed out on the website returns to signed out; local files are untouched",
                          not st["signed_in"] and "refresh_token" not in _keyring(lib)
                          and _tree_digest(keep) == digest and (keep / "project.json").is_file(), st["signed_in"], ["C6"])

                # C2: unknown state, a replayed callback, a wrong issuer
                grants = len(fc.events("token"))
                bogus = httpx.get(eng.url("/auth/callback"), params={"code": "x", "state": "not-the-state", "iss": fc.url})
                browser_sign_in(c)
                grants_ok = len(fc.events("token"))
                replay = httpx.get(fc.last_redirect)
                url = c.post("/api/cloud/sign-in").json()["authorize_url"]
                state = parse_qs(urlsplit(url).query)["state"][0]
                evil = httpx.get(eng.url("/auth/callback"), params={"code": "x", "state": state,
                                                                    "iss": "https://evil.example"})
                rec.check(S, "callbacks with an unknown or reused state, or another issuer, are refused without a token call",
                          bogus.status_code == 400 and "Start sign-in again" in bogus.text and replay.status_code == 400
                          and evil.status_code == 400 and grants_ok == grants + 1
                          and len(fc.events("token")) == grants_ok, [bogus.status_code, replay.status_code,
                                                                     evil.status_code], ["C2"])

                # C1: a pending sign-in keeps its link for "Open the browser again" until it's done or cancelled
                url = c.post("/api/cloud/sign-in").json()["authorize_url"]
                pending = c.get("/api/cloud/status").json()["pending_sign_in"]
                c.post("/api/cloud/sign-in/cancel")
                cleared = c.get("/api/cloud/status").json()["pending_sign_in"]
                rec.check(S, "a pending sign-in offers the same link again until it completes or is cancelled",
                          pending is not None and pending["url"] == url and 0 < pending["expires_in"] <= 600
                          and cleared is None, pending and pending["expires_in"] > 0, ["C1"])

                fc.deny_next = True
                r = browser_sign_in(c)
                rec.check(S, "a cancelled consent sends the browser to the website's cancelled page; still signed in",
                          "result=cancelled" in str(r.url) and c.get("/api/cloud/status").json()["signed_in"],
                          str(r.url).split("?")[-1], [])

                r = c.patch("/api/cloud/machine", json={"name": "Studio iMac"})
                st = c.get("/api/cloud/status").json()
                rn = fc.events("rename_device")
                rec.check(S, "renaming this machine renames it on the website and in later sign-ins",
                          r.status_code == 200 and st["machine"]["name"] == "Studio iMac" and rn
                          and rn[-1]["name"] == "Studio iMac" and rn[-1]["device"] == st["machine"]["device_id"],
                          st["machine"]["name"], [])
                c.post("/api/cloud/sign-out")
                browser_sign_in(c)
                rec.check(S, "the next sign-in carries the new name", fc.events("authorize")[-1]["device_name"] == "Studio iMac",
                          fc.events("authorize")[-1]["device_name"], [])
                outputs.append(json.dumps(c.get("/api/cloud/status").json()))
        finally:
            eng.stop()

        # a machine's own id: base64url of a hash of the OS id, stable, and not the OS id itself (4.2)
        fresh = work / S / "fresh-machine"
        fresh.mkdir(parents=True)
        env_f = {**env, "SPRITEGURU_LIBRARY": str(fresh)}
        ids = [cli(env_f, "cloud", "status")[1]["machine"]["id"] for _ in range(2)]
        raw = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True).stdout \
            if sys.platform == "darwin" else ""
        m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', raw)
        rec.check(S, "a machine's id is a stable 43-character hash that doesn't reveal the OS machine id",
                  ids[0] == ids[1] and re.fullmatch(r"[A-Za-z0-9_-]{43}", ids[0]) is not None
                  and (m is None or m.group(1) not in ids[0]), len(ids[0]), [])

        # C26: no keychain backend: the sign-in lives in the engine's memory only
        lib_c = work / S / "machine-c"
        env_c = machine_env(fc.url, lib_c, PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring")
        eng_c = Engine(free_port(), env_c, work / S / "engine-c.log")
        try:
            with eng_c.client() as c:
                browser_sign_in(c)
                st = c.get("/api/cloud/status").json()
                on_disk = leaks(fc.issued, files_text(lib_c, skip=()))
            eng_c.stop()
            eng_c = Engine(free_port(), env_c, work / S / "engine-c.log")
            with eng_c.client() as c:
                later = c.get("/api/cloud/status").json()
            rec.check(S, "without a keychain the sign-in works, says it won't persist, touches no file and ends with the engine",
                      st["signed_in"] and st["persisted"] is False and on_disk == 0 and not later["signed_in"],
                      [st["persisted"], on_disk, later["signed_in"]], ["C26"])
        finally:
            eng_c.stop()
        texts = outputs + [(work / S / "engine.log").read_text(), (work / S / "engine-c.log").read_text()] + \
            files_text(lib)
        rec.check(S, "no token appears in CLI output, engine logs, status answers or files other than the keychain",
                  leaks(fc.issued, texts) == 0, leaks(fc.issued, texts), ["P7"])
    finally:
        fc.stop()


def test_cloud_updates(rec, work, run_dir):
    """The daily update check (cloud plan 9): signed out from the public release list, signed in from
    the account's updates; a License whose updates ended hears that the newest build needs renewal,
    and a renewed one is offered the download. The answer is kept for a day."""
    S = "cloud-updates"
    from playwright.sync_api import expect, sync_playwright

    fc = FakeCloud().start()
    user = fc.add_user("updates@example.com", "Update Test")
    for plat in ("macos-arm64", "macos-x64", "windows-x64", "linux-x64"):
        fc.add_release("0.1.0", fc.now() - 30 * 86400, platform=plat)
        fc.add_release("0.2.0", fc.now() - 3600, platform=plat)
    user.license_until = fc.now() - 86400  # updates ended yesterday: 0.1.0 is covered, 0.2.0 is not
    lib = work / S / "machine"
    eng = Engine(free_port(), machine_env(fc.url, lib), work / S / "engine.log")
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)
    try:
        with eng.client() as c, sync_playwright() as pw:
            st = c.get("/api/cloud/status").json()
            up = st["update"]
            rec.check(S, "signed out, the public release list offers the newest build with an absolute download link",
                      up["available"] and up["version"] == "0.2.0" and up["url"].startswith(fc.url + "/api/download/")
                      and not up["needs_renewal"] and len(fc.events("releases_latest")) == 1, up["version"], [])
            c.get("/api/cloud/status")
            rec.check(S, "the answer is kept for a day: a second status asks nothing",
                      len(fc.events("releases_latest")) == 1, len(fc.events("releases_latest")), [])

            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(f"{eng.url()}/?token=t0k#/projects")
            banner = page.get_by_test_id("update-banner")
            expect(banner).to_contain_text("SpritePlay 0.2.0 is available.", timeout=15000)
            rec.check(S, "the gallery shows the available update with a Download button",
                      page.get_by_test_id("update-download").is_visible(), banner.inner_text(), [])

            browser_sign_in(c)
            st = c.get("/api/cloud/status", params={"refresh": "true"}).json()
            up = st["update"]
            page.reload()
            expect(banner).to_contain_text("needs renewed updates", timeout=15000)
            rec.check(S, "a License whose updates ended isn't offered 0.2.0; it hears the build needs renewed updates",
                      not up["available"] and up["needs_renewal"] and up["latest"] == "0.2.0" and up["version"] == "0.1.0"
                      and st["access"]["allowed"] and page.get_by_test_id("update-renew").is_visible(),
                      {"installable": up["version"], "renew": up["needs_renewal"]}, ["C33"])
            (run_dir / "outputs" / S).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / "01-needs-renewal.png"))
            rec.files.append({"scenario": S, "path": (shots / "01-needs-renewal.png").relative_to(run_dir).as_posix(),
                              "sha256": None, "in_digest": False})

            user.license_until = fc.now() + 365 * 86400  # renewed
            st = c.get("/api/cloud/status", params={"refresh": "true"}).json()
            page.reload()
            page.get_by_test_id("account-chip").click()
            entry = page.get_by_test_id("account-update")
            expect(entry).to_be_visible(timeout=10000)
            rec.check(S, "after renewing, the account menu offers the update",
                      st["update"]["available"] and "SpritePlay 0.2.0" in entry.inner_text()
                      and len(fc.events("updates_check")) >= 2, entry.inner_text(), [])
            browser.close()
    finally:
        eng.stop()
        fc.stop()
