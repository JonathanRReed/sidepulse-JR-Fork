from __future__ import annotations

from sidepulse.provider_feature_settings import (
    ProviderInstanceVisualPolicy,
    ProviderInstanceVisualProjection,
)
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
    assert section.lanes[0].title == "▰▰▱▱▱▱▱▱  Fable Weekly · 22% left"
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


def test_usage_center_renders_distinct_same_provider_instance_labels():
    def make(account, instance, remaining):
        return ProviderUsageSnapshot(
            provider_id="claude",
            account_label=account,
            observed_at=1000,
            state=ProviderSourceState.READY,
            reason_code=None,
            action_label=None,
            lanes=(UsageLane("claude", "weekly", "Weekly", remaining, 3000, "all", None, None, True, "official"),),
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            model_count=0,
            estimated_cost_usd=None,
            cache_savings_usd=None,
            credits_remaining=None,
            incident=None,
            source_instance_id=instance,
        )

    center = project_usage_center(
        ProviderUsageState(
            (make("personal@example.invalid", "personal", 36), make("work@example.invalid", "work", 72)),
            1000,
            1100,
            False,
        ),
        now=1000,
    )
    assert len(center.sections) == 2
    assert {section.source_instance_id for section in center.sections} == {"personal", "work"}
    assert {section.account for section in center.sections} == {
        "personal@example.invalid",
        "work@example.invalid",
    }
    assert {
        section.lanes[0].source_instance_id for section in center.sections
    } == {"personal", "work"}


def test_usage_center_uses_exact_profile_label_and_color_override():
    snapshot = ProviderUsageSnapshot(
        provider_id="claude",
        account_label="work@example.invalid",
        observed_at=1000,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=(
            UsageLane(
                "claude",
                "weekly",
                "Weekly",
                72,
                3000,
                "all",
                None,
                None,
                True,
                "official",
            ),
        ),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
        source_instance_id="work",
    )
    visual = ProviderInstanceVisualProjection(
        (
            ProviderInstanceVisualPolicy(
                provider_id="claude",
                source_instance_id="work",
                label="Client Claude",
                color_override="#112233",
            ),
        )
    )

    section = project_usage_center(
        ProviderUsageState((snapshot,), 1000, 1100, False),
        now=1000,
        visual=visual,
    ).sections[0]

    assert section.title == "Client Claude"
    assert section.color_override == "#112233"


def test_usage_center_falls_back_when_exact_profile_is_missing():
    snapshot = ProviderUsageSnapshot(
        provider_id="claude",
        account_label=None,
        observed_at=1000,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=(),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
        source_instance_id="personal",
    )
    visual = ProviderInstanceVisualProjection(
        (
            ProviderInstanceVisualPolicy(
                provider_id="claude",
                source_instance_id="work",
                label="Client Claude",
                color_override="#112233",
            ),
        )
    )

    section = project_usage_center(
        ProviderUsageState((snapshot,), 1000, 1100, False),
        now=1000,
        visual=visual,
    ).sections[0]

    assert section.title.startswith("Claude #")
    assert "personal" not in repr(section)
    assert section.color_override is None


def test_usage_center_never_exposes_opaque_account_or_source_identity():
    raw_account = "org-7535461b-2b9a-4371-b335-3928397be5cd"
    raw_source = "profile:work:8f14e45fceea167a5a36dedd4bea2543"
    source = ProviderUsageSnapshot(
        provider_id="codex",
        account_label=raw_account,
        observed_at=1000,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=(),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
        source_instance_id=raw_source,
    )

    section = project_usage_center(
        ProviderUsageState((source,), 1000, 1100, False),
        now=1000,
    ).sections[0]

    assert section.title.startswith("Codex #")
    assert section.account is None
    assert raw_account not in repr(section)
    assert raw_source not in repr(section)


def test_usage_center_privacy_mode_suppresses_email_and_alias():
    source = ProviderUsageSnapshot(
        provider_id="claude",
        account_label="person@example.com",
        observed_at=1000,
        state=ProviderSourceState.READY,
        reason_code=None,
        action_label=None,
        lanes=(),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
        source_instance_id="work",
    )
    visual = ProviderInstanceVisualProjection(
        (
            ProviderInstanceVisualPolicy("claude", "work", "Client Claude", None),
        )
    )

    section = project_usage_center(
        ProviderUsageState((source,), 1000, 1100, False),
        now=1000,
        visual=visual,
        privacy_mode=True,
    ).sections[0]

    assert section.title == "Claude"
    assert section.account is None
    assert "person@example.com" not in repr(section)
    assert "Client Claude" not in repr(section)
