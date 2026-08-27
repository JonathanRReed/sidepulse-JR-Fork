from __future__ import annotations

import builtins
import socket
import subprocess
from dataclasses import fields
from itertools import permutations

import pytest

from sidepulse.capacity_authority import CapacityProjection, select_binding_lanes
from sidepulse.capacity_history import (
    NO_OBSERVATION,
    CapacityHistorySummary,
    HistoryInterval,
)
from sidepulse.capacity_refresh import (
    RefreshCause,
    RefreshCoordinatorSnapshot,
    RefreshDecision,
    RefreshDecisionKind,
    RefreshDecisionReason,
    RefreshFailureKind,
    RefreshSourceKey,
    RefreshSourceState,
    RefreshStatusKind,
)
from sidepulse.capacity_types import (
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ExecutionContext,
    LaneApplicability,
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
from sidepulse.capacity_view import (
    MAX_CAPACITY_CARD_ROWS,
    MAX_CAPACITY_HISTORY_SUMMARIES,
    CapacityCardModel,
    CapacityCardRowModel,
    CapacityDetailSnapshot,
    CapacityHistoryPresentation,
    CapacityHistorySummaryInput,
    build_capacity_card,
    build_capacity_detail,
    build_manual_refresh_status,
    format_freshness,
    format_refresh_outcome,
    format_remaining,
    format_reset,
)

NOW = 10_000.0


def _source(
    provider_id: str = "codex",
    source_instance_id: str = "local:primary",
) -> SourceKey:
    return SourceKey(
        provider_id=provider_id,
        adapter_id="quota",
        source_instance_id=source_instance_id,
        capability_id="capacity.v1",
    )


def _health(
    source: SourceKey,
    *,
    kind: SourceHealthKind = SourceHealthKind.HEALTHY,
    observed_at: float = NOW,
    last_attempt_at: float | None = NOW,
    retry_at: float | None = None,
    reason_code: str | None = None,
    has_last_known_good: bool = False,
) -> CapacitySourceHealth:
    return CapacitySourceHealth(
        source=source,
        kind=kind,
        observed_at=observed_at,
        last_attempt_at=last_attempt_at,
        retry_at=retry_at,
        reason_code=reason_code,
        has_last_known_good=has_last_known_good,
    )


def _reset(
    state: ResetState = ResetState.FUTURE,
    *,
    reset_epoch: float | None = NOW + 3_723.0,
    observed_at: float = NOW,
) -> ResetFact:
    if state in {ResetState.UNKNOWN, ResetState.UNAVAILABLE}:
        reset_epoch = None
    if state is ResetState.DUE:
        reset_epoch = NOW
    return ResetFact(
        state=state,
        reset_epoch=reset_epoch,
        window_minutes=300.0,
        observed_at=observed_at,
    )


def _lane(
    *,
    provider_id: str = "codex",
    source_instance_id: str = "local:primary",
    window: str = "session",
    semantic_name: str = "Session window",
    horizon: QuotaHorizon = QuotaHorizon.SHORT,
    remaining: float | None = 50.0,
    value_state: ObservationState = ObservationState.OBSERVED,
    reset_state: ResetState = ResetState.FUTURE,
    reset_epoch: float | None = NOW + 3_723.0,
    health_kind: SourceHealthKind = SourceHealthKind.HEALTHY,
    health_observed_at: float = NOW,
    last_attempt_at: float | None = NOW,
    retry_at: float | None = None,
    reason_code: str | None = None,
    has_last_known_good: bool | None = None,
    effect: QuotaEffect = QuotaEffect.ALL_WORKLOADS,
    model: str | None = None,
    opaque_scope: str = "all",
    account_discriminator: str | None = "account-a",
) -> QuotaLaneObservation:
    source = _source(provider_id, source_instance_id)
    if has_last_known_good is None:
        has_last_known_good = value_state in {
            ObservationState.STALE,
            ObservationState.LAST_KNOWN_GOOD,
        }
    health = _health(
        source,
        kind=health_kind,
        observed_at=health_observed_at,
        last_attempt_at=last_attempt_at,
        retry_at=retry_at,
        reason_code=reason_code,
        has_last_known_good=has_last_known_good,
    )
    return QuotaLaneObservation(
        key=QuotaLaneKey(
            source=source,
            opaque_scope=opaque_scope,
            pool="general",
            model=model,
            window=window,
            effect=effect,
        ),
        semantic_name=semantic_name,
        horizon=horizon,
        value=CapacityValue(
            unit=CapacityUnit.PERCENT_REMAINING,
            remaining=remaining,
            state=value_state,
        ),
        reset=_reset(reset_state, reset_epoch=reset_epoch),
        observed_at=health_observed_at,
        source_health=health,
        account_discriminator=account_discriminator,
    )


def _snapshot(*lanes: QuotaLaneObservation) -> CapacitySnapshot:
    health = {lane.key.source: lane.source_health for lane in lanes}
    return CapacitySnapshot(
        observed_at=NOW,
        lanes=tuple(lanes),
        source_health=tuple(health.values()),
    )


def _projection(*lanes: QuotaLaneObservation) -> CapacityProjection:
    providers = tuple(dict.fromkeys(lane.key.source.provider_id for lane in lanes))
    instances = tuple(dict.fromkeys(lane.key.source.source_instance_id for lane in lanes))
    # The scope is the exact source of every lane, not every provider crossed
    # with every instance -- the cross product admits sources no build has.
    scopes = tuple(
        dict.fromkeys(
            (lane.key.source.provider_id, lane.key.source.source_instance_id)
            for lane in lanes
        )
    )
    return select_binding_lanes(
        _snapshot(*lanes),
        ExecutionContext(providers, instances, "gpt-5", None, scopes),
        NOW,
        allow_unbound_legacy=True,
    )


def _all_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, tuple):
        return " | ".join(_all_text(item) for item in value)
    if hasattr(value, "__dataclass_fields__"):
        return " | ".join(_all_text(getattr(value, field.name)) for field in fields(value))
    return ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            CapacityValue(CapacityUnit.PERCENT_REMAINING, 0.0, ObservationState.OBSERVED_ZERO),
            "0% left",
        ),
        (
            CapacityValue(CapacityUnit.PERCENT_REMAINING, 100.0, ObservationState.OBSERVED),
            "100% left",
        ),
        (
            CapacityValue(CapacityUnit.PERCENT_REMAINING, 0.4, ObservationState.OBSERVED),
            "<1% left",
        ),
        (
            CapacityValue(CapacityUnit.PERCENT_REMAINING, None, ObservationState.NULL),
            "No usage value reported",
        ),
        (
            CapacityValue(CapacityUnit.PERCENT_REMAINING, None, ObservationState.UNAVAILABLE),
            "Unavailable",
        ),
        (
            CapacityValue(CapacityUnit.PERCENT_REMAINING, None, ObservationState.PARTIAL),
            "Partial",
        ),
        (
            CapacityValue(CapacityUnit.PERCENT_REMAINING, 35.0, ObservationState.PARTIAL),
            "35% left, partial",
        ),
        (
            CapacityValue(CapacityUnit.PERCENT_REMAINING, 40.0, ObservationState.STALE),
            "40% left",
        ),
        (
            CapacityValue(CapacityUnit.PERCENT_REMAINING, 45.0, ObservationState.LAST_KNOWN_GOOD),
            "45% left",
        ),
    ],
)
def test_remaining_copy_preserves_every_typed_truth_state(
    value: CapacityValue,
    expected: str,
) -> None:
    text = format_remaining(value)

    assert text == expected
    assert "used" not in text.lower()


