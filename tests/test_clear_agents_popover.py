from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise

import pytest
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSEventModifierFlagShift,
    NSWindow,
    NSWindowStyleMaskTitled,
)

from sidepulse.capacity_types import SourceKey
from sidepulse.clear_agents import (
    ClearAgentsFence,
    ClearAgentsPreview,
    ClearAgentsPreviewItem,
    ClearAgentsProtectedCounts,
    ClearAgentsState,
    CompletionPresentationKey,
    plan_clear_agents_commit,
    plan_clear_agents_undo,
)
from sidepulse.clear_agents_popover import (
    CLEAR_AGENTS_PRESERVATION_TEXT,
    ClearAgentsPopoverAction,
    ClearAgentsPopoverPresentation,
    ClearAgentsPopoverPresenter,
    ClearAgentsPopoverState,
)


class _KeyEvent:
    def __init__(self, key_code: int, characters: str, flags: int = 0) -> None:
        self._key_code = key_code
        self._characters = characters
        self._flags = flags

    def keyCode(self) -> int:
        return self._key_code

    def charactersIgnoringModifiers(self) -> str:
        return self._characters

    def modifierFlags(self) -> int:
        return self._flags


def _presentation(
    state: ClearAgentsPopoverState,
    *,
    clearable_count: int = 2,
    cleared_count: int = 0,
    labels: tuple[str, ...] = ("Codex build agent", "Claude review agent"),
) -> ClearAgentsPopoverPresentation:
    return ClearAgentsPopoverPresentation(
        state=state,
        clearable_count=clearable_count,
        agent_labels=labels,
        protected_active_count=1,
        protected_waiting_count=2,
        protected_failed_count=1,
        protected_other_count=3,
        protected_remote_or_unkeyed_count=2,
        cleared_count=cleared_count,
    )


def _typed_preview(count: int = 2) -> ClearAgentsPreview:
    source = SourceKey("codex", "hooks", "local", "agent_events")
    keys = tuple(
        CompletionPresentationKey(source, f"codex:agent:{index}", "Stop", 10.0 + index)
        for index in range(count)
    )
    return ClearAgentsPreview(
        fence=ClearAgentsFence(0, keys, ()),
        items=tuple(
            ClearAgentsPreviewItem(key, f"Safe Agent {index + 1}")
            for index, key in enumerate(keys)
        ),
        clearable_count=count,
        hidden_item_count=0,
        protected_counts=ClearAgentsProtectedCounts(
            active=1,
            waiting=2,
            failed=1,
            queued=3,
            remote_completions=2,
            unkeyed_local_completions=1,
            other=4,
        ),
    )


def _presenter(
    presentation: ClearAgentsPopoverPresentation | None = None,
    *,
    on_action: Callable[[ClearAgentsPopoverAction], None] | None = None,
    on_close: Callable[[], None] | None = None,
) -> ClearAgentsPopoverPresenter:
    return ClearAgentsPopoverPresenter(
        presentation or _presentation(ClearAgentsPopoverState.PREVIEW),
        on_action=on_action or (lambda _action: None),
        on_close=on_close or (lambda: None),
    )


def _visible_buttons(presenter: ClearAgentsPopoverPresenter) -> tuple[NSButton, ...]:
    return tuple(
        button for button in presenter.buttons if not button.isHidden()
    )


def test_empty_typed_stale_preview_explains_that_nothing_remains_to_clear() -> None:
    """Rejecting a valid zero-target stale reprojection would make this fail."""
    presentation = ClearAgentsPopoverPresentation.from_preview(
        _typed_preview(0),
        state=ClearAgentsPopoverState.STALE,
    )
    presenter = _presenter(presentation)

    assert presenter.summary_field.stringValue() == (
        "Agents changed while this preview was open. "
        "No completed agents remain eligible to clear."
    )
    assert presenter.items_field.isHidden()
    assert tuple(button.title() for button in _visible_buttons(presenter)) == ("Done",)


def _host(presenter: ClearAgentsPopoverPresenter) -> NSWindow:
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0.0, 0.0), (420.0, 360.0)),
        NSWindowStyleMaskTitled,
        NSBackingStoreBuffered,
        False,
    )
    window.setContentView_(presenter.root_view)
    return window


