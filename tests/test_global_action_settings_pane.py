from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSView,
    NSWindow,
    NSWindowStyleMaskTitled,
)

from sidepulse.global_action_controller import GlobalActionChangeResult
from sidepulse.global_action_settings_pane import (
    GLOBAL_ACTION_GROUP_LABEL,
    GLOBAL_ACTION_RECORDER_HELP,
    GLOBAL_ACTION_RECORDER_LABEL,
    GLOBAL_ACTION_STATUS_LABEL,
    GlobalActionRecorderPresentation,
    GlobalActionRecorderState,
    GlobalActionSettingsPane,
    build_global_action_settings_pane,
    refresh_global_action_settings_controls,
)
from sidepulse.global_actions import (
    GlobalActionID,
    PersistedShortcutRefusal,
    ShortcutChord,
    ShortcutModifier,
    ShortcutValidationCode,
    parse_global_action_shortcuts,
    serialize_global_action_shortcuts,
)
from sidepulse.global_hotkeys import HotkeyRegistrationRefusal
from sidepulse.settings import AgentMonitorSettings


def _chord(
    key_code: int = 40,
    key_label: str = "K",
    *modifiers: ShortcutModifier,
) -> ShortcutChord:
    return ShortcutChord(
        key_code=key_code,
        key_label=key_label,
        modifiers=frozenset(modifiers or (ShortcutModifier.CONTROL,)),
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
    state: GlobalActionRecorderState,
    chord: ShortcutChord | None = None,
    *,
    detail: str | None = None,
) -> GlobalActionRecorderPresentation:
    return GlobalActionRecorderPresentation(
        state=state,
        committed_chord=chord,
        detail=detail,
    )


def _pane(
    *,
    presentation: GlobalActionRecorderPresentation | None = None,
    on_candidate: Callable[[ShortcutChord], GlobalActionRecorderPresentation]
    | None = None,
    on_clear: Callable[[], GlobalActionRecorderPresentation] | None = None,
    on_retry: Callable[[], GlobalActionRecorderPresentation] | None = None,
) -> GlobalActionSettingsPane:
    initial = presentation or _presentation(GlobalActionRecorderState.UNSET)
    return GlobalActionSettingsPane.create(
        presentation=initial,
        on_candidate=on_candidate
        or (
            lambda candidate: _presentation(
                GlobalActionRecorderState.ACTIVE,
                candidate,
            )
        ),
        on_clear=on_clear
        or (lambda: _presentation(GlobalActionRecorderState.CLEARED)),
        on_retry=on_retry or (lambda: initial),
    )


def _host(pane: GlobalActionSettingsPane) -> tuple[NSWindow, NSButton]:
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0.0, 0.0), (620.0, 240.0)),
        NSWindowStyleMaskTitled,
        NSBackingStoreBuffered,
        False,
    )
    container = NSView.alloc().initWithFrame_(((0.0, 0.0), (620.0, 240.0)))
    pane.view.setFrame_(((20.0, 40.0), (580.0, 170.0)))
    prior = NSButton.alloc().initWithFrame_(((20.0, 8.0), (100.0, 28.0)))
    prior.setTitle_("Prior")
    container.addSubview_(pane.view)
    container.addSubview_(prior)
    window.setContentView_(container)
    return window, prior


class _RejectingResponder(NSView):
    def acceptsFirstResponder(self) -> bool:
        return True

    def becomeFirstResponder(self) -> bool:
        return False


