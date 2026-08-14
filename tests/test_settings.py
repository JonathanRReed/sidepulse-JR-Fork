from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from sidepulse.capacity_calibration import (
    CALIBRATION_SCHEMA_VERSION,
    ROBUST_METHOD_VERSION,
    ForecastClaimClass,
    ForecastIdentityClass,
    ForecastReleaseAuthority,
)
from sidepulse.capacity_types import ForecastReleaseState, QuotaHorizon
from sidepulse.settings import (
    LED_DISPLAY_AGENT,
    LED_DISPLAY_QUOTA_RUNWAY,
    AgentMonitorSettings,
    DeviceDisplaySetting,
    load_settings,
    save_settings,
)


def test_notification_banner_is_off_for_new_installs_and_preserves_legacy_choice_once(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-settings.json"
    legacy = tmp_path / "legacy-settings.json"
    current = tmp_path / "current-settings.json"
    legacy.write_text("{}", encoding="utf-8")
    current.write_text(
        json.dumps({"notification_policy_version": 1}),
        encoding="utf-8",
    )

    assert AgentMonitorSettings().completion_notification_enabled is False
    assert load_settings(missing).completion_notification_enabled is False
    assert load_settings(current).completion_notification_enabled is False

    migrated = load_settings(legacy)
    assert migrated.completion_notification_enabled is True
    save_settings(migrated, legacy)
    payload = json.loads(legacy.read_text(encoding="utf-8"))
    assert payload["notification_policy_version"] == 1
    assert payload["completion_notification_enabled"] is True


def test_capacity_history_is_opt_in_with_independent_local_activity_consent() -> None:
    """New and migrated installations must not silently begin either retention stream."""
    settings = AgentMonitorSettings()

    assert settings.capacity_history_enabled is False
    assert settings.capacity_history_retention_days == 7
    assert settings.local_activity_history_enabled is False


def test_capacity_history_settings_round_trip_only_supported_retention(tmp_path: Path) -> None:
    """The persisted retention policy may only select 7, 30, or 90 days."""
    target = tmp_path / "settings.json"
    settings = replace(
        AgentMonitorSettings(),
        capacity_history_enabled=True,
        capacity_history_retention_days=30,
        local_activity_history_enabled=True,
    )

    save_settings(settings, target)
    restored = load_settings(target)

    assert restored.capacity_history_enabled is True
    assert restored.capacity_history_retention_days == 30
    assert restored.local_activity_history_enabled is True


def test_legacy_settings_do_not_import_broad_usage_or_transcript_history(
    tmp_path: Path,
) -> None:
    """Legacy usage windows and transcript flags are not consent to new history."""
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps(
            {
                "usage_graph_days": 90,
                "usage_history_enabled": True,
                "transcript_monitoring": {"codex": True, "claude": True},
                "transcript_aggregates": [{"prompt": "PRIVATE SENTINEL"}],
            }
        )
    )

    restored = load_settings(target)

    assert restored.capacity_history_enabled is False
    assert restored.capacity_history_retention_days == 7
    assert restored.local_activity_history_enabled is False


def test_invalid_or_boolean_retention_fails_closed_to_seven_days(tmp_path: Path) -> None:
    """Malformed settings cannot expand the privacy window or enable history."""
    for value in (14, 365, True, "90"):
        target = tmp_path / f"settings-{value}.json"
        target.write_text(
            json.dumps(
                {
                    "capacity_history_enabled": True,
                    "capacity_history_retention_days": value,
                    "local_activity_history_enabled": True,
                }
            )
        )

        restored = load_settings(target)

        assert restored.capacity_history_enabled is True
        assert restored.capacity_history_retention_days == 7
        assert restored.local_activity_history_enabled is True


def test_invalid_programmatic_retention_is_not_serialized(tmp_path: Path) -> None:
    """A non-UI caller cannot persist a privacy window outside 7, 30, or 90 days."""
    target = tmp_path / "settings.json"

    save_settings(
        replace(AgentMonitorSettings(), capacity_history_retention_days=365),
        target,
    )

    assert json.loads(target.read_text())["capacity_history_retention_days"] == 7


