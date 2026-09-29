"""The access gate (cloud plan 4.4): sprites are created only while the account has access.

The server decides; this module caches its last answer in the library state with the local time it
arrived and the server's own clock (the Date header). Ages are measured as time elapsed since that
check, so moving the local clock back makes the cache stale instead of stretching it (C32), and a
trial ends at `trial.endsAt` on the server's clock even while the app stays offline (C35).

Synthetic mode spends nothing and makes placeholder frames, so it needs no access (plan 15.6).
"""

from __future__ import annotations

import datetime as dt
import math
import time

from .. import __version__, library

GRACE = 7 * 86400  # paid access and trials stay valid this long after the last successful check
FRESH = 3600  # older than this, a check runs before the next job
STATE_KEY = "cloud_access"

MESSAGES = {
    "signed_out": "Sign in to start your 7-day trial.",
    "trial_ended": "Your trial has ended. Your projects are safe; creating sprites needs the License or a plan.",
    "trial_available": "Your trial starts with the next access check.",
    "offline": "Connect to the internet to check your access.",
}


class AccessRequired(Exception):
    """Raised before anything that calls a provider or starts a job without access."""

    def __init__(self, decision: dict):
        super().__init__(decision["message"])
        self.decision = decision

    def payload(self) -> dict:
        d = self.decision
        return {"code": "access_required", "status": d["status"], "reason": d["reason"], "message": d["message"]}


def _iso_ts(v) -> float | None:
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _date(ts: float | None) -> str:
    if not ts:
        return ""
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    return f"{d:%b} {d.day}, {d.year}"


def record(data: dict, served_at: float | None, *, partial: bool = False) -> None:
    """Keep the server's answer. Token responses carry only five of its fields (no `versionAllowed`,
    `cloud` or `plan`), so they merge into the last full answer and ask for a full check soon."""
    prev = cached() if partial else None
    merged = {**(prev["data"] if prev else {}), **data}
    library.set_state(**{STATE_KEY: {"data": merged, "partial": partial, "checked_local": time.time(),
                                     "checked_server": served_at if served_at else time.time()}})


def cached() -> dict | None:
    c = library.get_state(STATE_KEY)
    return c if isinstance(c, dict) and isinstance(c.get("data"), dict) else None


def clear() -> None:
    library.set_state(**{STATE_KEY: None})


def decide(signed_in: bool, cache: dict | None, now: float | None = None) -> dict:
    """What the app may do now, from the cached answer alone (no network)."""
    now = time.time() if now is None else now
    base = {"allowed": False, "status": "signed_out", "reason": "signed_out", "message": MESSAGES["signed_out"],
            "stale": True, "checked_at": None, "trial_ends_at": None, "trial_days_left": None, "updates_until": None,
            "version_allowed": True, "cloud": False, "plan": None, "license": None, "version": __version__}
    if not signed_in:
        return base
    if cache is None:
        return {**base, "status": "unknown", "reason": "offline", "message": MESSAGES["offline"]}
    data = cache["data"]
    elapsed = now - float(cache.get("checked_local") or 0)
    server_now = float(cache.get("checked_server") or 0) + elapsed
    status = str(data.get("status") or "unknown")
    trial = data.get("trial") or {}
    ends = _iso_ts(trial.get("endsAt"))
    out = {**base, "status": status, "stale": elapsed > FRESH or elapsed < 0 or bool(cache.get("partial")),
           "checked_at": dt.datetime.fromtimestamp(float(cache["checked_local"])).isoformat(timespec="seconds"),
           "trial_ends_at": trial.get("endsAt"), "updates_until": data.get("updatesUntil"),
           "version_allowed": data.get("versionAllowed", True) is not False, "cloud": bool(data.get("cloud")),
           "plan": data.get("plan"), "license": (data.get("license") or {}).get("number")}
    if status == "trial" and ends:
        # a hair under whole days, so a fresh 7-day trial reads 7 despite the Date header's whole seconds
        out["trial_days_left"] = max(0, math.ceil((ends - server_now) / 86400 - 1e-3))

    def lock(reason: str, message: str, status_: str | None = None) -> dict:
        return {**out, "allowed": False, "reason": reason, "message": message, **({"status": status_} if status_ else {})}

    if elapsed < 0 or elapsed > GRACE:  # the clock went back (C32), or offline past the grace (C31)
        return lock("offline", MESSAGES["offline"])
    if status == "trial" and ends is not None and server_now >= ends:  # the trial ran out while the app was open (C35)
        return lock("not_allowed", MESSAGES["trial_ended"], "trial_ended")
    if not data.get("allowed"):
        return lock("not_allowed", MESSAGES.get(status, "Creating sprites needs the License or a plan."))
    if data.get("versionAllowed") is False:  # a License whose updates ended before this build (C33)
        until = _date(_iso_ts(data.get("updatesUntil")))
        return lock("version", f"This version was released after your updates ended on {until}.")
    return {**out, "allowed": True, "reason": "ok", "message": ""}


def check(session, *, force: bool = False) -> dict:
    """The decision, asking the server first when the cache is old (or `force`). Offline, the cached
    answer holds within the grace period."""
    from .client import CloudError, Offline, SignedOut, server_time

    if not session.signed_in:
        return decide(False, None)
    d = decide(True, cached())
    if force or d["stale"] or d["status"] == "unknown" or d["status"] == "trial_available":
        try:
            r = session.request("GET", "/api/v1/desktop/access", params={"version": __version__})
            record(r.json(), server_time(r))
        except SignedOut:
            return decide(False, None)
        except (Offline, CloudError):
            pass
        d = decide(session.signed_in, cached())
    return d


def require(session, mode: str | None) -> None:
    """Raise AccessRequired unless the account may create sprites now (synthetic mode always may)."""
    if mode == "synthetic":
        return
    d = check(session)
    if not d["allowed"]:
        raise AccessRequired(d)