@pytest.mark.parametrize(
    ("state", "chord", "detail", "value", "status", "retry_visible"),
    [
        (
            GlobalActionRecorderState.UNSET,
            None,
            None,
            "Not set",
            "No shortcut assigned.",
            False,
        ),
        (
            GlobalActionRecorderState.RECORDING,
            None,
            None,
            "Recording",
            "Press a shortcut with Command or Control.",
            False,
        ),
        (
            GlobalActionRecorderState.ACTIVE,
            _chord(),
            None,
            "⌃K",
            "Shortcut active.",
            False,
        ),
        (
            GlobalActionRecorderState.LOCAL_CONFLICT,
            _chord(),
            "Open Agent Browser",
            "⌃K",
            "Already used by Open Agent Browser. The previous binding is unchanged.",
            False,
        ),
        (
            GlobalActionRecorderState.REGISTRATION_REFUSED,
            _chord(),
            None,
            "⌃K",
            "macOS refused shortcut registration. The previous binding is unchanged.",
            True,
        ),
        (
            GlobalActionRecorderState.SAVE_FAILURE,
            _chord(),
            None,
            "⌃K",
            "Could not save shortcut. The previous binding is unchanged.",
            True,
        ),
        (
            GlobalActionRecorderState.CLEARED,
            None,
            None,
            "Not set",
            "Shortcut cleared.",
            False,
        ),
    ],
)
def test_refresh_projects_every_recorder_state_into_retained_controls(
    state: GlobalActionRecorderState,
    chord: ShortcutChord | None,
    detail: str | None,
    value: str,
    status: str,
    retry_visible: bool,
) -> None:
    pane = _pane()
    controls = (pane.recorder, pane.status_field, pane.record_button, pane.clear_button)

    pane.refresh(_presentation(state, chord, detail=detail))

    assert (pane.recorder, pane.status_field, pane.record_button, pane.clear_button) == controls
    assert pane.recorder.accessibilityValue() == value
    assert pane.recorder.displayed_value == value
    assert pane.status_field.stringValue() == status
    assert pane.status_field.accessibilityValue() == status
    assert pane.retry_button.isHidden() is not retry_visible


def test_pane_exposes_exact_accessibility_group_recorder_help_and_status() -> None:
    pane = _pane()

    assert pane.view.isAccessibilityElement()
    assert pane.view.accessibilityRole() == "AXGroup"
    assert pane.view.accessibilityLabel() == GLOBAL_ACTION_GROUP_LABEL == "Global actions"
    assert (
        pane.recorder.accessibilityLabel()
        == GLOBAL_ACTION_RECORDER_LABEL
        == "Reveal current ask shortcut"
    )
    assert (
        pane.recorder.accessibilityHelp()
        == GLOBAL_ACTION_RECORDER_HELP
        == "Reveals the current ask or Agent Browser without observing ordinary typing."
    )
    assert pane.status_field.accessibilityLabel() == GLOBAL_ACTION_STATUS_LABEL
    assert pane.status_field.accessibilityValue() == "No shortcut assigned."


def test_key_view_loop_is_stable_and_includes_visible_controls() -> None:
    pane = _pane(presentation=_presentation(GlobalActionRecorderState.ACTIVE, _chord()))

    assert pane.view.nextKeyView() is pane.recorder
    focus_views = (pane.recorder, pane.record_button, pane.clear_button)
    for current, following in pairwise(focus_views):
        assert current.nextKeyView() is following
    assert focus_views[-1].nextKeyView() is focus_views[0]

    pane.refresh(_presentation(GlobalActionRecorderState.SAVE_FAILURE, _chord()))
    assert pane.clear_button.nextKeyView() is pane.retry_button
    assert pane.retry_button.nextKeyView() is pane.recorder


def test_record_moves_focus_in_and_escape_restores_the_prior_responder() -> None:
    pane = _pane(presentation=_presentation(GlobalActionRecorderState.ACTIVE, _chord()))
    window, prior = _host(pane)
    try:
        assert window.makeFirstResponder_(prior)

        pane.record_button.performClick_(None)

        assert window.firstResponder() is pane.recorder
        assert pane.recorder.accessibilityValue() == "Recording"
        pane.recorder.keyDown_(_KeyEvent(53, "\x1b"))
        assert window.firstResponder() is prior
        assert pane.recorder.accessibilityValue() == "⌃K"
        assert pane.status_field.stringValue() == "Shortcut active."
    finally:
        window.close()


