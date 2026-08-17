from __future__ import annotations

from pathlib import Path

from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    QuotaLane,
    QuotaUnit,
    TokenUsage,
)
from sidepulse.provider_usage_store import ProviderUsageStore


def sample() -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        provider_id="claude",
        state=ProviderSourceState.READY,
        observed_at=100.0,
        source_label="Claude OAuth",
        account_label="max",
        reason_code=None,
        action=None,
        lanes=(
            QuotaLane(
                provider_id="claude",
                lane_id="fable-weekly",
                label="Fable Weekly",
                remaining=12.0,
                used=88.0,
                total=100.0,
                unit=QuotaUnit.PERCENT,
                reset_at=200.0,
                source="claude-oauth",
                model="fable",
                bindable=False,
            ),
        ),
        token_usage=TokenUsage(
            input_tokens=100,
            cached_input_tokens=20,
            cache_creation_tokens=10,
            output_tokens=30,
            models=("claude-fable",),
            estimated_cost_usd=1.25,
            estimated_cache_savings_usd=0.20,
            pricing_coverage=1.0,
            pricing_table_version="test-v1",
            pricing_as_of="2026-08-16",
        ),
        credits=5.0,
        incident=None,
    )


def test_store_round_trip_preserves_dynamic_lanes_and_usage(tmp_path: Path) -> None:
    store = ProviderUsageStore(tmp_path / "provider-usage.json")
    store.save((sample(),))

    restored = store.load()
    assert restored == (sample(),)
    text = store.path.read_text(encoding="utf-8")
    assert "access_token" not in text
    assert "refresh_token" not in text
    assert "Bearer" not in text


def test_corrupt_store_degrades_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "provider-usage.json"
    path.write_text("{broken", encoding="utf-8")
    assert ProviderUsageStore(path).load() == ()


def test_store_replaces_each_provider_with_latest_snapshot(tmp_path: Path) -> None:
    store = ProviderUsageStore(tmp_path / "provider-usage.json")
    older = sample()
    newer = ProviderUsageSnapshot(
        provider_id="claude",
        state=ProviderSourceState.PARTIAL,
        observed_at=200.0,
        source_label="Claude local transcripts",
        account_label="max",
        reason_code="permission_required",
        action="Connect Claude usage",
        lanes=older.lanes,
        token_usage=older.token_usage,
        credits=older.credits,
        incident=None,
    )
    store.save((older, newer))
    assert store.load() == (newer,)
