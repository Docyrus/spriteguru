"""The asset library (cloud plan 8): browse a workspace's items, import one into the open project,
and publish a character or an animation from it.

An item is a small bundle of files (a character folder, an exported animation). Importing downloads
each file by hash, verified, into the place its kind belongs; a name that's taken gets `-2`. A
published folder that's already synced is referenced server-side (`from-project`) with nothing
re-uploaded; otherwise its files are uploaded first.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ..project import Project, local_dir, slug
from .client import CloudError, Session
from .index import Index, sha256_file
from .scope import Scope, valid_path
from .sync import Syncer, SyncPaused, _content_type

KINDS = ("character", "animation", "effect", "vehicle", "machine", "tileset", "other")
SUBJECT_KINDS = ("character", "vehicle", "machine", "effect")


class LibraryError(ValueError):
    pass


def browse(session: Session, owner: str | None = None, kind: str | None = None, q: str | None = None) -> list[dict]:
    params = {k: v for k, v in (("owner", owner), ("kind", kind), ("q", q)) if v}
    return session.get("/api/v1/library", params=params)["items"]


def item(session: Session, item_id: str) -> dict:
    return session.get(f"/api/v1/library/{item_id}")


def _free(parent: Path, base: str) -> str:
    name, n = base, 2
    while (parent / name).exists():
        name = f"{base}-{n}"
        n += 1
    return name


def _repath(value, old: str, new: str):
    """Project-relative paths inside a record follow the folder it was imported into."""
    if isinstance(value, str):
        return new + value[len(old):] if value.startswith(old) else value
    if isinstance(value, list):
        return [_repath(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _repath(v, old, new) for k, v in value.items()}
    return value


def import_item(session: Session, root: Path, item_id: str) -> dict:
    """Download an item into the project (8): subjects to characters/<slug>/, animations to
    animations/<id>/ (or its final/ when the item is only the export), everything else to imports/."""
    data = item(session, item_id)
    it, files = data["item"], data["files"]
    owner = it["ownerId"]
    paths = [f["path"] for f in files]
    if any(not valid_path(p) for p in paths):
        raise LibraryError("the item has a file path that can't be placed in a project")
    kind = it["kind"]
    if kind in SUBJECT_KINDS and "character.json" in paths:
        base = slug(it["name"])
        folder = _free(root / "characters", base)
        dest = f"characters/{folder}/"
    elif kind == "animation" or (kind == "effect" and "animation.json" in paths):
        folder = _free(root / "animations", slug(it["name"]))
        dest = f"animations/{folder}/" if "spec.json" in paths else f"animations/{folder}/final/"
    else:
        folder = _free(root / "imports", slug(it["name"]))
        dest = f"imports/{folder}/"
    syncer = Syncer.__new__(Syncer)  # borrow the verified download
    syncer.root, syncer.session, syncer.publish = root, session, (lambda e: None)
    syncer.dir, syncer.findings = local_dir(root) / "sync", []
    syncer.busy_prefixes = lambda: set()
    idx = Index(local_dir(root) / "import-index.json")
    written: list[str] = []
    try:
        for f in files:
            rel = dest + f["path"]
            syncer._download(rel, f["sha256"], f["size"], owner, idx)
            written.append(rel)
    except BaseException:
        shutil.rmtree(root / dest, ignore_errors=True)
        raise
    finally:
        (local_dir(root) / "import-index.json").unlink(missing_ok=True)
    if dest.startswith("characters/"):
        cj = root / dest / "character.json"
        rec = json.loads(cj.read_text())
        old = next((m.group(0) for v in (rec.get("views") or {}).values()
                    if isinstance(v, str) and (m := re.match(r"characters/[^/]+/", v))), None)
        if old is None and isinstance(rec.get("source_image"), str):
            m = re.match(r"characters/[^/]+/", rec["source_image"])
            old = m.group(0) if m else None
        if old and old != dest:
            rec = _repath(rec, old, dest)
        if slug(rec.get("name", folder)) != folder:
            rec["name"] = folder  # the record's name must lead back to its folder
        cj.write_text(json.dumps(rec, indent=2) + "\n")
    return {"item": it["id"], "name": it["name"], "kind": kind, "path": dest, "files": len(written),
            "paths": written[:200]}


def publish(session: Session, root: Path, prefix: str, *, name: str, kind: str, owner: str | None = None,
            description: str | None = None) -> dict:
    """Publish a project folder as a library item (8). A linked project whose folder is fully synced
    is referenced server-side; otherwise the folder's files are uploaded to the workspace first."""
    if kind not in KINDS:
        raise LibraryError(f"kind must be one of {', '.join(KINDS)}")
    prefix = prefix.strip("/") + "/"
    if not valid_path(prefix.rstrip("/")):
        raise LibraryError(f"{prefix} isn't a project folder")
    scope = Scope(root)
    idx = Index(local_dir(root) / "sync" / "index.json")
    local, _ = idx.scan(scope)
    mine = {p: l for p, l in local.items() if p.startswith(prefix)}
    if not mine:
        raise LibraryError(f"{prefix} has no files to publish")
    link = Project.open(root, migrate=False).config.cloud
    syncer = Syncer(root, session)
    synced = (link is not None and syncer.copied() is None
              and all(idx.base_sha(p) == l.sha256 for p, l in mine.items())
              and not [p for p in idx.base if p.startswith(prefix) and p not in local])
    target = owner or (link.owner_id if link else "me")
    body = {"name": name, "kind": kind, **({"description": description} if description else {})}
    if synced:
        made = session.post("/api/v1/library/from-project", {"projectId": link.project_id, "prefix": prefix,
                                                             "owner": target, **body})["item"]
        return {"item": made, "uploaded": 0, "from_project": True}
    if target == "me":
        target = session.get("/api/v1/me")["personal"]["id"]
    st = {"owner_id": target}
    fit = syncer._upload_all(mine, st)
    if len(fit) != len(mine):
        raise LibraryError("some files changed while they uploaded or are too large for your plan; try again")
    made = session.post("/api/v1/library", {
        "owner": target, **body,
        "files": [{"path": p[len(prefix):], "sha256": l.sha256, "size": l.size} for p, l in sorted(mine.items())]})["item"]
    return {"item": made, "uploaded": len(mine), "from_project": False}


__all__ = ["browse", "item", "import_item", "publish", "LibraryError", "CloudError", "SyncPaused", "sha256_file",
           "_content_type"]