@pytest.mark.parametrize("prior_condition", ["missing", "refuses_focus"])
@pytest.mark.parametrize(
    "terminal_outcome",
    ["success", "cancel", "clear", "conflict", "refusal", "save_failure"],
)
def test_every_terminal_outcome_focuses_record_when_prior_cannot_be_restored(
    prior_condition: str,
    terminal_outcome: str,
) -> None:
    previous = _chord()

    def candidate_result(candidate: ShortcutChord) -> GlobalActionRecorderPresentation:
        if terminal_outcome == "success":
            return _presentation(GlobalActionRecorderState.ACTIVE, candidate)
        if terminal_outcome == "conflict":
            return _presentation(
                GlobalActionRecorderState.LOCAL_CONFLICT,
                previous,
                detail="Open Agent Browser",
            )
        if terminal_outcome == "refusal":
            return _presentation(
                GlobalActionRecorderState.REGISTRATION_REFUSED,
                previous,
            )
        return _presentation(GlobalActionRecorderState.SAVE_FAILURE, previous)

    pane = _pane(
        presentation=_presentation(GlobalActionRecorderState.ACTIVE, previous),
        on_candidate=candidate_result,
        on_clear=lambda: _presentation(GlobalActionRecorderState.CLEARED),
    )
    window, prior = _host(pane)
    try:
        assert window.makeFirstResponder_(prior)
        pane.record_button.performClick_(None)
        if prior_condition == "missing":
            pane._prior_first_responder = None
        else:
            pane._prior_first_responder = _RejectingResponder.alloc().initWithFrame_(
                ((0.0, 0.0), (10.0, 10.0))
            )

        if terminal_outcome == "cancel":
            pane.recorder.keyDown_(_KeyEvent(53, "\x1b"))
        elif terminal_outcome == "clear":
            pane.recorder.keyDown_(_KeyEvent(51, "\x7f"))
        else:
            pane.recorder.keyDown_(
                _KeyEvent(45, "n", NSEventModifierFlagControl)
            )

        assert not pane.recorder.recording
        assert window.firstResponder() is pane.record_button
    finally:
        window.close()


def test_valid_modified_key_delegates_validation_and_emits_one_candidate() -> None:
    received: list[ShortcutChord] = []

    def accept(candidate: ShortcutChord) -> GlobalActionRecorderPresentation:
        received.append(candidate)
        return _presentation(GlobalActionRecorderState.ACTIVE, candidate)

    pane = _pane(on_candidate=accept)
    window, prior = _host(pane)
    try:
        assert window.makeFirstResponder_(prior)
        pane.record_button.performClick_(None)

        pane.recorder.keyDown_(
            _KeyEvent(
                40,
                "k",
                NSEventModifierFlagControl
                | NSEventModifierFlagOption
                | NSEventModifierFlagShift
                | NSEventModifierFlagCommand,
            )
        )

        assert received == [
            _chord(
                40,
                "K",
                ShortcutModifier.CONTROL,
                ShortcutModifier.OPTION,
                ShortcutModifier.SHIFT,
                ShortcutModifier.COMMAND,
            )
        ]
        assert window.firstResponder() is prior
        assert pane.recorder.accessibilityValue() == "⌃⌥⇧⌘K"
    finally:
        window.close()


def test_unmodified_and_reserved_keys_are_rejected_by_global_action_validation() -> None:
    received: list[ShortcutChord] = []
    pane = _pane(on_candidate=lambda candidate: received.append(candidate))
    window, _prior = _host(pane)
    try:
        pane.record_button.performClick_(None)

        pane.recorder.keyDown_(_KeyEvent(40, "k"))
        assert received == []
        assert pane.recorder.accessibilityValue() == "Recording"
        assert pane.status_field.stringValue() == "Include Command or Control."

        pane.recorder.keyDown_(_KeyEvent(12, "q", NSEventModifierFlagCommand))
        assert received == []
        assert pane.recorder.accessibilityValue() == "Recording"
        assert pane.status_field.stringValue() == (
            "That shortcut is reserved by JR-Bar. Press another shortcut."
        )
    finally:
        window.close()


