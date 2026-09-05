from __future__ import annotations

from dataclasses import replace

from sidepulse.capacity_types import (
    CapacityAccountBinding,
    CapacityEvidenceClass,
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ExecutionContext,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.models import AgentMode
from sidepulse.provider_facts import WorkIdentifier, WorkKey
from sidepulse.quota_power_hold import (
    AgentPowerHoldKey,
    CapacitySignal,
    QuotaPowerHoldCoordinator,
    quota_adjusted_work_mode,
)

CODEX = AgentPowerHoldKey("agent-1", "codex", "account-a")


def signal(
    observation_id: str,
    observed_at: float,
    *,
    provider_id: str = "codex",
    account_id: str = "account-a",
    remaining: float = 0.0,
    fresh: bool = True,
    authoritative: bool = True,
    applicable: bool = True,
    bindable: bool = True,
) -> CapacitySignal:
    return CapacitySignal(
        observation_id=observation_id,
        provider_id=provider_id,
        account_id=account_id,
        remaining_percent=remaining,
        observed_at=observed_at,
        fresh=fresh,
        authoritative=authoritative,
        applicable=applicable,
        bindable=bindable,
    )


def coordinator() -> QuotaPowerHoldCoordinator:
    result = QuotaPowerHoldCoordinator(quiet_window_seconds=45.0)
    result.renew(CODEX, event_at=100.0)
    return result


def test_first_zero_observation_keeps_agent_lease() -> None:
    holds = coordinator()

    receipt = holds.observe_capacity(CODEX, signal("zero-1", 110.0), now=110.0)

    assert holds.should_hold(CODEX, now=200.0)
    assert holds.global_hold(now=200.0)
    assert receipt.action == "retained"
    assert receipt.reason == "zero_confirmation_required"


def test_second_zero_before_quiet_window_keeps_agent_lease() -> None:
    holds = coordinator()
    holds.observe_capacity(CODEX, signal("zero-1", 110.0), now=110.0)

    receipt = holds.observe_capacity(CODEX, signal("zero-2", 120.0), now=120.0)

    assert holds.should_hold(CODEX, now=164.9)
    assert receipt.reason == "quiet_window_active"


def test_two_zero_observations_release_after_quiet_window() -> None:
    holds = coordinator()
    holds.observe_capacity(CODEX, signal("zero-1", 110.0), now=110.0)
    holds.observe_capacity(CODEX, signal("zero-2", 120.0), now=120.0)

    receipt = holds.reconcile(now=165.0)

    assert not holds.should_hold(CODEX, now=165.0)
    assert not holds.global_hold(now=165.0)
    assert receipt[-1].action == "released"
    assert receipt[-1].reason == "confirmed_zero_after_quiet_window"


def test_newer_agent_activity_restores_and_renews_hold() -> None:
    holds = coordinator()
    holds.observe_capacity(CODEX, signal("zero-1", 110.0), now=110.0)
    holds.observe_capacity(CODEX, signal("zero-2", 120.0), now=120.0)
    holds.reconcile(now=165.0)

    receipt = holds.renew(CODEX, event_at=166.0)

    assert holds.should_hold(CODEX, now=300.0)
    assert receipt.action == "renewed"
    assert receipt.reason == "newer_agent_activity"


def test_capacity_recovery_restores_hold() -> None:
    holds = coordinator()
    holds.observe_capacity(CODEX, signal("zero-1", 110.0), now=110.0)
    holds.observe_capacity(CODEX, signal("zero-2", 120.0), now=120.0)
    holds.reconcile(now=165.0)

    receipt = holds.observe_capacity(
        CODEX,
        signal("recovered", 170.0, remaining=25.0),
        now=170.0,
    )

    assert holds.should_hold(CODEX, now=300.0)
    assert receipt.action == "renewed"
    assert receipt.reason == "capacity_recovered"


def test_other_agent_retains_global_hold() -> None:
    holds = coordinator()
    claude = AgentPowerHoldKey("agent-2", "claude", "account-b")
    holds.renew(claude, event_at=105.0)
    holds.observe_capacity(CODEX, signal("zero-1", 110.0), now=110.0)
    holds.observe_capacity(CODEX, signal("zero-2", 120.0), now=120.0)
    holds.reconcile(now=165.0)

    assert not holds.should_hold(CODEX, now=165.0)
    assert holds.should_hold(claude, now=165.0)
    assert holds.global_hold(now=165.0)


def test_stale_uncertain_cross_account_and_unbindable_evidence_is_ignored() -> None:
    cases = (
        signal("stale", 110.0, fresh=False),
        signal("uncertain", 111.0, authoritative=False),
        signal("inapplicable", 112.0, applicable=False),
        signal("cross-account", 113.0, account_id="account-b"),
        signal("cross-provider", 114.0, provider_id="claude"),
        signal("unbound", 115.0, bindable=False),
    )

    for evidence in cases:
        holds = coordinator()
        first = holds.observe_capacity(CODEX, evidence, now=evidence.observed_at)
        second = holds.observe_capacity(
            CODEX,
            signal("valid-zero", evidence.observed_at + 1.0),
            now=evidence.observed_at + 1.0,
        )
        holds.reconcile(now=300.0)
        assert holds.should_hold(CODEX, now=300.0), evidence.observation_id
        assert first.action == "ignored"
        assert second.reason == "zero_confirmation_required"


def test_duplicate_or_out_of_order_zero_is_not_a_second_confirmation() -> None:
    holds = coordinator()
    holds.observe_capacity(CODEX, signal("same", 110.0), now=110.0)
    holds.observe_capacity(CODEX, signal("same", 111.0), now=111.0)
    holds.observe_capacity(CODEX, signal("older", 109.0), now=112.0)

    holds.reconcile(now=300.0)

    assert holds.should_hold(CODEX, now=300.0)


def test_quota_release_never_fabricates_completion_or_termination() -> None:
    holds = coordinator()
    holds.observe_capacity(CODEX, signal("zero-1", 110.0), now=110.0)
    holds.observe_capacity(CODEX, signal("zero-2", 120.0), now=120.0)

    receipts = holds.reconcile(now=165.0)

    assert not hasattr(holds, "complete")
    assert not hasattr(holds, "terminate")
    assert not hasattr(holds, "interrupt")
    assert all("complet" not in receipt.reason for receipt in receipts)
    assert all("terminat" not in receipt.reason for receipt in receipts)


def test_receipts_are_bounded() -> None:
    holds = QuotaPowerHoldCoordinator(quiet_window_seconds=45.0, receipt_limit=4)
    for index in range(10):
        key = AgentPowerHoldKey(f"agent-{index}", "codex", "account-a")
        holds.renew(key, event_at=float(index))

    assert len(holds.receipts()) == 4


def _runtime_capacity(observed_at: float, remaining: float) -> tuple[CapacitySnapshot, ExecutionContext, CapacityAccountBinding]:
    source = SourceKey("codex", "quota", "local", "remote_quota_windows")
    health = CapacitySourceHealth(
        source,
        SourceHealthKind.HEALTHY,
        observed_at,
        observed_at,
        None,
        None,
        False,
    )
    state = ObservationState.OBSERVED_ZERO if remaining == 0 else ObservationState.OBSERVED
    lane = QuotaLaneObservation(
        QuotaLaneKey(source, "all", "plan", None, "session", QuotaEffect.ALL_WORKLOADS),
        "Session",
        QuotaHorizon.SHORT,
        CapacityValue(CapacityUnit.PERCENT_REMAINING, remaining, state),
        ResetFact(ResetState.FUTURE, 1_000.0, 100.0, observed_at),
        observed_at,
        health,
        "account-a",
        "local_session",
    )
    snapshot = CapacitySnapshot(observed_at, (lane,), (health,))
    context = ExecutionContext(("codex",), ("local",), None, None, source_keys=(source,))
    binding = CapacityAccountBinding(
        source,
        "codex",
        "local_session",
        "account-a",
        "plan",
        CapacityEvidenceClass.OFFICIAL_LOCAL,
        100.0,
    )
    return snapshot, context, binding


def test_production_mode_releases_only_the_explicitly_bound_quota_exhausted_work() -> None:
    event_source = SourceKey("codex", "hooks", "local", "live_agent_events")
    work_key = WorkKey(event_source, WorkIdentifier("session-1"))
    status = type("Status", (), {})()
    status.agent_id = "codex:session:session-1"
    status.provider = "codex"
    status.work_key = work_key
    status.mode = AgentMode.WORKING
    status.is_subagent = False
    status.updated_at = type("Updated", (), {"timestamp": lambda self: 100.0})()
    holds = QuotaPowerHoldCoordinator()
    first, context, binding = _runtime_capacity(110.0, 0.0)

    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        statuses=(status,),
        bindings_by_work={work_key: binding},
        capacity_by_provider={"codex": first},
        context=context,
        coordinator=holds,
        now=110.0,
    ) is AgentMode.WORKING
    second, _, _ = _runtime_capacity(120.0, 0.0)
    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        statuses=(status,),
        bindings_by_work={work_key: binding},
        capacity_by_provider={"codex": second},
        context=context,
        coordinator=holds,
        now=164.9,
    ) is AgentMode.WORKING
    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        statuses=(status,),
        bindings_by_work={work_key: binding},
        capacity_by_provider={"codex": second},
        context=context,
        coordinator=holds,
        now=165.0,
    ) is AgentMode.IDLE_READY

    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        statuses=(status,),
        bindings_by_work={},
        capacity_by_provider={"codex": second},
        context=context,
        coordinator=QuotaPowerHoldCoordinator(),
        now=300.0,
    ) is AgentMode.WORKING


