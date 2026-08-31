from __future__ import annotations

from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
    most_constrained_lane,
    normalize_dynamic_lane,
    provider_descriptors,
    provider_status_line,
    select_authoritative_snapshot,
)


def _lane(
    *,
    provider: str = "claude",
    lane_id: str = "weekly",
    label: str = "Weekly",
    remaining: float = 40.0,
    reset: float = 2000.0,
    bindable: bool = True,
) -> UsageLane:
    return UsageLane(
        provider_id=provider,
        lane_id=lane_id,
        label=label,
        remaining_percent=remaining,
        reset_at=reset,
        scope="all",
        model=None,
        feature=None,
        bindable=bindable,
        source_id="official",
    )


def _snapshot(
    *,
    provider: str = "claude",
    state: ProviderSourceState = ProviderSourceState.READY,
    lanes: tuple[UsageLane, ...] = (),
    reason: str | None = None,
    action: str | None = None,
    observed: float = 1000.0,
    source_instance_id: str = "default",
) -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        provider_id=provider,
        account_label=None,
        observed_at=observed,
        state=state,
        reason_code=reason,
        action_label=action,
        lanes=lanes,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
        source_instance_id=source_instance_id,
    )


def test_snapshot_preserves_source_instance_identity() -> None:
    result = _snapshot(source_instance_id="personal")
    assert result.source_instance_id == "personal"


def test_registry_has_all_native_providers_and_no_codexbar() -> None:
    ids = tuple(descriptor.provider_id for descriptor in provider_descriptors())
    assert ids == (
        "codex",
        "claude",
        "cursor",
        "devin",
        "grok",
        "antigravity",
        "openai-api",
    )
    assert "codexbar" not in ids


def test_registry_declares_ordered_source_ladders() -> None:
    by_id = {descriptor.provider_id: descriptor for descriptor in provider_descriptors()}
    assert by_id["codex"].source_order[:2] == ("codex-auth", "codex-rollouts")
    assert by_id["claude"].source_order[:2] == ("claude-keychain", "claude-oauth")
    assert by_id["cursor"].supports_browser_sources is True
    assert by_id["devin"].supports_browser_sources is True


def test_dynamic_provider_lane_is_preserved_but_not_bindable() -> None:
    result = normalize_dynamic_lane(
        provider_id="claude",
        lane_id="fable-weekly",
        label="Fable Weekly",
        remaining_percent=18,
        reset_at=5000,
        source_id="claude-oauth",
        known_lane_ids={"five-hour", "weekly"},
    )
    assert result.label == "Fable Weekly"
    assert result.bindable is False
    assert result.model == "fable"


def test_actionable_failure_requires_action() -> None:
    try:
        _snapshot(
            state=ProviderSourceState.NEEDS_CONSENT,
            reason="browser_consent_required",
            action=None,
        )
    except ValueError as exc:
        assert "action" in str(exc)
    else:
        raise AssertionError("permission-required snapshot accepted without an action")


def test_first_ready_source_wins() -> None:
    missing = _snapshot(
        state=ProviderSourceState.SOURCE_NOT_FOUND,
        reason="missing",
        action="Sign in",
    )
    ready = _snapshot(lanes=(_lane(),), observed=1100)
    later = _snapshot(lanes=(_lane(remaining=80),), observed=1200)

    merged = select_authoritative_snapshot((missing, ready, later))
    assert merged is ready


def test_last_known_good_is_retained_as_stale_when_sources_fail() -> None:
    previous = _snapshot(lanes=(_lane(remaining=21),), observed=900)
    failure = _snapshot(
        state=ProviderSourceState.UNAVAILABLE,
        reason="network",
        action="Retry",
        observed=1000,
    )
    merged = select_authoritative_snapshot((failure,), last_known_good=previous)
    assert merged.state is ProviderSourceState.STALE
    assert merged.lanes == previous.lanes
    assert merged.reason_code == "network"


def test_most_constrained_lane_ignores_detail_only_unknown_lanes() -> None:
    known = _lane(lane_id="weekly", remaining=25, bindable=True)
    detail = _lane(lane_id="fable", remaining=5, bindable=False)
    result = most_constrained_lane(_snapshot(lanes=(known, detail)))
    assert result is known


def test_source_failure_summary_is_specific() -> None:
    result = _snapshot(
        provider="cursor",
        state=ProviderSourceState.NEEDS_CONSENT,
        reason="browser_consent_required",
        action="Enable Cursor browser access",
    )
    assert provider_status_line(result) == "Cursor · permission required"