@pytest.mark.parametrize(("key_code", "characters"), [(51, "\x7f"), (117, "\uf728")])
def test_delete_or_backspace_clears_only_after_recording_begins(
    key_code: int,
    characters: str,
) -> None:
    clear_count = 0

    def clear() -> GlobalActionRecorderPresentation:
        nonlocal clear_count
        clear_count += 1
        return _presentation(GlobalActionRecorderState.CLEARED)

    pane = _pane(
        presentation=_presentation(GlobalActionRecorderState.ACTIVE, _chord()),
        on_clear=clear,
    )
    delegated: list[object] = []
    pane.recorder.delegate_unhandled_key = delegated.append
    event = _KeyEvent(key_code, characters)

    pane.recorder.keyDown_(event)
    assert clear_count == 0
    assert delegated == [event]

    window, prior = _host(pane)
    try:
        assert window.makeFirstResponder_(prior)
        pane.record_button.performClick_(None)
        pane.recorder.keyDown_(event)

        assert clear_count == 1
        assert pane.recorder.accessibilityValue() == "Not set"
        assert pane.status_field.stringValue() == "Shortcut cleared."
        assert window.firstResponder() is prior
    finally:
        window.close()


@pytest.mark.parametrize(
    ("result_state", "detail", "expected_status"),
    [
        (
            GlobalActionRecorderState.LOCAL_CONFLICT,
            "Open Agent Browser",
            "Already used by Open Agent Browser. The previous binding is unchanged.",
        ),
        (
            GlobalActionRecorderState.REGISTRATION_REFUSED,
            None,
            "macOS refused shortcut registration. The previous binding is unchanged.",
        ),
    ],
)
def test_candidate_refusals_exit_recording_and_preserve_previous_binding(
    result_state: GlobalActionRecorderState,
    detail: str | None,
    expected_status: str,
) -> None:
    previous = _chord()
    pane = _pane(
        presentation=_presentation(GlobalActionRecorderState.ACTIVE, previous),
        on_candidate=lambda _candidate: _presentation(
            result_state,
            previous,
            detail=detail,
        ),
    )
    window, prior = _host(pane)
    try:
        assert window.makeFirstResponder_(prior)
        pane.record_button.performClick_(None)
        pane.recorder.keyDown_(_KeyEvent(45, "n", NSEventModifierFlagControl))

        assert window.firstResponder() is prior
        assert pane.recorder.accessibilityValue() == "⌃K"
        assert pane.status_field.stringValue() == expected_status
    finally:
        window.close()


def test_typed_candidate_save_failure_restores_previous_binding() -> None:
    previous = _chord()

    def fail_save(_candidate: ShortcutChord) -> GlobalActionRecorderPresentation:
        return _presentation(GlobalActionRecorderState.SAVE_FAILURE, previous)

    pane = _pane(
        presentation=_presentation(GlobalActionRecorderState.ACTIVE, previous),
        on_candidate=fail_save,
    )
    window, prior = _host(pane)
    try:
        assert window.makeFirstResponder_(prior)
        pane.record_button.performClick_(None)
        pane.recorder.keyDown_(_KeyEvent(45, "n", NSEventModifierFlagControl))

        assert window.firstResponder() is prior
        assert pane.recorder.accessibilityValue() == "⌃K"
        assert pane.status_field.stringValue() == (
            "Could not save shortcut. The previous binding is unchanged."
        )
    finally:
        window.close()


