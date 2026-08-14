"""Pure adapters for explicitly supplied, already-supported quota evidence.

This module has no source discovery, scheduling, credential, filesystem, or
network authority. Static provider descriptors own lane identity and semantics;
the adapter only validates and normalizes bounded facts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .capacity_types import (
    MAX_LANES_PER_OBSERVATION,
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ObservationState,
    QuotaLaneKey,
    ResetFact,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from .provider_contracts import CapacitySourceDescriptor, ContractValidationError


class EvidenceMetricKind(str, Enum):
    PERCENT_REMAINING = "percent_remaining"
    PERCENT_USED = "percent_used"


@dataclass(frozen=True, slots=True)
class SupportedLaneEvidence:
    key: QuotaLaneKey
    metric_kind: EvidenceMetricKind
    percent: float | None
    state: ObservationState
    reset_state: ResetState
    reset_epoch: float | None
    window_minutes: float | None

    def __post_init__(self) -> None:
        if not (
            isinstance(self.key, QuotaLaneKey)
            and isinstance(self.metric_kind, EvidenceMetricKind)
            and isinstance(self.state, ObservationState)
            and isinstance(self.reset_state, ResetState)
        ):
            raise ValueError("invalid supported lane evidence")


@dataclass(frozen=True, slots=True)
class SupportedCapacityEvidence:
    source: SourceKey
    health_kind: SourceHealthKind
    lanes: tuple[SupportedLaneEvidence, ...]
    account_discriminator: str | None
    has_last_known_good: bool
    # The authentication mode the reading actually came through. An account
    # binding is only exact when it names one, so an adapter that knows it
    # says so here rather than leaving the contract unable to express it.
    auth_mode: str | None = None

    def __post_init__(self) -> None:
        if not (
            isinstance(self.source, SourceKey)
            and isinstance(self.health_kind, SourceHealthKind)
            and type(self.lanes) is tuple
            and all(isinstance(lane, SupportedLaneEvidence) for lane in self.lanes)
            and len(self.lanes) <= MAX_LANES_PER_OBSERVATION
            and type(self.has_last_known_good) is bool
            and (self.auth_mode is None or isinstance(self.auth_mode, str))
        ):
            raise ValueError("invalid supported capacity evidence")


@dataclass(frozen=True, slots=True)
class NormalizedCapacityEvidence:
    snapshot: CapacitySnapshot
    diagnostics: tuple[str, ...]


_HEALTH_REASON_CODES = {
    SourceHealthKind.HEALTHY: None,
    SourceHealthKind.REFRESHING: "source_refreshing",
    SourceHealthKind.COOLDOWN: "source_cooldown",
    SourceHealthKind.SIGN_IN_REQUIRED: "source_sign_in_required",
    SourceHealthKind.ACCESS_DENIED: "source_access_denied",
    SourceHealthKind.TIMED_OUT: "source_timed_out",
    SourceHealthKind.UNSUPPORTED: "source_unsupported",
    SourceHealthKind.PARTIAL: "source_partial",
    SourceHealthKind.FAILED: "source_failed",
    SourceHealthKind.STALE: "source_stale",
}


def _remaining_value(evidence: SupportedLaneEvidence) -> tuple[CapacityValue, bool]:
    percent = evidence.percent
    converted = False
    if percent is not None:
        if (
            isinstance(percent, bool)
            or not isinstance(percent, (int, float))
            or not math.isfinite(float(percent))
            or not 0.0 <= float(percent) <= 100.0
        ):
            raise ValueError("invalid capacity percent evidence")
        percent = float(percent)
        if evidence.metric_kind is EvidenceMetricKind.PERCENT_USED:
            percent = 100.0 - percent
            converted = True

    state = evidence.state
    if percent == 0.0 and state is ObservationState.OBSERVED:
        state = ObservationState.OBSERVED_ZERO
    return CapacityValue(CapacityUnit.PERCENT_REMAINING, percent, state), converted


def normalize_supported_quota_evidence(
    descriptor: CapacitySourceDescriptor,
    evidence: SupportedCapacityEvidence,
    *,
    observed_at: float,
) -> NormalizedCapacityEvidence:
    """Normalize one explicit supported-source delivery without performing I/O."""
    if not isinstance(descriptor, CapacitySourceDescriptor) or not isinstance(
        evidence, SupportedCapacityEvidence
    ):
        raise ValueError("invalid supported capacity source")
    if evidence.source != descriptor.source:
        raise ValueError("capacity evidence source does not match descriptor")

    keys = tuple(lane.key for lane in evidence.lanes)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate capacity lane evidence")

    health = CapacitySourceHealth(
        source=evidence.source,
        kind=evidence.health_kind,
        observed_at=observed_at,
        last_attempt_at=observed_at,
        retry_at=None,
        reason_code=_HEALTH_REASON_CODES[evidence.health_kind],
        has_last_known_good=evidence.has_last_known_good,
    )
    observations = []
    diagnostics: list[str] = []
    for lane_evidence in evidence.lanes:
        value, converted = _remaining_value(lane_evidence)
        reset = ResetFact(
            state=lane_evidence.reset_state,
            reset_epoch=lane_evidence.reset_epoch,
            window_minutes=lane_evidence.window_minutes,
            observed_at=observed_at,
        )
        try:
            observation = descriptor.build_observation(
                key=lane_evidence.key,
                value=value,
                reset=reset,
                observed_at=observed_at,
                source_health=health,
                account_discriminator=evidence.account_discriminator,
                auth_mode=evidence.auth_mode,
            )
        except ContractValidationError as exc:
            raise ValueError("capacity lane evidence is not declared") from exc
        observations.append(observation)
        if converted and "converted_percent_used" not in diagnostics:
            diagnostics.append("converted_percent_used")

    snapshot = CapacitySnapshot(
        observed_at=observed_at,
        lanes=tuple(observations),
        source_health=(health,),
    )
    return NormalizedCapacityEvidence(snapshot=snapshot, diagnostics=tuple(diagnostics))


__all__ = [
    "EvidenceMetricKind",
    "NormalizedCapacityEvidence",
    "SupportedCapacityEvidence",
    "SupportedLaneEvidence",
    "normalize_supported_quota_evidence",
]
