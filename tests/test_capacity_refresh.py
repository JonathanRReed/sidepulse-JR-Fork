from __future__ import annotations

from dataclasses import replace

import pytest

from sidepulse.capacity_refresh import (
    MAX_REFRESH_DEADLINE_SECONDS,
    MAX_REFRESH_SOURCE_RECORDS,
    CapacityRefreshCoordinator,
    RefreshCause,
    RefreshCommit,
    RefreshCommitKind,
    RefreshCoordinatorSnapshot,
    RefreshDecision,
    RefreshDecisionKind,
    RefreshDecisionReason,
    RefreshFailureKind,
    RefreshSourceKey,
    RefreshSourceRegistration,
    RefreshStatusKind,
    RefreshValidationError,
)
from sidepulse.capacity_types import (
    CapacityAccountBinding,
    CapacityEvidenceClass,
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
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


def source(
    provider_id: str = "codex",
    *,
    source_instance_id: str = "local",
) -> SourceKey:
    return SourceKey(
        provider_id=provider_id,
        adapter_id="quota",
        source_instance_id=source_instance_id,
        capability_id="remote_quota_windows",
    )


def key(
    provider_id: str = "codex",
    *,
    pool: str = "shared",
    account_discriminator: str | None = "account-a",
    source_instance_id: str = "local",
    auth_mode: str | None = None,
) -> RefreshSourceKey:
    return RefreshSourceKey(
        source=source(provider_id, source_instance_id=source_instance_id),
        pool=pool,
        account_discriminator=account_discriminator,
        auth_mode=auth_mode,
    )


def coordinator(
    *keys: RefreshSourceKey,
    enabled: bool = True,
    supported: bool = True,
) -> CapacityRefreshCoordinator:
    return CapacityRefreshCoordinator(
        tuple(
            RefreshSourceRegistration(
                key=item,
                enabled=enabled,
                supported=supported,
            )
            for item in keys
        )
    )


def test_registration_rejects_binding_that_does_not_match_its_exact_source_account_or_pool() -> None:
    """Permitting a mismatched binding would let one account reuse another's refresh record."""
    refresh_key = key()
    binding = CapacityAccountBinding(
        source=source("claude"),
        provider_id="claude",
        auth_mode="consumer-plan",
        opaque_account_id="account-a",
        pool_id="shared",
        evidence_class=CapacityEvidenceClass.OFFICIAL_LOCAL,
        observed_at=10.0,
    )

    with pytest.raises(RefreshValidationError, match="binding"):
        RefreshSourceRegistration(refresh_key, enabled=True, supported=True, binding=binding)


def test_registration_rejects_binding_that_does_not_match_its_auth_mode() -> None:
    """Omitting auth mode from refresh identity would reuse another plan's result."""
    refresh_key = key(auth_mode="chatgpt-plan")
    binding = CapacityAccountBinding(
        source=refresh_key.source,
        provider_id="codex",
        auth_mode="api-organization",
        opaque_account_id="account-a",
        pool_id="shared",
        evidence_class=CapacityEvidenceClass.OFFICIAL_LOCAL,
        observed_at=10.0,
    )

    with pytest.raises(RefreshValidationError, match="binding"):
        RefreshSourceRegistration(refresh_key, enabled=True, supported=True, binding=binding)


def snapshot(
    refresh_key: RefreshSourceKey,
    *,
    observed_at: float,
    remaining: float = 50.0,
    with_lane: bool = True,
) -> CapacitySnapshot:
    health = CapacitySourceHealth(
        source=refresh_key.source,
        kind=SourceHealthKind.HEALTHY,
        observed_at=observed_at,
        last_attempt_at=observed_at,
        retry_at=None,
        reason_code=None,
        has_last_known_good=False,
    )
    if not with_lane:
        return CapacitySnapshot(
            observed_at=observed_at,
            lanes=(),
            source_health=(health,),
        )
    lane_key = QuotaLaneKey(
        source=refresh_key.source,
        opaque_scope="all",
        pool=refresh_key.pool,
        model=None,
        window="session",
        effect=QuotaEffect.ALL_WORKLOADS,
    )
    lane = QuotaLaneObservation(
        key=lane_key,
        semantic_name="Session window",
        horizon=QuotaHorizon.SHORT,
        value=CapacityValue(
            unit=CapacityUnit.PERCENT_REMAINING,
            remaining=remaining,
            state=(ObservationState.OBSERVED_ZERO if remaining == 0.0 else ObservationState.OBSERVED),
        ),
        reset=ResetFact(
            state=ResetState.UNKNOWN,
            reset_epoch=None,
            window_minutes=300.0,
            observed_at=observed_at,
        ),
        observed_at=observed_at,
        source_health=health,
        account_discriminator=refresh_key.account_discriminator,
    )
    return CapacitySnapshot(
        observed_at=observed_at,
        lanes=(lane,),
        source_health=(health,),
    )


def start(
    refresh: CapacityRefreshCoordinator,
    refresh_key: RefreshSourceKey,
    *,
    now: float,
    deadline: float,
    cause: RefreshCause = RefreshCause.AUTOMATIC,
) -> int:
    decision = refresh.request_refresh(refresh_key, cause, now)
    assert decision.kind is RefreshDecisionKind.START
    assert decision.generation is not None
    refresh.register_started(refresh_key, decision.generation, deadline)
    return decision.generation


def test_coordinator_rejects_more_than_sixteen_exact_source_records() -> None:
    registrations = tuple(
        RefreshSourceRegistration(
            key=key(f"provider{index}"),
            enabled=True,
            supported=True,
        )
        for index in range(MAX_REFRESH_SOURCE_RECORDS + 1)
    )

    with pytest.raises(RefreshValidationError):
        CapacityRefreshCoordinator(registrations)


def test_sixteen_records_are_retained_in_deterministic_key_order() -> None:
    registrations = tuple(
        RefreshSourceRegistration(
            key=key(f"provider{index}"),
            enabled=True,
            supported=True,
        )
        for index in reversed(range(MAX_REFRESH_SOURCE_RECORDS))
    )

    state = CapacityRefreshCoordinator(registrations).snapshot_state(100.0)

    assert len(state.sources) == MAX_REFRESH_SOURCE_RECORDS
    assert tuple(row.key for row in state.sources) == tuple(sorted(row.key for row in registrations))


def test_one_in_flight_generation_coalesces_duplicate_and_manual_requests() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)

    first = refresh.request_refresh(refresh_key, RefreshCause.MENU_OPEN, 100.0)
    duplicate = refresh.request_refresh(refresh_key, RefreshCause.AUTOMATIC, 101.0)
    manual = refresh.request_refresh(refresh_key, RefreshCause.MANUAL, 102.0)

    assert first.kind is RefreshDecisionKind.START
    assert first.generation == 1
    assert duplicate.kind is RefreshDecisionKind.COALESCED
    assert duplicate.generation == 1
    assert manual.kind is RefreshDecisionKind.COALESCED
    assert manual.generation == 1
    state = refresh.snapshot_state(102.0).sources[0]
    assert state.in_flight is True
    assert state.queued_manual is False


