"""The update check (cloud plan 9): once a day, the newest build for this platform. Signed in it asks
`/api/v1/desktop/updates`, which knows the account's updates; signed out, the public
`/api/v1/releases/latest`. A newer `installable` build is offered with its download link; when the
newest build is newer still (a License whose updates ended), that is said with a link to billing.
There is no automatic installation.
"""

from __future__ import annotations

import re
import time

import httpx

from .. import __version__, library
from . import config
from .client import CloudError, Offline, Session, SignedOut

DAY = 86400
STATE_KEY = "cloud_update"


def vkey(v: str | None) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v or "0"))


def _absolute(base: str, url: str | None) -> str | None:
    if not url:
        return None
    return url if url.startswith(("http://", "https://")) else f"{base}{url}"


def check(session: Session, *, force: bool = False) -> dict | None:
    """What the app shows about updates, from a check at most a day old (None before any check)."""
    cached = library.get_state(STATE_KEY)
    signed_in = session.signed_in
    fresh = isinstance(cached, dict) and time.time() - cached.get("checked", 0) < DAY \
        and cached.get("signed_in") == signed_in and cached.get("current") == __version__ \
        and cached.get("server") == session.base  # a check from another server isn't reused (N16)
    if fresh and not force:
        return cached["result"]
    plat = config.release_platform()
    try:
        if signed_in:
            r = session.get("/api/v1/desktop/updates", params={"platform": plat, "channel": "stable"})
            latest, installable = r.get("latest"), r.get("installable")
        else:
            r = session.get("/api/v1/releases/latest", auth=False)
            latest = next((x for x in r.get("releases", []) if x.get("platform") == plat), None)
            installable = latest
    except (Offline, SignedOut, CloudError, httpx.HTTPError):
        return cached["result"] if isinstance(cached, dict) else None
    result = {
        "current": __version__,
        "available": bool(installable) and vkey(installable["version"]) > vkey(__version__),
        "version": installable["version"] if installable else None,
        "url": _absolute(session.base, installable.get("url")) if installable else None,
        "notes": installable.get("notes") if installable else None,
        "latest": latest["version"] if latest else None,
        # the newest build needs updates this account no longer has
        "needs_renewal": bool(latest) and (installable is None or vkey(latest["version"]) > vkey(installable["version"]))
        and vkey(latest["version"]) > vkey(__version__),
    }
    library.set_state(**{STATE_KEY: {"checked": time.time(), "signed_in": signed_in, "current": __version__,
                                     "server": session.base, "result": result}})
    return result
