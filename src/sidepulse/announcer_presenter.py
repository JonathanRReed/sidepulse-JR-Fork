"""Typed ownership of Screen Bar announcer panel presentation state."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .announcer_stack import AnnouncerStackPlan, AnnouncerStackVisibility
from .answer_in_place import AnswerControlPlan


class _Panel(Protocol):
    def update(self, *args, **kwargs) -> None: ...

    def hide(self) -> None: ...

    def close(self) -> None: ...


class AnnouncerStackPresentationBridge:
    """Validate one presentation and own its native panel lifecycle."""

    def __init__(self, panel_factory: Callable[[], _Panel]) -> None:
        if not callable(panel_factory):
            raise ValueError("invalid announcer panel factory")
        self._panel_factory = panel_factory
        self._panel: _Panel | None = None
        self.plan: AnnouncerStackPlan | None = None
        self.intent_handler = None
        self.answer_plan: AnswerControlPlan | None = None
        self.answer_handler = None

    def set(
        self,
        plan: AnnouncerStackPlan | None,
        intent_handler,
        *,
        answer_plan: AnswerControlPlan | None = None,
        answer_handler=None,
    ) -> None:
        if plan is not None and type(plan) is not AnnouncerStackPlan:
            raise TypeError("invalid announcer stack plan")
        if plan is not None and not callable(intent_handler):
            raise TypeError("announcer stack requires an intent handler")
        if answer_plan is not None and type(answer_plan) is not AnswerControlPlan:
            raise TypeError("invalid announcer answer plan")
        if answer_plan is not None and not callable(answer_handler):
            raise TypeError("announcer answer plan requires a handler")
        self.plan = plan
        self.intent_handler = intent_handler if plan is not None else None
        self.answer_plan = answer_plan if plan is not None else None
        self.answer_handler = (
            answer_handler if plan is not None and answer_plan is not None else None
        )

    def _is_hidden_empty_plan(self) -> bool:
        plan = self.plan
        return bool(
            type(plan) is AnnouncerStackPlan
            and plan.visibility is AnnouncerStackVisibility.HIDDEN
            and plan.total_actionable_count == 0
        )

    def clear_hidden_empty_plan_for_legacy_text(self, text: str | None) -> bool:
        if not text or not self._is_hidden_empty_plan():
            return False
        self.plan = None
        self.intent_handler = None
        self.answer_plan = None
        self.answer_handler = None
        return True

    def present(self, window, *, allowed: bool, preferences: object) -> bool:
        plan = self.plan
        if plan is None:
            return False
        panel = self._panel
        if panel is None:
            panel = self._panel = self._panel_factory()
        if window is None:
            panel.hide()
            return True
        frame = window.frame()
        panel.update(
            plan,
            self.intent_handler,
            center_x=frame.origin.x + frame.size.width / 2.0,
            top_y=frame.origin.y - 2.0,
            allowed=allowed,
            preferences=preferences,
            answer_plan=self.answer_plan,
            answer_handler=self.answer_handler,
        )
        return True

    def hide(self) -> None:
        if self._panel is not None:
            self._panel.hide()

    def close(self) -> None:
        panel = self._panel
        if panel is not None:
            try:
                panel.close()
            except Exception:
                pass
        self._panel = None
        self.plan = None
        self.intent_handler = None
        self.answer_plan = None
        self.answer_handler = None


__all__ = ["AnnouncerStackPresentationBridge"]
