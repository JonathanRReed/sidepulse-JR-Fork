"""Strict private persistence for exact local acknowledgement state."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Final

from .local_triage import (
    MAX_LOCAL_ACKNOWLEDGEMENTS,
    LocalAcknowledgement,
    LocalTriageState,
)
from .private_io import atomic_private_write, read_private_text
from .provider_facts import (
    RequestKey,
    request_key_from_payload,
    request_key_to_payload,
)

_STORE_VERSION: Final = 1
_MAX_STORE_BYTES: Final = 1_048_576
_DOCUMENT_KEYS: Final = frozenset({"acknowledgements", "version"})
_ACKNOWLEDGEMENT_KEYS: Final = frozenset({"acknowledged_at", "request_key"})


class _InvalidTriageStore(ValueError):
    pass


def load_operator_triage(path: Path) -> LocalTriageState:
    """Load one strict bounded store, returning empty state on unsafe input."""
    try:
        raw = read_private_text(Path(path), max_bytes=_MAX_STORE_BYTES)
        document = _decode_document(raw)
        return _state_from_document(document)
    except (OSError, RecursionError, TypeError, UnicodeError, ValueError):
        return LocalTriageState(())


def save_operator_triage(path: Path, state: LocalTriageState) -> None:
    """Atomically save one content-free exact acknowledgement document."""
    acknowledgements = getattr(state, "acknowledgements", None)
    if not (
        isinstance(state, LocalTriageState)
        and type(acknowledgements) is tuple
        and len(acknowledgements) <= MAX_LOCAL_ACKNOWLEDGEMENTS
    ):
        raise ValueError("invalid operator triage state")

    payloads: list[dict[str, object]] = []
    seen: set[RequestKey] = set()
    for item in acknowledgements:
        if not isinstance(item, LocalAcknowledgement):
            raise ValueError("invalid operator triage state")
        if item.request_key in seen or not _valid_epoch(item.acknowledged_at):
            raise ValueError("invalid operator triage state")
        seen.add(item.request_key)
        payloads.append(
            {
                "request_key": request_key_to_payload(item.request_key),
                "acknowledged_at": float(item.acknowledged_at),
            }
        )

    serialized = json.dumps(
        {"version": _STORE_VERSION, "acknowledgements": payloads},
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    encoded = f"{serialized}\n"
    if len(encoded.encode("utf-8")) > _MAX_STORE_BYTES:
        raise ValueError("operator triage store exceeds maximum size")
    atomic_private_write(Path(path), encoded)


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _InvalidTriageStore
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _InvalidTriageStore


def _decode_document(raw: str) -> object:
    return json.loads(
        raw,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


def _state_from_document(document: object) -> LocalTriageState:
    if type(document) is not dict or frozenset(document) != _DOCUMENT_KEYS:
        raise _InvalidTriageStore
    version = document["version"]
    entries = document["acknowledgements"]
    if not (
        type(version) is int
        and version == _STORE_VERSION
        and type(entries) is list
        and len(entries) <= MAX_LOCAL_ACKNOWLEDGEMENTS
    ):
        raise _InvalidTriageStore

    acknowledgements: list[LocalAcknowledgement] = []
    seen: set[RequestKey] = set()
    for entry in entries:
        if type(entry) is not dict or frozenset(entry) != _ACKNOWLEDGEMENT_KEYS:
            raise _InvalidTriageStore
        request_key = request_key_from_payload(entry["request_key"])
        acknowledged_at = entry["acknowledged_at"]
        if (
            request_key is None
            or request_key in seen
            or not _valid_epoch(acknowledged_at)
        ):
            raise _InvalidTriageStore
        seen.add(request_key)
        acknowledgements.append(
            LocalAcknowledgement(request_key, float(acknowledged_at))
        )
    return LocalTriageState(tuple(acknowledgements))


def _valid_epoch(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and float(value) >= 0.0
    )
