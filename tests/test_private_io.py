from __future__ import annotations

import copy
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.private_io import (
    REDACTION_MARKER,
    RetentionPolicy,
    append_private_text,
    atomic_private_write,
    enforce_retention,
    ensure_private_directory,
    ensure_private_file,
    read_private_log_slice,
    read_private_text,
    redact_event_payload,
)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _swap_parent_when_opening(
    parent: Path,
    outside: Path,
    matches_leaf,
):
    """Return a held parent path and os.open double that swaps its pathname."""
    held_parent = parent.with_name(f"{parent.name}-held")
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and matches_leaf(Path(path).name):
            parent.rename(held_parent)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    return held_parent, swapping_open


def test_private_write_ignores_permissive_umask(tmp_path: Path) -> None:
    target = tmp_path / "state" / "settings.json"
    previous = os.umask(0)
    try:
        atomic_private_write(target, "secret settings")
    finally:
        os.umask(previous)

    assert target.read_text() == "secret settings"
    assert mode(target.parent) == 0o700
    assert mode(target) == 0o600


def test_create_only_private_write_preserves_existing_recovery_data(tmp_path: Path) -> None:
    target = tmp_path / "backup.json"
    atomic_private_write(target, "first backup", overwrite=False)
    with pytest.raises(FileExistsError):
        atomic_private_write(target, "replacement backup", overwrite=False)
    assert read_private_text(target) == "first backup"
    assert target.stat().st_nlink == 1
    assert list(tmp_path.glob("backup.json.*.tmp")) == []


def test_create_only_private_publish_has_one_winner_when_two_writers_race(tmp_path: Path) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    target = tmp_path / "backup.json"
    barrier = threading.Barrier(2)
    original_link = os.link

    def competing_link(*args, **kwargs):
        barrier.wait(timeout=2)
        return original_link(*args, **kwargs)

    def publish(value):
        try:
            atomic_private_write(target, value, overwrite=False)
        except FileExistsError:
            return None
        return value

    with patch("sidepulse.private_io.os.link", side_effect=competing_link), ThreadPoolExecutor(2) as executor:
        outcomes = list(executor.map(publish, ("first", "second")))
    winners = [value for value in outcomes if value is not None]
    assert len(winners) == 1
    assert read_private_text(target) == winners[0]
    assert target.stat().st_nlink == 1
    assert list(tmp_path.glob("backup.json.*.tmp")) == []


def test_existing_broad_file_and_parent_are_tightened_without_data_loss(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o777)
    target = state / "events.jsonl"
    target.write_bytes(b"preserve me\n")
    state.chmod(0o777)
    target.chmod(0o644)

    assert ensure_private_directory(state) == state
    assert ensure_private_file(target) == target

    assert target.read_bytes() == b"preserve me\n"
    assert mode(state) == 0o700
    assert mode(target) == 0o600


