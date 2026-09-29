"""spriteguru CLI (Typer). Calls the core directly; the API only wraps the same functions."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import keys as keys_mod
from .project import Project, ProjectError
from .spec import Style

app = typer.Typer(help="SpriteGuru engine: character description to game-ready animation assets.",
                  no_args_is_help=True, pretty_exceptions_enable=False)
keys_app = typer.Typer(help="API keys in the OS keychain (env vars override).", no_args_is_help=True)
char_app = typer.Typer(help="Characters and turnarounds (6.4).", no_args_is_help=True)
eval_app = typer.Typer(help="Evaluation harness (16).", no_args_is_help=True)
jobs_app = typer.Typer(help="Job history and resume (5).", no_args_is_help=True)
library_app = typer.Typer(help="The asset library in your cloud workspaces.", no_args_is_help=True)
cloud_app = typer.Typer(help="Your SpriteGuru account: sign-in, access and machines.", no_args_is_help=True)
app.add_typer(keys_app, name="keys")
app.add_typer(cloud_app, name="cloud")
app.add_typer(library_app, name="library")
app.add_typer(char_app, name="character")
app.add_typer(eval_app, name="eval")
app.add_typer(jobs_app, name="jobs")
con = Console(stderr=True)

ProjectOpt = typer.Option(None, "--project", "-p", help="Project folder (default: discovered from cwd).")
ModeOpt = typer.Option(None, "--mode", help="live (real providers) or synthetic (offline simulator).")


def _project(path: Optional[Path]) -> Project:
    try:
        return Project.discover(path)
    except ProjectError as e:
        con.print(f"[red]{e}[/red]")
        raise typer.Exit(2)


def _require_access(mode: Optional[str], proj: Optional[Project] = None) -> None:
    """Commands that call providers need the account's access (cloud plan 4.4); without it they exit
    with code 3 and the server's reason. Synthetic mode is free and needs none."""
    from .cloud.access import AccessRequired, require
    from .cloud.client import Session

    effective = mode or (proj.config.settings.provider_mode if proj else "live")
    if effective == "synthetic":
        return
    session = Session()
    try:
        require(session, effective)
    except AccessRequired as e:
        con.print(f"[red]{e.decision['message']}[/red]")
        print(json.dumps({"error": e.payload()}))
        raise typer.Exit(3)
    finally:
        session.close()


def _approver(yes: bool):
    async def approve(req) -> bool:
        if yes:
            con.print(f"[yellow]auto-approved[/yellow] {req.model} ~${req.estimate:.3f}: {req.reason}")
            return True
        if not sys.stdin.isatty():
            con.print(f"[red]needs approval[/red] {req.model} ~${req.estimate:.3f}: {req.reason} (pass --yes)")
            return False
        return await asyncio.to_thread(typer.confirm, f"Call {req.model} (~${req.estimate:.3f})? {req.reason}")
    return approve


def _printer(bus):
    q = bus.subscribe()

    async def pump():
        while True:
            e = await q.get()
            t = e.get("type")
            if t == "step_started":
                con.print(f"[cyan]▸ {e['step']}[/cyan]")
            elif t == "candidate_scored":
                top = "; ".join(f["message"] for f in e.get("top", [])[:2])
                con.print(f"  candidate {e['candidate']}: Q {e['score']:.0f} "
                          f"{'[green]accepted[/green]' if e['accepted'] else '[yellow]not accepted[/yellow]'} {top}")
            elif t == "decision":
                con.print(f"  decision: [bold]{e['action']}[/bold] — {e.get('reason', '')}")
            elif t == "spend" and not e.get("cached"):
                con.print(f"  spend: {e['model']} ${e['cost']:.4f}")
            elif t == "retry":
                con.print(f"  [yellow]retry {e['provider']} in {e['delay']:.1f}s: {e['error'][:80]}[/yellow]")
    return asyncio.create_task(pump())


# ---------------------------------------------------------------------------


