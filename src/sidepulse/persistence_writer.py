"""Bounded serial persistence execution with drain-on-close semantics."""

from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

MAX_PERSISTENCE_PENDING: Final = 64
MAX_PERSISTENCE_KEY_BYTES: Final = 96
MAX_PERSISTENCE_METRIC_COUNT: Final = 10_000

_SAFE_KEY = re.compile(r"[a-z0-9][a-z0-9._:-]*\Z")


class PersistenceDisposition(str, Enum):
    STARTED = "started"
    QUEUED = "queued"
    REPLACED_PENDING = "replaced_pending"
    REFUSED_FULL = "refused_full"
    REFUSED_CLOSED = "refused_closed"


class PersistenceOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REPLACED = "replaced"


@dataclass(frozen=True, slots=True)
class PersistenceReceipt:
    sequence: int
    key: str
    outcome: PersistenceOutcome
    error_code: str | None = None
    result: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("invalid persistence receipt sequence")
        _normalize_key(self.key)
        if type(self.outcome) is not PersistenceOutcome:
            raise ValueError("invalid persistence receipt outcome")
        if self.outcome is PersistenceOutcome.FAILED:
            if self.error_code != "operation_failed":
                raise ValueError("invalid persistence failure code")
        elif self.error_code is not None:
            raise ValueError("unexpected persistence error code")

    @property
    def succeeded(self) -> bool:
        return self.outcome is PersistenceOutcome.SUCCEEDED


@dataclass(frozen=True, slots=True)
class PersistenceWriterSnapshot:
    accepting: bool
    running: bool
    pending_count: int
    thread_alive: bool
    submitted: int
    started: int
    queued: int
    replaced_pending: int
    refused_full: int
    refused_closed: int
    reserved_drain_tail: int
    succeeded: int
    failed: int


@dataclass(frozen=True, slots=True)
class _PersistenceCommand:
    sequence: int
    key: str
    operation: Callable[[], object]
    receipt_handler: Callable[[PersistenceReceipt], None] | None


def _normalize_key(key: object) -> str:
    if (
        type(key) is not str
        or not key
        or key in {".", ".."}
        or key.startswith((".", "~"))
        or "/" in key
        or "\\" in key
        or "\x00" in key
        or len(key.encode("utf-8")) > MAX_PERSISTENCE_KEY_BYTES
        or _SAFE_KEY.fullmatch(key) is None
    ):
        raise ValueError("invalid persistence key")
    return key


def _bounded_increment(value: int, amount: int = 1) -> int:
    return min(MAX_PERSISTENCE_METRIC_COUNT, value + max(0, amount))


