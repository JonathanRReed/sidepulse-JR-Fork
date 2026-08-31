from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

import pytest

import sidepulse.runtime_scheduler as runtime_scheduler
from sidepulse.runtime_scheduler import (
    MAX_RUNTIME_METRIC_COUNT,
    AppKitTimerRegistry,
    LatestWinsWorker,
    RuntimeFeature,
    RuntimeTimerIntent,
    RuntimeWorkCommand,
    RuntimeWorkerDomain,
    RuntimeWorkerRegistry,
    RuntimeWorkerSnapshot,
    RuntimeWorkPriority,
    SubmissionDisposition,
)


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _FakeTimer:
    def __init__(
        self,
        *,
        delay: float,
        interval: float | None,
        target: object,
        selector: str,
        user_info: RuntimeFeature,
    ) -> None:
        self.delay = delay
        self.interval = interval
        self.target = target
        self.selector = selector
        self._user_info = user_info
        self.tolerance: float | None = None
        self.invalidations = 0

    def userInfo(self) -> RuntimeFeature:
        return self._user_info

    def setTolerance_(self, tolerance: float) -> None:
        self.tolerance = tolerance

    def invalidate(self) -> None:
        self.invalidations += 1


class _FakeAppKitFactory:
    def __init__(self) -> None:
        self.created: list[_FakeTimer] = []
        self.registrations: list[tuple[_FakeTimer, bool]] = []
        self.touched_threads: list[int] = []

    def create_timer(
        self,
        *,
        delay: float,
        interval: float | None,
        target: object,
        selector: str,
        user_info: RuntimeFeature,
    ) -> _FakeTimer:
        self.touched_threads.append(threading.get_ident())
        timer = _FakeTimer(
            delay=delay,
            interval=interval,
            target=target,
            selector=selector,
            user_info=user_info,
        )
        self.created.append(timer)
        return timer

    def register_timer(self, timer: _FakeTimer, *, common_modes: bool) -> None:
        self.touched_threads.append(threading.get_ident())
        self.registrations.append((timer, common_modes))


def _intent(
    feature: RuntimeFeature,
    *,
    fire_at: float = 101.0,
    interval: float | None = None,
    tolerance: float = 0.0,
    common_modes: bool = True,
) -> RuntimeTimerIntent:
    return RuntimeTimerIntent(feature, fire_at, interval, tolerance, common_modes)


def _command(
    domain: RuntimeWorkerDomain,
    key: str,
    generation: int,
    *,
    deadline: float | None = None,
    payload: object | None = None,
    priority: RuntimeWorkPriority = RuntimeWorkPriority.COALESCIBLE,
    coalesce_key: str | None = None,
) -> RuntimeWorkCommand:
    effective_deadline = time.monotonic() + 10_000.0 if deadline is None else deadline
    return RuntimeWorkCommand(
        domain,
        key,
        generation,
        effective_deadline,
        payload,
        priority,
        coalesce_key,
    )


def test_timer_intents_reject_invalid_or_ambiguous_schedules() -> None:
    clock = _Clock()
    factory = _FakeAppKitFactory()
    registry = AppKitTimerRegistry(
        handlers={feature: lambda: None for feature in RuntimeFeature},
        timer_factory=factory,
        monotonic=clock,
    )
    target = object()

    with pytest.raises(ValueError, match="feature"):
        RuntimeTimerIntent("unknown", 101.0, None, 0.0, True)  # type: ignore[arg-type]
    for invalid in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="fire_at"):
            _intent(RuntimeFeature.CAPACITY_DEADLINE, fire_at=invalid)
    with pytest.raises(ValueError, match="future"):
        registry.reconcile((_intent(RuntimeFeature.CAPACITY_DEADLINE, fire_at=100.0),), target=target)
    for interval in (0.0, -1.0, math.nan, math.inf):
        with pytest.raises(ValueError, match="interval"):
            _intent(RuntimeFeature.LID_OBSERVATION, interval=interval, tolerance=0.1)
    with pytest.raises(ValueError, match="tolerance"):
        _intent(RuntimeFeature.LID_OBSERVATION, interval=1.0, tolerance=1.01)
    with pytest.raises(ValueError, match="tolerance"):
        _intent(RuntimeFeature.LID_OBSERVATION, interval=1.0, tolerance=0.0)
    assert (
        _intent(
            RuntimeFeature.PRESENTATION_FRAME_FALLBACK,
            interval=1.0 / 60.0,
            tolerance=0.0,
        ).tolerance
        == 0.0
    )
    duplicate = _intent(RuntimeFeature.CAPACITY_DEADLINE)
    with pytest.raises(ValueError, match="duplicate"):
        registry.reconcile((duplicate, duplicate), target=target)
    assert factory.created == []


