"""What syncs (cloud plan 6.2): everything in the project folder except the project's `.gitignore`
patterns, the machine-local `.spriteplay/` (and `.spriteguru/`, its SpriteGuru-era name, N6), the
per-machine ledger (C12), temporary and OS files, and symlinks. Paths are relative with forward
slashes on every OS (C18); a path that differs from another only in case is refused (C17).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pathspec

MACHINE_LOCAL = (".spriteplay", ".spriteguru")
FIXED_EXCLUDES = [*(f"{d}/" for d in MACHINE_LOCAL), "/ledger.jsonl", "*.tmp", ".DS_Store", "Thumbs.db", "desktop.ini"]


def _spec(root: Path) -> pathspec.PathSpec:
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.is_file() else []
    return pathspec.PathSpec.from_lines("gitwildmatch", FIXED_EXCLUDES + lines)


def valid_path(rel: str) -> bool:
    """A project path both sides accept: relative, forward slashes, no empty, `.` or `..` segment."""
    if not rel or len(rel) > 512 or rel.startswith("/") or "\\" in rel or "\x00" in rel:
        return False
    return all(seg not in ("", ".", "..") for seg in rel.split("/"))


@dataclass
class Scope:
    root: Path
    spec: pathspec.PathSpec = field(init=False)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.spec = _spec(self.root)

    def includes(self, rel: str) -> bool:
        """Whether a (remote or local) path belongs to the synced scope."""
        return valid_path(rel) and not self.spec.match_file(rel) and not rel.startswith(tuple(f"{d}/" for d in MACHINE_LOCAL))

    def files(self) -> tuple[dict[str, os.stat_result], list[dict]]:
        """Every file in scope with its stat, plus findings for paths that can't sync."""
        out: dict[str, os.stat_result] = {}
        folded: dict[str, str] = {}
        findings: list[dict] = []
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            rel_dir = PurePosixPath(Path(dirpath).relative_to(self.root).as_posix())
            keep = []
            for d in sorted(dirnames):
                rd = "" if str(rel_dir) == "." else f"{rel_dir}/"
                full = Path(dirpath) / d
                if full.is_symlink() or self.spec.match_file(f"{rd}{d}/") or f"{rd}{d}" in MACHINE_LOCAL:
                    continue
                keep.append(d)
            dirnames[:] = keep
            for name in sorted(filenames):
                rel = name if str(rel_dir) == "." else f"{rel_dir}/{name}"
                full = Path(dirpath) / name
                if full.is_symlink() or self.spec.match_file(rel):
                    continue
                if not valid_path(rel):
                    findings.append({"code": "invalid_path", "path": rel,
                                     "message": f"{rel} can't sync: the name isn't a portable path."})
                    continue
                other = folded.get(rel.casefold())
                if other is not None:
                    findings.append({"code": "case_clash", "path": rel, "other": other,
                                     "message": f"{rel} and {other} differ only in case; {rel} doesn't sync."})
                    continue
                try:
                    st = full.stat()
                except OSError:
                    continue
                folded[rel.casefold()] = rel
                out[rel] = st
        return out, findings


GROUP_RE = re.compile(r"^(characters/[^/]+/|animations/[^/]+/|labels/)")


def group_of(rel: str) -> str:
    """Conflicts are resolved per asset (cloud plan 6.7): a character's folder, an animation's folder,
    the labels, project.json, or else the single file."""
    m = GROUP_RE.match(rel)
    return m.group(1) if m else rel
