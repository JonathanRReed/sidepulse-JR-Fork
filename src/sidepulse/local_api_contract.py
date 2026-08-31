"""Safe, versioned contract for local SidePulse integrations.

This module deliberately describes messages, rather than opening a socket or
executing an action. Consumers may use the contract over an explicitly
configured local transport. Every request is bounded and supports HMAC
authentication with a short validity window. Transport adapters decide whether
authentication is mandatory, and SidePulse's serve adapter always requires it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any

CONTRACT_VERSION = 1
MAX_CLIENT_ID = 96
MAX_NONCE = 128
MAX_PAYLOAD_BYTES = 16_384
MAX_RESPONSE_BYTES = 64_000
DEFAULT_TTL_SECONDS = 30.0
MAX_REPLAY_ENTRIES = 4_096

CAPABILITIES = frozenset({"status.read", "usage.read", "agents.read"})
READ_ONLY_CAPABILITIES = CAPABILITIES
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
_AUTH_TAG = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_FIELDS = frozenset(
    {
        "version",
        "client_id",
        "capability",
        "nonce",
        "issued_at",
        "expires_at",
        "payload",
        "auth",
    }
)


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()


def _bounded_text(value: object, limit: int) -> bool:
    return (
        type(value) is str
        and bool(value)
        and len(value) <= limit
        and _TOKEN.fullmatch(value) is not None
    )


def _finite_timestamp(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _secret_bytes(secret: object) -> bytes:
    if type(secret) is not bytes or not secret:
        raise ValueError("invalid local API authentication secret")
    return secret


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("invalid local API request fields")
        document[key] = value
    return document


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid local API request")


@dataclass(frozen=True)
class LocalAPIRequest:
    """A bounded request.  ``payload`` is intentionally metadata only."""

    client_id: str
    capability: str
    nonce: str
    issued_at: float
    expires_at: float
    payload: Mapping[str, Any] | None = None
    auth: str | None = None
    version: int = CONTRACT_VERSION

    def unsigned_document(self) -> dict[str, Any]:
        if self.payload is not None and (
            not isinstance(self.payload, Mapping) or len(self.payload) != 0
        ):
            raise ValueError("invalid local API payload")
        return {
            "version": self.version,
            "client_id": self.client_id,
            "capability": self.capability,
            "nonce": self.nonce,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "payload": dict(self.payload or {}),
        }

    def sign(self, secret: bytes) -> LocalAPIRequest:
        validate_request(self)
        tag = hmac.new(
            _secret_bytes(secret),
            _canonical(self.unsigned_document()),
            hashlib.sha256,
        ).hexdigest()
        return LocalAPIRequest(**{**self.__dict__, "auth": tag})

    def encode(self) -> bytes:
        validate_request(self)
        if self.payload is not None and len(self.payload) != 0:
            raise ValueError("invalid local API payload")
        document = {**self.unsigned_document(), "auth": self.auth}
        encoded = _canonical(document)
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("local API request exceeds size limit")
        return encoded


@dataclass(frozen=True)
class LocalAPIResponse:
    capability: str
    data: Mapping[str, Any] | None
    generated_at: float
    privacy: str = "redacted"
    error: str | None = None
    version: int = CONTRACT_VERSION

    def encode(self) -> bytes:
        if (
            self.version != CONTRACT_VERSION
            or self.capability not in READ_ONLY_CAPABILITIES
            or not _finite_timestamp(self.generated_at)
            or self.privacy != "redacted"
            or not isinstance(self.data, Mapping)
            or (self.error is not None and type(self.error) is not str)
        ):
            raise ValueError("invalid local API response")
        document = {
            "version": self.version,
            "capability": self.capability,
            "generated_at": self.generated_at,
            "privacy": self.privacy,
            "data": dict(self.data or {}),
            "error": self.error,
        }
        encoded = _canonical(document)
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise ValueError("local API response exceeds size limit")
        return encoded


def decode_request(raw: bytes | str) -> LocalAPIRequest:
    """Parse and validate the exact request envelope, rejecting unknown keys."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if type(raw) is not bytes or not raw or len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError("local API request exceeds size limit")
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid local API request") from exc
    if not isinstance(document, dict):
        raise ValueError("invalid local API request")
    if set(document) != _REQUEST_FIELDS:
        raise ValueError("invalid local API request fields")
    try:
        request = LocalAPIRequest(**document)
    except TypeError as exc:
        raise ValueError("invalid local API request") from exc
    validate_request(request)
    return request


def validate_request(
    request: LocalAPIRequest,
    *,
    now: float | None = None,
    secret: bytes | None = None,
) -> None:
    if type(request) is not LocalAPIRequest:
        raise ValueError("invalid local API request")
    if request.version != CONTRACT_VERSION or not _bounded_text(request.client_id, MAX_CLIENT_ID):
        raise ValueError("invalid local API request identity")
    if request.capability not in CAPABILITIES or not _bounded_text(request.nonce, MAX_NONCE):
        raise ValueError("unsupported local API capability")
    if not _finite_timestamp(request.issued_at) or not _finite_timestamp(
        request.expires_at
    ):
        raise ValueError("invalid local API timestamps")
    if (
        request.expires_at <= request.issued_at
        or request.expires_at - request.issued_at > DEFAULT_TTL_SECONDS
    ):
        raise ValueError("invalid local API expiry")
    if request.payload is not None and (
        not isinstance(request.payload, Mapping) or len(request.payload) != 0
    ):
        raise ValueError("invalid local API payload")
    if request.auth is not None and (
        type(request.auth) is not str or _AUTH_TAG.fullmatch(request.auth) is None
    ):
        raise ValueError("invalid local API authentication")
    if now is not None:
        if not _finite_timestamp(now):
            raise ValueError("invalid local API timestamps")
        if now < request.issued_at - 5 or now >= request.expires_at:
            raise ValueError("local API request expired")
    if secret is not None:
        expected = hmac.new(
            _secret_bytes(secret),
            _canonical(request.unsigned_document()),
            hashlib.sha256,
        ).hexdigest()
        if request.auth is None or not hmac.compare_digest(request.auth, expected):
            raise ValueError("invalid local API authentication")


class ReplayGuard:
    """Bounded nonce memory for a client, preventing request replay."""

    def __init__(self, limit: int = 512) -> None:
        if type(limit) is not int or not 1 <= limit <= MAX_REPLAY_ENTRIES:
            raise ValueError("invalid local API replay limit")
        self._seen: deque[tuple[str, str]] = deque(maxlen=limit)
        self._lock = Lock()

    def accept(self, request: LocalAPIRequest) -> bool:
        key = (request.client_id, request.nonce)
        with self._lock:
            if key in self._seen:
                return False
            self._seen.append(key)
            return True


def validate_authenticated_request(
    request: LocalAPIRequest,
    secret: bytes,
    *,
    now: float | None = None,
    replay_guard: ReplayGuard | None = None,
) -> None:
    validate_request(
        request,
        now=time.time() if now is None else now,
        secret=_secret_bytes(secret),
    )
    if replay_guard is not None and not replay_guard.accept(request):
        raise ValueError("replayed local API request")


def redacted_response(
    capability: str,
    data: Mapping[str, Any],
    *,
    generated_at: float | None = None,
) -> LocalAPIResponse:
    """Wrap data that the transport adapter has already allowlisted."""
    if capability not in READ_ONLY_CAPABILITIES:
        raise ValueError("unsupported local API capability")
    if not isinstance(data, Mapping):
        raise ValueError("invalid local API response")
    response = LocalAPIResponse(
        capability,
        data,
        time.time() if generated_at is None else generated_at,
    )
    response.encode()
    return response
