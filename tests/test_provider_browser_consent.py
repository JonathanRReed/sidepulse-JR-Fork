from __future__ import annotations

import json

from sidepulse.provider_browser_consent import (
    BROWSER_CONSENT_SCHEMA_VERSION,
    BrowserConsentStore,
    ChromiumRecord,
    decode_chromium_local_storage,
    extract_devin_session,
    load_browser_consents,
    save_browser_consents,
)


def test_browser_access_is_denied_until_exact_scope_is_granted():
    store = BrowserConsentStore.empty()
    assert not store.allows(
        provider_id="devin",
        browser="chrome",
        profile="Default",
        domain="app.devin.ai",
        field="auth1_session",
    )

    granted = store.grant(
        provider_id="devin",
        browser="chrome",
        profile="Default",
        domains=("app.devin.ai",),
        fields=("auth1_session", "organization"),
        background_repair=False,
        granted_at=1000,
    )

    assert granted.allows(
        provider_id="devin",
        browser="chrome",
        profile="Default",
        domain="app.devin.ai",
        field="auth1_session",
    )
    assert not granted.allows(
        provider_id="devin",
        browser="chrome",
        profile="Profile 2",
        domain="app.devin.ai",
        field="auth1_session",
    )
    assert not granted.allows(
        provider_id="devin",
        browser="chrome",
        profile="Default",
        domain="example.com",
        field="auth1_session",
    )


def test_revoke_removes_only_one_provider_profile_scope():
    store = BrowserConsentStore.empty().grant(
        provider_id="devin",
        browser="chrome",
        profile="Default",
        domains=("app.devin.ai",),
        fields=("auth1_session",),
        background_repair=False,
        granted_at=1000,
    ).grant(
        provider_id="cursor",
        browser="chrome",
        profile="Default",
        domains=("cursor.com",),
        fields=("session",),
        background_repair=False,
        granted_at=1000,
    )
    updated = store.revoke("devin", "chrome", "Default")
    assert len(updated.consents) == 1
    assert updated.consents[0].provider_id == "cursor"


def test_same_provider_consents_are_scoped_to_the_exact_source_instance():
    store = BrowserConsentStore.empty().grant(
        provider_id="devin",
        source_instance_id="work",
        browser="chrome",
        profile="Default",
        domains=("app.devin.ai",),
        fields=("auth1_session",),
        background_repair=False,
        granted_at=1000,
    )

    assert store.allows(
        provider_id="devin",
        source_instance_id="work",
        browser="chrome",
        profile="Default",
        domain="app.devin.ai",
        field="auth1_session",
    )
    assert not store.allows(
        provider_id="devin",
        source_instance_id="personal",
        browser="chrome",
        profile="Default",
        domain="app.devin.ai",
        field="auth1_session",
    )


def test_same_provider_consents_round_trip_without_collapsing(tmp_path):
    path = tmp_path / "browser-consent.json"
    store = BrowserConsentStore.empty()
    store = store.grant(
        provider_id="devin",
        source_instance_id="personal",
        browser="chrome",
        profile="Default",
        domains=("app.devin.ai",),
        fields=("auth1_session",),
        background_repair=False,
        granted_at=1000,
    )
    store = store.grant(
        provider_id="devin",
        source_instance_id="work",
        browser="chrome",
        profile="Default",
        domains=("app.devin.ai",),
        fields=("auth1_session",),
        background_repair=True,
        granted_at=1001,
    )

    save_browser_consents(store, path)
    loaded = load_browser_consents(path).store

    assert {
        consent.source_instance_id for consent in loaded.consents
    } == {"personal", "work"}
    assert loaded.allows(
        provider_id="devin",
        source_instance_id="personal",
        browser="chrome",
        profile="Default",
        domain="app.devin.ai",
        field="auth1_session",
    )
    assert loaded.allows(
        provider_id="devin",
        source_instance_id="work",
        browser="chrome",
        profile="Default",
        domain="app.devin.ai",
        field="auth1_session",
    )

    updated = loaded.revoke("devin", "chrome", "Default", source_instance_id="work")
    assert {
        consent.source_instance_id for consent in updated.consents
    } == {"personal"}


def test_legacy_consent_documents_migrate_to_default_source_instance():
    loaded = load_browser_consents(
        reader=lambda _path: json.dumps(
            {
                "settings_schema_version": 1,
                "consents": [
                    {
                        "provider_id": "devin",
                        "browser": "chrome",
                        "profile": "Default",
                        "domains": ["app.devin.ai"],
                        "fields": ["auth1_session"],
                        "background_repair": False,
                        "granted_at": 1000,
                    }
                ],
            }
        )
    )

    assert loaded.store.consents[0].source_instance_id == "default"
    writes = []
    save_browser_consents(
        loaded.store,
        loaded=loaded,
        writer=lambda _path, text: writes.append(json.loads(text)),
    )
    assert writes[0]["settings_schema_version"] == BROWSER_CONSENT_SCHEMA_VERSION
    assert writes[0]["consents"][0]["source_instance_id"] == "default"


def test_settings_round_trip_preserves_unknown_fields():
    loaded = load_browser_consents(
        reader=lambda _path: json.dumps(
            {
                "settings_schema_version": BROWSER_CONSENT_SCHEMA_VERSION,
                "future_extension": {"keep": True},
                "consents": [],
            }
        )
    )
    writes = []
    save_browser_consents(
        loaded.store.grant(
            provider_id="devin",
            browser="chrome",
            profile="Default",
            domains=("app.devin.ai",),
            fields=("auth1_session",),
            background_repair=True,
            granted_at=1000,
        ),
        loaded=loaded,
        writer=lambda _path, text: writes.append(json.loads(text)),
    )
    assert writes[0]["future_extension"] == {"keep": True}
    assert writes[0]["consents"][0]["background_repair"] is True


def test_chromium_local_storage_decoder_keeps_latest_live_record():
    records = (
        ChromiumRecord(1, "live", b"_https://app.devin.ai\x00\x01auth1_session", b"\x01old"),
        ChromiumRecord(2, "live", b"_https://app.devin.ai\x00\x01auth1_session", b"\x01new"),
        ChromiumRecord(3, "deleted", b"_https://app.devin.ai\x00\x01other", b"\x01gone"),
        ChromiumRecord(4, "live", b"_https://example.com\x00\x01auth1_session", b"\x01wrong"),
    )
    result = decode_chromium_local_storage(
        records,
        origin="https://app.devin.ai",
        allowed_keys=("auth1_session",),
    )
    assert result == {"auth1_session": "new"}


def test_devin_session_extraction_is_bounded_and_known_key_only():
    token, organization = extract_devin_session(
        {
            "auth1_session": json.dumps({"token": "auth1_fixture_value_long"}),
            "last-internal-org-for-external-org-v1-fixture": "org_fixture",
            "unrelated": json.dumps({"access_token": "should-not-be-used"}),
        }
    )
    assert token == "auth1_fixture_value_long"
    assert organization == "org_fixture"
