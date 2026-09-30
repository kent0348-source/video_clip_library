from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


PREFIX = "[clip_library]"


def log(message: str) -> None:
    print(f"{PREFIX} {message}", flush=True)


@contextmanager
def timed(label: str) -> Iterator[None]:
    log(f"{label}...")
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        log(f"{label} finished in {elapsed:.2f}s")
