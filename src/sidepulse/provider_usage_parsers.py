"""Bounded parsers that map provider-owned usage payloads into SidePulse facts."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
    normalize_dynamic_lane,
)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _reset_epoch(value: object) -> float | None:
    number = _number(value)
    if number is not None:
        return number if number >= 0.0 else None
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _used_percent(value: object) -> float | None:
    if not isinstance(value, dict):
        return None
    for key in ("used_percent", "usedPercent", "usagePercent", "percentUsed"):
        number = _number(value.get(key))
        if number is not None:
            return max(0.0, min(100.0, number))
    used = _number(value.get("used"))
    limit = _number(value.get("limit"))
    if used is not None and limit is not None and limit > 0.0:
        return max(0.0, min(100.0, used / limit * 100.0))
    return None


def _remaining_from_entry(value: object) -> float | None:
    used = _used_percent(value)
    return None if used is None else 100.0 - used


def _snapshot(
    provider_id: str,
    *,
    observed_at: float,
    lanes: tuple[UsageLane, ...] = (),
    account_label: str | None = None,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    model_count: int = 0,
    estimated_cost_usd: float | None = None,
    cache_savings_usd: float | None = None,
    credits_remaining: float | None = None,
    incident: str | None = None,
) -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        provider_id=provider_id,
        account_label=account_label,
        observed_at=observed_at,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=lanes,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        model_count=model_count,
        estimated_cost_usd=estimated_cost_usd,
        cache_savings_usd=cache_savings_usd,
        credits_remaining=credits_remaining,
        incident=incident,
    )


def parse_codex_usage(
    *,
    windows: list[dict] | tuple[dict, ...],
    observed_at: float,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    model_count: int = 0,
    estimated_cost_usd: float | None = None,
    cache_savings_usd: float | None = None,
    account_label: str | None = None,
) -> ProviderUsageSnapshot:
    lanes: list[UsageLane] = []
    for index, entry in enumerate(tuple(windows)[:64]):
        if not isinstance(entry, dict):
            continue
        raw_label = str(entry.get("label") or f"limit-{index + 1}").strip()
        minutes = _number(entry.get("window_minutes"))
        normalized = raw_label.lower()
        if normalized in {"primary", "5-hour", "five-hour"}:
            lane_id, label = "five-hour", "5-hour"
        elif normalized in {"secondary", "weekly"}:
            lane_id, label = "weekly", "Weekly"
        elif normalized in {"limit", f"limit-{index + 1}"} and minutes == 300:
            lane_id, label = "five-hour", "5-hour"
        elif normalized in {"limit", f"limit-{index + 1}"} and minutes == 10080:
            lane_id, label = "weekly", "Weekly"
        else:
            lane_id = "-".join(
                part for part in raw_label.lower().replace("_", "-").split() if part
            )[:128] or f"limit-{index + 1}"
            label = raw_label
        used = _number(entry.get("used_percent"))
        remaining = None if used is None else max(0.0, min(100.0, 100.0 - used))
        lanes.append(
            normalize_dynamic_lane(
                provider_id="codex",
                lane_id=lane_id,
                label=label,
                remaining_percent=remaining,
                reset_at=_reset_epoch(entry.get("resets_at", entry.get("reset_at"))),
                source_id="codex-rollouts",
                known_lane_ids={"five-hour", "weekly"},
            )
        )
    return _snapshot(
        "codex",
        observed_at=observed_at,
        lanes=tuple(lanes),
        account_label=account_label,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        model_count=model_count,
        estimated_cost_usd=estimated_cost_usd,
        cache_savings_usd=cache_savings_usd,
    )


def parse_claude_usage(
    *,
    windows: list[dict] | tuple[dict, ...],
    observed_at: float,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    model_count: int = 0,
    estimated_cost_usd: float | None = None,
    cache_savings_usd: float | None = None,
    account_label: str | None = None,
) -> ProviderUsageSnapshot:
    lanes: list[UsageLane] = []
    for index, entry in enumerate(tuple(windows)[:64]):
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or f"Limit {index + 1}").strip()
        normalized = label.lower().replace("_", "-")
        if normalized in {"5-hour", "five-hour"}:
            lane_id = "five-hour"
        elif normalized in {"weekly", "seven-day", "7-day"}:
            lane_id = "weekly"
            label = "Weekly"
        else:
            lane_id = "-".join(part for part in normalized.split() if part)[:128]
        used = _number(entry.get("used_percent"))
        lanes.append(
            normalize_dynamic_lane(
                provider_id="claude",
                lane_id=lane_id or f"limit-{index + 1}",
                label=label,
                remaining_percent=(
                    None if used is None else max(0.0, min(100.0, 100.0 - used))
                ),
                reset_at=_reset_epoch(entry.get("resets_at", entry.get("reset_at"))),
                source_id="claude-oauth",
                known_lane_ids={"five-hour", "weekly", "weekly-opus", "weekly-sonnet"},
            )
        )
    return _snapshot(
        "claude",
        observed_at=observed_at,
        lanes=tuple(lanes),
        account_label=account_label,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        model_count=model_count,
        estimated_cost_usd=estimated_cost_usd,
        cache_savings_usd=cache_savings_usd,
    )


def parse_cursor_usage(payload: object, *, observed_at: float) -> ProviderUsageSnapshot:
    if not isinstance(payload, dict):
        raise ValueError("invalid Cursor usage payload")
    reset = _reset_epoch(
        payload.get("billingCycleEnd", payload.get("billing_cycle_end"))
    )
    definitions = (
        ("included-plan", "Included plan", payload.get("planUsage", payload.get("plan"))),
        (
            "auto-composer",
            "Auto + Composer",
            payload.get("autoComposerUsage", payload.get("auto_composer")),
        ),
        ("api-models", "API models", payload.get("apiUsage", payload.get("api"))),
    )
    lanes = tuple(
        UsageLane(
            provider_id="cursor",
            lane_id=lane_id,
            label=label,
            remaining_percent=_remaining_from_entry(entry),
            reset_at=reset,
            scope="all",
            model=None,
            feature=lane_id,
            bindable=True,
            source_id="cursor-account-api",
        )
        for lane_id, label, entry in definitions
        if isinstance(entry, dict)
    )
    extra_cents = _number(
        payload.get("extraUsageCents", payload.get("extra_usage_cents"))
    )
    account = payload.get("account")
    account_label = (
        str(account.get("email")).strip()
        if isinstance(account, dict) and account.get("email")
        else None
    )
    return _snapshot(
        "cursor",
        observed_at=observed_at,
        lanes=lanes,
        account_label=account_label,
        estimated_cost_usd=(
            max(0.0, extra_cents) / 100.0 if extra_cents is not None else None
        ),
    )


def parse_devin_usage(payload: object, *, observed_at: float) -> ProviderUsageSnapshot:
    if not isinstance(payload, dict):
        raise ValueError("invalid Devin usage payload")
    lanes: list[UsageLane] = []
    for lane_id, label in (("daily", "Daily"), ("weekly", "Weekly")):
        entry = payload.get(lane_id)
        if not isinstance(entry, dict):
            entry = payload.get(f"{lane_id}Usage")
        if not isinstance(entry, dict):
            continue
        lanes.append(
            UsageLane(
                provider_id="devin",
                lane_id=lane_id,
                label=label,
                remaining_percent=_remaining_from_entry(entry),
                reset_at=_reset_epoch(
                    entry.get("resets_at", entry.get("reset_at"))
                ),
                scope="all",
                model=None,
                feature=None,
                bindable=True,
                source_id="devin-account-api",
            )
        )
    organization = payload.get("organization", payload.get("organization_id"))
    return _snapshot(
        "devin",
        observed_at=observed_at,
        lanes=tuple(lanes),
        account_label=str(organization).strip() if organization else None,
    )


def _grok_percent(payload: dict[str, Any]) -> float | None:
    config = payload.get("config")
    if isinstance(config, dict):
        direct = _number(config.get("creditUsagePercent"))
        if direct is not None:
            return max(0.0, min(100.0, direct))
    used_container = payload.get("onDemandUsed")
    cap_container = payload.get("onDemandCap")
    used = _number(used_container.get("val")) if isinstance(used_container, dict) else None
    cap = _number(cap_container.get("val")) if isinstance(cap_container, dict) else None
    if used is not None and cap is not None and cap > 0.0:
        return max(0.0, min(100.0, used / cap * 100.0))
    return None


def parse_grok_usage(payload: object, *, observed_at: float) -> ProviderUsageSnapshot:
    if not isinstance(payload, dict):
        raise ValueError("invalid Grok usage payload")
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    reset = _reset_epoch(
        config.get("currentPeriod", {}).get("end")
        if isinstance(config.get("currentPeriod"), dict)
        else config.get("billingPeriodEnd")
    )
    if reset is None:
        reset = _reset_epoch(config.get("billingPeriodEnd"))
    cycle_seconds = None if reset is None else reset - observed_at
    if cycle_seconds is not None and 4 * 86400 <= cycle_seconds <= 10 * 86400:
        label = "Weekly"
    elif cycle_seconds is not None and 20 * 86400 <= cycle_seconds <= 45 * 86400:
        label = "Monthly"
    else:
        label = "Credits"
    used = _grok_percent(payload)
    lane = UsageLane(
        provider_id="grok",
        lane_id="credits",
        label=label,
        remaining_percent=None if used is None else 100.0 - used,
        reset_at=reset,
        scope="all",
        model=None,
        feature="credits",
        bindable=True,
        source_id="grok-billing",
    )
    return _snapshot(
        "grok",
        observed_at=observed_at,
        lanes=(lane,),
        account_label=str(payload.get("email")).strip() if payload.get("email") else None,
    )


def parse_antigravity_usage(
    payload: object,
    *,
    observed_at: float,
) -> ProviderUsageSnapshot:
    if not isinstance(payload, dict):
        raise ValueError("invalid Antigravity usage payload")
    response = payload.get("response", payload)
    groups = response.get("groups") if isinstance(response, dict) else None
    if not isinstance(groups, list):
        raise ValueError("Antigravity payload has no quota groups")
    lanes: list[UsageLane] = []
    for group in groups[:16]:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("displayName") or "").lower()
        if "gemini" in group_name:
            prefix, scope = "Gemini", "gemini"
        elif "claude" in group_name or "gpt" in group_name:
            prefix, scope = "Claude + GPT", "claude-gpt"
        else:
            continue
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            continue
        for bucket in buckets[:16]:
            if not isinstance(bucket, dict):
                continue
            bucket_id = str(bucket.get("bucketId") or bucket.get("displayName") or "")
            bucket_text = f"{bucket_id} {bucket.get('displayName') or ''}".lower()
            window = "weekly" if "week" in bucket_text else "session"
            remaining_container = bucket.get("remaining")
            fraction = (
                _number(remaining_container.get("remainingFraction"))
                if isinstance(remaining_container, dict)
                else _number(bucket.get("remainingFraction"))
            )
            lanes.append(
                UsageLane(
                    provider_id="antigravity",
                    lane_id=f"{scope}-{window}",
                    label=f"{prefix} {'Weekly' if window == 'weekly' else 'Session'}",
                    remaining_percent=(
                        None
                        if fraction is None
                        else max(0.0, min(100.0, fraction * 100.0))
                    ),
                    reset_at=_reset_epoch(
                        bucket.get("resetTime", bucket.get("reset_at"))
                    ),
                    scope=scope,
                    model=None,
                    feature=None,
                    bindable=True,
                    source_id="antigravity-app",
                )
            )
    return _snapshot(
        "antigravity",
        observed_at=observed_at,
        lanes=tuple(lanes),
        account_label=(
            str(response.get("accountEmail")).strip()
            if isinstance(response, dict) and response.get("accountEmail")
            else None
        ),
    )


def parse_openai_api_usage(
    payload: object,
    *,
    observed_at: float,
) -> ProviderUsageSnapshot:
    if not isinstance(payload, dict):
        raise ValueError("invalid OpenAI API usage payload")
    usage = payload.get("usage")
    costs = payload.get("costs")
    input_tokens = 0
    output_tokens = 0
    models: set[str] = set()
    if isinstance(usage, dict) and isinstance(usage.get("data"), list):
        for bucket in usage["data"][:366]:
            if not isinstance(bucket, dict) or not isinstance(bucket.get("results"), list):
                continue
            for row in bucket["results"][:1024]:
                if not isinstance(row, dict):
                    continue
                input_tokens += int(max(0.0, _number(row.get("input_tokens")) or 0.0))
                output_tokens += int(max(0.0, _number(row.get("output_tokens")) or 0.0))
                if isinstance(row.get("model"), str) and row["model"].strip():
                    models.add(row["model"].strip())
    cost = 0.0
    cost_seen = False
    if isinstance(costs, dict) and isinstance(costs.get("data"), list):
        for bucket in costs["data"][:366]:
            if not isinstance(bucket, dict) or not isinstance(bucket.get("results"), list):
                continue
            for row in bucket["results"][:1024]:
                if not isinstance(row, dict):
                    continue
                amount = row.get("amount")
                value = _number(amount.get("value")) if isinstance(amount, dict) else None
                if value is not None:
                    cost += max(0.0, value)
                    cost_seen = True
    return _snapshot(
        "openai-api",
        observed_at=observed_at,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model_count=len(models),
        estimated_cost_usd=cost if cost_seen else None,
    )


__all__ = [
    "parse_antigravity_usage",
    "parse_claude_usage",
    "parse_codex_usage",
    "parse_cursor_usage",
    "parse_devin_usage",
    "parse_grok_usage",
    "parse_openai_api_usage",
]
