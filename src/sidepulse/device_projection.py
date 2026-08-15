"""Pure per-device filtering for SidePulse status surfaces.

The AppKit controller owns device discovery and settings. This module owns the
small, deterministic decision of which canonical rows one device may show. It
has no AppKit dependency, so provider-pin behavior can be tested everywhere.
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


def _representative(
    main_rows: tuple[ProjectedAgentRow, ...],
    worker_rows: tuple[ProjectedAgentRow, ...],
) -> ProjectedAgentRow | None:
    candidates = main_rows or worker_rows
    return min(
        candidates,
        key=lambda row: (
            _LIFECYCLE_PRIORITY[row.lifecycle_mode],
            -row.updated_at.timestamp(),
            row.agent_id,
        ),
        default=None,
    )


def _rows_for_provider(
    projection: AttentionProjection,
    provider_pin: str | None,
) -> tuple[tuple[ProjectedAgentRow, ...], tuple[ProjectedAgentRow, ...]]:
    if not provider_pin:
        return projection.visible_rows, projection.worker_rows
    return (
        tuple(row for row in projection.visible_rows if row.provider == provider_pin),
        tuple(row for row in projection.worker_rows if row.provider == provider_pin),
    )


def light_rows_for_provider(
    projection: AttentionProjection,
    provider_pin: str | None,
) -> tuple[ProjectedAgentRow, ...]:
    """Return the light-eligible rows allowed by an optional provider pin.

    Main agents remain a crowd. When only workers remain, one urgent worker is
    the stand-in for that provider's background crowd. Filtering happens before
    choosing the stand-in, so a busier worker from another provider cannot make
    a pinned device falsely appear idle.
    """
    if not provider_pin:
        return projection.light_rows
    main_rows, worker_rows = _rows_for_provider(projection, provider_pin)
    if main_rows:
        return main_rows
    representative = _representative((), worker_rows)
    return () if representative is None else (representative,)


def projection_for_provider(
    projection: AttentionProjection,
    provider_pin: str | None,
) -> AttentionProjection:
    """Project canonical attention onto one device's provider lane.

    Actionable attention is fleet-wide and intentionally bypasses pins. Stable
    lifecycle rows and workers follow the pin. The canonical main/worker split
    is preserved, avoiding the duplicate-worker bug caused by putting an
    orphaned worker into ``visible_rows`` and letting ``__post_init__`` demote it
    a second time.
    """
    if projection.actionable_attention or not provider_pin:
        return projection

    main_rows, worker_rows = _rows_for_provider(projection, provider_pin)
    representative = _representative(main_rows, worker_rows)
    if representative is None:
        return replace(
            projection,
            lifecycle_mode=LifecycleMode.IDLE,
            visible_rows=(),
            worker_rows=(),
            dominant_provider=None,
            click_target_agent_id=None,
        )

    return replace(
        projection,
        lifecycle_mode=representative.lifecycle_mode,
        visible_rows=main_rows,
        worker_rows=worker_rows,
        dominant_provider=representative.provider,
        click_target_agent_id=None,
    )


__all__ = ["light_rows_for_provider", "projection_for_provider"]
