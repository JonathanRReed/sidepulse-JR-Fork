"""Packaged compatibility manifest for reviewed external integrations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files

INTEGRATION_COMPATIBILITY_SCHEMA_VERSION = 1
INTEGRATION_COMPATIBILITY_MAX_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class IntegrationCompatibilityEntry:
    integration: str
    minimum_version: str
    maximum_tested_version: str
    protocol_fingerprint: str
    source_commit: str
    fixture_version: int
    connection_mode: str


@dataclass(frozen=True, slots=True)
class IntegrationCompatibilityManifest:
    schema_version: int
    entries: tuple[IntegrationCompatibilityEntry, ...]

    def entry(self, integration: str) -> IntegrationCompatibilityEntry | None:
        return next(
            (entry for entry in self.entries if entry.integration == integration),
            None,
        )


def load_integration_compatibility_manifest() -> IntegrationCompatibilityManifest:
    resource = files("sidepulse.resources").joinpath(
        "integration_compatibility.json"
    )
    raw = resource.read_bytes()
    if not raw or len(raw) > INTEGRATION_COMPATIBILITY_MAX_BYTES:
        raise ValueError("invalid integration compatibility manifest")
    document = json.loads(raw)
    if (
        type(document) is not dict
        or document.get("schemaVersion")
        != INTEGRATION_COMPATIBILITY_SCHEMA_VERSION
        or type(document.get("integrations")) is not list
    ):
        raise ValueError("invalid integration compatibility manifest")
    entries = []
    for row in document["integrations"]:
        if type(row) is not dict:
            raise ValueError("invalid integration compatibility entry")
        entry = IntegrationCompatibilityEntry(
            integration=str(row.get("integration") or ""),
            minimum_version=str(row.get("minimumVersion") or ""),
            maximum_tested_version=str(row.get("maximumTestedVersion") or ""),
            protocol_fingerprint=str(row.get("protocolFingerprint") or ""),
            source_commit=str(row.get("sourceCommit") or ""),
            fixture_version=row.get("fixtureVersion"),
            connection_mode=str(row.get("connectionMode") or ""),
        )
        if not (
            entry.integration == "t3code"
            and entry.minimum_version
            and entry.maximum_tested_version
            and entry.protocol_fingerprint.startswith("sha256:")
            and len(entry.protocol_fingerprint) == 71
            and len(entry.source_commit) == 40
            and type(entry.fixture_version) is int
            and entry.fixture_version >= 1
            and entry.connection_mode
        ):
            raise ValueError("invalid integration compatibility entry")
        entries.append(entry)
    if len(entries) != 1 or {entry.integration for entry in entries} != {"t3code"}:
        raise ValueError("incomplete integration compatibility manifest")
    return IntegrationCompatibilityManifest(
        schema_version=INTEGRATION_COMPATIBILITY_SCHEMA_VERSION,
        entries=tuple(sorted(entries, key=lambda entry: entry.integration)),
    )


__all__ = [
    "INTEGRATION_COMPATIBILITY_SCHEMA_VERSION",
    "IntegrationCompatibilityEntry",
    "IntegrationCompatibilityManifest",
    "load_integration_compatibility_manifest",
]
