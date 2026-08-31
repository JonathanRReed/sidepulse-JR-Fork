from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import objc
from AppKit import NSPopUpButton, NSSwitch
from Foundation import NSObject

from sidepulse import power_settings_pane
from sidepulse.models import AgentMode
from sidepulse.settings import AgentMonitorSettings


class _Target(NSObject):
    def init(self):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self.settings = AgentMonitorSettings()
        self.keep_awake = SimpleNamespace(last_mode=AgentMode.WORKING)
        self.sync_modes: list[AgentMode] = []
        self.messages: list[str] = []
        return self

    def sync_keep_awake(self, mode: AgentMode) -> None:
        self.sync_modes.append(mode)

    def set_settings_message(self, message: str) -> None:
        self.messages.append(message)

    @objc.IBAction
    def applyClosedLidGraceMinutes_(self, _sender):
        return None

    @objc.IBAction
    def toggleKeepAwakeOnBattery_(self, _sender):
        return None

    @objc.IBAction
    def setBatteryLedDisplayFromCheckbox_(self, _sender):
        return None

    @objc.IBAction
    def setBatteryPowerPreviewFromCheckbox_(self, _sender):
        return None

    @objc.IBAction
    def setBatteryChargingIdleFromCheckbox_(self, _sender):
        return None

    @objc.IBAction
    def toggleLowBatteryAlert_(self, _sender):
        return None

    @objc.IBAction
    def applyLowBatteryThreshold_(self, _sender):
        return None


def _switch(state: bool) -> NSSwitch:
    control = NSSwitch.alloc().init()
    control.setState_(1 if state else 0)
    return control


def test_power_choice_copy_names_four_distinct_decisions() -> None:
    assert power_settings_pane.POWER_CHOICE_LABELS == {
        "agent": "Keep Mac awake while agents work",
        "display": "Keep displays awake during holds",
        "battery": "Continue agent holds on battery",
        "closed_lid": "Closed-lid policy",
    }


def test_power_actions_save_exact_setting_and_sync_current_mode(monkeypatch) -> None:
    target = _Target.alloc().init()
    saved: list[AgentMonitorSettings] = []
    monkeypatch.setattr(power_settings_pane, "save_settings", saved.append)
    actions = power_settings_pane.PowerSettingsActions.alloc().initWithController_(
        target
    )

    actions.toggleAgentKeepAwake_(_switch(False))
    assert target.settings.agent_keep_awake_enabled is False
    assert target.settings.keep_display_awake is False
    assert saved[-1] == target.settings
    assert target.sync_modes[-1] is AgentMode.WORKING

    actions.toggleKeepDisplayAwake_(_switch(True))
    assert target.settings.agent_keep_awake_enabled is False
    assert target.settings.keep_display_awake is True
    assert saved[-1] == target.settings
    assert target.sync_modes[-1] is AgentMode.WORKING
    assert target.messages[-1] == "Displays will stay awake during active holds."


def test_power_action_restores_previous_settings_when_save_fails(monkeypatch) -> None:
    target = _Target.alloc().init()
    previous = target.settings
    failed_switch = _switch(True)
    target.settings_buttons = {"keep_display_awake": failed_switch}

    def fail_save(_settings) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(power_settings_pane, "save_settings", fail_save)
    actions = power_settings_pane.PowerSettingsActions.alloc().initWithController_(
        target
    )

    actions.toggleKeepDisplayAwake_(_switch(True))

    assert target.settings is previous
    assert target.sync_modes == []
    assert failed_switch.state() == 0
    assert target.messages[-1].startswith("Could not save display power setting:")


def test_power_pane_exposes_accessible_controls_and_retains_action_target() -> None:
    target = _Target.alloc().init()
    pane, fields, buttons = power_settings_pane.build_power_settings_pane(
        target,
        make_closed_lid_policy_popup=lambda _target: NSPopUpButton.alloc().init(),
    )

    assert pane is not None
    assert set(fields) == {
        "closed_lid_awake_policy_popup",
        "closed_lid_grace_field",
        "low_battery_threshold_field",
    }
    assert {
        "agent_keep_awake_enabled",
        "keep_display_awake",
        "keep_awake_on_battery",
        "battery_leds",
        "battery_power_preview",
        "battery_charging_idle",
        "low_battery_alert",
    } <= set(buttons)
    assert target.power_settings_actions is not None
    assert (
        buttons["agent_keep_awake_enabled"].accessibilityLabel()
        == power_settings_pane.POWER_CHOICE_LABELS["agent"]
    )
    assert (
        buttons["keep_display_awake"].accessibilityLabel()
        == power_settings_pane.POWER_CHOICE_LABELS["display"]
    )
    assert buttons["agent_keep_awake_enabled"].state() == 1
    assert buttons["keep_display_awake"].state() == 0


def test_settings_window_delegates_power_pane_and_shrinks(monkeypatch) -> None:
    from sidepulse import settings_window

    root = Path(__file__).resolve().parents[1]
    source = (root / "src/sidepulse/settings_window.py").read_text()
    refreshed: list[str] = []
    monkeypatch.setattr(
        settings_window,
        "_refresh_power_settings_controls",
        lambda _target: refreshed.append("power"),
    )
    monkeypatch.setattr(
        settings_window,
        "refresh_global_action_settings_controls",
        lambda _target: refreshed.append("global_actions"),
    )

    assert "return build_power_settings_pane(" in source
    settings_window.refresh_power_settings_controls(object())
    assert refreshed == ["power", "global_actions"]
    assert (root / "src/sidepulse/settings_window.py").stat().st_size < 218_442
