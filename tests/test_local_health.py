from __future__ import annotations

from dataclasses import replace

import pytest

from sidepulse.dnd_policy import (
    DndMode,
    DndSource,
    compose_dnd_contributions,
    contribution_for_mode,
)
from sidepulse.local_health import LocalHealthMonitor, format_local_health
from sidepulse.performance_metrics import PerformanceRegistry
from sidepulse.runtime_scheduler import RuntimeWorkerDomain, RuntimeWorkerSnapshot
from sidepulse.screen_bar_pipeline import (
    MAX_METRIC_COUNTER,
    PresentationMetricKind,
    PresentationMetrics,
)


def _worker(**changes) -> RuntimeWorkerSnapshot:
    return replace(
        RuntimeWorkerSnapshot.empty(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            accepting=True,
        ),
        **changes,
    )


def test_health_monitor_projects_all_nine_content_free_aggregates() -> None:
    now = [100.0]
    monitor = LocalHealthMonitor(monotonic=lambda: now[0])
    presentation = PresentationMetrics()
    performance = PerformanceRegistry(monotonic=lambda: now[0])
    performance.record("hardware_render", 10)
    performance.record("hardware_render", 30)
    performance.record("refresh", 5)
    first = monitor.observe(
        presentation=presentation.snapshot(),
        performance=performance.snapshot(),
        workers=(_worker(pending_count=2, result_count=1, thread_alive=True),),
        source_ages_seconds=(3.0, 10.0),
    )

    assert first.render_duty_cycle_percent is None
    assert first.screen_bar_renderer_latency is None
    assert first.dropped_batches is None
    assert first.delivered_fps is None
    assert first.queue_depth == 3
    assert first.peak_queue_depth == 3
    assert first.hardware_write_latency is not None
    assert first.hardware_write_latency.p95_ms == 30
    assert first.source_freshness_seconds == 10
    assert first.worker_count == 1
    assert first.active_worker_count == 1
    assert first.shutdown_latency is None
    assert first.refresh_duration is not None
    assert first.refresh_duration.p95_ms == 5

    presentation.record_duration(
        PresentationMetricKind.DISPLAY_CALLBACK_NS,
        100_000_000,
    )
    presentation.record_duration(
        PresentationMetricKind.SAMPLE_WORK_NS,
        100_000_000,
    )
    for _ in range(40):
        presentation.increment(PresentationMetricKind.PRESENTED_FRAME)
        presentation.increment(PresentationMetricKind.PROCESSED_CALLBACK)
    presentation.increment(PresentationMetricKind.BATCH_FALLBACK)
    presentation.increment(PresentationMetricKind.BATCH_INVALIDATED)
    now[0] = 102.0

    second = monitor.observe(
        presentation=presentation.snapshot(),
        performance=performance.snapshot(),
        workers=(_worker(pending_count=1, thread_alive=True),),
        source_ages_seconds=(12.0,),
    )

    assert second.render_duty_cycle_percent == pytest.approx(10.0)
    assert second.screen_bar_renderer_latency is not None
    assert second.screen_bar_renderer_latency.count == 1
    assert second.screen_bar_renderer_latency.latest_ms == pytest.approx(100.0)
    assert second.dropped_batches == 2
    assert second.delivered_fps == pytest.approx(20.0)
    assert second.queue_depth == 1
    assert second.peak_queue_depth == 3
    assert second.source_freshness_seconds == 12


def test_counter_reset_and_saturation_rebaseline_rates_as_unavailable() -> None:
    now = [10.0]
    monitor = LocalHealthMonitor(monotonic=lambda: now[0])
    metrics = PresentationMetrics()
    performance = PerformanceRegistry()
    monitor.observe(
        presentation=metrics.snapshot(),
        performance=performance.snapshot(),
        workers=(),
        source_ages_seconds=(),
    )
    metrics.increment(PresentationMetricKind.PRESENTED_FRAME)
    metrics.record_duration(PresentationMetricKind.DISPLAY_CALLBACK_NS, 1_000)
    now[0] = 11.0
    assert monitor.observe(
        presentation=metrics.snapshot(),
        performance=performance.snapshot(),
        workers=(),
        source_ages_seconds=(),
    ).delivered_fps == 1.0

    metrics.reset()
    now[0] = 12.0
    reset = monitor.observe(
        presentation=metrics.snapshot(),
        performance=performance.snapshot(),
        workers=(),
        source_ages_seconds=(),
    )
    assert reset.delivered_fps is None
    assert reset.render_duty_cycle_percent is None
    assert reset.dropped_batches is None

    metrics._counters[PresentationMetricKind.PRESENTED_FRAME] = MAX_METRIC_COUNTER
    metrics._counters[PresentationMetricKind.PROCESSED_CALLBACK] = MAX_METRIC_COUNTER
    now[0] = 13.0
    saturated = monitor.observe(
        presentation=metrics.snapshot(),
        performance=performance.snapshot(),
        workers=(),
        source_ages_seconds=(),
    )
    assert saturated.delivered_fps is None
    assert saturated.render_duty_cycle_percent is None


