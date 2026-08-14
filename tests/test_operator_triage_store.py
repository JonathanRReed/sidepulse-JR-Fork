from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.local_triage import LocalAcknowledgement, LocalTriageState
from sidepulse.operator_triage_store import load_operator_triage, save_operator_triage
from sidepulse.provider_facts import (
    RequestIdentifier,
    RequestKey,
    WorkIdentifier,
    WorkKey,
    request_key_to_payload,
)

NOW = 1_786_536_000.0


def _request_key(
    request_id: str = "request:01",
    *,
    source_instance: str = "local:01",
) -> RequestKey:
    source = SourceKey("codex", "hooks", source_instance, "live_agent_events")
    return RequestKey(
        WorkKey(source, WorkIdentifier("work:01")),
        RequestIdentifier(request_id),
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _state(*keys: RequestKey) -> LocalTriageState:
    return LocalTriageState(
        tuple(
            LocalAcknowledgement(key, NOW + float(index))
            for index, key in enumerate(keys)
        )
    )


def _document(*entries: dict[str, object]) -> str:
    return json.dumps(
        {"version": 1, "acknowledgements": list(entries)},
        separators=(",", ":"),
        sort_keys=True,
    )


def _entry(key: RequestKey, acknowledged_at: object = NOW) -> dict[str, object]:
    return {
        "request_key": request_key_to_payload(key),
        "acknowledged_at": acknowledged_at,
    }


def test_round_trip_is_private_bounded_exact_v1_json(tmp_path: Path) -> None:
    target = tmp_path / "state" / "operator-triage.json"
    first = _request_key("request:first")
    second = _request_key("request:second", source_instance="local:02")
    state = _state(first, second)

    save_operator_triage(target, state)

    assert load_operator_triage(target) == state
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600
    document = json.loads(target.read_text())
    assert set(document) == {"acknowledgements", "version"}
    assert document["version"] == 1
    assert len(document["acknowledgements"]) == 2
    assert all(
        set(entry) == {"acknowledged_at", "request_key"}
        for entry in document["acknowledgements"]
    )


@pytest.mark.parametrize("operation", ("load", "save"))
@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_store_refuses_linked_leaves(
    tmp_path: Path,
    operation: str,
    link_kind: str,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("outside stays unchanged")
    outside.chmod(0o644)
    target = tmp_path / "state" / "operator-triage.json"
    target.parent.mkdir()
    if link_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)

    if operation == "load":
        assert load_operator_triage(target) == LocalTriageState(())
    else:
        with pytest.raises(OSError):
            save_operator_triage(target, _state(_request_key()))

    assert outside.read_text() == "outside stays unchanged"
    assert _mode(outside) == 0o644


def test_load_uses_held_parent_during_parent_path_swap(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "operator-triage.json"
    inside_state = _state(_request_key("request:inside"))
    outside_state = _state(_request_key("request:outside"))
    save_operator_triage(target, inside_state)
    save_operator_triage(outside / target.name, outside_state)
    held_parent = parent.with_name("state-held")
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and Path(path).name == target.name:
            parent.rename(held_parent)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        loaded = load_operator_triage(target)

    assert loaded == inside_state
    assert load_operator_triage(held_parent / target.name) == inside_state
    assert load_operator_triage(outside / target.name) == outside_state


def test_load_refuses_oversized_file_before_reading_payload(tmp_path: Path) -> None:
    target = tmp_path / "state" / "operator-triage.json"
    target.parent.mkdir()
    target.write_bytes(b" " * 1_048_577)
    real_read = os.read
    bytes_read = 0

    def observing_read(descriptor: int, size: int) -> bytes:
        nonlocal bytes_read
        chunk = real_read(descriptor, size)
        bytes_read += len(chunk)
        return chunk

    with patch("sidepulse.private_io.os.read", side_effect=observing_read):
        assert load_operator_triage(target) == LocalTriageState(())

    assert bytes_read == 0


def test_load_refuses_file_growth_beyond_bound(tmp_path: Path) -> None:
    target = tmp_path / "state" / "operator-triage.json"
    save_operator_triage(target, _state(_request_key()))
    real_read = os.read
    grew = False

    def growing_read(descriptor: int, size: int) -> bytes:
        nonlocal grew
        chunk = real_read(descriptor, size)
        if not grew and chunk:
            with target.open("ab") as stream:
                stream.write(b" " * 1_048_577)
            grew = True
        return chunk

    with patch("sidepulse.private_io.os.read", side_effect=growing_read):
        assert load_operator_triage(target) == LocalTriageState(())


@pytest.mark.parametrize(
    "payload",
    (
        "{",
        json.dumps({"version": 2, "acknowledgements": []}),
        json.dumps({"version": True, "acknowledgements": []}),
        json.dumps({"version": 1, "acknowledgements": {}}),
        json.dumps({"version": 1, "acknowledgements": [], "prompt": "private"}),
        _document({"request_key": {}, "acknowledged_at": NOW}),
        _document(_entry(_request_key(), float("nan"))),
        _document(_entry(_request_key(), -1.0)),
        _document(
            {
                **_entry(_request_key()),
                "prompt": "Bearer sk-private /Users/private",
            }
        ),
    ),
)
def test_corrupt_unknown_extra_or_invalid_data_fails_closed(
    tmp_path: Path,
    payload: str,
) -> None:
    target = tmp_path / "state" / "operator-triage.json"
    target.parent.mkdir()
    target.write_text(payload)

    assert load_operator_triage(target) == LocalTriageState(())


def test_duplicate_json_key_and_duplicate_request_key_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "state" / "operator-triage.json"
    target.parent.mkdir()
    key = _request_key()
    target.write_text('{"version":1,"version":1,"acknowledgements":[]}')
    assert load_operator_triage(target) == LocalTriageState(())

    target.write_text(_document(_entry(key), _entry(key, NOW + 1.0)))
    assert load_operator_triage(target) == LocalTriageState(())


class _MappingSubclass(dict):
    pass


def test_mapping_subclass_from_decoder_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "state" / "operator-triage.json"
    save_operator_triage(target, _state(_request_key()))
    forged = _MappingSubclass(
        {"version": 1, "acknowledgements": [_entry(_request_key())]}
    )

    with patch("sidepulse.operator_triage_store._decode_document", return_value=forged):
        assert load_operator_triage(target) == LocalTriageState(())


def test_failed_replace_preserves_exact_previous_bytes(tmp_path: Path) -> None:
    target = tmp_path / "state" / "operator-triage.json"
    old_state = _state(_request_key("request:old"))
    new_state = _state(_request_key("request:new"))
    save_operator_triage(target, old_state)
    previous = target.read_bytes()

    with (
        patch("sidepulse.private_io.os.replace", side_effect=OSError("replace failed")),
        pytest.raises(OSError, match="replace failed"),
    ):
        save_operator_triage(target, new_state)

    assert target.read_bytes() == previous
    assert load_operator_triage(target) == old_state


@dataclass(frozen=True, slots=True)
class _ExtendedAcknowledgement(LocalAcknowledgement):
    prompt: str = ""
    command: str = ""
    raw_error: str = ""


def test_save_allowlist_excludes_secret_shaped_extended_attributes(tmp_path: Path) -> None:
    target = tmp_path / "state" / "operator-triage.json"
    sentinel = "Bearer sk-private /Users/jonathan/Secret command"
    extended = _ExtendedAcknowledgement(
        _request_key(),
        NOW,
        prompt=sentinel,
        command=sentinel,
        raw_error=sentinel,
    )
    state = LocalTriageState((extended,))  # type: ignore[arg-type]

    save_operator_triage(target, state)

    raw = target.read_text()
    assert sentinel not in raw
    assert "prompt" not in raw
    assert "command" not in raw
    assert "raw_error" not in raw


def test_save_refuses_more_than_five_hundred_twelve_records(tmp_path: Path) -> None:
    target = tmp_path / "state" / "operator-triage.json"
    acknowledgements = tuple(
        LocalAcknowledgement(_request_key(f"request:{index:03d}"), NOW)
        for index in range(513)
    )
    forged_state = object.__new__(LocalTriageState)
    object.__setattr__(forged_state, "acknowledgements", acknowledgements)

    with pytest.raises(ValueError, match="triage"):
        save_operator_triage(target, forged_state)

    assert len(acknowledgements) == 513
    assert not target.exists()
