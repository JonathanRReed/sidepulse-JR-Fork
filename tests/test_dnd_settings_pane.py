from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import objc
from AppKit import (
    NSDatePicker,
    NSLayoutAttributeWidth,
    NSPopUpButton,
    NSSwitch,
)
from Foundation import NSObject

from sidepulse import dnd_settings_pane
from sidepulse.dnd_policy import (
    DndMode,
    DndOverride,
    DndSource,
    compose_dnd_contributions,
    contribution_for_mode,
)
from sidepulse.focus_status import (
    FocusActivity,
    FocusAuthorization,
    FocusStatusObservation,
)
from sidepulse.settings import AgentMonitorSettings


class _DndSettingsTarget(NSObject):
    def init(self):
        self = objc.super(_DndSettingsTarget, self).init()
        if self is None:
            return None
        self.settings = AgentMonitorSettings()
        self.dnd_controller = SimpleNamespace(
            projection=compose_dnd_contributions(()),
            focus_observation=FocusStatusObservation(
                FocusAuthorization.NOT_DETERMINED,
                FocusActivity.UNAVAILABLE,
            ),
        )
        self.actions: list[tuple[str, str]] = []
        return self

    def active_focus_summary(self) -> str:
        return "Focus: Off"

    @objc.python_method
    def _record(self, name: str, sender) -> None:
        identifier = str(getattr(sender, "identifier", lambda: "")() or "")
        self.actions.append((name, identifier))

    @objc.IBAction
    def toggleDndSchedule_(self, sender):
        self._record("toggle_schedule", sender)

    @objc.IBAction
    def setDndScheduleStartTime_(self, sender):
        self._record("schedule_start", sender)

    @objc.IBAction
    def setDndScheduleEndTime_(self, sender):
        self._record("schedule_end", sender)

    @objc.IBAction
    def setDndScheduleMode_(self, sender):
        self._record("schedule_mode", sender)

    @objc.IBAction
    def setDndDimFraction_(self, sender):
        self._record("dim_fraction", sender)

    @objc.IBAction
    def toggleFocusSync_(self, sender):
        self._record("follow_focus", sender)

    @objc.IBAction
    def setDndFocusMode_(self, sender):
        self._record("focus_mode", sender)

    @objc.IBAction
    def requestDndFocusAuthorization_(self, sender):
        self._record("focus_authorization", sender)

    @objc.IBAction
    def startDndOneHour_(self, sender):
        self._record("one_hour", sender)

    @objc.IBAction
    def resumeDndUntilNextChange_(self, sender):
        self._record("resume", sender)

    @objc.IBAction
    def endDndOverride_(self, sender):
        self._record("end_override", sender)

    @objc.IBAction
    def toggleNightWarmth_(self, sender):
        self._record("night_warmth", sender)

    @objc.IBAction
    def setNightDimFraction_(self, sender):
        self._record("night_dim", sender)

    @objc.IBAction
    def applyTimeboxShortcuts_(self, sender):
        self._record("timebox", sender)

    @objc.IBAction
    def setFocusDimRule_(self, sender):
        self._record("focus_dim", sender)

    @objc.IBAction
    def setFocusProfileRule_(self, sender):
        self._record("focus_profile", sender)

    @objc.IBAction
    def setFocusSignalPolicy_(self, sender):
        self._record("focus_signal", sender)

    @objc.IBAction
    def openFullDiskAccessSettings_(self, sender):
        self._record("open_fda", sender)

    @objc.IBAction
    def revealFocusBinaryInFinder_(self, sender):
        self._record("reveal_binary", sender)


def _view_text(view) -> tuple[str, ...]:
    values: list[str] = []
    value_getter = getattr(view, "stringValue", None)
    if callable(value_getter):
        value = value_getter()
        if isinstance(value, str) and value:
            values.append(value)
    title_getter = getattr(view, "title", None)
    if callable(title_getter):
        title = title_getter()
        if isinstance(title, str) and title:
            values.append(title)
    for child in view.subviews():
        values.extend(_view_text(child))
    return tuple(values)


