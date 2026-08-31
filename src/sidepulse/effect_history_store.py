"""Strict private persistence for bounded, content-free effect history."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from .effect_history import (
    MAX_EFFECT_EVENTS,
    MAX_EFFECT_HISTORY_BYTES,
    EffectAcknowledgementSource,
    EffectEvent,
    EffectHistory,
    EffectHistoryValidationError,
    EffectOutcome,
    EffectSemanticCategory,
    EffectSuppressionReason,
    EffectSurface,
    effect_event_to_payload,
)
from .private_io import atomic_private_write, read_private_text
from .state_paths import default_state_dir

EFFECT_HISTORY_STORE_NAME: Final = "effect-history.json"
EFFECT_HISTORY_STORE_VERSION: Final = 1
MAX_EFFECT_HISTORY_STORE_BYTES: Final = MAX_EFFECT_HISTORY_BYTES

_DOCUMENT_FIELDS: Final = frozenset({"events", "last_seen_epoch", "version"})
_EVENT_FIELDS: Final = frozenset(
    {
        "acknowledgement_source",
        "effect_id",
        "event_id",
        "occurred_at_epoch",
        "outcome",
        "semantic_category",
        "suppression_reason",
        "surface",
        "version",
    }
)


class EffectHistoryRestoreHealth(str, Enum):
    HEALTHY = "healthy"
    MISSING = "missing"
    OVERSIZED = "oversized"
    UNSUPPORTED = "unsupported"
    CORRUPT = "corrupt"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EffectHistoryRestore:
    history: EffectHistory
    health: EffectHistoryRestoreHealth

    def __post_init__(self) -> None:
        if not (
            type(self.history) is EffectHistory
            and type(self.health) is EffectHistoryRestoreHealth
        ):
            raise EffectHistoryValidationError("invalid effect history restore")


class _CorruptEffectHistoryStore(ValueError):
    pass


class _UnsupportedEffectHistoryStore(ValueError):
    pass


def default_effect_history_path(home: Path | None = None) -> Path:
    """Return the owner-private effect-history path."""

    return default_state_dir(home) / EFFECT_HISTORY_STORE_NAME


def load_effect_history(path: Path | None = None) -> EffectHistoryRestore:
    """Load exact history, degrading to typed empty state on unsafe input."""

    target = default_effect_history_path() if path is None else Path(path)
    try:
        raw = read_private_text(
            target,
            max_bytes=MAX_EFFECT_HISTORY_STORE_BYTES,
        )
        document = _decode_document(raw)
        history = _history_from_document(document)
    except FileNotFoundError:
        return _degraded_restore(EffectHistoryRestoreHealth.MISSING)
    except _UnsupportedEffectHistoryStore:
        return _degraded_restore(EffectHistoryRestoreHealth.UNSUPPORTED)
    except OSError as error:
        health = (
            EffectHistoryRestoreHealth.OVERSIZED
            if "exceeds maximum size" in str(error)
            else EffectHistoryRestoreHealth.UNAVAILABLE
        )
        return _degraded_restore(health)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        return _degraded_restore(EffectHistoryRestoreHealth.CORRUPT)
    return EffectHistoryRestore(history, EffectHistoryRestoreHealth.HEALTHY)


def save_effect_history(path: Path, history: EffectHistory) -> Path:
    """Atomically write one exact, bounded effect-history document."""

    if type(history) is not EffectHistory:
        raise EffectHistoryValidationError("invalid effect history")
    try:
        canonical = EffectHistory(history.events, history.last_seen_epoch)
    except (AttributeError, TypeError, ValueError) as error:
        raise EffectHistoryValidationError("invalid effect history") from error
    encoded = _encode_history(canonical)
    if len(encoded.encode("utf-8")) > MAX_EFFECT_HISTORY_STORE_BYTES:
        raise EffectHistoryValidationError(
            "effect history store exceeds maximum size"
        )
    return atomic_private_write(Path(path), encoded)


def _degraded_restore(health: EffectHistoryRestoreHealth) -> EffectHistoryRestore:
    return EffectHistoryRestore(EffectHistory(), health)


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _CorruptEffectHistoryStore
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _CorruptEffectHistoryStore


def _decode_document(raw: str) -> object:
    return json.loads(
        raw,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


def _has_exact_fields(value: object, fields: frozenset[str]) -> bool:
    return type(value) is dict and frozenset(value) == fields


def _optional_enum(
    enum_type: type[EffectSuppressionReason] | type[EffectAcknowledgementSource],
    value: object,
) -> EffectSuppressionReason | EffectAcknowledgementSource | None:
    if value is None:
        return None
    if type(value) is not str:
        raise _CorruptEffectHistoryStore
    try:
        return enum_type(value)
    except ValueError as error:
        raise _CorruptEffectHistoryStore from error


def _event_from_payload(payload: object) -> EffectEvent:
    if not _has_exact_fields(payload, _EVENT_FIELDS):
        raise _CorruptEffectHistoryStore
    required_strings = (
        payload["event_id"],
        payload["effect_id"],
        payload["semantic_category"],
        payload["surface"],
        payload["outcome"],
    )
    if not all(type(value) is str for value in required_strings):
        raise _CorruptEffectHistoryStore
    try:
        suppression_reason = _optional_enum(
            EffectSuppressionReason,
            payload["suppression_reason"],
        )
        acknowledgement_source = _optional_enum(
            EffectAcknowledgementSource,
            payload["acknowledgement_source"],
        )
        return EffectEvent(
            event_id=payload["event_id"],
            occurred_at_epoch=payload["occurred_at_epoch"],
            effect_id=payload["effect_id"],
            semantic_category=EffectSemanticCategory(
                payload["semantic_category"]
            ),
            surface=EffectSurface(payload["surface"]),
            outcome=EffectOutcome(payload["outcome"]),
            suppression_reason=(
                suppression_reason
                if type(suppression_reason) is EffectSuppressionReason
                else None
            ),
            acknowledgement_source=(
                acknowledgement_source
                if type(acknowledgement_source) is EffectAcknowledgementSource
                else None
            ),
            version=payload["version"],
        )
    except (EffectHistoryValidationError, TypeError, ValueError) as error:
        raise _CorruptEffectHistoryStore from error


def _history_from_document(document: object) -> EffectHistory:
    if not _has_exact_fields(document, _DOCUMENT_FIELDS):
        raise _CorruptEffectHistoryStore
    version = document["version"]
    if type(version) is not int:
        raise _CorruptEffectHistoryStore
    if version != EFFECT_HISTORY_STORE_VERSION:
        raise _UnsupportedEffectHistoryStore
    events = document["events"]
    if type(events) is not list or len(events) > MAX_EFFECT_EVENTS:
        raise _CorruptEffectHistoryStore
    try:
        return EffectHistory(
            tuple(_event_from_payload(event) for event in events),
            document["last_seen_epoch"],
        )
    except (EffectHistoryValidationError, TypeError, ValueError) as error:
        raise _CorruptEffectHistoryStore from error


def _encode_history(history: EffectHistory) -> str:
    document = {
        "version": EFFECT_HISTORY_STORE_VERSION,
        "last_seen_epoch": history.last_seen_epoch,
        "events": [effect_event_to_payload(event) for event in history.events],
    }
    try:
        serialized = json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise EffectHistoryValidationError("invalid effect history") from error
    return f"{serialized}\n"


__all__ = [
    "EFFECT_HISTORY_STORE_NAME",
    "EFFECT_HISTORY_STORE_VERSION",
    "MAX_EFFECT_HISTORY_STORE_BYTES",
    "EffectHistoryRestore",
    "EffectHistoryRestoreHealth",
    "default_effect_history_path",
    "load_effect_history",
    "save_effect_history",
]
