"""Explicit, isolated browser-backed provider sources.

Browser state is never read merely because it exists. The caller must supply a
stored consent record for the exact provider, browser, and profile. Approved
files are copied into a private temporary directory before bounded parsing, so
SidePulse never opens a browser-owned LevelDB file in place.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .provider_usage_settings import BrowserConsent

MAX_LEVELDB_FILES = 128
MAX_LEVELDB_FILE_BYTES = 16 * 1024 * 1024
MAX_LEVELDB_TOTAL_BYTES = 64 * 1024 * 1024
_AUTH1 = re.compile(rb"auth1_[A-Za-z0-9._~-]{20,4096}")
_JWT = re.compile(rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_INTERNAL_ORG = re.compile(rb"org[-_][A-Za-z0-9_-]{8,200}")


@dataclass(frozen=True, slots=True)
class BrowserImportResult:
    provider_id: str
    reason: str
    access_token: str | None
    organization: str | None
    source_label: str

    def __repr__(self) -> str:
        return (
            "BrowserImportResult("
            f"provider_id={self.provider_id!r}, reason={self.reason!r}, "
            f"organization={self.organization!r}, "
            f"source_label={self.source_label!r}, "
            f"access_token={'<redacted>' if self.access_token else None})"
        )


def _validate_consent(consent: BrowserConsent, profile_root: Path) -> None:
    if type(consent) is not BrowserConsent or consent.provider_id != "devin":
        raise ValueError("Devin browser import requires Devin consent")
    if consent.browser not in {"chrome", "chromium", "brave", "edge"}:
        raise ValueError("Devin browser import requires a Chromium profile")
    if "app.devin.ai" not in consent.domains:
        raise ValueError("Devin consent does not allow app.devin.ai")
    if not any(
        field == "auth1_session" or field.startswith("auth1")
        for field in consent.fields
    ):
        raise ValueError("Devin consent does not allow the session field")
    if profile_root.name != consent.profile:
        raise ValueError("browser profile does not match consent")


def _copy_leveldb(source: Path, destination: Path) -> tuple[Path, ...]:
    copied: list[Path] = []
    total = 0
    try:
        entries = sorted(source.iterdir(), key=lambda path: path.name)
    except OSError:
        return ()
    for entry in entries[:MAX_LEVELDB_FILES]:
        try:
            info = entry.lstat()
        except OSError:
            continue
        if entry.is_symlink() or not entry.is_file():
            continue
        if info.st_size <= 0 or info.st_size > MAX_LEVELDB_FILE_BYTES:
            continue
        total += info.st_size
        if total > MAX_LEVELDB_TOTAL_BYTES:
            break
        target = destination / entry.name
        try:
            shutil.copyfile(entry, target, follow_symlinks=False)
            os.chmod(target, 0o600)
        except OSError:
            continue
        copied.append(target)
    return tuple(copied)


def _decode(match: bytes | None) -> str | None:
    if not match:
        return None
    try:
        return match.decode("ascii")
    except UnicodeDecodeError:
        return None


def _extract(files: tuple[Path, ...]) -> tuple[str | None, str | None]:
    token = None
    organization = None
    for path in files:
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        if token is None:
            auth = _AUTH1.search(payload)
            jwt = _JWT.search(payload)
            token = _decode(auth.group(0) if auth else jwt.group(0) if jwt else None)
        if organization is None:
            match = _INTERNAL_ORG.search(payload)
            raw = _decode(match.group(0) if match else None)
            if raw:
                organization = f"organizations/{raw}"
        if token and organization:
            break
    return token, organization


def import_devin_chromium_session(
    consent: BrowserConsent,
    profile_root: Path,
) -> BrowserImportResult:
    root = Path(profile_root).expanduser().absolute()
    _validate_consent(consent, root)
    source = root / "Local Storage" / "leveldb"
    source_label = f"{consent.browser.title()} {consent.profile}"
    if not source.is_dir() or source.is_symlink():
        return BrowserImportResult(
            "devin", "no_session", None, None, source_label
        )
    with tempfile.TemporaryDirectory(prefix="sidepulse-browser-import-") as directory:
        isolated = Path(directory)
        os.chmod(isolated, 0o700)
        files = _copy_leveldb(source, isolated)
        token, organization = _extract(files)
    return BrowserImportResult(
        provider_id="devin",
        reason="ok" if token else "no_session",
        access_token=token,
        organization=organization,
        source_label=source_label,
    )


__all__ = ["BrowserImportResult", "import_devin_chromium_session"]
