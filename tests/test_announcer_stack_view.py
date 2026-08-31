from __future__ import annotations

from itertools import pairwise

import pytest

from sidepulse.announcer_stack import (
    AnnouncerAlert,
    AnnouncerAlertIdentity,
    AnnouncerAlertPriority,
    AnnouncerStackAction,
    AnnouncerStackIntent,
    AnnouncerStackPlan,
    AnnouncerStackVisibility,
)
from sidepulse.answer_in_place import (
    AnswerActionKind,
    AnswerAttemptState,
    AnswerCapability,
    AnswerControlPlan,
)
from sidepulse.provider_contracts import (
    AdapterIdentifier,
    LocalRuntimeSurfaceIdentifier,
    ProductCapability,
    ProductCapabilityInvocation,
    ProviderIdentifier,
    SourceInstanceIdentifier,
)


@pytest.fixture(autouse=True)
def _allow_explicit_pointer_keying_in_native_panel_tests(monkeypatch) -> None:
    from sidepulse import announcer_stack_view

    monkeypatch.setattr(
        announcer_stack_view,
        "desktop_takeover_suppressed",
        lambda: False,
        raising=False,
    )


def _signatures(intents: list[AnnouncerStackIntent]) -> list[tuple]:
    return [(intent.action, intent.generation, intent.selected_identity) for intent in intents]


def _plan(
    visibility: AnnouncerStackVisibility = AnnouncerStackVisibility.COLLAPSED,
    *,
    count: int = 1,
    generation: int = 17,
    selected_index: int = 0,
    question: str = "Approve access to the project?",
    accessibility_label: str = "Screen Bar announcer",
) -> AnnouncerStackPlan:
    alerts = tuple(
        AnnouncerAlert(
            identity=AnnouncerAlertIdentity(f"request:{index}"),
            agent_id=f"agent:{index}",
            provider="codex",
            source_label=f"Source {index + 1}",
            session_label=f"Session {index + 1}",
            question=question,
            priority=AnnouncerAlertPriority.PERMISSION,
            first_seen_sequence=index,
            seen_on_screen_bar=False,
        )
        for index in range(count)
    )
    selected = alerts[selected_index] if alerts else None
    return AnnouncerStackPlan(
        generation=generation,
        visibility=visibility,
        alerts=alerts,
        selected_index=selected_index if alerts else None,
        total_actionable_count=count,
        unseen_count=count,
        highest_priority_source=alerts[0].source_label if alerts else None,
        collapsed_text="Source 1: Approve access to the project?" if alerts else None,
        position_text=f"{selected_index + 1} of {count}" if selected else None,
        accessibility_label=accessibility_label,
        accessibility_value="Source 1, Permission request, 1 of 1, Approve access to the project?; 1 actionable, 1 unseen",
        accessibility_help="Click to open this asking session or expand the asks. Mark Seen affects only the Screen Bar; the LED notification remains active.",
        can_previous=count > 1,
        can_next=count > 1,
        can_open=selected is not None,
        can_mark_seen=selected is not None,
    )


def _answer_plan(
    *,
    request_identity: AnnouncerAlertIdentity | None = None,
    generation: int = 17,
    state: AnswerAttemptState = AnswerAttemptState.IDLE,
    draft_text: str = "",
    primary_actions: tuple[AnswerActionKind, ...] = (
        AnswerActionKind.APPROVE,
        AnswerActionKind.DENY,
        AnswerActionKind.JUMP,
    ),
    status_text: str | None = None,
    can_edit_reply: bool = False,
    can_send: bool = True,
    can_cancel: bool = False,
) -> AnswerControlPlan:
    return AnswerControlPlan(
        request_identity=request_identity or AnnouncerAlertIdentity("request:0"),
        generation=generation,
        capability=AnswerCapability(
            supported=True,
            supports_reply_text=can_edit_reply,
            supports_binary_decision=not can_edit_reply,
            invocation=ProductCapabilityInvocation(
                product_capability=ProductCapability.ANSWERING,
                provider_id=ProviderIdentifier("codex"),
                adapter_id=AdapterIdentifier("hooks"),
                source_instance_id=SourceInstanceIdentifier("source:main"),
                local_runtime_surface=LocalRuntimeSurfaceIdentifier(
                    "local.answer_in_place"
                ),
            ),
        ),
        state=state,
        draft_text=draft_text,
        primary_actions=primary_actions,
        secondary_actions=(),
        status_text=status_text,
        can_edit_reply=can_edit_reply,
        can_send=can_send,
        can_cancel=can_cancel,
    )