def test_timer_reconcile_is_idempotent_and_common_mode_registration_is_stable() -> None:
    clock = _Clock()
    factory = _FakeAppKitFactory()
    callbacks: list[str] = []
    feature = RuntimeFeature.DISPLAY_ENVIRONMENT
    registry = AppKitTimerRegistry(
        handlers={feature: lambda: callbacks.append("display")},
        timer_factory=factory,
        monotonic=clock,
    )
    target = object()
    intent = _intent(feature, fire_at=101.0, interval=3.0, tolerance=0.25)

    for _ in range(100):
        registry.reconcile((intent,), target=target)

    snapshot = registry.snapshot()
    assert snapshot.active_features == (feature,)
    assert snapshot.created == 1
    assert snapshot.invalidated == 0
    assert snapshot.callback_count(feature) == 0
    assert len(factory.created) == 1
    assert factory.created[0].selector == "runtimeTimerFired:"
    assert factory.created[0].target is target
    assert factory.created[0].tolerance == 0.25
    assert factory.registrations == [(factory.created[0], True)]


def test_timer_withdrawal_invalidates_only_the_withdrawn_feature() -> None:
    clock = _Clock()
    factory = _FakeAppKitFactory()
    first = RuntimeFeature.LID_OBSERVATION
    second = RuntimeFeature.DEVICE_INVENTORY
    registry = AppKitTimerRegistry(
        handlers={first: lambda: None, second: lambda: None},
        timer_factory=factory,
        monotonic=clock,
    )
    target = object()
    registry.reconcile(
        (
            _intent(first, interval=1.0, tolerance=0.1),
            _intent(second, interval=2.0, tolerance=0.2),
        ),
        target=target,
    )

    registry.reconcile(
        (_intent(second, interval=2.0, tolerance=0.2),),
        target=target,
    )

    assert factory.created[0].invalidations == 1
    assert factory.created[1].invalidations == 0
    assert registry.snapshot().active_features == (second,)


@pytest.mark.parametrize(("callback_offset", "counter"), [(-0.25, "early"), (0.25, "late")])
def test_one_shot_is_removed_before_early_or_late_handler_reconciliation(
    callback_offset: float,
    counter: str,
) -> None:
    clock = _Clock()
    factory = _FakeAppKitFactory()
    feature = RuntimeFeature.FINITE_CUE_DEADLINE
    target = object()
    observed_active: list[tuple[RuntimeFeature, ...]] = []
    registry: AppKitTimerRegistry

    def handler() -> None:
        observed_active.append(registry.snapshot().active_features)
        registry.reconcile((_intent(feature, fire_at=clock() + 1.0),), target=target)

    registry = AppKitTimerRegistry(
        handlers={feature: handler},
        timer_factory=factory,
        monotonic=clock,
    )
    registry.reconcile((_intent(feature),), target=target)
    fired = factory.created[0]
    clock.value = 101.0 + callback_offset

    registry.dispatch(fired)

    snapshot = registry.snapshot()
    assert observed_active == [()]
    assert fired.invalidations == 1
    assert snapshot.active_features == (feature,)
    assert snapshot.fired == 1
    assert snapshot.callback_count(feature) == 1
    assert getattr(snapshot, counter) == 1
    assert len(factory.created) == 2


