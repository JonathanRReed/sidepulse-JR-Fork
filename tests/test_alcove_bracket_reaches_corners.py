"""The bracket must touch Alcove's corners, not float inside them.

The stroke is drawn ALCOVE_ACCENT_EDGE_INSET in from each window edge,
but the follow window was sized to the raw measured capsule width -- so
the visible bracket landed that inset inside Alcove's real corner on
each side. Measured on the owner's own screen as a ~6pt gap per side.
"""

from __future__ import annotations

from sidepulse.virtual_device import (
    ALCOVE_ACCENT_EDGE_INSET,
    alcove_accent_horizontal_bounds,
)


def test_stroke_lands_on_the_capsule_edges_after_widening() -> None:
    capsule_width = 260.0
    window_width = capsule_width + 2.0 * ALCOVE_ACCENT_EDGE_INSET
    left, right = alcove_accent_horizontal_bounds(window_width)
    # Measured from the window's left edge, the stroke must start exactly
    # where the capsule starts -- the window is wider by one inset a side.
    assert left == ALCOVE_ACCENT_EDGE_INSET
    assert right - left == capsule_width


def test_raw_capsule_width_would_leave_a_gap() -> None:
    """Guards the regression directly: sizing to the raw width is wrong."""
    capsule_width = 260.0
    left, right = alcove_accent_horizontal_bounds(capsule_width)
    assert right - left == capsule_width - 2.0 * ALCOVE_ACCENT_EDGE_INSET


def test_a_narrow_capsule_never_inverts() -> None:
    left, right = alcove_accent_horizontal_bounds(4.0)
    assert left <= right


def test_unwrapping_keeps_watching_alcove() -> None:
    """Compact is the polite mode, not a teardown.

    Turning wrap off used to stop the Alcove observer outright, so the
    compact accent silently sized itself to the hardware notch and never
    tracked the capsule at all.
    """
    from sidepulse import virtual_device

    device = virtual_device.VirtualStatusDevice.alloc().init()
    closed: list[int] = []
    device._alcove_observer = type(
        "Observer", (), {"close": lambda self, timeout_seconds=None: closed.append(1) or True}
    )()
    device.set_wraps_menu_bar(False)

    assert closed == [], "unwrapping must not tear down Alcove observation"
    assert device._alcove_observer is not None
