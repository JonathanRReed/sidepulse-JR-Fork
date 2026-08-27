"""provider_reconnect: honest repair results, failure gates, transitions."""

from __future__ import annotations

import json

from sidepulse.provider_reconnect import (
    TRANSIENT_BACKOFF_SECONDS,
    FailureGate,
    RepairOutcome,
    codex_activity_report,
    codex_app_server_probe,
    connection_loss_transitions,
    credential_fingerprint,
    grok_auth_status,
    newest_codex_rollout_age,
    note_failure,
    repair_claude_credential,
    repair_grok_credential,
    should_collect,
)


class FakeStore:
    def __init__(self, secrets=None):
        self.secrets = dict(secrets or {})
        self.deleted = []
        self.stored = []

    def get(self, provider_id, account):
        secret = self.secrets.get((provider_id, account))

        class Read:
            pass

        read = Read()
        read.available = secret is not None
        read.secret = secret
        return read

    def set(self, provider_id, account, secret):
        self.secrets[(provider_id, account)] = secret
        self.stored.append((provider_id, account))

    def delete(self, provider_id, account):
        self.deleted.append((provider_id, account))
        return self.secrets.pop((provider_id, account), None) is not None


def write_grok_auth(home, *, expires_at=None, email="jr@example.com"):
    grok_dir = home / ".grok"
    grok_dir.mkdir(parents=True, exist_ok=True)
    entry = {"key": "tok-" + "x" * 24, "email": email}
    if expires_at is not None:
        entry["expires_at"] = expires_at
    (grok_dir / "auth.json").write_text(
        json.dumps({"https://auth.x.ai::abc": entry}), encoding="utf-8"
    )


# --- grok ------------------------------------------------------------------


def test_grok_auth_status_live_expired_missing(tmp_path):
    assert grok_auth_status(tmp_path, 1000.0)[0] == "missing"
    write_grok_auth(tmp_path, expires_at=500.0)
    assert grok_auth_status(tmp_path, 1000.0)[0] == "expired"
    write_grok_auth(tmp_path, expires_at=2000.0)
    status, email = grok_auth_status(tmp_path, 1000.0)
    assert status == "ok"
    assert email == "jr@example.com"


def test_repair_grok_clears_wedged_token_when_cli_is_signed_in(tmp_path):
    write_grok_auth(tmp_path)
    store = FakeStore({("grok", "token"): "stale-stored-token-000000"})
    result = repair_grok_credential(store, home=tmp_path, now=1000.0)
    assert result.outcome is RepairOutcome.REPAIRED
    assert result.changed
    assert ("grok", "token") in store.deleted
    assert "signed in" in result.message


def test_repair_grok_expired_names_grok_login(tmp_path):
    write_grok_auth(tmp_path, expires_at=1.0)
    store = FakeStore()
    result = repair_grok_credential(store, home=tmp_path, now=1000.0)
    assert result.outcome is RepairOutcome.NEEDS_SIGN_IN
    assert "grok login" in result.message


# --- claude ----------------------------------------------------------------


def claude_payload(*, access="tok", expires_at=None, refresh="refresh-token"):
    oauth = {"accessToken": access, "refreshToken": refresh}
    if expires_at is not None:
        oauth["expiresAt"] = expires_at
    return json.dumps({"claudeAiOauth": oauth})


def _failing_refresher(raw, *, now):
    from sidepulse.claude_quota import ClaudeQuotaUnavailableError

    raise ClaudeQuotaUnavailableError("claude_remote_quota_network")