def test_timer_public_operations_fail_off_main_before_touching_appkit() -> None:
    clock = _Clock()
    factory = _FakeAppKitFactory()
    feature = RuntimeFeature.CORE_REFRESH_FALLBACK
    registry = AppKitTimerRegistry(
        handlers={feature: lambda: None},
        timer_factory=factory,
        monotonic=clock,
    )
    target = object()
    errors: list[type[BaseException]] = []

    def off_main() -> None:
        operations = (
            lambda: registry.reconcile((_intent(feature),), target=target),
            lambda: registry.invalidate(feature),
            registry.invalidate_all,
            registry.snapshot,
            lambda: registry.dispatch(
                _FakeTimer(
                    delay=1.0,
                    interval=None,
                    target=target,
                    selector="runtimeTimerFired:",
                    user_info=feature,
                )
            ),
        )
        for operation in operations:
            try:
                operation()
            except BaseException as exc:
                errors.append(type(exc))

    worker = threading.Thread(target=off_main)
    worker.start()
    worker.join(1.0)

    assert not worker.is_alive()
    assert errors == [RuntimeError] * 5
    assert factory.created == []
    assert factory.registrations == []
    registry.reconcile((_intent(feature),), target=target)
    assert factory.touched_threads == [threading.get_ident(), threading.get_ident()]


def test_runtime_work_command_rejects_private_or_unbounded_keys() -> None:
    domain = RuntimeWorkerDomain.SCREEN_BAR_SAMPLER
    invalid_keys = (
        "",
        "a" * 129,
        "/private/value",
        "../private",
        "folder/child",
        r"folder\child",
        "~/private",
        f"folder{chr(0xFF0F)}child",
        ".",
        "..",
    )
    for key in invalid_keys:
        with pytest.raises(ValueError, match="key"):
            _command(domain, key, 1)
    normalized = _command(domain, "Sampler:MAIN_1", 1)
    assert normalized.key == "sampler:main_1"
    with pytest.raises(ValueError, match="generation"):
        _command(domain, "main", 0)
    with pytest.raises(ValueError, match="deadline"):
        _command(domain, "main", 1, deadline=math.inf)
    with pytest.raises(ValueError):
        RuntimeWorkerDomain("provider_source")
    with pytest.raises(ValueError, match="priority"):
        RuntimeWorkCommand(domain, "main", 1, time.monotonic() + 1.0, None, 10)
    with pytest.raises(ValueError, match="coalescing"):
        _command(domain, "main", 1, coalesce_key="private/path")


def test_latest_wins_worker_preserves_priority_slot_and_final_trailing_state() -> None:
    started = threading.Event()
    release = threading.Event()
    executions: list[str] = []
    dispatched: list[Callable[[], None]] = []
    delivered: list[str] = []

    def execute(command: RuntimeWorkCommand) -> object:
        executions.append(str(command.payload))
        if command.payload == "blocker":
            started.set()
            assert release.wait(2.0)
        return command.payload

    worker = LatestWinsWorker(
        RuntimeWorkerDomain.HARDWARE_WRITE,
        executor=execute,
        result_handler=lambda _command, result: delivered.append(str(result)),
        dispatch_main=dispatched.append,
    )
    assert (
        worker.submit(
            _command(
                RuntimeWorkerDomain.HARDWARE_WRITE,
                "device-a",
                1,
                payload="blocker",
                coalesce_key="device-a:latest",
            )
        )
        is SubmissionDisposition.STARTED
    )
    assert started.wait(1.0)
    assert (
        worker.submit(
            _command(
                RuntimeWorkerDomain.HARDWARE_WRITE,
                "device-a",
                1,
                payload="ask",
                priority=RuntimeWorkPriority.IMPORTANT,
                coalesce_key="device-a:semantic-attention",
            )
        )
        is SubmissionDisposition.QUEUED
    )
    assert (
        worker.submit(
            _command(
                RuntimeWorkerDomain.HARDWARE_WRITE,
                "device-a",
                1,
                payload="final",
                coalesce_key="device-a:latest",
            )
        )
        is SubmissionDisposition.QUEUED
    )
    assert worker.snapshot().pending_count == 2

    release.set()
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed == 3
    assert executions == ["blocker", "ask", "final"]
    assert len(dispatched) == 1
    dispatched.pop()()
    assert delivered == ["ask", "final"]
    assert worker.close(timeout_seconds=1.0)


