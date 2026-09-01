from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sidepulse.provider_usage_codex_claude import collect_claude, collect_codex
from sidepulse.provider_usage_settings import default_provider_usage_settings


class FixtureCredentials:
    """Inert test-only credential source. Values are not real credentials."""

    def __init__(self, values=None):
        self.values = values or {}

    def get(self, provider, account):
        value = self.values.get((provider, account))
        return type(
            "Read",
            (),
            {
                "available": value is not None,
                "secret": value,
                "reason": None if value is not None else "credential_not_found",
            },
        )()


def preference(provider: str):
    return default_provider_usage_settings().preference(provider)


def test_codex_combines_local_quota_tokens_models_and_cost(tmp_path: Path):
    result = collect_codex(
        preference("codex"),
        home=tmp_path,
        observed_at=1000,
        local_scanner=lambda _home, _observed: {
            "windows": [
                {
                    "label": "primary",
                    "used_percent": 40,
                    "window_minutes": 300,
                    "resets_at": 2000,
                },
                {
                    "label": "Spark Weekly",
                    "used_percent": 75,
                    "window_minutes": 10080,
                    "resets_at": 3000,
                },
            ],
            "input_tokens": 100,
            "cached_input_tokens": 25,
            "output_tokens": 50,
            "model_count": 2,
            "estimated_cost_usd": 1.5,
            "cache_savings_usd": 0.2,
            "account_label": "account-fixture",
        },
    )
    assert result.state.value == "ready"
    assert result.lanes[0].remaining_percent == 60
    assert result.lanes[1].label == "Spark Weekly"
    assert result.input_tokens == 100
    assert result.model_count == 2
    assert result.estimated_cost_usd == 1.5


def test_codex_live_rate_limit_replaces_a_newer_but_stale_local_percentage(
    tmp_path: Path,
):
    result = collect_codex(
        preference("codex"),
        home=tmp_path,
        observed_at=1000,
        live_probe=lambda: {
            "used_percent": 95.0,
            "resets_at": 3000.0,
            "window_minutes": 10080,
        },
        local_scanner=lambda _home, _observed: {
            "windows": [
                {
                    "label": "primary",
                    "used_percent": 89.0,
                    "window_minutes": 10080,
                    "resets_at": 3000.0,
                }
            ],
            "windows_observed_at": 999.0,
            "input_tokens": 100,
            "cached_input_tokens": 25,
            "output_tokens": 50,
            "model_count": 2,
        },
    )

    assert result.state.value == "ready"
    assert result.lanes[0].remaining_percent == 5.0
    assert result.lanes[0].source_id == "codex-app-server"
    assert result.input_tokens == 100
    assert result.cached_input_tokens == 25
    assert result.output_tokens == 50


def test_codex_live_rate_limit_skips_the_default_cold_transcript_scan(
    tmp_path: Path,
):
    import sidepulse.provider_usage_codex_claude as subject

    with (
        patch.object(subject, "_cached_codex_local_scan", return_value=None) as cached,
        patch.object(
            subject,
            "_default_provider_local_scan",
            side_effect=AssertionError("cold scan should not run on the live path"),
        ),
    ):
        result = collect_codex(
            preference("codex"),
            home=tmp_path,
            observed_at=1000,
            live_probe=lambda: {
                "used_percent": 95.0,
                "resets_at": 3000.0,
                "window_minutes": 10080,
            },
            local_scanner=subject._default_codex_local_scan,
        )

    cached.assert_called_once_with(tmp_path, 1000)
    assert result.state.value == "ready"
    assert result.lanes[0].remaining_percent == 5.0
    assert result.lanes[0].source_id == "codex-app-server"


def test_codex_without_rollout_evidence_is_actionable(tmp_path: Path):
    result = collect_codex(
        preference("codex"),
        home=tmp_path,
        observed_at=1000,
        local_scanner=lambda _home, _observed: None,
    )
    assert result.state.value == "source_not_found"
    assert result.action_label == "Use Codex once or sign in"


def test_claude_combines_oauth_windows_and_local_tokens(tmp_path: Path):
    result = collect_claude(
        preference("claude"),
        home=tmp_path,
        observed_at=1000,
        credentials=FixtureCredentials(
            {("claude", "oauth-token"): "fixture-claude-session"}
        ),
        quota_fetcher=lambda _token: [
            {"label": "5-hour", "used_percent": 15, "resets_at": 2000},
            {"label": "Fable only", "used_percent": 80, "resets_at": 3000},
        ],
        local_scanner=lambda _home, _observed: {
            "input_tokens": 200,
            "cached_input_tokens": 100,
            "output_tokens": 50,
            "model_count": 3,
            "estimated_cost_usd": 2.25,
            "cache_savings_usd": 0.75,
        },
    )
    assert result.state.value == "ready"
    assert next(lane for lane in result.lanes if lane.model == "fable").remaining_percent == 20
    assert result.cached_input_tokens == 100
    assert result.cache_savings_usd == 0.75


def test_claude_default_quota_refresh_never_falls_back_to_a_cold_scan(
    tmp_path: Path,
):
    import sidepulse.provider_usage_codex_claude as subject

    with (
        patch.object(subject, "_cached_claude_local_scan", return_value=None) as cached,
        patch.object(
            subject,
            "_default_provider_local_scan",
            side_effect=AssertionError("cold scan should not run on the quota path"),
        ),
    ):
        result = collect_claude(
            preference("claude"),
            home=tmp_path,
            observed_at=1000,
            credentials=FixtureCredentials(
                {("claude", "oauth-token"): "fixture-claude-session"}
            ),
            quota_fetcher=lambda _token: [
                {"label": "5-hour", "used_percent": 95, "resets_at": 2000}
            ],
            local_scanner=subject._default_claude_local_scan,
        )

    cached.assert_called_once_with(tmp_path, 1000)
    assert result.state.value == "ready"
    assert result.lanes[0].remaining_percent == 5


def test_claude_cached_local_scan_reuses_bounded_aggregate(tmp_path: Path):
    import sidepulse.provider_usage_codex_claude as subject

    cache = {
        "files": {
            "transcript.jsonl": {
                "records": [[0, 0, 1, 900.0, 100, 25, 0, 50, 0]],
                "mtime": 900.0,
            }
        },
        "sessions": ["session-1"],
        "models": ["claude", "claude-sonnet"],
        "dedupes": ["event-1"],
    }
    with patch("sidepulse.usage_stats._load_cache", return_value=cache) as load:
        result = subject._cached_claude_local_scan(tmp_path, 1000)

    assert load.called
    assert result == {
        "input_tokens": 100,
        "cached_input_tokens": 25,
        "output_tokens": 50,
        "model_count": 1,
        "estimated_cost_usd": None,
        "cache_savings_usd": None,
    }


def test_claude_without_explicit_usage_connection_is_actionable(tmp_path: Path):
    result = collect_claude(
        preference("claude"),
        home=tmp_path,
        observed_at=1000,
        credentials=FixtureCredentials(),
        quota_fetcher=lambda _token: [],
        local_scanner=lambda _home, _observed: None,
    )
    assert result.state.value == "needs_consent"
    assert result.action_label == "Connect Claude usage"
