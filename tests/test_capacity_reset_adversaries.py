from __future__ import annotations

from dataclasses import replace

import pytest

from sidepulse.capacity_types import (
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
    SampleDisposition,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.reset_policy import evaluate_reset_continuity

SOURCE = SourceKey("codex", "local", "desktop", "capacity-v1")
OTHER_SOURCE = SourceKey("codex", "local", "laptop", "capacity-v1")
LANE = QuotaLaneKey(
    SOURCE,
    "all",
    "shared",
    None,
    "session",
    QuotaEffect.ALL_WORKLOADS,
)
OTHER_LANE = replace(LANE, source=OTHER_SOURCE)
ACCOUNT = "acct:opaque-1"


def _observation(
    *,
    remaining: float = 50.0,
    observed_at: float = 1_000.0,
    reset_epoch: float | None = 2_800.0,
    reset_state: ResetState = ResetState.FUTURE,
    lane_key: QuotaLaneKey = LANE,
    account: str | None = ACCOUNT,
) -> QuotaLaneObservation:
    source = lane_key.source
    health_kind = (
        SourceHealthKind.STALE
        if reset_state is ResetState.STALE
        else SourceHealthKind.HEALTHY
    )
    health = CapacitySourceHealth(
        source,
        health_kind,
        observed_at,
        observed_at,
        None,
        None,
        reset_state is ResetState.STALE,
    )
    value_state = (
        ObservationState.OBSERVED_ZERO
        if remaining == 0.0
        else ObservationState.OBSERVED
    )
    return QuotaLaneObservation(
        lane_key,
        "Session window",
        QuotaHorizon.SHORT,
        CapacityValue(CapacityUnit.PERCENT_REMAINING, remaining, value_state),
        ResetFact(reset_state, reset_epoch, 30.0, observed_at),
        observed_at,
        health,
        account,
    )


@pytest.mark.parametrize(
    ("state", "epoch", "forecast_eligible"),
    (
        (ResetState.FUTURE, 2_800.0, True),
        (ResetState.DUE, 999.0, False),
        (ResetState.UNKNOWN, None, False),
        (ResetState.UNAVAILABLE, None, False),
        (ResetState.DISPUTED, 2_800.0, False),
        (ResetState.STALE, 2_800.0, False),
    ),
)
def test_reset_states_remain_typed_without_inventing_a_boundary(
    state: ResetState,
    epoch: float | None,
    forecast_eligible: bool,
) -> None:
    observation = _observation(reset_state=state, reset_epoch=epoch)

    decision = evaluate_reset_continuity(None, observation, source_generation=1)

    assert decision.reset.state is state
    assert decision.reset.reset_epoch == epoch
    assert decision.remaining == 50.0
    assert decision.forecast_eligible is forecast_eligible


def test_exact_identity_accepts_ordered_consumption_in_one_cycle() -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=80.0),
        source_generation=7,
    )

    second = evaluate_reset_continuity(
        first.state,
        _observation(remaining=70.0, observed_at=1_100.0),
        source_generation=7,
    )

    assert second.disposition is SampleDisposition.ACCEPTED
    assert second.forecast_eligible is True
    assert second.reason_code == "reset_continuity_confirmed"


def test_absent_account_discriminator_refuses_identity_continuity() -> None:
    decision = evaluate_reset_continuity(
        None,
        _observation(account=None),
        source_generation=1,
    )

    assert decision.disposition is SampleDisposition.IDENTITY_AMBIGUOUS
    assert decision.forecast_eligible is False
    assert decision.remaining == 50.0
    assert decision.state.confirmed is None


@pytest.mark.parametrize(
    ("changed", "generation", "reason_code"),
    (
        ({"account": "acct:opaque-2"}, 3, "reset_identity_changed"),
        ({"lane_key": OTHER_LANE}, 3, "reset_identity_changed"),
        ({}, 4, "reset_source_generation_changed"),
    ),
)
def test_identity_or_source_generation_change_starts_an_unconfirmed_scope(
    changed: dict[str, object],
    generation: int,
    reason_code: str,
) -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=80.0),
        source_generation=3,
    )

    changed_observation = _observation(
        remaining=20.0,
        observed_at=1_100.0,
        **changed,
    )
    changed_decision = evaluate_reset_continuity(
        first.state,
        changed_observation,
        source_generation=generation,
    )

    assert changed_decision.disposition is SampleDisposition.IDENTITY_AMBIGUOUS
    assert changed_decision.forecast_eligible is False
    assert changed_decision.reason_code == reason_code
    assert changed_decision.state.confirmed is None
    assert changed_decision.state.pending is not None

    corroborated = evaluate_reset_continuity(
        changed_decision.state,
        replace(
            changed_observation,
            observed_at=1_200.0,
            reset=replace(changed_observation.reset, observed_at=1_200.0),
            source_health=replace(
                changed_observation.source_health,
                observed_at=1_200.0,
                last_attempt_at=1_200.0,
            ),
        ),
        source_generation=generation,
    )
    assert corroborated.disposition is SampleDisposition.ACCEPTED
    assert corroborated.reason_code == "reset_identity_corroborated"