def test_latest_wins_worker_replaces_only_matching_priority_slot() -> None:
    started = threading.Event()
    release = threading.Event()
    executions: list[str] = []
    worker = LatestWinsWorker(
        RuntimeWorkerDomain.HARDWARE_WRITE,
        executor=lambda command: (
            executions.append(str(command.payload)),
            started.set() if command.payload == "blocker" else None,
            release.wait(2.0) if command.payload == "blocker" else None,
        )[-1],
        result_handler=lambda _command, _result: None,
        dispatch_main=lambda _callback: None,
    )
    worker.submit(
        _command(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            "device-a",
            1,
            payload="blocker",
            coalesce_key="device-a:latest",
        )
    )
    assert started.wait(1.0)
    worker.submit(
        _command(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            "device-a",
            1,
            payload="old-ask",
            priority=RuntimeWorkPriority.IMPORTANT,
            coalesce_key="device-a:ask",
        )
    )
    assert (
        worker.submit(
            _command(
                RuntimeWorkerDomain.HARDWARE_WRITE,
                "device-a",
                1,
                payload="new-ask",
                priority=RuntimeWorkPriority.IMPORTANT,
                coalesce_key="device-a:ask",
            )
        )
        is SubmissionDisposition.REPLACED_PENDING
    )
    worker.submit(
        _command(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            "device-a",
            1,
            payload="final",
            coalesce_key="device-a:latest",
        )
    )
    release.set()
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed == 3
    assert executions == ["blocker", "new-ask", "final"]
    assert worker.close(timeout_seconds=1.0)


def test_latest_wins_worker_can_discard_one_resource_prefix_before_preview() -> None:
    started = threading.Event()
    release = threading.Event()
    executions: list[str] = []

    def execute(command: RuntimeWorkCommand) -> object:
        executions.append(str(command.payload))
        if command.payload == "blocker":
            started.set()
            assert release.wait(2.0)
        return None

    worker = LatestWinsWorker(
        RuntimeWorkerDomain.HARDWARE_WRITE,
        executor=execute,
        result_handler=lambda _command, _result: None,
        dispatch_main=lambda _callback: None,
    )
    worker.submit(
        _command(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            "device-a",
            1,
            payload="blocker",
            coalesce_key="device-a:latest",
        )
    )
    assert started.wait(1.0)
    for identity in ("device-a:ask", "device-a:cue-123", "device-b:latest"):
        worker.submit(
            _command(
                RuntimeWorkerDomain.HARDWARE_WRITE,
                identity.split(":", 1)[0],
                1,
                payload=identity,
                coalesce_key=identity,
            )
        )
    assert worker.discard_pending_prefix("device-a:") == 2
    assert worker.snapshot().pending_count == 1
    assert worker.wait_idle(timeout_seconds=0.0) is False
    release.set()
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed == 2
    assert worker.wait_idle(timeout_seconds=1.0) is True
    assert executions == ["blocker", "device-b:latest"]
    assert worker.close(timeout_seconds=1.0)


def test_latest_wins_worker_admits_urgent_work_by_evicting_one_lower_priority_slot() -> None:
    started = threading.Event()
    release = threading.Event()
    executions: list[str] = []

    def execute(command: RuntimeWorkCommand) -> object:
        executions.append(str(command.payload))
        if command.payload == "blocker":
            started.set()
            assert release.wait(2.0)
        return None

    worker = LatestWinsWorker(
        RuntimeWorkerDomain.HARDWARE_WRITE,
        executor=execute,
        result_handler=lambda _command, _result: None,
        dispatch_main=lambda _callback: None,
    )
    worker.submit(
        _command(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            "running",
            1,
            payload="blocker",
        )
    )
    assert started.wait(1.0)
    for index in range(32):
        assert (
            worker.submit(
                _command(
                    RuntimeWorkerDomain.HARDWARE_WRITE,
                    f"ordinary-{index}",
                    1,
                    payload=f"ordinary-{index}",
                )
            )
            is SubmissionDisposition.QUEUED
        )
    assert (
        worker.submit(
            _command(
                RuntimeWorkerDomain.HARDWARE_WRITE,
                "protected",
                1,
                payload="protected",
                priority=RuntimeWorkPriority.URGENT,
            )
        )
        is SubmissionDisposition.QUEUED
    )
    assert worker.snapshot().pending_count == 32
    assert worker.snapshot().cancelled == 1

    release.set()
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed == 33
    assert executions[1] == "protected"
    assert len([value for value in executions if value.startswith("ordinary-")]) == 31
    assert worker.close(timeout_seconds=1.0)