def test_independent_source_deadlines_expire_without_cross_source_mutation() -> None:
    codex = key("codex")
    claude = key("claude")
    refresh = coordinator(codex, claude)
    codex_generation = start(refresh, codex, now=1.0, deadline=10.0)
    claude_generation = start(refresh, claude, now=1.0, deadline=20.0)

    codex_commit = refresh.expire_deadline(codex, codex_generation, 10.0)
    claude_commit = refresh.expire_deadline(claude, claude_generation, 10.0)

    assert codex_commit.kind is RefreshCommitKind.TIMED_OUT
    assert claude_commit.kind is RefreshCommitKind.NOT_DUE
    states = {row.key: row for row in refresh.snapshot_state(10.0).sources}
    assert states[codex].in_flight is False
    assert states[codex].last_failure is RefreshFailureKind.TIMED_OUT
    assert states[claude].in_flight is True
    assert states[claude].deadline == 20.0


def test_late_generation_cannot_overwrite_newer_success() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation_one = start(refresh, refresh_key, now=0.0, deadline=5.0)
    assert refresh.expire_deadline(refresh_key, generation_one, 5.0).kind is RefreshCommitKind.TIMED_OUT

    generation_two = start(refresh, refresh_key, now=20.0, deadline=30.0)
    newest = snapshot(refresh_key, observed_at=2_000.0, remaining=60.0)
    accepted = refresh.register_success(
        refresh_key,
        generation_two,
        newest,
        completed_at=21.0,
    )
    obsolete = refresh.register_success(
        refresh_key,
        generation_one,
        snapshot(refresh_key, observed_at=1_000.0, remaining=10.0),
        completed_at=22.0,
    )

    assert accepted.kind is RefreshCommitKind.SUCCESS
    assert obsolete.kind is RefreshCommitKind.STALE_GENERATION
    state = refresh.snapshot_state(22.0).sources[0]
    assert state.generation == generation_two
    assert state.last_known_good is newest


