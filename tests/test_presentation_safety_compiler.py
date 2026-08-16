from sidepulse.animation import loop_duration_ms, parse_animation
from sidepulse.led_status import style_to_program
from sidepulse.presentation_compiler import (
    MIN_PRESENTATION_CYCLE_MS,
    MIN_SATURATED_RED_CYCLE_MS,
    compile_presentation_program,
)
from sidepulse.signals import PATTERN_BLINK, SignalStyle


def test_fast_loop_is_deterministically_slowed_to_the_global_limit() -> None:
    compiled = compile_presentation_program(
        "#00E5FF 100ms none\noff 100ms none\nrepeat"
    )

    assert compiled.accepted is True
    assert compiled.transformed is True
    animation = parse_animation(compiled.program)
    assert loop_duration_ms(animation) >= MIN_PRESENTATION_CYCLE_MS


def test_saturated_red_uses_the_stricter_cadence() -> None:
    compiled = compile_presentation_program(
        "#FF0000 100ms none\noff 100ms none\nrepeat"
    )

    assert compiled.accepted is True
    animation = parse_animation(compiled.program)
    assert loop_duration_ms(animation) >= MIN_SATURATED_RED_CYCLE_MS


def test_invalid_program_fails_closed_to_static_off() -> None:
    compiled = compile_presentation_program("not valid firmware text")

    assert compiled.accepted is False
    assert compiled.program.startswith("off")


def test_signal_renderer_cannot_bypass_the_safety_compiler() -> None:
    program = style_to_program(
        SignalStyle("#00E5FF", PATTERN_BLINK, 0.1, 1.0),
    )
    animation = parse_animation(program)
    durations = [
        segment.timing.effective_duration_ms
        for step in animation.steps
        if hasattr(step, "segments")
        for segment in step.segments
    ]
    assert durations
    assert min(durations) >= MIN_PRESENTATION_CYCLE_MS // 2