def test_unavailable_capacity_never_renders_as_observed_zero() -> None:
    unavailable = format_remaining(CapacityValue(CapacityUnit.PERCENT_REMAINING, None, ObservationState.UNAVAILABLE))
    null = format_remaining(CapacityValue(CapacityUnit.PERCENT_REMAINING, None, ObservationState.NULL))

    assert unavailable == "Unavailable"
    assert null == "No usage value reported"
    assert "0%" not in unavailable
    assert "0%" not in null


@pytest.mark.parametrize(
    ("reset", "now", "expected"),
    [
        (_reset(ResetState.FUTURE), NOW, "Resets in 1h 3m"),
        (_reset(ResetState.FUTURE, reset_epoch=NOW + 1.0), NOW + 2.0, "Resets now"),
        (_reset(ResetState.DUE), NOW, "Resets now"),
        (_reset(ResetState.UNKNOWN), NOW, "Reset unknown"),
        (_reset(ResetState.UNAVAILABLE), NOW, "Reset unavailable"),
        (_reset(ResetState.DISPUTED), NOW, "Reset disputed"),
        (_reset(ResetState.STALE), NOW, "Reset stale"),
    ],
)
def test_reset_copy_uses_typed_state_and_injected_clock(
    reset: ResetFact,
    now: float,
    expected: str,
) -> None:
    assert format_reset(reset, now) == expected


