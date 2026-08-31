"""Controller-agnostic capacity-history runtime helpers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .capacity_history import (
    CAPACITY_HISTORY_SCHEMA_VERSION,
    CapacityHistorySample,
    HistoryContinuity,
    HistoryInterval,
    HistoryValidationError,
)
from .capacity_history import (
    SUPPORTED_RETENTION_DAYS as SUPPORTED_HISTORY_RETENTION_DAYS,
)
from .capacity_history_store import CapacityHistoryStore, default_capacity_history_path
from .capacity_types import SampleDisposition
from .capacity_view import CapacityHistoryPresentation, CapacityHistorySummaryInput
from .persistence_writer import PersistenceDisposition


def resolve_capacity_history_store(
    controller: Any,
    *,
    log: Callable[[str], None],
) -> CapacityHistoryStore | None:
    with controller._capacity_history_lock:
        settings = getattr(controller, "settings", None)
        if not getattr(settings, "capacity_history_enabled", False):
            store = controller._capacity_history_store
            if store is not None:
                controller._capacity_history_generation += 1
                controller._capacity_history_store = None
                controller._capacity_history_retention_days = None
                try:
                    store.delete_capacity_history()
                except OSError as exc:
                    log(f"capacity history delete failed: {exc}")
            return None
        days = getattr(settings, "capacity_history_retention_days", 7)
        if days not in SUPPORTED_HISTORY_RETENTION_DAYS:
            days = 7
        store = controller._capacity_history_store
        if store is not None and controller._capacity_history_retention_days == days:
            return store
        if store is not None:
            try:
                store.apply_retention(days, now=time.time())
            except (HistoryValidationError, OSError, ValueError) as exc:
                log(f"capacity history retention failed: {exc}")
                return store
            controller._capacity_history_retention_days = days
            return store
        try:
            store = CapacityHistoryStore(
                default_capacity_history_path(),
                retention_days=days,
            )
            store.restore()
        except (HistoryValidationError, OSError, ValueError) as exc:
            log(f"capacity history unavailable: {exc}")
            controller._capacity_history_store = None
            controller._capacity_history_retention_days = None
            return None
        controller._capacity_history_generation += 1
        controller._capacity_history_store = store
        controller._capacity_history_retention_days = days
        return store


def record_capacity_history_runtime(
    controller: Any,
    authorised: Any,
    reset_decisions: dict[Any, Any],
    *,
    now: float,
    log: Callable[[str], None],
) -> int:
    with controller._capacity_history_lock:
        store = resolve_capacity_history_store(controller, log=log)
        if store is None or authorised is None:
            return 0
        generation = controller._capacity_history_generation
        admitted = 0
        for lane in getattr(authorised, "lanes", ()):
            decision = reset_decisions.get(lane.key)
            if decision is None or lane.account_discriminator is None:
                continue
            remaining = decision.remaining
            if remaining is None:
                continue
            if decision.disposition is SampleDisposition.IDENTITY_AMBIGUOUS:
                continuity = (
                    HistoryContinuity.MISSING
                    if decision.reason_code == "reset_identity_unavailable"
                    else HistoryContinuity.CHANGED
                )
            else:
                continuity = HistoryContinuity.CONTINUOUS
            accepted = decision.disposition is SampleDisposition.ACCEPTED
            try:
                sample = CapacityHistorySample(
                    schema_version=CAPACITY_HISTORY_SCHEMA_VERSION,
                    lane_key=lane.key,
                    account_discriminator=lane.account_discriminator,
                    observed_at=float(now),
                    remaining=max(0.0, min(100.0, float(remaining))),
                    reset_epoch=decision.reset.reset_epoch,
                    window_minutes=decision.reset.window_minutes,
                    source_health=lane.source_health.kind,
                    disposition=decision.disposition,
                    refusal_code=None if accepted else decision.reason_code,
                )
                if store.admit_capacity(sample, continuity).sample is not None:
                    admitted += 1
            except (HistoryValidationError, ValueError) as exc:
                log(f"capacity history sample refused: {exc}")
    if admitted:
        disposition = controller._persistence_writer.submit(
            "capacity-history",
            lambda: flush_capacity_history_store_runtime(
                controller,
                store,
                generation=generation,
                now=now,
                force=False,
            ),
            replace_pending=True,
        )
        if disposition in {
            PersistenceDisposition.REFUSED_FULL,
            PersistenceDisposition.REFUSED_CLOSED,
        }:
            log("capacity history write not queued")
    return admitted


def flush_capacity_history_store_runtime(
    controller: Any,
    store: CapacityHistoryStore,
    *,
    generation: int,
    now: float,
    force: bool,
) -> bool:
    with controller._capacity_history_lock:
        if (
            generation != controller._capacity_history_generation
            or controller._capacity_history_store is not store
        ):
            return False
        return store.flush(now=now, force=force)


def build_capacity_history_presentation(
    controller: Any,
    *,
    now: float,
    log: Callable[[str], None],
) -> CapacityHistoryPresentation:
    with controller._capacity_history_lock:
        store = resolve_capacity_history_store(controller, log=log)
        if store is None:
            return CapacityHistoryPresentation(enabled=False, summaries=())
        summaries = []
        for interval in HistoryInterval:
            try:
                summaries.append(
                    CapacityHistorySummaryInput(
                        interval=interval,
                        summary=store.summarize(interval, now=now),
                    )
                )
            except (HistoryValidationError, ValueError):
                continue
    return CapacityHistoryPresentation(enabled=True, summaries=tuple(summaries))


__all__ = [
    "build_capacity_history_presentation",
    "flush_capacity_history_store_runtime",
    "record_capacity_history_runtime",
    "resolve_capacity_history_store",
]
