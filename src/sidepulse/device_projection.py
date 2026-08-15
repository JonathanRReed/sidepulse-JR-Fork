"""Pure per-device filtering for the status surfaces.

The AppKit controller owns device discovery and settings. This module owns the
small, deterministic decision of which projected rows a device may show. It is
kept free of AppKit so provider-pin behavior is testable on every platform.
"""

from __future__ import annotations

from dataclasses import replace

from .attention import AttentionProjection, LifecycleMode, ProjectedAgentRow

_LIFECYCLE_PRIORITY = {
    LifecycleMode.WAITING: 0,
    LifecycleMode.ACTIVE: 1,
    LifecycleMode.FAILED_VISIBLE: 2,
    LifecycleMode.COMPLETED_RECENTLY: 3,
    LifecycleMode.IDLE: 4,
    LifecycleMode.UNKNOWN: 5,
}


def light_rows_for_provider(
    projection: AttentionProjection,
    provider_pin: str | None,
) -> tuple[ProjectedAgentRow, ...]:
    """Return the light-eligible rows allowed by an optional provider pin."""
    rows = projection.light_rows
    if not provider_pin:
        return rows
    return tuple(row for row in rows if row.provider == provider_pin)


def projection_for_provider(
    projection: AttentionProjection,
    provider_pin: str | None,
) -> AttentionProjection:
    """Project one canonical attention state onto one device's provider lane.

    Actionable attention is fleet-wide and intentionally bypasses provider
    pins. Stable lifecycle rows and worker rows follow the pin. An unpinned
    device preserves the canonical worker set instead of accidentally dropping
    it while reconstructing the projection.
    """
    if projection.actionable_attention:
        return projection

    rows = light_rows_for_provider(projection, provider_pin)
    if not rows:
        return replace(
            projection,
            lifecycle_mode=LifecycleMode.IDLE,
            visible_rows=(),
            worker_rows=(),
            dominant_provider=None,
            click_target_agent_id=None,
        )

    lifecycle = min(
        rows,
        key=lambda row: _LIFECYCLE_PRIORITY[row.lifecycle_mode],
    ).lifecycle_mode
    worker_rows = projection.worker_rows
    if provider_pin:
        worker_rows = tuple(
            row for row in worker_rows if row.provider == provider_pin
        )

    return replace(
        projection,
        lifecycle_mode=lifecycle,
        visible_rows=rows,
        worker_rows=worker_rows,
        dominant_provider=rows[0].provider,
        click_target_agent_id=None,
    )


__all__ = ["light_rows_for_provider", "projection_for_provider"]
