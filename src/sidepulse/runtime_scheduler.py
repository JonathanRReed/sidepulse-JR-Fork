"""Bounded timer and nonprovider worker authority for the macOS runtime.

The module is deliberately AppKit-free at import time. AppKit timer creation is
performed only by an injected factory, or by the lazy default adapter when a
main-thread reconciliation actually creates a timer.
"""

from __future__ import annotations

import math
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Final, Protocol

MAX_RUNTIME_PENDING_KEYS: Final = 32
MAX_RUNTIME_KEY_BYTES: Final = 128
MAX_RUNTIME_METRIC_COUNT: Final = 10_000
RUNTIME_TIMER_SELECTOR: Final = "runtimeTimerFired:"

_SAFE_RUNTIME_KEY: Final = re.compile(r"[a-z0-9][a-z0-9._:-]*")


class RuntimeFeature(str, Enum):
    PROVIDER_SOURCE_DEADLINE = "provider_source_deadline"
    CORE_REFRESH_FALLBACK = "core_refresh_fallback"
    LID_OBSERVATION = "lid_observation"
    DEVICE_INVENTORY = "device_inventory"
    DISPLAY_ENVIRONMENT = "display_environment"
    CALENDAR_OBSERVATION = "calendar_observation"
    REMINDERS_OBSERVATION = "reminders_observation"
    WEATHER_OBSERVATION = "weather_observation"
    TIMEBOX_DEADLINE = "timebox_deadline"
    ESCALATION_DEADLINE = "escalation_deadline"
    CAPACITY_DEADLINE = "capacity_deadline"
    EVENT_COALESCE_DEADLINE = "event_coalesce_deadline"
    FINITE_CUE_DEADLINE = "finite_cue_deadline"
    PRESENTATION_FRAME_FALLBACK = "presentation_frame_fallback"
    PRESENTATION_STATIC_DEADLINE = "presentation_static_deadline"
    ALCOVE_OBSERVATION = "alcove_observation"
    POINTER_PEEK = "pointer_peek"
    SETTINGS_SIGNAL_PREVIEW = "settings_signal_preview"
    SETTINGS_COLOR_PREVIEW = "settings_color_preview"
    SETUP_DEMO = "setup_demo"
    SETTINGS_MESSAGE_DEADLINE = "settings_message_deadline"
    TEST_SIGNAL_DEADLINE = "test_signal_deadline"


class RuntimeWorkerDomain(str, Enum):
    OS_POLL = "os_poll"
    HARDWARE_WRITE = "hardware_write"
    WEATHER_FETCH = "weather_fetch"
    SCREEN_BAR_SAMPLER = "screen_bar_sampler"
    ALCOVE_OBSERVER = "alcove_observer"


class SubmissionDisposition(str, Enum):
    STARTED = "started"
    QUEUED = "queued"
    REPLACED_PENDING = "replaced_pending"
    REFUSED = "refused"


class RuntimeWorkPriority(IntEnum):
    """Selection priority within a bounded worker mailbox.

    Coalescing remains scoped by ``RuntimeWorkCommand.coalesce_key``. Priority
    only decides which distinct pending slot runs first, so an important cue
    and the final ordinary state can both survive a burst.
    """

    COALESCIBLE = 0
    IMPORTANT = 10
    URGENT = 20
    EXPLICIT = 30


def _result_retention_priority(command: RuntimeWorkCommand) -> int:
    priority = int(command.priority)
    if (
        command.domain is RuntimeWorkerDomain.HARDWARE_WRITE
        and command.coalesce_key is not None
        and command.coalesce_key.endswith(":latest")
    ):
        return int(RuntimeWorkPriority.EXPLICIT) + 1
    return priority


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _bounded_increment(value: int, amount: int = 1) -> int:
    return min(MAX_RUNTIME_METRIC_COUNT, value + max(0, amount))


