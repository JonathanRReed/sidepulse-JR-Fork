"""The live Claude quota read.

Fixture payloads are the real endpoint's shape, including the model-tier
sub-caps the owner specifically wants visible (the weekly Opus allowance
inside the overall weekly limit).
"""

from __future__ import annotations

import io
import json

import pytest

from sidepulse import claude_quota
from sidepulse.claude_quota import (
    ClaudeQuotaUnavailableError,
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


def _requester(payload, status=200):
    """A stand-in for request_via_apple_stack: (status, body) in, out."""
    captured = {}

    def requester(url, *, method, headers, body=None, timeout):
        captured.update(
            url=url, method=method, headers=headers, body=body, timeout=timeout
        )
        return status, json.dumps(payload).encode("utf-8")

    requester.captured = captured
    return requester


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
    requester = _requester(USAGE_PAYLOAD)
    windows = fetch_windows(access_token="tok", requester=requester)

    labels = [window["label"] for window in windows]
    assert "5-hour" in labels and "weekly" in labels
    assert "Opus only" in labels, "the model-tier sub-cap was dropped"
    by_label = {window["label"]: window for window in windows}
    assert by_label["5-hour"]["utilization"] == pytest.approx(42.5)
    assert by_label["Opus only"]["utilization"] == pytest.approx(88.0)


def test_request_presents_the_expected_contract() -> None:
    requester = _requester(USAGE_PAYLOAD)
    fetch_windows(access_token="tok-123", requester=requester)

    captured = requester.captured
    assert captured["url"] == claude_quota.CLAUDE_USAGE_URL
    assert captured["method"] == "GET"
    headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert headers["authorization"] == "Bearer tok-123"
    assert headers["anthropic-beta"] == claude_quota.CLAUDE_OAUTH_BETA_HEADER
    assert headers["user-agent"].startswith("claude-code/")


def test_the_usage_read_does_not_ride_urllib() -> None:
    """It carries a bearer token: it belongs on the same guarded,
    same-origin transport as the token request, not on the client
    Cloudflare fingerprints."""
    import inspect

    source = inspect.getsource(claude_quota.fetch_windows)
    assert "urllib" not in source
    assert "request_via_apple_stack" in source


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, claude_quota.CLAUDE_REMOTE_QUOTA_UNAUTHORIZED),
        (429, claude_quota.CLAUDE_REMOTE_QUOTA_RATE_LIMITED),
        (500, claude_quota.CLAUDE_REMOTE_QUOTA_SERVER_ERROR),
    ],
)
def test_http_failures_map_to_reason_codes(code: int, expected: str) -> None:
    def requester(url, *, method, headers, body=None, timeout):
        return code, b"boom"

    with pytest.raises(ClaudeQuotaUnavailableError) as excinfo:
        fetch_windows(access_token="tok", requester=requester)
    assert str(excinfo.value) == expected


def test_network_failure_is_a_code_not_a_traceback() -> None:
    def requester(url, *, method, headers, body=None, timeout):
        raise OSError("no route to host")

    with pytest.raises(ClaudeQuotaUnavailableError) as excinfo:
        fetch_windows(access_token="tok", requester=requester)
    assert str(excinfo.value) == claude_quota.CLAUDE_REMOTE_QUOTA_NETWORK


def test_a_server_body_never_reaches_the_error_text() -> None:
    """Error strings surface in the UI and in doctor output."""
    secret_ish = "account_id=acct_0xdeadbeef owner=someone@example.com"

    def requester(url, *, method, headers, body=None, timeout):
        return 500, secret_ish.encode()

    with pytest.raises(ClaudeQuotaUnavailableError) as excinfo:
        fetch_windows(access_token="tok", requester=requester)
    assert "acct_0xdeadbeef" not in str(excinfo.value)
    assert "example.com" not in str(excinfo.value)


def test_oversized_response_is_refused() -> None:
    def requester(url, *, method, headers, body=None, timeout):
        return 200, b"{" + b"x" * (claude_quota.CLAUDE_USAGE_MAX_BYTES + 10)

    with pytest.raises(ClaudeQuotaUnavailableError):
        fetch_windows(access_token="tok", requester=requester)


def test_empty_payload_is_reported_not_silently_empty() -> None:
    with pytest.raises(ClaudeQuotaUnavailableError) as excinfo:
        fetch_windows(access_token="tok", requester=_requester({}))
    assert str(excinfo.value) == claude_quota.CLAUDE_REMOTE_QUOTA_NO_WINDOWS
