"""The per-provider animation must reach the SOLO-agent render path.

The setting was honored by the multi-agent renderers but a lone agent
renders through compose_presentation_program, which always chased --
picking "Breathe" for a solo Codex changed nothing (reported live
2026-08-21)."""

from __future__ import annotations

from sidepulse.colors import ColorSettings
from sidepulse.presentation_policy import (
    AccessibilityDisplayPreferences,
    GlanceInputs,
    GlanceOverrideReason,
    MotionClass,
    compose_presentation_program,
    resolve_glance,
)

PREFS = AccessibilityDisplayPreferences(False, False, False, False)


def _active_glance():
    return resolve_glance(
        GlanceInputs(
            actionable_episode_key=None,
            fresh_failure=None,
            fresh_completion=None,
            active=True,
            unresolved_failure=False,
            capacity=None,
            override_reason=GlanceOverrideReason.NONE,
            override_semantic=None,
        ),
        presentation_time=100.0,
        relay_epoch=90.0,
        preferences=PREFS,
    )


def _compose(color_settings=None, provider="codex"):
    return compose_presentation_program(
        _active_glance(),
        presentation_time=100.0,
        led_count=8,
        color="#10A37F",
        preferences=PREFS,
        provider=provider,
        color_settings=color_settings,
    )


def test_solo_agent_honors_the_provider_animation() -> None:
    base = ColorSettings.defaults()
    chase = _compose(color_settings=base)
    breathe = _compose(base.with_agent_animation("codex", "breathe"))
    blink = _compose(base.with_agent_animation("codex", "blink"))
    steady = _compose(base.with_agent_animation("codex", "steady"))

    # Automatic keeps the relay chase exactly as before.
    assert "pulse 0ms" in chase.dsl
    # Breathe: unison swell, still CONTINUOUS, still repeat-bounded.
    assert breathe.dsl != chase.dsl
    assert breathe.motion is MotionClass.CONTINUOUS
    assert "cosine" in breathe.dsl and "repeat" in breathe.dsl
    # Blink: hard edges.
    assert "none" in blink.dsl and blink.dsl != chase.dsl
    # Steady: no motion at all.
    assert steady.motion is MotionClass.STATIC

    # Another provider's setting must not leak onto codex.
    other = _compose(
        base.with_agent_animation("claude", "breathe"), provider="codex"
    )
    assert other.dsl == chase.dsl


def test_urgent_semantics_ignore_the_override() -> None:
    base = ColorSettings.defaults().with_agent_animation("codex", "steady")
    resolved = resolve_glance(
        GlanceInputs(
            actionable_episode_key="attention:req-1",
            fresh_failure=None,
            fresh_completion=None,
            active=True,
            unresolved_failure=False,
            capacity=None,
            override_reason=GlanceOverrideReason.NONE,
            override_semantic=None,
        ),
        presentation_time=100.0,
        relay_epoch=90.0,
        preferences=PREFS,
    )
    program = compose_presentation_program(
        resolved,
        presentation_time=100.0,
        led_count=8,
        color="#10A37F",
        preferences=PREFS,
        provider="codex",
        color_settings=base,
    )
    # An ask must never be flattened to steady by a provider preference.
    assert program.motion is not MotionClass.STATIC


def test_every_animation_style_renders_a_distinct_program() -> None:
    """The expanded vocabulary (heartbeat/scanner/comet/flicker) must
    each survive the safety compiler as its own shape -- a style that
    fails closed to the static glyph is a dead menu entry."""
    from sidepulse.colors import PROVIDER_ANIMATION_CHOICES

    base = ColorSettings.defaults()
    programs = {}
    for style in PROVIDER_ANIMATION_CHOICES:
        if style == "auto":
            continue
        program = _compose(base.with_agent_animation("codex", style))
        programs[style] = program.dsl
        if style != "steady":
            assert program.motion is MotionClass.CONTINUOUS, style
    assert len(set(programs.values())) == len(programs)


def test_multi_agent_segments_support_the_new_rhythms() -> None:
    """The per-LED segment builder must give heartbeat and flicker real
    shapes and degrade positional sweeps to the travelling wave."""
    from sidepulse.colors import _motion_segments
    from sidepulse.led_status import LedDisplayState

    base = ColorSettings.defaults()

    def segments(style):
        return _motion_segments(
            3,
            "#10A37F",
            LedDisplayState.WORKING,
            base.with_agent_animation("codex", style),
            cycle_ms=2400,
            settle_ms=160,
            chase_delay_ms=300,
            provider="codex",
        )

    _, heartbeat = segments("heartbeat")
    assert heartbeat.count("pulse") == 2  # lub-dub

    _, flicker = segments("flicker")
    _, breathe = segments("breathe")
    assert flicker != breathe  # detuned, not unison

    _, scanner = segments("scanner")
    _, chase = segments("chase")
    assert scanner == chase  # positional sweep degrades in a shared layout
