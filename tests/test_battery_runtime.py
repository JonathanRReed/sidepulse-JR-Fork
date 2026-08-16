import subprocess
import threading

from sidepulse.battery import (
    BATTERY_MODEL_TIMEOUT_SECONDS,
    BATTERY_READ_TIMEOUT_SECONDS,
    BatterySnapshot,
    default_full_charge_watts,
    read_battery_snapshot,
)
from sidepulse.battery_runtime import (
    BATTERY_REASON_TIMED_OUT,
    BatteryObservationService,
)


def test_battery_reader_has_a_strict_subprocess_timeout() -> None:
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    try:
        read_battery_snapshot(runner=runner)
    except subprocess.TimeoutExpired:
        pass

    assert calls[0][1]["timeout"] == BATTERY_READ_TIMEOUT_SECONDS


def test_hardware_model_probes_are_also_strictly_bounded() -> None:
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    default_full_charge_watts.cache_clear()
    assert default_full_charge_watts(runner=runner) == 100.0
    assert len(calls) == 2
    assert all(
        kwargs["timeout"] == BATTERY_MODEL_TIMEOUT_SECONDS
        for _args, kwargs in calls
    )


def test_request_returns_immediately_and_installs_result_asynchronously() -> None:
    release = threading.Event()
    completed = threading.Event()
    snapshot = BatterySnapshot(percent=64)

    def reader(**_kwargs):
        release.wait(1.0)
        return snapshot

    service = BatteryObservationService(reader=reader, minimum_interval=30.0)
    initial = service.request(
        full_charge_watts=None,
        callback=lambda _observation: completed.set(),
    )

    assert initial.snapshot is None
    assert initial.in_flight is True
    release.set()
    assert completed.wait(1.0)
    assert service.observation().snapshot == snapshot


def test_timeout_preserves_last_known_good_snapshot() -> None:
    calls = 0
    completed = threading.Event()
    first = BatterySnapshot(percent=72)

    def reader(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        raise subprocess.TimeoutExpired(["ioreg"], 2.0)

    service = BatteryObservationService(reader=reader, minimum_interval=0.1)
    service.request(
        full_charge_watts=None,
        callback=lambda _observation: completed.set(),
        force=True,
    )
    assert completed.wait(1.0)

    completed.clear()
    service.request(
        full_charge_watts=None,
        callback=lambda _observation: completed.set(),
        force=True,
    )
    assert completed.wait(1.0)

    observation = service.observation()
    assert observation.snapshot == first
    assert observation.reason == BATTERY_REASON_TIMED_OUT


def test_pending_requests_coalesce_to_the_latest_parameters() -> None:
    first_release = threading.Event()
    latest_complete = threading.Event()
    calls = []

    def reader(*, full_charge_watts=None):
        calls.append(full_charge_watts)
        if len(calls) == 1:
            first_release.wait(1.0)
        return BatterySnapshot(
            percent=50,
            full_charge_watts=full_charge_watts or 100.0,
        )

    service = BatteryObservationService(reader=reader, minimum_interval=30.0)
    service.request(full_charge_watts=90.0, force=True)
    service.request(full_charge_watts=100.0, force=True)
    service.request(
        full_charge_watts=140.0,
        callback=lambda _observation: latest_complete.set(),
        force=True,
    )

    first_release.set()
    assert latest_complete.wait(1.0)
    assert calls == [90.0, 140.0]
    assert service.observation().snapshot.full_charge_watts == 140.0