def test_two_zero_lanes_from_one_refresh_count_as_one_observation() -> None:
    event_source = SourceKey("codex", "hooks", "local", "live_agent_events")
    work_key = WorkKey(event_source, WorkIdentifier("session-1"))
    status = type("Status", (), {})()
    status.agent_id = "codex:session:session-1"
    status.provider = "codex"
    status.work_key = work_key
    status.mode = AgentMode.WORKING
    status.is_subagent = False
    status.updated_at = type("Updated", (), {"timestamp": lambda self: 100.0})()
    one_lane, context, binding = _runtime_capacity(110.0, 0.0)
    second_lane = replace(
        one_lane.lanes[0],
        key=replace(one_lane.lanes[0].key, window="weekly"),
        semantic_name="Weekly",
        horizon=QuotaHorizon.LONG,
    )
    same_refresh = replace(one_lane, lanes=(*one_lane.lanes, second_lane))
    holds = QuotaPowerHoldCoordinator()

    result = quota_adjusted_work_mode(
        AgentMode.WORKING,
        statuses=(status,),
        bindings_by_work={work_key: binding},
        capacity_by_provider={"codex": same_refresh},
        context=context,
        coordinator=holds,
        now=300.0,
    )

    assert result is AgentMode.WORKING


def test_unique_authoritative_all_workloads_account_auto_binds_across_source_instances() -> None:
    event_source = SourceKey("codex", "hooks", "global", "live_agent_events")
    work_key = WorkKey(event_source, WorkIdentifier("session-1"))
    status = type("Status", (), {})()
    status.agent_id = "codex:session:session-1"
    status.provider = "codex"
    status.work_key = work_key
    status.mode = AgentMode.WORKING
    status.is_subagent = False
    status.updated_at = type("Updated", (), {"timestamp": lambda self: 100.0})()
    first, context, _binding = _runtime_capacity(110.0, 0.0)
    source = first.lanes[0].key.source
    holds = QuotaPowerHoldCoordinator()

    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        statuses=(status,),
        bindings_by_work={},
        capacity_by_provider={"codex": first},
        evidence_class_by_source={source: CapacityEvidenceClass.OFFICIAL_LOCAL},
        context=context,
        coordinator=holds,
        now=110.0,
    ) is AgentMode.WORKING
    second, _, _ = _runtime_capacity(120.0, 0.0)
    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        statuses=(status,),
        bindings_by_work={},
        capacity_by_provider={"codex": second},
        evidence_class_by_source={source: CapacityEvidenceClass.OFFICIAL_LOCAL},
        context=context,
        coordinator=holds,
        now=165.0,
    ) is AgentMode.IDLE_READY


