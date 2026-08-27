"""The guided calibration flow: one question at a time, sliders last.

The calibration popover used to front-load everything -- a paragraph of
instructions, four reference patches, three RGB sliders. It worked, but
it asked the user to be a colorimeter. The stepper asks the one question
that matters ("does the light look white?") with one-tap coarse answers,
and only reveals the fine sliders when the nudges are not enough
(2026-08-26 flow rework). Before/after compare is one button.

View construction only: every action targets the controller, and the
control-map keys ("red_slider" etc.) are unchanged so the existing
refresh path keeps updating them.
"""

from __future__ import annotations

from AppKit import NSColor

from . import native_ui


def build_calibration_flow_content(device, target):
    from .settings_window import CALIBRATION_TEST_PATCHES, calibration_summary_text
    from .status_bar import (
        MAX_CHANNEL_GAIN,
        MIN_CHANNEL_GAIN,
        add_color_swatch,
    )

    del calibration_summary_text  # summary stays beside the button, not here
    stack = native_ui.make_stack(orientation="vertical", spacing=14.0)
    native_ui.constrain_width(stack, 300.0)

    stack.addArrangedSubview_(
        native_ui.make_label(
            f"Color Calibration — {device.name}",
            bold=True,
            size=13.0,
        )
    )
    stack.addArrangedSubview_(
        native_ui.make_label(
            "Every LED is now showing true white.\n"
            "Hold the device beside your screen.",
            secondary=True,
            size=11.0,
        )
    )

    question = native_ui.make_label(
        "Does the light look white to you?",
        bold=True,
        size=12.0,
    )
    stack.addArrangedSubview_(question)

    nudges = native_ui.make_stack(orientation="horizontal", spacing=8.0)
    warm_button = native_ui.make_button(
        "Too warm", target, "nudgeCalibrationWarmth:"
    )
    warm_button.setRepresentedObject_(
        {"device_id": device.device_id, "fix": "cooler"}
    )
    done_button = native_ui.make_button(
        "Looks white ✓", target, "calibrationLooksWhite:"
    )
    done_button.setRepresentedObject_(device.device_id)
    cool_button = native_ui.make_button(
        "Too cool", target, "nudgeCalibrationWarmth:"
    )
    cool_button.setRepresentedObject_(
        {"device_id": device.device_id, "fix": "warmer"}
    )
    for button in (warm_button, done_button, cool_button):
        nudges.addArrangedSubview_(button)
    stack.addArrangedSubview_(nudges)

    matched = native_ui.make_label(
        "Matched. The light now follows your eye.",
        secondary=True,
        size=11.0,
    )
    matched.setHidden_(True)
    stack.addArrangedSubview_(matched)

    native_ui.add_separator(stack)

    # The reference patches stay for fine-tuning by eye.
    patch_size = 26
    patch_gap = 10
    patches_width = len(CALIBRATION_TEST_PATCHES) * (patch_size + patch_gap) - patch_gap
    patches = native_ui.make_fixed_area(float(patches_width), float(patch_size))
    x = 0
    for _label, hex_color in CALIBRATION_TEST_PATCHES:
        add_color_swatch(
            patches,
            hex_color,
            x,
            1,
            target,
            "startCalibrationTest:",
            {"device_id": device.device_id, "hex": hex_color},
        )
        x += patch_size + patch_gap

    fine_section = native_ui.make_stack(orientation="vertical", spacing=10.0)
    fine_section.addArrangedSubview_(patches)
    controls: dict[str, object] = {}
    red, green, blue = device.channel_gains
    for label, channel, gain, action, tint in (
        ("Red", "red", red, "setDeviceRedGain:", NSColor.systemRedColor()),
        ("Green", "green", green, "setDeviceGreenGain:", NSColor.systemGreenColor()),
        ("Blue", "blue", blue, "setDeviceBlueGain:", NSColor.systemBlueColor()),
    ):
        slider = native_ui.make_slider(
            min_value=MIN_CHANNEL_GAIN * 100.0,
            max_value=MAX_CHANNEL_GAIN * 100.0,
            value=gain * 100.0,
            target=target,
            action=action,
            identifier=device.device_id,
        )
        native_ui.constrain_width(slider, 200.0)
        try:
            slider.setTrackFillColor_(tint)
        except Exception:
            pass
        fine_section.addArrangedSubview_(native_ui.make_row(label, slider))
        controls[f"{channel}_slider"] = slider
    fine_section.setHidden_(True)
    stack.addArrangedSubview_(fine_section)

    fine_toggle = native_ui.make_button(
        "Fine-tune by eye…", target, "toggleCalibrationFineTune:"
    )
    fine_toggle.setRepresentedObject_(device.device_id)
    compare_button = native_ui.make_button(
        "Compare with before", target, "toggleCalibrationCompare:"
    )
    compare_button.setRepresentedObject_(device.device_id)
    row = native_ui.make_stack(orientation="horizontal", spacing=8.0)
    row.addArrangedSubview_(fine_toggle)
    row.addArrangedSubview_(compare_button)
    stack.addArrangedSubview_(row)

    native_ui.add_separator(stack)
    auto_checkbox = native_ui.make_checkbox(
        "Auto-Brightness (matches screen)", target, "toggleDeviceAutoBrightness:"
    )
    auto_checkbox.setRepresentedObject_(device.device_id)
    auto_checkbox.setState_(1 if device.auto_brightness_enabled else 0)
    stack.addArrangedSubview_(auto_checkbox)
    reset_button = native_ui.make_button(
        "Reset to Default", target, "resetDeviceColorCalibration:"
    )
    reset_button.setRepresentedObject_(device.device_id)
    stack.addArrangedSubview_(reset_button)

    controls.update(
        {
            "auto_brightness_checkbox": auto_checkbox,
            "reset_button": reset_button,
            "calibration_fine_section": fine_section,
            "calibration_matched_label": matched,
            "calibration_compare_button": compare_button,
        }
    )
    return stack, controls