@pytest.mark.parametrize("callback_name", ["candidate", "clear", "retry"])
def test_unexpected_callback_exceptions_propagate(callback_name: str) -> None:
    previous = _chord()

    def integration_bug(*_args) -> GlobalActionRecorderPresentation:
        raise RuntimeError(f"{callback_name} integration bug")

    pane = _pane(
        presentation=_presentation(GlobalActionRecorderState.ACTIVE, previous),
        on_candidate=integration_bug if callback_name == "candidate" else None,
        on_clear=integration_bug if callback_name == "clear" else None,
        on_retry=integration_bug if callback_name == "retry" else None,
    )
    if callback_name == "candidate":
        window, _prior = _host(pane)
        pane.record_button.performClick_(None)
    else:
        window = None

    try:
        with pytest.raises(RuntimeError, match=f"{callback_name} integration bug"):
            if callback_name == "candidate":
                pane.recorder.keyDown_(
                    _KeyEvent(45, "n", NSEventModifierFlagControl)
                )
            elif callback_name == "clear":
                pane.clearShortcut_(None)
            else:
                pane.retryShortcut_(None)
        assert pane.status_field.stringValue() == "Shortcut active."
    finally:
        if window is not None:
            window.close()


@pytest.mark.parametrize("callback_name", ["candidate", "clear", "retry"])
@pytest.mark.parametrize("malformed", [None, object()])
def test_malformed_callback_refresh_results_propagate(
    callback_name: str,
    malformed: object,
) -> None:
    previous = _chord()

    def malformed_result(*_args):
        return malformed

    pane = _pane(
        presentation=_presentation(GlobalActionRecorderState.ACTIVE, previous),
        on_candidate=malformed_result if callback_name == "candidate" else None,
        on_clear=malformed_result if callback_name == "clear" else None,
        on_retry=malformed_result if callback_name == "retry" else None,
    )
    if callback_name == "candidate":
        window, _prior = _host(pane)
        pane.record_button.performClick_(None)
    else:
        window = None

    try:
        with pytest.raises(
            ValueError,
            match="global action recorder presentation is invalid",
        ):
            if callback_name == "candidate":
                pane.recorder.keyDown_(
                    _KeyEvent(45, "n", NSEventModifierFlagControl)
                )
            elif callback_name == "clear":
                pane.clearShortcut_(None)
            else:
                pane.retryShortcut_(None)
        assert pane.status_field.stringValue() == "Shortcut active."
    finally:
        if window is not None:
            window.close()


def test_direct_malformed_refresh_input_propagates() -> None:
    pane = _pane()

    with pytest.raises(
        ValueError,
        match="global action recorder presentation is invalid",
    ):
        pane.refresh(object())  # type: ignore[arg-type]


def test_clear_and_retry_callbacks_exit_recording_and_refresh_in_place() -> None:
    previous = _chord()
    retried = 0

    def refuse_clear() -> GlobalActionRecorderPresentation:
        return _presentation(GlobalActionRecorderState.SAVE_FAILURE, previous)

    def retry() -> GlobalActionRecorderPresentation:
        nonlocal retried
        retried += 1
        return _presentation(GlobalActionRecorderState.ACTIVE, previous)

    pane = _pane(
        presentation=_presentation(GlobalActionRecorderState.ACTIVE, previous),
        on_clear=refuse_clear,
        on_retry=retry,
    )
    window, prior = _host(pane)
    try:
        assert window.makeFirstResponder_(prior)
        pane.record_button.performClick_(None)
        pane.clear_button.performClick_(None)
        assert window.firstResponder() is prior
        assert pane.recorder.accessibilityValue() == "⌃K"
        assert not pane.retry_button.isHidden()

        pane.retry_button.performClick_(None)
        assert retried == 1
        assert pane.recorder.accessibilityValue() == "⌃K"
        assert pane.retry_button.isHidden()
    finally:
        window.close()


