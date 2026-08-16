"""Single-worker, latest-wins transcript fallback scanning."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

MAX_TRANSCRIPT_BATCH_RECORDS = 4_000
TRANSCRIPT_REASON_SCAN_FAILED = "transcript_scan_failed"
TRANSCRIPT_REASON_INVALID_MONITOR = "transcript_monitor_invalid"


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
    callback: Callable[[TranscriptFallbackBatch], None]
    force: bool


class TranscriptFallbackService:
    def __init__(self, *, maximum_records: int = MAX_TRANSCRIPT_BATCH_RECORDS) -> None:
        self._maximum_records = max(1, int(maximum_records))
        self._lock = threading.RLock()
        self._generation = 0
        self._closed = False
        self._in_flight = False
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
            self._generation += 1
            request = _Request(
                self._generation,
                monitor,
                known_signature,
                callback,
                bool(force),
            )
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
            name="SidePulseTranscriptFallback",
            daemon=True,
        ).start()

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
            if not request.force and signature == request.known_signature:
                records = ()
            else:
                records = tuple(
                    sorted(
                        record_reader(),
                        key=lambda record: record.logged_at,
                    )[-self._maximum_records :]
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
        callback = None
        pending = None
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
                callback(batch)
            except Exception:
                pass
