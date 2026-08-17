"""Owner-private persistence for non-secret provider usage snapshots."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .private_io import atomic_private_write, read_private_text
from .provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    QuotaLane,
    QuotaUnit,
    TokenUsage,
)

STORE_VERSION = 1
MAX_STORE_BYTES = 2 * 1024 * 1024


def default_provider_usage_store_path() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".local" / "state"
    return base / "sidepulse" / "provider-usage" / "snapshots.json"


def _token_dict(value: TokenUsage | None) -> dict | None:
    if value is None:
        return None
    return {
        "input_tokens": value.input_tokens,
        "cached_input_tokens": value.cached_input_tokens,
        "cache_creation_tokens": value.cache_creation_tokens,
        "output_tokens": value.output_tokens,
        "models": list(value.models),
        "estimated_cost_usd": value.estimated_cost_usd,
        "estimated_cache_savings_usd": value.estimated_cache_savings_usd,
        "pricing_coverage": value.pricing_coverage,
        "pricing_table_version": value.pricing_table_version,
        "pricing_as_of": value.pricing_as_of,
    }


def _lane_dict(value: QuotaLane) -> dict:
    return {
        "provider_id": value.provider_id,
        "lane_id": value.lane_id,
        "label": value.label,
        "remaining": value.remaining,
        "used": value.used,
        "total": value.total,
        "unit": value.unit.value,
        "reset_at": value.reset_at,
        "source": value.source,
        "scope": value.scope,
        "model": value.model,
        "feature": value.feature,
        "bindable": value.bindable,
    }


def _snapshot_dict(value: ProviderUsageSnapshot) -> dict:
    return {
        "provider_id": value.provider_id,
        "state": value.state.value,
        "observed_at": value.observed_at,
        "source_label": value.source_label,
        "account_label": value.account_label,
        "reason_code": value.reason_code,
        "action": value.action,
        "lanes": [_lane_dict(lane) for lane in value.lanes],
        "token_usage": _token_dict(value.token_usage),
        "credits": value.credits,
        "incident": value.incident,
    }


def _token_from(value: object) -> TokenUsage | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("invalid token usage")
    return TokenUsage(
        input_tokens=value.get("input_tokens"),
        cached_input_tokens=value.get("cached_input_tokens"),
        cache_creation_tokens=value.get("cache_creation_tokens"),
        output_tokens=value.get("output_tokens"),
        models=tuple(value.get("models", ())),
        estimated_cost_usd=value.get("estimated_cost_usd"),
        estimated_cache_savings_usd=value.get("estimated_cache_savings_usd"),
        pricing_coverage=value.get("pricing_coverage"),
        pricing_table_version=value.get("pricing_table_version"),
        pricing_as_of=value.get("pricing_as_of"),
    )


def _lane_from(value: object) -> QuotaLane:
    if not isinstance(value, dict):
        raise ValueError("invalid quota lane")
    return QuotaLane(
        provider_id=value.get("provider_id"),
        lane_id=value.get("lane_id"),
        label=value.get("label"),
        remaining=value.get("remaining"),
        used=value.get("used"),
        total=value.get("total"),
        unit=QuotaUnit(value.get("unit")),
        reset_at=value.get("reset_at"),
        source=value.get("source"),
        scope=value.get("scope", "all"),
        model=value.get("model"),
        feature=value.get("feature"),
        bindable=value.get("bindable", False),
    )


def _snapshot_from(value: object) -> ProviderUsageSnapshot:
    if not isinstance(value, dict):
        raise ValueError("invalid provider snapshot")
    return ProviderUsageSnapshot(
        provider_id=value.get("provider_id"),
        state=ProviderSourceState(value.get("state")),
        observed_at=value.get("observed_at"),
        source_label=value.get("source_label"),
        account_label=value.get("account_label"),
        reason_code=value.get("reason_code"),
        action=value.get("action"),
        lanes=tuple(_lane_from(row) for row in value.get("lanes", ())),
        token_usage=_token_from(value.get("token_usage")),
        credits=value.get("credits"),
        incident=value.get("incident"),
    )


class ProviderUsageStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (
            Path(path) if path is not None else default_provider_usage_store_path()
        ).expanduser().absolute()

    def load(self) -> tuple[ProviderUsageSnapshot, ...]:
        try:
            document = json.loads(
                read_private_text(self.path, max_bytes=MAX_STORE_BYTES)
            )
        except (OSError, ValueError, UnicodeError):
            return ()
        if not isinstance(document, dict) or document.get("version") != STORE_VERSION:
            return ()
        rows = document.get("snapshots")
        if not isinstance(rows, list):
            return ()
        snapshots: dict[str, ProviderUsageSnapshot] = {}
        for raw in rows[:64]:
            try:
                snapshot = _snapshot_from(raw)
            except (TypeError, ValueError):
                continue
            current = snapshots.get(snapshot.provider_id)
            if current is None or snapshot.observed_at >= current.observed_at:
                snapshots[snapshot.provider_id] = snapshot
        return tuple(
            snapshots[key]
            for key in sorted(snapshots)
        )

    def save(self, snapshots: tuple[ProviderUsageSnapshot, ...]) -> Path:
        if type(snapshots) is not tuple or not all(
            type(snapshot) is ProviderUsageSnapshot for snapshot in snapshots
        ):
            raise TypeError("snapshots must be ProviderUsageSnapshot values")
        latest: dict[str, ProviderUsageSnapshot] = {}
        for snapshot in snapshots:
            current = latest.get(snapshot.provider_id)
            if current is None or snapshot.observed_at >= current.observed_at:
                latest[snapshot.provider_id] = snapshot
        document = {
            "version": STORE_VERSION,
            "snapshots": [
                _snapshot_dict(latest[key]) for key in sorted(latest)
            ],
        }
        encoded = json.dumps(document, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > MAX_STORE_BYTES:
            raise ValueError("provider usage store exceeds size limit")
        atomic_private_write(self.path, encoded + "\n")
        return self.path


__all__ = [
    "ProviderUsageStore",
    "default_provider_usage_store_path",
]
