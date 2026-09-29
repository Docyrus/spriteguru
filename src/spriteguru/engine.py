"""Engine wiring shared by the CLI and the API: one hub, one runner per project."""

from __future__ import annotations

from .cache import Cache
from .jobs import EventBus, JobRunner
from .ledger import Ledger
from .project import Project
from .providers.hub import ProviderHub


def make_hub(project: Project, *, mode: str | None = None, replay: bool = False, approve=None,
             events=None) -> ProviderHub:
    s = project.config.settings
    return ProviderHub(Cache(project.cache_dir), Ledger(project.ledger_path), mode=mode or s.provider_mode,
                       replay=replay, session_cap=s.session_cap_usd, job_cap=s.job_cap_usd, approve=approve,
                       events=events)


def make_runner(project: Project, *, mode: str | None = None, replay: bool = False, approve=None,
                bus: EventBus | None = None) -> JobRunner:
    hub = make_hub(project, mode=mode, replay=replay)
    return JobRunner(project, hub, bus, interactive_approve=approve)
