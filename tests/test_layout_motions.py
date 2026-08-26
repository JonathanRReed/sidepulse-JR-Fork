"""Per-provider motions render for real in the multi-agent layouts.

Owner decision 2026-08-26 ("make motions real everywhere"): Cycle used
to ignore the motion picker entirely, Spatial Split honored only
Steady, and aurora/drift were byte-identical in every shared strip.
Every program below also has to survive the real firmware grammar --
the budget-degrade path included.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sidepulse.colors import (
    BLEND_MODE_CYCLE,
    BLEND_MODE_ROUND_ROBIN,
    BLEND_MODE_SPATIAL,
    ColorSettings,
    program_for_snapshot,
)
from sidepulse.firmware_validation import validate_firmware_program
from sidepulse.models import AgentMode, AgentStatus


def status(provider: str, session: str = "main") -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=f"{provider}:session:{session}",
        display_name=provider.title(),
        mode=AgentMode.WORKING,
        updated_at=datetime.now(timezone.utc),
        event_name="PostToolUse",
        session_id=session,
    )


def program(settings: ColorSettings, providers: tuple[str, ...]) -> str:
    _state, text = program_for_snapshot(
        tuple(status(provider) for provider in providers),
        led_count=8,
        colors=settings,
        brightness=255,
    )
    return text


def firmware_ok(text: str) -> None:
    result = validate_firmware_program(text, led_count=8)
    assert result.accepted, result.reason


def test_cycle_turns_render_the_chosen_rhythm_class() -> None:
    base = ColorSettings.defaults().with_blend_mode(BLEND_MODE_CYCLE)
    plain = program(base, ("claude", "codex"))
    heartbeat = program(
        base.with_agent_animation("claude", "heartbeat"), ("claude", "codex")
    )
    gradient = program(
        base.with_agent_animation("claude", "gradient"), ("claude", "codex")
    )
    assert heartbeat != plain, "Cycle ignored the motion picker"
    assert gradient != plain
    assert heartbeat != gradient
    # A positional turn owns the whole strip: indexed segments appear.
    assert "0:" in gradient
    for text in (plain, heartbeat, gradient):
        firmware_ok(text)


def test_cycle_degrades_from_the_end_to_fit_the_firmware_budget() -> None:
    base = ColorSettings.defaults().with_blend_mode(BLEND_MODE_CYCLE)
    settings = base
    providers = ("claude", "codex", "devin", "grok", "cursor", "hermes")
    for provider in providers:
        settings = settings.with_agent_animation(provider, "scanner")
    text = program(settings, providers)
    assert len(text.encode("utf-8")) <= 512
    assert text.count("\n") + 1 <= 20
    firmware_ok(text)


def test_spatial_blocks_honor_the_motion_vocabulary() -> None:
    base = ColorSettings.defaults().with_blend_mode(BLEND_MODE_SPATIAL)
    plain = program(base, ("claude", "codex"))
    kitt = program(base.with_agent_animation("claude", "kitt"), ("claude", "codex"))
    twinkle = program(
        base.with_agent_animation("claude", "twinkle"), ("claude", "codex")
    )
    assert kitt != plain, "Spatial Split honored only Steady"
    assert twinkle != plain
    assert kitt != twinkle
    for text in (plain, kitt, twinkle):
        firmware_ok(text)


def test_aurora_and_drift_are_distinct_in_shared_strips() -> None:
    base = ColorSettings.defaults().with_blend_mode(BLEND_MODE_ROUND_ROBIN)
    aurora = program(base.with_agent_animation("claude", "aurora"), ("claude", "codex"))
    drift = program(base.with_agent_animation("claude", "drift"), ("claude", "codex"))
    assert aurora != drift, "an 18-choice picker carried a dead pair"
    for text in (aurora, drift):
        firmware_ok(text)