def _selected_value(popup) -> str:
    return str(popup.selectedItem().representedObject() or "")


def _has_width_constraint(control, width: float) -> bool:
    return any(
        constraint.firstItem() is control
        and constraint.firstAttribute() == NSLayoutAttributeWidth
        and abs(float(constraint.constant()) - width) < 1e-9
        for constraint in control.constraints()
    )


def _assert_closed_key_view_loop(order: tuple[object, ...]) -> None:
    assert len({id(control) for control in order}) == len(order)
    visited = []
    current = order[0]
    for _ in order:
        visited.append(current)
        current = current.nextKeyView()
    assert tuple(visited) == order
    assert current is order[0]


def test_focus_pane_adds_one_dnd_card_and_preserves_existing_surfaces(
    monkeypatch,
) -> None:
    target = _DndSettingsTarget.alloc().init()
    monkeypatch.setattr(
        dnd_settings_pane.focus_sync,
        "configured_focus_modes",
        lambda: [],
    )

    pane, fields, buttons = dnd_settings_pane.build_dnd_settings_pane(target)

    text = _view_text(pane)
    visible_copy = "\n".join(text)
    assert text.count("Do Not Disturb") == 1
    assert {
        "Right Now",
        "Night Warmth",
        "Timebox Focus Handshake",
        "Per-Focus Rules",
    } <= set(text)
    assert "Focus Dimming" not in text
    for option in dnd_settings_pane.DND_MODE_OPTIONS:
        assert f"{option.label}: {option.description}" in visible_copy
    assert {
        "focus_now_label",
        "dnd_status_label",
        "dnd_schedule_start_time",
        "dnd_schedule_end_time",
        "dnd_schedule_mode",
        "dnd_dim_fraction",
        "dnd_focus_mode",
        "dnd_focus_authorization_status",
        "night_dim_popup",
        "timebox_on_field:15",
        "timebox_off_field:15",
    } <= set(fields)
    assert {
        "dnd_schedule_enabled",
        "focus_sync_enabled",
        "dnd_focus_authorization",
        "dnd_temporary_mode:mute",
        "dnd_temporary_mode:dim",
        "dnd_temporary_mode:pause",
        "dnd_temporary_mode:asks_only",
        "dnd_temporary_mode:dark",
        "dnd_resume",
        "dnd_end_override",
        "night_warmth_enabled",
    } <= set(buttons)
    assert _has_width_constraint(fields["timebox_on_field:15"], 150.0)
    assert _has_width_constraint(fields["timebox_off_field:15"], 150.0)


