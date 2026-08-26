from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.capacity_types import (
    CapacityUnit,
    CapacityValue,
    ObservationState,
    QuotaEffect,
    QuotaLaneKey,
    ResetState,
    SourceKey,
)
from sidepulse.provider_contracts import CapabilityIdentifier
from sidepulse.provider_facts import (
    EventToken,
    ProviderQuotaWindow,
    ProviderWatermark,
    SourceHealth,
    WatermarkBasis,
)
from sidepulse.providers import (
    NegotiatedProviderSource,
    negotiated_provider_sources,
    sources_with_capability,
)
from sidepulse.refresh_policy import ProviderRefreshState, plan_menu_open_refresh
from sidepulse.reset_policy import ResetBoundaryPlan, plan_reset_boundary_refresh
from sidepulse.usage_stats import (
    CACHE_VERSION,
    PricingCoverage,
    ProviderUsageResult,
    UsageSourceStatus,
    scan_provider_usage,
    scan_usage,
    usage_summary_line,
)
from sidepulse.usage_view import UsageWindowViewModel


def _source(provider: str, capability: str) -> NegotiatedProviderSource:
    matches = tuple(
        row
        for row in sources_with_capability(
            negotiated_provider_sources(),
            CapabilityIdentifier(capability),
        )
        if row.source_key.provider_id == provider
    )
    assert len(matches) == 1
    return matches[0]


def _write_rows(path: Path, *rows: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def _claude_row(
    message_id: str,
    *,
    model: str,
    input_tokens: int,
    cached_input_tokens: int = 0,
) -> dict[str, object]:
    return {
        "type": "assistant",
        "timestamp": "2026-08-12T12:00:00Z",
        "message": {
            "id": message_id,
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cached_input_tokens,
                "cache_creation_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    }


def _codex_row(*, input_tokens: int = 11) -> dict[str, object]:
    return {
        "type": "event_msg",
        "timestamp": "2026-08-12T12:00:00Z",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 0,
                }
            },
        },
    }