def test_repair_claude_renews_an_expired_token_and_writes_back():
    """The CodexBar contract: refresh with Claude Code's client, write
    the ROTATED tokens back so `claude` itself keeps working."""
    from sidepulse.claude_quota import ClaudeOAuthCredential

    store = FakeStore()
    written = []

    def refresher(raw, *, now):
        return (
            '{"claudeAiOauth":{"accessToken":"fresh"}}',
            ClaudeOAuthCredential(access_token="fresh", expires_at=now + 3600),
        )

    result = repair_claude_credential(
        store,
        now=2_000_000_000.0,
        keychain_payload_reader=lambda: claude_payload(
            access="a" * 32, expires_at=1_000_000_000.0
        ),
        keychain_writer=lambda payload: (written.append(payload), True)[1],
        refresher=refresher,
    )
    assert result.outcome is RepairOutcome.REPAIRED
    assert result.changed
    assert written == ['{"claudeAiOauth":{"accessToken":"fresh"}}'], (
        "write-back precedes everything; without it Claude Code is "
        "stranded on a spent refresh token"
    )
    assert store.secrets[("claude", "oauth-token")] == "fresh"


def test_repair_claude_keeps_the_token_when_write_back_fails():
    """A successful refresh already consumed the old refresh token --
    dropping the new one would strand BOTH apps. Keep ours, say so."""
    from sidepulse.claude_quota import ClaudeOAuthCredential

    store = FakeStore()
    result = repair_claude_credential(
        store,
        now=2_000_000_000.0,
        keychain_payload_reader=lambda: claude_payload(
            access="a" * 32, expires_at=1_000_000_000.0
        ),
        keychain_writer=lambda payload: False,
        refresher=lambda raw, *, now: (
            "{}",
            ClaudeOAuthCredential(access_token="fresh"),
        ),
    )
    assert result.outcome is RepairOutcome.REPAIRED
    assert store.secrets[("claude", "oauth-token")] == "fresh"
    assert "Keychain failed" in result.message


def test_a_rate_limited_renewal_never_reads_as_a_dead_sign_in():
    """429 is the token endpoint saying 'later', not 'sign in again' --
    the owner was sent to a terminal for a transient limit while their
    refresh token still had 20 days on it (2026-08-27)."""
    from sidepulse.provider_reconnect import note_claude_renewal

    note_claude_renewal(now=0.0, succeeded=True)  # clear any backoff
    store = FakeStore()
    result = repair_claude_credential(
        store,
        now=2_000_000_000.0,
        keychain_payload_reader=lambda: claude_payload(
            access="a" * 32, expires_at=1_000_000_000.0
        ),
        keychain_writer=lambda payload: True,
        refresher=_failing_refresher,
    )
    assert result.outcome is RepairOutcome.UNAVAILABLE
    assert not store.stored
    assert "rate limited" in result.message
    assert "sign in" not in result.message.lower()


def test_a_spent_refresh_token_does_ask_for_a_sign_in():
    from sidepulse.claude_quota import (
        CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN,
        ClaudeQuotaUnavailableError,
    )

    def refused(raw, *, now):
        raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_NEEDS_SIGN_IN)

    store = FakeStore()
    payload = json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "a" * 32,
                "refreshToken": "spent",
                "expiresAt": 1_000_000_000_000,
                "refreshTokenExpiresAt": 1_500_000_000_000,
            }
        }
    )
    result = repair_claude_credential(
        store,
        now=2_000_000_000.0,
        keychain_payload_reader=lambda: payload,
        keychain_writer=lambda payload: True,
        refresher=refused,
    )
    assert result.outcome is RepairOutcome.NEEDS_SIGN_IN
    assert "fully expired" in result.message


def test_renewal_backs_off_instead_of_hammering_the_token_endpoint():
    """A renewal on every collect cycle is what produced the 429 wall."""
    from sidepulse.provider_reconnect import (
        claude_renewal_allowed,
        note_claude_renewal,
    )

    note_claude_renewal(now=1000.0, succeeded=True)
    assert claude_renewal_allowed(1000.0)
    note_claude_renewal(now=1000.0, succeeded=False)
    assert not claude_renewal_allowed(1030.0), "a minute of quiet, minimum"
    assert claude_renewal_allowed(1100.0)
    note_claude_renewal(now=1100.0, succeeded=False)
    assert not claude_renewal_allowed(1300.0), "the ladder doubles"
    note_claude_renewal(now=1400.0, succeeded=True)
    assert claude_renewal_allowed(1400.0), "success clears the ladder"


