from __future__ import annotations

import threading

import pytest

from sidepulse.persistence_writer import (
    PersistenceDisposition,
    PersistenceOutcome,
    SerialPersistenceWriter,
)


def test_writer_executes_fifo_on_one_noncaller_thread_and_drains() -> None:
    release = threading.Event()
    started = threading.Event()
    calls: list[tuple[str, str]] = []
    receipts = []

    def first() -> str:
        calls.append(("first", threading.current_thread().name))
        started.set()
        release.wait(2.0)
        return "one"

    def second() -> str:
        calls.append(("second", threading.current_thread().name))
        return "two"

    writer = SerialPersistenceWriter(receipt_handler=receipts.append)
    assert writer.submit("first", first) is PersistenceDisposition.STARTED
    assert started.wait(1.0)
    assert writer.submit("second", second) is PersistenceDisposition.QUEUED

    release.set()
    assert writer.close(timeout_seconds=1.0) is True

    assert [name for name, _thread in calls] == ["first", "second"]
    assert len({thread for _name, thread in calls}) == 1
    assert calls[0][1] != threading.current_thread().name
    assert [receipt.outcome for receipt in receipts] == [
        PersistenceOutcome.SUCCEEDED,
        PersistenceOutcome.SUCCEEDED,
    ]
    assert [receipt.result for receipt in receipts] == ["one", "two"]


def test_latest_snapshot_replacement_moves_to_fifo_tail() -> None:
    release = threading.Event()
    started = threading.Event()
    calls: list[str] = []
    receipts = []

    def running() -> None:
        calls.append("running")
        started.set()
        release.wait(2.0)

    writer = SerialPersistenceWriter(receipt_handler=receipts.append)
    writer.submit("running", running)
    assert started.wait(1.0)
    writer.submit("reset-events", lambda: calls.append("old-reset"))
    writer.submit("percent-append", lambda: calls.append("append"))

    disposition = writer.submit(
        "reset-events",
        lambda: calls.append("new-reset"),
        replace_pending=True,
    )

    assert disposition is PersistenceDisposition.REPLACED_PENDING
    release.set()
    assert writer.close(timeout_seconds=1.0) is True
    assert calls == ["running", "append", "new-reset"]
    replaced = [
        receipt for receipt in receipts if receipt.outcome is PersistenceOutcome.REPLACED
    ]
    assert len(replaced) == 1
    assert replaced[0].key == "reset-events"


def test_ordered_appends_never_coalesce() -> None:
    release = threading.Event()
    started = threading.Event()
    calls: list[int] = []

    def running() -> None:
        started.set()
        release.wait(2.0)

    writer = SerialPersistenceWriter()
    writer.submit("running", running)
    assert started.wait(1.0)
    writer.submit("percent-append", lambda: calls.append(1))
    writer.submit("percent-append", lambda: calls.append(2))
    release.set()

    assert writer.close(timeout_seconds=1.0) is True
    assert calls == [1, 2]


def test_full_and_closed_writer_refuse_without_executing() -> None:
    release = threading.Event()
    started = threading.Event()
    calls: list[str] = []

    def running() -> None:
        started.set()
        release.wait(2.0)

    writer = SerialPersistenceWriter(max_pending=1)
    writer.submit("running", running)
    assert started.wait(1.0)
    assert (
        writer.submit("queued", lambda: calls.append("queued"))
        is PersistenceDisposition.QUEUED
    )
    assert (
        writer.submit("refused", lambda: calls.append("refused"))
        is PersistenceDisposition.REFUSED_FULL
    )
    release.set()
    assert writer.close(timeout_seconds=1.0) is True
    assert (
        writer.submit("closed", lambda: calls.append("closed"))
        is PersistenceDisposition.REFUSED_CLOSED
    )
    assert calls == ["queued"]


def test_one_reserved_drain_tail_can_exceed_a_full_queue() -> None:
    release = threading.Event()
    started = threading.Event()
    calls: list[str] = []

    def running() -> None:
        started.set()
        release.wait(2.0)

    writer = SerialPersistenceWriter(max_pending=1)
    writer.submit("running", running)
    assert started.wait(1.0)
    assert (
        writer.submit("queued", lambda: calls.append("queued"))
        is PersistenceDisposition.QUEUED
    )
    assert (
        writer.submit(
            "drain-tail",
            lambda: calls.append("drain-tail"),
            use_reserved_drain_tail=True,
        )
        is PersistenceDisposition.QUEUED
    )
    assert writer.snapshot().pending_count == 2
    assert writer.snapshot().reserved_drain_tail == 1
    assert (
        writer.submit(
            "second-tail",
            lambda: calls.append("second-tail"),
            use_reserved_drain_tail=True,
        )
        is PersistenceDisposition.REFUSED_FULL
    )

    release.set()
    assert writer.close(timeout_seconds=1.0) is True
    assert calls == ["queued", "drain-tail"]


def test_failure_receipt_is_content_free_and_later_work_continues() -> None:
    receipts = []
    calls: list[str] = []

    def fail() -> None:
        raise RuntimeError("private payload must not surface")

    writer = SerialPersistenceWriter(receipt_handler=receipts.append)
    writer.submit("broken", fail)
    writer.submit("later", lambda: calls.append("later"))

    assert writer.close(timeout_seconds=1.0) is True
    assert calls == ["later"]
    assert receipts[0].outcome is PersistenceOutcome.FAILED
    assert receipts[0].error_code == "operation_failed"
    assert "private payload" not in repr(receipts[0])
    assert receipts[1].outcome is PersistenceOutcome.SUCCEEDED


def test_close_timeout_is_bounded_but_worker_finishes_after_release() -> None:
    release = threading.Event()
    started = threading.Event()

    def blocked() -> None:
        started.set()
        release.wait(2.0)

    writer = SerialPersistenceWriter()
    writer.submit("blocked", blocked)
    assert started.wait(1.0)

    assert writer.close(timeout_seconds=0.01) is False
    release.set()
    assert writer.close(timeout_seconds=1.0) is True
    assert writer.snapshot().thread_alive is False


@pytest.mark.parametrize("key", ("", ".", "UPPER", "bad key", "../escape"))
def test_command_keys_are_narrow_and_normalized(key: str) -> None:
    writer = SerialPersistenceWriter()
    with pytest.raises(ValueError, match="invalid persistence key"):
        writer.submit(key, lambda: None)
    assert writer.close(timeout_seconds=0.0) is True
