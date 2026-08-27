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


def _total_runtime_seconds() -> float:
    """The finite program's full runtime: looped section times its count
    plus the coda after the repeat marker. Measured from the parsed
    program so the claim can never drift from the choreography again
    (2026-08-27 audit: a 2070ms cycle against a hand-kept 6.0s claim
    clipped the third cycle's fade)."""
    from .animation import RepeatStep, parse_animation, step_duration_ms

    animation = parse_animation(reset_celebration_program(1.0), led_count=8)
    repeat_at = next(
        (i for i, step in enumerate(animation.steps) if type(step) is RepeatStep),
        None,
    )
    durations = [step_duration_ms(step) for step in animation.steps]
    if repeat_at is None:
        return sum(durations) / 1000.0
    count = animation.steps[repeat_at].count or 1
    loop = sum(durations[:repeat_at])
    coda = sum(durations[repeat_at + 1 :])
    return (loop * count + coda) / 1000.0


#: Three cycles then dark; the display claim window must outlast the
#: program, so it is measured from it (plus a settle cushion).
try:
    RESET_CELEBRATION_SECONDS = _total_runtime_seconds() + 0.25
except Exception:  # pragma: no cover -- parse of our own constant program
    RESET_CELEBRATION_SECONDS = 6.75


__all__ = [
    "REFILL_FALLBACK_COLOR",
    "RESET_CELEBRATION_SECONDS",
    "reset_celebration_program",
]
