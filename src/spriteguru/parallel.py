"""Process-parallel map for CPU-bound batch work (eval sets, golden fixtures). Image analysis is
NumPy/OpenCV/ONNX work that threads cannot scale, so each item runs in its own process; results
keep input order, so digests do not depend on scheduling."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from typing import Any


def default_workers(cap: int = 6) -> int:
    return max(1, min(cap, (os.cpu_count() or 2) - 2))


def pmap(fn: Callable[..., Any], *iterables: Iterable, workers: int | None = None) -> list:
    args = [list(it) for it in iterables]
    n = default_workers() if workers is None else workers
    if n <= 1 or len(args[0]) <= 1:
        return [fn(*a) for a in zip(*args)]
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=min(n, len(args[0]))) as pool:
        return list(pool.map(fn, *args))
