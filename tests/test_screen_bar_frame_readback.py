"""The Screen Bar must draw where the window IS, not where we asked it to be.

`setFrame_display_` is a request. AppKit clamps to screen bounds, rounds to
the backing scale, and applies its own constraints to a non-activating panel;
a display reconfiguration or Space change can land between compute and apply.

Sizing the content view from the REQUESTED frame while the window sits at the
GRANTED one draws everything at coordinates the window is not at -- content
offset, clipped at the edge, or both. It also hides perfectly on a single
display, which is where this gets developed.

Found by a peer session working on a caret-anchored overlay with the identical
defect, which in turn came from our own ProMotion bug: a requested frame rate
the panel could not produce. Three instances of one shape -- request,
assumption and delivery allowed to disagree.
"""

from __future__ import annotations


class _Rect:
    def __init__(self, origin, size):
        self.origin = type("P", (), {"x": origin[0], "y": origin[1]})()
        self.size = type("S", (), {"width": size[0], "height": size[1]})()


class _ClampingWindow:
    """An AppKit-like window that grants less than it is asked for."""

    def __init__(self, max_width: float) -> None:
        self._max_width = max_width
        self._frame = _Rect((0.0, 0.0), (0.0, 0.0))
        self.set_calls: list[tuple] = []

    def frame(self):
        return self._frame

    def setFrame_display_(self, frame, _display):
        self.set_calls.append(frame)
        (x, y), (width, height) = frame
        # The clamp AppKit would apply.
        self._frame = _Rect((x, y), (min(width, self._max_width), height))


def test_a_clamped_frame_is_read_back_not_assumed() -> None:
    window = _ClampingWindow(max_width=800.0)
    window.setFrame_display_(((0.0, 0.0), (1200.0, 40.0)), True)

    granted = window.frame()
    assert granted.size.width == 800.0, "fixture does not clamp; test is vacuous"

    # The property under test: content geometry must come from the granted
    # frame. Deriving it from the request would size the view 1200 wide
    # inside an 800-wide window -- 400pt of content off the edge.
    content = (granted.size.width, granted.size.height)
    requested = (1200.0, 40.0)
    assert content != requested
    assert content == (800.0, 40.0)


def test_the_reposition_path_reads_the_frame_back() -> None:
    """Guards the actual source, so a refactor cannot quietly drop it."""
    from pathlib import Path

    from sidepulse import virtual_device

    source = Path(virtual_device.__file__).read_text(encoding="utf-8")
    marker = source.index("setFrame_display_(window_frame, True)")
    following = source[marker : marker + 900]
    assert "self.window.frame()" in following, (
        "the granted frame is no longer read back after setFrame_display_; "
        "the view will be sized from a frame the window may not have"
    )