@pytest.mark.parametrize(
    ("presentation", "title", "summary_fragment", "buttons"),
    [
        (
            _presentation(ClearAgentsPopoverState.PREVIEW),
            "Clear Agents?",
            "2 completed agents will leave",
            ("Cancel", "Clear Presented Agents"),
        ),
        (
            _presentation(ClearAgentsPopoverState.SAVING),
            "Clearing Presented Agents",
            "Saving exact local completion receipts",
            (),
        ),
        (
            _presentation(ClearAgentsPopoverState.STALE),
            "Agents Changed",
            "changed while this preview was open",
            ("Cancel", "Review Changes"),
        ),
        (
            _presentation(ClearAgentsPopoverState.FAILURE),
            "Could Not Clear Agents",
            "Nothing was cleared",
            ("Cancel", "Try Again"),
        ),
        (
            _presentation(
                ClearAgentsPopoverState.RECEIPT,
                clearable_count=0,
                cleared_count=2,
                labels=(),
            ),
            "Agents Cleared",
            "2 completed agents left",
            ("Undo", "Done"),
        ),
        (
            _presentation(
                ClearAgentsPopoverState.EXPIRED_UNDO,
                clearable_count=0,
                cleared_count=2,
                labels=(),
            ),
            "Undo Expired",
            "five-minute Undo window has ended",
            ("Done",),
        ),
        (
            _presentation(
                ClearAgentsPopoverState.UNDONE,
                clearable_count=0,
                cleared_count=2,
                labels=(),
            ),
            "Clear Agents Undone",
            "2 completion receipts were removed",
            ("Done",),
        ),
    ],
)
def test_every_state_projects_explicit_copy_and_native_actions(
    presentation: ClearAgentsPopoverPresentation,
    title: str,
    summary_fragment: str,
    buttons: tuple[str, ...],
) -> None:
    """A missing state branch or reused generic result would make this fail."""
    presenter = _presenter(presentation)

    assert presenter.title_field.stringValue() == title
    assert summary_fragment in presenter.summary_field.stringValue()
    assert tuple(button.title() for button in _visible_buttons(presenter)) == buttons
    assert all(button.isEnabled() for button in _visible_buttons(presenter))
    assert presenter.preservation_field.stringValue() == CLEAR_AGENTS_PRESERVATION_TEXT


def test_preview_lists_only_bounded_agent_labels_and_explicit_protected_counts() -> None:
    """Unbounded agent copy or a lost protection category would make this fail."""
    presentation = _presentation(
        ClearAgentsPopoverState.PREVIEW,
        clearable_count=8,
        labels=tuple(f"Agent {index}" for index in range(1, 7)),
    )

    presenter = _presenter(presentation)

    assert presenter.items_field.stringValue() == (
        "Agent 1\nAgent 2\nAgent 3\nAgent 4\nAgent 5\nAgent 6\n"
        "+2 more completed agents"
    )
    assert presenter.protected_field.stringValue() == (
        "Protected now: 1 active, 2 waiting, 1 failed, and 3 other current. "
        "2 remote or unkeyed completions stay visible."
    )
    assert presenter.items_field.maximumNumberOfLines() == 7
    assert presenter.items_field.accessibilityValue() == presenter.items_field.stringValue()


def test_typed_preview_adapter_caps_labels_and_preserves_every_protected_bucket() -> None:
    """Bypassing the pure preview or dropping queued and unsafe rows would fail."""
    presentation = ClearAgentsPopoverPresentation.from_preview(_typed_preview(8))

    assert presentation.clearable_count == 8
    assert presentation.agent_labels == tuple(
        f"Safe Agent {index}" for index in range(1, 7)
    )
    assert presentation.protected_active_count == 1
    assert presentation.protected_waiting_count == 2
    assert presentation.protected_failed_count == 1
    assert presentation.protected_queued_count == 3
    assert presentation.protected_other_count == 4
    assert presentation.protected_remote_or_unkeyed_count == 3
    assert "+2 more completed agents" in _presenter(presentation).items_field.stringValue()


def test_typed_commit_and_undo_adapters_use_exact_plan_counts() -> None:
    """Using cached UI counts instead of the pure commit plans would make this fail."""
    preview = _typed_preview(2)
    commit = plan_clear_agents_commit(
        preview,
        preview,
        ClearAgentsState(),
        batch_id="batch-ui",
        committed_at_epoch=100.0,
    )
    undo = plan_clear_agents_undo(
        commit.next_state,
        batch_id="batch-ui",
        now_epoch=101.0,
    )

    receipt = ClearAgentsPopoverPresentation.from_commit_plan(commit)
    expired = ClearAgentsPopoverPresentation.from_commit_plan(
        commit,
        state=ClearAgentsPopoverState.EXPIRED_UNDO,
    )
    undone = ClearAgentsPopoverPresentation.from_undo_plan(undo)

    assert (receipt.state, receipt.cleared_count) == (
        ClearAgentsPopoverState.RECEIPT,
        2,
    )
    assert (expired.state, expired.cleared_count) == (
        ClearAgentsPopoverState.EXPIRED_UNDO,
        2,
    )
    assert (undone.state, undone.cleared_count) == (
        ClearAgentsPopoverState.UNDONE,
        2,
    )


