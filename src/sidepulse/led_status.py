"""Presentation-safety facade for SidePulse LED program generation."""

from __future__ import annotations

from . import _led_status_legacy as _legacy
from .presentation_compiler import compile_presentation_program

_ORIGINAL_STYLE_TO_PROGRAM = _legacy.style_to_program
_ORIGINAL_FOR_STRIP = _legacy.AgentLedController._for_strip


def style_to_program(
    style,
    brightness: float = 255,
    *,
    color: str | None = None,
    led_count: int = 8,
) -> str:
    program = _ORIGINAL_STYLE_TO_PROGRAM(
        style,
        brightness,
        color=color,
        led_count=led_count,
    )
    return compile_presentation_program(program, led_count=led_count).program


def _safe_for_strip(self, program: str) -> str:
    compiled = compile_presentation_program(program, led_count=8)
    return _ORIGINAL_FOR_STRIP(self, compiled.program)


_legacy.style_to_program = style_to_program
_legacy.AgentLedController._for_strip = _safe_for_strip

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
