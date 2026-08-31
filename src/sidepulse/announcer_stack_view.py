"""Native, passive-to-keyable presentation for immutable announcer plans."""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEventModifierFlagShift,
    NSFocusRingOnly,
    NSFont,
    NSGraphicsContext,
    NSImage,
    NSImageOnly,
    NSLineBreakByTruncatingTail,
    NSPanel,
    NSSetFocusRingStyle,
    NSTextField,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSObject

from .accessibility_display import AccessibilityDisplayPreferences
from .announcer_stack import (
    AnnouncerAlertIdentity,
    AnnouncerStackAction,
    AnnouncerStackIntent,
    AnnouncerStackPlan,
    AnnouncerStackVisibility,
)
from .answer_in_place import AnswerActionKind, AnswerControlPlan
from .window_presentation import desktop_takeover_suppressed, present_window

_PILL_HEIGHT = 22.0
_PANEL_WIDTH = 360.0
_PANEL_HEIGHT = 176.0
_PANEL_MIN_WIDTH = 320.0
_PANEL_MAX_WIDTH = 460.0
_PADDING = 12.0
_ANSWER_CONTROL_GAP = 8.0
_ANSWER_BUTTON_WIDTH = 72.0
_ANSWER_ROW_Y = 60.0
_ANSWER_ROW_HEIGHT = 28.0
_STATUS_WINDOW_LEVEL = 25
_SURFACE_RADIUS = 10.0
_PRIMARY_FOREGROUND = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.94, 0.94, 0.97, 1.0)
_SECONDARY_FOREGROUND = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.72, 0.72, 0.78, 1.0)


class _AnnouncerWindow(NSPanel):
    def canBecomeMainWindow(self) -> bool:
        return False

    def canBecomeKeyWindow(self) -> bool:
        return bool(
            getattr(self, "announcer_expanded", False)
            and getattr(self, "announcer_pointer_authorized", False)
        )


class _CollapsedAnnouncerView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_CollapsedAnnouncerView, self).initWithFrame_(frame)
        if self is not None:
            self.click_handler = None
            self.surface_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.025, 0.025, 0.035, 1.0
            )
            self.border_color = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.22)
            self.setAccessibilityElement_(True)
            self.setAccessibilityRole_("AXButton")
            self.setAccessibilityLabel_("Screen Bar announcer")
        return self

    def acceptsFirstResponder(self) -> bool:
        return False

    def mouseDown_(self, _event) -> None:
        handler = self.click_handler
        if callable(handler):
            handler()

    def drawRect_(self, _rect) -> None:
        bounds = self.bounds()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((0.5, 0.5), (bounds.size.width - 1.0, bounds.size.height - 1.0)),
            bounds.size.height / 2.0,
            bounds.size.height / 2.0,
        )
        self.surface_color.set()
        path.fill()
        self.border_color.set()
        path.setLineWidth_(1.0)
        path.stroke()


class _ExpandedAnnouncerView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_ExpandedAnnouncerView, self).initWithFrame_(frame)
        if self is not None:
            self.key_handler = None
            self.tab_handler = None
            self.surface_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.025, 0.025, 0.035, 1.0
            )
            self.border_color = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.22)
            self.setAccessibilityElement_(True)
            self.setAccessibilityRole_("AXGroup")
        return self

    def acceptsFirstResponder(self) -> bool:
        return True

    def drawRect_(self, _rect) -> None:
        bounds = self.bounds()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((0.5, 0.5), (bounds.size.width - 1.0, bounds.size.height - 1.0)),
            _SURFACE_RADIUS,
            _SURFACE_RADIUS,
        )
        self.surface_color.set()
        path.fill()
        self.border_color.set()
        path.setLineWidth_(1.0)
        path.stroke()

    def keyDown_(self, event) -> None:
        action = None
        key_code = event.keyCode()
        if key_code == 48:
            handler = self.tab_handler
            if callable(handler):
                handler(bool(event.modifierFlags() & NSEventModifierFlagShift))
                return
        elif key_code in (123, 126):
            action = AnnouncerStackAction.PREVIOUS
        elif key_code in (124, 125):
            action = AnnouncerStackAction.NEXT
        elif key_code == 36:
            action = AnnouncerStackAction.OPEN
        elif key_code == 49:
            action = AnnouncerStackAction.MARK_SEEN
        elif key_code == 53:
            action = AnnouncerStackAction.COLLAPSE
        else:
            text = str(event.charactersIgnoringModifiers() or "").lower()
            if text == "d":
                action = AnnouncerStackAction.MARK_SEEN
            elif text in ("\r", "\n"):
                action = AnnouncerStackAction.OPEN
            elif text == "\x1b":
                action = AnnouncerStackAction.COLLAPSE
        handler = self.key_handler
        if action is not None and callable(handler):
            handler(action)
            return
        objc.super(_ExpandedAnnouncerView, self).keyDown_(event)


