from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sidepulse.transcript_runtime import TranscriptFallbackService


@dataclass(frozen=True)
class _Record:
    logged_at: datetime


class _BlockingMonitor:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.signature_calls = 0
        self.record_calls = 0

    def input_signature(self):
        self.signature_calls += 1
        self.release.wait(1.0)
        return "changed"

    def iter_records(self):
        self.record_calls += 1
        return iter((_Record(datetime.now(timezone.utc)),))


def test_identical_inflight_requests_share_one_scan_and_all_callbacks() -> None:
    monitor = _BlockingMonitor()
    service = TranscriptFallbackService()
    completed = threading.Event()
    callbacks = []

    service.request(
        monitor,
        known_signature="old",
        callback=lambda batch: callbacks.append(("first", batch.signature)),
    )
    service.request(
        monitor,
        known_signature="old",
        callback=lambda batch: (
            callbacks.append(("second", batch.signature)),
            completed.set(),
        ),
    )
    monitor.release.set()

    assert completed.wait(1.0)
    assert monitor.signature_calls == 1
    assert monitor.record_calls == 1
    assert callbacks == [("first", "changed"), ("second", "changed")]
    service.close()


def test_close_suppresses_an_inflight_callback() -> None:
    monitor = _BlockingMonitor()
    service = TranscriptFallbackService()
    called = threading.Event()

    service.request(
        monitor,
        known_signature="old",
        callback=lambda _batch: called.set(),
    )
    service.close()
    monitor.release.set()

    assert not called.wait(0.2)