def test_provider_usage_result_is_immutable_source_scoped_and_privacy_safe(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private-client-root"
    cache = tmp_path / "state" / "usage.json"
    raw_message_id = "message-private-account"
    raw_model = "private-experimental-model"
    transcript = _write_rows(
        root / "private-session-title.jsonl",
        _claude_row(
            raw_message_id,
            model=raw_model,
            input_tokens=23,
        ),
    )

    result = scan_provider_usage(
        _source("claude", "transcript_usage"),
        root,
        cache,
        since_epoch=0.0,
    )

    assert result.source_key == _source("claude", "transcript_usage").source_key
    assert result.coverage.status is UsageSourceStatus.OK
    assert result.observed_session_count == 1
    assert result.input_tokens == 23
    assert result.pricing_coverage is PricingCoverage.UNAVAILABLE
    assert result.priced_record_count == 0
    assert result.unpriced_record_count == 1
    assert result.covered_cost_estimate_usd is None
    assert result.covered_cache_savings_estimate_usd is None
    assert result.pricing_as_of is None
    with pytest.raises((AttributeError, TypeError)):
        result.input_tokens = 0  # type: ignore[misc]
    rendered = repr(result) + cache.read_text()
    for private_value in (str(root), str(transcript), raw_message_id, raw_model):
        assert private_value not in rendered


def test_provider_pricing_reports_covered_only_partial_and_complete_estimates(
    tmp_path: Path,
) -> None:
    source = _source("claude", "transcript_usage")
    mixed_root = tmp_path / "mixed"
    _write_rows(
        mixed_root / "usage.jsonl",
        _claude_row("known", model="claude-sonnet-5", input_tokens=1_000_000),
        _claude_row("unknown", model="future-model", input_tokens=2_000_000),
    )

    mixed = scan_provider_usage(source, mixed_root, None, since_epoch=0.0)

    assert mixed.pricing_coverage is PricingCoverage.PARTIAL
    assert mixed.priced_record_count == 1
    assert mixed.unpriced_record_count == 1
    assert mixed.covered_cost_estimate_usd == pytest.approx(3.0)
    assert mixed.covered_cache_savings_estimate_usd == pytest.approx(0.0)
    assert mixed.pricing_as_of == "2026-08-26"  # rates-v2: fable + GPT rows

    totals = scan_usage(mixed_root)
    copy = usage_summary_line(totals, "cost")
    assert copy is not None
    assert "Estimate, known models only" in copy

    known_root = tmp_path / "known"
    _write_rows(
        known_root / "usage.jsonl",
        _claude_row(
            "known-only",
            model="claude-sonnet-5",
            input_tokens=1_000_000,
            cached_input_tokens=1_000_000,
        ),
    )
    known = scan_provider_usage(source, known_root, None, since_epoch=0.0)

    assert known.pricing_coverage is PricingCoverage.COMPLETE
    assert known.covered_cost_estimate_usd == pytest.approx(3.3)
    assert known.covered_cache_savings_estimate_usd == pytest.approx(2.7)
    assert known.pricing_as_of == "2026-08-26"


def test_provider_local_failure_cache_and_health_never_cross_source_keys(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    claude_root.mkdir()
    _write_rows(codex_root / "rollout.jsonl", _codex_row(input_tokens=31))
    shared_cache = tmp_path / "state" / "shared-cache.json"
    real_scandir = os.scandir

    def refuse_claude(path):
        if Path(path) == claude_root:
            raise PermissionError("private failure detail")
        return real_scandir(path)

    with patch("sidepulse.usage_stats.os.scandir", side_effect=refuse_claude):
        claude = scan_provider_usage(
            _source("claude", "transcript_usage"),
            claude_root,
            shared_cache,
            since_epoch=0.0,
        )
    codex = scan_provider_usage(
        _source("codex", "transcript_usage"),
        codex_root,
        shared_cache,
        since_epoch=0.0,
    )

    assert claude.coverage.status is UsageSourceStatus.FAILED
    assert codex.coverage.status is UsageSourceStatus.OK
    assert codex.input_tokens == 31
    assert codex.coverage.cache_hits == 0
    persisted = json.loads(shared_cache.read_text())
    assert persisted["version"] == CACHE_VERSION
    assert persisted["source_key"] == {
        "provider_id": "codex",
        "adapter_id": "transcripts",
        "source_instance_id": "local",
        "capability_id": "transcript_usage",
    }


def test_old_or_other_source_cache_forces_one_cold_scan_and_warm_scan_writes_nothing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    cache = tmp_path / "state" / "usage.json"
    _write_rows(
        root / "usage.jsonl",
        _claude_row("message", model="claude-sonnet-5", input_tokens=17),
    )
    cache.parent.mkdir()
    cache.write_text(json.dumps({"version": CACHE_VERSION - 1, "files": {}}))
    source = _source("claude", "transcript_usage")

    cold = scan_provider_usage(source, root, cache, since_epoch=0.0)
    assert cold.coverage.files_read == 1
    assert cold.coverage.cache_hits == 0

    with patch("sidepulse.usage_stats.atomic_private_write") as write:
        warm = scan_provider_usage(source, root, cache, since_epoch=0.0)

    assert warm.input_tokens == cold.input_tokens
    assert warm.coverage.files_read == 0
    assert warm.coverage.cache_hits == 1
    write.assert_not_called()


def test_compatibility_aggregator_combines_independent_provider_results(
    tmp_path: Path,
) -> None:
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    _write_rows(
        claude_root / "usage.jsonl",
        _claude_row("claude", model="claude-sonnet-5", input_tokens=13),
    )
    _write_rows(codex_root / "rollout.jsonl", _codex_row(input_tokens=19))

    totals = scan_usage(claude_root, codex_root=codex_root)

    assert totals.input_tokens == 13
    assert totals.codex_tokens == 19
    assert totals.source_coverage["claude"].status is UsageSourceStatus.OK
    assert totals.source_coverage["codex"].status is UsageSourceStatus.OK


def _lane(
    source: SourceKey,
    *,
    scope: str,
    pool: str,
    model: str | None,
    window: str,
    effect: QuotaEffect,
) -> QuotaLaneKey:
    return QuotaLaneKey(source, scope, pool, model, window, effect)


def _window(lane_key: QuotaLaneKey, *, reset_epoch: float) -> UsageWindowViewModel:
    return UsageWindowViewModel(
        lane_key=lane_key,
        provider_title=lane_key.source.provider_id.title(),
        label="Product-owned limit",
        window_minutes=300,
        capacity=CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            40.0,
            ObservationState.OBSERVED,
        ),
        reset_at=reset_epoch,
        reset_epoch=reset_epoch,
        reset_state=ResetState.FUTURE,
    )


def test_reset_boundary_plan_keeps_exact_sources_and_lanes_without_delimiter_identity(
) -> None:
    desktop = SourceKey("codex", "quota", "desktop", "remote_quota_windows")
    laptop = SourceKey("codex", "quota", "laptop", "remote_quota_windows")
    global_lane = _lane(
        desktop,
        scope="scope:all",
        pool="shared",
        model=None,
        window="five-hour",
        effect=QuotaEffect.ALL_WORKLOADS,
    )
    model_lane = _lane(
        desktop,
        scope="scope:model",
        pool="shared",
        model="model:opus",
        window="weekly",
        effect=QuotaEffect.MODEL,
    )
    sibling_lane = _lane(
        laptop,
        scope="scope:all",
        pool="shared",
        model=None,
        window="five-hour",
        effect=QuotaEffect.ALL_WORKLOADS,
    )

    plan = plan_reset_boundary_refresh(
        (
            _window(global_lane, reset_epoch=1_040.0),
            _window(model_lane, reset_epoch=1_100.0),
            _window(sibling_lane, reset_epoch=1_040.0),
        ),
        now=1_000.0,
        normal_refresh_deadline=1_500.0,
    )

    assert plan == ResetBoundaryPlan(
        deadline=1_042.0,
        source_keys=(desktop, laptop),
        lane_keys=(global_lane, sibling_lane),
    )
    assert plan.provider_ids == ("codex",)
    assert len(plan.boundary_keys) == 2
    assert len(set(plan.boundary_keys)) == 2

    next_plan = plan_reset_boundary_refresh(
        (
            _window(global_lane, reset_epoch=1_040.0),
            _window(model_lane, reset_epoch=1_100.0),
            _window(sibling_lane, reset_epoch=1_040.0),
        ),
        now=1_000.0,
        normal_refresh_deadline=1_500.0,
        attempted_lane_keys=frozenset(plan.lane_keys),
    )
    assert next_plan.lane_keys == (model_lane,)


def _watermark(source_key: SourceKey, sequence: int) -> ProviderWatermark:
    return ProviderWatermark(
        source_key=source_key,
        basis=WatermarkBasis.PROVIDER_SEQUENCE,
        occurred_at_epoch=1_000.0 + sequence,
        event_token=EventToken(f"event:{sequence}"),
        sequence=sequence,
        tie_break_rank=1,
    )


def test_older_quota_watermark_cannot_replace_a_newer_reset_boundary() -> None:
    from sidepulse.reset_policy import accept_newer_quota_windows

    source_key = SourceKey("codex", "quota", "local", "remote_quota_windows")
    lane_key = QuotaLaneKey(
        source_key,
        "scope:source-only",
        "shared",
        None,
        "five-hour",
        QuotaEffect.ALL_WORKLOADS,
    )
    current = ProviderQuotaWindow(
        lane_key,
        30.0,
        300,
        2_000.0,
        _watermark(source_key, 2),
        SourceHealth.HEALTHY,
        False,
    )
    older = ProviderQuotaWindow(
        lane_key,
        20.0,
        300,
        1_900.0,
        _watermark(source_key, 1),
        SourceHealth.HEALTHY,
        False,
    )

    assert accept_newer_quota_windows((current,), (older,)) == (current,)


def test_static_capability_composition_runs_enabled_exact_sources_independently(
    tmp_path: Path,
) -> None:
    """Two capabilities, two providers, one plan -- and no shared scan state.

    This used to drive `provider_runtime.ProviderRuntime`, which nothing in
    the app ever constructed: the live refresh is
    `capacity_refresh.CapacityRefreshCoordinator` plus the status bar's own
    workers. Driving a second runtime proved that runtime worked, not that
    the shipped composition does, so the invocation plan and the per-source
    scans are asserted directly.
    """
    transcript_rows = sources_with_capability(
        negotiated_provider_sources(), CapabilityIdentifier("transcript_usage")
    )
    quota_rows = sources_with_capability(
        negotiated_provider_sources(), CapabilityIdentifier("remote_quota_windows")
    )
    rows = (*transcript_rows, *quota_rows)
    states = tuple(
        ProviderRefreshState(
            row.source_key,
            enabled=True,
            visible=True,
        )
        for row in rows
    )
    plan = plan_menu_open_refresh(states, now=1_000.0, low_power=False)
    expected_keys = tuple(row.source_key for row in rows)
    assert plan.invocations == expected_keys

    roots = {"codex": tmp_path / "codex", "claude": tmp_path / "claude"}
    _write_rows(roots["codex"] / "rollout.jsonl", _codex_row(input_tokens=29))
    _write_rows(
        roots["claude"] / "usage.jsonl",
        _claude_row("message", model="claude-sonnet-5", input_tokens=37),
    )
    usages: dict[SourceKey, ProviderUsageResult] = {}
    for row in transcript_rows:
        usages[row.source_key] = scan_provider_usage(
            row,
            roots[row.source_key.provider_id],
            None,
            since_epoch=0.0,
        )

    codex_key = next(
        row.source_key for row in transcript_rows if row.source_key.provider_id == "codex"
    )
    claude_key = next(
        row.source_key for row in transcript_rows if row.source_key.provider_id == "claude"
    )
    # The independence claim: each scan sees only its own provider's root.
    assert usages[codex_key].input_tokens == 29
    assert usages[claude_key].input_tokens == 37
    assert usages[codex_key].source_key == codex_key
    assert usages[claude_key].source_key == claude_key
    assert all(result.observed_session_count == 1 for result in usages.values())
