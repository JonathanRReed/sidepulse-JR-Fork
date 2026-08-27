"""Native Codex and Claude quota plus local token accounting."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .provider_usage_parsers import parse_claude_usage, parse_codex_usage
from .provider_usage_platform import ProviderSourceState, ProviderUsageSnapshot
from .provider_usage_settings import ProviderPreference


def _failure(
    provider_id: str,
    *,
    observed_at: float,
    state: ProviderSourceState,
    reason: str,
    action: str,
) -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        provider_id=provider_id,
        account_label=None,
        observed_at=observed_at,
        state=state,
        reason_code=reason,
        action_label=action,
        lanes=(),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )


def _credential(credentials, provider_id: str, account: str) -> str | None:
    try:
        result = credentials.get(provider_id, account)
    except Exception:
        return None
    value = getattr(result, "secret", None)
    if not getattr(result, "available", False) or not isinstance(value, str):
        return None
    value = value.strip()
    if not value or "\x00" in value or len(value.encode("utf-8")) > 64 * 1024:
        return None
    return value


def _default_provider_local_scan(
    provider_id: str,
    home: Path,
    observed_at: float,
) -> dict[str, object] | None:
    """Use SidePulse's bounded transcript scanner for exactly one provider."""
    try:
        from . import usage_stats
        from .providers import negotiated_provider_sources
    except ImportError:
        return None
    source = next(
        (
            item
            for item in negotiated_provider_sources()
            if item.source_key.provider_id == provider_id
            and item.source_key.capability_id == "transcript_usage"
            and item.observation_invocation_allowed
        ),
        None,
    )
    if source is None:
        return None
    root = (
        Path(home) / ".codex" / "sessions"
        if provider_id == "codex"
        else Path(home) / ".claude" / "projects"
    )
    cache = Path(home) / ".local" / "state" / "sidepulse" / "provider-usage-cache.json"
    try:
        result, totals = usage_stats._scan_provider_usage_with_totals(
            source,
            root,
            cache,
            since_epoch=max(0.0, observed_at - 30 * 24 * 60 * 60),
        )
    except Exception:
        return None
    records = tuple(
        record for record in getattr(totals, "records", ()) if record[0] == provider_id
    )
    document: dict[str, object] = {
        "input_tokens": int(getattr(result, "input_tokens", 0)),
        "cached_input_tokens": int(getattr(result, "cached_input_tokens", 0)),
        "output_tokens": int(getattr(result, "output_tokens", 0)),
        "model_count": len({record[2] for record in records if len(record) > 2}),
        "estimated_cost_usd": getattr(result, "covered_cost_estimate_usd", None),
        "cache_savings_usd": getattr(
            result,
            "covered_cache_savings_estimate_usd",
            None,
        ),
    }
    if provider_id == "codex":
        document["windows_observed_at"] = getattr(
            totals, "codex_rate_limit_observed_at", None
        )
        document["windows"] = [
            dict(window)
            for window in tuple(getattr(totals, "codex_rate_limit_evidence", ()))[:64]
            if isinstance(window, dict)
        ]
        try:
            from .credentials import read_codex_tokens

            tokens = read_codex_tokens(Path(home) / ".codex" / "auth.json")
        except Exception:
            tokens = None
        if tokens is not None and getattr(tokens, "account_id", None):
            document["account_label"] = str(tokens.account_id)
    return document


def _default_codex_local_scan(home: Path, observed_at: float) -> dict[str, object] | None:
    return _default_provider_local_scan("codex", home, observed_at)


def _default_claude_local_scan(home: Path, observed_at: float) -> dict[str, object] | None:
    return _default_provider_local_scan("claude", home, observed_at)


def _default_claude_quota_fetch(access_token: str) -> list[dict]:
    from .claude_quota import fetch_windows

    return fetch_windows(access_token=access_token)


#: Codex quota is only ever as fresh as the newest rollout the CLI wrote.
#: Past this, a reading is reported as STALE rather than as the current
#: number: usage burned on another machine, in the web app, or through a
#: surface that writes no rollout is invisible here, and silence is not
#: evidence that nothing changed. Reported live as "why does it say 48
#: percent, it should be around 96" -- the 48 was three days old.
CODEX_READING_STALE_SECONDS = 6 * 3600.0


def _codex_reading_freshness(
    snapshot: ProviderUsageSnapshot,
    evidence_observed_at: object,
    *,
    observed_at: float,
) -> ProviderUsageSnapshot:
    """Mark a Codex snapshot stale when its evidence has stopped moving."""
    if not isinstance(evidence_observed_at, (int, float)) or isinstance(
        evidence_observed_at, bool
    ):
        return snapshot
    age = float(observed_at) - float(evidence_observed_at)
    if age <= CODEX_READING_STALE_SECONDS or not snapshot.lanes:
        return snapshot
    hours = age / 3600.0
    since = f"{hours / 24.0:.0f}d" if hours >= 48.0 else f"{hours:.0f}h"
    # "run Codex to refresh" was said to a user who HAD just run Codex --
    # opened it, poked around, quit. The evidence only moves when a turn
    # COMPLETES, so the instruction has to say so.
    return dataclasses.replace(
        snapshot,
        state=ProviderSourceState.STALE,
        reason_code="local_reading_stale",
        action_label=(
            f"Last read {since} ago — finish one Codex prompt to refresh"
        ),
    )


