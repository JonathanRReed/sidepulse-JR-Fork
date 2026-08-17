from __future__ import annotations

from pathlib import Path

from sidepulse.provider_browser_sources import (
    BrowserImportResult,
    import_devin_chromium_session,
)
from sidepulse.provider_usage_settings import BrowserConsent


def consent(**changes) -> BrowserConsent:
    values = dict(
        provider_id="devin",
        browser="chrome",
        profile="Default",
        domains=("app.devin.ai",),
        fields=("auth1_session", "last-internal-org-for-external-org-v1-*"),
        background_repair=False,
        granted_at=100.0,
    )
    values.update(changes)
    return BrowserConsent(**values)


def profile(tmp_path: Path, payload: bytes) -> Path:
    root = tmp_path / "Default"
    store = root / "Local Storage" / "leveldb"
    store.mkdir(parents=True)
    (store / "000001.log").write_bytes(payload)
    return root


def test_import_requires_exact_provider_consent(tmp_path: Path) -> None:
    root = profile(tmp_path, b"auth1_abcdefghijklmnopqrstuvwxyz")
    try:
        import_devin_chromium_session(
            consent(provider_id="cursor"),
            root,
        )
    except ValueError as exc:
        assert "Devin" in str(exc)
    else:
        raise AssertionError("wrong-provider consent was accepted")


def test_devin_session_is_extracted_from_isolated_leveldb_copy(tmp_path: Path) -> None:
    source = (
        b"https://app.devin.ai\x00auth1_session\x00"
        b"auth1_abcdefghijklmnopqrstuvwxyz123456\x00"
        b"last-internal-org-for-external-org-v1-example\x00org_abcdefgh12345678"
    )
    root = profile(tmp_path, source)
    before = (root / "Local Storage" / "leveldb" / "000001.log").read_bytes()

    result = import_devin_chromium_session(consent(), root)

    assert result.access_token == "auth1_abcdefghijklmnopqrstuvwxyz123456"
    assert result.organization == "organizations/org_abcdefgh12345678"
    assert "auth1_" not in repr(result)
    assert (root / "Local Storage" / "leveldb" / "000001.log").read_bytes() == before


def test_import_refuses_symlinks_and_oversized_files(tmp_path: Path) -> None:
    root = tmp_path / "Default"
    store = root / "Local Storage" / "leveldb"
    store.mkdir(parents=True)
    outside = tmp_path / "outside.log"
    outside.write_bytes(b"auth1_abcdefghijklmnopqrstuvwxyz123456")
    (store / "linked.log").symlink_to(outside)

    result = import_devin_chromium_session(consent(), root)
    assert result.reason == "no_session"


def test_result_constructor_redacts_token() -> None:
    result = BrowserImportResult(
        provider_id="devin",
        reason="ok",
        access_token="secret-token",
        organization="org/example",
        source_label="Chrome Default",
    )
    assert "secret-token" not in repr(result)
