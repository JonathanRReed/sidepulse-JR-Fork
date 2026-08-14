"""We must sit above Alcove, and we must know that we do.

The level was a hardcoded near-INT32_MAX guess. It happens to be one
above Alcove's real layer today (measured live 2026-08-14: Alcove at
2147483629/2147483628), but nothing verified it -- so the day Alcove
raised its own level we would sit silently underneath it and the
bracket would simply vanish, with no error anywhere.
"""

from __future__ import annotations

from sidepulse.virtual_device import ABOVE_ALCOVE_WINDOW_LEVEL, alcove_window_level


def _windows(*levels: int):
    return [
        {"kCGWindowOwnerName": "Alcove", "kCGWindowLayer": level} for level in levels
    ] + [{"kCGWindowOwnerName": "Finder", "kCGWindowLayer": 0}]


def test_sits_one_above_alcoves_highest_window() -> None:
    level = alcove_window_level(window_lister=lambda: _windows(2147483628, 2147483629))
    assert level == 2147483630


def test_follows_alcove_upward_if_it_ever_raises_its_level() -> None:
    """The whole point: a future Alcove must not be able to hide us."""
    level = alcove_window_level(window_lister=lambda: _windows(2147483630))
    assert level > 2147483630


def test_never_drops_below_the_previous_behavior() -> None:
    assert alcove_window_level(window_lister=lambda: _windows(5)) == (
        ABOVE_ALCOVE_WINDOW_LEVEL
    )


def test_falls_back_when_alcove_is_absent_or_probing_fails() -> None:
    assert alcove_window_level(window_lister=list) == ABOVE_ALCOVE_WINDOW_LEVEL

    def _broken():
        raise RuntimeError("no window server")

    assert alcove_window_level(window_lister=_broken) == ABOVE_ALCOVE_WINDOW_LEVEL
