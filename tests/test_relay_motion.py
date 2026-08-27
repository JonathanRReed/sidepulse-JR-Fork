from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.attention import AttentionProjection, LifecycleMode, ProjectedAgentRow
from sidepulse.colors import (
    BLEND_MODE_RELAY,
    ColorSettings,
    program_for_projection,
    program_for_snapshot,
    relay_led_order,
    relay_phase_index,
    relay_step_ms,
)
from sidepulse.device_writer import MAX_LED_BYTES, MAX_LED_LINES
from sidepulse.led_status import (
    AgentLedController,
    LedDisplayState,
    apply_strip_transfer_to_program,
)
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.presentation_policy import (
    GlanceInputs,
    MotionClass,
    compose_presentation_program,
    continuous_presentation_identity,
    resolve_glance,
)


def _status(provider: str, mode: AgentMode = AgentMode.WORKING) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=provider,
        display_name=provider.title(),
        mode=mode,
        updated_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        event_name="Test",
    )


def _relay_settings() -> ColorSettings:
    return (
        ColorSettings.defaults()
        .with_blend_mode(BLEND_MODE_RELAY)
        .with_cycle_speed(1.6)
        .with_session_color("codex", "#FFFFFF")
        .with_session_color("claude", "#FFFFFF")
    )


def _body(program: str) -> list[str]:
    lines = program.splitlines()
    for index, line in enumerate(lines):
        if line.split(":", 1)[0].isdigit():
            return lines[index:]
    return lines


def _pulse_indices(program: str) -> list[int]:
    return [int(segment.split(":", 1)[0]) for segment in _body(program)[1].split("; ")]


def test_relay_step_is_full_traversal_divided_across_the_led_line() -> None:
    assert relay_step_ms(1.6, 8) == 200
    assert relay_step_ms(1.6, 2) == 800
    assert relay_step_ms(1.6, 8) * 8 == 1_600


@pytest.mark.parametrize("value", [0.0, -2.0, float("nan"), float("inf")])
def test_relay_step_invalid_traversal_uses_normalized_speed_floor(value: float) -> None:
    assert relay_step_ms(value, 8) == 37


def test_relay_order_visits_each_led_once_before_wrapping() -> None:
    assert relay_led_order(8, 0) == (0, 1, 2, 3, 4, 5, 6, 7)
    assert relay_led_order(8, 6) == (6, 7, 0, 1, 2, 3, 4, 5)
    assert set(relay_led_order(8, 6)) == set(range(8))


@pytest.mark.parametrize(
    ("elapsed_seconds", "expected"),
    [
        (0.0, 0),
        (0.8, 4),
        (1.4, 7),
        (1.6, 0),
        (1_601.41, 7),
    ],
)
def test_relay_phase_index_wraps_without_losing_large_elapsed_time(
    elapsed_seconds: float,
    expected: int,
) -> None:
    assert relay_phase_index(elapsed_seconds, 1.6, 8) == expected


def test_relay_program_rotates_delay_order_to_the_current_phase() -> None:
    statuses = (_status("codex"), _status("claude"))
    _state, program = program_for_snapshot(
        statuses,
        led_count=8,
        colors=_relay_settings(),
        relay_elapsed_seconds=0.8,
    )

    assert _pulse_indices(program) == [4, 5, 6, 7, 0, 1, 2, 3]
    delays = [int(segment.split()[-1][:-2]) for segment in _body(program)[1].split("; ")]
    assert delays == [0, 200, 400, 600, 800, 1_000, 1_200, 1_400]


def test_all_static_relay_program_is_byte_stable_across_elapsed_phases() -> None:
    completed = _status("codex", AgentMode.COMPLETED)
    failed = _status("claude", AgentMode.BLOCKED_ERROR)
    rows = (
        ProjectedAgentRow(
            agent_id=completed.agent_id,
            provider=completed.provider,
            display_name=completed.display_name,
            lifecycle_mode=LifecycleMode.COMPLETED_RECENTLY,
            actionable=False,
            is_subagent=False,
            updated_at=completed.updated_at,
            source_status=completed,
        ),
        ProjectedAgentRow(
            agent_id=failed.agent_id,
            provider=failed.provider,
            display_name=failed.display_name,
            lifecycle_mode=LifecycleMode.FAILED_VISIBLE,
            actionable=False,
            is_subagent=False,
            updated_at=failed.updated_at,
            source_status=failed,
        ),
    )
    projection = AttentionProjection(
        lifecycle_mode=LifecycleMode.COMPLETED_RECENTLY,
        actionable_attention=(),
        visible_rows=rows,
        transient_signals=(),
        dominant_provider="codex",
        click_target_agent_id=None,
    )

    _, phase_zero = program_for_projection(
        projection,
        led_count=8,
        colors=_relay_settings(),
        relay_elapsed_seconds=0.0,
    )
    _, phase_four = program_for_projection(
        projection,
        led_count=8,
        colors=_relay_settings(),
        relay_elapsed_seconds=0.8,
    )

    assert phase_four == phase_zero