class _AnnouncerButton(NSButton):
    """Native button with an explicit Tab route and visible responder ring."""

    def initWithFrame_(self, frame):
        self = objc.super(_AnnouncerButton, self).initWithFrame_(frame)
        if self is not None:
            self.tab_handler = None
        return self

    def keyDown_(self, event) -> None:
        if event.keyCode() == 48:
            handler = self.tab_handler
            if callable(handler):
                handler(bool(event.modifierFlags() & NSEventModifierFlagShift))
                return
        objc.super(_AnnouncerButton, self).keyDown_(event)

    def drawRect_(self, rect) -> None:
        objc.super(_AnnouncerButton, self).drawRect_(rect)
        window = self.window()
        if window is None or window.firstResponder() is not self:
            return
        NSGraphicsContext.saveGraphicsState()
        try:
            NSSetFocusRingStyle(NSFocusRingOnly)
            bounds = self.bounds()
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((2.5, 2.5), (bounds.size.width - 5.0, bounds.size.height - 5.0)),
                5.0,
                5.0,
            )
            NSColor.clearColor().set()
            path.fill()
        finally:
            NSGraphicsContext.restoreGraphicsState()


class _AnnouncerControlTarget(NSObject):
    def initWithPanel_(self, panel):
        self = objc.super(_AnnouncerControlTarget, self).init()
        if self is not None:
            self.panel = panel
        return self

    @objc.python_method
    def _emit(self, action: AnnouncerStackAction, sender) -> None:
        panel = getattr(self, "panel", None)
        if panel is not None:
            panel._emit_from_control(action, sender)

    @objc.IBAction
    def previous_(self, sender) -> None:
        self._emit(AnnouncerStackAction.PREVIOUS, sender)

    @objc.IBAction
    def next_(self, sender) -> None:
        self._emit(AnnouncerStackAction.NEXT, sender)

    @objc.IBAction
    def open_(self, sender) -> None:
        self._emit(AnnouncerStackAction.OPEN, sender)

    @objc.IBAction
    def markSeen_(self, sender) -> None:
        self._emit(AnnouncerStackAction.MARK_SEEN, sender)

    @objc.IBAction
    def collapse_(self, sender) -> None:
        self._emit(AnnouncerStackAction.COLLAPSE, sender)

    @objc.python_method
    def _emit_answer(self, action: AnswerActionKind, sender) -> None:
        panel = getattr(self, "panel", None)
        if panel is not None:
            panel._emit_answer_from_control(action, sender)

    @objc.IBAction
    def approve_(self, sender) -> None:
        self._emit_answer(AnswerActionKind.APPROVE, sender)

    @objc.IBAction
    def deny_(self, sender) -> None:
        self._emit_answer(AnswerActionKind.DENY, sender)

    @objc.IBAction
    def reply_(self, sender) -> None:
        self._emit_answer(AnswerActionKind.REPLY, sender)

    @objc.IBAction
    def cancel_(self, sender) -> None:
        self._emit_answer(AnswerActionKind.CANCEL, sender)

    @objc.IBAction
    def retry_(self, sender) -> None:
        self._emit_answer(AnswerActionKind.RETRY, sender)

    @objc.IBAction
    def jump_(self, sender) -> None:
        self._emit_answer(AnswerActionKind.JUMP, sender)


