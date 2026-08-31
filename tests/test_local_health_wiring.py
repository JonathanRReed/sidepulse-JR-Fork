from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from sidepulse import _status_bar_production as production
from sidepulse.dnd_policy import DndMode, DndSource, compose_dnd_contributions, contribution_for_mode
from sidepulse.effect_studio_physical_preview import PreviewReleaseReason
from sidepulse.local_health import LocalHealthMonitor
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.performance_metrics import PerformanceRegistry
from sidepulse.runtime_scheduler import RuntimeWorkerDomain, RuntimeWorkerSnapshot
from sidepulse.screen_bar_pipeline import PresentationMetrics


class _WorkerRegistry:
    def snapshot(self) -> tuple[RuntimeWorkerSnapshot, ...]:
        return (
            RuntimeWorkerSnapshot.empty(
                RuntimeWorkerDomain.OS_POLL,
                accepting=True,
            ),
        )


def _status(age_seconds: float) -> AgentStatus:
    return AgentStatus(
        provider="private-provider-label",
        agent_id="private-agent-id",
        display_name="private-display-name",
        mode=AgentMode.WORKING,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        event_name="private-event-name",
    )


def _target(performance: PerformanceRegistry) -> SimpleNamespace:
    dnd_projection = compose_dnd_contributions(
        (contribution_for_mode(DndSource.MANUAL, DndMode.MUTE),),
        next_transition_epoch=1_800_000_000.0,
    )
    return SimpleNamespace(
        _production_local_health_monitor=LocalHealthMonitor(),
        _runtime_worker_registry=_WorkerRegistry(),
        last_snapshot=SimpleNamespace(statuses=(_status(5.0),)),
        _performance=lambda: performance,
        current_dnd_projection=lambda: dnd_projection,
    )


def test_production_health_snapshot_reads_only_existing_memory_owners(
    monkeypatch,
) -> None:
    metrics = PresentationMetrics()
    performance = PerformanceRegistry()
    performance.record("refresh", 12)
    target = _target(performance)
    monkeypatch.setattr(production, "DEFAULT_PRESENTATION_METRICS", metrics)

    snapshot = production.JRStatusBarController.local_health_snapshot(target)
    rendered = production.JRStatusBarController.performance_diagnostics_text(target)

    assert snapshot.worker_count == 1
    assert snapshot.queue_depth == 0
    assert snapshot.source_freshness_seconds is not None
    assert 4.0 <= snapshot.source_freshness_seconds <= 10.0
    assert snapshot.refresh_duration is not None
    assert snapshot.refresh_duration.latest_ms == 12
    assert "Local Health (current run, never sent)" in rendered
    assert "Detailed timings (current run)" in rendered
    assert "DND Mute; source Manual; returns 2027-01-15 08:00Z" in rendered
    assert "private-provider-label" not in rendered
    assert "private-agent-id" not in rendered
    assert "private-event-name" not in rendered


def test_production_shutdown_records_latency_without_changing_close_order() -> None:
    events: list[str] = []
    performance = PerformanceRegistry()

    class _Service:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            events.append(self.name)

    target = _target(performance)
    target._effect_studio_physical_preview_adapter = SimpleNamespace(
        release=lambda reason: events.append(f"preview:{reason.value}")
    )
    for attribute, name in (
        ("_production_battery_service", "battery"),
        ("_production_transcript_service", "transcript"),
        ("_production_intake_service", "intake"),
        ("_production_ledger_publisher", "ledger"),
        ("_production_webhook_service", "webhook"),
    ):
        setattr(target, attribute, _Service(name))

    def legacy_terminate(_self, _notification):
        events.append("legacy")
        return "closed"

    result = production._terminate_controller(
        target,
        None,
        legacy_terminate=legacy_terminate,
    )

    assert result == "closed"
    assert events == [
        f"preview:{PreviewReleaseReason.APP_TERMINATION.value}",
        "battery",
        "transcript",
        "intake",
        "ledger",
        "webhook",
        "legacy",
    ]
    metric = performance.snapshot().metric("shutdown")
    assert metric is not None
    assert metric.count == 1
    assert metric.error_count == 0


def test_production_shutdown_records_error_and_does_not_swallow_it() -> None:
    performance = PerformanceRegistry()
    target = _target(performance)

    def fail_termination(_self, _notification):
        raise RuntimeError("termination sentinel")

    with pytest.raises(RuntimeError, match="termination sentinel"):
        production._terminate_controller(
            target,
            None,
            legacy_terminate=fail_termination,
        )

    metric = performance.snapshot().metric("shutdown")
    assert metric is not None
    assert metric.count == 1
    assert metric.error_count == 1


def test_refresh_records_duration_before_sampling_local_health() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "sidepulse"
        / "_status_bar_production.py"
    )
    text = source.read_text(encoding="utf-8")
    refresh_block = text[text.index("        def refresh_(self, sender):") :]
    refresh_block = refresh_block[: refresh_block.index("        @", 1)]

    assert refresh_block.index('"refresh",') < refresh_block.index(
        "self.local_health_snapshot()"
    )
