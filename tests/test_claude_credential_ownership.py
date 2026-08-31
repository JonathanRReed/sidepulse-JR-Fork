"""Claude Code owns its credential; JR Bar is a read-only consumer."""

from __future__ import annotations

import inspect
import json


class FakeStore:
    def __init__(self, secrets=None):
        self.secrets = dict(secrets or {})
        self.stored: list[tuple[str, str]] = []

    def get(self, provider_id, account):
        secret = self.secrets.get((provider_id, account))

        class Read:
            available = secret is not None

        read = Read()
        read.secret = secret
        return read

    def set(self, provider_id, account, secret):
        self.secrets[(provider_id, account)] = secret
        self.stored.append((provider_id, account))


def _payload(*, access: str, expires_at: float, refresh: str) -> str:
    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": access,
                "expiresAt": expires_at,
                "refreshToken": refresh,
            }
        }
    )


def test_credentials_has_no_third_party_keychain_write_surface():
    from sidepulse import credentials

    source = inspect.getsource(credentials)
    assert not hasattr(credentials, "write_keychain_secret")
    assert not hasattr(credentials, "keychain_account_for")
    assert "add-generic-password" not in source


def test_expired_claude_credential_never_reaches_network_process_or_store(
    monkeypatch,
):
    from sidepulse import claude_quota, credentials
    from sidepulse.provider_reconnect import RepairOutcome, repair_claude_credential

    calls: list[str] = []

    def network_sink(*_args, **_kwargs):
        calls.append("network")
        raise AssertionError("JR Bar must not consume Claude Code's refresh token")

    def process_sink(*_args, **_kwargs):
        calls.append("process")
        raise AssertionError("JR Bar must not write Claude Code's Keychain item")

    monkeypatch.setattr(
        claude_quota, "refresh_claude_payload", network_sink, raising=False
    )
    monkeypatch.setattr(credentials.subprocess, "run", process_sink)
    store = FakeStore()
    secret = "refresh-credential-that-must-stay-with-claude-code"

    result = repair_claude_credential(
        store,
        now=2_000.0,
        keychain_payload_reader=lambda: _payload(
            access="expired-access", expires_at=1_000.0, refresh=secret
        ),
    )

    assert result.outcome is RepairOutcome.NEEDS_PROVIDER_REFRESH
    assert result.changed is False
    assert "Claude Code" in result.message
    assert "run `claude`" in result.message.lower()
    assert secret not in result.message
    assert calls == []
    assert store.stored == []


def test_hostile_expired_payload_cannot_become_an_argument_or_stored_secret(
    monkeypatch,
):
    from sidepulse import claude_quota, credentials
    from sidepulse.provider_reconnect import RepairOutcome, repair_claude_credential

    calls: list[object] = []
    monkeypatch.setattr(
        claude_quota,
        "refresh_claude_payload",
        lambda *_args, **_kwargs: calls.append("network"),
        raising=False,
    )
    monkeypatch.setattr(
        credentials.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    hostile = "-w" + "very-long-secret" * 128
    store = FakeStore()

    result = repair_claude_credential(
        store,
        now=2_000.0,
        keychain_payload_reader=lambda: _payload(
            access=hostile, expires_at=1_000.0, refresh=hostile
        ),
    )

    assert result.outcome is RepairOutcome.NEEDS_PROVIDER_REFRESH
    assert hostile not in result.message
    assert calls == []
    assert store.secrets == {}


def test_valid_claude_access_token_is_copied_into_jr_bar_store_with_expiry():
    from sidepulse.provider_reconnect import RepairOutcome, repair_claude_credential

    store = FakeStore()
    result = repair_claude_credential(
        store,
        now=1_000.0,
        keychain_payload_reader=lambda: _payload(
            access="current-access-token", expires_at=2_000_000.0, refresh="unused"
        ),
    )

    assert result.outcome is RepairOutcome.REPAIRED
    assert result.changed is True
    assert store.secrets[("claude", "oauth-token")] == "current-access-token"
    assert store.secrets[("claude", "oauth-expires-at")] == "2000000"


def test_background_sync_reads_under_standing_grant_and_only_copies_access_token(
    tmp_path, monkeypatch
):
    from sidepulse import credentials, providers
    from sidepulse.credentials import CredentialOutcome, CredentialResult
    from sidepulse.provider_reconnect import sync_claude_credential_in_background

    payload = _payload(
        access="new-external-token", expires_at=2_000_000.0, refresh="not-consumed"
    )
    seen: list[tuple[bool, str]] = []

    def read(item, *, allow_prompt, ledger):
        seen.append((allow_prompt, item.service))
        assert ledger.path == tmp_path / "keychain-consent.json"
        return CredentialResult(CredentialOutcome.OK, secret=payload)

    monkeypatch.setattr(credentials, "read_keychain_secret", read)
    monkeypatch.setattr(providers, "default_state_dir", lambda: tmp_path)
    store = FakeStore()

    assert sync_claude_credential_in_background(store, home=tmp_path, now=1_000.0)
    assert seen == [(False, "Claude Code-credentials")]
    assert store.secrets[("claude", "oauth-token")] == "new-external-token"


def test_runtime_terminal_gate_has_no_claude_credential_mutation_bypass():
    from sidepulse import provider_usage_runtime

    source = inspect.getsource(provider_usage_runtime.ProviderUsageService._run_refresh)
    assert "renew_claude_credential_in_background" not in source
    assert "sync_claude_credential_in_background" not in source