def test_latest_wins_worker_refuses_lower_priority_replacement_of_protected_slot() -> None:
    started = threading.Event()
    release = threading.Event()
    executions: list[str] = []

    def execute(command: RuntimeWorkCommand) -> object:
        executions.append(str(command.payload))
        if command.payload == "blocker":
            started.set()
            assert release.wait(2.0)
        return None

    worker = LatestWinsWorker(
        RuntimeWorkerDomain.HARDWARE_WRITE,
        executor=execute,
        result_handler=lambda _command, _result: None,
        dispatch_main=lambda _callback: None,
    )
    worker.submit(
        _command(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            "running",
            1,
            payload="blocker",
        )
    )
    assert started.wait(1.0)
    worker.submit(
        _command(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            "device-a",
            1,
            payload="protected",
            priority=RuntimeWorkPriority.URGENT,
            coalesce_key="device-a:signal-failure",
        )
    )
    assert (
        worker.submit(
            _command(
                RuntimeWorkerDomain.HARDWARE_WRITE,
                "device-a",
                1,
                payload="downgrade",
                coalesce_key="device-a:signal-failure",
            )
        )
        is SubmissionDisposition.REFUSED
    )
    release.set()
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed == 2
    assert executions == ["blocker", "protected"]
    assert worker.close(timeout_seconds=1.0)


def test_hardware_result_mailbox_keeps_protected_and_trailing_receipts_under_stall() -> None:
    started = threading.Event()
    release = threading.Event()
    callbacks: list[Callable[[], None]] = []
    delivered: list[str] = []

    def execute(command: RuntimeWorkCommand) -> object:
        if command.payload == "blocker":
            started.set()
            assert release.wait(2.0)
        return command.payload

    worker = LatestWinsWorker(
        RuntimeWorkerDomain.HARDWARE_WRITE,
        executor=execute,
        result_handler=lambda _command, result: delivered.append(str(result)),
        dispatch_main=callbacks.append,
    )
    worker.submit(
        _command(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            "blocker",
            1,
            payload="blocker",
        )
    )
    assert started.wait(1.0)
    for index in range(31):
        worker.submit(
            _command(
                RuntimeWorkerDomain.HARDWARE_WRITE,
                f"important-{index}",
                1,
                payload=f"important-{index}",
                priority=RuntimeWorkPriority.IMPORTANT,
            )
        )
    worker.submit(
        _command(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            "device-a",
            1,
            payload="protected",
            priority=RuntimeWorkPriority.URGENT,
            coalesce_key="device-a:semantic-attention",
        )
    )
    release.set()
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed == 33
    worker.submit(
        _command(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            "device-a",
            1,
            payload="final",
            coalesce_key="device-a:latest",
        )
    )
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed == 34

    assert len(callbacks) == 1
    callbacks.pop()()
    assert "protected" in delivered
    assert "final" in delivered
    assert delivered.index("protected") < delivered.index("final")
    assert worker.close(timeout_seconds=1.0)


def test_latest_wins_worker_idle_wait_uses_injected_clock(monkeypatch) -> None:
    clock = _Clock()
    worker = LatestWinsWorker(
        RuntimeWorkerDomain.HARDWARE_WRITE,
        executor=lambda _command: None,
        result_handler=lambda _command, _result: None,
        dispatch_main=lambda _callback: None,
        monotonic=clock,
    )
    monkeypatch.setattr(
        runtime_scheduler.time,
        "monotonic",
        lambda: (_ for _ in ()).throw(AssertionError("global clock used")),
    )

    assert worker.wait_idle(timeout_seconds=0.0)
    assert worker.close(timeout_seconds=0.0)