@app.command()
def init(path: Path, style: str = typer.Option("hd-cartoon", help="pixel, hd-cartoon, painted or vector"),
         engine: str = typer.Option("godot", help="phaser, pixi, godot, unity or gamemaker"),
         pixel_height: int = typer.Option(48, help="logical character height (pixel style)"),
         palette_size: int = 16, fps: int = 12, asset_folder: Optional[Path] = None,
         mode: str = typer.Option("live", help="default provider mode for this project")):
    """Create a project folder (MyGame.sprites)."""
    st = Style(kind=style, pixel_height=pixel_height if style == "pixel" else None, palette_size=palette_size)
    proj = Project.init(path, style=st, engine=engine, fps=fps,
                        asset_folder=str(asset_folder.resolve()) if asset_folder else None)
    proj.config.settings.provider_mode = mode
    proj.save()
    con.print(f"[green]created[/green] {proj.root}")


@keys_app.command("set")
def keys_set(provider: str, value: Optional[str] = typer.Option(None, help="omit to be prompted")):
    """Store a provider key in the OS keychain."""
    v = value or typer.prompt(f"{provider} key", hide_input=True)
    keys_mod.set(provider, v)
    con.print(f"[green]stored[/green] {provider} key in the keychain")


@keys_app.command("status")
def keys_status():
    """Which providers have a key (never prints the key)."""
    t = Table("provider", "configured", "source")
    for p, s in keys_mod.status().items():
        t.add_row(p, "yes" if s["configured"] else "no", s["source"] or "-")
    Console().print(t)


@keys_app.command("delete")
def keys_delete(provider: str):
    keys_mod.delete(provider)
    con.print(f"deleted {provider} key from the keychain")


@char_app.command("new")
def character_new(name: str, describe: Optional[str] = typer.Option(None, "--describe", "-d",
                                                                     help="visual facts; optional with --image"),
                  image: Optional[Path] = typer.Option(None, "--image", "-i", help="reference image"),
                  use_image_as_view: bool = typer.Option(False, "--use-image-as-view",
                                                         help="use the image as the side view; skip the turnaround"),
                  kind: str = typer.Option("character", help="character, vehicle, machine or effect"),
                  blend: Optional[str] = typer.Option(None, help="effects: add (glow on black) or normal"),
                  mirrorable: bool = typer.Option(True, "--mirrorable/--asymmetric"),
                  candidates: int = typer.Option(1, help="turnaround candidates to generate"),
                  approve: bool = typer.Option(False, "--approve", help="approve the first candidate"),
                  seed: int = 0, yes: bool = typer.Option(False, "--yes", "-y"),
                  project: Optional[Path] = ProjectOpt, mode: Optional[str] = ModeOpt):
    """Create a subject (character, vehicle, machine or effect) from text and/or a reference image,
    then generate its turnaround (an effect gets one design view)."""
    from . import character as ch
    from .engine import make_hub

    proj = _project(project)
    if not describe and not image:
        con.print("[red]give --describe, --image, or both[/red]")
        raise typer.Exit(2)
    _require_access(mode, proj)
    try:
        ch.new_record(proj, name, describe or "", mirrorable=mirrorable, kind=kind, blend=blend,
                      image=image.read_bytes() if image else None)
    except (ValueError, OSError) as e:
        con.print(f"[red]{e}[/red]")
        raise typer.Exit(2)
    hub = make_hub(proj, mode=mode, approve=_approver(yes))
    cost = 0.0
    if use_image_as_view:
        if not image:
            con.print("[red]--use-image-as-view needs --image[/red]")
            raise typer.Exit(2)
        asyncio.run(ch.describe_from_image(proj, hub, name, keep_mirrorable=not mirrorable))
        ch.use_image_as_view(proj, name)
    else:
        t = asyncio.run(ch.generate_turnaround(proj, hub, name, n=candidates, seed=seed))
        cost = t.cost
    if approve:
        ch.approve(proj, name)
    rec = proj.character(name)
    con.print(f"[green]{rec.kind}[/green] {rec.name}: views {', '.join(rec.views)}; palette {len(rec.palette)} "
              f"colours; description: {rec.description[:90]}"
              + (" [green]approved[/green]" if rec.approved else " — run `spriteguru character approve`"))
    for w in rec.view_warnings:
        con.print(f"[yellow]warning:[/yellow] {w}")
    print(json.dumps({"character": rec.model_dump(mode="json"), "cost": cost}))