def test_refresh_replaces_callbacks_without_rebuilding_or_using_stale_closures() -> None:
    old: list[ShortcutChord] = []
    new: list[ShortcutChord] = []

    def receive_old(candidate: ShortcutChord) -> GlobalActionRecorderPresentation:
        old.append(candidate)
        return _presentation(GlobalActionRecorderState.ACTIVE, candidate)

    def receive_new(candidate: ShortcutChord) -> GlobalActionRecorderPresentation:
        new.append(candidate)
        return _presentation(GlobalActionRecorderState.ACTIVE, candidate)

    pane = _pane(on_candidate=receive_old)
    controls = (pane.recorder, pane.status_field, pane.record_button, pane.clear_button)
    pane.refresh(
        _presentation(GlobalActionRecorderState.UNSET),
        on_candidate=receive_new,
    )
    window, _prior = _host(pane)
    try:
        pane.record_button.performClick_(None)
        pane.recorder.keyDown_(_KeyEvent(40, "k", NSEventModifierFlagControl))

        assert old == []
        assert new == [_chord()]
        assert (pane.recorder, pane.status_field, pane.record_button, pane.clear_button) == controls
    finally:
        window.close()


def test_non_recording_keydown_delegates_without_consuming_text_input() -> None:
    pane = _pane()
    event = _KeyEvent(0, "a")
    delegated: list[object] = []
    pane.recorder.delegate_unhandled_key = delegated.append

    pane.recorder.keyDown_(event)

    assert delegated == [event]


def test_source_uses_no_event_monitor_or_cgeventtap() -> None:
    source = (
        Path(__file__).parents[1] / "src/sidepulse/global_action_settings_pane.py"
    ).read_text()

    assert "addGlobalMonitorForEvents" not in source
    assert "addLocalMonitorForEvents" not in source
    assert "CGEventTap" not in source


class _Registry:
    def __init__(self, chord: ShortcutChord | None = None) -> None:
        self.active_bindings = (
            {GlobalActionID.REVEAL_CURRENT_ASK: chord} if chord is not None else {}
        )


class _Lifecycle:
    def __init__(
        self,
        target: SimpleNamespace,
        chord: ShortcutChord | None = None,
    ) -> None:
        self.target = target
        self.registry = _Registry(chord)
        self.results: list[GlobalActionChangeResult] = []
        self.calls: list[tuple[object, ...]] = []

    def _result(self) -> GlobalActionChangeResult:
        return self.results.pop(0) if self.results else GlobalActionChangeResult(True)

    def _apply(self, chord: ShortcutChord | None) -> None:
        bindings = (
            {GlobalActionID.REVEAL_CURRENT_ASK: chord} if chord is not None else {}
        )
        self.target.settings = replace(
            self.target.settings,
            global_action_shortcuts=serialize_global_action_shortcuts(bindings),
        )
        self.registry.active_bindings = bindings

    def set_shortcut(
        self,
        action: GlobalActionID,
        chord: ShortcutChord,
    ) -> GlobalActionChangeResult:
        self.calls.append(("set", action, chord))
        result = self._result()
        if result.applied:
            self._apply(chord)
        return result

    def clear_shortcut(self, action: GlobalActionID) -> GlobalActionChangeResult:
        self.calls.append(("clear", action))
        result = self._result()
        if result.applied:
            self._apply(None)
        return result

    def refresh_from_settings(self) -> GlobalActionChangeResult:
        self.calls.append(("refresh",))
        result = self._result()
        if result.applied:
            chord = parse_global_action_shortcuts(
                self.target.settings.global_action_shortcuts
            ).binding_for(GlobalActionID.REVEAL_CURRENT_ASK)
            self.registry.active_bindings = (
                {GlobalActionID.REVEAL_CURRENT_ASK: chord}
                if chord is not None
                else {}
            )
        return result


def _integrated_target(
    *,
    persisted: ShortcutChord | None = None,
    active: ShortcutChord | None = None,
) -> tuple[SimpleNamespace, _Lifecycle]:
    bindings = (
        {GlobalActionID.REVEAL_CURRENT_ASK: persisted}
        if persisted is not None
        else {}
    )
    target = SimpleNamespace(
        settings=replace(
            AgentMonitorSettings(),
            global_action_shortcuts=serialize_global_action_shortcuts(bindings),
        ),
        settings_fields={},
    )
    lifecycle = _Lifecycle(target, active)
    target.global_action_lifecycle = lifecycle
    return target, lifecycle


