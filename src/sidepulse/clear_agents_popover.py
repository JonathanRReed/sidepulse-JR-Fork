"""Native, bounded presentation for previewing and undoing Clear Agents.

The retained status-bar controller supplies immutable presentation state and
receives typed intents. This module owns only the transient AppKit surface,
its dedicated close delegate, and deterministic responder behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from typing import Final

import objc
from AppKit import (
    NSBezelStyleRounded,
    NSButton,
    NSColor,
    NSEventModifierFlagShift,
    NSFont,
    NSLineBreakByTruncatingTail,
    NSMaxYEdge,
    NSPopover,
    NSPopoverBehaviorApplicationDefined,
    NSPopoverBehaviorTransient,
    NSTextField,
    NSView,
    NSViewController,
)
from Foundation import NSObject

from .clear_agents import (
    MAX_PREVIEW_ITEMS,
    ClearAgentsCommitPlan,
    ClearAgentsPreview,
    ClearAgentsUndoPlan,
)
from .window_presentation import activate_app

_POPOVER_WIDTH: Final = 420.0
_POPOVER_HEIGHT: Final = 360.0
_MAX_LABEL_LENGTH: Final = 80
_MAX_COUNT: Final = 2_048

CLEAR_AGENTS_PRESERVATION_TEXT: Final = (
    "Only exact local completion receipts change. History, transcripts, hooks, "
    "credentials, settings, Other Macs, live asks, and failures stay."
)
_ROOT_HELP: Final = (
    "Reviews a local presentation acknowledgement. Clear Agents does not stop "
    "or delete agent work. Use Tab to move, Return for the primary action, and "
    "Escape to close."
)


class ClearAgentsPopoverState(str, Enum):
    PREVIEW = "preview"
    SAVING = "saving"
    STALE = "stale"
    FAILURE = "failure"
    RECEIPT = "receipt"
    EXPIRED_UNDO = "expired_undo"
    UNDONE = "undone"


class ClearAgentsPopoverAction(str, Enum):
    CONFIRM = "confirm"
    CANCEL = "cancel"
    REFRESH = "refresh"
    RETRY = "retry"
    UNDO = "undo"
    DONE = "done"


def _valid_count(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_COUNT


def _valid_agent_label(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= _MAX_LABEL_LENGTH
        and value.isprintable()
        and "\x00" not in value
        and "\n" not in value
        and "\r" not in value
        and "/" not in value
        and "\\" not in value
    )


@dataclass(frozen=True, slots=True)
class ClearAgentsPopoverPresentation:
    """Content-safe view input, detached from canonical agent truth."""

    state: ClearAgentsPopoverState
    clearable_count: int = 0
    agent_labels: tuple[str, ...] = ()
    protected_active_count: int = 0
    protected_waiting_count: int = 0
    protected_failed_count: int = 0
    protected_queued_count: int = 0
    protected_other_count: int = 0
    protected_remote_or_unkeyed_count: int = 0
    cleared_count: int = 0

    def __post_init__(self) -> None:
        if type(self.state) is not ClearAgentsPopoverState:
            raise ValueError("invalid Clear Agents popover state")
        counts = (
            self.clearable_count,
            self.protected_active_count,
            self.protected_waiting_count,
            self.protected_failed_count,
            self.protected_queued_count,
            self.protected_other_count,
            self.protected_remote_or_unkeyed_count,
            self.cleared_count,
        )
        if not all(_valid_count(value) for value in counts):
            raise ValueError("invalid Clear Agents popover counts")
        if not (
            type(self.agent_labels) is tuple
            and len(self.agent_labels) <= min(MAX_PREVIEW_ITEMS, 6)
            and all(_valid_agent_label(label) for label in self.agent_labels)
        ):
            raise ValueError("invalid Clear Agents agent labels")

        nonempty_preview_states = {
            ClearAgentsPopoverState.PREVIEW,
            ClearAgentsPopoverState.SAVING,
            ClearAgentsPopoverState.FAILURE,
        }
        receipt_states = {
            ClearAgentsPopoverState.RECEIPT,
            ClearAgentsPopoverState.EXPIRED_UNDO,
            ClearAgentsPopoverState.UNDONE,
        }
        if self.state in nonempty_preview_states and not (
            self.clearable_count > 0
            and 0 < len(self.agent_labels) <= self.clearable_count
            and self.cleared_count == 0
        ):
            raise ValueError("invalid Clear Agents preview presentation")
        if self.state is ClearAgentsPopoverState.STALE and not (
            self.cleared_count == 0
            and (
                (
                    self.clearable_count > 0
                    and 0 < len(self.agent_labels) <= self.clearable_count
                )
                or (self.clearable_count == 0 and not self.agent_labels)
            )
        ):
            raise ValueError("invalid Clear Agents stale presentation")
        if self.state in receipt_states and not (
            self.clearable_count == 0
            and not self.agent_labels
            and self.cleared_count > 0
        ):
            raise ValueError("invalid Clear Agents receipt presentation")

    @classmethod
    def from_preview(
        cls,
        preview: ClearAgentsPreview,
        *,
        state: ClearAgentsPopoverState = ClearAgentsPopoverState.PREVIEW,
    ) -> ClearAgentsPopoverPresentation:
        """Project the typed pure preview without accepting arbitrary UI copy."""
        if type(preview) is not ClearAgentsPreview:
            raise TypeError("Clear Agents popover requires a typed preview")
        if state not in {
            ClearAgentsPopoverState.PREVIEW,
            ClearAgentsPopoverState.SAVING,
            ClearAgentsPopoverState.STALE,
            ClearAgentsPopoverState.FAILURE,
        }:
            raise ValueError("preview cannot project the requested popover state")
        protected = preview.protected_counts
        return cls(
            state=state,
            clearable_count=preview.clearable_count,
            agent_labels=tuple(item.safe_label for item in preview.items[:6]),
            protected_active_count=protected.active,
            protected_waiting_count=protected.waiting,
            protected_failed_count=protected.failed,
            protected_queued_count=protected.queued,
            protected_other_count=protected.other,
            protected_remote_or_unkeyed_count=(
                protected.remote_completions
                + protected.unkeyed_local_completions
            ),
        )

    @classmethod
    def from_commit_plan(
        cls,
        plan: ClearAgentsCommitPlan,
        *,
        state: ClearAgentsPopoverState = ClearAgentsPopoverState.RECEIPT,
    ) -> ClearAgentsPopoverPresentation:
        if type(plan) is not ClearAgentsCommitPlan:
            raise TypeError("Clear Agents receipt requires a typed commit plan")
        if state not in {
            ClearAgentsPopoverState.RECEIPT,
            ClearAgentsPopoverState.EXPIRED_UNDO,
        }:
            raise ValueError("commit plan cannot project the requested popover state")
        return cls(state=state, cleared_count=plan.cleared_count)

    @classmethod
    def from_undo_plan(
        cls,
        plan: ClearAgentsUndoPlan,
    ) -> ClearAgentsPopoverPresentation:
        if type(plan) is not ClearAgentsUndoPlan:
            raise TypeError("Clear Agents Undo requires a typed Undo plan")
        return cls(
            state=ClearAgentsPopoverState.UNDONE,
            cleared_count=plan.restored_count,
        )


@dataclass(frozen=True, slots=True)
class _ButtonPlan:
    action: ClearAgentsPopoverAction
    title: str
    accessibility_label: str
    help_text: str
    is_default: bool = False


_BUTTON_PLANS: Final[dict[ClearAgentsPopoverState, tuple[_ButtonPlan, ...]]] = {
    ClearAgentsPopoverState.PREVIEW: (
        _ButtonPlan(
            ClearAgentsPopoverAction.CANCEL,
            "Cancel",
            "Cancel Clear Agents",
            "Close this preview without changing presentation receipts.",
        ),
        _ButtonPlan(
            ClearAgentsPopoverAction.CONFIRM,
            "Clear Presented Agents",
            "Clear Presented Agents",
            "Save exact local completion receipts for the agents in this preview.",
            is_default=True,
        ),
    ),
    ClearAgentsPopoverState.SAVING: (),
    ClearAgentsPopoverState.STALE: (
        _ButtonPlan(
            ClearAgentsPopoverAction.CANCEL,
            "Cancel",
            "Cancel Clear Agents",
            "Close the changed preview without clearing anything.",
        ),
        _ButtonPlan(
            ClearAgentsPopoverAction.REFRESH,
            "Review Changes",
            "Review Changed Agents",
            "Refresh the preview before deciding whether to clear presentation receipts.",
            is_default=True,
        ),
    ),
    ClearAgentsPopoverState.FAILURE: (
        _ButtonPlan(
            ClearAgentsPopoverAction.CANCEL,
            "Cancel",
            "Cancel Clear Agents",
            "Close this failure receipt. Nothing was cleared.",
        ),
        _ButtonPlan(
            ClearAgentsPopoverAction.RETRY,
            "Try Again",
            "Try Clear Agents Again",
            "Revalidate the preview and try the private local save again.",
            is_default=True,
        ),
    ),
    ClearAgentsPopoverState.RECEIPT: (
        _ButtonPlan(
            ClearAgentsPopoverAction.UNDO,
            "Undo",
            "Undo Clear Agents",
            "Remove only the exact receipts added by the latest Clear Agents batch.",
        ),
        _ButtonPlan(
            ClearAgentsPopoverAction.DONE,
            "Done",
            "Finish Clear Agents",
            "Close this local presentation receipt.",
            is_default=True,
        ),
    ),
    ClearAgentsPopoverState.EXPIRED_UNDO: (
        _ButtonPlan(
            ClearAgentsPopoverAction.DONE,
            "Done",
            "Finish Clear Agents",
            "Close this expired Undo receipt.",
            is_default=True,
        ),
    ),
    ClearAgentsPopoverState.UNDONE: (
        _ButtonPlan(
            ClearAgentsPopoverAction.DONE,
            "Done",
            "Finish Clear Agents",
            "Close this successful Undo receipt.",
            is_default=True,
        ),
    ),
}

_EMPTY_STALE_BUTTONS: Final = (
    _ButtonPlan(
        ClearAgentsPopoverAction.DONE,
        "Done",
        "Finish Clear Agents",
        "Close this changed preview. No completed agents remain eligible to clear.",
        is_default=True,
    ),
)


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _state_copy(
    presentation: ClearAgentsPopoverPresentation,
) -> tuple[str, str]:
    count = presentation.clearable_count
    cleared = presentation.cleared_count
    state = presentation.state
    if state is ClearAgentsPopoverState.PREVIEW:
        return (
            "Clear Agents?",
            f"{count} completed {_plural(count, 'agent')} will leave the current presentation.",
        )
    if state is ClearAgentsPopoverState.SAVING:
        return (
            "Clearing Presented Agents",
            "Saving exact local completion receipts. Current work is unchanged.",
        )
    if state is ClearAgentsPopoverState.STALE:
        if count == 0:
            return (
                "Agents Changed",
                "Agents changed while this preview was open. "
                "No completed agents remain eligible to clear.",
            )
        return (
            "Agents Changed",
            "Agents changed while this preview was open. Review the refreshed list before clearing.",
        )
    if state is ClearAgentsPopoverState.FAILURE:
        return (
            "Could Not Clear Agents",
            "JR Bar could not save the local presentation receipts. Nothing was cleared.",
        )
    if state is ClearAgentsPopoverState.RECEIPT:
        return (
            "Agents Cleared",
            f"{cleared} completed {_plural(cleared, 'agent')} left the current presentation.",
        )
    if state is ClearAgentsPopoverState.EXPIRED_UNDO:
        return (
            "Undo Expired",
            f"{cleared} completed-agent {_plural(cleared, 'receipt')} "
            f"{'remains' if cleared == 1 else 'remain'} acknowledged. "
            "The five-minute Undo window has ended.",
        )
    return (
        "Clear Agents Undone",
        f"{cleared} completion {_plural(cleared, 'receipt')} "
        f"{'was' if cleared == 1 else 'were'} removed. "
        "Current canonical work decides what appears.",
    )


def _item_text(presentation: ClearAgentsPopoverPresentation) -> str:
    labels = presentation.agent_labels
    text = "\n".join(labels)
    hidden = presentation.clearable_count - len(labels)
    if hidden:
        suffix = f"+{hidden} more completed {_plural(hidden, 'agent')}"
        text = f"{text}\n{suffix}"
    return text


def _protected_text(presentation: ClearAgentsPopoverPresentation) -> str:
    other = presentation.protected_other_count + presentation.protected_queued_count
    remote = presentation.protected_remote_or_unkeyed_count
    return (
        f"Protected now: {presentation.protected_active_count} active, "
        f"{presentation.protected_waiting_count} waiting, "
        f"{presentation.protected_failed_count} failed, and {other} other current. "
        f"{remote} remote or unkeyed {_plural(remote, 'completion')} stay visible."
    )


def _label(
    text: str,
    frame,
    *,
    size: float,
    bold: bool = False,
    secondary: bool = False,
    lines: int = 1,
) -> NSTextField:
    field = NSTextField.alloc().initWithFrame_(frame)
    field.setStringValue_(text)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setBordered_(False)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setFont_(
        NSFont.boldSystemFontOfSize_(size)
        if bold
        else NSFont.systemFontOfSize_(size)
    )
    field.setTextColor_(
        NSColor.secondaryLabelColor() if secondary else NSColor.labelColor()
    )
    field.setLineBreakMode_(NSLineBreakByTruncatingTail)
    field.setMaximumNumberOfLines_(lines)
    if lines > 1:
        field.setUsesSingleLineMode_(False)
        field.cell().setWraps_(True)
        field.cell().setTruncatesLastVisibleLine_(True)
    return field


class _ClearAgentsRootView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_ClearAgentsRootView, self).initWithFrame_(frame)
        if self is not None:
            self.owner = None
            self.setAccessibilityElement_(True)
            self.setAccessibilityRole_("AXGroup")
            self.setAccessibilityHelp_(_ROOT_HELP)
        return self

    def acceptsFirstResponder(self) -> bool:
        return True

    def keyDown_(self, event) -> None:
        owner = self.owner
        if owner is not None and owner._handle_key_event(event, self):
            return
        objc.super(_ClearAgentsRootView, self).keyDown_(event)


class _ClearAgentsButton(NSButton):
    def initWithFrame_(self, frame):
        self = objc.super(_ClearAgentsButton, self).initWithFrame_(frame)
        if self is not None:
            self.owner = None
        return self

    def keyDown_(self, event) -> None:
        owner = self.owner
        if owner is not None and owner._handle_key_event(event, self):
            return
        objc.super(_ClearAgentsButton, self).keyDown_(event)


class _ClearAgentsActionTarget(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(_ClearAgentsActionTarget, self).init()
        if self is not None:
            self.owner = owner
        return self

    @objc.IBAction
    def perform_(self, sender) -> None:
        owner = getattr(self, "owner", None)
        if owner is not None:
            owner._emit_sender(sender)


class _ClearAgentsPopoverDelegate(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(_ClearAgentsPopoverDelegate, self).init()
        if self is not None:
            self.owner = owner
        return self

    def popoverDidClose_(self, _notification) -> None:
        owner = getattr(self, "owner", None)
        if owner is not None:
            owner._notify_closed()


class ClearAgentsPopoverPresenter:
    """Own one transient popover without becoming another state controller."""

    def __init__(
        self,
        presentation: ClearAgentsPopoverPresentation,
        *,
        on_action: Callable[[ClearAgentsPopoverAction], None],
        on_close: Callable[[], None],
    ) -> None:
        if type(presentation) is not ClearAgentsPopoverPresentation:
            raise TypeError("Clear Agents presenter requires a typed presentation")
        if not callable(on_action) or not callable(on_close):
            raise TypeError("Clear Agents presenter requires action and close handlers")
        self.presentation = presentation
        self._on_action = on_action
        self._on_close = on_close
        self._close_notification_emitted = False

        self.root_view = _ClearAgentsRootView.alloc().initWithFrame_(
            ((0.0, 0.0), (_POPOVER_WIDTH, _POPOVER_HEIGHT))
        )
        self.root_view.owner = self
        self.title_field = _label(
            "",
            ((20.0, 320.0), (380.0, 24.0)),
            size=16.0,
            bold=True,
        )
        self.summary_field = _label(
            "",
            ((20.0, 276.0), (380.0, 38.0)),
            size=13.0,
            lines=2,
        )
        self.items_field = _label(
            "",
            ((20.0, 164.0), (380.0, 106.0)),
            size=12.0,
            lines=7,
        )
        self.protected_field = _label(
            "",
            ((20.0, 112.0), (380.0, 46.0)),
            size=11.0,
            secondary=True,
            lines=3,
        )
        self.preservation_field = _label(
            CLEAR_AGENTS_PRESERVATION_TEXT,
            ((20.0, 60.0), (380.0, 46.0)),
            size=11.0,
            secondary=True,
            lines=3,
        )
        for field in (
            self.title_field,
            self.summary_field,
            self.items_field,
            self.protected_field,
            self.preservation_field,
        ):
            self.root_view.addSubview_(field)

        self.summary_field.setAccessibilityElement_(True)
        self.summary_field.setAccessibilityLabel_("Clear Agents status")
        self.items_field.setAccessibilityElement_(True)
        self.items_field.setAccessibilityLabel_("Agents to clear from presentation")
        self.protected_field.setAccessibilityElement_(True)
        self.protected_field.setAccessibilityLabel_("Protected agent work")
        self.preservation_field.setAccessibilityElement_(True)
        self.preservation_field.setAccessibilityLabel_("Preserved data")
        self.preservation_field.setAccessibilityValue_(
            CLEAR_AGENTS_PRESERVATION_TEXT
        )

        self.action_target = _ClearAgentsActionTarget.alloc().initWithOwner_(self)
        self.close_delegate = _ClearAgentsPopoverDelegate.alloc().initWithOwner_(self)
        buttons = []
        for index in range(2):
            button = _ClearAgentsButton.alloc().initWithFrame_(
                ((172.0 + index * 116.0, 18.0), (108.0, 30.0))
            )
            button.owner = self
            button.setBezelStyle_(NSBezelStyleRounded)
            button.setTarget_(self.action_target)
            button.setAction_("perform:")
            button.setRefusesFirstResponder_(False)
            button.setAccessibilityRole_("AXButton")
            self.root_view.addSubview_(button)
            buttons.append(button)
        self.buttons = tuple(buttons)

        view_controller = NSViewController.alloc().init()
        view_controller.setView_(self.root_view)
        self.popover = NSPopover.alloc().init()
        self.popover.setContentViewController_(view_controller)
        self.popover.setContentSize_((_POPOVER_WIDTH, _POPOVER_HEIGHT))
        self.popover.setBehavior_(NSPopoverBehaviorTransient)
        self.popover.setDelegate_(self.close_delegate)
        self.refresh(presentation)

    def focusable_controls(self) -> tuple[NSButton, ...]:
        return tuple(
            button
            for button in self.buttons
            if not button.isHidden() and button.isEnabled()
        )

    def _configure_key_view_loop(self) -> None:
        focusable = self.focusable_controls()
        if not focusable:
            self.root_view.setNextKeyView_(self.root_view)
            return
        self.root_view.setNextKeyView_(focusable[0])
        for current, following in pairwise(focusable):
            current.setNextKeyView_(following)
        focusable[-1].setNextKeyView_(focusable[0])

    def refresh(self, presentation: ClearAgentsPopoverPresentation) -> None:
        if type(presentation) is not ClearAgentsPopoverPresentation:
            raise TypeError("Clear Agents refresh requires a typed presentation")
        self.presentation = presentation
        self.popover.setBehavior_(
            NSPopoverBehaviorApplicationDefined
            if presentation.state is ClearAgentsPopoverState.SAVING
            else NSPopoverBehaviorTransient
        )
        title, summary = _state_copy(presentation)
        items = _item_text(presentation) if presentation.agent_labels else ""
        protected = (
            _protected_text(presentation)
            if presentation.clearable_count
            else "Current canonical agent work and history are unchanged."
        )

        self.title_field.setStringValue_(title)
        self.summary_field.setStringValue_(summary)
        self.summary_field.setAccessibilityValue_(summary)
        self.items_field.setStringValue_(items)
        self.items_field.setAccessibilityValue_(items)
        self.items_field.setHidden_(not items)
        self.protected_field.setStringValue_(protected)
        self.protected_field.setAccessibilityValue_(protected)
        self.root_view.setAccessibilityLabel_(
            "Clear Agents preview"
            if presentation.state is ClearAgentsPopoverState.PREVIEW
            else title
        )
        self.root_view.setAccessibilityValue_(
            " ".join(part for part in (summary, items, protected) if part)
        )

        plans = (
            _EMPTY_STALE_BUTTONS
            if presentation.state is ClearAgentsPopoverState.STALE
            and presentation.clearable_count == 0
            else _BUTTON_PLANS[presentation.state]
        )
        for index, button in enumerate(self.buttons):
            if index >= len(plans):
                button.setHidden_(True)
                button.setEnabled_(False)
                button.setRepresentedObject_(None)
                button.setKeyEquivalent_("")
                continue
            plan = plans[index]
            button.setHidden_(False)
            button.setEnabled_(True)
            button.setTitle_(plan.title)
            button.setAccessibilityLabel_(plan.accessibility_label)
            button.setAccessibilityHelp_(plan.help_text)
            button.setToolTip_(plan.help_text)
            button.setRepresentedObject_(plan.action.value)
            button.setKeyEquivalent_("\r" if plan.is_default else "")
        button_gap = 8.0
        visible_buttons = self.buttons[: len(plans)]
        button_widths = tuple(
            max(
                88.0,
                min(182.0, float(button.intrinsicContentSize().width) + 16.0),
            )
            for button in visible_buttons
        )
        next_x = (
            _POPOVER_WIDTH
            - 20.0
            - sum(button_widths)
            - max(0, len(plans) - 1) * button_gap
        )
        for button, width in zip(visible_buttons, button_widths, strict=True):
            button.setFrame_(((next_x, 18.0), (width, 30.0)))
            next_x += width + button_gap
        self._configure_key_view_loop()

        window = self.root_view.window()
        focusable = self.focusable_controls()
        if (
            window is not None
            and focusable
            and window.firstResponder() not in focusable
        ):
            window.makeFirstResponder_(focusable[0])
        elif window is not None and not focusable:
            window.makeFirstResponder_(self.root_view)

    def _emit_sender(self, sender) -> None:
        raw_action = sender.representedObject()
        try:
            action = ClearAgentsPopoverAction(str(raw_action))
        except ValueError:
            return
        self._on_action(action)
        if action in {ClearAgentsPopoverAction.CANCEL, ClearAgentsPopoverAction.DONE}:
            self.dismiss()

    def _activate_default(self) -> bool:
        focusable = self.focusable_controls()
        if not focusable:
            return False
        default = next(
            (button for button in focusable if str(button.keyEquivalent()) == "\r"),
            focusable[0],
        )
        default.performClick_(None)
        return True

    def _focus_relative(self, sender, *, reverse: bool) -> bool:
        focusable = self.focusable_controls()
        window = self.root_view.window()
        if not focusable or window is None:
            return False
        if sender in focusable:
            index = focusable.index(sender)
            next_index = (index - 1 if reverse else index + 1) % len(focusable)
        else:
            next_index = len(focusable) - 1 if reverse else 0
        return bool(window.makeFirstResponder_(focusable[next_index]))

    def _handle_key_event(self, event, sender) -> bool:
        key_code = int(event.keyCode())
        characters = str(event.charactersIgnoringModifiers() or "")
        if self.presentation.state is ClearAgentsPopoverState.SAVING and (
            key_code in {36, 48, 53} or characters in {"\t", "\r", "\n", "\x1b"}
        ):
            return True
        if key_code == 48 or characters == "\t":
            return self._focus_relative(
                sender,
                reverse=bool(int(event.modifierFlags()) & NSEventModifierFlagShift),
            )
        if key_code == 36 or characters in {"\r", "\n"}:
            return self._activate_default()
        if key_code == 53 or characters == "\x1b":
            self.dismiss()
            return True
        return False

    def _notify_closed(self) -> None:
        if self._close_notification_emitted:
            return
        self._close_notification_emitted = True
        self._on_close()

    def dismiss(self) -> None:
        try:
            self.popover.performClose_(None)
        finally:
            self._notify_closed()

    def focus_shown_window(self, window, *, application=None) -> bool:
        """Activate and focus only after AppKit has attached the popover window."""
        if window is None:
            return False
        focusable = self.focusable_controls()
        first_responder = focusable[0] if focusable else self.root_view
        if application is None:
            activate_app()
        else:
            activate_method = getattr(
                application,
                "activateIgnoring" + "OtherApps_",
                None,
            )
            fallback_method = getattr(application, "activate", None)
            if callable(activate_method):
                activate_method(True)
            elif callable(fallback_method):
                fallback_method()
        window.makeKeyWindow()
        return bool(window.makeFirstResponder_(first_responder))

    def show(self, sender) -> bool:
        if sender is None or not callable(getattr(sender, "bounds", None)):
            raise TypeError("Clear Agents popover requires an anchor view")
        self._close_notification_emitted = False
        self.popover.showRelativeToRect_ofView_preferredEdge_(
            sender.bounds(),
            sender,
            NSMaxYEdge,
        )
        return self.focus_shown_window(self.root_view.window())


__all__ = [
    "CLEAR_AGENTS_PRESERVATION_TEXT",
    "ClearAgentsPopoverAction",
    "ClearAgentsPopoverPresentation",
    "ClearAgentsPopoverPresenter",
    "ClearAgentsPopoverState",
]
