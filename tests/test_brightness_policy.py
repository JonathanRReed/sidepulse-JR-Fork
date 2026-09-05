from __future__ import annotations

import subprocess
import sys

import pytest

from sidepulse.brightness_policy import (
    MIN_AMBIENT_VISIBLE_BRIGHTNESS,
    MIN_ESCALATION_VISIBLE_BRIGHTNESS,
    BrightnessPolicyResult,
    BrightnessTraceStep,
    plan_ambient_brightness,
    plan_signal_brightness,
)


def _names(trace: tuple[BrightnessTraceStep, ...]) -> tuple[str, ...]:
    return tuple(step.name for step in trace)


def test_brightness_policy_import_does_not_pull_the_legacy_led_renderer() -> None:
    probe = (
        "import json, sys\n"
        "import sidepulse.brightness_policy\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('sidepulse'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 0, result.stderr
    assert '"sidepulse._led_status_legacy"' not in result.stdout
    assert '"sidepulse.led_status"' not in result.stdout


def test_ambient_brightness_preserves_the_current_factor_order() -> None:
    result = plan_ambient_brightness(
        base_brightness=200,
        idle_factor=0.5,
        focus_factor=1.0,
        night_factor=1.0,
        global_factor=1.0,
        escalation_boost=1.0,
        is_screen_bar=False,
        screen_bar_min_glow=0.25,
    )

    assert result == BrightnessPolicyResult(
        brightness=100,
        trace=(
            BrightnessTraceStep("base", before=200.0, after=200.0),
            BrightnessTraceStep(
                "idle_dim", before=200.0, factor=0.5, after=100.0
            ),
            BrightnessTraceStep(
                "focus_sync", before=100.0, factor=1.0, after=100.0
            ),
            BrightnessTraceStep(
                "night_dim", before=100.0, factor=1.0, after=100.0
            ),
            BrightnessTraceStep(
                "global_brightness", before=100.0, factor=1.0, after=100.0
            ),
            BrightnessTraceStep(
                "escalation_boost", before=100.0, factor=1.0, after=100.0
            ),
            BrightnessTraceStep("normalize", before=100.0, after=100.0),
        ),
    )


def test_ambient_brightness_applies_escalation_floor_before_screen_bar_floor() -> None:
    result = plan_ambient_brightness(
        base_brightness=10,
        idle_factor=0.1,
        focus_factor=0.1,
        night_factor=0.1,
        global_factor=0.1,
        escalation_boost=2.0,
        is_screen_bar=True,
        screen_bar_min_glow=0.25,
    )

    assert result.brightness == 64
    assert _names(result.trace) == (
        "base",
        "idle_dim",
        "focus_sync",
        "night_dim",
        "global_brightness",
        "escalation_boost",
        "escalation_floor",
        "ambient_visibility_floor",
        "screen_bar_min_glow",
        "normalize",
    )
    escalation_step = result.trace[-4]
    assert escalation_step.name == "escalation_floor"
    assert escalation_step.before == pytest.approx(0.002)
    assert escalation_step.floor == 12.0
    assert escalation_step.after == 12.0
    assert result.trace[-3] == BrightnessTraceStep(
        "ambient_visibility_floor",
        before=12.0,
        floor=61.0,
        after=61.0,
    )
    assert result.trace[-2] == BrightnessTraceStep(
        "screen_bar_min_glow",
        before=61.0,
        floor=63.75,
        after=63.75,
    )
    assert result.trace[-1] == BrightnessTraceStep(
        "normalize", before=63.75, after=64.0
    )


def test_ambient_brightness_applies_its_surface_floor_after_escalation() -> None:
    result = plan_ambient_brightness(
        base_brightness=1,
        idle_factor=0.1,
        focus_factor=0.1,
        night_factor=0.1,
        global_factor=0.1,
        escalation_boost=2.0,
        is_screen_bar=False,
        screen_bar_min_glow=0.25,
    )

    assert MIN_ESCALATION_VISIBLE_BRIGHTNESS == 12
    assert result.brightness == MIN_AMBIENT_VISIBLE_BRIGHTNESS
    assert result.trace[-3].name == "escalation_floor"
    assert result.trace[-3].before == pytest.approx(0.0002)
    assert result.trace[-3].floor == 12.0
    assert result.trace[-3].after == 12.0
    assert result.trace[-2] == BrightnessTraceStep(
        "ambient_visibility_floor",
        before=12.0,
        floor=61.0,
        after=61.0,
    )


def test_compounded_ambient_dims_do_not_turn_an_on_surface_effectively_black() -> None:
    """Auto, idle, night, and Focus dimming may compose without disappearing."""
    result = plan_ambient_brightness(
        base_brightness=95,
        idle_factor=0.3,
        focus_factor=1.0,
        night_factor=0.15,
        global_factor=1.0,
        escalation_boost=1.0,
        is_screen_bar=False,
        screen_bar_min_glow=0.0,
        dnd_factor=0.15,
    )

    assert result.brightness == MIN_AMBIENT_VISIBLE_BRIGHTNESS == 61
    assert "ambient_visibility_floor" in _names(result.trace)


def test_ambient_visibility_floor_recovers_from_tiny_automatic_base() -> None:
    result = plan_ambient_brightness(
        base_brightness=4,
        idle_factor=0.1,
        focus_factor=1.0,
        night_factor=0.1,
        global_factor=1.0,
        escalation_boost=1.0,
        is_screen_bar=False,
        screen_bar_min_glow=0.0,
    )

    assert result.brightness == MIN_AMBIENT_VISIBLE_BRIGHTNESS


def test_screen_bar_floor_does_not_revive_an_explicit_zero() -> None:
    result = plan_ambient_brightness(
        base_brightness=10,
        idle_factor=1.0,
        focus_factor=0.0,
        night_factor=1.0,
        global_factor=1.0,
        escalation_boost=1.0,
        is_screen_bar=True,
        screen_bar_min_glow=0.25,
    )

    assert result.brightness == 0
    assert _names(result.trace) == (
        "base",
        "idle_dim",
        "focus_sync",
        "night_dim",
        "global_brightness",
        "escalation_boost",
        "normalize",
    )


def test_signal_brightness_uses_only_configured_brightness_global_and_escalation() -> None:
    result = plan_signal_brightness(
        configured_brightness=200,
        global_factor=0.5,
        escalation_boost=2.0,
        focus_scale=0.5,
    )

    assert result == BrightnessPolicyResult(
        brightness=200,
        trace=(
            BrightnessTraceStep("configured_brightness", before=200.0, after=200.0),
            BrightnessTraceStep(
                "global_brightness", before=200.0, factor=0.5, after=100.0
            ),
            BrightnessTraceStep(
                "escalation_boost", before=100.0, factor=2.0, after=200.0
            ),
            BrightnessTraceStep(
                "minimum_signal_visibility", before=200.0, floor=1.0, after=200.0
            ),
            BrightnessTraceStep("normalize", before=200.0, after=200.0),
        ),
    )


def test_signal_brightness_focus_turn_off_wins() -> None:
    result = plan_signal_brightness(
        configured_brightness=200,
        global_factor=0.5,
        escalation_boost=2.0,
        focus_scale=0.0,
    )

    assert result == BrightnessPolicyResult(
        brightness=0,
        trace=(
            BrightnessTraceStep(
                "focus_turn_off", before=200.0, floor=0.0, after=0.0
            ),
            BrightnessTraceStep("normalize", before=0.0, after=0.0),
        ),
    )


@pytest.mark.parametrize("configured_brightness", [0, -1])
def test_signal_brightness_keeps_the_existing_minimum_one_floor_when_not_blocked(
    configured_brightness: int,
) -> None:
    result = plan_signal_brightness(
        configured_brightness=configured_brightness,
        global_factor=0.5,
        escalation_boost=1.0,
        focus_scale=1.0,
    )

    assert result.brightness == 1
    assert result.trace[-2].name == "minimum_signal_visibility"
    assert result.trace[-1].name == "normalize"


@pytest.mark.parametrize("focus_scale", [0.0, -0.1])
def test_signal_brightness_nonpositive_focus_scale_forces_zero(
    focus_scale: float,
) -> None:
    result = plan_signal_brightness(
        configured_brightness=200,
        global_factor=1.0,
        escalation_boost=2.0,
        focus_scale=focus_scale,
    )

    assert result.brightness == 0
    assert _names(result.trace) == ("focus_turn_off", "normalize")


def test_dnd_dim_scales_ambient_before_escalation_and_visibility_floors() -> None:
    result = plan_ambient_brightness(
        base_brightness=200,
        idle_factor=1.0,
        focus_factor=1.0,
        night_factor=1.0,
        global_factor=1.0,
        escalation_boost=2.0,
        is_screen_bar=True,
        screen_bar_min_glow=0.25,
        dnd_factor=0.1,
    )

    assert result.brightness == 64
    assert _names(result.trace) == (
        "base",
        "idle_dim",
        "focus_sync",
        "night_dim",
        "global_brightness",
        "dnd_dim",
        "escalation_boost",
        "escalation_floor",
        "ambient_visibility_floor",
        "screen_bar_min_glow",
        "normalize",
    )
    assert result.trace[5] == BrightnessTraceStep(
        "dnd_dim", before=200.0, factor=0.1, after=20.0
    )


def test_dnd_dark_zero_is_authoritative_over_every_ambient_floor() -> None:
    result = plan_ambient_brightness(
        base_brightness=200,
        idle_factor=1.0,
        focus_factor=1.0,
        night_factor=1.0,
        global_factor=1.0,
        escalation_boost=2.0,
        is_screen_bar=True,
        screen_bar_min_glow=0.75,
        dnd_factor=0.0,
    )

    assert result.brightness == 0
    assert _names(result.trace) == (
        "base",
        "idle_dim",
        "focus_sync",
        "night_dim",
        "global_brightness",
        "dnd_dim",
        "normalize",
    )


def test_dnd_dim_scales_signal_brightness_before_escalation_floor() -> None:
    result = plan_signal_brightness(
        configured_brightness=200,
        global_factor=0.5,
        escalation_boost=2.0,
        focus_scale=1.0,
        dnd_factor=0.2,
    )

    assert result.brightness == 40
    assert _names(result.trace) == (
        "configured_brightness",
        "global_brightness",
        "dnd_dim",
        "escalation_boost",
        "minimum_signal_visibility",
        "normalize",
    )


def test_dnd_dark_zero_is_authoritative_over_signal_visibility_floor() -> None:
    result = plan_signal_brightness(
        configured_brightness=200,
        global_factor=1.0,
        escalation_boost=2.0,
        focus_scale=1.0,
        dnd_factor=0.0,
    )

    assert result.brightness == 0
    assert _names(result.trace) == (
        "configured_brightness",
        "global_brightness",
        "dnd_dim",
        "normalize",
    )


@pytest.mark.parametrize(
    ("base_brightness", "expected"),
    [(61.6, 62), (300.0, 255), (-4.0, 0)],
)
def test_ambient_brightness_trace_exposes_final_rounding_and_clamping(
    base_brightness: float,
    expected: int,
) -> None:
    result = plan_ambient_brightness(
        base_brightness=base_brightness,
        idle_factor=1.0,
        focus_factor=1.0,
        night_factor=1.0,
        global_factor=1.0,
        escalation_boost=1.0,
        is_screen_bar=False,
        screen_bar_min_glow=0.25,
    )

    assert result.brightness == expected
    assert result.trace[-1] == BrightnessTraceStep(
        "normalize",
        before=base_brightness,
        after=float(expected),
    )


def test_sleep_dims_the_shared_brightness_intent_without_turning_it_off() -> None:
    result = plan_ambient_brightness(
        base_brightness=255,
        idle_factor=1.0,
        sleep_factor=0.0,
        focus_factor=1.0,
        night_factor=1.0,
        global_factor=1.0,
        escalation_boost=1.0,
        is_screen_bar=False,
        screen_bar_min_glow=0.0,
    )

    assert result.brightness == MIN_AMBIENT_VISIBLE_BRIGHTNESS
    sleep_step = next(step for step in result.trace if step.name == "sleep_dim")
    assert sleep_step.factor > 0.0


def test_idle_auto_off_is_separate_and_authoritative_over_visibility_floors() -> None:
    result = plan_ambient_brightness(
        base_brightness=255,
        idle_factor=0.3,
        sleep_factor=0.2,
        idle_auto_off=True,
        focus_factor=1.0,
        night_factor=1.0,
        global_factor=1.0,
        escalation_boost=2.0,
        is_screen_bar=True,
        screen_bar_min_glow=0.75,
    )

    assert result.brightness == 0
    assert _names(result.trace) == ("base", "idle_auto_off", "normalize")
