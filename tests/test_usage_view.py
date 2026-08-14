from __future__ import annotations

from sidepulse import claude_quota, usage_stats
from sidepulse.capacity_types import ObservationState, ResetState
from sidepulse.usage_view import (
    CostEstimateSection,
    LocalActivitySection,
    adapt_legacy_usage_windows,
    build_provider_usage_view,
)


def test_codex_windows_keep_five_hour_seven_day_and_additional_limits() -> None:
    windows = usage_stats.codex_windows_from_limits(
        {
            "primary": {
                "used_percent": 22,
                "window_minutes": 300,
                "resets_at": "2026-08-12T05:00:00Z",
            },
            "secondary": {
                "used_percent": 43,
                "window_minutes": 10_080,
                "reset_at": 1_777_777_777,
            },
            "additional_rate_limits": [
                {
                    "name": "Spark",
                    "used_percent": 68,
                    "window_minutes": 1_440,
                }
            ],
        }
    )

    view = build_provider_usage_view(
        "codex",
        "Codex",
        windows,
        last_success_at=1_000.0,
        now=1_000.0,
    )

    assert [window.duration_text for window in view.windows] == ["5h", "7d", "24h"]
    assert [window.percent_used for window in view.windows] == [22.0, 43.0, 68.0]
    assert view.windows[0].reset_at == "2026-08-12T05:00:00Z"
    assert view.windows[1].reset_at == 1_777_777_777
    assert "5h 78% left" in view.menu_line
    assert "7d 57% left" in view.menu_line
    assert "weekly" not in view.menu_line.lower()


def test_non_seven_day_primary_is_never_called_weekly() -> None:
    view = build_provider_usage_view(
        "codex",
        "Codex",
        ({"label": "primary", "used_percent": 75, "window_minutes": 1_440},),
        last_success_at=1_000.0,
        now=1_000.0,
    )

    assert "24h" in view.settings_text
    assert "weekly" not in view.settings_text.lower()


def test_claude_known_and_additional_windows_keep_duration_and_reset_values() -> None:
    windows = claude_quota.windows_from_payload(
        {
            "five_hour": {
                "utilization": 12,
                "resets_at": "2026-08-12T05:00:00Z",
            },
            "seven_day": {
                "utilization": 45,
                "resets_at": "2026-08-19T00:00:00Z",
            },
            "limits": [
                {
                    "name": "Opus",
                    "utilization": 80,
                    "window_minutes": 1_440,
                    "resets_at": "2026-08-13T00:00:00Z",
                }
            ],
        }
    )

    view = build_provider_usage_view(
        "claude",
        "Claude",
        windows,
        last_success_at=1_000.0,
        now=1_000.0,
    )

    assert [window.duration_text for window in view.windows] == ["5h", "7d", "24h"]
    assert [window.reset_at for window in view.windows] == [
        "2026-08-12T05:00:00Z",
        "2026-08-19T00:00:00Z",
        "2026-08-13T00:00:00Z",
    ]
    assert view.windows[2].label == "Opus"


def test_malformed_percent_is_ignored_and_numeric_percent_is_clamped() -> None:
    view = build_provider_usage_view(
        "codex",
        "Codex",
        (
            {"label": "bad", "used_percent": "lots", "window_minutes": 300},
            {"label": "low", "used_percent": -12, "window_minutes": 60},
            {"label": "high", "used_percent": 190, "window_minutes": 120},
        ),
        last_success_at=1_000.0,
        now=1_000.0,
    )

    assert [window.percent_used for window in view.windows] == [0.0, 100.0]
    assert "lots" not in view.menu_line


def test_capacity_remaining_is_clamped_and_usage_knowledge_is_explicit() -> None:
    view = build_provider_usage_view(
        "codex",
        "Codex",
        (
            {"label": "low", "used_percent": -12, "window_minutes": 60},
            {"label": "high", "used_percent": 190, "window_minutes": 120},
        ),
        last_success_at=1_000.0,
        now=1_000.0,
    )

    assert [window.percent_remaining for window in view.windows] == [100.0, 0.0]
    assert [window.usage_known for window in view.windows] == [True, True]


