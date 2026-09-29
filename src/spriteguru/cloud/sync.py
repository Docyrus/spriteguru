"""Project sync (cloud plan 6): link, push, pull, conflicts and the project lock.

The project folder stays the source of truth. Files are content-addressed: a push uploads the bytes
the workspace lacks and commits paths with their hashes on top of the revision this folder last
pulled; a pull lists what changed since then and applies the decision table (6.6) against the
baseline both sides last agreed on. Nothing guesses: when both sides changed a file, the user
chooses (6.7), and a commit never records a hash that doesn't match the bytes on disk (C7).

Everything here is synchronous and runs under `.spriteplay/sync/lock`, so the studio's engine and a
CLI command never sync one project at the same time (C25).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import httpx

from .. import library
from ..filelock import FileLock
from ..project import LOCAL_DIR, Project, local_dir, read_json, write_json
from ..spec import CloudLink
from . import config
from .client import CloudError, Offline, Session
from .index import Index, Local, sha256_file
from .scope import Scope, group_of

BATCH = 400  # changes per commit (server limit)
CHECK_GROUP = 2000  # hashes per blobs/check
UPLOADERS = 3
RETRIES = 3  # `409 retry` rounds before pausing (C9)
LOCK_WAIT = 30.0  # how long a second process waits for the project's sync lock (C25)


class SyncPaused(Exception):
    """Syncing can't go on right now; `code` and `message` say why (shown on the project)."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


class SyncBusy(Exception):
    """Another process holds this project's sync lock (C25)."""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


PUSH_BLOCKING = ("plan_required", "team_inactive", "quota_exceeded", "project_limit")