@dataclass(frozen=True, slots=True)
class RuntimeTimerIntent:
    feature: RuntimeFeature
    fire_at: float
    interval: float | None
    tolerance: float
    common_modes: bool

    def __post_init__(self) -> None:
        if type(self.feature) is not RuntimeFeature:
            raise ValueError("invalid runtime timer feature")
        if not _finite_number(self.fire_at) or float(self.fire_at) <= 0.0:
            raise ValueError("invalid runtime timer fire_at")
        interval = self.interval
        if interval is not None and (not _finite_number(interval) or float(interval) <= 0.0):
            raise ValueError("invalid runtime timer interval")
        if not _finite_number(self.tolerance) or float(self.tolerance) < 0.0:
            raise ValueError("invalid runtime timer tolerance")
        tolerance = float(self.tolerance)
        if interval is None and tolerance != 0.0:
            raise ValueError("one-shot runtime timer tolerance must be zero")
        if interval is not None:
            normalized_interval = float(interval)
            if tolerance > normalized_interval:
                raise ValueError("runtime timer tolerance exceeds interval")
            if tolerance == 0.0 and self.feature is not RuntimeFeature.PRESENTATION_FRAME_FALLBACK:
                raise ValueError("repeating runtime timer tolerance must be positive")
            object.__setattr__(self, "interval", normalized_interval)
        if type(self.common_modes) is not bool:
            raise ValueError("invalid runtime timer run-loop mode")
        object.__setattr__(self, "fire_at", float(self.fire_at))
        object.__setattr__(self, "tolerance", tolerance)


@dataclass(frozen=True, slots=True)
class RuntimeTimerSnapshot:
    active_features: tuple[RuntimeFeature, ...]
    callback_counts: tuple[tuple[RuntimeFeature, int], ...]
    created: int
    invalidated: int
    fired: int
    early: int
    late: int

    def callback_count(self, feature: RuntimeFeature) -> int:
        if type(feature) is not RuntimeFeature:
            raise ValueError("invalid runtime timer feature")
        return next(
            (count for candidate, count in self.callback_counts if candidate is feature),
            0,
        )


@dataclass(slots=True)
class _TimerEntry:
    intent: RuntimeTimerIntent
    timer: object
    target_identity: int
    next_expected_at: float


class _TimerFactory(Protocol):
    def create_timer(
        self,
        *,
        delay: float,
        interval: float | None,
        target: object,
        selector: str,
        user_info: RuntimeFeature,
    ) -> object: ...

    def register_timer(self, timer: object, *, common_modes: bool) -> None: ...


class _LazyAppKitTimerFactory:
    """Import Foundation only when the main-thread registry creates a timer."""

    def create_timer(
        self,
        *,
        delay: float,
        interval: float | None,
        target: object,
        selector: str,
        user_info: RuntimeFeature,
    ) -> object:
        from Foundation import NSDate, NSTimer

        repeats = interval is not None
        timer_interval = float(interval) if interval is not None else delay
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            timer_interval,
            target,
            selector,
            user_info,
            repeats,
        )
        if repeats and abs(delay - timer_interval) > 1e-9:
            timer.setFireDate_(NSDate.dateWithTimeIntervalSinceNow_(delay))
        return timer

    def register_timer(self, timer: object, *, common_modes: bool) -> None:
        from Foundation import NSDefaultRunLoopMode, NSRunLoop, NSRunLoopCommonModes

        mode = NSRunLoopCommonModes if common_modes else NSDefaultRunLoopMode
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, mode)


def _assert_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("runtime timer registry is main-thread-only")


