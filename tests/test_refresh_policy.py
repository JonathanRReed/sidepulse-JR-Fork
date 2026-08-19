from __future__ import annotations

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.providers import negotiated_provider_sources
from sidepulse.refresh_policy import (
    ProviderRefreshState,
    RetryScheduleKind,
    mark_refresh_failed,
    mark_refresh_succeeded,
    plan_menu_open_refresh,
    resolve_retry_schedule,
    retain_attempted_boundary_keys,
    retry_delay_seconds,
)


def source(
    provider_id: str,
    adapter_id: str = "hooks",
    source_instance_id: str = "global",
    capability_id: str = "live_agent_events",
) -> SourceKey:
    return SourceKey(provider_id, adapter_id, source_instance_id, capability_id)


def state(source_key: SourceKey, **changes) -> ProviderRefreshState:
    values = {
        "source_key": source_key,
        "enabled": True,
        "visible": True,
        "last_success_at": None,
    }
    values.update(changes)
    return ProviderRefreshState(**values)


def test_menu_open_selects_stale_and_missing_but_not_fresh_providers() -> None:
    states = (
        state(source("fresh"), last_success_at=800.0),
        state(source("stale"), last_success_at=699.0),
        state(source("missing")),
    )

    plan = plan_menu_open_refresh(states, now=1_000.0, low_power=False)

    assert plan.invocations == (source("stale"), source("missing"))


def test_menu_open_excludes_ineligible_in_flight_and_backed_off_providers() -> None:
    states = (
        state(source("disabled"), enabled=False),
        state(source("hidden"), visible=False),
        state(source("busy"), in_flight=True),
        state(source("backoff"), retry_not_before=1_001.0),
        state(source("ready"), retry_not_before=1_000.0),
    )

    plan = plan_menu_open_refresh(states, now=1_000.0, low_power=False)

    assert plan.invocations == (source("ready"),)


def test_exact_freshness_boundary_is_due() -> None:
    plan = plan_menu_open_refresh(
        (state(source("codex"), last_success_at=700.0),),
        now=1_000.0,
        low_power=False,
    )

    assert plan.invocations == (source("codex"),)


def test_low_power_extends_freshness_but_never_blocks_first_load() -> None:
    states = (
        state(source("cached"), last_success_at=600.0),
        state(source("missing")),
    )

    normal = plan_menu_open_refresh(states, now=1_000.0, low_power=False)
    low_power = plan_menu_open_refresh(states, now=1_000.0, low_power=True)

    assert normal.invocations == (source("cached"), source("missing"))
    assert low_power.invocations == (source("missing"),)


def test_implausibly_future_success_is_treated_as_missing() -> None:
    plan = plan_menu_open_refresh(
        (state(source("codex"), last_success_at=1_006.0),),
        now=1_000.0,
        low_power=False,
    )

    assert plan.invocations == (source("codex"),)


def test_nonfinite_success_timestamp_cannot_stay_fresh() -> None:
    plan = plan_menu_open_refresh(
        (state(source("codex"), last_success_at=float("nan")),),
        now=1_000.0,
        low_power=False,
    )

    assert plan.invocations == (source("codex"),)


def test_retry_delay_doubles_and_caps_without_jitter() -> None:
    assert [retry_delay_seconds(count) for count in (0, 1, 2, 3, 6, 20)] == [
        0.0,
        15.0,
        30.0,
        60.0,
        300.0,
        300.0,
    ]


def test_plan_preserves_input_order_and_emits_duplicate_provider_once() -> None:
    first = source("claude")
    second = source("codex")
    third = source("gemini")
    plan = plan_menu_open_refresh(
        (state(first), state(second), state(first), state(third)),
        now=1_000.0,
        low_power=False,
    )

    assert plan.invocations == (first, second, third)


def test_failure_and_success_transitions_are_immutable_and_reset_backoff() -> None:
    original = state(source("claude"), last_success_at=800.0, in_flight=True)

    failed = mark_refresh_failed(original, now=1_000.0, error_text="temporary")
    recovered = mark_refresh_succeeded(failed, now=1_020.0)

    assert original.in_flight is True
    assert original.consecutive_failures == 0
    assert failed.in_flight is False
    assert failed.last_success_at == 800.0
    assert failed.consecutive_failures == 1
    assert failed.retry_not_before == 1_015.0
    assert failed.error_text == "temporary"
    assert recovered.last_success_at == 1_020.0
    assert recovered.consecutive_failures == 0
    assert recovered.retry_not_before == 0.0
    assert recovered.error_text is None