class AnnouncerStackPanel:
    """A single native nonactivating panel owned by ``VirtualStatusDevice``."""

    def __init__(self) -> None:
        self.window: _AnnouncerWindow | None = None
        self.root_view = None
        self.expanded_view = None
        self.buttons: tuple[NSButton, ...] = ()
        self.answer_buttons: tuple[NSButton, ...] = ()
        self.visibility = AnnouncerStackVisibility.HIDDEN
        self._plan: AnnouncerStackPlan | None = None
        self._answer_plan: AnswerControlPlan | None = None
        self._intent_handler: Callable[[AnnouncerStackIntent], None] | None = None
        self._answer_handler: Callable[
            [AnswerActionKind, int, AnnouncerAlertIdentity, str | None], None
        ] | None = None
        self._control_target = _AnnouncerControlTarget.alloc().initWithPanel_(self)
        self._pointer_authorized = False
        self._presentation_allowed = False
        self.source_field = None
        self.session_field = None
        self.question_field = None
        self.detail_field = None
        self.answer_reply_field = None
        self.answer_status_field = None

    def _ensure_window(self) -> _AnnouncerWindow:
        if self.window is not None:
            return self.window
        window = _AnnouncerWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0.0, 0.0), (_PANEL_WIDTH, _PILL_HEIGHT)),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        window.announcer_expanded = False
        window.announcer_pointer_authorized = False
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        window.setHasShadow_(False)
        window.setLevel_(_STATUS_WINDOW_LEVEL)
        window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self.window = window
        return window

    @staticmethod
    def _selected_identity(plan: AnnouncerStackPlan):
        if plan.selected_index is None:
            return None
        return plan.alerts[plan.selected_index].identity

    def _emit(
        self,
        action: AnnouncerStackAction,
        generation: int | None = None,
        selected_identity=None,
    ) -> None:
        plan = self._plan
        handler = self._intent_handler
        window = self.window
        if not (
            self._presentation_allowed
            and self.visibility is not AnnouncerStackVisibility.HIDDEN
            and window is not None
            and plan is not None
            and callable(handler)
        ):
            return
        current_identity = self._selected_identity(plan)
        if generation is not None and (
            generation != plan.generation or selected_identity != current_identity
        ):
            return
        handler(AnnouncerStackIntent(action, plan.generation, current_identity))

    def _collapsed_click(self, generation: int, selected_identity) -> None:
        plan = self._plan
        if not (
            self._presentation_allowed
            and self.visibility is AnnouncerStackVisibility.COLLAPSED
            and plan is not None
            and generation == plan.generation
            and selected_identity == self._selected_identity(plan)
        ):
            return
        action = (
            AnnouncerStackAction.OPEN
            if plan.total_actionable_count == 1
            else AnnouncerStackAction.EXPAND
        )
        if action is AnnouncerStackAction.EXPAND:
            self._pointer_authorized = True
        self._emit(action, generation, selected_identity)

    @staticmethod
    def _label(text: str, frame, *, secondary: bool = False, wrap: bool = False):
        field = NSTextField.alloc().initWithFrame_(frame)
        field.setStringValue_(text)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setBordered_(False)
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setFont_(NSFont.systemFontOfSize_(11.0 if secondary else 12.0))
        field.setTextColor_(_SECONDARY_FOREGROUND if secondary else _PRIMARY_FOREGROUND)
        field.setLineBreakMode_(NSLineBreakByTruncatingTail)
        field.setMaximumNumberOfLines_(2 if wrap else 1)
        if wrap:
            field.setUsesSingleLineMode_(False)
            field.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
            field.cell().setWraps_(True)
            field.cell().setTruncatesLastVisibleLine_(True)
        return field

    def _button(
        self,
        title: str,
        label: str,
        selector: str,
        frame,
        enabled: bool,
        *,
        symbol: str | None = None,
    ) -> NSButton:
        button = _AnnouncerButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        if symbol is not None:
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, label)
            if image is not None:
                button.setImage_(image)
                button.setImagePosition_(NSImageOnly)
        button.setTarget_(self._control_target)
        button.setAction_(selector)
        button.setEnabled_(enabled)
        button.setRefusesFirstResponder_(False)
        button.setAccessibilityLabel_(label)
        button.setAccessibilityRole_("AXButton")
        button.tab_handler = lambda reverse, sender=button: self._focus_relative_to(
            sender,
            reverse,
        )
        return button

    def _focus_relative_to(self, sender, reverse: bool) -> None:
        window = self.window
        focus_views = self._focus_views()
        if window is None or not focus_views:
            return
        if sender in focus_views:
            index = focus_views.index(sender)
            target_index = (index - 1 if reverse else index + 1) % len(focus_views)
        else:
            target_index = len(focus_views) - 1 if reverse else 0
        previous = window.firstResponder()
        target = focus_views[target_index]
        if window.makeFirstResponder_(target):
            if previous is not None and hasattr(previous, "setNeedsDisplay_"):
                previous.setNeedsDisplay_(True)
            target.setNeedsDisplay_(True)

    def _focus_views(self) -> tuple[object, ...]:
        focusable: list[object] = []
        if self.answer_reply_field is not None and not self.answer_reply_field.isHidden():
            focusable.append(self.answer_reply_field)
        focusable.extend(
            button
            for button in self.answer_buttons
            if not button.isHidden() and button.isEnabled()
        )
        focusable.extend(button for button in self.buttons if button.isEnabled())
        return tuple(focusable)

    def _configure_key_view_loop(self, root: NSView) -> None:
        focus_views = self._focus_views()
        if not focus_views:
            return
        root.setNextKeyView_(focus_views[0])
        for current, following in pairwise(focus_views):
            current.setNextKeyView_(following)
        focus_views[-1].setNextKeyView_(focus_views[0])

    def _build_collapsed(self, plan: AnnouncerStackPlan, frame) -> NSView:
        root = _CollapsedAnnouncerView.alloc().initWithFrame_(((0.0, 0.0), frame[1]))
        root.click_handler = lambda: self._collapsed_click(
            plan.generation,
            self._selected_identity(plan),
        )
        root.setAccessibilityValue_(plan.accessibility_value)
        root.setAccessibilityHelp_(plan.accessibility_help)
        label = self._label(plan.collapsed_text or "", ((12.0, 3.0), (frame[1][0] - 24.0, 16.0)))
        root.addSubview_(label)
        return root

    def _build_expanded(self, frame) -> NSView:
        root = _ExpandedAnnouncerView.alloc().initWithFrame_(((0.0, 0.0), frame[1]))
        root.tab_handler = lambda reverse: self._focus_relative_to(root, reverse)
        width = frame[1][0]
        self.source_field = self._label("", ((_PADDING, 152.0), (width - 24.0, 16.0)))
        self.session_field = self._label("", ((_PADDING, 136.0), (width - 24.0, 16.0)), secondary=True)
        self.question_field = self._label("", ((_PADDING, 96.0), (width - 24.0, 32.0)), wrap=True)
        self.answer_reply_field = NSTextField.alloc().initWithFrame_(
            ((_PADDING, 64.0), (216.0, 24.0))
        )
        self.answer_reply_field.setEditable_(True)
        self.answer_reply_field.setSelectable_(True)
        self.answer_reply_field.setBordered_(True)
        self.answer_reply_field.setBezeled_(True)
        self.answer_reply_field.setDrawsBackground_(True)
        self.answer_reply_field.setFont_(NSFont.systemFontOfSize_(12.0))
        self.answer_reply_field.setHidden_(True)
        self.answer_status_field = self._label(
            "",
            ((_PADDING, 68.0), (width - 24.0, 16.0)),
            secondary=True,
        )
        self.answer_status_field.setHidden_(True)
        answer_controls = (
            ("Approve", "Approve request", "approve:"),
            ("Deny", "Deny request", "deny:"),
            ("Jump", "Open asking session", "jump:"),
        )
        answer_buttons = []
        x = width - 24.0 - 88.0
        for title, label, selector in reversed(answer_controls):
            button = self._button(
                title,
                label,
                selector,
                ((x, 60.0), (88.0, 28.0)),
                False,
            )
            button.setHidden_(True)
            button.setRepresentedObject_((0, None, None))
            root.addSubview_(button)
            answer_buttons.append(button)
            x -= 96.0
        self.answer_buttons = tuple(reversed(answer_buttons))
        self.detail_field = self._label("", ((_PADDING, 44.0), (width - 24.0, 16.0)), secondary=True)
        for field in (self.source_field, self.session_field, self.question_field, self.detail_field):
            root.addSubview_(field)
        root.addSubview_(self.answer_reply_field)
        root.addSubview_(self.answer_status_field)
        controls = (
            ("", "Previous Ask", "previous:", 32.0, False, "chevron.left"),
            ("", "Next Ask", "next:", 32.0, False, "chevron.right"),
            ("Open", "Open Asking Session", "open:", 64.0, False, None),
            ("Mark Seen", "Mark Seen on Screen Bar", "markSeen:", 96.0, False, None),
            ("Close", "Collapse Announcer", "collapse:", 64.0, True, None),
        )
        buttons = []
        x = 20.0
        for title, label, selector, button_width, enabled, symbol in controls:
            button = self._button(
                title,
                label,
                selector,
                ((x, 16.0), (button_width, 28.0)),
                enabled,
                symbol=symbol,
            )
            button.setRepresentedObject_((0, None))
            root.addSubview_(button)
            buttons.append(button)
            x += button_width + 8.0
        self.buttons = tuple(buttons)
        self._configure_key_view_loop(root)
        self.expanded_view = root
        return root

    def _answer_draft_text(self) -> str | None:
        if self.answer_reply_field is None or self.answer_reply_field.isHidden():
            return None
        return str(self.answer_reply_field.stringValue())

    def _layout_answer_controls(self) -> None:
        if (
            self.expanded_view is None
            or self.answer_reply_field is None
            or self.answer_status_field is None
        ):
            return
        visible_buttons = tuple(
            button for button in self.answer_buttons if not button.isHidden()
        )
        right = self.expanded_view.bounds().size.width - _PADDING
        button_span = (
            len(visible_buttons) * _ANSWER_BUTTON_WIDTH
            + max(0, len(visible_buttons) - 1) * _ANSWER_CONTROL_GAP
        )
        button_x = right - button_span
        for button in visible_buttons:
            button.setFrame_(
                (
                    (button_x, _ANSWER_ROW_Y),
                    (_ANSWER_BUTTON_WIDTH, _ANSWER_ROW_HEIGHT),
                )
            )
            button_x += _ANSWER_BUTTON_WIDTH + _ANSWER_CONTROL_GAP

        leading_views = []
        if not self.answer_status_field.isHidden():
            leading_views.append(self.answer_status_field)
        if not self.answer_reply_field.isHidden():
            leading_views.append(self.answer_reply_field)
        if not leading_views:
            return
        leading_right = right - button_span - _ANSWER_CONTROL_GAP
        leading_width = leading_right - _PADDING
        if len(leading_views) == 1:
            widths = (leading_width,)
        else:
            status_width = 72.0
            widths = (
                status_width,
                leading_width - status_width - _ANSWER_CONTROL_GAP,
            )
        leading_x = _PADDING
        for view, view_width in zip(leading_views, widths):
            height = 24.0 if view is self.answer_reply_field else 16.0
            view.setFrame_(
                (
                    (leading_x, _ANSWER_ROW_Y + (_ANSWER_ROW_HEIGHT - height) / 2.0),
                    (view_width, height),
                )
            )
            leading_x += view_width + _ANSWER_CONTROL_GAP

    def _emit_answer(
        self,
        action: AnswerActionKind,
        generation: int | None = None,
        selected_identity: AnnouncerAlertIdentity | None = None,
    ) -> None:
        answer_plan = self._answer_plan
        handler = self._answer_handler
        panel_plan = self._plan
        if not (
            self._presentation_allowed
            and self.visibility is AnnouncerStackVisibility.EXPANDED
            and answer_plan is not None
            and panel_plan is not None
            and callable(handler)
        ):
            return
        current_identity = self._selected_identity(panel_plan)
        if (
            current_identity is None
            or (
                generation is not None
                and (
                generation != panel_plan.generation
                or selected_identity != current_identity
                or answer_plan.request_identity != current_identity
                or answer_plan.generation != panel_plan.generation
            )
            )
        ):
            return
        handler(
            action,
            panel_plan.generation,
            current_identity,
            self._answer_draft_text() if action is AnswerActionKind.REPLY else None,
        )

    def _update_answer_controls(
        self,
        plan: AnnouncerStackPlan,
        answer_plan: AnswerControlPlan | None,
    ) -> None:
        if (
            self.answer_reply_field is None
            or self.answer_status_field is None
            or self.detail_field is None
            or self.expanded_view is None
        ):
            return
        selected_identity = self._selected_identity(plan)
        visible = (
            answer_plan is not None
            and selected_identity is not None
            and answer_plan.request_identity == selected_identity
            and answer_plan.generation == plan.generation
        )
        self._answer_plan = answer_plan if visible else None
        self.answer_reply_field.setHidden_(True)
        self.answer_status_field.setHidden_(True)
        self.answer_reply_field.setEditable_(False)
        for button in self.answer_buttons:
            button.setHidden_(True)
            button.setEnabled_(False)
            button.setRepresentedObject_((0, None, None))
        if not visible:
            self.answer_reply_field.setStringValue_("")
            self.answer_status_field.setStringValue_("")
            return
        assert answer_plan is not None
        self.answer_reply_field.setStringValue_(answer_plan.draft_text)
        if answer_plan.can_edit_reply:
            self.answer_reply_field.setHidden_(False)
            self.answer_reply_field.setEditable_(True)
        if answer_plan.status_text:
            self.answer_status_field.setStringValue_(answer_plan.status_text)
            self.answer_status_field.setHidden_(False)
        else:
            self.answer_status_field.setStringValue_("")
        action_labels = {
            AnswerActionKind.APPROVE: ("Approve", "Approve request", "approve:"),
            AnswerActionKind.DENY: ("Deny", "Deny request", "deny:"),
            AnswerActionKind.REPLY: ("Send", "Send reply", "reply:"),
            AnswerActionKind.CANCEL: ("Cancel", "Cancel send", "cancel:"),
            AnswerActionKind.RETRY: ("Retry", "Retry answer", "retry:"),
            AnswerActionKind.JUMP: ("Jump", "Open asking session", "jump:"),
        }
        for button, action in zip(self.answer_buttons, answer_plan.primary_actions):
            title, label, selector = action_labels[action]
            button.setTitle_(title)
            button.setAccessibilityLabel_(label)
            button.setAction_(selector)
            button.setEnabled_(
                answer_plan.can_send
                if action in {
                    AnswerActionKind.APPROVE,
                    AnswerActionKind.DENY,
                    AnswerActionKind.REPLY,
                }
                else True
            )
            button.setHidden_(False)
            button.setRepresentedObject_((plan.generation, selected_identity, action))
        self._layout_answer_controls()

    def _update_expanded(
        self,
        plan: AnnouncerStackPlan,
        answer_plan: AnswerControlPlan | None,
    ) -> None:
        root = self.expanded_view
        if root is None:
            return
        selected_identity = self._selected_identity(plan)
        root.key_handler = lambda action: self._emit(action, plan.generation, selected_identity)
        root.setAccessibilityLabel_(plan.accessibility_label)
        root.setAccessibilityValue_(plan.accessibility_value)
        root.setAccessibilityHelp_(plan.accessibility_help)
        selected = plan.alerts[plan.selected_index] if plan.selected_index is not None else None
        if selected is None:
            return
        self.source_field.setStringValue_(selected.source_label)
        self.session_field.setStringValue_(
            f"{selected.session_label} · {selected.priority.name.title()} request"
        )
        self.question_field.setStringValue_(selected.question)
        detail = f"{plan.position_text or ''} · {plan.total_actionable_count} asks · {plan.unseen_count} unseen"
        if selected.seen_on_screen_bar:
            detail += " · Seen on Screen Bar"
        self.detail_field.setStringValue_(detail)
        self._update_answer_controls(plan, answer_plan)
        for button, enabled in zip(
            self.buttons,
            (plan.can_previous, plan.can_next, plan.can_open, plan.can_mark_seen, True),
        ):
            button.setEnabled_(enabled)
            button.setRepresentedObject_((plan.generation, selected_identity))
        self._configure_key_view_loop(root)

    def _resign_key(self) -> None:
        window = self.window
        if window is not None and window.isKeyWindow():
            window.resignKeyWindow()

    def update(
        self,
        plan: AnnouncerStackPlan,
        intent_handler: Callable[[AnnouncerStackIntent], None] | None,
        *,
        center_x: float,
        top_y: float,
        allowed: bool = True,
        preferences: AccessibilityDisplayPreferences | None = None,
        answer_plan: AnswerControlPlan | None = None,
        answer_handler: Callable[
            [AnswerActionKind, int, AnnouncerAlertIdentity, str | None], None
        ]
        | None = None,
    ) -> None:
        previous_visibility = self.visibility
        next_visibility = plan.visibility if bool(allowed) else AnnouncerStackVisibility.HIDDEN
        if (
            previous_visibility is AnnouncerStackVisibility.EXPANDED
            and next_visibility is AnnouncerStackVisibility.COLLAPSED
        ):
            self._pointer_authorized = False
            self._resign_key()
        self._plan = plan
        self._answer_plan = answer_plan
        self._intent_handler = intent_handler
        self._answer_handler = answer_handler
        self._presentation_allowed = bool(allowed)
        self.visibility = next_visibility
        if self.visibility is AnnouncerStackVisibility.HIDDEN:
            self.hide()
            return
        expanded = self.visibility is AnnouncerStackVisibility.EXPANDED
        size = (_PANEL_WIDTH, _PANEL_HEIGHT) if expanded else (_PANEL_WIDTH, _PILL_HEIGHT)
        window = self._ensure_window()
        window.announcer_expanded = expanded and self._pointer_authorized
        window.announcer_pointer_authorized = self._pointer_authorized
        window.setFrame_display_(((center_x - size[0] / 2.0, top_y - size[1]), size), True)
        reusing_expanded = (
            expanded
            and self.expanded_view is not None
            and previous_visibility is AnnouncerStackVisibility.EXPANDED
        )
        if reusing_expanded:
            root = self.expanded_view
            root.setFrame_(((0.0, 0.0), size))
        else:
            self.buttons = ()
            self.answer_buttons = ()
            self.expanded_view = None
            root = self._build_expanded(((0.0, 0.0), size)) if expanded else self._build_collapsed(plan, ((0.0, 0.0), size))
            self.root_view = root
            window.setContentView_(root)
        if expanded:
            self._update_expanded(plan, answer_plan)
        present_window(window, key=False)
        if (
            expanded
            and self._pointer_authorized
            and not reusing_expanded
            and not desktop_takeover_suppressed()
        ):
            window.makeFirstResponder_(root)
            window.makeKeyWindow()
        elif not expanded:
            self._resign_key()

    def hide(self) -> None:
        self._presentation_allowed = False
        self._pointer_authorized = False
        self._resign_key()
        if self.window is not None:
            self.window.announcer_expanded = False
            self.window.announcer_pointer_authorized = False
            self.window.orderOut_(None)

    def close(self) -> None:
        self._intent_handler = None
        self._plan = None
        self._answer_plan = None
        self._answer_handler = None
        self.hide()
        if self.window is not None:
            self.window.close()
        self.window = None
        self.root_view = None
        self.expanded_view = None
        self.buttons = ()
        self.answer_buttons = ()
        self.source_field = None
        self.session_field = None
        self.question_field = None
        self.detail_field = None
        self.answer_reply_field = None
        self.answer_status_field = None
        self.visibility = AnnouncerStackVisibility.HIDDEN

    @objc.IBAction
    def previous_(self, _sender) -> None:
        self._emit_from_control(AnnouncerStackAction.PREVIOUS, _sender)

    @objc.IBAction
    def next_(self, _sender) -> None:
        self._emit_from_control(AnnouncerStackAction.NEXT, _sender)

    @objc.IBAction
    def open_(self, _sender) -> None:
        self._emit_from_control(AnnouncerStackAction.OPEN, _sender)

    @objc.IBAction
    def markSeen_(self, _sender) -> None:
        self._emit_from_control(AnnouncerStackAction.MARK_SEEN, _sender)

    @objc.IBAction
    def collapse_(self, _sender) -> None:
        self._emit_from_control(AnnouncerStackAction.COLLAPSE, _sender)

    def _emit_from_control(self, action: AnnouncerStackAction, sender) -> None:
        try:
            generation, selected_identity = sender.representedObject()
        except Exception:
            return
        if type(generation) is not int:
            return
        self._emit(action, generation, selected_identity)

    def _emit_answer_from_control(self, action: AnswerActionKind, sender) -> None:
        try:
            generation, selected_identity, action_kind = sender.representedObject()
        except Exception:
            return
        if type(generation) is not int or action_kind is not action:
            return
        self._emit_answer(action, generation, selected_identity)


__all__ = ["AnnouncerStackPanel"]
