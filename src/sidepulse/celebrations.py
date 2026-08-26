"""Finite celebration programs -- the fun the lights owe good news.

A rate-limit reset is the best news this app ever carries: the meter
just refilled and the expensive agents are cheap again. It deserved a
CodexBar-style confetti moment; instead it set a blink timer whose only
renderer sat behind a flag hard-wired to False, so every reset in the
product's history has passed in silence (feature audit, 2026-08-26).

These builders emit plain DSL. Every program is FINITE (a bounded
`repeat N` then off) so the firmware finishes the party on its own even
if the app dies mid-celebration, and everything stays inside the safety
compiler's 2Hz / 3-flashes budgets -- verified by test against the real
compiler and firmware grammar.
"""

from __future__ import annotations

from .led_status import apply_brightness

#: One confetti cycle: eight staggered polychrome sparks, a soft white
#: crest, then dark. Indexes past a 2-LED build are parsed and ignored,
#: so the Dot gets a two-spark version of the same party for free.
_CONFETTI_CYCLE = (
    "off 120ms cosine\n"
    "0:#FFD700 300ms pulse 0ms; 3:#00E5FF 300ms pulse 90ms; "
    "6:#FF2BA6 300ms pulse 180ms; 1:#7CFF4F 300ms pulse 270ms; "
    "5:#FFB000 300ms pulse 360ms; 2:#FF8800 300ms pulse 450ms; "
    "7:#8A36FF 300ms pulse 540ms; 4:#00FF88 300ms pulse 630ms\n"
    "#FFFFFF 260ms pulse\n"
    "off 400ms ease-out"
)

#: Three cycles ≈ 5.4s of confetti, then the strip goes dark and hands
#: the claim back. The display claim window should outlast this.
RESET_CELEBRATION_SECONDS = 6.0


def reset_celebration_program(brightness: float, led_count: int = 8) -> str:
    del led_count  # same program bytes on every build; extras no-op
    return apply_brightness(f"{_CONFETTI_CYCLE}\nrepeat 3\noff 250ms", brightness)


__all__ = ["RESET_CELEBRATION_SECONDS", "reset_celebration_program"]
