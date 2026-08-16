from pathlib import Path

from sidepulse.settings import (
    DEVICE_SETTING_PERSISTED_FIELDS,
    AgentMonitorSettings,
    DeviceDisplaySetting,
    load_settings,
    save_settings,
)


def _configured_device() -> DeviceDisplaySetting:
    return DeviceDisplaySetting(
        device_id="SidePulsePro",
        name="SidePulse Pro",
        path="/Volumes/SidePulsePro",
        led_display="studio",
        brightness=177,
        auto_brightness_enabled=True,
        red_gain=0.81,
        green_gain=0.72,
        blue_gain=0.93,
        resting_glow=0.17,
        blend_mode="relay",
        provider_pin="codex",
        signal_policy="asks_only",
    )


def test_device_setting_encoder_covers_the_durable_schema() -> None:
    payload = _configured_device().to_dict()
    assert set(payload) == DEVICE_SETTING_PERSISTED_FIELDS


def test_every_device_setting_survives_an_unrelated_save(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    expected = _configured_device()
    save_settings(AgentMonitorSettings(devices=(expected,)), target)

    restored = load_settings(target).with_tips_enabled(False)
    save_settings(restored, target)
    reloaded = load_settings(target)

    assert reloaded.devices == (expected,)