def test_freshness_copy_is_relative_bounded_and_marks_stale_truth() -> None:
    source = _source()
    healthy = _health(source, observed_at=NOW - 125.0, last_attempt_at=NOW - 30.0)
    stale = _health(
        source,
        kind=SourceHealthKind.TIMED_OUT,
        observed_at=NOW - 7_200.0,
        last_attempt_at=NOW - 60.0,
        has_last_known_good=True,
    )

    assert format_freshness(healthy.observed_at, healthy, NOW) == "Updated 2m ago"
    assert format_freshness(stale.observed_at, stale, NOW) == "Updated 2h ago, stale"
    assert format_freshness(NOW + 1.0, healthy, NOW) == "Update time unavailable"


def test_card_preserves_provider_window_remaining_reset_and_stale_marker() -> None:
    short = _lane(remaining=0.0, value_state=ObservationState.OBSERVED_ZERO)
    long = _lane(
        provider_id="claude",
        source_instance_id="remote:primary",
        window="weekly",
        semantic_name="Weekly window",
        horizon=QuotaHorizon.LONG,
        remaining=82.0,
        value_state=ObservationState.LAST_KNOWN_GOOD,
        # STALE, not TIMED_OUT. "Old but real" is the state this row exists to
        # render; a source that could not be read at all is a different claim
        # and no longer headlines the card at all (see below).
        health_kind=SourceHealthKind.STALE,
        health_observed_at=NOW - 7_200.0,
        last_attempt_at=NOW - 60.0,
    )

    card = build_capacity_card(_projection(long, short), NOW)

    assert card.heading == "Capacity"
    assert card.status_text is None
    assert len(card.rows) == 2
    assert [(row.provider, row.semantic_name) for row in card.rows] == [
        ("Codex", "Session window"),
        ("Claude", "Weekly window"),
    ]
    assert card.rows[0].remaining_text == "0% left"
    assert card.rows[0].reset_text == "Resets in 1h 3m"
    assert card.rows[1].remaining_text == "82% left"
    assert card.rows[1].stale is True
    assert card.rows[1].freshness_text == "Updated 2h ago, stale"
    assert "used" not in _all_text(card).lower()


@pytest.mark.parametrize(
    "health_kind",
    (
        SourceHealthKind.FAILED,
        SourceHealthKind.TIMED_OUT,
        SourceHealthKind.ACCESS_DENIED,
        SourceHealthKind.SIGN_IN_REQUIRED,
    ),
)
def test_a_source_that_could_not_be_read_never_headlines_the_card(
    health_kind: SourceHealthKind,
) -> None:
    """A retained reading is a memory, and a memory does not get to speak.

    `_value_refusal` forgives an unreachable source that still holds a
    last-known-good number so the ledger can keep showing it. That
    forgiveness used to make the lane bindable too, so a source that had just
    answered ACCESS_DENIED headlined the card exactly like a live one.
    """
    unreachable = _lane(
        provider_id="claude",
        source_instance_id="remote:primary",
        window="weekly",
        semantic_name="Weekly window",
        horizon=QuotaHorizon.LONG,
        remaining=82.0,
        value_state=ObservationState.LAST_KNOWN_GOOD,
        health_kind=health_kind,
        health_observed_at=NOW - 7_200.0,
        last_attempt_at=NOW - 60.0,
    )

    projection = _projection(unreachable, _lane(remaining=25.0))
    card = build_capacity_card(projection, NOW)

    assert [row.provider for row in card.rows] == ["Codex"]
    # It is still carried for detail, with the reason attached.
    withheld = next(
        row for row in projection.detail_lanes if row.lane.key == unreachable.key
    )
    assert withheld.bindable is False
    assert withheld.refusal_code == f"source_{health_kind.value}"


