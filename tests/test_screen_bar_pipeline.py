from __future__ import annotations

import math
import threading
import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from sidepulse.presentation_policy import MotionClass
from sidepulse.screen_bar_pipeline import (
    DEFAULT_PRESENTATION_METRICS,
    MAX_METRIC_COUNTER,
    ColorSample,
    PresentationMetricKind,
    PresentationMetrics,
    PresentationTick,
    SamplePair,
    SamplerCommand,
    ScreenBarSampler,
    TwoSampleBuffer,
    display_colors_for_tick,
    interpolate_sample,
    presentation_time,
)

RGBA = tuple[tuple[float, float, float, float], ...]


def _colors(*values: float) -> RGBA:
    return tuple((value, value, value, value) for value in values)


def _sample(generation: int, sampled_at: float, *values: float) -> ColorSample:
    return ColorSample(generation, sampled_at, _colors(*values))


def _pair(
    generation: int = 1,
    previous_at: float = 10.0,
    following_at: float = 12.0,
    previous: tuple[float, ...] = (0.0,),
    following: tuple[float, ...] = (1.0,),
) -> SamplePair:
    return SamplePair(
        _sample(generation, previous_at, *previous),
        _sample(generation, following_at, *following),
    )


class _DisplayLink:
    def __init__(self, target: object = 10.0) -> None:
        self.target = target
        self.calls = 0

    def targetTimestamp(self) -> object:
        self.calls += 1
        if isinstance(self.target, BaseException):
            raise self.target
        return self.target


@pytest.mark.parametrize(
    "target",
    [None, 0.0, -1.0, math.nan, math.inf, -math.inf, RuntimeError("unavailable")],
)
def test_presentation_time_falls_back_once_for_invalid_targets(target: object) -> None:
    """Catches an invalid target escaping validation or a retry on the frame path."""
    link = _DisplayLink(target)
    before = DEFAULT_PRESENTATION_METRICS.snapshot().counter(
        PresentationMetricKind.TARGET_FALLBACK
    )

    assert presentation_time(
        link, callback_timestamp=9.5, previous_target=9.0
    ) == pytest.approx(9.5)
    assert link.calls == 1
    after = DEFAULT_PRESENTATION_METRICS.snapshot().counter(
        PresentationMetricKind.TARGET_FALLBACK
    )
    assert after == min(before + 1, MAX_METRIC_COUNTER)


def test_presentation_time_falls_back_for_missing_or_regressing_target() -> None:
    """Catches feature probing or monotonic validation being removed."""
    assert presentation_time(
        object(), callback_timestamp=20.0, previous_target=19.0
    ) == pytest.approx(20.0)
    assert presentation_time(
        _DisplayLink(18.0), callback_timestamp=20.0, previous_target=19.0
    ) == pytest.approx(20.0)


def test_presentation_time_prefers_finite_positive_monotonic_target() -> None:
    """Catches callback arrival time replacing the display's presentation clock."""
    link = _DisplayLink(20.25)

    assert presentation_time(
        link, callback_timestamp=19.75, previous_target=20.0
    ) == pytest.approx(20.25)
    assert link.calls == 1


def test_presentation_time_keeps_fallback_monotonic_when_callback_regresses() -> None:
    """Catches an invalid display target moving presentation time backwards."""
    assert presentation_time(
        _DisplayLink(0.0), callback_timestamp=9.0, previous_target=10.0
    ) == pytest.approx(10.0)


def test_color_samples_and_ticks_are_immutable() -> None:
    """Catches a producer mutating a sample after its atomic publication."""
    sample = _sample(1, 1.0, 0.25)
    tick = PresentationTick(1.0, 1.1, 1)

    with pytest.raises(FrozenInstanceError):
        sample.sampled_at = 2.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        tick.generation = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        ColorSample(1, 1.0, [(0.0, 0.0, 0.0, 1.0)])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("target", "expected"),
    [(9.0, 0.0), (10.0, 0.0), (10.5, 0.25), (11.0, 0.5), (12.0, 1.0), (13.0, 1.0)],
)
def test_interpolation_uses_exact_endpoints_and_clamps_time(
    target: float, expected: float
) -> None:
    """Catches extrapolation or callback-count interpolation."""
    assert interpolate_sample(_pair(), target) == _colors(expected)


