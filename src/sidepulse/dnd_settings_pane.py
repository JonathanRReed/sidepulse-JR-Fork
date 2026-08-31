"""Native Focus and Do Not Disturb Settings pane.

This module owns view construction only. The retained status-bar controller owns
all writes through the selectors in ``DND_SETTINGS_SELECTORS`` and exposes read
truth through ``settings``, ``dnd_controller``, and ``active_focus_summary``.
No control writes Settings or requests Focus authorization by itself.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from AppKit import (
    NSDatePicker,
    NSDatePickerElementFlagHourMinute,
    NSDatePickerModeSingle,
    NSDatePickerStyleTextFieldAndStepper,
)
from Foundation import (
    NSCalendar,
    NSCalendarUnitHour,
    NSCalendarUnitMinute,
    NSDate,
    NSDateComponents,
    NSDateFormatter,
    NSDateFormatterMediumStyle,
    NSDateFormatterShortStyle,
)

from . import focus_sync, native_ui
from .app_bundle import default_app_bundle_path, running_inside_bundle
from .dnd_policy import DndMode, DndProjection, DndSource, compose_dnd_contributions
from .focus_status import FocusActivity, FocusAuthorization, FocusStatusObservation
from .product_identity import PRODUCT_DISPLAY_NAME
from .settings import CALIBRATION_PROFILE_SLOTS
from .settings_window_controls import log_status_bar


@dataclass(frozen=True, slots=True)
class DndModeOption:
    mode: DndMode
    label: str
    description: str


DND_MODE_OPTIONS: Final[tuple[DndModeOption, ...]] = (
    DndModeOption(
        DndMode.MUTE,
        "Mute",
        "Keep the lights visible, but hold banners, sounds, and notification webhooks.",
    ),
    DndModeOption(
        DndMode.DIM,
        "Dim",
        "Keep current light state and signals visible at the configured DND brightness.",
    ),
    DndModeOption(
        DndMode.PAUSE,
        "Pause",
        "Hold routine activity while critical asks, failures, and low battery may remain.",
    ),
    DndModeOption(
        DndMode.ASKS_ONLY,
        "Asks Only",
        "Show only a current actionable ask and its escalation.",
    ),
    DndModeOption(
        DndMode.DARK,
        "Fully Dark",
        "Withhold LEDs, Screen Bar visuals, announcers, finite cues, and notifications.",
    ),
)

DND_SETTINGS_SELECTORS: Final[dict[str, str]] = {
    "schedule_enabled": "toggleDndSchedule:",
    "schedule_start": "setDndScheduleStartTime:",
    "schedule_end": "setDndScheduleEndTime:",
    "schedule_mode": "setDndScheduleMode:",
    "dim_fraction": "setDndDimFraction:",
    "follow_focus": "toggleFocusSync:",
    "focus_mode": "setDndFocusMode:",
    "focus_authorization": "requestDndFocusAuthorization:",
    "one_hour": "startDndOneHour:",
    "resume": "resumeDndUntilNextChange:",
    "end_override": "endDndOverride:",
}

FOCUS_DIM_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("Shared dim (default)", "default"),
    ("Don't dim", "1.0"),
    ("Dim to 50%", "0.5"),
    ("Dim to 25%", "0.25"),
    ("Turn off", "0.0"),
)

_DND_DIM_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("Dim to 15%", "0.15"),
    ("Dim to 25%", "0.25"),
    ("Dim to 30%", "0.3"),
    ("Dim to 50%", "0.5"),
)

_AUTHORIZATION_HELP: Final = (
    "JR Bar reads only whether a macOS Focus is active. Apple does not expose "
    "the active Focus name through this public permission."
)
_STATUS_HELP: Final = (
    "Shows the active DND sources and the exact time the next scheduled or "
    "temporary change will occur. DND changes presentation, not agent state."
)


@dataclass(frozen=True, slots=True)
class DndSettingsPaneControls:
    fields: dict[str, object]
    buttons: dict[str, object]
    per_focus_container: object
    focus_roster_fingerprint: tuple[tuple[str, str], ...]


def _select_represented_value(popup, wanted: str) -> None:
    for index in range(popup.numberOfItems()):
        item = popup.itemAtIndex_(index)
        if str(item.representedObject() or "") == wanted:
            popup.selectItem_(item)
            return


def _set_switch_state(control, enabled: bool) -> None:
    if control is not None:
        control.setState_(1 if enabled else 0)


def set_time_picker_minutes(picker, minutes: int) -> None:
    if type(minutes) is not int or not 0 <= minutes < 24 * 60:
        raise ValueError("DND time picker minutes must be from 0 through 1439")
    calendar = NSCalendar.currentCalendar()
    components = NSDateComponents.alloc().init()
    components.setYear_(2001)
    components.setMonth_(1)
    components.setDay_(15)
    components.setHour_(minutes // 60)
    components.setMinute_(minutes % 60)
    components.setSecond_(0)
    value = calendar.dateFromComponents_(components)
    if value is None:
        raise RuntimeError("Could not create a local DND time value")
    picker.setDateValue_(value)


def minutes_from_time_picker(picker) -> int:
    components = NSCalendar.currentCalendar().components_fromDate_(
        NSCalendarUnitHour | NSCalendarUnitMinute,
        picker.dateValue(),
    )
    return int(components.hour()) * 60 + int(components.minute())


def _make_time_picker(target, selector: str, minutes: int, identifier: str):
    picker = NSDatePicker.alloc().init()
    picker.setDatePickerStyle_(NSDatePickerStyleTextFieldAndStepper)
    picker.setDatePickerMode_(NSDatePickerModeSingle)
    picker.setDatePickerElements_(NSDatePickerElementFlagHourMinute)
    picker.setTarget_(target)
    picker.setAction_(selector)
    picker.setIdentifier_(identifier)
    set_time_picker_minutes(picker, minutes)
    native_ui.constrain_width(picker, 116.0)
    return picker


def _make_mode_popup(target, selector: str, selected: DndMode):
    popup = native_ui.make_popup_button(target, selector)
    for option in DND_MODE_OPTIONS:
        popup.addItemWithTitle_(option.label)
        item = popup.lastItem()
        item.setRepresentedObject_(option.mode.value)
        item.setToolTip_(option.description)
        if option.mode is selected:
            popup.selectItem_(item)
    return popup


def _make_dim_popup(target, selected: float):
    popup = native_ui.make_popup_button(target, DND_SETTINGS_SELECTORS["dim_fraction"])
    for label, value in _DND_DIM_CHOICES:
        popup.addItemWithTitle_(label)
        item = popup.lastItem()
        item.setRepresentedObject_(value)
        if abs(float(value) - selected) < 1e-9:
            popup.selectItem_(item)
    return popup


def _format_transition_epoch(epoch: float) -> str:
    formatter = NSDateFormatter.alloc().init()
    formatter.setDateStyle_(NSDateFormatterMediumStyle)
    formatter.setTimeStyle_(NSDateFormatterShortStyle)
    return str(
        formatter.stringFromDate_(NSDate.dateWithTimeIntervalSince1970_(epoch))
    )


def dnd_status_text(
    projection: DndProjection,
    *,
    format_epoch: Callable[[float], str] = _format_transition_epoch,
) -> str:
    if type(projection) is not DndProjection:
        raise TypeError("DND Settings status requires a canonical projection")
    deadline = projection.next_transition_epoch
    if deadline is None:
        return projection.summary
    return f"{projection.summary}. Next change: {format_epoch(deadline)}."


def focus_authorization_text(observation: FocusStatusObservation) -> str:
    if type(observation) is not FocusStatusObservation:
        raise TypeError("Focus Settings requires a typed observation")
    authorization = observation.authorization
    if authorization is FocusAuthorization.AUTHORIZED:
        if observation.activity is FocusActivity.ACTIVE:
            return "Focus status is allowed and a Focus is active."
        if observation.activity is FocusActivity.INACTIVE:
            return "Focus status is allowed and no Focus is active."
        return "Focus status is allowed, but its current activity is unavailable."
    if authorization is FocusAuthorization.NOT_DETERMINED:
        return "Focus status access has not been requested."
    if authorization is FocusAuthorization.DENIED:
        return (
            "Focus status access is denied. JR Bar will not claim that Focus is active."
        )
    if authorization is FocusAuthorization.RESTRICTED:
        return "Focus status access is restricted on this Mac."
    return "Focus status is unavailable on this Mac."


def select_focus_dim_choice(popup, fraction: float | None) -> None:
    wanted = "default" if fraction is None else f"{float(fraction):g}"
    _select_represented_value(popup, wanted)


def make_focus_dim_popup(target, mode_identifier: str):
    popup = native_ui.make_popup_button(target, "setFocusDimRule:")
    popup.setIdentifier_(mode_identifier)
    current = target.settings.focus_dim_rules.get(mode_identifier)
    current_key = "default" if current is None else f"{current:g}"
    for label, key in FOCUS_DIM_CHOICES:
        popup.addItemWithTitle_(label)
        item = popup.lastItem()
        item.setRepresentedObject_(key)
        if key == current_key:
            popup.selectItem_(item)
    return popup


def _controller_truth(target) -> tuple[DndProjection, FocusStatusObservation]:
    controller = getattr(target, "dnd_controller", None)
    projection = getattr(controller, "projection", None)
    if type(projection) is not DndProjection:
        projection = compose_dnd_contributions(())
    observation = getattr(controller, "focus_observation", None)
    if type(observation) is not FocusStatusObservation:
        observation = FocusStatusObservation(
            FocusAuthorization.UNAVAILABLE,
            FocusActivity.UNAVAILABLE,
        )
    return projection, observation


def _refresh_dnd_card(
    target,
    fields: dict[str, object],
    buttons: dict[str, object],
    *,
    now_epoch: float,
) -> None:
    parsed = target.settings.dnd_settings()
    projection, observation = _controller_truth(target)
    schedule = parsed.schedule

    _set_switch_state(buttons.get("dnd_schedule_enabled"), schedule.enabled)
    start = fields.get("dnd_schedule_start_time")
    end = fields.get("dnd_schedule_end_time")
    schedule_mode = fields.get("dnd_schedule_mode")
    if start is not None:
        set_time_picker_minutes(start, schedule.start_minutes)
        start.setEnabled_(schedule.enabled)
    if end is not None:
        set_time_picker_minutes(end, schedule.end_minutes)
        end.setEnabled_(schedule.enabled)
    if schedule_mode is not None:
        _select_represented_value(schedule_mode, schedule.mode.value)
        schedule_mode.setEnabled_(schedule.enabled)

    dim = fields.get("dnd_dim_fraction")
    if dim is not None:
        _select_represented_value(dim, f"{parsed.dim_fraction:g}")

    follow_focus = bool(target.settings.focus_sync_enabled)
    _set_switch_state(buttons.get("focus_sync_enabled"), follow_focus)
    focus_mode = fields.get("dnd_focus_mode")
    if focus_mode is not None:
        _select_represented_value(focus_mode, parsed.focus_mode.value)
        focus_mode.setEnabled_(follow_focus)

    status = fields.get("dnd_status_label")
    if status is not None:
        text = dnd_status_text(projection)
        status.setStringValue_(text)
        status.setAccessibilityValue_(text)
        status.setAccessibilityHelp_(f"{_STATUS_HELP} {projection.reason}")

    authorization_status = fields.get("dnd_focus_authorization_status")
    if authorization_status is not None:
        text = focus_authorization_text(observation)
        authorization_status.setStringValue_(text)
        authorization_status.setAccessibilityValue_(text)

    authorization = buttons.get("dnd_focus_authorization")
    if authorization is not None:
        authorized = observation.authorization is FocusAuthorization.AUTHORIZED
        authorization.setHidden_(authorized)
        authorization.setEnabled_(
            observation.authorization is FocusAuthorization.NOT_DETERMINED
        )

    refusal = fields.get("dnd_refusal_status")
    if refusal is not None:
        refusal_count = len(parsed.refusals)
        refusal_text = (
            ""
            if not refusal_count
            else (
                f"{refusal_count} saved DND setting"
                f"{' was' if refusal_count == 1 else 's were'} refused. "
                "Valid DND settings remain active."
            )
        )
        refusal.setStringValue_(refusal_text)
        refusal.setAccessibilityValue_(refusal_text)
        refusal.setHidden_(not refusal_text)

    override = parsed.override
    override_active = override is not None and override.active_at(now_epoch)
    resume = buttons.get("dnd_resume")
    if resume is not None:
        schedule_active = DndSource.SCHEDULE in projection.active_sources
        already_resumed = bool(override_active and override is not None and override.resume)
        resume.setEnabled_(schedule_active and not already_resumed)
    end_override = buttons.get("dnd_end_override")
    if end_override is not None:
        end_override.setEnabled_(override_active)


def _make_dnd_card(target, fields: dict[str, object], buttons: dict[str, object]):
    parsed = target.settings.dnd_settings()
    outer, inner = native_ui.make_card("Do Not Disturb")
    inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Choose exactly what JR Bar may present. Agent state, history, and "
            "the Agent Browser continue in every mode.",
            secondary=True,
            size=12.0,
            max_width=560.0,
        )
    )
    inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "\n".join(
                f"{option.label}: {option.description}"
                for option in DND_MODE_OPTIONS
            ),
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )

    status = native_ui.make_wrapping_label("", size=13.0, max_width=560.0)
    status.setSelectable_(True)
    native_ui.set_accessibility_metadata(
        status,
        label="Do Not Disturb status",
        help_text=_STATUS_HELP,
        role="AXStaticText",
    )
    status.setAccessibilityValue_("")
    inner.addArrangedSubview_(status)
    fields["dnd_status_label"] = status

    refusal = native_ui.make_wrapping_label("", secondary=True, size=11.0, max_width=560.0)
    native_ui.set_accessibility_metadata(
        refusal,
        label="Do Not Disturb settings status",
        help_text="Reports saved DND values that JR Bar refused without deleting valid settings.",
        role="AXStaticText",
    )
    refusal.setAccessibilityValue_("")
    refusal.setHidden_(True)
    inner.addArrangedSubview_(refusal)
    fields["dnd_refusal_status"] = refusal

    native_ui.add_separator(inner)
    schedule_row, schedule_switch = native_ui.make_switch_row(
        "Use a daily DND schedule",
        target,
        DND_SETTINGS_SELECTORS["schedule_enabled"],
        help_text=(
            "Runs one local-time schedule each day. Overnight schedules are supported."
        ),
    )
    inner.addArrangedSubview_(schedule_row)
    buttons["dnd_schedule_enabled"] = schedule_switch

    start = _make_time_picker(
        target,
        DND_SETTINGS_SELECTORS["schedule_start"],
        parsed.schedule.start_minutes,
        "dnd_schedule_start",
    )
    inner.addArrangedSubview_(
        native_ui.make_row(
            "Starts",
            start,
            help_text="Local time when the daily DND schedule begins.",
        )
    )
    fields["dnd_schedule_start_time"] = start

    end = _make_time_picker(
        target,
        DND_SETTINGS_SELECTORS["schedule_end"],
        parsed.schedule.end_minutes,
        "dnd_schedule_end",
    )
    inner.addArrangedSubview_(
        native_ui.make_row(
            "Ends",
            end,
            help_text="Local time when the daily DND schedule ends.",
        )
    )
    fields["dnd_schedule_end_time"] = end

    schedule_mode = _make_mode_popup(
        target,
        DND_SETTINGS_SELECTORS["schedule_mode"],
        parsed.schedule.mode,
    )
    inner.addArrangedSubview_(
        native_ui.make_row(
            "Scheduled mode",
            schedule_mode,
            help_text="What JR Bar presents while the daily schedule is active.",
        )
    )
    fields["dnd_schedule_mode"] = schedule_mode

    dim = _make_dim_popup(target, parsed.dim_fraction)
    inner.addArrangedSubview_(
        native_ui.make_row(
            "Dim brightness",
            dim,
            help_text="Brightness used whenever any DND source selects Dim.",
        )
    )
    fields["dnd_dim_fraction"] = dim

    native_ui.add_separator(inner)
    follow_row, follow_switch = native_ui.make_switch_row(
        "Follow macOS Focus",
        target,
        DND_SETTINGS_SELECTORS["follow_focus"],
        help_text=_AUTHORIZATION_HELP,
    )
    inner.addArrangedSubview_(follow_row)
    buttons["focus_sync_enabled"] = follow_switch

    focus_mode = _make_mode_popup(
        target,
        DND_SETTINGS_SELECTORS["focus_mode"],
        parsed.focus_mode,
    )
    inner.addArrangedSubview_(
        native_ui.make_row(
            "Focus mode",
            focus_mode,
            help_text="What JR Bar presents while macOS reports that a Focus is active.",
        )
    )
    fields["dnd_focus_mode"] = focus_mode

    authorization_status = native_ui.make_wrapping_label(
        "",
        secondary=True,
        size=11.0,
        max_width=560.0,
    )
    native_ui.set_accessibility_metadata(
        authorization_status,
        label="Focus status authorization",
        help_text=_AUTHORIZATION_HELP,
        role="AXStaticText",
    )
    authorization_status.setAccessibilityValue_("")
    inner.addArrangedSubview_(authorization_status)
    fields["dnd_focus_authorization_status"] = authorization_status

    authorization = native_ui.make_button(
        "Allow Focus Status…",
        target,
        DND_SETTINGS_SELECTORS["focus_authorization"],
    )
    native_ui.set_accessibility_metadata(
        authorization,
        label="Allow Focus Status",
        help_text=(
            "Ask macOS for permission to report whether a Focus is active. "
            "This button is the only action that can show the system prompt."
        ),
    )
    authorization_row = native_ui.make_stack(
        orientation="horizontal",
        spacing=native_ui.SPACE_S,
    )
    authorization_row.addArrangedSubview_(authorization)
    authorization_row.addArrangedSubview_(native_ui.make_hspacer())
    inner.addArrangedSubview_(authorization_row)
    buttons["dnd_focus_authorization"] = authorization

    native_ui.add_separator(inner)
    inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Start a one-hour override. Its exact return time appears in the status above.",
            secondary=True,
            size=11.0,
            max_width=560.0,
        )
    )
    first_actions = native_ui.make_stack(
        orientation="horizontal",
        spacing=native_ui.SPACE_XS,
    )
    second_actions = native_ui.make_stack(
        orientation="horizontal",
        spacing=native_ui.SPACE_XS,
    )
    for index, option in enumerate(DND_MODE_OPTIONS):
        button = native_ui.make_button(
            f"{option.label} 1 Hour",
            target,
            DND_SETTINGS_SELECTORS["one_hour"],
        )
        button.setIdentifier_(option.mode.value)
        native_ui.set_accessibility_metadata(
            button,
            label=f"Start {option.label} for one hour",
            help_text=f"{option.description} Returns automatically after one hour.",
        )
        (first_actions if index < 3 else second_actions).addArrangedSubview_(button)
        buttons[f"dnd_temporary_mode:{option.mode.value}"] = button
    first_actions.addArrangedSubview_(native_ui.make_hspacer())
    second_actions.addArrangedSubview_(native_ui.make_hspacer())
    inner.addArrangedSubview_(first_actions)
    inner.addArrangedSubview_(second_actions)

    override_actions = native_ui.make_stack(
        orientation="horizontal",
        spacing=native_ui.SPACE_S,
    )
    resume = native_ui.make_button(
        "Resume Until Next Change",
        target,
        DND_SETTINGS_SELECTORS["resume"],
    )
    native_ui.set_accessibility_metadata(
        resume,
        label="Resume until next scheduled change",
        help_text=(
            "Temporarily suppress the active local schedule. An active macOS Focus still applies."
        ),
    )
    end_override = native_ui.make_button(
        "End Temporary Override",
        target,
        DND_SETTINGS_SELECTORS["end_override"],
    )
    native_ui.set_accessibility_metadata(
        end_override,
        label="End temporary DND override",
        help_text="Clear the active one-hour mode or temporary Resume override now.",
    )
    override_actions.addArrangedSubview_(resume)
    override_actions.addArrangedSubview_(end_override)
    override_actions.addArrangedSubview_(native_ui.make_hspacer())
    inner.addArrangedSubview_(override_actions)
    buttons["dnd_resume"] = resume
    buttons["dnd_end_override"] = end_override
    return outer


def _make_night_card(target, fields: dict[str, object], buttons: dict[str, object]):
    outer, inner = native_ui.make_card("Night Warmth")
    row, switch = native_ui.make_switch_row(
        "Warm the lights from 7 PM to 7 AM",
        target,
        "toggleNightWarmth:",
        help_text=(
            "Eases green and blue down after dark, like Night Shift for your LEDs. "
            "Composes with each device's calibration."
        ),
    )
    inner.addArrangedSubview_(row)
    buttons["night_warmth_enabled"] = switch
    native_ui.add_separator(inner)
    popup = native_ui.make_popup_button(target, "setNightDimFraction:")
    for label, key in (
        ("Don't dim at night", "1.0"),
        ("Dim to 50%", "0.5"),
        ("Dim to 30%", "0.3"),
        ("Dim to 15%", "0.15"),
    ):
        popup.addItemWithTitle_(label)
        popup.lastItem().setRepresentedObject_(key)
        if abs(float(key) - target.settings.night_dim_fraction) < 1e-9:
            popup.selectItem_(popup.lastItem())
    inner.addArrangedSubview_(
        native_ui.make_row("Night brightness (7 PM to 7 AM)", popup)
    )
    fields["night_dim_popup"] = popup
    return outer


def _make_timebox_card(target, fields: dict[str, object]):
    from .status_bar_legacy import TIMEBOX_PRESET_MINUTES

    outer, inner = native_ui.make_card("Timebox Focus Handshake")
    inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Each Timer preset can run a Shortcut when it starts and another when "
            "it ends or you press Stop. Name a Shortcut that turns a Focus on and "
            "its partner that turns it off. macOS asks permission once per Shortcut.",
            secondary=True,
            size=12.0,
            max_width=560.0,
        )
    )
    for preset_index, preset_minutes in enumerate(TIMEBOX_PRESET_MINUTES):
        pair = target.settings.timebox_shortcut_pair(str(preset_minutes))
        on_field = native_ui.make_field(
            pair[0],
            target=target,
            action="applyTimeboxShortcuts:",
        )
        on_field.setPlaceholderString_("Shortcut at start")
        native_ui.constrain_width(on_field, 150.0)
        off_field = native_ui.make_field(
            pair[1],
            target=target,
            action="applyTimeboxShortcuts:",
        )
        off_field.setPlaceholderString_("Shortcut at end")
        native_ui.constrain_width(off_field, 150.0)
        cluster = native_ui.make_stack(
            orientation="horizontal",
            spacing=native_ui.SPACE_XS,
        )
        cluster.addArrangedSubview_(on_field)
        cluster.addArrangedSubview_(off_field)
        inner.addArrangedSubview_(
            native_ui.make_row(f"{preset_minutes} minutes", cluster)
        )
        if preset_index < len(TIMEBOX_PRESET_MINUTES) - 1:
            native_ui.add_separator(inner)
        fields[f"timebox_on_field:{preset_minutes}"] = on_field
        fields[f"timebox_off_field:{preset_minutes}"] = off_field
    return outer


def _configured_focus_modes() -> tuple[tuple[str, str], ...]:
    try:
        focus_modes = tuple(focus_sync.configured_focus_modes() or ())
        log_status_bar(f"focus roster: {len(focus_modes)} mode(s)")
        return focus_modes
    except focus_sync.FocusSyncUnavailableError as exc:
        log_status_bar(f"focus roster unavailable: {exc}")
        return ()


def _clear_per_focus_controls(
    fields: dict[str, object],
    buttons: dict[str, object],
) -> None:
    for key in tuple(fields):
        if key.startswith(
            (
                "focus_rule_popup:",
                "focus_profile_popup:",
                "focus_signal_popup:",
            )
        ):
            del fields[key]
    fields.pop("focus_fda_path_label", None)
    buttons.pop("focus_fda_open", None)
    buttons.pop("focus_fda_reveal", None)


def _populate_per_focus_card(
    target,
    inner,
    fields: dict[str, object],
    buttons: dict[str, object],
    focus_modes: tuple[tuple[str, str], ...],
) -> None:
    for view in tuple(inner.arrangedSubviews()):
        inner.removeArrangedSubview_(view)
        view.removeFromSuperview()
    _clear_per_focus_controls(fields, buttons)

    inner.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Optional named Focus detail can dim, turn the lights off, apply a "
            "calibration profile, or limit signals. The public Focus permission "
            "above remains the authority for whether a Focus is active.",
            secondary=True,
            size=12.0,
            max_width=560.0,
        )
    )
    if not focus_modes:
        if running_inside_bundle():
            grant_target = str(default_app_bundle_path())
            grant_instructions = (
                f"In Privacy Settings, click + and pick {PRODUCT_DISPLAY_NAME} from your "
                f"Applications folder. If {PRODUCT_DISPLAY_NAME} is already listed, "
                "remove it first, then add it again. This pane fills with your named "
                "Focus modes once granted."
            )
        else:
            grant_target = os.path.realpath(sys.executable or "python3")
            grant_instructions = (
                "In Privacy Settings, click +, press Command-Shift-G, and paste that "
                "path. This pane fills with your named Focus modes once granted."
            )
        inner.addArrangedSubview_(
            native_ui.make_wrapping_label(
                f"Named per-Focus rules need Full Disk Access, granted to "
                f"{PRODUCT_DISPLAY_NAME} itself:",
                secondary=True,
                size=12.0,
                max_width=500.0,
            )
        )
        interpreter = native_ui.make_label(grant_target, secondary=True, size=11.0)
        interpreter.setSelectable_(True)
        native_ui.set_accessibility_metadata(
            interpreter,
            label="Full Disk Access permission target",
            help_text="Copy this exact app or program path into macOS Privacy Settings.",
            role="AXStaticText",
        )
        interpreter.setAccessibilityValue_(grant_target)
        inner.addArrangedSubview_(interpreter)
        fields["focus_fda_path_label"] = interpreter
        inner.addArrangedSubview_(
            native_ui.make_wrapping_label(
                grant_instructions,
                secondary=True,
                size=11.0,
                max_width=500.0,
            )
        )
        controls = native_ui.make_stack(
            orientation="horizontal",
            spacing=native_ui.SPACE_S,
        )
        open_fda = native_ui.make_button(
            "Open Privacy Settings…",
            target,
            "openFullDiskAccessSettings:",
        )
        native_ui.set_accessibility_metadata(
            open_fda,
            label="Open Full Disk Access settings",
            help_text="Open the macOS privacy pane used to allow named Focus rules.",
        )
        reveal_binary = native_ui.make_button(
            f"Reveal {PRODUCT_DISPLAY_NAME} in Finder"
            if running_inside_bundle()
            else "Reveal Program in Finder",
            target,
            "revealFocusBinaryInFinder:",
        )
        native_ui.set_accessibility_metadata(
            reveal_binary,
            label=f"Reveal {PRODUCT_DISPLAY_NAME} permission target in Finder",
            help_text="Show the exact app or program that needs Full Disk Access.",
        )
        controls.addArrangedSubview_(open_fda)
        controls.addArrangedSubview_(reveal_binary)
        controls.addArrangedSubview_(native_ui.make_hspacer())
        inner.addArrangedSubview_(controls)
        buttons["focus_fda_open"] = open_fda
        buttons["focus_fda_reveal"] = reveal_binary
        return

    for index, (identifier, name) in enumerate(focus_modes):
        dim = make_focus_dim_popup(target, identifier)
        profile = native_ui.make_popup_button(target, "setFocusProfileRule:")
        profile.setIdentifier_(identifier)
        current_profile = target.settings.focus_profile_rules.get(identifier)
        profile.addItemWithTitle_("No profile")
        profile.lastItem().setRepresentedObject_("")
        if current_profile is None:
            profile.selectItem_(profile.lastItem())
        for slot in CALIBRATION_PROFILE_SLOTS:
            profile.addItemWithTitle_(f"Apply {slot}")
            item = profile.lastItem()
            item.setRepresentedObject_(slot)
            if slot == current_profile:
                profile.selectItem_(item)

        signal = native_ui.make_popup_button(target, "setFocusSignalPolicy:")
        signal.setIdentifier_(identifier)
        current_signal = target.settings.focus_signal_policy.get(identifier, "all")
        for title, value in (
            ("All signals", "all"),
            ("Asks only", "asks_only"),
            ("Silent", "silent"),
        ):
            signal.addItemWithTitle_(title)
            item = signal.lastItem()
            item.setRepresentedObject_(value)
            if value == current_signal:
                signal.selectItem_(item)

        cluster = native_ui.make_stack(
            orientation="horizontal",
            spacing=native_ui.SPACE_S,
        )
        cluster.addArrangedSubview_(dim)
        cluster.addArrangedSubview_(profile)
        cluster.addArrangedSubview_(signal)
        inner.addArrangedSubview_(
            native_ui.make_row(
                name,
                cluster,
                help_text=(
                    "Optional named Focus rules for brightness, calibration profile, and signals."
                ),
            )
        )
        if index < len(focus_modes) - 1:
            native_ui.add_separator(inner)
        fields[f"focus_rule_popup:{identifier}"] = dim
        fields[f"focus_profile_popup:{identifier}"] = profile
        fields[f"focus_signal_popup:{identifier}"] = signal


def _make_per_focus_card(
    target,
    fields: dict[str, object],
    buttons: dict[str, object],
):
    outer, inner = native_ui.make_card("Per-Focus Rules")
    focus_modes = _configured_focus_modes()
    _populate_per_focus_card(target, inner, fields, buttons, focus_modes)
    return outer, inner, focus_modes


def _install_key_view_loop(
    fields: dict[str, object],
    buttons: dict[str, object],
) -> None:
    controls = [
        fields["dnd_status_label"],
        buttons["dnd_schedule_enabled"],
        fields["dnd_schedule_start_time"],
        fields["dnd_schedule_end_time"],
        fields["dnd_schedule_mode"],
        fields["dnd_dim_fraction"],
        buttons["focus_sync_enabled"],
        fields["dnd_focus_mode"],
        buttons["dnd_focus_authorization"],
        *(buttons[f"dnd_temporary_mode:{mode.value}"] for mode in DndMode),
        buttons["dnd_resume"],
        buttons["dnd_end_override"],
        buttons["night_warmth_enabled"],
        fields["night_dim_popup"],
    ]
    controls.extend(
        control
        for key, control in fields.items()
        if key.startswith(("timebox_on_field:", "timebox_off_field:"))
    )
    controls.extend(
        control
        for key in ("focus_fda_path_label",)
        if (control := fields.get(key)) is not None
    )
    controls.extend(
        button
        for key in ("focus_fda_open", "focus_fda_reveal")
        if (button := buttons.get(key)) is not None
    )
    controls.extend(
        control
        for key, control in fields.items()
        if key.startswith(
            (
                "focus_rule_popup:",
                "focus_profile_popup:",
                "focus_signal_popup:",
            )
        )
    )
    ordered_controls = tuple(controls)
    for current, following in zip(
        ordered_controls,
        (*ordered_controls[1:], ordered_controls[0]),
        strict=True,
    ):
        current.setNextKeyView_(following)
    fields["dnd_keyboard_order"] = ordered_controls


def build_dnd_settings_pane(target):
    """Build the existing Focus pane plus one explicit DND card."""
    stack = native_ui.make_fill_stack(spacing=native_ui.SPACE_L)
    fields: dict[str, object] = {}
    buttons: dict[str, object] = {}

    now_outer, now_inner = native_ui.make_card("Right Now")
    now_label = native_ui.make_label(
        target.active_focus_summary(),
        secondary=False,
        size=13.0,
    )
    native_ui.set_accessibility_metadata(
        now_label,
        label="Current Focus detail",
        help_text=(
            "Optional named Focus detail. Public Focus activity and DND state are shown below."
        ),
        role="AXStaticText",
    )
    now_label.setAccessibilityValue_(now_label.stringValue())
    now_inner.addArrangedSubview_(now_label)
    fields["focus_now_label"] = now_label
    stack.addArrangedSubview_(now_outer)

    stack.addArrangedSubview_(_make_dnd_card(target, fields, buttons))
    stack.addArrangedSubview_(_make_night_card(target, fields, buttons))
    stack.addArrangedSubview_(_make_timebox_card(target, fields))
    per_focus_outer, per_focus_container, focus_modes = _make_per_focus_card(
        target,
        fields,
        buttons,
    )
    stack.addArrangedSubview_(per_focus_outer)

    _install_key_view_loop(fields, buttons)
    target.dnd_settings_pane_controls = DndSettingsPaneControls(
        fields,
        buttons,
        per_focus_container,
        focus_modes,
    )
    _refresh_dnd_card(target, fields, buttons, now_epoch=time.time())
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def _select_profile_popup(popup, value: str | None) -> None:
    _select_represented_value(popup, value or "")


def refresh_dnd_settings_controls(target, *, now_epoch: float | None = None) -> None:
    retained = getattr(target, "dnd_settings_pane_controls", None)
    if type(retained) is DndSettingsPaneControls:
        fields = retained.fields
        buttons = retained.buttons
        focus_modes = _configured_focus_modes()
        if focus_modes != retained.focus_roster_fingerprint:
            _populate_per_focus_card(
                target,
                retained.per_focus_container,
                fields,
                buttons,
                focus_modes,
            )
            _install_key_view_loop(fields, buttons)
            target.dnd_settings_pane_controls = DndSettingsPaneControls(
                fields,
                buttons,
                retained.per_focus_container,
                focus_modes,
            )
    else:
        fields = getattr(target, "settings_fields", None) or {}
        buttons = getattr(target, "settings_buttons", None) or {}
    if not fields and not buttons:
        return

    now_label = fields.get("focus_now_label")
    if now_label is not None:
        value = target.active_focus_summary()
        now_label.setStringValue_(value)
        now_label.setAccessibilityValue_(value)

    _set_switch_state(
        buttons.get("night_warmth_enabled"),
        target.settings.night_warmth_enabled,
    )
    night_dim = fields.get("night_dim_popup")
    if night_dim is not None:
        _select_represented_value(
            night_dim,
            f"{target.settings.night_dim_fraction:g}",
        )

    for key, popup in fields.items():
        if type(key) is not str:
            continue
        if key.startswith("focus_rule_popup:"):
            identifier = key.split(":", 1)[1]
            select_focus_dim_choice(
                popup,
                target.settings.focus_dim_rules.get(identifier),
            )
        elif key.startswith("focus_profile_popup:"):
            identifier = key.split(":", 1)[1]
            _select_profile_popup(
                popup,
                target.settings.focus_profile_rules.get(identifier),
            )
        elif key.startswith("focus_signal_popup:"):
            identifier = key.split(":", 1)[1]
            _select_represented_value(
                popup,
                target.settings.focus_signal_policy.get(identifier, "all"),
            )

    effective_now = time.time() if now_epoch is None else float(now_epoch)
    _refresh_dnd_card(
        target,
        fields,
        buttons,
        now_epoch=effective_now,
    )


__all__ = [
    "DND_MODE_OPTIONS",
    "DND_SETTINGS_SELECTORS",
    "FOCUS_DIM_CHOICES",
    "DndSettingsPaneControls",
    "build_dnd_settings_pane",
    "dnd_status_text",
    "focus_authorization_text",
    "make_focus_dim_popup",
    "minutes_from_time_picker",
    "refresh_dnd_settings_controls",
    "select_focus_dim_choice",
    "set_time_picker_minutes",
]
