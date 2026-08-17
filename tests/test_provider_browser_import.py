from __future__ import annotations

import json
from pathlib import Path

from sidepulse.provider_browser_consent import BrowserConsentStore
from sidepulse.provider_browser_import import (
    BrowserImportState,
    import_devin_browser_session,
    read_chromium_local_storage,
)


class Record:
    def __init__(self, seq, state, user_key, value):
        self.seq = seq
        self.state = state
        self.user_key = user_key
        self.value = value


class RawDb:
    def __init__(self, path, records, opened):
        self.path = Path(path)
        self.records = records
        opened.append(self.path)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iterate_records_raw(self):
        return list(self.records)


class KeyState:
    Live = object()
    Deleted = object()


class Credentials:
    def __init__(self):
        self.values = {}

    def set(self, provider, account, secret):
        self.values[(provider, account)] = secret


def profile(tmp_path: Path) -> Path:
    root = tmp_path / "Default"
    leveldb = root / "Local Storage" / "leveldb"
    leveldb.mkdir(parents=True)
    (leveldb / "000001.log").write_bytes(b"fixture")
    (leveldb / "CURRENT").write_text("MANIFEST-000001\n")
    return root


def consent() -> BrowserConsentStore:
    return BrowserConsentStore.empty().grant(
        provider_id="devin",
        browser="chrome",
        profile="Default",
        domains=("app.devin.ai",),
        fields=("auth1_session", "organization"),
        background_repair=False,
        granted_at=1000,
    )


def test_leveldb_is_copied_before_reading_and_prefix_keys_are_bounded(tmp_path: Path):
    root = profile(tmp_path)
    opened = []
    records = [
        Record(
            1,
            KeyState.Live,
            b"_https://app.devin.ai\x00\x01auth1_session",
            b"\x01" + json.dumps({"token": "auth1_fixture_value_long"}).encode("latin-1"),
        ),
        Record(
            2,
            KeyState.Live,
            b"_https://app.devin.ai\x00\x01last-internal-org-for-external-org-v1-fixture",
            b"\x01org_fixture",
        ),
    ]

    result = read_chromium_local_storage(
        root,
        origin="https://app.devin.ai",
        allowed_keys=("auth1_session",),
        allowed_prefixes=("last-internal-org-for-external-org-v1-",),
        raw_db_factory=lambda path: RawDb(path, records, opened),
        key_state_live=KeyState.Live,
        key_state_deleted=KeyState.Deleted,
    )

    assert result["auth1_session"].startswith("{")
    assert result["last-internal-org-for-external-org-v1-fixture"] == "org_fixture"
    assert opened and opened[0] != root / "Local Storage" / "leveldb"
    assert not opened[0].exists()


def test_import_requires_exact_provider_browser_profile_consent(tmp_path: Path):
    credentials = Credentials()
    result = import_devin_browser_session(
        browser="chrome",
        profile="Default",
        profile_root=profile(tmp_path),
        consents=BrowserConsentStore.empty(),
        credentials=credentials,
    )
    assert result.state is BrowserImportState.CONSENT_REQUIRED
    assert credentials.values == {}


def test_import_stores_only_validated_session_and_returns_org(tmp_path: Path):
    root = profile(tmp_path)
    records = [
        Record(
            1,
            KeyState.Live,
            b"_https://app.devin.ai\x00\x01auth1_session",
            b"\x01" + json.dumps({"token": "auth1_fixture_value_long"}).encode("latin-1"),
        ),
        Record(
            2,
            KeyState.Live,
            b"_https://app.devin.ai\x00\x01last-internal-org-for-external-org-v1-fixture",
            b"\x01org_fixture",
        ),
    ]
    credentials = Credentials()

    result = import_devin_browser_session(
        browser="chrome",
        profile="Default",
        profile_root=root,
        consents=consent(),
        credentials=credentials,
        raw_db_factory=lambda path: RawDb(path, records, []),
        key_state_live=KeyState.Live,
        key_state_deleted=KeyState.Deleted,
    )

    assert result.state is BrowserImportState.IMPORTED
    assert result.organization == "org_fixture"
    assert credentials.values == {("devin", "token"): "auth1_fixture_value_long"}
    assert "auth1_fixture_value_long" not in repr(result)


def test_import_refuses_symlinked_profile_store(tmp_path: Path):
    root = tmp_path / "Default"
    root.mkdir()
    target = tmp_path / "real-leveldb"
    target.mkdir()
    (root / "Local Storage").mkdir()
    (root / "Local Storage" / "leveldb").symlink_to(target, target_is_directory=True)

    result = import_devin_browser_session(
        browser="chrome",
        profile="Default",
        profile_root=root,
        consents=consent(),
        credentials=Credentials(),
    )
    assert result.state is BrowserImportState.UNAVAILABLE
    assert result.reason == "unsafe_profile_store"