def test_interpolation_clamps_every_channel() -> None:
    """Catches malformed worker channels escaping the bounded display result."""
    pair = SamplePair(
        ColorSample(1, 1.0, ((-1.0, 0.25, 2.0, math.nan),)),
        ColorSample(1, 2.0, ((3.0, -2.0, 0.5, math.inf),)),
    )

    assert interpolate_sample(pair, 1.5) == ((1.0, 0.0, 1.0, 0.0),)


@pytest.mark.parametrize(
    "pair",
    [
        SamplePair(_sample(1, 1.0, 0.0), _sample(2, 2.0, 1.0)),
        SamplePair(_sample(1, 1.0, 0.0), _sample(1, 2.0, 1.0, 1.0)),
        SamplePair(_sample(1, 2.0, 0.0), _sample(1, 1.0, 1.0)),
    ],
)
def test_interpolation_rejects_generation_count_and_time_mismatch(
    pair: SamplePair,
) -> None:
    """Catches a torn pair producing a visually plausible but mixed frame."""
    with pytest.raises(ValueError):
        interpolate_sample(pair, 1.5)


def test_two_sample_buffer_rejects_stale_and_torn_publications() -> None:
    """Catches an older worker result replacing the current generation pair."""
    buffer = TwoSampleBuffer()
    first = _pair(generation=2, previous_at=2.0, following_at=3.0)
    newer = _pair(generation=2, previous_at=3.0, following_at=4.0)

    assert buffer.publish(first) is True
    assert buffer.publish(_pair(generation=1)) is False
    assert buffer.publish(_pair(generation=2, previous_at=1.0, following_at=2.0)) is False
    assert buffer.publish(newer) is True
    assert buffer.read(1) is None
    assert buffer.read(2) is newer


def test_two_sample_buffer_rejects_invalid_pair_without_losing_safe_pair() -> None:
    """Catches malformed publication clearing the last atomic safe reference."""
    buffer = TwoSampleBuffer()
    safe = _pair(generation=3)
    invalid = SamplePair(_sample(3, 1.0, 0.0), _sample(4, 2.0, 1.0))
    assert buffer.publish(safe)

    assert buffer.publish(invalid) is False
    assert buffer.read(3) is safe


