"""Immutable provider-neutral facts for capacity observations.

This module is deliberately pure. It validates already-supplied facts but does
not discover sources, perform I/O, refresh providers, retain history, or choose
which quota lanes bind in a presentation.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from functools import total_ordering
from typing import Final

MAX_IDENTITY_COMPONENT_LENGTH: Final = 64
MAX_SEMANTIC_NAME_LENGTH: Final = 64
MAX_REASON_CODE_LENGTH: Final = 64
MAX_ACCOUNT_DISCRIMINATOR_LENGTH: Final = 64
MAX_LANES_PER_OBSERVATION: Final = 32
MAX_SOURCE_HEALTH_FACTS: Final = 32
MAX_EXECUTION_CONTEXT_MEMBERS: Final = 32

_SLUG_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_OPAQUE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]*\Z")
_PRIVATE_IDENTIFIER_COMPONENT = re.compile(
    r"(?:^|[._~:-])"
    r"(?:api[_-]?key|authorization|bearer|cookie|credential|password|passwd|"
    r"private[_-]?key|secret|token)"
    r"(?:$|[._~:-])",
    re.IGNORECASE,
)


class CapacityValidationError(ValueError):
    """A capacity fact failed closed at the domain boundary."""


class CapacityUnit(str, Enum):
    PERCENT_REMAINING = "percent_remaining"


class CapacityEvidenceClass(str, Enum):
    OFFICIAL_LOCAL = "official_local"
    OFFICIAL_API = "official_api"
    OFFICIAL_ADMIN_API = "official_admin_api"
    UI_LINK_ONLY = "ui_link_only"
    UNSUPPORTED = "unsupported"


class QuotaEffect(str, Enum):
    ALL_WORKLOADS = "all_workloads"
    MODEL = "model"
    FEATURE = "feature"
    UNKNOWN = "unknown"


class QuotaHorizon(str, Enum):
    SHORT = "short"
    LONG = "long"
    OTHER = "other"


class LaneApplicability(str, Enum):
    APPLICABLE = "applicable"
    INAPPLICABLE = "inapplicable"
    AMBIGUOUS = "ambiguous"


class ObservationState(str, Enum):
    OBSERVED = "observed"
    OBSERVED_ZERO = "observed_zero"
    NULL = "null"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"
    STALE = "stale"
    LAST_KNOWN_GOOD = "last_known_good"


class ResetState(str, Enum):
    FUTURE = "future"
    DUE = "due"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    DISPUTED = "disputed"
    STALE = "stale"


class SourceHealthKind(str, Enum):
    HEALTHY = "healthy"
    REFRESHING = "refreshing"
    COOLDOWN = "cooldown"
    SIGN_IN_REQUIRED = "sign_in_required"
    ACCESS_DENIED = "access_denied"
    TIMED_OUT = "timed_out"
    UNSUPPORTED = "unsupported"
    PARTIAL = "partial"
    FAILED = "failed"
    STALE = "stale"


class SampleDisposition(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    IDENTITY_AMBIGUOUS = "identity_ambiguous"
    RESET_DISPUTED = "reset_disputed"
    SOURCE_PARTIAL = "source_partial"
    SOURCE_STALE = "source_stale"
    INVALID = "invalid"


class ForecastConfidence(str, Enum):
    UNAVAILABLE = "unavailable"
    LOW_LINEAR = "low_linear"
    MEDIUM_OBSERVED = "medium_observed"
    HIGH_HISTORICAL = "high_historical"


class ForecastReleaseState(str, Enum):
    WITHHELD = "withheld"
    AUTHORIZED = "authorized"
    REVOKED = "revoked"


def _is_finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _valid_identifier(value: object, *, opaque: bool = False) -> bool:
    pattern = _OPAQUE_IDENTIFIER if opaque else _SLUG_IDENTIFIER
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_IDENTITY_COMPONENT_LENGTH
        and pattern.fullmatch(value) is not None
    )


def _valid_optional_identifier(value: object, *, opaque: bool = False) -> bool:
    return value is None or _valid_identifier(value, opaque=opaque)


def is_safe_opaque_account_discriminator(value: object) -> bool:
    """Return whether an opaque account key is bounded and safe to retain."""
    return (
        _valid_identifier(value, opaque=True)
        and _PRIVATE_IDENTIFIER_COMPONENT.search(value) is None
    )


def _valid_timestamp(value: object) -> bool:
    return _is_finite_number(value) and value >= 0.0


@dataclass(frozen=True, order=True, slots=True)
class SourceKey:
    provider_id: str
    adapter_id: str
    source_instance_id: str
    capability_id: str

    def __post_init__(self) -> None:
        if not (
            _valid_identifier(self.provider_id)
            and _valid_identifier(self.adapter_id)
            and _valid_identifier(self.source_instance_id, opaque=True)
            and _valid_identifier(self.capability_id)
        ):
            raise CapacityValidationError("invalid source key")


@dataclass(frozen=True, slots=True)
class CapacityAccountBinding:
    """Exact non-display identity required to release account capacity."""

    source: SourceKey
    provider_id: str
    auth_mode: str
    opaque_account_id: str
    pool_id: str
    evidence_class: CapacityEvidenceClass
    observed_at: float

    def __post_init__(self) -> None:
        if not (
            isinstance(self.source, SourceKey)
            and _valid_identifier(self.provider_id)
            and self.provider_id == self.source.provider_id
            and _valid_identifier(self.auth_mode)
            and is_safe_opaque_account_discriminator(self.opaque_account_id)
            and _valid_identifier(self.pool_id)
            and isinstance(self.evidence_class, CapacityEvidenceClass)
            and _valid_timestamp(self.observed_at)
        ):
            raise CapacityValidationError("invalid capacity account binding")
        object.__setattr__(self, "observed_at", float(self.observed_at))


@total_ordering
@dataclass(frozen=True, slots=True)
class QuotaLaneKey:
    """Stable quota identity with no display or inferred-duration fields.

    For ``FEATURE`` lanes, ``opaque_scope`` is the exact feature discriminator
    matched by the authority layer against ``ExecutionContext.selected_feature``.
    ``model`` is reserved exclusively for ``MODEL`` lanes.
    """

    source: SourceKey
    opaque_scope: str
    pool: str
    model: str | None
    window: str
    effect: QuotaEffect

    def __post_init__(self) -> None:
        if not (
            isinstance(self.source, SourceKey)
            and _valid_identifier(self.opaque_scope, opaque=True)
            and _valid_identifier(self.pool)
            and _valid_optional_identifier(self.model, opaque=True)
            and _valid_identifier(self.window)
            and isinstance(self.effect, QuotaEffect)
        ):
            raise CapacityValidationError("invalid quota lane key")
        if self.effect is QuotaEffect.MODEL and self.model is None:
            raise CapacityValidationError("invalid quota lane key")
        if self.effect is not QuotaEffect.MODEL and self.model is not None:
            raise CapacityValidationError("invalid quota lane key")

    def _ordering_key(self) -> tuple[SourceKey, str, str, str, str, str]:
        return (
            self.source,
            self.opaque_scope,
            self.pool,
            self.model or "",
            self.window,
            self.effect.value,
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, QuotaLaneKey):
            return NotImplemented
        return self._ordering_key() < other._ordering_key()


@dataclass(frozen=True, slots=True)
class CapacityValue:
    unit: CapacityUnit
    remaining: float | None
    state: ObservationState

    def __post_init__(self) -> None:
        if not isinstance(self.unit, CapacityUnit) or not isinstance(
            self.state, ObservationState
        ):
            raise CapacityValidationError("invalid remaining capacity state")

        if self.remaining is not None:
            if not _is_finite_number(self.remaining) or not 0.0 <= self.remaining <= 100.0:
                raise CapacityValidationError("invalid remaining capacity")
            object.__setattr__(self, "remaining", float(self.remaining))

        if self.state is ObservationState.OBSERVED_ZERO:
            if self.remaining is None:
                raise CapacityValidationError("invalid remaining capacity state")
            if self.remaining != 0.0:
                raise CapacityValidationError("invalid observed zero state")
            return
        if self.remaining == 0.0:
            raise CapacityValidationError("invalid observed zero state")

        value_required = {
            ObservationState.OBSERVED,
            ObservationState.STALE,
            ObservationState.LAST_KNOWN_GOOD,
        }
        value_forbidden = {ObservationState.NULL, ObservationState.UNAVAILABLE}
        if self.state in value_required and self.remaining is None:
            raise CapacityValidationError("invalid remaining capacity state")
        if self.state in value_forbidden and self.remaining is not None:
            raise CapacityValidationError("invalid remaining capacity state")


@dataclass(frozen=True, slots=True)
class ResetFact:
    state: ResetState
    reset_epoch: float | None
    window_minutes: float | None
    observed_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.state, ResetState) or not _valid_timestamp(self.observed_at):
            raise CapacityValidationError("invalid reset fact")
        object.__setattr__(self, "observed_at", float(self.observed_at))

        if self.reset_epoch is not None:
            if not _valid_timestamp(self.reset_epoch):
                raise CapacityValidationError("invalid reset fact")
            object.__setattr__(self, "reset_epoch", float(self.reset_epoch))
        if self.window_minutes is not None:
            if not _is_finite_number(self.window_minutes) or self.window_minutes <= 0.0:
                raise CapacityValidationError("invalid reset fact")
            object.__setattr__(self, "window_minutes", float(self.window_minutes))

        if self.state is ResetState.FUTURE and (
            self.reset_epoch is None or self.reset_epoch <= self.observed_at
        ):
            raise CapacityValidationError("invalid reset fact")
        if self.state is ResetState.DUE and (
            self.reset_epoch is None or self.reset_epoch > self.observed_at
        ):
            raise CapacityValidationError("invalid reset fact")
        if self.state in {ResetState.UNKNOWN, ResetState.UNAVAILABLE} and self.reset_epoch is not None:
            raise CapacityValidationError("invalid reset fact")
        if self.state is ResetState.STALE and self.reset_epoch is None:
            raise CapacityValidationError("invalid reset fact")


@dataclass(frozen=True, slots=True)
class CapacitySourceHealth:
    source: SourceKey
    kind: SourceHealthKind
    observed_at: float
    last_attempt_at: float | None
    retry_at: float | None
    reason_code: str | None
    has_last_known_good: bool

    def __post_init__(self) -> None:
        if not (
            isinstance(self.source, SourceKey)
            and isinstance(self.kind, SourceHealthKind)
            and _valid_timestamp(self.observed_at)
            and (self.last_attempt_at is None or _valid_timestamp(self.last_attempt_at))
            and (self.retry_at is None or _valid_timestamp(self.retry_at))
            and type(self.has_last_known_good) is bool
        ):
            raise CapacityValidationError("invalid source health")
        if self.reason_code is not None and not (
            _valid_identifier(self.reason_code)
            and len(self.reason_code) <= MAX_REASON_CODE_LENGTH
        ):
            raise CapacityValidationError("invalid source health reason code")
        object.__setattr__(self, "observed_at", float(self.observed_at))
        if self.last_attempt_at is not None:
            object.__setattr__(self, "last_attempt_at", float(self.last_attempt_at))
        if self.retry_at is not None:
            object.__setattr__(self, "retry_at", float(self.retry_at))


@dataclass(frozen=True, slots=True)
class QuotaLaneObservation:
    key: QuotaLaneKey
    semantic_name: str
    horizon: QuotaHorizon
    value: CapacityValue
    reset: ResetFact
    observed_at: float
    source_health: CapacitySourceHealth
    account_discriminator: str | None
    auth_mode: str | None = None

    def __post_init__(self) -> None:
        if not (
            isinstance(self.key, QuotaLaneKey)
            and isinstance(self.semantic_name, str)
            and 1 <= len(self.semantic_name) <= MAX_SEMANTIC_NAME_LENGTH
            and self.semantic_name == self.semantic_name.strip()
            and self.semantic_name.isprintable()
            and isinstance(self.horizon, QuotaHorizon)
            and isinstance(self.value, CapacityValue)
            and isinstance(self.reset, ResetFact)
            and _valid_timestamp(self.observed_at)
            and isinstance(self.source_health, CapacitySourceHealth)
        ):
            raise CapacityValidationError("invalid quota lane observation")
        if self.source_health.source != self.key.source:
            raise CapacityValidationError("invalid observation source")
        if self.account_discriminator is not None and not is_safe_opaque_account_discriminator(
            self.account_discriminator
        ):
            raise CapacityValidationError("invalid account discriminator")
        if self.auth_mode is not None and not _valid_identifier(self.auth_mode):
            raise CapacityValidationError("invalid quota lane observation")
        object.__setattr__(self, "observed_at", float(self.observed_at))


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    observed_at: float
    lanes: tuple[QuotaLaneObservation, ...]
    source_health: tuple[CapacitySourceHealth, ...]

    def __post_init__(self) -> None:
        if not (
            _valid_timestamp(self.observed_at)
            and type(self.lanes) is tuple
            and type(self.source_health) is tuple
            and all(isinstance(lane, QuotaLaneObservation) for lane in self.lanes)
            and all(isinstance(health, CapacitySourceHealth) for health in self.source_health)
            and len(self.source_health) <= MAX_SOURCE_HEALTH_FACTS
        ):
            raise CapacityValidationError("invalid capacity snapshot")
        if len(self.lanes) > MAX_LANES_PER_OBSERVATION:
            raise CapacityValidationError("too many quota lanes")
        lane_keys = tuple(lane.key for lane in self.lanes)
        if len(lane_keys) != len(set(lane_keys)):
            raise CapacityValidationError("duplicate quota lane")
        health_sources = tuple(health.source for health in self.source_health)
        if len(health_sources) != len(set(health_sources)):
            raise CapacityValidationError("duplicate source health")
        object.__setattr__(self, "observed_at", float(self.observed_at))


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    provider_ids: tuple[str, ...]
    source_instances: tuple[str, ...]
    selected_model: str | None
    selected_feature: str | None

    def __post_init__(self) -> None:
        if not (
            type(self.provider_ids) is tuple
            and type(self.source_instances) is tuple
            and len(self.provider_ids) <= MAX_EXECUTION_CONTEXT_MEMBERS
            and len(self.source_instances) <= MAX_EXECUTION_CONTEXT_MEMBERS
            and all(_valid_identifier(provider_id) for provider_id in self.provider_ids)
            and all(
                _valid_identifier(source_instance, opaque=True)
                for source_instance in self.source_instances
            )
            and _valid_optional_identifier(self.selected_model, opaque=True)
            and _valid_optional_identifier(self.selected_feature, opaque=True)
            and len(self.provider_ids) == len(set(self.provider_ids))
            and len(self.source_instances) == len(set(self.source_instances))
        ):
            raise CapacityValidationError("invalid execution context")


__all__ = [
    "MAX_ACCOUNT_DISCRIMINATOR_LENGTH",
    "MAX_EXECUTION_CONTEXT_MEMBERS",
    "MAX_IDENTITY_COMPONENT_LENGTH",
    "MAX_LANES_PER_OBSERVATION",
    "MAX_REASON_CODE_LENGTH",
    "MAX_SEMANTIC_NAME_LENGTH",
    "MAX_SOURCE_HEALTH_FACTS",
    "CapacityAccountBinding",
    "CapacityEvidenceClass",
    "CapacitySnapshot",
    "CapacitySourceHealth",
    "CapacityUnit",
    "CapacityValidationError",
    "CapacityValue",
    "ExecutionContext",
    "ForecastConfidence",
    "ForecastReleaseState",
    "LaneApplicability",
    "ObservationState",
    "QuotaEffect",
    "QuotaHorizon",
    "QuotaLaneKey",
    "QuotaLaneObservation",
    "ResetFact",
    "ResetState",
    "SampleDisposition",
    "SourceHealthKind",
    "SourceKey",
    "is_safe_opaque_account_discriminator",
]
