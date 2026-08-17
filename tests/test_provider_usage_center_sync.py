from __future__ import annotations

from sidepulse.provider_usage_center import project_usage_center
from sidepulse.provider_usage_runtime import ProviderUsageState
from sidepulse.provider_usage_sync import MergedProviderSync


def test_usage_center_uses_synced_totals_when_available():
    merged = MergedProviderSync(
        quota_snapshots=(),
        machine_usage=(),
        total_input_tokens=100,
        total_cached_input_tokens=25,
        total_output_tokens=50,
        total_estimated_cost_usd=2.0,
        total_cache_savings_usd=0.5,
    )
    projection = project_usage_center(
        ProviderUsageState((), 1000, 1100, False),
        now=1000,
        merged_sync=merged,
    )
    assert projection.aggregate_metrics == (
        "175 tokens across synced Macs",
        "Estimated cost $2.00",
        "Cache savings $0.50",
    )