@pytest.mark.parametrize(
    ("changed", "generation", "reason_code"),
    (
        ({"account": "acct:opaque-2"}, 3, "reset_identity_changed"),
        ({}, 4, "reset_source_generation_changed"),
    ),
)
def test_identity_scope_change_takes_precedence_over_missing_reset_evidence(
    changed: dict[str, object],
    generation: int,
    reason_code: str,
) -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=80.0),
        source_generation=3,
    )

    changed_decision = evaluate_reset_continuity(
        first.state,
        _observation(
            remaining=20.0,
            observed_at=1_100.0,
            reset_epoch=None,
            reset_state=ResetState.UNKNOWN,
            **changed,
        ),
        source_generation=generation,
    )

    assert changed_decision.disposition is SampleDisposition.IDENTITY_AMBIGUOUS
    assert changed_decision.forecast_eligible is False
    assert changed_decision.reason_code == reason_code


def test_unknown_reset_still_anchors_exact_account_and_generation_scope() -> None:
    unknown = evaluate_reset_continuity(
        None,
        _observation(reset_epoch=None, reset_state=ResetState.UNKNOWN),
        source_generation=9,
    )

    changed = evaluate_reset_continuity(
        unknown.state,
        _observation(
            account="acct:opaque-2",
            remaining=20.0,
            observed_at=1_100.0,
        ),
        source_generation=9,
    )

    assert unknown.state.identity is not None
    assert unknown.state.confirmed is None
    assert changed.disposition is SampleDisposition.IDENTITY_AMBIGUOUS
    assert changed.reason_code == "reset_identity_changed"


def test_out_of_order_observation_cannot_replace_confirmed_continuity() -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=80.0, observed_at=1_100.0),
        source_generation=1,
    )

    late = evaluate_reset_continuity(
        first.state,
        _observation(remaining=10.0, observed_at=1_050.0),
        source_generation=1,
    )

    assert late.disposition is SampleDisposition.OUT_OF_ORDER
    assert late.forecast_eligible is False
    assert late.remaining == 10.0
    assert late.state == first.state


@pytest.mark.parametrize(
    ("old_observation", "old_generation"),
    (
        ({"account": ACCOUNT}, 3),
        ({"account": "acct:opaque-2"}, 3),
    ),
)
def test_out_of_order_result_cannot_roll_identity_scope_backward(
    old_observation: dict[str, object],
    old_generation: int,
) -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=80.0),
        source_generation=3,
    )
    current = evaluate_reset_continuity(
        first.state,
        _observation(
            account="acct:opaque-2",
            remaining=30.0,
            observed_at=1_200.0,
        ),
        source_generation=4,
    )

    late = evaluate_reset_continuity(
        current.state,
        _observation(
            remaining=20.0,
            observed_at=1_100.0,
            **old_observation,
        ),
        source_generation=old_generation,
    )

    assert late.disposition is SampleDisposition.OUT_OF_ORDER
    assert late.state == current.state
    assert late.forecast_eligible is False


def test_reset_epoch_moving_backward_is_quarantined() -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=70.0, reset_epoch=2_800.0),
        source_generation=1,
    )

    backward = evaluate_reset_continuity(
        first.state,
        _observation(
            remaining=60.0,
            observed_at=1_100.0,
            reset_epoch=2_700.0,
        ),
        source_generation=1,
    )

    assert backward.disposition is SampleDisposition.RESET_DISPUTED
    assert backward.reset.state is ResetState.DISPUTED
    assert backward.reset.reset_epoch == 2_700.0
    assert backward.forecast_eligible is False
    assert backward.remaining == 60.0
    assert backward.state.confirmed == first.state.confirmed


def test_multi_window_forward_jump_with_recovery_confirms_after_sleep() -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=5.0, reset_epoch=2_800.0),
        source_generation=1,
    )

    woke = evaluate_reset_continuity(
        first.state,
        _observation(
            remaining=90.0,
            observed_at=6_500.25,
            reset_epoch=8_200.5,
        ),
        source_generation=1,
    )

    assert woke.disposition is SampleDisposition.ACCEPTED
    assert woke.reset.state is ResetState.FUTURE
    assert woke.forecast_eligible is True
    assert woke.reason_code == "reset_cycle_confirmed"


