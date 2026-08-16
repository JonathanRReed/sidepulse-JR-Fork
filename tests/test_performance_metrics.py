from sidepulse.performance_metrics import PerformanceRegistry


def test_registry_reports_bounded_percentiles_and_error_counts() -> None:
    registry = PerformanceRegistry(maximum_samples=5, monotonic=lambda: 100.0)
    for value in (1, 2, 3, 4, 100, 200):
        registry.record(
            "refresh",
            value,
            outcome="error" if value == 200 else "ok",
            main_thread=True,
        )

    metric = registry.snapshot().metric("refresh")

    assert metric is not None
    assert metric.count == 5
    assert metric.p50_ms == 4
    assert metric.p95_ms == 200
    assert metric.maximum_ms == 200
    assert metric.latest_ms == 200
    assert metric.main_thread_count == 5
    assert metric.error_count == 1


def test_registry_bounds_metric_cardinality_by_recency() -> None:
    now = [0.0]
    registry = PerformanceRegistry(maximum_metrics=2, monotonic=lambda: now[0])
    registry.record("oldest", 1)
    now[0] = 1.0
    registry.record("middle", 1)
    now[0] = 2.0
    registry.record("newest", 1)

    names = tuple(metric.name for metric in registry.snapshot().metrics)

    assert names == ("middle", "newest")


def test_measure_context_records_errors_without_swallowing_them() -> None:
    registry = PerformanceRegistry()

    try:
        with registry.measure("operation"):
            raise RuntimeError("sentinel")
    except RuntimeError as exc:
        assert str(exc) == "sentinel"

    metric = registry.snapshot().metric("operation")
    assert metric is not None
    assert metric.error_count == 1
