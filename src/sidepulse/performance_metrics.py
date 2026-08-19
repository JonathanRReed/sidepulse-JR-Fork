"""Bounded, content-free runtime timing telemetry for SidePulse."""

from __future__ import annotations

import math
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass

MAX_METRICS = 64
MAX_SAMPLES_PER_METRIC = 512
MAX_METRIC_NAME_CHARS = 64
MAX_OUTCOME_CHARS = 32


@dataclass(frozen=True, slots=True)
class TimingSample:
    duration_ms: float
    observed_at: float
    main_thread: bool
    outcome: str

    def __post_init__(self) -> None:
        if not (
            isinstance(self.duration_ms, (int, float))
            and not isinstance(self.duration_ms, bool)
            and math.isfinite(float(self.duration_ms))
            and float(self.duration_ms) >= 0.0
            and isinstance(self.observed_at, (int, float))
            and not isinstance(self.observed_at, bool)
            and math.isfinite(float(self.observed_at))
            and float(self.observed_at) >= 0.0
            and type(self.main_thread) is bool
            and type(self.outcome) is str
            and 1 <= len(self.outcome) <= MAX_OUTCOME_CHARS
            and self.outcome.isprintable()
        ):
            raise ValueError("invalid timing sample")


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    name: str
    count: int
    p50_ms: float
    p95_ms: float
    maximum_ms: float
    latest_ms: float
    main_thread_count: int
    error_count: int
    outcomes: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "count": self.count,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "maximum_ms": self.maximum_ms,
            "latest_ms": self.latest_ms,
            "main_thread_count": self.main_thread_count,
            "error_count": self.error_count,
            "outcomes": dict(self.outcomes),
        }


@dataclass(frozen=True, slots=True)
class PerformanceSnapshot:
    generated_at: float
    metrics: tuple[MetricSnapshot, ...]

    def metric(self, name: str) -> MetricSnapshot | None:
        return next((metric for metric in self.metrics if metric.name == name), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "metrics": [metric.to_dict() for metric in self.metrics],
        }


def _quantile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


class PerformanceRegistry:
    def __init__(
        self,
        *,
        maximum_metrics: int = MAX_METRICS,
        maximum_samples: int = MAX_SAMPLES_PER_METRIC,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._maximum_metrics = max(1, int(maximum_metrics))
        self._maximum_samples = max(1, int(maximum_samples))
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._samples: dict[str, deque[TimingSample]] = {}

    def record(
        self,
        name: str,
        duration_ms: float,
        *,
        outcome: str = "ok",
        main_thread: bool | None = None,
    ) -> None:
        metric_name = str(name).strip()
        result = str(outcome).strip()
        if not (
            1 <= len(metric_name) <= MAX_METRIC_NAME_CHARS
            and metric_name.isprintable()
            and 1 <= len(result) <= MAX_OUTCOME_CHARS
            and result.isprintable()
        ):
            raise ValueError("invalid performance metric identity")
        sample = TimingSample(
            max(0.0, float(duration_ms)),
            self._monotonic(),
            (
                threading.current_thread() is threading.main_thread()
                if main_thread is None
                else bool(main_thread)
            ),
            result,
        )
        with self._lock:
            bucket = self._samples.get(metric_name)
            if bucket is None:
                if len(self._samples) >= self._maximum_metrics:
                    oldest_name = min(
                        self._samples,
                        key=lambda item: self._samples[item][-1].observed_at,
                    )
                    del self._samples[oldest_name]
                bucket = deque(maxlen=self._maximum_samples)
                self._samples[metric_name] = bucket
            bucket.append(sample)

    def measure(self, name: str, *, outcome: str = "ok"):
        return _Measurement(self, name, outcome)

    def snapshot(self) -> PerformanceSnapshot:
        with self._lock:
            copied = {
                name: tuple(samples)
                for name, samples in self._samples.items()
                if samples
            }
        metrics = []
        for name in sorted(copied):
            samples = copied[name]
            durations = [float(sample.duration_ms) for sample in samples]
            outcomes = Counter(sample.outcome for sample in samples)
            metrics.append(
                MetricSnapshot(
                    name=name,
                    count=len(samples),
                    p50_ms=_quantile(durations, 0.50),
                    p95_ms=_quantile(durations, 0.95),
                    maximum_ms=max(durations),
                    latest_ms=durations[-1],
                    main_thread_count=sum(sample.main_thread for sample in samples),
                    error_count=outcomes.get("error", 0),
                    outcomes=tuple(sorted(outcomes.items())),
                )
            )
        return PerformanceSnapshot(self._monotonic(), tuple(metrics))

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()


class _Measurement:
    def __init__(self, registry: PerformanceRegistry, name: str, outcome: str) -> None:
        self._registry = registry
        self._name = name
        self._outcome = outcome
        self._started_at: float | None = None

    def __enter__(self):
        self._started_at = time.perf_counter()
        return self

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        started = self._started_at
        if started is None:
            return False
        outcome = self._outcome if exc_type is None else "error"
        self._registry.record(
            self._name,
            (time.perf_counter() - started) * 1000.0,
            outcome=outcome,
        )
        return False