def test_two_and_eight_led_programs_map_one_elapsed_duration_by_fraction() -> None:
    statuses = (_status("codex"), _status("claude"))
    settings = _relay_settings()

    _, dot = program_for_snapshot(
        statuses,
        led_count=2,
        colors=settings,
        relay_elapsed_seconds=0.8,
    )
    _, screen_bar = program_for_snapshot(
        statuses,
        led_count=8,
        colors=settings,
        relay_elapsed_seconds=0.8,
    )

    assert _pulse_indices(dot)[0] == 1
    assert _pulse_indices(screen_bar)[0] == 4


def test_two_and_eight_led_phase_alignment_survives_integer_step_rounding() -> None:
    # At 299ms of the normalized 300ms traversal, both surfaces are in
    # their final fraction. Eight integer 37ms steps total only 296ms, so
    # deriving phase from rounded steps would wrap the Screen Bar early.
    assert relay_phase_index(0.299, 0.3, 2) == 1
    assert relay_phase_index(0.299, 0.3, 8) == 7


def test_zero_leds_emit_no_relay_instructions_and_one_agent_is_unchanged() -> None:
    settings = _relay_settings()
    statuses = (_status("codex"), _status("claude"))
    state, empty = program_for_snapshot(
        statuses,
        led_count=0,
        colors=settings,
        relay_elapsed_seconds=12.0,
    )
    assert state is LedDisplayState.WORKING
    assert empty == ""

    one_agent = (_status("codex"),)
    _, initial = program_for_snapshot(
        one_agent,
        led_count=1,
        colors=settings,
        relay_elapsed_seconds=0.0,
    )
    _, rebuilt = program_for_snapshot(
        one_agent,
        led_count=1,
        colors=settings,
        relay_elapsed_seconds=99.0,
    )
    assert rebuilt == initial


@pytest.mark.parametrize("led_count", [2, 8])
def test_relay_program_stays_inside_firmware_bounds(led_count: int) -> None:
    _, program = program_for_snapshot(
        (_status("codex"), _status("claude")),
        led_count=led_count,
        colors=_relay_settings().with_cycle_speed(10.0),
        relay_elapsed_seconds=987_654.3,
    )
    assert len(program.splitlines()) <= MAX_LED_LINES
    assert len(program.encode("utf-8")) <= MAX_LED_BYTES


