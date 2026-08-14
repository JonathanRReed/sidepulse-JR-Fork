"""Provider colours must stay distinct for a colourblind user, not just on paper.

Hue distance has now failed twice in this repo. It let grok and opencode both
ship systemGray, and when a ninth provider was registered the positional
fallback handed antigravity #FF3B30 -- ten degrees from the Ask/blocked seed,
i.e. a provider that looks permanently blocked.

Hue degrees cannot see either failure, because the thing that actually matters
is whether two colours are *discriminable by the eye reading them*, and roughly
8% of men read them with two cone types rather than three. So this measures
dE2000-style separation in Lab under normal vision, deuteranopia and
protanopia, using a real LMS simulation.

This is deliberately a different metric from colors._hue_gap. If someone
"simplifies" this file back to hue degrees, these tests stop protecting
anything -- which is the failure mode that produced four cannot-fail tests
elsewhere in this project.
"""

from __future__ import annotations

import pytest

from sidepulse.colors import (
    PROVIDER_BRAND_COLORS,
    STATE_SEED_COLORS,
    default_agent_color,
)
from sidepulse.providers import PROVIDER_SPECS

# Below this, two colours read as the same light. ~2.3 is "just noticeable";
# 10 is "clearly a different colour". Providers must clear the latter, with
# margin, in the worst of the three vision models.
MIN_SEPARATION_DE = 12.0

# Pairs that ALREADY collapse in the shipped palette, measured. This is a
# ratchet, not an excuse: nothing new may join, and each entry is a real
# defect a colourblind user is living with today.
#
# The first one is the serious one. For a protanope, codex's identity colour
# and the WORKING state seed are the same light (dE 0.6). A Codex agent does
# not look like "codex working" -- it looks like the working signal itself.
# Fixing it means moving codex off its own brand blue, which is an owner
# decision about brand fidelity versus legibility, so it is recorded here
# rather than changed unilaterally.
KNOWN_COLLAPSES: frozenset[tuple[str, str]] = frozenset(
    {
        ("codex", "Working"),
        ("openclaw", "brand:devin"),
        ("openclaw", "brand:claude"),
        ("claude", "brand:devin"),
        ("devin", "brand:claude"),
    }
)


def _srgb_to_linear(channel: float) -> float:
    channel /= 255.0
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _hex_to_linear(value: str) -> tuple[float, float, float]:
    cleaned = value.lstrip("#")
    return tuple(_srgb_to_linear(int(cleaned[i : i + 2], 16)) for i in (0, 2, 4))


def _to_lms(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    red, green, blue = rgb
    return (
        0.31399 * red + 0.63951 * green + 0.04649 * blue,
        0.15537 * red + 0.75789 * green + 0.08670 * blue,
        0.01776 * red + 0.10945 * green + 0.87247 * blue,
    )


def _from_lms(long_: float, medium: float, short: float) -> tuple[float, float, float]:
    return (
        5.47221 * long_ - 4.6419 * medium + 0.16963 * short,
        -1.1252 * long_ + 2.29317 * medium - 0.1678 * short,
        0.02980 * long_ - 0.19318 * medium + 1.16364 * short,
    )


def simulate(value: str, vision: str) -> tuple[float, float, float]:
    """Viénot-style dichromacy simulation in LMS."""
    long_, medium, short = _to_lms(_hex_to_linear(value))
    if vision == "deuteranopia":
        medium = 0.494207 * long_ + 1.24827 * short
    elif vision == "protanopia":
        long_ = 2.02344 * medium - 2.52581 * short
    return _from_lms(long_, medium, short)


def _lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    red, green, blue = (max(0.0, min(1.0, channel)) for channel in rgb)
    x = 0.4124 * red + 0.3576 * green + 0.1805 * blue
    y = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    z = 0.0193 * red + 0.1192 * green + 0.9505 * blue

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x / 0.95047), f(y / 1.0), f(z / 1.08883)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def separation(left: str, right: str, vision: str) -> float:
    a = _hex_to_linear(left) if vision == "normal" else simulate(left, vision)
    b = _hex_to_linear(right) if vision == "normal" else simulate(right, vision)
    return sum((x - y) ** 2 for x, y in zip(_lab(a), _lab(b))) ** 0.5