def test_dnd_card_uses_native_controls_plain_labels_and_exact_accessibility(
    monkeypatch,
) -> None:
    target = _DndSettingsTarget.alloc().init()
    target.settings = (
        target.settings.with_dnd_schedule(
            enabled=True,
            start_minutes=22 * 60,
            end_minutes=7 * 60,
            mode=DndMode.DIM,
        )
        .with_dnd_dim_fraction(0.25)
        .with_focus_sync_enabled(True)
        .with_dnd_focus_mode(DndMode.PAUSE)
    )
    target.dnd_controller = SimpleNamespace(
        projection=compose_dnd_contributions(
            (contribution_for_mode(DndSource.SCHEDULE, DndMode.DIM),),
            next_transition_epoch=1_800_000_000.0,
        ),
        focus_observation=FocusStatusObservation(
            FocusAuthorization.DENIED,
            FocusActivity.UNAVAILABLE,
        ),
    )
    monkeypatch.setattr(
        dnd_settings_pane.focus_sync,
        "configured_focus_modes",
        lambda: [],
    )

    _pane, fields, buttons = dnd_settings_pane.build_dnd_settings_pane(target)

    assert isinstance(fields["dnd_schedule_start_time"], NSDatePicker)
    assert isinstance(fields["dnd_schedule_end_time"], NSDatePicker)
    assert isinstance(fields["dnd_schedule_mode"], NSPopUpButton)
    assert isinstance(buttons["dnd_schedule_enabled"], NSSwitch)
    assert dnd_settings_pane.minutes_from_time_picker(
        fields["dnd_schedule_start_time"]
    ) == 22 * 60
    assert dnd_settings_pane.minutes_from_time_picker(
        fields["dnd_schedule_end_time"]
    ) == 7 * 60
    assert _selected_value(fields["dnd_schedule_mode"]) == "dim"
    assert _selected_value(fields["dnd_dim_fraction"]) == "0.25"
    assert _selected_value(fields["dnd_focus_mode"]) == "pause"
    assert all(
        str(fields["dnd_schedule_mode"].itemAtIndex_(index).toolTip() or "").strip()
        for index in range(fields["dnd_schedule_mode"].numberOfItems())
    )
    assert fields["dnd_status_label"].accessibilityLabel() == "Do Not Disturb status"
    assert fields["dnd_status_label"].accessibilityValue() == fields[
        "dnd_status_label"
    ].stringValue()
    assert "Next change:" in fields["dnd_status_label"].stringValue()
    assert str(fields["dnd_status_label"].accessibilityHelp() or "").strip()
    assert (
        fields["dnd_focus_authorization_status"].stringValue()
        == "Focus status access is denied. JR-Bar will not claim that Focus is active."
    )
    assert buttons["dnd_focus_authorization"].isEnabled() is False
    for mode in DndMode:
        button = buttons[f"dnd_temporary_mode:{mode.value}"]
        assert str(button.accessibilityLabel() or "").strip()
        assert str(button.accessibilityHelp() or "").strip()
        button.performClick_(None)
        assert target.actions[-1] == ("one_hour", mode.value)


def test_status_copy_includes_the_supplied_exact_return_time() -> None:
    projection = compose_dnd_contributions(
        (contribution_for_mode(DndSource.MANUAL, DndMode.MUTE),),
        next_transition_epoch=1_800_000_000.0,
    )

    assert dnd_settings_pane.dnd_status_text(
        projection,
        format_epoch=lambda _epoch: "Jan 15, 2027 at 2:00 AM",
    ) == "DND: Manual Mute. Next change: Jan 15, 2027 at 2:00 AM."


def test_key_view_loop_is_stable_and_disabled_state_is_explicit(monkeypatch) -> None:
    target = _DndSettingsTarget.alloc().init()
    monkeypatch.setattr(
        dnd_settings_pane.focus_sync,
        "configured_focus_modes",
        lambda: [],
    )

    _pane, fields, buttons = dnd_settings_pane.build_dnd_settings_pane(target)

    order = fields["dnd_keyboard_order"]
    assert order[0] is fields["dnd_status_label"]
    assert order[1] is buttons["dnd_schedule_enabled"]
    assert buttons["dnd_focus_authorization"] in order
    assert buttons["night_warmth_enabled"] in order
    assert fields["night_dim_popup"] in order
    assert fields["timebox_on_field:15"] in order
    assert fields["timebox_off_field:15"] in order
    assert fields["focus_fda_path_label"] in order
    assert buttons["focus_fda_open"] in order
    assert buttons["focus_fda_reveal"] in order
    assert order.index(buttons["night_warmth_enabled"]) > order.index(
        buttons["dnd_end_override"]
    )
    assert order.index(fields["timebox_on_field:15"]) > order.index(
        fields["night_dim_popup"]
    )
    assert order.index(buttons["focus_fda_open"]) > order.index(
        fields["timebox_off_field:60"]
    )
    assert tuple(
        buttons[f"dnd_temporary_mode:{mode.value}"] for mode in DndMode
    ) == tuple(item for item in order if item in {
        buttons[f"dnd_temporary_mode:{mode.value}"] for mode in DndMode
    })
    _assert_closed_key_view_loop(order)
    assert fields["dnd_schedule_start_time"].isEnabled() is False
    assert fields["dnd_schedule_end_time"].isEnabled() is False
    assert fields["dnd_schedule_mode"].isEnabled() is False
    assert fields["dnd_focus_mode"].isEnabled() is False
    assert buttons["dnd_focus_authorization"].isEnabled() is True
    assert buttons["dnd_resume"].isEnabled() is False
    assert buttons["dnd_end_override"].isEnabled() is False