def test_repair_claude_rejects_signed_out_shape():
    from sidepulse.provider_reconnect import note_claude_renewal

    note_claude_renewal(now=0.0, succeeded=True)
    store = FakeStore()
    result = repair_claude_credential(
        store,
        now=1000.0,
        keychain_payload_reader=lambda: claude_payload(access=""),
        keychain_writer=lambda payload: True,
        refresher=_failing_refresher,
    )
    assert result.outcome is RepairOutcome.UNAVAILABLE
    assert not store.stored


def test_repair_claude_without_a_refresh_token_never_attempts_renewal():
    store = FakeStore()

    def exploding_refresher(raw, *, now):
        raise AssertionError("no refresh token, nothing to try")

    result = repair_claude_credential(
        store,
        now=2_000_000_000.0,
        keychain_payload_reader=lambda: claude_payload(
            access="", refresh="", expires_at=None
        ),
        refresher=exploding_refresher,
    )
    assert result.outcome is RepairOutcome.NEEDS_SIGN_IN
    assert "empty" in result.message


def test_repair_claude_stores_fresh_token_and_reports_change():
    store = FakeStore({("claude", "oauth-token"): "old-token"})
    result = repair_claude_credential(
        store,
        now=1000.0,
        keychain_payload_reader=lambda: claude_payload(access="b" * 32),
    )
    assert result.outcome is RepairOutcome.REPAIRED
    assert result.changed
    assert store.secrets[("claude", "oauth-token")] == "b" * 32


def test_repair_claude_same_token_is_already_healthy():
    token = "c" * 32
    store = FakeStore({("claude", "oauth-token"): token})
    result = repair_claude_credential(
        store,
        now=1000.0,
        keychain_payload_reader=lambda: claude_payload(access=token),
    )
    assert result.outcome is RepairOutcome.ALREADY_HEALTHY
    assert not result.changed


# --- codex -----------------------------------------------------------------


def make_rollout(home, day, name, mtime):
    directory = home / ".codex" / "sessions" / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("{}\n", encoding="utf-8")
    import os

    os.utime(path, (mtime, mtime))
    return path


def test_newest_codex_rollout_age(tmp_path):
    assert newest_codex_rollout_age(tmp_path, 1000.0) is None
    make_rollout(tmp_path, "2026/08/20", "rollout-old.jsonl", 100.0)
    make_rollout(tmp_path, "2026/08/26", "rollout-new.jsonl", 900.0)
    assert newest_codex_rollout_age(tmp_path, 1000.0) == 100.0


def test_codex_activity_report_shapes(tmp_path):
    assert "Install the Codex CLI" in codex_activity_report(tmp_path, 1000.0)
    (tmp_path / ".codex" / "sessions").mkdir(parents=True)
    assert "no completed sessions" in codex_activity_report(tmp_path, 1000.0)
    now = 1_000_000.0
    make_rollout(tmp_path, "2026/08/26", "rollout-fresh.jsonl", now - 120.0)
    assert "rescanning now" in codex_activity_report(tmp_path, now).lower()
    make_rollout(tmp_path, "2026/08/26", "rollout-fresh.jsonl", now - 72 * 3600.0)
    report = codex_activity_report(tmp_path, now)
    assert "3 d old" in report
    assert "completion" in report


def test_codex_app_server_probe_parses_replies():
    lines = [
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"userAgent": "codex/0.149.1 mac"}}
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "rateLimits": {
                        "primary": {"used_percent": 4.0, "resets_at": 1788286790}
                    }
                },
            }
        ),
        json.dumps(
            {"jsonrpc": "2.0", "id": 3, "result": {"account": {"email": "a@b.c"}}}
        ),
    ]
    probe = codex_app_server_probe(runner=lambda: "\n".join(lines))
    assert probe == {
        "authenticated": True,
        "used_percent": 4.0,
        "resets_at": 1788286790.0,
        "version": "0.149.1",
    }
    assert codex_app_server_probe(runner=lambda: None) is None
    assert codex_app_server_probe(runner=lambda: "not json") is None


