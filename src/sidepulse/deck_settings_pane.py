"""Native, bounded controls for configuring Agent Deck actions."""

from __future__ import annotations

from typing import Final

import objc
from AppKit import (
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSFont,
    NSTextAlignmentCenter,
    NSTextField,
    NSView,
)
from Foundation import NSObject

from . import native_ui
from .deck_actions import DeckAction
from .deck_control_settings import DeckControlSettings

ACTION_CHOICES: Final = (
    ("Disabled", None),
    ("Open app", "open_app"),
    ("App shortcut", "shortcut"),
    ("Reveal current ask", "reveal_current_ask"),
    ("Agent browser", "open_agent_browser"),
    ("Usage Center", "open_usage"),
)

_MODIFIER_FLAGS = (
    (NSEventModifierFlagControl, "control", "⌃"),
    (NSEventModifierFlagOption, "option", "⌥"),
    (NSEventModifierFlagShift, "shift", "⇧"),
    (NSEventModifierFlagCommand, "command", "⌘"),
)

# macOS virtual key codes are persisted because CGEvent uses them. The UI always
# presents their key cap instead. This table covers the keyboard keys useful as
# application shortcuts and deliberately has no numeric-code fallback.
_KEY_LABELS: Final = {
    0: "A",
    1: "S",
    2: "D",
    3: "F",
    4: "H",
    5: "G",
    6: "Z",
    7: "X",
    8: "C",
    9: "V",
    11: "B",
    12: "Q",
    13: "W",
    14: "E",
    15: "R",
    16: "Y",
    17: "T",
    18: "1",
    19: "2",
    20: "3",
    21: "4",
    22: "6",
    23: "5",
    24: "=",
    25: "9",
    26: "7",
    27: "-",
    28: "8",
    29: "0",
    30: "]",
    31: "O",
    32: "U",
    33: "[",
    34: "I",
    35: "P",
    36: "Return",
    37: "L",
    38: "J",
    39: "'",
    40: "K",
    41: ";",
    42: "\\",
    43: ",",
    44: "/",
    45: "N",
    46: "M",
    47: ".",
    48: "Tab",
    49: "Space",
    50: "`",
    51: "Delete",
    53: "Escape",
    65: ".",
    67: "*",
    69: "+",
    71: "Clear",
    75: "/",
    76: "Enter",
    78: "-",
    81: "=",
    82: "0",
    83: "1",
    84: "2",
    85: "3",
    86: "4",
    87: "5",
    88: "6",
    89: "7",
    91: "8",
    92: "9",
    96: "F5",
    97: "F6",
    98: "F7",
    99: "F3",
    100: "F8",
    101: "F9",
    103: "F11",
    105: "F13",
    106: "F16",
    107: "F14",
    109: "F10",
    111: "F12",
    113: "F15",
    114: "Help",
    115: "Home",
    116: "Page Up",
    117: "Forward Delete",
    118: "F4",
    119: "End",
    120: "F2",
    121: "Page Down",
    122: "F1",
    123: "Left",
    124: "Right",
    125: "Down",
    126: "Up",
}


def _app_label(bundle_id: str, chosen_name: str | None = None) -> str:
    label = (chosen_name or bundle_id.rsplit(".", 1)[-1]).removesuffix(".app").strip()
    if not label or not label.isprintable():
        label = "Application"
    return label[:64]


def _shortcut_text(key_code: int, modifiers: tuple[str, ...]) -> str:
    symbols = "".join(symbol for _flag, name, symbol in _MODIFIER_FLAGS if name in modifiers)
    return f"{symbols}{_KEY_LABELS.get(key_code, 'Recorded key')}"


