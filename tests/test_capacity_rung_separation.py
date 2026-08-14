"""One fact, one rung. What the Capacity card is and is not allowed to say.

The owner's dropdown read:

    Codex · 7d 100% left      resets in 6d 23h · updated 2m ago · partial · Local
    Claude · Claude, last 365 days: 2508 sessions

Three separate rung violations in two rows.

  * A CAPACITY row showed a TRANSCRIPT SESSION COUNT. Claude had no OAuth
    token, so no ceiling at all; the row fell through to the local aggregate,
    which is a different fact of a different kind from a different source, and
    printed it in the slot reserved for a plan limit.
  * The provider title was emitted twice, by two owners who did not know about
    each other -- the view, which owns the title, and the summary line, which
    named the provider again.
  * `partial` and `Local transcripts · N files` describe the transcript scan's
    coverage, not the quota source's. They appeared on the capacity row, and
    again on the row below it.

And on the Codex row, a fourth: `100% left` was not a reading. Codex writes
zero-used with the boundary a full window away when it has nothing to report,
and the boundary slides forward with the wall clock each time it writes one.
The status bar rendered that as a confident percentage -- the same class of
defect as the Spark allowance this codebase already documents.
"""

from __future__ import annotations

import time

import pytest

from sidepulse import usage_stats
from sidepulse.capacity_types import ObservationState
from sidepulse.provider_capacity import negotiate_provider_capacity_policies
from sidepulse.providers import negotiated_provider_sources
from sidepulse.usage_view import (
    LocalActivitySection,
    build_provider_usage_view,
)

status_bar = pytest.importorskip("sidepulse.status_bar")


_NOW = 1_800_000_000.0


def _model(**kwargs):
    defaults = {
        "provider_id": "claude",
        "provider_title": "Claude",
        "windows": (),
        "now": 500.0,
        "reset_now": _NOW,
        "last_success_at": 500.0,
    }
    defaults.update(kwargs)
    provider_id = defaults.pop("provider_id")
    provider_title = defaults.pop("provider_title")
    windows = defaults.pop("windows")
    return build_provider_usage_view(provider_id, provider_title, windows, **defaults)


def _lines(model):
    return status_bar.capacity_menu_lines(
        model,
        monotonic_now=500.0,
        epoch_now=_NOW,
    )


# --------------------------------------------------------------------------
# A capacity row shows a capacity fact, or says it has none.
# --------------------------------------------------------------------------


def test_a_provider_with_no_ceiling_says_so_instead_of_borrowing_one() -> None:
    """The owner's Claude row, exactly: local activity, no quota window."""
    model = _model(
        local_activity=LocalActivitySection(
            summary_text="Last 365 days: 2508 sessions · estimated $1.00",
            detail_text="1 in · 2 cached · 3 out",
            partial=True,
            source_text="Local transcripts · 2707 files · partial",
        ),
    )
    primary, secondary = _lines(model)

    assert primary == "Claude · no reading"
    assert "2508" not in primary
    assert "session" not in primary.lower()
    assert "365" not in primary
    # The local aggregate is not lost -- it is on its own rung, where the
    # Settings pane reads it.
    assert "2508 sessions" in model.settings_text


def test_the_transcript_scans_coverage_never_qualifies_a_plan_limit() -> None:
    """`partial` and the file count belong to the scan, not to the ceiling."""
    model = _model(
        provider_id="codex",
        provider_title="Codex",
        windows=(
            {"label": "5-hour", "used_percent": 38.0, "window_minutes": 300},
        ),
        local_activity=LocalActivitySection(
            partial=True,
            source_text="Local transcripts · 2895 files · partial",
        ),
    )
    primary, secondary = _lines(model)

    assert primary == "Codex · 5h 62% left"
    assert "partial" not in secondary
    assert "Local transcripts" not in secondary
    assert "2895" not in secondary
    assert "updated just now" in secondary
    assert model.partial is True
    assert "2895 files" in model.source_text


