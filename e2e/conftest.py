"""E2E harness: every scenario drives the real CLI, API or studio, records checks, and the session
writes one artifact folder: summary.json, index.html, copied outputs and a digest of the
normalized results. Offline runs are deterministic, so the digest must repeat across runs.

    uv run pytest e2e                    # offline (synthetic providers), free; parallel (-n)
    uv run pytest e2e --quick            # skips the evaluation matrices: the fast development loop
    uv run pytest e2e -n 0               # the same, one process
    uv run pytest e2e --live             # also the live scenarios (real providers, real spend)

Scenarios run in parallel with pytest-xdist. The controller fixes the run id and cleans the folders
once; each worker writes its checks to `parts/<worker>.json`, and the controller merges them into
the one summary and digest. The digest sorts everything, so it does not depend on worker order.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ART_ROOT = ROOT / "e2e" / "artifacts"
WORK = ROOT / "e2e" / "work"


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="run live scenarios against real providers (spends money)")
    parser.addoption("--run-id", default=None, help="artifact folder name")
    parser.addoption("--quick", action="store_true",
                     help="skip the full evaluation matrices (generation set, pixel benchmark) for a fast loop")


def _is_worker(config) -> bool:
    return hasattr(config, "workerinput")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs real provider keys and spends money")
    config.addinivalue_line("markers", "full: an evaluation matrix; skipped by --quick")
    if _is_worker(config):
        # workers get only the command line, so the controller's run id arrives in workerinput
        config.option.run_id = config.workerinput["spriteguru_run_id"]
    else:
        # the controller fixes the run id and cleans once
        rid = config.getoption("--run-id") or dt.datetime.now().strftime("run-%Y%m%d-%H%M%S")
        config.option.run_id = rid
        if not config.option.collectonly:
            d = ART_ROOT / rid
            if d.exists():
                shutil.rmtree(d)
            (d / "parts").mkdir(parents=True)
            w = WORK / rid
            if w.exists():
                shutil.rmtree(w)
            _prune_work(rid)
            w.mkdir(parents=True)
    # engines started by the tests record projects in a library of their own, never the user's gallery
    os.environ["SPRITEGURU_LIBRARY"] = str(WORK / config.option.run_id / "_library")
    # Tests use a file-backed keyring per library and never touch the user's real keychain.
    support = str(ROOT / "e2e" / "support")
    os.environ["PYTHON_KEYRING_BACKEND"] = "filekeyring.FileKeyring"
    os.environ["PYTHONPATH"] = os.pathsep.join([support, *filter(None, [os.environ.get("PYTHONPATH")])])
    if support not in sys.path:
        sys.path.insert(0, support)


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    """xdist: hand each worker the controller's run id."""
    node.workerinput["spriteguru_run_id"] = node.config.option.run_id


def pytest_sessionfinish(session, exitstatus):
    if _is_worker(session.config) or session.config.option.collectonly:
        return
    write_artifact(ART_ROOT / session.config.option.run_id, session.config)


