from __future__ import annotations

from types import SimpleNamespace

from AppKit import (
    NSEvent,
    NSEventModifierFlagCommand,
    NSEventModifierFlagShift,
    NSEventTypeKeyDown,
)

from sidepulse.deck_actions import DeckAction
from sidepulse.deck_control_settings import DeckControlSettings
from sidepulse.deck_settings_pane import (
    ACTION_CHOICES,
    build_deck_settings_card,
    deck_mapping_selection_changed,
)


class _Event:
    def keyCode(self):
        return 8

    def charactersIgnoringModifiers(self):
        return "c"

    def modifierFlags(self):
        return NSEventModifierFlagCommand | NSEventModifierFlagShift


def _target(settings):
    return SimpleNamespace(_deck_control_settings=settings)


def test_card_exposes_bounded_actions_and_explicit_keymap_setup() -> None:
    pane = build_deck_settings_card(_target(DeckControlSettings()), DeckControlSettings())

    assert [label for label, _kind in ACTION_CHOICES] == [
        "Disabled",
        "Open app",
        "App shortcut",
        "Reveal current ask",
        "Agent browser",
        "Usage Center",
    ]
    assert pane.view.accessibilityLabel() == "Agent Deck controls"
    assert "active device layer" in pane.hardware_note.stringValue()
    assert "preview keymap changes before applying" in pane.hardware_note.stringValue()
    assert "Creator Micro master connection toggle" in pane.hardware_note.stringValue()
    assert pane.enable_checkbox.accessibilityLabel() == "Enable device actions"
    assert pane.key_popup.numberOfItems() == 20
    assert pane.key_popup.itemAtIndex_(0).title() == "AG00"
    assert pane.key_popup.itemAtIndex_(19).title() == "AG19"
    assert pane.description_field.stringValue() == "Choose what each device key does."
    assert pane.save_button.superview() is pane.save_row
    assert pane.inspect_setup_button.title() == "Inspect device setup…"
    assert pane.restore_setup_button.title() == "Restore device keymap…"
    assert pane.inspect_setup_button.target() is pane.target
    assert pane.restore_setup_button.target() is pane.target


def test_setup_buttons_disable_together_while_a_device_job_is_active() -> None:
    pane = build_deck_settings_card(_target(DeckControlSettings()), DeckControlSettings())

    pane.set_setup_pending(True)

    assert pane.inspect_setup_button.isEnabled() is False
    assert pane.restore_setup_button.isEnabled() is False


def test_existing_mapping_is_selected_and_summarized_without_numeric_key_codes() -> None:
    action = DeckAction("shortcut", "com.apple.Safari", 8, ("shift", "command"))
    settings = DeckControlSettings(True, ((3, action),))
    pane = build_deck_settings_card(_target(settings), settings)
    pane.key_popup.selectItemAtIndex_(3)

    deck_mapping_selection_changed(SimpleNamespace(deck_settings_pane=pane), pane.key_popup)

    assert pane.action_popup.titleOfSelectedItem() == "App shortcut"
    assert pane.application_button.title() == "Safari"
    assert pane.application_button.toolTip() == "com.apple.Safari"
    assert pane.summary_field.stringValue() == "AG03: ⇧⌘C in Safari"
    assert "8" not in pane.summary_field.stringValue()


def test_shortcut_recorder_builds_app_scoped_action_with_a_readable_chord() -> None:
    pane = build_deck_settings_card(_target(DeckControlSettings()), DeckControlSettings())
    pane.key_popup.selectItemAtIndex_(5)
    pane.action_popup.selectItemAtIndex_(2)
    pane.selected_bundle_id = "com.apple.TextEdit"

    pane.shortcut_recorder.begin_recording()
    pane.shortcut_recorder.keyDown_(_Event())

    key, action = pane.selected_mapping()
    assert key == 5
    assert action == DeckAction("shortcut", "com.apple.TextEdit", 8, ("shift", "command"))
    assert pane.shortcut_recorder.accessibilityValue() == "⇧⌘C"


def test_disabled_choice_removes_the_selected_mapping() -> None:
    action = DeckAction("open_usage")
    settings = DeckControlSettings(True, ((1, action),))
    pane = build_deck_settings_card(_target(settings), settings)
    pane.key_popup.selectItemAtIndex_(1)
    pane.action_popup.selectItemAtIndex_(0)

    assert pane.selected_mapping() == (1, None)


def test_command_shortcut_is_consumed_as_a_key_equivalent_before_menu_dispatch() -> None:
    pane = build_deck_settings_card(_target(DeckControlSettings()), DeckControlSettings())
    pane.action_popup.selectItemAtIndex_(2)
    pane.selected_bundle_id = "com.apple.TextEdit"
    event = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        NSEventTypeKeyDown,
        (0.0, 0.0),
        NSEventModifierFlagCommand,
        0.0,
        0,
        None,
        "q",
        "q",
        False,
        12,
    )

    pane.shortcut_recorder.begin_recording()

    assert pane.shortcut_recorder.performKeyEquivalent_(event) is True
    assert pane.shortcut_recorder.accessibilityValue() == "⌘Q"


def test_changing_mapping_selection_stops_shortcut_recording() -> None:
    pane = build_deck_settings_card(_target(DeckControlSettings()), DeckControlSettings())
    pane.shortcut_recorder.begin_recording()

    pane.key_popup.selectItemAtIndex_(1)
    pane.selection_changed(pane.key_popup)

    assert pane.shortcut_recorder.recording is False
    assert pane.shortcut_recorder.accessibilityValue() == "Not recorded"