def test_latest_wins_worker_bounds_pending_and_cancels_stale_generation() -> None:
    started_one = threading.Event()
    started_two = threading.Event()
    release_one = threading.Event()
    dispatched: list[Callable[[], None]] = []
    delivered: list[tuple[int, object]] = []

    def execute(command: RuntimeWorkCommand) -> object:
        if command.generation == 1:
            started_one.set()
            assert release_one.wait(2.0)
        elif command.generation == 2:
            started_two.set()
        return command.payload

    worker = LatestWinsWorker(
        RuntimeWorkerDomain.OS_POLL,
        executor=execute,
        result_handler=lambda command, result: delivered.append((command.generation, result)),
        dispatch_main=dispatched.append,
    )
    assert worker.snapshot().thread_alive is False
    assert (
        worker.submit(_command(RuntimeWorkerDomain.OS_POLL, "running", 1, payload="stale"))
        is SubmissionDisposition.STARTED
    )
    assert started_one.wait(1.0)
    assert (
        worker.submit(_command(RuntimeWorkerDomain.OS_POLL, "same", 1, payload="old")) is SubmissionDisposition.QUEUED
    )
    assert (
        worker.submit(_command(RuntimeWorkerDomain.OS_POLL, "same", 1, payload="new"))
        is SubmissionDisposition.REPLACED_PENDING
    )
    for index in range(31):
        disposition = worker.submit(_command(RuntimeWorkerDomain.OS_POLL, f"key:{index}", 1))
        assert disposition is SubmissionDisposition.QUEUED
    assert worker.snapshot().pending_count == 32
    assert worker.submit(_command(RuntimeWorkerDomain.OS_POLL, "overflow", 1)) is SubmissionDisposition.REFUSED

    worker.cancel_generation(1)
    assert worker.snapshot().pending_count == 0
    assert (
        worker.submit(_command(RuntimeWorkerDomain.OS_POLL, "fresh", 2, payload="fresh"))
        is SubmissionDisposition.QUEUED
    )
    release_one.set()
    assert started_two.wait(1.0)
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed >= 2

    assert len(dispatched) == 1
    dispatched.pop()()
    assert delivered == [(2, "fresh")]
    snapshot = worker.snapshot()
    assert snapshot.stale_results == 1
    assert snapshot.result_count == 0
    assert worker.close(timeout_seconds=1.0)
    assert worker.submit(_command(RuntimeWorkerDomain.OS_POLL, "late", 3)) is SubmissionDisposition.REFUSED


def test_lid_observation_burst_has_one_os_poll_execution_and_one_latest_pending() -> None:
    started = threading.Event()
    release = threading.Event()
    executions: list[int] = []

    def execute(command: RuntimeWorkCommand) -> object:
        executions.append(command.generation)
        if len(executions) == 1:
            started.set()
            assert release.wait(2.0)
        return command.payload

    worker = LatestWinsWorker(
        RuntimeWorkerDomain.OS_POLL,
        executor=execute,
        result_handler=lambda _command, _result: None,
        dispatch_main=lambda _drain: None,
    )

    assert (
        worker.submit(_command(RuntimeWorkerDomain.OS_POLL, "lid-observation", 1))
        is SubmissionDisposition.STARTED
    )
    assert started.wait(1.0)
    for generation in range(2, 101):
        worker.submit(
            _command(
                RuntimeWorkerDomain.OS_POLL,
                "lid-observation",
                generation,
                payload=generation,
            )
        )

    snapshot = worker.snapshot()
    assert snapshot.running
    assert snapshot.pending_count == 1
    assert snapshot.submitted == 100
    assert snapshot.started == 1
    assert snapshot.queued == 1
    assert snapshot.replaced_pending == 98
    assert sum(
        thread.name == "sidepulse-runtime-os-poll"
        for thread in threading.enumerate()
    ) == 1

    release.set()
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed == 2
    assert executions == [1, 100]
    assert worker.close(timeout_seconds=1.0)