@char_app.command("approve")
def character_approve(name: str, candidate: int = typer.Option(1, help="turnaround candidate (1-based)"),
                      project: Optional[Path] = ProjectOpt):
    from . import character as ch

    proj = _project(project)
    if candidate != 1:
        ch.choose_turnaround(proj, name, candidate - 1)
    rec = ch.approve(proj, name)
    con.print(f"[green]approved[/green] {rec.name}")
    for w in rec.view_warnings:
        con.print(f"[yellow]warning:[/yellow] {w}")


@char_app.command("list")
def character_list(project: Optional[Path] = ProjectOpt):
    proj = _project(project)
    t = Table("name", "kind", "style", "approved", "views", "palette")
    for r in proj.characters():
        t.add_row(r.name, r.kind, r.style.kind, "yes" if r.approved else "no", ", ".join(r.views), str(len(r.palette)))
    Console().print(t)


@app.command()
def gen(character: str, action: str, facing: str = typer.Option("E"), frames: Optional[int] = None,
        loop: Optional[bool] = typer.Option(None, "--loop/--no-loop"), view: str = typer.Option("side"),
        motion: str = typer.Option("in-place"), seed: int = 0, yes: bool = typer.Option(False, "--yes", "-y"),
        replay: bool = typer.Option(False, help="cached candidates only; never calls a provider"),
        project: Optional[Path] = ProjectOpt, mode: Optional[str] = ModeOpt):
    """Generate an animation: compile, generate, analyze, repair, export."""
    from . import service
    from .engine import make_runner

    proj = _project(project)
    rec = proj.character(character)
    if not rec.approved:
        con.print(f"[red]character {character!r} is not approved[/red]; run `spriteguru character approve {character}`")
        raise typer.Exit(2)
    if not replay:  # replay reads cached candidates only and never calls a provider
        _require_access(mode, proj)
    anim_id, spec = service.create_animation(proj, character, action, facing=facing, frames=frames, loop=loop,
                                             view=view, motion=motion)

    async def main():
        runner = make_runner(proj, mode=mode, replay=replay, approve=_approver(yes))
        pump = _printer(runner.bus)
        st = runner.create_animation_job(anim_id, seed=seed)
        con.print(f"job {st.id}")
        st = await runner.run(st.id)
        await asyncio.sleep(0.05)
        pump.cancel()
        return st

    st = asyncio.run(main())
    if st.state != "done":
        con.print(f"[red]{st.state}[/red]: {st.error}")
        print(json.dumps(st.model_dump(mode="json")))
        raise typer.Exit(1)
    r = st.result
    con.print(f"[green]exported[/green] {proj.root / r['final']} · Q {r['score']:.0f} · "
              f"{'accepted' if r['accepted'] else 'best effort'} · spend ${r['spend']:.4f}")
    print(json.dumps(st.model_dump(mode="json")))


