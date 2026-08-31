from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.clear_agents import (
    MAX_CLEAR_TARGETS,
    MAX_COMPLETION_RECEIPTS,
    ClearAgentsBatchReceipt,
    ClearAgentsState,
    CompletionPresentationKey,
    CompletionPresentationReceipt,
)
from sidepulse.clear_agents_store import (
    CLEAR_AGENTS_STORE_NAME,
    MAX_CLEAR_AGENTS_STORE_BYTES,
    ClearAgentsRestore,
    ClearAgentsRestoreHealth,
    default_clear_agents_path,
    load_clear_agents_state,
    save_clear_agents_state,
)

NOW = 1_788_120_000.0


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _key(
    suffix: str = "one",
    *,
    source_instance: str = "local:01",
) -> CompletionPresentationKey:
    return CompletionPresentationKey(
        SourceKey("codex", "hooks", source_instance, "live_agent_events"),
        f"codex:agent:{suffix}",
        "Stop",
        NOW + float(sum(ord(character) for character in suffix)),
    )


def _state(*keys: CompletionPresentationKey, undone: bool = False) -> ClearAgentsState:
    ordered = tuple(sorted(keys))
    receipts = tuple(
        CompletionPresentationReceipt(key, NOW + float(index))
        for index, key in enumerate(ordered)
    )
    if not ordered:
        return ClearAgentsState()
    batch = ClearAgentsBatchReceipt(
        "batch:01",
        ordered,
        NOW,
        NOW + 300.0,
        1,
        undone,
    )
    return ClearAgentsState(1, () if undone else receipts, batch)


def _document(
    *,
    generation: object = 0,
    receipts: object = (),
    latest_batch: object = None,
    version: object = 1,
) -> dict[str, object]:
    return {
        "generation": generation,
        "latest_batch": latest_batch,
        "receipts": list(receipts) if isinstance(receipts, (list, tuple)) else receipts,
        "version": version,
    }


def _key_payload(key: CompletionPresentationKey) -> dict[str, object]:
    source = key.source_key
    return {
        "agent_id": key.agent_id,
        "completed_at_epoch": key.completed_at_epoch,
        "event_name": key.event_name,
        "source_key": {
            "adapter_id": source.adapter_id,
            "capability_id": source.capability_id,
            "provider_id": source.provider_id,
            "source_instance_id": source.source_instance_id,
        },
    }


def _receipt_payload(key: CompletionPresentationKey) -> dict[str, object]:
    return {"acknowledged_at_epoch": NOW, "key": _key_payload(key)}


def _batch_payload(
    *keys: CompletionPresentationKey,
    undone: bool = False,
) -> dict[str, object]:
    return {
        "batch_id": "batch:01",
        "commit_generation": 1,
        "committed_at_epoch": NOW,
        "newly_added_keys": [_key_payload(key) for key in keys],
        "undo_deadline_epoch": NOW + 300.0,
        "undone": undone,
    }


def test_default_path_uses_existing_state_directory(tmp_path: Path) -> None:
    assert default_clear_agents_path(tmp_path) == (
        tmp_path
        / ".local"
        / "state"
        / "sidepulse"
        / "agent-monitor"
        / CLEAR_AGENTS_STORE_NAME
    )


