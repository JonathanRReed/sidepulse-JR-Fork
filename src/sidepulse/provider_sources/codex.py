"""Native Codex quota and local token usage source."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..credentials import read_codex_tokens
from ..provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    QuotaLane,
    QuotaUnit,
    TokenUsage,
)
from ..providers import default_state_dir, negotiated_provider_sources
from .. import usage_stats
from .common import bounded_percent, epoch_from_value, finite_number, slug


def default_codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    return Path(override).expanduser() if override else Path.home() / ".codex"


def _window_label(raw_label: str, minutes: float | None) -> tuple[str, str, str | None]:
    lower = raw_label.lower()
    weekly = bool(minutes is not None and minutes >= 6 * 24 * 60) or "week" in lower
    short = bool(minutes is not None and 240 <= minutes <= 360) or "5" in lower and "hour" in lower
    model = "spark" if "spark" in lower else None
    if model:
        return (
            "Spark Weekly" if weekly else "Spark 5-hour" if short else "Spark",
            "spark-weekly" if weekly else "spark-five-hour" if short else "spark",
            model,
        )
    if raw_label in {"primary", "5-hour", "five-hour"} or short:
        return ("5-hour", "five-hour", None)
    if raw_label in {"secondary", "weekly", "seven-day"} or weekly:
        return ("Weekly", "weekly", None)
    cleaned = " ".join(part for part in raw_label.replace("_", " ").replace("-", " ").split())
    title = cleaned.title() or "Additional limit"
    return (title, slug(title), slug(title))


def lanes_from_windows(windows: tuple[dict, ...] | list[dict]) -> tuple[QuotaLane, ...]:
    result: dict[str, QuotaLane] = {}
    for raw in tuple(windows)[:64]:
        if not isinstance(raw, dict):
            continue
        raw_label = str(raw.get("label") or raw.get("name") or raw.get("limit_name") or "additional").strip()
        used = bounded_percent(
            raw.get("used_percent", raw.get("utilization", raw.get("percent_used")))
        )
        remaining = bounded_percent(
            raw.get("remaining_percent", raw.get("percent_remaining"))
        )
        if remaining is None and used is not None:
            remaining = max(0.0, 100.0 - used)
        if used is None and remaining is not None:
            used = max(0.0, 100.0 - remaining)
        if used is None and remaining is None:
            continue
        minutes = finite_number(raw.get("window_minutes"))
        label, lane_id, model = _window_label(raw_label, minutes)
        reset_at = epoch_from_value(
            raw.get("resets_at", raw.get("reset_at", raw.get("resetAt")))
        )
        lane = QuotaLane(
            provider_id="codex",
            lane_id=lane_id,
            label=label,
            remaining=remaining,
            used=used,
            total=100.0,
            unit=QuotaUnit.PERCENT,
            reset_at=reset_at,
            source="codex-rollout",
            model=model,
            bindable=model is None and lane_id in {"five-hour", "weekly"},
        )
        current = result.get(lane_id)
        if current is None or (lane.reset_at or 0.0) >= (current.reset_at or 0.0):
            result[lane_id] = lane
    priority = {"five-hour": 0, "weekly": 1}
    return tuple(
        sorted(result.values(), key=lambda row: (priority.get(row.lane_id, 10), row.label.casefold()))
    )


def _transcript_source():
    return next(
        (
            source
            for source in negotiated_provider_sources()
            if source.source_key.provider_id == "codex"
            and source.source_key.capability_id == "transcript_usage"
            and source.observation_invocation_allowed
        ),
        None,
    )


def _token_usage(totals) -> TokenUsage | None:
    records = tuple(record for record in totals.records if record[0] == "codex")
    if not records:
        return None
    models = tuple(sorted({str(record[2]) for record in records if record[2]}))
    metrics = totals.pricing_coverage
    fraction = metrics.fraction
    priced = metrics.priced_records > 0
    return TokenUsage(
        input_tokens=sum(int(record[4]) for record in records),
        cached_input_tokens=sum(int(record[5]) for record in records),
        cache_creation_tokens=sum(int(record[6]) for record in records),
        output_tokens=sum(int(record[7]) for record in records),
        models=models,
        estimated_cost_usd=totals.estimated_cost_usd if priced else None,
        estimated_cache_savings_usd=(
            totals.estimated_cache_savings_usd if priced else None
        ),
        pricing_coverage=fraction,
        pricing_table_version=("sidepulse-pricing-v1" if priced else None),
        pricing_as_of=(metrics.table_as_of if priced else None),
    )


def collect_codex_usage(
    *,
    now: float | None = None,
    home: Path | None = None,
    since_epoch: float | None = None,
) -> ProviderUsageSnapshot:
    observed_at = time.time() if now is None else float(now)
    root = Path(home) if home is not None else default_codex_home()
    session_root = root / "sessions"
    credential = read_codex_tokens(root / "auth.json")
    source = _transcript_source()
    token_usage = None
    lanes: tuple[QuotaLane, ...] = ()

    if source is not None and session_root.is_dir():
        cache_path = default_state_dir() / "provider-usage" / "codex-scan.json"
        try:
            _result, totals = usage_stats._scan_provider_usage_with_totals(
                source,
                session_root,
                cache_path,
                since_epoch=(observed_at - 30 * 24 * 60 * 60 if since_epoch is None else since_epoch),
            )
            token_usage = _token_usage(totals)
            lanes = lanes_from_windows(totals.codex_rate_limit_evidence)
        except (OSError, ValueError):
            return ProviderUsageSnapshot(
                provider_id="codex",
                state=ProviderSourceState.FAILED,
                observed_at=observed_at,
                source_label="Codex local records",
                account_label=credential.account_id if credential else None,
                reason_code="codex_local_scan_failed",
                action="Retry Codex usage",
                lanes=(),
                token_usage=None,
                credits=None,
                incident=None,
            )

    if credential is None and token_usage is None and not lanes:
        return ProviderUsageSnapshot(
            provider_id="codex",
            state=ProviderSourceState.NEEDS_SIGN_IN,
            observed_at=observed_at,
            source_label="Codex local auth",
            account_label=None,
            reason_code="codex_login_not_found",
            action="Run codex login",
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )
    if not lanes:
        return ProviderUsageSnapshot(
            provider_id="codex",
            state=ProviderSourceState.PARTIAL,
            observed_at=observed_at,
            source_label="Codex local records",
            account_label=credential.account_id if credential else None,
            reason_code="codex_quota_not_observed",
            action="Run a new Codex turn, then refresh",
            lanes=(),
            token_usage=token_usage,
            credits=None,
            incident=None,
        )
    return ProviderUsageSnapshot(
        provider_id="codex",
        state=ProviderSourceState.READY,
        observed_at=observed_at,
        source_label="Codex local rollout records",
        account_label=credential.account_id if credential else None,
        reason_code=None,
        action=None,
        lanes=lanes,
        token_usage=token_usage,
        credits=None,
        incident=None,
    )


__all__ = ["collect_codex_usage", "default_codex_home", "lanes_from_windows"]
