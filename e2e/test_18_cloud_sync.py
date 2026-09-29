"""Cloud membership, phases 2–5 (docs/cloud-integration-plan.md scenarios 2–9 and 11): two machines
(two library roots with their own machine ids and keychains) sync one project through the fake
cloud. Machine A runs the engine and the studio's API; machine B uses the CLI, as a second window or
a build script would. Offline and deterministic: projects are made in synthetic mode."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pathspec

from cloudhelp import Engine, browser_sign_in, cli, cli_login, machine_env
from conftest import ROOT, free_port
from fakecloud import FakeCloud, MiB

IGNORED = pathspec.PathSpec.from_lines("gitwildmatch", ["cache/", "animations/*/jobs/", ".spriteplay/", "/ledger.jsonl",
                                                         "*.tmp", ".DS_Store"])


def _tree(root: Path) -> dict[str, str]:
    """Every file that belongs in the cloud copy, with its hash (computed here, not by the app)."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not p.is_symlink() and not IGNORED.match_file(rel):
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _cloud_tree(fc: FakeCloud, pid: str) -> dict[str, str]:
    return {r["path"]: r["sha256"] for r in fc.files[pid].values() if not r["deleted"]}


def _committed_paths(fc: FakeCloud) -> list[str]:
    return [p for e in fc.events("commit") for p in e["paths"]]


def _bad_paths(paths: list[str]) -> list[str]:
    """C27: paths that must never reach a commit."""
    return [p for p in paths if p.startswith(("cache/", ".spriteplay/")) or "/jobs/" in p or p == "ledger.jsonl"
            or p.endswith(".tmp")]


def _wait_job(c, job: str, timeout: float = 240) -> str:
    deadline = time.time() + timeout
    st = ""
    while time.time() < deadline:
        st = c.get(f"/api/jobs/{job}").json()["state"]
        if st in ("done", "failed", "cancelled"):
            return st
        time.sleep(0.2)
    return st


def _make_project(c, name: str = "Knight Quest") -> Path:
    """A synthetic project with an approved knight and an exported walk."""
    proj = Path(c.post("/api/library/projects", json={"name": name, "style": {"kind": "hd-cartoon"},
                                                      "engine": "phaser"}).json()["project"]["path"])
    c.post("/api/characters", json={"name": "knight", "description": "An armoured knight with a blue tabard."})
    for _ in range(300):
        if c.get("/api/characters/knight").json().get("views"):
            break
        time.sleep(0.1)
    c.post("/api/characters/knight/approve")
    anim = c.post("/api/animations", json={"character": "knight", "action": "walk", "facing": "E"}).json()["id"]
    job = c.post(f"/api/animations/{anim}/jobs", json={"seed": 0}).json()["id"]
    _wait_job(c, job)
    return proj


def _export(c, action: str) -> str:
    anim = c.post("/api/animations", json={"character": "knight", "action": action, "facing": "E"}).json()["id"]
    _wait_job(c, c.post(f"/api/animations/{anim}/jobs", json={"seed": 0}).json()["id"])
    return anim


def _edit_json(path: Path, **fields) -> None:
    d = json.loads(path.read_text())
    d.update(fields)
    path.write_text(json.dumps(d, indent=2) + "\n")


