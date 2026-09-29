"""The project library (P1–P11): where projects live, the gallery listing and the recent list.

Projects are ordinary project folders (`<name>.sprites`); the library adds a default parent folder,
a recent list and a hidden list, kept in `<root>/.spriteplay-library.json`. The root is
`SPRITEKIT_LIBRARY` when set (tests), else `~/Documents/SpritePlay`, or `~/Documents/SpriteGuru` on a
machine that had the library before the rename (N1). The SpriteGuru-era state file and blob cache
in the root are renamed on first use (N2).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from .env import get as app_env
from .project import Project, ProjectError, local_dir, write_json
from .spec import Style

STATE = ".spriteplay-library.json"
LOCK = ".spriteplay-library.lock"
FOLDER, LEGACY_FOLDER = "SpritePlay", "SpriteGuru"
# SpriteGuru-era names in the library root: renamed once (N2), stale locks removed
LEGACY_NAMES = {".spriteguru-library.json": STATE, ".spriteguru-cache": ".spriteplay-cache"}
LEGACY_LOCKS = (".spriteguru-library.lock", ".spriteguru-cloud.lock")
_migrated: set[Path] = set()
LEGACY_LAST = Path.home() / ".config" / "spriteguru" / "last_project"


class LibraryError(ValueError):
    """Bad input (400)."""


class LibraryConflict(RuntimeError):
    """The request clashes with what exists (409)."""


def root() -> Path:
    env = app_env("LIBRARY")
    if env:
        r = Path(env).expanduser()
    else:
        docs = Path.home() / "Documents"
        parent = docs if docs.is_dir() else Path.home()
        r = parent / FOLDER
        if not r.exists() and (parent / LEGACY_FOLDER).is_dir():
            r = parent / LEGACY_FOLDER  # N1: an existing library stays where it is; nothing is moved
    r.mkdir(parents=True, exist_ok=True)
    r = r.resolve()
    _migrate(r)
    return r


def _migrate(r: Path) -> None:
    """Rename the root's SpriteGuru-era files once per process, under the library lock. If both names
    exist the new one wins and the old is left alone; a failure leaves the old name for this run (N13)."""
    if r in _migrated:
        return
    _migrated.add(r)
    todo = [(r / old, r / new) for old, new in LEGACY_NAMES.items() if (r / old).exists() and not (r / new).exists()]
    stale = [r / name for name in LEGACY_LOCKS if (r / name).exists()]
    if not todo and not stale:
        return
    from .filelock import FileLock

    try:
        with FileLock(r / LOCK):
            for old, new in todo:
                if old.exists() and not new.exists():
                    os.rename(old, new)
            for lock in stale:
                lock.unlink(missing_ok=True)
    except (OSError, TimeoutError) as e:
        print(f"spriteguru: couldn't rename the library's SpriteGuru files in {r}: {e}", file=sys.stderr, flush=True)


def project_id(path: Path | str) -> str:
    return hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:12]


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _load() -> dict:
    p = root() / STATE
    try:
        data = json.loads(p.read_text()) if p.is_file() else {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("recent", [])
    data.setdefault("hidden", [])
    return data


def _save(data: dict) -> None:
    write_json(root() / STATE, data)


def get_state(key: str, default=None):
    """A value this machine keeps in the library state (the cloud's machine id and access cache)."""
    return _load().get(key, default)


def set_state(**values) -> None:
    """Update library state values (None removes one); processes take turns, so none loses another's write."""
    from .filelock import FileLock

    with FileLock(root() / LOCK):
        data = _load()
        for k, v in values.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        _save(data)


def _candidates() -> list[Path]:
    """Every folder the gallery may show: recent entries, projects in the root, the legacy last project."""
    data = _load()
    seen: dict[str, Path] = {}
    for e in data["recent"]:
        seen.setdefault(str(Path(e["path"])), Path(e["path"]))
    r = root()
    for pj in sorted(list(r.glob("*/project.json"))):
        seen.setdefault(str(pj.parent.resolve()), pj.parent.resolve())
    if not app_env("LIBRARY") and LEGACY_LAST.is_file():  # P11
        try:
            legacy = Path(LEGACY_LAST.read_text().strip())
            seen.setdefault(str(legacy.resolve()), legacy.resolve())
        except OSError:
            pass
    hidden = set(data["hidden"])
    return [p for k, p in seen.items() if k not in hidden]


def _thumbnail(p: Project) -> Path | None:
    """The active character's view, else the first approved character's, else the newest export."""
    chars = p.characters()
    active = p.config.active_character
    order = sorted(chars, key=lambda c: (c.name != active, not c.approved))
    for c in order:
        for view in ("side-e", "key", "front"):
            rel = c.views.get(view)
            if rel and (p.root / rel).is_file():
                return p.root / rel
    finals = sorted((p.root / "animations").glob("*/final/frames/000.png"), key=lambda f: f.stat().st_mtime)
    return finals[-1] if finals else None


def _updated(path: Path) -> str:
    stamps = [path / "project.json", path / "characters", path / "animations"]
    t = max((s.stat().st_mtime for s in stamps if s.exists()), default=0.0)
    return dt.datetime.fromtimestamp(t).isoformat(timespec="seconds") if t else ""


def card(path: Path, current: Path | None = None) -> dict:
    path = Path(path)
    pid = project_id(path)
    opened = {str(Path(e["path"])): e.get("last_opened") for e in _load()["recent"]}.get(str(path))
    base = {"id": pid, "name": path.name.removesuffix(".sprites"), "path": str(path), "style": None, "engine": None,
            "cloud": None,
            "characters": 0, "animations": 0, "thumbnail": None, "updated": "", "last_opened": opened,
            "missing": False, "error": None, "current": current is not None and Path(current) == path}
    if not (path / "project.json").is_file():
        return {**base, "missing": True}
    try:
        p = Project.open(path, migrate=False)
    except Exception as e:  # P7: one broken project never breaks the gallery
        return {**base, "error": f"unreadable project.json: {type(e).__name__}", "updated": _updated(path)}
    thumb = _thumbnail(p)
    return {**base, "name": p.config.name, "style": p.config.style.kind, "engine": p.config.engine,
            "cloud": _cloud_card(path, p),
            "characters": len(list((path / "characters").glob("*/character.json"))),
            "animations": len(list((path / "animations").glob("*/spec.json"))),
            "thumbnail": f"/api/library/projects/{pid}/thumbnail" if thumb else None, "updated": _updated(path)}


def _cloud_card(path: Path, p: Project) -> dict | None:
    """What the gallery card shows about syncing: linked, open conflicts, a pause."""
    if p.config.cloud is None:
        return None
    sync = local_dir(path, migrate=False) / "sync"  # the gallery only reads (N5)
    try:
        conflicts = json.loads((sync / "conflicts.json").read_text()) if (sync / "conflicts.json").is_file() else []
        state = json.loads((sync / "state.json").read_text()) if (sync / "state.json").is_file() else {}
    except (OSError, ValueError):
        conflicts, state = [], {}
    return {"project_id": p.config.cloud.project_id, "owner_id": p.config.cloud.owner_id,
            "conflicts": len(conflicts), "paused": (state.get("paused") or {}).get("code"),
            "last_pull": state.get("last_pull"), "last_push": state.get("last_push")}


def listing(current: Path | None = None) -> dict:
    cards = [card(p, current) for p in _candidates()]
    cards.sort(key=lambda c: (c["last_opened"] or "", c["updated"] or ""), reverse=True)
    return {"root": str(root()), "current": str(current) if current else None, "projects": cards}


def resolve(pid: str) -> Path:
    for p in _candidates():
        if project_id(p) == pid:
            return p
    raise KeyError(pid)


def thumbnail(pid: str) -> Path | None:
    p = resolve(pid)
    try:
        return _thumbnail(Project.open(p, migrate=False))
    except ProjectError:
        return None


def touch(path: Path) -> None:
    """Record an open: first in the recent list, visible again if it had been removed."""
    from .filelock import FileLock

    path = Path(path).resolve()
    with FileLock(root() / LOCK):
        data = _load()
        data["recent"] = [{"path": str(path), "last_opened": _now()}] + \
            [e for e in data["recent"] if Path(e["path"]) != path][:49]
        data["hidden"] = [h for h in data["hidden"] if Path(h) != path]
        _save(data)


def forget(pid: str) -> None:
    """P6: remove a card from the gallery; files are never touched."""
    from .filelock import FileLock

    path = resolve(pid)
    with FileLock(root() / LOCK):
        data = _load()
        data["recent"] = [e for e in data["recent"] if Path(e["path"]) != path]
        if str(path) not in data["hidden"]:
            data["hidden"].append(str(path))
        _save(data)


def folder_name(name: str) -> str:
    """P2: a readable, portable folder name for a project."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not s or s in {"con", "prn", "aux", "nul"}:
        raise LibraryError(f"cannot make a folder name from {name!r}")
    return s[:64]


def validate_path(path: Path | str) -> Path:
    """P3: only folders holding a readable project.json open."""
    p = Path(path).expanduser()
    if not (p / "project.json").is_file():
        raise LibraryError(f"{p} is not a SpritePlay project (no project.json)")
    try:
        Project.open(p, migrate=False)
    except Exception as e:
        raise LibraryError(f"{p} has an unreadable project.json: {type(e).__name__}") from e
    return p.resolve()


def create(name: str, *, style: Style, engine: str = "godot", fps: int = 12, location: str | None = None) -> Project:
    name = (name or "").strip()
    if not 1 <= len(name) <= 80:
        raise LibraryError("a project needs a name of 1 to 80 characters")
    parent = Path(location).expanduser() if location else root()
    if not parent.is_dir():
        raise LibraryError(f"location {parent} is not a folder")
    folder = parent / f"{folder_name(name)}.sprites"
    if folder.exists():
        raise LibraryConflict(f"{folder} already exists; choose another name")
    proj = Project.init(folder, style=style, engine=engine, fps=fps, name=name)
    touch(proj.root)
    return proj


def cloud_links() -> dict[str, str]:
    """Cloud project id -> the folder on this machine that syncs with it (the "In the cloud" row
    hides projects already here, cloud plan 6.9)."""
    out: dict[str, str] = {}
    for folder in _candidates():
        try:
            link = Project.open(folder, migrate=False).config.cloud
        except Exception:
            continue
        if link is not None:
            out.setdefault(link.project_id, str(folder))
    return out