VISIONS = ("normal", "deuteranopia", "protanopia")


def _every_reserved_colour() -> dict[str, str]:
    reserved = {name: value for name, value in STATE_SEED_COLORS}
    for provider, value in PROVIDER_BRAND_COLORS.items():
        reserved[f"brand:{provider}"] = value
    return reserved


@pytest.mark.parametrize("spec", PROVIDER_SPECS, ids=lambda s: s.provider)
def test_every_provider_colour_survives_dichromacy(spec) -> None:
    """Including against the STATE seeds.

    A provider colour that collapses onto the Ask/blocked seed for a
    deuteranope means that agent looks blocked whenever it is merely running.
    """
    colour = default_agent_color(spec.provider)
    own_brand = PROVIDER_BRAND_COLORS.get(spec.provider, "").upper()

    for name, other in _every_reserved_colour().items():
        if other.upper() == colour.upper() and other.upper() == own_brand:
            continue  # its own brand entry
        for vision in VISIONS:
            if (spec.provider, name) in KNOWN_COLLAPSES:
                continue
            gap = separation(colour, other, vision)
            assert gap >= MIN_SEPARATION_DE, (
                f"{spec.provider} {colour} is dE {gap:.1f} from {name} {other} "
                f"under {vision} -- indistinguishable light"
            )


def test_no_two_providers_collapse_onto_each_other() -> None:
    assigned = {spec.provider: default_agent_color(spec.provider) for spec in PROVIDER_SPECS}
    providers = sorted(assigned)
    for index, left in enumerate(providers):
        for right in providers[index + 1 :]:
            if (left, f"brand:{right}") in KNOWN_COLLAPSES or (
                right, f"brand:{left}"
            ) in KNOWN_COLLAPSES:
                continue
            for vision in VISIONS:
                gap = separation(assigned[left], assigned[right], vision)
                assert gap >= MIN_SEPARATION_DE, (
                    f"{left} and {right} are dE {gap:.1f} apart under {vision}"
                )


def test_antigravity_specifically_is_not_the_blocked_colour() -> None:
    """The regression that prompted all of this.

    The positional fallback handed antigravity #FF3B30, ten hue-degrees from
    the Ask/blocked seed. A provider wearing the blocked colour is a
    correctness bug, not a cosmetic one.
    """
    colour = default_agent_color("antigravity")
    ask = dict(STATE_SEED_COLORS)["Ask"]
    for vision in VISIONS:
        assert separation(colour, ask, vision) >= MIN_SEPARATION_DE


def test_the_known_collapses_are_still_real_and_still_only_these() -> None:
    """A baseline that silently drifts is worse than no baseline."""
    reserved = _every_reserved_colour()
    still_colliding = set()
    for spec in PROVIDER_SPECS:
        colour = default_agent_color(spec.provider)
        for name, other in reserved.items():
            if other.upper() == colour.upper():
                continue
            if min(separation(colour, other, v) for v in VISIONS) < MIN_SEPARATION_DE:
                still_colliding.add((spec.provider, name))
    assert still_colliding == set(KNOWN_COLLAPSES), (
        f"baseline drifted -- new: {sorted(still_colliding - set(KNOWN_COLLAPSES))}, "
        f"fixed (remove from KNOWN_COLLAPSES): "
        f"{sorted(set(KNOWN_COLLAPSES) - still_colliding)}"
    )


def test_the_real_antigravity_brand_blue_would_have_failed() -> None:
    """Records why the actual brand colour was rejected, so it is not
    'restored' later by someone being helpful."""
    brand_blue = "#3D8AFF"
    codex = PROVIDER_BRAND_COLORS["codex"]
    assert separation(brand_blue, codex, "normal") < MIN_SEPARATION_DE, (
        "if this now passes, Antigravity's real brand colour is usable and "
        "the substitute should be reconsidered"
    )
