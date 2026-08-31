"""Strict, content-bounded wire protocol for ordered hook admission."""

from __future__ import annotations

import json
import math
import os
import socket
import stat
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

from .state_paths import candidate_state_dirs, default_state_dir

HOOK_INGRESS_SOCKET_NAME: Final = "hook-ingress.sock"
HOOK_INGRESS_PROTOCOL_VERSION: Final = 1
HOOK_INGRESS_SEND_TIMEOUT_SECONDS: Final = 0.03
MAX_HOOK_INGRESS_PAYLOAD_BYTES: Final = 1024 * 1024
MAX_HOOK_INGRESS_HEADER_BYTES: Final = 8 * 1024
MAX_HOOK_INGRESS_WIRE_BYTES: Final = (
    MAX_HOOK_INGRESS_PAYLOAD_BYTES + MAX_HOOK_INGRESS_HEADER_BYTES + 32
)
MAX_HOOK_INGRESS_RESPONSE_BYTES: Final = 64
MAX_HOOK_LOG_PATH_BYTES: Final = 4096

_MAGIC: Final = b"JRBARHOOK\x01"
_LENGTHS = struct.Struct("!II")
_HEADER_FIELDS: Final = frozenset({"version", "provider", "log_path"})
_HOOK_PROVIDERS: Final = frozenset(
    {
        "antigravity",
        "claude",
        "codex",
        "cursor",
        "devin",
        "grok",
        "hermes",
        "kiro",
        "openclaw",
        "opencode",
    }
)


