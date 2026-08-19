"""Latest-known-good battery observation for the AppKit host.

Subprocess work runs on at most one daemon worker. UI callers receive the most
recent immutable observation immediately and never wait for ``ioreg``.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .battery import BatterySnapshot, read_battery_snapshot

BATTERY_OBSERVATION_MIN_INTERVAL_SECONDS = 5.0
BATTERY_REASON_UNAVAILABLE = "battery_unavailable"
BATTERY_REASON_TIMED_OUT = "battery_timed_out"
BATTERY_REASON_MALFORMED = "battery_malformed"


@dataclass(frozen=True, slots=True)
class BatteryObservation:
    snapshot: BatterySnapshot | None
    observed_at: float | None
    attempted_at: float | None
    reason: str | None
    in_flight: bool

    @property
    def available(self) -> bool:
        return self.snapshot is not None


@dataclass(frozen=True, slots=True)
class _BatteryRequest:
    generation: int
    full_charge_watts: float | None
    callback: Callable[[BatteryObservation], None] | None


class BatteryObservationService:
    def __init__(
        self,
        *,
        reader: Callable[..., BatterySnapshot] = read_battery_snapshot,
        monotonic: Callable[[], float] = time.monotonic,
        minimum_interval: float = BATTERY_OBSERVATION_MIN_INTERVAL_SECONDS,
    ) -> None:
        self._reader = reader
        self._monotonic = monotonic
        self._minimum_interval = max(0.1, float(minimum_interval))
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False
        self._in_flight = False
        self._pending: _BatteryRequest | None = None
        self._snapshot: BatterySnapshot | None = None
        self._observed_at: float | None = None
        self._attempted_at: float | None = None
        self._reason: str | None = None
        self._last_requested_watts: float | None = None

    def observation(self) -> BatteryObservation:
        with self._lock:
            return self._observation_locked()

    def request(
        self,
        *,
        full_charge_watts: float | None,
        callback: Callable[[BatteryObservation], None] | None = None,
        force: bool = False,
    ) -> BatteryObservation:
        now = self._monotonic()
        with self._lock:
            if self._closed:
                return self._observation_locked()
            due = (
                force
                or self._attempted_at is None
                or now - self._attempted_at >= self._minimum_interval
                or full_charge_watts != self._last_requested_watts
            )
            if not due:
                return self._observation_locked()
            self._generation += 1
            request = _BatteryRequest(
                self._generation,
                full_charge_watts,
                callback,
            )
            if self._in_flight:
                self._pending = request
                return self._observation_locked()
            self._start_locked(request, now)
            return self._observation_locked()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._pending = None

    def _observation_locked(self) -> BatteryObservation:
        return BatteryObservation(
            snapshot=self._snapshot,
            observed_at=self._observed_at,
            attempted_at=self._attempted_at,
            reason=self._reason,
            in_flight=self._in_flight,
        )

    def _start_locked(self, request: _BatteryRequest, now: float) -> None:
        self._in_flight = True
        self._attempted_at = now
        self._last_requested_watts = request.full_charge_watts
        threading.Thread(
            target=self._run,
            args=(request,),
            name="SidePulseBatteryObservation",
            daemon=True,
        ).start()

    def _run(self, request: _BatteryRequest) -> None:
        snapshot = None
        reason = None
        try:
            snapshot = self._reader(full_charge_watts=request.full_charge_watts)
            if not isinstance(snapshot, BatterySnapshot):
                snapshot = None
                reason = BATTERY_REASON_MALFORMED
        except (subprocess.TimeoutExpired, TimeoutError):
            reason = BATTERY_REASON_TIMED_OUT
        except Exception:
            reason = BATTERY_REASON_UNAVAILABLE

        callback = None
        observation = None
        with self._lock:
            if self._closed or request.generation != self._generation:
                self._in_flight = False
            else:
                if snapshot is not None:
                    self._snapshot = snapshot
                    self._observed_at = self._monotonic()
                    self._reason = None
                else:
                    self._reason = reason or BATTERY_REASON_UNAVAILABLE
                self._in_flight = False
                callback = request.callback
                observation = self._observation_locked()
            if not self._closed and self._pending is not None:
                pending = self._pending
                self._pending = None
                self._start_locked(pending, self._monotonic())

        if callback is not None and observation is not None:
            try:
                callback(observation)
            except Exception:
                pass