@app.command()
def analyze(sheet: Path, spec: Optional[Path] = typer.Option(None, help="spec.json"),
            grid: Optional[str] = typer.Option(None, help="COLSxROWS for a known uniform grid"),
            out: Optional[Path] = typer.Option(None, help="output folder (default: next to the sheet)"),
            action: Optional[str] = None, frames: Optional[int] = None, style: str = "hd-cartoon"):
    """Analyze any sheet: matte, layout, registration, temporal checks and findings; writes an HTML report."""
    import numpy as np

    from . import report as report_mod
    from .pipeline.analyze import analyze as run_analyze
    from .pipeline.matte import ingest
    from .spec import Character, SpriteSpec

    sp = None
    if spec:
        sp = SpriteSpec.model_validate_json(spec.read_text())
    elif action and frames:
        from . import choreo

        sp = SpriteSpec(character=Character(description="unknown"), action=action, frames=frames,
                        loop=choreo.default_loop(action), style=Style(kind=style))
    g = tuple(int(v) for v in grid.lower().split("x")) if grid else None
    rgba = ingest(sheet)
    an = run_analyze(rgba, sp, grid=g)
    out = out or sheet.with_suffix("")
    out.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    fd = out / "frames"
    fd.mkdir(exist_ok=True)
    for i, f in enumerate(an.frames):
        Image.fromarray(f, "RGBA").save(fd / f"{i:03d}.png")
    (out / "preview.gif").write_bytes(report_mod.gif_bytes(an.frames, an.durations,
                                                           pixel=bool(sp and sp.style.kind == "pixel")))
    html = report_mod.write_analysis(out, an, rgba, title=f"Analysis: {sheet.name}",
                                     pixel=bool(sp and sp.style.kind == "pixel"))
    con.print(f"Q {an.report.score:.0f} {'accepted' if an.report.accepted else 'not accepted'} · "
              f"{len(an.frames)} frames · report {html}")
    print(json.dumps({"score": an.report.score, "accepted": an.report.accepted, "frames": len(an.frames),
                      "report": str(html), "findings": [f.model_dump() for f in an.report.findings]}))


@app.command()
def repair(anim_id: str, frame: int = typer.Option(..., help="1-based frame number"),
           kind: str = typer.Option("pose", help="pose, identity or frame"),
           note: Optional[str] = typer.Option(None, help="what to fix (frame repairs)"),
           yes: bool = typer.Option(False, "--yes", "-y"), project: Optional[Path] = ProjectOpt,
           mode: Optional[str] = ModeOpt):
    """Masked repair of one frame on the latest job's winner, then re-analyze and re-export."""
    from .engine import make_runner
    from .manual import manual_repair

    proj = _project(project)
    _require_access(mode, proj)

    async def main():
        runner = make_runner(proj, mode=mode, approve=_approver(yes))
        return await manual_repair(runner, anim_id, frame - 1, kind=kind, note=note)

    res = asyncio.run(main())
    con.print(f"repaired frame {frame}: Q {res['before']:.0f} → {res['after']:.0f}; "
              f"{'kept' if res['kept'] else 'discarded (worse)'}")
    print(json.dumps(res))


@app.command()
def inbetween(anim_id: str, after: int = typer.Option(..., help="insert after this 1-based frame"),
              yes: bool = typer.Option(False, "--yes", "-y"), project: Optional[Path] = ProjectOpt,
              mode: Optional[str] = ModeOpt):
    """Generate and insert an in-between frame after a frame of the latest job's winner (13.6)."""
    from .engine import make_runner
    from .manual import manual_inbetween

    proj = _project(project)
    _require_access(mode, proj)

    async def main():
        runner = make_runner(proj, mode=mode, approve=_approver(yes))
        return await manual_inbetween(runner, anim_id, after - 1)

    res = asyncio.run(main())
    con.print(f"inserted an in-between after frame {after}: {res['frames']} frames, Q {res['score']}")
    print(json.dumps(res))


@app.command("export")
def export_cmd(anim_id: str, engine: Optional[str] = None, project: Optional[Path] = ProjectOpt):
    """Re-run the export for the project's engine from the final frame set."""
    from . import edits

    proj = _project(project)
    if engine:
        spec = proj.spec(anim_id)
        proj.save_spec(anim_id, spec.model_copy(update={"engine": engine}))
    res = edits.apply(proj, anim_id, [])
    con.print(f"[green]exported[/green] {res['dir']} ({len(res['files'])} files)"
              + (f", copied to {res['copied_to']}" if res["copied_to"] else ""))
    print(json.dumps({k: v for k, v in res.items() if k != "meta"}))


@app.command()
def ledger(project: Optional[Path] = ProjectOpt):
    """Spend summary from ledger.jsonl."""
    from .ledger import Ledger

    proj = _project(project)
    print(json.dumps(Ledger(proj.ledger_path).summary(), indent=2))


