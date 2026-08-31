from __future__ import annotations

import unittest
from dataclasses import replace
from itertools import pairwise
from unittest.mock import patch

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.alcove_observation import (
    AlcoveCaptureStatus,
    note_alcove_status,
    reset_alcove_status,
    reset_screen_recording_cache,
)
from sidepulse.global_action_settings_pane import (
    GLOBAL_ACTION_GROUP_LABEL,
    GLOBAL_ACTION_RECORDER_HELP,
    GLOBAL_ACTION_RECORDER_LABEL,
    GLOBAL_ACTION_STATUS_LABEL,
)
from sidepulse.global_actions import (
    GlobalActionID,
    ShortcutChord,
    ShortcutModifier,
    serialize_global_action_shortcuts,
)
from tests.test_sidepulse import isolate_controller


class SettingsAccessibilityRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_controller(self)

    @staticmethod
    def _text(view) -> tuple[str, ...]:
        values: list[str] = []
        for child in view.subviews():
            value = getattr(child, "stringValue", None)
            if callable(value):
                text = value()
                if isinstance(text, str) and text:
                    values.append(text)
            values.extend(SettingsAccessibilityRepairTests._text(child))
        return tuple(values)

    @staticmethod
    def _assert_choice(case, thumb) -> None:
        choice = thumb.accessibility_choice_control
        case.assertTrue(choice.acceptsFirstResponder())
        case.assertEqual(choice.accessibilityRole(), "AXRadioButton")
        case.assertTrue(str(choice.accessibilityLabel() or "").strip())
        case.assertTrue(str(choice.accessibilityHelp() or "").strip())
        case.assertFalse(thumb.isAccessibilityElement())

    def test_lid_presets_are_named_keyboard_radio_choices_with_explicit_order(self) -> None:
        for kind, presets in self.status_bar.LID_ANIMATION_PRESETS.items():
            _name, duration, program = presets[0]
            self.controller.settings = self.controller.settings.with_lid_animation(
                kind,
                program=program,
                duration_seconds=duration,
            )
        pane, _fields = self.status_bar._build_lid_animations_pane(self.controller)

        grouped: dict[str, list] = {}
        for (kind, _name), thumb in self.controller.lid_animation_thumbs.items():
            self._assert_choice(self, thumb)
            grouped.setdefault(kind, []).append(thumb.accessibility_choice_control)

        self.assertEqual(set(grouped), set(self.status_bar.LID_ANIMATION_PRESETS))
        for choices in grouped.values():
            self.assertGreater(len(choices), 1)
            for current, following in pairwise(choices):
                self.assertIs(current.nextKeyView(), following)
            self.assertEqual(sum(choice.state() == 1 for choice in choices), 1)
        self.assertTrue(any("press Space" in text for text in self._text(pane)))

        kind = next(iter(self.status_bar.LID_ANIMATION_PRESETS))
        choices = grouped[kind]
        choices[1].performClick_(None)
        _name, _duration, expected_program = self.status_bar.LID_ANIMATION_PRESETS[kind][1]
        self.assertEqual(
            self.controller.settings.lid_animation(kind).program,
            expected_program,
        )

    def test_studio_and_lid_program_editors_have_names_and_descriptions(self) -> None:
        _pane, fields = self.status_bar._build_lid_animations_pane(self.controller)
        editors = (
            self.controller.studio_editor,
            fields["closed_animation_program"],
            fields["open_animation_program"],
        )

        self.assertEqual(
            tuple(editor.accessibilityLabel() for editor in editors),
            (
                "Studio LED program",
                "Closed lid animation program",
                "Open lid animation program",
            ),
        )
        self.assertTrue(all(editor.isSelectable() for editor in editors))
        self.assertTrue(
            all(str(editor.accessibilityHelp() or "").strip() for editor in editors)
        )

    def test_shared_rows_name_the_actual_control_not_only_the_visual_label(self) -> None:
        from sidepulse import native_ui

        field = native_ui.make_field("")
        native_ui.make_row(
            "Duration",
            field,
            help_text="Enter the duration in seconds.",
        )
        _row, switch = native_ui.make_switch_row(
            "Enable ambient signals",
            self.controller,
            "toggleIdleDim:",
            help_text="Allow steady peripheral status signals.",
        )

        self.assertEqual(field.accessibilityLabel(), "Duration")
        self.assertEqual(field.accessibilityHelp(), "Enter the duration in seconds.")
        self.assertEqual(switch.accessibilityLabel(), "Enable ambient signals")
        self.assertEqual(
            switch.accessibilityHelp(),
            "Allow steady peripheral status signals.",
        )

    def test_signal_and_state_preview_choices_have_equivalent_keyboard_semantics(self) -> None:
        _signal_pane, signal_fields, _buttons = (
            self.status_bar._build_led_behavior_pane(self.controller)
        )
        self.status_bar._build_color_studio_pane(self.controller)

        signal_thumbs = [
            thumb
            for key, thumbs in signal_fields.items()
            if key.startswith("signal_thumbs:")
            for thumb in thumbs.values()
        ]
        state_thumbs = [
            thumb
            for thumbs in self.controller.colors_animation_thumbs.values()
            for thumb in thumbs.values()
        ]
        self.assertTrue(signal_thumbs)
        self.assertTrue(state_thumbs)
        for thumb in signal_thumbs + state_thumbs:
            self._assert_choice(self, thumb)

    def test_reduce_motion_uses_static_preview_programs_without_losing_semantics(self) -> None:
        self.status_bar._build_lid_animations_pane(self.controller)
        _signal_pane, signal_fields, _buttons = (
            self.status_bar._build_led_behavior_pane(self.controller)
        )
        self.status_bar._build_color_studio_pane(self.controller)
        self.controller.settings_fields.update(signal_fields)

        thumbs = list(self.controller.lid_animation_thumbs.values())
        thumbs.extend(
            thumb
            for key, values in signal_fields.items()
            if key.startswith("signal_thumbs:")
            for thumb in values.values()
        )
        thumbs.extend(
            thumb
            for values in self.controller.colors_animation_thumbs.values()
            for thumb in values.values()
        )
        self.assertTrue(thumbs)
        self.assertTrue(any("ms" in str(thumb.current_program) for thumb in thumbs))

        self.controller._accessibility_display_preferences = (
            AccessibilityDisplayPreferences(reduce_motion=True)
        )
        self.controller._refresh_lid_thumb_selection()
        for signal_key in self.status_bar.signals_module.DEFAULT_SIGNAL_STYLES:
            self.controller._render_signal_card(
                signal_key,
                self.controller.settings.signal_style(signal_key),
            )
        self.controller.refresh_colors_window()

        for thumb in thumbs:
            self._assert_choice(self, thumb)
            self.assertRegex(str(thumb.current_program), r"^#[0-9A-Fa-f]{6}$")
            self.assertIsNotNone(thumb.fixed_colors)
            self.assertIsNone(thumb.wasm_controller)

    def test_reduce_motion_skips_settings_pane_crossfades(self) -> None:
        self.controller.show_settings_window()
        self.controller._accessibility_display_preferences = (
            AccessibilityDisplayPreferences(reduce_motion=True)
        )

        self.controller.select_settings_pane("power")
        self.assertEqual(self.controller.settings_panes["power"].alphaValue(), 1.0)
        for key, pane in self.controller.settings_panes.items():
            self.assertEqual(pane.isHidden(), key != "power")

    def test_overview_embeds_one_retained_accessible_global_action_recorder(self) -> None:
        self.controller.show_settings_window()
        recorder_pane = self.controller.settings_fields[
            "global_action_settings_pane"
        ]
        profile = self.controller.settings_panes["profile"]

        self.assertTrue(recorder_pane.view.isDescendantOf_(profile))
        self.assertEqual(
            recorder_pane.view.accessibilityLabel(),
            GLOBAL_ACTION_GROUP_LABEL,
        )
        self.assertEqual(
            recorder_pane.recorder.accessibilityLabel(),
            GLOBAL_ACTION_RECORDER_LABEL,
        )
        self.assertEqual(
            recorder_pane.recorder.accessibilityHelp(),
            GLOBAL_ACTION_RECORDER_HELP,
        )
        self.assertEqual(
            recorder_pane.status_field.accessibilityLabel(),
            GLOBAL_ACTION_STATUS_LABEL,
        )
        self.assertEqual(
            recorder_pane.status_field.accessibilityValue(),
            "No shortcut assigned.",
        )
        self.assertIs(recorder_pane.view.nextKeyView(), recorder_pane.recorder)
        self.assertIs(
            recorder_pane.recorder.nextKeyView(),
            recorder_pane.record_button,
        )
        self.assertIs(
            recorder_pane.record_button.nextKeyView(),
            recorder_pane.clear_button,
        )
        self.assertIs(
            recorder_pane.clear_button.nextKeyView(),
            recorder_pane.recorder,
        )

        controls = (
            recorder_pane.view,
            recorder_pane.recorder,
            recorder_pane.status_field,
            recorder_pane.record_button,
            recorder_pane.clear_button,
            recorder_pane.retry_button,
        )
        chord = ShortcutChord(
            key_code=40,
            key_label="K",
            modifiers=frozenset({ShortcutModifier.CONTROL}),
        )
        self.controller.settings = replace(
            self.controller.settings,
            global_action_shortcuts=serialize_global_action_shortcuts(
                {GlobalActionID.REVEAL_CURRENT_ASK: chord}
            ),
        )
        self.controller.refresh_settings_window()

        self.assertEqual(
            controls,
            (
                recorder_pane.view,
                recorder_pane.recorder,
                recorder_pane.status_field,
                recorder_pane.record_button,
                recorder_pane.clear_button,
                recorder_pane.retry_button,
            ),
        )
        self.assertEqual(recorder_pane.recorder.accessibilityValue(), "⌃K")
        self.assertEqual(
            recorder_pane.status_field.accessibilityValue(),
            "Shortcut active.",
        )

    def test_alcove_row_exposes_status_and_permission_action_to_voiceover(self) -> None:
        from sidepulse import settings_window

        reset_alcove_status()
        reset_screen_recording_cache()
        self.addCleanup(reset_alcove_status)
        self.addCleanup(reset_screen_recording_cache)
        note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)
        with patch.object(settings_window, "alcove_follow_blocker", lambda **_kwargs: None):
            self.controller.show_settings_window()
            self.controller.ensure_settings_pane("colors_screen_bar")
            label = self.controller.settings_fields["alcove_follow_status"]
            button = self.controller.settings_buttons["alcove_screen_recording_permission"]

            self.assertEqual(label.accessibilityLabel(), "Permission denied")
            self.assertEqual(label.accessibilityValue(), label.stringValue())
            self.assertTrue(str(label.accessibilityHelp() or "").strip())
            self.assertEqual(button.accessibilityLabel(), "Open Screen Recording Settings")
            self.assertIn("Grant Screen Recording", button.accessibilityHelp())
            self.assertFalse(button.isHidden())

            note_alcove_status(AlcoveCaptureStatus.CAPTURED)
            settings_window.refresh_alcove_follow_controls(self.controller)
            self.assertEqual(label.accessibilityLabel(), "Fresh")
            self.assertTrue(button.isHidden())

    def test_permission_layout_keeps_alcove_subject_visible_and_collapses_action_row(self) -> None:
        from sidepulse import settings_window

        reset_alcove_status()
        reset_screen_recording_cache()
        self.addCleanup(reset_alcove_status)
        self.addCleanup(reset_screen_recording_cache)
        note_alcove_status(AlcoveCaptureStatus.SCREEN_RECORDING_DENIED)
        with patch.object(settings_window, "alcove_follow_blocker", lambda **_kwargs: None):
            self.controller.show_settings_window()
            self.controller.ensure_settings_pane("colors_screen_bar")
            pane = self.controller.settings_panes["colors_screen_bar"]
            status = self.controller.settings_fields["alcove_follow_status"]

            def find_subject(view):
                children = view.subviews()
                if status in children and any(
                    getattr(child, "stringValue", lambda: "")() == "Alcove following"
                    for child in children
                ):
                    return view
                for child in children:
                    found = find_subject(child)
                    if found is not None:
                        return found
                return None

            subject = find_subject(pane)
            self.assertIsNotNone(subject)
            pane.layoutSubtreeIfNeeded()
            if pane.window() is not None:
                pane.window().layoutIfNeeded()
            frame = subject.frame()
            self.assertGreater(frame.size.width, 0.0)
            self.assertGreater(frame.size.height, 0.0)
            self.assertIn(status, subject.subviews())
            action_row = self.controller.settings_fields["alcove_permission_row"]
            self.assertFalse(action_row.isHidden())

            note_alcove_status(AlcoveCaptureStatus.CAPTURED)
            settings_window.refresh_alcove_follow_controls(self.controller)
            self.assertTrue(action_row.isHidden())
            self.assertTrue(self.controller.settings_buttons["alcove_screen_recording_permission"].isHidden())

    def test_announcer_roots_expose_their_distinct_accessible_native_roles(self) -> None:
        from sidepulse.announcer_stack_view import _CollapsedAnnouncerView, _ExpandedAnnouncerView

        collapsed = _CollapsedAnnouncerView.alloc().initWithFrame_(((0.0, 0.0), (220.0, 22.0)))
        expanded = _ExpandedAnnouncerView.alloc().initWithFrame_(((0.0, 0.0), (360.0, 176.0)))
        self.assertTrue(collapsed.isAccessibilityElement())
        self.assertEqual(collapsed.accessibilityRole(), "AXButton")
        self.assertEqual(collapsed.accessibilityLabel(), "Screen Bar announcer")
        self.assertFalse(collapsed.acceptsFirstResponder())
        self.assertTrue(expanded.isAccessibilityElement())
        self.assertEqual(expanded.accessibilityRole(), "AXGroup")
        self.assertTrue(expanded.acceptsFirstResponder())


if __name__ == "__main__":
    unittest.main()
