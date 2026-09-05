"""Single-worker, coalesced background provider-intake probing."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

INTAKE_REASON_UNAVAILABLE = "intake_probe_unavailable"
MAX_INTAKE_CALLBACKS = 16


@dataclass(frozen=True, slots=True)
class IntakeProbeResult:
    generation: int
    probes: tuple[object, ...]
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.reason is None


class IntakeProbeService:
    """Run at most one identical provider probe and fan its result out."""

    def __init__(self, probe: Callable[[], object]) -> None:
        if not callable(probe):
            raise ValueError("intake probe must be callable")
        self._probe = probe
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False
        self._in_flight = False
        self._callbacks: list[Callable[[IntakeProbeResult], None]] = []
        self._rerun_requested = False
        self._rerun_callbacks: list[Callable[[IntakeProbeResult], None]] = []

    def request(
        self,
        callback: Callable[[IntakeProbeResult], None],
        *,
        force: bool = False,
    ) -> int | None:
        if not callable(callback):
            raise ValueError("intake callback must be callable")
        with self._lock:
            if self._closed:
                return None
            if self._in_flight:
                if force:
                    self._rerun_requested = True
                    if callback not in self._rerun_callbacks:
                        self._rerun_callbacks = [
                            *self._rerun_callbacks[-(MAX_INTAKE_CALLBACKS - 1) :],
                            callback,
                        ]
                    return self._generation + 1
                if callback not in self._callbacks:
                    self._callbacks = [
                        *self._callbacks[-(MAX_INTAKE_CALLBACKS - 1) :],
                        callback,
                    ]
                return self._generation
            self._generation += 1
            generation = self._generation
            self._callbacks = [callback]
            self._in_flight = True
            threading.Thread(
                target=self._run,
                args=(generation,),
                name="SidePulseIntakeProbe",
                daemon=True,
            ).start()
            return generation

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._callbacks = []
            self._rerun_requested = False
            self._rerun_callbacks = []

    def _run(self, generation: int) -> None:
        try:
            value = self._probe()
            probes = tuple(value) if value is not None else ()
            result = IntakeProbeResult(generation, probes)
        except Exception:
            result = IntakeProbeResult(
                generation,
                (),
                INTAKE_REASON_UNAVAILABLE,
            )
        callbacks: tuple[Callable[[IntakeProbeResult], None], ...] = ()
        rerun_generation: int | None = None
        with self._lock:
            current = not self._closed and generation == self._generation
            if current:
                callbacks = tuple(self._callbacks)
            if current and self._rerun_requested:
                self._generation += 1
                rerun_generation = self._generation
                self._callbacks = self._rerun_callbacks
                self._rerun_callbacks = []
                self._rerun_requested = False
            else:
                self._in_flight = False
                self._callbacks = []
        for callback in callbacks:
            try:
                callback(result)
            except Exception:
                pass
        if rerun_generation is not None:
            threading.Thread(
                target=self._run,
                args=(rerun_generation,),
                name="SidePulseIntakeProbe",
                daemon=True,
            ).start()
