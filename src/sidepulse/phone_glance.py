"""Small, authenticated phone-glance projection for private networks.

This is a data contract only. It never discovers peers, opens sockets, reads
credentials, executes commands, or forwards message content by default.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

MAX_BYTES: Final = 8 * 1024
MAX_AGE: Final = 5 * 60.0
MAX_FUTURE: Final = 5.0
MAX_STATUS_CHARS: Final = 64
MAX_OUTCOME_CHARS: Final = 64
MAX_LABEL_CHARS: Final = 160
MAX_MESSAGE_CHARS: Final = 512
MAX_SIGNATURE_CHARS: Final = 64
MAX_SIGNED_BODY_CHARS: Final = (MAX_BYTES * 4 + 2) // 3
MAX_MAP_FIELDS: Final = 16
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_PAYLOAD_FIELDS: Final = frozenset({"status", "outcome", "label", "message", "usage", "capacity"})
_USAGE_FIELDS: Final = frozenset(
    {"input_tokens", "cached_input_tokens", "output_tokens", "model_count", "estimated_cost_usd"}
)
_CAPACITY_FIELDS: Final = frozenset({"remaining_percent", "reset_at", "window", "label"})
_MAP_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SIGNATURE = re.compile(r"[0-9a-f]{64}\Z")


class PhoneGlanceRefused(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PhoneGlance:
    source_id: str
    sequence: int
    observed_at: float
    status: str
    outcome: str
    label: str | None = None
    message: str | None = None
    usage: Mapping[str, object] | None = None
    capacity: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not _valid_id(self.source_id):
            raise ValueError("invalid phone glance source id")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("invalid phone glance sequence")
        if not _finite_nonnegative(self.observed_at):
            raise ValueError("invalid phone glance timestamp")
        _text(self.status, MAX_STATUS_CHARS)
        _text(self.outcome, MAX_OUTCOME_CHARS)
        if self.label is not None:
            _text(self.label, MAX_LABEL_CHARS)
        if self.message is not None and type(self.message) is not str:
            raise ValueError("invalid phone glance message")
        if self.usage is not None and not isinstance(self.usage, Mapping):
            raise ValueError("invalid phone glance usage")
        if self.capacity is not None and not isinstance(self.capacity, Mapping):
            raise ValueError("invalid phone glance capacity")


@dataclass(frozen=True, slots=True)
class PhoneGlancePolicy:
    source_id: str
    include_message: bool = False
    include_usage: bool = False
    include_capacity: bool = False
    max_bytes: int = MAX_BYTES
    max_age: float = MAX_AGE
    max_future: float = MAX_FUTURE

    def __post_init__(self) -> None:
        if not _valid_id(self.source_id):
            raise ValueError("invalid phone glance source id")
        if any(type(value) is not bool for value in (self.include_message, self.include_usage, self.include_capacity)):
            raise ValueError("invalid phone glance policy")
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= MAX_BYTES:
            raise ValueError("invalid phone glance byte limit")
        if not _bounded_float(self.max_age, MAX_AGE) or not _bounded_float(
            self.max_future, MAX_FUTURE
        ):
            raise ValueError("invalid phone glance age limit")


@dataclass(frozen=True, slots=True)
class PhoneGlanceEnvelope:
    source_id: str
    sequence: int
    observed_at: float
    payload: Mapping[str, object]
    signature: str
    signed_body: str | None = None

    def __post_init__(self) -> None:
        if not _valid_id(self.source_id):
            raise ValueError("invalid phone glance source id")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("invalid phone glance sequence")
        if not _finite_nonnegative(self.observed_at):
            raise ValueError("invalid phone glance timestamp")
        _validate_payload(self.payload)
        _validate_signature(self.signature)
        if self.signed_body is not None:
            _decode_signed_body(self.signed_body)
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


def _valid_id(value: object) -> bool:
    return type(value) is str and bool(_ID.fullmatch(value))


def _finite_nonnegative(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _bounded_float(value: object, ceiling: float) -> bool:
    return _finite_nonnegative(value) and 0.0 < float(value) <= ceiling


def _text(value: object, limit: int) -> str:
    if type(value) is not str or not value or len(value) > limit:
        raise PhoneGlanceRefused("invalid glance text")
    if any(ord(character) < 32 or 0x7F <= ord(character) <= 0x9F for character in value):
        raise PhoneGlanceRefused("invalid glance text")
    return " ".join(value.split())


def _sanitize_map(value: Mapping[str, object], allowed: frozenset[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    keys = sorted(key for key in value if type(key) is str)
    for key in keys:
        if len(result) >= MAX_MAP_FIELDS:
            break
        if key not in allowed or _MAP_KEY.fullmatch(key) is None:
            continue
        item = value[key]
        if isinstance(item, bool):
            result[key] = item
        elif type(item) is int and 0 <= item <= 10**15:
            result[key] = item
        elif type(item) is float and math.isfinite(item) and 0.0 <= item <= 10**15:
            result[key] = item
        elif type(item) is str:
            text = _text(item, MAX_MESSAGE_CHARS)
            if text:
                result[key] = text
    return result


def _validate_payload(payload: object) -> None:
    if not isinstance(payload, Mapping) or not _PAYLOAD_FIELDS.issuperset(payload):
        raise PhoneGlanceRefused("invalid envelope")
    for required, limit in (("status", MAX_STATUS_CHARS), ("outcome", MAX_OUTCOME_CHARS)):
        if required not in payload:
            raise PhoneGlanceRefused("invalid envelope")
        _text(payload[required], limit)
    if "label" in payload:
        _text(payload["label"], MAX_LABEL_CHARS)
    if "message" in payload:
        _text(payload["message"], MAX_MESSAGE_CHARS)
    for field_name, allowed in (("usage", _USAGE_FIELDS), ("capacity", _CAPACITY_FIELDS)):
        if field_name not in payload:
            continue
        value = payload[field_name]
        if not isinstance(value, Mapping) or len(value) > MAX_MAP_FIELDS:
            raise PhoneGlanceRefused("invalid envelope")
        if any(type(key) is not str or _MAP_KEY.fullmatch(key) is None for key in value):
            raise PhoneGlanceRefused("invalid envelope")
        if _sanitize_map(value, allowed) != dict(value):
            raise PhoneGlanceRefused("invalid envelope")


def _validate_signature(signature: object) -> str:
    if type(signature) is not str or _SIGNATURE.fullmatch(signature) is None:
        raise PhoneGlanceRefused("invalid signature")
    return signature


def _encode_signed_body(payload: bytes) -> str:
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_BYTES:
        raise PhoneGlanceRefused("invalid signed body")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_signed_body(value: object) -> bytes:
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_SIGNED_BODY_CHARS
        or "=" in value
    ):
        raise PhoneGlanceRefused("invalid signed body")
    try:
        encoded = value.encode("ascii")
        payload = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise PhoneGlanceRefused("invalid signed body") from exc
    if not 1 <= len(payload) <= MAX_BYTES or _encode_signed_body(payload) != value:
        raise PhoneGlanceRefused("invalid signed body")
    return payload


def _json_matches(left: object, right: object) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _json_matches(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_matches(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return type(left) is type(right) and left == right


def _validate_signed_document(
    envelope: PhoneGlanceEnvelope,
    signed_bytes: bytes,
) -> None:
    try:
        document = json.loads(signed_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhoneGlanceRefused("invalid signed body") from exc
    expected = {
        "source_id": envelope.source_id,
        "sequence": envelope.sequence,
        "observed_at": envelope.observed_at,
        "payload": envelope.payload,
    }
    if not _json_matches(document, expected):
        raise PhoneGlanceRefused("signed body mismatch")


def _unsigned(envelope: PhoneGlanceEnvelope) -> bytes:
    return json.dumps(
        {
            "source_id": envelope.source_id,
            "sequence": envelope.sequence,
            "observed_at": envelope.observed_at,
            "payload": dict(envelope.payload),
        },
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def build_phone_glance(
    glance: PhoneGlance,
    policy: PhoneGlancePolicy,
    *,
    signer: Callable[[bytes], str],
) -> PhoneGlanceEnvelope:
    if type(glance) is not PhoneGlance or type(policy) is not PhoneGlancePolicy:
        raise PhoneGlanceRefused("invalid or unauthenticated glance")
    if glance.source_id != policy.source_id or not callable(signer):
        raise PhoneGlanceRefused("invalid or unauthenticated glance")
    payload: dict[str, object] = {
        "status": _text(glance.status, MAX_STATUS_CHARS),
        "outcome": _text(glance.outcome, MAX_OUTCOME_CHARS),
    }
    if glance.label is not None:
        payload["label"] = _text(glance.label, MAX_LABEL_CHARS)
    if policy.include_message and glance.message:
        payload["message"] = _text(glance.message, MAX_MESSAGE_CHARS)
    if policy.include_usage and glance.usage:
        usage = _sanitize_map(glance.usage, _USAGE_FIELDS)
        if usage:
            payload["usage"] = usage
    if policy.include_capacity and glance.capacity:
        capacity = _sanitize_map(glance.capacity, _CAPACITY_FIELDS)
        if capacity:
            payload["capacity"] = capacity
    envelope = PhoneGlanceEnvelope(
        glance.source_id,
        glance.sequence,
        float(glance.observed_at),
        payload,
        "0" * MAX_SIGNATURE_CHARS,
    )
    unsigned = _unsigned(envelope)
    try:
        signature = _validate_signature(signer(unsigned))
    except PhoneGlanceRefused:
        raise
    except Exception as exc:
        raise PhoneGlanceRefused("authentication failed") from exc
    return PhoneGlanceEnvelope(
        envelope.source_id,
        envelope.sequence,
        envelope.observed_at,
        envelope.payload,
        signature,
        _encode_signed_body(unsigned),
    )


def encode_phone_glance(
    envelope: PhoneGlanceEnvelope,
    *,
    max_bytes: int = MAX_BYTES,
) -> bytes:
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
        raise PhoneGlanceRefused("payload too large")
    if type(envelope) is not PhoneGlanceEnvelope:
        raise PhoneGlanceRefused("invalid envelope")
    _validate_payload(envelope.payload)
    _validate_signature(envelope.signature)
    signed_body = envelope.signed_body
    if signed_body is None:
        signed_body = _encode_signed_body(_unsigned(envelope))
    else:
        _decode_signed_body(signed_body)
    encoded = json.dumps(
        {
            "source_id": envelope.source_id,
            "sequence": envelope.sequence,
            "observed_at": envelope.observed_at,
            "payload": dict(envelope.payload),
            "signed_body": signed_body,
            "signature": envelope.signature,
        },
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise PhoneGlanceRefused("payload too large")
    return encoded


def receive_phone_glance(
    envelope: PhoneGlanceEnvelope,
    policy: PhoneGlancePolicy,
    *,
    verifier: Callable[[bytes, str], bool],
    now: float | None = None,
    last_sequence: int | None = None,
) -> PhoneGlanceEnvelope:
    if type(envelope) is not PhoneGlanceEnvelope or type(policy) is not PhoneGlancePolicy:
        raise PhoneGlanceRefused("invalid envelope")
    if envelope.source_id != policy.source_id:
        raise PhoneGlanceRefused("source mismatch or replay")
    if last_sequence is not None and (type(last_sequence) is not int or envelope.sequence <= last_sequence):
        raise PhoneGlanceRefused("source mismatch or replay")
    if now is None:
        now = time.time()
    if not _finite_nonnegative(now):
        raise PhoneGlanceRefused("invalid envelope")
    if envelope.observed_at < float(now) - policy.max_age:
        raise PhoneGlanceRefused("glance too old")
    if envelope.observed_at > float(now) + policy.max_future:
        raise PhoneGlanceRefused("glance from future")
    encode_phone_glance(envelope, max_bytes=policy.max_bytes)
    if "message" in envelope.payload and not policy.include_message:
        raise PhoneGlanceRefused("consent required")
    if "usage" in envelope.payload and not policy.include_usage:
        raise PhoneGlanceRefused("consent required")
    if "capacity" in envelope.payload and not policy.include_capacity:
        raise PhoneGlanceRefused("consent required")
    signed_bytes = _unsigned(envelope)
    if envelope.signed_body is not None:
        signed_bytes = _decode_signed_body(envelope.signed_body)
        _validate_signed_document(envelope, signed_bytes)
    try:
        valid = callable(verifier) and verifier(signed_bytes, envelope.signature) is True
    except Exception:
        valid = False
    if not valid:
        raise PhoneGlanceRefused("invalid signature")
    return envelope