@pytest.mark.parametrize("led_count", [2, 8])
def test_real_wasm_relay_visits_every_led_within_one_traversal(led_count: int) -> None:
    from sidepulse.led_wasm import LedWasmUnavailableError, SdLedWasmController

    try:
        controller = SdLedWasmController(led_count=led_count)
    except LedWasmUnavailableError as exc:
        pytest.skip(str(exc))

    _, program = program_for_snapshot(
        (_status("codex"), _status("claude")),
        led_count=led_count,
        colors=_relay_settings(),
        relay_elapsed_seconds=0.0,
    )
    epoch_ms = 7_000_000
    parsed = controller.parse(program, epoch_ms)
    assert parsed.ok, f"{parsed.error_name} at {parsed.line}:{parsed.column}"

    step_ms = 800 if led_count == 2 else 200
    settle_ms = 96 if led_count == 2 else 40
    visited: set[int] = set()
    for turn in range(led_count):
        # The midpoint of each pulse is safely away from both segment edges.
        pixels = controller.step(epoch_ms + settle_ms + turn * step_ms + step_ms // 2)
        intensity = [sum(pixel) for pixel in pixels]
        brightest = {index for index, value in enumerate(intensity) if value == max(intensity)}
        assert brightest == {turn}
        visited.update(brightest)

    assert visited == set(range(led_count))


def _semantic_relay(*, led_count: int, presentation_time: float):
    preferences = AccessibilityDisplayPreferences()
    resolved = resolve_glance(
        GlanceInputs(None, None, None, True, False, None),
        presentation_time=presentation_time,
        relay_epoch=100.0,
        preferences=preferences,
    )
    return compose_presentation_program(
        resolved,
        presentation_time=presentation_time,
        led_count=led_count,
        color="#FFFFFF",
        preferences=preferences,
    )


def _semantic_pulse_indices(program: str) -> list[int]:
    pulse_line = next(line for line in program.splitlines() if " pulse " in line)
    return [int(segment.split(":", 1)[0]) for segment in pulse_line.split("; ")]


def test_semantic_relay_uses_one_epoch_for_two_and_eight_led_surfaces() -> None:
    dot = _semantic_relay(led_count=2, presentation_time=100.8)
    pro = _semantic_relay(led_count=8, presentation_time=100.8)

    assert dot.motion is MotionClass.CONTINUOUS
    assert pro.motion is MotionClass.CONTINUOUS
    assert dot.relay_epoch == pro.relay_epoch == 100.0
    # The declared period is the loop the firmware actually runs --
    # settle prefix included -- so the two builds honestly differ
    # (settle scales with step). The shared epoch above is the
    # cross-surface phase contract; the period is per-build truth.
    assert dot.trusted_period_seconds == 1.696
    assert pro.trusted_period_seconds == 1.64
    assert _semantic_pulse_indices(dot.dsl)[0] == 1
    assert _semantic_pulse_indices(pro.dsl)[0] == 4
    assert dot.temporal is not None
    assert pro.temporal is not None
    assert sum(frame.duration_seconds for frame in dot.temporal.frames) == pytest.approx(3.2)
    assert sum(frame.duration_seconds for frame in pro.temporal.frames) == pytest.approx(3.2)


@pytest.mark.parametrize(
    ("elapsed", "dot_index", "pro_index", "screen_bar_index"),
    [
        (0.0, 0, 0, 0),
        (0.2, 0, 1, 1),
        (0.8, 1, 4, 4),
        (1.4, 1, 7, 7),
        (1.6, 0, 0, 0),
        (3.2, 0, 0, 0),
    ],
)
def test_semantic_relay_literal_phase_matrix_for_dot_pro_and_screen_bar(
    elapsed: float,
    dot_index: int,
    pro_index: int,
    screen_bar_index: int,
) -> None:
    presentation_time = 100.0 + elapsed
    dot = _semantic_relay(led_count=2, presentation_time=presentation_time)
    pro = _semantic_relay(led_count=8, presentation_time=presentation_time)
    screen_bar = _semantic_relay(led_count=8, presentation_time=presentation_time)

    assert (
        _semantic_pulse_indices(dot.dsl)[0],
        _semantic_pulse_indices(pro.dsl)[0],
        _semantic_pulse_indices(screen_bar.dsl)[0],
    ) == (dot_index, pro_index, screen_bar_index)
    assert dot.relay_epoch == pro.relay_epoch == screen_bar.relay_epoch == 100.0


@pytest.mark.parametrize(
    ("led_count", "elapsed"),
    [
        (2, 1.0),
        (8, 0.85),
    ],
)
def test_semantic_relay_applies_canonical_phase_once_in_rendered_pixels(
    led_count: int,
    elapsed: float,
) -> None:
    """A phase-rotated program must not advance by the same elapsed time again."""
    from sidepulse.led_wasm import LedWasmUnavailableError, SdLedWasmController

    try:
        controller = SdLedWasmController(led_count=led_count)
    except LedWasmUnavailableError as exc:
        pytest.skip(str(exc))

    presentation_time = 100.0 + elapsed
    program = _semantic_relay(
        led_count=led_count,
        presentation_time=presentation_time,
    )
    assert program.playback_anchor is not None
    parsed = controller.parse(program.dsl, round(program.playback_anchor * 1000.0))
    assert parsed.ok, f"{parsed.error_name} at {parsed.line}:{parsed.column}"

    pixels = controller.step(round(presentation_time * 1000.0))
    intensities = [sum(pixel) for pixel in pixels]
    assert intensities.index(max(intensities)) == relay_phase_index(
        elapsed,
        1.6,
        led_count,
    )


def test_semantic_relay_keeps_one_phase_independent_physical_identity() -> None:
    early = _semantic_relay(led_count=8, presentation_time=100.2)
    later = _semantic_relay(led_count=8, presentation_time=100.8)

    assert early.dsl != later.dsl
    assert continuous_presentation_identity(early) == continuous_presentation_identity(
        later
    )
    assert early.playback_anchor == pytest.approx(100.2)
    assert later.playback_anchor == pytest.approx(100.8)


def test_physical_relay_does_not_rewrite_when_only_canonical_phase_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    early = _semantic_relay(led_count=8, presentation_time=100.2)
    later = _semantic_relay(led_count=8, presentation_time=100.8)
    identity = continuous_presentation_identity(early)
    writes: list[str] = []

    def write(program: str, **_kwargs) -> Path:
        writes.append(program)
        return Path("/Volumes/SIDEPULSE/LEDS.LED")

    monkeypatch.setattr("sidepulse._led_status_legacy.write_led_program", write)
    controller = AgentLedController()
    first = controller.sync_program(
        early.dsl,
        LedDisplayState.WORKING,
        dedupe_token=identity,
    )
    second = controller.sync_program(
        later.dsl,
        LedDisplayState.WORKING,
        dedupe_token=continuous_presentation_identity(later),
    )

    assert first.changed
    assert not second.changed
    # One write, and it is the first program -- in the strip's own drive bytes,
    # since the surface transfer runs at this boundary. The subject here is the
    # dedupe, not the encoding: what matters is that the second call added
    # nothing.
    assert writes == [apply_strip_transfer_to_program(early.dsl)]


def test_semantic_relay_is_callback_count_independent_and_wraps_exactly() -> None:
    first = _semantic_relay(led_count=8, presentation_time=100.8)
    repeated = _semantic_relay(led_count=8, presentation_time=100.8)
    wrapped = _semantic_relay(led_count=8, presentation_time=101.6)

    assert repeated.dsl == first.dsl
    assert _semantic_pulse_indices(first.dsl)[0] == 4
    assert _semantic_pulse_indices(wrapped.dsl)[0] == 0
