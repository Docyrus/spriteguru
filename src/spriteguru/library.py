"""Local project gallery, default project folder, and SpritePlay-era migration."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from .env import get as app_env
from .project import Project, ProjectError, write_json
from .spec import Style

STATE = ".spriteguru-library.json"
LOCK = ".spriteguru-library.lock"
FOLDER, LEGACY_FOLDER = "SpriteGuru", "SpritePlay"
LEGACY_NAMES = {".spriteplay-library.json": STATE, ".spriteplay-cache": ".spriteguru-cache"}
LEGACY_LOCKS = (".spriteplay-library.lock", ".spriteplay-cloud.lock", ".spriteguru-cloud.lock")
CLOUD_STATE_KEYS = {"machine_id", "access", "access_cache", "auto_sync", "release_check", "release_server"}
LEGACY_LAST = Path.home() / ".config" / "spritekit" / "last_project"
_migrated: set[Path] = set()


class LibraryError(ValueError):
    """Bad input (400)."""


class LibraryConflict(RuntimeError):
    """The request clashes with what exists (409)."""


def _default_roots() -> tuple[Path, Path]:
    docs = Path.home() / "Documents"
    parent = docs if docs.is_dir() else Path.home()
    return parent / FOLDER, parent / LEGACY_FOLDER


def root() -> Path:
    explicit = app_env("LIBRARY")
    rewrite: tuple[Path, Path] | None = None
    if explicit:
        selected = Path(explicit).expanduser()
    else:
        canonical, legacy = _default_roots()
        if canonical.exists() or not legacy.is_dir():
            selected = canonical
        else:
            try:
                os.rename(legacy, canonical)
                selected = canonical
                rewrite = (legacy, canonical)
            except OSError as e:
                print(f"spriteguru: couldn't migrate {legacy} to SpriteGuru: {e}", file=sys.stderr, flush=True)
                selected = legacy
    selected.mkdir(parents=True, exist_ok=True)
    selected = selected.resolve()
    _migrate(selected, rewrite=rewrite)
    return selected


def all_roots() -> tuple[Path, ...]:
    primary = root()
    if app_env("LIBRARY"):
        return (primary,)
    canonical, legacy = _default_roots()
    roots = [primary]
    for candidate in (canonical.resolve(), legacy.resolve()):
        if candidate != primary and candidate.is_dir() and candidate not in roots:
            _migrate(candidate)
            roots.append(candidate)
    return tuple(roots)


def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text()) if path.is_file() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _rewrite_path(value: str, old: Path, new: Path) -> str:
    path = Path(value)
    try:
        rel = path.relative_to(old)
    except ValueError:
        return value
    return str(new / rel)


def _merge_paths(first: list, second: list, *, records: bool) -> list:
    out: list = []
    seen: set[str] = set()
    for value in [*first, *second]:
        key = str(value.get("path")) if records and isinstance(value, dict) else str(value)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _merge_state(canonical: dict, legacy: dict, rewrite: tuple[Path, Path] | None) -> dict:
    current = dict(canonical)
    old = dict(legacy)
    if rewrite:
        before, after = rewrite
        for data in (current, old):
            for entry in data.get("recent", []):
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    entry["path"] = _rewrite_path(entry["path"], before, after)
            data["hidden"] = [_rewrite_path(p, before, after) for p in data.get("hidden", [])]
    merged = {**current, **old}
    merged["recent"] = _merge_paths(old.get("recent", []), current.get("recent", []), records=True)[:50]
    merged["hidden"] = _merge_paths(old.get("hidden", []), current.get("hidden", []), records=False)
    for key in CLOUD_STATE_KEYS:
        merged.pop(key, None)
    return merged


def _migrate(folder: Path, *, rewrite: tuple[Path, Path] | None = None) -> None:
    folder = folder.resolve()
    if folder in _migrated:
        return
    _migrated.add(folder)
    from .filelock import FileLock

    canonical_path = folder / STATE
    legacy_path = folder / ".spriteplay-library.json"
    try:
        with FileLock(folder / LOCK):
            canonical = _read_state(canonical_path)
            legacy = _read_state(legacy_path)
            if canonical or legacy:
                write_json(canonical_path, _merge_state(canonical, legacy, rewrite))
            if legacy_path.exists() and legacy_path != canonical_path:
                legacy_path.unlink(missing_ok=True)
            legacy_cache = folder / ".spriteplay-cache"
            canonical_cache = folder / ".spriteguru-cache"
            if legacy_cache.exists() and not canonical_cache.exists():
                os.rename(legacy_cache, canonical_cache)
            for name in LEGACY_LOCKS:
                (folder / name).unlink(missing_ok=True)
    except (OSError, TimeoutError) as e:
        print(f"spriteguru: couldn't migrate local library state in {folder}: {e}", file=sys.stderr, flush=True)
    from . import keys

    keys.cleanup_cloud_credentials()


def _reset_for_tests() -> None:
    _migrated.clear()


def project_id(path: Path | str) -> str:
    return hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:12]


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _load() -> dict:
    data = _read_state(root() / STATE)
    data.setdefault("recent", [])
    data.setdefault("hidden", [])
    return data


def _save(data: dict) -> None:
    write_json(root() / STATE, data)


def get_state(key: str, default=None):
    return _load().get(key, default)


def set_state(**values) -> None:
    from .filelock import FileLock

    with FileLock(root() / LOCK):
        data = _load()
        for key, value in values.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        _save(data)


def _candidates() -> list[Path]:
    seen: dict[str, Path] = {}
    hidden: set[str] = set()
    for library_root in all_roots():
        data = _read_state(library_root / STATE)
        for entry in data.get("recent", []):
            if isinstance(entry, dict) and entry.get("path"):
                path = Path(entry["path"])
                seen.setdefault(str(path), path)
        hidden.update(str(path) for path in data.get("hidden", []))
        for project_json in sorted(library_root.glob("*/project.json")):
            path = project_json.parent.resolve()
            seen.setdefault(str(path), path)
    if not app_env("LIBRARY") and LEGACY_LAST.is_file():
        try:
            path = Path(LEGACY_LAST.read_text().strip()).resolve()
            seen.setdefault(str(path), path)
        except OSError:
            pass
    return [path for key, path in seen.items() if key not in hidden]


def _thumbnail(project: Project) -> Path | None:
    chars = project.characters()
    active = project.config.active_character
    order = sorted(chars, key=lambda item: (item.name != active, not item.approved))
    for character in order:
        for view in ("side-e", "key", "front"):
            rel = character.views.get(view)
            if rel and (project.root / rel).is_file():
                return project.root / rel
    finals = sorted((project.root / "animations").glob("*/final/frames/000.png"), key=lambda f: f.stat().st_mtime)
    return finals[-1] if finals else None


def _updated(path: Path) -> str:
    stamps = [path / "project.json", path / "characters", path / "animations"]
    modified = max((item.stat().st_mtime for item in stamps if item.exists()), default=0.0)
    return dt.datetime.fromtimestamp(modified).isoformat(timespec="seconds") if modified else ""


def card(path: Path, current: Path | None = None) -> dict:
    path = Path(path)
    pid = project_id(path)
    opened = {str(Path(e["path"])): e.get("last_opened") for e in _load()["recent"]}.get(str(path))
    base = {"id": pid, "name": path.name.removesuffix(".sprites"), "path": str(path), "style": None,
            "engine": None, "characters": 0, "animations": 0, "thumbnail": None, "updated": "",
            "last_opened": opened, "missing": False, "error": None,
            "current": current is not None and Path(current) == path}
    if not (path / "project.json").is_file():
        return {**base, "missing": True}
    try:
        project = Project.open(path, migrate=False)
    except Exception as e:
        return {**base, "error": f"unreadable project.json: {type(e).__name__}", "updated": _updated(path)}
    thumb = _thumbnail(project)
    return {**base, "name": project.config.name, "style": project.config.style.kind, "engine": project.config.engine,
            "characters": len(list((path / "characters").glob("*/character.json"))),
            "animations": len(list((path / "animations").glob("*/spec.json"))),
            "thumbnail": f"/api/library/projects/{pid}/thumbnail" if thumb else None, "updated": _updated(path)}


def listing(current: Path | None = None) -> dict:
    cards = [card(path, current) for path in _candidates()]
    cards.sort(key=lambda item: (item["last_opened"] or "", item["updated"] or ""), reverse=True)
    return {"root": str(root()), "current": str(current) if current else None, "projects": cards}


def resolve(pid: str) -> Path:
    for path in _candidates():
        if project_id(path) == pid:
            return path
    raise KeyError(pid)


def thumbnail(pid: str) -> Path | None:
    path = resolve(pid)
    try:
        return _thumbnail(Project.open(path, migrate=False))
    except ProjectError:
        return None


def touch(path: Path) -> None:
    from .filelock import FileLock

    path = Path(path).resolve()
    with FileLock(root() / LOCK):
        data = _load()
        data["recent"] = [{"path": str(path), "last_opened": _now()}] + [
            entry for entry in data["recent"] if Path(entry["path"]) != path
        ][:49]
        data["hidden"] = [hidden for hidden in data["hidden"] if Path(hidden) != path]
        _save(data)


def forget(pid: str) -> None:
    from .filelock import FileLock

    path = resolve(pid)
    with FileLock(root() / LOCK):
        data = _load()
        data["recent"] = [entry for entry in data["recent"] if Path(entry["path"]) != path]
        if str(path) not in data["hidden"]:
            data["hidden"].append(str(path))
        _save(data)


def folder_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug or slug in {"con", "prn", "aux", "nul"}:
        raise LibraryError(f"cannot make a folder name from {name!r}")
    return slug[:64]


def validate_path(path: Path | str) -> Path:
    project = Path(path).expanduser()
    if not (project / "project.json").is_file():
        raise LibraryError(f"{project} is not a SpriteGuru project (no project.json)")
    try:
        Project.open(project, migrate=False)
    except Exception as e:
        raise LibraryError(f"{project} has an unreadable project.json: {type(e).__name__}") from e
    return project.resolve()


def create(name: str, *, style: Style, engine: str = "godot", fps: int = 12,
           location: str | None = None) -> Project:
    name = (name or "").strip()
    if not 1 <= len(name) <= 80:
        raise LibraryError("a project needs a name of 1 to 80 characters")
    parent = Path(location).expanduser() if location else root()
    if not parent.is_dir():
        raise LibraryError(f"location {parent} is not a folder")
    folder = parent / f"{folder_name(name)}.sprites"
    if folder.exists():
        raise LibraryConflict(f"{folder} already exists; choose another name")
    project = Project.init(folder, style=style, engine=engine, fps=fps, name=name)
    touch(project.root)
    return project


def cloud_links() -> dict[str, str]:
    """Temporary empty result until the retired cloud routes are deleted in the next task."""
    return {}
