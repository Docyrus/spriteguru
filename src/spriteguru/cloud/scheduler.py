"""When the open project syncs (cloud plan 6.8): a full round when a linked project opens, a push 5
seconds after a change (an export, a turnaround, an approval, a settings change), a pull every 60
seconds while the studio is open and every 10 minutes otherwise, and "Sync now". Offline, rounds back
off from 30 seconds to 10 minutes and the project shows "Offline" instead of errors (C19). With
auto-sync off only manual rounds run.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable

from .. import library
from .client import Offline, SignedOut
from .sync import SyncBusy, Syncer, SyncPaused

PUSH_DEBOUNCE = 5.0
PULL_OPEN = 60.0
PULL_IDLE = 600.0
BACKOFF_MIN, BACKOFF_MAX = 30.0, 600.0
TRIGGERS = {"exported", "turnaround_done", "character_approved", "settings_changed", "job_done", "library_imported"}
OFFLINE_MESSAGE = "Offline. Changes will sync when you're connected."


def auto_sync() -> bool:
    return library.get_state("auto_sync", True) is not False


class ProjectSync:
    def __init__(self, root: Path, cloud, bus, busy: Callable[[], set[str]],
                 on_pulled: Callable[[list[str]], Awaitable[None]] | None = None):
        self.cloud = cloud
        self.bus = bus
        self.syncer = Syncer(root, cloud.session, publish=bus.publish, busy=busy)
        self.on_pulled = on_pulled
        self._wake = asyncio.Event()
        self._round = asyncio.Lock()
        self._push_at: float | None = None
        self._pull_at = 0.0  # a full round as soon as a linked project opens
        self._backoff = 0.0
        self._tasks: list[asyncio.Task] = []
        self.syncing = False
        self.offline = False
        self.last_result: dict | None = None

    # -- lifecycle ---------------------------------------------------------------------------------

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._loop()), asyncio.create_task(self._listen())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []

    def poke(self) -> None:
        """Something changed: push in a few seconds (a burst of changes makes one round)."""
        self._push_at = time.monotonic() + PUSH_DEBOUNCE
        self._wake.set()

    def _interval(self) -> float:
        return PULL_OPEN if self.bus.listeners > 1 else PULL_IDLE  # the scheduler itself listens too

    def _eligible(self) -> bool:
        try:
            return (self.cloud.session.signed_in and self.syncer.link_block() is not None
                    and self.syncer.copied() is None)
        except Exception:
            return False

    async def _listen(self) -> None:
        q = self.bus.subscribe()
        try:
            while True:
                e = await q.get()
                if e.get("type") in TRIGGERS:
                    self.poke()
        finally:
            self.bus.unsubscribe(q)

    async def _loop(self) -> None:
        while True:
            now = time.monotonic()
            due = self._pull_at <= now or (self._push_at is not None and self._push_at <= now)
            if due:
                if auto_sync() and self._eligible():
                    try:
                        await self.run()
                    except (Offline, SyncPaused, SyncBusy, SignedOut):
                        pass
                    except Exception as e:  # a bug must not end syncing for the session
                        self.bus.publish({"type": "sync_paused", "code": "error", "message": f"{type(e).__name__}: {e}"})
                        self._pull_at = time.monotonic() + self._interval()
                else:
                    self._pull_at = time.monotonic() + self._interval()
                    self._push_at = None
            deadline = min(self._pull_at, self._push_at if self._push_at is not None else float("inf"))
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), max(0.05, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                pass

    # -- rounds ------------------------------------------------------------------------------------------

    async def run(self, *, push: bool = True, pull: bool = True) -> dict:
        async with self._round:
            self.syncing = True
            try:
                res = await asyncio.to_thread(self.syncer.run, push=push, pull=pull)
            except Offline:
                self._backoff = min(BACKOFF_MAX, max(BACKOFF_MIN, self._backoff * 2))
                self._pull_at = time.monotonic() + self._backoff
                if not self.offline:
                    self.bus.publish({"type": "sync_paused", "code": "offline", "message": OFFLINE_MESSAGE})
                self.offline = True
                raise
            except SyncBusy:
                self._pull_at = time.monotonic() + BACKOFF_MIN
                raise
            except (SyncPaused, SignedOut):
                self._pull_at = time.monotonic() + self._interval()
                self._push_at = None
                raise
            finally:
                self.syncing = False
            self._backoff = 0.0
            self.offline = False
            self._pull_at = time.monotonic() + self._interval()
            self._push_at = None
            self.last_result = res
        if res["pulled"] and self.on_pulled is not None:
            await self.on_pulled(res["pulled"])
        return res

    async def exclusive(self, fn: Callable, *args):
        """Run a blocking sync operation (resolve, unlink) between rounds."""
        async with self._round:
            return await asyncio.to_thread(fn, *args)

    def status(self) -> dict:
        st = self.syncer.status()
        return {**st, "syncing": self.syncing, "offline": self.offline, "auto": auto_sync(),
                "offline_message": OFFLINE_MESSAGE if self.offline else None}