def test_card_has_distinct_no_source_null_partial_and_unavailable_status() -> None:
    empty = build_capacity_card(CapacityProjection((), ()), NOW)
    null = build_capacity_card(_projection(_lane(remaining=None, value_state=ObservationState.NULL)), NOW)
    partial = build_capacity_card(
        _projection(_lane(remaining=35.0, value_state=ObservationState.PARTIAL)),
        NOW,
    )
    unavailable = build_capacity_card(
        _projection(_lane(remaining=None, value_state=ObservationState.UNAVAILABLE)),
        NOW,
    )

    assert empty.rows == ()
    assert empty.status_text == "No capacity sources"
    assert null.status_text == "Usage unavailable"
    assert partial.status_text == "Capacity partial"
    assert unavailable.status_text == "Capacity unavailable"
    assert "0%" not in " | ".join(item.status_text or "" for item in (empty, null, partial, unavailable))


def test_card_model_refuses_a_third_root_row_even_when_constructed_directly() -> None:
    row = CapacityCardRowModel(
        provider="Codex",
        semantic_name="Session window",
        remaining_text="50% left",
        reset_text="Reset unknown",
        freshness_text="Updated just now",
        stale=False,
    )

    with pytest.raises(ValueError, match="at most two"):
        CapacityCardModel("Capacity", (row, row, row), None)
    assert MAX_CAPACITY_CARD_ROWS == 2


def test_card_refuses_a_hand_built_nonbinding_root_row() -> None:
    lane = _lane(remaining=None, value_state=ObservationState.UNAVAILABLE)
    projection = _projection(lane)
    authority = projection.detail_lanes[0]

    with pytest.raises(ValueError, match="binding authority"):
        build_capacity_card(
            CapacityProjection((authority,), projection.detail_lanes),
            NOW,
        )


def test_card_rows_are_deterministic_across_snapshot_input_permutations() -> None:
    lanes = (
        _lane(window="alpha", remaining=25.0),
        _lane(
            provider_id="claude",
            source_instance_id="remote:primary",
            window="weekly",
            semantic_name="Weekly window",
            horizon=QuotaHorizon.LONG,
            remaining=60.0,
        ),
        _lane(window="bravo", remaining=70.0),
    )

    rows = {
        tuple(
            (row.provider, row.semantic_name, row.remaining_text)
            for row in build_capacity_card(_projection(*order), NOW).rows
        )
        for order in permutations(lanes)
    }

    assert rows == {
        (
            ("Codex", "Session window", "25% left"),
            ("Claude", "Weekly window", "60% left"),
        )
    }


def test_detail_groups_every_lane_by_provider_and_applicability() -> None:
    codex = _lane(window="session", remaining=20.0)
    ambiguous = _lane(
        window="model-weekly",
        semantic_name="Model weekly",
        horizon=QuotaHorizon.LONG,
        remaining=70.0,
        effect=QuotaEffect.MODEL,
        model="gpt-5",
        opaque_scope="model:gpt-5",
    )
    claude = _lane(
        provider_id="claude",
        source_instance_id="remote:primary",
        window="weekly",
        semantic_name="Weekly window",
        horizon=QuotaHorizon.LONG,
        remaining=None,
        value_state=ObservationState.UNAVAILABLE,
    )
    snapshot = _snapshot(claude, ambiguous, codex)
    projection = select_binding_lanes(
        snapshot,
        ExecutionContext(
            provider_ids=("codex", "claude"),
            source_instances=("local:primary", "remote:primary"),
            selected_model=None,
            selected_feature=None,
            source_scopes=(
                ("codex", "local:primary"),
                ("claude", "remote:primary"),
            ),
        ),
        NOW,
        allow_unbound_legacy=True,
    )

    detail = build_capacity_detail(snapshot, projection, None, NOW)

    assert [group.provider for group in detail.providers] == ["Claude", "Codex"]
    assert [group.applicability for group in detail.providers[0].groups] == [LaneApplicability.APPLICABLE]
    assert [group.applicability for group in detail.providers[1].groups] == [
        LaneApplicability.APPLICABLE,
        LaneApplicability.AMBIGUOUS,
    ]
    rows = tuple(row for provider in detail.providers for group in provider.groups for row in group.rows)
    assert len(rows) == 3
    assert any(row.remaining_text == "Unavailable" for row in rows)
    assert any(row.applicability_text == "Execution context needed" for row in rows)
    assert "used" not in _all_text(detail).lower()


