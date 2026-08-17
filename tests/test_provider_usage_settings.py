from __future__ import annotations

import json

from sidepulse.provider_usage_settings import (
    PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION,
    ProviderUsageSettingsWriteRefusedError,
    default_provider_usage_settings,
    load_provider_usage_settings,
    save_provider_usage_settings,
)


def test_defaults_enable_consumer_providers_but_not_openai_admin() -> None:
    settings = default_provider_usage_settings()
    assert settings.preference("codex").enabled is True
    assert settings.preference("claude").enabled is True
    assert settings.preference("grok").enabled is True
    assert settings.preference("openai-api").enabled is False
    assert all(not item.browser_sources for item in settings.providers)


def test_configuration_updates_are_immutable_and_provider_scoped() -> None:
    original = default_provider_usage_settings()
    updated = (
        original.with_enabled("cursor", False)
        .with_browser_sources("devin", True)
        .with_option("devin", "organization", "org_example")
    )
    assert original.preference("cursor").enabled is True
    assert updated.preference("cursor").enabled is False
    assert updated.preference("devin").browser_sources is True
    assert updated.preference("devin").option("organization") == "org_example"


def test_round_trip_preserves_unknown_fields() -> None:
    original = {
        "settings_schema_version": PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION,
        "future_extension": {"keep": True},
        "providers": [
            {"provider_id": "codex", "enabled": False, "browser_sources": False}
        ],
    }
    loaded = load_provider_usage_settings(reader=lambda _path: json.dumps(original))
    writes = []
    save_provider_usage_settings(
        loaded.settings.with_enabled("claude", False),
        loaded=loaded,
        writer=lambda _path, text: writes.append(json.loads(text)),
    )
    document = writes[0]
    assert document["future_extension"] == {"keep": True}
    assert next(
        row for row in document["providers"] if row["provider_id"] == "codex"
    )["enabled"] is False
    assert next(
        row for row in document["providers"] if row["provider_id"] == "claude"
    )["enabled"] is False


def test_future_schema_is_read_only() -> None:
    loaded = load_provider_usage_settings(
        reader=lambda _path: json.dumps(
            {
                "settings_schema_version": PROVIDER_USAGE_SETTINGS_SCHEMA_VERSION + 1,
                "future": True,
            }
        )
    )
    assert loaded.read_only is True
    try:
        save_provider_usage_settings(
            loaded.settings,
            loaded=loaded,
            writer=lambda _path, _text: None,
        )
    except ProviderUsageSettingsWriteRefusedError:
        pass
    else:
        raise AssertionError("future settings were overwritten")


def test_browser_sources_are_rejected_for_providers_without_browser_support() -> None:
    settings = default_provider_usage_settings()
    try:
        settings.with_browser_sources("antigravity", True)
    except ValueError as exc:
        assert "browser" in str(exc)
    else:
        raise AssertionError("unsupported browser source was enabled")