def test_reset_metadata_without_usage_never_displays_a_fake_zero_percent() -> None:
    view = build_provider_usage_view(
        "claude",
        "Claude",
        (
            {
                "label": "5-hour",
                "utilization": "unknown",
                "window_minutes": 300,
                "resets_at": "2026-01-01T00:00:00Z",
            },
        ),
        last_success_at=1_767_225_500.0,
        now=1_767_225_500.0,
    )

    assert len(view.windows) == 1
    window = view.windows[0]
    assert window.usage_known is False
    assert window.reset_known is True
    assert window.percent_remaining is None
    assert window.reset_epoch == 1_767_225_600.0
    assert window.reset_text(1_767_225_500.0) == "in 2m"
    assert "0%" not in window.compact_text
    assert "0%" not in view.menu_line


def test_malformed_reset_is_omitted_without_hiding_valid_usage() -> None:
    view = build_provider_usage_view(
        "codex",
        "Codex",
        (
            {
                "label": "5-hour",
                "used_percent": 25,
                "window_minutes": 300,
                "resets_at": "not-a-reset",
            },
        ),
        last_success_at=1_000.0,
        now=1_000.0,
    )

    assert len(view.windows) == 1
    assert view.windows[0].usage_known is True
    assert view.windows[0].reset_known is False
    assert view.windows[0].reset_text(1_000.0) is None
    assert "75% left" in view.menu_line


def test_stale_last_known_good_data_remains_visible_with_provider_error() -> None:
    view = build_provider_usage_view(
        "claude",
        "Claude",
        ({"label": "five_hour", "utilization": 37, "window_minutes": 300},),
        last_success_at=600.0,
        now=1_000.0,
        error_text="temporarily unavailable",
    )

    assert view.stale is True
    assert view.missing is False
    assert "5h 63% left" in view.menu_line
    assert "stale" in view.menu_line.lower()
    assert "temporarily unavailable" in view.settings_text


def test_stale_last_known_good_keeps_remaining_but_withholds_reset_authority() -> None:
    view = build_provider_usage_view(
        "claude",
        "Claude",
        (
            {
                "label": "five_hour",
                "utilization": 37,
                "window_minutes": 300,
                "resets_at": 1_040.0,
            },
        ),
        last_success_at=600.0,
        now=1_000.0,
        reset_now=1_000.0,
        error_text="temporarily unavailable",
    )

    window = view.windows[0]
    assert window.percent_remaining == 63.0
    assert window.reset_epoch == 1_040.0
    assert window.reset_state is ResetState.STALE
    assert window.reset_known is False
    assert window.reset_text(1_000.0) is None


def test_missing_refreshing_fresh_and_error_states_are_distinct() -> None:
    missing = build_provider_usage_view("codex", "Codex", (), now=1_000.0)
    loading = build_provider_usage_view(
        "codex", "Codex", (), now=1_000.0, refreshing=True
    )
    fresh = build_provider_usage_view(
        "codex",
        "Codex",
        ({"used_percent": 20, "window_minutes": 300},),
        last_success_at=1_000.0,
        now=1_000.0,
    )
    error = build_provider_usage_view(
        "codex", "Codex", (), now=1_000.0, error_text="unavailable"
    )

    partial = build_provider_usage_view(
        "codex",
        "Codex",
        ({"used_percent": 21, "window_minutes": 300},),
        last_success_at=1_000.0,
        now=1_000.0,
        partial=True,
        source_text="18 files",
    )
    stale = build_provider_usage_view(
        "codex",
        "Codex",
        ({"used_percent": 22, "window_minutes": 300},),
        last_success_at=699.0,
        now=1_000.0,
    )

    states = {
        missing.menu_line,
        loading.menu_line,
        fresh.menu_line,
        partial.menu_line,
        stale.menu_line,
        error.menu_line,
    }
    assert len(states) == 6
    assert "not loaded" in missing.menu_line.lower()
    assert "loading" in loading.menu_line.lower()
    assert fresh.menu_line == "Codex · 5h 80% left"
    assert "partial" in partial.menu_line.lower()
    assert "18 files" in partial.menu_line
    assert "stale" in stale.menu_line.lower()
    assert "unavailable" in error.menu_line.lower()


def test_source_text_is_bounded_and_does_not_expose_absolute_paths() -> None:
    view = build_provider_usage_view(
        "codex",
        "Codex",
        (),
        last_success_at=1_000.0,
        now=1_000.0,
        partial=True,
        source_text=(
            "18 files · /Users/jonathan/.codex/sessions · "
            + "coverage " * 40
        ),
    )

    assert view.source_text is not None
    assert len(view.source_text) <= 120
    assert "/Users" not in view.source_text
    assert ".codex" not in view.source_text
    assert "18 files" in view.source_text