def test_independent_duration_counters_do_not_false_saturate_when_summed() -> None:
    now = [20.0]
    monitor = LocalHealthMonitor(monotonic=lambda: now[0])
    metrics = PresentationMetrics()
    performance = PerformanceRegistry()
    for kind in (
        PresentationMetricKind.DISPLAY_CALLBACK_NS,
        PresentationMetricKind.SAMPLE_WORK_NS,
    ):
        metrics._duration_counts[kind] = MAX_METRIC_COUNTER // 2 + 1
        metrics._duration_totals[kind] = 1_000

    monitor.observe(
        presentation=metrics.snapshot(),
        performance=performance.snapshot(),
        workers=(),
        source_ages_seconds=(),
    )
    metrics.record_duration(PresentationMetricKind.DISPLAY_CALLBACK_NS, 1_000)
    metrics.increment(PresentationMetricKind.PRESENTED_FRAME)
    now[0] = 21.0

    observed = monitor.observe(
        presentation=metrics.snapshot(),
        performance=performance.snapshot(),
        workers=(),
        source_ages_seconds=(),
    )

    assert observed.render_duty_cycle_percent == pytest.approx(0.0001)
    assert observed.delivered_fps == 1.0


def test_invalid_or_unobserved_inputs_remain_unavailable() -> None:
    monitor = LocalHealthMonitor(monotonic=lambda: 10.0)
    snapshot = monitor.observe(
        presentation=PresentationMetrics().snapshot(),
        performance=PerformanceRegistry().snapshot(),
        workers=(),
        source_ages_seconds=(-1.0, float("nan"), float("inf")),
    )

    assert snapshot.render_duty_cycle_percent is None
    assert snapshot.dropped_batches is None
    assert snapshot.delivered_fps is None
    assert snapshot.queue_depth == 0
    assert snapshot.hardware_write_latency is None
    assert snapshot.source_freshness_seconds is None
    assert snapshot.worker_count == 0
    assert snapshot.active_worker_count == 0
    assert snapshot.shutdown_latency is None
    assert snapshot.refresh_duration is None


def test_formatter_has_exact_fixed_rows_and_excludes_unowned_metric_content() -> None:
    performance = PerformanceRegistry()
    performance.record("private/session/path", 1, outcome="https://secret.invalid")
    snapshot = LocalHealthMonitor(monotonic=lambda: 10.0).observe(
        presentation=PresentationMetrics().snapshot(),
        performance=performance.snapshot(),
        workers=(),
        source_ages_seconds=(),
    )

    rendered = format_local_health(snapshot)

    assert rendered.splitlines()[0] == "Local Health (current run, never sent)"
    assert len(rendered.splitlines()) == 10
    for label in (
        "Render duty cycle",
        "Dropped batches",
        "Delivered FPS",
        "Runtime queue depth",
        "Hardware write latency",
        "Source freshness",
        "Runtime workers",
        "Shutdown latency",
        "Refresh duration",
    ):
        assert rendered.count(f"{label}:") == 1
    assert rendered.count("Unavailable") >= 7
    assert "private" not in rendered
    assert "secret" not in rendered
    assert "https" not in rendered


def test_formatter_reuses_the_fixed_source_row_for_bounded_dnd_facts() -> None:
    snapshot = LocalHealthMonitor(monotonic=lambda: 10.0).observe(
        presentation=PresentationMetrics().snapshot(),
        performance=PerformanceRegistry().snapshot(),
        workers=(),
        source_ages_seconds=(),
        dnd_projection=compose_dnd_contributions(
            (contribution_for_mode(DndSource.SCHEDULE, DndMode.DIM),),
            next_transition_epoch=1_800_000_000.0,
        ),
    )

    rendered = format_local_health(snapshot)

    assert len(rendered.splitlines()) == 10
    assert (
        "Source freshness: Unavailable; DND Dim; source Scheduled; "
        "returns 2027-01-15 08:00Z"
    ) in rendered