class Syncer:
    def __init__(self, root: Path, session: Session, *, publish: Callable[[dict], None] | None = None,
                 busy: Callable[[], set[str]] | None = None, lock_wait: float = LOCK_WAIT):
        self.lock_wait = lock_wait
        self.root = Path(root).resolve()
        self.session = session
        self.publish = publish or (lambda e: None)
        self.busy_prefixes = busy or (lambda: set())
        self.dir = local_dir(self.root) / "sync"
        self.lock = FileLock(self.dir / "lock")
        self.findings: list[dict] = []

    # -- local state -----------------------------------------------------------------------------------

    def link_block(self) -> CloudLink | None:
        return Project.open(self.root, migrate=False).config.cloud

    def state(self) -> dict | None:
        p = self.dir / "state.json"
        return read_json(p) if p.is_file() else None

    def _save_state(self, st: dict) -> None:
        write_json(self.dir / "state.json", st)

    def conflicts(self) -> dict[str, dict]:
        p = self.dir / "conflicts.json"
        return {c["path"]: c for c in read_json(p)} if p.is_file() else {}

    def _save_conflicts(self, conflicts: dict[str, dict]) -> None:
        write_json(self.dir / "conflicts.json", sorted(conflicts.values(), key=lambda c: c["path"]))

    def copied(self) -> dict | None:
        """A folder carrying another folder's link: duplicated in Finder, cloned or restored (C29)."""
        link = self.link_block()
        if link is None:
            return None
        st = self.state()
        # the folder is part of the identity: a copy made on this same machine carries matching state
        if (st and st.get("project_id") == link.project_id and st.get("machine_id") == config.machine_id()
                and st.get("root") == str(self.root)):
            return None
        return {"project_id": link.project_id, "owner_id": link.owner_id}

    def _busy(self, rel: str) -> bool:
        return any(rel.startswith(p) for p in self.busy_prefixes())

    def _progress(self, phase: str, done: int, total: int, nbytes: int = 0) -> None:
        self.publish({"type": "sync_progress", "phase": phase, "done": done, "total": total, "bytes": nbytes})

    # -- link, unlink, copied folders ------------------------------------------------------------------------

    def link(self, owner: str) -> dict:
        """Create the cloud project in a workspace (`me` or a team id), then push everything (6.3)."""
        proj = Project.open(self.root)
        if proj.config.cloud is not None:
            raise SyncPaused("already_linked", "This project already syncs to the cloud.")
        body = {"owner": owner, "name": proj.config.name, "style": proj.config.style.kind, "engine": proj.config.engine}
        made = self.session.post("/api/v1/projects", body)["project"]
        proj.save(cloud=CloudLink(project_id=made["id"], owner_id=made["ownerId"]))
        self._fresh_state(made["id"], made["ownerId"])
        return made

    def _fresh_state(self, project_id: str, owner_id: str) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._save_state({"project_id": project_id, "owner_id": owner_id, "machine_id": config.machine_id(),
                          "root": str(self.root), "rev": 0, "last_pull": None, "last_push": None, "paused": None})

    def keep_link(self) -> None:
        """C29, "keep syncing it here": start this folder's own sync state; the next run reconciles it
        with the cloud from scratch (same files are recorded as synced, different ones are conflicts)."""
        c = self.copied()
        if c is None:
            return
        self._fresh_state(c["project_id"], c["owner_id"])

    def unlink(self) -> None:
        """Stop syncing: the link and the local sync state go; the cloud copy stays (6.10)."""
        with self._locked():
            Project.open(self.root).save(cloud=None)
            shutil.rmtree(self.dir, ignore_errors=True)

    # -- one sync round ----------------------------------------------------------------------------------------

    class _Held:
        def __init__(self, lock: FileLock, wait: float):
            self.lock, self.wait = lock, wait

        def __enter__(self):
            if not self.lock.acquire(timeout=self.wait):
                raise SyncBusy("Syncing in another window.")
            return self

        def __exit__(self, *exc):
            self.lock.release()

    def _locked(self) -> "_Held":
        self.dir.mkdir(parents=True, exist_ok=True)
        return Syncer._Held(self.lock, self.lock_wait)

    def run(self, *, push: bool = True, pull: bool = True) -> dict:
        """Pull (always first when the cloud moved on), then push. Returns what happened."""
        link = self.link_block()
        if link is None:
            raise SyncPaused("not_linked", "This project isn't linked to the cloud.")
        if self.copied() is not None:
            raise SyncPaused("copied", "This folder is linked to a cloud project from another folder. Keep syncing "
                                       "it here, or make it a separate project?")
        with self._locked():
            st = self.state()
            self.findings = []
            self.publish({"type": "sync_started", "project": link.project_id})
            pushed: list[str] | None = None
            pulled: list[str] | None = None
            idx: Index | None = None
            try:
                remote = self._remote_project(link, st)
                scope = Scope(self.root)
                idx = Index(self.dir / "index.json")
                local, findings = idx.scan(scope)
                self.findings += findings
                pulled = []
                if pull or remote["headRev"] > st["rev"]:
                    pulled = self._pull(idx, scope, local, st)
                    local, _ = idx.scan(scope)
                pushed = self._push(idx, scope, local, st) if push else []
                st["paused"] = None
                st["findings"] = self.findings
                return {"rev": st["rev"], "pushed": pushed, "pulled": pulled, "findings": self.findings,
                        "hashed": idx.hashed, "conflicts": len(self.conflicts())}
            except SyncPaused as e:
                st["paused"] = e.payload()
                self.publish({"type": "sync_paused", "code": e.code, "message": e.message})
                raise
            finally:
                if idx is not None:
                    idx.save()
                self._save_state(st)
                if pushed is not None and pulled is not None:
                    self.publish({"type": "sync_done", "rev": st["rev"], "pushed": len(pushed), "pulled": len(pulled)})

    def _remote_project(self, link: CloudLink, st: dict) -> dict:
        try:
            p = self.session.get(f"/api/v1/projects/{link.project_id}")["project"]
        except CloudError as e:
            if e.status == 404:
                raise SyncPaused("removed", "This project was removed from the cloud. Restore it on spriteplay.com, "
                                            "or stop syncing it.")
            raise
        if p.get("deletedAt"):
            raise SyncPaused("removed", "This project is in the trash on spriteplay.com. Restore it there, or stop "
                                        "syncing it.")
        if p["ownerId"] != link.owner_id:  # moved to another workspace on the website (C23)
            Project.open(self.root).save(cloud=CloudLink(project_id=link.project_id, owner_id=p["ownerId"]))
            st["owner_id"] = p["ownerId"]
            self.publish({"type": "project_files_changed", "paths": ["project.json"]})
        return p

    # -- pull (6.5, 6.6) ----------------------------------------------------------------------------------------

    def _remote_changes(self, project_id: str, since: int) -> tuple[list[dict], int]:
        files, cursor, head = [], None, since
        while True:
            params = {"since": since, **({"cursor": cursor} if cursor else {})}
            page = self.session.get(f"/api/v1/projects/{project_id}/files", params=params)
            files += page["files"]
            head = page["headRev"]
            cursor = page.get("nextCursor")
            if not cursor:
                return files, head

    def _pull(self, idx: Index, scope: Scope, local: dict[str, Local], st: dict) -> list[str]:
        link = self.link_block()
        changes, head = self._remote_changes(link.project_id, st["rev"])
        conflicts = self.conflicts()
        folded = {p.casefold(): p for p in local}
        complete, touched = True, []
        todo = [r for r in changes if scope.includes(r["path"])]
        self._progress("download", 0, len(todo))
        for i, r in enumerate(todo):
            rel = r["path"]
            remote_sha = None if r["deleted"] else r["sha256"]
            if rel in conflicts:  # still open: keep its remote side current
                conflicts[rel]["remote"] = {k: r[k] for k in ("sha256", "size", "rev", "deleted")}
                continue
            if self._busy(rel):  # a running job's folder is never pulled into (C30)
                complete = False
                continue
            base = idx.base_sha(rel)
            mine = local[rel].sha256 if rel in local else None
            if remote_sha == base:
                continue
            if mine == base:
                if remote_sha is None:
                    self._remove(rel)
                    idx.agree(rel, None, 0, r["rev"])
                    folded.pop(rel.casefold(), None)
                else:
                    other = folded.get(rel.casefold())
                    if other is not None and other != rel:  # C17: the disk may not tell them apart
                        self.findings.append({"code": "case_clash", "path": rel, "other": other,
                                              "message": f"{rel} wasn't downloaded: it differs from {other} only in case."})
                        continue
                    if not self._download(rel, remote_sha, r["size"], st["owner_id"], idx, expect=local.get(rel)):
                        complete = False  # changed here meanwhile: the next round decides again
                        continue
                    folded[rel.casefold()] = rel
                    idx.agree(rel, remote_sha, r["size"], r["rev"])
                touched.append(rel)
            elif mine == remote_sha:  # both made the same change
                idx.agree(rel, remote_sha, r["size"], r["rev"])
            else:
                conflicts[rel] = self._conflict(rel, local.get(rel), r, base)
            self._progress("download", i + 1, len(todo))
            if i % 50 == 49:
                idx.save()
        self._save_conflicts(conflicts)
        if complete:
            st["rev"] = max(st["rev"], head)  # C10: only once everything up to head is in place
        st["last_pull"] = _now()
        new_conflicts = [c for c in conflicts.values() if c.get("new")]
        if new_conflicts:
            for c in conflicts.values():
                c.pop("new", None)
            self._save_conflicts(conflicts)
            self.publish({"type": "sync_conflict", "groups": sorted({c["group"] for c in conflicts.values()})})
        if any(t.startswith(("characters/", "animations/")) or t == "project.json" for t in touched):
            self.publish({"type": "project_files_changed", "paths": touched[:200]})
        return touched

    def _conflict(self, rel: str, mine: Local | None, remote: dict, base: str | None) -> dict:
        return {"path": rel, "group": group_of(rel), "base": base, "new": True,
                "local": {"sha256": mine.sha256, "size": mine.size} if mine else None,
                "remote": {k: remote[k] for k in ("sha256", "size", "rev", "deleted")}}

    def _remove(self, rel: str) -> None:
        target = self.root / rel
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        d = target.parent
        while d != self.root and d.is_dir() and not any(d.iterdir()):  # folders left empty go too
            d.rmdir()
            d = d.parent

    def _download(self, rel: str, sha: str, size: int, owner: str, idx: Index, *, expect: Local | None = None,
                  check: bool = False) -> bool:
        """Download to tmp/, verify the hash (C28), then move into place atomically (C10). A second
        mismatch pauses the pull; a broken connection leaves the partial file to resume from. With
        `check`, the local file must still be what the scan saw (`expect`, or absent), else nothing is
        replaced and False comes back."""
        check = check or expect is not None
        tmp = self.dir / "tmp" / sha
        tmp.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (0, 1):
            have = tmp.stat().st_size if tmp.is_file() else 0
            headers = {"Range": f"bytes={have}-"} if 0 < have < size else {}
            try:
                with self.session.stream("GET", f"/api/v1/blobs/{sha}", params={"owner": owner},
                                         headers=headers) as r:
                    mode = "ab" if r.status_code == 206 else "wb"
                    with open(tmp, mode) as fh:
                        for chunk in r.iter_bytes(1024 * 1024):
                            fh.write(chunk)
            except httpx.TransportError:
                raise Offline()
            if sha256_file(tmp) == sha:
                target = self.root / rel
                if check and (not self._unchanged(rel, expect) if expect is not None else target.exists()):
                    return False
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, target)
                stt = target.stat()
                idx.cache[rel] = {"sha256": sha, "size": stt.st_size, "mtime_ns": stt.st_mtime_ns}
                return True
            tmp.unlink(missing_ok=True)
        self.findings.append({"code": "hash_mismatch", "path": rel,
                              "message": f"{rel} arrived damaged twice and wasn't replaced."})
        raise SyncPaused("hash_mismatch", f"A download of {rel} arrived damaged twice. Syncing will try again later.",
                         path=rel)

    # -- push (6.4) ---------------------------------------------------------------------------------------------------

    def _push(self, idx: Index, scope: Scope, local: dict[str, Local], st: dict) -> list[str]:
        conflicts = self.conflicts()
        puts = {p: l for p, l in local.items() if l.sha256 != idx.base_sha(p) and p not in conflicts and not self._busy(p)}
        dels = [p for p in idx.base if p not in local and scope.includes(p) and p not in conflicts and not self._busy(p)]
        if not puts and not dels:
            return []
        puts = self._upload_all(puts, st)
        changes = [{"path": p, "sha256": l.sha256, "size": l.size, "mtime": l.mtime_ns // 1_000_000}
                   for p, l in sorted(puts.items())] + [{"path": p, "deleted": True} for p in sorted(dels)]
        done = self._commit(changes, idx, st, force=[], local=local)
        if done:
            st["last_push"] = _now()
        return done

    def _upload_all(self, puts: dict[str, Local], st: dict) -> dict[str, Local]:
        """Upload what the workspace lacks; returns the files still fit to commit (C7, C14)."""
        owner = st["owner_id"]
        by_sha: dict[str, tuple[str, Local]] = {}
        for p, l in puts.items():
            by_sha.setdefault(l.sha256, (p, l))
        missing: dict[str, dict] = {}
        items = list(by_sha.items())
        oversize: set[str] = set()
        for g in range(0, len(items), CHECK_GROUP):
            group = [(sha, pl) for sha, pl in items[g:g + CHECK_GROUP]]
            while group:
                try:
                    res = self.session.post("/api/v1/blobs/check", {
                        "owner": owner, "blobs": [{"sha256": sha, "size": pl[1].size} for sha, pl in group]})
                    break
                except CloudError as e:
                    if e.code == "file_too_large":  # listed and left out, not a failed sync (6.2)
                        big = set(e.details.get("files") or [])
                        oversize |= big
                        group = [(sha, pl) for sha, pl in group if sha not in big]
                        if not big:
                            raise SyncPaused(e.code, e.message)
                        continue
                    if e.status in (402, 413):
                        raise SyncPaused(e.code, e.message, **e.details)
                    raise
            else:
                continue
            for m in res["missing"]:
                missing[m["sha256"]] = {**m, "partSize": res.get("partSize")}
        if oversize:
            names = sorted(p for p, l in puts.items() if l.sha256 in oversize)
            self.findings.append({"code": "file_too_large", "paths": names,
                                  "message": f"{len(names)} file{'s are' if len(names) != 1 else ' is'} larger than "
                                             "your plan allows and didn't sync."})
        dropped: set[str] = set()
        todo = [(sha, m) for sha, m in missing.items()]
        total = sum(m["size"] for _, m in todo)
        sent = [0, 0]

        def one(sha: str, m: dict) -> None:
            p, l = by_sha[sha]
            if not self._upload(p, l, m, owner):
                dropped.add(sha)
            sent[0] += 1
            sent[1] += m["size"]
            self._progress("upload", sent[0], len(todo), sent[1])

        if todo:
            self._progress("upload", 0, len(todo), 0)
            with ThreadPoolExecutor(UPLOADERS) as ex:
                for f in [ex.submit(one, sha, m) for sha, m in todo]:
                    f.result()
        return {p: l for p, l in puts.items() if l.sha256 not in oversize and l.sha256 not in dropped}

    def _unchanged(self, rel: str, l: Local) -> bool:
        try:
            s = os.stat(self.root / rel)
        except OSError:
            return False
        return (s.st_size, s.st_mtime_ns) == (l.size, l.mtime_ns)

    def _upload(self, rel: str, l: Local, m: dict, owner: str) -> bool:
        """One file; False when it changed under us (C7): it goes in the next round instead."""
        if not self._unchanged(rel, l):
            return False
        path = self.root / rel
        ctype = _content_type(rel)
        try:
            if m.get("method") == "multipart":
                start = self.session.post(f"/api/v1/blobs/{l.sha256}/multipart", {"size": l.size, "contentType": ctype},
                                          params={"owner": owner})
                if "uploadId" in start:
                    parts = []
                    with open(path, "rb") as fh:
                        for n in range(1, start["parts"] + 1):
                            chunk = fh.read(start["partSize"])
                            r = self.session.request("PUT", f"/api/v1/uploads/{start['uploadId']}/parts/{n}",
                                                     content=chunk)
                            parts.append({"partNumber": n, "etag": r.json()["etag"]})
                    self.session.post(f"/api/v1/uploads/{start['uploadId']}/complete", {"parts": parts})
            else:
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != l.sha256:
                    return False
                self.session.request("PUT", f"/api/v1/blobs/{l.sha256}", params={"owner": owner}, content=data,
                                     headers={"Content-Type": ctype})
        except CloudError as e:
            if e.code == "hash_mismatch":
                return False
            if e.status in (402, 413):
                raise SyncPaused(e.code, e.message, **e.details)
            raise
        return self._unchanged(rel, l)

    def _commit(self, changes: list[dict], idx: Index, st: dict, *, force: list[str],
                local: dict[str, Local]) -> list[str]:
        link = self.link_block()
        done: list[str] = []
        conflicts = self.conflicts()
        for b in range(0, len(changes), BATCH):
            batch = changes[b:b + BATCH]
            retries, reuploaded = 0, False
            while batch:
                body = {"baseRev": st["rev"], "message": self._message(batch), "changes": batch,
                        "force": [c["path"] for c in batch if c["path"] in force]}
                self._progress("commit", len(done), len(changes))
                try:
                    res = self.session.post(f"/api/v1/projects/{link.project_id}/commit", body)
                except CloudError as e:
                    if e.code == "conflict":  # changed on the server meanwhile: record, commit the rest (C8)
                        hit = {c["path"]: c for c in e.details.get("conflicts", [])}
                        for rel, c in hit.items():
                            conflicts[rel] = self._conflict(rel, local.get(rel), {"path": rel, **c["remote"]},
                                                            idx.base_sha(rel))
                        batch = [c for c in batch if c["path"] not in hit]
                        self._save_conflicts({k: {kk: vv for kk, vv in v.items() if kk != "new"} for k, v in conflicts.items()})
                        self.publish({"type": "sync_conflict", "groups": sorted({c["group"] for c in conflicts.values()})})
                        continue
                    if e.code == "retry":  # another machine committed at the same moment (C9)
                        retries += 1
                        if retries > RETRIES:
                            raise SyncPaused("retry", "Another machine keeps committing to this project. Syncing will "
                                                      "try again shortly.")
                        scope = Scope(self.root)
                        fresh, _ = idx.scan(scope)
                        self._pull(idx, scope, fresh, st)
                        continue
                    if e.code == "missing_blobs" and not reuploaded:
                        reuploaded = True
                        need = set(e.details.get("missing") or [])
                        self._upload_all({c["path"]: local[c["path"]] for c in batch
                                          if c.get("sha256") in need and c["path"] in local}, st)
                        continue
                    if e.status in (402, 413):
                        raise SyncPaused(e.code, e.message, **e.details)
                    raise
                rev = res["rev"]
                for c in batch:
                    if c.get("deleted"):
                        idx.agree(c["path"], None, 0, rev)
                    else:
                        idx.agree(c["path"], c["sha256"], c["size"], rev)
                    done.append(c["path"])
                # our commit on top of our base: we're current. Otherwise other machines committed in
                # between, and a pull from our base must still fetch their changes.
                if rev == st["rev"] + 1 or (res.get("applied") == 0 and rev == st["rev"]):
                    st["rev"] = rev
                break
        return done

    def _message(self, batch: list[dict]) -> str:
        what: list[str] = []
        for c in batch:
            parts = c["path"].split("/")
            if parts[0] == "animations" and len(parts) > 2:
                w = f"{'deleted' if c.get('deleted') else 'exported'} {parts[1]}"
            elif parts[0] == "characters" and len(parts) > 2:
                w = f"updated {parts[1]}"
            else:
                w = f"updated {c['path']}"
            if w not in what:
                what.append(w)
        text = ", ".join(what[:2]) + (f" and {len(what) - 2} more" if len(what) > 2 else "")
        return f"{config.device_name()}: {text}"[:300]

    # -- conflicts (6.7) --------------------------------------------------------------------------------------------------

    def resolve(self, choices: list[dict]) -> dict:
        """[{path or group, keep: "mine" | "theirs"}]: "mine" re-commits this machine's version with the
        paths in `force`; "theirs" takes the cloud's (a download, or a deletion)."""
        with self._locked():
            st = self.state()
            conflicts = self.conflicts()
            idx = Index(self.dir / "index.json")
            scope = Scope(self.root)
            local, _ = idx.scan(scope)
            picks: dict[str, str] = {}
            for ch in choices:
                keep = ch.get("keep")
                if keep not in ("mine", "theirs"):
                    raise ValueError("keep must be 'mine' or 'theirs'")
                paths = [p for p, c in conflicts.items() if c["group"] == ch.get("group")] if ch.get("group") else \
                    [ch.get("path")]
                for p in paths:
                    if p not in conflicts:
                        raise ValueError(f"{p} has no open conflict")
                    picks[p] = keep
            mine = [p for p, k in picks.items() if k == "mine"]
            theirs = [p for p, k in picks.items() if k == "theirs"]
            for p in theirs:
                r = conflicts[p]["remote"]
                if r["deleted"]:
                    self._remove(p)
                    idx.agree(p, None, 0, r["rev"])
                else:
                    self._download(p, r["sha256"], r["size"], st["owner_id"], idx)
                    idx.agree(p, r["sha256"], r["size"], r["rev"])
                del conflicts[p]
            self._save_conflicts(conflicts)
            if mine:
                puts = {p: local[p] for p in mine if p in local}
                puts = self._upload_all(puts, st)
                changes = [{"path": p, "sha256": l.sha256, "size": l.size, "mtime": l.mtime_ns // 1_000_000}
                           for p, l in sorted(puts.items())] + \
                    [{"path": p, "deleted": True} for p in sorted(mine) if p not in local]
                for p in self._commit(changes, idx, st, force=mine, local=local):
                    conflicts.pop(p, None)
            self._save_conflicts(conflicts)
            idx.save()
            self._save_state(st)
            if theirs:
                self.publish({"type": "project_files_changed", "paths": theirs[:200]})
            return {"resolved": len(picks) - len([p for p in picks if p in conflicts]), "open": len(conflicts)}

    # -- what the studio shows -------------------------------------------------------------------------------------------

    def status(self) -> dict:
        link = self.link_block()
        if link is None:
            return {"linked": False}
        st = self.state() or {}
        copied = self.copied()
        conflicts = self.conflicts()
        groups: dict[str, list] = {}
        for c in conflicts.values():
            groups.setdefault(c["group"], []).append(c)
        pending = {"push": 0}
        if copied is None and st:
            idx = Index(self.dir / "index.json")
            local, _ = idx.scan(Scope(self.root))
            ups = [p for p, l in local.items() if l.sha256 != idx.base_sha(p) and p not in conflicts]
            dels = [p for p in idx.base if p not in local and p not in conflicts]
            pending = {"push": len(ups) + len(dels), "paths": sorted(ups + dels)[:20]}
        return {"linked": True, "project_id": link.project_id, "owner_id": link.owner_id, "rev": st.get("rev"),
                "last_pull": st.get("last_pull"), "last_push": st.get("last_push"), "paused": st.get("paused"),
                "copied": copied, "pending": pending, "findings": st.get("findings", []),
                "conflicts": [{"group": g, "files": sorted(v, key=lambda c: c["path"])} for g, v in sorted(groups.items())]}


def _content_type(rel: str) -> str:
    ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
    return {"png": "image/png", "gif": "image/gif", "webp": "image/webp", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "svg": "image/svg+xml", "json": "application/json", "jsonl": "application/json", "txt": "text/plain",
            "html": "text/html", "tres": "text/plain", "tscn": "text/plain", "mp4": "video/mp4"}.get(
        ext, "application/octet-stream")


def download_project(session: Session, project: dict, parent: Path, *, publish: Callable[[dict], None] | None = None,
                     cancelled: Callable[[], bool] | None = None) -> Path:
    """Put a cloud project on this machine (6.9): a new `<name>.sprites` folder (with -2, -3 when taken,
    C24), every live file verified, and project.json written last, so a half-finished download never
    looks like a project. A failure or cancel removes the partial folder."""
    from ..library import folder_name

    publish = publish or (lambda e: None)
    base = folder_name(project["name"])
    target = parent / f"{base}.sprites"
    n = 2
    while target.exists():
        target = parent / f"{base}-{n}.sprites"
        n += 1
    target.mkdir(parents=True)
    try:
        files, cursor, head = [], None, 0
        while True:
            page = session.get(f"/api/v1/projects/{project['id']}/files",
                               params={"since": 0, **({"cursor": cursor} if cursor else {})})
            files += page["files"]
            head = page["headRev"]
            cursor = page.get("nextCursor")
            if not cursor:
                break
        dummy = Syncer.__new__(Syncer)
        dummy.root, dummy.session, dummy.publish = target, session, publish
        dummy.dir, dummy.findings = target / LOCAL_DIR / "sync", []
        dummy.busy_prefixes = lambda: set()
        idx = Index(dummy.dir / "index.json")
        last = None
        wanted = [f for f in files if not f["deleted"] and Scope(target).includes(f["path"])]
        for i, f in enumerate(wanted):
            if cancelled and cancelled():
                raise SyncPaused("cancelled", "The download was cancelled.")
            if f["path"] == "project.json":
                last = f
                continue
            dummy._download(f["path"], f["sha256"], f["size"], project["ownerId"], idx)
            idx.agree(f["path"], f["sha256"], f["size"], f["rev"])
            publish({"type": "cloud_download_progress", "project_id": project["id"], "done": i + 1, "total": len(wanted)})
        if last is None:
            raise SyncPaused("no_project_file", "That cloud project has no project.json yet.")
        dummy._download("project.json", last["sha256"], last["size"], project["ownerId"], idx)
        idx.agree("project.json", last["sha256"], last["size"], last["rev"])
        idx.save()
        write_json(dummy.dir / "state.json", {"project_id": project["id"], "owner_id": project["ownerId"],
                                              "machine_id": config.machine_id(), "root": str(target.resolve()),
                                              "rev": head, "last_pull": _now(),
                                              "last_push": None, "paused": None})
        proj = Project.open(target)
        link = proj.config.cloud
        if link is None or link.project_id != project["id"] or link.owner_id != project["ownerId"]:
            # only when needed: an untouched copy stays byte-identical to the cloud's
            proj.save(cloud=CloudLink(project_id=project["id"], owner_id=project["ownerId"]))
        publish({"type": "cloud_download_progress", "project_id": project["id"], "done": len(wanted),
                 "total": len(wanted), "finished": True})
        return target
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise


__all__ = ["Syncer", "SyncPaused", "SyncBusy", "download_project", "library"]