_ANSWER_LAYOUT_CASES = (
    (
        "binary_idle",
        AnswerAttemptState.IDLE,
        (AnswerActionKind.APPROVE, AnswerActionKind.DENY, AnswerActionKind.JUMP),
        None,
        "",
        False,
        True,
        False,
        ("Approve", "Deny", "Jump"),
    ),
    (
        "reply_idle_with_draft",
        AnswerAttemptState.IDLE,
        (AnswerActionKind.REPLY, AnswerActionKind.JUMP),
        None,
        "Please continue with the focused verification.",
        True,
        True,
        False,
        ("Send", "Jump"),
    ),
    (
        "sending",
        AnswerAttemptState.SENDING,
        (AnswerActionKind.CANCEL, AnswerActionKind.JUMP),
        "Sending…",
        "Please continue with the focused verification.",
        False,
        False,
        True,
        ("Cancel", "Jump"),
    ),
    (
        "failed",
        AnswerAttemptState.FAILED,
        (AnswerActionKind.RETRY, AnswerActionKind.JUMP),
        "Provider refused",
        "Please continue with the focused verification.",
        False,
        False,
        False,
        ("Retry", "Jump"),
    ),
    (
        "timed_out",
        AnswerAttemptState.TIMED_OUT,
        (AnswerActionKind.RETRY, AnswerActionKind.JUMP),
        "Timed out",
        "Please continue with the focused verification.",
        False,
        False,
        False,
        ("Retry", "Jump"),
    ),
    (
        "cancelled",
        AnswerAttemptState.CANCELLED,
        (AnswerActionKind.REPLY, AnswerActionKind.JUMP),
        "Cancelled",
        "Please continue with the focused verification.",
        True,
        True,
        False,
        ("Send", "Jump"),
    ),
    (
        "sent_waiting_for_source",
        AnswerAttemptState.SENT,
        (AnswerActionKind.JUMP,),
        "Sent, waiting for source confirmation",
        "Please continue with the focused verification.",
        False,
        False,
        False,
        ("Jump",),
    ),
    (
        "unsupported_jump_only",
        AnswerAttemptState.IDLE,
        (AnswerActionKind.JUMP,),
        "Jump to session",
        "",
        False,
        False,
        False,
        ("Jump",),
    ),
)


def test_collapsed_panel_is_passive_accessible_button_and_clicks_one_ask_open() -> None:
    """Removing the passive root or its open intent would make this fail."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    received = []
    panel = AnnouncerStackPanel()
    plan = _plan()
    panel.update(plan, received.append, center_x=500.0, top_y=700.0)

    assert panel.visibility is AnnouncerStackVisibility.COLLAPSED
    assert panel.root_view.accessibilityRole() == "AXButton"
    assert panel.root_view.accessibilityLabel() == "Screen Bar announcer"
    assert not panel.root_view.acceptsFirstResponder()
    assert not panel.root_view.subviews()[0].isAccessibilityElement()
    panel.root_view.mouseDown_(None)
    assert _signatures(received) == [
        (AnnouncerStackAction.OPEN, plan.generation, plan.alerts[0].identity)
    ]


def test_multiple_collapsed_asks_expand_and_key_commands_keep_projected_identity() -> None:
    """Wrong keyboard mapping or a live, post-reconcile identity would fail."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    received = []
    panel = AnnouncerStackPanel()
    plan = _plan(count=2, generation=23, selected_index=1)
    panel.update(plan, received.append, center_x=500.0, top_y=700.0)
    panel.root_view.mouseDown_(None)
    assert _signatures(received) == [(
        AnnouncerStackAction.EXPAND,
        23,
        plan.alerts[1].identity,
    )]
    received.clear()

    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=23, selected_index=1),
        received.append,
        center_x=500.0,
        top_y=700.0,
    )
    assert panel.window.canBecomeKeyWindow()
    assert panel.expanded_view.acceptsFirstResponder()
    assert panel.window.firstResponder() is panel.expanded_view
    for action, event in (
        (AnnouncerStackAction.PREVIOUS, (123, "")),
        (AnnouncerStackAction.NEXT, (125, "")),
        (AnnouncerStackAction.OPEN, (36, "\r")),
        (AnnouncerStackAction.MARK_SEEN, (49, " ")),
        (AnnouncerStackAction.COLLAPSE, (53, "\x1b")),
    ):
        panel.expanded_view.keyDown_(type("Event", (), {
            "keyCode": lambda _self, code=event[0]: code,
            "charactersIgnoringModifiers": lambda _self, chars=event[1]: chars,
        })())
        assert _signatures(received) == [(action, 23, plan.alerts[1].identity)]
        received.clear()