def test_round_trip_is_private_exact_content_free_and_deterministic(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / CLEAR_AGENTS_STORE_NAME
    state = _state(_key("one"), _key("two", source_instance="local:02"))

    assert save_clear_agents_state(target, state) == target
    first_bytes = target.read_bytes()
    save_clear_agents_state(target, state)

    restored = load_clear_agents_state(target)
    assert restored.state == state
    assert restored.health is ClearAgentsRestoreHealth.HEALTHY
    assert target.read_bytes() == first_bytes
    assert first_bytes.endswith(b"\n")
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600
    document = json.loads(first_bytes)
    assert set(document) == {"generation", "latest_batch", "receipts", "version"}
    assert set(document["receipts"][0]) == {"acknowledged_at_epoch", "key"}
    assert set(document["receipts"][0]["key"]) == {
        "agent_id",
        "completed_at_epoch",
        "event_name",
        "source_key",
    }
    assert set(document["latest_batch"]) == {
        "batch_id",
        "commit_generation",
        "committed_at_epoch",
        "newly_added_keys",
        "undo_deadline_epoch",
        "undone",
    }
    forbidden = ("prompt", "transcript", "credential", "hook_path", "raw_error")
    assert all(word not in first_bytes.decode("utf-8") for word in forbidden)


def test_empty_and_undone_state_round_trip(tmp_path: Path) -> None:
    target = tmp_path / CLEAR_AGENTS_STORE_NAME
    empty = ClearAgentsState()
    save_clear_agents_state(target, empty)
    assert load_clear_agents_state(target).state == empty

    undone = _state(_key(), undone=True)
    save_clear_agents_state(target, undone)
    assert load_clear_agents_state(target).state == undone


def test_restore_health_distinguishes_missing_unsupported_corrupt_and_unavailable(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / CLEAR_AGENTS_STORE_NAME
    assert load_clear_agents_state(target).health is ClearAgentsRestoreHealth.MISSING

    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(_document(version=2)))
    unsupported = load_clear_agents_state(target)
    assert unsupported.state == ClearAgentsState()
    assert unsupported.health is ClearAgentsRestoreHealth.UNSUPPORTED

    target.write_text("{")
    corrupt = load_clear_agents_state(target)
    assert corrupt.state == ClearAgentsState()
    assert corrupt.health is ClearAgentsRestoreHealth.CORRUPT

    with patch(
        "sidepulse.clear_agents_store.read_private_text",
        side_effect=OSError("/private/path must not escape"),
    ):
        unavailable = load_clear_agents_state(target)
    assert unavailable.state == ClearAgentsState()
    assert unavailable.health is ClearAgentsRestoreHealth.UNAVAILABLE
    assert "/private/path" not in repr(unavailable)


def test_permission_denial_restores_empty_unavailable_state(tmp_path: Path) -> None:
    target = tmp_path / CLEAR_AGENTS_STORE_NAME
    with patch(
        "sidepulse.clear_agents_store.read_private_text",
        side_effect=PermissionError("permission denied"),
    ):
        restored = load_clear_agents_state(target)

    assert restored == ClearAgentsRestore(
        ClearAgentsState(),
        ClearAgentsRestoreHealth.UNAVAILABLE,
    )


@pytest.mark.parametrize(
    "payload",
    (
        _document(version=True),
        {"version": 1, "generation": 0, "receipts": []},
        {**_document(), "prompt": "private"},
        _document(generation=-1),
        _document(generation=1.0),
        _document(receipts={}),
        _document(receipts=[{"key": {}, "acknowledged_at_epoch": NOW}]),
        _document(receipts=[{**_receipt_payload(_key()), "prompt": "private"}]),
        _document(latest_batch={}),
        _document(latest_batch=_batch_payload(_key()), generation=0),
        _document(
            generation=1,
            receipts=[_receipt_payload(_key())],
            latest_batch={**_batch_payload(_key()), "undone": 1},
        ),
    ),
)
def test_malformed_unknown_or_inconsistent_data_is_corrupt(
    tmp_path: Path,
    payload: object,
) -> None:
    target = tmp_path / CLEAR_AGENTS_STORE_NAME
    target.write_text(json.dumps(payload))

    restored = load_clear_agents_state(target)

    assert restored.state == ClearAgentsState()
    assert restored.health is ClearAgentsRestoreHealth.CORRUPT


def test_duplicate_json_fields_and_duplicate_receipts_are_corrupt(
    tmp_path: Path,
) -> None:
    target = tmp_path / CLEAR_AGENTS_STORE_NAME
    target.write_text(
        '{"version":1,"version":1,"generation":0,"receipts":[],"latest_batch":null}'
    )
    assert load_clear_agents_state(target).health is ClearAgentsRestoreHealth.CORRUPT

    key = _key()
    target.write_text(
        json.dumps(
            _document(
                generation=1,
                receipts=[_receipt_payload(key), _receipt_payload(key)],
                latest_batch=_batch_payload(key),
            )
        )
    )
    assert load_clear_agents_state(target).health is ClearAgentsRestoreHealth.CORRUPT


def test_receipt_and_batch_count_caps_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / CLEAR_AGENTS_STORE_NAME
    receipt_keys = [_key(f"r{index}") for index in range(MAX_COMPLETION_RECEIPTS + 1)]
    target.write_text(
        json.dumps(_document(receipts=[_receipt_payload(key) for key in receipt_keys]))
    )
    assert load_clear_agents_state(target).health is ClearAgentsRestoreHealth.CORRUPT

    batch_keys = [_key(f"b{index}") for index in range(MAX_CLEAR_TARGETS + 1)]
    target.write_text(
        json.dumps(
            _document(
                generation=1,
                latest_batch={
                    **_batch_payload(batch_keys[0]),
                    "newly_added_keys": [_key_payload(key) for key in batch_keys],
                },
            )
        )
    )
    assert load_clear_agents_state(target).health is ClearAgentsRestoreHealth.CORRUPT


def test_oversized_store_is_unavailable_without_reading_payload(tmp_path: Path) -> None:
    target = tmp_path / CLEAR_AGENTS_STORE_NAME
    target.write_bytes(b" " * (MAX_CLEAR_AGENTS_STORE_BYTES + 1))
    real_read = os.read
    bytes_read = 0

    def observing_read(descriptor: int, size: int) -> bytes:
        nonlocal bytes_read
        chunk = real_read(descriptor, size)
        bytes_read += len(chunk)
        return chunk

    with patch("sidepulse.private_io.os.read", side_effect=observing_read):
        restored = load_clear_agents_state(target)

    assert restored.state == ClearAgentsState()
    assert restored.health is ClearAgentsRestoreHealth.UNAVAILABLE
    assert bytes_read == 0


@pytest.mark.parametrize("operation", ("load", "save"))
@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_store_refuses_linked_leaves_without_touching_outside_file(
    tmp_path: Path,
    operation: str,
    link_kind: str,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("outside stays unchanged")
    outside.chmod(0o644)
    target = tmp_path / "state" / CLEAR_AGENTS_STORE_NAME
    target.parent.mkdir()
    if link_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)

    if operation == "load":
        restored = load_clear_agents_state(target)
        assert restored.health is ClearAgentsRestoreHealth.UNAVAILABLE
    else:
        with pytest.raises(OSError):
            save_clear_agents_state(target, _state(_key()))

    assert outside.read_text() == "outside stays unchanged"
    assert _mode(outside) == 0o644


def test_failed_atomic_replace_preserves_exact_previous_bytes(tmp_path: Path) -> None:
    target = tmp_path / "state" / CLEAR_AGENTS_STORE_NAME
    old_state = _state(_key("old"))
    new_state = _state(_key("new"))
    save_clear_agents_state(target, old_state)
    previous = target.read_bytes()

    with (
        patch("sidepulse.private_io.os.replace", side_effect=OSError("replace failed")),
        pytest.raises(OSError, match="replace failed"),
    ):
        save_clear_agents_state(target, new_state)

    assert target.read_bytes() == previous
    assert load_clear_agents_state(target).state == old_state


@dataclass(frozen=True, slots=True)
class _ExtendedReceipt(CompletionPresentationReceipt):
    prompt: str = ""
    transcript: str = ""


def test_save_refuses_forged_or_secret_extended_receipts(tmp_path: Path) -> None:
    target = tmp_path / CLEAR_AGENTS_STORE_NAME
    sentinel = "Bearer sk-private /Users/private/transcript"
    forged = object.__new__(ClearAgentsState)
    object.__setattr__(forged, "generation", 1)
    object.__setattr__(
        forged,
        "receipts",
        (_ExtendedReceipt(_key(), NOW, sentinel, sentinel),),
    )
    object.__setattr__(forged, "latest_batch", None)

    with pytest.raises(ValueError, match="Clear Agents"):
        save_clear_agents_state(target, forged)

    assert not target.exists()


def test_save_refuses_encoded_state_over_byte_budget(tmp_path: Path) -> None:
    target = tmp_path / CLEAR_AGENTS_STORE_NAME
    receipts = tuple(
        CompletionPresentationReceipt(
            CompletionPresentationKey(
                SourceKey("codex", "hooks", f"s:{index}", "live_agent_events"),
                f"codex:agent:{'●' * 480}:{index}",
                "Stop",
                NOW + index,
            ),
            NOW,
        )
        for index in range(MAX_COMPLETION_RECEIPTS)
    )
    state = ClearAgentsState(
        generation=1,
        receipts=tuple(sorted(receipts, key=lambda receipt: receipt.key)),
    )

    with pytest.raises(ValueError, match="maximum size"):
        save_clear_agents_state(target, state)

    assert not target.exists()
