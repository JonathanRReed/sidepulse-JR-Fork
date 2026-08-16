import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sidepulse.transcript_runtime import (
    MAX_TRANSCRIPT_BATCH_RECORDS,
    TRANSCRIPT_REASON_INVALID_MONITOR,
    TranscriptFallbackService,
)


@dataclass(frozen=True)
class _Record:
    logged_at: datetime
    value: int


class _Monitor:
    def __init__(self, signature, records, release=None) -> None:
        self.signature = signature
        self.records = records
        self.release = release
        self.thread_names = []

    def input_signature(self):
        self.thread_names.append(threading.current_thread().name)
        if self.release is not None:
            self.release.wait(1.0)
        return self.signature

    def iter_records(self):
        self.thread_names.append(threading.current_thread().name)
        return iter(self.records)


def _record(value: int) -> _Record:
    return _Record(datetime.fromtimestamp(value, timezone.utc), value)


def test_scan_runs_off_caller_and_sorts_records() -> None:
    completed = threading.Event()
    batches = []
    monitor = _Monitor("changed", [_record(3), _record(1), _record(2)])
    service = TranscriptFallbackService()

    service.request(
        monitor,
        known_signature="old",
        callback=lambda batch: (batches.append(batch), completed.set()),
    )

    assert completed.wait(1.0)
    assert tuple(record.value for record in batches[0].records) == (1, 2, 3)
    assert all(name == "SidePulseTranscriptFallback" for name in monitor.thread_names)


def test_unchanged_signature_skips_record_iteration() -> None:
    completed = threading.Event()
    monitor = _Monitor("same", [_record(1)])
    service = TranscriptFallbackService()
    batches = []

    service.request(
        monitor,
        known_signature="same",
        callback=lambda batch: (batches.append(batch), completed.set()),
    )

    assert completed.wait(1.0)
    assert batches[0].records == ()
    assert monitor.thread_names == ["SidePulseTranscriptFallback"]


def test_inflight_requests_collapse_to_the_latest_monitor() -> None:
    release = threading.Event()
    completed = threading.Event()
    first = _Monitor("first", [_record(1)], release=release)
    middle = _Monitor("middle", [_record(2)])
    latest = _Monitor("latest", [_record(3)])
    batches = []
    service = TranscriptFallbackService()

    service.request(first, known_signature=None, callback=batches.append)
    service.request(middle, known_signature=None, callback=batches.append)
    service.request(
        latest,
        known_signature=None,
        callback=lambda batch: (batches.append(batch), completed.set()),
    )
    release.set()

    assert completed.wait(1.0)
    assert [batch.signature for batch in batches] == ["latest"]
    assert middle.thread_names == []


def test_batch_is_bounded_to_the_newest_records() -> None:
    completed = threading.Event()
    records = [_record(index) for index in range(MAX_TRANSCRIPT_BATCH_RECORDS + 10)]
    monitor = _Monitor("changed", records)
    batches = []
    service = TranscriptFallbackService()

    service.request(
        monitor,
        known_signature=None,
        callback=lambda batch: (batches.append(batch), completed.set()),
    )

    assert completed.wait(1.0)
    assert len(batches[0].records) == MAX_TRANSCRIPT_BATCH_RECORDS
    assert batches[0].records[0].value == 10


def test_invalid_monitor_fails_with_a_closed_reason_code() -> None:
    completed = threading.Event()
    batches = []
    service = TranscriptFallbackService()

    service.request(
        object(),
        known_signature=None,
        callback=lambda batch: (batches.append(batch), completed.set()),
    )

    assert completed.wait(1.0)
    assert batches[0].reason == TRANSCRIPT_REASON_INVALID_MONITOR