def test_refresh_rebuilds_per_focus_content_when_the_live_roster_changes(
    monkeypatch,
) -> None:
    target = _DndSettingsTarget.alloc().init()
    roster: list[tuple[str, str]] = []
    monkeypatch.setattr(
        dnd_settings_pane.focus_sync,
        "configured_focus_modes",
        lambda: list(roster),
    )
    pane, fields, buttons = dnd_settings_pane.build_dnd_settings_pane(target)
    retained = target.dnd_settings_pane_controls
    container = retained.per_focus_container
    schedule_switch = buttons["dnd_schedule_enabled"]

    assert "focus_rule_popup:work" not in fields
    assert buttons["focus_fda_open"] in fields["dnd_keyboard_order"]
    assert "Named per-Focus rules need Full Disk Access" in "\n".join(
        _view_text(pane)
    )

    roster[:] = [("work", "Work")]
    dnd_settings_pane.refresh_dnd_settings_controls(target)

    assert target.dnd_settings_pane_controls.per_focus_container is container
    assert buttons["dnd_schedule_enabled"] is schedule_switch
    assert "focus_fda_open" not in buttons
    assert "focus_fda_reveal" not in buttons
    assert "focus_fda_path_label" not in fields
    assert "focus_rule_popup:work" in fields
    assert "focus_profile_popup:work" in fields
    assert "focus_signal_popup:work" in fields
    assert fields["focus_rule_popup:work"] in fields["dnd_keyboard_order"]
    assert "Work" in _view_text(pane)
    assert "Named per-Focus rules need Full Disk Access" not in "\n".join(
        _view_text(pane)
    )

    roster[:] = [("personal", "Personal"), ("sleep", "Sleep")]
    dnd_settings_pane.refresh_dnd_settings_controls(target)

    assert target.dnd_settings_pane_controls.per_focus_container is container
    assert "focus_rule_popup:work" not in fields
    assert "focus_profile_popup:work" not in fields
    assert "focus_signal_popup:work" not in fields
    for identifier in ("personal", "sleep"):
        assert fields[f"focus_rule_popup:{identifier}"] in fields[
            "dnd_keyboard_order"
        ]
        assert fields[f"focus_profile_popup:{identifier}"] in fields[
            "dnd_keyboard_order"
        ]
        assert fields[f"focus_signal_popup:{identifier}"] in fields[
            "dnd_keyboard_order"
        ]
    _assert_closed_key_view_loop(fields["dnd_keyboard_order"])
    assert {"Personal", "Sleep"} <= set(_view_text(pane))


