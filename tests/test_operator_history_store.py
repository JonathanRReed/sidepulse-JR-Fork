from __future__ import annotations

import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.operator_history import HistoryCoverage, OperatorHistoryDay
from sidepulse.operator_history_store import (
    MAX_OPERATOR_HISTORY_ROWS,
    MAX_OPERATOR_HISTORY_STORE_BYTES,
    OperatorHistoryRestoreHealth,
    OperatorHistoryState,
    OperatorHistoryStore,
    default_operator_history_path,
    load_operator_history,
    save_operator_history,
)
from sidepulse.provider_contracts import ProviderIdentifier

NOW = 1_800_000_000.0
TODAY = datetime.fromtimestamp(NOW, timezone.utc).date()


def _day(
    age_days: int = 0,
    *,
    provider: str = "codex",
    offset: int = 0,
    completed: int = 1,
    failed: int = 0,
    coverage: HistoryCoverage = HistoryCoverage.COMPLETE,
    sample_count: int = 1,
) -> OperatorHistoryDay:
    day = date.fromordinal(TODAY.toordinal() - age_days)
    return OperatorHistoryDay(
        day.isoformat(),
        offset,
        ProviderIdentifier(provider),
        1,
        0,
        completed,
        failed,
        0,
        (1, 0, 0, 0),
        (0, 0, 0, 0),
        1,
        0,
        0,
        0,
        coverage,
        sample_count,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_default_store_uses_private_application_support_path(tmp_path: Path) -> None:
    assert default_operator_history_path(tmp_path) == (
        tmp_path / "Library" / "Application Support" / "SidePulse" / "operator-history.json"
    )


def test_zero_retention_is_disabled_and_creates_no_filesystem_state(tmp_path: Path) -> None:
    """The default disabled store must stay idle even when runtime facts arrive."""
    target = tmp_path / "Application Support" / "SidePulse" / "operator-history.json"
    store = OperatorHistoryStore(target, retention_days=0)

    assert not store.add_rows((_day(),))
    assert not store.flush(now=NOW)
    assert store.state == OperatorHistoryState()
    assert not store.dirty
    assert not target.parent.exists()


def test_saving_zero_retention_removes_an_existing_store_without_recreating_it(
    tmp_path: Path,
) -> None:
    """Persisting the disabled choice clears retained facts instead of writing an empty file."""
    target = tmp_path / "state" / "operator-history.json"
    save_operator_history(target, OperatorHistoryState((_day(),)), retention_days=7, now=NOW)

    state = save_operator_history(
        target,
        OperatorHistoryState((_day(),)),
        retention_days=0,
        now=NOW,
    )

    assert state == OperatorHistoryState()
    assert not target.exists()


def test_zero_retention_delete_wins_a_concurrent_store_flush(tmp_path: Path) -> None:
    """The disabled save path shares the merge lock so an in-flight flush cannot resurrect it."""
    target = tmp_path / "operator-history.json"
    store = OperatorHistoryStore(target, retention_days=7)
    store.add_rows((_day(),))
    store.flush(now=NOW)
    store.add_rows((_day(1, provider="claude"),))
    write_entered = threading.Event()
    release_write = threading.Event()
    delete_entered = threading.Event()

    from sidepulse import operator_history_store as history_store_module

    real_write = history_store_module.atomic_private_write
    real_unlink = history_store_module._secure_unlink

    def pausing_write(path: Path, data: str | bytes) -> Path:
        write_entered.set()
        assert release_write.wait(timeout=1.0)
        return real_write(path, data)

    def observed_unlink(path: Path) -> bool:
        delete_entered.set()
        return real_unlink(path)

    with (
        patch("sidepulse.operator_history_store.atomic_private_write", side_effect=pausing_write),
        patch("sidepulse.operator_history_store._secure_unlink", side_effect=observed_unlink),
    ):
        with ThreadPoolExecutor(max_workers=2) as executor:
            flush_future = executor.submit(store.flush, now=NOW)
            assert write_entered.wait(timeout=1.0)
            delete_future = executor.submit(
                save_operator_history,
                target,
                OperatorHistoryState(),
                retention_days=0,
                now=NOW,
            )
            delete_entered.wait(timeout=0.2)
            release_write.set()
            assert flush_future.result(timeout=2.0)
            assert delete_future.result(timeout=2.0) == OperatorHistoryState()

    assert not target.exists()


def test_enabled_round_trip_uses_exact_metadata_schema_and_private_modes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "operator-history.json"
    state = OperatorHistoryState((_day(1, provider="claude"), _day()))

    saved = save_operator_history(target, state, retention_days=7, now=NOW)
    restored = load_operator_history(target)

    assert restored.state == saved == state
    assert restored.health is OperatorHistoryRestoreHealth.HEALTHY
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600
    document = json.loads(target.read_text())
    assert set(document) == {"days", "version"}
    assert document["version"] == 1
    assert all(
        set(row)
        == {
            "acknowledged",
            "active_duration_bands",
            "attention_wait_bands",
            "completed",
            "coverage",
            "day_key",
            "device_recoveries",
            "failed",
            "needs_user",
            "primary_count",
            "provider_id",
            "sample_count",
            "source_recoveries",
            "started",
            "timezone_offset_minutes",
            "worker_count",
        }
        for row in document["days"]
    )


@pytest.mark.parametrize(
    "forbidden",
    (
        "semantic_event_key",
        "source_key",
        "work_key",
        "request_key",
        "identity_hash",
        "display_label",
        "session_title",
        "timeline",
        "prompt",
        "message",
        "command",
        "path",
        "raw_error",
        "email",
        "credential",
        "token",
        "cookie",
        "url",
        "navigation_target",
    ),
)
def test_load_rejects_forbidden_and_undeclared_fields(
    tmp_path: Path,
    forbidden: str,
) -> None:
    """Schema mutation cannot smuggle any identity, content, or action target into history."""
    target = tmp_path / "state" / "operator-history.json"
    save_operator_history(target, OperatorHistoryState((_day(),)), retention_days=7, now=NOW)
    document = json.loads(target.read_text())
    document["days"][0][forbidden] = "PRIVATE SENTINEL"
    target.write_text(json.dumps(document))

    restored = load_operator_history(target)

    assert restored.state == OperatorHistoryState()
    assert restored.health is OperatorHistoryRestoreHealth.CORRUPT
    diagnostic = target.with_name("operator-history.json.corrupt")
    assert json.loads(diagnostic.read_text()) == {
        "diagnostic": "operator_history_corrupt",
        "version": 1,
    }
    assert "PRIVATE SENTINEL" not in diagnostic.read_text()
    assert not target.exists()


def test_serialized_output_contains_no_runtime_identity_or_sentinel_corpus(
    tmp_path: Path,
) -> None:
    target = tmp_path / "operator-history.json"
    save_operator_history(target, OperatorHistoryState((_day(),)), retention_days=7, now=NOW)
    serialized = target.read_text().casefold()

    for forbidden in (
        "semantic_event_key",
        "source_key",
        "work_key",
        "request_key",
        "identity_hash",
        "display_label",
        "session_title",
        "timeline",
        "private sentinel",
        "/users/private/path",
        "person@example.com",
        "https://private.example",
        "raw error",
        "credential sentinel",
        "token sentinel",
        "cookie sentinel",
        "navigation target",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("retention_days", (7, 30, 90))
def test_supported_retention_keeps_today_and_drops_the_first_older_day(
    tmp_path: Path,
    retention_days: int,
) -> None:
    target = tmp_path / f"operator-history-{retention_days}.json"
    state = OperatorHistoryState(tuple(_day(age) for age in range(retention_days + 2)))

    saved = save_operator_history(
        target,
        state,
        retention_days=retention_days,
        now=NOW,
    )

    assert len(saved.rows) == retention_days
    assert saved.rows[-1].day_key == TODAY.isoformat()
    assert all(
        date.fromisoformat(row.day_key) >= date.fromordinal(TODAY.toordinal() - retention_days + 1)
        for row in saved.rows
    )


@pytest.mark.parametrize("retention_days", (-1, 1, 14, 91, True))
def test_unsupported_retention_fails_before_touching_disk(
    tmp_path: Path,
    retention_days: object,
) -> None:
    target = tmp_path / "operator-history.json"
    with pytest.raises(ValueError):
        save_operator_history(
            target,
            OperatorHistoryState((_day(),)),
            retention_days=retention_days,  # type: ignore[arg-type]
            now=NOW,
        )
    assert not target.exists()


def test_store_caps_90_days_by_32_provider_ids_and_two_mib(tmp_path: Path) -> None:
    """A hostile but valid provider matrix cannot grow the file past either bound."""
    providers = tuple(f"provider{index:02d}" for index in range(33))
    rows = tuple(_day(age, provider=provider) for age in range(91) for provider in providers)
    target = tmp_path / "operator-history.json"

    saved = save_operator_history(
        target,
        OperatorHistoryState(rows),
        retention_days=90,
        now=NOW,
    )

    assert len(saved.rows) <= MAX_OPERATOR_HISTORY_ROWS == 90 * 32
    assert len({row.provider_id for row in saved.rows}) <= 32
    assert target.stat().st_size <= MAX_OPERATOR_HISTORY_STORE_BYTES == 2 * 1024 * 1024


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
    target = tmp_path / "state" / "operator-history.json"
    target.parent.mkdir()
    if link_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)

    if operation == "load":
        restored = load_operator_history(target)
        assert restored.state == OperatorHistoryState()
        assert restored.health is OperatorHistoryRestoreHealth.UNAVAILABLE
    else:
        with pytest.raises(OSError):
            save_operator_history(
                target,
                OperatorHistoryState((_day(),)),
                retention_days=7,
                now=NOW,
            )

    assert outside.read_text() == "outside stays unchanged"
    assert _mode(outside) == 0o644


def test_load_uses_held_parent_during_leaf_open_parent_path_swap(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    outside = tmp_path / "outside"
    target = parent / "operator-history.json"
    inside_state = OperatorHistoryState((_day(),))
    outside_state = OperatorHistoryState((_day(1, provider="claude"),))
    save_operator_history(target, inside_state, retention_days=7, now=NOW)
    save_operator_history(outside / target.name, outside_state, retention_days=7, now=NOW)
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
        restored = load_operator_history(target)

    assert restored.state == inside_state
    assert load_operator_history(held_parent / target.name).state == inside_state
    assert load_operator_history(outside / target.name).state == outside_state


@pytest.mark.parametrize("operation", ("load", "save"))
def test_store_refuses_parent_swap_while_opening(tmp_path: Path, operation: str) -> None:
    parent = tmp_path / "state"
    outside = tmp_path / "outside"
    target = parent / "operator-history.json"
    state = OperatorHistoryState((_day(),))
    save_operator_history(target, state, retention_days=7, now=NOW)
    save_operator_history(outside / target.name, OperatorHistoryState(), retention_days=7, now=NOW)
    held_parent = parent.with_name("state-held")
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and Path(path) == parent:
            parent.rename(held_parent)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        if operation == "load":
            restored = load_operator_history(target)
            assert restored.state == OperatorHistoryState()
            assert restored.health is OperatorHistoryRestoreHealth.UNAVAILABLE
        else:
            with pytest.raises(OSError):
                save_operator_history(target, state, retention_days=7, now=NOW)

    assert load_operator_history(held_parent / target.name).state == state
    assert load_operator_history(outside / target.name).state == OperatorHistoryState()


def test_missing_corrupt_unsupported_and_oversize_are_visible_health_states(
    tmp_path: Path,
) -> None:
    missing = load_operator_history(tmp_path / "missing" / "operator-history.json")
    assert missing.health is OperatorHistoryRestoreHealth.MISSING

    target = tmp_path / "operator-history.json"
    target.write_text("not-json")
    corrupt = load_operator_history(target)
    assert corrupt.health is OperatorHistoryRestoreHealth.CORRUPT

    target.write_text('{"days":[],"version":2}')
    unsupported = load_operator_history(target)
    assert unsupported.health is OperatorHistoryRestoreHealth.UNSUPPORTED

    target.write_bytes(b"{" + b" " * MAX_OPERATOR_HISTORY_STORE_BYTES + b"}")
    oversize = load_operator_history(target)
    assert oversize.health is OperatorHistoryRestoreHealth.UNAVAILABLE


def test_file_growth_during_read_is_refused_without_retaining_payload(
    tmp_path: Path,
) -> None:
    target = tmp_path / "operator-history.json"
    save_operator_history(target, OperatorHistoryState((_day(),)), retention_days=7, now=NOW)
    real_read = os.read
    grew = False

    def growing_read(descriptor: int, size: int) -> bytes:
        nonlocal grew
        if not grew:
            with target.open("ab") as stream:
                stream.write(b"PRIVATE SENTINEL" + b"x" * MAX_OPERATOR_HISTORY_STORE_BYTES)
            grew = True
        return real_read(descriptor, size)

    with patch("sidepulse.private_io.os.read", side_effect=growing_read):
        restored = load_operator_history(target)

    assert restored.health is OperatorHistoryRestoreHealth.UNAVAILABLE
    diagnostic = target.with_name("operator-history.json.corrupt")
    assert "PRIVATE SENTINEL" not in diagnostic.read_text()


def test_atomic_replace_failure_preserves_previous_state(tmp_path: Path) -> None:
    target = tmp_path / "state" / "operator-history.json"
    previous = OperatorHistoryState((_day(),))
    replacement = OperatorHistoryState((_day(1, provider="claude"),))
    save_operator_history(target, previous, retention_days=7, now=NOW)
    previous_bytes = target.read_bytes()

    with patch(
        "sidepulse.private_io._replace_private_leaf",
        side_effect=OSError("injected replace failure"),
    ):
        with pytest.raises(OSError):
            save_operator_history(target, replacement, retention_days=7, now=NOW)

    assert target.read_bytes() == previous_bytes
    assert load_operator_history(target).state == previous


def test_failed_store_flush_remains_dirty_and_retries_exact_pending_rows(
    tmp_path: Path,
) -> None:
    target = tmp_path / "operator-history.json"
    store = OperatorHistoryStore(target, retention_days=7)
    store.add_rows((_day(),))

    with patch(
        "sidepulse.private_io._replace_private_leaf",
        side_effect=OSError("injected replace failure"),
    ):
        with pytest.raises(OSError):
            store.flush(now=NOW)

    assert store.dirty
    assert store.state == OperatorHistoryState((_day(),))
    assert store.flush(now=NOW)
    assert not store.dirty
    assert load_operator_history(target).state == OperatorHistoryState((_day(),))


def test_two_stale_store_instances_merge_concurrent_disjoint_rows(tmp_path: Path) -> None:
    """Read-modify-write under the store lock prevents a lost concurrent update."""
    target = tmp_path / "operator-history.json"
    first = OperatorHistoryStore(target, retention_days=7)
    second = OperatorHistoryStore(target, retention_days=7)
    first.restore()
    second.restore()
    first.add_rows((_day(),))
    second.add_rows((_day(1, provider="claude"),))

    assert first.flush(now=NOW)
    assert second.flush(now=NOW)

    assert load_operator_history(target).state == OperatorHistoryState((_day(1, provider="claude"), _day()))


def test_simultaneous_store_flushes_preserve_both_pending_batches(tmp_path: Path) -> None:
    """The real private read-merge-write boundary serializes simultaneous writers."""
    target = tmp_path / "operator-history.json"
    first = OperatorHistoryStore(target, retention_days=7)
    second = OperatorHistoryStore(target, retention_days=7)
    first.add_rows((_day(),))
    second.add_rows((_day(1, provider="claude"),))
    ready = threading.Barrier(2)

    def flush(store: OperatorHistoryStore) -> bool:
        ready.wait()
        return store.flush(now=NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(flush, (first, second)))

    assert results == (True, True)
    assert load_operator_history(target).state == OperatorHistoryState((_day(1, provider="claude"), _day()))


def test_corrupt_restore_projection_never_claims_clean_no_observation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "operator-history.json"
    target.write_text("not-json")
    store = OperatorHistoryStore(target, retention_days=7)

    restored = store.restore()
    projection = store.project(range_days=7, now=NOW)

    assert restored.health is OperatorHistoryRestoreHealth.CORRUPT
    assert projection.rows == ()
    assert projection.health_label == "History unavailable"
    assert projection.summary_sentences == ("Operator history could not be restored.",)


def test_exact_clear_removes_only_operator_history_and_clears_memory(tmp_path: Path) -> None:
    target = tmp_path / "operator-history.json"
    capacity = tmp_path / "capacity-history.json"
    capacity.write_text("capacity stays")
    store = OperatorHistoryStore(target, retention_days=7)
    store.add_rows((_day(),))
    store.flush(now=NOW)

    assert store.clear()

    assert store.state == OperatorHistoryState()
    assert not store.dirty
    assert not target.exists()
    assert capacity.read_text() == "capacity stays"


def test_clear_failure_preserves_in_memory_truth_and_disk_for_retry(tmp_path: Path) -> None:
    target = tmp_path / "operator-history.json"
    store = OperatorHistoryStore(target, retention_days=7)
    store.add_rows((_day(),))
    store.flush(now=NOW)

    with patch(
        "sidepulse.operator_history_store._secure_unlink",
        side_effect=OSError("injected clear failure"),
    ):
        with pytest.raises(OSError):
            store.clear()

    assert store.state == OperatorHistoryState((_day(),))
    assert target.exists()


def test_clear_wins_a_flush_that_already_observed_dirty_state(tmp_path: Path) -> None:
    """A concurrent consent clear cannot be followed by a stale flush recreating the file."""
    target = tmp_path / "operator-history.json"
    store = OperatorHistoryStore(target, retention_days=7)
    store.add_rows((_day(),))
    store.flush(now=NOW)
    store.add_rows((_day(1, provider="claude"),))
    clear_holds_lock = threading.Event()
    release_clear = threading.Event()
    flush_started = threading.Event()

    from sidepulse import operator_history_store as history_store_module

    real_unlink = history_store_module._secure_unlink

    def pausing_unlink(path: Path) -> bool:
        clear_holds_lock.set()
        assert release_clear.wait(timeout=1.0)
        return real_unlink(path)

    def observed_valid_now(value: object) -> bool:
        flush_started.set()
        return value == NOW

    with (
        patch("sidepulse.operator_history_store._secure_unlink", side_effect=pausing_unlink),
        patch("sidepulse.operator_history_store._valid_now", side_effect=observed_valid_now),
    ):
        with ThreadPoolExecutor(max_workers=2) as executor:
            clear_future = executor.submit(store.clear)
            assert clear_holds_lock.wait(timeout=1.0)
            flush_future = executor.submit(store.flush, now=NOW)
            assert flush_started.wait(timeout=1.0)
            release_clear.set()
            assert clear_future.result(timeout=2.0)
            assert not flush_future.result(timeout=2.0)

    assert store.state == OperatorHistoryState()
    assert not store.dirty
    assert not target.exists()


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_clear_refuses_linked_history_without_touching_outside_file(
    tmp_path: Path,
    link_kind: str,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("outside stays")
    target = tmp_path / "state" / "operator-history.json"
    target.parent.mkdir()
    if link_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)
    store = OperatorHistoryStore(target, retention_days=7)

    with pytest.raises(OSError):
        store.clear()

    assert outside.read_text() == "outside stays"
