"""Where the sign-in lives (cloud plan 4.3): the refresh token and the account summary in the OS
keychain (service `spriteplay-cloud`), the access token in memory only. A sign-in saved under the
SpriteGuru-era service `spriteguru-cloud` moves to the new one the first time it's read (N4).
Without a keychain backend (headless Linux) both stay in memory for the session and are never
written to a file (C26).

Nothing here logs a token or returns one to the API.
"""

from __future__ import annotations

import json

SERVICE = "spriteplay-cloud"
LEGACY_SERVICE = "spriteguru-cloud"


def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        kr = keyring.get_keyring()
        name = f"{type(kr).__module__}.{type(kr).__name__}"
        if isinstance(kr, fail.Keyring) or ".null." in name or name.endswith(".fail.Keyring"):
            return None
        return keyring
    except Exception:
        return None


class TokenStore:
    def __init__(self):
        self._mem: dict[str, str] = {}
        self._kr = _keyring()

    @property
    def persisted(self) -> bool:
        return self._kr is not None

    def get(self, name: str) -> str | None:
        if self._kr is None:
            return self._mem.get(name)
        try:
            value = self._kr.get_password(SERVICE, name)
            if value is None:
                value = self._kr.get_password(LEGACY_SERVICE, name)
                if value is not None:
                    self._move(name, value)
            return value
        except Exception:
            return self._mem.get(name)

    def _move(self, name: str, value: str) -> None:
        """N4: copy a SpriteGuru-era entry to the new service, then drop the old one. If the copy
        fails the old entry stays, and is read again next time."""
        try:
            self._kr.set_password(SERVICE, name, value)
        except Exception:
            return
        try:
            self._kr.delete_password(LEGACY_SERVICE, name)
        except Exception:
            pass

    def set(self, name: str, value: str) -> None:
        if self._kr is not None:
            try:
                self._kr.set_password(SERVICE, name, value)
                self._mem.pop(name, None)
                return
            except Exception:
                self._kr = None  # the backend refused: this session keeps tokens in memory (C26)
        self._mem[name] = value

    def delete(self, name: str) -> None:
        self._mem.pop(name, None)
        if self._kr is not None:
            for service in (SERVICE, LEGACY_SERVICE):  # signing out removes both (N4)
                try:
                    self._kr.delete_password(service, name)
                except Exception:
                    pass

    @property
    def refresh_token(self) -> str | None:
        return self.get("refresh_token")

    @property
    def account(self) -> dict | None:
        raw = self.get("account")
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def save(self, refresh_token: str, account: dict | None = None) -> None:
        """The refresh token first: it is the one thing a lost rotation cannot recover (C4)."""
        self.set("refresh_token", refresh_token)
        if account is not None:
            self.set("account", json.dumps(account))

    def clear(self) -> None:
        self.delete("refresh_token")
        self.delete("account")