def test_operator_history_retention_defaults_to_zero_and_off() -> None:
    """No persisted operator ledger exists until a nonzero choice is explicit."""
    settings = AgentMonitorSettings()

    assert settings.operator_history_retention_days == 0


def test_operator_history_retention_round_trips_supported_choices(tmp_path: Path) -> None:
    """Every supported nonzero choice survives settings persistence exactly."""
    for retention_days in (7, 30, 90):
        target = tmp_path / f"settings-{retention_days}.json"

        save_settings(
            replace(
                AgentMonitorSettings(),
                operator_history_retention_days=retention_days,
            ),
            target,
        )

        payload = json.loads(target.read_text())
        assert payload["operator_history_retention_days"] == retention_days
        assert load_settings(target).operator_history_retention_days == retention_days


def test_operator_history_migration_and_malformed_values_fail_closed(
    tmp_path: Path,
) -> None:
    """Broad legacy consent and malformed exact values must leave history disabled."""
    legacy_target = tmp_path / "settings-legacy.json"
    legacy_target.write_text(
        json.dumps(
            {
                "operator_history_enabled": True,
                "history_retention_days": 90,
                "usage_history_enabled": True,
                "local_activity_history_enabled": True,
            }
        )
    )

    assert load_settings(legacy_target).operator_history_retention_days == 0

    for index, value in enumerate((-1, 1, 14, 91, 7.0, 14.0, 30.0, 90.0, True, "90", None)):
        target = tmp_path / f"settings-invalid-{index}.json"
        target.write_text(json.dumps({"operator_history_retention_days": value}))

        assert load_settings(target).operator_history_retention_days == 0


def test_invalid_programmatic_operator_retention_is_serialized_off(
    tmp_path: Path,
) -> None:
    """Non-UI callers cannot persist an unsupported operator-history window."""
    target = tmp_path / "settings.json"

    save_settings(
        replace(AgentMonitorSettings(), operator_history_retention_days=365),
        target,
    )

    assert json.loads(target.read_text())["operator_history_retention_days"] == 0


def test_forecast_release_authority_defaults_to_withheld() -> None:
    """New installations must not gain forecast release authority implicitly."""
    authority = AgentMonitorSettings().forecast_release_authority

    assert authority.release_state is ForecastReleaseState.WITHHELD
    assert authority.permitted_claim_classes == ()


def test_legacy_forecast_flags_and_local_calibration_cannot_authorize(tmp_path: Path) -> None:
    """Migrating permissive legacy keys must still produce withheld authority."""
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps(
            {
                "forecast_enabled": True,
                "forecast_release_state": "authorized",
                "forecast_calibration": {"mean_absolute_error": 0.0},
            }
        )
    )

    authority = load_settings(target).forecast_release_authority

    assert authority.release_state is ForecastReleaseState.WITHHELD
    assert authority.permitted_claim_classes == ()


def test_explicit_release_authority_round_trips_as_one_versioned_record(
    tmp_path: Path,
) -> None:
    """Dropping any authority field during persistence must invalidate its exact scope."""
    target = tmp_path / "settings.json"
    authority = ForecastReleaseAuthority(
        method_version=ROBUST_METHOD_VERSION,
        schema_version=CALIBRATION_SCHEMA_VERSION,
        identity_class=ForecastIdentityClass.OPAQUE_ACCOUNT,
        horizon=QuotaHorizon.SHORT,
        permitted_claim_classes=(ForecastClaimClass.EXHAUSTION_ENVELOPE,),
        calibration_sample_min=50,
        calibration_sample_max=200,
        issued_at=1_000.0,
        expires_at=20_000.0,
        release_state=ForecastReleaseState.AUTHORIZED,
    )

    save_settings(
        replace(AgentMonitorSettings(), forecast_release_authority=authority),
        target,
    )
    restored = load_settings(target)

    assert restored.forecast_release_authority == authority
    assert set(json.loads(target.read_text())["forecast_release_authority"]) == {
        "calibration_sample_max",
        "calibration_sample_min",
        "expires_at",
        "horizon",
        "identity_class",
        "issued_at",
        "method_version",
        "permitted_claim_classes",
        "release_state",
        "schema_version",
    }


