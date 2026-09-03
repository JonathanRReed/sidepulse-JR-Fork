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
    collect_opencode,
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


def test_antigravity_allows_http_loopback_and_discovers_dynamically():
    payload = {
        "response": {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "weekly",
                            "remaining": {"remainingFraction": 0.9},
                        }
                    ],
                }
            ]
        }
    }
    http = FixtureHttp([payload])
    # http:// scheme allowed
    result = collect_antigravity(
        preference("antigravity", options={"endpoint": "http://127.0.0.1:54321"}),
        observed_at=1000,
        http_json=http,
    )
    assert result.state.value == "ready"
    assert "http://127.0.0.1:54321" in http.calls[0][1]

    # Dynamic discovery via command_runner
    def runner(args, _timeout):
        if args[0] == "ps":
            return "12345 /opt/antigravity/bin/language_server --csrf_token testcsrf123\n"
        if args[0] == "lsof":
            return "language 12345 user 12u IPv4 0x1234 0t0 TCP 127.0.0.1:44556 (LISTEN)\n"
        return ""

    http2 = FixtureHttp([payload])
    res2 = collect_antigravity(
        preference("antigravity"),
        observed_at=1000,
        http_json=http2,
        command_runner=runner,
    )
    assert res2.state.value == "ready"
    assert "http://127.0.0.1:44556" in http2.calls[0][1]
    assert http2.calls[0][2]["X-Codeium-Csrf-Token"] == "testcsrf123"


def test_antigravity_multi_port_discovery_tries_candidate_ports():
    payload = {
        "response": {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "weekly",
                            "remaining": {"remainingFraction": 0.93},
                        },
                        {
                            "bucketId": "5h",
                            "remaining": {"remainingFraction": 0.58},
                        },
                    ],
                },
                {
                    "displayName": "Claude and GPT models",
                    "buckets": [
                        {
                            "bucketId": "weekly",
                            "remaining": {"remainingFraction": 0.83},
                        },
                        {
                            "bucketId": "5h",
                            "remaining": {"remainingFraction": 1.0},
                        },
                    ],
                },
            ]
        }
    }

    # Simulate port 1 failing (e.g. 400 HTTPS error) and port 2 succeeding
    def http_fail_then_succeed(method, url, **kwargs):
        if "59237" in url:
            raise ProviderHttpError(400, "Bad Request: Client sent an HTTP request to an HTTPS server")
        return payload

    def runner(args, _timeout):
        if args[0] == "ps":
            return "31583 /Applications/Antigravity.app/Contents/Resources/bin/language_server --csrf_token csrfABC\n"
        if args[0] == "lsof":
            return "ls 31583 user 12u IPv4 0x1 0t0 TCP 127.0.0.1:59237 (LISTEN)\nls 31583 user 13u IPv4 0x2 0t0 TCP 127.0.0.1:59238 (LISTEN)\n"
        return ""

    result = collect_antigravity(
        preference("antigravity"),
        observed_at=1000,
        http_json=http_fail_then_succeed,
        command_runner=runner,
    )
    assert result.state.value == "ready"
    assert len(result.lanes) == 4
    labels = [l.label for l in result.lanes]
    assert "Gemini Weekly" in labels
    assert "Gemini 5-Hour" in labels
    assert "Claude + GPT Weekly" in labels
    assert "Claude + GPT 5-Hour" in labels


def test_antigravity_endpoint_cache_reuses_live_process():
    import os
    import sidepulse.provider_usage_collectors as puc

    puc._cached_antigravity_connection.clear()
    puc._cached_antigravity_connection["endpoint"] = "http://127.0.0.1:9999"
    puc._cached_antigravity_connection["csrf"] = "token-123"
    puc._cached_antigravity_connection["pid"] = os.getpid()

    called_urls = []
    def mock_http(method, url, **kwargs):
        called_urls.append(url)
        return {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "displayName": "Weekly",
                            "bucketId": "weekly",
                            "remaining": {"remainingFraction": 0.9},
                        }
                    ],
                }
            ]
        }

    res = puc.collect_antigravity(
        preference("antigravity"),
        observed_at=1000,
        http_json=mock_http,
    )
    assert res.state.value == "ready"
    assert len(called_urls) == 1
    assert "http://127.0.0.1:9999" in called_urls[0]
    puc._cached_antigravity_connection.clear()


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


def test_devin_browser_setting_requires_an_explicit_import_before_collection():
    http = FixtureHttp([])
    result = collect_devin(
        preference("devin", browser_sources=True),
        observed_at=1000,
        credentials=FixtureCredentials({}),
        http_json=http,
    )
    assert result.state.value == "source_not_found"
    assert result.action_label == "Import Devin browser session"
    assert http.calls == []