@jobs_app.command("list")
def jobs_list(project: Optional[Path] = ProjectOpt):
    from .engine import make_runner

    proj = _project(project)
    runner = make_runner(proj)
    t = Table("job", "state", "route", "winner", "score", "spend")
    for st in runner.all_jobs():
        w = next((c for c in st.candidates if c["id"] == st.winner), {})
        t.add_row(st.id, st.state, st.route or "-", st.winner or "-", f"{w.get('score', 0):.0f}", f"${st.spend:.4f}")
    Console().print(t)


@jobs_app.command("resume")
def jobs_resume(yes: bool = typer.Option(False, "--yes", "-y"), project: Optional[Path] = ProjectOpt,
                mode: Optional[str] = ModeOpt):
    """Resume unfinished jobs from their last completed step (provider calls replay from the cache)."""
    from .engine import make_runner

    proj = _project(project)

    async def main():
        runner = make_runner(proj, mode=mode, approve=_approver(yes))
        ids = await runner.resume_unfinished()
        for jid in ids:
            await runner.jobs[jid].task
        return [runner.jobs[j].state for j in ids]

    states = asyncio.run(main())
    for st in states:
        con.print(f"{st.id}: {st.state}")
    print(json.dumps([s.model_dump(mode="json") for s in states]))


@eval_app.command("fixtures")
def eval_fixtures(out: Path = typer.Option(Path("tests/golden")), case: Optional[list[str]] = None,
                  workers: Optional[int] = typer.Option(None, help="parallel processes (default: most cores)")):
    """(Re)generate the golden fixture sheets with ground truth."""
    from .eval import fixtures

    paths = fixtures.write_all(out, case, workers=workers)
    con.print(f"wrote {len(paths)} fixtures to {out}")


@eval_app.command("run")
def eval_run(golden: Path = typer.Argument(Path("tests/golden")), out: Optional[Path] = None,
             replay: bool = typer.Option(True, help="analysis set from files and cache only; never calls a provider"),
             case: Optional[list[str]] = None, project: Optional[Path] = ProjectOpt,
             gate: Optional[Path] = typer.Option(None, help="baseline results.json for the regression gate"),
             workers: Optional[int] = typer.Option(None, help="parallel processes (default: most cores)")):
    """Score the analyzer on the golden set; writes results.json, index.html and eval.sqlite."""
    import datetime as dt

    from .eval import golden as golden_mod
    from .eval import store

    out = out or Path("e2e/artifacts/eval") / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    doc = golden_mod.run(golden, out, case, workers=workers)
    db = (_project(project).eval_db if project else Path(out) / "eval.sqlite")
    store.record(db, doc, label=str(golden))
    s = doc["summary"]
    con.print(f"{s['passed']}/{s['cases']} cases pass · frame IoU mean {s['frame_iou_mean']} · digest "
              f"{doc['digest'][:12]} · {out / 'index.html'}")
    if gate:
        verdict = store.gate(json.loads(Path(gate).read_text()), doc)
        con.print(("[green]gate passed[/green]" if verdict["pass"] else "[red]gate failed[/red]") + f": {verdict}")
        print(json.dumps({"summary": s, "digest": doc["digest"], "gate": verdict}))
        raise typer.Exit(0 if verdict["pass"] else 1)
    print(json.dumps({"summary": s, "digest": doc["digest"], "out": str(out)}))


@eval_app.command("pixel-bench")
def eval_pixel_bench(out: Optional[Path] = None):
    """Benchmark pixel reconstruction against unfake on sheets with known logical pixels (12.6)."""
    import datetime as dt

    from .eval import pixelbench

    out = out or Path("e2e/artifacts/pixel-bench") / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    doc = pixelbench.run(out)
    s = doc["summary"]
    con.print(f"exact logical-pixel match: ours {s['ours']:.3f} · unfake {s['unfake']} · "
              f"{'beats unfake' if s['beats_unfake'] else 'does not beat unfake'}")
    print(json.dumps(doc["summary"]))


