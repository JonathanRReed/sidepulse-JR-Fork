from pathlib import Path

import pytest

from sidepulse import firmware_validation
from sidepulse.animation import loop_duration_ms, parse_animation
from sidepulse.device_writer import DeviceWriteError, write_led_program
from sidepulse.presentation_compiler import MIN_PRESENTATION_CYCLE_MS


def test_exact_post_safety_program_is_validated_before_the_file_is_opened(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed = []

    def validate(program: str, *, led_count: int) -> None:
        observed.append((program, led_count))

    monkeypatch.setattr(firmware_validation, "require_firmware_program", validate)
    target = write_led_program(
        "#00E5FF 100ms none\noff 100ms none\nrepeat",
        device_path=tmp_path,
    )

    written = target.read_text(encoding="utf-8")
    assert observed == [(written, 8)]
    assert loop_duration_ms(parse_animation(written)) >= MIN_PRESENTATION_CYCLE_MS


def test_firmware_rejection_occurs_before_any_device_file_is_created(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def reject(_program: str, *, led_count: int) -> None:
        del led_count
        raise firmware_validation.FirmwareValidationError("rejected")

    monkeypatch.setattr(firmware_validation, "require_firmware_program", reject)

    with pytest.raises(DeviceWriteError, match="rejected"):
        write_led_program("#00E5FF 1s pulse\nrepeat", device_path=tmp_path)

    assert not (tmp_path / "LEDS.LED").exists()