def test_detail_projects_source_success_attempt_cooldown_and_typed_health() -> None:
    lane = _lane(
        health_kind=SourceHealthKind.COOLDOWN,
        health_observed_at=NOW - 3_600.0,
        last_attempt_at=NOW - 30.0,
        retry_at=NOW + 125.0,
        has_last_known_good=True,
    )
    snapshot = _snapshot(lane)

    detail = build_capacity_detail(snapshot, _projection(lane), None, NOW)

    assert len(detail.source_health) == 1
    health = detail.source_health[0]
    assert health.provider == "Codex"
    assert health.status_text == "Cooling down"
    assert health.last_success_text == "Last successful observation retained"
    assert health.last_attempt_text == "Last attempt just now"
    assert health.cooldown_text == "Cooldown ends in 3m"
    assert health.has_last_known_good is True


def test_detail_uses_refresh_event_times_against_the_injected_render_clock() -> None:
    lane = _lane()
    snapshot = _snapshot(lane)
    refresh_key = RefreshSourceKey(lane.key.source, lane.key.pool, lane.account_discriminator)
    refresh_state = RefreshSourceState(
        key=refresh_key,
        enabled=True,
        supported=True,
        generation=3,
        in_flight=False,
        active_cause=None,
        deadline=None,
        queued_manual=True,
        status=RefreshStatusKind.COOLDOWN,
        last_attempt_at=80.0,
        last_success_at=50.0,
        retry_at=220.0,
        retry_schedule=None,
        consecutive_failures=1,
        last_failure=RefreshFailureKind.FAILED,
        last_known_good=snapshot,
        has_last_known_good=True,
    )
    detail_snapshot = CapacityDetailSnapshot(
        capacity=snapshot,
        refresh=RefreshCoordinatorSnapshot(100.0, (refresh_state,)),
        refresh_now=230.0,
    )

    detail = build_capacity_detail(detail_snapshot, _projection(lane), None, NOW)

    assert len(detail.source_health) == 1
    health = detail.source_health[0]
    assert health.status_text == "Cooling down"
    assert health.last_success_text == "Last success 3m ago"
    assert health.last_attempt_text == "Last attempt 2m ago"
    assert health.cooldown_text == "Cooldown ends now"


def test_detail_refuses_refresh_sources_outside_the_capacity_snapshot() -> None:
    lane = _lane()
    snapshot = _snapshot(lane)
    foreign_source = _source("claude", "remote:primary")
    foreign_state = RefreshSourceState(
        key=RefreshSourceKey(foreign_source, "general", "account-b"),
        enabled=True,
        supported=True,
        generation=0,
        in_flight=False,
        active_cause=None,
        deadline=None,
        queued_manual=False,
        status=RefreshStatusKind.IDLE,
        last_attempt_at=None,
        last_success_at=None,
        retry_at=None,
        retry_schedule=None,
        consecutive_failures=0,
        last_failure=None,
        last_known_good=None,
        has_last_known_good=False,
    )
    detail_snapshot = CapacityDetailSnapshot(
        capacity=snapshot,
        refresh=RefreshCoordinatorSnapshot(NOW, (foreign_state,)),
        refresh_now=NOW,
    )

    with pytest.raises(ValueError, match="refresh source does not match capacity snapshot"):
        build_capacity_detail(detail_snapshot, _projection(lane), None, NOW)


@pytest.mark.parametrize(
    "refresh_key",
    (
        RefreshSourceKey(_source(), "alternate", "account-a"),
        RefreshSourceKey(_source(), "general", "account-b"),
    ),
)
def test_detail_refuses_refresh_scope_that_does_not_match_a_capacity_lane(
    refresh_key: RefreshSourceKey,
) -> None:
    lane = _lane()
    snapshot = _snapshot(lane)
    state = RefreshSourceState(
        key=refresh_key,
        enabled=True,
        supported=True,
        generation=0,
        in_flight=False,
        active_cause=None,
        deadline=None,
        queued_manual=False,
        status=RefreshStatusKind.IDLE,
        last_attempt_at=None,
        last_success_at=None,
        retry_at=None,
        retry_schedule=None,
        consecutive_failures=0,
        last_failure=None,
        last_known_good=None,
        has_last_known_good=False,
    )
    detail_snapshot = CapacityDetailSnapshot(
        snapshot,
        RefreshCoordinatorSnapshot(NOW, (state,)),
        NOW,
    )

    with pytest.raises(ValueError, match="refresh source does not match capacity snapshot"):
        build_capacity_detail(detail_snapshot, _projection(lane), None, NOW)


