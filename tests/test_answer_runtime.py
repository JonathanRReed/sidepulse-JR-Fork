from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest

from sidepulse.announcer_stack import AnnouncerAlertIdentity
from sidepulse.answer_in_place import (
    AnswerActionKind,
    AnswerAttemptState,
    AnswerCapability,
    project_answer_controls,
)
from sidepulse.answer_runtime import (
    ANSWER_TIMEOUT_SECONDS,
    AnswerHandlerRegistry,
    AnswerRuntime,
)
from sidepulse.provider_contracts import (
    AdapterIdentifier,
    LocalRuntimeSurfaceIdentifier,
    ProductCapability,
    ProductCapabilityInvocation,
    ProviderIdentifier,
    SourceInstanceIdentifier,
)
from sidepulse.provider_facts import RequestKind


def _invocation(
    *,
    provider: str = "codex",
    adapter: str = "hooks",
    source: str = "source:main",
) -> ProductCapabilityInvocation:
    return ProductCapabilityInvocation(
        product_capability=ProductCapability.ANSWERING,
        provider_id=ProviderIdentifier(provider),
        adapter_id=AdapterIdentifier(adapter),
        source_instance_id=SourceInstanceIdentifier(source),
        local_runtime_surface=LocalRuntimeSurfaceIdentifier(
            "local.answer_in_place"
        ),
    )


class _ManualFuture:
    def __init__(self, operation, *, fail_callback_registration: bool = False) -> None:
        self.operation = operation
        self.callbacks = []
        self.value = None
        self.error: BaseException | None = None
        self._done = False
        self.cancelled = False
        self.fail_callback_registration = fail_callback_registration

    def add_done_callback(self, callback) -> None:
        if self.fail_callback_registration:
            raise RuntimeError("callback registration failed")
        self.callbacks.append(callback)

    def done(self) -> bool:
        return self._done

    def cancel(self) -> bool:
        if self._done:
            return False
        self.cancelled = True
        return True

    def result(self):
        if self.cancelled:
            raise RuntimeError("cancelled")
        if self.error is not None:
            raise self.error
        return self.value

    def run(self) -> None:
        if not self.cancelled:
            try:
                self.value = self.operation()
            except BaseException as error:
                self.error = error
        self._done = True
        for callback in tuple(self.callbacks):
            callback(self)


class _ManualExecutor:
    def __init__(
        self,
        *,
        fail_submit: bool = False,
        fail_callback_registration: bool = False,
    ) -> None:
        self.futures: list[_ManualFuture] = []
        self.shutdown_calls: list[tuple[bool, bool]] = []
        self.fail_submit = fail_submit
        self.fail_callback_registration = fail_callback_registration

    def submit(self, operation):
        if self.fail_submit:
            raise RuntimeError("submit failed")
        future = _ManualFuture(
            operation,
            fail_callback_registration=self.fail_callback_registration,
        )
        self.futures.append(future)
        return future

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


@dataclass
class _ManualTimer:
    delay: float
    callback: object
    started: bool = False
    cancelled: bool = False
    fail_start: bool = False

    def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("timer start failed")
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if not self.cancelled:
            self.callback()


class _ManualTimerFactory:
    def __init__(self, *, fail_factory: bool = False, fail_start: bool = False) -> None:
        self.timers: list[_ManualTimer] = []
        self.fail_factory = fail_factory
        self.fail_start = fail_start

    def __call__(self, delay: float, callback) -> _ManualTimer:
        if self.fail_factory:
            raise RuntimeError("timer factory failed")
        timer = _ManualTimer(delay, callback, fail_start=self.fail_start)
        self.timers.append(timer)
        return timer


@dataclass
class _Harness:
    runtime: AnswerRuntime
    registry: AnswerHandlerRegistry
    executor: _ManualExecutor
    timer_factory: _ManualTimerFactory
    main_callbacks: list[object]
    handler_calls: list[tuple]

    def drain_main(self) -> None:
        while self.main_callbacks:
            self.main_callbacks.pop(0)()


