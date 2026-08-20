from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .presentation_policy import MotionClass

MAX_SAMPLE_RATE_HZ = 60.0
MIN_SAMPLE_INTERVAL_SECONDS = 1.0 / MAX_SAMPLE_RATE_HZ
METRIC_RESERVOIR_CAPACITY = 512
MAX_METRIC_COUNTER = 10_000

Color = tuple[float, float, float, float]
Colors = tuple[Color, ...]


class PresentationMetricKind(str, Enum):
    DISPLAY_CALLBACK_NS = "display_callback_ns"
    SAMPLE_WORK_NS = "sample_work_ns"
    SAMPLE_AGE_NS = "sample_age_ns"
    ALCOVE_WORK_NS = "alcove_work_ns"
    GEOMETRY_BUILD_NS = "geometry_build_ns"
    PAINT_BUILD_NS = "paint_build_ns"
    TARGET_FALLBACK = "target_fallback"
    STALE_SAMPLE = "stale_sample"
    DROPPED_COMMAND = "dropped_command"
    STALE_OBSERVATION = "stale_observation"


_DURATION_METRICS = frozenset(
    {
        PresentationMetricKind.DISPLAY_CALLBACK_NS,
        PresentationMetricKind.SAMPLE_WORK_NS,
        PresentationMetricKind.SAMPLE_AGE_NS,
        PresentationMetricKind.ALCOVE_WORK_NS,
        PresentationMetricKind.GEOMETRY_BUILD_NS,
        PresentationMetricKind.PAINT_BUILD_NS,
    }
)


def _require_metric_kind(kind: PresentationMetricKind) -> None:
    if not isinstance(kind, PresentationMetricKind):
        raise TypeError("metric kind must be a PresentationMetricKind")


@dataclass(frozen=True, slots=True)
class PresentationMetricsSnapshot:
    duration_values: tuple[tuple[PresentationMetricKind, tuple[int, ...]], ...]
    counter_values: tuple[tuple[PresentationMetricKind, int], ...]

    def durations(self, kind: PresentationMetricKind) -> tuple[int, ...]:
        _require_metric_kind(kind)
        return next((values for candidate, values in self.duration_values if candidate is kind), ())

    def counter(self, kind: PresentationMetricKind) -> int:
        _require_metric_kind(kind)
        return next((value for candidate, value in self.counter_values if candidate is kind), 0)


