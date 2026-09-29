"""Generation golden set (16.1–16.2): six characters across action and view combinations, spread
over the four styles so every route in 6.5 is covered. Measures, per route: auto-accept rate, cost
per accepted sheet, repair calls per accepted sheet, and p50/p95 latency."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import time
from pathlib import Path

import numpy as np

from .. import report as report_mod
from .. import service
from ..character import approve, generate_turnaround, new_record
from ..engine import make_runner
from ..project import Project
from ..spec import Style

CHARACTERS = [
    ("humanoid", "hd-cartoon", "A young adventurer in a green tunic with brown boots and a leather satchel."),
    ("chibi", "painted", "A chibi girl with a huge head, pink twin tails and a yellow raincoat."),
    ("quadruped", "hd-cartoon", "A small orange fox with a white-tipped tail and a blue collar."),
    ("robot", "vector", "A friendly round robot with a red scarf, stubby arms and legs."),
    ("mage", "pixel", "A caped mage in a purple robe holding a wooden staff."),
    ("fighter", "hd-cartoon", "An armed fighter in steel armour with a sword and a round shield."),
]
COMBOS = [("idle", "side", "E"), ("walk", "side", "E"), ("run", "side", "E"), ("jump", "side", "E"),
          ("attack-melee", "side", "E"), ("hurt", "side", "E"), ("death", "side", "E"), ("cast", "side", "E"),
          ("walk", "top-down", "S"), ("walk", "top-down", "N")]


def specs(limit: int | None = None) -> list[tuple[str, str, str, str, str]]:
    """Combinations outer, characters inner: any prefix spreads across every style and route."""
    out = [(name, style, desc, action, f"{view}:{facing}") for action, view, facing in COMBOS
           for name, style, desc in CHARACTERS]
    return out[:limit] if limit else out


def _project(root: Path, style: str, mode: str) -> Project:
    d = root / f"{style}.sprites"
    proj = Project.open(d) if (d / "project.json").is_file() else Project.init(
        d, style=Style(kind=style, pixel_height=48 if style == "pixel" else None))
    proj.config.settings.provider_mode = mode
    proj.config.settings.session_cap_usd = 1e9
    proj.config.settings.job_cap_usd = 1e9
    proj.save()
    return proj


async def _yes(_):
    return True


async def _setup(root: Path, mode: str, cases: list) -> None:
    """Projects and approved characters first, one at a time: several cases share each of them."""
    done: set[tuple[str, str]] = set()
    for name, style, desc, _action, _vf in cases:
        if (style, name) in done:
            continue
        done.add((style, name))
        proj = _project(root, style, mode)
        try:
            proj.character(name)
        except Exception:
            runner = make_runner(proj, mode=mode)
            runner.hub.approve = _yes
            new_record(proj, name, desc)
            await generate_turnaround(proj, runner.hub, name)
            approve(proj, name)


async def _case_async(root: Path, mode: str, case: tuple) -> dict:
    name, style, _desc, action, vf = case
    view, facing = vf.split(":")
    proj = Project.open(root / f"{style}.sprites")
    runner = make_runner(proj, mode=mode)
    runner.hub.approve = _yes
    anim_id, spec = service.create_animation(proj, name, action, facing=facing, view=view)
    t0 = time.perf_counter()
    st = runner.create_animation_job(anim_id)
    st = await runner.run(st.id)
    latency = time.perf_counter() - t0
    res = st.result or {}
    return {"case": anim_id, "style": style, "route": st.route, "state": st.state,
            "accepted": bool(res.get("accepted")), "score": res.get("score"),
            "cost": round(runner.hub.ledger.job_spend(st.id), 5), "repairs": st.attempts.get("repairs", 0),
            "rerolls": st.attempts.get("rerolls", 0), "latency_s": round(latency, 2), "error": st.error}


def _case(root: str, mode: str, case: tuple) -> dict:
    """One spec in its own process (a job is CPU-bound image analysis; processes scale, threads do not)."""
    return asyncio.run(_case_async(Path(root), mode, case))


def _rows(root: Path, mode: str, limit: int | None, workers: int) -> list[dict]:
    cases = specs(limit)
    asyncio.run(_setup(root, mode, cases))
    if workers <= 1:
        return [_case(str(root), mode, c) for c in cases]
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=workers) as pool:  # results keep spec order
        return list(pool.map(_case, [str(root)] * len(cases), [mode] * len(cases), cases))


def default_workers(mode: str) -> int:
    """Offline: most of the cores. Live: two, so parallel jobs stay within provider rate limits."""
    from ..parallel import default_workers as cores

    return 2 if mode == "live" else cores()


def summarize(rows: list[dict]) -> dict:
    routes: dict[str, dict] = {}
    for r in rows:
        d = routes.setdefault(r["route"] or "failed", {"n": 0, "accepted": 0, "cost": 0.0, "repairs": 0, "lat": []})
        d["n"] += 1
        d["accepted"] += int(r["accepted"])
        d["cost"] += r["cost"]
        d["repairs"] += r["repairs"]
        d["lat"].append(r["latency_s"])
    out = {}
    for k, d in routes.items():
        acc = d["accepted"]
        out[k] = {"specs": d["n"], "auto_accept_rate": round(acc / d["n"], 4),
                  "cost_per_accepted": round(d["cost"] / acc, 5) if acc else None,
                  "repairs_per_accepted": round(d["repairs"] / acc, 3) if acc else None,
                  "p50_latency_s": round(float(np.percentile(d["lat"], 50)), 2),
                  "p95_latency_s": round(float(np.percentile(d["lat"], 95)), 2)}
    return out


def run(root: Path, out: Path, *, mode: str = "synthetic", limit: int | None = None,
        workers: int | None = None) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    rows = _rows(root, mode, limit, default_workers(mode) if workers is None else workers)
    routes = summarize(rows)
    stable = {"routes": {k: {kk: vv for kk, vv in v.items() if "latency" not in kk} for k, v in routes.items()},
              "rows": [{k: v for k, v in r.items() if k != "latency_s"} for r in rows]}
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()
    doc = {"kind": "generation", "mode": mode, "digest": digest,
           "summary": {"cases": len(rows), "passed": sum(r["accepted"] for r in rows), "routes": routes},
           "results": [{**r, "passed": r["accepted"]} for r in rows]}
    (out / "results.json").write_text(json.dumps(doc, indent=2, default=str))
    trs = "".join(f"<tr><td>{html.escape(k)}</td><td>{v['specs']}</td><td>{v['auto_accept_rate']:.0%}</td>"
                  f"<td>{v['cost_per_accepted']}</td><td>{v['repairs_per_accepted']}</td><td>{v['p50_latency_s']}"
                  f"</td><td>{v['p95_latency_s']}</td></tr>" for k, v in routes.items())
    rrs = "".join(f"<tr><td>{html.escape(r['case'])}</td><td>{html.escape(str(r['route']))}</td><td>{r['score']}</td>"
                  f"<td>{'yes' if r['accepted'] else 'no'}</td><td>${r['cost']:.4f}</td><td>{r['repairs']}</td>"
                  f"<td>{html.escape(r['error'] or '')}</td></tr>" for r in rows)
    body = (f"<h1>Generation golden set</h1><p class='muted'>{mode} · {len(rows)} specs · digest "
            f"<code>{digest[:16]}</code></p><div class='card'><table><tr><th>Route</th><th>Specs</th>"
            f"<th>Auto-accept</th><th>Cost / accepted</th><th>Repairs / accepted</th><th>p50 s</th><th>p95 s</th>"
            f"</tr>{trs}</table></div><div class='card'><table><tr><th>Spec</th><th>Route</th><th>Q</th>"
            f"<th>Accepted</th><th>Cost</th><th>Repairs</th><th>Error</th></tr>{rrs}</table></div>")
    (out / "index.html").write_text(report_mod.page("Generation golden set", body))
    return doc
