"""Authenticated, bounded remote observation without a remote command surface.

This module deliberately stops at an event-stream protocol boundary.  It does
not discover peers, open sockets, invoke SSH, or execute a command.  A caller
may inject an authenticated event-stream transport and receive a minimized
observation envelope.  Status and outcome are the only default-consented
fields; message text, usage, and capacity are separate opt-ins.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Protocol, TypeAlias

REMOTE_OBSERVATION_SCHEMA_VERSION: Final = 1
MAX_EVENT_BYTES: Final = 16 * 1024
MAX_BATCH_BYTES: Final = 256 * 1024
MAX_EVENTS_PER_STREAM: Final = 64
MAX_MESSAGE_CHARS: Final = 512
MAX_MAP_FIELDS: Final = 16
MAX_STREAM_SECONDS: Final = 30.0
MAX_EVENT_AGE_SECONDS: Final = 5 * 60.0
MAX_FUTURE_SKEW_SECONDS: Final = 5.0
MAX_SOURCE_ID_CHARS: Final = 128
MAX_STREAM_ID_CHARS: Final = 128
MAX_STATUS_CHARS: Final = 64
MAX_OUTCOME_CHARS: Final = 64
MAX_SIGNATURE_CHARS: Final = 512

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAP_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_ENVELOPE_FIELDS: Final = frozenset(
    {"schema_version", "source_id", "stream_id", "sequence", "observed_at", "payload", "signature"}
)
_PAYLOAD_FIELDS: Final = frozenset({"status", "outcome", "message", "usage", "capacity"})
_USAGE_FIELDS: Final = frozenset(
    {"input_tokens", "cached_input_tokens", "output_tokens", "model_count", "estimated_cost_usd"}
)
_CAPACITY_FIELDS: Final = frozenset({"remaining_percent", "reset_at", "window", "label"})


class RemoteObservationScope(str, Enum):
    """Independently consentable parts of a remote observation."""

    STATUS_OUTCOME = "status_outcome"
    MESSAGE_TEXT = "message_text"
    USAGE = "usage"
    CAPACITY = "capacity"


DEFAULT_CONSENT: Final = frozenset({RemoteObservationScope.STATUS_OUTCOME})


class RemoteObservationRefusalCode(str, Enum):
    """Closed, non-sensitive refusal vocabulary for the observation boundary."""

    AUTHENTICATION_REQUIRED = "authentication_required"
    INVALID_ENVELOPE = "invalid_envelope"
    INVALID_SIGNATURE = "invalid_signature"
    UNSUPPORTED_VERSION = "unsupported_version"
    SOURCE_IDENTITY_MISMATCH = "source_identity_mismatch"
    STREAM_IDENTITY_MISMATCH = "stream_identity_mismatch"
    REPLAY = "replay"
    SEQUENCE_GAP = "sequence_gap"
    TOO_LARGE = "too_large"
    TOO_MANY = "too_many"
    TOO_OLD = "too_old"
    FROM_FUTURE = "from_future"
    STREAM_EXPIRED = "stream_expired"
    CONSENT_REQUIRED = "consent_required"
    REMOTE_COMMAND_FORBIDDEN = "remote_command_forbidden"
    NO_EVENT_STREAM = "no_event_stream"
    UNAUTHENTICATED_EVENT_STREAM = "unauthenticated_event_stream"


@dataclass(frozen=True, slots=True)
class RemoteObservationRefusal:
    code: RemoteObservationRefusalCode

    def __post_init__(self) -> None:
        if type(self.code) is not RemoteObservationRefusalCode:
            raise ValueError("invalid remote observation refusal")


class RemoteObservationRefused(ValueError):
    """Exception form for callers that choose fail-closed control flow."""

    def __init__(self, code: RemoteObservationRefusalCode) -> None:
        if type(code) is not RemoteObservationRefusalCode:
            raise ValueError("invalid remote observation refusal")
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class RemoteObservation:
    """Raw event input.  The envelope builder removes unconsented content."""

    source_id: str
    stream_id: str
    sequence: int
    observed_at: float
    status: str
    outcome: str
    message: str | None = None
    usage: Mapping[str, object] = field(default_factory=dict, repr=False)
    capacity: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_identifier(self.source_id, MAX_SOURCE_ID_CHARS, "source id")
        _validate_identifier(self.stream_id, MAX_STREAM_ID_CHARS, "stream id")
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("invalid observation sequence")
        if not _finite_nonnegative(self.observed_at):
            raise ValueError("invalid observation timestamp")
        if not _safe_text(self.status, MAX_STATUS_CHARS) or not _safe_text(
            self.outcome, MAX_OUTCOME_CHARS
        ):
            raise ValueError("invalid observation status")
        if self.message is not None and type(self.message) is not str:
            raise ValueError("invalid observation message")
        if not isinstance(self.usage, Mapping) or not isinstance(self.capacity, Mapping):
            raise ValueError("invalid observation fields")


@dataclass(frozen=True, slots=True)
class RemoteObservationPolicy:
    """Source-scoped consent and resource limits for one event stream."""

    source_id: str
    stream_id: str = "default"
    consents: frozenset[RemoteObservationScope] = DEFAULT_CONSENT
    max_event_bytes: int = MAX_EVENT_BYTES
    max_batch_bytes: int = MAX_BATCH_BYTES
    max_events: int = MAX_EVENTS_PER_STREAM
    max_event_age_seconds: float = MAX_EVENT_AGE_SECONDS
    max_future_skew_seconds: float = MAX_FUTURE_SKEW_SECONDS
    max_stream_seconds: float = MAX_STREAM_SECONDS

    def __post_init__(self) -> None:
        _validate_identifier(self.source_id, MAX_SOURCE_ID_CHARS, "source id")
        _validate_identifier(self.stream_id, MAX_STREAM_ID_CHARS, "stream id")
        if (
            type(self.consents) is not frozenset
            or not self.consents
            or not self.consents.issubset(set(RemoteObservationScope))
            or RemoteObservationScope.STATUS_OUTCOME not in self.consents
        ):
            raise ValueError("status and outcome consent is required")
        if (
            type(self.max_event_bytes) is not int
            or not 1 <= self.max_event_bytes <= MAX_EVENT_BYTES
            or type(self.max_batch_bytes) is not int
            or not 1 <= self.max_batch_bytes <= MAX_BATCH_BYTES
            or type(self.max_events) is not int
            or not 1 <= self.max_events <= MAX_EVENTS_PER_STREAM
            or not _bounded_float(self.max_event_age_seconds, MAX_EVENT_AGE_SECONDS)
            or not _bounded_float(self.max_future_skew_seconds, MAX_FUTURE_SKEW_SECONDS)
            or not _bounded_float(self.max_stream_seconds, MAX_STREAM_SECONDS)
        ):
            raise ValueError("invalid remote observation bounds")

    def allows(self, scope: RemoteObservationScope) -> bool:
        if type(scope) is not RemoteObservationScope:
            raise ValueError("invalid observation scope")
        return scope in self.consents

    def with_consent(self, scope: RemoteObservationScope) -> RemoteObservationPolicy:
        if type(scope) is not RemoteObservationScope:
            raise ValueError("invalid observation scope")
        return RemoteObservationPolicy(
            self.source_id,
            self.stream_id,
            self.consents | {scope},
            self.max_event_bytes,
            self.max_batch_bytes,
            self.max_events,
            self.max_event_age_seconds,
            self.max_future_skew_seconds,
            self.max_stream_seconds,
        )


@dataclass(frozen=True, slots=True)
class RemoteObservationEnvelope:
    schema_version: int
    source_id: str
    stream_id: str
    sequence: int
    observed_at: float
    payload: Mapping[str, object]
    signature: str


@dataclass(frozen=True, slots=True)
class RemoteObservationDecision:
    accepted: bool
    envelope: RemoteObservationEnvelope | None = None
    refusal: RemoteObservationRefusal | None = None

    def __post_init__(self) -> None:
        if self.accepted != (self.envelope is not None and self.refusal is None):
            raise ValueError("invalid remote observation decision")
        if not self.accepted and self.refusal is None:
            raise ValueError("a refused observation needs a refusal code")


@dataclass(frozen=True, slots=True)
class RemoteObservationBatch:
    accepted: tuple[RemoteObservationEnvelope, ...] = ()
    refusals: tuple[RemoteObservationRefusal, ...] = ()


Signer: TypeAlias = Callable[[bytes], str]
Verifier: TypeAlias = Callable[[bytes, str], bool]


def _validate_identifier(value: object, limit: int, label: str) -> None:
    if type(value) is not str or len(value) > limit or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")


def _finite_nonnegative(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _bounded_float(value: object, ceiling: float) -> bool:
    return _finite_nonnegative(value) and 0.0 < float(value) <= ceiling


def _safe_text(value: object, limit: int) -> str:
    if type(value) is not str or not value or len(value) > limit:
        return ""
    if any(ord(character) < 32 or 0x7F <= ord(character) <= 0x9F for character in value):
        return ""
    return " ".join(value.split())


def _sanitize_message(value: str) -> str:
    cleaned = "".join(
        " " if unicodedata.category(character) in {"Cc", "Cf", "Cs", "Co", "Cn"} else character
        for character in value
    )
    return " ".join(cleaned.split())[:MAX_MESSAGE_CHARS]


def _sanitize_map(value: Mapping[str, object], allowed: frozenset[str]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in sorted(value):
        if len(result) >= MAX_MAP_FIELDS or type(key) is not str:
            continue
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
            text = _sanitize_message(item)
            if text:
                result[key] = text
    return result


def _validate_payload(payload: object) -> None:
    if not isinstance(payload, dict) or not _PAYLOAD_FIELDS.issuperset(payload):
        raise ValueError("invalid observation payload")
    for required in ("status", "outcome"):
        if required not in payload or not _safe_text(payload[required], MAX_STATUS_CHARS):
            raise ValueError("invalid observation payload")
    if "message" in payload:
        if type(payload["message"]) is not str or len(payload["message"]) > MAX_MESSAGE_CHARS:
            raise ValueError("invalid observation payload")
    for field_name, allowed in (("usage", _USAGE_FIELDS), ("capacity", _CAPACITY_FIELDS)):
        if field_name in payload:
            value = payload[field_name]
            if not isinstance(value, dict) or not set(value).issubset(allowed):
                raise ValueError("invalid observation payload")
            if any(type(key) is not str or _MAP_KEY.fullmatch(key) is None for key in value):
                raise ValueError("invalid observation payload")
            if len(value) > MAX_MAP_FIELDS:
                raise ValueError("invalid observation payload")
            if _sanitize_map(value, allowed) != value:
                raise ValueError("invalid observation payload")


def _unsigned_document(envelope: RemoteObservationEnvelope) -> dict[str, object]:
    return {
        "schema_version": envelope.schema_version,
        "source_id": envelope.source_id,
        "stream_id": envelope.stream_id,
        "sequence": envelope.sequence,
        "observed_at": envelope.observed_at,
        "payload": dict(envelope.payload),
    }


def _canonical_unsigned(envelope: RemoteObservationEnvelope) -> bytes:
    return json.dumps(
        _unsigned_document(envelope),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _validate_signature(signature: object) -> str:
    if type(signature) is not str or not 1 <= len(signature) <= MAX_SIGNATURE_CHARS:
        raise ValueError("invalid observation signature")
    if any(ord(character) < 33 or ord(character) > 126 for character in signature):
        raise ValueError("invalid observation signature")
    return signature


def build_observation_envelope(
    observation: RemoteObservation,
    policy: RemoteObservationPolicy,
    *,
    signer: Signer,
) -> RemoteObservationEnvelope:
    """Minimize a raw observation and sign exactly the bytes sent to a peer."""
    if type(observation) is not RemoteObservation or type(policy) is not RemoteObservationPolicy:
        raise ValueError("invalid remote observation input")
    if observation.source_id != policy.source_id:
        raise RemoteObservationRefused(RemoteObservationRefusalCode.SOURCE_IDENTITY_MISMATCH)
    if observation.stream_id != policy.stream_id:
        raise RemoteObservationRefused(RemoteObservationRefusalCode.STREAM_IDENTITY_MISMATCH)
    if not callable(signer):
        raise RemoteObservationRefused(RemoteObservationRefusalCode.AUTHENTICATION_REQUIRED)
    payload: dict[str, object] = {
        "status": _sanitize_message(observation.status)[:MAX_STATUS_CHARS],
        "outcome": _sanitize_message(observation.outcome)[:MAX_OUTCOME_CHARS],
    }
    if policy.allows(RemoteObservationScope.MESSAGE_TEXT) and observation.message:
        message = _sanitize_message(observation.message)
        if message:
            payload["message"] = message
    if policy.allows(RemoteObservationScope.USAGE):
        usage = _sanitize_map(observation.usage, _USAGE_FIELDS)
        if usage:
            payload["usage"] = usage
    if policy.allows(RemoteObservationScope.CAPACITY):
        capacity = _sanitize_map(observation.capacity, _CAPACITY_FIELDS)
        if capacity:
            payload["capacity"] = capacity
    unsigned = RemoteObservationEnvelope(
        REMOTE_OBSERVATION_SCHEMA_VERSION,
        observation.source_id,
        observation.stream_id,
        observation.sequence,
        float(observation.observed_at),
        payload,
        "pending",
    )
    try:
        signature = _validate_signature(signer(_canonical_unsigned(unsigned)))
    except RemoteObservationRefused:
        raise
    except Exception as exc:
        raise RemoteObservationRefused(RemoteObservationRefusalCode.AUTHENTICATION_REQUIRED) from exc
    return RemoteObservationEnvelope(
        unsigned.schema_version,
        unsigned.source_id,
        unsigned.stream_id,
        unsigned.sequence,
        unsigned.observed_at,
        unsigned.payload,
        signature,
    )


def encode_envelope(envelope: RemoteObservationEnvelope) -> bytes:
    """Serialize an envelope using a deterministic, duplicate-free shape."""
    if type(envelope) is not RemoteObservationEnvelope:
        raise ValueError("invalid remote observation envelope")
    _validate_identifier(envelope.source_id, MAX_SOURCE_ID_CHARS, "source id")
    _validate_identifier(envelope.stream_id, MAX_STREAM_ID_CHARS, "stream id")
    _validate_signature(envelope.signature)
    _validate_payload(envelope.payload)
    document = {**_unsigned_document(envelope), "signature": envelope.signature}
    if document["schema_version"] != REMOTE_OBSERVATION_SCHEMA_VERSION:
        raise ValueError("unsupported observation version")
    encoded = json.dumps(
        document, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
    ).encode("utf-8")
    if len(encoded) > MAX_EVENT_BYTES:
        raise RemoteObservationRefused(RemoteObservationRefusalCode.TOO_LARGE)
    return encoded


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("duplicate observation field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("invalid observation number")


def decode_envelope(
    encoded: bytes,
    *,
    verifier: Verifier,
    max_bytes: int = MAX_EVENT_BYTES,
) -> RemoteObservationEnvelope:
    if (
        type(encoded) is not bytes
        or type(max_bytes) is not int
        or not 1 <= max_bytes <= MAX_EVENT_BYTES
        or len(encoded) > max_bytes
    ):
        raise RemoteObservationRefused(RemoteObservationRefusalCode.TOO_LARGE)
    if not callable(verifier):
        raise RemoteObservationRefused(RemoteObservationRefusalCode.AUTHENTICATION_REQUIRED)
    try:
        document = json.loads(
            encoded.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, ValueError, TypeError):
        raise RemoteObservationRefused(RemoteObservationRefusalCode.INVALID_ENVELOPE) from None
    if not isinstance(document, dict) or frozenset(document) != _ENVELOPE_FIELDS:
        raise RemoteObservationRefused(RemoteObservationRefusalCode.INVALID_ENVELOPE)
    try:
        envelope = RemoteObservationEnvelope(
            document["schema_version"],
            document["source_id"],
            document["stream_id"],
            document["sequence"],
            document["observed_at"],
            document["payload"],
            document["signature"],
        )
        _validate_identifier(envelope.source_id, MAX_SOURCE_ID_CHARS, "source id")
        _validate_identifier(envelope.stream_id, MAX_STREAM_ID_CHARS, "stream id")
        if envelope.schema_version != REMOTE_OBSERVATION_SCHEMA_VERSION:
            raise RemoteObservationRefused(RemoteObservationRefusalCode.UNSUPPORTED_VERSION)
        if type(envelope.sequence) is not int or envelope.sequence <= 0:
            raise ValueError("invalid sequence")
        if not _finite_nonnegative(envelope.observed_at):
            raise ValueError("invalid observation envelope")
        _validate_payload(envelope.payload)
        signature = _validate_signature(envelope.signature)
    except RemoteObservationRefused:
        raise
    except (TypeError, ValueError):
        raise RemoteObservationRefused(RemoteObservationRefusalCode.INVALID_ENVELOPE) from None
    try:
        valid = verifier(_canonical_unsigned(envelope), signature)
    except Exception:
        valid = False
    if valid is not True:
        raise RemoteObservationRefused(RemoteObservationRefusalCode.INVALID_SIGNATURE)
    return envelope


@dataclass(slots=True)
class RemoteObservationReceiver:
    policy: RemoteObservationPolicy
    verifier: Verifier
    monotonic: Callable[[], float] = time.monotonic
    _last_sequence: int | None = field(default=None, init=False, repr=False)
    _accepted_count: int = field(default=0, init=False, repr=False)
    _accepted_bytes: int = field(default=0, init=False, repr=False)
    _started_at: float | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.policy) is not RemoteObservationPolicy or not callable(self.verifier):
            raise ValueError("invalid observation receiver")
        if not callable(self.monotonic):
            raise ValueError("invalid observation clock")

    def accept(
        self,
        event: bytes | RemoteObservationEnvelope,
        *,
        now: float | None = None,
    ) -> RemoteObservationDecision:
        if now is None:
            now = time.time()
        if type(now) not in {int, float} or not math.isfinite(float(now)):
            return _refusal(RemoteObservationRefusalCode.INVALID_ENVELOPE)
        try:
            if isinstance(event, bytes):
                envelope = decode_envelope(event, verifier=self.verifier, max_bytes=self.policy.max_event_bytes)
                encoded_size = len(event)
            elif type(event) is RemoteObservationEnvelope:
                envelope = event
                if event.signature == "":
                    return _refusal(RemoteObservationRefusalCode.AUTHENTICATION_REQUIRED)
                encoded_size = len(encode_envelope(event))
                if not self._verify_envelope(event):
                    return _refusal(RemoteObservationRefusalCode.INVALID_SIGNATURE)
            else:
                return _refusal(RemoteObservationRefusalCode.INVALID_ENVELOPE)
        except RemoteObservationRefused as exc:
            return _refusal(exc.code)
        except (TypeError, ValueError):
            return _refusal(RemoteObservationRefusalCode.INVALID_ENVELOPE)
        refusal = self._check(envelope, encoded_size, float(now))
        if refusal is not None:
            return _refusal(refusal)
        if self._started_at is None:
            self._started_at = float(self.monotonic())
        self._last_sequence = envelope.sequence
        self._accepted_count += 1
        self._accepted_bytes += encoded_size
        return RemoteObservationDecision(True, envelope, None)

    def _verify_envelope(self, envelope: RemoteObservationEnvelope) -> bool:
        try:
            return self.verifier(_canonical_unsigned(envelope), envelope.signature) is True
        except Exception:
            return False

    def _check(
        self, envelope: RemoteObservationEnvelope, encoded_size: int, now: float
    ) -> RemoteObservationRefusalCode | None:
        if envelope.source_id != self.policy.source_id:
            return RemoteObservationRefusalCode.SOURCE_IDENTITY_MISMATCH
        if envelope.stream_id != self.policy.stream_id:
            return RemoteObservationRefusalCode.STREAM_IDENTITY_MISMATCH
        if envelope.observed_at < now - self.policy.max_event_age_seconds:
            return RemoteObservationRefusalCode.TOO_OLD
        if envelope.observed_at > now + self.policy.max_future_skew_seconds:
            return RemoteObservationRefusalCode.FROM_FUTURE
        if self._started_at is not None and float(self.monotonic()) - self._started_at > self.policy.max_stream_seconds:
            return RemoteObservationRefusalCode.STREAM_EXPIRED
        if (
            encoded_size > self.policy.max_event_bytes
            or self._accepted_bytes + encoded_size > self.policy.max_batch_bytes
        ):
            return RemoteObservationRefusalCode.TOO_LARGE
        if self._accepted_count >= self.policy.max_events:
            return RemoteObservationRefusalCode.TOO_MANY
        if self._last_sequence is not None:
            if envelope.sequence <= self._last_sequence:
                return RemoteObservationRefusalCode.REPLAY
            if envelope.sequence != self._last_sequence + 1:
                return RemoteObservationRefusalCode.SEQUENCE_GAP
        if "message" in envelope.payload and not self.policy.allows(RemoteObservationScope.MESSAGE_TEXT):
            return RemoteObservationRefusalCode.CONSENT_REQUIRED
        if "usage" in envelope.payload and not self.policy.allows(RemoteObservationScope.USAGE):
            return RemoteObservationRefusalCode.CONSENT_REQUIRED
        if "capacity" in envelope.payload and not self.policy.allows(RemoteObservationScope.CAPACITY):
            return RemoteObservationRefusalCode.CONSENT_REQUIRED
        return None

    @property
    def last_sequence(self) -> int | None:
        return self._last_sequence

    @property
    def accepted_count(self) -> int:
        return self._accepted_count

    @property
    def accepted_bytes(self) -> int:
        return self._accepted_bytes


def _refusal(code: RemoteObservationRefusalCode) -> RemoteObservationDecision:
    return RemoteObservationDecision(False, None, RemoteObservationRefusal(code))


class AuthenticatedEventStream(Protocol):
    """Minimal injectable transport contract.  No command method exists here."""

    authenticated_event_stream: bool

    def stream_events(
        self, source_id: str, *, max_events: int, deadline: float
    ) -> Iterable[bytes | RemoteObservationEnvelope]: ...


def select_event_stream(
    event_stream: object | None, *, command_transport: object | None = None
) -> AuthenticatedEventStream:
    """Select an authenticated stream, ignoring a command fallback entirely."""
    if event_stream is None:
        if command_transport is not None:
            raise RemoteObservationRefused(RemoteObservationRefusalCode.REMOTE_COMMAND_FORBIDDEN)
        raise RemoteObservationRefused(RemoteObservationRefusalCode.NO_EVENT_STREAM)
    authenticated = getattr(event_stream, "authenticated_event_stream", None)
    if authenticated is None:
        authenticated = getattr(event_stream, "authenticated", None)
    if authenticated is not True:
        raise RemoteObservationRefused(RemoteObservationRefusalCode.UNAUTHENTICATED_EVENT_STREAM)
    if not callable(getattr(event_stream, "stream_events", None)):
        raise RemoteObservationRefused(RemoteObservationRefusalCode.NO_EVENT_STREAM)
    return event_stream  # type: ignore[return-value]


def collect_remote_observations(
    *,
    event_stream: object | None,
    receiver: RemoteObservationReceiver,
    now: float | None = None,
    command_transport: object | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> RemoteObservationBatch:
    """Collect only from an authenticated event stream under a hard deadline."""
    try:
        stream = select_event_stream(event_stream, command_transport=command_transport)
    except RemoteObservationRefused as exc:
        return RemoteObservationBatch(refusals=(RemoteObservationRefusal(exc.code),))
    started = float(monotonic())
    if now is None:
        now = time.time()
    deadline = started + receiver.policy.max_stream_seconds
    accepted: list[RemoteObservationEnvelope] = []
    refusals: list[RemoteObservationRefusal] = []
    try:
        events = stream.stream_events(
            receiver.policy.source_id,
            max_events=receiver.policy.max_events - receiver._accepted_count,
            deadline=deadline,
        )
        for event in events:
            if float(monotonic()) > deadline:
                refusals.append(RemoteObservationRefusal(RemoteObservationRefusalCode.STREAM_EXPIRED))
                break
            decision = receiver.accept(event, now=now)
            if decision.accepted:
                accepted.append(decision.envelope)  # type: ignore[arg-type]
            elif decision.refusal is not None:
                refusals.append(decision.refusal)
                if decision.refusal.code is RemoteObservationRefusalCode.TOO_MANY:
                    break
    except Exception:
        refusals.append(RemoteObservationRefusal(RemoteObservationRefusalCode.NO_EVENT_STREAM))
    return RemoteObservationBatch(tuple(accepted), tuple(refusals))


def hmac_signer(secret: bytes) -> Signer:
    if type(secret) is not bytes or len(secret) < 16:
        raise ValueError("invalid observation signing secret")
    return lambda payload: hmac.new(secret, payload, hashlib.sha256).hexdigest()


def hmac_verifier(secret: bytes) -> Verifier:
    signing = hmac_signer(secret)
    return lambda payload, signature: hmac.compare_digest(signing(payload), signature)


__all__ = [
    "DEFAULT_CONSENT",
    "MAX_BATCH_BYTES",
    "MAX_EVENTS_PER_STREAM",
    "MAX_EVENT_BYTES",
    "AuthenticatedEventStream",
    "RemoteObservation",
    "RemoteObservationBatch",
    "RemoteObservationDecision",
    "RemoteObservationEnvelope",
    "RemoteObservationPolicy",
    "RemoteObservationReceiver",
    "RemoteObservationRefusal",
    "RemoteObservationRefusalCode",
    "RemoteObservationRefused",
    "RemoteObservationScope",
    "build_observation_envelope",
    "collect_remote_observations",
    "decode_envelope",
    "encode_envelope",
    "hmac_signer",
    "hmac_verifier",
    "select_event_stream",
]
