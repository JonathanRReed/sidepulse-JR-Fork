from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from sidepulse.attention import (
    AttentionProjection,
    LifecycleMode,
    ProjectedAgentRow,
    SignalKind,
    TransientSignal,
)
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.presentation_policy import (
    FiniteCue,
    FiniteCueBudget,
    GlanceSemantic,
)
from sidepulse.signal_coordinator import FiniteCueCoordinator, FiniteSignalCoordinator


def failure(event_key: str) -> TransientSignal:
    return TransientSignal(
        event_key=event_key,
        kind=SignalKind.FAILURE,
        repetitions=2,
        source_agent_id="claude:agent:worker",
    )


def failure_with_repetitions(event_key: str, repetitions: object) -> TransientSignal:
    return TransientSignal(
        event_key=event_key,
        kind=SignalKind.FAILURE,
        repetitions=repetitions,  # type: ignore[arg-type]
        source_agent_id="claude:agent:worker",
    )


def projection_with(*signals: TransientSignal) -> AttentionProjection:
    return AttentionProjection(
        lifecycle_mode=LifecycleMode.FAILED_VISIBLE if signals else LifecycleMode.IDLE,
        actionable_attention=(),
        visible_rows=(),
        transient_signals=signals,
        dominant_provider="claude" if signals else None,
        click_target_agent_id=None,
    )


def actionable_projection(*signals: TransientSignal) -> AttentionProjection:
    source = AgentStatus(
        provider="codex",
        agent_id="codex:session:main",
        display_name="Codex main",
        mode=AgentMode.WAITING_FOR_INPUT,
        updated_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        event_name="PermissionRequest",
    )
    row = ProjectedAgentRow(
        agent_id=source.agent_id,
        provider=source.provider,
        display_name=source.display_name,
        lifecycle_mode=LifecycleMode.WAITING,
        actionable=True,
        is_subagent=False,
        updated_at=source.updated_at,
        source_status=source,
    )
    return replace(
        projection_with(*signals),
        lifecycle_mode=LifecycleMode.WAITING,
        actionable_attention=(row,),
        visible_rows=(row,),
        dominant_provider="codex",
        click_target_agent_id=source.agent_id,
    )


def test_failure_signal_plays_exactly_two_repetitions_then_restores() -> None:
    coordinator = FiniteSignalCoordinator(failure_cycle_seconds=1.0)

    assert coordinator.observe(projection_with(failure("event-1")), now=10.0)
    active = coordinator.active(10.0)

    assert active is not None
    assert active.signal.event_key == "event-1"
    assert active.started_at == 10.0
    assert active.ends_at == 12.0
    assert coordinator.next_deadline == 12.0
    assert coordinator.active(11.99) is active
    assert coordinator.active(12.0) is None
    assert coordinator.next_deadline is None


def test_warm_state_watermark_does_not_replay_restored_failure() -> None:
    coordinator = FiniteSignalCoordinator(failure_cycle_seconds=1.0)
    restored = projection_with(failure("old-event"))

    coordinator.establish_watermark(restored, now=20.0)

    assert not coordinator.observe(restored, now=21.0)
    assert coordinator.active(21.0) is None
    assert coordinator.consumed_event_keys == ("old-event",)


def test_duplicate_observation_never_extends_active_deadline() -> None:
    coordinator = FiniteSignalCoordinator(failure_cycle_seconds=1.0)
    observed = projection_with(failure("event-1"))

    coordinator.observe(observed, now=10.0)
    coordinator.observe(observed, now=11.5)

    assert coordinator.next_deadline == 12.0
    assert coordinator.active(12.0) is None


def test_consumed_keys_are_bounded_by_count_and_age() -> None:
    coordinator = FiniteSignalCoordinator(
        failure_cycle_seconds=0.5,
        max_consumed_keys=2,
        consumed_ttl_seconds=5.0,
    )

    coordinator.observe(projection_with(failure("event-1")), now=0.0)
    coordinator.active(1.0)
    coordinator.observe(projection_with(failure("event-2")), now=2.0)
    coordinator.active(3.0)
    coordinator.observe(projection_with(failure("event-3")), now=4.0)

    assert coordinator.consumed_event_keys == ("event-2", "event-3")

    coordinator.active(5.0)
    coordinator.observe(projection_with(failure("event-4")), now=10.0)
    assert coordinator.consumed_event_keys == ("event-4",)


