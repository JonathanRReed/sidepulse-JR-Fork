"""Bounded, AppKit-free answer-in-place execution authority."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Final, Protocol

from .announcer_stack import AnnouncerAlertIdentity
from .answer_in_place import (
    ANSWER_IN_PLACE_RUNTIME_SURFACE,
    AnswerActionKind,
    AnswerAttempt,
    AnswerAttemptState,
    reconcile_answer_attempt,
)
from .provider_contracts import ProductCapability, ProductCapabilityInvocation
from .provider_facts import RequestKind

ANSWER_TIMEOUT_SECONDS: Final[float] = 10.0
ANSWER_CLOSE_TIMEOUT_SECONDS: Final[float] = 1.0
MAX_ANSWER_HANDLERS: Final[int] = 32
_SEND_ACTIONS: Final[frozenset[AnswerActionKind]] = frozenset(
    {
        AnswerActionKind.APPROVE,
        AnswerActionKind.DENY,
        AnswerActionKind.REPLY,
    }
)

AnswerHandler = Callable[..., None]


class _FutureLike(Protocol):
    def add_done_callback(self, callback: Callable[[object], None]) -> None: ...

    def cancel(self) -> bool: ...

    def done(self) -> bool: ...

    def result(self) -> object: ...


class _ExecutorLike(Protocol):
    def submit(self, operation: Callable[[], None]) -> _FutureLike: ...

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None: ...


class _TimerLike(Protocol):
    def start(self) -> None: ...

    def cancel(self) -> None: ...


class AnswerHandlerRegistry:
    """Explicit exact-invocation registry for reviewed local handlers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: dict[ProductCapabilityInvocation, AnswerHandler] = {}
        self._closed = False

    @staticmethod
    def _is_local_answer_invocation(invocation: object) -> bool:
        return bool(
            type(invocation) is ProductCapabilityInvocation
            and invocation.product_capability is ProductCapability.ANSWERING
            and invocation.local_runtime_surface == ANSWER_IN_PLACE_RUNTIME_SURFACE
            and invocation.capability_id is None
            and invocation.capability_version is None
        )

    def register(
        self,
        invocation: ProductCapabilityInvocation,
        handler: AnswerHandler,
    ) -> None:
        if not self._is_local_answer_invocation(invocation) or not callable(handler):
            raise ValueError("invalid answer handler registration")
        with self._lock:
            if self._closed:
                raise RuntimeError("answer handler registry is closed")
            if invocation not in self._handlers and len(self._handlers) >= MAX_ANSWER_HANDLERS:
                raise RuntimeError("answer handler registry is full")
            self._handlers[invocation] = handler

    def resolve(self, invocation: object) -> AnswerHandler | None:
        if not self._is_local_answer_invocation(invocation):
            return None
        with self._lock:
            if self._closed:
                return None
            return self._handlers.get(invocation)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._handlers.clear()


@dataclass(frozen=True, slots=True)
class _AnswerSubmission:
    invocation: ProductCapabilityInvocation
    request_identity: AnnouncerAlertIdentity
    generation: int
    request_kind: RequestKind
    action: AnswerActionKind
    reply_text: str | None


def _default_timer_factory(
    delay: float,
    callback: Callable[[], None],
) -> _TimerLike:
    return threading.Timer(delay, callback)


def _default_dispatch_main(callback: Callable[[], None]) -> None:
    try:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(callback)
    except ImportError:
        callback()