def test_no_fact_is_printed_on_both_rows_of_one_provider() -> None:
    """The card's two rows must not restate each other."""
    model = _model(
        provider_id="codex",
        provider_title="Codex",
        windows=(
            {
                "label": "5-hour",
                "used_percent": 38.0,
                "window_minutes": 300,
                "resets_at": _NOW + 3_600.0,
            },
        ),
        local_activity=LocalActivitySection(
            partial=True,
            source_text="Local transcripts · 12 files · partial",
        ),
    )
    primary, secondary = _lines(model)

    shared = {part.strip() for part in primary.split("·")} & {
        part.strip() for part in secondary.split("·")
    }
    assert shared == set()


def test_a_window_with_no_usable_percentage_says_no_reading() -> None:
    model = _model(
        provider_id="codex",
        provider_title="Codex",
        windows=(
            {
                "label": "5-hour",
                "used_percent": "unknown",
                "window_minutes": 300,
                "resets_at": _NOW + 120.0,
            },
        ),
    )
    primary, _secondary = _lines(model)

    assert primary == "Codex · 5h no reading"
    assert "%" not in primary


def test_extra_window_rows_follow_the_same_rule() -> None:
    model = _model(
        windows=(
            {"label": "5-hour", "used_percent": 10.0, "window_minutes": 300},
            {"label": "Weekly Opus", "used_percent": 88.0, "window_minutes": 10_080},
            {"label": "Weekly Sonnet", "utilization": None, "window_minutes": 10_080,
             "resets_at": _NOW + 600.0},
        ),
    )
    rows = status_bar.capacity_window_lines(model, epoch_now=_NOW)

    assert rows[0] == "Weekly Opus 12% left"
    assert rows[1].startswith("Weekly Sonnet no reading")


# --------------------------------------------------------------------------
# One title, one owner.
# --------------------------------------------------------------------------


def test_the_summary_line_names_the_period_and_never_the_provider() -> None:
    totals = usage_stats.UsageTotals()
    totals.sessions.add("s1")
    totals.input_tokens = 2_000_000

    line = usage_stats.usage_summary_line(
        totals,
        "sessions",
        period_label="Last 365 days",
    )

    assert line == "Last 365 days: 1 session"
    assert "Claude" not in line


def test_the_view_adds_exactly_one_provider_title() -> None:
    """"Claude · Claude, last 365 days: ..." had two owners for one fact."""
    totals = usage_stats.UsageTotals()
    totals.sessions.add("s1")
    model = _model(
        local_activity=LocalActivitySection(
            summary_text=usage_stats.usage_summary_line(
                totals,
                "sessions",
                period_label="Last 365 days",
            ),
        ),
    )

    assert model.menu_line.count("Claude") == 1
    assert model.menu_line == "Claude · Last 365 days: 1 session"


# --------------------------------------------------------------------------
# Never a confident percentage for an unattested reading.
# --------------------------------------------------------------------------


def _codex_descriptor():
    return next(
        row.descriptor
        for row in negotiate_provider_capacity_policies(negotiated_provider_sources())
        if row.descriptor is not None
        and row.descriptor.source == usage_stats.CODEX_QUOTA_SOURCE
    )


def _codex_lanes(payload, *, observed_at=_NOW):
    evidence = usage_stats.codex_capacity_evidence_from_windows(
        _codex_descriptor(),
        usage_stats.codex_windows_from_limits(payload),
        observed_at=observed_at,
    )
    return evidence.lanes


def test_a_window_that_only_says_it_just_opened_reports_no_balance() -> None:
    """The owner's Codex row: used 0.0, boundary exactly one window ahead.

    Every rate-limit record in the live rollout file carried
    `resets_at ~= observed_at + 604800` and `used_percent == 0.0`, while that
    same session consumed 376,434 input tokens. The boundary slid forward with
    the wall clock between records; a real ceiling's does not move.
    """
    lanes = _codex_lanes(
        {
            "primary": {
                "used_percent": 0.0,
                "window_minutes": 10_080,
                "resets_at": _NOW + 604_797.0,
            }
        }
    )

    assert len(lanes) == 1
    assert lanes[0].percent is None
    assert lanes[0].state is ObservationState.NULL