def test_exact_source_invalidation_fences_work_and_preserves_sibling() -> None:
    codex = key("codex")
    claude = key("claude")
    refresh = coordinator(codex, claude)
    initial_generation = start(refresh, codex, now=90.0, deadline=99.0)
    refresh.register_success(
        codex,
        initial_generation,
        snapshot(codex, observed_at=1_000.0, remaining=44.0),
        completed_at=95.0,
    )
    codex_generation = start(refresh, codex, now=100.0, deadline=110.0)
    claude_generation = start(refresh, claude, now=100.0, deadline=120.0)
    refresh.register_failure(
        codex,
        codex_generation,
        RefreshFailureKind.SOURCE_UNAVAILABLE,
        completed_at=105.0,
        retry_at=150.0,
    )
    refresh.request_refresh(codex, RefreshCause.MANUAL, 106.0)

    invalidated = refresh.invalidate_source(codex, now=107.0)

    assert invalidated.key == codex
    assert invalidated.generation == 3
    assert invalidated.in_flight is False
    assert invalidated.deadline is None
    assert invalidated.queued_manual is False
    assert invalidated.last_attempt_at is None
    assert invalidated.retry_at is None
    assert invalidated.last_known_good is None
    assert invalidated.last_success_at is None
    assert invalidated.last_failure is None
    assert invalidated.consecutive_failures == 0
    sibling = {row.key: row for row in refresh.snapshot_state(107.0).sources}[
        claude
    ]
    assert sibling.generation == claude_generation
    assert sibling.in_flight is True
    assert sibling.deadline == 120.0
    restarted = refresh.request_refresh(codex, RefreshCause.AUTOMATIC, 107.0)
    assert restarted.kind is RefreshDecisionKind.START
    assert restarted.generation == 4


def test_manual_refresh_queues_once_and_never_bypasses_retry_after() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation = start(refresh, refresh_key, now=100.0, deadline=120.0)
    failure = refresh.register_failure(
        refresh_key,
        generation,
        RefreshFailureKind.FAILED,
        completed_at=110.0,
        retry_at=200.0,
    )

    automatic = refresh.request_refresh(refresh_key, RefreshCause.AUTOMATIC, 150.0)
    first_manual = refresh.request_refresh(refresh_key, RefreshCause.MANUAL, 151.0)
    repeated_manual = tuple(
        refresh.request_refresh(refresh_key, RefreshCause.MANUAL, 152.0 + index) for index in range(9)
    )
    early = refresh.take_due_queued_refresh(refresh_key, 199.999)
    due = refresh.take_due_queued_refresh(refresh_key, 200.0)
    duplicate_due = refresh.take_due_queued_refresh(refresh_key, 200.0)

    assert failure.kind is RefreshCommitKind.FAILURE
    assert failure.retry_at == 200.0
    assert automatic.kind is RefreshDecisionKind.COALESCED
    assert automatic.retry_at == 200.0
    assert first_manual.kind is RefreshDecisionKind.QUEUED_FOR_COOLDOWN
    assert first_manual.retry_at == 200.0
    assert all(item.kind is RefreshDecisionKind.COALESCED for item in repeated_manual)
    assert all(item.retry_at == 200.0 for item in repeated_manual)
    assert early.kind is RefreshDecisionKind.COALESCED
    assert due.kind is RefreshDecisionKind.START
    assert due.generation == 2
    assert duplicate_due.kind is RefreshDecisionKind.COALESCED
    assert refresh.snapshot_state(200.0).sources[0].queued_manual is False


