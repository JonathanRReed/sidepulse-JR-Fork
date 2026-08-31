from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sidepulse.announcer_presenter import AnnouncerStackPresentationBridge
from sidepulse.announcer_stack import (
    empty_announcer_stack_state,
    project_announcer_stack,
)


def _hidden_plan():
    return project_announcer_stack(
        empty_announcer_stack_state(),
        None,
        (),
        (),
    )


def test_bridge_validates_and_retains_one_typed_presentation() -> None:
    bridge = AnnouncerStackPresentationBridge(MagicMock)
    handler = MagicMock()
    plan = _hidden_plan()

    bridge.set(plan, handler)

    assert bridge.plan is plan
    with pytest.raises(TypeError, match="invalid announcer stack plan"):
        bridge.set(object(), handler)
    with pytest.raises(TypeError, match="requires an intent handler"):
        bridge.set(plan, None)
    with pytest.raises(TypeError, match="invalid announcer answer plan"):
        bridge.set(plan, handler, answer_plan=object(), answer_handler=handler)


def test_bridge_owns_panel_visibility_and_bounded_close() -> None:
    panel = MagicMock()
    panel_factory = MagicMock(return_value=panel)
    bridge = AnnouncerStackPresentationBridge(panel_factory)
    plan = _hidden_plan()
    handler = MagicMock()
    window = MagicMock()
    window.frame.return_value = MagicMock(
        origin=MagicMock(x=100.0, y=80.0),
        size=MagicMock(width=40.0),
    )
    preferences = object()
    bridge.set(plan, handler)

    assert bridge.present(window, allowed=True, preferences=preferences) is True
    panel_factory.assert_called_once_with()
    panel.update.assert_called_once_with(
        plan,
        handler,
        center_x=120.0,
        top_y=78.0,
        allowed=True,
        preferences=preferences,
        answer_plan=None,
        answer_handler=None,
    )

    bridge.hide()
    panel.hide.assert_called_once_with()
    bridge.close()
    panel.close.assert_called_once_with()
    assert bridge.plan is None


def test_bridge_hides_existing_panel_when_window_is_unavailable() -> None:
    panel = MagicMock()
    bridge = AnnouncerStackPresentationBridge(lambda: panel)
    bridge.set(_hidden_plan(), MagicMock())

    assert bridge.present(None, allowed=False, preferences=object()) is True

    panel.hide.assert_called_once_with()
    panel.update.assert_not_called()


def test_explicit_nonempty_legacy_text_clears_only_hidden_empty_typed_plan() -> None:
    bridge = AnnouncerStackPresentationBridge(MagicMock)
    handler = MagicMock()
    bridge.set(_hidden_plan(), handler)

    assert bridge.clear_hidden_empty_plan_for_legacy_text("Needs you") is True

    assert bridge.plan is None
    assert bridge.intent_handler is None
    assert bridge.answer_plan is None
    assert bridge.answer_handler is None


def test_empty_legacy_text_does_not_clear_hidden_empty_typed_plan() -> None:
    bridge = AnnouncerStackPresentationBridge(MagicMock)
    handler = MagicMock()
    plan = _hidden_plan()
    bridge.set(plan, handler)

    assert bridge.clear_hidden_empty_plan_for_legacy_text("") is False
    assert bridge.plan is plan
    assert bridge.intent_handler is handler