def _harness(handler=None) -> _Harness:
    registry = AnswerHandlerRegistry()
    calls: list[tuple] = []

    def default_handler(
        invocation,
        *,
        request_kind,
        answer_kind,
        reply_text,
    ) -> None:
        calls.append((invocation, request_kind, answer_kind, reply_text))

    registry.register(_invocation(), handler or default_handler)
    executor = _ManualExecutor()
    timer_factory = _ManualTimerFactory()
    main_callbacks: list[object] = []
    runtime = AnswerRuntime(
        registry=registry,
        executor=executor,
        timer_factory=timer_factory,
        dispatch_main=main_callbacks.append,
    )
    return _Harness(
        runtime,
        registry,
        executor,
        timer_factory,
        main_callbacks,
        calls,
    )


def _reconcile(harness: _Harness, *, generation: int = 4, draft: str = ""):
    return harness.runtime.reconcile(
        AnnouncerAlertIdentity("request:0"),
        generation,
        draft_text=draft,
    )


def _submit(
    harness: _Harness,
    *,
    generation: int = 4,
    action: AnswerActionKind = AnswerActionKind.APPROVE,
    reply_text: str | None = None,
) -> bool:
    return harness.runtime.submit(
        _invocation(),
        request_identity=AnnouncerAlertIdentity("request:0"),
        generation=generation,
        request_kind=(
            RequestKind.INPUT
            if action is AnswerActionKind.REPLY
            else RequestKind.PERMISSION
        ),
        action=action,
        reply_text=reply_text,
    )


def test_registry_resolves_only_the_exact_reviewed_local_invocation() -> None:
    registry = AnswerHandlerRegistry()

    def handler(*_args, **_kwargs) -> None:
        return None

    exact = _invocation()
    registry.register(exact, handler)

    assert registry.resolve(exact) is handler
    assert registry.resolve(_invocation(source="source:other")) is None
    assert registry.resolve(object()) is None


def test_missing_handler_refuses_without_changing_the_idle_attempt() -> None:
    runtime = AnswerRuntime(
        registry=AnswerHandlerRegistry(),
        executor=_ManualExecutor(),
        timer_factory=_ManualTimerFactory(),
        dispatch_main=lambda callback: callback(),
    )
    identity = AnnouncerAlertIdentity("request:0")
    runtime.reconcile(identity, 4)

    accepted = runtime.submit(
        _invocation(),
        request_identity=identity,
        generation=4,
        request_kind=RequestKind.PERMISSION,
        action=AnswerActionKind.APPROVE,
        reply_text=None,
    )

    assert accepted is False
    assert runtime.snapshot(identity, 4).state is AnswerAttemptState.IDLE


def test_submit_preserves_exact_invocation_and_uses_fixed_timeout() -> None:
    harness = _harness()
    identity = AnnouncerAlertIdentity("request:0")
    _reconcile(harness)

    assert _submit(harness) is True
    assert harness.runtime.snapshot(identity, 4).state is AnswerAttemptState.SENDING
    assert len(harness.executor.futures) == 1
    assert len(harness.timer_factory.timers) == 1
    assert harness.timer_factory.timers[0].delay == ANSWER_TIMEOUT_SECONDS == 10.0
    assert harness.timer_factory.timers[0].started is True

    harness.executor.futures[0].run()
    assert harness.runtime.snapshot(identity, 4).state is AnswerAttemptState.SENDING
    harness.drain_main()

    assert harness.handler_calls == [
        (
            _invocation(),
            RequestKind.PERMISSION,
            AnswerActionKind.APPROVE,
            None,
        )
    ]
    assert harness.runtime.snapshot(identity, 4).state is AnswerAttemptState.SENT
    assert harness.timer_factory.timers[0].cancelled is True