def test_retry_after_is_scoped_by_source_pool_and_account_discriminator() -> None:
    blocked = key("codex", pool="shared", account_discriminator="account-a")
    other_source = key("claude", pool="shared", account_discriminator="account-a")
    other_pool = key("codex", pool="fable", account_discriminator="account-a")
    other_account = key("codex", pool="shared", account_discriminator="account-b")
    refresh = coordinator(blocked, other_source, other_pool, other_account)
    generation = start(refresh, blocked, now=10.0, deadline=20.0)
    refresh.register_failure(
        blocked,
        generation,
        RefreshFailureKind.FAILED,
        completed_at=11.0,
        retry_at=100.0,
    )

    blocked_decision = refresh.request_refresh(blocked, RefreshCause.MANUAL, 50.0)
    sibling_decisions = tuple(
        refresh.request_refresh(item, RefreshCause.AUTOMATIC, 50.0)
        for item in (other_source, other_pool, other_account)
    )

    assert blocked_decision.kind is RefreshDecisionKind.QUEUED_FOR_COOLDOWN
    assert all(item.kind is RefreshDecisionKind.START for item in sibling_decisions)
    assert tuple(item.generation for item in sibling_decisions) == (1, 1, 1)


def test_disabled_unsupported_and_unknown_sources_never_create_timers() -> None:
    disabled = key("codex")
    unsupported = key("claude")
    unknown = key("gemini")
    refresh = CapacityRefreshCoordinator(
        (
            RefreshSourceRegistration(disabled, enabled=False, supported=True),
            RefreshSourceRegistration(unsupported, enabled=True, supported=False),
        )
    )

    disabled_decision = refresh.request_refresh(disabled, RefreshCause.MANUAL, 10.0)
    unsupported_decision = refresh.request_refresh(unsupported, RefreshCause.MANUAL, 10.0)
    unknown_decision = refresh.request_refresh(unknown, RefreshCause.MANUAL, 10.0)

    assert disabled_decision.kind is RefreshDecisionKind.DISABLED
    assert unsupported_decision.kind is RefreshDecisionKind.UNSUPPORTED
    assert unknown_decision.kind is RefreshDecisionKind.UNSUPPORTED
    states = {row.key: row for row in refresh.snapshot_state(10.0).sources}
    assert len(states) == 2
    assert states[disabled].status is RefreshStatusKind.DISABLED
    assert states[unsupported].status is RefreshStatusKind.UNSUPPORTED
    assert all(row.in_flight is False and row.deadline is None for row in states.values())


def test_failure_retains_last_known_good_with_original_observation_time() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation_one = start(refresh, refresh_key, now=100.0, deadline=120.0)
    original = snapshot(refresh_key, observed_at=1_000.0, remaining=40.0)
    refresh.register_success(
        refresh_key,
        generation_one,
        original,
        completed_at=110.0,
    )
    generation_two = start(refresh, refresh_key, now=120.0, deadline=140.0)

    commit = refresh.register_failure(
        refresh_key,
        generation_two,
        RefreshFailureKind.ACCESS_DENIED,
        completed_at=130.0,
        retry_at=None,
    )
    state = refresh.snapshot_state(130.0).sources[0]

    assert commit.kind is RefreshCommitKind.FAILURE
    assert state.last_known_good is original
    assert state.last_known_good.observed_at == 1_000.0
    assert state.last_success_at == 110.0
    assert state.has_last_known_good is True
    assert state.last_failure is RefreshFailureKind.ACCESS_DENIED
    assert state.status is RefreshStatusKind.COOLDOWN


def test_failure_without_last_known_good_remains_explicit() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation = start(refresh, refresh_key, now=10.0, deadline=20.0)

    commit = refresh.register_failure(
        refresh_key,
        generation,
        RefreshFailureKind.SOURCE_UNAVAILABLE,
        completed_at=15.0,
        retry_at=None,
    )
    state = refresh.snapshot_state(15.0).sources[0]

    assert commit.kind is RefreshCommitKind.FAILURE
    assert commit.has_last_known_good is False
    assert state.last_known_good is None
    assert state.has_last_known_good is False
    assert state.last_failure is RefreshFailureKind.SOURCE_UNAVAILABLE


