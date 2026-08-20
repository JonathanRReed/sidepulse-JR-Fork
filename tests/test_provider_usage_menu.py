from __future__ import annotations

from sidepulse.provider_usage_menu import project_usage_menu
from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.provider_usage_runtime import ProviderUsageState


def snapshot(provider, label, remaining, *, state=ProviderSourceState.READY, action=None):
    lanes = ()
    reason = None
    if remaining is not None:
        lanes = (
            UsageLane(
                provider_id=provider,
                lane_id="weekly",
                label=label,
                remaining_percent=remaining,
                reset_at=3000,
                scope="all",
                model=None,
                feature=None,
                bindable=True,
                source_id="fixture",
            ),
        )
    if state not in {ProviderSourceState.READY, ProviderSourceState.DISABLED}:
        reason = "fixture_reason"
    return ProviderUsageSnapshot(
        provider_id=provider,
        account_label=None,
        observed_at=1000,
        state=state,
        reason_code=reason,
        action_label=action,
        lanes=lanes,
        input_tokens=100,
        cached_input_tokens=25,
        output_tokens=50,
        model_count=2,
        estimated_cost_usd=1.25,
        cache_savings_usd=0.25,
        credits_remaining=None,
        incident=None,
    )


def test_summary_shows_two_tightest_trustworthy_providers():
    state = ProviderUsageState(
        (
            snapshot("codex", "Weekly", 71),
            snapshot("claude", "Fable Weekly", 36),
            snapshot("grok", "Weekly", 90),
        ),
        1000,
        1100,
        False,
    )
    projection = project_usage_menu(state, now=1000)
    assert projection.title == "Usage · Claude 36% · Codex 71%"
    assert projection.rows[0].title.startswith("Codex")
    assert projection.rows[1].title.startswith("Claude")


def test_actionable_missing_source_is_named_instead_of_no_reading():
    state = ProviderUsageState(
        (
            snapshot(
                "claude",
                "Weekly",
                None,
                state=ProviderSourceState.NEEDS_CONSENT,
                action="Connect Claude usage",
            ),
        ),
        1000,
        1100,
        False,
    )
    projection = project_usage_menu(state, now=1000)
    assert projection.title == "Usage · setup needed"
    assert projection.rows[0].title == "Claude · permission required"
    assert projection.rows[0].action_label == "Connect Claude usage"
    assert "no reading" not in repr(projection).lower()


def test_detail_row_includes_reset_tokens_models_and_estimate():
    state = ProviderUsageState(
        (snapshot("claude", "Weekly", 36),),
        1000,
        1100,
        False,
    )
    row = project_usage_menu(state, now=1000).rows[0]
    assert row.detail == "Weekly 36% left · resets in 33m"
    assert row.usage_detail == "175 tokens · 2 models · est. $1.25"


def test_refreshing_state_has_stable_title():
    state = ProviderUsageState((), None, None, True)
    projection = project_usage_menu(state, now=1000)
    assert projection.title == "Usage · refreshing…"


def test_lane_lines_render_codebar_style_meters():
    row = project_usage_menu(
        ProviderUsageState((snapshot("claude", "5-hour", 74),), 1000, 1100, False),
        now=1000,
    ).rows[0]
    assert row.lane_lines == ("▰▰▰▰▰▰▱▱  5-hour · 74% left · resets in 33m",)


def test_lane_meter_never_shows_empty_while_something_remains():
    from sidepulse.provider_usage_qol import format_lane_meter as _lane_meter

    assert _lane_meter(100.0) == "▰▰▰▰▰▰▰▰"
    assert _lane_meter(50.0) == "▰▰▰▰▱▱▱▱"
    assert _lane_meter(2.0) == "▰▱▱▱▱▱▱▱"  # almost-out still shows one cell
    assert _lane_meter(0.0) == "▱▱▱▱▱▱▱▱"


def test_lane_without_percent_still_lists_its_reset():
    # The 2026-08-20 live failure shape: the OAuth fetch succeeded and
    # lanes existed with real reset times, but every remaining_percent
    # was None (parse_claude_usage read "used_percent" while
    # claude_quota emits "utilization") -- the menu said only "ready".
    # Even in that degraded shape the lanes must say SOMETHING.
    row = project_usage_menu(
        ProviderUsageState(
            (snapshot("claude", "Weekly", None, state=ProviderSourceState.READY),),
            1000,
            1100,
            False,
        ),
        now=1000,
    ).rows[0]
    assert row.lane_lines == ()  # snapshot() builds no lanes when remaining is None
    assert row.title == "Claude · ready"
