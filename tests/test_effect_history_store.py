from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.effect_history import (
    EffectAcknowledgementSource,
    EffectEvent,
    EffectHistory,
    EffectOutcome,
    EffectSemanticCategory,
    EffectSuppressionReason,
    EffectSurface,
)
from sidepulse.effect_history_store import (
    EFFECT_HISTORY_STORE_NAME,
    EFFECT_HISTORY_STORE_VERSION,
    MAX_EFFECT_HISTORY_STORE_BYTES,
    EffectHistoryRestore,
    EffectHistoryRestoreHealth,
    default_effect_history_path,
    load_effect_history,
    save_effect_history,
)

NOW = 1_800_000_000.0


def _event(
    suffix: str = "one",
    *,
    outcome: EffectOutcome = EffectOutcome.SHOWN,
    surface: EffectSurface = EffectSurface.GLANCE_LIGHT,
) -> EffectEvent:
    return EffectEvent(
        event_id=f"effect-event:{suffix}",
        occurred_at_epoch=NOW,
        effect_id="ask-heartbeat",
        semantic_category=EffectSemanticCategory.ATTENTION,
        surface=surface,
        outcome=outcome,
        suppression_reason=(
            EffectSuppressionReason.DO_NOT_DISTURB
            if outcome is EffectOutcome.SUPPRESSED
            else None
        ),
        acknowledgement_source=(
            EffectAcknowledgementSource.SCREEN_BAR
            if outcome is EffectOutcome.ACKNOWLEDGED
            else None
        ),
    )


def _event_payload(event: EffectEvent) -> dict[str, object]:
    return {
        "acknowledgement_source": (
            None
            if event.acknowledgement_source is None
            else event.acknowledgement_source.value
        ),
        "effect_id": event.effect_id,
        "event_id": event.event_id,
        "occurred_at_epoch": event.occurred_at_epoch,
        "outcome": event.outcome.value,
        "semantic_category": event.semantic_category.value,
        "suppression_reason": (
            None if event.suppression_reason is None else event.suppression_reason.value
        ),
        "surface": event.surface.value,
        "version": event.version,
    }


def _document(
    *,
    version: object = EFFECT_HISTORY_STORE_VERSION,
    last_seen_epoch: object = 0.0,
    events: object = (),
) -> dict[str, object]:
    return {
        "events": list(events) if isinstance(events, (list, tuple)) else events,
        "last_seen_epoch": last_seen_epoch,
        "version": version,
    }


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_default_path_uses_existing_private_state_directory(tmp_path: Path) -> None:
    assert default_effect_history_path(tmp_path) == (
        tmp_path
        / ".local"
        / "state"
        / "sidepulse"
        / "agent-monitor"
        / EFFECT_HISTORY_STORE_NAME
    )