def collect_codex(
    preference: ProviderPreference,
    *,
    home: Path,
    observed_at: float,
    local_scanner: Callable[[Path, float], dict[str, object] | None] = _default_codex_local_scan,
) -> ProviderUsageSnapshot:
    del preference
    facts = local_scanner(Path(home), observed_at)
    if not isinstance(facts, dict):
        return _failure(
            "codex",
            observed_at=observed_at,
            state=ProviderSourceState.SOURCE_NOT_FOUND,
            reason="local_usage_not_found",
            action="Use Codex once or sign in",
        )
    windows = facts.get("windows")
    if not isinstance(windows, (list, tuple)):
        windows = ()
    observed_evidence_at = facts.get("windows_observed_at")
    try:
        snapshot = parse_codex_usage(
            windows=windows,
            observed_at=observed_at,
            input_tokens=max(0, int(facts.get("input_tokens", 0))),
            cached_input_tokens=max(0, int(facts.get("cached_input_tokens", 0))),
            output_tokens=max(0, int(facts.get("output_tokens", 0))),
            model_count=max(0, int(facts.get("model_count", 0))),
            estimated_cost_usd=facts.get("estimated_cost_usd"),
            cache_savings_usd=facts.get("cache_savings_usd"),
            account_label=(
                str(facts["account_label"])
                if facts.get("account_label") is not None
                else None
            ),
        )
        return _codex_reading_freshness(
            snapshot, observed_evidence_at, observed_at=observed_at
        )
    except (TypeError, ValueError):
        return _failure(
            "codex",
            observed_at=observed_at,
            state=ProviderSourceState.ERROR,
            reason="invalid_local_usage",
            action="Retry",
        )


def _with_local_usage(
    snapshot: ProviderUsageSnapshot,
    local: dict[str, object] | None,
) -> ProviderUsageSnapshot:
    if not isinstance(local, dict):
        return snapshot
    try:
        return replace(
            snapshot,
            input_tokens=max(0, int(local.get("input_tokens", 0))),
            cached_input_tokens=max(0, int(local.get("cached_input_tokens", 0))),
            output_tokens=max(0, int(local.get("output_tokens", 0))),
            model_count=max(0, int(local.get("model_count", 0))),
            estimated_cost_usd=local.get("estimated_cost_usd"),
            cache_savings_usd=local.get("cache_savings_usd"),
        )
    except (TypeError, ValueError):
        return snapshot


def collect_claude(
    preference: ProviderPreference,
    *,
    home: Path,
    observed_at: float,
    credentials,
    quota_fetcher: Callable[[str], list[dict]] = _default_claude_quota_fetch,
    local_scanner: Callable[[Path, float], dict[str, object] | None] = _default_claude_local_scan,
) -> ProviderUsageSnapshot:
    del preference
    local = local_scanner(Path(home), observed_at)
    # Renew BEFORE asking, not after a 401. The reactive path made the
    # dropdown flash "reconnect" once per token lifetime while the fix
    # was already automatic (2026-08-27 owner report).
    try:
        from .provider_reconnect import (
            claude_token_is_stale,
            renew_claude_credential_in_background,
        )

        if claude_token_is_stale(credentials, now=observed_at):
            renew_claude_credential_in_background(
                credentials, home=Path(home), now=observed_at
            )
    except Exception:
        pass
    access_token = _credential(credentials, "claude", "oauth-token")
    if access_token is None:
        return _with_local_usage(
            _failure(
                "claude",
                observed_at=observed_at,
                state=ProviderSourceState.NEEDS_CONSENT,
                reason="usage_connection_required",
                action="Connect Claude usage",
            ),
            local,
        )
    try:
        windows = quota_fetcher(access_token)
    except Exception as error:
        reason = str(error)
        if "unauthorized" in reason or "needs_sign_in" in reason:
            state = ProviderSourceState.NEEDS_SIGN_IN
            reason_code = "authentication_required"
            action = "Reconnect Claude"
        elif "rate_limit" in reason:
            state = ProviderSourceState.RATE_LIMITED
            reason_code = "rate_limited"
            action = "Retry later"
        else:
            state = ProviderSourceState.UNAVAILABLE
            reason_code = "network_unavailable"
            action = "Retry"
        return _with_local_usage(
            _failure(
                "claude",
                observed_at=observed_at,
                state=state,
                reason=reason_code,
                action=action,
            ),
            local,
        )
    if not isinstance(windows, list):
        return _with_local_usage(
            _failure(
                "claude",
                observed_at=observed_at,
                state=ProviderSourceState.ERROR,
                reason="invalid_provider_response",
                action="Retry",
            ),
            local,
        )
    values = local or {}
    try:
        return parse_claude_usage(
            windows=windows,
            observed_at=observed_at,
            input_tokens=max(0, int(values.get("input_tokens", 0))),
            cached_input_tokens=max(0, int(values.get("cached_input_tokens", 0))),
            output_tokens=max(0, int(values.get("output_tokens", 0))),
            model_count=max(0, int(values.get("model_count", 0))),
            estimated_cost_usd=values.get("estimated_cost_usd"),
            cache_savings_usd=values.get("cache_savings_usd"),
        )
    except (TypeError, ValueError):
        return _failure(
            "claude",
            observed_at=observed_at,
            state=ProviderSourceState.ERROR,
            reason="invalid_provider_response",
            action="Retry",
        )


__all__ = ["collect_claude", "collect_codex"]
