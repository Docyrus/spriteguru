"""Local FastAPI server (section 2): binds 127.0.0.1 only, requires a random per-launch token on
every request and allows only the studio's origin, because any web page in the browser could
otherwise send requests to localhost and spend API credits."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import sys
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import __version__, choreo, edits, embed, keys, registry, service
from . import character as char_mod
from .engine import make_runner
from .env import get as app_env, present as app_env_present
from .jobs import EventBus, JobRunner
from .ledger import Ledger
from .pipeline import matte as matte_mod
from .project import Project, ProjectError, read_json
from .spec import Style

STUDIO_DIST = Path(__file__).resolve().parent / "studio_dist"
DEV_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]


class CharacterIn(BaseModel):
    name: str
    description: str = ""
    mirrorable: bool = True
    style: Style | None = None
    candidates: int = 1
    seed: int = 0
    kind: str = "character"  # character, vehicle, machine, effect
    blend: str | None = None  # effects: add or normal
    image: str | None = None  # reference image as a data URL or base64
    use_image_as_view: bool = False


class ProjectIn(BaseModel):
    name: str
    style: Style
    engine: str = "godot"
    fps: int = 12
    location: str | None = None  # parent folder; the library root when omitted


class AnimationIn(BaseModel):
    character: str
    action: str
    facing: str = "E"
    frames: int | None = None
    loop: bool | None = None
    view: str = "side"
    motion: str = "in-place"


class SettingsIn(BaseModel):
    session_cap_usd: float | None = None
    job_cap_usd: float | None = None
    sol_effort: str | None = None
    luna_effort: str | None = None
    provider_mode: str | None = None
    judge_enabled: bool | None = None
    asset_folder: str | None = None
    engine: str | None = None
    fps: int | None = None


class State:
    """One engine, at most one open project (P1). The event bus outlives project switches, so an open
    studio keeps its socket and hears `project_changed`."""

    def __init__(self, project: Project | None, token: str, mode: str | None):
        self.token = token
        self.bus = EventBus()
        self.mode = mode
        self.project: Project | None = None
        self.runner: JobRunner | None = None
        self.tasks: dict[str, asyncio.Task] = {}
        self.port = 0
        self.stopping = False
        from .cloud.service import CloudService

        self.cloud = CloudService(self.bus.publish)
        self.sync = None  # the open project's sync scheduler (cloud plan 6.8), started on the event loop
        if project is not None:
            self.attach(project)

    def attach(self, project: Project | None) -> None:
        """P5: a new runner, hub and ledger for the new project; nothing carries over."""
        self.project = project
        self.runner = make_runner(project, mode=self.mode, bus=self.bus) if project is not None else None
        self.tasks = {}

    def busy(self) -> str | None:
        """P4: what is still running and spending in the open project, if anything."""
        if self.runner is not None:
            for jid, h in self.runner.jobs.items():
                if h.task is not None and not h.task.done():
                    return f"job {jid}"
        for name, t in self.tasks.items():
            if not t.done():
                return name.replace(":", " for ")
        return None

    def busy_prefixes(self) -> set[str]:
        """Folders a running job or turnaround is writing: sync neither pushes nor pulls them (6.4, C30)."""
        from .project import slug

        out: set[str] = set()
        try:
            if self.runner is not None:
                for h in list(self.runner.jobs.values()):
                    if h.task is not None and not h.task.done() and h.state.anim_id:
                        out.add(f"animations/{h.state.anim_id}/")
            for name, t in list(self.tasks.items()):
                if not t.done() and name.startswith("turnaround:"):
                    out.add(f"characters/{slug(name.split(':', 1)[1])}/")
        except RuntimeError:  # the job table changed while it was read: be safe for this round
            out.add("animations/")
        return out

    async def start_sync(self) -> None:
        from .cloud.scheduler import ProjectSync

        await self.stop_sync()
        if self.project is None:
            return

        async def pulled(paths: list[str]) -> None:
            # the studio refreshes pulled animations the way it does after an export (C30)
            for anim in sorted({p.split("/")[1] for p in paths if p.startswith("animations/") and "/final/" in p}):
                self.bus.publish({"type": "exported", "anim": anim, "source": "sync"})
            if "project.json" in paths and self.project is not None:  # settings came from another machine
                fresh = Project.open(self.project.root).config
                self.project.config = fresh
                if self.runner is not None:
                    self.runner.project.config = fresh
                    self.runner.hub.session_cap = fresh.settings.session_cap_usd
                    self.runner.hub.job_cap = fresh.settings.job_cap_usd

        self.sync = ProjectSync(self.project.root, self.cloud, self.bus, self.busy_prefixes, pulled)
        self.sync.start()

    async def stop_sync(self) -> None:
        if self.sync is not None:
            await self.sync.stop()
            self.sync = None

    def rebuild_runner(self) -> None:
        old = self.runner
        self.runner = make_runner(self.project, mode=self.mode, bus=self.bus)
        if old is not None:
            self.runner.jobs.update(old.jobs)


# project-free endpoints: the gallery, keys, models and the account work before any project is open (P1)
_OPEN_PREFIXES = ("/api/library", "/api/keys", "/api/models", "/api/cloud")


def create_app(project: Project | None, token: str, *, mode: str | None = None) -> FastAPI:
    app = FastAPI(title="spriteguru", version=__version__)
    S = State(project, token, mode)
    app.state.S = S
    app.add_middleware(CORSMiddleware, allow_origins=DEV_ORIGINS if app_env_present("DEV") else [],
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        path = request.url.path
        if path.startswith("/api/"):
            tok = request.headers.get("x-spriteguru-token") or request.query_params.get("token")
            if not tok or not secrets.compare_digest(tok, S.token):
                return JSONResponse({"detail": "missing or invalid token"}, status_code=401)
            if S.project is None and not path.startswith(_OPEN_PREFIXES):
                return JSONResponse({"detail": "no project open"}, status_code=409)
        return await call_next(request)

    from .cloud.access import AccessRequired
    from .cloud.client import CloudError

    @app.exception_handler(AccessRequired)
    async def _no_access(request: Request, e: AccessRequired):
        # 402 with the server's reason; `detail` keeps the studio's existing error display working
        return JSONResponse({"detail": e.decision["message"], "error": e.payload()}, status_code=402)

    @app.exception_handler(CloudError)
    async def _cloud_error(request: Request, e: CloudError):
        # a cloud 401 is the account's, not the studio's launch token: the studio reads 401 as the latter
        status = 403 if e.status == 401 else e.status if 400 <= e.status < 600 else 503
        if e.status in (402, 413):
            S.cloud.invalidate_me()
        return JSONResponse({"detail": e.message, "error": e.payload()}, status_code=status)

    async def _require_access() -> None:
        """Before anything that calls a provider or starts a job (cloud plan 4.4); synthetic mode is free."""
        await asyncio.to_thread(S.cloud.require, S.runner.hub.mode if S.runner is not None else S.mode)

    @app.on_event("startup")
    async def _resume():
        if S.runner is not None:
            await S.runner.resume_unfinished()
        await S.start_sync()

    @app.on_event("shutdown")
    async def _stop():
        S.stopping = True

    # -- project, settings, keys ------------------------------------------------------

    def _active(p: Project) -> str | None:
        """P9: an active character that no longer exists reads as none."""
        name = p.config.active_character
        if name and (p.character_dir(name) / "character.json").is_file():
            return p.character(name).name
        return None

    def _project_payload() -> dict:
        return get_project()

    @app.get("/api/project")
    def get_project():
        p = S.project
        return {"name": p.config.name, "root": str(p.root), "path": str(p.root), "active_character": _active(p),
                "config": p.config.model_dump(mode="json"),
                "keys": keys.status(), "mode": S.runner.hub.mode, "models": {
                    "embeddings": embed.status(), "matte": {"available": matte_mod.ml_available(),
                                                            "model": app_env("MATTE_MODEL",
                                                                             "birefnet-general")}},
                "roles": registry.load()["roles"], "ledger": Ledger(p.ledger_path).summary(),
                "version": __version__}

    @app.put("/api/project/active-character")
    def put_active_character(name: str | None = Body(None, embed=True)):
        p = S.project
        if name is not None:
            try:
                rec = p.character(name)
            except ProjectError as e:
                raise HTTPException(404, str(e))  # P10: the previous choice is kept
            name = rec.name
        p.config.active_character = name
        if S.runner is not None:
            S.runner.project.config.active_character = name
        p.save()
        S.bus.publish({"type": "active_character", "name": name})
        return {"active_character": name}

    # -- the project library (gallery, create, open, switch) ------------------------------

    from . import library as lib

    async def _switch(project: Project | None) -> None:
        busy = S.busy()
        if busy:
            raise HTTPException(409, f"{busy} is still running in {S.project.config.name}; "
                                     "wait for it or cancel it before switching projects")
        await S.stop_sync()  # P5: the old project's syncing ends with its runner
        S.attach(project)
        S.bus.history.clear()  # a studio connecting later must not replay the old project's events
        if project is not None:
            lib.touch(project.root)
            await S.runner.resume_unfinished()
            await S.start_sync()
        S.bus.publish({"type": "project_changed", "path": str(project.root) if project else None,
                       "name": project.config.name if project else None})

    @app.get("/api/library")
    def get_library():
        return lib.listing(S.project.root if S.project else None)

    @app.post("/api/library/projects")
    async def create_project(body: ProjectIn):
        busy = S.busy()
        if busy:
            raise HTTPException(409, f"{busy} is still running in {S.project.config.name}")
        from .spec import ProjectConfig

        if body.engine not in ProjectConfig.model_fields["engine"].annotation.__args__:
            raise HTTPException(400, f"unknown engine {body.engine!r}")
        try:
            proj = lib.create(body.name, style=body.style, engine=body.engine, fps=body.fps, location=body.location)
        except lib.LibraryError as e:
            raise HTTPException(400, str(e))
        except lib.LibraryConflict as e:
            raise HTTPException(409, str(e))
        await _switch(proj)
        return {"project": _project_payload(), "card": lib.card(proj.root, proj.root)}

    @app.post("/api/library/open")
    async def open_project(id: str | None = Body(None, embed=True), path: str | None = Body(None, embed=True)):
        if id:
            try:
                target = lib.resolve(id)
            except KeyError:
                raise HTTPException(404, f"no project {id!r} in the library")
            if not (target / "project.json").is_file():
                raise HTTPException(409, f"{target} is missing; it was moved or deleted")
        elif path:
            target = Path(path)
        else:
            raise HTTPException(400, "give a project id or path")
        try:
            target = lib.validate_path(target)
        except lib.LibraryError as e:
            raise HTTPException(400, str(e))
        if S.project is not None and S.project.root == target:
            lib.touch(target)
            return _project_payload()
        await _switch(Project.open(target))
        return _project_payload()

    @app.post("/api/library/close")
    async def close_project():
        await _switch(None)
        return {"ok": True}

    @app.delete("/api/library/projects/{pid}")
    def forget_project(pid: str):
        try:
            lib.forget(pid)
        except KeyError:
            raise HTTPException(404, f"no project {pid!r} in the library")
        return {"ok": True}

    @app.get("/api/library/projects/{pid}/thumbnail")
    def project_thumbnail(pid: str):
        try:
            thumb = lib.thumbnail(pid)
        except KeyError:
            raise HTTPException(404, f"no project {pid!r} in the library")
        if thumb is None:
            raise HTTPException(404, "no thumbnail yet")
        return FileResponse(thumb, media_type="image/png")

    @app.patch("/api/settings")
    def patch_settings(body: SettingsIn):
        from pydantic import ValidationError

        from .spec import ProjectConfig, Settings

        cfg = S.project.config
        data = body.model_dump(exclude_none=True)
        top = {k: data.pop(k) for k in ("asset_folder", "engine", "fps") if k in data}
        try:
            settings = Settings.model_validate({**cfg.settings.model_dump(), **data})
            new = ProjectConfig.model_validate({**cfg.model_dump(), **top, "settings": settings.model_dump()})
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False))
        S.project.config = cfg = new
        S.runner.project.config = new
        S.project.save()
        if body.provider_mode:
            S.mode = body.provider_mode
        hub = S.runner.hub
        hub.session_cap, hub.job_cap = cfg.settings.session_cap_usd, cfg.settings.job_cap_usd
        if body.provider_mode:
            hub.mode = body.provider_mode
        S.bus.publish({"type": "settings_changed"})
        return cfg.model_dump(mode="json")

    @app.put("/api/keys/{provider}")
    def put_key(provider: str, value: str = Body(..., embed=True)):
        try:
            keys.set(provider, value)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(400, str(e))
        S.runner.hub._providers.pop(provider, None)
        return keys.status()[provider]

    @app.delete("/api/keys/{provider}")
    def delete_key(provider: str):
        keys.delete(provider)
        return keys.status().get(provider, {})

    @app.post("/api/models/embeddings/download")
    async def download_embeddings():
        ok = await asyncio.to_thread(embed.ensure_model, True)
        return {"ok": ok, **embed.status()}

    # -- characters ---------------------------------------------------------------------

    @app.get("/api/characters")
    def list_characters():
        return [c.model_dump(mode="json") for c in S.project.characters()]

    @app.get("/api/characters/{name}")
    def get_character(name: str):
        try:
            return S.project.character(name).model_dump(mode="json")
        except ProjectError as e:
            raise HTTPException(404, str(e))

    async def _turnaround(name: str, n: int, seed: int):
        S.bus.publish({"type": "turnaround_started", "character": name})
        try:
            t = await char_mod.generate_turnaround(S.project, S.runner.hub, name, n=n, seed=seed)
            S.bus.publish({"type": "turnaround_done", "character": name, "cost": t.cost})
        except Exception as e:
            S.bus.publish({"type": "turnaround_failed", "character": name, "error": f"{type(e).__name__}: {e}"})

    def _decode_image(data: str | None) -> bytes | None:
        if not data:
            return None
        import base64
        import binascii

        raw = data.split(",", 1)[1] if data.startswith("data:") else data
        try:
            return base64.b64decode(raw, validate=False)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "image is not valid base64")

    async def _from_image_view(name: str, keep_mirrorable: bool):
        S.bus.publish({"type": "turnaround_started", "character": name})
        try:
            await char_mod.describe_from_image(S.project, S.runner.hub, name, keep_mirrorable=keep_mirrorable)
            char_mod.use_image_as_view(S.project, name)
            S.bus.publish({"type": "turnaround_done", "character": name, "cost": 0.0})
        except Exception as e:
            S.bus.publish({"type": "turnaround_failed", "character": name, "error": f"{type(e).__name__}: {e}"})

    @app.post("/api/characters")
    async def create_character(body: CharacterIn):
        if body.kind not in ("character", "vehicle", "machine", "effect"):
            raise HTTPException(400, f"unknown kind {body.kind!r}")
        if (S.project.character_dir(body.name) / "character.json").is_file():
            raise HTTPException(409, f"a subject named {body.name!r} already exists")
        await _require_access()
        try:
            rec = char_mod.new_record(S.project, body.name, body.description, body.style, body.mirrorable,
                                      kind=body.kind, blend=body.blend, image=_decode_image(body.image))
        except (ProjectError, ValueError) as e:
            raise HTTPException(400, str(e))
        S.project.config.active_character = rec.name  # a new subject is what the user works on next
        S.project.save()
        S.bus.publish({"type": "active_character", "name": rec.name})
        if body.use_image_as_view:
            if not rec.source_image:
                raise HTTPException(400, "use_image_as_view needs an image")
            S.tasks[f"turnaround:{rec.name}"] = asyncio.create_task(_from_image_view(rec.name, not body.mirrorable))
        else:
            S.tasks[f"turnaround:{rec.name}"] = asyncio.create_task(_turnaround(rec.name, body.candidates, body.seed))
        return rec.model_dump(mode="json")

    @app.put("/api/characters/{name}/image")
    async def replace_image(name: str, image: str = Body(..., embed=True),
                            use_image_as_view: bool = Body(False, embed=True)):
        """Replace a subject's reference image. The views now come from a stale image, so approval is
        withdrawn; with `use_image_as_view` the new image is cut into the views right away, otherwise
        regenerate the turnaround."""
        try:
            rec = S.project.character(name)
        except ProjectError as e:
            raise HTTPException(404, str(e))
        if use_image_as_view:
            await _require_access()
        try:
            img = char_mod.normalize_image(_decode_image(image))
        except ValueError as e:
            raise HTTPException(400, str(e))
        d = S.project.character_dir(rec.name) / "ref"
        d.mkdir(parents=True, exist_ok=True)
        img.save(d / "source.png")
        rec.source_image = S.project.rel(d / "source.png")
        rec.approved = False
        S.project.save_character(rec)
        if use_image_as_view:
            S.tasks[f"turnaround:{rec.name}"] = asyncio.create_task(_from_image_view(rec.name, not rec.mirrorable))
        return rec.model_dump(mode="json")

    @app.post("/api/characters/{name}/turnaround")
    async def regenerate_turnaround(name: str, n: int = Body(1, embed=True), seed: int = Body(0, embed=True)):
        S.project.character(name)
        await _require_access()
        S.tasks[f"turnaround:{name}"] = asyncio.create_task(_turnaround(name, n, seed))
        return {"started": True}

    @app.post("/api/characters/{name}/choose")
    def choose(name: str, index: int = Body(..., embed=True)):
        try:
            return char_mod.choose_turnaround(S.project, name, index).model_dump(mode="json")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/characters/{name}/approve")
    def approve_character(name: str):
        try:
            rec = char_mod.approve(S.project, name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        S.bus.publish({"type": "character_approved", "character": rec.name})
        return rec.model_dump(mode="json")

    # -- animations -------------------------------------------------------------------------

    @app.get("/api/actions")
    def actions(kind: str = "character"):
        if kind not in choreo.KINDS:
            raise HTTPException(400, f"unknown kind {kind!r}")
        from . import prompts as P

        return [{"action": a, "kind": kind, "frames": choreo.default_frames(a, kind),
                 "loop": choreo.default_loop(a, kind), "title": P.action_title(a, kind)} for a in choreo.actions(kind)]

    def _anim_summary(anim_id: str) -> dict:
        p = S.project
        spec = p.spec(anim_id)
        meta_p = p.animation_dir(anim_id) / "meta.json"
        meta = read_json(meta_p) if meta_p.is_file() else {}
        final = p.animation_dir(anim_id) / "final" / "animation.json"
        jobs = [j for j in S.runner.all_jobs() if j.anim_id == anim_id]
        return {"id": anim_id, "character": meta.get("character"), "spec": spec.model_dump(mode="json"),
                "route": registry.route_for(spec), "final": read_json(final) if final.is_file() else None,
                "latest_job": jobs[0].model_dump(mode="json") if jobs else None, "jobs": [j.id for j in jobs]}

    @app.get("/api/animations")
    def list_animations():
        return [_anim_summary(a) for a in S.project.animations()]

    @app.post("/api/animations")
    def create_animation(body: AnimationIn):
        try:
            anim_id, _ = service.create_animation(S.project, body.character, body.action, facing=body.facing,
                                                  frames=body.frames, loop=body.loop, view=body.view,
                                                  motion=body.motion)
        except (ValueError, ProjectError) as e:
            raise HTTPException(400, str(e))
        return _anim_summary(anim_id)

    @app.get("/api/animations/{anim_id}")
    def get_animation(anim_id: str):
        try:
            return _anim_summary(anim_id)
        except ProjectError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/animations/{anim_id}/estimate")
    def estimate(anim_id: str):
        est = service.estimate(S.project, anim_id, mode=S.runner.hub.mode)
        g = est.pop("guide")
        if g is not None:
            d = S.project.animation_dir(anim_id)
            g.image.save(d / "preview-guide.png")
            est["guide_url"] = f"/api/files/{S.project.rel(d / 'preview-guide.png')}"
            est["guide_mode"] = (g.meta or {}).get("mode", "character")
        return est

    @app.post("/api/animations/{anim_id}/jobs")
    async def start_job(anim_id: str, seed: int = Body(0, embed=True)):
        await _require_access()
        st = S.runner.create_animation_job(anim_id, seed=seed)
        S.runner.start(st.id)
        return st.model_dump(mode="json")

    @app.post("/api/animations/{anim_id}/edits")
    def apply_edits(anim_id: str, body: list[dict] = Body(..., embed=True, alias="edits")):
        try:
            res = edits.apply(S.project, anim_id, body)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e))
        S.bus.publish({"type": "exported", "anim": anim_id})
        return {k: v for k, v in res.items()}

    @app.post("/api/animations/{anim_id}/export")
    def export(anim_id: str, engine: str | None = Body(None, embed=True)):
        if engine:
            spec = S.project.spec(anim_id)
            S.project.save_spec(anim_id, spec.model_copy(update={"engine": engine}))
        res = edits.apply(S.project, anim_id, [])
        S.bus.publish({"type": "exported", "anim": anim_id})
        return res

    @app.post("/api/animations/{anim_id}/repair")
    async def repair(anim_id: str, frame: int = Body(..., embed=True), kind: str = Body("pose", embed=True),
                     note: str | None = Body(None, embed=True), job: str | None = Body(None, embed=True)):
        from .manual import manual_repair

        await _require_access()
        try:
            res = await manual_repair(S.runner, anim_id, frame, kind=kind, note=note, job_id=job)
        except ValueError as e:
            raise HTTPException(400, str(e))
        S.bus.publish({"type": "exported", "anim": anim_id})
        return res

    # -- jobs -----------------------------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs():
        return [j.model_dump(mode="json") for j in S.runner.all_jobs()]

    @app.get("/api/jobs/{job_id:path}/candidates/{cid}/report")
    def candidate_report(job_id: str, cid: str):
        st = S.runner.get(job_id)
        if st is None:
            raise HTTPException(404, "no such job")
        cand = next((c for c in st.candidates if c["id"] == cid), None)
        if not cand or "report" not in cand:
            raise HTTPException(404, "no report")
        return read_json(S.project.abs(cand["report"]))

    @app.post("/api/jobs/{job_id:path}/cancel")
    def cancel(job_id: str):
        return {"cancelled": S.runner.cancel(job_id)}

    @app.post("/api/jobs/{job_id:path}/approve")
    def approve(job_id: str, ok: bool = Body(True, embed=True)):
        return {"delivered": S.runner.approve(job_id, ok)}

    async def _use_candidate(st, candidate: str) -> dict:
        from .jobs import _Handle

        h = S.runner.jobs.get(st.id) or _Handle(st)
        S.runner.jobs[st.id] = h
        for c in st.candidates:
            c["chosen"] = c["id"] == candidate
        st.winner = candidate
        res = await S.runner._export(h)
        S.runner.save(st)
        S.bus.publish({"type": "exported", "anim": st.anim_id})
        return res

    @app.post("/api/jobs/{job_id:path}/winner")
    async def choose_winner(job_id: str, candidate: str = Body(..., embed=True)):
        """A hand-picked winner outranks every automatic choice; the export is rewritten."""
        st = S.runner.get(job_id)
        if st is None:
            raise HTTPException(404, "no such job")
        chosen = next((c for c in st.candidates if c["id"] == candidate), None)
        if chosen is None or "score" not in chosen:
            raise HTTPException(400, "candidate has no analysis")
        return await _use_candidate(st, candidate)

    @app.post("/api/animations/{anim_id}/inbetween")
    async def inbetween(anim_id: str, after: int = Body(..., embed=True), job: str | None = Body(None, embed=True)):
        from .manual import manual_inbetween

        await _require_access()
        try:
            res = await manual_inbetween(S.runner, anim_id, after, job_id=job)
        except ValueError as e:
            raise HTTPException(400, str(e))
        S.bus.publish({"type": "exported", "anim": anim_id})
        return res

    @app.post("/api/findings/label")
    def label_finding(job: str = Body(..., embed=True), candidate: str = Body(..., embed=True),
                      index: int = Body(..., embed=True), correct: bool = Body(..., embed=True)):
        """Mark a finding right or wrong; judge precision per issue type is calibrated from these."""
        st = S.runner.get(job)
        cand = next((c for c in (st.candidates if st else []) if c["id"] == candidate), None)
        if cand is None or "report" not in cand:
            raise HTTPException(404, "no such candidate")
        findings = read_json(S.project.abs(cand["report"]))["findings"]
        if not 0 <= index < len(findings):
            raise HTTPException(400, "no such finding")
        f = findings[index]
        row = {"job": job, "candidate": candidate, "index": index, "metric": f["metric"], "source": f["source"],
               "issue": f.get("issue"), "correct": correct}
        p = S.project.root / "labels" / "findings.jsonl"
        p.parent.mkdir(exist_ok=True)
        with p.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        return row

    @app.get("/api/animations/{anim_id}/remedy-costs")
    def remedy_costs(anim_id: str):
        """Estimated cost of each one-click remedy for this animation."""
        est = service.estimate(S.project, anim_id, mode=S.runner.hub.mode)
        g = est.get("guide")
        mode = S.runner.hub.mode
        repair_model = registry.model_for("repair")
        size = (g.plan.width, g.plan.height) if g is not None else (1024, 1024)
        repair = 0.0 if mode == "synthetic" else registry.estimate_cost(repair_model, "edit", {}, input_sizes=[size, (512, 512)],
                                                                         n=1, size=size, prompt="x" * 400)
        return {"frame_repair": round(repair, 4), "identity_fix": round(repair, 4), "pose_fix": round(repair, 4),
                "inbetween": round(repair * 0.4, 4), "reroll": est["estimate_usd"], "confirm_layout": 0.0,
                "auto_fixed": 0.0, "ml_matte": 0.0, "regenerate_frame": round(repair, 4)}

    @app.post("/api/jobs/{job_id:path}/layout")
    async def confirm_layout(job_id: str, candidate: str = Body(..., embed=True),
                             cells: list[list[int]] = Body(..., embed=True), use: bool = Body(True, embed=True)):
        """Layout confirmation with hand-placed cells; also saved as a labelled golden example. The
        user has said where the frames are, so by default the confirmed candidate becomes the one
        exported (L23); `use: false` only adds it for comparison."""
        from .jobs import _Handle
        from .pipeline import layout as layout_mod
        import io

        from PIL import Image

        from .pipeline.analyze import GuideInfo, analyze

        st = S.runner.get(job_id)
        if st is None:
            raise HTTPException(404, "no such job")
        cand = next((x for x in st.candidates if x["id"] == candidate), None)
        if cand is None or not cand.get("sheet"):
            raise HTTPException(404, f"candidate {candidate!r} has no sheet to lay out")
        h = S.runner.jobs.get(job_id) or _Handle(st)
        S.runner.jobs[job_id] = h
        c, _ = S.runner._compiled(h)
        img = S.project.abs(cand["sheet"]).read_bytes()
        W, H = Image.open(io.BytesIO(img)).size
        # L20: well-formed cells only; a few pixels of overhang from dragging are clamped
        if len(cells) < 2:
            raise HTTPException(400, "a layout needs at least two cells")
        fixed = []
        for i, b in enumerate(cells):
            if len(b) != 4:
                raise HTTPException(400, f"cell {i + 1} must be [x0, y0, x1, y1]")
            x0, y0, x1, y1 = (int(v) for v in b)
            if x0 < -8 or y0 < -8 or x1 > W + 8 or y1 > H + 8:
                raise HTTPException(400, f"cell {i + 1} lies outside the {W}x{H} sheet")
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            if x1 - x0 < 4 or y1 - y0 < 4:
                raise HTTPException(400, f"cell {i + 1} has no area")
            fixed.append([x0, y0, x1, y1])
        cells = fixed
        # L16/L21: the cells are the layout; the guide still drives matte seeds, registration and pose
        guide = GuideInfo.from_guide(c.guide) if c.guide is not None else None
        prior = layout_mod.Prior(n=len(cells), cells=[tuple(x) for x in cells], forced=True,
                                 exclude=guide.exclude if guide else ([c.plan.strip_rect] if c.plan and c.plan.strip else []),
                                 detached=c.spec.character.kind == "effect")
        an = await asyncio.to_thread(analyze, img, c.spec, guide=guide, prior=prior, reference=c.reference)
        labels = S.project.root / "labels"
        labels.mkdir(exist_ok=True)
        tag = f"{st.id.replace('@', '_').replace('/', '_')}_{candidate}"
        (labels / f"{tag}.png").write_bytes(img)
        (labels / f"{tag}.json").write_text(json.dumps({"cells": cells, "spec": c.spec.model_dump(mode="json")}))
        from .generate import CandidateOut

        out = CandidateOut("c1", img, "image/png", {"layout_confirmed": cells})
        rnd = max(x["round"] for x in st.candidates) + 1
        ids = S.runner._store_candidates(h, [out], rnd, "layout", parent=candidate, key=cand.get("key"))
        entry = next(x for x in st.candidates if x["id"] == ids[0])
        cdir = S.project.abs(st.dir) / "candidates"
        from . import report as report_mod
        from .jobs import _save_frames

        _save_frames(cdir / f"{ids[0]}.frames", an.frames)
        (cdir / f"{ids[0]}.report.json").write_text(json.dumps(an.report.model_dump(mode="json"), indent=2))
        if an.matte is not None and an.matte.rgba.ndim == 3:
            Image.fromarray(an.matte.rgba, "RGBA").save(cdir / f"{ids[0]}.matte.png")
            removed = an.matte.mask & ~an.layout.clean_mask
            Image.fromarray((removed * 255).astype(np.uint8), "L").save(cdir / f"{ids[0]}.removed.png")
            entry["matte"] = S.project.rel(cdir / f"{ids[0]}.matte.png")
            entry["removed"] = S.project.rel(cdir / f"{ids[0]}.removed.png")
        html = report_mod.write_analysis(cdir / f"{ids[0]}.report", an, matte_mod.ingest(img),
                                         title=f"{st.anim_id} · {ids[0]} (confirmed layout)")
        entry.update(score=an.report.score, accepted=an.report.accepted,
                     frames=S.project.rel(cdir / f"{ids[0]}.frames"),
                     report=S.project.rel(cdir / f"{ids[0]}.report.json"), html=S.project.rel(html))
        S.runner.save(st)
        if use:
            await _use_candidate(st, ids[0])
            entry = next(x for x in st.candidates if x["id"] == ids[0])
        return entry

    @app.get("/api/jobs/{job_id:path}")
    def get_job(job_id: str):
        st = S.runner.get(job_id)
        if st is None:
            raise HTTPException(404, "no such job")
        return st.model_dump(mode="json")

    # -- ledger and files ---------------------------------------------------------------------

    @app.get("/api/ledger")
    def ledger(limit: int = 200):
        led = Ledger(S.project.ledger_path)
        entries = list(led.entries())[-limit:]
        return {"summary": led.summary(), "entries": entries}

    @app.get("/api/files/{rel:path}")
    def files(rel: str):
        try:
            p = S.project.abs(rel)
        except ProjectError:
            raise HTTPException(403, "outside the project")
        if not p.is_file():
            raise HTTPException(404, "no such file")
        return FileResponse(p, headers={"Cache-Control": "no-cache"})

    @app.websocket("/api/events")
    async def events(ws: WebSocket):
        tok = ws.query_params.get("token")
        host = (ws.headers.get("host") or "").split(":")[0]
        if not tok or not secrets.compare_digest(tok, S.token) or host not in ("127.0.0.1", "localhost"):
            await ws.close(code=4401)
            return
        await ws.accept()
        q = S.bus.subscribe()
        try:
            for e in list(S.bus.history)[-50:]:
                await ws.send_json(json.loads(json.dumps(e, default=str)))
            while not S.stopping:
                try:
                    e = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue  # wake up regularly so shutdown and dead clients are noticed
                await ws.send_json(json.loads(json.dumps(e, default=str)))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            S.bus.unsubscribe(q)
            try:
                await ws.close()
            except Exception:
                pass

    # -- cloud account (cloud plan 3, 4, 5) --------------------------------------------------------

    def _redirect_uri() -> str:
        return f"http://127.0.0.1:{S.port}/auth/callback"

    @app.get("/api/cloud/status")
    async def cloud_status(refresh: bool = False):
        return await asyncio.to_thread(S.cloud.status, refresh=refresh)

    @app.post("/api/cloud/access/refresh")
    async def cloud_access_refresh():
        """Re-check access now, after buying in the browser or when the window regains focus (C36)."""
        return await asyncio.to_thread(S.cloud.access, True)

    @app.post("/api/cloud/sign-in")
    async def cloud_sign_in():
        """Start a PKCE sign-in; the studio opens `authorize_url` in the browser (and again on request, C1)."""
        if not S.port:
            raise HTTPException(503, "the engine does not know its port yet")
        return await asyncio.to_thread(S.cloud.start_sign_in, _redirect_uri())

    @app.post("/api/cloud/sign-in/cancel")
    def cloud_sign_in_cancel():
        S.cloud.signin.cancel()
        return {"ok": True}

    @app.post("/api/cloud/sign-out")
    async def cloud_sign_out():
        await asyncio.to_thread(S.cloud.sign_out)
        return {"signed_in": False}

    @app.patch("/api/cloud/machine")
    async def cloud_machine(name: str = Body(..., embed=True)):
        try:
            return await asyncio.to_thread(S.cloud.rename_machine, name)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # -- project sync (cloud plan 6) -----------------------------------------------------------------

    from .cloud.client import Offline as _Offline, SignedOut as _SignedOut
    from .cloud.sync import SyncBusy, SyncPaused

    @app.exception_handler(SyncPaused)
    async def _sync_paused(request: Request, e: SyncPaused):
        if e.code in ("plan_required", "team_inactive", "quota_exceeded", "project_limit", "file_too_large"):
            S.cloud.invalidate_me()
        return JSONResponse({"detail": e.message, "error": e.payload()}, status_code=409)

    @app.exception_handler(SyncBusy)
    async def _sync_busy(request: Request, e: SyncBusy):
        return JSONResponse({"detail": str(e), "error": {"code": "busy", "message": str(e)}}, status_code=409)

    def _sync():
        if S.sync is None:
            raise HTTPException(409, "no project open")
        return S.sync

    def _workspace_name(owner: str | None) -> str | None:
        me = S.cloud._me or {}
        if not owner:
            return None
        if owner == (me.get("personal") or {}).get("id") or owner == (me.get("user") or {}).get("id"):
            return "Personal"
        return next((t["name"] for t in me.get("teams", []) if t["id"] == owner), None)

    @app.get("/api/project/sync")
    async def project_sync():
        st = await asyncio.to_thread(_sync().status)
        if st.get("linked"):
            await asyncio.to_thread(S.cloud.me)
            name = _workspace_name(st["owner_id"])
            if name is None:  # a workspace the cached account doesn't know yet (moved to a team, C23)
                await asyncio.to_thread(S.cloud.me, True)
                name = _workspace_name(st["owner_id"])
            st["workspace"] = name
        return st

    @app.post("/api/project/sync/link")
    async def project_sync_link(owner: str = Body("me", embed=True)):
        sync = _sync()
        made = await sync.exclusive(sync.syncer.link, owner)
        try:
            await sync.run()
        except (SyncPaused, _Offline):
            pass  # the link stands; the project shows why the first push waits
        return {"project": made, "sync": await asyncio.to_thread(sync.status)}

    @app.post("/api/project/sync/unlink")
    async def project_sync_unlink():
        sync = _sync()
        await sync.exclusive(sync.syncer.unlink)
        S.bus.publish({"type": "sync_unlinked"})
        return {"linked": False}

    @app.post("/api/project/sync/now")
    async def project_sync_now(push: bool = Body(True, embed=True), pull: bool = Body(True, embed=True)):
        return await _sync().run(push=push, pull=pull)

    @app.post("/api/project/sync/resolve")
    async def project_sync_resolve(choices: list[dict] = Body(..., embed=True)):
        sync = _sync()
        try:
            res = await sync.exclusive(sync.syncer.resolve, choices)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if res["open"] == 0:
            S.bus.publish({"type": "sync_conflict", "groups": []})
        return res

    @app.post("/api/project/sync/copied")
    async def project_sync_copied(keep: bool = Body(..., embed=True)):
        """C29: keep syncing this copied folder here, or make it a separate project (the link goes)."""
        sync = _sync()
        await sync.exclusive(sync.syncer.keep_link if keep else sync.syncer.unlink)
        if keep:
            try:
                await sync.run()
            except (SyncPaused, _Offline):
                pass
        return await asyncio.to_thread(sync.status)

    @app.put("/api/cloud/auto-sync")
    def cloud_auto_sync(on: bool = Body(..., embed=True)):
        lib.set_state(auto_sync=on)
        if on and S.sync is not None:
            S.sync.poke()
        return {"auto": on}

    @app.get("/api/cloud/projects")
    async def cloud_projects():
        """Cloud projects across workspaces, each marked with its local folder when it's on this machine."""
        rows = (await asyncio.to_thread(S.cloud.session.get, "/api/v1/projects"))["projects"]
        here = await asyncio.to_thread(lib.cloud_links)
        return {"projects": [{**r, "local_path": here.get(r["id"])} for r in rows]}

    @app.get("/api/cloud/blob/{owner}/{sha}")
    async def cloud_blob(owner: str, sha: str):
        """Thumbnails and conflict previews: an <img> can't send the bearer token, so the engine fetches
        the file, verifies its hash and keeps it on disk (cloud plan 3)."""
        import re

        if not re.fullmatch(r"[a-f0-9]{64}", sha) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", owner):
            raise HTTPException(400, "bad blob reference")
        cache = lib.root() / ".spriteplay-cache" / "blobs"
        path = cache / sha
        if not path.is_file():
            def fetch() -> None:
                import hashlib

                cache.mkdir(parents=True, exist_ok=True)
                tmp = cache / f"{sha}.part"
                h = hashlib.sha256()
                with S.cloud.session.stream("GET", f"/api/v1/blobs/{sha}", params={"owner": owner}) as r, \
                        open(tmp, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        h.update(chunk)
                        fh.write(chunk)
                if h.hexdigest() != sha:
                    tmp.unlink(missing_ok=True)
                    raise HTTPException(502, "the cloud file did not match its hash")
                os.replace(tmp, path)

            await asyncio.to_thread(fetch)
        head = path.read_bytes()[:12]
        media = ("image/png" if head.startswith(b"\x89PNG") else "image/gif" if head.startswith(b"GIF8") else
                 "image/jpeg" if head.startswith(b"\xff\xd8") else "image/webp" if head[8:12] == b"WEBP" else
                 "application/json" if head.lstrip()[:1] in (b"{", b"[") else "application/octet-stream")
        return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=31536000, immutable"})

    _downloads: dict[str, bool] = {}

    @app.post("/api/cloud/projects/{pid}/download")
    async def cloud_download(pid: str, location: str | None = Body(None, embed=True),
                             open_after: bool = Body(True, embed=True)):
        """Download a cloud project into a new folder (6.9), then open it."""
        from .cloud.sync import download_project

        project = (await asyncio.to_thread(S.cloud.session.get, f"/api/v1/projects/{pid}"))["project"]
        parent = Path(location).expanduser() if location else lib.root()
        if not parent.is_dir():
            raise HTTPException(400, f"location {parent} is not a folder")
        _downloads[pid] = False
        try:
            target = await asyncio.to_thread(download_project, S.cloud.session, project, parent,
                                             publish=S.bus.publish, cancelled=lambda: _downloads.get(pid, False))
        finally:
            _downloads.pop(pid, None)
        lib.touch(target)
        opened = False
        if open_after and not S.busy():
            await _switch(Project.open(target))
            opened = True
        return {"path": str(target), "opened": opened, "card": lib.card(target, S.project.root if S.project else None)}

    @app.post("/api/cloud/projects/{pid}/download/cancel")
    def cloud_download_cancel(pid: str):
        if pid in _downloads:
            _downloads[pid] = True
        return {"cancelling": pid in _downloads}

    # -- asset library (cloud plan 8) ------------------------------------------------------------------

    from .cloud import library as cloud_library

    @app.get("/api/cloud/library")
    async def cloud_library_list(owner: str | None = None, kind: str | None = None, q: str | None = None):
        return {"items": await asyncio.to_thread(cloud_library.browse, S.cloud.session, owner, kind, q)}

    @app.get("/api/cloud/library/{item_id}")
    async def cloud_library_item(item_id: str):
        return await asyncio.to_thread(cloud_library.item, S.cloud.session, item_id)

    @app.post("/api/cloud/library/{item_id}/import")
    async def cloud_library_import(item_id: str):
        """Import an item into the open project; a linked project pushes it on the next round."""
        if S.project is None:
            raise HTTPException(409, "no project open")
        run = lambda: cloud_library.import_item(S.cloud.session, S.project.root, item_id)  # noqa: E731
        try:
            res = await (S.sync.exclusive(run) if S.sync is not None else asyncio.to_thread(run))
        except cloud_library.LibraryError as e:
            raise HTTPException(400, str(e))
        S.bus.publish({"type": "project_files_changed", "paths": res["paths"]})
        S.bus.publish({"type": "library_imported", "item": item_id, "path": res["path"]})
        return res

    @app.post("/api/project/library/publish")
    async def project_library_publish(prefix: str = Body(..., embed=True), name: str = Body(..., embed=True),
                                      kind: str = Body(..., embed=True), owner: str | None = Body(None, embed=True),
                                      description: str | None = Body(None, embed=True)):
        run = lambda: cloud_library.publish(S.cloud.session, S.project.root, prefix, name=name, kind=kind,  # noqa: E731
                                            owner=owner, description=description)
        try:
            return await (S.sync.exclusive(run) if S.sync is not None else asyncio.to_thread(run))
        except cloud_library.LibraryError as e:
            raise HTTPException(400, str(e))

    @app.get("/auth/callback", include_in_schema=False)
    async def auth_callback(request: Request):
        """The browser's return from sign-in (outside /api/, so the launch token does not apply). It does
        nothing unless `state` matches the pending sign-in (C2), answers only with a redirect or a short
        page, and logs nothing from the query."""
        from fastapi.responses import RedirectResponse

        from .cloud.auth import INVALID_PAGE, browser_redirect

        params = {k: v for k, v in request.query_params.items() if k in ("code", "state", "iss", "error")}
        result = await asyncio.to_thread(S.cloud.complete_sign_in, params)
        if result == "invalid":
            return HTMLResponse(INVALID_PAGE, status_code=400)
        return RedirectResponse(browser_redirect(S.cloud.session.base, result), status_code=302)

    # -- studio ----------------------------------------------------------------------------------

    @app.get("/{path:path}", include_in_schema=False)
    def studio(path: str):
        target = STUDIO_DIST / path
        if path and target.is_file() and STUDIO_DIST in target.resolve().parents:
            return FileResponse(target)
        index = STUDIO_DIST / "index.html"
        if index.is_file():
            return HTMLResponse(index.read_text())
        return HTMLResponse("<h1>spriteguru engine</h1><p>The studio UI is not built. Run <code>npm run build</code>"
                            " in studio/.</p>")

    return app


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_server(project: Project | None, *, port: int = 0, token: str | None = None, mode: str | None = None,
               open_browser: bool = False) -> None:
    """Bind 127.0.0.1 first, then announce {port, token}: the socket already accepts (connections
    queue in the backlog) by the time a launcher or test reads the announcement."""
    import uvicorn

    token = token or secrets.token_urlsafe(24)
    if project is not None:  # a project opened on the command line gets its gallery card too
        from . import library

        library.touch(project.root)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    port = sock.getsockname()[1]
    app = create_app(project, token, mode=mode)
    app.state.S.port = port
    url = f"http://127.0.0.1:{port}/?token={token}"
    print(json.dumps({"port": port, "token": token, "url": url}), flush=True)
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    config = uvicorn.Config(app, log_level=app_env("LOG", "warning"), timeout_graceful_shutdown=3)
    uvicorn.Server(config).run(sockets=[sock])