@pytest.mark.parametrize(
    "seam",
    ("executor-submit", "timer-factory", "callback-registration", "timer-start"),
)
def test_bootstrap_failure_is_visible_and_notifies_the_projector(seam: str) -> None:
    registry = AnswerHandlerRegistry()
    registry.register(_invocation(), lambda *_args, **_kwargs: None)
    executor = _ManualExecutor(
        fail_submit=seam == "executor-submit",
        fail_callback_registration=seam == "callback-registration",
    )
    timers = _ManualTimerFactory(
        fail_factory=seam == "timer-factory",
        fail_start=seam == "timer-start",
    )
    main_callbacks: list[object] = []
    changes = []
    runtime = AnswerRuntime(
        registry=registry,
        executor=executor,
        timer_factory=timers,
        dispatch_main=main_callbacks.append,
        on_change=changes.append,
    )
    identity = AnnouncerAlertIdentity("request:bootstrap")
    runtime.reconcile(identity, 8)

    accepted = runtime.submit(
        _invocation(),
        request_identity=identity,
        generation=8,
        request_kind=RequestKind.PERMISSION,
        action=AnswerActionKind.APPROVE,
        reply_text=None,
    )
    while main_callbacks:
        main_callbacks.pop(0)()

    assert accepted is True
    attempt = runtime.snapshot(identity, 8)
    assert attempt.state is AnswerAttemptState.FAILED
    assert attempt.last_error == "Send failed: RuntimeError"
    assert changes == [attempt]


def test_default_runtime_invokes_handler_off_the_calling_thread() -> None:
    registry = AnswerHandlerRegistry()
    identity = AnnouncerAlertIdentity("request:thread")
    caller_thread = threading.get_ident()
    called_thread: list[int] = []
    done = threading.Event()

    def handler(*_args, **_kwargs) -> None:
        called_thread.append(threading.get_ident())
        done.set()

    registry.register(_invocation(), handler)
    runtime = AnswerRuntime(registry=registry)
    runtime.reconcile(identity, 1)
    assert runtime.submit(
        _invocation(),
        request_identity=identity,
        generation=1,
        request_kind=RequestKind.PERMISSION,
        action=AnswerActionKind.APPROVE,
        reply_text=None,
    )

    assert done.wait(1.0)
    assert called_thread and called_thread[0] != caller_thread
    runtime.close()


def test_timeout_preserves_draft_and_projects_retry_and_jump() -> None:
    harness = _harness()
    identity = AnnouncerAlertIdentity("request:0")
    _reconcile(harness, draft="Use the existing selection")
    assert _submit(
        harness,
        action=AnswerActionKind.REPLY,
        reply_text="Use the existing selection",
    )

    harness.timer_factory.timers[0].fire()
    harness.drain_main()
    attempt = harness.runtime.snapshot(identity, 4)

    assert attempt.state is AnswerAttemptState.TIMED_OUT
    assert attempt.draft_text == "Use the existing selection"
    plan = project_answer_controls(
        RequestKind.INPUT,
        AnswerCapability(True, True, False, _invocation()),
        attempt,
    )
    assert plan.primary_actions == (
        AnswerActionKind.RETRY,
        AnswerActionKind.JUMP,
    )


def test_failure_preserves_draft_and_retry_reuses_exact_submission() -> None:
    calls: list[tuple] = []

    def failing_handler(
        invocation,
        *,
        request_kind,
        answer_kind,
        reply_text,
    ) -> None:
        calls.append((invocation, request_kind, answer_kind, reply_text))
        if len(calls) == 1:
            raise RuntimeError("provider refused")

    harness = _harness(failing_handler)
    identity = AnnouncerAlertIdentity("request:0")
    _reconcile(harness, draft="Please continue")
    assert _submit(
        harness,
        action=AnswerActionKind.REPLY,
        reply_text="Please continue",
    )
    harness.executor.futures[0].run()
    harness.drain_main()

    failed = harness.runtime.snapshot(identity, 4)
    assert failed.state is AnswerAttemptState.FAILED
    assert failed.draft_text == "Please continue"
    assert failed.last_error == "Send failed: RuntimeError"

    assert harness.runtime.retry(identity, 4) is True
    assert len(harness.executor.futures) == 2
    harness.executor.futures[1].run()
    harness.drain_main()

    assert calls == [
        (_invocation(), RequestKind.INPUT, AnswerActionKind.REPLY, "Please continue"),
        (_invocation(), RequestKind.INPUT, AnswerActionKind.REPLY, "Please continue"),
    ]
    assert harness.runtime.snapshot(identity, 4).state is AnswerAttemptState.SENT


def test_cancel_is_local_and_late_success_cannot_overwrite_cancelled() -> None:
    harness = _harness()
    identity = AnnouncerAlertIdentity("request:0")
    _reconcile(harness)
    assert _submit(harness)

    assert harness.runtime.cancel(identity, 4) is True
    assert harness.runtime.snapshot(identity, 4).state is AnswerAttemptState.CANCELLED
    harness.executor.futures[0].run()
    harness.drain_main()

    assert harness.runtime.snapshot(identity, 4).state is AnswerAttemptState.CANCELLED