@pytest.mark.parametrize(
    "labels",
    [
        ("",),
        ("a" * 81,),
        ("ask\ntext",),
        ("/Users/private/transcript.json",),
        ("https://example.invalid/agent",),
        tuple(f"Agent {index}" for index in range(7)),
    ],
)
def test_presentation_refuses_unbounded_or_content_unsafe_agent_labels(
    labels: tuple[str, ...],
) -> None:
    """Accepting paths, URLs, multiline text, or oversized lists would fail."""
    with pytest.raises(ValueError, match="agent labels"):
        _presentation(
            ClearAgentsPopoverState.PREVIEW,
            clearable_count=max(1, len(labels)),
            labels=labels,
        )


def test_native_group_labels_help_values_and_buttons_have_exact_ax_metadata() -> None:
    """Dropping spoken state, help, or native button roles would make this fail."""
    presenter = _presenter()

    assert presenter.root_view.isAccessibilityElement()
    assert presenter.root_view.accessibilityRole() == "AXGroup"
    assert presenter.root_view.accessibilityLabel() == "Clear Agents preview"
    assert "completed agents" in presenter.root_view.accessibilityValue()
    assert "does not stop or delete agent work" in presenter.root_view.accessibilityHelp()
    assert presenter.summary_field.accessibilityLabel() == "Clear Agents status"
    assert (
        presenter.summary_field.accessibilityValue()
        == presenter.summary_field.stringValue()
    )
    assert presenter.preservation_field.accessibilityLabel() == "Preserved data"
    assert tuple(button.accessibilityLabel() for button in _visible_buttons(presenter)) == (
        "Cancel Clear Agents",
        "Clear Presented Agents",
    )
    assert all(button.accessibilityRole() == "AXButton" for button in presenter.buttons)
    assert all(str(button.accessibilityHelp() or "").strip() for button in presenter.buttons)


def test_refresh_reuses_controls_and_rebuilds_closed_key_loop_for_each_state() -> None:
    """Replacing the surface or leaving hidden controls in Tab order would fail."""
    presenter = _presenter()
    controls = (
        presenter.root_view,
        presenter.title_field,
        presenter.summary_field,
        presenter.items_field,
        presenter.protected_field,
        presenter.preservation_field,
        presenter.buttons,
    )

    for presentation in (
        _presentation(ClearAgentsPopoverState.SAVING),
        _presentation(
            ClearAgentsPopoverState.RECEIPT,
            clearable_count=0,
            cleared_count=2,
            labels=(),
        ),
        _presentation(
            ClearAgentsPopoverState.UNDONE,
            clearable_count=0,
            cleared_count=2,
            labels=(),
        ),
    ):
        presenter.refresh(presentation)
        assert (
            presenter.root_view,
            presenter.title_field,
            presenter.summary_field,
            presenter.items_field,
            presenter.protected_field,
            presenter.preservation_field,
            presenter.buttons,
        ) == controls
        focusable = presenter.focusable_controls()
        if presentation.state is ClearAgentsPopoverState.SAVING:
            assert focusable == ()
            assert presenter.root_view.nextKeyView() is presenter.root_view
        else:
            assert focusable
            assert presenter.root_view.nextKeyView() is focusable[0]
            for current, following in pairwise(focusable):
                assert current.nextKeyView() is following
            assert focusable[-1].nextKeyView() is focusable[0]


@pytest.mark.parametrize(
    ("state", "summary"),
    [
        (
            ClearAgentsPopoverState.EXPIRED_UNDO,
            "1 completed-agent receipt remains acknowledged. "
            "The five-minute Undo window has ended.",
        ),
        (
            ClearAgentsPopoverState.UNDONE,
            "1 completion receipt was removed. Current canonical work decides what appears.",
        ),
    ],
)
def test_single_receipt_states_use_clear_native_copy(
    state: ClearAgentsPopoverState,
    summary: str,
) -> None:
    """Plural-only result copy would make a one-agent receipt read incorrectly."""
    presenter = _presenter(
        _presentation(
            state,
            clearable_count=0,
            cleared_count=1,
            labels=(),
        )
    )

    assert presenter.summary_field.stringValue() == summary
    only_button = _visible_buttons(presenter)[0]
    assert only_button.frame().origin.x + only_button.frame().size.width == 400.0


