from __future__ import annotations

import json
from pathlib import Path

from sidepulse.provider_usage_event_store import (
    MAX_SEEN_RESET_EVENTS,
    load_seen_reset_events,
    save_seen_reset_events,
)


def test_seen_reset_events_round_trip_and_bound(tmp_path: Path):
    target = tmp_path / "events.json"
    events = tuple(f"codex:weekly:{index:024x}" for index in range(MAX_SEEN_RESET_EVENTS + 20))
    save_seen_reset_events(events, target)
    loaded = load_seen_reset_events(target)
    assert len(loaded) == MAX_SEEN_RESET_EVENTS
    assert loaded[-1] == events[-1]


def test_invalid_document_fails_closed(tmp_path: Path):
    target = tmp_path / "events.json"
    target.write_text(json.dumps({"schema_version": 99, "seen": ["bad"]}))
    assert load_seen_reset_events(target) == ()