def test_detail_snapshot_requires_a_current_clock_for_refresh_state() -> None:
    snapshot = _snapshot(_lane())
    refresh = RefreshCoordinatorSnapshot(100.0, ())

    with pytest.raises(ValueError, match="provided together"):
        CapacityDetailSnapshot(snapshot, refresh)
    with pytest.raises(ValueError, match="provided together"):
        CapacityDetailSnapshot(snapshot, None, 100.0)
    with pytest.raises(ValueError, match="precedes snapshot"):
        CapacityDetailSnapshot(snapshot, refresh, 99.0)


def test_detail_accepts_refresh_health_for_an_explicit_empty_source_snapshot() -> None:
    source = _source()
    health = _health(source)
    snapshot = CapacitySnapshot(NOW, (), (health,))
    state = RefreshSourceState(
        key=RefreshSourceKey(source, "general", "account-a"),
        enabled=True,
        supported=True,
        generation=0,
        in_flight=False,
        active_cause=None,
        deadline=None,
        queued_manual=False,
        status=RefreshStatusKind.HEALTHY,
        last_attempt_at=90.0,
        last_success_at=90.0,
        retry_at=None,
        retry_schedule=None,
        consecutive_failures=0,
        last_failure=None,
        last_known_good=None,
        has_last_known_good=False,
    )
    detail_snapshot = CapacityDetailSnapshot(
        snapshot,
        RefreshCoordinatorSnapshot(100.0, (state,)),
        120.0,
    )

    detail = build_capacity_detail(
        detail_snapshot,
        CapacityProjection((), ()),
        None,
        NOW,
    )

    assert detail.providers == ()
    assert [(row.provider, row.status_text) for row in detail.source_health] == [("Codex", "Healthy")]


def test_detail_refuses_multiple_refresh_scopes_for_one_health_only_source() -> None:
    source = _source()
    snapshot = CapacitySnapshot(NOW, (), (_health(source),))

    def idle_state(account: str) -> RefreshSourceState:
        return RefreshSourceState(
            key=RefreshSourceKey(source, "general", account),
            enabled=True,
            supported=True,
            generation=0,
            in_flight=False,
            active_cause=None,
            deadline=None,
            queued_manual=False,
            status=RefreshStatusKind.IDLE,
            last_attempt_at=None,
            last_success_at=None,
            retry_at=None,
            retry_schedule=None,
            consecutive_failures=0,
            last_failure=None,
            last_known_good=None,
            has_last_known_good=False,
        )

    refresh = RefreshCoordinatorSnapshot(
        NOW,
        (idle_state("account-a"), idle_state("account-b")),
    )
    detail_snapshot = CapacityDetailSnapshot(snapshot, refresh, NOW)

    with pytest.raises(ValueError, match="multiple refresh scopes for capacity source"):
        build_capacity_detail(
            detail_snapshot,
            CapacityProjection((), ()),
            None,
            NOW,
        )


def test_detail_renders_only_bounded_metadata_history_summaries_when_enabled() -> None:
    lane = _lane()
    snapshot = _snapshot(lane)
    history = CapacityHistoryPresentation(
        enabled=True,
        summaries=(
            CapacityHistorySummaryInput(
                HistoryInterval.DAY,
                CapacityHistorySummary(4, 1, 20.0, 80.0, ()),
            ),
            CapacityHistorySummaryInput(
                HistoryInterval.SEVEN_DAYS,
                CapacityHistorySummary(0, 0, NO_OBSERVATION, NO_OBSERVATION, ()),
            ),
        ),
    )

    detail = build_capacity_detail(snapshot, _projection(lane), history, NOW)

    assert detail.history_enabled is True
    assert [(row.label, row.summary_text) for row in detail.history] == [
        ("Day", "4 observations, 1 confirmed reset, 20% to 80% left"),
        ("7 days", "No observation"),
    ]
    assert "used" not in _all_text(detail).lower()


def test_history_presentation_is_bounded_to_three_unique_ranges() -> None:
    summary = CapacityHistorySummary(0, 0, NO_OBSERVATION, NO_OBSERVATION, ())
    duplicate = CapacityHistorySummaryInput(HistoryInterval.DAY, summary)

    with pytest.raises(ValueError, match="unique"):
        CapacityHistoryPresentation(True, (duplicate, duplicate))
    with pytest.raises(ValueError, match="at most three"):
        CapacityHistoryPresentation(
            True,
            tuple(
                CapacityHistorySummaryInput(HistoryInterval.DAY, summary)
                for _ in range(MAX_CAPACITY_HISTORY_SUMMARIES + 1)
            ),
        )