def test_round_trip_is_deterministic_private_and_exactly_content_free(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / EFFECT_HISTORY_STORE_NAME
    state = EffectHistory(
        (
            _event("light"),
            _event("screen", surface=EffectSurface.SCREEN_BAR),
            _event("suppressed", outcome=EffectOutcome.SUPPRESSED),
            _event("ack", outcome=EffectOutcome.ACKNOWLEDGED),
        ),
        NOW - 1.0,
    )

    assert save_effect_history(target, state) == target
    first_bytes = target.read_bytes()
    save_effect_history(target, state)
    restored = load_effect_history(target)

    assert restored == EffectHistoryRestore(state, EffectHistoryRestoreHealth.HEALTHY)
    assert target.read_bytes() == first_bytes
    assert first_bytes.endswith(b"\n")
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600
    document = json.loads(first_bytes)
    assert set(document) == {"events", "last_seen_epoch", "version"}
    assert document["version"] == EFFECT_HISTORY_STORE_VERSION == 1
    assert all(
        set(event)
        == {
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
        for event in document["events"]
    )
    serialized = first_bytes.decode("utf-8").casefold()
    for forbidden in (
        '"prompt"',
        '"message"',
        '"session"',
        '"path"',
        '"url"',
        "private sentinel",
        "person@example.com",
        "https://private.example",
        "/users/private/path",
    ):
        assert forbidden not in serialized


def test_restore_health_distinguishes_size_version_corruption_and_availability(
    tmp_path: Path,
) -> None:
    target = tmp_path / EFFECT_HISTORY_STORE_NAME
    assert load_effect_history(target).health is EffectHistoryRestoreHealth.MISSING

    target.write_text(json.dumps(_document(version=2)))
    assert load_effect_history(target).health is EffectHistoryRestoreHealth.UNSUPPORTED

    target.write_text("{")
    assert load_effect_history(target).health is EffectHistoryRestoreHealth.CORRUPT

    target.write_bytes(b" " * (MAX_EFFECT_HISTORY_STORE_BYTES + 1))
    assert load_effect_history(target).health is EffectHistoryRestoreHealth.OVERSIZED

    with patch(
        "sidepulse.effect_history_store.read_private_text",
        side_effect=PermissionError("/private/path must not escape"),
    ):
        unavailable = load_effect_history(target)
    assert unavailable == EffectHistoryRestore(
        EffectHistory(),
        EffectHistoryRestoreHealth.UNAVAILABLE,
    )
    assert "/private/path" not in repr(unavailable)


@pytest.mark.parametrize(
    "document",
    (
        _document(version=True),
        {"version": 1, "events": []},
        {**_document(), "prompt": "private"},
        _document(last_seen_epoch=True),
        _document(last_seen_epoch=-1),
        _document(events={}),
        _document(events=[{**_event_payload(_event()), "message": "private"}]),
        _document(events=[{**_event_payload(_event()), "version": 2}]),
        _document(events=[{**_event_payload(_event()), "surface": "unknown"}]),
        _document(
            events=[
                {
                    **_event_payload(_event(outcome=EffectOutcome.SUPPRESSED)),
                    "suppression_reason": None,
                }
            ]
        ),
    ),
)
def test_unknown_malformed_or_inconsistent_fields_are_corrupt(
    tmp_path: Path,
    document: object,
) -> None:
    target = tmp_path / EFFECT_HISTORY_STORE_NAME
    target.write_text(json.dumps(document))

    restored = load_effect_history(target)

    assert restored == EffectHistoryRestore(
        EffectHistory(),
        EffectHistoryRestoreHealth.CORRUPT,
    )


def test_duplicate_json_fields_and_duplicate_exact_events_are_corrupt(
    tmp_path: Path,
) -> None:
    target = tmp_path / EFFECT_HISTORY_STORE_NAME
    target.write_text('{"version":1,"version":1,"last_seen_epoch":0,"events":[]}')
    assert load_effect_history(target).health is EffectHistoryRestoreHealth.CORRUPT

    payload = _event_payload(_event())
    target.write_text(json.dumps(_document(events=[payload, payload])))
    assert load_effect_history(target).health is EffectHistoryRestoreHealth.CORRUPT


def test_oversized_store_is_classified_without_reading_payload(tmp_path: Path) -> None:
    target = tmp_path / EFFECT_HISTORY_STORE_NAME
    target.write_bytes(b" " * (MAX_EFFECT_HISTORY_STORE_BYTES + 1))
    real_read = os.read
    bytes_read = 0

    def observing_read(descriptor: int, size: int) -> bytes:
        nonlocal bytes_read
        chunk = real_read(descriptor, size)
        bytes_read += len(chunk)
        return chunk

    with patch("sidepulse.private_io.os.read", side_effect=observing_read):
        restored = load_effect_history(target)

    assert restored.health is EffectHistoryRestoreHealth.OVERSIZED
    assert bytes_read == 0


@pytest.mark.parametrize("operation", ("load", "save"))
@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_store_refuses_linked_leaf_without_touching_outside_file(
    tmp_path: Path,
    operation: str,
    link_kind: str,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("outside stays unchanged")
    outside.chmod(0o644)
    target = tmp_path / "state" / EFFECT_HISTORY_STORE_NAME
    target.parent.mkdir()
    if link_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)

    if operation == "load":
        restored = load_effect_history(target)
        assert restored.health is EffectHistoryRestoreHealth.UNAVAILABLE
    else:
        with pytest.raises(OSError):
            save_effect_history(target, EffectHistory((_event(),)))

    assert outside.read_text() == "outside stays unchanged"
    assert _mode(outside) == 0o644


def test_failed_atomic_replace_preserves_previous_history(tmp_path: Path) -> None:
    target = tmp_path / "state" / EFFECT_HISTORY_STORE_NAME
    old = EffectHistory((_event("old"),))
    new = EffectHistory((_event("new"),))
    save_effect_history(target, old)
    previous = target.read_bytes()

    with (
        patch("sidepulse.private_io.os.replace", side_effect=OSError("replace failed")),
        pytest.raises(OSError, match="replace failed"),
    ):
        save_effect_history(target, new)

    assert target.read_bytes() == previous
    assert load_effect_history(target).history == old
