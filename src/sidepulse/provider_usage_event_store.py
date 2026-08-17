"""Owner-private deduplication store for finite provider reset cues."""

from __future__ import annotations

import json
import re
from pathlib import Path

RESET_EVENT_STORE_SCHEMA_VERSION = 1
MAX_SEEN_RESET_EVENTS = 512
_EVENT_ID = re.compile(r"[a-z][a-z0-9-]{0,31}:[a-z0-9][a-z0-9._:-]{0,127}:[0-9a-f]{24}\Z")


def default_reset_event_store_path(home: Path | None = None) -> Path:
    base = Path.home() if home is None else Path(home)
    return base / ".local" / "state" / "sidepulse" / "provider-reset-events.json"


def load_seen_reset_events(path: Path | None = None) -> tuple[str, ...]:
    target = default_reset_event_store_path() if path is None else Path(path)
    try:
        from .private_io import read_private_text

        document = json.loads(read_private_text(target, max_bytes=128 * 1024))
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return ()
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != RESET_EVENT_STORE_SCHEMA_VERSION
        or not isinstance(document.get("seen"), list)
    ):
        return ()
    rows = []
    for value in document["seen"][-MAX_SEEN_RESET_EVENTS:]:
        if isinstance(value, str) and _EVENT_ID.fullmatch(value) and value not in rows:
            rows.append(value)
    return tuple(rows)


def save_seen_reset_events(
    event_ids: tuple[str, ...] | list[str],
    path: Path | None = None,
) -> Path:
    rows = []
    for value in tuple(event_ids)[-MAX_SEEN_RESET_EVENTS:]:
        if isinstance(value, str) and _EVENT_ID.fullmatch(value) and value not in rows:
            rows.append(value)
    document = {
        "schema_version": RESET_EVENT_STORE_SCHEMA_VERSION,
        "seen": rows[-MAX_SEEN_RESET_EVENTS:],
    }
    target = default_reset_event_store_path() if path is None else Path(path)
    from .private_io import atomic_private_write

    atomic_private_write(
        target,
        json.dumps(document, separators=(",", ":"), sort_keys=True),
    )
    return target


__all__ = [
    "MAX_SEEN_RESET_EVENTS",
    "RESET_EVENT_STORE_SCHEMA_VERSION",
    "default_reset_event_store_path",
    "load_seen_reset_events",
    "save_seen_reset_events",
]