class _DeckShortcutRecorder(NSView):
    def initWithOwner_(self, owner):
        self = objc.super(_DeckShortcutRecorder, self).initWithFrame_(((0, 0), (150, 32)))
        if self is None:
            return None
        self.owner = owner
        self.recording = False
        self.key_code = None
        self.modifiers = ()
        self.label = NSTextField.labelWithString_("Not recorded")
        self.label.setAlignment_(NSTextAlignmentCenter)
        self.label.setFont_(NSFont.monospacedSystemFontOfSize_weight_(13.0, 0.0))
        self.label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.label.setAccessibilityElement_(False)
        self.addSubview_(self.label)
        self.label.centerXAnchor().constraintEqualToAnchor_(self.centerXAnchor()).setActive_(True)
        self.label.centerYAnchor().constraintEqualToAnchor_(self.centerYAnchor()).setActive_(True)
        self.setAccessibilityElement_(True)
        self.setAccessibilityRole_("AXTextField")
        self.setAccessibilityLabel_("App shortcut")
        self.setAccessibilityHelp_("Records one keyboard shortcut for the selected application.")
        native_ui.constrain_width(self, 150.0)
        native_ui.constrain_height(self, 32.0)
        return self

    @objc.python_method
    def begin_recording(self) -> None:
        self.recording = True
        self.label.setStringValue_("Press a shortcut")
        self.setAccessibilityValue_("Press a shortcut")

    @objc.python_method
    def set_shortcut(self, key_code: int | None, modifiers: tuple[str, ...] = ()) -> None:
        self.key_code = key_code
        self.modifiers = modifiers
        text = "Not recorded" if key_code is None else _shortcut_text(key_code, modifiers)
        self.label.setStringValue_(text)
        self.setAccessibilityValue_(text)

    def acceptsFirstResponder(self):
        return True

    def keyDown_(self, event) -> None:
        if not self.recording:
            return objc.super(_DeckShortcutRecorder, self).keyDown_(event)
        key_code = int(event.keyCode())
        if key_code not in _KEY_LABELS:
            self.owner.set_status("That key is not supported. Press another shortcut.")
            return
        flags = int(event.modifierFlags())
        modifiers = tuple(name for flag, name, _symbol in _MODIFIER_FLAGS if flags & flag)
        self.recording = False
        self.set_shortcut(key_code, modifiers)
        self.owner.set_status("Shortcut recorded. Choose Save mapping to apply it.")

    def performKeyEquivalent_(self, event) -> bool:
        if not self.recording:
            return bool(objc.super(_DeckShortcutRecorder, self).performKeyEquivalent_(event))
        self.keyDown_(event)
        return True

    @objc.python_method
    def stop_recording(self) -> None:
        if not self.recording:
            return
        self.recording = False
        self.set_shortcut(self.key_code, self.modifiers)