def test_expanded_panel_uses_native_group_and_exact_button_labels() -> None:
    """Replacing native controls or their spoken actions would make this fail."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    panel = AnnouncerStackPanel()
    plan = _plan(
        AnnouncerStackVisibility.EXPANDED,
        count=2,
        accessibility_label="Screen Bar asking sessions",
    )
    panel.update(
        plan,
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
    )
    assert panel.root_view.accessibilityRole() == "AXGroup"
    assert panel.root_view.accessibilityLabel() == plan.accessibility_label
    assert tuple(button.accessibilityLabel() for button in panel.buttons) == (
        "Previous Ask",
        "Next Ask",
        "Open Asking Session",
        "Mark Seen on Screen Bar",
        "Collapse Announcer",
    )
    assert all(button.acceptsFirstResponder() for button in panel.buttons)
    assert 320.0 <= panel.window.frame().size.width <= 460.0


def test_expanded_panel_renders_binary_answer_controls_without_changing_footer_controls() -> None:
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    panel = AnnouncerStackPanel()
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2),
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
        answer_plan=_answer_plan(),
    )

    assert tuple(button.title() for button in panel.answer_buttons if not button.isHidden()) == (
        "Approve",
        "Deny",
        "Jump",
    )
    assert panel.answer_reply_field.isHidden()
    assert tuple(button.accessibilityLabel() for button in panel.buttons) == (
        "Previous Ask",
        "Next Ask",
        "Open Asking Session",
        "Mark Seen on Screen Bar",
        "Collapse Announcer",
    )


def test_expanded_panel_renders_reply_send_cancel_retry_and_status_states() -> None:
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    panel = AnnouncerStackPanel()
    expanded = _plan(AnnouncerStackVisibility.EXPANDED, count=1)
    panel.update(
        expanded,
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
        answer_plan=_answer_plan(
            primary_actions=(AnswerActionKind.REPLY, AnswerActionKind.JUMP),
            can_edit_reply=True,
            can_send=False,
        ),
    )
    assert not panel.answer_reply_field.isHidden()
    assert panel.answer_reply_field.isEditable()
    assert tuple(button.title() for button in panel.answer_buttons if not button.isHidden()) == (
        "Send",
        "Jump",
    )
    assert not panel.answer_buttons[0].isEnabled()

    panel.update(
        expanded,
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
        answer_plan=_answer_plan(
            state=AnswerAttemptState.SENDING,
            draft_text="Ship it",
            primary_actions=(AnswerActionKind.CANCEL, AnswerActionKind.JUMP),
            status_text="Sending…",
            can_send=False,
            can_cancel=True,
        ),
    )
    assert panel.answer_reply_field.isHidden()
    assert panel.answer_status_field.stringValue() == "Sending…"
    assert tuple(button.title() for button in panel.answer_buttons if not button.isHidden()) == (
        "Cancel",
        "Jump",
    )

    panel.update(
        expanded,
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
        answer_plan=_answer_plan(
            state=AnswerAttemptState.FAILED,
            draft_text="Ship it",
            primary_actions=(AnswerActionKind.RETRY, AnswerActionKind.JUMP),
            status_text="Provider refused",
            can_send=False,
        ),
    )
    assert panel.answer_status_field.stringValue() == "Provider refused"
    assert tuple(button.title() for button in panel.answer_buttons if not button.isHidden()) == (
        "Retry",
        "Jump",
    )


@pytest.mark.parametrize(
    (
        "case_name",
        "state",
        "actions",
        "status_text",
        "draft_text",
        "can_edit_reply",
        "can_send",
        "can_cancel",
        "expected_titles",
    ),
    _ANSWER_LAYOUT_CASES,
    ids=tuple(case[0] for case in _ANSWER_LAYOUT_CASES),
)
def test_all_answer_states_use_a_nonoverlapping_native_row_and_coherent_key_order(
    case_name: str,
    state: AnswerAttemptState,
    actions: tuple[AnswerActionKind, ...],
    status_text: str | None,
    draft_text: str,
    can_edit_reply: bool,
    can_send: bool,
    can_cancel: bool,
    expected_titles: tuple[str, ...],
) -> None:
    """Every projected state must retain readable geometry and semantic order."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    panel = AnnouncerStackPanel()
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2),
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
        answer_plan=_answer_plan(
            state=state,
            draft_text=draft_text,
            primary_actions=actions,
            status_text=status_text,
            can_edit_reply=can_edit_reply,
            can_send=can_send,
            can_cancel=can_cancel,
        ),
    )

    visible_buttons = tuple(
        button for button in panel.answer_buttons if not button.isHidden()
    )
    assert tuple(button.title() for button in visible_buttons) == expected_titles
    assert panel.answer_reply_field.isHidden() is not can_edit_reply
    assert panel.answer_reply_field.isEditable() is can_edit_reply
    assert (
        None
        if panel.answer_status_field.isHidden()
        else panel.answer_status_field.stringValue()
    ) == status_text

    visible_row = []
    if not panel.answer_status_field.isHidden():
        visible_row.append(panel.answer_status_field)
    if not panel.answer_reply_field.isHidden():
        visible_row.append(panel.answer_reply_field)
    visible_row.extend(visible_buttons)
    visible_row.sort(key=lambda view: view.frame().origin.x)
    frames = tuple(view.frame() for view in visible_row)
    assert frames
    assert frames[0].origin.x >= 12.0
    assert frames[-1].origin.x + frames[-1].size.width == 348.0
    assert all(
        following.origin.x - (current.origin.x + current.size.width) == 8.0
        for current, following in pairwise(frames)
    ), case_name
    assert all(
        0.0 <= frame.origin.y
        and frame.origin.y + frame.size.height <= panel.root_view.bounds().size.height
        for frame in frames
    )
    assert all(
        button.intrinsicContentSize().width <= button.frame().size.width
        for button in visible_buttons
    )

    focus_views = []
    if can_edit_reply:
        focus_views.append(panel.answer_reply_field)
    focus_views.extend(button for button in visible_buttons if button.isEnabled())
    focus_views.extend(button for button in panel.buttons if button.isEnabled())
    assert panel._focus_views() == tuple(focus_views)
    assert panel.root_view.nextKeyView() is focus_views[0]
    for current, following in pairwise(focus_views):
        assert current.nextKeyView() is following
    assert focus_views[-1].nextKeyView() is focus_views[0]

    detail = panel.detail_field.frame()
    footer_top = max(
        button.frame().origin.y + button.frame().size.height for button in panel.buttons
    )
    answer_bottom = min(frame.origin.y for frame in frames)
    question_bottom = panel.question_field.frame().origin.y
    assert footer_top <= detail.origin.y
    assert detail.origin.y + detail.size.height <= answer_bottom
    assert max(frame.origin.y + frame.size.height for frame in frames) <= question_bottom