def test_display_path_is_target_driven_and_uses_only_published_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches forbidden sampler, parser, I/O, wait, environment, or logging work on a display tick."""
    buffer = TwoSampleBuffer()
    assert buffer.publish(_pair(previous_at=100.0, following_at=101.0))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("forbidden display-callback work")

    monkeypatch.setattr(time, "sleep", forbidden)
    monkeypatch.setattr(threading.Condition, "wait", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("os.getenv", forbidden)
    monkeypatch.setattr("json.loads", forbidden)
    monkeypatch.setattr("logging.Logger._log", forbidden)

    early = PresentationTick(99.90, 100.25, 1)
    late = PresentationTick(100.20, 100.25, 1)
    expected = _colors(0.25)
    assert display_colors_for_tick(
        buffer, early, last_safe_colors=None, static_fallback_colors=_colors(0.1)
    ) == expected
    assert display_colors_for_tick(
        buffer, late, last_safe_colors=None, static_fallback_colors=_colors(0.1)
    ) == expected


def test_display_path_reuses_last_safe_then_static_fallback_without_worker_work() -> None:
    """Catches a late sampler causing synchronous parsing or a blank frame."""
    buffer = TwoSampleBuffer()
    tick = PresentationTick(10.0, 10.1, 8)

    assert display_colors_for_tick(
        buffer,
        tick,
        last_safe_colors=_colors(0.75),
        static_fallback_colors=_colors(0.25),
    ) == _colors(0.75)
    assert display_colors_for_tick(
        buffer,
        tick,
        last_safe_colors=None,
        static_fallback_colors=_colors(0.25),
    ) == _colors(0.25)


def test_metrics_reservoirs_counters_and_labels_are_bounded() -> None:
    """Catches per-frame metrics growing without bound or accepting identifier labels."""
    metrics = PresentationMetrics()
    for value in range(10_000):
        metrics.record_duration(PresentationMetricKind.SAMPLE_WORK_NS, value)
    for _ in range(MAX_METRIC_COUNTER + 100):
        metrics.increment(PresentationMetricKind.DROPPED_COMMAND)

    snapshot = metrics.snapshot()
    assert len(snapshot.durations(PresentationMetricKind.SAMPLE_WORK_NS)) == 512
    assert snapshot.durations(PresentationMetricKind.SAMPLE_WORK_NS)[0] == 9_488
    assert snapshot.counter(PresentationMetricKind.DROPPED_COMMAND) == MAX_METRIC_COUNTER
    with pytest.raises(TypeError):
        metrics.increment("session-123")  # type: ignore[arg-type]


class _BlockingController:
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release
        self.programs: list[str] = []
        self.thread_ids: set[int] = set()
        self.step_times: list[int] = []

    def parse(self, program: str, _anchor_ms: int) -> object:
        self.programs.append(program)
        self.thread_ids.add(threading.get_ident())
        if program == "first":
            self.started.set()
            assert self.release.wait(2.0)
        return SimpleNamespace(ok=True)

    def step(self, now_ms: int) -> list[tuple[int, int, int]]:
        self.thread_ids.add(threading.get_ident())
        self.step_times.append(now_ms)
        return [(now_ms % 256, 64, 128), (0, 255, 32)]


def _command(
    generation: int,
    program: str,
    *,
    motion: MotionClass = MotionClass.STATIC,
    sample_interval: float = 1.0 / 120.0,
) -> SamplerCommand:
    return SamplerCommand(
        generation=generation,
        program=program,
        parse_anchor=100.0,
        static_fallback_program="fallback",
        sample_interval=sample_interval,
        motion=motion,
        next_visual_change_at=None,
    )


def test_sampler_mailbox_is_latest_wins_and_uses_one_worker() -> None:
    """Catches an unbounded command queue or per-command sampler thread."""
    started = threading.Event()
    release = threading.Event()
    controller = _BlockingController(started, release)
    metrics = PresentationMetrics()
    sampler = ScreenBarSampler(
        TwoSampleBuffer(),
        controller_factory=lambda: controller,
        led_count=2,
        metrics=metrics,
    )
    try:
        sampler.reconcile(_command(1, "first"))
        assert started.wait(2.0)
        sampler.reconcile(_command(2, "stale-pending"))
        sampler.reconcile(_command(3, "latest"))
        release.set()
        assert sampler.wait_until_published(3, timeout_seconds=2.0)

        assert controller.programs == ["first", "latest"]
        assert len(controller.thread_ids) == 1
        assert threading.get_ident() not in controller.thread_ids
        assert metrics.snapshot().counter(PresentationMetricKind.DROPPED_COMMAND) == 1
    finally:
        release.set()
        assert sampler.close(timeout_seconds=2.0)


def test_sampler_constructs_default_controller_only_on_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches WASM controller construction moving onto the reconciling caller."""
    started = threading.Event()
    release = threading.Event()
    controller = _BlockingController(started, release)
    construction_threads: list[int] = []

    def factory(*, led_count: int) -> _BlockingController:
        assert led_count == 2
        construction_threads.append(threading.get_ident())
        return controller

    monkeypatch.setattr(
        "sidepulse.screen_bar_pipeline._default_controller_factory", factory
    )
    sampler = ScreenBarSampler(TwoSampleBuffer(), led_count=2)
    try:
        sampler.reconcile(_command(1, "first"))
        assert started.wait(2.0)
        assert construction_threads == [sampler.worker_ident]
        assert threading.get_ident() not in construction_threads
    finally:
        release.set()
        assert sampler.close(timeout_seconds=2.0)


