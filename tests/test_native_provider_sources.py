from __future__ import annotations

import base64
import json
from pathlib import Path

from sidepulse.provider_sources.antigravity import parse_quota_payload as parse_antigravity
from sidepulse.provider_sources.claude import lanes_from_windows as claude_lanes
from sidepulse.provider_sources.codex import lanes_from_windows as codex_lanes
from sidepulse.provider_sources.cursor import (
    cursor_cookie_header,
    parse_usage_summary as parse_cursor,
    read_cursor_app_token,
)
from sidepulse.provider_sources.devin import parse_quota_payload as parse_devin
from sidepulse.provider_sources.grok import parse_auth_document as parse_grok_auth
from sidepulse.provider_usage_platform import ProviderSourceState, QuotaUnit


def test_codex_preserves_dynamic_spark_lanes() -> None:
    lanes = codex_lanes(
        (
            {"label": "primary", "used_percent": 25.0, "window_minutes": 300},
            {"label": "secondary", "used_percent": 70.0, "window_minutes": 10080},
            {
                "label": "GPT-5.3-Codex-Spark",
                "used_percent": 80.0,
                "window_minutes": 10080,
            },
        )
    )
    assert [row.label for row in lanes] == ["5-hour", "Weekly", "Spark Weekly"]
    assert [row.remaining for row in lanes] == [75.0, 30.0, 20.0]
    assert lanes[-1].model == "spark"
    assert lanes[-1].bindable is False


def test_claude_preserves_unknown_model_scoped_weekly_lanes() -> None:
    lanes = claude_lanes(
        [
            {"label": "5-hour", "utilization": 10.0},
            {"label": "weekly", "utilization": 40.0},
            {"label": "Fable only", "utilization": 88.0},
        ]
    )
    assert [row.label for row in lanes] == ["5-hour", "Weekly", "Fable Weekly"]
    assert lanes[-1].model == "fable"
    assert lanes[-1].remaining == 12.0


def _jwt(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def test_cursor_app_token_uses_read_only_sqlite_and_builds_cookie(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "state.vscdb"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    token = _jwt({"sub": "auth0|user-123", "email": "me@example.com", "exp": 4_000_000_000})
    connection.execute(
        "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
        ("cursorAuth/accessToken", token),
    )
    connection.commit()
    connection.close()

    session = read_cursor_app_token(path, now=1_800_000_000.0)
    assert session is not None
    assert session.user_id == "user-123"
    assert session.email == "me@example.com"
    header = cursor_cookie_header(session)
    assert header.startswith("WorkosCursorSessionToken=user-123%3A%3A")
    assert token in header


def test_cursor_usage_summary_maps_plan_and_feature_lanes() -> None:
    snapshot = parse_cursor(
        {
            "billingCycleEnd": "2030-01-01T00:00:00Z",
            "membershipType": "pro",
            "individualUsage": {
                "plan": {
                    "used": 400,
                    "limit": 2000,
                    "remaining": 1600,
                    "totalPercentUsed": 20.0,
                    "autoPercentUsed": 10.0,
                    "apiPercentUsed": 30.0,
                },
                "onDemand": {"enabled": True, "used": 125, "limit": 500, "remaining": 375},
            },
        },
        observed_at=100.0,
    )
    assert snapshot.state is ProviderSourceState.READY
    assert [row.label for row in snapshot.lanes] == [
        "Included plan",
        "Auto + Composer",
        "API models",
        "On-demand",
    ]
    assert snapshot.lanes[-1].unit is QuotaUnit.USD
    assert snapshot.lanes[-1].remaining == 3.75


def test_devin_daily_and_weekly_payload_is_parsed() -> None:
    snapshot = parse_devin(
        {
            "daily_percentage": 0.25,
            "weekly_percentage": 75,
            "daily_reset_at": 2_000_000_000,
            "weekly_reset_at": 2_100_000_000,
            "plan_name": "team",
            "overage_balance": 12.5,
        },
        organization="org/example",
        observed_at=100.0,
    )
    assert [row.label for row in snapshot.lanes] == ["Daily", "Weekly"]
    assert [row.remaining for row in snapshot.lanes] == [75.0, 25.0]
    assert snapshot.credits == 12.5
    assert snapshot.account_label == "example"


def test_grok_auth_prefers_usable_oidc_entry() -> None:
    credential = parse_grok_auth(
        {
            "https://accounts.x.ai/sign-in": {"key": "legacy", "email": "old@example.com"},
            "https://auth.x.ai::consumer": {
                "key": "oidc-token",
                "email": "new@example.com",
                "auth_mode": "oidc",
            },
        }
    )
    assert credential is not None
    assert credential.access_token == "oidc-token"
    assert credential.account_label == "new@example.com"


def test_antigravity_dynamic_families_are_preserved() -> None:
    snapshot = parse_antigravity(
        {
            "quotaFamilies": [
                {"name": "Gemini Session", "remainingPercent": 62, "resetAt": 2_000_000_000},
                {"name": "Gemini Weekly", "remainingPercent": 31, "resetAt": 2_100_000_000},
                {"name": "Claude + GPT Weekly", "remainingPercent": 18},
            ],
            "account": "me@example.com",
        },
        observed_at=100.0,
    )
    assert [row.label for row in snapshot.lanes] == [
        "Gemini Session",
        "Gemini Weekly",
        "Claude + GPT Weekly",
    ]
    assert snapshot.account_label == "me@example.com"
