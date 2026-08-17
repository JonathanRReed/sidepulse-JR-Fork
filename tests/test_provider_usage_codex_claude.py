from __future__ import annotations

from pathlib import Path

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
