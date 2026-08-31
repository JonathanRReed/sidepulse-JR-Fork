from __future__ import annotations

import json
from pathlib import Path

from sidepulse.settings import AgentMonitorSettings, load_settings, save_settings


def test_power_hold_defaults_keep_system_working_but_allow_display_sleep() -> None:
    settings = AgentMonitorSettings()

    assert settings.agent_keep_awake_enabled is True
    assert settings.keep_display_awake is False
    assert settings.keep_awake_on_battery is True
    assert settings.closed_lid_awake_policy == "never"


def test_agent_and_display_choices_are_immutable_and_independent() -> None:
    defaults = AgentMonitorSettings()
    no_agent_hold = defaults.with_agent_keep_awake_enabled(False)
    display_hold = no_agent_hold.with_keep_display_awake(True)

    assert defaults.agent_keep_awake_enabled is True
    assert defaults.keep_display_awake is False
    assert no_agent_hold.agent_keep_awake_enabled is False
    assert no_agent_hold.keep_display_awake is False
    assert display_hold.agent_keep_awake_enabled is False
    assert display_hold.keep_display_awake is True
    assert display_hold.keep_awake_on_battery is True
    assert display_hold.closed_lid_awake_policy == "never"


def test_agent_and_display_choices_round_trip_independently(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    expected = (
        AgentMonitorSettings()
        .with_agent_keep_awake_enabled(False)
        .with_keep_display_awake(True)
    )

    save_settings(expected, target)
    reloaded = load_settings(target)

    assert reloaded.agent_keep_awake_enabled is False
    assert reloaded.keep_display_awake is True
    document = json.loads(target.read_text())
    assert document["agent_keep_awake_enabled"] is False
    assert document["keep_display_awake"] is True


def test_absent_power_choice_keys_use_safe_defaults(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    save_settings(
        AgentMonitorSettings()
        .with_agent_keep_awake_enabled(False)
        .with_keep_display_awake(True),
        target,
    )
    document = json.loads(target.read_text())
    document.pop("agent_keep_awake_enabled")
    document.pop("keep_display_awake")
    target.write_text(json.dumps(document))

    reloaded = load_settings(target)

    assert reloaded.agent_keep_awake_enabled is True
    assert reloaded.keep_display_awake is False


def test_ambiguous_power_choice_values_use_safe_defaults(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    save_settings(AgentMonitorSettings(), target)
    document = json.loads(target.read_text())
    document["agent_keep_awake_enabled"] = 1
    document["keep_display_awake"] = "true"
    target.write_text(json.dumps(document))

    reloaded = load_settings(target)

    assert reloaded.agent_keep_awake_enabled is True
    assert reloaded.keep_display_awake is False


def test_unrelated_save_retains_power_choices_and_readable_extension(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    save_settings(
        AgentMonitorSettings()
        .with_agent_keep_awake_enabled(False)
        .with_keep_display_awake(True),
        target,
    )
    document = json.loads(target.read_text())
    document["future_power_extension"] = {"preserve": True}
    target.write_text(json.dumps(document))

    reloaded = load_settings(target).with_tips_enabled(False)
    save_settings(reloaded, target)
    final = json.loads(target.read_text())

    assert final["agent_keep_awake_enabled"] is False
    assert final["keep_display_awake"] is True
    assert final["future_power_extension"] == {"preserve": True}
