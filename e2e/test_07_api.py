"""The local API end to end over HTTP and WebSocket: security guards, a character and an animation
driven only through the API, hand fixes in the frame editor, winner choice and layout confirmation."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import websockets

from conftest import free_port
from test_06_jobs import _serve, _stop


def test_api(rec, work):
    S = "api"
    from conftest import sk

    proj = work / "api" / "A.sprites"
    sk("init", str(proj), "--style", "hd-cartoon", "--engine", "phaser", "--mode", "synthetic")
    port = free_port()
    srv = _serve(proj, port, {})
    base = f"http://127.0.0.1:{port}"
    H = {"X-SpriteKit-Token": "t0k"}
    try:
        rec.check(S, "no token -> 401", httpx.get(f"{base}/api/project").status_code == 401)
        rec.check(S, "wrong token -> 401", httpx.get(f"{base}/api/project", headers={"X-SpriteKit-Token": "x"}).status_code == 401)
        rec.check(S, "foreign Host header -> 403 (DNS rebinding)",
                  httpx.get(f"{base}/api/project", headers={**H, "Host": "evil.example"}).status_code == 403)
        r = httpx.options(f"{base}/api/project", headers={"Origin": "https://evil.example",
                                                            "Access-Control-Request-Method": "GET"})
        rec.check(S, "CORS does not allow foreign origins", "access-control-allow-origin" not in r.headers,
                  dict(r.headers).get("access-control-allow-origin"))
        proj_info = httpx.get(f"{base}/api/project", headers=H).json()
        from spriteguru import keys

        leaked = [p for p in keys.ENV if keys.get(p) and keys.get(p) in json.dumps(proj_info)]
        rec.check(S, "project endpoint never returns key values", not leaked, leaked, ["P7"])

        async def flow():
            events: list[dict] = []
            async with websockets.connect(f"ws://127.0.0.1:{port}/api/events?token=t0k") as ws:
                async def pump():
                    async for m in ws:
                        events.append(json.loads(m))

                task = asyncio.create_task(pump())
                async with httpx.AsyncClient(base_url=base, headers=H, timeout=120) as c:
                    async def wait(pred, timeout=240):
                        t0 = time.time()
                        while time.time() - t0 < timeout:
                            hit = next((e for e in events if pred(e)), None)
                            if hit:
                                return hit
                            await asyncio.sleep(0.1)
                        raise TimeoutError(f"no event matching; last: {events[-3:]}")

                    (await c.post("/api/characters", json={"name": "mage", "description":
                                                           "A caped mage in a purple robe holding a staff."})).raise_for_status()
                    await wait(lambda e: e.get("type") == "turnaround_done")
                    ch = (await c.post("/api/characters/mage/approve")).json()
                    rec.check(S, "character created and approved via API", ch["approved"] and len(ch["views"]) == 4)
                    a = (await c.post("/api/animations", json={"character": "mage", "action": "cast"})).json()
                    est = (await c.get(f"/api/animations/{a['id']}/estimate")).json()
                    g = await c.get(est["guide_url"])
                    rec.check(S, "estimate shows route, cost and the guide canvas before sending",
                              est["compiled"]["route"] == "guided" and g.status_code == 200 and
                              g.headers["content-type"] == "image/png", est["compiled"]["route"])
                    job = (await c.post(f"/api/animations/{a['id']}/jobs", json={"seed": 1})).json()
                    done = await wait(lambda e: e.get("type") in ("job_done", "job_failed") and e.get("job") == job["id"])
                    rec.check(S, "job finished via API", done["type"] == "job_done", done.get("error"))
                    kinds = {e["type"] for e in events if e.get("job") == job["id"]}
                    rec.check(S, "WebSocket streamed step, candidate, decision events",
                              {"step_started", "step_done", "candidate_scored", "decision", "job_done"} <= kinds,
                              sorted(kinds))
                    st = (await c.get(f"/api/jobs/{job['id']}")).json()
                    rep = (await c.get(f"/api/jobs/{job['id']}/candidates/{st['winner']}/report")).json()
                    rec.check(S, "candidate report served", rep["score"] == next(x["score"] for x in st["candidates"]
                                                                                 if x["id"] == st["winner"]))
                    rec.check(S, "matte and removed-marks images saved for sheet review",
                              all("matte" in x and "removed" in x for x in st["candidates"]))
                    # frame editor: nudge, flip, duration, pivot, reorder -> immediate re-export
                    before = json.loads((proj / "animations" / a["id"] / "final" / "animation.json").read_text())
                    ed = [{"op": "duration", "frame": 0, "ms": 200}, {"op": "nudge", "frame": 1, "dx": 3, "dy": -2},
                          {"op": "flip", "frame": 2}, {"op": "pivot", "x": 0.5, "y": 0.9},
                          {"op": "reorder", "order": [0, 1, 2, 3, 5, 4]}]
                    res = (await c.post(f"/api/animations/{a['id']}/edits", json={"edits": ed})).json()
                    after = json.loads((proj / "animations" / a["id"] / "final" / "animation.json").read_text())
                    rec.check(S, "frame edits re-export immediately",
                              after["durations"][0] == 200 and after["pivot"] == [0.5, 0.9] and
                              after["durations"][4] == before["durations"][5], after["durations"])
                    from validate import check_final

                    v = check_final(proj / "animations" / a["id"] / "final", "phaser")
                    rec.check(S, "edited export still agrees with its sheet", v["ok"], v["errors"], ["E2"])
                    bad = await c.post(f"/api/animations/{a['id']}/edits", json={"edits": [{"op": "reorder", "order": [0, 0]}]})
                    rec.check(S, "invalid edit rejected", bad.status_code == 400)
                    # winner choice and layout confirmation
                    other = next(x["id"] for x in st["candidates"] if x["id"] != st["winner"])
                    (await c.post(f"/api/jobs/{job['id']}/winner", json={"candidate": other})).raise_for_status()
                    st2 = (await c.get(f"/api/jobs/{job['id']}")).json()
                    rec.check(S, "hand-picked winner exported", st2["winner"] == other, other)
                    rep = (await c.get(f"/api/jobs/{job['id']}/candidates/{st['winner']}/report")).json()
                    cells = [f["region"] for f in rep["layout"]["frames"]]
                    ent = (await c.post(f"/api/jobs/{job['id']}/layout",
                                        json={"candidate": st["winner"], "cells": cells})).json()
                    rec.check(S, "confirmed layout re-analyzed and saved as a label", "score" in ent and
                              any((proj / "labels").glob("*.json")), ent.get("score"), ["L13"])
                    costs = (await c.get(f"/api/animations/{a['id']}/remedy-costs")).json()
                    rec.check(S, "remedy costs listed", {"frame_repair", "reroll", "confirm_layout"} <= set(costs))
                    led = (await c.get("/api/ledger")).json()
                    rec.check(S, "ledger endpoint", led["summary"]["calls"] > 0, led["summary"]["calls"])
                task.cancel()

        asyncio.run(flow())
    finally:
        _stop(srv)