class AnswerRuntime:
    """One controller-owned answer attempt with exact stale-result fences."""

    def __init__(
        self,
        *,
        registry: AnswerHandlerRegistry,
        executor: _ExecutorLike | None = None,
        timer_factory: Callable[[float, Callable[[], None]], _TimerLike] | None = None,
        dispatch_main: Callable[[Callable[[], None]], None] | None = None,
        on_change: Callable[[AnswerAttempt], None] | None = None,
    ) -> None:
        if type(registry) is not AnswerHandlerRegistry:
            raise ValueError("invalid answer handler registry")
        dependencies = tuple(
            dependency
            for dependency in (timer_factory, dispatch_main, on_change)
            if dependency is not None
        )
        if not all(callable(dependency) for dependency in dependencies):
            raise ValueError("invalid answer runtime dependency")
        self._registry = registry
        self._executor: _ExecutorLike = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="sidepulse-answer",
        )
        if not (
            callable(getattr(self._executor, "submit", None))
            and callable(getattr(self._executor, "shutdown", None))
        ):
            raise ValueError("invalid answer runtime executor")
        self._timer_factory = timer_factory or _default_timer_factory
        self._dispatch_main = dispatch_main or _default_dispatch_main
        self._on_change = on_change
        self._lock = threading.RLock()
        self._worker_condition = threading.Condition(self._lock)
        self._active_work_count = 0
        self._attempt: AnswerAttempt | None = None
        self._retry_submission: _AnswerSubmission | None = None
        self._future: _FutureLike | None = None
        self._timer: _TimerLike | None = None
        self._token = 0
        self._closed = False

    @property
    def registry(self) -> AnswerHandlerRegistry:
        return self._registry

    def has_handler(self, invocation: object) -> bool:
        return self._registry.resolve(invocation) is not None

    def snapshot(
        self,
        request_identity: AnnouncerAlertIdentity,
        generation: int,
    ) -> AnswerAttempt | None:
        if type(request_identity) is not AnnouncerAlertIdentity:
            return None
        if type(generation) is not int or generation < 0:
            return None
        with self._lock:
            attempt = self._attempt
            if (
                attempt is None
                or attempt.request_identity != request_identity
                or attempt.generation != generation
            ):
                return None
            return attempt

    def reconcile(
        self,
        request_identity: AnnouncerAlertIdentity,
        generation: int,
        *,
        draft_text: str = "",
    ) -> AnswerAttempt:
        if type(draft_text) is not str:
            raise ValueError("invalid answer draft")
        with self._lock:
            if self._closed:
                raise RuntimeError("answer runtime is closed")
            previous = self._attempt
            attempt = reconcile_answer_attempt(previous, request_identity, generation)
            if attempt is not previous:
                self._invalidate_pending_locked()
                self._retry_submission = None
                if draft_text:
                    attempt = replace(attempt, draft_text=draft_text)
                self._attempt = attempt
            elif draft_text != attempt.draft_text and attempt.state in {
                AnswerAttemptState.IDLE,
                AnswerAttemptState.CANCELLED,
            }:
                attempt = replace(attempt, draft_text=draft_text)
                self._attempt = attempt
            return attempt

    def clear(self) -> None:
        with self._lock:
            self._invalidate_pending_locked()
            self._attempt = None
            self._retry_submission = None

    def submit(
        self,
        invocation: ProductCapabilityInvocation,
        *,
        request_identity: AnnouncerAlertIdentity,
        generation: int,
        request_kind: RequestKind,
        action: AnswerActionKind,
        reply_text: str | None,
    ) -> bool:
        if (
            type(invocation) is not ProductCapabilityInvocation
            or type(request_identity) is not AnnouncerAlertIdentity
            or type(generation) is not int
            or generation < 0
            or type(request_kind) is not RequestKind
            or type(action) is not AnswerActionKind
            or action not in _SEND_ACTIONS
            or (reply_text is not None and type(reply_text) is not str)
            or (action is AnswerActionKind.REPLY) != (reply_text is not None)
        ):
            return False
        handler = self._registry.resolve(invocation)
        if handler is None:
            return False
        with self._lock:
            attempt = self._attempt
            if (
                self._closed
                or attempt is None
                or attempt.request_identity != request_identity
                or attempt.generation != generation
                or attempt.state is AnswerAttemptState.SENDING
            ):
                return False
            draft_text = reply_text if reply_text is not None else attempt.draft_text
            submission = _AnswerSubmission(
                invocation=invocation,
                request_identity=request_identity,
                generation=generation,
                request_kind=request_kind,
                action=action,
                reply_text=reply_text,
            )
            self._retry_submission = submission
            self._attempt = replace(
                attempt,
                state=AnswerAttemptState.SENDING,
                draft_text=draft_text,
                last_error=None,
            )
            return self._start_submission_locked(submission, handler)

    def cancel(
        self,
        request_identity: AnnouncerAlertIdentity,
        generation: int,
    ) -> bool:
        with self._lock:
            attempt = self._attempt
            if (
                self._closed
                or attempt is None
                or attempt.request_identity != request_identity
                or attempt.generation != generation
                or attempt.state is not AnswerAttemptState.SENDING
            ):
                return False
            future = self._future
            if future is not None:
                try:
                    if future.done():
                        return False
                except Exception:
                    return False
            self._invalidate_pending_locked()
            self._attempt = replace(
                attempt,
                state=AnswerAttemptState.CANCELLED,
                last_error=None,
            )
            return True

    def retry(
        self,
        request_identity: AnnouncerAlertIdentity,
        generation: int,
    ) -> bool:
        with self._lock:
            attempt = self._attempt
            submission = self._retry_submission
            if (
                self._closed
                or attempt is None
                or submission is None
                or attempt.request_identity != request_identity
                or attempt.generation != generation
                or submission.request_identity != request_identity
                or submission.generation != generation
                or attempt.state
                not in {
                    AnswerAttemptState.FAILED,
                    AnswerAttemptState.TIMED_OUT,
                    AnswerAttemptState.CANCELLED,
                }
            ):
                return False
            handler = self._registry.resolve(submission.invocation)
            if handler is None:
                return False
            self._attempt = replace(
                attempt,
                state=AnswerAttemptState.SENDING,
                last_error=None,
            )
            return self._start_submission_locked(submission, handler)

    def _start_submission_locked(
        self,
        submission: _AnswerSubmission,
        handler: AnswerHandler,
    ) -> bool:
        self._token += 1
        token = self._token

        def operation() -> None:
            try:
                handler(
                    submission.invocation,
                    request_kind=submission.request_kind,
                    answer_kind=submission.action,
                    reply_text=submission.reply_text,
                )
            finally:
                with self._worker_condition:
                    self._active_work_count -= 1
                    self._worker_condition.notify_all()

        future = None
        timer = None
        self._active_work_count += 1
        try:
            future = self._executor.submit(operation)
            self._future = future
            timer = self._timer_factory(
                ANSWER_TIMEOUT_SECONDS,
                lambda: self._dispatch_main(
                    lambda: self._apply_timeout(token, submission)
                ),
            )
            self._timer = timer
            future.add_done_callback(
                lambda completed: self._dispatch_main(
                    lambda: self._apply_completion(token, submission, completed)
                )
            )
            timer.start()
        except Exception as error:
            self._token += 1
            if timer is not None:
                try:
                    timer.cancel()
                except Exception:
                    pass
            if future is None:
                self._active_work_count -= 1
                self._worker_condition.notify_all()
            else:
                self._cancel_future_locked(future)
            self._future = None
            self._timer = None
            attempt = self._attempt
            if attempt is not None:
                failed = replace(
                    attempt,
                    state=AnswerAttemptState.FAILED,
                    last_error=f"Send failed: {type(error).__name__}"[:280],
                )
                self._attempt = failed
                self._schedule_change(failed)
            return True
        return True

    def _matches_locked(self, token: int, submission: _AnswerSubmission) -> bool:
        attempt = self._attempt
        return bool(
            not self._closed
            and token == self._token
            and attempt is not None
            and attempt.request_identity == submission.request_identity
            and attempt.generation == submission.generation
            and attempt.state is AnswerAttemptState.SENDING
            and self._retry_submission == submission
        )

    def _apply_timeout(self, token: int, submission: _AnswerSubmission) -> None:
        changed: AnswerAttempt | None = None
        with self._lock:
            if not self._matches_locked(token, submission):
                return
            self._token += 1
            self._cancel_timer_locked()
            future = self._future
            self._future = None
            if future is not None:
                self._cancel_future_locked(future)
            assert self._attempt is not None
            changed = replace(
                self._attempt,
                state=AnswerAttemptState.TIMED_OUT,
                last_error="Timed out",
            )
            self._attempt = changed
        self._notify_change(changed)

    def _apply_completion(
        self,
        token: int,
        submission: _AnswerSubmission,
        future: object,
    ) -> None:
        try:
            result = getattr(future, "result")()
        except BaseException as error:
            error_name = type(error).__name__
            state = AnswerAttemptState.FAILED
            message = f"Send failed: {error_name}"[:280]
        else:
            del result
            state = AnswerAttemptState.SENT
            message = None
        changed: AnswerAttempt | None = None
        with self._lock:
            if not self._matches_locked(token, submission):
                return
            self._token += 1
            self._cancel_timer_locked()
            self._future = None
            assert self._attempt is not None
            changed = replace(
                self._attempt,
                state=state,
                last_error=message,
            )
            self._attempt = changed
        self._notify_change(changed)

    def _notify_change(self, attempt: AnswerAttempt) -> None:
        callback = self._on_change
        if callback is not None:
            callback(attempt)

    def _schedule_change(self, attempt: AnswerAttempt) -> None:
        try:
            self._dispatch_main(lambda: self._notify_change(attempt))
        except Exception:
            self._notify_change(attempt)

    def _cancel_timer_locked(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None:
            timer.cancel()

    def _invalidate_pending_locked(self) -> None:
        self._token += 1
        self._cancel_timer_locked()
        future = self._future
        self._future = None
        if future is not None:
            self._cancel_future_locked(future)

    def _cancel_future_locked(self, future: _FutureLike) -> None:
        try:
            cancelled = future.cancel()
        except Exception:
            cancelled = False
        if cancelled:
            self._active_work_count -= 1
            self._worker_condition.notify_all()

    def close(
        self,
        *,
        timeout_seconds: float = ANSWER_CLOSE_TIMEOUT_SECONDS,
    ) -> bool:
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(float(timeout_seconds))
            or not 0.0 <= float(timeout_seconds) <= ANSWER_CLOSE_TIMEOUT_SECONDS
        ):
            raise ValueError("invalid answer runtime close timeout")
        timeout = float(timeout_seconds)
        first_close = False
        with self._lock:
            if not self._closed:
                first_close = True
                self._closed = True
                self._invalidate_pending_locked()
                self._attempt = None
                self._retry_submission = None
        if first_close:
            self._registry.close()
            self._executor.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + timeout
        with self._worker_condition:
            while self._active_work_count:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._worker_condition.wait(remaining)
            return True


__all__ = [
    "ANSWER_CLOSE_TIMEOUT_SECONDS",
    "ANSWER_TIMEOUT_SECONDS",
    "MAX_ANSWER_HANDLERS",
    "AnswerHandler",
    "AnswerHandlerRegistry",
    "AnswerRuntime",
]
