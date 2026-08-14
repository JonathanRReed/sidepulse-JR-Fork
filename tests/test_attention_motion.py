from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sidepulse.attention import AttentionProjection, LifecycleMode, ProjectedAgentRow, SignalKind, TransientSignal
from sidepulse.colors import (
    BLEND_MODE_CLASSIC,
    ColorSettings,
    program_for_projection,
    program_for_snapshot,
)
from sidepulse.led_status import failure_signal_program, style_to_program
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.signal_coordinator import ActiveSignal
from sidepulse.signals import DEFAULT_SIGNAL_STYLES, SIGNAL_COMPLETION


def _status(provider: str, mode: AgentMode) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=provider,
        display_name=provider.title(),
        mode=mode,
        updated_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        event_name="PermissionRequest" if mode is AgentMode.WAITING_FOR_INPUT else "Test",
    )


def test_attention_arrival_is_explicit_finite_motion_over_a_static_base() -> None:
    statuses = (
        _status("codex", AgentMode.WORKING),
        _status("claude", AgentMode.WAITING_FOR_INPUT),
    )
    settings = ColorSettings.defaults().with_mode_color("ask", "#123456")

    _, base = program_for_snapshot(statuses, led_count=8, colors=settings)

    assert "repeat" not in base
    assert base.splitlines()[-1].startswith("0:")

    _, arrival = program_for_snapshot(
        statuses,
        led_count=8,
        colors=settings,
        include_attention_arrival=True,
    )
    attention_tap = "#123456 240ms"
    assert arrival.splitlines().count(attention_tap) == 2
    assert "repeat" not in arrival
    assert arrival.endswith(base)

    _, regenerated = program_for_snapshot(statuses, led_count=8, colors=settings)
    assert regenerated == base


@pytest.mark.parametrize(
    "statuses, settings",
    [
        (
            (_status("claude", AgentMode.WAITING_FOR_INPUT),),
            ColorSettings.defaults(),
        ),
        (
            (
                _status("codex", AgentMode.WORKING),
                _status("claude", AgentMode.WAITING_FOR_INPUT),
            ),
            ColorSettings.defaults().with_blend_mode(BLEND_MODE_CLASSIC),
        ),
    ],
)
def test_every_attention_render_path_settles_to_a_static_anchor(
    statuses: tuple[AgentStatus, ...],
    settings: ColorSettings,
) -> None:
    settings = settings.with_mode_color("ask", "#123456")

    _, base = program_for_snapshot(statuses, led_count=8, colors=settings)
    _, arrival = program_for_snapshot(
        statuses,
        led_count=8,
        colors=settings,
        include_attention_arrival=True,
    )

    assert "repeat" not in base
    assert arrival.splitlines().count("#123456 240ms") == 2
    assert "repeat" not in arrival
    assert arrival.endswith(base)


def test_projection_attention_uses_the_same_finite_arrival_contract() -> None:
    status = _status("claude", AgentMode.WAITING_FOR_INPUT)
    row = ProjectedAgentRow(
        agent_id=status.agent_id,
        provider=status.provider,
        display_name=status.display_name,
        lifecycle_mode=LifecycleMode.WAITING,
        actionable=True,
        is_subagent=False,
        updated_at=status.updated_at,
        source_status=status,
    )
    projection = AttentionProjection(
        lifecycle_mode=LifecycleMode.WAITING,
        actionable_attention=(row,),
        visible_rows=(row,),
        transient_signals=(),
        dominant_provider="claude",
        click_target_agent_id="claude",
    )
    settings = ColorSettings.defaults().with_mode_color("ask", "#123456")

    _, base = program_for_projection(projection, led_count=8, colors=settings)
    _, arrival = program_for_projection(
        projection,
        led_count=8,
        colors=settings,
        include_attention_arrival=True,
    )

    assert "repeat" not in base
    assert arrival.splitlines().count("#123456 240ms") == 2
    assert "repeat" not in arrival
    assert arrival.endswith(base)


def test_completion_style_cannot_append_an_unbounded_repeat() -> None:
    program = style_to_program(DEFAULT_SIGNAL_STYLES[SIGNAL_COMPLETION])

    assert "repeat" not in program
    assert program.endswith("#00FF66")


@pytest.mark.parametrize("repetitions", (1, 2))
def test_failure_signal_honors_exact_repetitions_and_settles_at_deadline(
    repetitions: int,
) -> None:
    started_at = 10.0
    cycle_seconds = 0.9
    active = ActiveSignal(
        signal=TransientSignal(
            event_key=f"failure-{repetitions}",
            kind=SignalKind.FAILURE,
            repetitions=repetitions,
            source_agent_id="codex",
        ),
        started_at=started_at,
        ends_at=started_at + cycle_seconds * repetitions,
    )

    program = failure_signal_program("#123456", active)
    lines = program.splitlines()
    timed_lines = [line for line in lines if line.endswith("cosine")]
    durations_ms = [
        int(line.split()[-2].removesuffix("ms"))
        for line in timed_lines
    ]

    assert lines.count("#123456 510ms cosine") == repetitions
    assert lines.count("off 390ms cosine") == repetitions
    assert sum(durations_ms) == round((active.ends_at - active.started_at) * 1000)
    assert lines[-1] == "#123456"
    assert "repeat" not in lines


def test_failure_signal_one_and_two_repetition_programs_are_distinct() -> None:
    def active(repetitions: int) -> ActiveSignal:
        return ActiveSignal(
            signal=TransientSignal(
                event_key=f"failure-{repetitions}",
                kind=SignalKind.FAILURE,
                repetitions=repetitions,
                source_agent_id="codex",
            ),
            started_at=10.0,
            ends_at=10.0 + 0.9 * repetitions,
        )

    once = failure_signal_program("#123456", active(1))
    twice = failure_signal_program("#123456", active(2))

    assert once != twice
