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
    assert projection.title == "Usage · ▰▰▰▱▱▱▱▱  Claude 36% · Codex 71% · Grok 90%"
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
    assert row.lane_lines == ("▰▰▰▰▰▰▱▱  5-hour · 74% left · resets in 33m · surplus",)


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


def test_display_flags_curate_meters_totals_cost_and_detail_lanes():
    from sidepulse.provider_usage_settings import MenuUsageDisplay

    state = ProviderUsageState((snapshot("claude", "5-hour", 74),), 1000, 1100, False)
    quiet = project_usage_menu(
        state,
        now=1000,
        display=MenuUsageDisplay(
            show_meters=False, show_totals=False, show_cost=False
        ),
    ).rows[0]
    assert quiet.lane_lines == ("5-hour · 74% left · resets in 33m · surplus",)
    assert quiet.usage_detail is None

    no_cost = project_usage_menu(
        state,
        now=1000,
        display=MenuUsageDisplay(show_cost=False),
    ).rows[0]
    assert no_cost.usage_detail == "175 tokens · 2 models"


def test_hidden_providers_leave_the_rows_and_the_title():
    state = ProviderUsageState(
        (snapshot("claude", "5-hour", 36), snapshot("codex", "Weekly", 71)),
        1000,
        1100,
        False,
    )
    projection = project_usage_menu(
        state, now=1000, hidden_providers=frozenset({"claude"})
    )
    assert [row.provider_id for row in projection.rows] == ["codex"]
    assert projection.title == "Usage · ▰▰▰▰▰▰▱▱  Codex 71%"


def test_lanes_past_their_threshold_are_flagged_for_alert_rendering():
    projection = project_usage_menu(
        ProviderUsageState(
            (snapshot("claude", "5-hour", 12), snapshot("codex", "Weekly", 80)),
            1000,
            1100,
            False,
        ),
        now=1000,
        thresholds={"claude": 20.0, "codex": 20.0},
    )
    by_provider = {row.provider_id: row for row in projection.rows}
    assert by_provider["claude"].alert_lane_indexes == (0,)
    assert by_provider["codex"].alert_lane_indexes == ()