def test_explicit_empty_success_clears_previous_last_known_good() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation_one = start(refresh, refresh_key, now=10.0, deadline=20.0)
    refresh.register_success(
        refresh_key,
        generation_one,
        snapshot(refresh_key, observed_at=1_000.0),
        completed_at=15.0,
    )
    generation_two = start(refresh, refresh_key, now=20.0, deadline=30.0)

    commit = refresh.register_success(
        refresh_key,
        generation_two,
        snapshot(refresh_key, observed_at=2_000.0, with_lane=False),
        completed_at=25.0,
    )
    state = refresh.snapshot_state(25.0).sources[0]

    assert commit.kind is RefreshCommitKind.SUCCESS
    assert commit.has_last_known_good is False
    assert state.last_known_good is None
    assert state.status is RefreshStatusKind.HEALTHY


@pytest.mark.parametrize(
    ("failure_kind", "expected_status"),
    (
        (RefreshFailureKind.FAILED, RefreshStatusKind.FAILED),
        (RefreshFailureKind.TIMED_OUT, RefreshStatusKind.TIMED_OUT),
        (RefreshFailureKind.SIGN_IN_REQUIRED, RefreshStatusKind.SIGN_IN_REQUIRED),
        (RefreshFailureKind.ACCESS_DENIED, RefreshStatusKind.ACCESS_DENIED),
        (RefreshFailureKind.SOURCE_UNAVAILABLE, RefreshStatusKind.FAILED),
    ),
)
def test_failure_status_returns_after_cooldown_boundary(
    failure_kind: RefreshFailureKind,
    expected_status: RefreshStatusKind,
) -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation = start(refresh, refresh_key, now=10.0, deadline=20.0)
    refresh.register_failure(
        refresh_key,
        generation,
        failure_kind,
        completed_at=15.0,
        retry_at=30.0,
    )

    during = refresh.snapshot_state(29.999).sources[0]
    at_boundary = refresh.snapshot_state(30.0).sources[0]

    assert during.status is RefreshStatusKind.COOLDOWN
    assert at_boundary.status is expected_status


def test_expire_before_deadline_is_explicit_and_non_mutating() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation = start(refresh, refresh_key, now=10.0, deadline=20.0)

    commit = refresh.expire_deadline(refresh_key, generation, 19.999)
    state = refresh.snapshot_state(19.999).sources[0]

    assert commit.kind is RefreshCommitKind.NOT_DUE
    assert commit.retry_at == 20.0
    assert state.in_flight is True
    assert state.deadline == 20.0


def test_stale_failure_and_timeout_are_refused_without_mutation() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation = start(refresh, refresh_key, now=10.0, deadline=20.0)
    current = snapshot(refresh_key, observed_at=1_000.0)
    refresh.register_success(refresh_key, generation, current, completed_at=15.0)

    failure = refresh.register_failure(
        refresh_key,
        generation,
        RefreshFailureKind.FAILED,
        completed_at=16.0,
        retry_at=30.0,
    )
    timeout = refresh.expire_deadline(refresh_key, generation, 20.0)
    state = refresh.snapshot_state(20.0).sources[0]

    assert failure.kind is RefreshCommitKind.STALE_GENERATION
    assert timeout.kind is RefreshCommitKind.STALE_GENERATION
    assert state.last_known_good is current
    assert state.last_failure is None


def test_success_rejects_cross_source_pool_and_account_snapshots() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation = start(refresh, refresh_key, now=10.0, deadline=20.0)

    with pytest.raises(RefreshValidationError):
        refresh.register_success(
            refresh_key,
            generation,
            snapshot(key("claude"), observed_at=1_000.0),
            completed_at=15.0,
        )
    with pytest.raises(RefreshValidationError):
        refresh.register_success(
            refresh_key,
            generation,
            snapshot(key(pool="fable"), observed_at=1_000.0),
            completed_at=15.0,
        )
    with pytest.raises(RefreshValidationError):
        refresh.register_success(
            refresh_key,
            generation,
            snapshot(key(account_discriminator="account-b"), observed_at=1_000.0),
            completed_at=15.0,
        )

    assert refresh.snapshot_state(15.0).sources[0].in_flight is True


