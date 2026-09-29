"""Jobs (J1–J3): a crashed job resumes from its checkpoint without paying twice; cancellation lets
the in-flight call land in the cache; at most three jobs run at once."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from conftest import ROOT, free_port, sk


def test_crash_and_resume(rec, work):
    S = "jobs"
    proj = work / "jobs" / "J.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--mode", "synthetic")
    sk("character", "new", "knight", "--describe", "An armoured knight with a blue tabard and a round shield.",
       "--approve", "--project", str(proj))
    r = sk("gen", "knight", "attack-melee", "--project", str(proj), env={"SPRITEGURU_CRASH_AFTER": "generate-0"},
           check=False)
    rec.check(S, "process died after the generate step", r["code"] == 3, r["code"], ["J1"])
    job = json.loads(next((proj / "animations" / "knight-attack-melee-E" / "jobs").glob("*/job.json")).read_text())
    rec.check(S, "checkpoint shows generate done, job unfinished",
              job["state"] == "running" and [s["name"] for s in job["steps"] if s["status"] == "done"] ==
              ["compile", "generate-0"], [s["name"] for s in job["steps"]], ["J1"])
    before = [json.loads(l) for l in (proj / "ledger.jsonl").read_text().splitlines()]
    res = sk("jobs", "resume", "--project", str(proj))["json"]
    rec.check(S, "resume finishes the job", res and res[0]["state"] == "done", res[0]["state"] if res else None, ["J1"])
    after = [json.loads(l) for l in (proj / "ledger.jsonl").read_text().splitlines()][len(before):]
    gen_calls = [l for l in after if l.get("purpose") == "guided_sheet" and l["status"] in ("sent", "ok")]
    rec.check(S, "resume did not repeat the finished provider call", not gen_calls,
              [l["status"] for l in after if l.get("purpose") == "guided_sheet"], ["J1"])


def _serve(proj: Path, port: int, env: dict) -> subprocess.Popen:
    e = {**os.environ, **env}
    p = subprocess.Popen([sys.executable, "-m", "spriteguru.cli", "serve", "--project", str(proj), "--port", str(port),
                          "--token", "t0k", "--mode", "synthetic"], env=e, cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    deadline = time.time() + 90  # a loaded machine (parallel scenarios) can take a while to start a server
    while time.time() < deadline:
        if p.poll() is not None:
            break
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/project", headers={"X-SpriteGuru-Token": "t0k"}).status_code == 200:
                return p
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    p.kill()
    raise RuntimeError("server did not start")


def _stop(p: subprocess.Popen) -> None:
    p.terminate()
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(5)


def test_cancel_and_concurrency(rec, work):
    S = "jobs"
    # its own project: tests run in parallel and in any order, so none may rely on another's setup
    proj = work / "jobs-concurrency" / "J.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--mode", "synthetic")
    sk("character", "new", "knight", "--describe", "An armoured knight with a blue tabard and a round shield.",
       "--approve", "--project", str(proj))
    port = free_port()
    srv = _serve(proj, port, {"SPRITEGURU_SYNTH_DELAY": "1.0"})
    H = {"X-SpriteGuru-Token": "t0k"}
    base = f"http://127.0.0.1:{port}/api"
    try:
        with httpx.Client(headers=H, timeout=60) as c:
            # J2: cancel while the generate call is in flight
            c.post(f"{base}/animations", json={"character": "knight", "action": "cast"}).raise_for_status()
            job = c.post(f"{base}/animations/knight-cast-E/jobs", json={"seed": 41}).json()
            jid = job["id"]
            for _ in range(100):
                st = c.get(f"{base}/jobs/{jid}").json()
                if st.get("step") == "generate-0":
                    break
                time.sleep(0.05)
            c.post(f"{base}/jobs/{jid}/cancel")
            for _ in range(200):
                st = c.get(f"{base}/jobs/{jid}").json()
                if st["state"] in ("cancelled", "done", "failed"):
                    break
                time.sleep(0.1)
            done = [s["name"] for s in st["steps"] if s["status"] == "done"]
            rec.check(S, "cancel stops between steps", st["state"] == "cancelled" and "analyze-0" not in done,
                      {"state": st["state"], "done": done}, ["J2"])
            rec.check(S, "the in-flight call finished and landed in the cache", "generate-0" in done and
                      len(st["candidates"]) == 2, len(st["candidates"]), ["J2"])
            # J3: five jobs at once, never more than three running
            ids = []
            for i, action in enumerate(["idle", "hurt", "crouch", "death", "jump"]):
                c.post(f"{base}/animations", json={"character": "knight", "action": action}).raise_for_status()
                ids.append(c.post(f"{base}/animations/knight-{action}-E/jobs", json={"seed": i}).json()["id"])
            peak = 0
            for _ in range(900):
                # one request per sample: five separate GETs on a loaded machine can catch one job just
                # finishing and the next just starting, and count four
                by_id = {j["id"]: j["state"] for j in c.get(f"{base}/jobs").json()}
                states = [by_id.get(j, "queued") for j in ids]
                peak = max(peak, sum(s in ("running", "awaiting_approval") for s in states))
                if all(s in ("done", "failed", "cancelled") for s in states):
                    break
                time.sleep(0.1)
            rec.check(S, "at most three jobs run at once", peak == 3, peak, ["J3"])
            rec.check(S, "all five finished", all(s == "done" for s in states), states)
    finally:
        _stop(srv)
