"""Exact final-byte validation against the packaged SidePulse firmware parser."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ._led_wasm_legacy import LedWasmUnavailableError, SdLedWasmController


class FirmwareValidationError(ValueError):
    pass


class FirmwareValidationUnavailableError(FirmwareValidationError):
    pass


@dataclass(frozen=True, slots=True)
class FirmwareValidationResult:
    accepted: bool
    reason: str | None = None
    line: int | None = None
    column: int | None = None


_THREAD_LOCAL = threading.local()


def _controller(led_count: int) -> SdLedWasmController:
    count = 2 if int(led_count) == 2 else 8
    controllers = getattr(_THREAD_LOCAL, "controllers", None)
    if controllers is None:
        controllers = {}
        _THREAD_LOCAL.controllers = controllers
    controller = controllers.get(count)
    if controller is None:
        controller = SdLedWasmController(led_count=count)
        controllers[count] = controller
    return controller


def validate_firmware_program(
    program: str,
    *,
    led_count: int,
) -> FirmwareValidationResult:
    try:
        controller = _controller(led_count)
        controller.reset(0)
        result = controller.parse(program, 0)
    except LedWasmUnavailableError as exc:
        raise FirmwareValidationUnavailableError(
            "firmware parser is unavailable"
        ) from exc
    except Exception as exc:
        raise FirmwareValidationUnavailableError(
            "firmware parser could not run"
        ) from exc
    if result.ok:
        return FirmwareValidationResult(True)
    return FirmwareValidationResult(
        False,
        result.error_name,
        result.line,
        result.column,
    )


def require_firmware_program(program: str, *, led_count: int) -> None:
    result = validate_firmware_program(program, led_count=led_count)
    if result.accepted:
        return
    raise FirmwareValidationError(
        f"firmware rejected program: {result.reason} "
        f"at line {result.line}, column {result.column}"
    )
