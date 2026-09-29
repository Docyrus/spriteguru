"""A fake spriteplay.com for the cloud scenarios (cloud plan 12): the desktop contract of
spriteguru-web (docs/desktop-integration.md, and the worker code it summarizes) in memory, with
deterministic ids and the same payloads and error shapes.

It runs in the test's process (uvicorn in a thread), so a scenario sets its knobs directly on the
object: access per account, limits, team state, a revoked machine, an injected `409 retry`, a
dropped or corrupted download, the server clock. `stop()` and `start()` keep the state, so going
offline and back is real (connection refused), not a simulated error.

Only what the desktop uses is implemented. Where the real server is lenient (no case-collision
check on paths), the fake is too.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

GiB = 1024 ** 3
MiB = 1024 ** 2
TRIAL_LIMITS = {"storageBytes": 2 * GiB, "devices": 2, "cloudProjects": 3, "historyDays": 7, "maxFileBytes": 100 * MiB,
                "teams": False}
LICENSE_LIMITS = {**TRIAL_LIMITS, "devices": 3}
PRO_LIMITS = {"storageBytes": 100 * GiB, "devices": None, "cloudProjects": None, "historyDays": 30,
              "maxFileBytes": 2 * GiB, "teams": False}
TEAM_SEAT_STORAGE = 200 * GiB
PATH_OK = re.compile(r"^(?!/)(?!.*\\)(?!.*\x00).{1,512}$")


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **details):
        self.status, self.code, self.message, self.details = status, code, message, details


def _valid_path(p: str) -> bool:
    return bool(PATH_OK.match(p)) and all(seg not in ("", ".", "..") for seg in p.split("/"))


@dataclass
class User:
    id: str
    email: str
    name: str
    pro: bool = False
    license_until: float | None = None  # a License with updates until then
    license_number: str | None = None
    trial_ends: float | None = None  # a trial row (started 7 days before)
    limits: dict | None = None  # overrides the plan's limits (quota and file-size scenarios)


@dataclass
class Team:
    id: str
    name: str
    slug: str
    members: dict  # user id -> role
    active: bool = True
    seats: int = 5
    storage_limit: int | None = None


@dataclass
class Device:
    id: str
    user: str
    machine_id: str
    name: str
    platform: str
    app_version: str | None
    refresh: str | None = None
    revoked: bool = False
    created: float = field(default_factory=time.time)
    last_seen: float | None = None


class FakeCloud:
    def __init__(self):
        self.port = 0
        self.lock = threading.RLock()
        self._ids = itertools.count(1)
        self.clock_offset = 0.0
        self.users: dict[str, User] = {}
        self.teams: dict[str, Team] = {}
        self.devices: dict[str, Device] = {}
        self.codes: dict[str, dict] = {}
        self.tokens: dict[str, dict] = {}  # access token -> {user, device, exp}
        self.machine_trials: dict[str, float] = {}  # machine id -> trial end
        self.releases: list[dict] = []
        self.projects: dict[str, dict] = {}
        self.files: dict[str, dict[str, dict]] = {}  # project -> path -> row
        self.versions: dict[str, list[dict]] = {}
        self.revisions: dict[str, list[dict]] = {}
        self.blobs: dict[str, dict[str, dict]] = {}  # owner -> sha -> {size, type, data}
        self.uploads: dict[str, dict] = {}
        self.library: dict[str, dict] = {}
        self.library_files: dict[str, list[dict]] = {}
        self.log: list[dict] = []  # what the app did, for assertions
        self.issued: list[str] = []  # every token handed out, so a scenario can prove none leaked
        self.user_agents: set[str] = set()  # every User-Agent the app sent (N11)
        # knobs
        self.session_user: str | None = None  # the account signed in on the website (auto-approves)
        self.last_redirect: str | None = None  # the last callback URL handed to a browser (replay tests)
        self.deny_next = False
        self.access_ttl = 3600
        self.single_put_max = 99614720
        self.part_size = 52428800
        self.files_page = 2000
        self.retry_commits = 0  # the next N commits answer 409 retry
        self.corrupt_downloads: dict[str, int] = {}  # hash -> how many more downloads get wrong bytes
        self.upload_hook = None  # called with (sha) while an upload is received (a file changing mid-upload)
        self.drop_downloads: set[str] = set()  # hashes whose download breaks off mid-body (each once)
        self.app = self._build()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "FakeCloud":
        if not self.port:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                self.port = s.getsockname()[1]
        # one Date header, the fake's own clock, as the real server sends (uvicorn would add a second)
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error", lifespan="off",
                                date_header=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started and time.time() < deadline:
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            self._thread.join(10)
            self._server = None

    # -- knobs ----------------------------------------------------------------------------------

    def now(self) -> float:
        return time.time() + self.clock_offset

    def _id(self, prefix: str) -> str:
        n = next(self._ids)
        return f"{prefix}_{n:020d}" if prefix else f"{n:032d}"

    def add_user(self, email: str, name: str = "Test User", **kw) -> User:
        with self.lock:
            u = User(self._id(""), email, name, **kw)
            self.users[u.id] = u
            self.session_user = self.session_user or u.id
            return u

    def add_team(self, name: str, members: dict, **kw) -> Team:
        with self.lock:
            t = Team(self._id(""), name, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), members, **kw)
            self.teams[t.id] = t
            return t

    def add_release(self, version: str, published: float, platform: str = "macos-arm64", channel: str = "stable"):
        self.releases.append({"version": version, "publishedAt": published, "platform": platform, "channel": channel,
                              "size": 1000, "sha256": None, "notes": None})

    def revoke_machine(self, machine_id: str) -> None:
        with self.lock:
            for d in self.devices.values():
                if d.machine_id == machine_id:
                    d.revoked, d.refresh = True, None

    def expire_access_tokens(self) -> None:
        with self.lock:
            for t in self.tokens.values():
                t["exp"] = 0

    def events(self, kind: str) -> list[dict]:
        return [e for e in self.log if e["kind"] == kind]

    # -- entitlements (worker/lib/entitlements.ts) ----------------------------------------------------

    def _teams_of(self, uid: str) -> list[Team]:
        return [t for t in self.teams.values() if uid in t.members]

    def _trial(self, u: User) -> dict | None:
        if u.trial_ends is None:
            return None
        return {"startedAt": _iso(u.trial_ends - 7 * 86400), "endsAt": _iso(u.trial_ends),
                "active": self.now() < u.trial_ends}

    def _start_trial(self, u: User, machine_id: str | None) -> None:
        if not machine_id or u.pro or u.license_until is not None or u.trial_ends is not None:
            return
        if any(t.active for t in self._teams_of(u.id)):
            return
        # a machine passes on what is left of its earlier trial, usually nothing
        u.trial_ends = self.machine_trials.get(machine_id, self.now() + 7 * 86400)
        self.machine_trials.setdefault(machine_id, u.trial_ends)

    def entitlement(self, u: User) -> dict:
        trial = self._trial(u)
        trial_active = bool(trial and trial["active"])
        lic = None
        if u.license_until is not None:
            lic = {"number": u.license_number or "SG-7K2F-9QXA-M3TD", "status": "active",
                   "purchasedAt": _iso(u.license_until - 365 * 86400), "updatesUntil": _iso(u.license_until)}
        if u.pro:
            e = {"plan": "pro", "via": "subscription", "status": "pro", "allowed": True, "cloud": True,
                 "updatesUntil": None, "limits": PRO_LIMITS}
        elif any(t.active for t in self._teams_of(u.id)):
            e = {"plan": "pro", "via": "team", "status": "team", "allowed": True, "cloud": True, "updatesUntil": None,
                 "limits": PRO_LIMITS}
        elif lic:
            e = {"plan": "license", "via": "license", "status": "license", "allowed": True, "cloud": trial_active,
                 "updatesUntil": lic["updatesUntil"], "limits": LICENSE_LIMITS}
        else:
            status = "trial_available" if trial is None else "trial" if trial_active else "trial_ended"
            e = {"plan": "free", "via": "free", "status": status, "allowed": trial_active, "cloud": trial_active,
                 "updatesUntil": None, "limits": TRIAL_LIMITS}
        if u.limits:
            e["limits"] = {**e["limits"], **u.limits}
        return {**e, "trial": trial, "license": lic}

    def present_access(self, u: User) -> dict:
        e = self.entitlement(u)
        return {"allowed": e["allowed"], "status": e["status"], "trial": e["trial"], "license": e["license"],
                "updatesUntil": e["updatesUntil"]}

    def desktop_access(self, u: User, version: str | None) -> dict:
        e = self.entitlement(u)
        return {**self.present_access(u), "versionAllowed": self._version_allowed(e, version), "cloud": e["cloud"],
                "plan": e["plan"]}

    def _version_allowed(self, e: dict, version: str | None) -> bool:
        if not e["allowed"]:
            return False
        if e["updatesUntil"] is None or not version:
            return True
        pubs = [r["publishedAt"] for r in self.releases if r["version"] == version and r["publishedAt"]]
        if not pubs:
            return True
        until = dt.datetime.fromisoformat(e["updatesUntil"].replace("Z", "+00:00")).timestamp()
        return min(pubs) <= until

    # -- workspaces (worker/lib/access.ts) -----------------------------------------------------------

    def workspace(self, u: User, owner: str | None) -> dict:
        owner = u.id if owner in (None, "", "me") else owner
        if owner == u.id:
            e = self.entitlement(u)
            return {"type": "user", "id": u.id, "name": "Personal", "role": "owner", "canWrite": e["cloud"],
                    "canManage": True, "library": e["plan"] == "pro", "_entitlement": e}
        t = self.teams.get(owner)
        if t is None or u.id not in t.members:
            raise ApiError(404, "not_found", "That workspace was not found.")
        return {"type": "org", "id": t.id, "name": t.name, "role": t.members[u.id], "canWrite": t.active,
                "canManage": t.members[u.id] in ("owner", "admin"), "library": t.active, "_team": t}

    def _require_write(self, u: User, ws: dict) -> None:
        if ws["canWrite"]:
            return
        if ws["type"] == "user":
            msg = ("Cloud sync is part of Pro and Team; the License covers the desktop app. Your synced files stay "
                   "available to download." if ws["_entitlement"]["plan"] == "license" else
                   "Cloud sync is part of Pro and Team, and your trial has ended. Your synced files stay available "
                   "to download.")
            raise ApiError(402, "plan_required", msg)
        raise ApiError(402, "team_inactive", f"{ws['name']} has no active Team plan. Members can download files, but "
                                             "syncing and uploads are paused until an owner renews it.")

    def _require_library(self, ws: dict) -> None:
        if ws["library"]:
            return
        if ws["type"] == "user":
            raise ApiError(402, "plan_required", "The asset library is part of Pro and Team. Upgrade to keep assets "
                                                 "outside projects.")
        raise ApiError(402, "team_inactive", f"{ws['name']} has no active Team plan, so its library is read-only.")

    def _limits(self, ws: dict) -> dict:
        if ws["type"] == "user":
            return ws["_entitlement"]["limits"]
        t = ws["_team"]
        return {"storageBytes": t.storage_limit if t.storage_limit is not None else TEAM_SEAT_STORAGE * t.seats,
                "maxFileBytes": 5 * GiB, "cloudProjects": None, "historyDays": 90}

    def _used(self, owner: str) -> int:
        return sum(b["size"] for b in self.blobs.get(owner, {}).values())

    def _assert_quota(self, ws: dict, needed: int) -> None:
        limit = self._limits(ws)["storageBytes"]
        used = self._used(ws["id"])
        if limit is not None and used + needed > limit:
            raise ApiError(413, "quota_exceeded", f"{ws['name']} is out of cloud storage.", used=used, limit=limit,
                           needed=needed)

    @staticmethod
    def _public_ws(ws: dict, keys=("type", "id", "name")) -> dict:
        return {k: ws[k] for k in keys}

    # -- the app --------------------------------------------------------------------------------------------

    def _build(self) -> FastAPI:
        app = FastAPI()
        fc = self

        @app.exception_handler(ApiError)
        async def _api_error(request: Request, e: ApiError):
            return JSONResponse({"error": {"code": e.code, "message": e.message, **e.details}}, status_code=e.status,
                                headers=fc._date())

        @app.middleware("http")
        async def _date_header(request: Request, call_next):
            fc.user_agents.add(request.headers.get("user-agent") or "")
            resp = await call_next(request)
            resp.headers["date"] = fc._date()["date"]
            return resp

        def user_of(request: Request) -> tuple[User, Device]:
            auth = request.headers.get("authorization") or ""
            if not auth.startswith("Bearer "):
                raise ApiError(401, "unauthenticated", "Sign in to continue.")
            with fc.lock:
                t = fc.tokens.get(auth[7:])
                if t is None or t["exp"] <= fc.now():
                    raise ApiError(401, "invalid_token", "The access token is invalid or expired.")
                d = fc.devices[t["device"]]
                if d.revoked:
                    raise ApiError(401, "device_revoked", "This machine was signed out. Sign in again from the app.")
                d.last_seen = fc.now()
                return fc.users[t["user"]], d

        async def body_of(request: Request) -> dict:
            if "application/json" in (request.headers.get("content-type") or ""):
                return await request.json()
            form = await request.form()
            return {k: v for k, v in form.items()}

        def oauth_error(status: int, code: str, desc: str) -> JSONResponse:
            return JSONResponse({"error": code, "error_description": desc}, status_code=status,
                                headers={"cache-control": "no-store"})

        # ---- OAuth -------------------------------------------------------------------------------------------

        @app.get("/.well-known/oauth-authorization-server")
        def discovery():
            b = fc.url
            return {"issuer": b, "authorization_endpoint": f"{b}/api/oauth/authorize",
                    "token_endpoint": f"{b}/api/oauth/token", "revocation_endpoint": f"{b}/api/oauth/revoke",
                    "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
                    "scopes_supported": ["profile", "sync"], "authorization_response_iss_parameter_supported": True}

        def with_query(uri: str, **params) -> str:
            parts = urlsplit(uri)
            q = parse_qs(parts.query)
            q.update({k: [v] for k, v in params.items() if v is not None})
            return urlunsplit(parts._replace(query=urlencode({k: v[0] for k, v in q.items()})))

        @app.get("/authorize")
        def authorize_page(result: str = "", error: str = ""):
            """The website's own "you can close this tab" page, which the app's callback redirects to."""
            fc.log.append({"kind": "authorize_page", "result": result or error})
            from fastapi.responses import HTMLResponse

            return HTMLResponse(f"<!doctype html><title>SpritePlay</title><p data-result='{result or error}'>"
                                f"{'Signed in. You can close this tab.' if result == 'signed-in' else 'Sign-in cancelled.'}")

        @app.get("/api/oauth/authorize")
        def authorize(request: Request):
            """The real site shows a consent page; the fake approves as `session_user` at once."""
            q = dict(request.query_params)
            ru = q.get("redirect_uri", "")
            p = urlsplit(ru)
            if (p.scheme != "http" or p.hostname not in ("127.0.0.1", "localhost", "::1") or not p.port
                    or p.username or p.fragment or q.get("client_id") != "spriteguru-desktop"):
                return RedirectResponse("/authorize?error=invalid_client", 302)
            ok = (q.get("response_type") == "code" and q.get("code_challenge_method") == "S256"
                  and re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", q.get("code_challenge", ""))
                  and 8 <= len(q.get("state", "")) <= 512
                  and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", q.get("machine_id", ""))
                  and 1 <= len(q.get("device_name", "").strip()) <= 80
                  and q.get("platform") in ("macos", "windows", "linux"))
            if not ok:
                return RedirectResponse(with_query(ru, error="invalid_request", error_description="bad parameters",
                                                   state=q.get("state")), 302)
            if fc.deny_next:
                fc.deny_next = False
                return RedirectResponse(with_query(ru, error="access_denied",
                                                   error_description="The user cancelled sign-in.", state=q["state"]), 302)
            with fc.lock:
                u = fc.users[fc.session_user]
                fc._start_trial(u, q["machine_id"])
                code = secrets.token_urlsafe(32)
                fc.codes[code] = {"user": u.id, "challenge": q["code_challenge"], "redirect_uri": ru,
                                  "client_id": q["client_id"], "machine_id": q["machine_id"],
                                  "device_name": q["device_name"].strip(), "platform": q["platform"],
                                  "app_version": q.get("app_version"), "exp": fc.now() + 300}
                fc.log.append({"kind": "authorize", "machine_id": q["machine_id"], "device_name": q["device_name"],
                               "platform": q["platform"], "app_version": q.get("app_version")})
            fc.last_redirect = with_query(ru, code=code, state=q["state"], iss=fc.url)
            return RedirectResponse(fc.last_redirect, 302)

        def issue(u: User, d: Device) -> dict:
            at = secrets.token_urlsafe(32)
            rt = secrets.token_urlsafe(32)
            fc.tokens[at] = {"user": u.id, "device": d.id, "exp": fc.now() + fc.access_ttl}
            d.refresh = hashlib.sha256(rt.encode()).hexdigest()
            fc.issued += [at, rt]
            return {"access_token": at, "token_type": "Bearer", "expires_in": fc.access_ttl, "refresh_token": rt,
                    "scope": "profile sync", "device_id": d.id}

        @app.post("/api/oauth/token")
        async def token(request: Request):
            try:
                b = await body_of(request)
            except Exception:
                return oauth_error(400, "invalid_request", "The request body could not be read.")
            if b.get("client_id") != "spriteguru-desktop":
                return oauth_error(401, "invalid_client", "Unknown client.")
            grant = b.get("grant_type")
            with fc.lock:
                if grant == "authorization_code":
                    if not (b.get("code") and b.get("code_verifier") and b.get("redirect_uri")):
                        return oauth_error(400, "invalid_request", "code, code_verifier and redirect_uri are required.")
                    if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", b["code_verifier"]):
                        return oauth_error(400, "invalid_grant", "The code verifier is malformed.")
                    c = fc.codes.pop(b["code"], None)  # used up even when the exchange fails
                    if c is None or c["exp"] < fc.now():
                        return oauth_error(400, "invalid_grant", "The code is invalid or expired.")
                    if c["redirect_uri"] != b["redirect_uri"]:
                        return oauth_error(400, "invalid_grant", "The code was issued for a different redirect.")
                    import base64

                    ch = base64.urlsafe_b64encode(hashlib.sha256(b["code_verifier"].encode()).digest()).decode().rstrip("=")
                    if ch != c["challenge"]:
                        return oauth_error(400, "invalid_grant", "PKCE verification failed.")
                    u = fc.users[c["user"]]
                    d = next((x for x in fc.devices.values() if x.user == u.id and x.machine_id == c["machine_id"]), None)
                    if d is None:
                        d = Device(fc._id("dev"), u.id, c["machine_id"], c["device_name"], c["platform"], c["app_version"])
                        fc.devices[d.id] = d
                    d.name, d.platform, d.app_version, d.revoked = c["device_name"], c["platform"], c["app_version"], False
                    fc._start_trial(u, d.machine_id)
                    out = {**issue(u, d), "user": {"id": u.id, "name": u.name, "email": u.email},
                           "access": fc.present_access(u)}
                    fc.log.append({"kind": "token", "grant": grant, "device": d.id})
                    return JSONResponse(out, headers={"cache-control": "no-store"})
                if grant == "refresh_token":
                    rt = b.get("refresh_token")
                    if not rt:
                        return oauth_error(400, "invalid_request", "refresh_token is required.")
                    h = hashlib.sha256(rt.encode()).hexdigest()
                    d = next((x for x in fc.devices.values() if x.refresh == h and not x.revoked), None)
                    if d is None:
                        fc.log.append({"kind": "refresh_refused"})
                        return oauth_error(400, "invalid_grant", "The refresh token is invalid, expired or was revoked.")
                    if b.get("app_version"):
                        d.app_version = str(b["app_version"])[:32]
                    u = fc.users[d.user]
                    fc._start_trial(u, d.machine_id)
                    fc.log.append({"kind": "token", "grant": grant, "device": d.id})
                    return JSONResponse({**issue(u, d), "access": fc.present_access(u)},
                                        headers={"cache-control": "no-store"})
            return oauth_error(400, "unsupported_grant_type", "Use authorization_code or refresh_token.")

        @app.post("/api/oauth/revoke")
        async def revoke(request: Request):
            b = await body_of(request)
            with fc.lock:
                h = hashlib.sha256(str(b.get("token", "")).encode()).hexdigest()
                for d in fc.devices.values():
                    if d.refresh == h:
                        d.revoked, d.refresh = True, None
                        fc.log.append({"kind": "revoke", "device": d.id})
            return Response(status_code=200)

        # ---- access, account, machines, releases --------------------------------------------------------------

        @app.get("/api/v1/desktop/access")
        def desktop_access(request: Request, version: str | None = None):
            u, d = user_of(request)
            with fc.lock:
                fc._start_trial(u, d.machine_id)
                fc.log.append({"kind": "access_check", "version": version})
                return fc.desktop_access(u, (version or "")[:32] or None)

        def release_json(r: dict, absolute: bool = True) -> dict:
            url = f"/api/download/{r['platform']}?version={r['version']}"
            return {"version": r["version"], "size": r["size"], "sha256": r["sha256"], "notes": r["notes"],
                    "publishedAt": _iso(r["publishedAt"]), "url": (fc.url + url) if absolute else url}

        def vkey(v: str) -> tuple:
            return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v))

        @app.get("/api/v1/desktop/updates")
        def desktop_updates(request: Request, platform: str = "", channel: str = "stable"):
            u, _ = user_of(request)
            if platform not in ("macos-arm64", "macos-x64", "windows-x64", "linux-x64"):
                raise ApiError(400, "invalid_request", "platform must be one of macos-arm64, macos-x64, windows-x64, "
                                                       "linux-x64")
            fc.log.append({"kind": "updates_check", "platform": platform})
            rows = sorted((r for r in fc.releases if r["platform"] == platform and r["channel"] == channel),
                          key=lambda r: vkey(r["version"]))
            e = fc.entitlement(u)
            latest = rows[-1] if rows else None
            installable = latest
            if e["updatesUntil"]:
                until = dt.datetime.fromisoformat(e["updatesUntil"].replace("Z", "+00:00")).timestamp()
                ok = [r for r in rows if r["publishedAt"] <= until]
                installable = ok[-1] if ok else None
            if not e["allowed"]:
                installable = None
            return {"latest": release_json(latest) if latest else None,
                    "installable": release_json(installable) if installable else None,
                    "updatesUntil": e["updatesUntil"], "access": fc.present_access(u)}

        @app.get("/api/v1/releases/latest")
        def releases_latest(channel: str = "stable"):
            fc.log.append({"kind": "releases_latest"})
            out = []
            for plat in ("macos-arm64", "macos-x64", "windows-x64", "linux-x64"):
                rows = sorted((r for r in fc.releases if r["platform"] == plat and r["channel"] == channel),
                              key=lambda r: vkey(r["version"]))
                if rows:
                    out.append({"platform": plat, "label": plat, **release_json(rows[-1], absolute=False)})
            return {"releases": out}

        @app.get("/api/v1/me")
        def me(request: Request):
            u, d = user_of(request)
            with fc.lock:
                e = fc.entitlement(u)
                personal = {"id": u.id, "type": "user", "name": "Personal", "plan": e["plan"], "via": e["via"],
                            "cloud": e["cloud"], "library": e["plan"] == "pro", "limits": e["limits"],
                            "subscription": None,
                            "storage": {"used": fc._used(u.id), "limit": e["limits"]["storageBytes"]},
                            "projects": sum(1 for p in fc.projects.values() if p["ownerId"] == u.id and not p["deletedAt"]),
                            "devices": sum(1 for x in fc.devices.values() if x.user == u.id and not x.revoked)}
                teams = []
                for t in fc._teams_of(u.id):
                    limit = t.storage_limit if t.storage_limit is not None else TEAM_SEAT_STORAGE * t.seats
                    teams.append({"id": t.id, "type": "org", "name": t.name, "slug": t.slug, "logo": None,
                                  "role": t.members[u.id], "active": t.active, "devBypass": False, "seats": t.seats,
                                  "members": len(t.members), "storage": {"used": fc._used(t.id), "limit": limit},
                                  "subscription": None})
                return {"user": {"id": u.id, "name": u.name, "email": u.email, "emailVerified": True, "image": None},
                        "via": "device", "isStaff": False, "billingEnabled": False, "personal": personal, "teams": teams,
                        "desktop": fc.present_access(u)}

        @app.get("/api/v1/devices")
        def devices(request: Request):
            u, d = user_of(request)
            rows = [x for x in fc.devices.values() if x.user == u.id and not x.revoked]
            rows.sort(key=lambda x: x.last_seen or 0, reverse=True)
            return {"devices": [{"id": x.id, "name": x.name, "platform": x.platform, "appVersion": x.app_version,
                                 "createdAt": _iso(x.created), "lastSeenAt": _iso(x.last_seen), "lastIp": None}
                                for x in rows], "current": d.id}

        @app.patch("/api/v1/devices/{did}")
        async def rename_device(did: str, request: Request):
            u, _ = user_of(request)
            b = await request.json()
            x = fc.devices.get(did)
            if x is None or x.user != u.id:
                raise ApiError(404, "not_found", "That machine was not found.")
            name = str(b.get("name", "")).strip()
            if not 1 <= len(name) <= 80:
                raise ApiError(400, "invalid_request", "name: Too small")
            x.name = name
            fc.log.append({"kind": "rename_device", "device": did, "name": name})
            return {"ok": True}

        @app.delete("/api/v1/devices/{did}")
        def delete_device(did: str, request: Request):
            u, _ = user_of(request)
            x = fc.devices.get(did)
            if x is None or x.user != u.id:
                raise ApiError(404, "not_found", "That machine was not found.")
            x.revoked, x.refresh = True, None
            return {"ok": True}

        # ---- projects -------------------------------------------------------------------------------------------

        def project_of(u: User, pid: str, *, trashed_ok: bool = False) -> tuple[dict, dict]:
            p = fc.projects.get(pid)
            if p is None or (p["deletedAt"] and not trashed_ok):
                raise ApiError(404, "not_found", "That project was not found.")
            return p, fc.workspace(u, p["ownerId"])

        def project_json(p: dict, ws: dict, extra=("role", "canWrite", "canManage")) -> dict:
            return {**{k: v for k, v in p.items()}, "createdAt": _iso(p["createdAt"]), "updatedAt": _iso(p["updatedAt"]),
                    "deletedAt": _iso(p["deletedAt"]), "workspace": fc._public_ws(ws, ("type", "id", "name", *extra))}

        @app.get("/api/v1/projects")
        def list_projects(request: Request, owner: str | None = None, trash: str | None = None):
            u, _ = user_of(request)
            with fc.lock:
                spaces = [fc.workspace(u, owner)] if owner else [fc.workspace(u, None)] + \
                    [fc.workspace(u, t.id) for t in fc._teams_of(u.id)]
                ids = {w["id"]: w for w in spaces}
                rows = [p for p in fc.projects.values() if p["ownerId"] in ids and bool(p["deletedAt"]) == bool(trash)]
                rows.sort(key=lambda p: p["updatedAt"], reverse=True)
                return {"projects": [{**project_json(p, ids[p["ownerId"]], ()), "creatorName": fc.users[p["createdBy"]].name}
                                     for p in rows]}

        @app.post("/api/v1/projects")
        async def create_project(request: Request):
            u, _ = user_of(request)
            b = await request.json()
            with fc.lock:
                ws = fc.workspace(u, b.get("owner", "me"))
                fc._require_write(u, ws)
                limit = fc._limits(ws).get("cloudProjects")
                count = sum(1 for p in fc.projects.values() if p["ownerId"] == ws["id"] and not p["deletedAt"])
                if limit is not None and count >= limit:
                    raise ApiError(402, "project_limit", "The trial syncs 3 projects. Pro syncs as many as you like.",
                                   limit=limit)
                name = str(b.get("name", "")).strip()
                if not 1 <= len(name) <= 80:
                    raise ApiError(400, "invalid_request", "name: Too small")
                pid = fc._id("prj")
                now = fc.now()
                p = {"id": pid, "ownerType": ws["type"], "ownerId": ws["id"], "name": name, "style": b.get("style"),
                     "engine": b.get("engine"), "description": b.get("description"), "headRev": 0, "fileCount": 0,
                     "totalBytes": 0, "thumbnailSha": None, "createdBy": u.id, "createdAt": now, "updatedAt": now,
                     "deletedAt": None}
                fc.projects[pid], fc.files[pid], fc.versions[pid], fc.revisions[pid] = p, {}, [], []
                fc.log.append({"kind": "create_project", "project": pid, "owner": ws["id"]})
                return JSONResponse({"project": project_json(p, ws)}, status_code=201)

        @app.get("/api/v1/projects/{pid}")
        def get_project(pid: str, request: Request):
            u, _ = user_of(request)
            with fc.lock:
                p, ws = project_of(u, pid, trashed_ok=True)
                return {"project": {**project_json(p, ws), "creatorName": fc.users[p["createdBy"]].name,
                                    "canDelete": p["createdBy"] == u.id or ws["canManage"]}}

        @app.patch("/api/v1/projects/{pid}")
        async def patch_project(pid: str, request: Request):
            u, _ = user_of(request)
            b = await request.json()
            with fc.lock:
                p, ws = project_of(u, pid)
                fc._require_write(u, ws)
                for k in ("name", "description", "style", "engine", "thumbnailSha"):
                    if k in b:
                        p[k] = b[k]
                p["updatedAt"] = fc.now()
                return {"project": project_json(p, ws)}

        @app.delete("/api/v1/projects/{pid}")
        def trash_project(pid: str, request: Request):
            u, _ = user_of(request)
            with fc.lock:
                p, ws = project_of(u, pid)
                p["deletedAt"] = fc.now()
                return {"ok": True}

        @app.post("/api/v1/projects/{pid}/restore")
        def restore_project(pid: str, request: Request):
            u, _ = user_of(request)
            with fc.lock:
                p, ws = project_of(u, pid, trashed_ok=True)
                p["deletedAt"] = None
                return {"ok": True}

        @app.post("/api/v1/projects/{pid}/transfer")
        async def transfer(pid: str, request: Request):
            u, _ = user_of(request)
            b = await request.json()
            with fc.lock:
                p, src = project_of(u, pid)
                if not src["canManage"]:
                    raise ApiError(403, "forbidden", "Only team owners and admins can do this.")
                dst = fc.workspace(u, b.get("owner"))
                if dst["id"] == src["id"]:
                    raise ApiError(400, "bad_request", "The project is already in that workspace.")
                fc._require_write(u, dst)
                fc._copy_blobs(src["id"], dst["id"], [r["sha256"] for r in fc.files[pid].values() if r["sha256"]])
                p["ownerId"], p["ownerType"] = dst["id"], dst["type"]
                return {"ok": True, "workspace": fc._public_ws(dst)}

        def file_json(r: dict) -> dict:
            return {**r, "updatedAt": _iso(r["updatedAt"])}

        @app.get("/api/v1/projects/{pid}/files")
        def files(pid: str, request: Request, since: int = 0, cursor: str | None = None, limit: int | None = None):
            u, _ = user_of(request)
            with fc.lock:
                p, _ws = project_of(u, pid)
                size = max(1, min(5000, limit or fc.files_page))
                rows = sorted(fc.files[pid].values(), key=lambda r: r["path"])
                rows = [r for r in rows if (not r["deleted"] if since == 0 else r["rev"] > since)]
                if cursor:
                    rows = [r for r in rows if r["path"] > cursor]
                page = rows[:size]
                nxt = page[-1]["path"] if len(rows) > size else None
                return {"headRev": p["headRev"], "since": since, "files": [file_json(r) for r in page],
                        "nextCursor": nxt}

        @app.get("/api/v1/projects/{pid}/revisions")
        def revisions(pid: str, request: Request):
            u, _ = user_of(request)
            with fc.lock:
                project_of(u, pid)
                return {"revisions": [{**r, "createdAt": _iso(r["createdAt"])} for r in reversed(fc.revisions[pid])][:50]}

        @app.get("/api/v1/projects/{pid}/history")
        def history(pid: str, request: Request, path: str = ""):
            u, _ = user_of(request)
            with fc.lock:
                project_of(u, pid)
                if not path or not _valid_path(path):
                    raise ApiError(400, "bad_request", "Add ?path=<file path>.")
                vs = [v for v in reversed(fc.versions[pid]) if v["path"] == path][:100]
                return {"path": path, "versions": [{"rev": v["rev"], "sha256": v["sha256"], "size": v["size"],
                                                    "createdAt": _iso(v["createdAt"]), "userName": v["userName"]}
                                                   for v in vs]}

        @app.post("/api/v1/projects/{pid}/commit")
        async def commit(pid: str, request: Request):
            u, d = user_of(request)
            b = await request.json()
            with fc.lock:
                return fc._commit(u, d, pid, b)

        @app.post("/api/v1/projects/{pid}/restore-file")
        async def restore_file(pid: str, request: Request):
            u, d = user_of(request)
            b = await request.json()
            with fc.lock:
                p, ws = project_of(u, pid)
                v = next((v for v in fc.versions[pid] if v["path"] == b.get("path") and v["rev"] == b.get("rev")), None)
                if v is None:
                    raise ApiError(404, "not_found", "That version was not found.")
                ch = {"path": v["path"], "deleted": True} if v["sha256"] is None else \
                    {"path": v["path"], "sha256": v["sha256"], "size": v["size"]}
                return fc._commit(u, d, pid, {"baseRev": p["headRev"], "changes": [ch], "force": [v["path"]],
                                              "message": f"Restored {v['path']} from revision {v['rev']}"})

        # ---- blobs ---------------------------------------------------------------------------------------------------

        @app.post("/api/v1/blobs/check")
        async def blobs_check(request: Request):
            u, _ = user_of(request)
            b = await request.json()
            with fc.lock:
                ws = fc.workspace(u, b.get("owner"))
                fc._require_write(u, ws)
                items = b.get("blobs") or []
                if len(items) > 2000:
                    raise ApiError(400, "invalid_request", "blobs: Too big")
                maxb = fc._limits(ws)["maxFileBytes"]
                big = [x["sha256"] for x in items if maxb is not None and x["size"] > maxb]
                if big:
                    raise ApiError(413, "file_too_large", f"Files over {maxb // MiB} MB cannot be synced on this plan.",
                                   files=big, limit=maxb)
                held = fc.blobs.get(ws["id"], {})
                missing, seen = [], set()
                for x in items:
                    if x["sha256"] not in held and x["sha256"] not in seen:
                        seen.add(x["sha256"])
                        missing.append({"sha256": x["sha256"], "size": x["size"],
                                        "method": "multipart" if x["size"] > fc.single_put_max else "put"})
                needed = sum(x["size"] for x in missing)
                fc._assert_quota(ws, needed)
                fc.log.append({"kind": "blobs_check", "owner": ws["id"], "count": len(items), "missing": len(missing)})
                return {"missing": missing, "storage": {"used": fc._used(ws["id"]),
                                                        "limit": fc._limits(ws)["storageBytes"], "needed": needed},
                        "partSize": fc.part_size}

        def store_blob(owner: str, sha: str, data: bytes, ctype: str) -> None:
            fc.blobs.setdefault(owner, {})[sha] = {"size": len(data), "type": ctype, "data": data}

        @app.put("/api/v1/blobs/{sha}")
        async def put_blob(sha: str, request: Request, owner: str | None = None):
            u, _ = user_of(request)
            if not re.fullmatch(r"[a-f0-9]{64}", sha):
                raise ApiError(400, "bad_request", "The hash must be 64 lowercase hex characters.")
            if not owner:
                raise ApiError(400, "bad_request", "Add ?owner=<workspace id>.")
            with fc.lock:
                ws = fc.workspace(u, owner)
                fc._require_write(u, ws)
                try:
                    length = int(request.headers.get("content-length", ""))
                except ValueError:
                    raise ApiError(411, "length_required", "Send a Content-Length header.")
                if length > fc.single_put_max:
                    raise ApiError(413, "use_multipart", "Files over 95 MB must use a multipart upload.")
                maxb = fc._limits(ws)["maxFileBytes"]
                if maxb is not None and length > maxb:
                    raise ApiError(413, "file_too_large", "This file is larger than your plan allows.")
                if sha in fc.blobs.get(ws["id"], {}):
                    return {"sha256": sha, "size": length, "stored": False}
                fc._assert_quota(ws, length)
            data = await request.body()
            if fc.upload_hook is not None:
                fc.upload_hook(sha)
            if not data:
                raise ApiError(400, "bad_request", "The request has no body.")
            if hashlib.sha256(data).hexdigest() != sha:
                raise ApiError(422, "hash_mismatch", "The uploaded bytes do not match the hash.")
            with fc.lock:
                store_blob(ws["id"], sha, data, request.headers.get("content-type") or "application/octet-stream")
                fc.log.append({"kind": "upload", "owner": ws["id"], "sha256": sha, "size": len(data), "method": "put"})
            return JSONResponse({"sha256": sha, "size": length, "stored": True}, status_code=201)

        @app.post("/api/v1/blobs/{sha}/multipart")
        async def multipart_start(sha: str, request: Request, owner: str | None = None):
            u, _ = user_of(request)
            b = await request.json()
            if not owner:
                raise ApiError(400, "bad_request", "Add ?owner=<workspace id>.")
            with fc.lock:
                ws = fc.workspace(u, owner)
                fc._require_write(u, ws)
                size = int(b.get("size") or 0)
                maxb = fc._limits(ws)["maxFileBytes"]
                if maxb is not None and size > maxb:
                    raise ApiError(413, "file_too_large", "This file is larger than your plan allows.")
                if sha in fc.blobs.get(ws["id"], {}):
                    return {"sha256": sha, "stored": False}
                fc._assert_quota(ws, size)
                uid = fc._id("upl")
                parts = -(-size // fc.part_size)
                fc.uploads[uid] = {"user": u.id, "owner": ws["id"], "sha": sha, "size": size, "parts": parts,
                                   "partSize": fc.part_size, "type": b.get("contentType") or "application/octet-stream",
                                   "data": {}}
                return JSONResponse({"uploadId": uid, "partSize": fc.part_size, "parts": parts}, status_code=201)

        @app.put("/api/v1/uploads/{uid}/parts/{n}")
        async def upload_part(uid: str, n: int, request: Request):
            u, _ = user_of(request)
            up = fc.uploads.get(uid)
            if up is None or up["user"] != u.id:
                raise ApiError(404, "not_found", "That upload was not found.")
            if not 1 <= n <= up["parts"]:
                raise ApiError(400, "bad_request", f"Part numbers run from 1 to {up['parts']}.")
            data = await request.body()
            if not data:
                raise ApiError(400, "bad_request", "The request has no body.")
            etag = hashlib.md5(data).hexdigest()
            up["data"][n] = (etag, data)
            return {"partNumber": n, "etag": etag}

        @app.post("/api/v1/uploads/{uid}/complete")
        async def upload_complete(uid: str, request: Request):
            u, _ = user_of(request)
            b = await request.json()
            with fc.lock:
                up = fc.uploads.get(uid)
                if up is None or up["user"] != u.id:
                    raise ApiError(404, "not_found", "That upload was not found.")
                try:
                    data = b"".join(up["data"][p["partNumber"]][1] for p in sorted(b["parts"], key=lambda p: p["partNumber"])
                                    if up["data"][p["partNumber"]][0] == p["etag"])
                except KeyError:
                    data = b""
                del fc.uploads[uid]
                if len(data) != up["size"] or hashlib.sha256(data).hexdigest() != up["sha"]:
                    raise ApiError(422, "hash_mismatch", "The uploaded bytes do not match the hash.")
                store_blob(up["owner"], up["sha"], data, up["type"])
                fc.log.append({"kind": "upload", "owner": up["owner"], "sha256": up["sha"], "size": len(data),
                               "method": "multipart", "parts": len(b["parts"])})
                return {"sha256": up["sha"], "size": len(data), "stored": True}

        @app.get("/api/v1/blobs/{sha}")
        def get_blob(sha: str, request: Request, owner: str | None = None):
            u, _ = user_of(request)
            if not owner:
                raise ApiError(400, "bad_request", "Add ?owner=<workspace id>.")
            with fc.lock:
                ws = fc.workspace(u, owner)
                blob = fc.blobs.get(ws["id"], {}).get(sha)
                if blob is None:
                    raise ApiError(404, "not_found", "That file was not found.")
                data = blob["data"]
                if fc.corrupt_downloads.get(sha, 0) > 0:
                    fc.corrupt_downloads[sha] -= 1
                    data = bytes([data[0] ^ 0xFF]) + data[1:] if data else b"x"
                    fc.log.append({"kind": "corrupted", "sha256": sha})
                drop = sha in fc.drop_downloads
                fc.drop_downloads.discard(sha)
                fc.log.append({"kind": "download", "owner": ws["id"], "sha256": sha})
            headers = {"etag": f'"{sha}"', "accept-ranges": "bytes", "cache-control": "private, max-age=31536000, immutable"}
            rng = request.headers.get("range")
            if rng and rng.startswith("bytes="):
                a, _, bpart = rng[6:].partition("-")
                start = int(a or 0)
                end = int(bpart) if bpart else len(data) - 1
                chunk = data[start:end + 1]
                headers["content-range"] = f"bytes {start}-{start + len(chunk) - 1}/{len(data)}"
                return Response(chunk, status_code=206, media_type=blob["type"], headers=headers)
            if drop:
                async def broken():
                    yield data[: max(1, len(data) // 2)]
                    raise ConnectionResetError("dropped by the fake cloud")

                from starlette.responses import StreamingResponse

                fc.log.append({"kind": "dropped", "sha256": sha})
                return StreamingResponse(broken(), media_type=blob["type"],
                                         headers={**headers, "content-length": str(len(data))})
            return Response(data, media_type=blob["type"], headers=headers)

        # ---- library ------------------------------------------------------------------------------------------------

        def item_json(it: dict) -> dict:
            return {**it, "createdAt": _iso(it["createdAt"]), "updatedAt": _iso(it["updatedAt"]), "deletedAt": None}

        @app.get("/api/v1/library")
        def library_list(request: Request, owner: str | None = None, kind: str | None = None, q: str | None = None):
            u, _ = user_of(request)
            with fc.lock:
                spaces = [fc.workspace(u, owner)] if owner else [fc.workspace(u, None)] + \
                    [fc.workspace(u, t.id) for t in fc._teams_of(u.id)]
                ids = {w["id"]: w for w in spaces}
                rows = [it for it in fc.library.values() if it["ownerId"] in ids and not it.get("_deleted")
                        and (not kind or it["kind"] == kind)
                        and (not q or q.lower() in it["name"].lower() or q.lower() in (it["description"] or "").lower())]
                rows.sort(key=lambda it: it["updatedAt"], reverse=True)
                return {"items": [{**item_json(it), "creatorName": fc.users[it["createdBy"]].name,
                                   "workspace": fc._public_ws(ids[it["ownerId"]])} for it in rows[:500]]}

        def new_item(u: User, ws: dict, b: dict, files: list[dict], source: str | None) -> dict:
            now = fc.now()
            thumb = None
            for pat in (r"preview\.gif$", r"side-e\.png$", r"sheet\.png$", r"\.(png|gif|webp|jpe?g)$"):
                thumb = next((f["sha256"] for f in files if re.search(pat, f["path"])), None)
                if thumb:
                    break
            it = {"id": fc._id("lib"), "ownerType": ws["type"], "ownerId": ws["id"], "name": str(b["name"]).strip(),
                  "kind": b["kind"], "description": b.get("description"), "tags": b.get("tags") or [],
                  "thumbnailSha": thumb, "fileCount": len(files), "totalBytes": sum(f["size"] for f in files),
                  "sourceProjectId": source, "createdBy": u.id, "createdAt": now, "updatedAt": now}
            fc.library[it["id"]] = it
            fc.library_files[it["id"]] = [{"path": f["path"], "sha256": f["sha256"], "size": f["size"]} for f in files]
            return it

        KINDS = ("character", "animation", "effect", "vehicle", "machine", "tileset", "other")

        @app.post("/api/v1/library")
        async def library_create(request: Request):
            u, _ = user_of(request)
            b = await request.json()
            with fc.lock:
                ws = fc.workspace(u, b.get("owner", "me"))
                fc._require_write(u, ws)
                fc._require_library(ws)
                if b.get("kind") not in KINDS:
                    raise ApiError(400, "invalid_request", "kind: Invalid option")
                files = b.get("files") or []
                if not files or any(not _valid_path(f["path"]) for f in files):
                    raise ApiError(400, "bad_request", "Some file paths are not valid.")
                missing = [f["sha256"] for f in files if f["sha256"] not in fc.blobs.get(ws["id"], {})]
                if missing:
                    raise ApiError(400, "bad_request", "Upload these files first.", missing=missing)
                it = new_item(u, ws, b, files, b.get("sourceProjectId"))
                fc.log.append({"kind": "library_create", "item": it["id"], "files": len(files)})
                return JSONResponse({"item": item_json(it)}, status_code=201)

        @app.post("/api/v1/library/from-project")
        async def library_from_project(request: Request):
            u, _ = user_of(request)
            b = await request.json()
            with fc.lock:
                p = fc.projects.get(b.get("projectId", ""))
                if p is None or p["deletedAt"]:
                    raise ApiError(404, "not_found", "That project was not found.")
                src = fc.workspace(u, p["ownerId"])
                ws = fc.workspace(u, b.get("owner") or p["ownerId"])
                fc._require_write(u, ws)
                fc._require_library(ws)
                prefix = str(b.get("prefix", "")).lstrip("/")
                prefix = prefix if prefix.endswith("/") else prefix + "/"
                rows = [r for r in fc.files[p["id"]].values() if not r["deleted"] and r["path"].startswith(prefix)]
                if not rows:
                    raise ApiError(400, "bad_request", "That folder has no synced files.")
                if len(rows) > 500:
                    raise ApiError(400, "bad_request", "A library item holds up to 500 files.")
                if ws["id"] != src["id"]:
                    fc._copy_blobs(src["id"], ws["id"], [r["sha256"] for r in rows])
                files = [{"path": r["path"][len(prefix):], "sha256": r["sha256"], "size": r["size"]} for r in rows]
                it = new_item(u, ws, b, files, p["id"])
                fc.log.append({"kind": "library_from_project", "item": it["id"], "prefix": prefix,
                               "project": p["id"], "owner": ws["id"]})
                return JSONResponse({"item": item_json(it)}, status_code=201)

        @app.get("/api/v1/library/{iid}")
        def library_item(iid: str, request: Request):
            u, _ = user_of(request)
            with fc.lock:
                it = fc.library.get(iid)
                if it is None or it.get("_deleted"):
                    raise ApiError(404, "not_found", "That library item was not found.")
                ws = fc.workspace(u, it["ownerId"])
                return {"item": {**item_json(it), "workspace": {**fc._public_ws(ws),
                                                                "canWrite": ws["canWrite"] and ws["library"],
                                                                "canManage": ws["canManage"]}},
                        "files": fc.library_files[iid]}

        @app.post("/api/v1/library/{iid}/copy")
        async def library_copy(iid: str, request: Request):
            u, _ = user_of(request)
            b = await request.json()
            with fc.lock:
                it = fc.library.get(iid)
                if it is None:
                    raise ApiError(404, "not_found", "That library item was not found.")
                src = fc.workspace(u, it["ownerId"])
                ws = fc.workspace(u, b.get("owner"))
                fc._require_write(u, ws)
                fc._require_library(ws)
                files = fc.library_files[iid]
                fc._copy_blobs(src["id"], ws["id"], [f["sha256"] for f in files])
                copy = new_item(u, ws, it, files, it["sourceProjectId"])
                return JSONResponse({"item": item_json(copy)}, status_code=201)

        return app

    # -- helpers used by routes ---------------------------------------------------------------------------------

    def _date(self) -> dict:
        import email.utils

        return {"date": email.utils.formatdate(self.now(), usegmt=True)}

    def _copy_blobs(self, src: str, dst: str, shas: list[str]) -> None:
        held = self.blobs.setdefault(dst, {})
        need = {s for s in shas if s and s not in held}
        needed = sum(self.blobs[src][s]["size"] for s in need)
        self._assert_quota(self._ws_by_id(dst), needed)
        for s in need:
            held[s] = self.blobs[src][s]

    def _ws_by_id(self, owner: str) -> dict:
        if owner in self.users:
            return self.workspace(self.users[owner], owner)
        t = self.teams[owner]
        return self.workspace(self.users[next(iter(t.members))], owner)

    def _commit(self, u: User, d: Device, pid: str, b: dict) -> Any:
        p = self.projects.get(pid)
        if p is None or p["deletedAt"]:
            raise ApiError(404, "not_found", "That project was not found.")
        ws = self.workspace(u, p["ownerId"])
        changes = b.get("changes") or []
        if not 1 <= len(changes) <= 400:
            raise ApiError(400, "invalid_request", "changes: Too big" if changes else "changes: Too small")
        self._require_write(u, ws)
        paths = [c["path"] for c in changes]
        if len(set(paths)) != len(paths):
            raise ApiError(400, "bad_request", "Each path can appear once per commit.")
        bad = [x for x in paths if not _valid_path(x)]
        if bad:
            raise ApiError(400, "bad_request", "Some paths are not valid project paths.", paths=bad[:20])
        force = set(b.get("force") or [])
        base = int(b.get("baseRev", 0))
        rows = self.files[pid]
        todo, skipped, conflicts = [], 0, []
        for c in changes:
            cur = rows.get(c["path"])
            if c.get("deleted"):
                same = cur is None or cur["deleted"]
            else:
                same = cur is not None and not cur["deleted"] and cur["sha256"] == c["sha256"]
            if same:
                skipped += 1
                continue
            if cur is not None and cur["rev"] > base and c["path"] not in force:
                conflicts.append({"path": c["path"], "remote": {"sha256": cur["sha256"], "size": cur["size"],
                                                                "rev": cur["rev"], "deleted": cur["deleted"]}})
                continue
            todo.append(c)
        if conflicts:
            self.log.append({"kind": "conflict", "project": pid, "paths": [c["path"] for c in conflicts]})
            raise ApiError(409, "conflict", "Some files changed on the server since your last sync.",
                           conflicts=conflicts, headRev=p["headRev"])
        if not todo:
            return {"rev": p["headRev"], "applied": 0, "skipped": skipped}
        missing = sorted({c["sha256"] for c in todo if not c.get("deleted")} - set(self.blobs.get(ws["id"], {})))
        if missing:
            raise ApiError(400, "missing_blobs", "Upload these files before committing.", missing=missing[:200])
        if self.retry_commits > 0:
            self.retry_commits -= 1
            self.log.append({"kind": "retry_injected", "project": pid})
            raise ApiError(409, "retry", "Another machine committed at the same moment. Fetch changes and try again.",
                           headRev=p["headRev"] + 1)
        rev = p["headRev"] + 1
        now = self.now()
        added = modified = removed = 0
        for c in todo:
            cur = rows.get(c["path"])
            if c.get("deleted"):
                removed += 1
                rows[c["path"]] = {"path": c["path"], "sha256": None, "size": 0, "mtime": None, "rev": rev,
                                   "deleted": True, "updatedAt": now, "updatedBy": u.id}
            else:
                if cur is None or cur["deleted"]:
                    added += 1
                else:
                    modified += 1
                rows[c["path"]] = {"path": c["path"], "sha256": c["sha256"], "size": c["size"], "mtime": c.get("mtime"),
                                   "rev": rev, "deleted": False, "updatedAt": now, "updatedBy": u.id}
            r = rows[c["path"]]
            self.versions[pid].append({"path": r["path"], "rev": rev, "sha256": r["sha256"], "size": r["size"],
                                       "createdAt": now, "userName": u.name})
        live = [r for r in rows.values() if not r["deleted"]]
        p.update(headRev=rev, fileCount=len(live), totalBytes=sum(r["size"] for r in live), updatedAt=now)
        if not p["thumbnailSha"]:
            for pat in ("/final/preview.gif", "/ref/side-e.png", "/final/sheet.png"):
                hit = next((c["sha256"] for c in todo if not c.get("deleted") and c["path"].endswith(pat)), None)
                if hit:
                    p["thumbnailSha"] = hit
                    break
        self.revisions[pid].append({"rev": rev, "message": b.get("message"), "added": added, "modified": modified,
                                    "removed": removed, "createdAt": now, "userId": u.id, "userName": u.name,
                                    "deviceId": d.id})
        self.log.append({"kind": "commit", "project": pid, "rev": rev, "paths": [c["path"] for c in todo],
                         "force": sorted(force), "message": b.get("message"), "device": d.id})
        return {"rev": rev, "applied": len(todo), "skipped": skipped, "added": added, "modified": modified,
                "removed": removed}