def test_integrated_set_projects_success_from_committed_settings_and_registry() -> None:
    previous = _chord()
    candidate = _chord(45, "N", ShortcutModifier.COMMAND)
    target, lifecycle = _integrated_target(persisted=previous, active=previous)
    pane = build_global_action_settings_pane(target)

    pane._submit_candidate(candidate)

    assert lifecycle.calls == [
        ("set", GlobalActionID.REVEAL_CURRENT_ASK, candidate)
    ]
    assert pane._presentation == _presentation(
        GlobalActionRecorderState.ACTIVE,
        candidate,
    )
    assert pane.pending_operation is None


@pytest.mark.parametrize(
    ("failure", "state"),
    [
        (
            GlobalActionChangeResult(
                False,
                refusal=HotkeyRegistrationRefusal.from_os_status(-9878),
            ),
            GlobalActionRecorderState.REGISTRATION_REFUSED,
        ),
        (
            GlobalActionChangeResult(False, save_failure="write_refused"),
            GlobalActionRecorderState.SAVE_FAILURE,
        ),
    ],
)
def test_failed_set_preserves_previous_chord_and_retry_repeats_exact_candidate(
    failure: GlobalActionChangeResult,
    state: GlobalActionRecorderState,
) -> None:
    previous = _chord()
    candidate = _chord(45, "N", ShortcutModifier.COMMAND)
    target, lifecycle = _integrated_target(persisted=previous, active=previous)
    lifecycle.results = [failure, GlobalActionChangeResult(True)]
    pane = build_global_action_settings_pane(target)

    pane._submit_candidate(candidate)

    assert pane._presentation == _presentation(state, previous)
    assert pane.pending_operation is not None
    pane.retryShortcut_(None)
    assert lifecycle.calls == [
        ("set", GlobalActionID.REVEAL_CURRENT_ASK, candidate),
        ("set", GlobalActionID.REVEAL_CURRENT_ASK, candidate),
    ]
    assert pane._presentation == _presentation(
        GlobalActionRecorderState.ACTIVE,
        candidate,
    )
    assert pane.pending_operation is None


@pytest.mark.parametrize(
    ("failure", "state"),
    [
        (
            GlobalActionChangeResult(
                False,
                refusal=HotkeyRegistrationRefusal.from_os_status(-9878),
            ),
            GlobalActionRecorderState.REGISTRATION_REFUSED,
        ),
        (
            GlobalActionChangeResult(False, save_failure="concurrent_write"),
            GlobalActionRecorderState.SAVE_FAILURE,
        ),
    ],
)
def test_failed_clear_preserves_previous_chord_and_retry_repeats_clear(
    failure: GlobalActionChangeResult,
    state: GlobalActionRecorderState,
) -> None:
    previous = _chord()
    target, lifecycle = _integrated_target(persisted=previous, active=previous)
    lifecycle.results = [failure, GlobalActionChangeResult(True)]
    pane = build_global_action_settings_pane(target)

    pane.clearShortcut_(None)

    assert pane._presentation == _presentation(state, previous)
    pane.retryShortcut_(None)
    assert lifecycle.calls == [
        ("clear", GlobalActionID.REVEAL_CURRENT_ASK),
        ("clear", GlobalActionID.REVEAL_CURRENT_ASK),
    ]
    assert pane._presentation == _presentation(GlobalActionRecorderState.CLEARED)
    assert pane.pending_operation is None


def test_startup_registration_mismatch_retries_refresh_from_settings() -> None:
    persisted = _chord()
    target, lifecycle = _integrated_target(persisted=persisted, active=None)
    pane = build_global_action_settings_pane(target)

    assert pane._presentation == _presentation(
        GlobalActionRecorderState.REGISTRATION_REFUSED,
        persisted,
    )
    assert pane.pending_operation is not None
    pane.retryShortcut_(None)
    assert lifecycle.calls == [("refresh",)]
    assert pane._presentation == _presentation(
        GlobalActionRecorderState.ACTIVE,
        persisted,
    )


