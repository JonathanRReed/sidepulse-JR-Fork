

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

    def opener(request, timeout):
        seen_requests.append(json_module.loads(request.data.decode("utf-8")))
        return _FakeResponse(
            json_module.dumps(
                {
                    "access_token": "minted",
                    "refresh_token": "rotated",
                    "expires_in": 3600,
                }
            ).encode("utf-8")
        )

    new_payload, credential = refresh_claude_payload(
        raw, now=1_000.0, opener=opener
    )
    assert seen_requests == [
        {
            "grant_type": "refresh_token",
            "refresh_token": "spendable",
            "client_id": CLAUDE_CODE_OAUTH_CLIENT_ID,
        }
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


def test_refresh_refusal_maps_to_needs_sign_in():
    from urllib.error import HTTPError

    import pytest

    from sidepulse.claude_quota import (
        CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN,
        ClaudeQuotaUnavailableError,
        refresh_claude_payload,
    )

    def opener(request, timeout):
        raise HTTPError(request.full_url, 400, "invalid_grant", None, None)

    with pytest.raises(ClaudeQuotaUnavailableError) as caught:
        refresh_claude_payload(
            '{"claudeAiOauth":{"refreshToken":"spent"}}',
            now=1_000.0,
            opener=opener,
        )
    assert str(caught.value) == CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN


def test_refresh_without_a_refresh_token_never_calls_the_network():
    import pytest

    from sidepulse.claude_quota import (
        ClaudeQuotaUnavailableError,
        refresh_claude_payload,
    )

    def opener(request, timeout):
        raise AssertionError("must not reach the network")

    with pytest.raises(ClaudeQuotaUnavailableError):
        refresh_claude_payload(
            '{"claudeAiOauth":{"accessToken":"only"}}', now=1.0, opener=opener
        )