def test_latest_wins_worker_refuses_expired_and_replaces_result_mailbox() -> None:
    clock = _Clock()
    dispatched: list[Callable[[], None]] = []
    delivered: list[object] = []
    completed = threading.Condition()
    executions = 0

    def execute(command: RuntimeWorkCommand) -> object:
        nonlocal executions
        with completed:
            executions += 1
            completed.notify_all()
        return command.payload

    worker = LatestWinsWorker(
        RuntimeWorkerDomain.WEATHER_FETCH,
        executor=execute,
        result_handler=lambda _command, result: delivered.append(result),
        dispatch_main=dispatched.append,
        monotonic=clock,
    )
    assert (
        worker.submit(_command(RuntimeWorkerDomain.WEATHER_FETCH, "weather", 1, deadline=99.0))
        is SubmissionDisposition.REFUSED
    )
    assert (
        worker.submit(_command(RuntimeWorkerDomain.WEATHER_FETCH, "weather", 1, deadline=101.0, payload="first"))
        is SubmissionDisposition.STARTED
    )
    with completed:
        assert completed.wait_for(lambda: executions == 1, timeout=1.0)
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed == 1
    assert (
        worker.submit(_command(RuntimeWorkerDomain.WEATHER_FETCH, "weather", 2, deadline=101.0, payload="second"))
        is SubmissionDisposition.QUEUED
    )
    with completed:
        assert completed.wait_for(lambda: executions == 2, timeout=1.0)
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed == 2

    snapshot = worker.snapshot()
    assert snapshot.result_count == 1
    assert snapshot.replaced_results == 1
    assert len(dispatched) == 1
    dispatched.pop()()
    assert delivered == ["second"]
    assert worker.close(timeout_seconds=1.0)


def test_latest_wins_worker_result_keys_remain_bounded_when_main_drain_is_delayed() -> None:
    dispatched: list[Callable[[], None]] = []
    worker = LatestWinsWorker(
        RuntimeWorkerDomain.OS_POLL,
        executor=lambda command: command.payload,
        result_handler=lambda _command, _result: None,
        dispatch_main=dispatched.append,
    )

    for generation in range(1, 101):
        assert worker.submit(
            _command(
                RuntimeWorkerDomain.OS_POLL,
                f"feature:{generation}",
                generation,
                payload=generation,
            )
        ) in {SubmissionDisposition.STARTED, SubmissionDisposition.QUEUED}
        assert worker.wait_idle(timeout_seconds=2.0)
        assert worker.snapshot().completed >= generation

    snapshot = worker.snapshot()
    assert len(dispatched) == 1
    assert snapshot.result_count == 32
    assert snapshot.stale_results == 68
    assert worker.close(timeout_seconds=1.0)


def test_first_submit_and_concurrent_close_cannot_join_an_unstarted_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_thread_type = threading.Thread
    start_entered = threading.Event()
    release_start = threading.Event()
    close_entered = threading.Event()

    class _GatedThread:
        def __init__(self, *, target, name, daemon) -> None:
            self._target = target
            self._name = name
            self._daemon = daemon
            self._inner: threading.Thread | None = None

        @property
        def ident(self) -> int | None:
            return None if self._inner is None else self._inner.ident

        def start(self) -> None:
            start_entered.set()
            assert release_start.wait(1.0)
            self._inner = real_thread_type(
                target=self._target,
                name=self._name,
                daemon=self._daemon,
            )
            self._inner.start()

        def join(self, timeout: float | None = None) -> None:
            if self._inner is None:
                raise RuntimeError("cannot join thread before it is started")
            self._inner.join(timeout)

        def is_alive(self) -> bool:
            return self._inner is not None and self._inner.is_alive()

    monkeypatch.setattr(runtime_scheduler.threading, "Thread", _GatedThread)
    worker = LatestWinsWorker(
        RuntimeWorkerDomain.OS_POLL,
        executor=lambda command: command.payload,
        result_handler=lambda _command, _result: None,
        dispatch_main=lambda _drain: None,
    )
    errors: list[BaseException] = []
    closed: list[bool] = []

    def submit() -> None:
        try:
            worker.submit(_command(RuntimeWorkerDomain.OS_POLL, "lid", 1))
        except BaseException as exc:
            errors.append(exc)

    def close() -> None:
        close_entered.set()
        try:
            closed.append(worker.close(timeout_seconds=1.0))
        except BaseException as exc:
            errors.append(exc)

    submitter = real_thread_type(target=submit)
    submitter.start()
    assert start_entered.wait(1.0)
    closer = real_thread_type(target=close)
    closer.start()
    assert close_entered.wait(1.0)
    release_start.set()
    submitter.join(1.0)
    closer.join(1.0)

    assert not submitter.is_alive()
    assert not closer.is_alive()
    assert errors == []
    assert closed == [True]


