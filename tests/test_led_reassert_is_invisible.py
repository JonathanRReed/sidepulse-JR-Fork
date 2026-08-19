"""A reassert refreshes device state without replaying the bright
transition frame -- the flash that read as an agent asking a question."""

from __future__ import annotations

from pathlib import Path

from sidepulse._led_status_legacy import (
    AgentLedController,
    LedDisplayState,
    _steady_state_variant,
)


def test_steady_state_variant_drops_only_the_approach_frame() -> None:
    program = (
        "brightness 18\n"
        "0:#F87C5F 160ms none; 1:#260C06 160ms none\n"
        "0:#010101 1600ms pulse 0ms; 1:#020305 1600ms pulse 200ms\n"
        "repeat"
    )
    assert _steady_state_variant(program) == (
        "brightness 18\n"
        "0:#010101 1600ms pulse 0ms; 1:#020305 1600ms pulse 200ms\n"
        "repeat"
    )


def test_steady_state_variant_keeps_short_programs_intact() -> None:
    for program in ("off", "#00FF00 1s", "brightness 40\n#00FF00 1s\nrepeat"):
        assert _steady_state_variant(program) == program


def test_reassert_write_omits_the_approach_frame(tmp_path: Path) -> None:
    device = tmp_path / "SidePulsePro"
    device.mkdir()
    controller = AgentLedController(device_path=device)
    controller.reassert_after_seconds = 0.0

    program = (
        "0:#F87C5F 160ms none; 1:#260C06 160ms none\n"
        "0:#010101 1600ms pulse 0ms; 1:#020305 1600ms pulse 200ms\n"
        "repeat"
    )
    def content_lines() -> list[str]:
        return [
            line
            for line in (device / "LEDS.LED").read_text().splitlines()
            if line and not line.startswith("brightness ")
        ]

    first = controller.sync_program(
        program, LedDisplayState.WORKING, dedupe_token=("t", 1)
    )
    assert first.changed
    # Full write: approach frame + loop + repeat (colours strip-transferred).
    assert len(content_lines()) == 3

    second = controller.sync_program(
        program, LedDisplayState.WORKING, dedupe_token=("t", 1)
    )
    assert second.changed
    lines = content_lines()
    assert len(lines) == 2
    assert lines[-1].startswith("repeat")


def test_a_done_agent_rests_dark_beside_a_working_one() -> None:
    """The completion sweep is the celebration; a done agent's LEDs must
    not hold bright for the 20-minute visibility window afterwards."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from sidepulse.attention import project_attention
    from sidepulse.colors import ColorSettings, program_for_projection
    from sidepulse.led_wasm import LedWasmUnavailableError, SdLedWasmController
    from sidepulse.models import AgentMode, AgentStatus
    from sidepulse.settings import AgentMonitorSettings

    def status(provider, sid, mode, event):
        return AgentStatus(
            provider=provider,
            agent_id=f"{provider}:session:{sid}",
            display_name=sid,
            mode=mode,
            updated_at=datetime.now(timezone.utc),
            event_name=event,
            session_id=sid,
        )

    snap = SimpleNamespace(
        statuses=(
            status("claude", "a", AgentMode.COMPLETED, "Stop"),
            status("grok", "b", AgentMode.WORKING, "PostToolUse"),
        ),
        stale_statuses=(),
        collected_at=datetime.now(timezone.utc),
    )
    projection = project_attention(snap, AgentMonitorSettings())
    _state, program = program_for_projection(
        projection, led_count=8, colors=ColorSettings(), relay_elapsed_seconds=0.3
    )
    try:
        controller = SdLedWasmController(led_count=8)
    except LedWasmUnavailableError:
        import pytest

        pytest.skip("JavaScriptCore unavailable")
    assert controller.parse(program, 0).ok
    # The done agent's LEDs (alternating assignment) must never sit at a
    # held bright level across a full loop; sample generously.
    max_seen = [0] * 8
    for t in range(0, 8000, 40):
        pixels = controller.step(t)
        for index in range(8):
            max_seen[index] = max(max_seen[index], sum(pixels[index]))
    bright_leds = [index for index, value in enumerate(max_seen) if value > 400]
    # Only the WORKING agent's pulses may get bright; a steady-held done
    # peak lit half the strip before this rule.
    assert len(bright_leds) <= 4, (bright_leds, max_seen)