def test_many_failures_coalesce_to_one_pending_cue_and_cannot_extend_burst() -> None:
    coordinator = FiniteSignalCoordinator(
        failure_cycle_seconds=1.0,
        max_signals_per_burst=2,
    )

    coordinator.observe(
        projection_with(failure("event-1"), failure("event-2"), failure("event-3")),
        now=10.0,
    )

    assert coordinator.active(10.0).signal.event_key == "event-1"
    assert coordinator.active(12.0).signal.event_key == "event-2"
    assert coordinator.next_deadline == 14.0
    assert coordinator.active(14.0) is None
    assert coordinator.active(30.0) is None
    assert coordinator.consumed_event_keys == ("event-1", "event-2", "event-3")


def test_actionable_attention_preempts_failure_without_replay_after_resolution() -> None:
    coordinator = FiniteSignalCoordinator(failure_cycle_seconds=1.0)
    observed = failure("event-1")

    coordinator.observe(projection_with(observed), now=10.0)
    assert coordinator.active(10.5) is not None

    coordinator.observe(actionable_projection(observed), now=10.5)
    assert coordinator.active(10.5) is None
    assert coordinator.next_deadline is None

    assert not coordinator.observe(projection_with(observed), now=11.0)
    assert coordinator.active(11.0) is None


def test_unchanged_visible_failure_does_not_replay_after_consumed_ttl_expires() -> None:
    coordinator = FiniteSignalCoordinator(
        failure_cycle_seconds=1.0,
        consumed_ttl_seconds=2.0,
    )
    observed = projection_with(failure("event-1"))

    coordinator.observe(observed, now=10.0)
    assert coordinator.active(12.0) is None

    assert not coordinator.observe(observed, now=20.0)
    assert coordinator.active(20.0) is None


@pytest.mark.parametrize("repetitions", (True, False, 0, -1, 3, 1.5, "2"))
def test_legacy_coordinator_rejects_nonexact_or_over_budget_repetitions(
    repetitions: object,
) -> None:
    coordinator = FiniteSignalCoordinator(failure_cycle_seconds=1.0)
    invalid = projection_with(failure_with_repetitions("invalid", repetitions))

    assert not coordinator.observe(invalid, now=10.0)
    assert coordinator.active(10.0) is None
    assert coordinator.consumed_event_keys == ()


@pytest.mark.parametrize("invalid_now", (math.nan, math.inf, -1.0, 1_700_000_000.0))
def test_legacy_coordinator_rejects_invalid_or_wall_clock_time_without_mutation(
    invalid_now: float,
) -> None:
    coordinator = FiniteSignalCoordinator(failure_cycle_seconds=1.0)
    valid = projection_with(failure("valid"))

    assert not coordinator.observe(valid, now=invalid_now)
    assert coordinator.active(invalid_now) is None
    coordinator.establish_watermark(valid, now=invalid_now)
    assert coordinator.consumed_event_keys == ()


def test_legacy_valid_two_repeat_failure_behavior_is_preserved() -> None:
    coordinator = FiniteSignalCoordinator(failure_cycle_seconds=1.0)

    assert coordinator.observe(projection_with(failure("valid")), now=10.0)

    active = coordinator.active(10.0)
    assert active is not None
    assert active.signal.repetitions == 2
    assert active.ends_at == 12.0
    assert coordinator.active(12.0) is None


def cue(
    event_key: str,
    semantic: GlanceSemantic = GlanceSemantic.FRESH_FAILURE,
    *,
    repetitions: int = 2,
    duration_seconds: float = 0.5,
) -> FiniteCue:
    return FiniteCue(event_key, semantic, repetitions, duration_seconds)


def test_finite_cues_have_one_active_and_one_distinct_pending() -> None:
    coordinator = FiniteCueCoordinator()

    state = coordinator.observe(
        (
            cue("failure-1"),
            cue("completion-1", GlanceSemantic.FRESH_COMPLETION),
        ),
        now=10.0,
        play_motion=True,
    )

    assert state.active == cue("failure-1")
    assert state.pending == cue("completion-1", GlanceSemantic.FRESH_COMPLETION)
    assert state.next_deadline == 11.0
    assert not state.overflowed


def test_duplicate_cue_does_not_queue_or_extend_the_active_deadline() -> None:
    coordinator = FiniteCueCoordinator()
    duplicate = cue("failure-1")

    first = coordinator.observe((duplicate, duplicate), now=10.0, play_motion=True)
    repeated = coordinator.observe((duplicate,), now=10.75, play_motion=True)

    assert first.active == duplicate
    assert first.pending is None
    assert repeated.active == duplicate
    assert repeated.pending is None
    assert repeated.next_deadline == 11.0


