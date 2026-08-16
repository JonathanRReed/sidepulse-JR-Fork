"""Typed in-process snapshots and domain deltas for refresh admission.

This module is deliberately pure: no AppKit, no filesystem, no network, and no
clock reads. The legacy host supplies immutable observations and receives a
bounded set of changed domains.
"""

from __future__ import annotations

import dataclasses
import hashlib
import heapq
import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from pathlib import Path

MAX_NORMALIZATION_DEPTH = 14
MAX_COLLECTION_ITEMS = 4096
MAX_TEXT_BYTES = 4096


class CoreDomain(str, Enum):
    AGENTS = "agents"
    OPERATOR = "operator"
    ATTENTION = "attention"
    BATTERY = "battery"
    SETTINGS = "settings"
    REMOTE = "remote"
    PRESENTATION = "presentation"
    DEVICES = "devices"
    USAGE = "usage"
    MENU = "menu"


@dataclasses.dataclass(frozen=True, slots=True)
class DomainFingerprint:
    domain: CoreDomain
    digest: str

    def __post_init__(self) -> None:
        if not (
            type(self.domain) is CoreDomain
            and type(self.digest) is str
            and len(self.digest) == 64
            and all(character in "0123456789abcdef" for character in self.digest)
        ):
            raise ValueError("invalid domain fingerprint")


@dataclasses.dataclass(frozen=True, slots=True)
class CoreSnapshot:
    schema_version: int
    generation: int
    fingerprints: tuple[DomainFingerprint, ...]

    def __post_init__(self) -> None:
        if not (
            self.schema_version == 1
            and type(self.generation) is int
            and self.generation >= 0
            and type(self.fingerprints) is tuple
            and all(type(item) is DomainFingerprint for item in self.fingerprints)
            and len({item.domain for item in self.fingerprints}) == len(self.fingerprints)
        ):
            raise ValueError("invalid core snapshot")

    def fingerprint(self, domain: CoreDomain) -> str | None:
        return next(
            (item.digest for item in self.fingerprints if item.domain is domain),
            None,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class StateDelta:
    schema_version: int
    from_generation: int
    to_generation: int
    changed_domains: frozenset[CoreDomain]
    urgent: bool

    def __post_init__(self) -> None:
        if not (
            self.schema_version == 1
            and type(self.from_generation) is int
            and type(self.to_generation) is int
            and 0 <= self.from_generation <= self.to_generation
            and type(self.changed_domains) is frozenset
            and all(type(domain) is CoreDomain for domain in self.changed_domains)
            and type(self.urgent) is bool
        ):
            raise ValueError("invalid state delta")

    @property
    def changed(self) -> bool:
        return bool(self.changed_domains)

    def affects(self, *domains: CoreDomain) -> bool:
        return bool(self.changed_domains.intersection(domains))


def _bounded_text(value: str) -> object:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_TEXT_BYTES:
        return value
    return {
        "$text_sha256": hashlib.sha256(encoded).hexdigest(),
        "$text_bytes": len(encoded),
    }


def _json_sort_key(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _bounded_smallest(values) -> list[object]:
    """Select a deterministic prefix without materializing an unbounded sort."""
    return heapq.nsmallest(MAX_COLLECTION_ITEMS, values, key=_json_sort_key)


def _normalize(value: object, *, depth: int = 0) -> object:
    if depth > MAX_NORMALIZATION_DEPTH:
        return {"$depth": type(value).__name__}
    if value is None or type(value) in {bool, int, str}:
        return _bounded_text(value) if type(value) is str else value
    if type(value) is float:
        return value if math.isfinite(value) else {"$float": repr(value)}
    if isinstance(value, Enum):
        return {
            "$enum": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": _normalize(value.value, depth=depth + 1),
        }
    if isinstance(value, (datetime, date)):
        return {"$time": value.isoformat()}
    if isinstance(value, Path):
        return {"$path": _bounded_text(str(value))}
    if isinstance(value, bytes):
        return {
            "$bytes_sha256": hashlib.sha256(value).hexdigest(),
            "$bytes": len(value),
        }
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {
            field.name: _normalize(getattr(value, field.name), depth=depth + 1)
            for field in dataclasses.fields(value)
            if field.metadata.get("core_state", True)
        }
        return {
            "$type": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": fields,
        }
    if isinstance(value, Mapping):
        normalized_pairs = (
            [
                _normalize(key, depth=depth + 1),
                _normalize(item, depth=depth + 1),
            ]
            for key, item in value.items()
        )
        return {
            "$mapping": _bounded_smallest(normalized_pairs),
            "$length": len(value),
        }
    if isinstance(value, (tuple, list)):
        return {
            "$sequence": [
                _normalize(item, depth=depth + 1)
                for item in value[:MAX_COLLECTION_ITEMS]
            ],
            "$length": len(value),
        }
    if isinstance(value, (set, frozenset)):
        normalized = (
            _normalize(item, depth=depth + 1)
            for item in value
        )
        return {
            "$set": _bounded_smallest(normalized),
            "$length": len(value),
        }
    type_name = f"{type(value).__module__}.{type(value).__qualname__}"
    return {"$opaque_type": type_name}


def stable_digest(value: object) -> str:
    payload = json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CoreStateStore:
    def __init__(self) -> None:
        self._snapshot = CoreSnapshot(1, 0, ())

    @property
    def snapshot(self) -> CoreSnapshot:
        return self._snapshot

    def observe(
        self,
        values: Mapping[CoreDomain, object],
        *,
        urgent_domains: frozenset[CoreDomain] = frozenset(),
    ) -> StateDelta:
        if not (
            isinstance(values, Mapping)
            and all(type(domain) is CoreDomain for domain in values)
            and type(urgent_domains) is frozenset
            and all(type(domain) is CoreDomain for domain in urgent_domains)
        ):
            raise ValueError("invalid core observation")
        previous = self._snapshot
        fingerprints = tuple(
            DomainFingerprint(domain, stable_digest(values[domain]))
            for domain in sorted(values, key=lambda item: item.value)
        )
        changed = frozenset(
            item.domain
            for item in fingerprints
            if previous.fingerprint(item.domain) != item.digest
        )
        removed = frozenset(
            item.domain for item in previous.fingerprints if item.domain not in values
        )
        changed |= removed
        generation = previous.generation + 1 if changed else previous.generation
        self._snapshot = CoreSnapshot(1, generation, fingerprints)
        return StateDelta(
            1,
            previous.generation,
            generation,
            changed,
            bool(changed & urgent_domains),
        )