def test_a_stationary_boundary_with_nothing_spent_is_still_a_real_reading() -> None:
    """The suppression must not swallow a genuine untouched allowance."""
    lanes = _codex_lanes(
        {
            "primary": {
                "used_percent": 0.0,
                "window_minutes": 10_080,
                # Two days into a seven-day window: the boundary is fixed and
                # the allowance really is untouched.
                "resets_at": _NOW + 5 * 86_400.0,
            }
        }
    )

    assert lanes[0].percent == 0.0
    assert lanes[0].state is ObservationState.OBSERVED


def test_a_nonzero_reading_at_a_fresh_boundary_is_never_suppressed() -> None:
    lanes = _codex_lanes(
        {
            "primary": {
                "used_percent": 3.0,
                "window_minutes": 300,
                "resets_at": _NOW + 18_000.0,
            }
        }
    )

    assert lanes[0].percent == 3.0
    assert lanes[0].state is ObservationState.OBSERVED


def test_the_card_never_prints_a_percentage_for_the_suppressed_window() -> None:
    """End to end: the placeholder reaches the dropdown as "no reading"."""
    model = _model(
        provider_id="codex",
        provider_title="Codex",
        windows=(
            {
                "label": "primary",
                "used_percent": None,
                "window_minutes": 10_080,
                "resets_at": _NOW + 604_797.0,
            },
        ),
    )
    primary, _secondary = _lines(model)

    assert "100%" not in primary
    assert "%" not in primary
    assert primary == "Codex · 7d no reading"


# --------------------------------------------------------------------------
# A lane is the window it is, not the key it arrived under.
# --------------------------------------------------------------------------


def test_a_weekly_window_under_the_primary_key_binds_to_the_weekly_lane() -> None:
    """`primary` carrying 10,080 minutes is the WEEKLY ceiling, renamed.

    The card printed "7d" from `window_minutes` while the authority layer had
    the same row filed as `five-hour` from the label. OpenAI removed the 5-hour
    window entirely for about two and a half weeks in 2026, which is exactly
    when a payload like this appears.
    """
    lanes = _codex_lanes(
        {
            "primary": {
                "used_percent": 42.0,
                "window_minutes": 10_080,
                "resets_at": _NOW + 200_000.0,
            }
        }
    )

    assert tuple(lane.key.window for lane in lanes) == ("weekly",)


def test_the_ordinary_pair_still_binds_the_way_it_always_did() -> None:
    lanes = _codex_lanes(
        {
            "primary": {"used_percent": 25.0, "window_minutes": 300},
            "secondary": {"used_percent": 70.0, "window_minutes": 10_080},
        }
    )

    assert tuple(lane.key.window for lane in lanes) == ("five-hour", "weekly")


def test_a_declared_label_carrying_an_undeclared_window_is_dropped() -> None:
    """Force-fitting an unknown length is how the 7d/5h mismatch happened."""
    lanes = _codex_lanes(
        {"primary": {"used_percent": 25.0, "window_minutes": 1_440}}
    )

    assert lanes == ()


def test_an_undeclared_label_is_still_dropped_whatever_its_length() -> None:
    """The Spark rule, unchanged: length decides WHICH lane, never WHETHER."""
    lanes = _codex_lanes(
        {
            "additional_rate_limits": [
                {
                    "name": "GPT-5.3-Codex-Spark",
                    "used_percent": 97.0,
                    "window_minutes": 300,
                }
            ]
        }
    )

    assert lanes == ()


def test_a_window_with_no_stated_length_falls_back_to_its_label() -> None:
    lanes = _codex_lanes({"secondary": {"used_percent": 12.0}})

    assert tuple(lane.key.window for lane in lanes) == ("weekly",)


def test_time_moves_but_the_suppression_rule_does_not_depend_on_the_clock() -> None:
    """Guard against a rule that only holds at one instant."""
    for offset in (0.0, 1.0, 3_600.0, 86_400.0):
        observed = time.time() + offset
        lanes = _codex_lanes(
            {
                "primary": {
                    "used_percent": 0.0,
                    "window_minutes": 10_080,
                    "resets_at": observed + 604_800.0,
                }
            },
            observed_at=observed,
        )
        assert lanes[0].percent is None
