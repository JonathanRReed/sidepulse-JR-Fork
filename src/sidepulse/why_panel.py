from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .decision_trace import capacity_detail_text, decision_trace_text
from .why_light_context import format_why_light_context

__all__ = [
    "build_window",
    "panel_body",
    "present_panel",
    "refresh_visible_panel",
    "set_text_preserving_position",
]


@dataclass(frozen=True)
class _ScrollState:
    control: Any
    scroll_view: Any
    clip_view: Any
    x: float
    y: float
    viewport_height: float


def _selector(target: Any, name: str) -> Any | None:
    if target is None:
        return None
    try:
        candidate = getattr(target, name)
    except Exception:
        return None
    return candidate if callable(candidate) else None


def _optional_call(target: Any, name: str) -> Any | None:
    candidate = _selector(target, name)
    if candidate is None:
        return None
    try:
        return candidate()
    except Exception:
        return None


def _pair(value: Any) -> tuple[float, float] | None:
    try:
        return float(value.x), float(value.y)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        return float(value.width), float(value.height)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        first, second = value
        return float(first), float(second)
    except (TypeError, ValueError):
        return None


def _rect_parts(rect: Any) -> tuple[float, float, float, float] | None:
    try:
        origin = rect.origin
        size = rect.size
    except AttributeError:
        try:
            origin, size = rect
        except (TypeError, ValueError):
            return None
    origin_pair = _pair(origin)
    size_pair = _pair(size)
    if origin_pair is None or size_pair is None:
        return None
    return (*origin_pair, *size_pair)


def _selection(control: Any) -> tuple[int, int] | None:
    selected_range = _optional_call(control, "selectedRange")
    if selected_range is None:
        return None
    try:
        location = selected_range.location
        length = selected_range.length
    except AttributeError:
        try:
            location, length = selected_range
        except (TypeError, ValueError):
            return None
    try:
        return int(location), int(length)
    except (TypeError, ValueError, OverflowError):
        return None


def _scroll_state(control: Any) -> _ScrollState | None:
    scroll_view = _optional_call(control, "enclosingScrollView")
    clip_view = _optional_call(scroll_view, "contentView")
    bounds = _rect_parts(_optional_call(clip_view, "bounds"))
    if bounds is None:
        return None
    x, y, _width, height = bounds
    return _ScrollState(control, scroll_view, clip_view, x, y, max(0.0, height))


def _document_vertical_bounds(state: _ScrollState) -> tuple[float, float] | None:
    document = _optional_call(state.scroll_view, "documentView") or state.control
    bounds = _rect_parts(_optional_call(document, "bounds"))
    if bounds is None:
        bounds = _rect_parts(_optional_call(document, "frame"))
    if bounds is None:
        return None
    _x, y, _width, height = bounds
    minimum = y
    maximum = max(minimum, y + max(0.0, height) - state.viewport_height)
    return minimum, maximum


def _restore_scroll(state: _ScrollState) -> None:
    vertical_bounds = _document_vertical_bounds(state)
    y = state.y
    if vertical_bounds is not None:
        minimum, maximum = vertical_bounds
        y = min(maximum, max(minimum, y))

    scroll_to_point = _selector(state.clip_view, "scrollToPoint_")
    if scroll_to_point is None:
        scroll_to_point = _selector(state.control, "scrollPoint_")
    if scroll_to_point is None:
        return
    try:
        scroll_to_point((state.x, y))
    except Exception:
        return

    reflect = _selector(state.scroll_view, "reflectScrolledClipView_")
    if reflect is not None:
        try:
            reflect(state.clip_view)
        except Exception:
            pass


def _replace_text(control: Any, value: str) -> bool:
    for name in ("setString_", "setStringValue_"):
        setter = _selector(control, name)
        if setter is not None:
            setter(value)
            return True
    return False


def set_text_preserving_position(control: Any, value: str) -> None:
    """Replace a text control's value without moving an active panel reader."""

    if control is None:
        return

    selection = _selection(control)
    scroll_state = _scroll_state(control)
    if not _replace_text(control, value):
        return

    set_selected_range = _selector(control, "setSelectedRange_")
    if selection is not None and set_selected_range is not None:
        location, length = selection
        location = min(len(value), max(0, location))
        length = min(len(value) - location, max(0, length))
        try:
            set_selected_range((location, length))
        except Exception:
            pass

    if scroll_state is not None:
        _restore_scroll(scroll_state)


def panel_body(controller: object, *, why_context: object | None = None) -> str:
    """Build the complete current-light explanation from cached facts."""
    context = (
        controller.current_why_light_context()
        if why_context is None
        else why_context
    )
    parts = [
        decision_trace_text(controller.current_decision_trace()),
        format_why_light_context(context),
    ]
    try:
        capacity = capacity_detail_text(controller.capacity_detail_models(now=time.time()))
    except (TypeError, ValueError):
        capacity = ""
    if capacity:
        parts.append(capacity)
    return "\n\n".join(parts)


def refresh_visible_panel(controller: object, body: str) -> bool:
    """Refresh a visible panel while retaining its reading position."""
    window = getattr(controller, "why_panel_window", None)
    if window is None or not window.isVisible():
        return False
    set_text_preserving_position(controller.why_panel_text_view, body)
    return True


def present_panel(
    controller: object,
    body: str,
    *,
    window_builder: Callable[[object], object],
    presenter: Callable[[object], None],
    activator: Callable[[], None],
) -> None:
    """Present and focus AppKit through callables supplied by the controller."""
    if getattr(controller, "why_panel_window", None) is None:
        controller.why_panel_window = window_builder(controller)
    set_text_preserving_position(controller.why_panel_text_view, body)
    presenter(controller.why_panel_window)
    make_first_responder = getattr(
        controller.why_panel_window,
        "makeFirstResponder_",
        None,
    )
    if callable(make_first_responder):
        try:
            make_first_responder(controller.why_panel_text_view)
        except Exception:
            pass
    activator()


def build_window(
    target: object,
    *,
    window_class: Any,
    view_class: Any,
    style_mask: int,
    backing_store: int,
    title: str,
    text_view_builder: Callable[..., object],
) -> object:
    """Build the selectable AppKit explanation window from injected types."""
    width, height = 620, 660
    window = window_class.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (width, height)),
        style_mask,
        backing_store,
        False,
    )
    window.setTitle_(title)
    window.setReleasedWhenClosed_(False)
    window.center()
    root = view_class.alloc().initWithFrame_(((0, 0), (width, height)))
    window.setContentView_(root)
    margin = 16
    text_view = text_view_builder(
        root,
        "",
        margin,
        margin,
        width - 2 * margin,
        height - 2 * margin,
    )
    text_view.setEditable_(False)
    text_view.setSelectable_(True)
    text_view.setAccessibilityLabel_("Why this light explanation")
    text_view.setAccessibilityHelp_(
        "Selectable current-light explanation. Use Command-A and Command-C to copy."
    )
    window.setInitialFirstResponder_(text_view)
    target.why_panel_text_view = text_view
    return window
