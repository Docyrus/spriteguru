"""The local sync index (cloud plan 6.1): the baseline both sides last agreed on, and a hash cache.

`base` maps a path to the {sha256, size, rev} the cloud and this folder last agreed on. `cache`
maps a path to {sha256, size, mtime_ns}: a hash is reused while the file's size and mtime_ns are
unchanged (C21), so a rescan only stats. mtimes are a local cache key only and are never compared
across machines (C20).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..project import read_json, write_json
from .scope import Scope

CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class Local:
    sha256: str
    size: int
    mtime_ns: int


class Index:
    def __init__(self, path: Path):
        self.path = path
        data = read_json(path) if path.is_file() else {}
        self.base: dict[str, dict] = data.get("base", {})
        self.cache: dict[str, dict] = data.get("cache", {})
        self.hashed = 0  # files hashed by the last scan (the rest came from the cache)

    def save(self) -> None:
        write_json(self.path, {"base": self.base, "cache": self.cache})

    def scan(self, scope: Scope) -> tuple[dict[str, Local], list[dict]]:
        """Every synced file with its hash, reusing cached hashes while size and mtime_ns hold."""
        files, findings = scope.files()
        out: dict[str, Local] = {}
        self.hashed = 0
        for rel, st in files.items():
            c = self.cache.get(rel)
            if c and c["size"] == st.st_size and c["mtime_ns"] == st.st_mtime_ns:
                out[rel] = Local(c["sha256"], st.st_size, st.st_mtime_ns)
                continue
            try:
                sha = sha256_file(scope.root / rel)
                st2 = os.stat(scope.root / rel)
            except OSError:
                continue
            self.hashed += 1
            if (st2.st_size, st2.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                continue  # changing while it was read (an export in progress): next round (C7)
            out[rel] = Local(sha, st.st_size, st.st_mtime_ns)
            self.cache[rel] = {"sha256": sha, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        for rel in list(self.cache):
            if rel not in files:
                del self.cache[rel]
        return out, findings

    def agree(self, rel: str, sha: str | None, size: int, rev: int) -> None:
        """Record what both sides now hold for a path (None: deleted on both)."""
        if sha is None:
            self.base.pop(rel, None)
        else:
            self.base[rel] = {"sha256": sha, "size": size, "rev": rev}

    def base_sha(self, rel: str) -> str | None:
        b = self.base.get(rel)
        return b["sha256"] if b else None
