"""Project folder layout (section 5): characters, specs, job history, cache and outputs."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

from .env import get as app_env
from .spec import CharacterRecord, CloudLink, ProjectConfig, SpriteSpec, Style


class ProjectError(RuntimeError):
    pass


# machine-local project state (C13): a path on one machine and a choice that changes every few
# minutes would conflict constantly if they synced, so they live in .spriteplay/local.json
LOCAL_FIELDS = ("active_character", "asset_folder")
LOCAL_DIR = ".spriteplay"
LEGACY_LOCAL_DIR = ".spriteguru"  # the folder's name before SpriteGuru became SpritePlay (N5)
_KEEP = object()


def local_dir(root: Path | str, migrate: bool = True) -> Path:
    """A project's machine-local folder. One still under its SpriteGuru name is renamed when the
    project is opened for writing, and read where it is otherwise; if both exist the new one wins (N5).
    A rename that fails leaves the old folder in use for this run (N13)."""
    root = Path(root)
    new, old = root / LOCAL_DIR, root / LEGACY_LOCAL_DIR
    if new.exists() or not old.is_dir():
        return new
    if not migrate:
        return old
    try:
        os.rename(old, new)
    except OSError as e:
        if new.exists():  # another process renamed it first
            return new
        print(f"spriteguru: couldn't rename {old} to {new.name}: {e}", file=sys.stderr, flush=True)
        return old
    ignore_local(root)  # N7: git ignores the renamed folder from the moment it exists
    return new


def ignore_local(root: Path) -> None:
    """Machine-local state never goes into the game repository (or the cloud). An old `.spriteguru/`
    line stays, so a machine still on the SpriteGuru build doesn't add it back (N7)."""
    gi = Path(root) / ".gitignore"
    text = gi.read_text() if gi.is_file() else ""
    if f"{LOCAL_DIR}/" not in text.split():
        gi.write_text(text + ("" if not text or text.endswith("\n") else "\n") + f"{LOCAL_DIR}/\n")


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not s:
        raise ProjectError(f"cannot make a name from {text!r}")
    return s


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    os.replace(tmp, path)


def read_json(path: Path):
    return json.loads(path.read_text())


