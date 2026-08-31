from __future__ import annotations

from dataclasses import dataclass

import pytest

from sidepulse.why_panel import set_text_preserving_position


@dataclass
class _Point:
    x: float
    y: float


@dataclass
class _Size:
    width: float
    height: float


@dataclass
class _Rect:
    origin: _Point
    size: _Size


class _ClipView:
    def __init__(self, *, y: float, height: float) -> None:
        self._origin = _Point(0.0, y)
        self._size = _Size(320.0, height)

    def bounds(self) -> _Rect:
        return _Rect(self._origin, self._size)

    def scrollToPoint_(self, point) -> None:
        if hasattr(point, "x") and hasattr(point, "y"):
            x, y = point.x, point.y
        else:
            x, y = point
        self._origin = _Point(float(x), float(y))


class _ScrollView:
    def __init__(self, document, *, y: float, height: float) -> None:
        self._document = document
        self.clip_view = _ClipView(y=y, height=height)
        self.reflected_clip_views: list[_ClipView] = []

    def contentView(self) -> _ClipView:
        return self.clip_view

    def documentView(self):
        return self._document

    def reflectScrolledClipView_(self, clip_view: _ClipView) -> None:
        self.reflected_clip_views.append(clip_view)


class _TextView:
    def __init__(
        self,
        text: str,
        *,
        selection: tuple[int, int],
        scroll_y: float,
        viewport_height: float,
    ) -> None:
        self.text = text
        self.selection = selection
        self.editable = False
        self.selectable = True
        self.scroll_view = _ScrollView(
            self,
            y=scroll_y,
            height=viewport_height,
        )

    def string(self) -> str:
        return self.text

    def setString_(self, text: str) -> None:
        self.text = text
        self.selection = (len(text), 0)
        self.scroll_view.clip_view.scrollToPoint_((0.0, 0.0))

    def selectedRange(self) -> tuple[int, int]:
        return self.selection

    def setSelectedRange_(self, selection: tuple[int, int]) -> None:
        self.selection = tuple(selection)

    def enclosingScrollView(self) -> _ScrollView:
        return self.scroll_view

    def bounds(self) -> _Rect:
        line_count = max(1, self.text.count("\n") + 1)
        return _Rect(_Point(0.0, 0.0), _Size(320.0, line_count * 10.0))


class _SelectionOnlyTextView:
    def __init__(self, text: str, selection: tuple[int, int]) -> None:
        self.text = text
        self.selection = selection

    def setString_(self, text: str) -> None:
        self.text = text
        self.selection = (len(text), 0)

    def selectedRange(self) -> tuple[int, int]:
        return self.selection

    def setSelectedRange_(self, selection: tuple[int, int]) -> None:
        self.selection = tuple(selection)


class _StringTextDouble:
    def __init__(self) -> None:
        self.text = "old"

    def setString_(self, text: str) -> None:
        self.text = text


class _StringValueTextDouble:
    def __init__(self) -> None:
        self.text = "old"

    def setStringValue_(self, text: str) -> None:
        self.text = text


def test_refresh_preserves_selection_scroll_and_reader_configuration_for_longer_text() -> None:
    old_text = "\n".join(f"old line {index}" for index in range(12))
    new_text = "\n".join(f"new line {index}" for index in range(24))
    control = _TextView(
        old_text,
        selection=(8, 12),
        scroll_y=50.0,
        viewport_height=40.0,
    )

    set_text_preserving_position(control, new_text)

    assert control.string() == new_text
    assert control.selectedRange() == (8, 12)
    assert control.scroll_view.contentView().bounds().origin.y == 50.0
    assert control.selectable is True
    assert control.editable is False


def test_refresh_clamps_selection_and_vertical_scroll_for_shorter_text() -> None:
    old_text = "\n".join(f"old line {index}" for index in range(20))
    control = _TextView(
        old_text,
        selection=(40, 8),
        scroll_y=120.0,
        viewport_height=40.0,
    )

    set_text_preserving_position(control, "short")

    assert control.string() == "short"
    assert control.selectedRange() == (5, 0)
    assert control.scroll_view.contentView().bounds().origin.y == 0.0


def test_refresh_clamps_selection_length_when_start_remains_valid() -> None:
    control = _SelectionOnlyTextView("a longer old value", (2, 12))

    set_text_preserving_position(control, "short")

    assert control.text == "short"
    assert control.selectedRange() == (2, 3)


def test_refresh_without_scroll_container_still_updates_and_restores_selection() -> None:
    control = _SelectionOnlyTextView("old value", (1, 3))

    set_text_preserving_position(control, "new longer value")

    assert control.text == "new longer value"
    assert control.selectedRange() == (1, 3)


@pytest.mark.parametrize("control_type", [_StringTextDouble, _StringValueTextDouble])
def test_refresh_supports_simple_text_control_doubles(control_type) -> None:
    control = control_type()

    set_text_preserving_position(control, "new")

    assert control.text == "new"


def test_refresh_with_none_control_is_a_noop() -> None:
    set_text_preserving_position(None, "new")