class PresentationMetrics:
    """Bounded, label-free presentation telemetry kept only in memory.

    Mutations intentionally avoid locks. CPython deque appends and reference
    assignments are atomic under the GIL, which keeps the display callback
    from ever waiting on a metrics lock. A snapshot may straddle one append,
    but every individual value and every reservoir remains bounded.
    """

    def __init__(self) -> None:
        self._durations = {
            kind: deque(maxlen=METRIC_RESERVOIR_CAPACITY) for kind in _DURATION_METRICS
        }
        self._counters = {kind: 0 for kind in PresentationMetricKind if kind not in _DURATION_METRICS}

    def record_duration(self, kind: PresentationMetricKind, nanoseconds: int) -> None:
        _require_metric_kind(kind)
        if kind not in _DURATION_METRICS:
            raise ValueError(f"{kind.value} is not a duration metric")
        try:
            value = int(nanoseconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TypeError("duration must be an integer") from exc
        self._durations[kind].append(max(0, value))

    def increment(self, kind: PresentationMetricKind) -> None:
        _require_metric_kind(kind)
        if kind in _DURATION_METRICS:
            raise ValueError(f"{kind.value} is not a counter metric")
        current = self._counters[kind]
        if current < MAX_METRIC_COUNTER:
            self._counters[kind] = current + 1

    def snapshot(self) -> PresentationMetricsSnapshot:
        return PresentationMetricsSnapshot(
            duration_values=tuple(
                (kind, tuple(self._durations[kind]))
                for kind in PresentationMetricKind
                if kind in _DURATION_METRICS
            ),
            counter_values=tuple(
                (kind, self._counters[kind])
                for kind in PresentationMetricKind
                if kind not in _DURATION_METRICS
            ),
        )


DEFAULT_PRESENTATION_METRICS = PresentationMetrics()


@dataclass(frozen=True, slots=True)
class PresentationTick:
    callback_timestamp: float
    target_timestamp: float
    generation: int


@dataclass(frozen=True, slots=True)
class ColorSample:
    generation: int
    sampled_at: float
    colors: Colors

    def __post_init__(self) -> None:
        if not isinstance(self.colors, tuple) or any(
            not isinstance(color, tuple) or len(color) != 4 for color in self.colors
        ):
            raise TypeError("colors must be a tuple of four-channel tuples")


@dataclass(frozen=True, slots=True)
class SamplePair:
    previous: ColorSample
    following: ColorSample


@dataclass(frozen=True, slots=True)
class SamplerCommand:
    generation: int
    program: str
    parse_anchor: float
    static_fallback_program: str
    sample_interval: float
    motion: MotionClass
    next_visual_change_at: float | None


def _valid_positive_finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0.0


def _fallback_timestamp(callback_timestamp: float, previous_target: float | None) -> float:
    callback = float(callback_timestamp) if _valid_positive_finite(callback_timestamp) else 0.0
    previous = float(previous_target) if _valid_positive_finite(previous_target) else 0.0
    return max(callback, previous)


def presentation_time(
    display_link: object,
    *,
    callback_timestamp: float,
    previous_target: float | None,
) -> float:
    """Return one validated display target, or one monotonic callback fallback."""

    try:
        target_method = getattr(display_link, "targetTimestamp")
        target = target_method()
        valid = _valid_positive_finite(target)
        if valid and _valid_positive_finite(previous_target):
            valid = float(target) >= float(previous_target)
        if valid:
            return float(target)
    except Exception:
        pass
    DEFAULT_PRESENTATION_METRICS.increment(PresentationMetricKind.TARGET_FALLBACK)
    return _fallback_timestamp(callback_timestamp, previous_target)


def _clamp_channel(value: object) -> float:
    try:
        channel = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(channel):
        return 0.0
    return min(1.0, max(0.0, channel))


def _validate_pair(pair: SamplePair) -> None:
    if pair.previous.generation != pair.following.generation:
        raise ValueError("sample generations differ")
    if len(pair.previous.colors) != len(pair.following.colors):
        raise ValueError("sample LED counts differ")
    if not math.isfinite(pair.previous.sampled_at) or not math.isfinite(pair.following.sampled_at):
        raise ValueError("sample timestamps must be finite")
    if pair.following.sampled_at < pair.previous.sampled_at:
        raise ValueError("sample timestamps regress")


def interpolate_sample(pair: SamplePair, target_timestamp: float) -> Colors:
    _validate_pair(pair)
    start = pair.previous.sampled_at
    end = pair.following.sampled_at
    if not math.isfinite(target_timestamp) or target_timestamp <= start or end == start:
        fraction = 0.0
    elif target_timestamp >= end:
        fraction = 1.0
    else:
        fraction = (target_timestamp - start) / (end - start)
    result: list[Color] = []
    for previous, following in zip(pair.previous.colors, pair.following.colors, strict=True):
        channels = tuple(
            _clamp_channel(float(before) + (float(after) - float(before)) * fraction)
            if math.isfinite(float(before)) and math.isfinite(float(after))
            else 0.0
            for before, after in zip(previous, following, strict=True)
        )
        result.append(channels)  # type: ignore[arg-type]
    return tuple(result)


class TwoSampleBuffer:
    """One atomic reference to exactly two immutable samples."""

    def __init__(self) -> None:
        self._pair: SamplePair | None = None

    def publish(self, pair: SamplePair) -> bool:
        try:
            _validate_pair(pair)
        except (TypeError, ValueError):
            return False
        current = self._pair
        generation = pair.previous.generation
        if current is not None:
            current_generation = current.previous.generation
            if generation < current_generation:
                return False
            if generation == current_generation and pair.previous.sampled_at < current.previous.sampled_at:
                return False
        self._pair = pair
        return True

    def read(self, generation: int) -> SamplePair | None:
        pair = self._pair
        if pair is None or pair.previous.generation != generation:
            return None
        return pair


def _safe_colors(colors: Colors) -> Colors:
    return tuple(tuple(_clamp_channel(channel) for channel in color) for color in colors)  # type: ignore[return-value]


def display_colors_for_tick(
    buffer: TwoSampleBuffer,
    tick: PresentationTick,
    *,
    last_safe_colors: Colors | None,
    static_fallback_colors: Colors,
) -> Colors:
    """Pure, bounded display-callback path over already sampled colors."""

    started = time.perf_counter_ns()
    pair = buffer.read(tick.generation)
    if pair is None:
        DEFAULT_PRESENTATION_METRICS.increment(PresentationMetricKind.STALE_SAMPLE)
        result = last_safe_colors if last_safe_colors is not None else static_fallback_colors
    else:
        result = interpolate_sample(pair, tick.target_timestamp)
        age_ns = int(max(0.0, tick.target_timestamp - pair.following.sampled_at) * 1_000_000_000.0)
        DEFAULT_PRESENTATION_METRICS.record_duration(PresentationMetricKind.SAMPLE_AGE_NS, age_ns)
    DEFAULT_PRESENTATION_METRICS.record_duration(
        PresentationMetricKind.DISPLAY_CALLBACK_NS, time.perf_counter_ns() - started
    )
    return _safe_colors(result)


class _ParseResult(Protocol):
    ok: bool


class _SamplerController(Protocol):
    def parse(self, program: str, now_ms: int) -> _ParseResult: ...

    def step(self, now_ms: int) -> Sequence[Sequence[int]]: ...


def _default_controller_factory(*, led_count: int) -> _SamplerController:
    from .led_wasm import SdLedWasmController

    return SdLedWasmController(led_count)


_EMPTY = object()


class ScreenBarSampler:
    """One serial worker with a capacity-one latest-wins command mailbox."""

    def __init__(
        self,
        buffer: TwoSampleBuffer,
        *,
        controller_factory: Callable[[], _SamplerController] | None = None,
        led_count: int = 8,
        metrics: PresentationMetrics | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._buffer = buffer
        self._led_count = 2 if int(led_count) == 2 else 8
        self._controller_factory = controller_factory or (
            lambda: _default_controller_factory(led_count=self._led_count)
        )
        self._metrics = metrics or DEFAULT_PRESENTATION_METRICS
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._pending: object | SamplerCommand | None = _EMPTY
        self._closed = False
        self._busy = False
        self._published_generation: int | None = None
        self._batch_queue: list[tuple[int, Sequence[Sequence[int]]]] = []
        self._worker_ident: int | None = None
        self._worker = threading.Thread(
            target=self._run,
            name="sidepulse-screen-bar-sampler",
            daemon=True,
        )
        self._worker.start()

    @property
    def worker_ident(self) -> int | None:
        return self._worker_ident

    def reconcile(self, command: SamplerCommand | None) -> None:
        with self._condition:
            if self._closed:
                return
            if self._pending is not _EMPTY:
                self._metrics.increment(PresentationMetricKind.DROPPED_COMMAND)
            self._pending = command
            self._condition.notify()

    def wait_until_published(self, generation: int, *, timeout_seconds: float) -> bool:
        deadline = self._monotonic() + max(0.0, timeout_seconds)
        with self._condition:
            while self._published_generation != generation and not self._closed:
                remaining = deadline - self._monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return self._published_generation == generation

    def wait_until_idle(self, *, timeout_seconds: float) -> bool:
        deadline = self._monotonic() + max(0.0, timeout_seconds)
        with self._condition:
            while (self._busy or self._pending is not _EMPTY) and not self._closed:
                remaining = deadline - self._monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return not self._busy and self._pending is _EMPTY

    def close(self, *, timeout_seconds: float) -> bool:
        with self._condition:
            self._closed = True
            self._pending = _EMPTY
            self._condition.notify_all()
        if threading.get_ident() == self._worker_ident:
            return False
        self._worker.join(max(0.0, timeout_seconds))
        return not self._worker.is_alive()

    def _take_command(self) -> SamplerCommand | object | None:
        with self._condition:
            while self._pending is _EMPTY and not self._closed:
                self._condition.wait()
            if self._closed:
                return _EMPTY
            command = self._pending
            self._pending = _EMPTY
            self._busy = command is not None
            self._condition.notify_all()
            return command

    def _is_superseded(self) -> bool:
        with self._condition:
            return self._closed or self._pending is not _EMPTY

    def _wait_sample_interval(self, deadline: float) -> bool:
        with self._condition:
            while not self._closed and self._pending is _EMPTY:
                remaining = deadline - self._monotonic()
                if remaining <= 0.0:
                    return True
                self._condition.wait(remaining)
            return False

    def _mark_idle(self, generation: int | None = None) -> None:
        with self._condition:
            self._busy = False
            if generation is not None:
                self._published_generation = generation
            self._condition.notify_all()

    def _run(self) -> None:
        self._worker_ident = threading.get_ident()
        try:
            controller = self._controller_factory()
        except Exception:
            with self._condition:
                self._closed = True
                self._busy = False
                self._condition.notify_all()
            return
        while True:
            command = self._take_command()
            if command is _EMPTY:
                return
            if command is None:
                self._mark_idle()
                continue
            self._execute_command(controller, command)

    def _execute_command(
        self, controller: _SamplerController, command: SamplerCommand
    ) -> None:
        started = time.perf_counter_ns()
        try:
            effective = self._prepare_program(controller, command)
            if effective is None or self._is_superseded():
                return
            interval = _sample_interval(effective.sample_interval)
            previous_at = self._monotonic()
            previous = self._step(controller, effective.generation, previous_at)
            if previous is None:
                return
            following_at = previous_at + interval
            if not self._wait_sample_interval(following_at):
                return
            following = self._step(controller, effective.generation, following_at)
            if following is None or self._is_superseded():
                return
            pair = SamplePair(previous, following)
            if not self._buffer.publish(pair):
                return
            self._notify_published(effective.generation)

            if effective.motion is MotionClass.STATIC:
                return
            self._sample_motion(controller, effective, pair, interval)
        except Exception:
            return
        finally:
            self._metrics.record_duration(
                PresentationMetricKind.SAMPLE_WORK_NS, time.perf_counter_ns() - started
            )
            self._mark_idle()

    def _prepare_program(
        self, controller: _SamplerController, command: SamplerCommand
    ) -> SamplerCommand | None:
        program = command.program
        if command.motion is MotionClass.FINITE and (
            not _valid_positive_finite(command.next_visual_change_at)
            or self._monotonic() >= float(command.next_visual_change_at)
        ):
            program = command.static_fallback_program
        if self._parse(controller, program, command.parse_anchor):
            if program == command.program:
                return command
            return SamplerCommand(
                command.generation,
                program,
                command.parse_anchor,
                command.static_fallback_program,
                command.sample_interval,
                MotionClass.STATIC,
                None,
            )
        if self._buffer.read(command.generation) is not None:
            return None
        if program == command.static_fallback_program:
            return None
        if not self._parse(
            controller, command.static_fallback_program, command.parse_anchor
        ):
            return None
        return SamplerCommand(
            command.generation,
            command.static_fallback_program,
            command.parse_anchor,
            command.static_fallback_program,
            command.sample_interval,
            MotionClass.STATIC,
            None,
        )

    @staticmethod
    def _parse(
        controller: _SamplerController, program: str, parse_anchor: float
    ) -> bool:
        if not isinstance(program, str) or not program:
            return False
        if not _valid_positive_finite(parse_anchor):
            return False
        result = controller.parse(program, int(parse_anchor * 1000.0))
        return bool(getattr(result, "ok", False))

    _STEP_BATCH_FRAMES = 24

    def _pixels_for(
        self,
        controller: _SamplerController,
        sampled_at: float,
        interval: float | None,
    ) -> Sequence[Sequence[int]] | None:
        """One frame, prefetching a batch when the engine supports it.

        Per-frame JavaScriptCore calls were the app's hottest path while
        the bar animates; a batch renders N future frames in one call and
        the queue serves them as their timestamps come due. Any timing
        mismatch (a stall, a new cadence) simply drops the queue."""
        now_ms = int(sampled_at * 1000.0)
        step_batch = getattr(controller, "step_batch", None)
        if interval is None or not callable(step_batch):
            return controller.step(now_ms)
        interval_ms = max(1, int(round(interval * 1000.0)))
        queue = self._batch_queue
        if queue:
            expected_ms, frame = queue[0]
            if abs(expected_ms - now_ms) <= 1:
                queue.pop(0)
                return frame
            self._batch_queue = queue = []
        try:
            frames = step_batch(now_ms, interval_ms, self._STEP_BATCH_FRAMES)
        except Exception:
            return controller.step(now_ms)
        if not frames:
            return controller.step(now_ms)
        for offset, frame in enumerate(frames[1:], start=1):
            # Expected stamps must use the same float arithmetic the
            # motion loop uses (sampled_at + k*interval). Stepping by the
            # rounded interval_ms instead drifts ~1/3ms per frame at
            # 30/60fps, blowing the +/-1ms gate a few frames in and
            # discarding most of every batch the engine rendered.
            queue.append((int((sampled_at + offset * interval) * 1000.0), frame))
        return frames[0]

    def _step(
        self,
        controller: _SamplerController,
        generation: int,
        sampled_at: float,
        *,
        interval: float | None = None,
    ) -> ColorSample | None:
        pixels = self._pixels_for(controller, sampled_at, interval)
        if pixels is None:
            return None
        if len(pixels) != self._led_count:
            return None
        colors: list[Color] = []
        for pixel in pixels:
            if len(pixel) != 3:
                return None
            channels: list[float] = []
            for raw in pixel:
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    return None
                value = float(raw)
                if not math.isfinite(value) or value < 0.0 or value > 255.0:
                    return None
                channels.append(value / 255.0)
            colors.append((channels[0], channels[1], channels[2], 1.0))
        return ColorSample(generation, sampled_at, tuple(colors))

    def _notify_published(self, generation: int) -> None:
        with self._condition:
            self._published_generation = generation
            self._condition.notify_all()

    def _sample_motion(
        self,
        controller: _SamplerController,
        command: SamplerCommand,
        pair: SamplePair,
        interval: float,
    ) -> None:
        current = pair
        self._batch_queue = []
        while not self._is_superseded():
            next_at = current.following.sampled_at + interval
            if command.motion is MotionClass.FINITE and (
                command.next_visual_change_at is None
                or next_at > command.next_visual_change_at
            ):
                return
            if not self._wait_sample_interval(next_at):
                return
            following = self._step(
                controller, command.generation, next_at, interval=interval
            )
            if following is None or self._is_superseded():
                return
            current = SamplePair(current.following, following)
            if not self._buffer.publish(current):
                return
            self._notify_published(command.generation)


def _sample_interval(requested: float) -> float:
    if not _valid_positive_finite(requested):
        return MIN_SAMPLE_INTERVAL_SECONDS
    return max(MIN_SAMPLE_INTERVAL_SECONDS, float(requested))