def test_register_started_requires_current_reserved_generation_and_future_deadline() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    decision = refresh.request_refresh(refresh_key, RefreshCause.AUTOMATIC, 10.0)
    assert decision.generation == 1

    with pytest.raises(RefreshValidationError):
        refresh.register_started(refresh_key, 2, 20.0)
    with pytest.raises(RefreshValidationError):
        refresh.register_started(refresh_key, 1, 10.0)

    refresh.register_started(refresh_key, 1, 20.0)
    assert refresh.snapshot_state(10.0).sources[0].deadline == 20.0


def test_refresh_key_rejects_display_account_text_paths_and_unbounded_pool() -> None:
    with pytest.raises(RefreshValidationError):
        RefreshSourceKey(source(), "shared", "person@example.com")
    with pytest.raises(RefreshValidationError):
        RefreshSourceKey(source(), "shared", "/Users/person/account")
    with pytest.raises(RefreshValidationError):
        RefreshSourceKey(source(), "x" * 65, "account-a")


def test_nonfinite_times_and_retry_boundaries_fail_closed() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)

    with pytest.raises(RefreshValidationError):
        refresh.request_refresh(refresh_key, RefreshCause.AUTOMATIC, float("nan"))
    generation = start(refresh, refresh_key, now=10.0, deadline=20.0)
    with pytest.raises(RefreshValidationError):
        refresh.register_failure(
            refresh_key,
            generation,
            RefreshFailureKind.FAILED,
            completed_at=15.0,
            retry_at=float("inf"),
        )
    with pytest.raises(RefreshValidationError):
        refresh.snapshot_state(float("nan"))


def test_in_flight_coalescing_never_mislabels_deadline_as_retry_after() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    start(refresh, refresh_key, now=10.0, deadline=20.0)

    decision = refresh.request_refresh(refresh_key, RefreshCause.MANUAL, 15.0)

    assert decision.kind is RefreshDecisionKind.COALESCED
    assert decision.reason is RefreshDecisionReason.IN_FLIGHT
    assert decision.retry_at is None


def test_success_requires_a_registered_outer_deadline() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    decision = refresh.request_refresh(refresh_key, RefreshCause.AUTOMATIC, 10.0)
    assert decision.generation == 1

    with pytest.raises(RefreshValidationError):
        refresh.register_success(
            refresh_key,
            1,
            snapshot(refresh_key, observed_at=1_000.0),
            completed_at=15.0,
        )

    assert refresh.snapshot_state(15.0).sources[0].in_flight is True


def test_completion_at_outer_deadline_times_out_without_publishing_snapshot() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation = start(refresh, refresh_key, now=10.0, deadline=20.0)

    commit = refresh.register_success(
        refresh_key,
        generation,
        snapshot(refresh_key, observed_at=1_000.0),
        completed_at=20.0,
    )
    state = refresh.snapshot_state(20.0).sources[0]

    assert commit.kind is RefreshCommitKind.TIMED_OUT
    assert state.last_known_good is None
    assert state.last_failure is RefreshFailureKind.TIMED_OUT


def test_attempt_completion_cannot_move_backward_in_monotonic_time() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation = start(refresh, refresh_key, now=10.0, deadline=20.0)

    with pytest.raises(RefreshValidationError):
        refresh.register_failure(
            refresh_key,
            generation,
            RefreshFailureKind.FAILED,
            completed_at=9.999,
            retry_at=None,
        )

    assert refresh.snapshot_state(10.0).sources[0].in_flight is True


def test_outer_deadline_is_bounded_to_canonical_runtime_limit() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    decision = refresh.request_refresh(refresh_key, RefreshCause.AUTOMATIC, 10.0)
    assert decision.generation == 1

    with pytest.raises(RefreshValidationError):
        refresh.register_started(
            refresh_key,
            1,
            10.0 + MAX_REFRESH_DEADLINE_SECONDS + 0.001,
        )

    refresh.register_started(
        refresh_key,
        1,
        10.0 + MAX_REFRESH_DEADLINE_SECONDS,
    )


