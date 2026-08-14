from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime

import pytest

from sidepulse.capacity_types import (
    CapacityUnit,
    CapacityValue,
    ObservationState,
    QuotaEffect,
    QuotaLaneKey,
    ResetFact,
    ResetState,
    SourceKey,
)
from sidepulse.reset_policy import (
    ResetBoundaryPlan,
    ResetCountdown,
    derive_reset_countdown,
    format_reset_countdown,
    next_countdown_deadline,
    parse_reset_epoch,
    plan_reset_boundary_refresh,
)
from sidepulse.usage_view import UsageWindowViewModel, adapt_legacy_usage_windows


def _source(provider: str, instance: str = "local") -> SourceKey:
    return SourceKey(provider, "quota", instance, "remote_quota_windows")


def _lane(
    provider: str,
    scope: str,
    window: str,
    *,
    instance: str = "local",
) -> QuotaLaneKey:
    return QuotaLaneKey(
        _source(provider, instance),
        scope,
        "shared",
        None,
        window,
        QuotaEffect.ALL_WORKLOADS,
    )


def _typed_window(
    lane_key: QuotaLaneKey,
    reset_epoch: float,
    *,
    state: ResetState = ResetState.FUTURE,
) -> UsageWindowViewModel:
    return UsageWindowViewModel(
        lane_key=lane_key,
        provider_title=lane_key.source.provider_id.title(),
        label="Product-owned limit",
        window_minutes=300,
        capacity=CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            40.0,
            ObservationState.OBSERVED,
        ),
        reset_at=reset_epoch,
        reset_epoch=reset_epoch,
        reset_state=state,
    )


@pytest.mark.parametrize(
    ("reset", "now", "expected"),
    (
        (
            ResetFact(ResetState.FUTURE, 1_090.5, 300.0, 1_000.0),
            1_000.0,
            ResetCountdown(ResetState.FUTURE, 90.5, 0, 0, 2),
        ),
        (
            ResetFact(ResetState.FUTURE, 1_000.0, 300.0, 999.0),
            1_000.0,
            ResetCountdown(ResetState.DUE, 0.0, 0, 0, 0),
        ),
        (
            ResetFact(ResetState.DUE, 999.5, 300.0, 1_000.0),
            1_001.0,
            ResetCountdown(ResetState.DUE, 0.0, 0, 0, 0),
        ),
        (
            ResetFact(ResetState.UNKNOWN, None, 300.0, 1_000.0),
            1_001.0,
            ResetCountdown(ResetState.UNKNOWN, None, None, None, None),
        ),
        (
            ResetFact(ResetState.UNAVAILABLE, None, 300.0, 1_000.0),
            1_001.0,
            ResetCountdown(ResetState.UNAVAILABLE, None, None, None, None),
        ),
        (
            ResetFact(ResetState.DISPUTED, 1_100.0, 300.0, 1_000.0),
            1_001.0,
            ResetCountdown(ResetState.DISPUTED, None, None, None, None),
        ),
        (
            ResetFact(ResetState.STALE, 1_100.0, 300.0, 1_000.0),
            1_001.0,
            ResetCountdown(ResetState.STALE, None, None, None, None),
        ),
    ),
)
def test_typed_countdown_preserves_reset_truth_and_clamps_due(
    reset: ResetFact,
    now: float,
    expected: ResetCountdown,
) -> None:
    assert derive_reset_countdown(reset, now=now) == expected


def test_typed_countdown_uses_only_the_injected_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_clock_read() -> float:
        raise AssertionError("countdown read the process wall clock")

    monkeypatch.setattr(time, "time", unexpected_clock_read)
    reset = ResetFact(ResetState.FUTURE, 1_060.001, 300.0, 1_000.0)

    assert derive_reset_countdown(reset, now=1_000.0) == ResetCountdown(
        ResetState.FUTURE,
        60.001,
        0,
        0,
        2,
    )


def test_typed_countdown_uses_epoch_arithmetic_across_daylight_saving() -> None:
    before = datetime.fromisoformat("2026-03-08T01:30:00-06:00").timestamp()
    after = datetime.fromisoformat("2026-03-08T04:30:00-05:00").timestamp()
    reset = ResetFact(ResetState.FUTURE, after, 300.0, before)

    assert derive_reset_countdown(reset, now=before) == ResetCountdown(
        ResetState.FUTURE,
        7_200.0,
        0,
        2,
        0,
    )


def test_typed_countdown_recomputes_after_wall_clock_rollback() -> None:
    reset = ResetFact(ResetState.FUTURE, 2_000.0, 300.0, 1_000.0)

    before_rollback = derive_reset_countdown(reset, now=1_500.0)
    after_rollback = derive_reset_countdown(reset, now=1_440.0)

    assert before_rollback.remaining_seconds == 500.0
    assert after_rollback.remaining_seconds == 560.0


