"""Validation for synthetic, provider-owned compatibility fixtures.

Fixtures are test inputs, not captures of provider traffic.  The manifest keeps
ownership and review evidence next to a bounded fixture identity, while the
validator refuses accidental private data before a fixture can enter a test
or fast-validation lane.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path

from .providers import PROVIDER_REGISTRY

PROVIDER_FIXTURE_OWNERSHIP_SCHEMA_VERSION = 1
PROVIDER_FIXTURE_OWNERSHIP_MAX_BYTES = 64 * 1024
PROVIDER_FIXTURE_MAX_BYTES = 64 * 1024
PROVIDER_FIXTURE_MAX_ENTRIES = 128
PROVIDER_FIXTURE_MAX_STRING_LENGTH = 160
PROVIDER_FIXTURE_TYPES = frozenset(
    {"hook-event", "usage-snapshot", "quota-snapshot", "session-state"}
)

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_SLUG_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:[-._][a-z0-9]+)*\Z")
_HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_EMAIL_PATTERN = re.compile(r"\b[^\s@/]+@[^\s@/]+\.[^\s@/]+\b")
_TOKEN_PATTERN = re.compile(
    r"(?:\bbearer\s+|\b(?:sk|pk|ghp|gho|github_pat|xox[baprs])[-_])[A-Za-z0-9._~-]{8,}",
    re.IGNORECASE,
)
_CONTENT_PATTERN = re.compile(r"\b(?:prompt|transcript)\b", re.IGNORECASE)
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:email|path|token|credential|secret|password|passwd|authorization|cookie|prompt|transcript|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_PATH_PATTERN = re.compile(
    r"(?:[\\/]|~)|\b(?:file|path)://|\b[A-Za-z]:[\\/]", re.IGNORECASE
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"\b(?:token|credential|secret|password|api[_ -]?key)\b", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class ProviderFixtureEntry:
    provider: str
    fixture_id: str
    fixture_type: str
    source_reference: str
    reviewed_on: str
    fixture_version: int
    sha256: str
    synthetic: bool


@dataclass(frozen=True, slots=True)
class ProviderFixtureOwnershipManifest:
    schema_version: int
    reviewed_on: str
    cross_provider_identifiers: tuple[str, ...]
    entries: tuple[ProviderFixtureEntry, ...]

    def entry(self, provider: str) -> ProviderFixtureEntry | None:
        return next((entry for entry in self.entries if entry.provider == provider), None)


def load_provider_fixture_ownership_manifest() -> ProviderFixtureOwnershipManifest:
    """Load and validate the packaged ownership manifest, without file I/O on fixtures."""
    resource = files("sidepulse.resources").joinpath("provider_fixture_ownership.json")
    raw = resource.read_bytes()
    if not raw or len(raw) > PROVIDER_FIXTURE_OWNERSHIP_MAX_BYTES:
        raise ValueError("invalid provider fixture ownership manifest")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid provider fixture ownership manifest") from error
    return validate_provider_fixture_document(document)


def validate_provider_fixture_ownership(
    fixtures_root: Path | str | None = None,
) -> ProviderFixtureOwnershipManifest:
    """Validate ownership metadata and every derived synthetic fixture file."""
    manifest = load_provider_fixture_ownership_manifest()
    root = _default_fixture_root() if fixtures_root is None else Path(fixtures_root)
    expected_paths = {_fixture_path(root, entry) for entry in manifest.entries}
    try:
        actual_paths = {path for path in root.rglob("*.json") if path.is_file()}
    except OSError as error:
        raise ValueError("invalid provider fixture root") from error
    if actual_paths != expected_paths:
        raise ValueError("unowned provider fixture file")
    for entry in manifest.entries:
        fixture_path = _fixture_path(root, entry)
        if fixture_path.is_symlink() or not fixture_path.is_file():
            raise ValueError(f"invalid provider fixture file: {entry.fixture_id}")
        try:
            raw = fixture_path.read_bytes()
        except OSError as error:
            raise ValueError(f"missing provider fixture: {entry.fixture_id}") from error
        if not raw or len(raw) > PROVIDER_FIXTURE_MAX_BYTES:
            raise ValueError(f"invalid provider fixture size: {entry.fixture_id}")
        try:
            fixture = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid provider fixture: {entry.fixture_id}") from error
        _validate_fixture(entry, fixture, raw, manifest.cross_provider_identifiers)
    return manifest


def validate_provider_fixture_document(
    document: object,
    fixtures_root: Path | str | None = None,
) -> ProviderFixtureOwnershipManifest:
    """Validate a manifest document, optionally validating its fixture files.

    This small pure entry point lets tests exercise an explicit cross-provider
    allowlist without modifying the packaged resource.
    """
    manifest = _parse_manifest(document)
    if fixtures_root is not None:
        root = Path(fixtures_root)
        for entry in manifest.entries:
            fixture_path = _fixture_path(root, entry)
            if fixture_path.is_symlink() or not fixture_path.is_file():
                raise ValueError(f"invalid provider fixture file: {entry.fixture_id}")
            try:
                raw = fixture_path.read_bytes()
                fixture = json.loads(raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid provider fixture: {entry.fixture_id}") from error
            _validate_fixture(entry, fixture, raw, manifest.cross_provider_identifiers)
    return manifest


def _parse_manifest(document: object) -> ProviderFixtureOwnershipManifest:
    if type(document) is not dict:
        raise ValueError("invalid provider fixture ownership manifest")
    expected_keys = {"schemaVersion", "reviewedOn", "crossProviderIdentifiers", "fixtures"}
    if set(document) != expected_keys:
        raise ValueError("invalid provider fixture ownership manifest")
    if document.get("schemaVersion") != PROVIDER_FIXTURE_OWNERSHIP_SCHEMA_VERSION:
        raise ValueError("unsupported provider fixture ownership schema")
    reviewed_on = _validated_date(document.get("reviewedOn"), "manifest review date")
    identifiers = document.get("crossProviderIdentifiers")
    if type(identifiers) is not list or len(identifiers) > PROVIDER_FIXTURE_MAX_ENTRIES:
        raise ValueError("invalid cross-provider identifier allowlist")
    cross_provider_identifiers: list[str] = []
    known_providers = set(PROVIDER_REGISTRY)
    for identifier in identifiers:
        _validate_bounded_slug(identifier, "cross-provider identifier")
        if identifier not in known_providers:
            raise ValueError("unknown cross-provider identifier")
        if identifier in cross_provider_identifiers:
            raise ValueError("duplicate cross-provider identifier")
        cross_provider_identifiers.append(identifier)

    rows = document.get("fixtures")
    if type(rows) is not list or not 1 <= len(rows) <= PROVIDER_FIXTURE_MAX_ENTRIES:
        raise ValueError("invalid provider fixture entries")
    entries = tuple(_parse_entry(row) for row in rows)
    providers = tuple(entry.provider for entry in entries)
    if len(set(providers)) != len(providers) or set(providers) != known_providers:
        raise ValueError("incomplete provider fixture ownership manifest")
    return ProviderFixtureOwnershipManifest(
        schema_version=PROVIDER_FIXTURE_OWNERSHIP_SCHEMA_VERSION,
        reviewed_on=reviewed_on,
        cross_provider_identifiers=tuple(cross_provider_identifiers),
        entries=tuple(sorted(entries, key=lambda entry: entry.provider)),
    )


def _parse_entry(row: object) -> ProviderFixtureEntry:
    expected_keys = {
        "provider",
        "fixtureId",
        "fixtureType",
        "sourceReference",
        "reviewedOn",
        "fixtureVersion",
        "sha256",
        "synthetic",
    }
    if type(row) is not dict or set(row) != expected_keys:
        raise ValueError("invalid provider fixture ownership entry")
    provider = row.get("provider")
    if not isinstance(provider, str) or provider not in PROVIDER_REGISTRY:
        raise ValueError("invalid provider fixture ownership")
    fixture_id = _validate_bounded_slug(row.get("fixtureId"), "fixture id")
    fixture_type = row.get("fixtureType")
    if not isinstance(fixture_type, str) or fixture_type not in PROVIDER_FIXTURE_TYPES:
        raise ValueError("invalid fixture type")
    source_reference = _validate_bounded_slug(row.get("sourceReference"), "source reference")
    reviewed_on = _validated_date(row.get("reviewedOn"), "fixture review date")
    fixture_version = row.get("fixtureVersion")
    if type(fixture_version) is not int or not 1 <= fixture_version <= 1_000_000:
        raise ValueError("invalid fixture version")
    sha256 = row.get("sha256")
    if not isinstance(sha256, str) or _HASH_PATTERN.fullmatch(sha256) is None:
        raise ValueError("invalid fixture SHA-256")
    if row.get("synthetic") is not True:
        raise ValueError("provider fixtures must be synthetic")
    return ProviderFixtureEntry(
        provider=provider,
        fixture_id=fixture_id,
        fixture_type=fixture_type,
        source_reference=source_reference,
        reviewed_on=reviewed_on,
        fixture_version=fixture_version,
        sha256=sha256,
        synthetic=True,
    )


def _validate_fixture(
    entry: ProviderFixtureEntry,
    fixture: object,
    raw: bytes,
    allowlist: tuple[str, ...],
) -> None:
    expected_keys = {"provider", "fixtureType", "fixtureVersion", "payload"}
    if type(fixture) is not dict or set(fixture) != expected_keys:
        raise ValueError(f"invalid fixture content: {entry.fixture_id}")
    if fixture.get("provider") != entry.provider:
        raise ValueError(f"provider ownership mismatch: {entry.fixture_id}")
    if fixture.get("fixtureType") != entry.fixture_type or fixture.get("fixtureVersion") != entry.fixture_version:
        raise ValueError(f"fixture metadata mismatch: {entry.fixture_id}")
    if not isinstance(fixture.get("payload"), dict):
        raise ValueError(f"invalid fixture payload: {entry.fixture_id}")
    _reject_private_or_content_like_data(fixture["payload"], entry.fixture_id)
    referenced = _provider_identifiers(fixture["payload"])
    unauthorized = referenced - {entry.provider} - set(allowlist)
    if unauthorized:
        raise ValueError(f"cross-provider identifier in fixture: {entry.fixture_id}")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if digest != entry.sha256:
        raise ValueError(f"fixture SHA-256 mismatch: {entry.fixture_id}")


def _reject_private_or_content_like_data(value: object, fixture_id: str, *, key: str = "") -> None:
    if isinstance(value, dict):
        for name, nested in value.items():
            if not isinstance(name, str) or _SENSITIVE_KEY_PATTERN.search(name):
                raise ValueError(f"fixture content contains restricted metadata: {fixture_id}")
            _reject_private_or_content_like_data(nested, fixture_id, key=name)
        return
    if isinstance(value, list):
        for nested in value:
            _reject_private_or_content_like_data(nested, fixture_id, key=key)
        return
    if isinstance(value, str):
        if len(value) > PROVIDER_FIXTURE_MAX_STRING_LENGTH:
            raise ValueError(f"fixture content is unbounded: {fixture_id}")
        if (
            _EMAIL_PATTERN.search(value)
            or _TOKEN_PATTERN.search(value)
            or _SENSITIVE_VALUE_PATTERN.search(value)
            or _CONTENT_PATTERN.search(value)
            or _PATH_PATTERN.search(value)
        ):
            raise ValueError(f"fixture content contains restricted data: {fixture_id}")


def _provider_identifiers(value: object) -> set[str]:
    identifiers: set[str] = set()
    known = set(PROVIDER_REGISTRY)
    if isinstance(value, dict):
        for nested in value.values():
            identifiers.update(_provider_identifiers(nested))
    elif isinstance(value, list):
        for nested in value:
            identifiers.update(_provider_identifiers(nested))
    elif isinstance(value, str) and value in known:
        identifiers.add(value)
    return identifiers


def _validated_date(value: object, label: str) -> str:
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"invalid {label}") from error
    return value


def _validate_bounded_slug(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= PROVIDER_FIXTURE_MAX_STRING_LENGTH
        or _SLUG_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(f"invalid {label}")
    if (
        _EMAIL_PATTERN.search(value)
        or _TOKEN_PATTERN.search(value)
        or _SENSITIVE_VALUE_PATTERN.search(value)
        or _CONTENT_PATTERN.search(value)
        or _PATH_PATTERN.search(value)
    ):
        raise ValueError(f"restricted {label}")
    return value


def _fixture_path(root: Path, entry: ProviderFixtureEntry) -> Path:
    return root / entry.provider / f"{entry.fixture_id}.json"


def _default_fixture_root() -> Path:
    return Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "providers"


__all__ = [
    "PROVIDER_FIXTURE_OWNERSHIP_MAX_BYTES",
    "PROVIDER_FIXTURE_OWNERSHIP_SCHEMA_VERSION",
    "PROVIDER_FIXTURE_TYPES",
    "ProviderFixtureEntry",
    "ProviderFixtureOwnershipManifest",
    "load_provider_fixture_ownership_manifest",
    "validate_provider_fixture_document",
    "validate_provider_fixture_ownership",
]