# --- gates -----------------------------------------------------------------


def test_transient_gate_backs_off_and_forced_bypasses():
    gate = FailureGate()
    gate = note_failure(gate, now=0.0, terminal=False, fingerprint=None)
    assert gate.retry_at == TRANSIENT_BACKOFF_SECONDS[0]
    assert not should_collect(gate, now=1.0, fingerprint=None, forced=False)
    assert should_collect(gate, now=1.0, fingerprint=None, forced=True)
    assert should_collect(
        gate, now=TRANSIENT_BACKOFF_SECONDS[0], fingerprint=None, forced=False
    )
    for _ in range(10):
        gate = note_failure(gate, now=0.0, terminal=False, fingerprint=None)
    assert gate.retry_at == TRANSIENT_BACKOFF_SECONDS[-1]


def test_terminal_gate_lifts_on_credential_change():
    fingerprint = (("x", 1, 2, 3),)
    gate = note_failure(
        FailureGate(), now=0.0, terminal=True, fingerprint=fingerprint
    )
    assert not should_collect(gate, now=10.0, fingerprint=fingerprint, forced=False)
    changed = (("x", 9, 9, 9),)
    assert should_collect(gate, now=10.0, fingerprint=changed, forced=False)
    # Never permanently quiet: the ladder cap still re-checks eventually.
    assert should_collect(
        gate,
        now=TRANSIENT_BACKOFF_SECONDS[-1] + 1.0,
        fingerprint=fingerprint,
        forced=False,
    )


def test_credential_fingerprint_tracks_the_source_file(tmp_path):
    assert credential_fingerprint(tmp_path, "grok") is None
    write_grok_auth(tmp_path)
    first = credential_fingerprint(tmp_path, "grok")
    assert first is not None
    write_grok_auth(tmp_path, email="other@example.com")
    assert credential_fingerprint(tmp_path, "grok") != first


# --- transitions -----------------------------------------------------------


class Snap:
    def __init__(self, provider_id, state_value):
        class State:
            pass

        self.provider_id = provider_id
        self.state = State()
        self.state.value = state_value


def test_connection_loss_transitions_edge_only():
    before = (Snap("grok", "ready"), Snap("claude", "stale"), Snap("codex", "error"))
    after = (
        Snap("grok", "needs_sign_in"),
        Snap("claude", "rate_limited"),
        Snap("codex", "error"),
    )
    events = connection_loss_transitions(before, after)
    assert events == (
        ("grok:needs_sign_in", "grok", "needs_sign_in"),
        ("claude:rate_limited", "claude", "rate_limited"),
    )
    # Deduped by key; a still-broken provider announces nothing new.
    assert (
        connection_loss_transitions(
            before, after, seen_keys=frozenset(k for k, _p, _s in events)
        )
        == ()
    )


def test_repair_grok_defers_to_a_server_rejection(tmp_path):
    """The server is the authority on a token, not the file's own
    expiry stamp. Clicked live three times (2026-08-26): the file held
    a valid-looking session, the server 401'd it, and every click said
    "signed in — refreshing now" while nothing could change."""
    write_grok_auth(tmp_path)
    store = FakeStore()
    result = repair_grok_credential(
        store, home=tmp_path, now=1000.0, server_rejected=True
    )
    assert result.outcome is RepairOutcome.NEEDS_SIGN_IN
    assert "grok login" in result.message
    assert "rejecting" in result.message
    # Without the rejection context the healthy message stands.
    healthy = repair_grok_credential(store, home=tmp_path, now=1000.0)
    assert healthy.outcome in (
        RepairOutcome.REPAIRED,
        RepairOutcome.ALREADY_HEALTHY,
    )