class _RegistryWorker:
    def __init__(self, domain: RuntimeWorkerDomain, order: list[RuntimeWorkerDomain], clock: _Clock) -> None:
        self.domain = domain
        self.order = order
        self.clock = clock
        self.received_timeout: float | None = None

    def snapshot(self) -> RuntimeWorkerSnapshot:
        return RuntimeWorkerSnapshot.empty(self.domain, accepting=True)

    def close(self, *, timeout_seconds: float) -> bool:
        self.received_timeout = timeout_seconds
        self.order.append(self.domain)
        self.clock.advance(0.1)
        return True


def test_worker_registry_rejects_duplicates_and_closes_reverse_under_shared_budget() -> None:
    clock = _Clock()
    order: list[RuntimeWorkerDomain] = []
    registry = RuntimeWorkerRegistry(monotonic=clock)
    workers: dict[RuntimeWorkerDomain, _RegistryWorker] = {}
    for domain in RuntimeWorkerDomain:
        worker = _RegistryWorker(domain, order, clock)
        workers[domain] = worker
        registry.register(domain, worker)
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(RuntimeWorkerDomain.OS_POLL, workers[RuntimeWorkerDomain.OS_POLL])
    assert tuple(snapshot.domain for snapshot in registry.snapshot()) == tuple(RuntimeWorkerDomain)

    assert registry.close_all(timeout_seconds=1.0)

    assert order == list(reversed(tuple(RuntimeWorkerDomain)))
    expected = 1.0
    for domain in reversed(tuple(RuntimeWorkerDomain)):
        assert workers[domain].received_timeout == pytest.approx(expected)
        expected -= 0.1
    with pytest.raises(RuntimeError, match="closed"):
        registry.register(
            RuntimeWorkerDomain.OS_POLL,
            _RegistryWorker(RuntimeWorkerDomain.OS_POLL, order, clock),
        )


def test_worker_registry_close_refuses_late_submissions() -> None:
    worker = LatestWinsWorker(
        RuntimeWorkerDomain.ALCOVE_OBSERVER,
        executor=lambda command: command.payload,
        result_handler=lambda _command, _result: None,
        dispatch_main=lambda _drain: None,
    )
    registry = RuntimeWorkerRegistry()
    registry.register(RuntimeWorkerDomain.ALCOVE_OBSERVER, worker)

    assert registry.close_all(timeout_seconds=1.0)
    assert worker.submit(_command(RuntimeWorkerDomain.ALCOVE_OBSERVER, "alcove", 1)) is SubmissionDisposition.REFUSED


def test_ten_thousand_commands_and_reconciliations_stay_bounded() -> None:
    clock = _Clock()
    factory = _FakeAppKitFactory()
    feature = RuntimeFeature.POINTER_PEEK
    registry = AppKitTimerRegistry(
        handlers={feature: lambda: None},
        timer_factory=factory,
        monotonic=clock,
    )
    target = object()
    intent = _intent(feature, interval=0.2, tolerance=0.02)
    for _ in range(10_000):
        registry.reconcile((intent,), target=target)

    release = threading.Event()
    started = threading.Event()

    def execute(command: RuntimeWorkCommand) -> object:
        started.set()
        assert release.wait(2.0)
        return command.payload

    worker = LatestWinsWorker(
        RuntimeWorkerDomain.SCREEN_BAR_SAMPLER,
        executor=execute,
        result_handler=lambda _command, _result: None,
        dispatch_main=lambda _drain: None,
    )
    assert (
        worker.submit(_command(RuntimeWorkerDomain.SCREEN_BAR_SAMPLER, "program", 1)) is SubmissionDisposition.STARTED
    )
    assert started.wait(1.0)
    for generation in range(2, 10_001):
        worker.submit(_command(RuntimeWorkerDomain.SCREEN_BAR_SAMPLER, "program", generation))

    timer_snapshot = registry.snapshot()
    worker_snapshot = worker.snapshot()
    assert timer_snapshot.active_features == (feature,)
    assert timer_snapshot.created == 1
    assert len(timer_snapshot.callback_counts) == len(RuntimeFeature)
    assert worker_snapshot.pending_count == 1
    assert worker_snapshot.result_count == 0
    assert worker_snapshot.submitted == MAX_RUNTIME_METRIC_COUNT
    assert worker_snapshot.thread_alive
    assert sum(thread.name == "sidepulse-runtime-screen-bar-sampler" for thread in threading.enumerate()) == 1

    release.set()
    assert worker.close(timeout_seconds=1.0)