class Project:
    def __init__(self, root: Path, config: ProjectConfig):
        self.root = root.resolve()
        self.config = config

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def init(cls, root: Path, *, style: Style, engine: str = "godot", fps: int = 12,
             asset_folder: str | None = None, name: str | None = None) -> "Project":
        root = Path(root)
        if (root / "project.json").exists():
            raise ProjectError(f"{root} already holds a project")
        cfg = ProjectConfig(name=name or root.name.removesuffix(".sprites"), style=style,
                            engine=engine, fps=fps, asset_folder=asset_folder)
        proj = cls(root, cfg)
        for sub in ("characters", "animations", "cache"):
            (proj.root / sub).mkdir(parents=True, exist_ok=True)
        (proj.root / ".gitignore").write_text(f"cache/\nanimations/*/jobs/\n{LOCAL_DIR}/\n")
        # the project lives in the game repository; images go through Git LFS (section 5)
        (proj.root / ".gitattributes").write_text("".join(f"*.{ext} filter=lfs diff=lfs merge=lfs -text\n"
                                                          for ext in ("png", "gif", "mp4", "zip")))
        proj.save()
        return proj

    @classmethod
    def open(cls, root: Path | str, *, migrate: bool = True) -> "Project":
        """Open a project. Machine-local fields come from .spriteplay/local.json; a project that still
        keeps them in project.json has them moved there (C13), unless `migrate` is False (the
        gallery only reads)."""
        root = Path(root)
        path = root / "project.json"
        if not path.is_file():
            raise ProjectError(f"no project.json in {root}")
        raw = read_json(path)
        local_p = local_dir(root, migrate) / "local.json"
        local = read_json(local_p) if local_p.is_file() else {}
        legacy = {k: raw[k] for k in LOCAL_FIELDS if k in raw}
        data = {**raw, **{k: local.get(k, legacy.get(k)) for k in LOCAL_FIELDS}}
        proj = cls(root, ProjectConfig.model_validate(data))
        if migrate and legacy:
            proj.save()
        return proj

    @classmethod
    def discover(cls, explicit: str | Path | None = None) -> "Project":
        if explicit:
            return cls.open(explicit)
        env = app_env("PROJECT")
        if env:
            return cls.open(env)
        here = Path.cwd().resolve()
        for d in (here, *here.parents):
            if (d / "project.json").is_file():
                return cls.open(d)
            hits = sorted(d.glob("*.sprites/project.json"))
            if d == here and len(hits) == 1:
                return cls.open(hits[0].parent)
        raise ProjectError("no project found; pass --project or run `spriteguru init`")

    def save(self, *, cloud=_KEEP) -> None:
        """Write project.json and .spriteplay/local.json. The `cloud` link is the one on disk unless
        `cloud` is given (link, unlink, a new owner): the engine holds its config in memory for a whole
        session, and a settings save must never drop a link that sync wrote meanwhile."""
        if cloud is _KEEP:
            path = self.root / "project.json"
            try:
                disk = read_json(path).get("cloud") if path.is_file() else None
            except ValueError:
                disk = None
            self.config.cloud = CloudLink.model_validate(disk) if disk else None
        else:
            self.config.cloud = cloud
        data = self.config.model_dump(mode="json")
        local = {k: data.pop(k) for k in LOCAL_FIELDS}
        if data.get("cloud") is None:
            data.pop("cloud", None)
        write_json(local_dir(self.root) / "local.json", local)
        write_json(self.root / "project.json", data)
        ignore_local(self.root)

    # -- paths ---------------------------------------------------------------

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def ledger_path(self) -> Path:
        return self.root / "ledger.jsonl"

    @property
    def eval_db(self) -> Path:
        return self.root / "eval.sqlite"

    def rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    def abs(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise ProjectError(f"path {rel!r} escapes the project")
        return p

    # -- characters ------------------------------------------------------------

    def character_dir(self, name: str) -> Path:
        return self.root / "characters" / slug(name)

    def characters(self) -> list[CharacterRecord]:
        out = []
        for p in sorted((self.root / "characters").glob("*/character.json")):
            out.append(CharacterRecord.model_validate(read_json(p)))
        return out

    def character(self, name: str) -> CharacterRecord:
        p = self.character_dir(name) / "character.json"
        if not p.is_file():
            raise ProjectError(f"no character {name!r}")
        return CharacterRecord.model_validate(read_json(p))

    def save_character(self, rec: CharacterRecord) -> None:
        d = self.character_dir(rec.name)
        (d / "ref").mkdir(parents=True, exist_ok=True)
        write_json(d / "character.json", rec)

    # -- animations --------------------------------------------------------------

    @staticmethod
    def animation_id(character: str, action: str, facing: str) -> str:
        return f"{slug(character)}-{slug(action)}-{facing}"

    def animation_dir(self, anim_id: str) -> Path:
        return self.root / "animations" / anim_id

    def animations(self) -> list[str]:
        return sorted(p.parent.name for p in (self.root / "animations").glob("*/spec.json"))

    def spec(self, anim_id: str) -> SpriteSpec:
        p = self.animation_dir(anim_id) / "spec.json"
        if not p.is_file():
            raise ProjectError(f"no animation {anim_id!r}")
        return SpriteSpec.model_validate(read_json(p))

    def save_spec(self, anim_id: str, spec: SpriteSpec) -> None:
        write_json(self.animation_dir(anim_id) / "spec.json", spec)

    def new_job_dir(self, anim_id: str) -> Path:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        base = self.animation_dir(anim_id) / "jobs"
        d = base / stamp
        i = 1
        while d.exists():
            i += 1
            d = base / f"{stamp}-{i}"
        d.mkdir(parents=True)
        return d

    def jobs(self, anim_id: str) -> list[Path]:
        return sorted((self.animation_dir(anim_id) / "jobs").glob("*/job.json"))
