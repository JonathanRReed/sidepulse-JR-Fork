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
            "auth1_session": json.dumps({"token": "auth1_fixture_value"}),
            "last-internal-org-for-external-org-v1-fixture": "org_fixture",
            "unrelated": json.dumps({"access_token": "should-not-be-used"}),
        }
    )
    assert token == "auth1_fixture_value"
    assert organization == "org_fixture"
