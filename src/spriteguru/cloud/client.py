"""The signed-in session and every HTTP call to the cloud (cloud plan 3 and 4.3).

One Session per process: the engine keeps one for its lifetime, a CLI command makes its own. The
refresh token rotates on every use, so refreshes are single-flight within a process and run under
a lock in the library root across processes; the keychain is re-read inside that lock, so the
studio's engine and a CLI command never spend the same refresh token twice (C37). The new refresh
token reaches the keychain before the new access token is used (C4).
"""

from __future__ import annotations

import contextlib
import email.utils
import threading
import time
from typing import Any, Callable, Iterator

import httpx

from .. import __version__, library
from ..filelock import FileLock
from . import config
from .tokens import TokenStore

REFRESH_MARGIN = 300  # refresh when the access token has less than 5 minutes left


class CloudError(Exception):
    """An error from the cloud: `message` can be shown to the user as-is."""

    def __init__(self, code: str, message: str, status: int = 0, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    @classmethod
    def from_response(cls, r: httpx.Response) -> "CloudError":
        try:
            body = r.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            details = {k: v for k, v in err.items() if k not in ("code", "message")}
            return cls(str(err.get("code") or r.status_code), str(err.get("message") or r.reason_phrase), r.status_code,
                       details)
        if isinstance(err, str):  # RFC 6749 errors from the token endpoint
            return cls(err, str(body.get("error_description") or err), r.status_code)
        return cls(str(r.status_code), f"The SpritePlay server answered {r.status_code} {r.reason_phrase}", r.status_code)

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


class Offline(CloudError):
    def __init__(self, message: str = "The SpritePlay server can't be reached. Check your internet connection."):
        super().__init__("offline", message, 0)


class SignedOut(CloudError):
    def __init__(self, message: str = "Sign in to SpritePlay first.", reason: str = "signed_out"):
        super().__init__(reason, message, 401)


def server_time(r: httpx.Response) -> float | None:
    """The server's clock from the Date header, so a wrong local clock can't stretch access (C32)."""
    values = r.headers.get_list("date")
    try:
        d = email.utils.parsedate_to_datetime(values[0]) if values else None
    except (TypeError, ValueError, IndexError):
        return None
    # only a well-formed GMT date counts; a proxy that merges headers must not skew the clock
    return d.timestamp() if d is not None and d.tzinfo is not None else None


class Session:
    def __init__(self, on_event: Callable[[dict], None] | None = None, transport: httpx.BaseTransport | None = None):
        self.base = config.base_url()
        self.store = TokenStore()
        self.on_event = on_event or (lambda e: None)
        self.http = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), transport=transport,
                                 headers={"User-Agent": f"SpritePlay/{__version__}"})
        self._access_token: str | None = None
        self._expires = 0.0  # monotonic
        self._lock = threading.Lock()

    # -- state -------------------------------------------------------------------------

    @property
    def signed_in(self) -> bool:
        return bool(self.store.refresh_token)

    @property
    def account(self) -> dict | None:
        return self.store.account if self.signed_in else None

    def adopt(self, tokens: dict, served_at: float | None) -> None:
        """Take a token response: keychain first, then memory, then the access it carries."""
        from . import access

        account = self.store.account or {}
        user = tokens.get("user") or {}
        if user or tokens.get("device_id"):
            account = {**account, "user_id": user.get("id", account.get("user_id")),
                       "email": user.get("email", account.get("email")), "name": user.get("name", account.get("name")),
                       "device_id": tokens.get("device_id", account.get("device_id"))}
        self.store.save(tokens["refresh_token"], account)
        self._access_token = tokens["access_token"]
        self._expires = time.monotonic() + float(tokens.get("expires_in") or 3600)
        if isinstance(tokens.get("access"), dict):
            access.record(tokens["access"], served_at, partial=True)

    def forget(self, reason: str) -> None:
        """Signed out on this machine: keychain, memory and the access cache (C6)."""
        from . import access

        self.store.clear()
        self._access_token = None
        self._expires = 0.0
        access.clear()
        self.on_event({"type": "cloud_account", "signed_in": False, "reason": reason})

    # -- tokens ----------------------------------------------------------------------------

    def access_token(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._access_token and self._expires - time.monotonic() > REFRESH_MARGIN:
                return self._access_token
            self._refresh()
            return self._access_token  # type: ignore[return-value]

    def _refresh(self) -> None:
        with FileLock(library.root() / ".spriteplay-cloud.lock"):
            rt = self.store.refresh_token  # re-read inside the lock: another process may have rotated it (C37)
            if not rt:
                raise SignedOut()
            try:
                r = self.http.post(f"{self.base}/api/oauth/token", data={
                    "grant_type": "refresh_token", "client_id": config.CLIENT_ID, "refresh_token": rt,
                    "app_version": __version__})
            except httpx.TransportError:
                raise Offline()
            if r.status_code >= 400:
                err = CloudError.from_response(r)
                if err.code in ("invalid_grant", "device_revoked", "invalid_token"):
                    # the stored token itself was refused: nobody else can hold a newer one (C4)
                    self.forget("revoked" if err.code == "device_revoked" else "expired")
                    raise SignedOut("Your sign-in has ended. Sign in again.", "revoked" if err.code == "device_revoked"
                                    else "expired")
                raise err
            self.adopt(r.json(), server_time(r))

    # -- requests ------------------------------------------------------------------------------

    def request(self, method: str, path: str, *, auth: bool = True, **kw: Any) -> httpx.Response:
        """One call to the cloud. `401 invalid_token` refreshes and retries once (C5); `device_revoked`
        signs this machine out (C6); connection errors raise Offline (C19)."""
        headers = dict(kw.pop("headers", None) or {})
        for attempt in (0, 1):
            if auth:
                headers["Authorization"] = f"Bearer {self.access_token(force=attempt == 1)}"
            try:
                r = self.http.request(method, f"{self.base}{path}", headers=headers, **kw)
            except httpx.TransportError:
                raise Offline()
            if r.status_code == 401 and auth:
                err = CloudError.from_response(r)
                if err.code == "device_revoked":
                    self.forget("revoked")
                    raise SignedOut(err.message or "This machine was signed out on spriteplay.com.", "revoked")
                if err.code == "invalid_token" and attempt == 0:
                    continue
                raise err
            if r.status_code >= 400:
                raise CloudError.from_response(r)
            return r
        raise AssertionError("unreachable")

    @contextlib.contextmanager
    def stream(self, method: str, path: str, **kw: Any) -> Iterator[httpx.Response]:
        """A streamed call (downloads) with the same token handling as `request`. Transport errors
        while reading the body surface as httpx errors for the caller to treat as offline."""
        headers = dict(kw.pop("headers", None) or {})
        for attempt in (0, 1):
            headers["Authorization"] = f"Bearer {self.access_token(force=attempt == 1)}"
            try:
                r = self.http.send(self.http.build_request(method, f"{self.base}{path}", headers=headers, **kw),
                                   stream=True)
            except httpx.TransportError:
                raise Offline()
            if r.status_code == 401:
                r.read()
                r.close()
                err = CloudError.from_response(r)
                if err.code == "device_revoked":
                    self.forget("revoked")
                    raise SignedOut(err.message, "revoked")
                if err.code == "invalid_token" and attempt == 0:
                    continue
                raise err
            if r.status_code >= 400:
                r.read()
                r.close()
                raise CloudError.from_response(r)
            try:
                yield r
            finally:
                r.close()
            return

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw).json()

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        r = self.request("POST", path, json=body, **kw)
        return r.json() if r.content else None

    def patch(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("PATCH", path, json=body, **kw).json()

    def close(self) -> None:
        self.http.close()
