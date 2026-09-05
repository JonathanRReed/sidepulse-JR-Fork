"""Clean-room, provider-neutral Creator Micro 2 vendor-HID boundary."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class SemanticState(StrEnum):
    INPUT_REQUIRED = "input_required"
    FAILURE = "failure"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RESET = "reset"
    ACTIVE = "active"
    COMPLETED = "completed"
    QUOTA_WARNING = "quota_warning"
    IDLE = "idle"

    @property
    def priority(self) -> int:
        return _SEMANTIC_PRIORITY[self]


_SEMANTIC_PRIORITY = {
    SemanticState.INPUT_REQUIRED: 800,
    SemanticState.FAILURE: 700,
    SemanticState.QUOTA_EXHAUSTED: 600,
    SemanticState.RESET: 500,
    SemanticState.ACTIVE: 400,
    SemanticState.COMPLETED: 300,
    SemanticState.QUOTA_WARNING: 200,
    SemanticState.IDLE: 100,
}


@dataclass(frozen=True)
class Receipt:
    code: str
    detail: str = ""
    recoverable: bool = True


class DeviceTransport(Protocol):
    def open(self, *, nonexclusive: bool = True) -> None: ...
    def write(self, report: bytes) -> None: ...
    def read(self, *, timeout_ms: int) -> bytes | None: ...
    def close(self) -> None: ...


class NoDeviceError(OSError):
    """The optional backend is available, but no matching collection exists."""


@dataclass(frozen=True)
class DeviceCapability:
    methods: frozenset[str] = frozenset()

    @classmethod
    def from_methods(cls, methods: list[str] | set[str] | frozenset[str]) -> DeviceCapability:
        return cls(frozenset(methods))

    @property
    def can_light(self) -> bool:
        return "lights.preview" in self.methods

    @property
    def can_agent_status(self) -> bool:
        return "v.oai.thstatus" in self.methods


@dataclass
class DeviceConflict:
    issued_ids: set[int] = field(default_factory=set)
    active: bool = False

    def observe(self, response_id: object) -> str | None:
        if type(response_id) is not int or response_id not in self.issued_ids:
            self.active = True
            return "foreign_response_id"
        self.issued_ids.remove(response_id)
        return None


class CreatorMicro2Framer:
    REPORT_ID = 6
    RPC_CHANNEL = 2
    DEBUG_CHANNEL = 1
    REPORT_SIZE = 64
    CHUNK_SIZE = 61
    VENDOR_ID = 0x303A
    PRODUCT_IDS = frozenset((0x8297, 0x8298))
    USAGE_PAGE = 0xFF00
    USAGE = 1
    MAX_REPORTS = 64
    MAX_SETUP_BYTES = 132_096  # Two JSON encodings of a 64 KiB keymap, plus envelope.

    @classmethod
    def bounded_budget(cls, value: int) -> int:
        if type(value) is not int or not 1 <= value <= cls.MAX_SETUP_BYTES:
            raise ValueError("invalid JSON-RPC byte budget")
        return value

    @staticmethod
    def _valid_id(value: object) -> bool:
        return type(value) is int and 0 <= value < 1000

    @classmethod
    def validate_request(cls, value: Any) -> None:
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise ValueError("invalid JSON-RPC 2.0 request")
        if not isinstance(value.get("method"), str) or not value["method"] or not cls._valid_id(value.get("id")):
            raise ValueError("invalid JSON-RPC 2.0 request")
        if set(value) - {"jsonrpc", "method", "params", "id"}:
            raise ValueError("invalid JSON-RPC 2.0 request")

    @classmethod
    def validate_incoming(cls, value: Any) -> None:
        if not isinstance(value, dict) or value.get("jsonrpc", "2.0") != "2.0":
            raise ValueError("invalid JSON-RPC 2.0 envelope")
        if "id" in value:
            if not cls._valid_id(value["id"]):
                raise ValueError("invalid JSON-RPC response id")
            keys = set(value) - {"jsonrpc"}
            conventional = keys in ({"id", "result"}, {"id", "error"})
            firmware = keys in (
                {"id", "method", "params"},
                {"id", "method", "result"},
            ) and isinstance(value.get("method"), str)
            if not conventional and not firmware:
                raise ValueError("invalid JSON-RPC response")
            if "error" in value and not isinstance(value["error"], dict):
                raise ValueError("invalid JSON-RPC error")
            return
        keys = set(value) - {"jsonrpc"}
        abbreviated = keys == {"m", "p"} and isinstance(value.get("m"), str)
        standard = keys == {"method", "params"} and isinstance(value.get("method"), str)
        if not (abbreviated or standard):
            raise ValueError("invalid JSON-RPC notification")

    @classmethod
    def _encode(cls, message: dict[str, Any], *, max_bytes: int | None = None) -> list[bytes]:
        budget = cls.bounded_budget(cls.MAX_REPORTS * cls.CHUNK_SIZE if max_bytes is None else max_bytes)
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if not payload or len(payload) > budget:
            raise ValueError("payload exceeds report limit")
        parts = [payload[offset : offset + cls.CHUNK_SIZE] for offset in range(0, len(payload), cls.CHUNK_SIZE)]
        return [
            bytes((cls.REPORT_ID, cls.RPC_CHANNEL, len(part))) + part.ljust(cls.CHUNK_SIZE, b"\0") for part in parts
        ]

    @classmethod
    def encode_request(cls, message: dict[str, Any], *, max_bytes: int | None = None) -> list[bytes]:
        cls.validate_request(message)
        return cls._encode(message, max_bytes=max_bytes)

    @classmethod
    def encode_message(cls, message: dict[str, Any]) -> list[bytes]:
        cls.validate_incoming(message)
        return cls._encode(message)

    encode = encode_request

    @classmethod
    def decode(cls, reports: bytes) -> dict[str, Any]:
        decoder = RpcStreamDecoder()
        messages: list[dict[str, Any]] = []
        if not reports or len(reports) % cls.REPORT_SIZE:
            raise ValueError("incomplete HID report")
        for offset in range(0, len(reports), cls.REPORT_SIZE):
            messages.extend(decoder.feed(reports[offset : offset + cls.REPORT_SIZE]))
        if len(messages) != 1 or decoder.pending_bytes:
            raise ValueError("incomplete JSON-RPC message")
        return messages[0]

    @classmethod
    def discover(cls, info: dict[str, Any]) -> bool:
        return (
            info.get("vendor_id") == cls.VENDOR_ID
            and info.get("product_id") in cls.PRODUCT_IDS
            and info.get("usage_page") == cls.USAGE_PAGE
            and info.get("usage") == cls.USAGE
        )


class RpcStreamDecoder:
    """Reassemble the unnumbered HID fragment stream into complete JSON objects."""

    MAX_BUFFER = CreatorMicro2Framer.MAX_REPORTS * CreatorMicro2Framer.CHUNK_SIZE

    def __init__(self, *, max_bytes: int = MAX_BUFFER) -> None:
        self.max_bytes = CreatorMicro2Framer.bounded_budget(max_bytes)
        self._buffer = bytearray()
        self._reset_scan()

    def _reset_scan(self) -> None:
        self._scan = 0
        self._start = None
        self._depth = 0
        self._in_string = False
        self._escaped = False

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, report: bytes) -> list[dict[str, Any]]:
        try:
            fragment = self._fragment(report)
            if fragment is None:
                return []
            self._buffer.extend(fragment)
            if len(self._buffer) > self.max_bytes:
                raise ValueError("JSON-RPC fragment buffer limit exceeded")
            return self._drain()
        except ValueError:
            self._buffer.clear()
            self._reset_scan()
            raise

    @staticmethod
    def _fragment(report: bytes) -> bytes | None:
        if len(report) == CreatorMicro2Framer.REPORT_SIZE:
            if report[0] != CreatorMicro2Framer.REPORT_ID:
                raise ValueError("unexpected HID report id")
            channel, length, start = report[1], report[2], 3
        elif len(report) == CreatorMicro2Framer.REPORT_SIZE - 1:
            channel, length, start = report[0], report[1], 2
        else:
            raise ValueError("incomplete HID report")
        if channel not in (CreatorMicro2Framer.DEBUG_CHANNEL, CreatorMicro2Framer.RPC_CHANNEL):
            raise ValueError("unexpected HID channel")
        if length > CreatorMicro2Framer.CHUNK_SIZE or start + length > len(report):
            raise ValueError("invalid HID report length")
        if channel == CreatorMicro2Framer.DEBUG_CHANNEL:
            return None
        return report[start : start + length]

    def _drain(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        consumed = 0
        # Resume at the next byte. Rescanning every earlier fragment makes a
        # full keymap quadratic even though the wire arrives 61 bytes at a time.
        for index in range(self._scan, len(self._buffer)):
            byte = self._buffer[index]
            char = chr(byte)
            if self._start is None:
                if char.isspace():
                    consumed = index + 1
                    continue
                if char != "{":
                    raise ValueError("invalid JSON-RPC payload")
                self._start, self._depth = index, 1
                continue
            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif char == "\\":
                    self._escaped = True
                elif char == '"':
                    self._in_string = False
            elif char == '"':
                self._in_string = True
            elif char == "{":
                self._depth += 1
            elif char == "}":
                self._depth -= 1
                if self._depth == 0:
                    raw = bytes(self._buffer[self._start : index + 1])
                    try:
                        value = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
                        raise ValueError("invalid JSON-RPC payload") from exc
                    CreatorMicro2Framer.validate_incoming(value)
                    messages.append(value)
                    consumed, self._start = index + 1, None
        self._scan = len(self._buffer) - consumed
        if consumed:
            del self._buffer[:consumed]
            if self._start is not None:
                self._start -= consumed
        return messages


class CreatorMicro2Adapter:
    PROBE_METHODS = ("v.oai.thstatus", "lights.preview")

    def __init__(
        self,
        transport: DeviceTransport,
        info: dict[str, Any],
        *,
        capabilities: DeviceCapability | None = None,
        clock: Callable[[], float] = time.monotonic,
        rpc_timeout_ms: int = 8_000,
        reconnect_backoff_s: float = 1.0,
        rpc_max_bytes: int = RpcStreamDecoder.MAX_BUFFER,
    ) -> None:
        self.transport = transport
        self.info = info
        self._capabilities = capabilities or DeviceCapability()
        self._clock = clock
        self._rpc_timeout_ms = max(1, min(rpc_timeout_ms, 8_000))
        self._reconnect_backoff_s = max(0.0, reconnect_backoff_s)
        self._reconnect_at = 0.0
        self._next_id = 1
        self._rpc_max_bytes = CreatorMicro2Framer.bounded_budget(rpc_max_bytes)
        self._decoder = RpcStreamDecoder(max_bytes=self._rpc_max_bytes)
        self._notifications: deque[tuple[float, dict[str, Any]]] = deque(maxlen=128)
        self.conflict = DeviceConflict()
        self.connected = False

    def discover(self) -> bool:
        return CreatorMicro2Framer.discover(self.info)

    def connect(self) -> Receipt:
        if not self.discover():
            return Receipt("no_device", "Creator Micro 2 vendor collection not found")
        if self._clock() < self._reconnect_at:
            return Receipt("backoff", "waiting before reconnect")
        if self.connected:
            return Receipt("connected")
        try:
            self.transport.open(nonexclusive=True)
        except NoDeviceError:
            return Receipt("no_device", "Creator Micro 2 vendor collection not found")
        except PermissionError:
            return Receipt("permission_denied", "HID access requires Input Monitoring permission")
        except (ImportError, OSError) as exc:
            return Receipt("transport_unavailable", str(exc))
        self.connected = True
        self._decoder = RpcStreamDecoder(max_bytes=self._rpc_max_bytes)
        return Receipt("connected")

    def capabilities(self) -> DeviceCapability:
        return self._capabilities

    def negotiate_capabilities(self) -> Receipt:
        if not self.connected:
            return Receipt("not_connected")
        supported: set[str] = set()
        for method in self.PROBE_METHODS:
            params: list[Any] | dict[str, Any] = [] if method == "v.oai.thstatus" else {}
            receipt, response = self._call(method, params)
            if receipt.code == "device_conflict":
                return receipt
            if receipt.code not in {"applied", "rpc_error"}:
                return receipt
            error = response.get("error") if response else None
            if not (isinstance(error, dict) and error.get("code") == -32601):
                supported.add(method)
        self._capabilities = DeviceCapability.from_methods(supported)
        return Receipt("capabilities_negotiated", ",".join(sorted(supported)))

    def apply(self, state: SemanticState, params: list[Any] | dict[str, Any] | None = None) -> Receipt:
        if not self.connected:
            return Receipt("not_connected")
        if self.conflict.active:
            return Receipt("device_conflict", "another client controls the device", recoverable=False)
        method = "v.oai.thstatus"
        if method not in self._capabilities.methods:
            return Receipt("unsupported_method", method)
        if params is None:
            from .creator_micro_lighting import creator_micro_light_params

            params = creator_micro_light_params(state.value)
        receipt, _ = self._call(method, params)
        return receipt

    def _call(self, method: str, params: list[Any] | dict[str, Any] | None) -> tuple[Receipt, dict[str, Any] | None]:
        ident = self._next_id
        self._next_id = 0 if ident == 999 else ident + 1
        self.conflict.issued_ids.add(ident)
        request = {"jsonrpc": "2.0", "method": method, "params": params, "id": ident}
        try:
            for report in CreatorMicro2Framer.encode_request(request, max_bytes=self._rpc_max_bytes):
                self.transport.write(report)
        except ValueError:
            self.conflict.issued_ids.discard(ident)
            return Receipt("request_too_large", "request exceeds the bounded RPC budget"), None
        except PermissionError as exc:
            self.conflict.issued_ids.discard(ident)
            return Receipt("write_opt_in_required", str(exc)), None
        except OSError as exc:
            self.conflict.issued_ids.discard(ident)
            self._disconnect_for_retry()
            return Receipt("transport_unavailable", str(exc)), None

        deadline = self._clock() + self._rpc_timeout_ms / 1000
        while self._clock() <= deadline:
            remaining = max(0, min(self._rpc_timeout_ms, int((deadline - self._clock()) * 1000)))
            try:
                report = self.transport.read(timeout_ms=remaining)
            except OSError as exc:
                self.conflict.issued_ids.discard(ident)
                self._disconnect_for_retry()
                return Receipt("transport_unavailable", str(exc)), None
            if not report:
                break
            try:
                messages = self._decoder.feed(report)
            except ValueError as exc:
                self.conflict.issued_ids.discard(ident)
                self._disconnect_for_retry()
                return Receipt("malformed_report", str(exc)), None
            response = None
            for message in messages:
                if "id" not in message:
                    self._queue_notification(message)
                    continue
                if self.conflict.observe(message["id"]):
                    return Receipt("device_conflict", "foreign response id", recoverable=False), message
                if message["id"] != ident:
                    return Receipt("device_conflict", "response id race", recoverable=False), message
                response = message
            if response is not None:
                if "method" in response and response["method"] != method:
                    self._disconnect_for_retry()
                    return Receipt("malformed_report", "response method does not match request"), None
                if "error" in response:
                    return Receipt("rpc_error", str(response["error"])), response
                return Receipt("applied"), response
        self.conflict.issued_ids.discard(ident)
        self._disconnect_for_retry()
        return Receipt("timeout", f"timeout waiting for {method}"), None

    def _queue_notification(self, message: dict[str, Any]) -> None:
        method = message.get("m", message.get("method"))
        params = message.get("p", message.get("params"))
        self._notifications.append((self._clock(), {"method": method, "params": params}))

    def poll_inputs(self) -> list[dict[str, Any]]:
        if not self.connected or self.conflict.active:
            self._notifications.clear()
            return []
        if self.connected:
            for _ in range(64):
                try:
                    report = self.transport.read(timeout_ms=0)
                except OSError:
                    self._disconnect_for_retry()
                    break
                if not report:
                    break
                try:
                    messages = self._decoder.feed(report)
                except ValueError:
                    continue
                for message in messages:
                    if "id" in message:
                        self.conflict.observe(message["id"])
                    else:
                        self._queue_notification(message)
                if self.conflict.active:
                    break
        output = (
            [note for received, note in self._notifications if 0 <= self._clock() - received <= 0.5]
            if self.connected and not self.conflict.active else []
        )
        self._notifications.clear()
        return output

    def _disconnect_for_retry(self) -> None:
        self._notifications.clear()
        if self.connected:
            try:
                self.transport.close()
            finally:
                self.connected = False
        self._reconnect_at = self._clock() + self._reconnect_backoff_s

    def close(self) -> None:
        self._notifications.clear()
        if self.connected:
            try:
                self.transport.close()
            finally:
                self.connected = False
        self.conflict.issued_ids.clear()