def test_due_boundary_after_sleep_does_not_silently_advance() -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=10.0, reset_epoch=2_800.0),
        source_generation=1,
    )

    due = evaluate_reset_continuity(
        first.state,
        _observation(
            remaining=8.0,
            observed_at=2_900.0,
            reset_epoch=2_800.0,
            reset_state=ResetState.DUE,
        ),
        source_generation=1,
    )

    assert due.disposition is SampleDisposition.ACCEPTED
    assert due.reset.state is ResetState.DUE
    assert due.reset.reset_epoch == 2_800.0
    assert due.forecast_eligible is False


def test_reset_advance_without_recovery_requires_corroboration() -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=20.0, reset_epoch=2_800.0),
        source_generation=1,
    )

    disputed = evaluate_reset_continuity(
        first.state,
        _observation(
            remaining=19.0,
            observed_at=1_100.0,
            reset_epoch=4_600.0,
        ),
        source_generation=1,
    )

    assert disputed.disposition is SampleDisposition.RESET_DISPUTED
    assert disputed.reset.state is ResetState.DISPUTED
    assert disputed.remaining == 19.0
    assert disputed.forecast_eligible is False

    corroborated = evaluate_reset_continuity(
        disputed.state,
        _observation(
            remaining=18.0,
            observed_at=1_200.0,
            reset_epoch=4_600.0,
        ),
        source_generation=1,
    )
    assert corroborated.disposition is SampleDisposition.ACCEPTED
    assert corroborated.reset.state is ResetState.FUTURE
    assert corroborated.reason_code == "reset_cycle_corroborated"


def test_usage_recovery_without_reset_advance_requires_corroboration() -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=20.0, reset_epoch=2_800.0),
        source_generation=1,
    )

    disputed = evaluate_reset_continuity(
        first.state,
        _observation(
            remaining=80.0,
            observed_at=1_100.0,
            reset_epoch=2_800.0,
        ),
        source_generation=1,
    )

    assert disputed.disposition is SampleDisposition.RESET_DISPUTED
    assert disputed.reset.state is ResetState.DISPUTED
    assert disputed.remaining == 80.0

    corroborated = evaluate_reset_continuity(
        disputed.state,
        _observation(
            remaining=75.0,
            observed_at=1_200.0,
            reset_epoch=2_800.0,
        ),
        source_generation=1,
    )
    assert corroborated.disposition is SampleDisposition.ACCEPTED
    assert corroborated.reset.state is ResetState.FUTURE
    assert corroborated.reason_code == "reset_cycle_corroborated"


def test_transient_remaining_spike_does_not_corroborate_a_new_cycle() -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=20.0, reset_epoch=2_800.0),
        source_generation=1,
    )
    disputed = evaluate_reset_continuity(
        first.state,
        _observation(
            remaining=80.0,
            observed_at=1_100.0,
            reset_epoch=2_800.0,
        ),
        source_generation=1,
    )

    recovered_truth = evaluate_reset_continuity(
        disputed.state,
        _observation(
            remaining=10.0,
            observed_at=1_200.0,
            reset_epoch=2_800.0,
        ),
        source_generation=1,
    )

    assert recovered_truth.disposition is SampleDisposition.ACCEPTED
    assert recovered_truth.reason_code == "reset_continuity_confirmed"


def test_pending_cycle_cannot_be_corroborated_by_another_account() -> None:
    first = evaluate_reset_continuity(
        None,
        _observation(remaining=20.0, reset_epoch=2_800.0),
        source_generation=1,
    )
    disputed = evaluate_reset_continuity(
        first.state,
        _observation(
            remaining=19.0,
            observed_at=1_100.0,
            reset_epoch=4_600.0,
        ),
        source_generation=1,
    )

    other_account = evaluate_reset_continuity(
        disputed.state,
        _observation(
            remaining=18.0,
            observed_at=1_200.0,
            reset_epoch=4_600.0,
            account="acct:opaque-2",
        ),
        source_generation=1,
    )

    assert other_account.disposition is SampleDisposition.IDENTITY_AMBIGUOUS
    assert other_account.forecast_eligible is False
    assert other_account.reason_code == "reset_identity_changed"


def test_invalid_source_generation_fails_closed_without_hiding_remaining() -> None:
    decision = evaluate_reset_continuity(
        None,
        _observation(remaining=40.0),
        source_generation=-1,
    )

    assert decision.disposition is SampleDisposition.INVALID
    assert decision.forecast_eligible is False
    assert decision.remaining == 40.0
    assert decision.state.confirmed is None