def test_higher_priority_new_cue_replaces_pending_without_extending_active() -> None:
    coordinator = FiniteCueCoordinator()
    active = cue("completion-1", GlanceSemantic.FRESH_COMPLETION, repetitions=1)
    pending = cue("completion-2", GlanceSemantic.FRESH_COMPLETION, repetitions=1)
    attention = cue("attention-1", GlanceSemantic.ATTENTION, repetitions=1)

    first = coordinator.observe((active, pending), now=10.0, play_motion=True)
    replaced = coordinator.observe(
        (attention, active, pending),
        now=10.25,
        play_motion=True,
    )

    assert first.next_deadline == 10.5
    assert replaced.active == active
    assert replaced.pending == attention
    assert replaced.next_deadline == 10.5
    assert replaced.overflowed


def test_three_distinct_cues_coalesce_to_one_static_overflow_summary() -> None:
    coordinator = FiniteCueCoordinator()

    state = coordinator.observe(
        (cue("failure-1"), cue("failure-2"), cue("failure-3")),
        now=10.0,
        play_motion=True,
    )

    assert state.active == cue("failure-1")
    assert state.pending == cue("failure-2")
    assert state.overflowed
    assert coordinator.consumed_event_keys == (
        "failure-1",
        "failure-2",
        "failure-3",
    )


def test_invalid_or_over_budget_cues_never_enter_motion_state() -> None:
    coordinator = FiniteCueCoordinator()
    invalid = (
        cue("", repetitions=1),
        cue("x" * 129, repetitions=1),
        cue("too-many-repetitions", repetitions=3),
        cue("zero-duration", duration_seconds=0.0),
        cue("long-duration", duration_seconds=61.0),
    )

    state = coordinator.observe(invalid, now=10.0, play_motion=True)

    assert state.active is None
    assert state.pending is None
    assert state.next_deadline is None
    assert not state.overflowed
    assert coordinator.consumed_event_keys == ()


def test_consumed_watermark_evicts_to_exactly_256_keys() -> None:
    coordinator = FiniteCueCoordinator()
    restored = tuple(cue(f"episode-{index}", repetitions=1) for index in range(257))

    state = coordinator.establish_watermark(restored, now=10.0)

    assert state.active is None
    assert state.pending is None
    assert state.overflowed
    assert len(coordinator.consumed_event_keys) == 256
    assert "episode-0" not in coordinator.consumed_event_keys
    assert coordinator.consumed_event_keys[-1] == "episode-256"

    coordinator.observe((), now=11.0, play_motion=True)
    replay_after_eviction = coordinator.observe(
        (cue("episode-0", repetitions=1),),
        now=12.0,
        play_motion=True,
    )
    assert replay_after_eviction.active == cue("episode-0", repetitions=1)


def test_repeated_257_cue_observation_does_not_replay_trimmed_visible_episode() -> None:
    coordinator = FiniteCueCoordinator()
    visible = tuple(cue(f"episode-{index}", repetitions=1) for index in range(257))

    first = coordinator.observe(visible, now=10.0, play_motion=True)
    assert first.active == cue("episode-0", repetitions=1)
    coordinator.advance(now=11.0)

    repeated = coordinator.observe(visible, now=12.0, play_motion=True)

    assert repeated.active is None
    assert repeated.pending is None
    assert repeated.overflowed


def test_repeated_257_cue_watermark_does_not_replay_trimmed_visible_episode() -> None:
    coordinator = FiniteCueCoordinator()
    visible = tuple(cue(f"episode-{index}", repetitions=1) for index in range(257))

    established = coordinator.establish_watermark(visible, now=10.0)
    repeated = coordinator.observe(visible, now=11.0, play_motion=True)

    assert established.overflowed
    assert repeated.active is None
    assert repeated.pending is None
    assert repeated.overflowed


def test_overflow_replacement_is_fresh_without_replaying_retained_episodes() -> None:
    coordinator = FiniteCueCoordinator()
    visible = tuple(cue(f"episode-{index}", repetitions=1) for index in range(257))
    coordinator.establish_watermark(visible, now=10.0)
    replacement = (
        cue("replacement", repetitions=1),
        *visible[1:],
    )

    state = coordinator.observe(replacement, now=11.0, play_motion=True)

    assert state.active == cue("replacement", repetitions=1)
    assert state.pending is None
    assert state.overflowed