def test_cloud_sync_two_machines(rec, work):
    """Link, first push, downloading on a second machine, changes both ways, deletions, a conflict
    kept from this machine, a deletion that conflicts with a change, a project moved to a team, and a
    copied folder (scenarios 2, 3, 4, 5, 11; C8, C11–C13, C18, C20, C21, C23, C24, C27, C29)."""
    S = "cloud-sync"
    fc = FakeCloud().start()
    user = fc.add_user("sync@example.com", "Sync Test", pro=True)
    lib_a, lib_b = work / S / "machine-a", work / S / "machine-b"
    env_a, env_b = machine_env(fc.url, lib_a), machine_env(fc.url, lib_b)
    eng = Engine(free_port(), env_a, work / S / "engine-a.log")
    try:
        with eng.client() as c:
            browser_sign_in(c)
            proj = _make_project(c)
            (proj / "notes.tmp").write_text("scratch")
            r = c.post("/api/project/sync/link", json={"owner": "me"})
            pid = r.json()["project"]["id"]
            local = _tree(proj)
            cloud = _cloud_tree(fc, pid)
            committed = _committed_paths(fc)
            rec.check(S, "linking to Personal creates the cloud project and the first push commits every file in scope",
                      r.status_code == 200 and cloud == local and len(local) > 20
                      and json.loads((proj / "project.json").read_text())["cloud"]["project_id"] == pid,
                      {"files": len(local), "same": cloud == local}, [])
            rec.check(S, "no cache, job, .spriteplay, ledger or temporary path reaches a commit",
                      _bad_paths(committed) == [] and any(p.startswith("cache/") for p in
                                                          [x.relative_to(proj).as_posix() for x in proj.rglob("*")])
                      and (proj / "ledger.jsonl").is_file(), _bad_paths(committed), ["C12", "C27"])

            again = c.post("/api/project/sync/now").json()
            commits = len(fc.events("commit"))
            rec.check(S, "a second round is a no-op that only stats files (hashes reused)",
                      again["pushed"] == [] and again["pulled"] == [] and again["hashed"] == 0
                      and commits == len(fc.events("commit")), again["hashed"], ["C21"])

            c.put("/api/project/active-character", json={"name": None})
            c.put("/api/project/active-character", json={"name": "knight"})
            after = c.post("/api/project/sync/now").json()
            local_json = json.loads((proj / ".spriteplay" / "local.json").read_text())
            rec.check(S, "the active character lives in .spriteplay/local.json, so switching it syncs nothing",
                      after.get("pushed") == [] and local_json.get("active_character") == "knight"
                      and "active_character" not in json.loads((proj / "project.json").read_text()),
                      after.get("pushed", after), ["C13"])
            c.patch("/api/settings", json={"session_cap_usd": 12})
            rec.check(S, "saving settings after linking keeps the link (the engine's config in memory predates it)",
                      json.loads((proj / "project.json").read_text()).get("cloud", {}).get("project_id") == pid
                      and c.post("/api/project/sync/now").status_code == 200, None, [])

            # machine B: signs in from the CLI, lists the cloud project and downloads it
            code, _, _ = cli_login(env_b)
            code, listing, _ = cli(env_b, "cloud", "projects")
            row = next(x for x in listing["projects"] if x["id"] == pid)
            code, got, _ = cli(env_b, "cloud", "download", pid)
            b_proj = Path(got["path"])
            rec.check(S, "machine B lists the project in the cloud and downloads an identical copy",
                      row["local_path"] is None and _tree(b_proj) == _tree(proj) and b_proj.parent == lib_b.resolve(),
                      {"files": len(_tree(b_proj)), "same": _tree(b_proj) == _tree(proj)}, [])
            code, again_b, _ = cli(env_b, "cloud", "download", pid)
            code, listing, _ = cli(env_b, "cloud", "projects")
            rec.check(S, "downloading again makes knight-quest-2.sprites, never merging into the first; the listing "
                         "now knows the local folder",
                      Path(again_b["path"]).name == "knight-quest-2.sprites" and Path(again_b["path"]).is_dir()
                      and next(x for x in listing["projects"] if x["id"] == pid)["local_path"] is not None,
                      Path(again_b["path"]).name, ["C24"])
            shutil.rmtree(again_b["path"])

            # scenario 4: A exports, B pulls; B deletes, A pulls the deletion
            anim = _export(c, "idle")
            c.post("/api/project/sync/now")
            code, res_b, _ = cli(env_b, "sync", "--project", str(b_proj))
            idle_files = [p for p in _tree(proj) if p.startswith(f"animations/{anim}/")]
            rec.check(S, "an animation exported on A arrives on B with the same bytes",
                      code == 0 and idle_files and all(p in res_b["pulled"] for p in idle_files)
                      and _tree(b_proj) == _tree(proj), len(idle_files), [])
            gone = f"animations/{anim}/final/preview.gif"
            (b_proj / gone).unlink()
            code, res_b, _ = cli(env_b, "sync", "--project", str(b_proj))
            res_a = c.post("/api/project/sync/now").json()
            rec.check(S, "a file deleted on B is deleted on A", gone in res_b["pushed"] and gone in res_a["pulled"]
                      and not (proj / gone).exists() and _tree(b_proj) == _tree(proj), gone, [])

            # scenario 5: both change the knight's character.json; A keeps its own
            cj = "characters/knight/character.json"
            _edit_json(b_proj / cj, description="Changed on machine B.")
            cli(env_b, "sync", "--project", str(b_proj))
            _edit_json(proj / cj, description="Changed on machine A.")
            res_a = c.post("/api/project/sync/now").json()
            st = c.get("/api/project/sync").json()
            groups = [g["group"] for g in st["conflicts"]]
            rec.check(S, "both machines changed character.json: one conflict group for the knight's folder; the rest "
                         "keeps syncing", res_a["conflicts"] == 1 and groups == ["characters/knight/"]
                      and json.loads((proj / cj).read_text())["description"] == "Changed on machine A.", groups, ["C8"])
            r = c.post("/api/project/sync/resolve", json={"choices": [{"group": "characters/knight/", "keep": "mine"}]})
            forced = fc.events("commit")[-1]
            code, res_b, _ = cli(env_b, "sync", "--project", str(b_proj))
            rec.check(S, "keeping this machine's version re-commits it with force; B then pulls A's version",
                      r.json()["open"] == 0 and cj in forced["force"] and cj in res_b["pulled"]
                      and json.loads((b_proj / cj).read_text())["description"] == "Changed on machine A.",
                      r.json(), ["C8"])

            # C11: deleted on A, changed on B; A keeps the deletion
            notes = "characters/knight/notes.txt"
            (proj / notes).write_text("first")
            c.post("/api/project/sync/now")
            cli(env_b, "sync", "--project", str(b_proj))
            (b_proj / notes).write_text("changed on B")
            cli(env_b, "sync", "--project", str(b_proj))
            (proj / notes).unlink()
            res_a = c.post("/api/project/sync/now").json()
            conflict = c.get("/api/project/sync").json()["conflicts"]
            c.post("/api/project/sync/resolve", json={"choices": [{"path": notes, "keep": "mine"}]})
            code, res_b, _ = cli(env_b, "sync", "--project", str(b_proj))
            rec.check(S, "deleted here and changed there is a conflict; keeping the deletion deletes it on B too",
                      res_a["conflicts"] == 1 and conflict and conflict[0]["files"][0]["local"] is None
                      and not (b_proj / notes).exists() and not (proj / notes).exists(), res_a["conflicts"], ["C11"])

            # C23: the project moves to a team on the website; both machines follow
            team = fc.add_team("Aurora Studio", {user.id: "owner"})
            with fc.lock:
                fc._copy_blobs(user.id, team.id, [r["sha256"] for r in fc.files[pid].values() if r["sha256"]])
                fc.projects[pid]["ownerId"], fc.projects[pid]["ownerType"] = team.id, "org"
            code, res_b, _ = cli(env_b, "sync", "--project", str(b_proj))
            res_a = c.post("/api/project/sync/now").json()
            owner_a = json.loads((proj / "project.json").read_text())["cloud"]["owner_id"]
            owner_b = json.loads((b_proj / "project.json").read_text())["cloud"]["owner_id"]
            st = c.get("/api/project/sync").json()
            rec.check(S, "a project moved to a team picks up the new owner on both machines, with no conflict",
                      owner_a == owner_b == team.id and res_a["conflicts"] == 0 and st["workspace"] == "Aurora Studio"
                      and _tree(b_proj) == _tree(proj), st.get("workspace"), ["C23"])

            # C29: a copied folder asks before any push
            copy = lib_a / "copy.sprites"
            shutil.copytree(proj, copy)
            c.post("/api/library/open", json={"path": str(copy)})
            st = c.get("/api/project/sync").json()
            blocked = c.post("/api/project/sync/now")
            commits = len(fc.events("commit"))
            kept = c.post("/api/project/sync/copied", json={"keep": True}).json()
            rec.check(S, "a copied folder is asked about before syncing; keeping it here reconciles with no new commit",
                      st["copied"] is not None and blocked.status_code == 409 and blocked.json()["error"]["code"] == "copied"
                      and kept["copied"] is None and kept["conflicts"] == [] and len(fc.events("commit")) == commits,
                      blocked.json()["error"]["code"], ["C29"])
            copy2 = lib_a / "copy2.sprites"
            shutil.copytree(proj, copy2)
            c.post("/api/library/open", json={"path": str(copy2)})
            sep = c.post("/api/project/sync/copied", json={"keep": False}).json()
            rec.check(S, "making a copy separate removes its link and its sync state",
                      sep["linked"] is False and "cloud" not in json.loads((copy2 / "project.json").read_text())
                      and not (copy2 / ".spriteplay" / "sync").exists(), sep, ["C29"])
            rec.check(S, "no cache, job, .spriteplay, ledger or temporary path ever reached a commit",
                      _bad_paths(_committed_paths(fc)) == [], len(_committed_paths(fc)), ["C27"])
    finally:
        eng.stop()
        fc.stop()


