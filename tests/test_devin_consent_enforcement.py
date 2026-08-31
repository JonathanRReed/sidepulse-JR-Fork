"""Devin browser reads require one exact persisted consent identity."""

from __future__ import annotations

import json
from pathlib import Path

from sidepulse.provider_browser_consent import BrowserConsentStore
from sidepulse.provider_usage_settings import default_provider_usage_settings


class Credentials:
    def __init__(self, token: str | None = None):
        self.token = token

    def get(self, provider_id, account):
        value = self.token if (provider_id, account) == ("devin", "token") else None
        return type(
            "Read",
            (),
            {"available": value is not None, "secret": value, "reason": None},
        )()


def _preference(*, browser_sources: bool = True):
    settings = default_provider_usage_settings().with_browser_sources(
        "devin", browser_sources
    )
    return settings.preference("devin")


def _grant(
    *, browser: str = "zen", profile: str = "Default"
) -> BrowserConsentStore:
    return BrowserConsentStore.empty().grant(
        provider_id="devin",
        browser=browser,
        profile=profile,
        domains=("app.devin.ai",),
        fields=("auth1_session", "organization"),
        background_repair=False,
        granted_at=1000.0,
    )


def test_browser_sources_setting_alone_never_invokes_a_browser_reader(monkeypatch):
    from sidepulse import browser_session_import
    from sidepulse import provider_usage_collectors as collectors

    monkeypatch.setattr(
        browser_session_import,
        "import_devin_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("browser_sources is not browser consent")
        ),
    )
    calls = []

    def http_json(method, url, *, headers, timeout):
        calls.append((method, url, headers, timeout))
        return {"daily_percentage": 10}

    result = collectors.collect_devin(
        _preference(),
        observed_at=1000.0,
        credentials=Credentials("stored-devin-token"),
        http_json=http_json,
    )

    assert result.state.value == "source_not_found"
    assert calls == [], "organization remains required, but no browser was read"


def test_consent_resolver_fails_closed_before_reading_for_missing_or_wrong_scope(
    tmp_path: Path,
):
    from sidepulse.provider_browser_access import _consented_devin_session

    reads = []

    def reader(*, home, browser, profile):
        reads.append((home, browser, profile))
        raise AssertionError("a mismatched consent must fail before filesystem access")

    assert (
        _consented_devin_session(
            home=tmp_path,
            consents=BrowserConsentStore.empty(),
            session_reader=reader,
        )
        is None
    )
    wrong = BrowserConsentStore.empty().grant(
        provider_id="devin",
        browser="zen",
        profile="Default",
        domains=("example.com",),
        fields=("auth1_session", "organization"),
        background_repair=False,
        granted_at=1000.0,
    )
    assert (
        _consented_devin_session(
            home=tmp_path,
            consents=wrong,
            session_reader=reader,
        )
        is None
    )
    assert reads == []


def test_consent_resolver_refuses_ambiguous_profiles_before_reading(tmp_path: Path):
    from sidepulse.provider_browser_access import _consented_devin_session

    consents = _grant(profile="Default").grant(
        provider_id="devin",
        browser="zen",
        profile="Work",
        domains=("app.devin.ai",),
        fields=("auth1_session", "organization"),
        background_repair=False,
        granted_at=1001.0,
    )
    reads = []
    assert (
        _consented_devin_session(
            home=tmp_path,
            consents=consents,
            session_reader=lambda **kwargs: reads.append(kwargs),
        )
        is None
    )
    assert reads == []


def test_consent_resolver_reads_only_the_exact_granted_browser_profile(
    tmp_path: Path,
):
    from sidepulse.browser_session_import import BrowserSession
    from sidepulse.provider_browser_access import _consented_devin_session

    expected = BrowserSession(
        token="auth1_exact-profile-token",
        organization="org/acme",
        internal_organization_id="org-abc12345",
        source_label="Zen Work",
    )
    reads = []

    def reader(*, home, browser, profile):
        reads.append((home, browser, profile))
        return expected

    result = _consented_devin_session(
        home=tmp_path,
        consents=_grant(profile="Work"),
        session_reader=reader,
    )
    assert result is expected
    assert reads == [(tmp_path, "zen", "Work")]


def test_exact_firefox_family_reader_never_scans_sibling_profiles(tmp_path: Path):
    from sidepulse.browser_session_import import (
        DEVIN_ORIGIN,
        import_devin_session_from_profile,
        origin_directory_name,
    )

    root = tmp_path / "Library" / "Application Support" / "zen" / "Profiles"
    target = root / "Work"
    sibling = root / "Personal"
    for profile, token in ((target, "auth1_work_profile_token"), (sibling, "auth1_personal_profile_token")):
        database_dir = (
            profile
            / "storage"
            / "default"
            / origin_directory_name(DEVIN_ORIGIN)
            / "ls"
        )
        database_dir.mkdir(parents=True)
        import sqlite3

        with sqlite3.connect(database_dir / "data.sqlite") as connection:
            connection.execute(
                "CREATE TABLE data (key TEXT, value BLOB, compression_type INTEGER)"
            )
            connection.execute(
                "INSERT INTO data VALUES (?, ?, 0)",
                ("auth1_session", json.dumps({"token": token})),
            )

    session = import_devin_session_from_profile(
        home=tmp_path,
        browser="zen",
        profile="Work",
    )
    assert session is not None
    assert session.token == "auth1_work_profile_token"
    assert session.source_label == "Zen Work"


def test_firefox_reader_never_returns_unconsented_local_storage_fields(
    tmp_path: Path,
):
    import sqlite3

    from sidepulse.browser_session_import import read_local_storage

    database = tmp_path / "data.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE data (key TEXT, value BLOB, compression_type INTEGER)"
        )
        connection.executemany(
            "INSERT INTO data VALUES (?, ?, 0)",
            (
                ("auth1_session", json.dumps({"token": "auth1_exact-field-token"})),
                ("totally-unrelated-cache", "private unrelated value"),
            ),
        )

    entries = read_local_storage(database)

    assert "auth1_session" in entries
    assert "totally-unrelated-cache" not in entries


def test_profile_name_cannot_escape_the_consented_browser_root(tmp_path: Path):
    from sidepulse.browser_session_import import import_devin_session_from_profile

    assert (
        import_devin_session_from_profile(
            home=tmp_path,
            browser="zen",
            profile="../Other",
        )
        is None
    )
