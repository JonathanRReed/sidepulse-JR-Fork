"""Pure applicability and compact authority for canonical capacity facts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from .capacity_types import (
    CapacityAccountBinding,
    CapacityEvidenceClass,
    CapacitySnapshot,
    CapacitySourceHealth,
    ExecutionContext,
    LaneApplicability,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneObservation,
    ResetState,
    SourceHealthKind,
)

MAX_BINDING_LANES = 2
MAX_CAPACITY_BINDING_AGE_SECONDS: Final = 900.0

_FRESH_STATES = {
    ObservationState.OBSERVED,
    ObservationState.OBSERVED_ZERO,
}
_FALLBACK_STATES = {
    ObservationState.STALE,
    ObservationState.LAST_KNOWN_GOOD,
}
_DIRECT_HEALTH_KINDS = {
    SourceHealthKind.HEALTHY,
    SourceHealthKind.REFRESHING,
    SourceHealthKind.COOLDOWN,
}


@dataclass(frozen=True, slots=True)
class LaneAuthority:
    """One canonical lane plus its context-specific authority decision."""

    lane: QuotaLaneObservation
    applicability: LaneApplicability
    bindable: bool
    reset_credible: bool
    freshness: ObservationState
    refusal_code: str | None

    @property
    def provider_name(self) -> str:
        """Return the bounded provider identity retained for display projection."""
        return self.lane.key.source.provider_id


@dataclass(frozen=True, slots=True)
class CapacityProjection:
    """At most two binding rows plus every bounded lane for detail display."""

    binding_lanes: tuple[LaneAuthority, ...]
    detail_lanes: tuple[LaneAuthority, ...]

    def __post_init__(self) -> None:
        if len(self.binding_lanes) > MAX_BINDING_LANES:
            raise ValueError("capacity projection accepts at most two binding lanes")


def _validated_now(now: float) -> float:
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise ValueError("now must be a finite nonnegative timestamp")
    value = float(now)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("now must be a finite nonnegative timestamp")
    return value


def _source_in_context(lane: QuotaLaneObservation, context: ExecutionContext) -> bool:
    source = lane.key.source
    return (
        source.provider_id in context.provider_ids
        and source.source_instance_id in context.source_instances
    )


def _applicability_decision(
    lane: QuotaLaneObservation,
    context: ExecutionContext,
) -> tuple[LaneApplicability, str | None]:
    if not _source_in_context(lane, context):
        return LaneApplicability.INAPPLICABLE, "source_out_of_context"

    effect = lane.key.effect
    if effect is QuotaEffect.ALL_WORKLOADS:
        return LaneApplicability.APPLICABLE, None
    if effect is QuotaEffect.UNKNOWN:
        return LaneApplicability.INAPPLICABLE, "unknown_effect"
    if effect is QuotaEffect.MODEL:
        if context.selected_model is None:
            return LaneApplicability.AMBIGUOUS, "model_unknown"
        if context.selected_model != lane.key.model:
            return LaneApplicability.INAPPLICABLE, "model_mismatch"
        return LaneApplicability.APPLICABLE, None
    if effect is QuotaEffect.FEATURE:
        if context.selected_feature is None:
            return LaneApplicability.AMBIGUOUS, "feature_unknown"
        if context.selected_feature != lane.key.opaque_scope:
            return LaneApplicability.INAPPLICABLE, "feature_mismatch"
        return LaneApplicability.APPLICABLE, None
    return LaneApplicability.INAPPLICABLE, "unknown_effect"


def classify_applicability(
    lane: QuotaLaneObservation,
    context: ExecutionContext,
) -> LaneApplicability:
    """Classify a lane only from canonical identity and execution context."""
    applicability, _reason = _applicability_decision(lane, context)
    return applicability


def _effective_freshness(lane: QuotaLaneObservation) -> ObservationState:
    state = lane.value.state
    health = lane.source_health
    if state in _FALLBACK_STATES:
        return state
    if health.kind is SourceHealthKind.STALE:
        return ObservationState.STALE
    if health.kind not in _DIRECT_HEALTH_KINDS and health.has_last_known_good:
        return ObservationState.LAST_KNOWN_GOOD
    return state


def _value_refusal(lane: QuotaLaneObservation) -> str | None:
    state = lane.value.state
    if state is ObservationState.NULL:
        return "usage_missing"
    if state is ObservationState.UNAVAILABLE:
        return "usage_unavailable"
    if state is ObservationState.PARTIAL or lane.source_health.kind is SourceHealthKind.PARTIAL:
        return "usage_partial"
    if state not in _FRESH_STATES | _FALLBACK_STATES:
        return "usage_invalid"
    if lane.value.remaining is None:
        return "usage_missing"

    health = lane.source_health
    if health.kind in _DIRECT_HEALTH_KINDS or health.kind is SourceHealthKind.STALE:
        return None
    if health.has_last_known_good:
        return None
    return f"source_{health.kind.value}"


def _binding_refusal(
    lane: QuotaLaneObservation,
    binding: CapacityAccountBinding | None,
    *,
    required: bool,
    now: float,
) -> str | None:
    if binding is None:
        return "account_binding_required" if required else None
    if binding.source != lane.key.source or binding.provider_id != lane.key.source.provider_id:
        return "source_binding_mismatch"
    if binding.pool_id != lane.key.pool:
        return "pool_binding_mismatch"
    if lane.account_discriminator != binding.opaque_account_id:
        return "account_binding_mismatch"
    if lane.auth_mode != binding.auth_mode:
        return "auth_mode_binding_mismatch"
    if binding.observed_at > now:
        return "binding_clock_uncertain"
    if now - binding.observed_at > MAX_CAPACITY_BINDING_AGE_SECONDS:
        return "binding_stale"
    if binding.evidence_class in {
        CapacityEvidenceClass.UI_LINK_ONLY,
        CapacityEvidenceClass.UNSUPPORTED,
    }:
        return "capacity_not_observable"
    return None


def evaluate_lane_authority(
    lane: QuotaLaneObservation,
    context: ExecutionContext,
    now: float,
    *,
    binding: CapacityAccountBinding | None = None,
    allow_unbound_legacy: bool = False,
) -> LaneAuthority:
    """Evaluate whether one already-normalized lane may bind right now."""
    reference = _validated_now(now)
    applicability, refusal = _applicability_decision(lane, context)
    if refusal is None:
        refusal = _binding_refusal(
            lane,
            binding,
            required=not allow_unbound_legacy,
            now=reference,
        ) or _value_refusal(lane)

    reset = lane.reset
    reset_credible = bool(
        reset.state is ResetState.FUTURE
        and reset.reset_epoch is not None
        and reset.reset_epoch > reference
    )
    return LaneAuthority(
        lane=lane,
        applicability=applicability,
        bindable=applicability is LaneApplicability.APPLICABLE and refusal is None,
        reset_credible=reset_credible,
        freshness=_effective_freshness(lane),
        refusal_code=refusal,
    )


def _selection_rank(authority: LaneAuthority) -> tuple[float, float, float, object]:
    lane = authority.lane
    is_fallback = authority.freshness in _FALLBACK_STATES
    remaining = lane.value.remaining
    reset_epoch = lane.reset.reset_epoch if authority.reset_credible else None
    return (
        1.0 if is_fallback else 0.0,
        remaining if remaining is not None else math.inf,
        reset_epoch if reset_epoch is not None else math.inf,
        lane.key,
    )


def select_binding_lanes(
    snapshot: CapacitySnapshot,
    context: ExecutionContext,
    now: float,
    limit: int = MAX_BINDING_LANES,
    *,
    bindings: tuple[CapacityAccountBinding, ...] | None = None,
    allow_unbound_legacy: bool = False,
) -> CapacityProjection:
    """Select a deterministic short/long compact projection without I/O."""
    _validated_now(now)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("binding lane limit must be a nonnegative integer")
    if limit > MAX_BINDING_LANES:
        raise ValueError("capacity projection accepts at most two binding lanes")
    if bindings is not None and (
        type(bindings) is not tuple
        or not all(type(binding) is CapacityAccountBinding for binding in bindings)
        or len(
            {
                (binding.source, binding.pool_id, binding.opaque_account_id)
                for binding in bindings
            }
        )
        != len(bindings)
    ):
        raise ValueError("invalid capacity account bindings")
    bindings_by_identity = (
        {
            (binding.source, binding.pool_id, binding.opaque_account_id): binding
            for binding in bindings
        }
        if bindings is not None
        else {}
    )
    
    detail = tuple(
        sorted(
            (
                evaluate_lane_authority(
                    lane,
                    context,
                    now,
                    binding=bindings_by_identity.get(
                        (lane.key.source, lane.key.pool, lane.account_discriminator)
                    ),
                    allow_unbound_legacy=allow_unbound_legacy,
                )
                for lane in snapshot.lanes
            ),
            key=lambda authority: authority.lane.key,
        )
    )
    candidates = tuple(sorted((row for row in detail if row.bindable), key=_selection_rank))
    if limit == 0 or not candidates:
        return CapacityProjection((), detail)

    selected: tuple[LaneAuthority, ...]
    short = tuple(row for row in candidates if row.lane.horizon is QuotaHorizon.SHORT)
    long = tuple(row for row in candidates if row.lane.horizon is QuotaHorizon.LONG)
    if limit == 2 and short and long:
        selected = (short[0], long[0])
    elif limit == 2:
        first = candidates[0]
        second = next(
            (
                row
                for row in candidates[1:]
                if row.lane.horizon is not first.lane.horizon
                or row.lane.key.source != first.lane.key.source
            ),
            None,
        )
        selected = (first,) if second is None else (first, second)
    else:
        selected = candidates[:limit]
    return CapacityProjection(selected, detail)


def project_source_health(
    snapshot: CapacitySnapshot,
    now: float,
) -> tuple[CapacitySourceHealth, ...]:
    """Return bounded source health in stable source-key order."""
    _validated_now(now)
    return tuple(sorted(snapshot.source_health, key=lambda health: health.source))


__all__ = [
    "MAX_BINDING_LANES",
    "MAX_CAPACITY_BINDING_AGE_SECONDS",
    "CapacityProjection",
    "LaneAuthority",
    "classify_applicability",
    "evaluate_lane_authority",
    "project_source_health",
    "select_binding_lanes",
]
