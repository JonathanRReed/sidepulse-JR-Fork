"""Bounded FIFO hook processing with guarded local admission and drain receipts."""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Final

from . import audit
from .hook_ingress_protocol import (
    MAX_HOOK_INGRESS_WIRE_BYTES,
    HookIngressDisposition,
    HookIngressRequest,
    decode_hook_ingress_request,
    default_hook_ingress_socket_path,
    encode_hook_ingress_response,
)
from .ipc import (
    SERVER_ACCEPT_TIMEOUT_SECONDS,
    ProviderRefreshHint,
    _bind_socket_in_guard,
    _existing_socket_refuses_connections,
    _identity,
    _same_uid_peer,
    _SocketPathGuard,
)
from .private_io import append_private_text, ensure_private_directory
from .state_paths import default_state_dir

MAX_HOOK_INGRESS_ACCEPTED: Final = 32
MAX_HOOK_INGRESS_METRIC_COUNT: Final = 10_000
HOOK_INGRESS_READ_TIMEOUT_SECONDS: Final = 0.25
HOOK_INGRESS_LISTEN_BACKLOG: Final = 32


class HookIngressOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUSED_FULL = "refused_full"
    REFUSED_CLOSED = "refused_closed"
    REFUSED_INVALID = "refused_invalid"
    REJECTED_SHUTDOWN_TIMEOUT = "rejected_shutdown_timeout"


@dataclass(frozen=True, slots=True)
class HookIngressReceipt:
    sequence: int
    outcome: HookIngressOutcome
    request: HookIngressRequest | None = field(default=None, repr=False, compare=False)
    error_code: str | None = None
    result: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("invalid hook ingress receipt sequence")
        if type(self.outcome) is not HookIngressOutcome:
            raise ValueError("invalid hook ingress receipt outcome")
        expected_error = {
            HookIngressOutcome.SUCCEEDED: None,
            HookIngressOutcome.FAILED: "processing_failed",
            HookIngressOutcome.REFUSED_FULL: "refused_full",
            HookIngressOutcome.REFUSED_CLOSED: "refused_closed",
            HookIngressOutcome.REFUSED_INVALID: "refused_invalid",
            HookIngressOutcome.REJECTED_SHUTDOWN_TIMEOUT: "shutdown_timeout",
        }[self.outcome]
        if self.error_code != expected_error:
            raise ValueError("invalid hook ingress receipt error code")
        if self.request is not None and type(self.request) is not HookIngressRequest:
            raise ValueError("invalid hook ingress receipt request")

    @property
    def provider(self) -> str | None:
        return None if self.request is None else self.request.provider


@dataclass(frozen=True, slots=True)
class HookIngressSnapshot:
    accepting: bool
    running: bool
    pending_count: int
    accepted_outstanding: int
    thread_alive: bool
    socket_running: bool
    submitted: int
    accepted: int
    refused_full: int
    refused_closed: int
    refused_invalid: int
    succeeded: int
    failed: int
    shutdown_timeout: int


@dataclass(frozen=True, slots=True)
class _AcceptedHook:
    sequence: int
    request: HookIngressRequest = field(repr=False)


@dataclass(frozen=True, slots=True)
class AppOwnedHookIngressProcessor:
    """Canonical hook processing with a synchronous app monitor refresh."""

    refresh_hint_handler: Callable[[ProviderRefreshHint], object] = field(repr=False)

    def __post_init__(self) -> None:
        if not callable(self.refresh_hint_handler):
            raise ValueError("invalid hook ingress refresh handler")

    def __call__(self, request: HookIngressRequest) -> object:
        from .hook import process_hook_payload

        return process_hook_payload(
            request.provider,
            Path(request.log_path),
            request.payload_text,
            refresh_hint_handler=self.refresh_hint_handler,
        )


def _bounded_increment(value: int, amount: int = 1) -> int:
    return min(MAX_HOOK_INGRESS_METRIC_COUNT, value + max(0, amount))