def test_cloud_sync_limits(rec, work):
    """Plans and pauses: the trial's project limit, a License, quota, a file too large, an inactive
    team (read-only), a project trashed on the website, going offline (scenario 9; C14–C16, C19, C22)."""
    S = "cloud-limits"
    fc = FakeCloud().start()
    trial = fc.add_user("trial@example.com", "Trial Test")
    lib = work / S / "machine"
    env = machine_env(fc.url, lib)
    eng = Engine(free_port(), env, work / S / "engine.log")
    try:
        with eng.client() as c:
            browser_sign_in(c)
            names = ["One", "Two", "Three", "Four"]
            codes = []
            for n in names:
                c.post("/api/library/projects", json={"name": n, "style": {"kind": "pixel"}, "engine": "godot"})
                r = c.post("/api/project/sync/link", json={"owner": "me"})
                codes.append(r.status_code if r.status_code != 402 else r.json()["error"]["code"])
            four = Path(c.get("/api/project").json()["path"])
            rec.check(S, "the trial syncs three projects; a fourth link is refused with project_limit and not created",
                      codes == [200, 200, 200, "project_limit"] and "cloud" not in json.loads((four / "project.json").read_text()),
                      codes, ["C16"])

            trial.license_until = fc.now() + 365 * 86400
            trial.trial_ends = fc.now() - 1
            c.post("/api/cloud/access/refresh")
            r = c.post("/api/project/sync/link", json={"owner": "me"})
            rec.check(S, "a License links nothing: plan_required, with the server's message",
                      r.status_code == 402 and r.json()["error"]["code"] == "plan_required"
                      and "License covers the desktop app" in r.json()["detail"], r.json()["error"]["code"], ["C16"])

            # quota and file size, on Pro with small limits
            trial.pro = True
            trial.limits = {"storageBytes": 30_000, "maxFileBytes": None}
            c.post("/api/library/projects", json={"name": "Quota", "style": {"kind": "pixel"}, "engine": "godot"})
            proj = Path(c.get("/api/project").json()["path"])
            (proj / "characters" / "big.bin").write_bytes(os.urandom(40_000))
            r = c.post("/api/project/sync/link", json={"owner": "me"})
            st = c.get("/api/project/sync").json()
            pid = st["project_id"]
            rec.check(S, "over quota nothing is committed; the project pauses pushes with the server's reason",
                      st["paused"] and st["paused"]["code"] == "quota_exceeded" and fc.files[pid] == {}
                      and (proj / "characters" / "big.bin").is_file(), st["paused"] and st["paused"]["code"], ["C14"])
            trial.limits = {"storageBytes": None, "maxFileBytes": 20_000}
            res = c.post("/api/project/sync/now").json()
            st = c.get("/api/project/sync").json()
            rec.check(S, "a file larger than the plan allows is left out and listed; everything else syncs",
                      "characters/big.bin" not in _cloud_tree(fc, pid) and "project.json" in _cloud_tree(fc, pid)
                      and any(f["code"] == "file_too_large" and "characters/big.bin" in f["paths"] for f in res["findings"])
                      and st["paused"] is None, [f["message"] for f in res["findings"]], ["C14"])
            trial.limits = None

            # an inactive team: read-only
            team = fc.add_team("Aurora Studio", {trial.id: "member"})
            c.post("/api/library/projects", json={"name": "Team Game", "style": {"kind": "pixel"}, "engine": "godot"})
            tproj = Path(c.get("/api/project").json()["path"])
            r = c.post("/api/project/sync/link", json={"owner": team.id})
            team.active = False
            (tproj / "characters" / "new.txt").write_text("after the plan lapsed")
            r2 = c.post("/api/project/sync/now")
            st = c.get("/api/project/sync").json()
            me = c.get("/api/cloud/status", params={"refresh": "true"}).json()
            rec.check(S, "an inactive team is read-only: the push pauses with team_inactive; the team shows the state",
                      r.status_code == 200 and r2.status_code == 409 and st["paused"]["code"] == "team_inactive"
                      and "has no active Team plan" in st["paused"]["message"]
                      and [t["active"] for t in me["teams"]] == [False], st["paused"]["code"], ["C15"])
            r3 = c.post("/api/project/sync/now", json={"push": False, "pull": True})
            rec.check(S, "pulls keep working for an inactive team", r3.status_code == 200, r3.status_code, ["C15"])
            team.active = True

            # C22: trashed on the website
            with fc.lock:
                fc.projects[st["project_id"]]["deletedAt"] = fc.now()
            r = c.post("/api/project/sync/now")
            st = c.get("/api/project/sync").json()
            rec.check(S, "a project trashed on the website pauses syncing with restore and stop actions to offer",
                      r.status_code == 409 and st["paused"]["code"] == "removed" and "Restore it" in st["paused"]["message"],
                      st["paused"]["code"], ["C22"])
            with fc.lock:
                fc.projects[st["project_id"]]["deletedAt"] = None
            c.post("/api/project/sync/now")

            # C19: offline shows as a state, not an error, and clears when back
            fc.stop()
            r = c.post("/api/project/sync/now")
            st = c.get("/api/project/sync").json()
            fc.start()
            r2 = c.post("/api/project/sync/now")
            st2 = c.get("/api/project/sync").json()
            rec.check(S, "offline, the project says changes will sync when connected; it clears once back",
                      r.status_code == 503 and st["offline"] and st["offline_message"].startswith("Offline")
                      and r2.status_code == 200 and not st2["offline"], [r.status_code, st["offline"]], ["C19"])
    finally:
        eng.stop()
        fc.stop()


