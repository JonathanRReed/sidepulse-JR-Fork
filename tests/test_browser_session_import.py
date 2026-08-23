"""Importing a provider session from the browser the user already uses.

Reported as "Why do I need an API key? The implementation inside of
CodexBar doesn't require an API key. That's messed up." Correct on both
counts: Devin's own web session is a bearer token sitting in browser
local storage, and this app's "Enable browser access" button only ever
flipped a preference flag -- nothing read a browser at all.

Every fixture here is synthetic. These tests must never read the real
profiles on the machine running them.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sidepulse.browser_session_import import (
    DEVIN_ORIGIN,
    devin_session_from_entries,
    firefox_profile_directories,
    import_devin_session,
    is_internal_organization_id,
    origin_directory_name,
    read_local_storage,
)

ORG_ID = "org-0a14cdbfc824400b9e77be502f2060c5"
TOKEN = "auth1_" + "b" * 52


def write_local_storage(profile: Path, rows: list[tuple[str, str, int]]) -> Path:
    """One origin's data.sqlite in Firefox's real on-disk shape."""
    directory = profile / "storage" / "default" / origin_directory_name(DEVIN_ORIGIN) / "ls"
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "data.sqlite"
    connection = sqlite3.connect(database)
    with connection:
        connection.execute(
            "CREATE TABLE data( key TEXT PRIMARY KEY, utf16_length INTEGER NOT NULL, "
            "conversion_type INTEGER NOT NULL, compression_type INTEGER NOT NULL, "
            "last_access_time INTEGER NOT NULL DEFAULT 0, value BLOB NOT NULL)"
        )
        for key, value, compression in rows:
            connection.execute(
                "INSERT INTO data (key, utf16_length, conversion_type, compression_type, value)"
                " VALUES (?, ?, ?, ?, ?)",
                (key, len(value), 0, compression, value.encode("utf-8")),
            )
    connection.close()
    return database


def zen_profile(home: Path, name: str = "abc.Default (release)") -> Path:
    profile = home / "Library" / "Application Support" / "zen" / "Profiles" / name
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def test_a_real_shaped_zen_profile_yields_token_and_organization(tmp_path) -> None:
    profile = zen_profile(tmp_path)
    write_local_storage(
        profile,
        [
            ("auth1_session", json.dumps({"token": TOKEN, "userId": "u1"}), 0),
            ("last-internal-org-for-external-org-v1-null", ORG_ID, 0),
            # Compressed VALUE, load-bearing KEY: this is where the slug is.
            ("post-auth-v3-null-user-u1-org_name-acme-7535461b", "", 1),
        ],
    )
    session = import_devin_session(tmp_path)
    assert session is not None
    assert session.token == TOKEN
    assert session.organization == "org/acme-7535461b"
    assert session.internal_organization_id == ORG_ID
    assert session.source_label.startswith("Zen")


def test_a_compressed_value_still_contributes_its_key(tmp_path) -> None:
    """The regression that shipped in the first draft of this feature:
    rows whose value is snappy-compressed were dropped whole, taking the
    organization slug (which lives in the KEY) with them and forcing the
    internal-id URL form."""
    profile = zen_profile(tmp_path)
    database = write_local_storage(
        profile,
        [
            ("auth1_session", json.dumps({"token": TOKEN}), 0),
            (f"feature-flags-cache:{ORG_ID}", "compressed-bytes-we-cannot-read", 1),
        ],
    )
    entries = read_local_storage(database)
    assert f"feature-flags-cache:{ORG_ID}" in entries
    session = devin_session_from_entries(entries, source_label="Zen test")
    assert session is not None
    assert session.internal_organization_id == ORG_ID
    assert session.organization == f"organizations/{ORG_ID}"


def test_no_devin_session_is_not_an_error(tmp_path) -> None:
    profile = zen_profile(tmp_path)
    write_local_storage(profile, [("devin-webapp-theme", "dark", 0)])
    assert import_devin_session(tmp_path) is None


def test_a_missing_browser_is_not_an_error(tmp_path) -> None:
    assert import_devin_session(tmp_path) is None
    assert firefox_profile_directories(tmp_path) == []


def test_the_auth0_shape_is_read_when_auth1_is_absent(tmp_path) -> None:
    jwt = "eyJ" + "c" * 40 + ".body.sig"
    session = devin_session_from_entries(
        {
            "@@auth0spajs@@::client::default": json.dumps(
                {"body": {"access_token": jwt}}
            )
        },
        source_label="Zen test",
    )
    assert session is not None
    assert session.token == jwt


def test_a_profile_that_is_a_symlink_is_refused(tmp_path) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    root = tmp_path / "Library" / "Application Support" / "zen" / "Profiles"
    root.mkdir(parents=True)
    (root / "linked").symlink_to(real, target_is_directory=True)
    assert firefox_profile_directories(tmp_path) == []


def test_the_reader_never_writes_to_the_browsers_database(tmp_path) -> None:
    """Opened immutable: a running browser must not be disturbed, and a
    read must not be able to corrupt a profile."""
    profile = zen_profile(tmp_path)
    database = write_local_storage(
        profile, [("auth1_session", json.dumps({"token": TOKEN}), 0)]
    )
    before = database.stat().st_mtime_ns
    assert read_local_storage(database)
    assert database.stat().st_mtime_ns == before
    assert not (database.parent / "data.sqlite-wal").exists()
    assert not (database.parent / "data.sqlite-journal").exists()


def test_a_garbage_database_is_survivable(tmp_path) -> None:
    profile = zen_profile(tmp_path)
    directory = profile / "storage" / "default" / origin_directory_name(DEVIN_ORIGIN) / "ls"
    directory.mkdir(parents=True)
    (directory / "data.sqlite").write_bytes(b"not a database at all")
    assert read_local_storage(directory / "data.sqlite") == {}
    assert import_devin_session(tmp_path) is None


def test_the_best_session_wins_when_several_profiles_have_one(tmp_path) -> None:
    """A profile that knows its organization beats one that does not --
    usage is keyed on the organization, so a token alone is useless."""
    bare = zen_profile(tmp_path, "aaa.Bare")
    write_local_storage(bare, [("auth1_session", json.dumps({"token": TOKEN}), 0)])
    full = zen_profile(tmp_path, "bbb.Full")
    write_local_storage(
        full,
        [
            ("auth1_session", json.dumps({"token": TOKEN}), 0),
            ("last-internal-org-for-external-org-v1-acme", ORG_ID, 0),
        ],
    )
    session = import_devin_session(tmp_path)
    assert session is not None
    assert session.organization == "org/acme"
    assert "bbb.Full" in session.source_label


def test_internal_organization_ids_are_recognised_but_slugs_are_not() -> None:
    assert is_internal_organization_id(ORG_ID)
    assert is_internal_organization_id("org_0a14cdbfc824400b")
    assert not is_internal_organization_id("acme-7535461b")
    assert not is_internal_organization_id("org-")
    assert not is_internal_organization_id("")
