"""Strict private persistence for Clear Agents presentation receipts.

The document is deliberately content-free. It stores only source-bound
completion identities, acknowledgement epochs, the state generation, and the
latest bounded Undo receipt. Canonical agent state and display content never
cross this boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from .capacity_types import SourceKey
from .clear_agents import (
    MAX_CLEAR_TARGETS,
    MAX_COMPLETION_RECEIPTS,
    ClearAgentsBatchReceipt,
    ClearAgentsState,
    CompletionPresentationKey,
    CompletionPresentationReceipt,
)
from .private_io import atomic_private_write, read_private_text
from .state_paths import default_state_dir

CLEAR_AGENTS_STORE_NAME: Final = "clear-agents.json"
CLEAR_AGENTS_STORE_VERSION: Final = 1
MAX_CLEAR_AGENTS_STORE_BYTES: Final = 1_048_576

_DOCUMENT_FIELDS: Final = frozenset(
    {"generation", "latest_batch", "receipts", "version"}
)
_RECEIPT_FIELDS: Final = frozenset({"acknowledged_at_epoch", "key"})
_KEY_FIELDS: Final = frozenset(
    {"agent_id", "completed_at_epoch", "event_name", "source_key"}
)
_SOURCE_FIELDS: Final = frozenset(
    {"adapter_id", "capability_id", "provider_id", "source_instance_id"}
)
_BATCH_FIELDS: Final = frozenset(
    {
        "batch_id",
        "commit_generation",
        "committed_at_epoch",
        "newly_added_keys",
        "undo_deadline_epoch",
        "undone",
    }
)


class ClearAgentsRestoreHealth(str, Enum):
    HEALTHY = "healthy"
    MISSING = "missing"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ClearAgentsRestore:
    state: ClearAgentsState
    health: ClearAgentsRestoreHealth

    def __post_init__(self) -> None:
        if not (
            type(self.state) is ClearAgentsState
            and type(self.health) is ClearAgentsRestoreHealth
        ):
            raise ValueError("invalid Clear Agents restore")


class _CorruptClearAgentsStore(ValueError):
    pass


class _UnsupportedClearAgentsStore(ValueError):
    pass


def default_clear_agents_path(home: Path | None = None) -> Path:
    """Return the owner-private Clear Agents receipt path."""

    return default_state_dir(home) / CLEAR_AGENTS_STORE_NAME


def load_clear_agents_state(path: Path | None = None) -> ClearAgentsRestore:
    """Restore strict receipt state with typed, content-free failure health."""

    target = default_clear_agents_path() if path is None else Path(path)
    try:
        raw = read_private_text(target, max_bytes=MAX_CLEAR_AGENTS_STORE_BYTES)
        document = _decode_document(raw)
        state = _state_from_document(document)
    except FileNotFoundError:
        return _degraded_restore(ClearAgentsRestoreHealth.MISSING)
    except _UnsupportedClearAgentsStore:
        return _degraded_restore(ClearAgentsRestoreHealth.UNSUPPORTED)
    except OSError:
        return _degraded_restore(ClearAgentsRestoreHealth.UNAVAILABLE)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        return _degraded_restore(ClearAgentsRestoreHealth.CORRUPT)
    return ClearAgentsRestore(state, ClearAgentsRestoreHealth.HEALTHY)


def save_clear_agents_state(path: Path, state: ClearAgentsState) -> Path:
    """Atomically save one exact bounded receipt document."""

    if type(state) is not ClearAgentsState:
        raise ValueError("invalid Clear Agents state")
    try:
        canonical = ClearAgentsState(
            generation=state.generation,
            receipts=state.receipts,
            latest_batch=state.latest_batch,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("invalid Clear Agents state") from error
    encoded = _encode_state(canonical)
    if len(encoded.encode("utf-8")) > MAX_CLEAR_AGENTS_STORE_BYTES:
        raise ValueError("Clear Agents store exceeds maximum size")
    return atomic_private_write(Path(path), encoded)


def _degraded_restore(health: ClearAgentsRestoreHealth) -> ClearAgentsRestore:
    return ClearAgentsRestore(ClearAgentsState(), health)


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _CorruptClearAgentsStore
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _CorruptClearAgentsStore


def _decode_document(raw: str) -> object:
    return json.loads(
        raw,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


def _has_exact_fields(value: object, fields: frozenset[str]) -> bool:
    return type(value) is dict and frozenset(value) == fields


def _source_payload(source: SourceKey) -> dict[str, str]:
    if type(source) is not SourceKey:
        raise ValueError("invalid completion source")
    return {
        "provider_id": source.provider_id,
        "adapter_id": source.adapter_id,
        "source_instance_id": source.source_instance_id,
        "capability_id": source.capability_id,
    }


def _key_payload(key: CompletionPresentationKey) -> dict[str, object]:
    if type(key) is not CompletionPresentationKey:
        raise ValueError("invalid completion presentation key")
    return {
        "source_key": _source_payload(key.source_key),
        "agent_id": key.agent_id,
        "event_name": key.event_name,
        "completed_at_epoch": key.completed_at_epoch,
    }


def _receipt_payload(
    receipt: CompletionPresentationReceipt,
) -> dict[str, object]:
    if type(receipt) is not CompletionPresentationReceipt:
        raise ValueError("invalid completion presentation receipt")
    return {
        "key": _key_payload(receipt.key),
        "acknowledged_at_epoch": receipt.acknowledged_at_epoch,
    }


def _batch_payload(batch: ClearAgentsBatchReceipt) -> dict[str, object]:
    if type(batch) is not ClearAgentsBatchReceipt:
        raise ValueError("invalid Clear Agents batch receipt")
    return {
        "batch_id": batch.batch_id,
        "newly_added_keys": [_key_payload(key) for key in batch.newly_added_keys],
        "committed_at_epoch": batch.committed_at_epoch,
        "undo_deadline_epoch": batch.undo_deadline_epoch,
        "commit_generation": batch.commit_generation,
        "undone": batch.undone,
    }


def _encode_state(state: ClearAgentsState) -> str:
    document = {
        "version": CLEAR_AGENTS_STORE_VERSION,
        "generation": state.generation,
        "receipts": [_receipt_payload(receipt) for receipt in state.receipts],
        "latest_batch": (
            None if state.latest_batch is None else _batch_payload(state.latest_batch)
        ),
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
        raise ValueError("invalid Clear Agents state") from error
    return f"{serialized}\n"


def _source_from_payload(payload: object) -> SourceKey:
    if not _has_exact_fields(payload, _SOURCE_FIELDS):
        raise _CorruptClearAgentsStore
    values = (
        payload["provider_id"],
        payload["adapter_id"],
        payload["source_instance_id"],
        payload["capability_id"],
    )
    if not all(type(value) is str for value in values):
        raise _CorruptClearAgentsStore
    try:
        return SourceKey(*values)
    except ValueError as error:
        raise _CorruptClearAgentsStore from error


def _key_from_payload(payload: object) -> CompletionPresentationKey:
    if not _has_exact_fields(payload, _KEY_FIELDS):
        raise _CorruptClearAgentsStore
    try:
        return CompletionPresentationKey(
            source_key=_source_from_payload(payload["source_key"]),
            agent_id=payload["agent_id"],
            event_name=payload["event_name"],
            completed_at_epoch=payload["completed_at_epoch"],
        )
    except (TypeError, ValueError) as error:
        raise _CorruptClearAgentsStore from error


def _receipt_from_payload(payload: object) -> CompletionPresentationReceipt:
    if not _has_exact_fields(payload, _RECEIPT_FIELDS):
        raise _CorruptClearAgentsStore
    try:
        return CompletionPresentationReceipt(
            key=_key_from_payload(payload["key"]),
            acknowledged_at_epoch=payload["acknowledged_at_epoch"],
        )
    except (TypeError, ValueError) as error:
        raise _CorruptClearAgentsStore from error


def _batch_from_payload(payload: object) -> ClearAgentsBatchReceipt:
    if not _has_exact_fields(payload, _BATCH_FIELDS):
        raise _CorruptClearAgentsStore
    keys = payload["newly_added_keys"]
    if type(keys) is not list or not 1 <= len(keys) <= MAX_CLEAR_TARGETS:
        raise _CorruptClearAgentsStore
    try:
        return ClearAgentsBatchReceipt(
            batch_id=payload["batch_id"],
            newly_added_keys=tuple(_key_from_payload(key) for key in keys),
            committed_at_epoch=payload["committed_at_epoch"],
            undo_deadline_epoch=payload["undo_deadline_epoch"],
            commit_generation=payload["commit_generation"],
            undone=payload["undone"],
        )
    except (TypeError, ValueError) as error:
        raise _CorruptClearAgentsStore from error


def _state_from_document(document: object) -> ClearAgentsState:
    if not _has_exact_fields(document, _DOCUMENT_FIELDS):
        raise _CorruptClearAgentsStore
    version = document["version"]
    if type(version) is not int:
        raise _CorruptClearAgentsStore
    if version != CLEAR_AGENTS_STORE_VERSION:
        raise _UnsupportedClearAgentsStore
    receipts = document["receipts"]
    if type(receipts) is not list or len(receipts) > MAX_COMPLETION_RECEIPTS:
        raise _CorruptClearAgentsStore
    latest_batch = document["latest_batch"]
    if latest_batch is not None and type(latest_batch) is not dict:
        raise _CorruptClearAgentsStore
    try:
        return ClearAgentsState(
            generation=document["generation"],
            receipts=tuple(_receipt_from_payload(receipt) for receipt in receipts),
            latest_batch=(
                None if latest_batch is None else _batch_from_payload(latest_batch)
            ),
        )
    except (TypeError, ValueError) as error:
        raise _CorruptClearAgentsStore from error


__all__ = [
    "CLEAR_AGENTS_STORE_NAME",
    "CLEAR_AGENTS_STORE_VERSION",
    "MAX_CLEAR_AGENTS_STORE_BYTES",
    "ClearAgentsRestore",
    "ClearAgentsRestoreHealth",
    "default_clear_agents_path",
    "load_clear_agents_state",
    "save_clear_agents_state",
]
