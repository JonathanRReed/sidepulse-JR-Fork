from __future__ import annotations

from sidepulse.provider_usage_platform import (
    DEFAULT_PROVIDER_DESCRIPTORS,
    ProviderSourceState,
    ProviderUsageSnapshot,
    QuotaLane,
    QuotaUnit,
    TokenUsage,
    most_constrained_summary,
    retain_last_known_good,
)


def lane(
    provider: str,
    lane_id: str,
    label: str,
    remaining: float,
    *,
    reset_at: float | None = None,
    model: str | None = None,
) -> QuotaLane:
    return QuotaLane(
        provider_id=provider,
        lane_id=lane_id,
        label=label,
        remaining=remaining,
        used=100.0 - remaining,
        total=100.0,
        unit=QuotaUnit.PERCENT,
        reset_at=reset_at,
        source="fixture",
        model=model,
        bindable=model is None,
    )


def snapshot(
    provider: str,
    *,
    state: ProviderSourceState = ProviderSourceState.READY,
    lanes: tuple[QuotaLane, ...] = (),
    observed_at: float = 100.0,
    reason: str | None = None,
    action: str | None = None,
) -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        provider_id=provider,
        state=state,
        observed_at=observed_at,
        source_label="fixture",
        account_label=None,
        reason_code=reason,
        action=action,
        lanes=lanes,
        token_usage=None,
        credits=None,
        incident=None,
    )


def test_registry_contains_every_first_party_provider_once() -> None:
    assert tuple(row.provider_id for row in DEFAULT_PROVIDER_DESCRIPTORS) == (
        "codex",
        "claude",
        "cursor",
        "devin",
        "grok",
        "antigravity",
        "openai-api",
    )
    assert all(row.source_order for row in DEFAULT_PROVIDER_DESCRIPTORS)
    assert len({row.provider_id for row in DEFAULT_PROVIDER_DESCRIPTORS}) == 7


def test_nonready_states_require_an_action_and_reason() -> None:
    try:
        snapshot("claude", state=ProviderSourceState.NEEDS_CONSENT)
    except ValueError as exc:
        assert "action" in str(exc)
    else:
        raise AssertionError("nonready provider state accepted without an action")


def test_dynamic_scoped_lanes_are_preserved() -> None:
    rows = (
        lane("codex", "spark-weekly", "Spark Weekly", 28.0, model="spark"),
        lane("claude", "fable-weekly", "Fable Weekly", 11.0, model="fable"),
    )
    assert [row.label for row in rows] == ["Spark Weekly", "Fable Weekly"]
    assert rows[0].model == "spark"
    assert rows[1].model == "fable"
    assert all(row.bindable is False for row in rows)


def test_most_constrained_summary_uses_trustworthy_ready_lanes() -> None:
    summary = most_constrained_summary(
        (
            snapshot("codex", lanes=(lane("codex", "weekly", "Weekly", 71.0),)),
            snapshot("claude", lanes=(lane("claude", "fable", "Fable", 22.0),)),
            snapshot(
                "cursor",
                state=ProviderSourceState.NEEDS_CONSENT,
                reason="cursor_needs_consent",
                action="Connect Cursor",
            ),
        )
    )
    assert summary == "Claude Fable 22%"


def test_last_known_good_becomes_stale_instead_of_disappearing() -> None:
    previous = snapshot(
        "codex",
        lanes=(lane("codex", "weekly", "Weekly", 55.0),),
        observed_at=100.0,
    )
    failed = snapshot(
        "codex",
        state=ProviderSourceState.FAILED,
        observed_at=150.0,
        reason="network_failed",
        action="Retry Codex usage",
    )
    merged = retain_last_known_good(previous, failed)
    assert merged.state is ProviderSourceState.STALE
    assert merged.lanes == previous.lanes
    assert merged.reason_code == "network_failed"
    assert merged.action == "Retry Codex usage"
    assert merged.observed_at == previous.observed_at


def test_token_usage_tracks_models_and_estimate_coverage() -> None:
    usage = TokenUsage(
        input_tokens=100,
        cached_input_tokens=50,
        cache_creation_tokens=25,
        output_tokens=20,
        models=("claude-sonnet", "claude-opus"),
        estimated_cost_usd=1.25,
        estimated_cache_savings_usd=0.40,
        pricing_coverage=0.75,
        pricing_table_version="fixture-v1",
        pricing_as_of="2026-08-16",
    )
    assert usage.total_tokens == 195
    assert usage.model_count == 2
