"""Explicit, provider-scoped browser consent and Chromium localStorage decoding."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

BROWSER_CONSENT_SCHEMA_VERSION = 1
_PROVIDER = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
_FIELD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}\Z")
_ALLOWED_BROWSERS = frozenset({"chrome", "chromium", "brave", "edge", "firefox"})


class BrowserConsentError(ValueError):
    pass


class BrowserConsentWriteRefusedError(BrowserConsentError):
    pass


@dataclass(frozen=True, slots=True)
class BrowserConsent:
    provider_id: str
    browser: str
    profile: str
    domains: tuple[str, ...]
    fields: tuple[str, ...]
    background_repair: bool
    granted_at: float

    def __post_init__(self) -> None:
        normalized_domains = tuple(domain.lower().strip(".") for domain in self.domains)
        if (
            _PROVIDER.fullmatch(self.provider_id or "") is None
            or self.browser not in _ALLOWED_BROWSERS
            or not isinstance(self.profile, str)
            or not self.profile
            or len(self.profile) > 256
            or type(self.domains) is not tuple
            or not self.domains
            or len(normalized_domains) != len(set(normalized_domains))
            or not all(_DOMAIN.fullmatch(domain) for domain in normalized_domains)
            or type(self.fields) is not tuple
            or not self.fields
            or len(self.fields) != len(set(self.fields))
            or not all(_FIELD.fullmatch(field) for field in self.fields)
            or type(self.background_repair) is not bool
            or isinstance(self.granted_at, bool)
            or not isinstance(self.granted_at, (int, float))
            or float(self.granted_at) < 0.0
        ):
            raise BrowserConsentError("invalid browser consent")
        object.__setattr__(self, "domains", normalized_domains)
        object.__setattr__(self, "granted_at", float(self.granted_at))

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.provider_id, self.browser, self.profile


@dataclass(frozen=True, slots=True)
class BrowserConsentStore:
    consents: tuple[BrowserConsent, ...]

    def __post_init__(self) -> None:
        if type(self.consents) is not tuple or not all(
            type(consent) is BrowserConsent for consent in self.consents
        ):
            raise BrowserConsentError("invalid browser consent store")
        identities = tuple(consent.identity for consent in self.consents)
        if len(identities) != len(set(identities)):
            raise BrowserConsentError("duplicate browser consent")

    @classmethod
    def empty(cls) -> BrowserConsentStore:
        return cls(())

    def grant(
        self,
        *,
        provider_id: str,
        browser: str,
        profile: str,
        domains: tuple[str, ...],
        fields: tuple[str, ...],
        background_repair: bool,
        granted_at: float,
    ) -> BrowserConsentStore:
        consent = BrowserConsent(
            provider_id,
            browser,
            profile,
            domains,
            fields,
            background_repair,
            granted_at,
        )
        rows = [current for current in self.consents if current.identity != consent.identity]
        rows.append(consent)
        return BrowserConsentStore(tuple(sorted(rows, key=lambda row: row.identity)))

    def revoke(self, provider_id: str, browser: str, profile: str) -> BrowserConsentStore:
        identity = (provider_id, browser, profile)
        return BrowserConsentStore(
            tuple(consent for consent in self.consents if consent.identity != identity)
        )

    def allows(
        self,
        *,
        provider_id: str,
        browser: str,
        profile: str,
        domain: str,
        field: str,
    ) -> bool:
        domain = domain.lower().strip(".")
        return any(
            consent.identity == (provider_id, browser, profile)
            and domain in consent.domains
            and field in consent.fields
            for consent in self.consents
        )


@dataclass(frozen=True, slots=True)
class LoadedBrowserConsents:
    store: BrowserConsentStore
    read_only: bool
    unknown_fields: tuple[tuple[str, object], ...]


def default_browser_consent_path(home: Path | None = None) -> Path:
    base = Path.home() if home is None else Path(home)
    return base / ".config" / "sidepulse" / "browser-consent.json"


def _consent_from_document(value: object) -> BrowserConsent | None:
    if not isinstance(value, dict):
        return None
    try:
        return BrowserConsent(
            provider_id=value.get("provider_id"),
            browser=value.get("browser"),
            profile=value.get("profile"),
            domains=tuple(value.get("domains", ())),
            fields=tuple(value.get("fields", ())),
            background_repair=value.get("background_repair", False),
            granted_at=value.get("granted_at", 0.0),
        )
    except (BrowserConsentError, TypeError, ValueError):
        return None


def load_browser_consents(
    path: Path | None = None,
    *,
    reader: Callable[[Path], str] | None = None,
) -> LoadedBrowserConsents:
    target = default_browser_consent_path() if path is None else Path(path)
    read = reader
    if read is None:
        from .private_io import read_private_text

        read = read_private_text
    try:
        document = json.loads(read(target))
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return LoadedBrowserConsents(BrowserConsentStore.empty(), False, ())
    if not isinstance(document, dict):
        return LoadedBrowserConsents(BrowserConsentStore.empty(), True, ())
    version = document.get("settings_schema_version")
    if type(version) is not int or version > BROWSER_CONSENT_SCHEMA_VERSION:
        return LoadedBrowserConsents(
            BrowserConsentStore.empty(),
            True,
            tuple(document.items()),
        )
    rows = document.get("consents", ())
    consents = []
    if isinstance(rows, list):
        for row in rows[:256]:
            parsed = _consent_from_document(row)
            if parsed is not None and parsed.identity not in {
                existing.identity for existing in consents
            }:
                consents.append(parsed)
    unknown = tuple(
        (key, value)
        for key, value in document.items()
        if key not in {"settings_schema_version", "consents"}
    )
    return LoadedBrowserConsents(
        BrowserConsentStore(tuple(sorted(consents, key=lambda item: item.identity))),
        False,
        unknown,
    )


def save_browser_consents(
    store: BrowserConsentStore,
    path: Path | None = None,
    *,
    loaded: LoadedBrowserConsents | None = None,
    writer: Callable[[Path, str], object] | None = None,
) -> Path:
    if type(store) is not BrowserConsentStore:
        raise BrowserConsentError("invalid browser consent store")
    if loaded is not None and loaded.read_only:
        raise BrowserConsentWriteRefusedError("browser consent settings are read-only")
    document = dict(loaded.unknown_fields if loaded is not None else ())
    document["settings_schema_version"] = BROWSER_CONSENT_SCHEMA_VERSION
    document["consents"] = [
        {
            "provider_id": consent.provider_id,
            "browser": consent.browser,
            "profile": consent.profile,
            "domains": list(consent.domains),
            "fields": list(consent.fields),
            "background_repair": consent.background_repair,
            "granted_at": consent.granted_at,
        }
        for consent in store.consents
    ]
    target = default_browser_consent_path() if path is None else Path(path)
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    write = writer
    if write is None:
        from .private_io import atomic_private_write

        write = atomic_private_write
    write(target, text)
    return target


@dataclass(frozen=True, slots=True)
class ChromiumRecord:
    sequence: int
    state: str
    user_key: bytes
    value: bytes

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or self.sequence < 0
            or self.state not in {"live", "deleted"}
            or not isinstance(self.user_key, bytes)
            or not isinstance(self.value, bytes)
            or len(self.user_key) > 64 * 1024
            or len(self.value) > 1024 * 1024
        ):
            raise BrowserConsentError("invalid Chromium record")


def _decode_prefixed(value: bytes) -> str | None:
    if not value:
        return ""
    try:
        if value[0] == 0:
            return value[1:].decode("utf-16-le")
        if value[0] == 1:
            return value[1:].decode("latin-1")
    except UnicodeDecodeError:
        return None
    return None


def decode_chromium_local_storage(
    records: tuple[ChromiumRecord, ...],
    *,
    origin: str,
    allowed_keys: tuple[str, ...],
) -> dict[str, str]:
    if not isinstance(origin, str) or not origin.startswith("https://"):
        raise BrowserConsentError("invalid Chromium origin")
    allowed = frozenset(allowed_keys)
    latest: dict[str, tuple[int, str | None]] = {}
    for record in sorted(records, key=lambda item: item.sequence):
        key = record.user_key
        if not key.startswith(b"_") or b"\x00" not in key:
            continue
        storage_key, encoded_script_key = key[1:].split(b"\x00", 1)
        try:
            decoded_origin = storage_key.decode("latin-1")
        except UnicodeDecodeError:
            continue
        if decoded_origin.rstrip("/") != origin.rstrip("/"):
            continue
        script_key = _decode_prefixed(encoded_script_key)
        if script_key not in allowed:
            continue
        value = _decode_prefixed(record.value) if record.state == "live" else None
        latest[script_key] = (record.sequence, value)
    return {
        key: value
        for key, (_sequence, value) in latest.items()
        if value is not None
    }


def _find_token(value: object) -> str | None:
    if isinstance(value, dict):
        direct = value.get("token")
        if isinstance(direct, str) and 20 <= len(direct) <= 64 * 1024:
            return direct
        for key in ("access_token", "accessToken"):
            candidate = value.get(key)
            if isinstance(candidate, str) and 20 <= len(candidate) <= 64 * 1024:
                return candidate
        for nested in tuple(value.values())[:128]:
            found = _find_token(nested)
            if found is not None:
                return found
    if isinstance(value, list):
        for nested in value[:128]:
            found = _find_token(nested)
            if found is not None:
                return found
    return None


def extract_devin_session(storage: dict[str, str]) -> tuple[str | None, str | None]:
    token = None
    organization = None
    for key, raw in tuple(storage.items())[:256]:
        if key.endswith("auth1_session") or "auth0spajs@@::" in key:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            token = _find_token(parsed)
            if token is not None:
                break
    for key, raw in tuple(storage.items())[:256]:
        if "last-internal-org-for-external-org-v1-" in key:
            candidate = raw.strip().strip('"')
            if candidate and len(candidate) <= 256:
                organization = candidate
                break
    return token, organization


__all__ = [
    "BROWSER_CONSENT_SCHEMA_VERSION",
    "BrowserConsent",
    "BrowserConsentError",
    "BrowserConsentStore",
    "BrowserConsentWriteRefusedError",
    "ChromiumRecord",
    "LoadedBrowserConsents",
    "decode_chromium_local_storage",
    "default_browser_consent_path",
    "extract_devin_session",
    "load_browser_consents",
    "save_browser_consents",
]
