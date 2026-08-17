from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sidepulse.provider_usage_collectors import (
    ProviderHttpError,
    collect_antigravity,
    collect_cursor,
    collect_devin,
    collect_grok,
    collect_openai_api,
)
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


class FixtureHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=20.0):
        self.calls.append((method, url, headers or {}, body, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def preference(provider, **changes):
    pref = default_provider_usage_settings().preference(provider)
    if "browser_sources" in changes:
        pref = pref.__class__(
            pref.provider_id,
            pref.enabled,
            changes["browser_sources"],
            pref.reset_celebrations,
            pref.threshold_remaining,
            pref.options,
        )
    for key, value in changes.get("options", {}).items():
        pref = pref.with_option(key, value)
    return pref


def test_cursor_reads_local_app_session_and_fetches_usage(tmp_path: Path):
    db = tmp_path / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
            ("cursorAuth/accessToken", "fixture-cursor-session"),
        )
    http = FixtureHttp(
        [
            {"email": "person@example.invalid"},
            {"planUsage": {"usedPercent": 30}, "billingCycleEnd": 3000},
        ]
    )

    result = collect_cursor(
        preference("cursor"), home=tmp_path, observed_at=1000, http_json=http
    )

    assert result.state.value == "ready"
    assert result.lanes[0].remaining_percent == 70
    assert result.account_label == "person@example.invalid"
    assert http.calls[0][2]["Authorization"] == "Bearer fixture-cursor-session"
    assert http.calls[1][1].endswith("/api/usage-summary")


def test_cursor_without_local_auth_explains_browser_consent(tmp_path: Path):
    result = collect_cursor(
        preference("cursor"),
        home=tmp_path,
        observed_at=1000,
        http_json=FixtureHttp([]),
    )
    assert result.state.value == "needs_consent"
    assert result.action_label == "Enable Cursor browser access"


def test_devin_uses_sidepulse_credential_and_org_endpoint():
    http = FixtureHttp(
        [
            {
                "daily": {"used_percent": 10, "resets_at": 2000},
                "weekly": {"used_percent": 20, "resets_at": 3000},
            }
        ]
    )
    result = collect_devin(
        preference("devin", options={"organization": "org_fixture"}),
        observed_at=1000,
        credentials=FixtureCredentials({("devin", "token"): "fixture-devin-session"}),
        http_json=http,
    )
    assert result.state.value == "ready"
    assert "/api/org_fixture/billing/quota/usage" in http.calls[0][1]
    assert http.calls[0][2]["Authorization"] == "Bearer fixture-devin-session"


def test_grok_reads_local_auth_and_cli_proxy(tmp_path: Path):
    grok = tmp_path / ".grok"
    grok.mkdir()
    (grok / "auth.json").write_text(
        json.dumps(
            {
                "https://auth.x.ai::fixture": {
                    "key": "fixture-grok-session",
                    "expires_at": 9999999999,
                    "email": "person@example.invalid",
                    "auth_mode": "supergrok",
                }
            }
        )
    )
    http = FixtureHttp(
        [{"config": {"creditUsagePercent": 44, "billingPeriodEnd": 3000}}]
    )
    result = collect_grok(
        preference("grok"),
        home=tmp_path,
        observed_at=1000,
        credentials=FixtureCredentials(),
        http_json=http,
    )
    assert result.state.value == "ready"
    assert result.lanes[0].remaining_percent == 56
    assert result.account_label == "person@example.invalid"
    assert http.calls[0][2]["x-xai-token-auth"] == "xai-grok-cli"


def test_grok_missing_login_is_actionable(tmp_path: Path):
    result = collect_grok(
        preference("grok"),
        home=tmp_path,
        observed_at=1000,
        credentials=FixtureCredentials(),
        http_json=FixtureHttp([]),
    )
    assert result.state.value == "needs_sign_in"
    assert result.action_label == "Run grok login"


def test_antigravity_uses_configured_loopback_endpoint():
    http = FixtureHttp(
        [
            {
                "response": {
                    "groups": [
                        {
                            "displayName": "Gemini Models",
                            "buckets": [
                                {
                                    "bucketId": "weekly",
                                    "remaining": {"remainingFraction": 0.75},
                                }
                            ],
                        }
                    ]
                }
            }
        ]
    )
    result = collect_antigravity(
        preference("antigravity", options={"endpoint": "https://127.0.0.1:4321"}),
        observed_at=1000,
        http_json=http,
        command_runner=lambda _args, _timeout: "",
    )
    assert result.state.value == "ready"
    assert result.lanes[0].label == "Gemini Weekly"
    assert "RetrieveUserQuotaSummary" in http.calls[0][1]


def test_openai_admin_usage_uses_official_organization_endpoints():
    http = FixtureHttp(
        [
            {
                "data": [
                    {
                        "results": [
                            {"input_tokens": 10, "output_tokens": 5, "model": "gpt-fixture"}
                        ]
                    }
                ]
            },
            {"data": [{"results": [{"amount": {"value": 1.25}}]}]},
        ]
    )
    result = collect_openai_api(
        preference("openai-api"),
        observed_at=2_000_000,
        credentials=FixtureCredentials(
            {("openai-api", "admin-key"): "fixture-openai-admin-session"}
        ),
        http_json=http,
    )
    assert result.state.value == "ready"
    assert result.estimated_cost_usd == 1.25
    assert "/v1/organization/usage/completions" in http.calls[0][1]
    assert "/v1/organization/costs" in http.calls[1][1]


def test_http_unauthorized_maps_to_sign_in_required():
    result = collect_devin(
        preference("devin", options={"organization": "org_fixture"}),
        observed_at=1000,
        credentials=FixtureCredentials({("devin", "token"): "fixture-invalid"}),
        http_json=FixtureHttp([ProviderHttpError(401, "unauthorized")]),
    )
    assert result.state.value == "needs_sign_in"
    assert result.reason_code == "authentication_required"