def test_successful_empty_result_clears_windows_without_becoming_missing() -> None:
    view = build_provider_usage_view(
        "codex",
        "Codex",
        (),
        last_success_at=1_000.0,
        now=1_000.0,
    )

    assert view.windows == ()
    assert view.missing is False
    assert view.stale is False
    assert "No usage yet" in view.menu_line


def test_settings_text_keeps_local_summary_alongside_rate_windows() -> None:
    view = build_provider_usage_view(
        "codex",
        "Codex",
        ({"used_percent": 20, "window_minutes": 300},),
        last_success_at=1_000.0,
        now=1_000.0,
        summary_text="Codex today: 3 sessions · 2M tokens",
    )

    assert "Codex today: 3 sessions" in view.settings_text
    assert "5h 80% left" in view.settings_text


def test_named_legacy_adapter_normalizes_used_first_once_and_preserves_null_zero() -> None:
    windows = adapt_legacy_usage_windows(
        "codex",
        "Codex",
        (
            {"label": "exhausted", "used_percent": 100, "window_minutes": 300},
            {
                "label": "reset-only",
                "used_percent": None,
                "window_minutes": 10_080,
                "resets_at": 2_000.0,
            },
        ),
        now=1_000.0,
    )

    assert [window.percent_remaining for window in windows] == [0.0, None]
    assert [window.capacity.state for window in windows] == [
        ObservationState.OBSERVED_ZERO,
        ObservationState.NULL,
    ]
    assert [window.percent_used for window in windows] == [100.0, None]


def test_compact_capacity_copy_is_remaining_first_after_legacy_adaptation() -> None:
    window = adapt_legacy_usage_windows(
        "codex",
        "Codex",
        ({"label": "primary", "used_percent": 25, "window_minutes": 300},),
        now=1_000.0,
    )[0]

    assert window.compact_text == "5h 75% left"
    assert "25%" not in window.compact_text


def test_local_activity_and_cost_estimate_are_typed_sections_not_quota_lanes() -> None:
    activity = LocalActivitySection(
        summary_text="Codex today: 3 sessions · 2M tokens",
        detail_text="3 local sessions observed",
        partial=True,
        source_text="Local transcripts · 18 files · partial",
    )
    estimate = CostEstimateSection(text="Estimated local cost: $1.20")

    view = build_provider_usage_view(
        "codex",
        "Codex",
        ({"used_percent": 20, "window_minutes": 300},),
        last_success_at=1_000.0,
        now=1_000.0,
        local_activity=activity,
        cost_estimate=estimate,
    )

    assert view.local_activity == activity
    assert view.cost_estimate == estimate
    assert view.windows[0].percent_remaining == 80.0
    assert view.windows[0].capacity.state is ObservationState.OBSERVED
    assert "3 sessions" in view.settings_text
    assert "Estimated local cost" in view.settings_text
    assert "partial" in view.menu_line.lower()


def test_claude_normalization_ignores_boolean_utilization_and_accepts_reset_alias() -> None:
    windows = claude_quota.windows_from_payload(
        {
            "five_hour": {"utilization": True},
            "seven_day": {
                "utilization": 40,
                "reset_at": 1_777_777_777,
            },
        }
    )

    assert len(windows) == 1
    assert windows[0]["window_minutes"] == 10_080
    assert windows[0]["resets_at"] == 1_777_777_777


def test_normalizers_skip_nonfinite_and_boolean_window_values_without_crashing() -> None:
    codex = usage_stats.codex_windows_from_limits(
        {
            "primary": {"used_percent": float("nan"), "window_minutes": 300},
            "secondary": {"used_percent": 40, "window_minutes": float("inf")},
        }
    )
    claude = claude_quota.windows_from_payload(
        {
            "five_hour": {"utilization": float("inf")},
            "limits": [{"utilization": 20, "window_minutes": True}],
        }
    )

    assert codex == [
        {
            "label": "secondary",
            "used_percent": 40.0,
            "window_minutes": None,
            "resets_at": None,
        }
    ]
    assert claude == [
        {
            "label": "limit",
            "utilization": 20.0,
            "window_minutes": None,
            "resets_at": None,
        }
    ]