def _valid_timeout(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


class SerialPersistenceWriter:
    """One lazy FIFO worker that drains every accepted command on close."""

    def __init__(
        self,
        *,
        max_pending: int = MAX_PERSISTENCE_PENDING,
        receipt_handler: Callable[[PersistenceReceipt], None] | None = None,
        thread_name: str = "SidePulsePersistence",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            type(max_pending) is not int
            or max_pending <= 0
            or max_pending > MAX_PERSISTENCE_PENDING
        ):
            raise ValueError("invalid persistence pending bound")
        if receipt_handler is not None and not callable(receipt_handler):
            raise ValueError("invalid persistence receipt handler")
        if type(thread_name) is not str or not thread_name.strip():
            raise ValueError("invalid persistence thread name")
        if not callable(monotonic):
            raise ValueError("invalid persistence clock")
        self._max_pending = max_pending
        self._receipt_handler = receipt_handler
        self._thread_name = thread_name.strip()
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._pending: deque[_PersistenceCommand] = deque()
        self._running: _PersistenceCommand | None = None
        self._thread: threading.Thread | None = None
        self._accepting = True
        self._sequence = 0
        self._metrics = {
            "submitted": 0,
            "started": 0,
            "queued": 0,
            "replaced_pending": 0,
            "refused_full": 0,
            "refused_closed": 0,
            "reserved_drain_tail": 0,
            "succeeded": 0,
            "failed": 0,
        }

    def submit(
        self,
        key: str,
        operation: Callable[[], object],
        *,
        replace_pending: bool = False,
        use_reserved_drain_tail: bool = False,
        receipt_handler: Callable[[PersistenceReceipt], None] | None = None,
    ) -> PersistenceDisposition:
        normalized = _normalize_key(key)
        if not callable(operation):
            raise ValueError("invalid persistence operation")
        if type(replace_pending) is not bool:
            raise ValueError("invalid persistence replacement policy")
        if type(use_reserved_drain_tail) is not bool:
            raise ValueError("invalid persistence drain-tail policy")
        if receipt_handler is not None and not callable(receipt_handler):
            raise ValueError("invalid persistence receipt handler")

        displaced: _PersistenceCommand | None = None
        first_start = False
        with self._condition:
            self._increment("submitted")
            if not self._accepting:
                self._increment("refused_closed")
                return PersistenceDisposition.REFUSED_CLOSED
            if replace_pending:
                rows = list(self._pending)
                for index in range(len(rows) - 1, -1, -1):
                    if rows[index].key == normalized:
                        displaced = rows.pop(index)
                        self._pending = deque(rows)
                        break
            if displaced is None and len(self._pending) >= self._max_pending:
                if (
                    not use_reserved_drain_tail
                    or self._metrics["reserved_drain_tail"]
                ):
                    self._increment("refused_full")
                    return PersistenceDisposition.REFUSED_FULL
                # One process-lifetime overflow slot is reserved for a final
                # dirty snapshot submitted immediately before close. The
                # queue remains bounded at max_pending + 1.
                self._increment("reserved_drain_tail")
            self._sequence += 1
            command = _PersistenceCommand(
                self._sequence,
                normalized,
                operation,
                receipt_handler,
            )
            self._pending.append(command)
            if displaced is not None:
                self._increment("replaced_pending")
                disposition = PersistenceDisposition.REPLACED_PENDING
            else:
                first_start = self._thread is None
                disposition = (
                    PersistenceDisposition.STARTED
                    if first_start
                    else PersistenceDisposition.QUEUED
                )
                if not first_start:
                    self._increment("queued")
            if first_start:
                thread = threading.Thread(
                    target=self._run,
                    name=self._thread_name,
                    daemon=True,
                )
                self._thread = thread
                try:
                    thread.start()
                except Exception:
                    self._thread = None
                    self._pending.remove(command)
                    self._increment("refused_full")
                    raise
            self._condition.notify_all()

        if displaced is not None:
            self._publish(
                displaced,
                PersistenceReceipt(
                    displaced.sequence,
                    displaced.key,
                    PersistenceOutcome.REPLACED,
                ),
            )
        return disposition

    def wait_idle(self, *, timeout_seconds: float) -> bool:
        if not _valid_timeout(timeout_seconds):
            raise ValueError("invalid persistence wait timeout")
        deadline = self._monotonic() + float(timeout_seconds)
        with self._condition:
            while self._running is not None or self._pending:
                remaining = deadline - self._monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self, *, timeout_seconds: float) -> bool:
        if not _valid_timeout(timeout_seconds):
            raise ValueError("invalid persistence close timeout")
        with self._condition:
            self._accepting = False
            self._condition.notify_all()
            thread = self._thread
        if thread is None:
            return True
        if thread.ident == threading.get_ident():
            return False
        thread.join(float(timeout_seconds))
        return not thread.is_alive()

    def snapshot(self) -> PersistenceWriterSnapshot:
        with self._condition:
            thread = self._thread
            return PersistenceWriterSnapshot(
                accepting=self._accepting,
                running=self._running is not None,
                pending_count=len(self._pending),
                thread_alive=bool(thread is not None and thread.is_alive()),
                submitted=self._metrics["submitted"],
                started=self._metrics["started"],
                queued=self._metrics["queued"],
                replaced_pending=self._metrics["replaced_pending"],
                refused_full=self._metrics["refused_full"],
                refused_closed=self._metrics["refused_closed"],
                reserved_drain_tail=self._metrics["reserved_drain_tail"],
                succeeded=self._metrics["succeeded"],
                failed=self._metrics["failed"],
            )

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and self._accepting:
                    self._condition.wait()
                if not self._pending:
                    self._thread = None
                    self._condition.notify_all()
                    return
                command = self._pending.popleft()
                self._running = command
                self._increment("started")
            try:
                result = command.operation()
                receipt = PersistenceReceipt(
                    command.sequence,
                    command.key,
                    PersistenceOutcome.SUCCEEDED,
                    result=result,
                )
            except Exception:
                receipt = PersistenceReceipt(
                    command.sequence,
                    command.key,
                    PersistenceOutcome.FAILED,
                    error_code="operation_failed",
                )
            with self._condition:
                self._running = None
                self._increment(
                    "succeeded" if receipt.succeeded else "failed"
                )
                self._condition.notify_all()
            self._publish(command, receipt)

    def _publish(
        self,
        command: _PersistenceCommand,
        receipt: PersistenceReceipt,
    ) -> None:
        handlers = (command.receipt_handler, self._receipt_handler)
        delivered: set[int] = set()
        for handler in handlers:
            if handler is None or id(handler) in delivered:
                continue
            delivered.add(id(handler))
            try:
                handler(receipt)
            except Exception:
                continue

    def _increment(self, name: str) -> None:
        self._metrics[name] = _bounded_increment(self._metrics[name])


__all__ = [
    "MAX_PERSISTENCE_PENDING",
    "PersistenceDisposition",
    "PersistenceOutcome",
    "PersistenceReceipt",
    "PersistenceWriterSnapshot",
    "SerialPersistenceWriter",
]
