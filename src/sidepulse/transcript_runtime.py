"""Single-worker, deduplicated and bounded transcript fallback scanning."""

from __future__ import annotations

import heapq
import threading
from dataclasses import dataclass, replace
from typing import Callable

MAX_TRANSCRIPT_BATCH_RECORDS = 4_000
MAX_TRANSCRIPT_SCAN_RECORDS = 100_000
MAX_TRANSCRIPT_CALLBACKS = 16
TRANSCRIPT_REASON_SCAN_FAILED = "transcript_scan_failed"
TRANSCRIPT_REASON_INVALID_MONITOR = "transcript_monitor_invalid"
TRANSCRIPT_REASON_SCAN_BUDGET = "transcript_scan_budget_exceeded"


@dataclass(frozen=True, slots=True)
class TranscriptFallbackBatch:
    generation: int
    monitor_identity: int
    signature: object | None
    records: tuple[object, ...]
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.reason is None


@dataclass(frozen=True, slots=True)
class _Request:
    generation: int
    monitor: object
    known_signature: object | None
    callbacks: tuple[Callable[[TranscriptFallbackBatch], None], ...]
    force: bool

    @property
    def key(self) -> tuple[int, object | None, bool]:
        return (id(self.monitor), self.known_signature, self.force)

    def with_callback(
        self,
        callback: Callable[[TranscriptFallbackBatch], None],
    ) -> _Request:
        if callback in self.callbacks:
            return self
        callbacks = (*self.callbacks[-(MAX_TRANSCRIPT_CALLBACKS - 1) :], callback)
        return replace(self, callbacks=callbacks)


class TranscriptFallbackService:
    def __init__(
        self,
        *,
        maximum_records: int = MAX_TRANSCRIPT_BATCH_RECORDS,
        maximum_scanned_records: int = MAX_TRANSCRIPT_SCAN_RECORDS,
    ) -> None:
        self._maximum_records = max(1, int(maximum_records))
        self._maximum_scanned_records = max(
            self._maximum_records,
            int(maximum_scanned_records),
        )
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False
        self._in_flight: _Request | None = None
        self._pending: _Request | None = None

    def request(
        self,
        monitor: object,
        *,
        known_signature: object | None,
        callback: Callable[[TranscriptFallbackBatch], None],
        force: bool = False,
    ) -> int | None:
        if not callable(callback):
            raise ValueError("transcript callback must be callable")
        with self._lock:
            if self._closed:
                return None
            key = (id(monitor), known_signature, bool(force))
            if self._in_flight is not None and self._in_flight.key == key:
                self._in_flight = self._in_flight.with_callback(callback)
                return self._in_flight.generation
            if self._pending is not None and self._pending.key == key:
                self._pending = self._pending.with_callback(callback)
                return self._pending.generation
            self._generation += 1
            request = _Request(
                self._generation,
                monitor,
                known_signature,
                (callback,),
                bool(force),
            )
            if self._in_flight is None:
                self._start_locked(request)
            else:
                self._pending = request
            return request.generation

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._pending = None
            if self._in_flight is not None:
                self._in_flight = replace(self._in_flight, callbacks=())

    def _start_locked(self, request: _Request) -> None:
        self._in_flight = request
        threading.Thread(
            target=self._run,
            args=(request,),
            name="SidePulseTranscriptFallback",
            daemon=True,
        ).start()

    def _latest_records(self, records) -> tuple[object, ...]:
        scanned = 0

        def bounded_records():
            nonlocal scanned
            for record in records:
                scanned += 1
                if scanned > self._maximum_scanned_records:
                    raise OverflowError
                yield record

        latest = heapq.nlargest(
            self._maximum_records,
            bounded_records(),
            key=lambda record: record.logged_at,
        )
        latest.sort(key=lambda record: record.logged_at)
        return tuple(latest)

    def _scan(self, request: _Request) -> TranscriptFallbackBatch:
        monitor = request.monitor
        signature_reader = getattr(monitor, "input_signature", None)
        record_reader = getattr(monitor, "iter_records", None)
        if not callable(signature_reader) or not callable(record_reader):
            return TranscriptFallbackBatch(
                request.generation,
                id(monitor),
                None,
                (),
                TRANSCRIPT_REASON_INVALID_MONITOR,
            )
        try:
            signature = signature_reader()
            records = (
                ()
                if not request.force and signature == request.known_signature
                else self._latest_records(record_reader())
            )
        except OverflowError:
            return TranscriptFallbackBatch(
                request.generation,
                id(monitor),
                None,
                (),
                TRANSCRIPT_REASON_SCAN_BUDGET,
            )
        except Exception:
            return TranscriptFallbackBatch(
                request.generation,
                id(monitor),
                None,
                (),
                TRANSCRIPT_REASON_SCAN_FAILED,
            )
        return TranscriptFallbackBatch(
            request.generation,
            id(monitor),
            signature,
            records,
        )

    def _run(self, request: _Request) -> None:
        batch = self._scan(request)
        callbacks: tuple[Callable[[TranscriptFallbackBatch], None], ...] = ()
        with self._lock:
            current = self._in_flight
            superseded = self._pending is not None
            if current is not None and current.generation == request.generation:
                callbacks = (
                    current.callbacks
                    if not self._closed and not superseded
                    else ()
                )
                self._in_flight = None
            if not self._closed and self._pending is not None:
                next_request = self._pending
                self._pending = None
                self._start_locked(next_request)
        # Identical refresh requests share one scan. A genuinely newer request
        # supersedes the old result before it can reach the controller.
        for callback in callbacks:
            try:
                callback(batch)
            except Exception:
                pass