def test_stale_answer_control_cannot_emit_after_reconciliation() -> None:
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    received = []
    panel = AnnouncerStackPanel()
    old_plan = _plan(AnnouncerStackVisibility.EXPANDED, generation=71)
    panel.update(
        old_plan,
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
        answer_plan=_answer_plan(generation=71),
        answer_handler=lambda action, generation, identity, draft: received.append(
            (action, generation, identity, draft)
        ),
    )
    retained = type("RetainedButton", (), {
        "representedObject": lambda _self: (
            old_plan.generation,
            old_plan.alerts[0].identity,
            AnswerActionKind.APPROVE,
        )
    })()
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, generation=72),
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
        answer_plan=_answer_plan(generation=72),
        answer_handler=lambda action, generation, identity, draft: received.append(
            (action, generation, identity, draft)
        ),
    )
    panel._emit_answer_from_control(AnswerActionKind.APPROVE, retained)

    assert received == []

def test_real_native_open_control_emits_its_projected_intent() -> None:
    """A button disconnected from its native action would make this fail."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    received = []
    panel = AnnouncerStackPanel()
    plan = _plan(AnnouncerStackVisibility.EXPANDED, generation=29)
    panel.update(plan, received.append, center_x=500.0, top_y=700.0)
    panel.buttons[2].performClick_(None)
    assert _signatures(received) == [
        (AnnouncerStackAction.OPEN, 29, plan.alerts[0].identity)
    ]


def test_hidden_or_suppressed_plan_hides_and_resigns_key_status() -> None:
    """Leaving a key panel visible after suppression would make this fail."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    panel = AnnouncerStackPanel()
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED),
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
    )
    panel.update(_plan(AnnouncerStackVisibility.HIDDEN, count=0), None, center_x=500.0, top_y=700.0)
    assert panel.visibility is AnnouncerStackVisibility.HIDDEN
    assert not panel.window.isVisible()
    assert not panel.window.canBecomeKeyWindow()