class DeckSettingsPane(NSObject):
    def initWithTarget_settings_(self, target, settings):
        self = objc.super(DeckSettingsPane, self).init()
        if self is None:
            return None
        self.target = target
        self.settings = settings
        self.selected_bundle_id = None
        self.selected_app_name = None
        self._build()
        self.refresh(settings)
        return self

    @objc.python_method
    def _build(self) -> None:
        self.view, content = native_ui.make_card("Agent Deck controls")
        self.view.setAccessibilityElement_(True)
        self.view.setAccessibilityRole_("AXGroup")
        self.view.setAccessibilityLabel_("Agent Deck controls")

        self.description_field = native_ui.make_wrapping_label(
            "Choose what each device key does.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
        content.addArrangedSubview_(self.description_field)
        self.enable_checkbox = native_ui.make_checkbox(
            "Enable device actions",
            self.target,
            "toggleDeckControls:",
            help_text="Allow configured Agent Deck keys to run their saved actions.",
        )
        self.enable_checkbox.setAccessibilityLabel_("Enable device actions")
        content.addArrangedSubview_(self.enable_checkbox)
        native_ui.add_separator(content)

        self.key_popup = native_ui.make_popup_button(self.target, "deckMappingSelectionChanged:")
        for key in range(20):
            self.key_popup.addItemWithTitle_(f"AG{key:02d}")
            self.key_popup.lastItem().setRepresentedObject_(key)
        content.addArrangedSubview_(native_ui.make_row("Logical key", self.key_popup))

        self.action_popup = native_ui.make_popup_button(self.target, "deckMappingSelectionChanged:")
        for label, kind in ACTION_CHOICES:
            self.action_popup.addItemWithTitle_(label)
            self.action_popup.lastItem().setRepresentedObject_(kind or "")
        content.addArrangedSubview_(native_ui.make_row("Action", self.action_popup))

        self.application_button = native_ui.make_button("Choose Application…", self.target, "chooseDeckApp:")
        self.application_button.setAccessibilityHelp_(
            "Choose a macOS application. JR-Bar stores only its bundle identifier. "
            "App shortcuts run only while that app is frontmost and require macOS Accessibility permission."
        )
        content.addArrangedSubview_(native_ui.make_row("Application", self.application_button))

        self.shortcut_recorder = _DeckShortcutRecorder.alloc().initWithOwner_(self)
        self.record_button = native_ui.make_button("Record Shortcut", self, "recordShortcut:")
        shortcut_controls = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        shortcut_controls.addArrangedSubview_(self.shortcut_recorder)
        shortcut_controls.addArrangedSubview_(self.record_button)
        content.addArrangedSubview_(native_ui.make_row("Shortcut", shortcut_controls))

        self.save_button = native_ui.make_button("Save mapping", self.target, "saveDeckMapping:")
        self.save_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        self.save_row.addArrangedSubview_(native_ui.make_hspacer())
        self.save_row.addArrangedSubview_(self.save_button)
        content.addArrangedSubview_(self.save_row)
        self.summary_field = native_ui.make_wrapping_label("", secondary=True, size=11.0, max_width=560.0)
        self.summary_field.setAccessibilityLabel_("Existing Agent Deck mappings")
        content.addArrangedSubview_(self.summary_field)
        self.status_field = native_ui.make_wrapping_label("", secondary=True, size=11.0, max_width=560.0)
        self.status_field.setAccessibilityLabel_("Agent Deck settings status")
        content.addArrangedSubview_(self.status_field)
        self.hardware_note = native_ui.make_wrapping_label(
            "An active device layer must send AG key events. Inspect device setup to "
            "preview keymap changes before applying them. "
            "The Creator Micro master connection toggle above must also be on.",
            secondary=True,
            size=10.0,
            max_width=560.0,
        )
        content.addArrangedSubview_(self.hardware_note)
        self.inspect_setup_button = native_ui.make_button(
            "Inspect device setup…", self.target, "inspectCreatorMicroSetup:",
        )
        self.inspect_setup_button.setAccessibilityHelp_(
            "Read the approved Creator Micro 2 keymap and preview the exact key changes."
        )
        self.restore_setup_button = native_ui.make_button(
            "Restore device keymap…", self.target, "restoreCreatorMicroKeymap:",
        )
        self.restore_setup_button.setAccessibilityHelp_(
            "Restore JR-Bar's private backup after confirmation."
        )
        setup_row = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        setup_row.addArrangedSubview_(self.inspect_setup_button)
        setup_row.addArrangedSubview_(self.restore_setup_button)
        setup_row.addArrangedSubview_(native_ui.make_hspacer())
        content.addArrangedSubview_(setup_row)

    def recordShortcut_(self, _sender) -> None:
        self.shortcut_recorder.begin_recording()
        window = self.shortcut_recorder.window()
        if window is not None:
            window.makeFirstResponder_(self.shortcut_recorder)

    @objc.python_method
    def set_status(self, text: str) -> None:
        self.status_field.setStringValue_(text)
        self.status_field.setAccessibilityValue_(text)

    @objc.python_method
    def refresh(self, settings: DeckControlSettings) -> None:
        if type(settings) is not DeckControlSettings:
            raise ValueError("deck settings are unavailable")
        self.settings = settings
        self.shortcut_recorder.stop_recording()
        self.enable_checkbox.setState_(1 if settings.enabled else 0)
        self._load_selected_mapping()

    @objc.python_method
    def set_save_pending(self, pending: bool) -> None:
        self.enable_checkbox.setEnabled_(not pending)
        self.save_button.setEnabled_(not pending)

    @objc.python_method
    def set_setup_pending(self, pending: bool) -> None:
        self.inspect_setup_button.setEnabled_(not pending)
        self.restore_setup_button.setEnabled_(not pending)

    @objc.python_method
    def _load_selected_mapping(self) -> None:
        key = int(self.key_popup.indexOfSelectedItem())
        action = next((value for index, value in self.settings.bindings if index == key), None)
        kind = action.kind if action is not None else None
        selected = next(index for index, (_label, value) in enumerate(ACTION_CHOICES) if value == kind)
        self.action_popup.selectItemAtIndex_(selected)
        self.selected_bundle_id = action.bundle_id if action is not None else None
        self.selected_app_name = None
        if self.selected_bundle_id:
            self.application_button.setTitle_(_app_label(self.selected_bundle_id))
            self.application_button.setToolTip_(self.selected_bundle_id)
            self.application_button.setAccessibilityHelp_(self.selected_bundle_id)
        else:
            self.application_button.setTitle_("Choose Application…")
            self.application_button.setToolTip_(None)
        if action is not None and action.kind == "shortcut":
            self.shortcut_recorder.set_shortcut(action.key_code, action.modifiers)
        else:
            self.shortcut_recorder.set_shortcut(None)
        self._refresh_visibility()
        self._refresh_summary()

    @objc.python_method
    def selection_changed(self, sender) -> None:
        self.shortcut_recorder.stop_recording()
        if sender is self.key_popup:
            self._load_selected_mapping()
        else:
            self._refresh_visibility()

    @objc.python_method
    def _refresh_visibility(self) -> None:
        kind = ACTION_CHOICES[int(self.action_popup.indexOfSelectedItem())][1]
        needs_app = kind in {"open_app", "shortcut"}
        self.application_button.setEnabled_(needs_app)
        self.shortcut_recorder.setHidden_(kind != "shortcut")
        self.record_button.setHidden_(kind != "shortcut")

    @objc.python_method
    def selected_mapping(self) -> tuple[int, DeckAction | None]:
        key = int(self.key_popup.indexOfSelectedItem())
        kind = ACTION_CHOICES[int(self.action_popup.indexOfSelectedItem())][1]
        if kind is None:
            return key, None
        if kind == "open_app":
            return key, DeckAction(kind, bundle_id=self._required_bundle_id())
        if kind == "shortcut":
            if self.shortcut_recorder.key_code is None:
                raise ValueError("Record a shortcut before saving this mapping.")
            return key, DeckAction(
                kind,
                bundle_id=self._required_bundle_id(),
                key_code=self.shortcut_recorder.key_code,
                modifiers=self.shortcut_recorder.modifiers,
            )
        return key, DeckAction(kind)

    @objc.python_method
    def _required_bundle_id(self) -> str:
        if not self.selected_bundle_id:
            raise ValueError("Choose an application before saving this mapping.")
        return self.selected_bundle_id

    @objc.python_method
    def _refresh_summary(self) -> None:
        if not self.settings.bindings:
            text = "No mappings saved."
        else:
            text = "\n".join(_mapping_summary(key, action) for key, action in self.settings.bindings)
        self.summary_field.setStringValue_(text)
        self.summary_field.setAccessibilityValue_(text)


def _mapping_summary(key: int, action: DeckAction) -> str:
    prefix = f"AG{key:02d}: "
    if action.kind == "open_app":
        return prefix + f"Open {_app_label(action.bundle_id)}"
    if action.kind == "shortcut":
        return prefix + (
            f"{_shortcut_text(action.key_code, action.modifiers)} "
            f"in {_app_label(action.bundle_id)}"
        )
    label = next(label for label, kind in ACTION_CHOICES if kind == action.kind)
    return prefix + label


def build_deck_settings_card(target: object, settings: DeckControlSettings | None) -> DeckSettingsPane:
    effective = settings if type(settings) is DeckControlSettings else DeckControlSettings()
    pane = DeckSettingsPane.alloc().initWithTarget_settings_(target, effective)
    if settings is None:
        pane.enable_checkbox.setEnabled_(False)
        pane.save_button.setEnabled_(False)
        pane.set_status("Device action settings are loading or unavailable.")
    return pane


def deck_mapping_selection_changed(controller: object, sender: object) -> None:
    controller.deck_settings_pane.selection_changed(sender)


__all__ = [
    "ACTION_CHOICES",
    "DeckSettingsPane",
    "build_deck_settings_card",
    "deck_mapping_selection_changed",
]
