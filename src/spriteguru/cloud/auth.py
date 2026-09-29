"""Browser sign-in (cloud plan 4.1): OAuth 2.0 authorization code with PKCE (S256) and a loopback
redirect. The engine serves the callback on its own port; the CLI without an engine runs a one-shot
listener on 127.0.0.1:0 (C3).

A pending sign-in lives 10 minutes and its `state` works once. A callback with an unknown or reused
`state`, or an `iss` that isn't the configured cloud, is refused without calling the token endpoint
(C2). Nothing from the callback's query is logged.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from .. import __version__
from . import config
from .client import CloudError, Offline, Session, server_time

PENDING_TTL = 600.0


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)  # 64 characters
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


@dataclass
class Pending:
    state: str
    verifier: str
    redirect_uri: str
    url: str
    started: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.started > PENDING_TTL

    @property
    def expires_in(self) -> int:
        return max(0, int(PENDING_TTL - (time.monotonic() - self.started)))


class SignIn:
    """At most one pending sign-in; a second start replaces the first."""

    def __init__(self, session: Session):
        self.session = session
        self.pending: Pending | None = None
        self._lock = threading.Lock()
        self.done = threading.Event()
        self.result: str | None = None

    def start(self, redirect_uri: str) -> Pending:
        verifier, challenge = pkce()
        state = secrets.token_urlsafe(24)
        q = {"response_type": "code", "client_id": config.CLIENT_ID, "redirect_uri": redirect_uri,
             "code_challenge": challenge, "code_challenge_method": "S256", "state": state, "scope": "profile sync",
             "machine_id": config.machine_id(), "device_name": config.device_name(),
             "platform": config.platform_name(), "app_version": __version__}
        p = Pending(state, verifier, redirect_uri, f"{self.session.base}/api/oauth/authorize?{urlencode(q)}")
        with self._lock:
            self.pending = p
            self.done.clear()
            self.result = None
        return p

    def current(self) -> Pending | None:
        """The pending sign-in, so the studio can offer "Open the browser again" (C1)."""
        with self._lock:
            if self.pending is not None and self.pending.expired:
                self.pending = None
            return self.pending

    def cancel(self) -> None:
        with self._lock:
            self.pending = None

    def complete(self, params: dict) -> str:
        """Handle the browser's return: "signed-in", "cancelled", "invalid" (C2) or "failed"."""
        state = params.get("state") or ""
        with self._lock:
            p = self.pending
            if p is None or p.expired or not state or not secrets.compare_digest(state, p.state):
                return "invalid"  # an unknown state never cancels the real pending sign-in
            self.pending = None  # one use
        if params.get("error"):
            return self._finish("cancelled")
        if params.get("iss") != self.session.base or not params.get("code"):
            return self._finish("invalid")
        try:
            r = self.session.http.post(f"{self.session.base}/api/oauth/token", data={
                "grant_type": "authorization_code", "client_id": config.CLIENT_ID, "code": params["code"],
                "code_verifier": p.verifier, "redirect_uri": p.redirect_uri})
        except httpx.TransportError:
            return self._finish("failed")
        if r.status_code >= 400:
            return self._finish("failed")
        self.session.adopt(r.json(), server_time(r))
        self.session.on_event({"type": "cloud_account", "signed_in": True})
        return self._finish("signed-in")

    def _finish(self, result: str) -> str:
        self.result = result
        self.done.set()
        return result


def browser_redirect(base: str, result: str) -> str:
    """Where the browser goes after the callback: the website's own "you can close this tab" page."""
    return f"{base}/authorize?result={'signed-in' if result == 'signed-in' else 'cancelled'}"


INVALID_PAGE = ("<!doctype html><meta charset=utf-8><title>SpritePlay</title>"
                "<body style='font-family:system-ui;margin:3rem'><h1>This sign-in link has expired</h1>"
                "<p>Start sign-in again from the SpritePlay app.</p></body>")


def sign_out(session: Session) -> None:
    """Revoke the refresh token on the server (best effort), then forget it here (plan 10)."""
    rt = session.store.refresh_token
    if rt:
        try:
            session.http.post(f"{session.base}/api/oauth/revoke", data={"token": rt, "client_id": config.CLIENT_ID})
        except httpx.TransportError:
            pass
    session.forget("signed_out")


def loopback_login(session: Session, *, open_browser: bool = True, timeout: float = PENDING_TTL,
                   announce=None) -> str:
    """CLI sign-in without an engine (C3): a one-shot listener on 127.0.0.1:0 serves the callback,
    then closes."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlsplit

    flow = SignIn(session)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parts = urlsplit(self.path)
            if parts.path != "/auth/callback":
                self.send_response(404)
                self.end_headers()
                return
            params = {k: v[0] for k, v in parse_qs(parts.query).items()}
            result = flow.complete(params)
            if result == "invalid":
                body = INVALID_PAGE.encode()
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(302)
            self.send_header("Location", browser_redirect(session.base, result))
            self.end_headers()

        def log_message(self, *args):  # the query carries the code: log nothing
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    pending = flow.start(f"http://127.0.0.1:{port}/auth/callback")
    if announce:
        announce(pending.url)
    if open_browser:
        import webbrowser

        webbrowser.open(pending.url)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        flow.done.wait(timeout)
    finally:
        server.shutdown()
        server.server_close()
    return flow.result or "timeout"


__all__ = ["SignIn", "Pending", "sign_out", "loopback_login", "browser_redirect", "INVALID_PAGE", "CloudError",
           "Offline"]