@eval_app.command("judge")
def eval_judge(project: Optional[Path] = ProjectOpt, apply: bool = typer.Option(True, help="write disabled types")):
    """Judge precision per issue type from labels/findings.jsonl; switches off types below 0.8 (14.2)."""
    from .qa.judge import calibrate

    proj = _project(project)
    p = proj.root / "labels" / "findings.jsonl"
    labels = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.is_file() else []
    res = calibrate(labels)
    if apply:
        proj.config.settings.judge_disabled = res["disabled"]
        proj.save()
    con.print(f"{len(labels)} labels · disabled issue types: {', '.join(res['disabled']) or 'none'}")
    print(json.dumps(res))


@eval_app.command("gen")
def eval_gen(root: Path = typer.Option(Path("e2e/work/genset"), help="folder for the per-style projects"),
             out: Optional[Path] = None, mode: str = typer.Option("synthetic", help="synthetic or live"),
             limit: Optional[int] = typer.Option(None, help="first N specs (spread over every route)"),
             workers: Optional[int] = typer.Option(None, help="parallel job processes (default: offline most "
                                                              "cores, live 2)"),
             gate: Optional[Path] = typer.Option(None, help="baseline results.json for the regression gate"),
             yes: bool = typer.Option(False, "--yes", "-y", help="confirm live spend")):
    """Run the generation golden set (6 characters × 10 action/view combos) and report per route."""
    import datetime as dt

    from .eval import genset
    from .eval import store

    if mode == "live" and not yes:
        con.print("[red]the live generation set spends money; pass --yes[/red]")
        raise typer.Exit(2)
    _require_access(mode)
    out = out or Path("e2e/artifacts/eval-gen") / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    doc = genset.run(root, out, mode=mode, limit=limit, workers=workers)
    store.record(out / "eval.sqlite", doc, label=f"gen:{mode}", kind="generation")
    for route, v in doc["summary"]["routes"].items():
        con.print(f"{route:14s} {v['specs']:3d} specs · auto-accept {v['auto_accept_rate']:.0%} · cost/accepted "
                  f"{v['cost_per_accepted']} · p95 {v['p95_latency_s']} s")
    verdict = store.gate(json.loads(Path(gate).read_text()), doc) if gate else None
    print(json.dumps({"summary": doc["summary"], "digest": doc["digest"], "out": str(out), "gate": verdict}))
    if verdict is not None and not verdict["pass"]:
        raise typer.Exit(1)


# -- cloud account (cloud plan 3) -------------------------------------------------------------


@cloud_app.command("login")
def cloud_login(no_browser: bool = typer.Option(False, "--no-browser", help="print the sign-in link only"),
                timeout: float = typer.Option(600.0, help="seconds to wait for the browser")):
    """Sign in through the browser. A one-shot listener on 127.0.0.1 receives the callback (C3)."""
    from .cloud.auth import loopback_login
    from .cloud.client import Session
    from .cloud.service import CloudService

    session = Session()

    def announce(url: str) -> None:
        con.print(f"Sign in to SpriteGuru in your browser. If it did not open, visit:\n{url}")
        print(json.dumps({"authorize_url": url}), flush=True)

    result = loopback_login(session, open_browser=not no_browser, timeout=timeout, announce=announce)
    if result != "signed-in":
        con.print(f"[red]sign-in {result}[/red]")
        print(json.dumps({"signed_in": False, "result": result}))
        raise typer.Exit(1)
    st = CloudService(lambda e: None, session).status(refresh=True)
    con.print(f"[green]signed in[/green] as {st['user'].get('email')} on {st['machine']['name']}")
    print(json.dumps(st))


@cloud_app.command("logout")
def cloud_logout():
    """Sign this machine out: revoke the sign-in on the server and forget it here."""
    from .cloud.auth import sign_out
    from .cloud.client import Session

    session = Session()
    was = session.signed_in
    sign_out(session)
    print(json.dumps({"signed_in": False, "was_signed_in": was}))


@cloud_app.command("status")
def cloud_status(refresh: bool = typer.Option(False, help="ask the server now instead of the cached answer")):
    """The account, this machine and the access object (never a token)."""
    from .cloud.service import CloudService

    print(json.dumps(CloudService(lambda e: None).status(refresh=refresh)))