def test_refresh_updates_retained_controls_from_current_controller_truth(
    monkeypatch,
) -> None:
    target = _DndSettingsTarget.alloc().init()
    monkeypatch.setattr(
        dnd_settings_pane.focus_sync,
        "configured_focus_modes",
        lambda: [],
    )
    _pane, fields, buttons = dnd_settings_pane.build_dnd_settings_pane(target)
    identities = {key: id(value) for key, value in fields.items()}
    identities.update({key: id(value) for key, value in buttons.items()})
    now = 1_800_000_000.0
    override = DndOverride.for_mode(
        DndMode.ASKS_ONLY,
        created_epoch=now - 60.0,
        until_epoch=now + 3_600.0,
    )
    target.settings = (
        target.settings.with_dnd_schedule(
            enabled=True,
            start_minutes=8 * 60 + 30,
            end_minutes=17 * 60 + 45,
            mode=DndMode.MUTE,
        )
        .with_dnd_dim_fraction(0.3)
        .with_focus_sync_enabled(True)
        .with_dnd_focus_mode(DndMode.DIM)
        .with_dnd_override(override)
    )
    target.dnd_controller = SimpleNamespace(
        projection=compose_dnd_contributions(
            (contribution_for_mode(DndSource.MANUAL, DndMode.ASKS_ONLY),),
            next_transition_epoch=override.until_epoch,
        ),
        focus_observation=FocusStatusObservation(
            FocusAuthorization.AUTHORIZED,
            FocusActivity.ACTIVE,
        ),
    )

    dnd_settings_pane.refresh_dnd_settings_controls(target, now_epoch=now)

    assert all(
        id(value) == identities[key]
        for key, value in fields.items()
        if key in identities
    )
    assert all(
        id(value) == identities[key]
        for key, value in buttons.items()
        if key in identities
    )
    assert buttons["dnd_schedule_enabled"].state() == 1
    assert dnd_settings_pane.minutes_from_time_picker(
        fields["dnd_schedule_start_time"]
    ) == 8 * 60 + 30
    assert dnd_settings_pane.minutes_from_time_picker(
        fields["dnd_schedule_end_time"]
    ) == 17 * 60 + 45
    assert _selected_value(fields["dnd_schedule_mode"]) == "mute"
    assert _selected_value(fields["dnd_dim_fraction"]) == "0.3"
    assert _selected_value(fields["dnd_focus_mode"]) == "dim"
    assert fields["dnd_focus_authorization_status"].stringValue() == (
        "Focus status is allowed and a Focus is active."
    )
    assert buttons["dnd_focus_authorization"].isHidden()
    assert buttons["dnd_end_override"].isEnabled()


def test_configured_focus_rules_keep_three_controls_and_refresh_every_axis(
    monkeypatch,
) -> None:
    target = _DndSettingsTarget.alloc().init()
    target.settings = replace(
        target.settings,
        focus_dim_rules={"work": 0.5},
        focus_profile_rules={"work": "Day"},
        focus_signal_policy={"work": "asks_only"},
    )
    monkeypatch.setattr(
        dnd_settings_pane.focus_sync,
        "configured_focus_modes",
        lambda: [("work", "Work")],
    )
    _pane, fields, _buttons = dnd_settings_pane.build_dnd_settings_pane(target)

    assert _selected_value(fields["focus_rule_popup:work"]) == "0.5"
    assert _selected_value(fields["focus_profile_popup:work"]) == "Day"
    assert _selected_value(fields["focus_signal_popup:work"]) == "asks_only"

    target.settings = replace(
        target.settings,
        focus_dim_rules={"work": 0.25},
        focus_profile_rules={"work": "Night"},
        focus_signal_policy={"work": "silent"},
    )
    dnd_settings_pane.refresh_dnd_settings_controls(target)

    assert _selected_value(fields["focus_rule_popup:work"]) == "0.25"
    assert _selected_value(fields["focus_profile_popup:work"]) == "Night"
    assert _selected_value(fields["focus_signal_popup:work"]) == "silent"


def test_settings_window_delegates_focus_without_adding_navigation(monkeypatch) -> None:
    from sidepulse import settings_navigation, settings_window

    sentinel = (object(), {"field": object()}, {"button": object()})
    monkeypatch.setattr(
        settings_window,
        "build_dnd_settings_pane",
        lambda _target: sentinel,
    )

    assert settings_window._build_settings_pane(object(), "focus") is sentinel
    assert "focus" in settings_navigation.legacy_page_keys()
    assert all(category.key != "dnd" for category in settings_navigation.SETTINGS_CATEGORIES)
    assert all(
        page.key != "dnd"
        for category in settings_navigation.SETTINGS_CATEGORIES
        for page in category.pages
    )
