from __future__ import annotations

from dataclasses import dataclass

from sidepulse import firmware_validation


@dataclass(frozen=True)
class _ParseResult:
    ok: bool = True
    error_name: str = "none"
    line: int = 0
    column: int = 0


class _Controller:
    def __init__(self) -> None:
        self.reset_calls = 0
        self.parse_calls = 0

    def reset(self, _now_ms: int) -> None:
        self.reset_calls += 1

    def parse(self, _program: str, _now_ms: int) -> _ParseResult:
        self.parse_calls += 1
        return _ParseResult()


def test_exact_program_and_led_count_are_validated_once(monkeypatch) -> None:
    controllers = {2: _Controller(), 8: _Controller()}
    firmware_validation.validate_firmware_program.cache_clear()
    monkeypatch.setattr(
        firmware_validation,
        "_controller",
        lambda led_count: controllers[led_count],
    )

    try:
        first = firmware_validation.validate_firmware_program(
            "#00E5FF",
            led_count=8,
        )
        second = firmware_validation.validate_firmware_program(
            "#00E5FF",
            led_count=8,
        )
        dot = firmware_validation.validate_firmware_program(
            "#00E5FF",
            led_count=2,
        )
    finally:
        firmware_validation.validate_firmware_program.cache_clear()

    assert first.accepted and second.accepted and dot.accepted
    assert controllers[8].reset_calls == 1
    assert controllers[8].parse_calls == 1
    assert controllers[2].reset_calls == 1
    assert controllers[2].parse_calls == 1