@pytest.mark.parametrize(
    ("value", "now", "expected"),
    (
        ("2026-01-01T00:00:00Z", 1_767_225_500.0, 1_767_225_600.0),
        ("2026-01-01T01:00:00+01:00", 1_767_225_500.0, 1_767_225_600.0),
        ("2026-01-01T00:00:00", 1_767_225_500.0, 1_767_225_600.0),
        (1_767_225_600, 1_767_225_500.0, 1_767_225_600.0),
        (1_767_225_600.5, 1_767_225_500.0, 1_767_225_600.5),
        (1_767_225_600_000, 1_767_225_500.0, 1_767_225_600.0),
        (100_000_000_000, 99_999_990.0, 100_000_000.0),
        (1_031_622_400.0, 1_000_000_000.0, 1_031_622_400.0),
    ),
)
def test_parse_reset_epoch_normalizes_supported_future_values(
    value,
    now: float,
    expected: float,
) -> None:
    assert parse_reset_epoch(value, now=now) == expected


@pytest.mark.parametrize(
    "value",
    (
        True,
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        "",
        "not-a-date",
        "2026-99-99T00:00:00Z",
        object(),
        None,
        1_000.0,
        999.0,
        1_031_622_400.001,
    ),
)
def test_parse_reset_epoch_rejects_malformed_nonfuture_and_implausible_values(
    value,
) -> None:
    assert parse_reset_epoch(value, now=1_000.0) is None


@pytest.mark.parametrize(
    ("reset_epoch", "expected"),
    (
        (None, None),
        (999.0, None),
        (1_000.0, None),
        (1_000.001, "now"),
        (1_059.999, "now"),
        (1_060.0, "in 1m"),
        (1_060.001, "in 2m"),
        (4_599.999, "in 1h"),
        (4_600.0, "in 1h"),
        (4_600.001, "in 1h 1m"),
        (87_399.999, "in 1d"),
        (87_400.0, "in 1d"),
        (91_000.0, "in 1d 1h"),
    ),
)
def test_reset_countdown_uses_literal_ceil_minute_boundaries(
    reset_epoch: float | None,
    expected: str | None,
) -> None:
    assert format_reset_countdown(reset_epoch, now=1_000.0) == expected


def test_next_countdown_deadline_is_the_earliest_displayed_text_transition() -> None:
    assert next_countdown_deadline((1_130.0,), now=1_000.0) == 1_010.0
    assert next_countdown_deadline((1_130.0, 1_045.0), now=1_000.0) == 1_010.0
    assert next_countdown_deadline((1_060.0,), now=1_000.0) == 1_060.0
    assert next_countdown_deadline((999.0, math.nan), now=1_000.0) is None


@pytest.mark.parametrize(
    ("remaining_seconds", "initial_text", "deadline_delay", "next_text"),
    (
        (91_800.0, "in 1d 2h", 1_800.0, "in 1d 1h"),
        (177_300.0, "in 2d 2h", 900.0, "in 2d 1h"),
    ),
)
def test_multi_day_countdown_deadline_follows_the_next_displayed_hour_transition(
    remaining_seconds: float,
    initial_text: str,
    deadline_delay: float,
    next_text: str,
) -> None:
    now = 1_000.0
    reset_epoch = now + remaining_seconds

    assert format_reset_countdown(reset_epoch, now=now) == initial_text
    deadline = next_countdown_deadline((reset_epoch,), now=now)
    assert deadline == now + deadline_delay
    assert format_reset_countdown(reset_epoch, now=deadline) == next_text


def test_reset_plan_groups_every_provider_at_the_earliest_shared_boundary() -> None:
    claude_short = _lane("claude", "scope:short", "five-hour")
    claude_long = _lane("claude", "scope:long", "weekly")
    codex_short = _lane("codex", "scope:short", "five-hour")
    codex_long = _lane("codex", "scope:long", "weekly")
    plan = plan_reset_boundary_refresh(
        (
            _typed_window(claude_short, 1_040.0),
            _typed_window(claude_long, 1_100.0),
            _typed_window(codex_short, 1_040.0),
            _typed_window(codex_long, 1_200.0),
        ),
        now=1_000.0,
        normal_refresh_deadline=1_500.0,
    )

    assert plan == ResetBoundaryPlan(
        deadline=1_042.0,
        source_keys=(claude_short.source, codex_short.source),
        lane_keys=(claude_short, codex_short),
    )


def test_reset_plan_enforces_minimum_delay_and_yields_to_normal_refresh() -> None:
    windows = (_typed_window(_lane("codex", "scope:short", "five-hour"), 1_001.0),)

    due_after = plan_reset_boundary_refresh(
        windows,
        now=1_000.0,
        normal_refresh_deadline=1_005.001,
    )
    due_first = plan_reset_boundary_refresh(
        windows,
        now=1_000.0,
        normal_refresh_deadline=1_005.0,
    )

    assert due_after.deadline == 1_005.0
    assert due_first == ResetBoundaryPlan(None, (), ())