def test_due_queued_manual_intent_wins_an_automatic_boundary_request() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation = start(refresh, refresh_key, now=10.0, deadline=20.0)
    refresh.register_failure(
        refresh_key,
        generation,
        RefreshFailureKind.FAILED,
        completed_at=15.0,
        retry_at=30.0,
    )
    refresh.request_refresh(refresh_key, RefreshCause.MANUAL, 20.0)

    decision = refresh.request_refresh(
        refresh_key,
        RefreshCause.AUTOMATIC,
        30.0,
    )

    assert decision.kind is RefreshDecisionKind.START
    assert decision.cause is RefreshCause.MANUAL
    assert refresh.snapshot_state(30.0).sources[0].queued_manual is False


def test_take_due_rejects_an_untyped_key_at_the_domain_boundary() -> None:
    refresh = coordinator(key())

    with pytest.raises(RefreshValidationError):
        refresh.take_due_queued_refresh("codex", 10.0)  # type: ignore[arg-type]


def test_stale_cross_scope_payload_is_refused_before_payload_scope_validation() -> None:
    refresh_key = key()
    refresh = coordinator(refresh_key)
    generation = start(refresh, refresh_key, now=10.0, deadline=20.0)
    refresh.register_success(
        refresh_key,
        generation,
        snapshot(refresh_key, observed_at=1_000.0),
        completed_at=15.0,
    )

    commit = refresh.register_success(
        refresh_key,
        generation,
        snapshot(key("claude"), observed_at=2_000.0),
        completed_at=16.0,
    )

    assert commit.kind is RefreshCommitKind.STALE_GENERATION
    assert refresh.snapshot_state(16.0).sources[0].last_known_good is not None


@pytest.mark.parametrize(
    "account_discriminator",
    ("token-secret", "password:account", "bearer-credential"),
)
def test_refresh_key_rejects_credential_like_account_discriminators(
    account_discriminator: str,
) -> None:
    with pytest.raises(RefreshValidationError):
        RefreshSourceKey(source(), "shared", account_discriminator)


def test_public_decision_rejects_contradictory_start_fields() -> None:
    with pytest.raises(RefreshValidationError):
        RefreshDecision(
            kind=RefreshDecisionKind.START,
            key=key(),
            cause=RefreshCause.AUTOMATIC,
            generation=1,
            retry_at=20.0,
            reason=RefreshDecisionReason.ELIGIBLE,
        )
    with pytest.raises(RefreshValidationError):
        RefreshDecision(
            kind=RefreshDecisionKind.DISABLED,
            key=key(),
            cause=RefreshCause.AUTOMATIC,
            generation=1,
            retry_at=None,
            reason=RefreshDecisionReason.DISABLED,
        )


def test_public_commit_rejects_failure_without_typed_failure_kind() -> None:
    with pytest.raises(RefreshValidationError):
        RefreshCommit(
            kind=RefreshCommitKind.FAILURE,
            key=key(),
            generation=1,
            committed_at=10.0,
            retry_at=20.0,
            has_last_known_good=False,
            failure_kind=None,
        )


def test_public_source_state_rejects_contradictory_lkg_and_active_fields() -> None:
    state = coordinator(key()).snapshot_state(10.0).sources[0]

    with pytest.raises(RefreshValidationError):
        replace(state, has_last_known_good=True)
    with pytest.raises(RefreshValidationError):
        replace(state, in_flight=True, active_cause=None)


def test_public_coordinator_snapshot_rejects_duplicate_and_unsorted_sources() -> None:
    first = coordinator(key("codex")).snapshot_state(10.0).sources[0]
    second = coordinator(key("claude")).snapshot_state(10.0).sources[0]
    ordered = tuple(sorted((first, second), key=lambda row: row.key))

    with pytest.raises(RefreshValidationError):
        RefreshCoordinatorSnapshot(10.0, (first, first))
    with pytest.raises(RefreshValidationError):
        RefreshCoordinatorSnapshot(10.0, tuple(reversed(ordered)))