def test_malformed_or_partial_release_authority_fails_closed(tmp_path: Path) -> None:
    """A partial local authority record must not survive as authorized."""
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps(
            {
                "forecast_release_authority": {
                    "schema_version": CALIBRATION_SCHEMA_VERSION,
                    "release_state": "authorized",
                }
            }
        )
    )

    authority = load_settings(target).forecast_release_authority

    assert authority.release_state is ForecastReleaseState.WITHHELD
    assert authority.permitted_claim_classes == ()


def test_settings_migration_disables_legacy_quota_authority_and_runway(
    tmp_path: Path,
) -> None:
    """Old quota preferences load safely but cannot remain active authority."""
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps(
            {
                "claude_plan_limits_enabled": True,
                "quota_alerts_enabled": True,
                "quota_alert_thresholds": [10, 50, 90],
                "led_display": LED_DISPLAY_QUOTA_RUNWAY,
                "devices": [
                    {
                        "id": "SidePulsePro",
                        "name": "SidePulse Pro",
                        "path": "/private/tmp/SidePulsePro",
                        "led_display": LED_DISPLAY_QUOTA_RUNWAY,
                    }
                ],
                "webhook_events": [
                    "completion",
                    "quota_threshold",
                    "quota_sunrise",
                ],
            }
        )
    )

    restored = load_settings(target)

    assert restored.claude_plan_limits_enabled is False
    assert restored.quota_alerts_enabled is False
    assert restored.quota_alert_thresholds == (90.0, 95.0)
    assert restored.led_display == LED_DISPLAY_AGENT
    assert restored.devices[0].led_display == LED_DISPLAY_AGENT
    assert restored.webhook_events == ("completion",)

    save_settings(restored, target)
    migrated = json.loads(target.read_text())
    assert migrated["settings_schema_version"] == 1
    assert "claude_plan_limits_enabled" not in migrated
    assert "quota_alerts_enabled" not in migrated
    assert "quota_alert_thresholds" not in migrated
    assert migrated["led_display"] == LED_DISPLAY_AGENT
    assert migrated["devices"][0]["led_display"] == LED_DISPLAY_AGENT
    assert migrated["webhook_events"] == ["completion"]


def test_settings_migration_programmatic_legacy_quota_controls_fail_closed(
    tmp_path: Path,
) -> None:
    """No old mutator or direct legacy value can re-enable a capacity effect."""
    settings = (
        AgentMonitorSettings()
        .with_claude_plan_limits_enabled(True)
        .with_quota_alerts_enabled(True)
        .with_quota_alert_thresholds((25.0,))
        .with_led_display(LED_DISPLAY_QUOTA_RUNWAY)
        .with_device_display(
            "SidePulsePro",
            LED_DISPLAY_QUOTA_RUNWAY,
            path="/private/tmp/SidePulsePro",
        )
    )

    assert settings.claude_plan_limits_enabled is False
    assert settings.quota_alerts_enabled is False
    assert settings.quota_alert_thresholds == (90.0, 95.0)
    assert settings.led_display == LED_DISPLAY_AGENT
    assert settings.devices[0].led_display == LED_DISPLAY_AGENT
    with pytest.raises(ValueError):
        settings.with_webhook_event("quota_threshold", True)

    target = tmp_path / "settings.json"
    save_settings(
        replace(
            settings,
            claude_plan_limits_enabled=True,
            quota_alerts_enabled=True,
            quota_alert_thresholds=(1.0,),
            led_display=LED_DISPLAY_QUOTA_RUNWAY,
            devices=(
                DeviceDisplaySetting(
                    device_id="SidePulsePro",
                    name="SidePulse Pro",
                    path="/private/tmp/SidePulsePro",
                    led_display=LED_DISPLAY_QUOTA_RUNWAY,
                ),
            ),
            webhook_events=("completion", "quota_threshold", "quota_sunrise"),
        ),
        target,
    )

    payload = json.loads(target.read_text())
    assert "claude_plan_limits_enabled" not in payload
    assert "quota_alerts_enabled" not in payload
    assert "quota_alert_thresholds" not in payload
    assert payload["led_display"] == LED_DISPLAY_AGENT
    assert payload["devices"][0]["led_display"] == LED_DISPLAY_AGENT
    assert payload["webhook_events"] == ["completion"]