def free_port() -> int:
    """A free local port for a test server (parallel scenarios must not share fixed ports)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# the longest scenarios start first, so parallel workers finish together (seconds, measured)
SLOW_FIRST = ["test_generation_set", "test_golden_set", "test_image_references", "test_repair_loop",
              "test_studio_projects", "test_layout_strip_and_confirm", "test_subject_vehicles",
              "test_cancel_and_concurrency", "test_subject_styles", "test_inbetween_and_judge_calibration",
              "test_character_animations"]


def pytest_collection_modifyitems(config, items):
    rank = {name: i for i, name in enumerate(SLOW_FIRST)}
    items.sort(key=lambda it: rank.get(it.originalname if hasattr(it, "originalname") else it.name, len(rank)))
    if config.getoption("--quick"):
        quick = pytest.mark.skip(reason="evaluation matrix: runs without --quick")
        for item in items:
            if "full" in item.keywords:
                item.add_marker(quick)
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live scenario: pass --live to run (spends money)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


class Recorder:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.checks: list[dict] = []
        self.files: list[dict] = []
        self.notes: dict[str, dict] = {}

    def check(self, scenario: str, name: str, ok: bool, value=None, covers: list[str] | None = None,
              stable: bool = True) -> bool:
        self.checks.append({"scenario": scenario, "check": name, "pass": bool(ok), "value": _jsonable(value),
                            "covers": covers or [], "stable": stable})
        return bool(ok)

    def keep(self, scenario: str, src: Path, name: str | None = None, digest: bool = True) -> Path:
        """Copy an output into the artifact; its sha256 joins the digest when `digest`."""
        dst = self.run_dir / "outputs" / scenario / (name or src.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            h = None
        else:
            shutil.copy2(src, dst)
            h = hashlib.sha256(src.read_bytes()).hexdigest()
        self.files.append({"scenario": scenario, "path": dst.relative_to(self.run_dir).as_posix(), "sha256": h,
                           "in_digest": digest and h is not None})
        return dst

    def note(self, scenario: str, **kv):
        self.notes.setdefault(scenario, {}).update({k: _jsonable(v) for k, v in kv.items()})


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


@pytest.fixture(scope="session")
def run_dir(request) -> Path:
    d = ART_ROOT / request.config.option.run_id
    (d / "parts").mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="session")
def rec(run_dir, request) -> Recorder:
    r = Recorder(run_dir)
    yield r
    worker = getattr(request.config, "workerinput", {}).get("workerid", "main")
    (run_dir / "parts" / f"{worker}.json").write_text(json.dumps(
        {"checks": r.checks, "files": r.files, "notes": r.notes}, default=str))


KEEP_WORK = 3  # offline work folders are ~2 GB each and regenerable; live ones hold paid outputs


def _prune_work(current: str) -> None:
    """Keep the newest few offline work folders; never touch live-run folders or the current one."""
    if not WORK.is_dir():
        return
    offline = sorted((d for d in WORK.iterdir() if d.is_dir() and "live" not in d.name and d.name != current),
                     key=lambda d: d.stat().st_mtime, reverse=True)
    for d in offline[KEEP_WORK:]:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session")
def work(run_dir) -> Path:
    d = WORK / run_dir.name
    d.mkdir(parents=True, exist_ok=True)
    return d


def sk(*args: str, cwd: Path | None = None, env: dict | None = None, check: bool = True,
       timeout: int = 900) -> dict:
    """Run the spriteguru CLI; returns {code, json (last JSON line on stdout), stdout, stderr}."""
    e = {**os.environ, "SPRITEGURU_LOG": "warning", **(env or {})}
    p = subprocess.run([sys.executable, "-m", "spriteguru.cli", *args], cwd=cwd or ROOT, env=e, capture_output=True,
                       text=True, timeout=timeout)
    data = None
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        pass
    for line in ([] if data is not None else reversed(p.stdout.strip().splitlines())):
        line = line.strip()
        if line.startswith("{") or line.startswith("["):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if check and p.returncode != 0:
        raise AssertionError(f"spriteguru {' '.join(args)} failed ({p.returncode}):\n{p.stderr[-3000:]}")
    return {"code": p.returncode, "json": data, "stdout": p.stdout, "stderr": p.stderr}


def write_artifact(run_dir: Path, config) -> None:
    """Merge every worker's part into the run's summary, digest and index."""
    r = Recorder(run_dir)
    for part in sorted((run_dir / "parts").glob("*.json")):
        data = json.loads(part.read_text())
        r.checks += data["checks"]
        r.files += data["files"]
        for k, v in data["notes"].items():
            r.notes.setdefault(k, {}).update(v)
    order = lambda c: (c["scenario"], c["check"], json.dumps(c["value"], sort_keys=True))  # noqa: E731
    r.checks.sort(key=order)
    r.files.sort(key=lambda f: (f["scenario"], f["path"]))
    stable = {
        "checks": sorted(({k: c[k] for k in ("scenario", "check", "pass", "value")} for c in r.checks if c["stable"]),
                         key=order),
        "files": sorted((f["path"], f["sha256"]) for f in r.files if f["in_digest"]),
    }
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    passed = sum(c["pass"] for c in r.checks)
    covers = sorted({m for c in r.checks if c["pass"] for m in c["covers"]})
    failing = sorted({m for c in r.checks if not c["pass"] for m in c["covers"]})
    summary = {"run": r.run_dir.name, "time": dt.datetime.now().isoformat(timespec="seconds"),
               "live": bool(config.getoption("--live")), "digest": digest, "checks_total": len(r.checks),
               "checks_passed": passed, "all_passed": passed == len(r.checks),
               "failure_modes_verified": covers, "failure_modes_failing": failing,
               "checks": r.checks, "files": r.files, "notes": r.notes}
    (r.run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (r.run_dir / "digest.txt").write_text(digest + "\n")
    (r.run_dir / "index.html").write_text(_html(summary))
    latest = ART_ROOT / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(r.run_dir.name)
    except OSError:
        pass


def _html(s: dict) -> str:
    from spriteguru.report import page

    by: dict[str, list] = {}
    for c in s["checks"]:
        by.setdefault(c["scenario"], []).append(c)
    parts = [f"<h1>SpriteGuru E2E</h1><p class='muted'>{html.escape(s['run'])} · {s['time']} · "
             f"{'live + offline' if s['live'] else 'offline (synthetic providers)'}</p>",
             f"<div class='card'><b>{s['checks_passed']}/{s['checks_total']}</b> checks pass · digest "
             f"<code>{s['digest'][:16]}</code><div class='muted'>failure modes verified: "
             f"{html.escape(', '.join(s['failure_modes_verified']))}</div>"
             f"<div class='muted'>failing: {html.escape(', '.join(s['failure_modes_failing']) or 'none')}</div></div>"]
    for scen, checks in by.items():
        rows = "".join(f"<tr><td><span class='pill {'ok' if c['pass'] else 'fail'}'>{'pass' if c['pass'] else 'fail'}"
                       f"</span></td><td>{html.escape(c['check'])}</td><td><code>{html.escape(json.dumps(c['value'])[:300])}"
                       f"</code></td><td>{html.escape(', '.join(c['covers']))}</td></tr>" for c in checks)
        imgs = "".join(f"<figure style='margin:6px;display:inline-block;max-width:420px'><img src='{f['path']}' "
                       f"style='max-height:220px'><figcaption class='muted'>{html.escape(f['path'].split('/')[-1])}"
                       f"</figcaption></figure>"
                       for f in s["files"] if f["scenario"] == scen and f["path"].endswith((".png", ".gif")))
        links = "".join(f"<li><a href='{f['path']}'>{html.escape(f['path'])}</a></li>" for f in s["files"]
                        if f["scenario"] == scen and f["path"].endswith(".html"))
        note = s["notes"].get(scen)
        parts.append(f"<h2>{html.escape(scen)}</h2><div class='card'><table><tr><th></th><th>Check</th><th>Value</th>"
                     f"<th>Covers</th></tr>{rows}</table>{'<ul>' + links + '</ul>' if links else ''}"
                     f"{'<div>' + imgs + '</div>' if imgs else ''}"
                     f"{'<pre>' + html.escape(json.dumps(note, indent=1)[:4000]) + '</pre>' if note else ''}</div>")
    return page("SpriteGuru E2E", "".join(parts))
