"""Content-addressed cache of provider outputs (4.3)."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .providers.base import Candidate


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, key: str) -> Path:
        return self.root / key[:2] / key

    def get(self, key: str) -> list[Candidate] | None:
        d = self._dir(key)
        meta_path = d / "meta.json"
        if not meta_path.is_file():
            return None
        meta = json.loads(meta_path.read_text())
        out = []
        for item in meta["outputs"]:
            p = d / item["file"]
            if not p.is_file():
                return None
            out.append(Candidate(p.read_bytes(), item["media_type"], dict(item.get("meta", {}))))
        return out

    def meta(self, key: str) -> dict[str, Any] | None:
        p = self._dir(key) / "meta.json"
        return json.loads(p.read_text()) if p.is_file() else None

    def put(self, key: str, candidates: list[Candidate], info: dict[str, Any]) -> None:
        d = self._dir(key)
        tmp = d.with_name(d.name + f".tmp{os.getpid()}")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        outputs = []
        for i, c in enumerate(candidates):
            name = f"out_{i}.{c.ext}"
            (tmp / name).write_bytes(c.data)
            outputs.append({"file": name, "media_type": c.media_type, "meta": c.meta})
        meta = {"key": key, "created": time.time(), "outputs": outputs, **info}
        (tmp / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        try:
            os.replace(tmp, d)
        except OSError:
            # another process stored the same key first (parallel jobs); the content is identical
            shutil.rmtree(tmp, ignore_errors=True)

    def keys(self) -> list[str]:
        return sorted(p.parent.name for p in self.root.glob("*/*/meta.json"))
