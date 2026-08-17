from __future__ import annotations

from sidepulse.provider_usage_parsers import (
    parse_antigravity_usage,
    parse_claude_usage,
    parse_codex_usage,
    parse_cursor_usage,
    parse_devin_usage,
    parse_grok_usage,
    parse_openai_api_usage,
)


def test_codex_preserves_spark_weekly_as_a_dynamic_lane() -> None:
    snapshot = parse_codex_usage(
        windows=[
            {
                "label": "primary",
                "used_percent": 30,
                "window_minutes": 300,
                "resets_at": 2000,
            },
            {
                "label": "secondary",
                "used_percent": 40,
                "window_minutes": 10080,
                "resets_at": 3000,
            },
            {
                "label": "Spark Weekly",
                "used_percent": 75,
                "window_minutes": 10080,
                "resets_at": 3000,
            },
        ],
        observed_at=1000,
        input_tokens=100,
        cached_input_tokens=25,
        output_tokens=50,
        model_count=2,
        estimated_cost_usd=1.5,
    )
    by_label = {lane.label: lane for lane in snapshot.lanes}
    assert by_label["5-hour"].remaining_percent == 70
    assert by_label["Weekly"].remaining_percent == 60
    assert by_label["Spark Weekly"].remaining_percent == 25
    assert by_label["Spark Weekly"].bindable is False
    assert snapshot.model_count == 2


def test_claude_preserves_fable_and_model_scoped_windows() -> None:
    snapshot = parse_claude_usage(
        windows=[
            {"label": "5-hour", "used_percent": 10, "resets_at": 2000},
            {"label": "weekly", "used_percent": 20, "resets_at": 3000},
            {"label": "Fable only", "used_percent": 80, "resets_at": 3000},
        ],
        observed_at=1000,
    )
    fable = next(lane for lane in snapshot.lanes if lane.model == "fable")
    assert fable.label == "Fable only"
    assert fable.remaining_percent == 20
    assert fable.bindable is False


def test_cursor_usage_summary_maps_plan_auto_api_and_extra_spend() -> None:
    snapshot = parse_cursor_usage(
        {
            "planUsage": {"usedPercent": 35},
            "autoComposerUsage": {"usedPercent": 20},
            "apiUsage": {"usedPercent": 5},
            "billingCycleEnd": "2026-09-01T00:00:00Z",
            "extraUsageCents": 1234,
            "account": {"email": "person@example.com"},
        },
        observed_at=1000,
    )
    assert [lane.label for lane in snapshot.lanes] == [
        "Included plan",
        "Auto + Composer",
        "API models",
    ]
    assert snapshot.lanes[0].remaining_percent == 65
    assert snapshot.estimated_cost_usd == 12.34
    assert snapshot.account_label == "person@example.com"


def test_devin_maps_daily_and_weekly_windows() -> None:
    snapshot = parse_devin_usage(
        {
            "daily": {"used_percent": 25, "resets_at": 2000},
            "weekly": {"used_percent": 60, "resets_at": 3000},
            "organization": "org_example",
        },
        observed_at=1000,
    )
    assert [(lane.label, lane.remaining_percent) for lane in snapshot.lanes] == [
        ("Daily", 75),
        ("Weekly", 40),
    ]
    assert snapshot.account_label == "org_example"


def test_grok_maps_cli_proxy_credit_usage_and_reset() -> None:
    snapshot = parse_grok_usage(
        {
            "config": {
                "creditUsagePercent": 72,
                "currentPeriod": {"end": "2026-09-01T00:00:00Z"},
            },
            "email": "person@example.com",
        },
        observed_at=1000,
    )
    assert snapshot.lanes[0].label in {"Weekly", "Monthly", "Credits"}
    assert snapshot.lanes[0].remaining_percent == 28
    assert snapshot.account_label == "person@example.com"


def test_antigravity_maps_four_summary_buckets() -> None:
    snapshot = parse_antigravity_usage(
        {
            "response": {
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "session-five-hour",
                                "displayName": "Session",
                                "remaining": {"remainingFraction": 0.4},
                                "resetTime": 2000,
                            },
                            {
                                "bucketId": "weekly",
                                "displayName": "Weekly",
                                "remaining": {"remainingFraction": 0.8},
                                "resetTime": 3000,
                            },
                        ],
                    },
                    {
                        "displayName": "Claude and GPT models",
                        "buckets": [
                            {
                                "bucketId": "session-five-hour",
                                "displayName": "Session",
                                "remaining": {"remainingFraction": 0.5},
                                "resetTime": 2000,
                            },
                            {
                                "bucketId": "weekly",
                                "displayName": "Weekly",
                                "remaining": {"remainingFraction": 0.7},
                                "resetTime": 3000,
                            },
                        ],
                    },
                ]
            }
        },
        observed_at=1000,
    )
    assert [lane.label for lane in snapshot.lanes] == [
        "Gemini Session",
        "Gemini Weekly",
        "Claude + GPT Session",
        "Claude + GPT Weekly",
    ]
    assert snapshot.lanes[0].remaining_percent == 40


def test_openai_usage_aggregates_tokens_models_requests_and_cost() -> None:
    snapshot = parse_openai_api_usage(
        {
            "usage": {
                "data": [
                    {
                        "results": [
                            {
                                "input_tokens": 100,
                                "output_tokens": 50,
                                "num_model_requests": 2,
                                "model": "gpt-5.4",
                            },
                            {
                                "input_tokens": 25,
                                "output_tokens": 10,
                                "num_model_requests": 1,
                                "model": "gpt-5.3",
                            },
                        ]
                    }
                ]
            },
            "costs": {"data": [{"results": [{"amount": {"value": 2.75}}]}]},
        },
        observed_at=1000,
    )
    assert snapshot.input_tokens == 125
    assert snapshot.output_tokens == 60
    assert snapshot.model_count == 2
    assert snapshot.estimated_cost_usd == 2.75