def test_reconciled_panel_rejects_a_stale_native_button() -> None:
    """An old control must not act on the reconciled selection."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    received = []
    panel = AnnouncerStackPanel()
    old = _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=31)
    panel.update(old, received.append, center_x=500.0, top_y=700.0)
    stale_next = type("RetainedEvent", (), {
        "representedObject": lambda _self: (old.generation, old.alerts[0].identity),
    })()
    replacement = _plan(
        AnnouncerStackVisibility.EXPANDED,
        count=2,
        generation=32,
        selected_index=1,
    )
    panel.update(replacement, received.append, center_x=500.0, top_y=700.0)
    panel.next_(stale_next)
    assert received == []


def test_virtual_device_uses_one_shared_announcer_suppression_predicate(monkeypatch) -> None:
    """Dropping fullscreen from the gate would leave its presenter visible."""
    from sidepulse import virtual_device

    calls = []

    class Presenter:
        def update(self, plan, handler, **kwargs):
            calls.append((plan, handler, kwargs["allowed"]))

        def hide(self):
            calls.append(("hide",))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(virtual_device, "AnnouncerStackPanel", Presenter)
    device = virtual_device.VirtualStatusDevice.alloc().init()
    device.window = type("Window", (), {
        "isVisible": lambda _self: True,
        "frame": lambda _self: type("Frame", (), {
            "origin": type("Origin", (), {"x": 100.0, "y": 700.0})(),
            "size": type("Size", (), {"width": 220.0})(),
        })(),
    })()
    plan = _plan()
    device.set_announcer_stack(plan, lambda _intent: None)
    assert calls[-1][0] is plan
    assert calls[-1][2] is True

    device._fullscreen_hidden = True
    device._sync_announcer()
    assert calls[-1][0] is plan
    assert calls[-1][2] is False


def test_programmatic_expansion_stays_passive_until_collapsed_pointer_arms_it() -> None:
    """A reconciliation alone must never take keyboard focus."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    received = []
    panel = AnnouncerStackPanel()
    expanded = _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=41)
    panel.update(expanded, received.append, center_x=500.0, top_y=700.0)
    assert not panel.window.canBecomeKeyWindow()
    assert not panel.window.isKeyWindow()

    collapsed = _plan(AnnouncerStackVisibility.COLLAPSED, count=2, generation=42)
    panel.update(collapsed, received.append, center_x=500.0, top_y=700.0)
    panel.root_view.mouseDown_(None)
    assert _signatures(received) == [
        (AnnouncerStackAction.EXPAND, 42, collapsed.alerts[0].identity)
    ]
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=43),
        received.append,
        center_x=500.0,
        top_y=700.0,
    )
    assert panel.window.canBecomeKeyWindow()
    assert panel.window.firstResponder() is panel.expanded_view


