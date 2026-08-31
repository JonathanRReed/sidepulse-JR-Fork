from __future__ import annotations

import threading

from sidepulse.provider_usage_runtime import ProviderUsageState
from sidepulse.provider_usage_sync_runtime import ProviderSyncRefresh
from sidepulse.provider_usage_sync_service import ProviderSyncService


class Runtime:
    def __init__(self, gate=None):
        self.calls = 0
        self.gate = gate

    def refresh(self, state):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(2)
        return ProviderSyncRefresh(
            enabled=True,
            local_packet=None,
            remote_packets=(),
            merged=None,
            health=(),
            refreshed_at=1000,
        )


def test_request_runs_off_caller_thread_and_coalesces():
    gate = threading.Event()
    runtime = Runtime(gate)
    service = ProviderSyncService(runtime)
    callbacks = []
    state = ProviderUsageState((), None, None, False)

    first = service.request(state, callback=callbacks.append, force=True)
    second = service.request(state, callback=callbacks.append, force=True)

    assert first.refreshing is True
    assert second.refreshing is True
    gate.set()
    assert service.wait_idle(timeout_seconds=1.0)
    assert runtime.calls == 1
    assert len(callbacks) == 2
    assert callbacks and callbacks[0].refreshing is False
    assert service.close(timeout_seconds=1.0)


def test_request_after_worker_capture_runs_latest_state():
    class RecordingRuntime:
        def __init__(self):
            self.states = []
            self.started = threading.Event()
            self.gate = threading.Event()

        def refresh(self, state):
            self.states.append(state)
            self.started.set()
            if len(self.states) == 1:
                self.gate.wait(2)
            return ProviderSyncRefresh(
                enabled=True,
                local_packet=None,
                remote_packets=(),
                merged=None,
                health=(),
                refreshed_at=len(self.states),
            )

    runtime = RecordingRuntime()
    service = ProviderSyncService(runtime)
    callbacks = []
    first = ProviderUsageState((), None, None, False)
    second = ProviderUsageState((), None, None, True)

    def callback(result):
        callbacks.append(result)

    try:
        service.request(first, callback=callback, force=True)
        assert runtime.started.wait(1)
        service.request(second, callback=callback, force=True)
        runtime.gate.set()

        assert service.wait_idle(timeout_seconds=1.0)
        assert runtime.states == [first, second]
        # Requests share the latest refresh, while each accepted callback
        # is delivered exactly once with that final result.
        assert [result.refresh.refreshed_at for result in callbacks] == [2, 2]
    finally:
        service.close(timeout_seconds=1.0)


def test_identical_request_after_worker_capture_reuses_inflight_refresh():
    class RecordingRuntime:
        def __init__(self):
            self.calls = 0
            self.started = threading.Event()
            self.gate = threading.Event()

        def refresh(self, _state):
            self.calls += 1
            self.started.set()
            self.gate.wait(2)
            return ProviderSyncRefresh(True, None, (), None, (), self.calls)

    runtime = RecordingRuntime()
    service = ProviderSyncService(runtime)
    callbacks = []
    state = ProviderUsageState((), None, None, False)

    try:
        service.request(state, callback=callbacks.append, force=True)
        assert runtime.started.wait(1)
        service.request(state, callback=callbacks.append, force=True)
        runtime.gate.set()

        assert service.wait_idle(timeout_seconds=1.0)
        assert runtime.calls == 1
        assert [result.refresh.refreshed_at for result in callbacks] == [1, 1]
    finally:
        service.close(timeout_seconds=1.0)


def test_request_during_callback_delivery_triggers_a_fresh_sync_run():
    class RecordingRuntime:
        def __init__(self):
            self.calls = 0

        def refresh(self, _state):
            self.calls += 1
            return ProviderSyncRefresh(True, None, (), None, (), self.calls)

    runtime = RecordingRuntime()
    service = ProviderSyncService(runtime)
    callbacks = []
    second_delivered = threading.Event()
    first_delivery = threading.Event()

    def second_callback(result):
        callbacks.append(("second", result.refresh.refreshed_at))
        second_delivered.set()

    def first_callback(result):
        callbacks.append(("first", result.refresh.refreshed_at))
        first_delivery.set()
        service.request(
            ProviderUsageState((), None, None, True),
            callback=second_callback,
            force=True,
        )

    try:
        service.request(
            ProviderUsageState((), None, None, False),
            callback=first_callback,
            force=True,
        )

        assert first_delivery.wait(1.0)
        assert second_delivered.wait(1.0)
        assert service.wait_idle(timeout_seconds=1.0)
        assert runtime.calls == 2
        assert callbacks == [("first", 1), ("second", 2)]
    finally:
        service.close(timeout_seconds=1.0)


def test_close_reports_when_in_flight_worker_exceeds_timeout():
    started = threading.Event()
    gate = threading.Event()

    class BlockingRuntime:
        def refresh(self, _state):
            started.set()
            gate.wait(2)
            return ProviderSyncRefresh(True, None, (), None, (), 1000)

    service = ProviderSyncService(BlockingRuntime())
    state = ProviderUsageState((), None, None, False)
    service.request(state, callback=lambda _value: None, force=True)
    assert started.wait(1)

    assert service.wait_idle(timeout_seconds=0.0) is False
    assert service.close(timeout_seconds=0.0) is False
    gate.set()
    assert service.close(timeout_seconds=1.0) is True
    assert service.snapshot().closed is True


def test_runtime_error_becomes_bounded_failure():
    class Broken:
        def refresh(self, _state):
            raise RuntimeError("private error detail")

    service = ProviderSyncService(Broken())
    result = service.refresh_now(ProviderUsageState((), None, None, False))
    assert result.refreshing is False
    assert result.refresh is None
    assert result.reason == "sync_refresh_failed"


def test_close_prevents_new_work():
    runtime = Runtime()
    service = ProviderSyncService(runtime)
    service.close()
    result = service.request(
        ProviderUsageState((), None, None, False),
        callback=lambda _value: None,
        force=True,
    )
    assert result.closed is True
    assert runtime.calls == 0
