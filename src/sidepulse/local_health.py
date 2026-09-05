"""Bounded, content-free current-run health aggregation for JR-Bar."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from .dnd_policy import DndMode, DndProjection, DndSource
from .performance_metrics import MetricSnapshot, PerformanceSnapshot
from .runtime_scheduler import RuntimeWorkerSnapshot
from .screen_bar_pipeline import (
    MAX_METRIC_COUNTER,
    MAX_METRIC_TOTAL_NS,
    PresentationMetricKind,
    PresentationMetricsSnapshot,
)

_WORK_DURATION_KINDS = (
    PresentationMetricKind.DISPLAY_CALLBACK_NS,
    PresentationMetricKind.SAMPLE_WORK_NS,
    PresentationMetricKind.ALCOVE_WORK_NS,
    PresentationMetricKind.GEOMETRY_BUILD_NS,
    PresentationMetricKind.PAINT_BUILD_NS,
)
MAX_DND_HEALTH_RETURN_EPOCH = 4_102_444_800.0


@dataclass(frozen=True, slots=True)
class LocalHealthTiming:
    count: int
    p50_ms: float
    p95_ms: float
    latest_ms: float

    @classmethod
    def from_metric(cls, metric: MetricSnapshot | None) -> LocalHealthTiming | None:
        if metric is None or metric.count <= 0:
            return None
        return cls(
            count=metric.count,
            p50_ms=metric.p50_ms,
            p95_ms=metric.p95_ms,
            latest_ms=metric.latest_ms,
        )

    @classmethod
    def from_nanoseconds(
        cls,
        values: Sequence[int],
        *,
        count: int,
    ) -> LocalHealthTiming | None:
        if not values or count <= 0:
            return None
        ordered = sorted(max(0, int(value)) for value in values)

        def percentile(fraction: float) -> float:
            index = max(
                0,
                min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1),
            )
            return ordered[index] / 1_000_000.0

        return cls(
            count=count,
            p50_ms=percentile(0.50),
            p95_ms=percentile(0.95),
            latest_ms=max(0, int(values[-1])) / 1_000_000.0,
        )

    def to_dict(self) -> dict[str, int | float]:
        return {
            "count": self.count,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "latest_ms": self.latest_ms,
        }


@dataclass(frozen=True, slots=True)
class LocalHealthDndStatus:
    available: bool
    modes: tuple[DndMode, ...] = ()
    sources: tuple[DndSource, ...] = ()
    return_epoch: float | None = None

    def __post_init__(self) -> None:
        if type(self.available) is not bool:
            raise TypeError("DND health availability must be a boolean")
        if type(self.modes) is not tuple or not all(
            type(mode) is DndMode for mode in self.modes
        ):
            raise TypeError("DND health modes must be typed")
        if type(self.sources) is not tuple or not all(
            type(source) is DndSource for source in self.sources
        ):
            raise TypeError("DND health sources must be typed")
        if len(self.modes) > 4 or len(set(self.modes)) != len(self.modes):
            raise ValueError("DND health modes must remain bounded")
        if len(self.sources) > 4 or len(set(self.sources)) != len(self.sources):
            raise ValueError("DND health sources must remain bounded")
        if self.return_epoch is not None and _finite_nonnegative(
            self.return_epoch
        ) is None:
            raise ValueError("DND health return time must be finite")
        if (
            self.return_epoch is not None
            and self.return_epoch > MAX_DND_HEALTH_RETURN_EPOCH
        ):
            raise ValueError("DND health return time exceeds the bounded range")
        if not self.available and (
            self.modes or self.sources or self.return_epoch is not None
        ):
            raise ValueError("unavailable DND health cannot contain facts")

    @classmethod
    def from_projection(cls, projection: object) -> LocalHealthDndStatus:
        if type(projection) is not DndProjection:
            return cls(False)
        return cls(
            True,
            tuple(dict.fromkeys(projection.active_modes)),
            projection.active_sources,
            projection.next_transition_epoch,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "modes": [mode.value for mode in self.modes],
            "sources": [source.value for source in self.sources],
            "return_epoch": self.return_epoch,
        }


@dataclass(frozen=True, slots=True)
class LocalHealthSnapshot:
    generated_at: float
    render_duty_cycle_percent: float | None
    screen_bar_renderer_latency: LocalHealthTiming | None
    dropped_batches: int | None
    delivered_fps: float | None
    queue_depth: int
    peak_queue_depth: int
    hardware_write_latency: LocalHealthTiming | None
    source_freshness_seconds: float | None
    worker_count: int
    active_worker_count: int
    shutdown_latency: LocalHealthTiming | None
    refresh_duration: LocalHealthTiming | None
    dnd_status: LocalHealthDndStatus

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "render_duty_cycle_percent": self.render_duty_cycle_percent,
            "screen_bar_renderer_latency": (
                self.screen_bar_renderer_latency.to_dict()
                if self.screen_bar_renderer_latency is not None
                else None
            ),
            "dropped_batches": self.dropped_batches,
            "delivered_fps": self.delivered_fps,
            "queue_depth": self.queue_depth,
            "peak_queue_depth": self.peak_queue_depth,
            "hardware_write_latency": (
                self.hardware_write_latency.to_dict()
                if self.hardware_write_latency is not None
                else None
            ),
            "source_freshness_seconds": self.source_freshness_seconds,
            "worker_count": self.worker_count,
            "active_worker_count": self.active_worker_count,
            "shutdown_latency": (
                self.shutdown_latency.to_dict()
                if self.shutdown_latency is not None
                else None
            ),
            "refresh_duration": (
                self.refresh_duration.to_dict()
                if self.refresh_duration is not None
                else None
            ),
            "dnd_status": self.dnd_status.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class _PresentationBaseline:
    observed_at: float
    work_total_ns: int
    work_count: int
    presented_frames: int
    processed_callbacks: int
    dropped_batches: int
    activity_observed: bool
    saturated: bool


def _finite_nonnegative(value: object) -> float | None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        return None
    return float(value)


def _presentation_baseline(
    snapshot: PresentationMetricsSnapshot,
    observed_at: float,
) -> _PresentationBaseline:
    work_totals = tuple(
        snapshot.duration_total(kind) for kind in _WORK_DURATION_KINDS
    )
    work_counts = tuple(
        snapshot.duration_count(kind) for kind in _WORK_DURATION_KINDS
    )
    work_total = sum(work_totals)
    work_count = sum(work_counts)
    presented = snapshot.counter(PresentationMetricKind.PRESENTED_FRAME)
    processed = snapshot.counter(PresentationMetricKind.PROCESSED_CALLBACK)
    suppressed = snapshot.counter(PresentationMetricKind.SUPPRESSED_CALLBACK)
    batch_fallbacks = snapshot.counter(PresentationMetricKind.BATCH_FALLBACK)
    batch_invalidations = snapshot.counter(
        PresentationMetricKind.BATCH_INVALIDATED
    )
    dropped = batch_fallbacks + batch_invalidations
    activity = bool(work_count or presented or processed or suppressed or dropped)
    saturated = bool(
        any(total >= MAX_METRIC_TOTAL_NS for total in work_totals)
        or any(count >= MAX_METRIC_COUNTER for count in work_counts)
        or presented >= MAX_METRIC_COUNTER
        or batch_fallbacks >= MAX_METRIC_COUNTER
        or batch_invalidations >= MAX_METRIC_COUNTER
        or dropped >= MAX_METRIC_COUNTER
    )
    return _PresentationBaseline(
        observed_at=observed_at,
        work_total_ns=work_total,
        work_count=work_count,
        presented_frames=presented,
        processed_callbacks=processed,
        dropped_batches=min(MAX_METRIC_COUNTER, dropped),
        activity_observed=activity,
        saturated=saturated,
    )


class LocalHealthMonitor:
    """Aggregate existing bounded owners without retaining source content."""

    def __init__(self, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        if not callable(monotonic):
            raise ValueError("invalid local health clock")
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._previous_presentation: _PresentationBaseline | None = None
        self._presentation_observed = False
        self._peak_queue_depth = 0

    def observe(
        self,
        *,
        presentation: PresentationMetricsSnapshot,
        performance: PerformanceSnapshot,
        workers: Sequence[RuntimeWorkerSnapshot],
        source_ages_seconds: Sequence[float],
        dnd_projection: DndProjection | None = None,
    ) -> LocalHealthSnapshot:
        if not isinstance(presentation, PresentationMetricsSnapshot):
            raise ValueError("invalid presentation health snapshot")
        if not isinstance(performance, PerformanceSnapshot):
            raise ValueError("invalid performance health snapshot")
        if any(not isinstance(worker, RuntimeWorkerSnapshot) for worker in workers):
            raise ValueError("invalid worker health snapshot")
        observed_at = _finite_nonnegative(self._monotonic())
        if observed_at is None:
            raise RuntimeError("local health clock returned an invalid value")
        current = _presentation_baseline(presentation, observed_at)
        queue_depth = sum(worker.pending_count + worker.result_count for worker in workers)
        active_worker_count = sum(worker.thread_alive for worker in workers)
        valid_ages = tuple(
            valid
            for age in source_ages_seconds
            if (valid := _finite_nonnegative(age)) is not None
        )

        with self._lock:
            previous = self._previous_presentation
            self._previous_presentation = current
            self._peak_queue_depth = max(self._peak_queue_depth, queue_depth)
            render_duty: float | None = None
            delivered_fps: float | None = None
            reset_or_invalid = bool(
                previous is not None
                and (
                    current.observed_at <= previous.observed_at
                    or current.work_total_ns < previous.work_total_ns
                    or current.work_count < previous.work_count
                    or current.presented_frames < previous.presented_frames
                    or current.processed_callbacks < previous.processed_callbacks
                    or current.dropped_batches < previous.dropped_batches
                    or current.saturated
                    or previous.saturated
                )
            )
            if reset_or_invalid:
                self._presentation_observed = current.activity_observed
            elif previous is not None and (
                self._presentation_observed or current.activity_observed
            ):
                elapsed = current.observed_at - previous.observed_at
                work_delta = current.work_total_ns - previous.work_total_ns
                frame_delta = current.presented_frames - previous.presented_frames
                if elapsed > 0.0:
                    render_duty = min(
                        100.0,
                        max(0.0, work_delta / (elapsed * 1_000_000_000.0) * 100.0),
                    )
                    delivered_fps = max(0.0, frame_delta / elapsed)
                self._presentation_observed = True
            else:
                self._presentation_observed = bool(current.activity_observed)

            dropped_batches = (
                current.dropped_batches if self._presentation_observed else None
            )
            peak_queue_depth = self._peak_queue_depth

        return LocalHealthSnapshot(
            generated_at=observed_at,
            render_duty_cycle_percent=render_duty,
            screen_bar_renderer_latency=LocalHealthTiming.from_nanoseconds(
                presentation.durations(
                    PresentationMetricKind.DISPLAY_CALLBACK_NS
                ),
                count=presentation.duration_count(
                    PresentationMetricKind.DISPLAY_CALLBACK_NS
                ),
            ),
            dropped_batches=dropped_batches,
            delivered_fps=delivered_fps,
            queue_depth=queue_depth,
            peak_queue_depth=peak_queue_depth,
            hardware_write_latency=LocalHealthTiming.from_metric(
                performance.metric("hardware_render")
            ),
            source_freshness_seconds=max(valid_ages, default=None),
            worker_count=len(workers),
            active_worker_count=active_worker_count,
            shutdown_latency=LocalHealthTiming.from_metric(
                performance.metric("shutdown")
            ),
            refresh_duration=LocalHealthTiming.from_metric(
                performance.metric("refresh")
            ),
            dnd_status=LocalHealthDndStatus.from_projection(dnd_projection),
        )


def _timing_text(value: LocalHealthTiming | None) -> str:
    if value is None:
        return "Unavailable"
    return (
        f"P50 {value.p50_ms:.1f} ms, P95 {value.p95_ms:.1f} ms, "
        f"latest {value.latest_ms:.1f} ms, n={value.count}"
    )


def format_local_health(snapshot: LocalHealthSnapshot) -> str:
    """Render exactly nine fixed rows without dynamic identities or content."""
    if not isinstance(snapshot, LocalHealthSnapshot):
        raise ValueError("invalid local health projection")
    unavailable = "Unavailable"
    dnd = snapshot.dnd_status
    dnd_text = "DND Unavailable"
    if dnd.available:
        mode_labels = {
            DndMode.MUTE: "Mute",
            DndMode.DIM: "Dim",
            DndMode.PAUSE: "Pause",
            DndMode.ASKS_ONLY: "Asks Only",
            DndMode.DARK: "Fully Dark",
        }
        source_labels = {
            DndSource.MANUAL: "Manual",
            DndSource.SCHEDULE: "Scheduled",
            DndSource.MACOS_FOCUS: "macOS Focus",
            DndSource.NAMED_FOCUS: "Named Focus",
        }
        modes = "+".join(mode_labels[value] for value in dnd.modes) or (
            "Named Rule" if dnd.sources else "Off"
        )
        sources = "+".join(source_labels[value] for value in dnd.sources) or "None"
        source_label = "source" if len(dnd.sources) == 1 else "sources"
        returns = (
            datetime.fromtimestamp(dnd.return_epoch, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%MZ"
            )
            if dnd.return_epoch is not None
            else "Unscheduled"
        )
        dnd_text = f"DND {modes}; {source_label} {sources}; returns {returns}"
    return "\n".join(
        (
            "Local Health (current run, never sent)",
            "Render duty cycle: "
            + (
                f"{snapshot.render_duty_cycle_percent:.1f}%"
                if snapshot.render_duty_cycle_percent is not None
                else unavailable
            ),
            "Dropped batches: "
            + (
                str(snapshot.dropped_batches)
                if snapshot.dropped_batches is not None
                else unavailable
            ),
            "Delivered FPS: "
            + (
                f"{snapshot.delivered_fps:.1f}"
                if snapshot.delivered_fps is not None
                else unavailable
            ),
            f"Runtime queue depth: {snapshot.queue_depth} current, "
            f"{snapshot.peak_queue_depth} peak",
            "Hardware write latency: "
            + _timing_text(snapshot.hardware_write_latency),
            "Source freshness: "
            + (
                f"oldest visible update {snapshot.source_freshness_seconds:.1f} s"
                if snapshot.source_freshness_seconds is not None
                else unavailable
            )
            + f"; {dnd_text}",
            f"Runtime workers: {snapshot.worker_count} registered, "
            f"{snapshot.active_worker_count} live",
            "Shutdown latency: " + _timing_text(snapshot.shutdown_latency),
            "Refresh duration: " + _timing_text(snapshot.refresh_duration),
        )
    )


__all__ = [
    "MAX_DND_HEALTH_RETURN_EPOCH",
    "LocalHealthDndStatus",
    "LocalHealthMonitor",
    "LocalHealthSnapshot",
    "LocalHealthTiming",
    "format_local_health",
]
