"""Consent-gated, read-only browser session import for native providers."""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from .provider_browser_consent import (
    BrowserConsentStore,
    ChromiumRecord,
    decode_chromium_local_storage,
    extract_devin_session,
)

_MAX_LEVELDB_FILES = 512
_MAX_LEVELDB_BYTES = 256 * 1024 * 1024
_MAX_LEVELDB_RECORDS = 250_000
_LEVELDB_FILE = re.compile(
    r"(?:[0-9A-Fa-f]{6}\.(?:ldb|sst|log)|CURRENT|LOCK|LOG(?:\.old)?|MANIFEST-[0-9A-Fa-f]{6})\Z"
)
_DEVIN_ORG_PREFIX = "last-internal-org-for-external-org-v1-"


class BrowserImportState(str, Enum):
    IMPORTED = "imported"
    CONSENT_REQUIRED = "consent_required"
    SESSION_NOT_FOUND = "session_not_found"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class BrowserImportResult:
    provider_id: str
    state: BrowserImportState
    organization: str | None
    reason: str | None
    source_label: str | None

    def __repr__(self) -> str:
        return (
            "BrowserImportResult("
            f"provider_id={self.provider_id!r}, state={self.state.value!r}, "
            f"organization={self.organization!r}, reason={self.reason!r}, "
            f"source_label={self.source_label!r})"
        )


def _profile_leveldb(profile_root: Path) -> Path:
    root = Path(profile_root).expanduser()
    source = root / "Local Storage" / "leveldb"
    try:
        root_info = root.lstat()
        source_info = source.lstat()
    except OSError as error:
        raise OSError("profile_store_not_found") from error
    if (
        stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(source_info.st_mode)
        or not stat.S_ISDIR(source_info.st_mode)
        or root_info.st_uid != os.getuid()
        or source_info.st_uid != os.getuid()
    ):
        raise OSError("unsafe_profile_store")
    return source


