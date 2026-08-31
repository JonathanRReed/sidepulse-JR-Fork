"""Remaining-percent history for EVERY provider, not just the two local ones.

The token/cost/sessions graph can only show claude and codex because only
they leave local transcript records. Every other provider answers a single
point-in-time question -- "what percent is left right now" -- so the only
way to chart them is to remember those answers. This module is that memory:
a private JSONL of (provider, source instance, lane, remaining_percent,
observed_at) samples, deduplicated at the exact account source so an all-day
session adds dozens of points, not thousands, plus the projection that turns
the file into the same graph-model shape the settings chart already draws.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import audit
from .persistence_writer import (
    PersistenceDisposition,
    PersistenceOutcome,
    PersistenceReceipt,
)
from .private_io import append_private_text, atomic_private_write, read_private_text
from .provider_feature_settings import ProviderInstanceRetentionProjection
from .providers import default_state_dir

PERCENT_HISTORY_FILE_NAME = "usage-percent-history.jsonl"
#: A lane's sample is worth writing when its value moved at least this
#: much, or this much time passed -- whichever comes first.
PERCENT_RECORD_MIN_DELTA = 1.0
PERCENT_RECORD_MIN_SECONDS = 1800.0


def default_percent_history_path(home: Path | None = None) -> Path:
    return default_state_dir(home) / PERCENT_HISTORY_FILE_NAME


def filter_new_observations(
    last_recorded: dict[tuple[str, str, str], tuple[float, float]],
    observations: list[tuple[str, str, str, float]],
    *,
    now_epoch: float,
) -> tuple[list[dict], dict[tuple[str, str, str], tuple[float, float]]]:
    """(records worth appending, updated last-recorded map).

    ``last_recorded`` maps (provider_id, source_instance_id, lane_id) to
    (percent, epoch) and lives on the controller so dedupe never rereads
    the file.
    """
    updated = dict(last_recorded)
    fresh: list[dict] = []
    for provider_id, source_instance_id, lane_id, remaining_percent in observations:
        if not provider_id or not source_instance_id or not lane_id:
            continue
        try:
            value = float(remaining_percent)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= value <= 100.0):
            continue
        key = (provider_id, source_instance_id, lane_id)
        previous = updated.get(key)
        if previous is not None:
            prior_value, prior_epoch = previous
            if (
                abs(value - prior_value) < PERCENT_RECORD_MIN_DELTA
                and now_epoch - prior_epoch < PERCENT_RECORD_MIN_SECONDS
            ):
                continue
        updated[key] = (value, now_epoch)
        fresh.append(
            {
                "provider_id": provider_id,
                "source_instance_id": source_instance_id,
                "lane_id": lane_id,
                "remaining_percent": round(value, 2),
                "observed_at_epoch": round(float(now_epoch), 3),
            }
        )
    return fresh, updated


def _retention_by_identity(
    projection: ProviderInstanceRetentionProjection,
) -> dict[tuple[str, str], int]:
    if type(projection) is not ProviderInstanceRetentionProjection:
        raise TypeError("expected ProviderInstanceRetentionProjection")
    return {policy.identity: policy.retention_days for policy in projection.providers}


def _retention_signature(
    projection: ProviderInstanceRetentionProjection,
) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        sorted(
            (policy.provider_id, policy.source_instance_id, policy.retention_days)
            for policy in projection.providers
        )
    )


def _history_record(
    provider_id: str,
    source_instance_id: str,
    lane_id: str,
    percent: float,
    epoch: float,
) -> dict:
    return {
        "provider_id": provider_id,
        "source_instance_id": source_instance_id,
        "lane_id": lane_id,
        "remaining_percent": round(percent, 2),
        "observed_at_epoch": round(epoch, 3),
    }


def _serialize_history(records: list[dict]) -> str:
    return "".join(
        json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        for record in records
    )


def _retained_history_records(
    history_text: str,
    projection: ProviderInstanceRetentionProjection,
    *,
    now_epoch: float,
) -> list[dict]:
    retention_by_identity = _retention_by_identity(projection)
    retained: list[dict] = []
    for provider_id, source_instance_id, lane_id, percent, epoch in _parse_history_lines(
        history_text
    ):
        retention_days = retention_by_identity.get((provider_id, source_instance_id), 0)
        if retention_days == 0:
            continue
        if epoch < now_epoch - retention_days * 86_400.0:
            continue
        retained.append(
            _history_record(
                provider_id,
                source_instance_id,
                lane_id,
                percent,
                epoch,
            )
        )
    return retained


def append_percent_observations(
    path: Path,
    records: list[dict],
    *,
    retention_projection: ProviderInstanceRetentionProjection | None = None,
    now_epoch: float | None = None,
) -> int:
    """Persist pre-filtered records and enforce exact-instance retention."""
    if retention_projection is None:
        if not records:
            return 0
        text = _serialize_history(records)
        append_private_text(path, text)
        audit.compact_jsonl_file(path)
        return len(records)

    current_epoch = time.time() if now_epoch is None else float(now_epoch)
    try:
        existing = read_private_text(path, errors="replace")
        existed = True
    except FileNotFoundError:
        existing = ""
        existed = False
    text = "".join(
        json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        for record in records
    )
    retained = _retained_history_records(
        existing + text,
        retention_projection,
        now_epoch=current_epoch,
    )
    if not existed and not retained:
        return 0
    atomic_private_write(path, _serialize_history(retained))
    audit.compact_jsonl_file(path)
    eligible_identities = {
        policy.identity
        for policy in retention_projection.providers
        if policy.retention_days > 0
    }
    return sum(
        1
        for record in records
        if (record.get("provider_id"), record.get("source_instance_id", "default"))
        in eligible_identities
    )


def _parse_history_lines(text: str) -> list[tuple[str, str, str, float, float]]:
    parsed: list[tuple[str, str, str, float, float]] = []
    for line in text.splitlines():
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            continue
        if type(payload) is not dict:
            continue
        provider_id = payload.get("provider_id")
        source_instance_id = payload.get("source_instance_id", "default")
        lane_id = payload.get("lane_id")
        percent = payload.get("remaining_percent")
        epoch = payload.get("observed_at_epoch")
        if (
            type(provider_id) is not str
            or not provider_id
            or type(source_instance_id) is not str
            or not source_instance_id
            or type(lane_id) is not str
            or type(percent) not in (int, float)
            or type(epoch) not in (int, float)
            or not (0.0 <= float(percent) <= 100.0)
        ):
            continue
        parsed.append(
            (
                provider_id,
                source_instance_id,
                lane_id,
                float(percent),
                float(epoch),
            )
        )
    return parsed


def percent_graph_model(
    history_text: str,
    *,
    days: int,
    provider_ids: tuple[str, ...],
    now: datetime | None = None,
    period_label: str | None = None,
) -> dict:
    """The settings chart's model shape, from percent history.

    One value per exact provider instance per local calendar day: the day's
    WORST (minimum) remaining percent across that instance's lanes -- the
    number that answers "how squeezed did I get". Days with no sample carry
    the previous known value forward so the line stays readable; days before
    the first sample remain gaps.
    """
    current = now or datetime.now()
    day_keys = [
        (current - timedelta(days=offset)).date().isoformat()
        for offset in range(days - 1, -1, -1)
    ]
    label_stride = max(1, days // 6)
    labels = tuple(
        day[5:].replace("-", "/") if index % label_stride == 0 else ""
        for index, day in enumerate(day_keys)
    )
    worst_by_identity_day: dict[tuple[str, str], dict[str, float]] = {}
    for provider_id, source_instance_id, _lane_id, percent, epoch in _parse_history_lines(
        history_text
    ):
        if provider_id not in provider_ids:
            continue
        day = datetime.fromtimestamp(epoch).date().isoformat()
        identity = (provider_id, source_instance_id)
        per_day = worst_by_identity_day.setdefault(identity, {})
        existing = per_day.get(day)
        if existing is None or percent < existing:
            per_day[day] = percent
    series = []
    for provider_id in provider_ids:
        identities = sorted(
            (
                identity
                for identity in worst_by_identity_day
                if identity[0] == provider_id
            ),
            key=lambda identity: (identity[1] != "default", identity[1]),
        )
        for identity in identities:
            _provider_id, source_instance_id = identity
            per_day = worst_by_identity_day[identity]
            values: list[float] = []
            carried: float | None = None
            for day in day_keys:
                observed = per_day.get(day)
                if observed is not None:
                    carried = observed
                values.append(carried if carried is not None else -1.0)
            if all(value < 0.0 for value in values):
                continue
            # Days BEFORE the first sample stay negative: the chart renders
            # them as a gap. They used to be backfilled with the first known
            # reading, which drew a fabricated flat line across every day
            # before history began (noticeable on 90/365 ranges -- history
            # starts 2026-08-21).
            label = (
                provider_id
                if source_instance_id == "default"
                else f"{provider_id} · {source_instance_id}"
            )
            series.append(
                {
                    "provider_id": provider_id,
                    "source_instance_id": source_instance_id,
                    "identity": identity,
                    "label": label,
                    "values": tuple(values),
                }
            )
    return {
        "days": days,
        "period_label": period_label or f"Last {days} days",
        "metric": "percent",
        "labels": labels,
        "series": tuple(series),
        "scale_max": 100.0,
    }


@dataclass(frozen=True, slots=True)
class _PendingPercentWrite:
    records: tuple[dict, ...]
    retention_signature: tuple[tuple[str, str, int], ...] | None


def _controller_retention_projection(
    controller,
    explicit: ProviderInstanceRetentionProjection | None,
) -> ProviderInstanceRetentionProjection | None:
    if explicit is not None:
        if type(explicit) is not ProviderInstanceRetentionProjection:
            raise TypeError("expected ProviderInstanceRetentionProjection")
        return explicit
    direct = getattr(controller, "_sidepulse_provider_instance_retention", None)
    if type(direct) is ProviderInstanceRetentionProjection:
        return direct
    policies = getattr(controller, "_sidepulse_provider_instance_policies", None)
    projected = getattr(policies, "retention", None)
    return (
        projected
        if type(projected) is ProviderInstanceRetentionProjection
        else None
    )


def record_state_observations(
    controller,
    snapshots,
    *,
    writer,
    retention_projection: ProviderInstanceRetentionProjection | None = None,
) -> bool | None:
    """Queue fresh usage percents on the shared serial persistence writer.

    The controller carries the last-recorded map so dedupe never rereads
    the file. A refused write does not advance that map, so the observation
    remains eligible for a later retry.
    """
    retention = _controller_retention_projection(controller, retention_projection)
    retention_by_identity = (
        _retention_by_identity(retention) if retention is not None else None
    )
    observations = [
        (
            snapshot.provider_id,
            getattr(snapshot, "source_instance_id", "default"),
            lane.lane_id,
            lane.remaining_percent,
        )
        for snapshot in snapshots
        for lane in snapshot.lanes
        if lane.remaining_percent is not None
        and (
            retention_by_identity is None
            or retention_by_identity.get(
                (
                    snapshot.provider_id,
                    getattr(snapshot, "source_instance_id", "default"),
                ),
                0,
            )
            > 0
        )
    ]
    signature = _retention_signature(retention) if retention is not None else None
    if not observations and signature is None:
        return None
    lock = getattr(controller, "_sidepulse_percent_history_lock", None)
    if lock is None:
        lock = threading.Lock()
        controller._sidepulse_percent_history_lock = lock
    with lock:
        committed = dict(
            getattr(controller, "_sidepulse_percent_history_last", {})
        )
        pending = dict(
            getattr(controller, "_sidepulse_percent_history_pending", {})
        )
        planned = dict(committed)
        for pending_write in pending.values():
            for record in pending_write.records:
                planned[
                    (
                        record["provider_id"],
                        record["source_instance_id"],
                        record["lane_id"],
                    )
                ] = (
                    record["remaining_percent"],
                    record["observed_at_epoch"],
                )
        pending_signatures = {
            pending_write.retention_signature for pending_write in pending.values()
        }
        committed_signature = getattr(
            controller,
            "_sidepulse_percent_history_retention_signature",
            None,
        )
        needs_retention = (
            signature is not None
            and signature != committed_signature
            and signature not in pending_signatures
        )
        observed_epoch = time.time()
        fresh, _updated = filter_new_observations(
            planned,
            observations,
            now_epoch=observed_epoch,
        )
        if not fresh and not needs_retention:
            return None
        records = tuple(fresh)
        token = object()
        pending[token] = _PendingPercentWrite(records, signature)
        controller._sidepulse_percent_history_pending = pending

    def _complete(receipt: PersistenceReceipt) -> None:
        with lock:
            current_pending = dict(
                getattr(controller, "_sidepulse_percent_history_pending", {})
            )
            completed = current_pending.pop(token, None)
            if completed is None:
                return
            controller._sidepulse_percent_history_pending = current_pending
            if receipt.outcome is PersistenceOutcome.SUCCEEDED:
                committed_now = dict(
                    getattr(controller, "_sidepulse_percent_history_last", {})
                )
                if retention is not None:
                    retention_now = _retention_by_identity(retention)
                    committed_now = {
                        key: value
                        for key, value in committed_now.items()
                        if retention_now.get(key[:2], 0) > 0
                        and value[1]
                        >= observed_epoch - retention_now[key[:2]] * 86_400.0
                    }
                    controller._sidepulse_percent_history_retention_signature = (
                        completed.retention_signature
                    )
                for record in completed.records:
                    committed_now[
                        (
                            record["provider_id"],
                            record["source_instance_id"],
                            record["lane_id"],
                        )
                    ] = (
                        record["remaining_percent"],
                        record["observed_at_epoch"],
                    )
                controller._sidepulse_percent_history_last = committed_now

    def _persist() -> int:
        path = default_percent_history_path()
        if retention is None:
            return append_percent_observations(path, list(records))
        return append_percent_observations(
            path,
            list(records),
            retention_projection=retention,
            now_epoch=observed_epoch,
        )

    try:
        disposition = writer.submit(
            "usage-percent-history",
            _persist,
            receipt_handler=_complete,
        )
    except Exception:
        with lock:
            current_pending = dict(
                getattr(controller, "_sidepulse_percent_history_pending", {})
            )
            current_pending.pop(token, None)
            controller._sidepulse_percent_history_pending = current_pending
        return False
    if disposition in {
        PersistenceDisposition.REFUSED_FULL,
        PersistenceDisposition.REFUSED_CLOSED,
    }:
        with lock:
            current_pending = dict(
                getattr(controller, "_sidepulse_percent_history_pending", {})
            )
            current_pending.pop(token, None)
            controller._sidepulse_percent_history_pending = current_pending
        return False
    return True


def shared_percent_graph_model(
    *,
    days: int,
    period_label: str,
    now: datetime | None = None,
) -> dict:
    """percent_graph_model over the default store, for the full registry.

    The full registry, not the curated token pair: percent is the one
    metric every provider can answer, and the curation default of
    ("claude", "codex") is exactly why the chart looked like a
    two-provider app. Providers with no history contribute no line.
    """
    from .private_io import read_private_text
    from .provider_usage_platform import provider_descriptors

    path = default_percent_history_path()
    history_text = read_private_text(path) if path.exists() else ""
    return percent_graph_model(
        history_text,
        days=days,
        provider_ids=tuple(
            descriptor.provider_id for descriptor in provider_descriptors()
        ),
        now=now,
        period_label=period_label,
    )


__all__ = [
    "append_percent_observations",
    "default_percent_history_path",
    "filter_new_observations",
    "percent_graph_model",
    "shared_percent_graph_model",
]