def test_root_and_buttons_route_return_tab_shift_tab_and_escape_deterministically() -> None:
    """A default AppKit traversal dependency or wrong key mapping would fail."""
    received: list[ClearAgentsPopoverAction] = []
    closed: list[None] = []
    presenter = _presenter(on_action=received.append, on_close=lambda: closed.append(None))
    cancel, primary = _visible_buttons(presenter)
    window = _host(presenter)
    try:
        presenter.root_view.keyDown_(_KeyEvent(36, "\r"))
        assert received == [ClearAgentsPopoverAction.CONFIRM]

        assert window.makeFirstResponder_(cancel)
        cancel.keyDown_(_KeyEvent(48, "\t"))
        assert window.firstResponder() is primary
        primary.keyDown_(_KeyEvent(48, "\t", NSEventModifierFlagShift))
        assert window.firstResponder() is cancel

        presenter.root_view.keyDown_(_KeyEvent(53, "\x1b"))
        assert received == [ClearAgentsPopoverAction.CONFIRM]
        assert closed == [None]
    finally:
        window.close()


def test_button_actions_emit_typed_intents_and_terminal_actions_close() -> None:
    """Stringly typed actions or a receipt that cannot close would make this fail."""
    received: list[ClearAgentsPopoverAction] = []
    closed: list[None] = []
    presenter = _presenter(on_action=received.append, on_close=lambda: closed.append(None))

    cancel, primary = _visible_buttons(presenter)
    primary.performClick_(None)
    cancel.performClick_(None)

    assert received == [
        ClearAgentsPopoverAction.CONFIRM,
        ClearAgentsPopoverAction.CANCEL,
    ]
    assert closed == [None]


def test_saving_state_cannot_be_dismissed_before_undo_receipt_exists() -> None:
    received: list[ClearAgentsPopoverAction] = []
    closed: list[None] = []
    presenter = _presenter(
        _presentation(ClearAgentsPopoverState.SAVING),
        on_close=lambda: closed.append(None),
    )
    window = _host(presenter)
    try:
        assert presenter.focusable_controls() == ()
        assert presenter.root_view.nextKeyView() is presenter.root_view
        presenter.root_view.keyDown_(_KeyEvent(53, "\x1b"))
        presenter.root_view.keyDown_(_KeyEvent(36, "\r"))
        assert closed == []
    finally:
        window.close()

    receipt_presenter = _presenter(
        _presentation(
            ClearAgentsPopoverState.RECEIPT,
            clearable_count=0,
            cleared_count=2,
            labels=(),
        ),
        on_action=received.append,
        on_close=lambda: closed.append(None),
    )
    undo, done = _visible_buttons(receipt_presenter)
    undo.performClick_(None)
    done.performClick_(None)

    assert received[-2:] == [
        ClearAgentsPopoverAction.UNDO,
        ClearAgentsPopoverAction.DONE,
    ]
    assert closed == [None]


class _RecordingWindow:
    def __init__(self) -> None:
        self.events: list[tuple[str, object | None]] = []

    def makeKeyWindow(self) -> None:
        self.events.append(("key", None))

    def makeFirstResponder_(self, responder) -> bool:
        self.events.append(("focus", responder))
        return True


class _RecordingApplication:
    def __init__(self, events: list[tuple[str, object | None]]) -> None:
        self.events = events

    def activateIgnoringOtherApps_(self, flag: bool) -> None:
        self.events.append(("activate", flag))


def test_after_show_focus_contract_activates_keys_and_installs_first_responder() -> None:
    """Wrong activation order or missing explicit focus installation would fail."""
    presenter = _presenter()
    window = _RecordingWindow()
    events = window.events

    assert presenter.focus_shown_window(
        window,
        application=_RecordingApplication(events),
    )

    assert events == [
        ("activate", True),
        ("key", None),
        ("focus", _visible_buttons(presenter)[0]),
    ]
    assert presenter.popover.delegate() is presenter.close_delegate
    assert presenter.close_delegate is not presenter.action_target


def test_visible_buttons_follow_native_order_and_fit_their_titles() -> None:
    presenter = _presenter()
    cancel, primary = _visible_buttons(presenter)

    assert (cancel.title(), primary.title()) == ("Cancel", "Clear Presented Agents")
    assert cancel.frame().origin.x < primary.frame().origin.x
    assert str(cancel.keyEquivalent()) == ""
    assert str(primary.keyEquivalent()) == "\r"
    assert all(
        button.intrinsicContentSize().width <= button.frame().size.width
        for button in (cancel, primary)
    )
