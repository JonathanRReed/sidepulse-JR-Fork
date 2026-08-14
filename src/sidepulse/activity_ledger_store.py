"""Strict private persistence for the bounded recent-activity ledger.

Deliberately NOT the delivery ledger, and the reasoning is worth keeping:

``delivery_ledger`` answers "have we already delivered this cue?". It is
write-once dedup state whose whole value is that it is never trimmed by
time -- evicting an old receipt makes a stale notification fire again. It is
also content-free on purpose (its store's docstring says "metadata-only"),
keyed by ``SemanticEventKey``, ordered by delivery key rather than by time,
and guarded by a state machine that raises on any transition that is not a
legal delivery progression.

This ledger answers "what did I miss?". It is a time-ordered display feed
that MUST be trimmed by time, that must carry the session's display name to
be readable at all, and that has no transitions -- an entry is an immutable
historical fact. Putting both in one document means one of them trims the
other: dropping the oldest row to make room for a new completion would
silently re-arm a notification that had already fired.

So: a separate document, the same disciplines -- exact-field strict decode,
typed restore health, ``atomic_private_write``, and a hard byte cap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .activity_ledger import (
    MAX_ACTIVITY_ENTRIES,
    MAX_ACTIVITY_LEDGER_BYTES,
    ActivityEntry,
    ActivityKind,
    ActivityLedger,
    ActivityRestoreHealth,
    ActivityValidationError,
    activity_entry_to_payload,
    bounded_activity_entries,
)
from .private_io import atomic_private_write, read_private_text
from .providers import default_state_dir

ACTIVITY_LEDGER_NAME: Final = "activity-ledger.json"
_STORE_VERSION: Final = 1
_MAX_STORE_BYTES: Final = MAX_ACTIVITY_LEDGER_BYTES
_DOCUMENT_FIELDS: Final = frozenset({"entries", "last_seen_epoch", "version"})
_ENTRY_FIELDS: Final = frozenset(
    {"detail", "kind", "label", "occurred_at_epoch", "provider", "subject_id"}
)


class _CorruptActivityStore(ValueError):
    pass


class _UnsupportedActivityStore(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ActivityLedgerRestore:
    ledger: ActivityLedger
    health: ActivityRestoreHealth

    def __post_init__(self) -> None:
        if not (
            type(self.ledger) is ActivityLedger
            and type(self.health) is ActivityRestoreHealth
        ):
            raise ActivityValidationError("invalid activity ledger restore")


def default_activity_ledger_path(home: Path | None = None) -> Path:
    return default_state_dir(home) / ACTIVITY_LEDGER_NAME


def load_activity_ledger(path: Path) -> ActivityLedgerRestore:
    """Load the ledger, returning typed degraded health instead of raising.

    A ledger that cannot be read must never take the status bar with it: the
    worst honest outcome is an empty "Since you left" section, and that is
    what every failure path here produces.
    """
    try:
        raw = read_private_text(Path(path), max_bytes=_MAX_STORE_BYTES)
        document = _decode_document(raw)
        ledger = _ledger_from_document(document)
    except FileNotFoundError:
        return _degraded_restore(ActivityRestoreHealth.MISSING)
    except _UnsupportedActivityStore:
        return _degraded_restore(ActivityRestoreHealth.UNSUPPORTED)
    except OSError:
        return _degraded_restore(ActivityRestoreHealth.UNAVAILABLE)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        return _degraded_restore(ActivityRestoreHealth.CORRUPT)
    return ActivityLedgerRestore(ledger, ActivityRestoreHealth.HEALTHY)


def save_activity_ledger(path: Path, ledger: ActivityLedger) -> None:
    """Atomically save one bounded activity document.

    The entry bound is re-applied here rather than trusted: this is the last
    place before bytes hit the disk, and a caller that hand-built a ledger
    must not be able to write a file the loader would then reject as corrupt.
    """
    if type(ledger) is not ActivityLedger:
        raise ActivityValidationError("invalid activity ledger")
    bounded = ActivityLedger(
        bounded_activity_entries(ledger.entries),
        ledger.last_seen_epoch,
    )
    encoded = _encode_document(bounded)
    if len(encoded.encode("utf-8")) > _MAX_STORE_BYTES:
        # Unreachable while bounded_activity_entries holds, so it is an
        # assertion about that invariant rather than a runtime path: refuse
        # rather than write a file the loader will call corrupt.
        raise ActivityValidationError("activity ledger store exceeds maximum size")
    atomic_private_write(Path(path), encoded)


def _encode_document(ledger: ActivityLedger) -> str:
    document = {
        "version": _STORE_VERSION,
        "last_seen_epoch": float(ledger.last_seen_epoch),
        "entries": [activity_entry_to_payload(entry) for entry in ledger.entries],
    }
    return (
        json.dumps(
            document,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _degraded_restore(health: ActivityRestoreHealth) -> ActivityLedgerRestore:
    return ActivityLedgerRestore(ActivityLedger(), health)


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _CorruptActivityStore
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _CorruptActivityStore


def _decode_document(raw: str) -> object:
    return json.loads(
        raw,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


def _has_exact_fields(value: object, fields: frozenset[str]) -> bool:
    return type(value) is dict and frozenset(value) == fields


def _ledger_from_document(document: object) -> ActivityLedger:
    if not _has_exact_fields(document, _DOCUMENT_FIELDS):
        raise _CorruptActivityStore
    version = document["version"]
    if type(version) is not int:
        raise _CorruptActivityStore
    if version != _STORE_VERSION:
        raise _UnsupportedActivityStore
    entries = document["entries"]
    if type(entries) is not list or len(entries) > MAX_ACTIVITY_ENTRIES:
        raise _CorruptActivityStore
    last_seen = document["last_seen_epoch"]
    if type(last_seen) not in {int, float} or isinstance(last_seen, bool):
        raise _CorruptActivityStore
    try:
        return ActivityLedger(
            tuple(_entry_from_payload(item) for item in entries),
            float(last_seen),
        )
    except (TypeError, ValueError) as error:
        raise _CorruptActivityStore from error


def _entry_from_payload(payload: object) -> ActivityEntry:
    if not _has_exact_fields(payload, _ENTRY_FIELDS):
        raise _CorruptActivityStore
    kind = payload["kind"]
    subject_id = payload["subject_id"]
    detail = payload["detail"]
    if not (
        type(kind) is str
        and (subject_id is None or type(subject_id) is str)
        and (detail is None or type(detail) is str)
    ):
        raise _CorruptActivityStore
    return ActivityEntry(
        ActivityKind(kind),
        payload["occurred_at_epoch"],
        payload["label"],
        payload["provider"],
        subject_id,
        detail,
    )


__all__ = [
    "ACTIVITY_LEDGER_NAME",
    "ActivityLedgerRestore",
    "default_activity_ledger_path",
    "load_activity_ledger",
    "save_activity_ledger",
]
