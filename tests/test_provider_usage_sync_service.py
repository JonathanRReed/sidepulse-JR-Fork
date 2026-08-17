from __future__ import annotations

import threading
import time

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
    deadline = time.time() + 3
    while time.time() < deadline and not callbacks:
        time.sleep(0.01)
    assert runtime.calls == 1
    assert callbacks and callbacks[0].refreshing is False
    service.close()


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