def test_completed_future_cannot_be_relabelled_cancelled_before_main_drain() -> None:
    harness = _harness()
    identity = AnnouncerAlertIdentity("request:0")
    _reconcile(harness)
    assert _submit(harness)
    harness.executor.futures[0].run()

    assert harness.runtime.cancel(identity, 4) is False
    assert harness.runtime.snapshot(identity, 4).state is AnswerAttemptState.SENDING
    harness.drain_main()

    assert harness.runtime.snapshot(identity, 4).state is AnswerAttemptState.SENT


@pytest.mark.parametrize("completion", ("success", "failure", "timeout"))
def test_stale_callbacks_cannot_mutate_a_new_generation_or_identity(completion) -> None:
    harness = _harness(
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(RuntimeError("late"))
            if completion == "failure"
            else None
        )
    )
    first = AnnouncerAlertIdentity("request:0")
    second = AnnouncerAlertIdentity("request:1")
    _reconcile(harness)
    assert _submit(harness)

    if completion == "timeout":
        harness.timer_factory.timers[0].fire()
    else:
        harness.executor.futures[0].run()
    harness.runtime.reconcile(second, 5)
    harness.drain_main()

    assert harness.runtime.snapshot(first, 4) is None
    current = harness.runtime.snapshot(second, 5)
    assert current.state is AnswerAttemptState.IDLE


def test_canonical_clear_wins_before_a_late_completion() -> None:
    harness = _harness()
    identity = AnnouncerAlertIdentity("request:0")
    _reconcile(harness)
    assert _submit(harness)

    harness.runtime.clear()
    harness.executor.futures[0].run()
    harness.drain_main()

    assert harness.runtime.snapshot(identity, 4) is None


def test_close_invalidates_pending_callbacks_and_refuses_new_work() -> None:
    harness = _harness()
    identity = AnnouncerAlertIdentity("request:0")
    _reconcile(harness)
    assert _submit(harness)

    assert harness.runtime.close(timeout_seconds=0.0) is True
    harness.executor.futures[0].run()
    harness.timer_factory.timers[0].fire()
    harness.drain_main()

    assert harness.executor.shutdown_calls == [(False, True)]
    assert harness.runtime.snapshot(identity, 4) is None
    assert _submit(harness) is False


def test_bounded_close_reports_a_running_handler_without_mutating_late_state() -> None:
    registry = AnswerHandlerRegistry()
    identity = AnnouncerAlertIdentity("request:blocked-close")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def handler(*_args, **_kwargs) -> None:
        started.set()
        release.wait()
        finished.set()

    registry.register(_invocation(), handler)
    runtime = AnswerRuntime(registry=registry)
    runtime.reconcile(identity, 1)
    assert runtime.submit(
        _invocation(),
        request_identity=identity,
        generation=1,
        request_kind=RequestKind.PERMISSION,
        action=AnswerActionKind.APPROVE,
        reply_text=None,
    )
    assert started.wait(1.0)

    try:
        assert runtime.close(timeout_seconds=0.0) is False
        assert runtime.snapshot(identity, 1) is None
    finally:
        release.set()
    assert finished.wait(1.0)
    assert runtime.snapshot(identity, 1) is None


def test_bounded_close_reports_a_normally_completed_worker() -> None:
    registry = AnswerHandlerRegistry()
    identity = AnnouncerAlertIdentity("request:complete-close")
    completed = threading.Event()

    def handler(*_args, **_kwargs) -> None:
        completed.set()

    registry.register(_invocation(), handler)
    runtime = AnswerRuntime(registry=registry)
    runtime.reconcile(identity, 1)
    assert runtime.submit(
        _invocation(),
        request_identity=identity,
        generation=1,
        request_kind=RequestKind.PERMISSION,
        action=AnswerActionKind.APPROVE,
        reply_text=None,
    )
    assert completed.wait(1.0)

    assert runtime.close(timeout_seconds=1.0) is True
    assert runtime.snapshot(identity, 1) is None