def test_private_writes_refuse_symlinks_and_non_directory_parents(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside stays unchanged")
    linked = tmp_path / "state.json"
    linked.symlink_to(outside)

    with pytest.raises(OSError):
        atomic_private_write(linked, "overwrite")
    assert outside.read_text() == "outside stays unchanged"

    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("regular file")
    with pytest.raises(OSError):
        ensure_private_directory(not_a_directory / "child")


def test_failed_atomic_replace_preserves_target_and_removes_scratch(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "latest.json"
    atomic_private_write(target, "old state")

    with (
        patch("sidepulse.private_io.os.replace", side_effect=OSError("replace failed")),
        pytest.raises(OSError, match="replace failed"),
    ):
        atomic_private_write(target, "new state")

    assert target.read_text() == "old state"
    assert list(target.parent.glob(f"{target.name}.*.tmp")) == []


def test_append_is_private_and_refuses_preplanted_symlink(tmp_path: Path) -> None:
    target = tmp_path / "hooks" / "codex.jsonl"
    append_private_text(target, "one\n")
    append_private_text(target, "two\n")

    assert target.read_text() == "one\ntwo\n"
    assert mode(target.parent) == 0o700
    assert mode(target) == 0o600

    outside = tmp_path / "outside.jsonl"
    outside.write_text("outside\n")
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(OSError):
        append_private_text(target, "attack\n")
    assert outside.read_text() == "outside\n"


def test_atomic_write_parent_descriptor_prevents_path_swap_redirect(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "latest.json"
    atomic_private_write(target, "old state")
    held_parent, swapping_open = _swap_parent_when_opening(
        parent,
        outside,
        lambda leaf: leaf.startswith("latest.json.") and leaf.endswith(".tmp"),
    )

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        atomic_private_write(target, "new state")

    assert (held_parent / "latest.json").read_text() == "new state"
    assert list(held_parent.glob("latest.json.*.tmp")) == []
    assert list(outside.iterdir()) == []


def test_ensure_file_parent_descriptor_prevents_path_swap_redirect(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "state"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "settings.json"
    held_parent, swapping_open = _swap_parent_when_opening(
        parent,
        outside,
        lambda leaf: leaf == target.name,
    )

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        ensure_private_file(target)

    assert (held_parent / target.name).is_file()
    assert mode(held_parent / target.name) == 0o600
    assert list(outside.iterdir()) == []


def test_append_parent_descriptor_prevents_path_swap_redirect(tmp_path: Path) -> None:
    parent = tmp_path / "hooks"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "codex.jsonl"
    append_private_text(target, "inside one\n")
    held_parent, swapping_open = _swap_parent_when_opening(
        parent,
        outside,
        lambda leaf: leaf == target.name,
    )

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        append_private_text(target, "inside two\n")

    assert (held_parent / target.name).read_text() == "inside one\ninside two\n"
    assert list(outside.iterdir()) == []


def test_read_parent_descriptor_prevents_path_swap_redirect(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    parent.mkdir()
    target = parent / "settings.json"
    target.write_text("inside settings")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / target.name).write_text("outside private data")
    held_parent, swapping_open = _swap_parent_when_opening(
        parent,
        outside,
        lambda leaf: leaf == target.name,
    )

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        result = read_private_text(target, max_bytes=1_024)

    assert result == "inside settings"
    assert (held_parent / target.name).read_text() == "inside settings"
    assert (outside / target.name).read_text() == "outside private data"


def test_nontightening_read_keeps_ambient_parent_and_file_modes(tmp_path: Path) -> None:
    parent = tmp_path / "provider-config"
    parent.mkdir(mode=0o755)
    target = parent / "config.json"
    target.write_text("provider config")
    parent.chmod(0o755)
    target.chmod(0o644)

    assert read_private_text(target, tighten=False) == "provider config"

    assert mode(parent) == 0o755
    assert mode(target) == 0o644


def test_bounded_read_refuses_oversized_opened_leaf_before_payload_read(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "settings.json"
    target.parent.mkdir(mode=0o777)
    target.write_bytes(b"012345678")
    target.parent.chmod(0o777)
    target.chmod(0o644)
    real_read = os.read
    payload_bytes_read = 0

    def observing_read(descriptor: int, size: int) -> bytes:
        nonlocal payload_bytes_read
        chunk = real_read(descriptor, size)
        payload_bytes_read += len(chunk)
        return chunk

    with (
        patch("sidepulse.private_io.os.read", side_effect=observing_read),
        pytest.raises(OSError, match="maximum size"),
    ):
        read_private_text(target, max_bytes=8)

    assert payload_bytes_read == 0
    assert mode(target.parent) == 0o700
    assert mode(target) == 0o600


def test_bounded_read_caps_growth_at_limit_plus_one(tmp_path: Path) -> None:
    target = tmp_path / "state" / "settings.json"
    target.parent.mkdir()
    target.write_bytes(b"safe")
    real_read = os.read
    payload_bytes_read = 0
    grew = False

    def grow_then_read(descriptor: int, size: int) -> bytes:
        nonlocal grew, payload_bytes_read
        if not grew:
            with target.open("ab") as stream:
                stream.write(b"x" * 100)
            grew = True
        chunk = real_read(descriptor, size)
        payload_bytes_read += len(chunk)
        return chunk

    with (
        patch("sidepulse.private_io.os.read", side_effect=grow_then_read),
        pytest.raises(OSError, match="maximum size"),
    ):
        read_private_text(target, max_bytes=8)

    assert payload_bytes_read == 9


def test_bounded_read_refuses_leaf_replacement_during_read(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    parent.mkdir()
    target = parent / "settings.json"
    target.write_text("inside settings")
    replacement = parent / "replacement.json"
    replacement.write_text("replacement data")
    original = parent / "original.json"
    real_read = os.read
    replaced = False

    def replace_after_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        chunk = real_read(descriptor, size)
        if not replaced:
            target.rename(original)
            replacement.rename(target)
            replaced = True
        return chunk

    with (
        patch("sidepulse.private_io.os.read", side_effect=replace_after_read),
        pytest.raises(OSError, match="changed during operation"),
    ):
        read_private_text(target, max_bytes=1_024)

    assert original.read_text() == "inside settings"
    assert target.read_text() == "replacement data"


def test_bounded_read_refuses_symlink_leaf(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("outside stays unchanged")
    target = tmp_path / "state" / "settings.json"
    target.parent.mkdir()
    target.symlink_to(outside)

    with pytest.raises(OSError):
        read_private_text(target, max_bytes=1_024)

    assert outside.read_text() == "outside stays unchanged"


@pytest.mark.parametrize("max_bytes", (-1, True, 1.5))
def test_private_read_rejects_invalid_byte_limits(
    tmp_path: Path,
    max_bytes: object,
) -> None:
    target = tmp_path / "state" / "settings.json"
    target.parent.mkdir()
    target.write_text("settings")

    with pytest.raises(ValueError, match="max_bytes"):
        read_private_text(target, max_bytes=max_bytes)


@pytest.mark.parametrize("operation", ("ensure", "atomic", "append", "read"))
def test_private_file_helpers_refuse_hard_link_targets(
    tmp_path: Path,
    operation: str,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside stays unchanged")
    outside.chmod(0o644)
    parent = tmp_path / "state"
    parent.mkdir()
    target = parent / "private.txt"
    os.link(outside, target)

    with pytest.raises(OSError):
        if operation == "ensure":
            ensure_private_file(target)
        elif operation == "atomic":
            atomic_private_write(target, "replacement")
        elif operation == "append":
            append_private_text(target, "attack")
        else:
            read_private_text(target, max_bytes=1_024)

    assert outside.read_text() == "outside stays unchanged"
    assert mode(outside) == 0o644


def test_retention_skips_hard_links_outside_selected_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_text("outside stays unchanged")
    outside.chmod(0o644)
    root = tmp_path / "retained"
    root.mkdir()
    linked = root / "linked.jsonl"
    os.link(outside, linked)

    removed = enforce_retention(root, RetentionPolicy(max_files=0))

    assert removed == ()
    assert linked.exists()
    assert outside.read_text() == "outside stays unchanged"
    assert mode(outside) == 0o644


def test_retention_refuses_hard_link_created_after_scan(tmp_path: Path) -> None:
    from sidepulse import private_io

    root = tmp_path / "retained"
    root.mkdir()
    target = root / "candidate.jsonl"
    target.write_text("must stay")
    outside_link = tmp_path / "outside-link.jsonl"
    real_entries = private_io._retention_entries

    def entries_then_link(*args, **kwargs):
        entries = real_entries(*args, **kwargs)
        os.link(target, outside_link)
        return entries

    with patch(
        "sidepulse.private_io._retention_entries",
        side_effect=entries_then_link,
    ):
        removed = enforce_retention(root, RetentionPolicy(max_files=0))

    assert removed == ()
    assert target.read_text() == "must stay"
    assert outside_link.read_text() == "must stay"


def test_recursive_redaction_preserves_lifecycle_fields_and_input(
    tmp_path: Path,
) -> None:
    payload = {
        "hook_event_name": "Notification",
        "logged_at": "2026-08-12T12:00:00Z",
        "session_id": "session-1",
        "turn_id": "turn-2",
        "agent_id": "agent-3",
        "cwd": str(tmp_path / "project"),
        "workspaceRoot": str(tmp_path / "project"),
        "tool_name": "Bash",
        "notification_type": "idle_prompt",
        "agent_origin": "Codex UI",
        "message": "Claude is waiting for your input",
        "headers": {
            "Authorization": "Bearer secret-token",
            "Cookie": "session=secret-cookie",
            "X-Request-ID": "safe-routing-id",
        },
        "authToken": "nested-token",
        "api_key": "private-api-key",
        "webhook_url": "https://secret.example/hook",
        "prompt": "private user prompt",
        "raw": "malformed private body",
        "tool_input": {"command": "cat ~/.credentials"},
        "toolResult": {"stdout": "private result"},
        "tool_use_result": {"stdout": "alternate private result"},
        "command": "cat ~/.credentials",
        "request": {"body": "private request"},
        "response_body": ["private", {"token": "deeper-secret"}],
    }
    original = copy.deepcopy(payload)

    redacted = redact_event_payload(payload)

    assert payload == original
    assert redacted["hook_event_name"] == "Notification"
    assert redacted["session_id"] == "session-1"
    assert redacted["turn_id"] == "turn-2"
    assert redacted["agent_id"] == "agent-3"
    assert redacted["cwd"] == str(tmp_path / "project")
    assert redacted["workspaceRoot"] == str(tmp_path / "project")
    assert redacted["tool_name"] == "Bash"
    assert redacted["notification_type"] == "idle_prompt"
    assert redacted["agent_origin"] == "Codex UI"
    assert redacted["message"] == "Claude is waiting for your input"
    assert redacted["headers"]["Authorization"] == REDACTION_MARKER
    assert redacted["headers"]["Cookie"] == REDACTION_MARKER
    assert redacted["headers"]["X-Request-ID"] == "safe-routing-id"
    for key in (
        "authToken",
        "api_key",
        "webhook_url",
        "prompt",
        "raw",
        "tool_input",
        "toolResult",
        "tool_use_result",
        "command",
        "request",
        "response_body",
    ):
        assert redacted[key] == REDACTION_MARKER


def test_message_is_preserved_only_for_notification_classification() -> None:
    notification = redact_event_payload(
        {
            "hook_event_name": "Notification",
            "notification_type": "idle_prompt",
            "message": "Claude is waiting for your input",
        }
    )
    camel_notification = redact_event_payload(
        {
            "hookEventName": "notification",
            "notificationType": "idle_prompt",
            "message": "Turn complete",
        }
    )
    codex_stop = redact_event_payload(
        {
            "logged_at": "2026-08-12T12:00:00Z",
            "event": {
                "hook_event_name": "Stop",
                "message": "private assistant response",
            },
        }
    )

    assert notification["message"] == "Claude is waiting for your input"
    assert camel_notification["message"] == "Turn complete"
    assert codex_stop["event"]["message"] == REDACTION_MARKER
    for event_name in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
        redacted = redact_event_payload(
            {"hook_event_name": event_name, "message": "private event body"}
        )
        assert redacted["message"] == REDACTION_MARKER


def test_retention_removes_age_then_oldest_with_stable_path_tie(
    tmp_path: Path,
) -> None:
    root = tmp_path / "retained"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    nested.chmod(0o755)
    expired = root / "expired.jsonl"
    same_age_a = root / "a.jsonl"
    same_age_b = root / "b.jsonl"
    newest = root / "c.jsonl"
    for path, data, modified in (
        (expired, b"x", 100.0),
        (same_age_a, b"aaaa", 800.0),
        (same_age_b, b"bbbb", 800.0),
        (newest, b"cccc", 900.0),
    ):
        path.write_bytes(data)
        os.utime(path, (modified, modified))

    outside = tmp_path / "outside.jsonl"
    outside.write_text("must survive")
    outside.chmod(0o644)
    linked = root / "linked.jsonl"
    linked.symlink_to(outside)

    removed = enforce_retention(
        root,
        RetentionPolicy(
            max_age_seconds=500,
            max_files=2,
            max_total_bytes=8,
            now_epoch=1000.0,
        ),
    )

    assert removed == (expired, same_age_a)
    assert sorted(path.name for path in root.iterdir()) == [
        "b.jsonl",
        "c.jsonl",
        "linked.jsonl",
        "nested",
    ]
    assert outside.read_text() == "must survive"
    assert mode(outside) == 0o644
    assert mode(root) == 0o700
    assert mode(nested) == 0o700
    assert mode(same_age_b) == 0o600
    assert mode(newest) == 0o600


def test_retention_refuses_symlink_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        enforce_retention(linked_root, RetentionPolicy(max_files=0))

    assert (outside / "keep.txt").read_text() == "keep"


def test_tail_read_returns_newest_bytes_of_an_over_cap_file(tmp_path: Path) -> None:
    """tail=True on an over-cap file yields its newest max_bytes.

    The raising default silenced a provider for a day (2026-08-21): its
    events log crossed the collector's cap, every reconcile read raised,
    and the freshest events -- the only ones the collector wanted -- were
    exactly the bytes the head-read could never reach."""
    target = tmp_path / "log.jsonl"
    lines = [f"line-{index:04d}\n" for index in range(200)]
    atomic_private_write(target, "".join(lines))

    text = read_private_text(target, max_bytes=64, tail=True)
    assert len(text.encode("utf-8")) == 64
    assert text.endswith("line-0199\n")

    # The raising contract stays the default for callers that treat an
    # oversized file as corruption rather than history.
    with pytest.raises(OSError):
        read_private_text(target, max_bytes=64)

    # A file under the cap is returned whole in both modes.
    assert read_private_text(target, max_bytes=1_000_000, tail=True) == "".join(lines)


def test_log_slice_reads_only_appended_bytes(tmp_path: Path) -> None:
    """The cursor read that keeps per-event reconciles O(new data)."""
    target = tmp_path / "events.jsonl"
    atomic_private_write(target, "one\ntwo\n")

    text, cursor = read_private_log_slice(target, cursor=None, max_bytes=1_000)
    assert text == "one\ntwo\n"

    # Nothing new: empty slice, cursor unchanged.
    again, cursor2 = read_private_log_slice(target, cursor=cursor, max_bytes=1_000)
    assert again == ""
    assert cursor2 == cursor

    append_private_text(target, "three\n")
    fresh, cursor3 = read_private_log_slice(target, cursor=cursor, max_bytes=1_000)
    assert fresh == "three\n"

    # A torn trailing line is withheld and re-delivered whole.
    append_private_text(target, "fo")
    torn, cursor4 = read_private_log_slice(target, cursor=cursor3, max_bytes=1_000)
    assert torn == ""
    append_private_text(target, "ur\n")
    healed, _ = read_private_log_slice(target, cursor=cursor4, max_bytes=1_000)
    assert healed == "four\n"

    # Rotation (new inode) falls back to a fresh bounded tail.
    atomic_private_write(target, "rotated-a\nrotated-b\n")
    rotated, rotated_cursor = read_private_log_slice(
        target, cursor=cursor3, max_bytes=1_000
    )
    assert rotated == "rotated-a\nrotated-b\n"
    assert (rotated_cursor[0], rotated_cursor[1]) != (cursor3[0], cursor3[1])

    # Over-cap fresh start keeps only the newest bytes; the possibly
    # partial first line is the caller's parse-skip, same as tail reads.
    atomic_private_write(target, "x" * 50 + "\n" + "tail-line\n")
    capped, _ = read_private_log_slice(target, cursor=None, max_bytes=16)
    assert capped.endswith("tail-line\n")
    assert len(capped.encode("utf-8")) <= 16
