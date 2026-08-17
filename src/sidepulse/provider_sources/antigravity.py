"""Antigravity/agy local quota payload parser and bounded local source."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..private_io import read_private_text
from ..provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    QuotaLane,
    QuotaUnit,
)
from .common import (
    ProviderSourceError,
    bounded_percent,
    clean_string,
    epoch_from_value,
    json_request,
    slug,
)


def default_quota_files() -> tuple[Path, ...]:
    return (
        Path.home() / ".antigravity" / "quota.json",
        Path.home() / ".agy" / "quota.json",
    )


def _family_rows(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        for key in ("quotaFamilies", "quota_families", "families", "limits"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)][:64]
        rows: list[dict] = []
        for key, value in payload.items():
            if isinstance(value, dict) and any(
                name in value
                for name in (
                    "remainingPercent",
                    "remaining_percent",
                    "usedPercent",
                    "used_percent",
                )
            ):
                rows.append({"name": key, **value})
        if rows:
            return rows[:64]
        for value in payload.values():
            nested = _family_rows(value)
            if nested:
                return nested
    elif isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)][:64]
    return []


def parse_quota_payload(
    payload: object,
    *,
    observed_at: float,
) -> ProviderUsageSnapshot:
    families = _family_rows(payload)
    lanes: list[QuotaLane] = []
    for index, row in enumerate(families):
        name = clean_string(
            row.get("name", row.get("label", row.get("displayName"))),
            maximum=160,
        ) or f"Quota {index + 1}"
        remaining = bounded_percent(
            row.get("remainingPercent", row.get("remaining_percent"))
        )
        used = bounded_percent(row.get("usedPercent", row.get("used_percent")))
        if remaining is None and used is not None:
            remaining = 100.0 - used
        if used is None and remaining is not None:
            used = 100.0 - remaining
        if used is None or remaining is None:
            continue
        lower = name.lower()
        model = None
        feature = None
        if any(value in lower for value in ("claude", "gpt", "gemini")):
            model = slug(name)
        elif not any(value in lower for value in ("session", "week", "daily", "month")):
            feature = slug(name)
        lane_id = slug(name)
        lanes.append(
            QuotaLane(
                provider_id="antigravity",
                lane_id=lane_id,
                label=name,
                remaining=remaining,
                used=used,
                total=100.0,
                unit=QuotaUnit.PERCENT,
                reset_at=epoch_from_value(
                    row.get("resetAt", row.get("reset_at", row.get("resetsAt")))
                ),
                source="antigravity-local",
                model=model,
                feature=feature,
                bindable=False,
            )
        )
    if not lanes:
        raise ValueError("Antigravity payload has no quota families")
    account = None
    if isinstance(payload, dict):
        account = clean_string(
            payload.get("account", payload.get("email", payload.get("user"))),
            maximum=300,
        )
    return ProviderUsageSnapshot(
        provider_id="antigravity",
        state=ProviderSourceState.READY,
        observed_at=observed_at,
        source_label="Antigravity local quota",
        account_label=account,
        reason_code=None,
        action=None,
        lanes=tuple(lanes),
        token_usage=None,
        credits=None,
        incident=None,
    )


def collect_antigravity_usage(
    *,
    now: float | None = None,
    endpoint: str | None = None,
    quota_files: tuple[Path, ...] | None = None,
    opener=None,
) -> ProviderUsageSnapshot:
    observed_at = time.time() if now is None else float(now)
    for path in quota_files or default_quota_files():
        try:
            payload = json.loads(read_private_text(path, max_bytes=1024 * 1024))
            return parse_quota_payload(payload, observed_at=observed_at)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return ProviderUsageSnapshot(
                provider_id="antigravity",
                state=ProviderSourceState.FAILED,
                observed_at=observed_at,
                source_label="Antigravity local quota",
                account_label=None,
                reason_code="antigravity_local_payload_invalid",
                action="Restart Antigravity or agy",
                lanes=(),
                token_usage=None,
                credits=None,
                incident=None,
            )
    resolved_endpoint = endpoint or os.environ.get("ANTIGRAVITY_USAGE_URL")
    if not resolved_endpoint:
        return ProviderUsageSnapshot(
            provider_id="antigravity",
            state=ProviderSourceState.NOT_DETECTED,
            observed_at=observed_at,
            source_label="Antigravity local server",
            account_label=None,
            reason_code="antigravity_source_not_detected",
            action="Start Antigravity or agy",
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )
    if not resolved_endpoint.startswith(("http://127.0.0.1:", "http://localhost:")):
        return ProviderUsageSnapshot(
            provider_id="antigravity",
            state=ProviderSourceState.FAILED,
            observed_at=observed_at,
            source_label="Antigravity local server",
            account_label=None,
            reason_code="antigravity_endpoint_not_loopback",
            action="Use a loopback Antigravity endpoint",
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )
    try:
        payload = json_request(
            resolved_endpoint,
            headers={"Accept": "application/json", "User-Agent": "SidePulse/0.2.2"},
            opener=opener,
        )
        return parse_quota_payload(payload, observed_at=observed_at)
    except (ProviderSourceError, ValueError) as exc:
        reason = exc.reason_code if isinstance(exc, ProviderSourceError) else "antigravity_parse_failed"
        return ProviderUsageSnapshot(
            provider_id="antigravity",
            state=ProviderSourceState.FAILED,
            observed_at=observed_at,
            source_label="Antigravity local server",
            account_label=None,
            reason_code=reason,
            action="Restart Antigravity or agy",
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )


__all__ = [
    "collect_antigravity_usage",
    "default_quota_files",
    "parse_quota_payload",
]
