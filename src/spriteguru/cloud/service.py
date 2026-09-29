"""The engine's cloud state (cloud plan 3): one session for the engine's lifetime, the pending
sign-in, and the cached account (`/me`, 5 minutes). Every method is synchronous; the API calls them
in a worker thread. Status payloads never carry a token (P7).
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from . import access, config
from .auth import SignIn, sign_out
from .client import CloudError, Offline, Session, SignedOut

ME_TTL = 300.0


class CloudService:
    def __init__(self, publish: Callable[[dict], None], session: Session | None = None):
        self.publish = publish
        self._session: Session | None = session
        self._signin: SignIn | None = SignIn(session) if session is not None else None
        self._me: dict | None = None
        self._me_at = 0.0
        self._me_error: dict | None = None
        self._lock = threading.Lock()
        self._last_access: tuple | None = None

    # -- plumbing -------------------------------------------------------------------------------

    @property
    def session(self) -> Session:
        with self._lock:
            if self._session is None:
                self._session = Session(on_event=self._event)
                self._signin = SignIn(self._session)
            return self._session

    @property
    def signin(self) -> SignIn:
        self.session
        return self._signin  # type: ignore[return-value]

    def _event(self, e: dict) -> None:
        if e.get("type") == "cloud_account" and not e.get("signed_in"):
            self._me, self._me_at, self._last_access = None, 0.0, None
        self.publish(e)

    def _announce_access(self, d: dict) -> None:
        """Tell the studio when the access state changes (a purchase, the trial ending, going offline)."""
        key = (d["allowed"], d["status"], d["reason"], d["trial_days_left"])
        if key != self._last_access:
            self._last_access = key
            self.publish({"type": "cloud_access", "allowed": d["allowed"], "status": d["status"], "reason": d["reason"]})

    # -- account ------------------------------------------------------------------------------------

    def me(self, force: bool = False) -> dict | None:
        s = self.session
        if not s.signed_in:
            return None
        if not force and self._me is not None and time.monotonic() - self._me_at < ME_TTL:
            return self._me
        try:
            self._me = s.get("/api/v1/me")
            self._me_at = time.monotonic()
            self._me_error = None
        except SignedOut:
            return None
        except (Offline, CloudError) as e:
            self._me_error = {"code": e.code, "message": e.message}
        return self._me

    def invalidate_me(self) -> None:
        """After a 402 or 413 the plan, limits or storage may have changed: re-read /me next time."""
        self._me_at = 0.0

    def access(self, force: bool = False) -> dict:
        d = access.check(self.session, force=force)
        self._announce_access(d)
        return d

    def require(self, mode: str | None) -> None:
        if mode == "synthetic":
            return
        d = self.access()
        if not d["allowed"]:
            raise access.AccessRequired(d)

    def status(self, *, refresh: bool = False) -> dict:
        s = self.session
        me = self.me(force=refresh) if s.signed_in else None
        access_now = self.access(force=refresh)
        signed_in = s.signed_in  # after the calls: any of them may have signed this machine out (C4, C6)
        acct = s.account or {}
        pending = self.signin.current()
        user = (me or {}).get("user") or {"id": acct.get("user_id"), "name": acct.get("name"),
                                         "email": acct.get("email")}
        return {
            "signed_in": signed_in,
            "persisted": s.store.persisted,
            "cloud_url": s.base,
            "user": user if signed_in else None,
            "machine": {"id": config.machine_id(), "name": config.device_name(), "device_id": acct.get("device_id")},
            "access": access_now,
            "personal": (me or {}).get("personal") if signed_in else None,
            "teams": (me or {}).get("teams", []) if signed_in else [],
            "account_error": self._me_error if signed_in else None,
            "pending_sign_in": {"url": pending.url, "expires_in": pending.expires_in} if pending else None,
            "update": self.update(force=refresh),
        }

    def update(self, force: bool = False) -> dict | None:
        from . import releases

        return releases.check(self.session, force=force)

    # -- sign-in ---------------------------------------------------------------------------------------

    def start_sign_in(self, redirect_uri: str) -> dict:
        p = self.signin.start(redirect_uri)
        return {"authorize_url": p.url, "expires_in": p.expires_in}

    def complete_sign_in(self, params: dict) -> str:
        result = self.signin.complete(params)
        if result == "signed-in":
            self.me(force=True)
            self.access(force=False)
        return result

    def sign_out(self) -> None:
        self.signin.cancel()
        sign_out(self.session)

    def rename_machine(self, name: str) -> dict:
        from .. import library

        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("a machine name needs 1 to 80 characters")
        s = self.session
        device = (s.account or {}).get("device_id")
        if s.signed_in and device:
            s.patch(f"/api/v1/devices/{device}", {"name": name})
        library.set_state(device_name=name)
        return {"name": name}
