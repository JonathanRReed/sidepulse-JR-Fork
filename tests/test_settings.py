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
    CLAUDE_PLAN_LIMITS_CONSENT_VERSION,
    CURRENT_SETTINGS_SCHEMA_VERSION,
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
    """Old quota preferences load safely but cannot remain active authority.

    `claude_plan_limits_enabled` is back in this set, and for a sharper
    reason than the others. 0.2.1 shipped a build that PERSISTED this key
    while its own code documented the flag as inert, so a stored `true` may
    never have been a decision about anything. Honouring it on upgrade starts
    reading the user's Keychain and presenting an OAuth token to
    api.anthropic.com on the 5-minute worker, from a value they were never
    asked for. It needs fresh consent, stamped by this build.

    quota_alert_thresholds and the quota webhooks still cannot load --
    nothing consumes an authorised threshold crossing. The runway LED
    left this set on 2026-08-26: the JR usage plane's gated lanes feed
    quota_runway_state now, so the choice persists and round-trips
    (rendering still fails closed to Agent when no lane has a percent).
    """
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
    assert restored.claude_plan_limits_consent_version == 0
    # quota_alerts_enabled is a REAL, consumed, UI-toggleable flag as of
    # 2026-08-26 (the quota blink claim, pace notifications). A stored
    # true now loads as true -- the old strip-on-load guarded a promise
    # the app could not keep, and it can keep it now.
    assert restored.quota_alerts_enabled is True
    assert restored.quota_alert_thresholds == (90.0, 95.0)
    # 2026-08-26: the runway choice survives the load -- the JR usage
    # plane now feeds quota_runway_state, so the downgrade is retired.
    assert restored.led_display == LED_DISPLAY_QUOTA_RUNWAY
    assert restored.devices[0].led_display == LED_DISPLAY_QUOTA_RUNWAY
    assert restored.webhook_events == ("completion",)

    save_settings(restored, target)
    migrated = json.loads(target.read_text())
    assert migrated["settings_schema_version"] == CURRENT_SETTINGS_SCHEMA_VERSION
    assert migrated["claude_plan_limits_enabled"] is False
    assert migrated["claude_plan_limits_consent_version"] == 0
    # Lossless persistence keeps unconsumed legacy keys on disk so a
    # downgrade loses nothing; what matters is that a reload can never
    # turn them back into active authority.
    reloaded = load_settings(target)
    assert reloaded.quota_alerts_enabled is True
    assert reloaded.quota_alert_thresholds == (90.0, 95.0)
    assert reloaded.led_display == LED_DISPLAY_QUOTA_RUNWAY
    assert migrated["led_display"] == LED_DISPLAY_QUOTA_RUNWAY
    assert migrated["devices"][0]["led_display"] == LED_DISPLAY_QUOTA_RUNWAY
    assert migrated["webhook_events"] == ["completion"]


def test_settings_migration_programmatic_legacy_quota_controls_fail_closed(
    tmp_path: Path,
) -> None:
    """No old mutator or direct legacy value can re-enable a capacity effect.

    The Claude opt-in is the one mutator that honours its argument now: it
    grants a READ, not an effect. It is still not reachable from a forged
    dataclass, because the value alone is not consent -- only a stamp this
    build wrote is, and `replace()` cannot forge one it does not set.
    Thresholds and quota webhooks still grant an EFFECT with no
    authority-fed producer, so they stay unreachable from both the
    mutators and a hand-forged dataclass. The runway display gained its
    producer on 2026-08-26 (the JR usage plane's gated lanes), so its
    mutators and persistence are honest preferences now.
    """
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

    assert settings.claude_plan_limits_enabled is True
    # The alerts mutator honours its argument now (consumers exist);
    # thresholds and quota webhooks below still fail closed -- nothing
    # feeds them authority yet. The runway mutators honour their
    # argument since 2026-08-26: the JR usage plane's gated lanes are
    # the runway LED's producer.
    assert settings.quota_alerts_enabled is True
    assert settings.quota_alert_thresholds == (90.0, 95.0)
    assert settings.led_display == LED_DISPLAY_QUOTA_RUNWAY
    assert settings.devices[0].led_display == LED_DISPLAY_QUOTA_RUNWAY
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
    assert payload["claude_plan_limits_enabled"] is True
    # The alerts flag is persisted now (real feature); thresholds stay
    # stripped -- still no authority-fed producer for custom values.
    assert payload["quota_alerts_enabled"] is True
    assert "quota_alert_thresholds" not in payload
    assert payload["led_display"] == LED_DISPLAY_QUOTA_RUNWAY
    assert payload["devices"][0]["led_display"] == LED_DISPLAY_QUOTA_RUNWAY
    assert payload["webhook_events"] == ["completion"]