def _valid_timeout(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def default_hook_ingress_rejection_path() -> Path:
    return default_state_dir() / "hook-ingress-rejections.jsonl"


def _process_request(request: HookIngressRequest) -> object:
    from .hook import process_hook_payload

    return process_hook_payload(
        request.provider,
        Path(request.log_path),
        request.payload_text,
    )


class HookIngressService:
    """One guarded socket, one bounded admission FIFO, and one worker."""

    def __init__(
        self,
        *,
        process: Callable[[HookIngressRequest], object] = _process_request,
        maximum_accepted: int = MAX_HOOK_INGRESS_ACCEPTED,
        receipt_handler: Callable[[HookIngressReceipt], None] | None = None,
        rejection_recorder: Callable[[HookIngressReceipt], None] | None = None,
        rejection_path: Path | None = None,
        socket_path: Path | None = None,
        peer_uid_reader: Callable[[socket.socket], int] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(process):
            raise ValueError("invalid hook ingress processor")
        if (
            type(maximum_accepted) is not int
            or maximum_accepted <= 0
            or maximum_accepted > MAX_HOOK_INGRESS_ACCEPTED
        ):
            raise ValueError("invalid hook ingress bound")
        if receipt_handler is not None and not callable(receipt_handler):
            raise ValueError("invalid hook ingress receipt handler")
        if rejection_recorder is not None and not callable(rejection_recorder):
            raise ValueError("invalid hook ingress rejection recorder")
        if peer_uid_reader is not None and not callable(peer_uid_reader):
            raise ValueError("invalid hook ingress peer reader")
        if not callable(monotonic):
            raise ValueError("invalid hook ingress clock")
        self._process = process
        self._maximum_accepted = maximum_accepted
        self._receipt_handler = receipt_handler
        self._rejection_path = Path(
            rejection_path or default_hook_ingress_rejection_path()
        ).expanduser()
        self._rejection_recorder = rejection_recorder or self._record_rejection
        self.socket_path = Path(
            socket_path or default_hook_ingress_socket_path()
        ).expanduser()
        self._peer_uid_reader = peer_uid_reader
        self._monotonic = monotonic

        self._condition = threading.Condition()
        self._pending: deque[_AcceptedHook] = deque()
        self._running: _AcceptedHook | None = None
        self._worker: threading.Thread | None = None
        self._accepting = True
        self._sequence = 0
        self._timed_out_sequences: set[int] = set()
        self._metrics = {
            "submitted": 0,
            "accepted": 0,
            "refused_full": 0,
            "refused_closed": 0,
            "refused_invalid": 0,
            "succeeded": 0,
            "failed": 0,
            "shutdown_timeout": 0,
        }

        self._server_lock = threading.RLock()
        self._server_socket: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._server_running = False
        self._active_connection: socket.socket | None = None
        self._path_guard: _SocketPathGuard | None = None
        self._bound_identity: tuple[int, int] | None = None

    def start(self) -> Path:
        with self._condition:
            if not self._accepting:
                raise OSError("hook ingress is closed")
        with self._server_lock:
            if (
                self._server_running
                or self._server_socket is not None
                or self._path_guard is not None
                or (
                    self._server_thread is not None
                    and self._server_thread.is_alive()
                )
            ):
                raise OSError("hook ingress server is already running")
            ensure_private_directory(self.socket_path.parent)
            guard = _SocketPathGuard(self.socket_path)
            server: socket.socket | None = None
            bound_identity: tuple[int, int] | None = None
            try:
                existing = guard.leaf()
                if existing is not None:
                    expected = _identity(guard.socket_leaf())
                    if not _existing_socket_refuses_connections(guard, expected):
                        raise OSError(
                            f"refusing to replace live or unproven socket: {self.socket_path}"
                        )
                    guard.unlink_socket(expected)
                guard.assert_absent()
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                bound_identity = _bind_socket_in_guard(server, guard)
                guard.assert_parent()
                guard.chmod_socket(bound_identity, 0o600)
                server.listen(HOOK_INGRESS_LISTEN_BACKLOG)
                server.settimeout(SERVER_ACCEPT_TIMEOUT_SECONDS)
                guard.assert_socket_identity(bound_identity)
                self._path_guard = guard
                self._bound_identity = bound_identity
                self._server_socket = server
                self._server_running = True
                thread = threading.Thread(
                    target=self._serve,
                    name="JRBarHookIngressAccept",
                    daemon=True,
                )
                self._server_thread = thread
                thread.start()
                return self.socket_path
            except Exception:
                if server is not None:
                    server.close()
                if bound_identity is not None:
                    try:
                        guard.unlink_owned_socket(bound_identity)
                    except OSError:
                        pass
                guard.close()
                self._path_guard = None
                self._bound_identity = None
                self._server_socket = None
                self._server_running = False
                raise

    def submit(self, request: HookIngressRequest) -> HookIngressDisposition:
        if type(request) is not HookIngressRequest:
            self.refuse_invalid()
            return HookIngressDisposition.REFUSED_INVALID
        receipt: HookIngressReceipt | None = None
        with self._condition:
            self._sequence += 1
            sequence = self._sequence
            self._increment("submitted")
            if not self._accepting:
                self._increment("refused_closed")
                receipt = HookIngressReceipt(
                    sequence,
                    HookIngressOutcome.REFUSED_CLOSED,
                    request=request,
                    error_code="refused_closed",
                )
                disposition = HookIngressDisposition.REFUSED_CLOSED
            elif self._outstanding_locked() >= self._maximum_accepted:
                self._increment("refused_full")
                receipt = HookIngressReceipt(
                    sequence,
                    HookIngressOutcome.REFUSED_FULL,
                    request=request,
                    error_code="refused_full",
                )
                disposition = HookIngressDisposition.REFUSED_FULL
            else:
                command = _AcceptedHook(sequence, request)
                self._pending.append(command)
                self._increment("accepted")
                disposition = HookIngressDisposition.ACCEPTED
                if self._worker is None or not self._worker.is_alive():
                    thread = threading.Thread(
                        target=self._run,
                        name="JRBarHookIngressWorker",
                        daemon=True,
                    )
                    self._worker = thread
                    try:
                        thread.start()
                    except Exception:
                        self._worker = None
                        self._pending.remove(command)
                        self._accepting = False
                        self._increment("refused_closed")
                        receipt = HookIngressReceipt(
                            sequence,
                            HookIngressOutcome.REFUSED_CLOSED,
                            request=request,
                            error_code="refused_closed",
                        )
                        disposition = HookIngressDisposition.REFUSED_CLOSED
                self._condition.notify_all()
        if receipt is not None:
            self._publish(receipt)
        return disposition

    def refuse_invalid(self) -> HookIngressReceipt:
        with self._condition:
            self._sequence += 1
            sequence = self._sequence
            self._increment("submitted")
            self._increment("refused_invalid")
        receipt = HookIngressReceipt(
            sequence,
            HookIngressOutcome.REFUSED_INVALID,
            error_code="refused_invalid",
        )
        self._publish(receipt)
        return receipt

    def wait_idle(self, *, timeout_seconds: float) -> bool:
        deadline = self._deadline(timeout_seconds)
        with self._condition:
            while self._running is not None or self._pending:
                remaining = deadline - self._now()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def wait_stopped(self, *, timeout_seconds: float) -> bool:
        deadline = self._deadline(timeout_seconds)
        with self._condition:
            while self._worker is not None and self._worker.is_alive():
                remaining = deadline - self._now()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
        with self._server_lock:
            thread = self._server_thread
        if thread is not None and thread.is_alive():
            remaining = deadline - self._now()
            if remaining <= 0.0:
                return False
            thread.join(remaining)
        return not (thread is not None and thread.is_alive())

    def close(self, *, timeout_seconds: float) -> bool:
        deadline = self._deadline(timeout_seconds)
        with self._condition:
            self._accepting = False
            self._condition.notify_all()
            worker = self._worker
        current = threading.current_thread()
        if worker is current:
            drained = False
        else:
            if worker is not None:
                worker.join(max(0.0, deadline - self._now()))
            drained = worker is None or not worker.is_alive()

        timeout_receipts: list[HookIngressReceipt] = []
        if not drained:
            with self._condition:
                unfinished = tuple(
                    command
                    for command in (
                        *((self._running,) if self._running is not None else ()),
                        *self._pending,
                    )
                    if command.sequence not in self._timed_out_sequences
                )
                self._pending.clear()
                for command in unfinished:
                    self._timed_out_sequences.add(command.sequence)
                    self._increment("shutdown_timeout")
                    timeout_receipts.append(
                        HookIngressReceipt(
                            command.sequence,
                            HookIngressOutcome.REJECTED_SHUTDOWN_TIMEOUT,
                            request=command.request,
                            error_code="shutdown_timeout",
                        )
                    )
                self._condition.notify_all()
        for receipt in timeout_receipts:
            self._publish(receipt)

        socket_stopped = self._stop_server(deadline)
        return drained and socket_stopped

    def snapshot(self) -> HookIngressSnapshot:
        with self._condition:
            worker = self._worker
            outstanding = self._outstanding_locked()
            snapshot_values = {
                name: self._metrics[name]
                for name in (
                    "submitted",
                    "accepted",
                    "refused_full",
                    "refused_closed",
                    "refused_invalid",
                    "succeeded",
                    "failed",
                    "shutdown_timeout",
                )
            }
            accepting = self._accepting
            running = self._running is not None
            pending_count = len(self._pending)
            thread_alive = bool(worker is not None and worker.is_alive())
        with self._server_lock:
            socket_running = self._server_running
        return HookIngressSnapshot(
            accepting=accepting,
            running=running,
            pending_count=pending_count,
            accepted_outstanding=outstanding,
            thread_alive=thread_alive,
            socket_running=socket_running,
            **snapshot_values,
        )

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and self._accepting:
                    self._condition.wait()
                if not self._pending:
                    self._worker = None
                    self._condition.notify_all()
                    return
                command = self._pending.popleft()
                self._running = command
            try:
                result = self._process(command.request)
                receipt = HookIngressReceipt(
                    command.sequence,
                    HookIngressOutcome.SUCCEEDED,
                    request=command.request,
                    result=result,
                )
            except Exception:
                receipt = HookIngressReceipt(
                    command.sequence,
                    HookIngressOutcome.FAILED,
                    request=command.request,
                    error_code="processing_failed",
                )
            with self._condition:
                self._running = None
                timed_out = command.sequence in self._timed_out_sequences
                if not timed_out:
                    self._increment(
                        "succeeded"
                        if receipt.outcome is HookIngressOutcome.SUCCEEDED
                        else "failed"
                    )
                self._condition.notify_all()
            if not timed_out:
                self._publish(receipt)

    def _serve(self) -> None:
        while True:
            with self._server_lock:
                server = self._server_socket
                if not self._server_running or server is None:
                    return
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._server_lock:
                if not self._server_running or self._server_socket is not server:
                    connection.close()
                    return
                self._active_connection = connection
            try:
                with connection:
                    self._handle_connection(connection)
            finally:
                with self._server_lock:
                    if self._active_connection is connection:
                        self._active_connection = None

    def _handle_connection(self, connection: socket.socket) -> None:
        if not _same_uid_peer(connection, self._peer_uid_reader):
            return
        try:
            connection.settimeout(HOOK_INGRESS_READ_TIMEOUT_SECONDS)
        except OSError:
            return
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = connection.recv(65536)
            except (TimeoutError, OSError):
                return
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_HOOK_INGRESS_WIRE_BYTES:
                self.refuse_invalid()
                self._send_response(
                    connection,
                    HookIngressDisposition.REFUSED_INVALID,
                )
                return
            chunks.append(chunk)
        request = decode_hook_ingress_request(b"".join(chunks))
        if request is None:
            self.refuse_invalid()
            disposition = HookIngressDisposition.REFUSED_INVALID
        else:
            disposition = self.submit(request)
        self._send_response(connection, disposition)

    @staticmethod
    def _send_response(
        connection: socket.socket,
        disposition: HookIngressDisposition,
    ) -> None:
        try:
            connection.sendall(encode_hook_ingress_response(disposition))
        except OSError:
            pass

    def _stop_server(self, deadline: float) -> bool:
        with self._server_lock:
            self._server_running = False
            server = self._server_socket
            self._server_socket = None
            connection = self._active_connection
            thread = self._server_thread
            if server is not None:
                try:
                    server.close()
                except OSError:
                    pass
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    connection.close()
                except OSError:
                    pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - self._now()))
        stopped = thread is None or not thread.is_alive()
        with self._server_lock:
            guard = self._path_guard
            expected = self._bound_identity
            self._path_guard = None
            self._bound_identity = None
            if stopped:
                self._server_thread = None
            if guard is not None:
                if expected is not None:
                    try:
                        guard.unlink_owned_socket(expected)
                    except OSError:
                        pass
                guard.close()
        return stopped

    def _publish(self, receipt: HookIngressReceipt) -> None:
        handlers: tuple[Callable[[HookIngressReceipt], None] | None, ...]
        if receipt.outcome is HookIngressOutcome.SUCCEEDED:
            handlers = (self._receipt_handler,)
        else:
            handlers = (self._receipt_handler, self._rejection_recorder)
        delivered: list[Callable[[HookIngressReceipt], None]] = []
        for handler in handlers:
            if handler is None or handler in delivered:
                continue
            delivered.append(handler)
            try:
                handler(receipt)
            except Exception:
                continue

    def _record_rejection(self, receipt: HookIngressReceipt) -> None:
        document = {
            "version": 1,
            "recorded_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "sequence": receipt.sequence,
            "provider": receipt.provider,
            "reason": receipt.outcome.value,
        }
        append_private_text(
            self._rejection_path,
            json.dumps(document, separators=(",", ":"), sort_keys=True) + "\n",
        )
        audit.compact_jsonl_file(self._rejection_path)

    def _outstanding_locked(self) -> int:
        running = int(
            self._running is not None
            and self._running.sequence not in self._timed_out_sequences
        )
        return running + sum(
            command.sequence not in self._timed_out_sequences
            for command in self._pending
        )

    def _increment(self, name: str, amount: int = 1) -> None:
        self._metrics[name] = _bounded_increment(self._metrics[name], amount)

    def _now(self) -> float:
        value = self._monotonic()
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise RuntimeError("hook ingress clock returned an invalid value")
        return float(value)

    def _deadline(self, timeout_seconds: float) -> float:
        if not _valid_timeout(timeout_seconds):
            raise ValueError("invalid hook ingress timeout")
        return self._now() + float(timeout_seconds)


__all__ = [
    "HOOK_INGRESS_READ_TIMEOUT_SECONDS",
    "MAX_HOOK_INGRESS_ACCEPTED",
    "HookIngressOutcome",
    "HookIngressReceipt",
    "HookIngressService",
    "HookIngressSnapshot",
    "default_hook_ingress_rejection_path",
]
