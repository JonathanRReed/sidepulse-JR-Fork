"""We must sit above Alcove, and we must know that we do.

The level was a hardcoded near-INT32_MAX guess. It happens to be one
above Alcove's real layer today (measured live 2026-08-14: Alcove at
2147483629/2147483628), but nothing verified it -- so the day Alcove
raised its own level we would sit silently underneath it and the
bracket would simply vanish, with no error anywhere.
"""

from __future__ import annotations

from sidepulse.virtual_device import (
    ABOVE_ALCOVE_WINDOW_LEVEL,
    AlcoveWindowProbe,
    AlcoveWindowSnapshot,
    alcove_window_level,
)


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


def test_window_probe_coalesces_hot_reads_and_invalidates_on_screen_change() -> None:
    """A presentation tick only schedules discovery; it never performs it inline."""
    calls: list[tuple[float, float]] = []
    queued: list[object] = []

    def discover(screen_x: float, screen_width: float) -> AlcoveWindowSnapshot:
        calls.append((screen_x, screen_width))
        return AlcoveWindowSnapshot(
            values=(99, screen_x + 444.0, 0.0, 624.0),
            level=ABOVE_ALCOVE_WINDOW_LEVEL + 1,
        )

    probe = AlcoveWindowProbe(
        probe=discover,
        ttl_seconds=1.0,
        start_refresh=queued.append,
    )

    assert probe.read(0.0, 1512.0, now=0.0) is None
    for _ in range(100):
        assert probe.read(0.0, 1512.0, now=0.5) is None
    assert calls == []
    assert len(queued) == 1

    queued.pop()()
    first = probe.read(0.0, 1512.0, now=0.5)
    assert first is not None and first.values[0] == 99
    assert calls == [(0.0, 1512.0)]

    # A new display key cannot consume geometry discovered for the old one.
    assert probe.read(1512.0, 1920.0, now=0.6) is None
    assert len(queued) == 1
    assert calls == [(0.0, 1512.0)]
    queued.pop()()
    second = probe.read(1512.0, 1920.0, now=0.7)
    assert second is not None and second.values[1] == 1956.0
    assert calls == [(0.0, 1512.0), (1512.0, 1920.0)]


def test_window_probe_refreshes_once_after_ttl_while_serving_cached_geometry() -> None:
    queued: list[object] = []
    calls = 0

    def discover(_screen_x: float, _screen_width: float) -> AlcoveWindowSnapshot:
        nonlocal calls
        calls += 1
        return AlcoveWindowSnapshot(
            values=(calls, 444.0, 0.0, 624.0),
            level=ABOVE_ALCOVE_WINDOW_LEVEL,
        )

    probe = AlcoveWindowProbe(
        probe=discover,
        ttl_seconds=1.0,
        start_refresh=queued.append,
    )
    probe.read(0.0, 1512.0, now=0.0)
    queued.pop()()

    for _ in range(100):
        cached = probe.read(0.0, 1512.0, now=2.0)
        assert cached is not None and cached.values[0] == 1
    assert len(queued) == 1
    assert calls == 1

    queued.pop()()
    refreshed = probe.read(0.0, 1512.0, now=2.1)
    assert refreshed is not None and refreshed.values[0] == 2
    assert calls == 2