def _copy_leveldb_snapshot(source: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    file_count = 0
    total_bytes = 0
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        try:
            info = entry.lstat()
        except OSError as error:
            raise OSError("profile_store_changed") from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            continue
        if _LEVELDB_FILE.fullmatch(entry.name) is None:
            continue
        file_count += 1
        total_bytes += info.st_size
        if file_count > _MAX_LEVELDB_FILES or total_bytes > _MAX_LEVELDB_BYTES:
            raise OSError("profile_store_too_large")
        target = destination / entry.name
        with entry.open("rb") as reader, target.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        os.chmod(target, 0o600)
    if file_count == 0:
        raise OSError("profile_store_empty")


def _script_key(user_key: bytes, origin: str) -> str | None:
    if not user_key.startswith(b"_") or b"\x00" not in user_key:
        return None
    raw_origin, encoded = user_key[1:].split(b"\x00", 1)
    try:
        decoded_origin = raw_origin.decode("latin-1").rstrip("/")
    except UnicodeDecodeError:
        return None
    if decoded_origin != origin.rstrip("/") or not encoded:
        return None
    try:
        if encoded[0] == 0:
            return encoded[1:].decode("utf-16-le")
        if encoded[0] == 1:
            return encoded[1:].decode("latin-1")
    except UnicodeDecodeError:
        return None
    return None


def _default_leveldb_runtime():
    import rleveldb

    return rleveldb.RawLevelDb, rleveldb.KeyState.Live, rleveldb.KeyState.Deleted


def read_chromium_local_storage(
    profile_root: Path,
    *,
    origin: str,
    allowed_keys: tuple[str, ...],
    allowed_prefixes: tuple[str, ...] = (),
    raw_db_factory: Callable[[str], object] | None = None,
    key_state_live: object | None = None,
    key_state_deleted: object | None = None,
) -> dict[str, str]:
    source = _profile_leveldb(Path(profile_root))
    if raw_db_factory is None or key_state_live is None or key_state_deleted is None:
        default_factory, default_live, default_deleted = _default_leveldb_runtime()
        raw_db_factory = raw_db_factory or default_factory
        key_state_live = default_live if key_state_live is None else key_state_live
        key_state_deleted = default_deleted if key_state_deleted is None else key_state_deleted

    with tempfile.TemporaryDirectory(prefix="sidepulse-browser-import-") as directory:
        snapshot = Path(directory) / "leveldb"
        _copy_leveldb_snapshot(source, snapshot)
        try:
            with raw_db_factory(str(snapshot)) as database:
                raw_records = database.iterate_records_raw()
        except Exception as error:
            raise OSError("leveldb_read_failed") from error
        if not isinstance(raw_records, list) or len(raw_records) > _MAX_LEVELDB_RECORDS:
            raise OSError("leveldb_record_budget_exceeded")
        records: list[ChromiumRecord] = []
        dynamic_keys: set[str] = set(allowed_keys)
        for raw in raw_records:
            state = getattr(raw, "state", None)
            if state is key_state_live:
                normalized_state = "live"
            elif state is key_state_deleted:
                normalized_state = "deleted"
            else:
                continue
            user_key = getattr(raw, "user_key", b"")
            value = getattr(raw, "value", b"")
            sequence = getattr(raw, "seq", -1)
            try:
                record = ChromiumRecord(sequence, normalized_state, user_key, value)
            except (TypeError, ValueError):
                continue
            records.append(record)
            key = _script_key(record.user_key, origin)
            if key is not None and any(key.startswith(prefix) for prefix in allowed_prefixes):
                dynamic_keys.add(key)
        return decode_chromium_local_storage(
            tuple(records),
            origin=origin,
            allowed_keys=tuple(sorted(dynamic_keys)),
        )


def import_devin_browser_session(
    *,
    browser: str,
    profile: str,
    profile_root: Path,
    consents: BrowserConsentStore,
    credentials,
    raw_db_factory: Callable[[str], object] | None = None,
    key_state_live: object | None = None,
    key_state_deleted: object | None = None,
) -> BrowserImportResult:
    if not (
        consents.allows(
            provider_id="devin",
            browser=browser,
            profile=profile,
            domain="app.devin.ai",
            field="auth1_session",
        )
        and consents.allows(
            provider_id="devin",
            browser=browser,
            profile=profile,
            domain="app.devin.ai",
            field="organization",
        )
    ):
        return BrowserImportResult(
            "devin",
            BrowserImportState.CONSENT_REQUIRED,
            None,
            "browser_consent_required",
            None,
        )
    try:
        storage = read_chromium_local_storage(
            profile_root,
            origin="https://app.devin.ai",
            allowed_keys=("auth1_session",),
            allowed_prefixes=(_DEVIN_ORG_PREFIX,),
            raw_db_factory=raw_db_factory,
            key_state_live=key_state_live,
            key_state_deleted=key_state_deleted,
        )
    except ModuleNotFoundError:
        return BrowserImportResult(
            "devin",
            BrowserImportState.UNAVAILABLE,
            None,
            "leveldb_reader_unavailable",
            None,
        )
    except OSError as error:
        return BrowserImportResult(
            "devin",
            BrowserImportState.UNAVAILABLE,
            None,
            str(error),
            None,
        )
    token, organization = extract_devin_session(storage)
    if token is None:
        return BrowserImportResult(
            "devin",
            BrowserImportState.SESSION_NOT_FOUND,
            organization,
            "browser_session_not_found",
            f"{browser} {profile}",
        )
    try:
        credentials.set("devin", "token", token)
    except Exception:
        return BrowserImportResult(
            "devin",
            BrowserImportState.UNAVAILABLE,
            organization,
            "keychain_write_failed",
            f"{browser} {profile}",
        )
    return BrowserImportResult(
        "devin",
        BrowserImportState.IMPORTED,
        organization,
        None,
        f"{browser} {profile}",
    )


__all__ = [
    "BrowserImportResult",
    "BrowserImportState",
    "import_devin_browser_session",
    "read_chromium_local_storage",
]
