from __future__ import annotations

import json
from pathlib import Path

from sidepulse.provider_usage_settings import (
    BrowserConsent,
    ProviderUsageSettings,
    load_provider_usage_settings,
    save_provider_usage_settings,
)


def test_defaults_enable_consumer_providers_but_not_admin_api() -> None:
    settings = ProviderUsageSettings.defaults()
    assert settings.is_enabled("codex")
    assert settings.is_enabled("claude")
    assert settings.is_enabled("cursor")
    assert settings.is_enabled("devin")
    assert settings.is_enabled("grok")
    assert settings.is_enabled("antigravity")
    assert not settings.is_enabled("openai-api")


def test_browser_consent_is_provider_profile_and_field_scoped() -> None:
    consent = BrowserConsent(
        provider_id="devin",
        browser="chrome",
        profile="Default",
        domains=("app.devin.ai",),
        fields=("auth1_session", "last-internal-org-for-external-org-v1-*"),
        background_repair=True,
        granted_at=100.0,
    )
    settings = ProviderUsageSettings.defaults().with_browser_consent(consent)

    assert settings.browser_consent("devin", "chrome", "Default") == consent
    assert settings.browser_consent("cursor", "chrome", "Default") is None
    assert settings.browser_consent("devin", "chrome", "Profile 1") is None


def test_settings_round_trip_preserves_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "providers.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "enabled": ["codex", "claude"],
                "future": {"keep": True},
            }
        ),
        encoding="utf-8",
    )
    loaded = load_provider_usage_settings(path)
    settings = loaded.settings.with_enabled("grok", True).with_option(
        "devin", "organization", "org/example"
    )
    save_provider_usage_settings(settings, path, loaded=loaded)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["future"] == {"keep": True}
    assert set(document["enabled"]) == {"codex", "claude", "grok"}
    assert document["options"]["devin"]["organization"] == "org/example"


def test_future_schema_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "providers.json"
    original = {"schema_version": 99, "enabled": ["codex"], "future": True}
    path.write_text(json.dumps(original), encoding="utf-8")
    loaded = load_provider_usage_settings(path)

    assert loaded.read_only is True
    try:
        save_provider_usage_settings(loaded.settings, path, loaded=loaded)
    except ValueError as exc:
        assert "newer" in str(exc)
    else:
        raise AssertionError("future provider settings were overwritten")
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_external_edit_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "providers.json"
    loaded = load_provider_usage_settings(path)
    save_provider_usage_settings(loaded.settings, path, loaded=loaded)
    loaded = load_provider_usage_settings(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["external"] = True
    path.write_text(json.dumps(document), encoding="utf-8")

    try:
        save_provider_usage_settings(
            loaded.settings.with_enabled("cursor", False), path, loaded=loaded
        )
    except ValueError as exc:
        assert "changed" in str(exc)
    else:
        raise AssertionError("external provider settings edit was overwritten")