def test_sampler_lifecycle_none_replaces_pending_program() -> None:
    """Catches hide or sleep leaving stale pending sampler work queued."""
    started = threading.Event()
    release = threading.Event()
    controller = _BlockingController(started, release)
    metrics = PresentationMetrics()
    sampler = ScreenBarSampler(
        TwoSampleBuffer(), controller_factory=lambda: controller, metrics=metrics
    )
    try:
        sampler.reconcile(_command(1, "first"))
        assert started.wait(2.0)
        sampler.reconcile(_command(2, "must-not-run"))
        sampler.reconcile(None)
        release.set()
        time.sleep(0.03)

        assert controller.programs == ["first"]
        assert metrics.snapshot().counter(PresentationMetricKind.DROPPED_COMMAND) == 1
    finally:
        release.set()
        assert sampler.close(timeout_seconds=2.0)


def test_sampler_caps_logical_samples_at_60_hz_and_publishes_rgba_pair() -> None:
    """Catches 120 Hz callbacks doubling WASM source sampling."""
    controller = _BlockingController(threading.Event(), threading.Event())
    buffer = TwoSampleBuffer()
    sampler = ScreenBarSampler(
        buffer, controller_factory=lambda: controller, led_count=2
    )
    try:
        sampler.reconcile(_command(4, "static", sample_interval=1.0 / 120.0))
        assert sampler.wait_until_published(4, timeout_seconds=2.0)
        pair = buffer.read(4)
        assert pair is not None
        assert pair.following.sampled_at - pair.previous.sampled_at == pytest.approx(
            1.0 / 60.0
        )
        assert len(controller.step_times) == 2
        assert pair.previous.colors[0][3] == 1.0
        assert pair.following.colors[1][1] == 1.0
    finally:
        assert sampler.close(timeout_seconds=2.0)


class _FallbackController:
    def __init__(self) -> None:
        self.programs: list[str] = []
        self.thread_ids: set[int] = set()

    def parse(self, program: str, _anchor_ms: int) -> object:
        self.thread_ids.add(threading.get_ident())
        self.programs.append(program)
        return SimpleNamespace(ok=program == "safe-fallback")

    def step(self, _now_ms: int) -> list[tuple[int, int, int]]:
        self.thread_ids.add(threading.get_ident())
        return [(255, 0, 0)] * 2


def test_sampler_failure_publishes_static_fallback_on_worker() -> None:
    """Catches a parse failure publishing malformed output or parsing fallback on the caller."""
    controller = _FallbackController()
    buffer = TwoSampleBuffer()
    sampler = ScreenBarSampler(
        buffer, controller_factory=lambda: controller, led_count=2
    )
    command = SamplerCommand(
        generation=9,
        program="bad-program",
        parse_anchor=time.monotonic(),
        static_fallback_program="safe-fallback",
        sample_interval=1.0 / 60.0,
        motion=MotionClass.FINITE,
        next_visual_change_at=time.monotonic() + 2.0,
    )
    try:
        sampler.reconcile(command)
        assert sampler.wait_until_published(9, timeout_seconds=2.0)
        pair = buffer.read(9)
        assert pair is not None
        assert pair.previous.colors == ((1.0, 0.0, 0.0, 1.0),) * 2
        assert controller.programs == ["bad-program", "safe-fallback"]
        assert controller.thread_ids == {sampler.worker_ident}
    finally:
        assert sampler.close(timeout_seconds=2.0)


def test_sampler_failure_preserves_last_safe_pair_for_generation() -> None:
    """Catches a later failed sample clearing a current generation's safe frame."""
    buffer = TwoSampleBuffer()
    safe = _pair(generation=10)
    assert buffer.publish(safe)

    class BrokenController:
        def parse(self, _program: str, _anchor_ms: int) -> object:
            raise RuntimeError("parser failed")

    sampler = ScreenBarSampler(buffer, controller_factory=BrokenController)
    try:
        sampler.reconcile(_command(10, "bad"))
        assert sampler.wait_until_idle(timeout_seconds=2.0)
        assert buffer.read(10) is safe
    finally:
        assert sampler.close(timeout_seconds=2.0)
