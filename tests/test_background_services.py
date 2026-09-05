import threading
from pathlib import Path

from sidepulse.intake_runtime import INTAKE_REASON_UNAVAILABLE, IntakeProbeService
from sidepulse.ledger_runtime import RemoteLedgerPublisher


def test_intake_probe_runs_off_caller_and_coalesces_identical_requests() -> None:
    release = threading.Event()
    completed = threading.Event()
    calls = []
    results = []

    def probe():
        calls.append(threading.current_thread().name)
        release.wait(1.0)
        return ("probe",)

    service = IntakeProbeService(probe)
    service.request(lambda result: results.append(("first", result)))
    service.request(lambda result: results.append(("middle", result)))
    service.request(
        lambda result: (results.append(("latest", result)), completed.set())
    )
    release.set()

    assert completed.wait(1.0)
    assert calls == ["SidePulseIntakeProbe"]
    assert [label for label, _result in results] == ["first", "middle", "latest"]
    assert all(result.probes == ("probe",) for _label, result in results)


def test_intake_failure_uses_a_closed_reason_code() -> None:
    completed = threading.Event()
    results = []
    service = IntakeProbeService(lambda: (_ for _ in ()).throw(OSError("private")))

    service.request(lambda result: (results.append(result), completed.set()))

    assert completed.wait(1.0)
    assert results[0].reason == INTAKE_REASON_UNAVAILABLE


def test_forced_intake_request_queues_one_fresh_probe_after_in_flight_work() -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    completed = threading.Event()
    calls: list[int] = []
    results: list[tuple[str, int]] = []

    def probe():
        call = len(calls) + 1
        calls.append(call)
        if call == 1:
            first_started.set()
            assert release_first.wait(1.0)
        return (call,)

    service = IntakeProbeService(probe)
    service.request(lambda result: results.append(("initial", result.probes[0])))
    assert first_started.wait(1.0)
    service.request(
        lambda result: (results.append(("forced", result.probes[0])), completed.set()),
        force=True,
    )
    release_first.set()

    assert completed.wait(1.0)
    assert calls == [1, 2]
    assert results == [("initial", 1), ("forced", 2)]


def test_remote_ledger_publication_is_latest_wins_and_off_caller() -> None:
    release = threading.Event()
    completed = threading.Event()
    calls = []
    results = []

    def publish(statuses, *, generated_at, settings):
        calls.append((statuses, generated_at, settings, threading.current_thread().name))
        if len(calls) == 1:
            release.wait(1.0)
        return Path(f"/tmp/{statuses[0]}.json")

    publisher = RemoteLedgerPublisher(publish)
    publisher.request(
        statuses=("first",),
        generated_at=1,
        settings="settings-1",
        signature="sig-1",
        callback=results.append,
    )
    publisher.request(
        statuses=("middle",),
        generated_at=2,
        settings="settings-2",
        signature="sig-2",
        callback=results.append,
    )
    publisher.request(
        statuses=("latest",),
        generated_at=3,
        settings="settings-3",
        signature="sig-3",
        callback=lambda result: (results.append(result), completed.set()),
    )
    release.set()

    assert completed.wait(1.0)
    assert [call[0] for call in calls] == [("first",), ("latest",)]
    assert all(call[3] == "SidePulseRemoteLedgerPublish" for call in calls)
    assert [result.request.signature for result in results] == ["sig-3"]
    assert results[0].path == Path("/tmp/latest.json")
