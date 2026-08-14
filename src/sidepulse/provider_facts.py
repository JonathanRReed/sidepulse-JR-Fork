"""Pure, content-free provider identities and observation facts.

The records in this module validate data already supplied by a provider
boundary. They perform no discovery, I/O, provider invocation, persistence, or
state reduction.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Final

from .capacity_types import (
    CapacityValidationError,
    QuotaLaneKey,
    SourceKey,
)
from .provider_contracts import (
    AdapterIdentifier,
    CapabilityIdentifier,
    ContractValidationError,
    DiagnosticIdentifier,
    ProviderIdentifier,
    SchemaVersion,
    SourceInstanceIdentifier,
)

MAX_IDENTIFIER_LENGTH: Final = 64
MAX_WORK_FACTS_PER_BATCH: Final = 1_000
MAX_REQUEST_FACTS_PER_BATCH: Final = 1_000
MAX_DIAGNOSTICS_PER_BATCH: Final = 16
MAX_DIAGNOSTIC_COUNT: Final = 1_000

_KEY_SCHEMA_VERSION: Final = SchemaVersion(1, 0)
_VERSION_FIELDS: Final = frozenset({"major", "minor"})
_WORK_KEY_FIELDS: Final = frozenset(
    {
        "version",
        "provider_id",
        "adapter_id",
        "source_instance_id",
        "capability_id",
        "work_id",
    }
)
_REQUEST_KEY_FIELDS: Final = _WORK_KEY_FIELDS | {"request_id"}
_PRODUCT_PROVIDER_LABELS: Final = {
    "codex": "Codex",
    "claude": "Claude",
    "devin": "Devin",
    "grok": "Grok",
    "cursor": "Cursor",
    "hermes": "Hermes",
    "openclaw": "OpenClaw",
    "opencode": "OpenCode",
    "antigravity": "Antigravity",
}
_PRIVATE_IDENTIFIER_COMPONENT: Final = re.compile(
    r"(?:^|[._~:\-])"
    r"(?:api[_-]?key|authorization|bearer|cookie|credential|password|passwd|"
    r"private[_-]?key)"
    r"(?:$|[._~:\-])",
    re.IGNORECASE,
)


class ProviderFactValidationError(ValueError):
    """A provider fact failed closed at the pure domain boundary."""


def _is_finite_nonnegative_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0.0


def _validate_opaque_identifier(value: object) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_IDENTIFIER_LENGTH
        or _PRIVATE_IDENTIFIER_COMPONENT.search(value) is not None
    ):
        raise ProviderFactValidationError("invalid opaque identifier")
    try:
        SourceInstanceIdentifier(value)
    except ContractValidationError as error:
        raise ProviderFactValidationError("invalid opaque identifier") from error


def _validate_source_key(value: object) -> bool:
    if type(value) is not SourceKey:
        return False
    components = (
        value.provider_id,
        value.adapter_id,
        value.source_instance_id,
        value.capability_id,
    )
    if not all(type(component) is str for component in components):
        return False
    try:
        ProviderIdentifier(value.provider_id)
        AdapterIdentifier(value.adapter_id)
        SourceInstanceIdentifier(value.source_instance_id)
        CapabilityIdentifier(value.capability_id)
    except ContractValidationError:
        return False
    return True


@dataclass(frozen=True, order=True, slots=True)
class WorkIdentifier:
    value: str

    def __post_init__(self) -> None:
        _validate_opaque_identifier(self.value)


@dataclass(frozen=True, order=True, slots=True)
class RequestIdentifier:
    value: str

    def __post_init__(self) -> None:
        _validate_opaque_identifier(self.value)


@dataclass(frozen=True, order=True, slots=True)
class EventToken:
    value: str

    def __post_init__(self) -> None:
        _validate_opaque_identifier(self.value)


class WatermarkOrder(IntEnum):
    OLDER = -1
    EQUAL = 0
    NEWER = 1


@dataclass(frozen=True, order=True, slots=True)
class WorkKey:
    source_key: SourceKey
    work_id: WorkIdentifier

    def __post_init__(self) -> None:
        if not _validate_source_key(self.source_key) or type(self.work_id) is not WorkIdentifier:
            raise ProviderFactValidationError("invalid work key")


@dataclass(frozen=True, order=True, slots=True)
class RequestKey:
    work_key: WorkKey
    request_id: RequestIdentifier

    def __post_init__(self) -> None:
        if type(self.work_key) is not WorkKey or type(self.request_id) is not RequestIdentifier:
            raise ProviderFactValidationError("invalid request key")


class WatermarkBasis(str, Enum):
    PROVIDER_SEQUENCE = "provider_sequence"
    PROVIDER_EVENT_ID = "provider_event_id"
    OCCURRED_AT_TIE_BREAK = "occurred_at_tie_break"


@dataclass(frozen=True, slots=True)
class ProviderWatermark:
    source_key: SourceKey
    basis: WatermarkBasis
    occurred_at_epoch: float
    event_token: EventToken
    sequence: int | None
    tie_break_rank: int

    def __post_init__(self) -> None:
        valid_sequence = self.sequence is None or (
            type(self.sequence) is int and self.sequence >= 0
        )
        sequence_matches_basis = (
            self.sequence is not None
            if self.basis is WatermarkBasis.PROVIDER_SEQUENCE
            else self.sequence is None
        )
        if not (
            _validate_source_key(self.source_key)
            and type(self.basis) is WatermarkBasis
            and _is_finite_nonnegative_number(self.occurred_at_epoch)
            and type(self.event_token) is EventToken
            and valid_sequence
            and sequence_matches_basis
            and type(self.tie_break_rank) is int
            and 0 <= self.tie_break_rank <= 255
        ):
            raise ProviderFactValidationError("invalid provider watermark")
        object.__setattr__(self, "occurred_at_epoch", float(self.occurred_at_epoch))


def _watermark_order(left: object, right: object) -> WatermarkOrder:
    if left < right:  # type: ignore[operator]
        return WatermarkOrder.OLDER
    if left > right:  # type: ignore[operator]
        return WatermarkOrder.NEWER
    return WatermarkOrder.EQUAL


def compare_watermarks(
    left: ProviderWatermark,
    right: ProviderWatermark,
) -> WatermarkOrder:
    """Compare two validated watermarks from exactly one source."""
    if type(left) is not ProviderWatermark or type(right) is not ProviderWatermark:
        raise ProviderFactValidationError("invalid provider watermark")
    if left.source_key != right.source_key:
        raise ValueError("watermarks belong to different sources")
    if (
        left.basis is WatermarkBasis.PROVIDER_SEQUENCE
        and right.basis is WatermarkBasis.PROVIDER_SEQUENCE
    ):
        return _watermark_order(left.sequence, right.sequence)
    return _watermark_order(
        (left.occurred_at_epoch, left.tie_break_rank, left.event_token.value),
        (right.occurred_at_epoch, right.tie_break_rank, right.event_token.value),
    )


def _version_payload() -> dict[str, object]:
    return {"major": _KEY_SCHEMA_VERSION.major, "minor": _KEY_SCHEMA_VERSION.minor}


def work_key_to_payload(key: WorkKey) -> dict[str, object]:
    """Encode a work key into the exact built-in-dict v1 wire shape."""
    if type(key) is not WorkKey:
        raise ProviderFactValidationError("invalid work key")
    return {
        "version": _version_payload(),
        "provider_id": key.source_key.provider_id,
        "adapter_id": key.source_key.adapter_id,
        "source_instance_id": key.source_key.source_instance_id,
        "capability_id": key.source_key.capability_id,
        "work_id": key.work_id.value,
    }


def request_key_to_payload(key: RequestKey) -> dict[str, object]:
    """Encode a request key into the exact built-in-dict v1 wire shape."""
    if type(key) is not RequestKey:
        raise ProviderFactValidationError("invalid request key")
    return {
        **work_key_to_payload(key.work_key),
        "request_id": key.request_id.value,
    }


def _has_exact_fields(payload: dict[object, object], fields: frozenset[str]) -> bool:
    if len(payload) != len(fields):
        return False
    for key in payload:
        if type(key) is not str or key not in fields:
            return False
    return True


def _decode_key_components(
    payload: object,
    fields: frozenset[str],
) -> tuple[SourceKey, WorkIdentifier] | None:
    if type(payload) is not dict or not _has_exact_fields(payload, fields):
        return None

    version = payload["version"]
    if type(version) is not dict or not _has_exact_fields(version, _VERSION_FIELDS):
        return None
    major = version["major"]
    minor = version["minor"]
    if type(major) is not int or type(minor) is not int:
        return None
    try:
        if SchemaVersion(major, minor) != _KEY_SCHEMA_VERSION:
            return None
    except ContractValidationError:
        return None

    scalar_names = (
        "provider_id",
        "adapter_id",
        "source_instance_id",
        "capability_id",
        "work_id",
    )
    if not all(type(payload[name]) is str for name in scalar_names):
        return None
    try:
        provider_id = ProviderIdentifier(payload["provider_id"])
        adapter_id = AdapterIdentifier(payload["adapter_id"])
        source_instance_id = SourceInstanceIdentifier(payload["source_instance_id"])
        capability_id = CapabilityIdentifier(payload["capability_id"])
        work_id = WorkIdentifier(payload["work_id"])
        source_key = SourceKey(
            provider_id.value,
            adapter_id.value,
            source_instance_id.value,
            capability_id.value,
        )
        return source_key, work_id
    except (CapacityValidationError, ContractValidationError, ProviderFactValidationError):
        return None


def work_key_from_payload(payload: object) -> WorkKey | None:
    """Decode only the exact built-in-dict v1 work-key shape."""
    components = _decode_key_components(payload, _WORK_KEY_FIELDS)
    if components is None:
        return None
    source_key, work_id = components
    return WorkKey(source_key, work_id)


def request_key_from_payload(payload: object) -> RequestKey | None:
    """Decode only the exact built-in-dict v1 request-key shape."""
    components = _decode_key_components(payload, _REQUEST_KEY_FIELDS)
    if components is None or type(payload["request_id"]) is not str:  # type: ignore[index]
        return None
    source_key, work_id = components
    try:
        request_id = RequestIdentifier(payload["request_id"])  # type: ignore[index]
        return RequestKey(WorkKey(source_key, work_id), request_id)
    except ProviderFactValidationError:
        return None


class ObservationAuthority(IntEnum):
    UNTRUSTED_HINT = 0
    RESTORED_LAST_KNOWN = 1
    FALLBACK_OBSERVATION = 2
    DIRECT_PROVIDER_OBSERVATION = 3
    AUTHORITATIVE_PROVIDER = 4


class SourceHealth(str, Enum):
    HEALTHY = "healthy"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    AUTH_REQUIRED = "auth_required"
    ACCESS_DENIED = "access_denied"
    RATE_LIMITED = "rate_limited"
    TIMED_OUT = "timed_out"
    UNSUPPORTED = "unsupported"


class SourceFreshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    TIMING_UNCERTAIN = "timing_uncertain"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    RESTORED = "restored"


class WorkLifecycle(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ProviderTerminalCause(str, Enum):
    NONE = "none"
    CODEX_USAGE_LIMIT = "codex_usage_limit"


class NextActor(str, Enum):
    USER = "user"
    PROVIDER = "provider"
    NONE = "none"
    UNKNOWN = "unknown"


class ProviderRequestState(str, Enum):
    LIVE = "live"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class RequestKind(str, Enum):
    PERMISSION = "permission"
    INPUT = "input"
    APPROVAL = "approval"
    REVIEW = "review"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderFactDiagnostic:
    """Bounded product-owned diagnostic code and aggregate count."""

    identifier: DiagnosticIdentifier
    count: int = 1

    def __post_init__(self) -> None:
        if not (
            type(self.identifier) is DiagnosticIdentifier
            and type(self.count) is int
            and 1 <= self.count <= MAX_DIAGNOSTIC_COUNT
        ):
            raise ProviderFactValidationError("invalid diagnostic")


def _expected_safe_label(key: WorkKey) -> str:
    provider_label = _PRODUCT_PROVIDER_LABELS.get(key.source_key.provider_id, "Provider")
    return f"{provider_label} {key.work_id.value}"


@dataclass(frozen=True, slots=True)
class ProviderWorkFact:
    key: WorkKey
    lifecycle: WorkLifecycle
    watermark: ProviderWatermark
    safe_label: str
    parent_key: WorkKey | None
    next_actor: NextActor
    terminal_cause: ProviderTerminalCause = ProviderTerminalCause.NONE

    def __post_init__(self) -> None:
        if not (
            type(self.key) is WorkKey
            and type(self.lifecycle) is WorkLifecycle
            and type(self.watermark) is ProviderWatermark
            and self.watermark.source_key == self.key.source_key
            and type(self.next_actor) is NextActor
            and type(self.terminal_cause) is ProviderTerminalCause
        ):
            raise ProviderFactValidationError("invalid work fact")
        if type(self.safe_label) is not str or self.safe_label != _expected_safe_label(self.key):
            raise ProviderFactValidationError("invalid safe label")
        if self.parent_key is not None and (
            type(self.parent_key) is not WorkKey
            or self.parent_key.source_key != self.key.source_key
            or self.parent_key == self.key
        ):
            raise ProviderFactValidationError("invalid work parent")
        if self.terminal_cause is ProviderTerminalCause.CODEX_USAGE_LIMIT and not (
            self.key.source_key.provider_id == "codex"
            and self.lifecycle is WorkLifecycle.FAILED
            and self.next_actor is NextActor.NONE
        ):
            raise ProviderFactValidationError("invalid terminal cause")


@dataclass(frozen=True, slots=True)
class ProviderRequestFact:
    key: RequestKey
    state: ProviderRequestState
    request_kind: RequestKind
    next_actor: NextActor
    watermark: ProviderWatermark

    def __post_init__(self) -> None:
        if not (
            type(self.key) is RequestKey
            and type(self.state) is ProviderRequestState
            and type(self.request_kind) is RequestKind
            and type(self.next_actor) is NextActor
            and type(self.watermark) is ProviderWatermark
            and self.watermark.source_key == self.key.work_key.source_key
        ):
            raise ProviderFactValidationError("invalid request fact")


@dataclass(frozen=True, slots=True)
class ProviderFactBatch:
    source_key: SourceKey
    observation_authority: ObservationAuthority
    source_health: SourceHealth
    source_freshness: SourceFreshness
    observed_at_epoch: float
    watermark: ProviderWatermark
    work_facts: tuple[ProviderWorkFact, ...]
    request_facts: tuple[ProviderRequestFact, ...]
    diagnostics: tuple[ProviderFactDiagnostic, ...]

    def __post_init__(self) -> None:
        if not (
            _validate_source_key(self.source_key)
            and type(self.observation_authority) is ObservationAuthority
            and type(self.source_health) is SourceHealth
            and type(self.source_freshness) is SourceFreshness
            and _is_finite_nonnegative_number(self.observed_at_epoch)
            and type(self.watermark) is ProviderWatermark
            and self.watermark.source_key == self.source_key
        ):
            raise ProviderFactValidationError("invalid fact batch")
        object.__setattr__(self, "observed_at_epoch", float(self.observed_at_epoch))

        if type(self.work_facts) is not tuple or not all(
            type(fact) is ProviderWorkFact for fact in self.work_facts
        ):
            raise ProviderFactValidationError("invalid work facts")
        if len(self.work_facts) > MAX_WORK_FACTS_PER_BATCH:
            raise ProviderFactValidationError("too many work facts")
        if any(
            fact.key.source_key != self.source_key
            or fact.watermark.source_key != self.source_key
            or (
                fact.parent_key is not None
                and fact.parent_key.source_key != self.source_key
            )
            for fact in self.work_facts
        ):
            raise ProviderFactValidationError("cross-source work fact")
        work_keys = tuple(fact.key for fact in self.work_facts)
        if len(work_keys) != len(set(work_keys)):
            raise ProviderFactValidationError("duplicate work fact")

        if type(self.request_facts) is not tuple or not all(
            type(fact) is ProviderRequestFact for fact in self.request_facts
        ):
            raise ProviderFactValidationError("invalid request facts")
        if len(self.request_facts) > MAX_REQUEST_FACTS_PER_BATCH:
            raise ProviderFactValidationError("too many request facts")
        if any(
            fact.key.work_key.source_key != self.source_key
            or fact.watermark.source_key != self.source_key
            for fact in self.request_facts
        ):
            raise ProviderFactValidationError("cross-source request fact")
        request_keys = tuple(fact.key for fact in self.request_facts)
        if len(request_keys) != len(set(request_keys)):
            raise ProviderFactValidationError("duplicate request fact")

        if type(self.diagnostics) is not tuple or not all(
            type(diagnostic) is ProviderFactDiagnostic for diagnostic in self.diagnostics
        ):
            raise ProviderFactValidationError("invalid diagnostics")
        if len(self.diagnostics) > MAX_DIAGNOSTICS_PER_BATCH:
            raise ProviderFactValidationError("too many diagnostics")
        diagnostic_ids = tuple(diagnostic.identifier for diagnostic in self.diagnostics)
        if len(diagnostic_ids) != len(set(diagnostic_ids)):
            raise ProviderFactValidationError("duplicate diagnostic")

        object.__setattr__(
            self,
            "work_facts",
            tuple(sorted(self.work_facts, key=lambda fact: fact.key)),
        )
        object.__setattr__(
            self,
            "request_facts",
            tuple(sorted(self.request_facts, key=lambda fact: fact.key)),
        )
        object.__setattr__(
            self,
            "diagnostics",
            tuple(sorted(self.diagnostics, key=lambda diagnostic: diagnostic.identifier.value)),
        )


@dataclass(frozen=True, slots=True)
class ProviderQuotaWindow:
    lane_key: QuotaLaneKey
    used_percent: float | None
    window_minutes: int | None
    reset_epoch: float | None
    watermark: ProviderWatermark
    source_health: SourceHealth
    partial: bool

    def __post_init__(self) -> None:
        used_percent_valid = self.used_percent is None or (
            type(self.used_percent) in {int, float}
            and math.isfinite(self.used_percent)
            and 0.0 <= self.used_percent <= 100.0
        )
        window_valid = self.window_minutes is None or (
            type(self.window_minutes) is int and self.window_minutes > 0
        )
        reset_valid = self.reset_epoch is None or _is_finite_nonnegative_number(
            self.reset_epoch
        )
        if not (
            type(self.lane_key) is QuotaLaneKey
            and used_percent_valid
            and window_valid
            and reset_valid
            and type(self.watermark) is ProviderWatermark
            and self.lane_key.source == self.watermark.source_key
            and type(self.source_health) is SourceHealth
            and type(self.partial) is bool
        ):
            raise ProviderFactValidationError("invalid quota window")
        if self.used_percent is not None:
            object.__setattr__(self, "used_percent", float(self.used_percent))
        if self.reset_epoch is not None:
            object.__setattr__(self, "reset_epoch", float(self.reset_epoch))