@cloud_app.command("machines")
def cloud_machines(rename: Optional[str] = typer.Option(None, help="rename this machine"),
                   sign_out: Optional[str] = typer.Option(None, "--sign-out", help="sign out a machine by id")):
    """List the account's machines; rename this one or sign one out."""
    from .cloud.client import CloudError
    from .cloud.service import CloudService

    svc = CloudService(lambda e: None)
    try:
        if rename:
            svc.rename_machine(rename)
        if sign_out:
            svc.session.request("DELETE", f"/api/v1/devices/{sign_out}")
        print(json.dumps(svc.session.get("/api/v1/devices")))
    except CloudError as e:
        con.print(f"[red]{e.message}[/red]")
        print(json.dumps({"error": e.payload()}))
        raise typer.Exit(1)


@cloud_app.command("projects")
def cloud_projects():
    """Cloud projects in every workspace you belong to, with the folder they sync to on this machine."""
    from . import library
    from .cloud.client import CloudError, Session

    try:
        rows = Session().get("/api/v1/projects")["projects"]
    except CloudError as e:
        con.print(f"[red]{e.message}[/red]")
        print(json.dumps({"error": e.payload()}))
        raise typer.Exit(1)
    here = library.cloud_links()
    print(json.dumps({"projects": [{**r, "local_path": here.get(r["id"])} for r in rows]}))


@cloud_app.command("download")
def cloud_download(project_id: str, to: Optional[Path] = typer.Option(None, "--to", help="parent folder (default: the library)")):
    """Put a cloud project on this machine in a new folder (never merged into an existing one)."""
    from . import library
    from .cloud.client import CloudError, Session
    from .cloud.sync import SyncPaused, download_project

    session = Session()
    try:
        project = session.get(f"/api/v1/projects/{project_id}")["project"]
        target = download_project(session, project, (to or library.root()).expanduser())
    except (CloudError, SyncPaused) as e:
        con.print(f"[red]{e.message}[/red]")
        print(json.dumps({"error": e.payload()}))
        raise typer.Exit(1)
    library.touch(target)
    con.print(f"[green]downloaded[/green] {project['name']} to {target}")
    print(json.dumps({"path": str(target), "project_id": project_id}))


def _library_call(fn, *args, **kw):
    from .cloud import library as cloud_library
    from .cloud.client import CloudError
    from .cloud.sync import SyncPaused

    try:
        return fn(*args, **kw)
    except (CloudError, SyncPaused, cloud_library.LibraryError) as e:
        msg = getattr(e, "message", str(e))
        con.print(f"[red]{msg}[/red]")
        print(json.dumps({"error": e.payload() if hasattr(e, "payload") else {"code": "bad_request", "message": msg}}))
        raise typer.Exit(1)


@library_app.command("list")
def library_list(owner: Optional[str] = typer.Option(None, help="me or a team id (default: every workspace)"),
                 kind: Optional[str] = None, q: Optional[str] = typer.Option(None, "--search", "-q")):
    """Library items in your workspaces."""
    from .cloud import library as cloud_library
    from .cloud.client import Session

    print(json.dumps({"items": _library_call(cloud_library.browse, Session(), owner, kind, q)}))


@library_app.command("import")
def library_import(item_id: str, project: Optional[Path] = ProjectOpt):
    """Import a library item into the project (characters/, animations/ or imports/)."""
    from .cloud import library as cloud_library
    from .cloud.client import Session

    proj = _project(project)
    res = _library_call(cloud_library.import_item, Session(), proj.root, item_id)
    con.print(f"[green]imported[/green] {res['name']} into {res['path']}")
    print(json.dumps(res))


@library_app.command("publish")
def library_publish(prefix: str, name: str = typer.Option(..., "--name"), kind: str = typer.Option(..., "--kind"),
                    owner: Optional[str] = typer.Option(None, help="me or a team id (default: the project's workspace)"),
                    project: Optional[Path] = ProjectOpt):
    """Publish a project folder (e.g. characters/knight/) as a library item."""
    from .cloud import library as cloud_library
    from .cloud.client import Session

    proj = _project(project)
    res = _library_call(cloud_library.publish, Session(), proj.root, prefix, name=name, kind=kind, owner=owner)
    con.print(f"[green]published[/green] {name}" + (" (from the synced project)" if res["from_project"] else ""))
    print(json.dumps(res))