@pytest.mark.parametrize(
    ("decision", "expected", "can_request"),
    [
        (
            RefreshDecision(
                RefreshDecisionKind.START,
                RefreshSourceKey(_source(), "general", "account-a"),
                RefreshCause.MANUAL,
                1,
                None,
                RefreshDecisionReason.ELIGIBLE,
            ),
            "Refreshing",
            True,
        ),
        (
            RefreshDecision(
                RefreshDecisionKind.COALESCED,
                RefreshSourceKey(_source(), "general", "account-a"),
                RefreshCause.MANUAL,
                1,
                None,
                RefreshDecisionReason.IN_FLIGHT,
            ),
            "Refresh already in progress",
            True,
        ),
        (
            RefreshDecision(
                RefreshDecisionKind.QUEUED_FOR_COOLDOWN,
                RefreshSourceKey(_source(), "general", "account-a"),
                RefreshCause.MANUAL,
                None,
                NOW + 125.0,
                RefreshDecisionReason.COOLDOWN,
            ),
            "Refresh queued for 3m",
            True,
        ),
        (
            RefreshDecision(
                RefreshDecisionKind.COALESCED,
                RefreshSourceKey(_source(), "general", "account-a"),
                RefreshCause.MANUAL,
                None,
                NOW + 125.0,
                RefreshDecisionReason.ALREADY_QUEUED,
            ),
            "Refresh already queued for 3m",
            True,
        ),
        (
            RefreshDecision(
                RefreshDecisionKind.DISABLED,
                RefreshSourceKey(_source(), "general", "account-a"),
                RefreshCause.MANUAL,
                None,
                None,
                RefreshDecisionReason.DISABLED,
            ),
            "Capacity refresh disabled",
            False,
        ),
        (
            RefreshDecision(
                RefreshDecisionKind.UNSUPPORTED,
                RefreshSourceKey(_source(), "general", "account-a"),
                RefreshCause.MANUAL,
                None,
                None,
                RefreshDecisionReason.UNSUPPORTED,
            ),
            "Capacity refresh unsupported",
            False,
        ),
    ],
)
def test_manual_refresh_outcomes_are_typed_distinct_and_actionable_only_when_accepted(
    decision: RefreshDecision,
    expected: str,
    can_request: bool,
) -> None:
    assert format_refresh_outcome(decision, NOW) == expected
    status = build_manual_refresh_status(decision, NOW)
    assert status.text == expected
    assert status.can_request is can_request
    assert status.announcement_minute == int(NOW // 60)


def test_copy_projection_does_no_filesystem_subprocess_or_network_work(monkeypatch: pytest.MonkeyPatch) -> None:
    lane = _lane()
    snapshot = _snapshot(lane)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("presentation attempted external work")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)

    card = build_capacity_card(_projection(lane), NOW)
    detail = build_capacity_detail(snapshot, _projection(lane), None, NOW)

    assert card.rows[0].remaining_text == "50% left"
    assert detail.providers[0].groups[0].rows[0].remaining_text == "50% left"


def test_untrusted_diagnostic_fields_never_enter_copy() -> None:
    lane = _lane(
        semantic_name="/Users/private/Bearer token raw-error",
        reason_code="raw-error",
        account_discriminator="acct:opaque-adversary",
        source_instance_id="private:source",
        remaining=None,
        value_state=ObservationState.UNAVAILABLE,
        health_kind=SourceHealthKind.FAILED,
    )
    snapshot = _snapshot(lane)
    projection = _projection(lane)

    card = build_capacity_card(projection, NOW)
    detail = build_capacity_detail(snapshot, projection, None, NOW)
    copy = _all_text((card, detail)).lower()

    assert "users/private" not in copy
    assert "bearer" not in copy
    assert "raw-error" not in copy
    assert "acct:opaque-adversary" not in copy
    assert "private:source" not in copy
    assert "short window" in copy


def test_detail_rejects_projection_from_another_snapshot() -> None:
    first = _lane(window="first")
    second = _lane(window="second")

    with pytest.raises(ValueError, match="projection does not match snapshot"):
        build_capacity_detail(_snapshot(first), _projection(second), None, NOW)


def test_card_refuses_nonfinite_clock() -> None:
    projection = _projection(_lane())

    with pytest.raises(ValueError, match="finite nonnegative"):
        build_capacity_card(projection, float("nan"))
    with pytest.raises(ValueError, match="finite nonnegative"):
        format_reset(_reset(), -1.0)