def test_startup_malformed_persisted_shortcut_projects_visible_refusal() -> None:
    raw = {
        GlobalActionID.REVEAL_CURRENT_ASK.value: {
            "key_code": 40,
            "key_label": "K",
            "modifiers": ["command"],
            "future_field": True,
        }
    }
    target, lifecycle = _integrated_target()
    target.settings = replace(target.settings, global_action_shortcuts=raw)

    pane = build_global_action_settings_pane(target)

    assert pane._presentation.state.value == "persisted_refusal"
    assert pane._presentation.committed_chord is None
    assert pane._presentation.persisted_refusals == (
        PersistedShortcutRefusal(
            GlobalActionID.REVEAL_CURRENT_ASK.value,
            ShortcutValidationCode.MALFORMED,
        ),
    )
    assert pane.status_field.stringValue() == (
        "Saved shortcut settings contain an unsupported entry."
    )
    assert target.settings.global_action_shortcuts == raw
    assert lifecycle.calls == []


def test_retained_refresh_keeps_valid_binding_visible_with_unknown_entry() -> None:
    persisted = _chord()
    target, lifecycle = _integrated_target(persisted=persisted, active=persisted)
    pane = build_global_action_settings_pane(target)
    target.settings_fields["global_action_settings_pane"] = pane
    raw = {
        GlobalActionID.REVEAL_CURRENT_ASK.value: persisted.to_dict(),
        "future_action": {"unexpected": True},
    }
    target.settings = replace(target.settings, global_action_shortcuts=raw)

    refresh_global_action_settings_controls(target)

    assert pane._presentation.state.value == "persisted_refusal"
    assert pane._presentation.committed_chord == persisted
    assert pane._presentation.persisted_refusals == (
        PersistedShortcutRefusal(
            "future_action",
            ShortcutValidationCode.UNKNOWN_ACTION,
        ),
    )
    assert pane.recorder.accessibilityValue() == "⌃K"
    assert pane.status_field.stringValue() == (
        "Saved shortcut settings contain an unsupported entry. "
        "Valid shortcuts remain active."
    )
    assert lifecycle.registry.active_bindings == {
        GlobalActionID.REVEAL_CURRENT_ASK: persisted
    }
    assert target.settings.global_action_shortcuts == raw
    assert lifecycle.calls == []


def test_settings_refresh_retains_controls_pending_retry_and_current_callbacks() -> None:
    previous = _chord()
    candidate = _chord(45, "N", ShortcutModifier.COMMAND)
    target, old_lifecycle = _integrated_target(persisted=previous, active=previous)
    old_lifecycle.results = [
        GlobalActionChangeResult(False, save_failure="write_refused")
    ]
    pane = build_global_action_settings_pane(target)
    target.settings_fields["global_action_settings_pane"] = pane
    controls = (
        pane.view,
        pane.recorder,
        pane.status_field,
        pane.record_button,
        pane.clear_button,
        pane.retry_button,
    )
    pane._submit_candidate(candidate)
    replacement = _Lifecycle(target, previous)
    target.global_action_lifecycle = replacement

    refresh_global_action_settings_controls(target)
    pane.retryShortcut_(None)

    assert controls == (
        pane.view,
        pane.recorder,
        pane.status_field,
        pane.record_button,
        pane.clear_button,
        pane.retry_button,
    )
    assert old_lifecycle.calls == [
        ("set", GlobalActionID.REVEAL_CURRENT_ASK, candidate)
    ]
    assert replacement.calls == [
        ("set", GlobalActionID.REVEAL_CURRENT_ASK, candidate)
    ]
    assert pane._presentation == _presentation(
        GlobalActionRecorderState.ACTIVE,
        candidate,
    )