class HookIngressDisposition(str, Enum):
    ACCEPTED = "accepted"
    REFUSED_FULL = "refused_full"
    REFUSED_CLOSED = "refused_closed"
    REFUSED_INVALID = "refused_invalid"
    SUBMISSION_AMBIGUOUS = "submission_ambiguous"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class HookIngressRequest:
    provider: str
    log_path: str
    payload_text: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.provider) is not str
            or self.provider not in _HOOK_PROVIDERS
            or type(self.log_path) is not str
            or not self.log_path
            or not Path(self.log_path).is_absolute()
            or len(self.log_path.encode("utf-8")) > MAX_HOOK_LOG_PATH_BYTES
            or any(ord(character) < 32 for character in self.log_path)
            or type(self.payload_text) is not str
        ):
            raise ValueError("invalid hook ingress request")
        try:
            payload_size = len(self.payload_text.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("invalid hook ingress request") from exc
        if payload_size > MAX_HOOK_INGRESS_PAYLOAD_BYTES:
            raise ValueError("invalid hook ingress request")


def default_hook_ingress_socket_path() -> Path:
    return default_state_dir() / HOOK_INGRESS_SOCKET_NAME


def candidate_hook_ingress_socket_paths() -> tuple[Path, ...]:
    return tuple(path / HOOK_INGRESS_SOCKET_NAME for path in candidate_state_dirs())


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("invalid hook ingress header")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("invalid hook ingress header")


def encode_hook_ingress_request(request: HookIngressRequest) -> bytes:
    if type(request) is not HookIngressRequest:
        raise ValueError("invalid hook ingress request")
    header = json.dumps(
        {
            "version": HOOK_INGRESS_PROTOCOL_VERSION,
            "provider": request.provider,
            "log_path": request.log_path,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload = request.payload_text.encode("utf-8")
    if len(header) > MAX_HOOK_INGRESS_HEADER_BYTES:
        raise ValueError("invalid hook ingress request")
    encoded = _MAGIC + _LENGTHS.pack(len(header), len(payload)) + header + payload
    if len(encoded) > MAX_HOOK_INGRESS_WIRE_BYTES:
        raise ValueError("invalid hook ingress request")
    return encoded


def decode_hook_ingress_request(payload: bytes) -> HookIngressRequest | None:
    if type(payload) is not bytes or not payload.startswith(_MAGIC):
        return None
    lengths_at = len(_MAGIC)
    header_at = lengths_at + _LENGTHS.size
    if len(payload) < header_at:
        return None
    try:
        header_size, body_size = _LENGTHS.unpack(payload[lengths_at:header_at])
    except struct.error:
        return None
    if (
        header_size <= 0
        or header_size > MAX_HOOK_INGRESS_HEADER_BYTES
        or body_size > MAX_HOOK_INGRESS_PAYLOAD_BYTES
        or len(payload) != header_at + header_size + body_size
        or len(payload) > MAX_HOOK_INGRESS_WIRE_BYTES
    ):
        return None
    header_end = header_at + header_size
    try:
        document = json.loads(
            payload[header_at:header_end].decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        body = payload[header_end:].decode("utf-8")
    except (TypeError, UnicodeError, ValueError):
        return None
    if (
        type(document) is not dict
        or frozenset(document) != _HEADER_FIELDS
        or type(document["version"]) is not int
        or document["version"] != HOOK_INGRESS_PROTOCOL_VERSION
        or type(document["provider"]) is not str
        or type(document["log_path"]) is not str
    ):
        return None
    try:
        return HookIngressRequest(document["provider"], document["log_path"], body)
    except ValueError:
        return None


def encode_hook_ingress_response(disposition: HookIngressDisposition) -> bytes:
    if disposition not in {
        HookIngressDisposition.ACCEPTED,
        HookIngressDisposition.REFUSED_FULL,
        HookIngressDisposition.REFUSED_CLOSED,
        HookIngressDisposition.REFUSED_INVALID,
    }:
        raise ValueError("invalid hook ingress response")
    return f"{disposition.value}\n".encode("ascii")


def decode_hook_ingress_response(payload: bytes) -> HookIngressDisposition:
    if type(payload) is not bytes or len(payload) > MAX_HOOK_INGRESS_RESPONSE_BYTES:
        return HookIngressDisposition.UNAVAILABLE
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        return HookIngressDisposition.UNAVAILABLE
    for disposition in (
        HookIngressDisposition.ACCEPTED,
        HookIngressDisposition.REFUSED_FULL,
        HookIngressDisposition.REFUSED_CLOSED,
        HookIngressDisposition.REFUSED_INVALID,
    ):
        if text == f"{disposition.value}\n":
            return disposition
    return HookIngressDisposition.UNAVAILABLE


def _valid_timeout(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0.0 < float(value) <= 1.0
    )


def _trusted_socket_leaf(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return bool(
        stat.S_ISSOCK(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_nlink == 1
    )


def submit_hook_ingress(
    request: HookIngressRequest,
    *,
    socket_path: Path | None = None,
    timeout_seconds: float = HOOK_INGRESS_SEND_TIMEOUT_SECONDS,
    socket_factory: Callable[..., socket.socket] = socket.socket,
    require_socket_leaf: bool = True,
) -> HookIngressDisposition:
    encoded = encode_hook_ingress_request(request)
    if not _valid_timeout(timeout_seconds):
        raise ValueError("invalid hook ingress timeout")
    if not callable(socket_factory) or type(require_socket_leaf) is not bool:
        raise ValueError("invalid hook ingress dependency")
    targets = (
        (Path(socket_path).expanduser(),)
        if socket_path is not None
        else candidate_hook_ingress_socket_paths()
    )
    for target in targets:
        if require_socket_leaf and not _trusted_socket_leaf(target):
            continue
        client = None
        connected = False
        try:
            client = socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(float(timeout_seconds))
            client.connect(str(target))
            connected = True
            client.sendall(encoded)
            client.shutdown(socket.SHUT_WR)
            response = client.recv(MAX_HOOK_INGRESS_RESPONSE_BYTES + 1)
            disposition = decode_hook_ingress_response(response)
            if disposition is not HookIngressDisposition.UNAVAILABLE:
                return disposition
            return HookIngressDisposition.SUBMISSION_AMBIGUOUS
        except Exception:
            if connected:
                return HookIngressDisposition.SUBMISSION_AMBIGUOUS
            continue
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
    return HookIngressDisposition.UNAVAILABLE


__all__ = [
    "HOOK_INGRESS_PROTOCOL_VERSION",
    "HOOK_INGRESS_SEND_TIMEOUT_SECONDS",
    "HOOK_INGRESS_SOCKET_NAME",
    "MAX_HOOK_INGRESS_PAYLOAD_BYTES",
    "MAX_HOOK_INGRESS_RESPONSE_BYTES",
    "MAX_HOOK_INGRESS_WIRE_BYTES",
    "HookIngressDisposition",
    "HookIngressRequest",
    "candidate_hook_ingress_socket_paths",
    "decode_hook_ingress_request",
    "decode_hook_ingress_response",
    "default_hook_ingress_socket_path",
    "encode_hook_ingress_request",
    "encode_hook_ingress_response",
    "submit_hook_ingress",
]