def test_reset_plan_skips_attempted_keys_and_moves_to_next_boundary() -> None:
    codex_short = _lane("codex", "scope:short", "five-hour")
    codex_long = _lane("codex", "scope:long", "weekly")
    claude_short = _lane("claude", "scope:short", "five-hour")
    plan = plan_reset_boundary_refresh(
        (
            _typed_window(codex_short, 1_040.0),
            _typed_window(codex_long, 1_100.0),
            _typed_window(claude_short, 1_040.0),
        ),
        now=1_000.0,
        normal_refresh_deadline=None,
        attempted_lane_keys={codex_short, claude_short},
    )

    assert plan == ResetBoundaryPlan(
        deadline=1_102.0,
        source_keys=(codex_long.source,),
        lane_keys=(codex_long,),
    )


def test_reset_plan_preserves_caller_order_and_deduplicates_windows() -> None:
    claude_short = _lane("claude", "scope:short", "five-hour")
    codex_short = _lane("codex", "scope:short", "five-hour")
    claude_long = _lane("claude", "scope:long", "weekly")
    plan = plan_reset_boundary_refresh(
        (
            _typed_window(claude_short, 1_100.0),
            _typed_window(claude_short, 1_100.0),
            _typed_window(codex_short, 1_100.0),
            _typed_window(claude_long, 1_100.0),
        ),
        now=1_000.0,
        normal_refresh_deadline=1_500.0,
    )

    assert plan.provider_ids == ("claude", "codex")
    assert plan.lane_keys == (claude_short, codex_short, claude_long)


def test_reset_plan_ignores_malformed_and_expired_window_resets() -> None:
    plan = plan_reset_boundary_refresh(
        {
            "codex": (
                {"label": "bad", "reset_epoch": float("nan")},
                {"label": "past", "reset_epoch": 999.0},
                {"label": "far", "reset_epoch": 1_031_622_400.001},
            )
        },
        now=1_000.0,
        normal_refresh_deadline=None,
    )

    assert plan == ResetBoundaryPlan(None, (), ())


@pytest.mark.parametrize("state", (ResetState.STALE, ResetState.DISPUTED))
def test_reset_plan_ignores_typed_untrusted_boundaries(state: ResetState) -> None:
    window = _typed_window(
        _lane("codex", "scope:short", "five-hour"),
        1_040.0,
        state=state,
    )

    plan = plan_reset_boundary_refresh(
        {"codex": (window,)},
        now=1_000.0,
        normal_refresh_deadline=1_500.0,
    )

    assert plan == ResetBoundaryPlan(None, (), ())


def test_reset_plan_accepts_controller_object_windows_and_reset_alias_fallback() -> None:
    @dataclass(frozen=True)
    class ObjectWindow:
        label: str
        window_minutes: int
        reset_epoch: object
        reset_at: object = None

    direct = ObjectWindow("5-hour", 300, 1_040.0)
    alias_fallback = ObjectWindow("weekly", 10_080, "malformed", 1_040.0)

    plan = plan_reset_boundary_refresh(
        {"codex": (direct, alias_fallback)},
        now=1_000.0,
        normal_refresh_deadline=1_500.0,
    )

    assert plan.deadline == 1_042.0
    assert plan.provider_ids == ("codex",)
    assert len(plan.source_keys) == 1
    assert len(plan.lane_keys) == 2


def test_legacy_boundary_projection_matches_in_flight_epoch_key_without_losing_typed_keys(
) -> None:
    windows = adapt_legacy_usage_windows(
        "codex",
        "Codex",
        ({"label": "primary", "resets_at": 1_040.0},),
        now=1_000.0,
    )
    plan = plan_reset_boundary_refresh(
        windows,
        now=1_000.0,
        normal_refresh_deadline=1_500.0,
    )

    assert plan.source_keys == (
        SourceKey("codex", "quota", "local", "remote_quota_windows"),
    )
    assert len(plan.lane_keys) == 1
    assert plan.boundary_keys == (
        "codex|quota|local|remote_quota_windows|legacy:0|unspecified|"
        "unspecified|unspecified|all_workloads",
    )
    assert plan.boundary_keys == ("codex|primary|1040",)
    assert not (plan.boundary_keys != ("codex|primary|1040",))


def test_typed_boundary_identity_does_not_depend_on_delimiter_shaped_labels() -> None:
    first = _lane("codex", "scope:first", "five-hour", instance="desktop")
    second = _lane("codex", "scope:second", "five-hour", instance="laptop")
    plan = plan_reset_boundary_refresh(
        (
            _typed_window(first, 1_040.0),
            _typed_window(second, 1_040.0),
        ),
        now=1_000.0,
        normal_refresh_deadline=1_500.0,
    )

    assert plan.provider_ids == ("codex",)
    assert plan.source_keys == (first.source, second.source)
    assert len(set(plan.boundary_keys)) == 2


def test_reset_plan_ignores_empty_and_invalid_provider_groups() -> None:
    plan = plan_reset_boundary_refresh(
        (
            {"provider_id": "", "windows": ({"reset_epoch": 1_040.0},)},
            {"windows": ({"reset_epoch": 1_040.0},)},
            object(),
            {"provider_id": "codex", "windows": None},
        ),
        now=1_000.0,
        normal_refresh_deadline=1_500.0,
    )

    assert plan == ResetBoundaryPlan(None, (), ())