def test_headless_suppression_blocks_pointer_authorized_key_acquisition(
    monkeypatch,
) -> None:
    from sidepulse import announcer_stack_view

    received = []
    panel = announcer_stack_view.AnnouncerStackPanel()
    collapsed = _plan(AnnouncerStackVisibility.COLLAPSED, count=2, generation=44)
    panel.update(collapsed, received.append, center_x=500.0, top_y=700.0)
    panel.root_view.mouseDown_(None)
    monkeypatch.setattr(
        announcer_stack_view,
        "desktop_takeover_suppressed",
        lambda: True,
    )

    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=45),
        received.append,
        center_x=500.0,
        top_y=700.0,
    )

    assert panel.window.canBecomeKeyWindow()
    assert not panel.window.isKeyWindow()
    assert panel.window.firstResponder() is not panel.expanded_view


def test_collapsed_reconciliation_revokes_pointer_authorization_before_later_expansion() -> None:
    """A later programmatic expansion needs a fresh collapsed pointer click."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    panel = AnnouncerStackPanel()
    collapsed = _plan(AnnouncerStackVisibility.COLLAPSED, count=2, generation=46)
    panel.update(collapsed, lambda _intent: None, center_x=500.0, top_y=700.0)
    panel.root_view.mouseDown_(None)
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=47),
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
    )
    assert panel.window.canBecomeKeyWindow()
    panel.update(
        _plan(AnnouncerStackVisibility.COLLAPSED, count=2, generation=48),
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
    )
    assert not panel._pointer_authorized
    assert not panel.window.canBecomeKeyWindow()
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=49),
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
    )
    assert not panel.window.canBecomeKeyWindow()


def test_suppressed_retained_control_cannot_emit_an_intent() -> None:
    """A queued control must be inert as soon as presentation is suppressed."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    received = []
    panel = AnnouncerStackPanel()
    collapsed = _plan(AnnouncerStackVisibility.COLLAPSED, count=2, generation=51)
    panel.update(collapsed, received.append, center_x=500.0, top_y=700.0)
    panel.root_view.mouseDown_(None)
    received.clear()
    plan = _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=52)
    panel.update(plan, received.append, center_x=500.0, top_y=700.0)
    retained_open = panel.buttons[2]
    panel.update(plan, received.append, center_x=500.0, top_y=700.0, allowed=False)
    retained_open.performClick_(None)
    assert received == []


def test_stale_collapsed_pointer_cannot_authorize_a_programmatic_expansion() -> None:
    """An old mouse event must not turn a passive reconciled panel keyable."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    received = []
    panel = AnnouncerStackPanel()
    old = _plan(AnnouncerStackVisibility.COLLAPSED, count=2, generation=56)
    panel.update(old, received.append, center_x=500.0, top_y=700.0)
    stale_root = panel.root_view
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=57),
        received.append,
        center_x=500.0,
        top_y=700.0,
    )
    stale_root.mouseDown_(None)
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=58),
        received.append,
        center_x=500.0,
        top_y=700.0,
    )
    assert received == []
    assert not panel.window.canBecomeKeyWindow()


def test_expanded_reconciliation_reuses_controls_and_keeps_tab_focus() -> None:
    """Replacing the hierarchy would lose an in-progress native tab traversal."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    panel = AnnouncerStackPanel()
    collapsed = _plan(AnnouncerStackVisibility.COLLAPSED, count=2, generation=61)
    panel.update(collapsed, lambda _intent: None, center_x=500.0, top_y=700.0)
    panel.root_view.mouseDown_(None)
    first = _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=62)
    panel.update(first, lambda _intent: None, center_x=500.0, top_y=700.0)
    root = panel.expanded_view
    buttons = panel.buttons
    tab = type("TabEvent", (), {
        "keyCode": lambda _self: 48,
        "charactersIgnoringModifiers": lambda _self: "\t",
        "modifierFlags": lambda _self: 0,
    })()
    root.keyDown_(tab)
    assert panel.window.firstResponder() is buttons[0]
    buttons[0].keyDown_(tab)
    assert panel.window.firstResponder() is buttons[1]
    second = _plan(AnnouncerStackVisibility.EXPANDED, count=2, generation=63, selected_index=1)
    panel.update(second, lambda _intent: None, center_x=500.0, top_y=700.0)
    assert panel.expanded_view is root
    assert panel.buttons == buttons
    assert panel.window.firstResponder() is buttons[1]