class AppKitTimerRegistry:
    """One main-thread AppKit timer per locked runtime feature."""

    def __init__(
        self,
        handlers: Mapping[RuntimeFeature, Callable[[], None]],
        *,
        timer_factory: _TimerFactory | None = None,
        assert_main_thread: Callable[[], None] = _assert_main_thread,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        copied_handlers: dict[RuntimeFeature, Callable[[], None]] = {}
        for feature, handler in handlers.items():
            if type(feature) is not RuntimeFeature or not callable(handler):
                raise ValueError("invalid runtime feature handler")
            copied_handlers[feature] = handler
        if not callable(assert_main_thread) or not callable(monotonic):
            raise ValueError("invalid runtime timer dependency")
        if timer_factory is not None and (
            not callable(getattr(timer_factory, "create_timer", None))
            or not callable(getattr(timer_factory, "register_timer", None))
        ):
            raise ValueError("invalid AppKit timer factory")
        self._handlers = copied_handlers
        self._factory = timer_factory or _LazyAppKitTimerFactory()
        self._assert_main = assert_main_thread
        self._monotonic = monotonic
        self._entries: dict[RuntimeFeature, _TimerEntry] = {}
        self._callback_counts = {feature: 0 for feature in RuntimeFeature}
        self._created = 0
        self._invalidated = 0
        self._fired = 0
        self._early = 0
        self._late = 0

    def reconcile(
        self,
        intents: tuple[RuntimeTimerIntent, ...],
        *,
        target: object,
    ) -> None:
        self._assert_main()
        if type(intents) is not tuple:
            raise ValueError("runtime timer intents must be a tuple")
        if target is None:
            raise ValueError("runtime timer target is required")
        desired: dict[RuntimeFeature, RuntimeTimerIntent] = {}
        for intent in intents:
            if type(intent) is not RuntimeTimerIntent:
                raise ValueError("invalid runtime timer intent")
            if intent.feature in desired:
                raise ValueError("duplicate runtime timer feature")
            if intent.feature not in self._handlers:
                raise ValueError("runtime timer feature has no static handler")
            desired[intent.feature] = intent
        now = self._now()
        target_identity = id(target)
        for feature, intent in desired.items():
            current = self._entries.get(feature)
            unchanged = current is not None and current.intent == intent and current.target_identity == target_identity
            if intent.fire_at <= now and not unchanged:
                raise ValueError("runtime timer fire_at must be in the future")

        for feature in tuple(self._entries):
            if feature not in desired:
                self._invalidate_entry(feature)
        for feature in RuntimeFeature:
            intent = desired.get(feature)
            if intent is None:
                continue
            current = self._entries.get(feature)
            if current is not None and current.intent == intent and current.target_identity == target_identity:
                continue
            if current is not None:
                self._invalidate_entry(feature)
            self._create_entry(intent, target=target, now=now)

    def invalidate(self, feature: RuntimeFeature) -> None:
        self._assert_main()
        if type(feature) is not RuntimeFeature:
            raise ValueError("invalid runtime timer feature")
        self._invalidate_entry(feature)

    def invalidate_all(self) -> None:
        self._assert_main()
        for feature in tuple(self._entries):
            self._invalidate_entry(feature)

    def dispatch(self, timer: object) -> bool:
        """Dispatch a timer delivered by the fixed ``runtimeTimerFired_`` selector."""

        self._assert_main()
        try:
            feature = timer.userInfo()
        except Exception:
            return False
        if type(feature) is not RuntimeFeature:
            return False
        entry = self._entries.get(feature)
        if entry is None or entry.timer is not timer:
            return False
        now = self._now()
        self._fired = _bounded_increment(self._fired)
        self._callback_counts[feature] = _bounded_increment(self._callback_counts[feature])
        if now < entry.next_expected_at - entry.intent.tolerance:
            self._early = _bounded_increment(self._early)
        elif now > entry.next_expected_at + entry.intent.tolerance:
            self._late = _bounded_increment(self._late)

        if entry.intent.interval is None:
            self._invalidate_entry(feature)
        else:
            interval = entry.intent.interval
            assert interval is not None
            if now < entry.next_expected_at:
                entry.next_expected_at += interval
            else:
                elapsed_intervals = math.floor((now - entry.next_expected_at) / interval)
                entry.next_expected_at += (elapsed_intervals + 1) * interval

        self._handlers[feature]()
        return True

    def snapshot(self) -> RuntimeTimerSnapshot:
        self._assert_main()
        return RuntimeTimerSnapshot(
            active_features=tuple(feature for feature in RuntimeFeature if feature in self._entries),
            callback_counts=tuple((feature, self._callback_counts[feature]) for feature in RuntimeFeature),
            created=self._created,
            invalidated=self._invalidated,
            fired=self._fired,
            early=self._early,
            late=self._late,
        )

    def _now(self) -> float:
        value = self._monotonic()
        if not _finite_number(value) or float(value) < 0.0:
            raise RuntimeError("runtime monotonic clock returned an invalid value")
        return float(value)

    def _create_entry(
        self,
        intent: RuntimeTimerIntent,
        *,
        target: object,
        now: float,
    ) -> None:
        delay = intent.fire_at - now
        timer = self._factory.create_timer(
            delay=delay,
            interval=intent.interval,
            target=target,
            selector=RUNTIME_TIMER_SELECTOR,
            user_info=intent.feature,
        )
        try:
            set_tolerance = getattr(timer, "setTolerance_", None)
            if callable(set_tolerance):
                set_tolerance(intent.tolerance)
            self._factory.register_timer(timer, common_modes=intent.common_modes)
        except Exception:
            try:
                timer.invalidate()
            except Exception:
                pass
            raise
        self._entries[intent.feature] = _TimerEntry(
            intent=intent,
            timer=timer,
            target_identity=id(target),
            next_expected_at=intent.fire_at,
        )
        self._created = _bounded_increment(self._created)

    def _invalidate_entry(self, feature: RuntimeFeature) -> None:
        entry = self._entries.pop(feature, None)
        if entry is None:
            return
        try:
            entry.timer.invalidate()
        finally:
            self._invalidated = _bounded_increment(self._invalidated)


def _normalize_work_key(key: object) -> str:
    if type(key) is not str:
        raise ValueError("invalid runtime work key")
    normalized = unicodedata.normalize("NFKC", key).strip().casefold()
    if (
        not normalized
        or normalized in {".", ".."}
        or normalized.startswith((".", "~"))
        or "/" in normalized
        or "\\" in normalized
        or "\x00" in normalized
        or len(normalized.encode("utf-8")) > MAX_RUNTIME_KEY_BYTES
        or _SAFE_RUNTIME_KEY.fullmatch(normalized) is None
    ):
        raise ValueError("invalid runtime work key")
    return normalized


@dataclass(frozen=True, slots=True)
class RuntimeWorkCommand:
    domain: RuntimeWorkerDomain
    key: str
    generation: int
    deadline: float
    payload: object
    priority: RuntimeWorkPriority = RuntimeWorkPriority.COALESCIBLE
    coalesce_key: str | None = None

    def __post_init__(self) -> None:
        if type(self.domain) is not RuntimeWorkerDomain:
            raise ValueError("invalid runtime worker domain")
        object.__setattr__(self, "key", _normalize_work_key(self.key))
        if type(self.priority) is not RuntimeWorkPriority:
            raise ValueError("invalid runtime work priority")
        coalesce_key = self.key if self.coalesce_key is None else self.coalesce_key
        try:
            normalized_coalesce_key = _normalize_work_key(coalesce_key)
        except ValueError as exc:
            raise ValueError("invalid runtime work coalescing key") from exc
        object.__setattr__(self, "coalesce_key", normalized_coalesce_key)
        if type(self.generation) is not int or self.generation <= 0:
            raise ValueError("invalid runtime work generation")
        if not _finite_number(self.deadline) or float(self.deadline) <= 0.0:
            raise ValueError("invalid runtime work deadline")
        object.__setattr__(self, "deadline", float(self.deadline))


@dataclass(frozen=True, slots=True)
class RuntimeWorkerSnapshot:
    domain: RuntimeWorkerDomain
    accepting: bool
    running: bool
    pending_count: int
    result_count: int
    thread_alive: bool
    submitted: int
    started: int
    queued: int
    replaced_pending: int
    refused: int
    completed: int
    failed: int
    cancelled: int
    stale_results: int
    replaced_results: int
    dispatched_results: int

    @classmethod
    def empty(
        cls,
        domain: RuntimeWorkerDomain,
        *,
        accepting: bool,
    ) -> RuntimeWorkerSnapshot:
        if type(domain) is not RuntimeWorkerDomain or type(accepting) is not bool:
            raise ValueError("invalid empty runtime worker snapshot")
        return cls(
            domain=domain,
            accepting=accepting,
            running=False,
            pending_count=0,
            result_count=0,
            thread_alive=False,
            submitted=0,
            started=0,
            queued=0,
            replaced_pending=0,
            refused=0,
            completed=0,
            failed=0,
            cancelled=0,
            stale_results=0,
            replaced_results=0,
            dispatched_results=0,
        )


@dataclass(frozen=True, slots=True)
class _WorkResult:
    command: RuntimeWorkCommand
    result: object


class LatestWinsWorker:
    """One lazy serial worker with bounded keyed pending and result mailboxes."""

    _METRIC_NAMES: Final = (
        "submitted",
        "started",
        "queued",
        "replaced_pending",
        "refused",
        "completed",
        "failed",
        "cancelled",
        "stale_results",
        "replaced_results",
        "dispatched_results",
    )

    def __init__(
        self,
        domain: RuntimeWorkerDomain,
        *,
        executor: Callable[[RuntimeWorkCommand], object],
        result_handler: Callable[[RuntimeWorkCommand, object], None],
        dispatch_main: Callable[[Callable[[], None]], None],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(domain) is not RuntimeWorkerDomain:
            raise ValueError("invalid runtime worker domain")
        if not all(callable(dependency) for dependency in (executor, result_handler, dispatch_main, monotonic)):
            raise ValueError("invalid runtime worker dependency")
        self._domain = domain
        self._executor = executor
        self._result_handler = result_handler
        self._dispatch_main = dispatch_main
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._pending: dict[str, RuntimeWorkCommand] = {}
        self._results: dict[str, _WorkResult] = {}
        self._running: RuntimeWorkCommand | None = None
        self._thread: threading.Thread | None = None
        self._accepting = True
        self._cancelled_through = 0
        self._drain_scheduled = False
        self._metrics = {name: 0 for name in self._METRIC_NAMES}

    def submit(self, command: RuntimeWorkCommand) -> SubmissionDisposition:
        if type(command) is not RuntimeWorkCommand:
            raise ValueError("invalid runtime work command")
        if command.domain is not self._domain:
            raise ValueError("runtime work command domain mismatch")
        now = self._now()
        first_start = False
        with self._condition:
            self._increment("submitted")
            if not self._accepting or command.generation <= self._cancelled_through or command.deadline <= now:
                self._increment("refused")
                return SubmissionDisposition.REFUSED
            coalesce_key = command.coalesce_key
            assert coalesce_key is not None
            existing = self._pending.get(coalesce_key)
            if existing is not None:
                if command.priority < existing.priority:
                    self._increment("refused")
                    return SubmissionDisposition.REFUSED
                self._pending[coalesce_key] = command
                self._increment("replaced_pending")
                self._condition.notify_all()
                return SubmissionDisposition.REPLACED_PENDING
            if len(self._pending) >= MAX_RUNTIME_PENDING_KEYS:
                victim_key, victim = min(
                    self._pending.items(),
                    key=lambda item: (
                        int(item[1].priority),
                        -item[1].deadline,
                        item[0],
                    ),
                )
                if command.priority <= victim.priority:
                    self._increment("refused")
                    return SubmissionDisposition.REFUSED
                del self._pending[victim_key]
                self._increment("cancelled")
            first_start = self._thread is None
            self._pending[coalesce_key] = command
            if first_start:
                thread = threading.Thread(
                    target=self._run,
                    name=f"sidepulse-runtime-{self._domain.value.replace('_', '-')}",
                    daemon=True,
                )
                self._thread = thread
                try:
                    thread.start()
                except Exception:
                    self._thread = None
                    self._pending.pop(coalesce_key, None)
                    self._increment("refused")
                    raise
            else:
                self._increment("queued")
            self._condition.notify_all()
        if first_start:
            return SubmissionDisposition.STARTED
        return SubmissionDisposition.QUEUED

    def discard_pending(self, coalesce_key: str) -> bool:
        """Discard one exact semantic slot without cancelling the generation."""

        normalized = _normalize_work_key(coalesce_key)
        with self._condition:
            removed = self._pending.pop(normalized, None) is not None
            if removed:
                self._increment("cancelled")
                self._condition.notify_all()
            return removed

    def discard_pending_prefix(self, coalesce_prefix: str) -> int:
        """Discard every bounded slot under one opaque resource prefix."""

        normalized = _normalize_work_key(coalesce_prefix)
        with self._condition:
            keys = tuple(key for key in self._pending if key.startswith(normalized))
            for key in keys:
                del self._pending[key]
            if keys:
                self._metrics["cancelled"] = _bounded_increment(
                    self._metrics["cancelled"], len(keys)
                )
                self._condition.notify_all()
            return len(keys)

    def wait_idle(self, *, timeout_seconds: float) -> bool:
        """Wait until no command is running or pending without consuming results."""

        if not _finite_number(timeout_seconds) or float(timeout_seconds) < 0.0:
            raise ValueError("invalid runtime worker idle timeout")
        deadline = self._now() + float(timeout_seconds)
        with self._condition:
            while self._running is not None or self._pending:
                remaining = deadline - self._now()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def cancel_generation(self, generation: int) -> None:
        if type(generation) is not int or generation <= 0:
            raise ValueError("invalid runtime work generation")
        with self._condition:
            if generation <= self._cancelled_through:
                return
            self._cancelled_through = generation
            pending_keys = tuple(key for key, command in self._pending.items() if command.generation <= generation)
            result_keys = tuple(key for key, result in self._results.items() if result.command.generation <= generation)
            for key in pending_keys:
                del self._pending[key]
            for key in result_keys:
                del self._results[key]
            cancelled_count = len(pending_keys) + len(result_keys)
            if self._running is not None and self._running.generation <= generation:
                cancelled_count += 1
            self._metrics["cancelled"] = _bounded_increment(self._metrics["cancelled"], cancelled_count)
            self._condition.notify_all()

    def close(self, *, timeout_seconds: float) -> bool:
        if not _finite_number(timeout_seconds) or float(timeout_seconds) < 0.0:
            raise ValueError("invalid runtime worker close timeout")
        with self._condition:
            self._accepting = False
            self._metrics["cancelled"] = _bounded_increment(
                self._metrics["cancelled"], len(self._pending) + len(self._results)
            )
            self._pending.clear()
            self._results.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is None:
            return True
        if thread.ident == threading.get_ident():
            return False
        thread.join(float(timeout_seconds))
        return not thread.is_alive()

    def snapshot(self) -> RuntimeWorkerSnapshot:
        with self._condition:
            thread = self._thread
            return RuntimeWorkerSnapshot(
                domain=self._domain,
                accepting=self._accepting,
                running=self._running is not None,
                pending_count=len(self._pending),
                result_count=len(self._results),
                thread_alive=bool(thread is not None and thread.is_alive()),
                submitted=self._metrics["submitted"],
                started=self._metrics["started"],
                queued=self._metrics["queued"],
                replaced_pending=self._metrics["replaced_pending"],
                refused=self._metrics["refused"],
                completed=self._metrics["completed"],
                failed=self._metrics["failed"],
                cancelled=self._metrics["cancelled"],
                stale_results=self._metrics["stale_results"],
                replaced_results=self._metrics["replaced_results"],
                dispatched_results=self._metrics["dispatched_results"],
            )

    def _run(self) -> None:
        while True:
            command = self._take_next_command()
            if command is None:
                return
            failed = False
            result: object = None
            try:
                result = self._executor(command)
            except Exception:
                failed = True
            schedule_drain = False
            with self._condition:
                self._running = None
                self._increment("completed")
                now = self._now()
                stale = not self._accepting or command.generation <= self._cancelled_through or command.deadline <= now
                if failed:
                    self._increment("failed")
                elif stale:
                    self._increment("stale_results")
                else:
                    result_key = command.coalesce_key
                    assert result_key is not None
                    admit_result = True
                    if result_key in self._results:
                        self._increment("replaced_results")
                        del self._results[result_key]
                    elif len(self._results) >= MAX_RUNTIME_PENDING_KEYS:
                        victim_key, victim = min(
                            self._results.items(),
                            key=lambda item: _result_retention_priority(
                                item[1].command
                            ),
                        )
                        if _result_retention_priority(
                            command
                        ) < _result_retention_priority(victim.command):
                            admit_result = False
                        else:
                            del self._results[victim_key]
                        self._increment("stale_results")
                    if admit_result:
                        self._results[result_key] = _WorkResult(command, result)
                    if admit_result and not self._drain_scheduled:
                        self._drain_scheduled = True
                        schedule_drain = True
                self._condition.notify_all()
            if schedule_drain:
                try:
                    self._dispatch_main(self._drain_results)
                except Exception:
                    with self._condition:
                        self._drain_scheduled = False
                        self._increment("failed")
                        self._condition.notify_all()

    def _take_next_command(self) -> RuntimeWorkCommand | None:
        with self._condition:
            while True:
                now = self._now()
                expired = tuple(
                    key
                    for key, command in self._pending.items()
                    if command.deadline <= now or command.generation <= self._cancelled_through
                )
                for key in expired:
                    del self._pending[key]
                if expired:
                    self._metrics["refused"] = _bounded_increment(self._metrics["refused"], len(expired))
                if self._pending:
                    slot, command = min(
                        self._pending.items(),
                        key=lambda item: (
                            -int(item[1].priority),
                            item[1].deadline,
                            item[1].key,
                            item[0],
                        ),
                    )
                    del self._pending[slot]
                    self._running = command
                    self._increment("started")
                    return command
                if not self._accepting:
                    return None
                self._condition.wait()

    def _drain_results(self) -> None:
        with self._condition:
            self._drain_scheduled = False
            if not self._accepting:
                self._results.clear()
                return
            if self._domain is RuntimeWorkerDomain.HARDWARE_WRITE:
                # Insertion order is completion order. Replacing one slot
                # deletes and re-appends it above, preserving final device
                # truth instead of reordering semantic callbacks by name.
                results = tuple(self._results.values())
            else:
                # Existing shared workers have deterministic key-order
                # dependencies between observations reconciled on main.
                results = tuple(self._results[key] for key in sorted(self._results))
            self._results.clear()
        for item in results:
            with self._condition:
                valid = (
                    self._accepting
                    and item.command.generation > self._cancelled_through
                    and item.command.deadline > self._now()
                )
                if not valid:
                    self._increment("stale_results")
            if not valid:
                continue
            try:
                self._result_handler(item.command, item.result)
            except Exception:
                with self._condition:
                    self._increment("failed")
            else:
                with self._condition:
                    self._increment("dispatched_results")

    def _now(self) -> float:
        value = self._monotonic()
        if not _finite_number(value) or float(value) < 0.0:
            raise RuntimeError("runtime monotonic clock returned an invalid value")
        return float(value)

    def _increment(self, name: str) -> None:
        self._metrics[name] = _bounded_increment(self._metrics[name])


class _RegisteredWorker(Protocol):
    def snapshot(self) -> RuntimeWorkerSnapshot: ...

    def close(self, *, timeout_seconds: float) -> bool: ...


class RuntimeWorkerRegistry:
    """Own worker domains and close them in reverse order under one budget."""

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        if not callable(monotonic):
            raise ValueError("invalid runtime worker registry clock")
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._workers: dict[RuntimeWorkerDomain, _RegisteredWorker] = {}
        self._registration_order: list[RuntimeWorkerDomain] = []
        self._closed = False

    def register(self, domain: RuntimeWorkerDomain, worker: object) -> None:
        if type(domain) is not RuntimeWorkerDomain:
            raise ValueError("invalid runtime worker domain")
        if not callable(getattr(worker, "snapshot", None)) or not callable(getattr(worker, "close", None)):
            raise ValueError("invalid runtime worker")
        with self._lock:
            if self._closed:
                raise RuntimeError("runtime worker registry is closed")
            if domain in self._workers:
                raise ValueError("duplicate runtime worker domain")
            self._workers[domain] = worker  # type: ignore[assignment]
            self._registration_order.append(domain)

    def snapshot(self) -> tuple[RuntimeWorkerSnapshot, ...]:
        with self._lock:
            workers = tuple((domain, self._workers[domain]) for domain in self._registration_order)
        snapshots: list[RuntimeWorkerSnapshot] = []
        for domain, worker in workers:
            snapshot = worker.snapshot()
            if type(snapshot) is not RuntimeWorkerSnapshot or snapshot.domain is not domain:
                raise RuntimeError("runtime worker returned an invalid snapshot")
            snapshots.append(snapshot)
        return tuple(snapshots)

    def close_all(self, *, timeout_seconds: float) -> bool:
        if not _finite_number(timeout_seconds) or float(timeout_seconds) < 0.0:
            raise ValueError("invalid runtime worker registry timeout")
        started_at = self._now()
        deadline = started_at + float(timeout_seconds)
        with self._lock:
            self._closed = True
            workers = tuple(self._workers[domain] for domain in reversed(self._registration_order))
        all_closed = True
        for worker in workers:
            remaining = max(0.0, deadline - self._now())
            try:
                closed = worker.close(timeout_seconds=remaining)
            except Exception:
                closed = False
            all_closed = bool(closed) and all_closed
        return all_closed

    def _now(self) -> float:
        value = self._monotonic()
        if not _finite_number(value) or float(value) < 0.0:
            raise RuntimeError("runtime monotonic clock returned an invalid value")
        return float(value)
