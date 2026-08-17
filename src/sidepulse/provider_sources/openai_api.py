"""Optional OpenAI organization usage source using an Admin API key."""

from __future__ import annotations

import time

from ..provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    QuotaLane,
    QuotaUnit,
    TokenUsage,
)
from .common import ProviderSourceError, clean_string, finite_number, json_request

OPENAI_USAGE_URL = "https://api.openai.com/v1/organization/usage/completions"
OPENAI_COSTS_URL = "https://api.openai.com/v1/organization/costs"


def _results(payload: object) -> tuple[dict, ...]:
    if not isinstance(payload, dict):
        return ()
    buckets = payload.get("data")
    if not isinstance(buckets, list):
        return ()
    rows: list[dict] = []
    for bucket in buckets[:366]:
        if not isinstance(bucket, dict):
            continue
        values = bucket.get("results")
        if isinstance(values, list):
            rows.extend(value for value in values[:1024] if isinstance(value, dict))
    return tuple(rows[:8192])


def parse_organization_usage(
    usage_payload: object,
    cost_payload: object,
    *,
    observed_at: float,
    account_label: str | None,
) -> ProviderUsageSnapshot:
    usage_rows = _results(usage_payload)
    input_tokens = 0
    cached_input_tokens = 0
    output_tokens = 0
    models: set[str] = set()
    for row in usage_rows:
        input_tokens += max(0, int(finite_number(row.get("input_tokens")) or 0))
        cached_input_tokens += max(
            0, int(finite_number(row.get("input_cached_tokens")) or 0)
        )
        output_tokens += max(0, int(finite_number(row.get("output_tokens")) or 0))
        model = clean_string(row.get("model"), maximum=200)
        if model:
            models.add(model)
    cost_usd = 0.0
    cost_rows = _results(cost_payload)
    for row in cost_rows:
        amount = row.get("amount")
        if isinstance(amount, dict):
            value = finite_number(amount.get("value"))
        else:
            value = finite_number(row.get("amount"))
        if value is not None and value >= 0.0:
            cost_usd += value
    if not usage_rows and not cost_rows:
        raise ValueError("OpenAI organization returned no usage buckets")
    token_usage = TokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_creation_tokens=0,
        output_tokens=output_tokens,
        models=tuple(sorted(models)),
        estimated_cost_usd=None,
        estimated_cache_savings_usd=None,
        pricing_coverage=None,
        pricing_table_version=None,
        pricing_as_of=None,
    )
    lanes = ()
    if cost_rows:
        lanes = (
            QuotaLane(
                provider_id="openai-api",
                lane_id="organization-spend",
                label="Organization spend",
                remaining=None,
                used=cost_usd,
                total=None,
                unit=QuotaUnit.USD,
                reset_at=None,
                source="openai-organization-costs",
                feature="organization-spend",
                bindable=False,
            ),
        )
    return ProviderUsageSnapshot(
        provider_id="openai-api",
        state=ProviderSourceState.READY,
        observed_at=observed_at,
        source_label="OpenAI organization APIs",
        account_label=account_label,
        reason_code=None,
        action=None,
        lanes=lanes,
        token_usage=token_usage,
        credits=None,
        incident=None,
    )


def collect_openai_api_usage(
    *,
    admin_key: str | None,
    project_id: str | None = None,
    now: float | None = None,
    opener=None,
) -> ProviderUsageSnapshot:
    observed_at = time.time() if now is None else float(now)
    key = clean_string(admin_key, maximum=64 * 1024)
    if key is None:
        return ProviderUsageSnapshot(
            provider_id="openai-api",
            state=ProviderSourceState.NEEDS_CONSENT,
            observed_at=observed_at,
            source_label="OpenAI Admin API",
            account_label=project_id,
            reason_code="openai_admin_key_required",
            action="Add an OpenAI Admin API key",
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )
    start_time = int(observed_at - 30 * 24 * 60 * 60)
    query = f"?start_time={start_time}&bucket_width=1d&limit=31"
    if project_id:
        query += f"&project_ids={project_id}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "SidePulse/0.2.2",
    }
    try:
        usage = json_request(OPENAI_USAGE_URL + query, headers=headers, opener=opener)
        costs = json_request(OPENAI_COSTS_URL + query, headers=headers, opener=opener)
        return parse_organization_usage(
            usage,
            costs,
            observed_at=observed_at,
            account_label=project_id,
        )
    except (ProviderSourceError, ValueError) as exc:
        reason = exc.reason_code if isinstance(exc, ProviderSourceError) else "openai_usage_parse_failed"
        return ProviderUsageSnapshot(
            provider_id="openai-api",
            state=(
                ProviderSourceState.NEEDS_SIGN_IN
                if reason == "unauthorized"
                else ProviderSourceState.FAILED
            ),
            observed_at=observed_at,
            source_label="OpenAI organization APIs",
            account_label=project_id,
            reason_code=reason,
            action=("Replace the OpenAI Admin key" if reason == "unauthorized" else "Retry OpenAI API usage"),
            lanes=(),
            token_usage=None,
            credits=None,
            incident=None,
        )


__all__ = ["collect_openai_api_usage", "parse_organization_usage"]