def test_trimmed_overflow_episode_requires_disappearance_before_reappearance() -> None:
    coordinator = FiniteCueCoordinator()
    visible = tuple(cue(f"episode-{index}", repetitions=1) for index in range(257))
    coordinator.establish_watermark(visible, now=10.0)

    removed = coordinator.observe(visible[1:], now=11.0, play_motion=True)
    reappeared = coordinator.observe(
        (*visible[1:], visible[0]),
        now=12.0,
        play_motion=True,
    )

    assert removed.active is None
    assert reappeared.active == visible[0]
    assert reappeared.pending is None


@pytest.mark.parametrize(
    "refresh_reason",
    (
        "reconnect",
        "ordinary_refresh",
        "renderer_regeneration",
        "accessibility_change",
        "physical_write_completion",
    ),
)
def test_non_event_refreshes_cannot_replay_the_same_upstream_episode_key(
    refresh_reason: str,
) -> None:
    coordinator = FiniteCueCoordinator()
    observed = cue("upstream-episode", repetitions=1)

    coordinator.observe((observed,), now=10.0, play_motion=True)
    assert coordinator.advance(now=10.5).active is None

    refreshed = coordinator.observe((observed,), now=11.0, play_motion=True)

    assert refresh_reason
    assert refreshed.active is None
    assert refreshed.pending is None
    assert refreshed.next_deadline is None


def test_warm_restore_consumes_upstream_keys_without_motion_or_replay() -> None:
    coordinator = FiniteCueCoordinator()
    restored = cue("warm-episode", repetitions=1)

    watermarked = coordinator.establish_watermark((restored,), now=10.0)
    refreshed = coordinator.observe((restored,), now=11.0, play_motion=True)

    assert watermarked.active is None
    assert watermarked.next_deadline is None
    assert refreshed.active is None
    assert coordinator.consumed_event_keys == ("warm-episode",)


def test_sleep_consumes_active_pending_and_new_cues_without_wake_replay() -> None:
    coordinator = FiniteCueCoordinator()
    first = cue("failure-1")
    second = cue("completion-1", GlanceSemantic.FRESH_COMPLETION)

    awake = coordinator.observe((first, second), now=10.0, play_motion=True)
    sleeping = coordinator.observe((first, second), now=10.25, play_motion=False)
    woke = coordinator.observe((first, second), now=20.0, play_motion=True)

    assert awake.active == first
    assert awake.pending == second
    assert sleeping.active is None
    assert sleeping.pending is None
    assert sleeping.next_deadline is None
    assert woke.active is None
    assert woke.pending is None


def test_late_advance_does_not_replay_pending_cue_after_its_episode_window() -> None:
    coordinator = FiniteCueCoordinator()
    coordinator.observe(
        (
            cue("failure-1", repetitions=1),
            cue("completion-1", GlanceSemantic.FRESH_COMPLETION, repetitions=1),
        ),
        now=10.0,
        play_motion=True,
    )

    state = coordinator.advance(now=12.0)

    assert state.active is None
    assert state.pending is None
    assert state.next_deadline is None


def test_regressing_or_wall_clock_time_cannot_mutate_cue_state() -> None:
    coordinator = FiniteCueCoordinator()
    first = coordinator.observe(
        (cue("failure-1", repetitions=1),),
        now=10.0,
        play_motion=True,
    )

    regressed = coordinator.observe(
        (cue("failure-2", repetitions=1),),
        now=9.0,
        play_motion=True,
    )
    wall_clock = coordinator.advance(now=1_700_000_000.0)

    assert regressed == first
    assert wall_clock == first


def test_invalid_play_motion_flag_does_not_advance_the_monotonic_watermark() -> None:
    coordinator = FiniteCueCoordinator()
    first = coordinator.observe(
        (cue("failure-1", repetitions=1),),
        now=10.0,
        play_motion=True,
    )

    invalid = coordinator.observe(
        (cue("failure-2", repetitions=1),),
        now=20.0,
        play_motion=1,  # type: ignore[arg-type]
    )
    valid = coordinator.observe(
        (cue("failure-2", repetitions=1),),
        now=10.25,
        play_motion=True,
    )

    assert invalid == first
    assert valid.active == first.active
    assert valid.pending == cue("failure-2", repetitions=1)


def test_budget_configuration_cannot_weaken_hard_cue_bounds() -> None:
    coordinator = FiniteCueCoordinator(
        FiniteCueBudget(
            max_repetitions=99,
            max_active=99,
            max_pending=99,
            max_consumed_keys=999,
        )
    )

    state = coordinator.observe(
        tuple(cue(f"episode-{index}", repetitions=3) for index in range(300)),
        now=10.0,
        play_motion=True,
    )

    assert state.active is None
    assert state.pending is None
    assert coordinator.budget == FiniteCueBudget()
