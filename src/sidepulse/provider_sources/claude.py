"""Native Claude subscription quota and local transcript usage source."""

from __future__ import annotations

import time
from pathlib import Path

from .. import claude_quota, usage_stats
from ..credentials import (
    CLAUDE_CODE_KEYCHAIN,
    CredentialOutcome,
    KeychainConsentLedger,
    read_keychain_secret,
)
from ..provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    QuotaLane,
    QuotaUnit,
    TokenUsage,
)
from ..providers import default_state_dir, negotiated_provider_sources
from .common import bounded_percent, epoch_from_value, slug


def default_claude_root() -> Path:
    return Path.home() / ".claude"


def _label(raw_label: str) -> tuple[str, str, str | None]:
    normalized = " ".join(raw_label.replace("_", " ").replace("-", " ").split())
    lower = normalized.lower()
    if lower in {"5 hour", "five hour"}:
        return ("5-hour", "five-hour", None)
    if lower in {"weekly", "seven day", "7 day"}:
        return ("Weekly", "weekly", None)
    model = None
    if lower.endswith(" only"):
        model = slug(normalized[:-5])
    elif "weekly" in lower:
        candidate = lower.replace("weekly", "").strip()
        model = slug(candidate) if candidate else None
    if model:
        display = model.replace("-", " ").title()
        return (f"{display} Weekly", f"{model}-weekly", model)
    title = normalized.title() or "Additional limit"
    return (title, slug(title), slug(title))