def test_unique_provider_account_can_release_multiple_active_sessions() -> None:
    event_source = SourceKey("codex", "hooks", "global", "live_agent_events")
    statuses = []
    for index in (1, 2):
        work_key = WorkKey(event_source, WorkIdentifier(f"session-{index}"))
        status = type("Status", (), {})()
        status.agent_id = f"codex:session:session-{index}"
        status.provider = "codex"
        status.work_key = work_key
        status.mode = AgentMode.WORKING
        status.is_subagent = False
        status.updated_at = type("Updated", (), {"timestamp": lambda self: 100.0})()
        statuses.append(status)
    first, context, _binding = _runtime_capacity(110.0, 0.0)
    source = first.lanes[0].key.source
    holds = QuotaPowerHoldCoordinator()
    common = {
        "statuses": tuple(statuses),
        "bindings_by_work": {},
        "evidence_class_by_source": {source: CapacityEvidenceClass.OFFICIAL_LOCAL},
        "context": context,
        "coordinator": holds,
    }

    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        capacity_by_provider={"codex": first},
        now=110.0,
        **common,
    ) is AgentMode.WORKING
    second, _, _ = _runtime_capacity(120.0, 0.0)
    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        capacity_by_provider={"codex": second},
        now=165.0,
        **common,
    ) is AgentMode.IDLE_READY


