"""Latest-wins background provider-intake probing."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

INTAKE_REASON_UNAVAILABLE = "intake_probe_unavailable"


@dataclass(frozen=True, slots=True)
class IntakeProbeResult:
    generation: int
    probes: tuple[object, ...]
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.reason is None


@dataclass(frozen=True, slots=True)
class _Request:
    generation: int
    callback: Callable[[IntakeProbeResult], None]


class IntakeProbeService:
    def __init__(self, probe: Callable[[], object]) -> None:
        if not callable(probe):
            raise ValueError("intake probe must be callable")
        self._probe = probe
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False
        self._in_flight = False
        self._pending: _Request | None = None

    def request(self, callback: Callable[[IntakeProbeResult], None]) -> int | None:
        if not callable(callback):
            raise ValueError("intake callback must be callable")
        with self._lock:
            if self._closed:
                return None
            self._generation += 1
            request = _Request(self._generation, callback)
            if self._in_flight:
                self._pending = request
            else:
                self._start_locked(request)
            return request.generation

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._pending = None

    def _start_locked(self, request: _Request) -> None:
        self._in_flight = True
        threading.Thread(
            target=self._run,
            args=(request,),
            name="SidePulseIntakeProbe",
            daemon=True,
        ).start()

    def _run(self, request: _Request) -> None:
        try:
            value = self._probe()
            probes = tuple(value) if value is not None else ()
            result = IntakeProbeResult(request.generation, probes)
        except Exception:
            result = IntakeProbeResult(
                request.generation,
                (),
                INTAKE_REASON_UNAVAILABLE,
            )
        callback = None
        with self._lock:
            current = not self._closed and request.generation == self._generation
            self._in_flight = False
            if current:
                callback = request.callback
            if not self._closed and self._pending is not None:
                pending = self._pending
                self._pending = None
                self._start_locked(pending)
        if callback is not None:
            try:
                callback(result)
            except Exception:
                pass
