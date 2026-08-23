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


def test_devin_sends_the_org_header_the_endpoint_actually_requires():
    """A valid session token alone returns 401. Confirmed live against a
    real account: the request only authenticates when it also carries
    x-cog-org-id, which is why "Import" could appear to succeed and the
    card still said reconnect."""
    http = FixtureHttp([{"daily_percentage": 80, "weekly_percentage": 40}])
    result = collect_devin(
        preference(
            "devin",
            options={"organization": "org/acme", "organization_id": "org-abc12345"},
        ),
        observed_at=1000,
        credentials=FixtureCredentials({("devin", "token"): "auth1_fixture"}),
        http_json=http,
    )
    assert result.state.value == "ready"
    _method, url, headers = http.calls[0][:3]
    assert headers["x-cog-org-id"] == "org-abc12345"
    # The internal id is the path segment the endpoint answers on, and
    # the slash in an "org/<slug>" value must survive: quote(safe="")
    # used to escape it to %2F, so no stored org shape could ever work.
    assert "/api/org-abc12345/billing/quota/usage" in url
    assert "%2F" not in url


def test_devin_imports_the_browser_session_instead_of_demanding_a_key(monkeypatch):
    """The whole point: browser access enabled and no stored credential
    still produces usage, because the session Devin already wrote in the
    user's browser is taken instead of an API key."""
    import sidepulse.provider_usage_collectors as module
    from sidepulse.browser_session_import import BrowserSession

    monkeypatch.setattr(
        module,
        "_import_devin_browser_session",
        lambda: BrowserSession(
            token="auth1_from_browser",
            organization="org/acme",
            internal_organization_id="org-abc12345",
            source_label="Zen default",
        ),
    )
    http = FixtureHttp([{"daily_percentage": 80, "weekly_percentage": 40}])
    result = collect_devin(
        preference("devin", browser_sources=True),
        observed_at=1000,
        credentials=FixtureCredentials({}),
        http_json=http,
    )
    assert result.state.value == "ready"
    assert http.calls[0][2]["Authorization"] == "Bearer auth1_from_browser"
    assert [lane.lane_id for lane in result.lanes] == ["daily", "weekly"]


def test_devin_without_browser_access_still_asks_for_consent_first():
    result = collect_devin(
        preference("devin", browser_sources=False),
        observed_at=1000,
        credentials=FixtureCredentials({}),
        http_json=FixtureHttp([]),
    )
    assert result.state.value == "needs_consent"
    assert result.action_label == "Enable Devin browser access"


def test_devin_prefers_the_live_browser_session_over_a_stale_stored_one(monkeypatch):
    """Devin rotates its web token. If a frozen copy outranked the live
    one, every rotation would show up as a card that broke by itself and
    demanded a reconnect."""
    import sidepulse.provider_usage_collectors as module
    from sidepulse.browser_session_import import BrowserSession

    monkeypatch.setattr(
        module,
        "_import_devin_browser_session",
        lambda: BrowserSession(
            token="auth1_rotated_today",
            organization="org/acme",
            internal_organization_id="org-abc12345",
            source_label="Zen default",
        ),
    )
    http = FixtureHttp([{"daily_percentage": 10}])
    result = collect_devin(
        preference("devin", browser_sources=True),
        observed_at=1000,
        credentials=FixtureCredentials({("devin", "token"): "auth1_from_last_month"}),
        http_json=http,
    )
    assert result.state.value == "ready"
    assert http.calls[0][2]["Authorization"] == "Bearer auth1_rotated_today"


def test_devin_falls_back_to_the_stored_token_when_no_browser_session_exists(monkeypatch):
    import sidepulse.provider_usage_collectors as module

    monkeypatch.setattr(module, "_import_devin_browser_session", lambda: None)
    http = FixtureHttp([{"daily_percentage": 10}])
    result = collect_devin(
        preference(
            "devin",
            browser_sources=True,
            options={"organization_id": "org-abc12345"},
        ),
        observed_at=1000,
        credentials=FixtureCredentials({("devin", "token"): "pasted-key"}),
        http_json=http,
    )
    assert result.state.value == "ready"
    assert http.calls[0][2]["Authorization"] == "Bearer pasted-key"
