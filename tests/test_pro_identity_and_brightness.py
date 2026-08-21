"""The Pro strip's own STATUS.TXT serial must win over its generic volume name.

Field bug: the Pro mounts as /Volumes/SidePulse with VolumeName "SIDEPULSE",
so name-based classification called it a Dot, keyed its remembered settings
under sidepulse:dot:volume:..., and the real device's brightness controls
edited an entry no write path ever read. The firmware's STATUS.TXT
(serial SPP-000067) is the device's self-identification -- use it.
"""

from __future__ import annotations

import plistlib
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sidepulse.device_identity import DeviceKind
from sidepulse.device_inventory import (
    hardware_status_serial,
    inventory_mounts,
    refine_facts_with_hardware_status,
)


def completed(payload: dict, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["diskutil"],
        returncode=returncode,
        stdout=plistlib.dumps(payload),
        stderr=b"",
    )


def _runner_for(mount: Path):
    def runner(arguments, **kwargs):
        return completed(
            {
                "MountPoint": str(mount),
                "VolumeName": "SIDEPULSE",
                "VolumeUUID": "A1B2-C3D4",
            }
        )

    return runner


def _write_status(mount: Path, serial: str = "SPP-000067") -> None:
    (mount / "STATUS.TXT").write_text(
        f"release_version 1.0.2\nserial {serial}\nserial_number 67\nstate idle\n"
    )


def test_status_txt_serial_reclassifies_the_pro(tmp_path: Path) -> None:
    mount = tmp_path / "SidePulse"
    mount.mkdir()
    _write_status(mount)

    identities = inventory_mounts(tmp_path, runner=_runner_for(mount))

    assert len(identities) == 1
    identity = identities[0]
    assert identity.kind is DeviceKind.PRO
    assert identity.label == "SidePulse Pro"
    assert identity.evidence == "serial"
    assert identity.key.startswith("sidepulse:pro:serial:")


def test_missing_status_txt_keeps_name_based_kind(tmp_path: Path) -> None:
    mount = tmp_path / "SidePulse"
    mount.mkdir()

    identities = inventory_mounts(tmp_path, runner=_runner_for(mount))

    assert len(identities) == 1
    assert identities[0].kind is DeviceKind.DOT


def test_unknown_serial_prefix_keeps_name_based_kind(tmp_path: Path) -> None:
    mount = tmp_path / "SidePulse"
    mount.mkdir()
    _write_status(mount, serial="ZZZ-000001")

    identities = inventory_mounts(tmp_path, runner=_runner_for(mount))

    assert len(identities) == 1
    identity = identities[0]
    # Unknown prefix: kind stays name-derived, but the hardware serial is
    # still the strongest stable evidence for the key.
    assert identity.kind is DeviceKind.DOT
    assert identity.evidence == "serial"


def test_garbage_status_txt_is_tolerated(tmp_path: Path) -> None:
    mount = tmp_path / "SidePulse"
    mount.mkdir()
    (mount / "STATUS.TXT").write_bytes(b"\xff\xfe\x00garbage\nserial\n")

    assert hardware_status_serial(mount) is None
    identities = inventory_mounts(tmp_path, runner=_runner_for(mount))
    assert len(identities) == 1


def test_refine_is_a_noop_without_serial(tmp_path: Path) -> None:
    from sidepulse.device_identity import DeviceHardwareFacts

    facts = DeviceHardwareFacts(mount_path="/Volumes/SidePulse", product_name="SIDEPULSE")
    assert refine_facts_with_hardware_status(facts, tmp_path) is facts


def test_rekeyed_ghost_entry_is_not_persistable() -> None:
    """The old dot-keyed entry for a mount now owned by a pro key must die.

    Otherwise the re-classified Pro leaves behind a phantom disconnected
    "SidePulse Dot" row whose junk preferences (brightness 0) linger in
    settings forever.
    """
    from sidepulse import status_bar
    from sidepulse.device_identity import StableDeviceIdentity

    pro = StableDeviceIdentity(
        key="sidepulse:pro:serial:abc123",
        kind=DeviceKind.PRO,
        label="SidePulse Pro",
        mount_path="/Volumes/SidePulse",
        connected=True,
        evidence="serial",
    )
    with patch.object(status_bar._DEVICE_IDENTITIES, "snapshot", return_value=(pro,)):
        assert not status_bar.persistable_device_identity(
            "sidepulse:dot:volume:2b55a4f03d29c236f7a51346",
            "/Volumes/SidePulse",
        )
        # The live key itself stays persistable.
        assert status_bar.persistable_device_identity(
            "sidepulse:pro:serial:abc123",
            "/Volumes/SidePulse",
        )
        # A stable key for some OTHER mount is untouched by this rule.
        assert status_bar.persistable_device_identity(
            "sidepulse:dot:volume:elsewhere",
            "/Volumes/SidePulseDot",
        )