def test_menu_bar_glance_prefers_running_providers_then_tightest():
    from sidepulse.provider_usage_menu import menu_bar_quota_glance

    state = ProviderUsageState(
        (snapshot("claude", "5-hour", 36), snapshot("codex", "Weekly", 71)),
        1000,
        1100,
        False,
    )
    # Nobody running: the tightest visible provider speaks.
    assert menu_bar_quota_glance(state, now=1000).text == "36%"
    assert (
        menu_bar_quota_glance(
            state, hidden_providers=frozenset({"claude"}), now=1000
        ).text
        == "71%"
    )
    # Codex is the one RUNNING: its runway owns the glance even though
    # Claude is tighter overall.
    assert (
        menu_bar_quota_glance(
            state, active_providers=frozenset({"codex"}), now=1000
        ).text
        == "71%"
    )
    # Both running: the lowest among the active ones wins.
    assert (
        menu_bar_quota_glance(
            state,
            active_providers=frozenset({"codex", "claude"}),
            now=1000,
        ).text
        == "36%"
    )
    # No numbers anywhere -> no glance, never "unknown%".
    empty = ProviderUsageState(
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
    assert menu_bar_quota_glance(empty, now=1000) is None


def test_pace_verdicts_cover_fast_surplus_on_pace_and_critical():
    from sidepulse.usage_pace import lane_pace, pace_phrase

    hour = 3600.0
    # Halfway through a 5-hour window (2.5h elapsed, 2.5h to reset).
    def pace(remaining):
        return lane_pace(
            remaining_percent=remaining,
            reset_at=1_000_000.0 + 2.5 * hour,
            lane_id="five-hour",
            now=1_000_000.0,
        )

    assert pace(55.0).verdict == "on_pace"  # used 45% at 50% elapsed
    assert pace(90.0).verdict == "surplus"  # used 10% at 50% elapsed
    # Used 60% at 50% elapsed: fast (ratio 1.2 < 1.25 is on_pace, so use 65)
    fast = pace(30.0)  # used 70% -> runs dry before reset
    assert fast.verdict == "critical"
    assert "runs out in ~" in pace_phrase(fast, now=1_000_000.0)
    assert pace(0.2).verdict == "out"
    # Unknown window duration -> no reading, never a guess.
    assert (
        lane_pace(
            remaining_percent=50.0,
            reset_at=1_000_000.0 + hour,
            lane_id="mystery-lane",
            now=1_000_000.0,
        )
        is None
    )


def test_lane_lines_carry_the_pace_tag():
    # snapshot() builds a weekly lane resetting at t=3000; at now=1000
    # the window is ~100% elapsed with 64% used -- ratio ~0.64, on pace.
    row = project_usage_menu(
        ProviderUsageState((snapshot("claude", "Weekly", 36),), 1000, 1100, False),
        now=1000,
    ).rows[0]
    assert row.lane_lines[0].endswith("· on pace")


def test_a_lane_alerts_once_per_reset_window_when_it_turns_critical():
    from sidepulse.usage_pace import critical_pace_transitions

    hour = 3600.0
    base = 1_000_000.0

    def snap(remaining):
        # Halfway through a 5-hour window; heavy use turns it critical.
        return snapshot_with_lane("codex", "5-hour", "five-hour", remaining,
                                  reset_at=base + 2.5 * hour)

    def snapshot_with_lane(provider, label, lane_id, remaining, *, reset_at):
        return ProviderUsageSnapshot(
            provider_id=provider,
            account_label=None,
            observed_at=base,
            state=ProviderSourceState.READY,
            reason_code=None,
            action_label=None,
            lanes=(
                UsageLane(
                    provider_id=provider,
                    lane_id=lane_id,
                    label=label,
                    remaining_percent=remaining,
                    reset_at=reset_at,
                    scope="all",
                    model=None,
                    feature=None,
                    bindable=True,
                    source_id="fixture",
                ),
            ),
            input_tokens=0, cached_input_tokens=0, output_tokens=0,
            model_count=0, estimated_cost_usd=None, cache_savings_usd=None,
            credits_remaining=None, incident=None,
        )

    healthy = (snap(55.0),)   # on pace
    critical = (snap(30.0),)  # projected dry before reset

    # Transition INTO critical: one alert.
    alerts = critical_pace_transitions(
        healthy, critical, now=base, seen_keys=frozenset()
    )
    assert len(alerts) == 1
    key, provider_id, label = alerts[0]
    assert provider_id == "codex" and label == "5-hour"

    # Same critical state again: not news.
    assert critical_pace_transitions(
        critical, critical, now=base, seen_keys=frozenset()
    ) == ()
    # And the seen-set silences even a fresh transition for that window.
    assert critical_pace_transitions(
        healthy, critical, now=base, seen_keys=frozenset({key})
    ) == ()


def test_one_heavy_evening_cannot_condemn_a_fresh_weekly_window():
    """'Why is it red for codex' (2026-08-21): ~5% into a 7-day window,
    one heavy session extrapolated to runs-dry-before-reset and painted
    93%-left RED. A critical verdict needs a baseline: before 15% of
    the window has elapsed the worst pace may say is 'spending fast'."""
    from sidepulse.usage_pace import lane_pace

    day = 86_400.0
    now = 1_000_000.0
    # 8 hours into a 7-day window, 7% already used -- burns dry in ~4
    # days at that rate, well before the reset 6.7 days away.
    early = lane_pace(
        remaining_percent=93.0,
        reset_at=now + 7 * day - 8 * 3600.0,
        lane_id="weekly",
        now=now,
    )
    assert early is not None and early.verdict == "fast"

    # The same projection PAST the baseline is honestly critical.
    seasoned = lane_pace(
        remaining_percent=50.0,
        reset_at=now + 4 * day,  # 3 of 7 days elapsed (43%)
        lane_id="weekly",
        now=now,
    )
    assert seasoned is not None and seasoned.verdict == "critical"


def _titles(*snapshots):

    state = ProviderUsageState(
        snapshots=tuple(snapshots),
        refreshed_at=1000,
        next_refresh_at=None,
        refreshing=False,
    )
    return [row.title for row in project_usage_menu(state, now=1000).rows]


def test_a_stale_reading_says_so_on_the_row_itself():
    """The title IS the glance -- "Codex · 48% left" is a claim about
    right now. Marking only the submenu detail hid staleness one level
    down: reported as "am i on the latest version its out of date for
    codex" against a figure three days old that looked current here."""
    stale = snapshot(
        "codex", "Weekly", 48, state=ProviderSourceState.STALE, action="Run Codex"
    )
    assert _titles(stale) == ["Codex · 48% left · stale"]


def test_a_broken_sign_in_says_reconnect_not_stale():
    """Both mean "last-known, not live", but the owner can act on
    reconnect and cannot act on stale."""
    import dataclasses

    expired = dataclasses.replace(
        snapshot(
            "claude", "Weekly", 71, state=ProviderSourceState.STALE, action="Reconnect"
        ),
        reason_code="authentication_required",
    )
    assert _titles(expired) == ["Claude · 71% left · reconnect"]


def test_a_live_reading_carries_no_marker():
    fresh = snapshot("devin", "Weekly", 100)
    assert _titles(fresh) == ["Devin · 100% left"]
