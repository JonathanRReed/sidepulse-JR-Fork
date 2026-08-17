from __future__ import annotations

from sidepulse.provider_usage_center import project_usage_center, usage_center_text
from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.provider_usage_runtime import ProviderUsageState


def test_usage_center_projects_dynamic_lanes_and_quality_of_life_fields():
    snapshot = ProviderUsageSnapshot(
        provider_id="claude",
        account_label="person@example.invalid",
        observed_at=1000,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=(
            UsageLane(
                provider_id="claude",
                lane_id="fable-weekly",
                label="Fable Weekly",
                remaining_percent=22,
                reset_at=4600,
                scope="all",
                model="fable",
                feature=None,
                bindable=False,
                source_id="claude-oauth",
            ),
        ),
        input_tokens=1000,
        cached_input_tokens=500,
        output_tokens=250,
        model_count=4,
        estimated_cost_usd=3.25,
        cache_savings_usd=1.10,
        credits_remaining=12,
        incident=None,
    )
    center = project_usage_center(
        ProviderUsageState((snapshot,), 1000, 1100, False),
        now=1000,
    )
    section = center.sections[0]
    assert section.title == "Claude"
    assert section.account == "person@example.invalid"
    assert section.lanes[0].title == "Fable Weekly · 22% left"
    assert section.lanes[0].subtitle == "resets in 1h 0m · detail only"
    assert section.metrics == (
        "1,750 tokens",
        "4 models",
        "Estimated cost $3.25",
        "Cache savings $1.10",
        "12 credits left",
    )


def test_usage_center_names_source_action_and_never_says_no_reading():
    snapshot = ProviderUsageSnapshot(
        provider_id="cursor",
        account_label=None,
        observed_at=1000,
        state=ProviderSourceState.NEEDS_CONSENT,
        reason_code="browser_consent_required",
        action_label="Enable Cursor browser access",
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
    center = project_usage_center(
        ProviderUsageState((snapshot,), 1000, 1100, False),
        now=1000,
    )
    assert center.sections[0].status == "Permission required"
    assert center.sections[0].action_label == "Enable Cursor browser access"
    assert "no reading" not in usage_center_text(center).lower()