def test_question_field_uses_two_line_tail_truncation_without_view_slicing() -> None:
    """The native cell, not an extra presenter cap, owns visual truncation."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    panel = AnnouncerStackPanel()
    plan = _plan(AnnouncerStackVisibility.EXPANDED, question="x" * 80)
    panel.update(plan, lambda _intent: None, center_x=500.0, top_y=700.0)
    assert panel.question_field.stringValue() == "x" * 80
    assert panel.question_field.maximumNumberOfLines() == 2
    assert not panel.question_field.usesSingleLineMode()
    assert panel.question_field.cell().wraps()
    assert panel.question_field.cell().truncatesLastVisibleLine()


def test_native_roots_have_solid_semantic_surfaces() -> None:
    """Text must never float directly over arbitrary desktop pixels."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    panel = AnnouncerStackPanel()
    panel.update(_plan(), lambda _intent: None, center_x=500.0, top_y=700.0)
    assert panel.root_view.surface_color is not None
    assert panel.root_view.border_color is not None


def test_visible_panel_presentation_uses_desktop_takeover_gate(monkeypatch) -> None:
    """Source-native tests must not raise the panel onto the owner's desktop."""
    from sidepulse import announcer_stack_view

    calls = []
    monkeypatch.setattr(
        announcer_stack_view,
        "present_window",
        lambda window, *, key: calls.append((window, key)),
        raising=False,
    )
    panel = announcer_stack_view.AnnouncerStackPanel()

    panel.update(_plan(), lambda _intent: None, center_x=500.0, top_y=700.0)

    assert calls == [(panel.window, False)]


def test_fixed_dark_surfaces_use_fixed_light_foregrounds_and_eight_point_controls() -> None:
    """Aqua must retain readable foregrounds on the fixed dark panel."""
    from sidepulse.announcer_stack_view import AnnouncerStackPanel

    panel = AnnouncerStackPanel()
    collapsed = _plan(AnnouncerStackVisibility.COLLAPSED, count=2)
    panel.update(collapsed, lambda _intent: None, center_x=500.0, top_y=700.0)
    panel.root_view.mouseDown_(None)
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2),
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
    )
    primary = panel.source_field.textColor().getRed_green_blue_alpha_(None, None, None, None)
    secondary = panel.session_field.textColor().getRed_green_blue_alpha_(None, None, None, None)
    assert min(primary[:3]) >= 0.88
    assert min(secondary[:3]) >= 0.64
    frames = tuple(button.frame() for button in panel.buttons)
    assert all(
        following.origin.x - (current.origin.x + current.size.width) == 8.0
        for current, following in pairwise(frames)
    )
    assert frames[0].origin.x == 20.0
    assert frames[-1].origin.x + frames[-1].size.width == 340.0
    assert tuple(button.title() for button in panel.buttons[:2]) == ("", "")
    assert all(button.image() is not None for button in panel.buttons[:2])
    assert all(
        button.intrinsicContentSize().width <= button.frame().size.width
        for button in panel.buttons[2:]
    )
    panel.root_view.mouseDown_(None)
    panel.update(
        _plan(AnnouncerStackVisibility.EXPANDED, count=2),
        lambda _intent: None,
        center_x=500.0,
        top_y=700.0,
    )
    assert panel.root_view.surface_color is not None
    assert panel.root_view.border_color is not None