def test_cloud_sync_robustness(rec, work):
    """A file changing mid-upload, a concurrent commit (409 retry), a download that breaks off, a
    damaged download, the project lock between processes, remote paths that differ only in case, and
    a running job's folder (scenarios 6, 7; C7, C9, C10, C17, C25, C28, C30)."""
    S = "cloud-robust"
    fc = FakeCloud().start()
    fc.add_user("robust@example.com", "Robust Test", pro=True)
    lib_a, lib_b = work / S / "machine-a", work / S / "machine-b"
    env_a, env_b = machine_env(fc.url, lib_a), machine_env(fc.url, lib_b, SPRITEGURU_SYNTH_DELAY="0.6")
    eng = Engine(free_port(), env_a, work / S / "engine-a.log")
    try:
        with eng.client() as c:
            browser_sign_in(c)
            proj = _make_project(c, "Robust")
            pid = c.post("/api/project/sync/link", json={"owner": "me"}).json()["project"]["id"]

            # C7: a file rewritten while it uploads is left for the next round, never committed stale
            target = proj / "characters" / "knight" / "notes.txt"
            target.write_text("version one")
            old = hashlib.sha256(b"version one").hexdigest()

            def rewrite(sha):
                if sha == old:
                    target.write_text("version two, written during the upload")
                    fc.upload_hook = None

            fc.upload_hook = rewrite
            first = c.post("/api/project/sync/now").json()
            second = c.post("/api/project/sync/now").json()
            stale = [e for e in fc.events("commit") if "characters/knight/notes.txt" in e["paths"]]
            rec.check(S, "a file that changes while it uploads is skipped that round and committed with its new bytes next",
                      "characters/knight/notes.txt" not in first["pushed"] and "characters/knight/notes.txt" in second["pushed"]
                      and _cloud_tree(fc, pid)["characters/knight/notes.txt"]
                      == hashlib.sha256(target.read_bytes()).hexdigest() and len(stale) == 1,
                      [first["pushed"], second["pushed"]], ["C7"])

            # C9: another machine commits at the same moment
            target.write_text("version three")
            fc.retry_commits = 1
            res = c.post("/api/project/sync/now").json()
            rec.check(S, "a 409 retry is followed by a pull and a recommit",
                      len(fc.events("retry_injected")) == 1 and "characters/knight/notes.txt" in res["pushed"]
                      and res["rev"] == fc.projects[pid]["headRev"], res["rev"], ["C9"])
            fc.retry_commits = 9
            target.write_text("version four")
            r = c.post("/api/project/sync/now")
            fc.retry_commits = 0
            rec.check(S, "after three retries the project pauses with a message instead of looping",
                      r.status_code == 409 and r.json()["error"]["code"] == "retry", r.json()["error"]["code"], ["C9"])
            c.post("/api/project/sync/now")

            # machine B downloads; then C10 and C28 on its pulls
            cli_login(env_b)
            code, got, _ = cli(env_b, "cloud", "download", pid)
            b_proj = Path(got["path"])
            f1, f2 = proj / "characters" / "knight" / "a.bin", proj / "characters" / "knight" / "b.bin"
            f1.write_bytes(os.urandom(300_000))
            f2.write_bytes(os.urandom(300_000))
            c.post("/api/project/sync/now")
            rev_before = json.loads((b_proj / ".spriteplay" / "sync" / "state.json").read_text())["rev"]
            fc.drop_downloads = {hashlib.sha256(f2.read_bytes()).hexdigest()}
            code1, out1, _ = cli(env_b, "sync", "--project", str(b_proj))
            rev_mid = json.loads((b_proj / ".spriteplay" / "sync" / "state.json").read_text())["rev"]
            code2, out2, _ = cli(env_b, "sync", "--project", str(b_proj))
            rec.check(S, "a download that breaks off leaves the revision unchanged; the next pull resumes and finishes",
                      code1 == 1 and out1["error"]["code"] == "offline" and rev_mid == rev_before and code2 == 0
                      and len(fc.events("dropped")) == 1 and _tree(b_proj) == _tree(proj), [code1, code2], ["C10"])

            f1.write_bytes(os.urandom(1000))
            c.post("/api/project/sync/now")
            fc.corrupt_downloads = {hashlib.sha256(f1.read_bytes()).hexdigest(): 1}
            code, out, _ = cli(env_b, "sync", "--project", str(b_proj))
            once = code == 0 and (b_proj / "characters/knight/a.bin").read_bytes() == f1.read_bytes()
            f1.write_bytes(os.urandom(1000))
            c.post("/api/project/sync/now")
            fc.corrupt_downloads = {hashlib.sha256(f1.read_bytes()).hexdigest(): 2}
            code, out, _ = cli(env_b, "sync", "--project", str(b_proj))
            rec.check(S, "a damaged download is discarded and retried; damaged twice, the pull pauses with a finding",
                      once and code == 5 and out["error"]["code"] == "hash_mismatch"
                      and (b_proj / "characters/knight/a.bin").read_bytes() != f1.read_bytes()
                      and not any((b_proj / ".spriteplay" / "sync" / "tmp").glob("*")), out["error"]["code"], ["C28"])
            cli(env_b, "sync", "--project", str(b_proj))

            # C25: one project, two processes
            from spriteguru.filelock import FileLock

            lk = FileLock(b_proj / ".spriteplay" / "sync" / "lock")
            lk.acquire()
            code, out, _ = cli(env_b, "sync", "--wait", "1", "--project", str(b_proj))
            lk.release()
            holder = subprocess.Popen([sys.executable, "-c", (
                "import os,sys; sys.path.insert(0,'src'); from spriteguru.filelock import FileLock; "
                f"FileLock(__import__('pathlib').Path({str(b_proj / '.spriteplay' / 'sync' / 'lock')!r})).acquire(); "
                "os._exit(0)")], cwd=ROOT, env=env_b)
            holder.wait(30)
            t0 = time.time()
            code2, _, _ = cli(env_b, "sync", "--wait", "5", "--project", str(b_proj))
            rec.check(S, "a project syncing in another process is skipped with a message; a crashed holder's lock is free",
                      code == 4 and out["error"]["message"] == "Syncing in another window." and code2 == 0
                      and time.time() - t0 < 20, [code, code2], ["C25"])

            # C17: remote paths that differ only in case
            (proj / "labels").mkdir(exist_ok=True)
            (proj / "labels" / "Readme.txt").write_text("mixed case")
            c.post("/api/project/sync/now")
            data = b"upper case"
            sha = hashlib.sha256(data).hexdigest()
            owner = fc.projects[pid]["ownerId"]
            with fc.lock:  # another client (on a case-sensitive disk) commits labels/README.txt
                fc.blobs[owner][sha] = {"size": len(data), "type": "text/plain", "data": data}
                p = fc.projects[pid]
                fc.files[pid]["labels/README.txt"] = {"path": "labels/README.txt", "sha256": sha, "size": len(data),
                                                      "mtime": None, "rev": p["headRev"] + 1, "deleted": False,
                                                      "updatedAt": fc.now(), "updatedBy": "someone"}
                p["headRev"] += 1
            code, out, _ = cli(env_b, "sync", "--project", str(b_proj))
            clash = [f for f in out.get("findings", []) if f["code"] == "case_clash"]
            names = sorted(x.name for x in (b_proj / "labels").iterdir())
            rec.check(S, "a remote path differing only in case isn't written over the other; a finding names both",
                      code == 0 and clash and {clash[0]["path"], clash[0]["other"]} == {"labels/README.txt", "labels/Readme.txt"}
                      and len([n for n in names if n.lower() == "readme.txt"]) == 1, [clash[:1], names], ["C17"])

            # C30: machine B's engine runs a job; A's changes to that animation wait until it ends
            eng_b = Engine(free_port(), env_b, work / S / "engine-b.log", project=b_proj)
            try:
                with eng_b.client() as cb:
                    run = cb.post("/api/animations/knight-walk-E/jobs", json={"seed": 7}).json()["id"]
                    (proj / "animations" / "knight-walk-E" / "spec-note.txt").write_text("from A during B's job")
                    c.post("/api/project/sync/now")
                    during = cb.post("/api/project/sync/now", json={"push": False, "pull": True}).json()
                    running = cb.get(f"/api/jobs/{run}").json()["state"] not in ("done", "failed", "cancelled")
                    _wait_job(cb, run)
                    after = cb.post("/api/project/sync/now", json={"push": False, "pull": True}).json()
                rec.check(S, "a running job's animation folder is not pulled into until the job ends",
                          running and "animations/knight-walk-E/spec-note.txt" not in during["pulled"]
                          and "animations/knight-walk-E/spec-note.txt" in after["pulled"], [running, len(during["pulled"])],
                          ["C30"])
            finally:
                eng_b.stop()
            rec.check(S, "no cache, job, .spriteplay, ledger or temporary path ever reached a commit",
                      _bad_paths(_committed_paths(fc)) == [], len(_committed_paths(fc)), ["C27"])
    finally:
        eng.stop()
        fc.stop()
