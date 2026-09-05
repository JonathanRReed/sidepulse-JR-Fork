from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from sidepulse.lighting_settings_pane import build_effects_page
from sidepulse.onboarding_runtime import (
    set_idle_auto_off_timeout,
    set_sleep_dim_percentage,
)
from sidepulse.settings import AgentMonitorSettings


class _Field:
    def __init__(self, value: str) -> None:
        self.value = value

    def stringValue(self) -> str:
        return self.value

    def setStringValue_(self, value: str) -> None:
        self.value = value


def _legacy():
    return SimpleNamespace(save_settings=Mock(), set_field_value=lambda field, value: field.setStringValue_(value))


def test_effects_page_exposes_persisted_sleep_level_and_idle_timeout_fields() -> None:
    target = SimpleNamespace(settings=AgentMonitorSettings())

    _pane, fields, _buttons = build_effects_page(target)

    assert fields["sleep_dim_percentage"].stringValue() == "20"
    assert fields["sleep_dim_percentage"].action() == "applySleepDimPercentage:"
    assert fields["idle_auto_off_timeout"].stringValue() == "60"
    assert fields["idle_auto_off_timeout"].action() == "applyIdleAutoOffTimeout:"


def test_sleep_dim_percentage_persists_normalized_value_and_refreshes_light() -> None:
    legacy = _legacy()
    controller = SimpleNamespace(
        settings=AgentMonitorSettings(),
        refresh_=Mock(),
        set_settings_message=Mock(),
    )
    field = _Field("35")

    assert set_sleep_dim_percentage(controller, field, legacy) is True

    assert controller.settings.sleep_dim_fraction == 0.35
    assert field.value == "35"
    legacy.save_settings.assert_called_once_with(controller.settings)
    controller.refresh_.assert_called_once_with(None)


def test_idle_auto_off_timeout_clamps_and_persists_minutes() -> None:
    legacy = _legacy()
    controller = SimpleNamespace(
        settings=AgentMonitorSettings(),
        refresh_=Mock(),
        set_settings_message=Mock(),
    )
    field = _Field("2")

    assert set_idle_auto_off_timeout(controller, field, legacy) is True

    assert controller.settings.idle_auto_off_after_minutes == 5.0
    assert field.value == "5"
    legacy.save_settings.assert_called_once_with(controller.settings)
    controller.refresh_.assert_called_once_with(None)


def test_invalid_lighting_number_restores_the_persisted_value() -> None:
    legacy = _legacy()
    controller = SimpleNamespace(
        settings=AgentMonitorSettings(),
        refresh_=Mock(),
        set_settings_message=Mock(),
    )
    field = _Field("not a number")

    assert set_sleep_dim_percentage(controller, field, legacy) is False

    assert field.value == "20"
    legacy.save_settings.assert_not_called()
    controller.refresh_.assert_not_called()