def test_devin_without_browser_access_still_asks_for_consent_first():
    result = collect_devin(
        preference("devin", browser_sources=False),
        observed_at=1000,
        credentials=FixtureCredentials({}),
        http_json=FixtureHttp([]),
    )
    assert result.state.value == "needs_consent"
    assert result.action_label == "Enable Devin browser access"


def test_devin_uses_only_the_explicitly_imported_stored_session():
    http = FixtureHttp([{"daily_percentage": 10}])
    result = collect_devin(
        preference(
            "devin",
            browser_sources=True,
            options={"organization_id": "org-abc12345"},
        ),
        observed_at=1000,
        credentials=FixtureCredentials({("devin", "token"): "auth1_exact_import"}),
        http_json=http,
    )
    assert result.state.value == "ready"
    assert http.calls[0][2]["Authorization"] == "Bearer auth1_exact_import"


def test_devin_uses_a_manual_stored_token_when_browser_sources_are_enabled():
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


def test_codex_reading_that_stopped_moving_is_reported_stale():
    """Reported live as "why does it say 48 percent, it should be around
    96": the 48 was computed from a rollout written three days earlier.
    Codex quota is only as fresh as the newest rollout, and usage burned
    elsewhere is invisible here, so a frozen reading must say so."""
    from sidepulse.provider_usage_codex_claude import (
        CODEX_READING_STALE_SECONDS,
        collect_codex,
    )

    now = 1_000_000.0

    def scan(_home, _observed_at):
        return {
            "windows": [
                {"label": "primary", "window_minutes": 10080, "used_percent": 52.0}
            ],
            "windows_observed_at": now - CODEX_READING_STALE_SECONDS - 60.0,
        }

    result = collect_codex(
        preference("codex"), home=Path("/tmp"), observed_at=now, local_scanner=scan
    )
    assert result.state.value == "stale"
    assert result.reason_code == "local_reading_stale"
    # The number is still shown -- it is the newest thing known, just old.
    assert result.lanes[0].remaining_percent == 48.0
    assert "ago" in result.action_label


def test_a_fresh_codex_reading_is_not_flagged():
    from sidepulse.provider_usage_codex_claude import collect_codex

    now = 1_000_000.0

    def scan(_home, _observed_at):
        return {
            "windows": [
                {"label": "primary", "window_minutes": 10080, "used_percent": 52.0}
            ],
            "windows_observed_at": now - 60.0,
        }

    result = collect_codex(
        preference("codex"), home=Path("/tmp"), observed_at=now, local_scanner=scan
    )
    assert result.state.value == "ready"
    assert result.action_label is None


def test_antigravity_cli_fallback_when_server_not_running(tmp_path: Path):
    gemini_dir = tmp_path / ".gemini"
    gemini_dir.mkdir(parents=True)
    creds_file = gemini_dir / "oauth_creds.json"
    creds_file.write_text(json.dumps({"email": "testuser@example.com"}), encoding="utf-8")

    result = collect_antigravity(
        preference("antigravity"),
        observed_at=1000,
        command_runner=lambda _args, _timeout: "",
        home=tmp_path,
    )
    assert result.state.value == "ready"
    assert result.account_label == "testuser@example.com"
    assert len(result.lanes) == 1
    assert result.lanes[0].label == "Antigravity CLI"


def test_opencode_collector_detects_free_tier_and_tokens(tmp_path: Path):
    root = tmp_path / ".local" / "share" / "opencode"
    root.mkdir(parents=True)
    auth_file = root / "auth.json"
    auth_file.write_text(json.dumps({"github-copilot": {"token": "test"}}), encoding="utf-8")
    db_file = root / "opencode.db"
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE session (tokens_input INT, tokens_output INT, model TEXT)")
    con.execute("INSERT INTO session VALUES (500, 100, 'muse-spark')")
    con.execute("CREATE TABLE message (id TEXT, session_id TEXT, time_created INT, data TEXT)")
    err_json = json.dumps({"error": {"type": "FreeUsageLimitError"}})
    observed = 1_700_000_000.0
    err_time_ms = int((observed - 600.0) * 1000.0)
    con.execute("INSERT INTO message VALUES ('m1', 's1', ?, ?)", (err_time_ms, err_json))
    con.commit()
    con.close()

    result = collect_opencode(
        preference("opencode"),
        observed_at=observed,
        home=tmp_path,
    )
    assert result.state.value == "rate_limited"
    assert result.account_label == "github-copilot"
    assert result.input_tokens == 500
    assert result.output_tokens == 100
    assert result.lanes[0].remaining_percent < 100.0