def test_provider_id_property_is_read_only_compatibility_projection() -> None:
    refresh_state = state(source("codex", "quota", "local", "remote_quota_windows"))

    assert refresh_state.provider_id == "codex"
    with pytest.raises((AttributeError, TypeError)):
        refresh_state.provider_id = "claude"  # type: ignore[misc]


def test_registry_capabilities_are_enumerated_without_collapsing_provider_rows() -> None:
    rows = negotiated_provider_sources()
    states = tuple(state(row.source_key) for row in rows)

    plan = plan_menu_open_refresh(states, now=1_000.0, low_power=False)

    assert plan.invocations == tuple(row.source_key for row in rows)
    assert len(plan.invocations) == len(rows) == 19


def test_same_provider_different_sources_remain_independently_due() -> None:
    quota = source("codex", "quota", "local", "remote_quota_windows")
    transcripts = source("codex", "transcripts", "local", "transcript_usage")

    plan = plan_menu_open_refresh(
        (
            state(quota, retry_not_before=1_001.0),
            state(transcripts, retry_not_before=1_000.0),
        ),
        now=1_000.0,
        low_power=False,
    )

    assert plan.invocations == (transcripts,)


def test_nonfinite_planning_time_schedules_nothing() -> None:
    refresh_state = state(source("codex"))

    assert plan_menu_open_refresh((refresh_state,), now=float("nan"), low_power=False).invocations == ()
    assert plan_menu_open_refresh((refresh_state,), now=float("inf"), low_power=False).invocations == ()


def test_nonfinite_retry_boundary_fails_closed() -> None:
    source_key = source("codex")
    states = (
        state(source_key, retry_not_before=float("nan")),
        state(source("claude"), retry_not_before=float("inf")),
    )

    assert plan_menu_open_refresh(states, now=1_000.0, low_power=False).invocations == ()


def test_boundary_attempt_retention_is_deterministic_and_capped_at_64() -> None:
    retained = retain_attempted_boundary_keys(
        (),
        tuple(f"boundary-{index:02d}" for index in range(70)),
    )

    assert len(retained) == 64
    assert retained[0] == "boundary-06"
    assert retained[-1] == "boundary-69"


def test_boundary_attempt_retention_deduplicates_and_normalizes_set_order() -> None:
    retained = retain_attempted_boundary_keys(
        {"boundary-b", "boundary-a"},
        {"boundary-d", "boundary-c", "boundary-b"},
    )

    assert retained == (
        "boundary-a",
        "boundary-b",
        "boundary-c",
        "boundary-d",
    )


def test_failure_retry_schedule_is_an_explicit_exponential_boundary() -> None:
    schedule = resolve_retry_schedule(
        completed_at=100.0,
        consecutive_failures=2,
        retry_at=None,
    )

    assert schedule.kind is RetryScheduleKind.EXPONENTIAL_BACKOFF
    assert schedule.retry_not_before == 130.0


def test_retry_after_zero_is_not_confused_with_an_absent_boundary() -> None:
    schedule = resolve_retry_schedule(
        completed_at=100.0,
        consecutive_failures=1,
        retry_at=0.0,
    )

    assert schedule.kind is RetryScheduleKind.RETRY_AFTER
    assert schedule.retry_not_before == 100.0


def test_retry_after_is_clamped_to_a_bounded_one_hour_horizon() -> None:
    schedule = resolve_retry_schedule(
        completed_at=100.0,
        consecutive_failures=1,
        retry_at=100_000.0,
    )

    assert schedule.kind is RetryScheduleKind.RETRY_AFTER
    assert schedule.retry_not_before == 3_700.0


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -1.0))
def test_retry_schedule_rejects_invalid_completion_time(value: float) -> None:
    with pytest.raises(ValueError):
        resolve_retry_schedule(
            completed_at=value,
            consecutive_failures=1,
            retry_at=None,
        )


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -1.0))
def test_retry_schedule_rejects_invalid_explicit_boundary(value: float) -> None:
    with pytest.raises(ValueError):
        resolve_retry_schedule(
            completed_at=100.0,
            consecutive_failures=1,
            retry_at=value,
        )
