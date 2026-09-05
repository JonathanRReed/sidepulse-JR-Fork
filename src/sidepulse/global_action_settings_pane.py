"""Bounded AppKit recorder row for the Reveal Current Ask global action."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from typing import Final

import objc
from AppKit import (
    NSBezierPath,
    NSColor,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSFocusRingOnly,
    NSFont,
    NSGraphicsContext,
    NSSetFocusRingStyle,
    NSTextAlignmentCenter,
    NSTextField,
    NSView,
)
from Foundation import NSObject

from . import native_ui
from .global_action_controller import GlobalActionChangeResult
from .global_actions import (
    GlobalActionID,
    PersistedShortcutRefusal,
    ShortcutChord,
    ShortcutModifier,
    ShortcutValidationCode,
    ShortcutValidationError,
    format_shortcut,
    parse_global_action_shortcuts,
    validate_shortcut,
)

GLOBAL_ACTION_GROUP_LABEL: Final = "Global actions"
GLOBAL_ACTION_RECORDER_LABEL: Final = "Reveal current ask shortcut"
GLOBAL_ACTION_RECORDER_HELP: Final = (
    "Reveals the current ask or Agent Browser without observing ordinary typing."
)
GLOBAL_ACTION_STATUS_LABEL: Final = "Global action status"

_DESCRIPTION: Final = (
    "Set a keyboard shortcut for Reveal Current Ask. The shortcut opens the current "
    "ask when one is available, or opens Agent Browser."
)
_RECORDING_STATUS: Final = "Press a shortcut with Command or Control."
_UNASSIGNED_STATUS: Final = "No shortcut assigned."
_ACTIVE_STATUS: Final = "Shortcut active."
_CLEARED_STATUS: Final = "Shortcut cleared."
_REGISTRATION_REFUSED_STATUS: Final = (
    "macOS refused shortcut registration. The previous binding is unchanged."
)
_PERSISTED_REFUSAL_STATUS: Final = (
    "Saved shortcut settings contain an unsupported entry."
)
_SAVE_FAILURE_STATUS: Final = (
    "Could not save shortcut. The previous binding is unchanged."
)
_LOCAL_CONFLICT_FALLBACK: Final = "another JR-Bar action"
_MAX_DETAIL_LENGTH: Final = 96

_ESCAPE_KEY_CODE = 53
_DELETE_KEY_CODES = frozenset({51, 117})
_DELETE_CHARACTERS = frozenset({"\x08", "\x7f", "\uf728"})


class GlobalActionRecorderState(str, Enum):
    UNSET = "unset"
    RECORDING = "recording"
    ACTIVE = "active"
    LOCAL_CONFLICT = "local_conflict"
    REGISTRATION_REFUSED = "registration_refused"
    PERSISTED_REFUSAL = "persisted_refusal"
    SAVE_FAILURE = "save_failure"
    CLEARED = "cleared"


@dataclass(frozen=True, slots=True)
class GlobalActionRecorderPresentation:
    """One immutable, controller-supplied recorder presentation."""

    state: GlobalActionRecorderState
    committed_chord: ShortcutChord | None = None
    detail: str | None = None
    persisted_refusals: tuple[PersistedShortcutRefusal, ...] = ()

    def __post_init__(self) -> None:
        if type(self.state) is not GlobalActionRecorderState:
            raise ValueError("global action recorder state is invalid")
        if self.committed_chord is not None:
            validate_shortcut(self.committed_chord)
        if self.state is GlobalActionRecorderState.ACTIVE and self.committed_chord is None:
            raise ValueError("active recorder presentation requires a committed chord")
        if not (
            type(self.persisted_refusals) is tuple
            and all(
                type(refusal) is PersistedShortcutRefusal
                for refusal in self.persisted_refusals
            )
        ):
            raise ValueError("persisted shortcut refusals are invalid")
        if (
            self.state is GlobalActionRecorderState.PERSISTED_REFUSAL
        ) != bool(self.persisted_refusals):
            raise ValueError("persisted refusal presentation is inconsistent")
        if self.state in {
            GlobalActionRecorderState.UNSET,
            GlobalActionRecorderState.CLEARED,
        } and self.committed_chord is not None:
            raise ValueError("unset recorder presentation cannot contain a chord")
        if self.detail is not None and (
            type(self.detail) is not str
            or not self.detail.strip()
            or len(self.detail) > _MAX_DETAIL_LENGTH
            or not self.detail.isprintable()
        ):
            raise ValueError("global action recorder detail is invalid")


CandidateCallback = Callable[[ShortcutChord], GlobalActionRecorderPresentation]
ActionCallback = Callable[[], GlobalActionRecorderPresentation]


class _PendingOperationKind(str, Enum):
    SET = "set"
    CLEAR = "clear"
    REFRESH = "refresh"


@dataclass(frozen=True, slots=True)
class _PendingOperation:
    kind: _PendingOperationKind
    chord: ShortcutChord | None = None
    failure_state: GlobalActionRecorderState | None = None

    def __post_init__(self) -> None:
        if self.kind is _PendingOperationKind.SET and self.chord is None:
            raise ValueError("pending shortcut set requires a chord")
        if self.kind is not _PendingOperationKind.SET and self.chord is not None:
            raise ValueError("only a pending shortcut set may retain a chord")


def _status_text(presentation: GlobalActionRecorderPresentation) -> str:
    state = presentation.state
    if state is GlobalActionRecorderState.UNSET:
        return _UNASSIGNED_STATUS
    if state is GlobalActionRecorderState.RECORDING:
        return _RECORDING_STATUS
    if state is GlobalActionRecorderState.ACTIVE:
        return _ACTIVE_STATUS
    if state is GlobalActionRecorderState.LOCAL_CONFLICT:
        conflict = presentation.detail or _LOCAL_CONFLICT_FALLBACK
        return f"Already used by {conflict}. The previous binding is unchanged."
    if state is GlobalActionRecorderState.REGISTRATION_REFUSED:
        return _REGISTRATION_REFUSED_STATUS
    if state is GlobalActionRecorderState.PERSISTED_REFUSAL:
        if presentation.committed_chord is not None:
            return f"{_PERSISTED_REFUSAL_STATUS} Valid shortcuts remain active."
        return _PERSISTED_REFUSAL_STATUS
    if state is GlobalActionRecorderState.SAVE_FAILURE:
        return _SAVE_FAILURE_STATUS
    return _CLEARED_STATUS


def _recorder_value(presentation: GlobalActionRecorderPresentation) -> str:
    if presentation.state is GlobalActionRecorderState.RECORDING:
        return "Recording"
    if presentation.committed_chord is None:
        return "Not set"
    return format_shortcut(presentation.committed_chord)


def _bounded_modifiers(flags: int) -> frozenset[ShortcutModifier]:
    modifiers: set[ShortcutModifier] = set()
    if flags & NSEventModifierFlagControl:
        modifiers.add(ShortcutModifier.CONTROL)
    if flags & NSEventModifierFlagOption:
        modifiers.add(ShortcutModifier.OPTION)
    if flags & NSEventModifierFlagShift:
        modifiers.add(ShortcutModifier.SHIFT)
    if flags & NSEventModifierFlagCommand:
        modifiers.add(ShortcutModifier.COMMAND)
    return frozenset(modifiers)


def _candidate_from_event(event) -> ShortcutChord:
    characters = str(event.charactersIgnoringModifiers() or "")
    label = characters.upper() if len(characters) == 1 and characters.isalpha() else characters
    candidate = ShortcutChord(
        key_code=int(event.keyCode()),
        key_label=label,
        modifiers=_bounded_modifiers(int(event.modifierFlags())),
    )
    validate_shortcut(candidate)
    return candidate


def _validation_status(error: ShortcutValidationError | ValueError) -> str:
    if isinstance(error, ShortcutValidationError):
        if error.code in {
            ShortcutValidationCode.NO_MODIFIERS,
            ShortcutValidationCode.COMMAND_OR_CONTROL_REQUIRED,
            ShortcutValidationCode.OPTION_SHIFT_ONLY,
        }:
            return "Include Command or Control."
        if error.code is ShortcutValidationCode.RESERVED_MENU_EQUIVALENT:
            return "That shortcut is reserved by JR-Bar. Press another shortcut."
    return "That shortcut is not supported. Press another shortcut."


class _ShortcutRecorderView(NSView):
    """Receives keys only while its owning pane has explicit recorder focus."""

    def initWithFrame_(self, frame):
        self = objc.super(_ShortcutRecorderView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.owner = None
        self.recording = False
        self.displayed_value = "Not set"
        self.delegate_unhandled_key = self._delegate_to_super
        self.value_field = NSTextField.labelWithString_(self.displayed_value)
        self.value_field.setAlignment_(NSTextAlignmentCenter)
        self.value_field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(13.0, 0.0))
        self.value_field.setTextColor_(NSColor.labelColor())
        self.value_field.setAccessibilityElement_(False)
        self.value_field.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.addSubview_(self.value_field)
        self.value_field.centerXAnchor().constraintEqualToAnchor_(
            self.centerXAnchor()
        ).setActive_(True)
        self.value_field.centerYAnchor().constraintEqualToAnchor_(
            self.centerYAnchor()
        ).setActive_(True)
        self.value_field.leadingAnchor().constraintGreaterThanOrEqualToAnchor_constant_(
            self.leadingAnchor(), 10.0
        ).setActive_(True)
        self.value_field.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(
            self.trailingAnchor(), -10.0
        ).setActive_(True)
        self.setAccessibilityElement_(True)
        self.setAccessibilityRole_("AXTextField")
        self.setAccessibilityLabel_(GLOBAL_ACTION_RECORDER_LABEL)
        self.setAccessibilityHelp_(GLOBAL_ACTION_RECORDER_HELP)
        self.setToolTip_(GLOBAL_ACTION_RECORDER_HELP)
        return self

    def acceptsFirstResponder(self) -> bool:
        return True

    def becomeFirstResponder(self) -> bool:
        accepted = bool(objc.super(_ShortcutRecorderView, self).becomeFirstResponder())
        self.setNeedsDisplay_(True)
        return accepted

    def resignFirstResponder(self) -> bool:
        accepted = bool(objc.super(_ShortcutRecorderView, self).resignFirstResponder())
        self.setNeedsDisplay_(True)
        return accepted

    def drawRect_(self, rect) -> None:
        objc.super(_ShortcutRecorderView, self).drawRect_(rect)
        bounds = self.bounds()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((0.5, 0.5), (bounds.size.width - 1.0, bounds.size.height - 1.0)),
            7.0,
            7.0,
        )
        NSColor.controlBackgroundColor().setFill()
        path.fill()
        NSColor.separatorColor().setStroke()
        path.setLineWidth_(1.0)
        path.stroke()
        window = self.window()
        if window is None or window.firstResponder() is not self:
            return
        NSGraphicsContext.saveGraphicsState()
        try:
            NSSetFocusRingStyle(NSFocusRingOnly)
            NSColor.clearColor().setFill()
            path.fill()
        finally:
            NSGraphicsContext.restoreGraphicsState()

    @objc.python_method
    def set_displayed_value(self, value: str) -> None:
        self.displayed_value = value
        self.value_field.setStringValue_(value)
        self.setAccessibilityValue_(value)
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _delegate_to_super(self, event) -> None:
        objc.super(_ShortcutRecorderView, self).keyDown_(event)

    def keyDown_(self, event) -> None:
        if not self.recording:
            self.delegate_unhandled_key(event)
            return
        key_code = int(event.keyCode())
        characters = str(event.charactersIgnoringModifiers() or "")
        owner = self.owner
        if owner is None:
            self.delegate_unhandled_key(event)
            return
        if key_code == _ESCAPE_KEY_CODE or characters == "\x1b":
            owner._cancel_recording()
            return
        if key_code in _DELETE_KEY_CODES or characters in _DELETE_CHARACTERS:
            owner._clear_from_recorder()
            return
        try:
            candidate = _candidate_from_event(event)
        except (ShortcutValidationError, ValueError, TypeError) as error:
            owner._set_recording_status(_validation_status(error))
            return
        owner._submit_candidate(candidate)


class GlobalActionSettingsPane(NSObject):
    """Retained controls and callbacks for the standalone Overview row."""

    @classmethod
    @objc.python_method
    def create(
        cls,
        *,
        presentation: GlobalActionRecorderPresentation,
        on_candidate: CandidateCallback,
        on_clear: ActionCallback,
        on_retry: ActionCallback,
    ) -> GlobalActionSettingsPane:
        return cls.alloc().initWithPresentation_callbacks_(
            presentation,
            (on_candidate, on_clear, on_retry),
        )

    def initWithPresentation_callbacks_(self, presentation, callbacks):
        self = objc.super(GlobalActionSettingsPane, self).init()
        if self is None:
            return None
        self._presentation = presentation
        self._resting_presentation = presentation
        self._prior_first_responder = None
        self.pending_operation: _PendingOperation | None = None
        self._on_candidate, self._on_clear, self._on_retry = callbacks
        self._build_view()
        self.refresh(presentation)
        return self

    @objc.python_method
    def _build_view(self) -> None:
        root, content = native_ui.make_card("Global Actions")
        root.setAccessibilityElement_(True)
        root.setAccessibilityRole_("AXGroup")
        root.setAccessibilityLabel_(GLOBAL_ACTION_GROUP_LABEL)
        root.setAccessibilityHelp_(GLOBAL_ACTION_RECORDER_HELP)

        description = native_ui.make_wrapping_label(
            _DESCRIPTION,
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
        content.addArrangedSubview_(description)

        self.recorder = _ShortcutRecorderView.alloc().initWithFrame_(
            ((0.0, 0.0), (164.0, 32.0))
        )
        self.recorder.owner = self
        native_ui.constrain_width(self.recorder, 164.0)
        native_ui.constrain_height(self.recorder, 32.0)

        self.record_button = native_ui.make_button(
            "Record Shortcut", self, "recordShortcut:"
        )
        self.record_button.setAccessibilityLabel_("Record Shortcut")
        self.record_button.setAccessibilityHelp_(
            "Move focus to the shortcut recorder and listen for one modified key."
        )
        self.clear_button = native_ui.make_button(
            "Clear", self, "clearShortcut:"
        )
        self.clear_button.setAccessibilityLabel_("Clear Shortcut")
        self.clear_button.setAccessibilityHelp_(
            "Remove the configured Reveal Current Ask shortcut."
        )
        self.retry_button = native_ui.make_button("Retry", self, "retryShortcut:")
        self.retry_button.setAccessibilityLabel_("Retry Shortcut")
        self.retry_button.setAccessibilityHelp_(
            "Retry the previous shortcut registration or save operation."
        )

        controls = native_ui.make_stack(
            orientation="horizontal",
            spacing=native_ui.SPACE_S,
        )
        controls.addArrangedSubview_(self.recorder)
        controls.addArrangedSubview_(self.record_button)
        controls.addArrangedSubview_(self.clear_button)
        controls.addArrangedSubview_(self.retry_button)
        content.addArrangedSubview_(
            native_ui.make_row(
                "Reveal Current Ask",
                controls,
                help_text=GLOBAL_ACTION_RECORDER_HELP,
            )
        )

        self.status_field = native_ui.make_wrapping_label(
            "",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
        self.status_field.setAccessibilityElement_(True)
        self.status_field.setAccessibilityLabel_(GLOBAL_ACTION_STATUS_LABEL)
        self.status_field.setAccessibilityHelp_(GLOBAL_ACTION_RECORDER_HELP)
        content.addArrangedSubview_(self.status_field)
        self.view = root

    @objc.python_method
    def refresh(
        self,
        presentation: GlobalActionRecorderPresentation,
        *,
        on_candidate: CandidateCallback | None = None,
        on_clear: ActionCallback | None = None,
        on_retry: ActionCallback | None = None,
    ) -> None:
        if type(presentation) is not GlobalActionRecorderPresentation:
            raise ValueError("global action recorder presentation is invalid")
        was_recording = bool(self.recorder.recording)
        if on_candidate is not None:
            self._on_candidate = on_candidate
        if on_clear is not None:
            self._on_clear = on_clear
        if on_retry is not None:
            self._on_retry = on_retry
        self._presentation = presentation
        self.recorder.recording = (
            presentation.state is GlobalActionRecorderState.RECORDING
        )
        if not self.recorder.recording:
            self._resting_presentation = presentation
        value = _recorder_value(presentation)
        status = _status_text(presentation)
        self.recorder.set_displayed_value(value)
        self.status_field.setStringValue_(status)
        self.status_field.setAccessibilityValue_(status)
        has_committed_chord = presentation.committed_chord is not None
        self.clear_button.setEnabled_(has_committed_chord or self.recorder.recording)
        retry_visible = presentation.state in {
            GlobalActionRecorderState.REGISTRATION_REFUSED,
            GlobalActionRecorderState.SAVE_FAILURE,
        }
        self.retry_button.setHidden_(not retry_visible)
        self._update_key_loop(retry_visible=retry_visible)
        if was_recording and not self.recorder.recording:
            self._restore_focus()

    @objc.python_method
    def _update_key_loop(self, *, retry_visible: bool) -> None:
        controls = [self.recorder, self.record_button, self.clear_button]
        if retry_visible:
            controls.append(self.retry_button)
        self.view.setNextKeyView_(controls[0])
        for current, following in pairwise(controls):
            current.setNextKeyView_(following)
        controls[-1].setNextKeyView_(controls[0])

    @objc.IBAction
    def recordShortcut_(self, _sender) -> None:
        window = self.recorder.window()
        if not self.recorder.recording:
            self._resting_presentation = self._presentation
            self._prior_first_responder = (
                window.firstResponder() if window is not None else None
            )
        recording = GlobalActionRecorderPresentation(
            state=GlobalActionRecorderState.RECORDING,
            committed_chord=self._resting_presentation.committed_chord,
        )
        self.refresh(recording)
        if window is not None:
            window.makeFirstResponder_(self.recorder)

    @objc.IBAction
    def clearShortcut_(self, _sender) -> None:
        if self.recorder.recording:
            self._finish_recording()
        self._apply_callback(self._on_clear)

    @objc.IBAction
    def retryShortcut_(self, _sender) -> None:
        if self.recorder.recording:
            self._finish_recording()
        self._apply_callback(self._on_retry)

    @objc.python_method
    def _cancel_recording(self) -> None:
        self._finish_recording()

    @objc.python_method
    def _clear_from_recorder(self) -> None:
        self._finish_recording()
        self._apply_callback(self._on_clear)

    @objc.python_method
    def _submit_candidate(self, candidate: ShortcutChord) -> None:
        self._finish_recording()
        self._apply_callback(self._on_candidate, candidate)

    @objc.python_method
    def _set_recording_status(self, status: str) -> None:
        self.status_field.setStringValue_(status)
        self.status_field.setAccessibilityValue_(status)

    @objc.python_method
    def _finish_recording(self) -> None:
        if not self.recorder.recording:
            return
        self.refresh(self._resting_presentation)

    @objc.python_method
    def _restore_focus(self) -> None:
        window = self.recorder.window()
        prior = self._prior_first_responder
        self._prior_first_responder = None
        if window is None:
            return
        try:
            if prior is not None and prior is not self.recorder:
                window.makeFirstResponder_(prior)
                if window.firstResponder() is prior:
                    return
        except Exception:
            pass
        window.makeFirstResponder_(self.record_button)

    @objc.python_method
    def _apply_callback(self, callback: Callable, *args) -> None:
        self.refresh(callback(*args))


_ACTION = GlobalActionID.REVEAL_CURRENT_ASK


def _persisted_chord(target: object) -> ShortcutChord | None:
    settings = getattr(target, "settings")
    return parse_global_action_shortcuts(
        getattr(settings, "global_action_shortcuts", {})
    ).binding_for(_ACTION)


def _parsed_persisted_shortcuts(target: object):
    settings = getattr(target, "settings")
    return parse_global_action_shortcuts(
        getattr(settings, "global_action_shortcuts", {})
    )


def _active_chord(target: object) -> tuple[bool, ShortcutChord | None]:
    lifecycle = getattr(target, "global_action_lifecycle")
    registry = getattr(lifecycle, "registry", None)
    if registry is None:
        return False, None
    active_bindings = getattr(registry, "active_bindings")
    return True, active_bindings.get(_ACTION)


def _settled_presentation(
    target: object,
    *,
    cleared: bool = False,
    persisted_refusals: tuple[PersistedShortcutRefusal, ...] | None = None,
) -> GlobalActionRecorderPresentation:
    parsed = _parsed_persisted_shortcuts(target)
    persisted = parsed.binding_for(_ACTION)
    refusals = parsed.refusals if persisted_refusals is None else persisted_refusals
    if refusals:
        return GlobalActionRecorderPresentation(
            GlobalActionRecorderState.PERSISTED_REFUSAL,
            persisted,
            persisted_refusals=refusals,
        )
    has_registry, active = _active_chord(target)
    if has_registry and active != persisted:
        return GlobalActionRecorderPresentation(
            GlobalActionRecorderState.REGISTRATION_REFUSED,
            persisted,
        )
    if persisted is None:
        state = (
            GlobalActionRecorderState.CLEARED
            if cleared
            else GlobalActionRecorderState.UNSET
        )
        return GlobalActionRecorderPresentation(state)
    return GlobalActionRecorderPresentation(
        GlobalActionRecorderState.ACTIVE,
        persisted,
    )


def _failure_presentation(
    target: object,
    state: GlobalActionRecorderState,
    *,
    detail: str | None = None,
) -> GlobalActionRecorderPresentation:
    return GlobalActionRecorderPresentation(
        state,
        _persisted_chord(target),
        detail,
    )


def _validated_change_result(result: object) -> GlobalActionChangeResult:
    if type(result) is not GlobalActionChangeResult:
        raise ValueError("global action lifecycle result is invalid")
    failures = int(result.refusal is not None) + int(result.save_failure is not None)
    if result.applied and failures:
        raise ValueError("applied global action lifecycle result contains a failure")
    if not result.applied and failures != 1:
        raise ValueError("failed global action lifecycle result requires one failure")
    return result


class _GlobalActionSettingsIntegration:
    """Projects lifecycle transactions into one retained recorder pane."""

    def __init__(self, target: object) -> None:
        self.target = target
        self.pane: GlobalActionSettingsPane | None = None

    def bind(self, pane: GlobalActionSettingsPane) -> None:
        self.pane = pane

    def _pane(self) -> GlobalActionSettingsPane:
        if self.pane is None:
            raise RuntimeError("global action settings pane is not bound")
        return self.pane

    def _run(self, operation: _PendingOperation) -> GlobalActionRecorderPresentation:
        lifecycle = getattr(self.target, "global_action_lifecycle")
        try:
            if operation.kind is _PendingOperationKind.SET:
                result = lifecycle.set_shortcut(_ACTION, operation.chord)
            elif operation.kind is _PendingOperationKind.CLEAR:
                result = lifecycle.clear_shortcut(_ACTION)
            else:
                result = lifecycle.refresh_from_settings()
        except ShortcutValidationError as error:
            if error.code is not ShortcutValidationCode.DUPLICATE_BINDING:
                raise
            pane = self._pane()
            pane.pending_operation = None
            detail = error.conflicting_action or _LOCAL_CONFLICT_FALLBACK
            return _failure_presentation(
                self.target,
                GlobalActionRecorderState.LOCAL_CONFLICT,
                detail=detail,
            )

        change = _validated_change_result(result)
        pane = self._pane()
        if change.applied:
            pane.pending_operation = None
            settled = _settled_presentation(
                self.target,
                cleared=operation.kind is _PendingOperationKind.CLEAR,
                persisted_refusals=change.persisted_refusals,
            )
            if settled.state is GlobalActionRecorderState.REGISTRATION_REFUSED:
                pane.pending_operation = _PendingOperation(
                    _PendingOperationKind.REFRESH,
                    failure_state=GlobalActionRecorderState.REGISTRATION_REFUSED,
                )
            return settled

        state = (
            GlobalActionRecorderState.REGISTRATION_REFUSED
            if change.refusal is not None
            else GlobalActionRecorderState.SAVE_FAILURE
        )
        pane.pending_operation = _PendingOperation(
            operation.kind,
            chord=operation.chord,
            failure_state=state,
        )
        return _failure_presentation(self.target, state)

    def submit(self, candidate: ShortcutChord) -> GlobalActionRecorderPresentation:
        return self._run(_PendingOperation(_PendingOperationKind.SET, candidate))

    def clear(self) -> GlobalActionRecorderPresentation:
        return self._run(_PendingOperation(_PendingOperationKind.CLEAR))

    def retry(self) -> GlobalActionRecorderPresentation:
        operation = self._pane().pending_operation
        if operation is None:
            return _settled_presentation(self.target)
        return self._run(operation)

    def refresh(self) -> None:
        pane = self._pane()
        operation = pane.pending_operation
        settled = _settled_presentation(self.target)
        if operation is not None:
            satisfied = (
                operation.kind is _PendingOperationKind.SET
                and settled.state is GlobalActionRecorderState.ACTIVE
                and settled.committed_chord == operation.chord
            ) or (
                operation.kind is _PendingOperationKind.CLEAR
                and settled.committed_chord is None
                and settled.state is not GlobalActionRecorderState.REGISTRATION_REFUSED
            ) or (
                operation.kind is _PendingOperationKind.REFRESH
                and settled.state is not GlobalActionRecorderState.REGISTRATION_REFUSED
            )
            if satisfied:
                pane.pending_operation = None
                if operation.kind is _PendingOperationKind.CLEAR:
                    settled = GlobalActionRecorderPresentation(
                        GlobalActionRecorderState.CLEARED
                    )
            else:
                settled = _failure_presentation(
                    self.target,
                    operation.failure_state
                    or GlobalActionRecorderState.SAVE_FAILURE,
                )
        pane.refresh(
            settled,
            on_candidate=self.submit,
            on_clear=self.clear,
            on_retry=self.retry,
        )


def build_global_action_settings_pane(target: object) -> GlobalActionSettingsPane:
    """Build the Overview recorder against the controller's live lifecycle."""
    integration = _GlobalActionSettingsIntegration(target)
    presentation = _settled_presentation(target)
    pane = GlobalActionSettingsPane.create(
        presentation=presentation,
        on_candidate=integration.submit,
        on_clear=integration.clear,
        on_retry=integration.retry,
    )
    integration.bind(pane)
    pane._integration = integration
    if presentation.state is GlobalActionRecorderState.REGISTRATION_REFUSED:
        pane.pending_operation = _PendingOperation(
            _PendingOperationKind.REFRESH,
            failure_state=GlobalActionRecorderState.REGISTRATION_REFUSED,
        )
    return pane


def refresh_global_action_settings_controls(target: object) -> None:
    """Refresh a retained Overview recorder without rebuilding its controls."""
    fields = getattr(target, "settings_fields", {})
    pane = fields.get("global_action_settings_pane")
    if pane is None:
        return
    if type(pane) is not GlobalActionSettingsPane:
        raise ValueError("retained global action settings pane is invalid")
    integration = getattr(pane, "_integration", None)
    if type(integration) is not _GlobalActionSettingsIntegration:
        raise ValueError("retained global action settings integration is invalid")
    integration.target = target
    integration.refresh()


__all__ = [
    "GLOBAL_ACTION_GROUP_LABEL",
    "GLOBAL_ACTION_RECORDER_HELP",
    "GLOBAL_ACTION_RECORDER_LABEL",
    "GLOBAL_ACTION_STATUS_LABEL",
    "GlobalActionRecorderPresentation",
    "GlobalActionRecorderState",
    "GlobalActionSettingsPane",
    "build_global_action_settings_pane",
    "refresh_global_action_settings_controls",
]
