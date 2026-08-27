

# --- OAuth renewal (the CodexBar contract) ---------------------------------


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self, n: int) -> bytes:
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_refresh_rebuilds_the_payload_and_preserves_unknown_fields():
    import json as json_module

    from sidepulse.claude_quota import (
        CLAUDE_CODE_OAUTH_CLIENT_ID,
        refresh_claude_payload,
    )

    raw = json_module.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "old",
                "refreshToken": "spendable",
                "expiresAt": 1,
                "subscriptionType": "max",
                "scopes": ["user:inference"],
            },
            "somethingClaudeCodeCaresAbout": True,
        }
    )
    seen_requests = []

    def poster(url, fields, *, timeout):
        seen_requests.append((url, dict(fields)))
        return 200, json_module.dumps(
            {
                "access_token": "minted",
                "refresh_token": "rotated",
                "expires_in": 3600,
            }
        ).encode("utf-8")

    new_payload, credential = refresh_claude_payload(
        raw, now=1_000.0, poster=poster
    )
    assert seen_requests == [
        (
            "https://platform.claude.com/v1/oauth/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": "spendable",
                "client_id": CLAUDE_CODE_OAUTH_CLIENT_ID,
            },
        )
    ]
    rebuilt = json_module.loads(new_payload)
    oauth = rebuilt["claudeAiOauth"]
    assert oauth["accessToken"] == "minted"
    assert oauth["refreshToken"] == "rotated"
    assert oauth["expiresAt"] == 4_600_000  # ms, Claude Code's own unit
    assert oauth["scopes"] == ["user:inference"], "unknown fields survive"
    assert rebuilt["somethingClaudeCodeCaresAbout"] is True
    assert credential.access_token == "minted"
    assert credential.expires_at == 4_600.0
    assert credential.subscription_type == "max"


def test_a_spent_refresh_token_maps_to_needs_sign_in():
    import pytest

    from sidepulse.claude_quota import (
        CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN,
        ClaudeQuotaUnavailableError,
        refresh_claude_payload,
    )

    with pytest.raises(ClaudeQuotaUnavailableError) as caught:
        refresh_claude_payload(
            '{"claudeAiOauth":{"refreshToken":"spent"}}',
            now=1_000.0,
            poster=lambda url, fields, *, timeout: (400, b'{"error":"invalid_grant"}'),
        )
    assert str(caught.value) == CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN


def test_a_blocked_client_is_never_mistaken_for_a_dead_credential():
    """Cloudflare answers a fingerprinted client with 403/429. Neither
    says anything about the SIGN-IN, and treating them as invalid_grant
    is what sent the owner to a terminal for a network problem."""
    import pytest

    from sidepulse.claude_quota import (
        CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN,
        CLAUDE_REMOTE_QUOTA_RATE_LIMITED,
        ClaudeQuotaUnavailableError,
        refresh_claude_payload,
    )

    for status, expected in ((429, CLAUDE_REMOTE_QUOTA_RATE_LIMITED), (403, None)):
        with pytest.raises(ClaudeQuotaUnavailableError) as caught:
            refresh_claude_payload(
                '{"claudeAiOauth":{"refreshToken":"fine"}}',
                now=1_000.0,
                poster=lambda url, fields, *, timeout, _s=status: (_s, b"blocked"),
            )
        assert str(caught.value) != CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN
        if expected:
            assert str(caught.value) == expected


def test_refresh_without_a_refresh_token_never_calls_the_network():
    import pytest

    from sidepulse.claude_quota import (
        ClaudeQuotaUnavailableError,
        refresh_claude_payload,
    )

    def poster(url, fields, *, timeout):
        raise AssertionError("must not reach the network")

    with pytest.raises(ClaudeQuotaUnavailableError):
        refresh_claude_payload(
            '{"claudeAiOauth":{"accessToken":"only"}}', now=1.0, poster=poster
        )


def test_the_token_request_is_form_encoded_through_the_apple_stack():
    """Source-level contract: JSON over urllib is what Cloudflare bans."""
    import inspect

    from sidepulse import claude_quota

    source = inspect.getsource(claude_quota.post_form_via_apple_stack)
    assert "NSURLSession" in source
    assert "application/x-www-form-urlencoded" in source


def test_only_invalid_grant_is_terminal_on_the_token_endpoint():
    """400/401 also carry invalid_request / invalid_client -- transient
    faults that say nothing about the sign-in. Treating every 400 as
    'go re-login' wedges the provider behind a terminal gate."""
    import pytest

    from sidepulse.claude_quota import (
        CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN,
        CLAUDE_REMOTE_QUOTA_REFRESH_REJECTED,
        ClaudeQuotaUnavailableError,
        refresh_claude_payload,
    )

    cases = (
        (400, b'{"error":"invalid_grant"}', CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN),
        (400, b'{"error":"invalid_request"}', CLAUDE_REMOTE_QUOTA_REFRESH_REJECTED),
        (401, b'{"error":"invalid_client"}', CLAUDE_REMOTE_QUOTA_REFRESH_REJECTED),
        (400, b"not json at all", CLAUDE_REMOTE_QUOTA_REFRESH_REJECTED),
        (400, b'{"error":{"type":"invalid_grant"}}', CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN),
    )
    for status, body, expected in cases:
        with pytest.raises(ClaudeQuotaUnavailableError) as caught:
            refresh_claude_payload(
                '{"claudeAiOauth":{"refreshToken":"tok"}}',
                now=1_000.0,
                poster=lambda url, fields, *, timeout, _s=status, _b=body: (_s, _b),
            )
        assert str(caught.value) == expected, (status, body)


def test_a_refresh_without_a_lifetime_is_refused():
    """Accepting it would write back the OLD expiresAt, read as expired
    next cycle, and rotate the refresh token every pass forever."""
    import json as json_module

    import pytest

    from sidepulse.claude_quota import (
        ClaudeQuotaUnavailableError,
        refresh_claude_payload,
    )

    body = json_module.dumps({"access_token": "minted"}).encode()
    with pytest.raises(ClaudeQuotaUnavailableError):
        refresh_claude_payload(
            '{"claudeAiOauth":{"refreshToken":"tok","expiresAt":1}}',
            now=1_000.0,
            poster=lambda url, fields, *, timeout: (200, body),
        )


def test_the_redirect_guard_refuses_a_cross_origin_hop():
    """A 30x to another host would hand the Authorization header to
    whoever answered it. Ported from CodexBar's ProviderHTTPClient."""
    from types import SimpleNamespace

    from sidepulse.claude_quota import _redirect_guard_class

    guard = _redirect_guard_class().alloc().initWithOrigin_(
        ("https", "platform.claude.com", None)
    )
    answers = []

    def _request(scheme, host):
        return SimpleNamespace(
            URL=lambda: SimpleNamespace(
                scheme=lambda: scheme, host=lambda: host, port=lambda: None
            )
        )

    same = _request("https", "platform.claude.com")
    guard.URLSession_task_willPerformHTTPRedirection_newRequest_completionHandler_(
        None, None, None, same, answers.append
    )
    assert answers[-1] is same, "a same-origin redirect is allowed"

    guard.URLSession_task_willPerformHTTPRedirection_newRequest_completionHandler_(
        None, None, None, _request("https", "evil.example.com"), answers.append
    )
    assert answers[-1] is None, "a cross-origin redirect is refused"

    guard.URLSession_task_willPerformHTTPRedirection_newRequest_completionHandler_(
        None, None, None, _request("http", "platform.claude.com"), answers.append
    )
    assert answers[-1] is None, "a downgrade to http is refused"
