"""An exclusive lock between processes on one machine (the studio's engine and CLI commands).

The OS releases it when the holder exits, so a crashed process never leaves a stale lock behind.
The holder writes its pid and start time into the file, so a waiting process can say who holds it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class FileLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None
        self._thread = threading.Lock()  # threads of one process queue here first

    def _try(self, fd: int) -> bool:
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def acquire(self, timeout: float | None = None, poll: float = 0.05) -> bool:
        """Wait up to `timeout` seconds (forever when None); False when it is still held."""
        deadline = None if timeout is None else time.monotonic() + timeout
        if not self._thread.acquire(timeout=-1 if timeout is None else max(timeout, 0)):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        while not self._try(fd):
            if deadline is not None and time.monotonic() >= deadline:
                os.close(fd)
                self._thread.release()
                return False
            time.sleep(poll)
        self._fd = fd
        info = json.dumps({"pid": os.getpid(), "since": dt.datetime.now().isoformat(timespec="seconds")})
        # the pid lives after byte 0, which Windows locks
        os.lseek(fd, 1, os.SEEK_SET)
        os.write(fd, info.encode())
        os.ftruncate(fd, 1 + len(info))
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
            self._thread.release()

    def holder(self) -> dict | None:
        """Who holds (or last held) the lock, from the file."""
        try:
            raw = self.path.read_bytes()[1:]
            return json.loads(raw) if raw else None
        except (OSError, ValueError):
            return None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
