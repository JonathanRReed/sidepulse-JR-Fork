"""The live Claude quota read.

Fixture payloads are the real endpoint's shape, including the model-tier
sub-caps the owner specifically wants visible (the weekly Opus allowance
inside the overall weekly limit).
"""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from sidepulse import claude_quota
from sidepulse.claude_quota import (
    ClaudeQuotaUnavailableError,
    credential_from_keychain_payload,
    fetch_windows,
)


class _Response(io.BytesIO):
    def __init__(self, payload: object, status: int = 200) -> None:
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _opener(payload: object, status: int = 200):
    captured: dict = {}

    def opener(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(payload, status)

    opener.captured = captured
    return opener


USAGE_PAYLOAD = {
    "five_hour": {"utilization": 42.5, "resets_at": "2026-08-14T12:00:00Z"},
    "seven_day": {"utilization": 61.0, "resets_at": "2026-08-18T00:00:00Z"},
    "seven_day_opus": {"utilization": 88.0, "resets_at": "2026-08-18T00:00:00Z"},
}


def test_no_credential_still_fails_closed() -> None:
    """The pre-existing zero-argument contract is preserved."""
    with pytest.raises(ClaudeQuotaUnavailableError) as excinfo:
        fetch_windows()
    assert str(excinfo.value) == claude_quota.CLAUDE_REMOTE_QUOTA_UNSUPPORTED


def test_live_read_returns_every_window_including_the_model_subcap() -> None:
    opener = _opener(USAGE_PAYLOAD)
    windows = fetch_windows(access_token="tok", opener=opener)

    labels = [window["label"] for window in windows]
    assert "5-hour" in labels and "weekly" in labels
    assert "Opus only" in labels, "the model-tier sub-cap was dropped"
    by_label = {window["label"]: window for window in windows}
    assert by_label["5-hour"]["utilization"] == pytest.approx(42.5)
    assert by_label["Opus only"]["utilization"] == pytest.approx(88.0)


def test_request_presents_the_expected_contract() -> None:
    opener = _opener(USAGE_PAYLOAD)
    fetch_windows(access_token="tok-123", opener=opener)

    request = opener.captured["request"]
    assert request.full_url == claude_quota.CLAUDE_USAGE_URL
    assert request.get_method() == "GET"
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["authorization"] == "Bearer tok-123"
    assert headers["anthropic-beta"] == claude_quota.CLAUDE_OAUTH_BETA_HEADER
    assert headers["user-agent"].startswith("claude-code/")


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, claude_quota.CLAUDE_REMOTE_QUOTA_UNAUTHORIZED),
        (429, claude_quota.CLAUDE_REMOTE_QUOTA_RATE_LIMITED),
        (500, claude_quota.CLAUDE_REMOTE_QUOTA_SERVER_ERROR),
    ],
)
def test_http_failures_map_to_reason_codes(code: int, expected: str) -> None:
    def opener(request, timeout):
        raise HTTPError(request.full_url, code, "boom", {}, None)

    with pytest.raises(ClaudeQuotaUnavailableError) as excinfo:
        fetch_windows(access_token="tok", opener=opener)
    assert str(excinfo.value) == expected


def test_network_failure_is_a_code_not_a_traceback() -> None:
    def opener(request, timeout):
        raise URLError("no route to host")

    with pytest.raises(ClaudeQuotaUnavailableError) as excinfo:
        fetch_windows(access_token="tok", opener=opener)
    assert str(excinfo.value) == claude_quota.CLAUDE_REMOTE_QUOTA_NETWORK


def test_a_server_body_never_reaches_the_error_text() -> None:
    """Error strings surface in the UI and in doctor output."""
    secret_ish = "account_id=acct_0xdeadbeef owner=someone@example.com"

    def opener(request, timeout):
        raise HTTPError(request.full_url, 500, secret_ish, {}, io.BytesIO(secret_ish.encode()))

    with pytest.raises(ClaudeQuotaUnavailableError) as excinfo:
        fetch_windows(access_token="tok", opener=opener)
    assert "acct_0xdeadbeef" not in str(excinfo.value)
    assert "example.com" not in str(excinfo.value)


def test_oversized_response_is_refused() -> None:
    class Huge(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout):
        return Huge(b"{" + b"x" * (claude_quota.CLAUDE_USAGE_MAX_BYTES + 10))

    with pytest.raises(ClaudeQuotaUnavailableError):
        fetch_windows(access_token="tok", opener=opener)


def test_empty_payload_is_reported_not_silently_empty() -> None:
    with pytest.raises(ClaudeQuotaUnavailableError) as excinfo:
        fetch_windows(access_token="tok", opener=_opener({}))
    assert str(excinfo.value) == claude_quota.CLAUDE_REMOTE_QUOTA_NO_WINDOWS


def test_keychain_payload_parses_claude_codes_own_shape() -> None:
    raw = json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "at-value",
                "refreshToken": "rt-value",
                "expiresAt": 1_800_000_000_000,  # milliseconds
                "subscriptionType": "max",
            }
        }
    )
    credential = credential_from_keychain_payload(raw)
    assert credential is not None
    assert credential.access_token == "at-value"
    assert credential.subscription_type == "max"
    assert credential.expires_at == pytest.approx(1_800_000_000.0)
    assert "at-value" not in repr(credential), "token leaked into repr"


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "{}", '{"claudeAiOauth": {}}', '{"claudeAiOauth": {"accessToken": ""}}', "[]"],
)
def test_unusable_keychain_payloads_are_absence(raw: str) -> None:
    assert credential_from_keychain_payload(raw) is None


def test_expiry_is_honoured() -> None:
    raw = json.dumps({"claudeAiOauth": {"accessToken": "at", "expiresAt": 1_000_000_000_000}})
    credential = credential_from_keychain_payload(raw)
    assert credential is not None
    assert credential.is_expired(1_000_000_001.0)
    assert not credential.is_expired(999_999_999.0)


def test_a_refresh_only_credential_is_named_not_shrugged_at() -> None:
    """Observed live: accessToken empty, expiresAt 0, refreshToken present.

    Claude Code mints access tokens on demand. We must NOT mint one
    ourselves -- the refresh token rotates on use, so doing so would
    invalidate Claude Code's copy and break the user's `claude` login. The
    correct behaviour is to recognise the state and say so.
    """
    raw = json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "",
                "refreshToken": "rt-value",
                "expiresAt": 0,
                "subscriptionType": "max",
            }
        }
    )
    assert claude_quota.credential_from_keychain_payload(raw) is None
    assert claude_quota.credential_needs_sign_in(raw) is True


def test_a_healthy_credential_does_not_ask_for_sign_in() -> None:
    raw = json.dumps(
        {"claudeAiOauth": {"accessToken": "at", "refreshToken": "rt", "expiresAt": 0}}
    )
    assert claude_quota.credential_needs_sign_in(raw) is False


@pytest.mark.parametrize("raw", ["", "not json", "{}", "[]", '{"claudeAiOauth": {}}'])
def test_absent_credentials_are_not_a_sign_in_prompt(raw: str) -> None:
    """No credential at all is "not connected", not "your session expired"."""
    assert claude_quota.credential_needs_sign_in(raw) is False
