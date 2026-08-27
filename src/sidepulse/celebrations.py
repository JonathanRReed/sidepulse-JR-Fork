"""Finite celebration programs: the refill, once, then dark.

The original reset celebration was generic confetti -- eight polychrome
sparks that could have meant anything. The event is "the meter refilled",
so the shape now TELLS that story (2026-08-26, Live Activities grammar:
glanceable meaning): the provider's own color rises LED-by-LED like a
gauge refilling, the strip crests white once, two sparkles wink, then
dark. Three cycles, then the display claim hands back. Indexes past a
2-LED build are parsed and ignored, so the Dot gets a two-LED refill of
the same story for free.
"""

from __future__ import annotations

from .led_status import apply_brightness

#: The neutral refill color when no provider is known (a manual test,
#: a celebration fired before identity resolved).
REFILL_FALLBACK_COLOR = "#FFD700"

#: Three cycles then dark; the display claim window should outlast this.
RESET_CELEBRATION_SECONDS = 6.0


def _refill_cycle(color: str) -> str:
    rise = "; ".join(
        f"{index}:{color} 240ms cosine {index * 90}ms" for index in range(8)
    )
    sparkles = f"2:{color} 220ms pulse 60ms; 5:{color} 220ms pulse 200ms"
    return "\n".join(
        [
            "off 120ms cosine",
            rise,
            "#FFFFFF 260ms pulse",
            sparkles,
            "off 400ms ease-out",
        ]
    )


def reset_celebration_program(
    brightness: float,
    led_count: int = 8,
    *,
    color: str | None = None,
) -> str:
    del led_count  # same program bytes on every build; extras no-op
    cycle = _refill_cycle(color or REFILL_FALLBACK_COLOR)
    return apply_brightness(f"{cycle}\nrepeat 3\noff 250ms", brightness)


__all__ = [
    "REFILL_FALLBACK_COLOR",
    "RESET_CELEBRATION_SECONDS",
    "reset_celebration_program",
]
