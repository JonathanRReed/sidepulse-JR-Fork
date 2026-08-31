from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import TypeVar

_PayloadT = TypeVar("_PayloadT")


class UsageRefreshWorkerOwner:
    """Own short-lived usage refresh threads and close them under one budget."""

    def __init__(self) -> None:
        self._workers: set[object] = set()
        self._lock = threading.RLock()
        self._closed = False

    def start(
        self,
        target: Callable[[_PayloadT], None],
        payload: _PayloadT,
    ) -> bool:
        worker_holder: list[object] = []

        def run(worker_payload: _PayloadT) -> None:
            try:
                target(worker_payload)
            finally:
                with self._lock:
                    if worker_holder:
                        self._workers.discard(worker_holder[0])

        worker = threading.Thread(target=run, args=(payload,), daemon=True)
        worker_holder.append(worker)
        with self._lock:
            if self._closed:
                return False
            self._workers.add(worker)
            try:
                worker.start()
            except Exception:
                self._workers.discard(worker)
                raise
        return True

    def snapshot(self) -> tuple[object, ...]:
        with self._lock:
            return tuple(self._workers)

    def close_all(self, *, timeout_seconds: float) -> bool:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or float(timeout_seconds) < 0.0
        ):
            raise ValueError("invalid usage refresh close timeout")
        deadline = time.monotonic() + float(timeout_seconds)
        with self._lock:
            self._closed = True
        while True:
            with self._lock:
                workers = tuple(self._workers)
            if not workers:
                return True
            for worker in workers:
                if not isinstance(worker, threading.Thread):
                    with self._lock:
                        self._workers.discard(worker)
                    continue
                if worker is threading.current_thread():
                    return False
                worker.join(timeout=max(0.0, deadline - time.monotonic()))
                if not worker.is_alive():
                    with self._lock:
                        self._workers.discard(worker)
            if time.monotonic() >= deadline:
                with self._lock:
                    return not self._workers