def test_auto_binding_refuses_ambiguous_accounts_and_non_global_or_untrusted_capacity() -> None:
    event_source = SourceKey("codex", "hooks", "global", "live_agent_events")
    work_key = WorkKey(event_source, WorkIdentifier("session-1"))
    status = type("Status", (), {})()
    status.agent_id = "codex:session:session-1"
    status.provider = "codex"
    status.work_key = work_key
    status.mode = AgentMode.WORKING
    status.is_subagent = False
    status.updated_at = type("Updated", (), {"timestamp": lambda self: 100.0})()
    snapshot, context, _binding = _runtime_capacity(110.0, 0.0)
    lane = snapshot.lanes[0]
    source = lane.key.source
    other_account = replace(
        lane,
        key=replace(lane.key, pool="other"),
        account_discriminator="account-b",
    )
    ambiguous = replace(snapshot, lanes=(lane, other_account))
    model_only = replace(
        snapshot,
        lanes=(
            replace(
                lane,
                key=replace(
                    lane.key,
                    effect=QuotaEffect.MODEL,
                    model="gpt-5",
                ),
            ),
        ),
    )
    cases = (
        (ambiguous, CapacityEvidenceClass.OFFICIAL_LOCAL),
        (model_only, CapacityEvidenceClass.OFFICIAL_LOCAL),
        (snapshot, CapacityEvidenceClass.UI_LINK_ONLY),
        (replace(snapshot, lanes=(replace(lane, account_discriminator=None),)), CapacityEvidenceClass.OFFICIAL_LOCAL),
    )

    for candidate, evidence_class in cases:
        assert quota_adjusted_work_mode(
            AgentMode.WORKING,
            statuses=(status,),
            bindings_by_work={},
            capacity_by_provider={"codex": candidate},
            evidence_class_by_source={source: evidence_class},
            context=context,
            coordinator=QuotaPowerHoldCoordinator(),
            now=300.0,
        ) is AgentMode.WORKING


def test_explicit_work_binding_overrides_ambiguous_provider_accounts() -> None:
    event_source = SourceKey("codex", "hooks", "global", "live_agent_events")
    work_key = WorkKey(event_source, WorkIdentifier("session-1"))
    status = type("Status", (), {})()
    status.agent_id = "codex:session:session-1"
    status.provider = "codex"
    status.work_key = work_key
    status.mode = AgentMode.WORKING
    status.is_subagent = False
    status.updated_at = type("Updated", (), {"timestamp": lambda self: 100.0})()
    first, context, binding = _runtime_capacity(110.0, 0.0)
    other = replace(
        first.lanes[0],
        key=replace(first.lanes[0].key, pool="other"),
        account_discriminator="account-b",
    )
    first = replace(first, lanes=(*first.lanes, other))
    source = first.lanes[0].key.source
    holds = QuotaPowerHoldCoordinator()
    common = {
        "statuses": (status,),
        "bindings_by_work": {work_key: binding},
        "evidence_class_by_source": {source: CapacityEvidenceClass.OFFICIAL_LOCAL},
        "context": context,
        "coordinator": holds,
    }

    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        capacity_by_provider={"codex": first},
        now=110.0,
        **common,
    ) is AgentMode.WORKING
    second = replace(
        first,
        observed_at=120.0,
        lanes=tuple(replace(lane, observed_at=120.0) for lane in first.lanes),
    )
    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        capacity_by_provider={"codex": second},
        now=165.0,
        **common,
    ) is AgentMode.IDLE_READY


def test_provider_mismatch_preserves_hold() -> None:
    event_source = SourceKey("codex", "hooks", "global", "live_agent_events")
    work_key = WorkKey(event_source, WorkIdentifier("session-1"))
    status = type("Status", (), {})()
    status.agent_id = "codex:session:session-1"
    status.provider = "codex"
    status.work_key = work_key
    status.mode = AgentMode.WORKING
    status.is_subagent = False
    status.updated_at = type("Updated", (), {"timestamp": lambda self: 100.0})()
    snapshot, context, _binding = _runtime_capacity(110.0, 0.0)
    source = snapshot.lanes[0].key.source

    assert quota_adjusted_work_mode(
        AgentMode.WORKING,
        statuses=(status,),
        bindings_by_work={},
        capacity_by_provider={"claude": snapshot},
        evidence_class_by_source={source: CapacityEvidenceClass.OFFICIAL_LOCAL},
        context=context,
        coordinator=QuotaPowerHoldCoordinator(),
        now=300.0,
    ) is AgentMode.WORKING
