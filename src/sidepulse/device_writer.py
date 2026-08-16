"""Safety and firmware-validation facade for physical SidePulse writes."""

from __future__ import annotations

from pathlib import Path

from . import _device_writer_legacy as _legacy

_ORIGINAL_WRITE_LED_PROGRAM = _legacy.write_led_program


def _led_count_for_target(target: Path) -> int:
    normalized = "".join(
        character
        for character in target.parent.name.lower()
        if character.isalnum()
    )
    return 2 if "sidepulsedot" in normalized else 8


def write_led_program(
    text: str,
    *,
    device_path: Path | None = None,
    file_name: str = _legacy.DEFAULT_FILE_NAME,
    dry_run: bool = False,
    preserve_existing_inode: bool = False,
) -> Path:
    from .firmware_validation import (
        FirmwareValidationError,
        FirmwareValidationUnavailableError,
        require_firmware_program,
    )
    from .presentation_compiler import compile_presentation_program

    normalized = _legacy.normalize_led_text(text)
    _legacy.validate_led_text(normalized)
    target = _legacy.resolve_target_path(
        device_path=device_path,
        file_name=file_name,
    )
    led_count = _led_count_for_target(target)
    compiled = compile_presentation_program(normalized, led_count=led_count)
    if not compiled.accepted:
        raise _legacy.DeviceWriteError(
            "LED program failed the presentation safety gate."
        )
    final_program = compiled.program
    _legacy.validate_led_text(final_program)
    if not dry_run:
        try:
            require_firmware_program(final_program, led_count=led_count)
        except FirmwareValidationUnavailableError as exc:
            raise _legacy.DeviceWriteError(
                "The packaged firmware parser is unavailable; write refused."
            ) from exc
        except FirmwareValidationError as exc:
            raise _legacy.DeviceWriteError(str(exc)) from exc
    return _ORIGINAL_WRITE_LED_PROGRAM(
        final_program,
        device_path=target,
        file_name=file_name,
        dry_run=dry_run,
        preserve_existing_inode=preserve_existing_inode,
    )


_legacy.write_led_program = write_led_program

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