def test_claude_real_fable_limits_shape_keeps_only_scoped_weekly_extra() -> None:
    # Exact shape observed by pinned CodexBar during Anthropic's Fable 5
    # promotional access window on 2026-07-03.
    payload = {
        "five_hour": {
            "utilization": 11.0,
            "resets_at": "2026-07-03T00:30:00.282668+00:00",
        },
        "seven_day": {
            "utilization": 9.0,
            "resets_at": "2026-07-08T09:00:00.282694+00:00",
        },
        "seven_day_opus": None,
        "seven_day_sonnet": None,
        "limits": [
            {
                "kind": "session",
                "group": "session",
                "percent": 11,
                "resets_at": "2026-07-03T00:30:00.282668+00:00",
                "scope": None,
                "is_active": True,
            },
            {
                "kind": "weekly_all",
                "group": "weekly",
                "percent": 9,
                "resets_at": "2026-07-08T09:00:00.282694+00:00",
                "scope": None,
                "is_active": False,
            },
            {
                "kind": "weekly_scoped",
                "group": "weekly",
                "percent": 5,
                "resets_at": "2026-07-08T09:00:00.283070+00:00",
                "scope": {
                    "model": {"id": None, "display_name": "Fable"},
                    "surface": None,
                },
                "is_active": False,
            },
        ],
    }

    windows = claude_quota.windows_from_payload(payload)

    assert [(window["label"], window["utilization"]) for window in windows] == [
        ("5-hour", 11.0),
        ("weekly", 9.0),
        ("Fable only", 5.0),
    ]
    assert [window["window_minutes"] for window in windows] == [300, 10_080, 10_080]
    assert windows[2]["resets_at"] == "2026-07-08T09:00:00.283070+00:00"


def test_claude_scoped_windows_precede_one_routines_alias_without_duplicates() -> None:
    payload = {
        "five_hour": {
            "utilization": 12.5,
            "resets_at": "2025-12-25T12:00:00.000Z",
        },
        "seven_day": {
            "utilization": 30,
            "resets_at": "2025-12-31T00:00:00.000Z",
        },
        "seven_day_routines": {
            "utilization": 18,
            "resets_at": "2026-01-01T00:00:00.000Z",
        },
        "seven_day_cowork": {
            "utilization": 19,
            "resets_at": "2026-01-02T00:00:00.000Z",
        },
        "limits": [
            {
                "kind": "weekly_scoped",
                "group": "weekly",
                "percent": 29,
                "resets_at": "2025-12-31T00:00:00.000Z",
                "scope": {
                    "model": {"id": None, "display_name": "Fable"},
                    "surface": None,
                },
                "is_active": False,
            },
            {
                "kind": "weekly_scoped",
                "group": "weekly",
                "percent": 31,
                "resets_at": "2025-12-31T00:00:00.000Z",
                "scope": {
                    "model": {"id": "all-models", "display_name": "All models"}
                },
            },
        ],
    }

    windows = claude_quota.windows_from_payload(payload)

    assert [window["label"] for window in windows] == [
        "5-hour",
        "weekly",
        "Fable only",
        "Daily Routines",
    ]
    assert windows[-1]["utilization"] == 18.0
    assert windows[-1]["window_minutes"] == 10_080


def test_claude_top_level_model_weekly_aliases_preserve_duration_and_reset() -> None:
    sonnet = claude_quota.windows_from_payload(
        {
            "five_hour": {"utilization": 10},
            "seven_day_sonnet": {
                "utilization": 41,
                "resets_at": "2026-07-08T09:00:00Z",
            },
        }
    )
    opus = claude_quota.windows_from_payload(
        {
            "five_hour": {"utilization": 10},
            "seven_day_opus": {
                "utilization": 42,
                "resets_at": "2026-07-09T09:00:00Z",
            },
        }
    )

    assert sonnet[1] == {
        "label": "Sonnet only",
        "utilization": 41.0,
        "window_minutes": 10_080,
        "resets_at": "2026-07-08T09:00:00Z",
    }
    assert opus[1] == {
        "label": "Opus only",
        "utilization": 42.0,
        "window_minutes": 10_080,
        "resets_at": "2026-07-09T09:00:00Z",
    }


def test_claude_malformed_routines_alias_falls_through_to_valid_cowork() -> None:
    windows = claude_quota.windows_from_payload(
        {
            "five_hour": {"utilization": 12.5},
            "seven_day_routines": {"utilization": "unknown"},
            "seven_day_cowork": {
                "utilization": 14,
                "resets_at": "2026-01-01T00:00:00.000Z",
            },
        }
    )

    assert windows[-1] == {
        "label": "Daily Routines",
        "utilization": 14.0,
        "window_minutes": 10_080,
        "resets_at": "2026-01-01T00:00:00.000Z",
    }