def _working_status(event_name: str, age_seconds: float):
    from sidepulse.models import AgentMode, AgentStatus

    now = datetime.now(timezone.utc)
    return (
        AgentStatus(
            provider="claude",
            agent_id="claude:session:x",
            display_name="x",
            mode=AgentMode.WORKING,
            updated_at=now - timedelta(seconds=age_seconds),
            event_name=event_name,
            session_id="x",
        ),
        now,
    )


def test_working_after_tool_failure_demotes_like_post_tool() -> None:
    from sidepulse._collector_legacy import status_for_snapshot
    from sidepulse.models import AgentMode

    status, now = _working_status("PostToolUseFailure", 3 * 60.0)
    effective = status_for_snapshot(
        status, now, post_tool_working_visible_seconds=2 * 60.0
    )
    assert effective.mode is AgentMode.ENDED_UNCONFIRMED


def test_hook_silent_working_demotes_after_silence_window() -> None:
    """A "working" agent with no hook events for 10 minutes is a dead one
    (crashed turn, killed terminal) -- it must not pulse for the full
    hour-long stale window as a phantom."""
    from sidepulse._collector_legacy import (
        WORKING_SILENCE_SECONDS,
        status_for_snapshot,
    )
    from sidepulse.models import AgentMode

    status, now = _working_status("UserPromptSubmit", WORKING_SILENCE_SECONDS + 1.0)
    effective = status_for_snapshot(
        status, now, post_tool_working_visible_seconds=2 * 60.0
    )
    assert effective.mode is AgentMode.ENDED_UNCONFIRMED


def test_recent_working_stays_working() -> None:
    from sidepulse._collector_legacy import status_for_snapshot
    from sidepulse.models import AgentMode

    for event in ("PostToolUse", "PostToolUseFailure", "UserPromptSubmit"):
        status, now = _working_status(event, 30.0)
        effective = status_for_snapshot(
            status, now, post_tool_working_visible_seconds=2 * 60.0
        )
        assert effective.mode is AgentMode.WORKING


def test_long_thinking_turn_survives_the_post_tool_window() -> None:
    """WORKING that is NOT post-tool (agent mid-turn, thinking) gets the
    LONGER silence window, not the 2-minute post-tool one -- derived
    from the shared constant so the pin moves with the ratified line."""
    from sidepulse._collector_legacy import (
        WORKING_SILENCE_SECONDS,
        status_for_snapshot,
    )
    from sidepulse.models import AgentMode

    status, now = _working_status("UserPromptSubmit", WORKING_SILENCE_SECONDS - 60.0)
    effective = status_for_snapshot(
        status, now, post_tool_working_visible_seconds=2 * 60.0
    )
    assert effective.mode is AgentMode.WORKING

    ended, later = _working_status("UserPromptSubmit", WORKING_SILENCE_SECONDS + 60.0)
    assert (
        status_for_snapshot(
            ended, later, post_tool_working_visible_seconds=2 * 60.0
        ).mode
        is AgentMode.ENDED_UNCONFIRMED
    )


def test_a_whisper_too_dim_to_hold_its_hue_goes_honestly_dark():
    """2026-08-20, photographed live: at drive 1-2 the green die emits
    several times more light than red or blue, so 'barely-visible white'
    #010101 rendered as a clearly GREEN glow -- 'why is the SidePulse
    green when it should be off.' A whole LED whose brightest drive
    lands below STRIP_HUE_HOLDING_DRIVE goes dark instead of lying."""
    from sidepulse._led_status_legacy import (
        NEUTRAL_CHANNEL_GAINS,
        apply_strip_transfer_to_hex,
    )

    for whisper in ("#010101", "#000101", "#020204"):
        assert apply_strip_transfer_to_hex(whisper, NEUTRAL_CHANNEL_GAINS) == "#000000"
    # A dim color that CAN say its hue keeps the classic floor behavior.
    assert apply_strip_transfer_to_hex("#010530", NEUTRAL_CHANNEL_GAINS) != "#000000"
    # And true black stays black.
    assert apply_strip_transfer_to_hex("#000000", NEUTRAL_CHANNEL_GAINS) == "#000000"