@app.command()
def sync(push_only: bool = typer.Option(False, "--push", help="push only"),
         pull_only: bool = typer.Option(False, "--pull", help="pull only"),
         link: Optional[str] = typer.Option(None, "--link", help="link to a workspace first: me or a team id"),
         unlink: bool = typer.Option(False, "--unlink", help="stop syncing; the cloud copy stays"),
         keep: Optional[bool] = typer.Option(None, "--keep-link/--separate",
                                             help="a copied folder: keep syncing it here, or make it separate"),
         resolve: Optional[list[str]] = typer.Option(None, "--resolve", help="PATH=mine or PATH=theirs (repeatable)"),
         wait: float = typer.Option(30.0, help="seconds to wait while another window syncs this project"),
         project: Optional[Path] = ProjectOpt):
    """Sync the project with the cloud: pull what changed, push what changed here (cloud plan 6)."""
    from .cloud.client import CloudError, Session
    from .cloud.sync import SyncBusy, SyncPaused, Syncer

    proj = _project(project)
    syncer = Syncer(proj.root, Session(), lock_wait=wait)
    try:
        if unlink or keep is False:
            syncer.unlink()
            print(json.dumps({"linked": False}))
            return
        if link:
            made = syncer.link(link)
            con.print(f"[green]linked[/green] {made['name']} ({made['id']})")
        if keep:
            syncer.keep_link()
        if resolve:
            choices = []
            for r in resolve:
                path, _, keep_side = r.rpartition("=")
                choices.append({"path": path, "keep": keep_side})
            syncer.resolve(choices)
        res = syncer.run(push=not pull_only, pull=not push_only)
    except SyncBusy as e:
        con.print(f"[yellow]{e}[/yellow]")
        print(json.dumps({"error": {"code": "busy", "message": str(e)}}))
        raise typer.Exit(4)
    except SyncPaused as e:
        con.print(f"[yellow]{e.message}[/yellow]")
        print(json.dumps({"error": e.payload(), "status": syncer.status()}))
        raise typer.Exit(5)
    except (CloudError, ValueError) as e:
        msg = getattr(e, "message", str(e))
        con.print(f"[red]{msg}[/red]")
        print(json.dumps({"error": e.payload() if isinstance(e, CloudError) else {"code": "bad_request", "message": msg}}))
        raise typer.Exit(1)
    con.print(f"rev {res['rev']}: pushed {len(res['pushed'])}, pulled {len(res['pulled'])}"
              + (f", {res['conflicts']} conflicts" if res["conflicts"] else ""))
    print(json.dumps({**res, "status": syncer.status()}))


@app.command()
def serve(port: int = 0, token: Optional[str] = None, project: Optional[Path] = ProjectOpt,
          mode: Optional[str] = ModeOpt, open_browser: bool = typer.Option(False, "--open")):
    """Run the engine API on 127.0.0.1; prints {port, token} as JSON on startup. Without --project
    it starts with no project open and the studio shows the project gallery (P1)."""
    from .api import run_server

    run_server(_project(project) if project else None, port=port, token=token, mode=mode, open_browser=open_browser)


@app.command()
def studio(project: Optional[Path] = ProjectOpt, mode: Optional[str] = ModeOpt,
           window: bool = typer.Option(True, "--window/--browser", help="native window (pywebview) or browser")):
    """Start the engine and open the studio on the project gallery; with --project that project is
    already open (and listed in the gallery)."""
    proj = _project(project) if project else None
    if window:
        from .launcher import launch

        launch(proj.root if proj else None, mode=mode)
    else:
        from .api import run_server

        run_server(proj, port=0, mode=mode, open_browser=True)


def main():
    app()


if __name__ == "__main__":
    main()
