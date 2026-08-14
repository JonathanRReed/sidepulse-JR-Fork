"""Neither animated surface may be frozen by its own dedupe.

Reported live: "the screen bar and pulse bar aren't doing anything right
now; they're just stuck on a color."

The two surfaces are animated by different machinery and were given
opposite protection. The LED strip is animated by FIRMWARE looping a
program it already holds, so a phase-free dedupe is exactly right there
-- and it still re-asserts every 60s (``LED_REASSERT_SECONDS``). The
Screen Bar is animated by a Python sampler with six silent ``return``
paths and a ``_step`` that returns None on any LED-count or
channel-range mismatch, and its gate had NO time term at all: once
``_program_identity`` matched it returned forever, and any one of those
exits left the notch painting ``last_safe_colors`` with no error, no log
and nothing to re-command it.

So the notch's gate now includes what actually advances its animation --
whether its sampler is running the motion the token claims -- plus a
reassert as the backstop.
"""

from __future__ import annotations

import pytest

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.presentation_policy import (
    GlanceOverrideReason,
    GlanceSemantic,
    MotionClass,
    ResolvedGlance,
    SemanticGlyph,
    compose_presentation_program,
    continuous_presentation_identity,
)


class _Window:
    def isVisible(self) -> bool:
        return True

    def orderOut_(self, _sender) -> None:
        return None


class _View:
    def __init__(self) -> None:
        self.programs: list[tuple[str, float]] = []
        self.fixed_colors = [(0.0, 0.0, 0.0, 0.0)] * 8

    def setPresentationProgram_startedAt_(self, program, started_at) -> None:
        self.programs.append((str(program), float(started_at)))


class _Sampler:
    def __init__(self) -> None:
        self.commands: list[object] = []

    def reconcile(self, command: object) -> None:
        self.commands.append(command)

    def close(self, *, timeout_seconds: float) -> bool:
        return True


@pytest.fixture()
def notch(monkeypatch):
    from sidepulse import virtual_device

    clock = {"now": 1_000.0}
    monkeypatch.setattr(virtual_device.time, "monotonic", lambda: clock["now"])
    device = virtual_device.VirtualStatusDevice.alloc().init()
    device.window = _Window()
    device.view = _View()
    device.show = lambda: None
    device._refresh_render_cadence = lambda *_args, **_kwargs: None
    device._advance_presentation_generation = lambda **_kwargs: None
    device._sampler = _Sampler()
    return device, clock, virtual_device


def _continuous_program():
    resolved = ResolvedGlance(
        semantic=GlanceSemantic.ACTIVE,
        glyph=SemanticGlyph.CENTER_PAIR,
        cue=None,
        override_reason=GlanceOverrideReason.NONE,
        relay_epoch=900.0,
        next_visual_change_at=None,
    )
    return compose_presentation_program(
        resolved,
        presentation_time=1_000.0,
        led_count=8,
        color="#D97757",
        preferences=AccessibilityDisplayPreferences(),
    )


def _apply(device, presentation, *, anchor: float) -> None:
    device.set_program(
        presentation.dsl,
        started_at=anchor,
        motion=presentation.motion,
        static_fallback_program=presentation.static_fallback_dsl,
        dedupe_token=continuous_presentation_identity(presentation),
    )


def test_the_notch_dedupe_still_suppresses_an_unchanged_repeat(notch) -> None:
    """The dedupe must keep doing its job -- this is not a licence to churn."""
    device, _clock, _module = notch
    presentation = _continuous_program()
    assert presentation.motion is MotionClass.CONTINUOUS

    _apply(device, presentation, anchor=1_000.0)
    baseline = len(device._sampler.commands)
    for _ in range(20):
        _apply(device, presentation, anchor=1_000.0)

    assert len(device._sampler.commands) == baseline


def test_a_dead_sampler_does_not_hold_the_notch_frozen(notch) -> None:
    """The gate must include whatever ADVANCES the animation.

    A phase-free token says nothing about whether anything is still
    playing it. Six silent returns in ScreenBarSampler can stop the
    motion loop; the token still matches, so the surface was never
    re-commanded and stayed on one colour indefinitely.
    """
    device, _clock, _module = notch
    presentation = _continuous_program()
    _apply(device, presentation, anchor=1_000.0)

    # Exactly what a silent sampler exit leaves behind: the token still
    # matches, but nothing is drawing frames any more.
    device._sampler = _Sampler()
    device._animation_active = False

    _apply(device, presentation, anchor=1_000.0)

    assert device._animation_active is True
    assert len(device._sampler.commands) == 1
    assert device._sampler.commands[0].motion is MotionClass.CONTINUOUS


def test_the_notch_reasserts_on_a_timer_like_the_strip_does(notch) -> None:
    """The backstop, for a freeze the liveness check cannot see."""
    from sidepulse.virtual_device import SCREEN_BAR_REASSERT_SECONDS

    device, clock, _module = notch
    presentation = _continuous_program()
    _apply(device, presentation, anchor=1_000.0)
    device._sampler = _Sampler()

    clock["now"] += SCREEN_BAR_REASSERT_SECONDS - 0.5
    _apply(device, presentation, anchor=1_000.0)
    assert device._sampler.commands == []

    clock["now"] += 1.0
    _apply(device, presentation, anchor=1_000.0)
    assert len(device._sampler.commands) == 1


def test_a_reassert_resumes_the_same_phase_rather_than_restarting_it(notch) -> None:
    """Re-commanding must not be visible as a stutter.

    ``parse_anchor`` is absolute monotonic time, so handing the sampler
    the same anchor resumes the animation where it is instead of
    snapping it back to frame zero.
    """
    from sidepulse.virtual_device import SCREEN_BAR_REASSERT_SECONDS

    device, clock, _module = notch
    presentation = _continuous_program()
    _apply(device, presentation, anchor=1_000.0)
    device._sampler = _Sampler()

    clock["now"] += SCREEN_BAR_REASSERT_SECONDS + 1.0
    _apply(device, presentation, anchor=1_000.0)

    assert device._sampler.commands[0].parse_anchor == 1_000.0


def test_the_strip_keeps_its_phase_free_dedupe(monkeypatch, tmp_path) -> None:
    """The strip's firmware loops the program, so phase must NOT write.

    Regression guard in the other direction: a text compare here differs
    on essentially every call, the 60s reassert never engages, and the
    device is rewritten every refresh at ~30 syscalls plus an fsync and a
    readback, to USB mass storage.
    """
    from datetime import datetime, timezone

    from sidepulse.colors import BLEND_MODE_RELAY, ColorSettings
    from sidepulse.led_status import AgentLedController
    from sidepulse.models import AgentMode, AgentStatus

    statuses = tuple(
        AgentStatus(
            provider=provider,
            agent_id=f"{provider}:session:{provider}",
            display_name=provider,
            mode=AgentMode.WORKING,
            updated_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
            event_name="PreToolUse",
            session_id=provider,
        )
        for provider in ("claude", "codex")
    )
    colors = ColorSettings.defaults().with_blend_mode(BLEND_MODE_RELAY)
    controller = AgentLedController(device_path=tmp_path, file_name="LEDS.LED")

    first = controller.sync_snapshot(statuses, colors, relay_elapsed_seconds=0.0)
    later = controller.sync_snapshot(statuses, colors, relay_elapsed_seconds=0.37)

    assert first.changed is True
    assert later.changed is False