def lanes_from_windows(windows: list[dict] | tuple[dict, ...]) -> tuple[QuotaLane, ...]:
    rows: dict[str, QuotaLane] = {}
    for raw in tuple(windows)[:64]:
        if not isinstance(raw, dict):
            continue
        raw_label = str(raw.get("label") or raw.get("name") or "additional").strip()
        used = bounded_percent(
            raw.get("utilization", raw.get("used_percent", raw.get("percent_used")))
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
        label, lane_id, model = _label(raw_label)
        reset_at = epoch_from_value(
            raw.get("resets_at", raw.get("reset_at", raw.get("resetAt")))
        )
        rows[lane_id] = QuotaLane(
            provider_id="claude",
            lane_id=lane_id,
            label=label,
            remaining=remaining,
            used=used,
            total=100.0,
            unit=QuotaUnit.PERCENT,
            reset_at=reset_at,
            source="claude-oauth",
            model=model,
            bindable=model is None and lane_id in {"five-hour", "weekly"},
        )
    priority = {"five-hour": 0, "weekly": 1}
    return tuple(
        sorted(rows.values(), key=lambda row: (priority.get(row.lane_id, 10), row.label.casefold()))
    )


def _transcript_source():
    return next(
        (
            source
            for source in negotiated_provider_sources()
            if source.source_key.provider_id == "claude"
            and source.source_key.capability_id == "transcript_usage"
            and source.observation_invocation_allowed
        ),
        None,
    )


def _local_token_usage(root: Path, observed_at: float) -> TokenUsage | None:
    source = _transcript_source()
    if source is None or not root.is_dir():
        return None
    cache_path = default_state_dir() / "provider-usage" / "claude-scan.json"
    try:
        _result, totals = usage_stats._scan_provider_usage_with_totals(
            source,
            root,
            cache_path,
            since_epoch=observed_at - 30 * 24 * 60 * 60,
        )
    except (OSError, ValueError):
        return None
    records = tuple(record for record in totals.records if record[0] == "claude")
    if not records:
        return None
    metrics = totals.pricing_coverage
    priced = metrics.priced_records > 0
    return TokenUsage(
        input_tokens=sum(int(record[4]) for record in records),
        cached_input_tokens=sum(int(record[5]) for record in records),
        cache_creation_tokens=sum(int(record[6]) for record in records),
        output_tokens=sum(int(record[7]) for record in records),
        models=tuple(sorted({str(record[2]) for record in records if record[2]})),
        estimated_cost_usd=totals.estimated_cost_usd if priced else None,
        estimated_cache_savings_usd=(
            totals.estimated_cache_savings_usd if priced else None
        ),
        pricing_coverage=metrics.fraction,
        pricing_table_version=(metrics.table_version if priced else None),
        pricing_as_of=(metrics.table_as_of if priced else None),
    )


def _credential_failure_snapshot(
    *,
    observed_at: float,
    token_usage: TokenUsage | None,
    outcome: CredentialOutcome,
) -> ProviderUsageSnapshot:
    if outcome is CredentialOutcome.NOT_FOUND:
        state = ProviderSourceState.NEEDS_SIGN_IN
        reason = "claude_login_not_found"
        action = "Run claude login"
    elif outcome is CredentialOutcome.COOLING_DOWN:
        state = ProviderSourceState.PARTIAL if token_usage else ProviderSourceState.NEEDS_CONSENT
        reason = "claude_keychain_cooling_down"
        action = "Retry Claude permission later"
    elif outcome is CredentialOutcome.DENIED:
        state = ProviderSourceState.PARTIAL if token_usage else ProviderSourceState.NEEDS_CONSENT
        reason = "claude_keychain_denied"
        action = "Allow Claude usage access"
    else:
        state = ProviderSourceState.PARTIAL if token_usage else ProviderSourceState.NEEDS_CONSENT
        reason = "claude_usage_permission_required"
        action = "Connect Claude usage"
    return ProviderUsageSnapshot(
        provider_id="claude",
        state=state,
        observed_at=observed_at,
        source_label="Claude local transcripts",
        account_label=None,
        reason_code=reason,
        action=action,
        lanes=(),
        token_usage=token_usage,
        credits=None,
        incident=None,
    )


def collect_claude_usage(
    *,
    now: float | None = None,
    root: Path | None = None,
    allow_keychain_prompt: bool = False,
    keychain_runner=None,
    quota_opener=None,
) -> ProviderUsageSnapshot:
    observed_at = time.time() if now is None else float(now)
    transcript_root = Path(root) if root is not None else default_claude_root()
    token_usage = _local_token_usage(transcript_root, observed_at)
    ledger = KeychainConsentLedger(
        default_state_dir() / "provider-usage" / "keychain-consent.json"
    )
    result = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN,
        allow_prompt=allow_keychain_prompt,
        ledger=ledger,
        now=observed_at,
        runner=keychain_runner,
    )
    if not result.ok:
        return _credential_failure_snapshot(
            observed_at=observed_at,
            token_usage=token_usage,
            outcome=result.outcome,
        )
    credential = claude_quota.credential_from_keychain_payload(result.secret)
    if credential is None:
        action = "Run claude once, then refresh" if claude_quota.credential_needs_sign_in(result.secret) else "Run claude login"
        return ProviderUsageSnapshot(
            provider_id="claude",
            state=ProviderSourceState.PARTIAL if token_usage else ProviderSourceState.NEEDS_SIGN_IN,
            observed_at=observed_at,
            source_label="Claude local transcripts",
            account_label=None,
            reason_code="claude_access_token_unavailable",
            action=action,
            lanes=(),
            token_usage=token_usage,
            credits=None,
            incident=None,
        )
    if credential.is_expired(observed_at):
        return ProviderUsageSnapshot(
            provider_id="claude",
            state=ProviderSourceState.PARTIAL if token_usage else ProviderSourceState.NEEDS_SIGN_IN,
            observed_at=observed_at,
            source_label="Claude OAuth",
            account_label=credential.subscription_type,
            reason_code="claude_access_token_expired",
            action="Run claude once, then refresh",
            lanes=(),
            token_usage=token_usage,
            credits=None,
            incident=None,
        )
    try:
        windows = claude_quota.fetch_windows(
            access_token=credential.access_token,
            opener=quota_opener,
        )
    except claude_quota.ClaudeQuotaUnavailableError as exc:
        return ProviderUsageSnapshot(
            provider_id="claude",
            state=ProviderSourceState.PARTIAL if token_usage else ProviderSourceState.FAILED,
            observed_at=observed_at,
            source_label="Claude OAuth",
            account_label=credential.subscription_type,
            reason_code=str(exc),
            action=("Run claude login" if "unauthorized" in str(exc) else "Retry Claude usage"),
            lanes=(),
            token_usage=token_usage,
            credits=None,
            incident=None,
        )
    lanes = lanes_from_windows(windows)
    if not lanes:
        return ProviderUsageSnapshot(
            provider_id="claude",
            state=ProviderSourceState.PARTIAL if token_usage else ProviderSourceState.FAILED,
            observed_at=observed_at,
            source_label="Claude OAuth",
            account_label=credential.subscription_type,
            reason_code="claude_no_quota_windows",
            action="Retry Claude usage",
            lanes=(),
            token_usage=token_usage,
            credits=None,
            incident=None,
        )
    return ProviderUsageSnapshot(
        provider_id="claude",
        state=ProviderSourceState.READY,
        observed_at=observed_at,
        source_label="Claude OAuth + local transcripts",
        account_label=credential.subscription_type,
        reason_code=None,
        action=None,
        lanes=lanes,
        token_usage=token_usage,
        credits=None,
        incident=None,
    )


__all__ = ["collect_claude_usage", "default_claude_root", "lanes_from_windows"]
