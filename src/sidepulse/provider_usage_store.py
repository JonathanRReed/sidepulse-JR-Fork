"""Owner-private persistence for native provider usage snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from .provider_usage_platform import (
    DEFAULT_SOURCE_INSTANCE_ID,
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from .provider_usage_runtime import ProviderUsageState

PROVIDER_USAGE_STORE_SCHEMA_VERSION = 2
MAX_STORE_BYTES = 2 * 1024 * 1024


def default_provider_usage_state_path(home: Path | None = None) -> Path:
    base = Path.home() if home is None else Path(home)
    return base / ".local" / "state" / "sidepulse" / "provider-usage.json"


def _lane_document(lane: UsageLane) -> dict[str, object]:
    return {
        "provider_id": lane.provider_id,
        "lane_id": lane.lane_id,
        "label": lane.label,
        "remaining_percent": lane.remaining_percent,
        "reset_at": lane.reset_at,
        "scope": lane.scope,
        "model": lane.model,
        "feature": lane.feature,
        "bindable": lane.bindable,
        "source_id": lane.source_id,
    }


def _snapshot_document(snapshot: ProviderUsageSnapshot) -> dict[str, object]:
    return {
        "provider_id": snapshot.provider_id,
        "account_label": snapshot.account_label,
        "observed_at": snapshot.observed_at,
        "state": snapshot.state.value,
        "reason_code": snapshot.reason_code,
        "action_label": snapshot.action_label,
        "lanes": [_lane_document(lane) for lane in snapshot.lanes],
        "input_tokens": snapshot.input_tokens,
        "cached_input_tokens": snapshot.cached_input_tokens,
        "output_tokens": snapshot.output_tokens,
        "model_count": snapshot.model_count,
        "estimated_cost_usd": snapshot.estimated_cost_usd,
        "cache_savings_usd": snapshot.cache_savings_usd,
        "credits_remaining": snapshot.credits_remaining,
        "incident": snapshot.incident,
        "source_instance_id": snapshot.source_instance_id,
    }


def save_provider_usage_state(
    state: ProviderUsageState,
    path: Path | None = None,
) -> Path:
    if type(state) is not ProviderUsageState:
        raise ValueError("invalid provider usage state")
    if state.refreshing:
        raise ValueError("refreshing provider usage state cannot be persisted")
    document = {
        "schema_version": PROVIDER_USAGE_STORE_SCHEMA_VERSION,
        "refreshed_at": state.refreshed_at,
        "next_refresh_at": state.next_refresh_at,
        "snapshots": [_snapshot_document(snapshot) for snapshot in state.snapshots],
    }
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(text.encode("utf-8")) > MAX_STORE_BYTES:
        raise ValueError("provider usage state exceeds storage budget")
    target = default_provider_usage_state_path() if path is None else Path(path)
    from .private_io import atomic_private_write

    atomic_private_write(target, text)
    return target


def _lane(value: object) -> UsageLane | None:
    if not isinstance(value, dict):
        return None
    try:
        return UsageLane(
            provider_id=value.get("provider_id"),
            lane_id=value.get("lane_id"),
            label=value.get("label"),
            remaining_percent=value.get("remaining_percent"),
            reset_at=value.get("reset_at"),
            scope=value.get("scope"),
            model=value.get("model"),
            feature=value.get("feature"),
            bindable=value.get("bindable"),
            source_id=value.get("source_id"),
        )
    except (TypeError, ValueError):
        return None


def _snapshot(value: object) -> ProviderUsageSnapshot | None:
    if not isinstance(value, dict):
        return None
    raw_lanes = value.get("lanes")
    lanes = []
    if isinstance(raw_lanes, list):
        for raw_lane in raw_lanes[:64]:
            lane = _lane(raw_lane)
            if lane is not None:
                lanes.append(lane)
    try:
        state = ProviderSourceState(value.get("state"))
        return ProviderUsageSnapshot(
            provider_id=value.get("provider_id"),
            account_label=value.get("account_label"),
            observed_at=value.get("observed_at"),
            state=state,
            reason_code=value.get("reason_code"),
            action_label=value.get("action_label"),
            lanes=tuple(lanes),
            input_tokens=value.get("input_tokens", 0),
            cached_input_tokens=value.get("cached_input_tokens", 0),
            output_tokens=value.get("output_tokens", 0),
            model_count=value.get("model_count", 0),
            estimated_cost_usd=value.get("estimated_cost_usd"),
            cache_savings_usd=value.get("cache_savings_usd"),
            credits_remaining=value.get("credits_remaining"),
            incident=value.get("incident"),
            source_instance_id=value.get("source_instance_id", DEFAULT_SOURCE_INSTANCE_ID),
        )
    except (TypeError, ValueError):
        return None


def load_provider_usage_state(
    path: Path | None = None,
) -> ProviderUsageState:
    target = default_provider_usage_state_path() if path is None else Path(path)
    try:
        from .private_io import read_private_text

        document = json.loads(read_private_text(target, max_bytes=MAX_STORE_BYTES))
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return ProviderUsageState((), None, None, False)
    if (
        not isinstance(document, dict)
        or document.get("schema_version") not in {
            1,
            PROVIDER_USAGE_STORE_SCHEMA_VERSION,
        }
    ):
        return ProviderUsageState((), None, None, False)
    snapshots = []
    raw_snapshots = document.get("snapshots")
    if isinstance(raw_snapshots, list):
        for raw_snapshot in raw_snapshots[:16]:
            snapshot = _snapshot(raw_snapshot)
            if snapshot is not None and snapshot.identity not in {
                existing.identity for existing in snapshots
            }:
                snapshots.append(snapshot)
    refreshed_at = document.get("refreshed_at")
    next_refresh_at = document.get("next_refresh_at")
    if refreshed_at is not None and not isinstance(refreshed_at, (int, float)):
        refreshed_at = None
    if next_refresh_at is not None and not isinstance(next_refresh_at, (int, float)):
        next_refresh_at = None
    return ProviderUsageState(
        tuple(snapshots),
        None if refreshed_at is None else float(refreshed_at),
        None if next_refresh_at is None else float(next_refresh_at),
        False,
    )


__all__ = [
    "MAX_STORE_BYTES",
    "PROVIDER_USAGE_STORE_SCHEMA_VERSION",
    "default_provider_usage_state_path",
    "load_provider_usage_state",
    "save_provider_usage_state",
]
