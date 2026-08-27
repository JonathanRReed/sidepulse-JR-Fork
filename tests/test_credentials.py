"""Credential reads are the one place this app can embarrass its user.

Two properties are non-negotiable: it never raises a Keychain dialog on a
background timer, and a secret never reaches a log, a repr, or a traceback.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sidepulse.credentials import (
    CLAUDE_CODE_KEYCHAIN,
    CodexTokens,
    CredentialOutcome,
    CredentialResult,
    KeychainConsentLedger,
    KeychainItem,
    read_codex_tokens,
    read_keychain_secret,
)


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["security"], returncode=returncode, stdout=stdout, stderr="")


def test_background_reads_never_reach_the_keychain(tmp_path: Path) -> None:
    """The property that keeps this app off the 'why is it asking' list."""
    calls: list[KeychainItem] = []

    def runner(item):
        calls.append(item)
        return _completed(0, "secret-value")

    result = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN,
        allow_prompt=False,
        ledger=KeychainConsentLedger(tmp_path / "consent.json"),
        runner=runner,
    )

    assert result.outcome is CredentialOutcome.PROMPT_NOT_ALLOWED
    assert calls == [], "a background read reached the Keychain"
    assert result.secret is None


def test_explicit_read_returns_the_secret(tmp_path: Path) -> None:
    result = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN,
        allow_prompt=True,
        ledger=KeychainConsentLedger(tmp_path / "consent.json"),
        runner=lambda item: _completed(0, "secret-value\n"),
    )
    assert result.ok
    assert result.secret == "secret-value"


def test_a_denial_is_not_retried_on_the_next_tick(tmp_path: Path) -> None:
    """"Deny" is an answer. Asking again in 30 seconds is harassment."""
    ledger = KeychainConsentLedger(tmp_path / "consent.json")
    attempts = 0

    def runner(item):
        nonlocal attempts
        attempts += 1
        return _completed(128)  # user dismissed the dialog

    first = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN, allow_prompt=True, ledger=ledger, runner=runner, now=1_000.0
    )
    assert first.outcome is CredentialOutcome.DENIED
    assert first.retry_at is not None and first.retry_at > 1_000.0

    second = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN, allow_prompt=True, ledger=ledger, runner=runner, now=1_060.0
    )
    assert second.outcome is CredentialOutcome.COOLING_DOWN
    assert attempts == 1, "prompted again while cooling down"


def test_the_cooldown_survives_a_restart(tmp_path: Path) -> None:
    """A fresh ledger object reads the same file -- relaunching is not consent."""
    path = tmp_path / "consent.json"
    KeychainConsentLedger(path).record_denial(CLAUDE_CODE_KEYCHAIN.service, 1_000.0)

    result = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN,
        allow_prompt=True,
        ledger=KeychainConsentLedger(path),
        runner=lambda item: pytest.fail("prompted after a restart"),
        now=1_500.0,
    )
    assert result.outcome is CredentialOutcome.COOLING_DOWN


def test_repeated_denials_escalate_the_cooldown(tmp_path: Path) -> None:
    ledger = KeychainConsentLedger(tmp_path / "consent.json")
    first = ledger.record_denial("svc", 0.0)
    second = ledger.record_denial("svc", 0.0)
    assert second > first, "a second denial must back off harder"


def test_success_clears_the_cooldown(tmp_path: Path) -> None:
    path = tmp_path / "consent.json"
    ledger = KeychainConsentLedger(path)
    ledger.record_denial(CLAUDE_CODE_KEYCHAIN.service, 1_000.0)

    read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN,
        allow_prompt=True,
        ledger=ledger,
        runner=lambda item: _completed(0, "ok"),
        now=999_999.0,
    )
    assert ledger.retry_at(CLAUDE_CODE_KEYCHAIN.service, 1_000_000.0) is None


def test_a_secret_never_appears_in_a_repr() -> None:
    """reprs end up in tracebacks, logs and pytest output."""
    result = CredentialResult(CredentialOutcome.OK, secret="hunter2")
    assert "hunter2" not in repr(result)
    assert "redacted" in repr(result)

    tokens = CodexTokens(
        access_token="tok-secret",
        account_id="acct-1",
        refresh_token="refresh-secret",
        last_refresh=None,
    )
    rendered = repr(tokens)
    assert "tok-secret" not in rendered and "refresh-secret" not in rendered
    assert "acct-1" in rendered, "non-secret identity is useful in diagnostics"


def test_codex_tokens_read_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "auth_mode": "oauth",
                "last_refresh": "2026-08-13T00:00:00Z",
                "tokens": {
                    "access_token": "at",
                    "account_id": "acct",
                    "refresh_token": "rt",
                },
            }
        )
    )
    tokens = read_codex_tokens(path)
    assert tokens is not None
    assert tokens.access_token == "at"
    assert tokens.account_id == "acct"


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        '{"tokens": {}}',
        '{"tokens": {"access_token": ""}}',
        '{"tokens": []}',
        "not json at all",
    ],
)
def test_malformed_codex_auth_is_absence_not_a_crash(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "auth.json"
    path.write_text(payload)
    assert read_codex_tokens(path) is None


def test_missing_codex_auth_is_absence(tmp_path: Path) -> None:
    assert read_codex_tokens(tmp_path / "nope.json") is None


# --- Standing consent: background reads only after a granted foreground one


def _ok_runner(item):
    import subprocess

    return subprocess.CompletedProcess([], 0, stdout="secret-payload\n", stderr="")


def test_background_read_is_refused_without_a_standing_grant(tmp_path):
    ledger = KeychainConsentLedger(tmp_path / "consent.json")

    def runner(item):
        raise AssertionError("no grant on record: security must not run")

    result = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN,
        allow_prompt=False,
        ledger=ledger,
        runner=runner,
    )
    assert result.outcome is CredentialOutcome.PROMPT_NOT_ALLOWED


def test_a_granted_foreground_read_authorizes_background_reads(tmp_path):
    ledger = KeychainConsentLedger(tmp_path / "consent.json")
    first = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN,
        allow_prompt=True,
        ledger=ledger,
        runner=_ok_runner,
    )
    assert first.ok
    assert ledger.standing_grant(CLAUDE_CODE_KEYCHAIN.service)

    background = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN,
        allow_prompt=False,
        ledger=ledger,
        runner=_ok_runner,
    )
    assert background.ok
    assert background.secret == "secret-payload"


def test_a_denial_revokes_the_standing_grant(tmp_path):
    import subprocess

    ledger = KeychainConsentLedger(tmp_path / "consent.json")
    ledger.record_success(CLAUDE_CODE_KEYCHAIN.service, 1_000.0)

    def cancels(item):
        return subprocess.CompletedProcess([], 128, stdout="", stderr="")

    denied = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN,
        allow_prompt=True,
        ledger=ledger,
        runner=cancels,
        now=2_000.0,
    )
    assert denied.outcome is CredentialOutcome.DENIED
    assert not ledger.standing_grant(CLAUDE_CODE_KEYCHAIN.service), (
        "a revoked grant falls back to foreground-only"
    )
