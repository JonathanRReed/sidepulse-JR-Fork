"""Motion alone must never rewrite the device.

A relay program ends in `repeat` and encodes its rotation as per-LED
pulse delays, so the hardware animates itself. The dedupe compared the
rendered text, which carries a wall-clock phase, so it differed on
essentially every call: the 60-second reassert window never engaged and
the device was rewritten every refresh -- roughly 25-40 syscalls plus an
fsync and a full readback each time, on an app that also refreshes on a
0.25s event floor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sidepulse.colors import BLEND_MODE_RELAY, ColorSettings
from sidepulse.led_status import AgentLedController
from sidepulse.models import AgentMode, AgentStatus


def _status(provider: str, mode: AgentMode = AgentMode.WORKING) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=f"{provider}:session:1",
        display_name=provider,
        mode=mode,
        updated_at=datetime.now(timezone.utc),
        event_name="PreToolUse",
    )


def _controller(tmp_path: Path) -> AgentLedController:
    device = tmp_path / "SidePulsePro"
    device.mkdir()
    return AgentLedController(device_path=device)


def test_advancing_relay_phase_writes_once(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    colors = ColorSettings.defaults().with_blend_mode(BLEND_MODE_RELAY)
    statuses = (_status("codex"), _status("claude"))

    writes = 0
    for phase in (0.0, 0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8):
        result = controller.sync_snapshot(
            statuses, colors, relay_elapsed_seconds=phase
        )
        if result.changed:
            writes += 1

    # One write to establish the loop; the device animates the rest.
    assert writes == 1, f"phase motion caused {writes} device writes"


def test_a_real_change_still_writes(tmp_path: Path) -> None:
    """Dedupe must not become blindness: content changes still land."""
    controller = _controller(tmp_path)
    colors = ColorSettings.defaults().with_blend_mode(BLEND_MODE_RELAY)

    first = controller.sync_snapshot(
        (_status("codex"), _status("claude")), colors, relay_elapsed_seconds=0.0
    )
    assert first.changed is True

    # An agent enters an ask -- visually different at any phase.
    changed = controller.sync_snapshot(
        (_status("codex"), _status("claude", AgentMode.WAITING_FOR_INPUT)),
        colors,
        relay_elapsed_seconds=0.4,
    )
    assert changed.changed is True


def test_screen_bar_borrows_the_hardware_animation_by_default() -> None:
    """One light language in two places.

    Linked (the default), the notch must not render a different
    animation than the LEDs -- two surfaces disagreeing about the same
    moment is the bug this option exists to prevent.
    """
    from sidepulse.settings import AgentMonitorSettings, DeviceDisplaySetting

    settings = AgentMonitorSettings(
        devices=(
            DeviceDisplaySetting(
                device_id="virtual:status-bar", name="Screen Bar", path="virtual:status-bar",
                blend_mode="cycle",
            ),
            DeviceDisplaySetting(
                device_id="/Volumes/SidePulse", name="SidePulse Pro", path="/Volumes/SidePulse",
                blend_mode="relay",
            ),
        )
    )
    assert settings.link_screen_bar_to_hardware is True

    class _Controller:
        pass

    from sidepulse.status_bar import StatusBarController

    controller = _Controller()
    controller.settings = settings
    # Linked: the notch borrows the hardware's animation, ignoring its own.
    assert StatusBarController.screen_bar_blend_override(controller) == "relay"
    # Unlinked: the notch keeps its own choice.
    controller.settings = settings.with_link_screen_bar_to_hardware(False)
    assert StatusBarController.screen_bar_blend_override(controller) == "cycle"