def test_quota_runway_display_choice_round_trips(tmp_path: Path) -> None:
    """The runway choice persists and loads (producer landed 2026-08-26)."""
    target = tmp_path / "settings.json"
    settings = (
        AgentMonitorSettings()
        .with_led_display(LED_DISPLAY_QUOTA_RUNWAY)
        .with_device_display(
            "SidePulsePro",
            LED_DISPLAY_QUOTA_RUNWAY,
            path="/private/tmp/SidePulsePro",
        )
    )
    save_settings(settings, target)
    loaded = load_settings(target)
    assert loaded.led_display == LED_DISPLAY_QUOTA_RUNWAY
    assert loaded.devices[0].led_display == LED_DISPLAY_QUOTA_RUNWAY
    assert loaded.display_for_device("SidePulsePro") == LED_DISPLAY_QUOTA_RUNWAY


def test_a_forged_claude_opt_in_carries_no_consent_across_a_reload(
    tmp_path: Path,
) -> None:
    """The value is not the consent; the stamp is, and `replace` has none.

    This is the shape a settings file written by an older build is in: the
    flag says true and nothing says the user was ever asked. It writes itself
    out honestly and fails closed on the way back in, so no relaunch turns it
    into a Keychain read and a call to api.anthropic.com.
    """
    target = tmp_path / "settings.json"
    forged = replace(AgentMonitorSettings(), claude_plan_limits_enabled=True)
    save_settings(forged, target)

    payload = json.loads(target.read_text())
    assert payload["claude_plan_limits_enabled"] is True
    assert payload["claude_plan_limits_consent_version"] == 0
    assert load_settings(target).claude_plan_limits_enabled is False


def test_a_real_claude_opt_in_survives_a_save_and_reload(tmp_path: Path) -> None:
    """Requiring a stamp must not also break the opt-in the user did give."""
    target = tmp_path / "settings.json"
    save_settings(AgentMonitorSettings().with_claude_plan_limits_enabled(True), target)

    payload = json.loads(target.read_text())
    assert payload["claude_plan_limits_enabled"] is True
    assert payload["claude_plan_limits_consent_version"] == (
        CLAUDE_PLAN_LIMITS_CONSENT_VERSION
    )
    assert load_settings(target).claude_plan_limits_enabled is True

    # And turning it back off clears the stamp, so re-enabling it later is a
    # fresh decision rather than a leftover one.
    save_settings(load_settings(target).with_claude_plan_limits_enabled(False), target)
    cleared = json.loads(target.read_text())
    assert cleared["claude_plan_limits_enabled"] is False
    assert cleared["claude_plan_limits_consent_version"] == 0


def test_a_stamp_from_another_consent_generation_is_not_consent(
    tmp_path: Path,
) -> None:
    """The stamp is a generation, not a checkbox: only this build's counts."""
    target = tmp_path / "settings.json"
    for stamp in (
        CLAUDE_PLAN_LIMITS_CONSENT_VERSION - 1,
        CLAUDE_PLAN_LIMITS_CONSENT_VERSION + 1,
        # JSON `true` equals 1 numerically, which would pass a bare `==`.
        True,
        "1",
        None,
    ):
        target.write_text(
            json.dumps(
                {
                    "claude_plan_limits_enabled": True,
                    "claude_plan_limits_consent_version": stamp,
                }
            )
        )
        assert load_settings(target).claude_plan_limits_enabled is False, stamp


def test_removing_a_provider_animation_actually_persists(tmp_path: Path) -> None:
    """Runtime-owned collections must honour deletions: the lossless merge
    used to resurrect removed entries from the remembered source document,
    so switching a provider's animation back to Automatic never stuck."""
    from sidepulse.colors import PROVIDER_ANIMATION_AUTO

    path = tmp_path / "settings.json"
    first = AgentMonitorSettings()
    first = first.with_colors(first.colors.with_agent_animation("claude", "blink"))
    save_settings(first, path)
    assert load_settings(path).colors.provider_animation == {"claude": "blink"}

    loaded = load_settings(path)
    cleared = loaded.with_colors(
        loaded.colors.with_agent_animation("claude", PROVIDER_ANIMATION_AUTO)
    )
    save_settings(cleared, path)

    assert load_settings(path).colors.provider_animation == {}
    assert json.loads(path.read_text())["colors"]["provider_animation"] == {}
