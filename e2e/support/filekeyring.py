"""A file-backed keyring for the E2E runs (C38): one JSON file per library root, so each simulated
machine has its own keychain and no test ever reads or writes the user's real one. Selected with
PYTHON_KEYRING_BACKEND=filekeyring.FileKeyring (the harness sets it and puts this folder on the path)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class FileKeyring(KeyringBackend):
    priority = 1

    @staticmethod
    def _file() -> Path:
        root = Path(os.environ.get("SPRITEGURU_LIBRARY") or ".").expanduser()
        root.mkdir(parents=True, exist_ok=True)
        return root / ".test-keyring.json"

    def _load(self) -> dict:
        try:
            return json.loads(self._file().read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        tmp = self._file().with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self._file())

    def get_password(self, service, username):
        return self._load().get(service, {}).get(username)

    def set_password(self, service, username, password):
        data = self._load()
        data.setdefault(service, {})[username] = password
        self._save(data)

    def delete_password(self, service, username):
        data = self._load()
        if username not in data.get(service, {}):
            raise PasswordDeleteError(username)
        del data[service][username]
        self._save(data)
