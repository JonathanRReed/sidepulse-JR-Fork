"""Quota-aware renewable leases for agent power holds."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

DEFAULT_QUIET_WINDOW_SECONDS = 45.0
MIN_QUIET_WINDOW_SECONDS = 30.0
MAX_QUIET_WINDOW_SECONDS = 60.0
DEFAULT_RECEIPT_LIMIT = 32


def _valid_identity(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128


def _timestamp(value: object) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ValueError("timestamp must be finite and nonnegative")
    return float(value)


@dataclass(frozen=True, slots=True)
class AgentPowerHoldKey:
    agent_id: str
    provider_id: str
    account_id: str

    def __post_init__(self) -> None:
        if not all(
            _valid_identity(value)
            for value in (self.agent_id, self.provider_id, self.account_id)
        ):
            raise ValueError("invalid power hold identity")


@dataclass(frozen=True, slots=True)
class CapacitySignal:
    observation_id: str
    provider_id: str
    account_id: str
    remaining_percent: float
    observed_at: float
    fresh: bool
    authoritative: bool
    applicable: bool
    bindable: bool

    def __post_init__(self) -> None:
        if not all(
            _valid_identity(value)
            for value in (self.observation_id, self.provider_id, self.account_id)
        ):
            raise ValueError("invalid capacity signal identity")
        if (
            type(self.remaining_percent) not in {int, float}
            or not math.isfinite(self.remaining_percent)
            or not 0.0 <= self.remaining_percent <= 100.0
            or any(
                type(value) is not bool
                for value in (
                    self.fresh,
                    self.authoritative,
                    self.applicable,
                    self.bindable,
                )
            )
        ):
            raise ValueError("invalid capacity signal")
        object.__setattr__(self, "remaining_percent", float(self.remaining_percent))
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at))


@dataclass(frozen=True, slots=True)
class PowerHoldReceipt:
    agent_id: str
    provider_id: str
    action: str
    reason: str
    recorded_at: float


@dataclass(slots=True)
class _Lease:
    holding: bool
    last_activity_at: float
    zero_observations: list[tuple[str, float]]
    latest_capacity_at: float | None = None
    release_after: float | None = None


class QuotaPowerHoldCoordinator:
    """Keep per-agent holds separate from provider lifecycle truth.

    A zero-capacity reading can stop one lease from renewing. It cannot close,
    interrupt, or otherwise mutate the work represented by that lease.
    """

    def __init__(
        self,
        *,
        quiet_window_seconds: float = DEFAULT_QUIET_WINDOW_SECONDS,
        receipt_limit: int = DEFAULT_RECEIPT_LIMIT,
    ) -> None:
        quiet = float(quiet_window_seconds)
        if not MIN_QUIET_WINDOW_SECONDS <= quiet <= MAX_QUIET_WINDOW_SECONDS:
            raise ValueError("quiet window must be between 30 and 60 seconds")
        if isinstance(receipt_limit, bool) or not isinstance(receipt_limit, int) or receipt_limit < 1:
            raise ValueError("receipt limit must be positive")
        self.quiet_window_seconds = quiet
        self._leases: dict[AgentPowerHoldKey, _Lease] = {}
        self._receipts: deque[PowerHoldReceipt] = deque(maxlen=receipt_limit)

    def _record(
        self,
        key: AgentPowerHoldKey,
        *,
        action: str,
        reason: str,
        now: float,
    ) -> PowerHoldReceipt:
        receipt = PowerHoldReceipt(
            agent_id=key.agent_id,
            provider_id=key.provider_id,
            action=action,
            reason=reason,
            recorded_at=now,
        )
        self._receipts.append(receipt)
        return receipt

    def renew(self, key: AgentPowerHoldKey, *, event_at: float) -> PowerHoldReceipt:
        when = _timestamp(event_at)
        lease = self._leases.get(key)
        if lease is None:
            self._leases[key] = _Lease(True, when, [])
            return self._record(
                key, action="renewed", reason="agent_activity", now=when
            )
        if when <= lease.last_activity_at:
            return self._record(
                key, action="ignored", reason="agent_event_not_newer", now=when
            )
        lease.holding = True
        lease.last_activity_at = when
        lease.zero_observations.clear()
        lease.release_after = None
        return self._record(
            key, action="renewed", reason="newer_agent_activity", now=when
        )

    def observe_capacity(
        self,
        key: AgentPowerHoldKey,
        signal: CapacitySignal,
        *,
        now: float,
    ) -> PowerHoldReceipt:
        reference = _timestamp(now)
        lease = self._leases.get(key)
        refusal = self._signal_refusal(key, signal, reference)
        if lease is None:
            refusal = refusal or "agent_lease_missing"
        if refusal is not None:
            return self._record(key, action="ignored", reason=refusal, now=reference)
        assert lease is not None

        if lease.latest_capacity_at is not None and signal.observed_at <= lease.latest_capacity_at:
            return self._record(
                key, action="ignored", reason="capacity_not_newer", now=reference
            )
        lease.latest_capacity_at = signal.observed_at

        if signal.remaining_percent > 0.0:
            lease.holding = True
            lease.zero_observations.clear()
            lease.release_after = None
            return self._record(
                key, action="renewed", reason="capacity_recovered", now=reference
            )

        if any(identifier == signal.observation_id for identifier, _ in lease.zero_observations):
            return self._record(
                key, action="ignored", reason="duplicate_zero_observation", now=reference
            )
        lease.zero_observations.append((signal.observation_id, signal.observed_at))
        lease.zero_observations[:] = lease.zero_observations[-2:]
        if len(lease.zero_observations) < 2:
            return self._record(
                key,
                action="retained",
                reason="zero_confirmation_required",
                now=reference,
            )

        lease.release_after = max(lease.last_activity_at, signal.observed_at) + self.quiet_window_seconds
        if reference < lease.release_after:
            return self._record(
                key, action="retained", reason="quiet_window_active", now=reference
            )
        return self._release_if_due(key, lease, reference)

    @staticmethod
    def _signal_refusal(
        key: AgentPowerHoldKey,
        signal: CapacitySignal,
        now: float,
    ) -> str | None:
        if signal.provider_id != key.provider_id:
            return "provider_binding_mismatch"
        if signal.account_id != key.account_id:
            return "account_binding_mismatch"
        if signal.observed_at > now:
            return "capacity_clock_uncertain"
        if not signal.fresh:
            return "capacity_stale"
        if not signal.authoritative:
            return "capacity_uncertain"
        if not signal.applicable:
            return "capacity_inapplicable"
        if not signal.bindable:
            return "capacity_unbindable"
        return None

    def _release_if_due(
        self,
        key: AgentPowerHoldKey,
        lease: _Lease,
        now: float,
    ) -> PowerHoldReceipt:
        if lease.holding and lease.release_after is not None and now >= lease.release_after:
            lease.holding = False
            return self._record(
                key,
                action="released",
                reason="confirmed_zero_after_quiet_window",
                now=now,
            )
        return self._record(
            key, action="retained", reason="lease_state_unchanged", now=now
        )

    def reconcile(self, *, now: float) -> tuple[PowerHoldReceipt, ...]:
        reference = _timestamp(now)
        changed = []
        for key, lease in self._leases.items():
            if lease.holding and lease.release_after is not None and reference >= lease.release_after:
                changed.append(self._release_if_due(key, lease, reference))
        return tuple(changed)

    def should_hold(self, key: AgentPowerHoldKey, *, now: float) -> bool:
        self.reconcile(now=now)
        lease = self._leases.get(key)
        return bool(lease is not None and lease.holding)

    def global_hold(self, *, now: float) -> bool:
        self.reconcile(now=now)
        return any(lease.holding for lease in self._leases.values())

    def receipts(self) -> tuple[PowerHoldReceipt, ...]:
        return tuple(self._receipts)


def quota_adjusted_work_mode(
    mode,
    *,
    statuses,
    bindings_by_work,
    capacity_by_provider,
    context,
    coordinator: QuotaPowerHoldCoordinator,
    now: float,
    evidence_class_by_source=None,
):
    """Apply bound quota leases before the legacy aggregate keep-awake mode.

    Unbound work keeps the original mode. This adapter only suppresses the
    aggregate work hold when every active main agent has an explicit account
    binding and its own lease has stopped renewing.
    """
    from .capacity_authority import evaluate_lane_authority
    from .capacity_types import (
        CapacityAccountBinding,
        CapacityEvidenceClass,
        ObservationState,
        QuotaEffect,
        SourceHealthKind,
    )
    from .models import AgentMode

    work_modes = {
        AgentMode.WORKING,
        AgentMode.TOOL_RUNNING,
        AgentMode.LONG_TASK_PROGRESS,
    }
    if mode not in work_modes:
        coordinator.reconcile(now=now)
        return mode

    evidence_classes = evidence_class_by_source or {}

    def unique_auto_binding(provider_id):
        snapshot = capacity_by_provider.get(provider_id)
        if snapshot is None:
            return None
        candidates = {}
        for lane in snapshot.lanes:
            evidence_class = evidence_classes.get(lane.key.source)
            if (
                lane.key.source.provider_id != provider_id
                or lane.key.effect is not QuotaEffect.ALL_WORKLOADS
                or lane.account_discriminator is None
                or lane.auth_mode is None
                or evidence_class
                not in {
                    CapacityEvidenceClass.OFFICIAL_LOCAL,
                    CapacityEvidenceClass.OFFICIAL_API,
                    CapacityEvidenceClass.OFFICIAL_ADMIN_API,
                }
                or lane.source_health.kind is not SourceHealthKind.HEALTHY
                or lane.value.state
                not in {ObservationState.OBSERVED, ObservationState.OBSERVED_ZERO}
                or lane.observed_at > now
                or now - lane.observed_at > 900.0
            ):
                continue
            binding = CapacityAccountBinding(
                source=lane.key.source,
                provider_id=provider_id,
                auth_mode=lane.auth_mode,
                opaque_account_id=lane.account_discriminator,
                pool_id=lane.key.pool,
                evidence_class=evidence_class,
                observed_at=lane.observed_at,
            )
            identity = (
                binding.source,
                binding.opaque_account_id,
                binding.pool_id,
                binding.auth_mode,
                binding.evidence_class,
            )
            candidates[identity] = binding
        return next(iter(candidates.values())) if len(candidates) == 1 else None

    auto_bindings = {}
    has_unbound_work = False
    for status in statuses:
        if getattr(status, "is_subagent", True) or getattr(status, "mode", None) not in work_modes:
            continue
        work_key = getattr(status, "work_key", None)
        binding = bindings_by_work.get(work_key)
        source = getattr(work_key, "source_key", None)
        if binding is None and source is not None:
            if source.provider_id not in auto_bindings:
                auto_bindings[source.provider_id] = unique_auto_binding(
                    source.provider_id
                )
            binding = auto_bindings[source.provider_id]
        if (
            binding is None
            or source is None
            or binding.provider_id != source.provider_id
        ):
            has_unbound_work = True
            continue

        key = AgentPowerHoldKey(
            getattr(status, "agent_id"),
            binding.provider_id,
            binding.opaque_account_id,
        )
        coordinator.renew(key, event_at=status.updated_at.timestamp())
        snapshot = capacity_by_provider.get(binding.provider_id)
        if snapshot is None:
            continue
        for lane in snapshot.lanes:
            if lane.key.source != binding.source or lane.key.pool != binding.pool_id:
                continue
            authority = evaluate_lane_authority(
                lane,
                context,
                now,
                binding=binding,
            )
            coordinator.observe_capacity(
                key,
                CapacitySignal(
                    observation_id=(
                        f"{lane.key.opaque_scope}:{lane.key.window}:{lane.observed_at}"
                    ),
                    provider_id=binding.provider_id,
                    account_id=binding.opaque_account_id,
                    remaining_percent=lane.value.remaining or 0.0,
                    observed_at=lane.observed_at,
                    fresh=authority.freshness
                    in {ObservationState.OBSERVED, ObservationState.OBSERVED_ZERO},
                    authoritative=authority.bindable,
                    applicable=authority.applicability.value == "applicable",
                    bindable=authority.bindable,
                ),
                now=now,
            )

    if has_unbound_work or coordinator.global_hold(now=now):
        return mode
    return AgentMode.IDLE_READY


__all__ = [
    "AgentPowerHoldKey",
    "CapacitySignal",
    "PowerHoldReceipt",
    "QuotaPowerHoldCoordinator",
    "quota_adjusted_work_mode",
]
