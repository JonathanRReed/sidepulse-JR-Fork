"""The one place this app reads a credential.

Every provider that needs a token comes through here, for three reasons.

**Prompting is a user-visible event.** Reading another application's Keychain
item raises a system dialog naming this app. A background refresh loop that
does that is indistinguishable from malware, and after the first "Deny" it
would ask again on the next tick, forever. So Keychain reads are *never*
attempted in the background: they require an explicit user action, and a
denial earns an escalating cooldown that survives restarts.

**Secrets must not leak into diagnostics.** This module returns a secret to
its caller and does nothing else with it -- no logging, no error text, no
caching to disk, no inclusion in doctor output. Failures are reported as
codes, never as content.

**One audited surface.** A single hardened entry point can be reviewed once.
Six ad-hoc `subprocess.run(["security", ...])` calls cannot.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .private_io import atomic_private_write, read_private_text

# `security` exits 128 when the user dismisses or denies the access dialog.
_SECURITY_USER_CANCELED = 128
_SECURITY_TIMEOUT_SECONDS = 30.0
# Codex writes plain JSON; a real one is a few KB. Anything larger is not it.
CODEX_AUTH_MAX_BYTES = 256 * 1024

# A denial is an answer, not an error to retry. Back off hard, and remember
# across restarts -- re-prompting on every launch is how an app gets deleted.
_COOLDOWN_SECONDS = (60.0 * 60.0, 24.0 * 60.0 * 60.0)


class CredentialOutcome(Enum):
    """Why a credential read ended the way it did. Never carries content."""

    OK = "ok"
    NOT_FOUND = "not_found"
    DENIED = "denied"
    COOLING_DOWN = "cooling_down"
    PROMPT_NOT_ALLOWED = "prompt_not_allowed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CredentialResult:
    """A read attempt. `secret` is populated only when outcome is OK."""

    outcome: CredentialOutcome
    secret: str | None = None
    retry_at: float | None = None

    def __repr__(self) -> str:  # pragma: no cover - defensive, but cheap
        # Never let a secret reach a traceback, a log line, or a test dump.
        held = "<redacted>" if self.secret is not None else "None"
        return f"CredentialResult(outcome={self.outcome.value}, secret={held})"

    @property
    def ok(self) -> bool:
        return self.outcome is CredentialOutcome.OK and self.secret is not None


@dataclass(frozen=True, slots=True)
class KeychainItem:
    service: str
    account: str | None = None


# Claude Code's own OAuth credential, written by `claude login`.
CLAUDE_CODE_KEYCHAIN = KeychainItem(service="Claude Code-credentials")


class KeychainConsentLedger:
    """Remembers denials so a refused prompt is not asked again on a timer."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict:
        try:
            payload = json.loads(read_private_text(self.path, max_bytes=64 * 1024))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def retry_at(self, service: str, now: float) -> float | None:
        """When this service may be prompted for again, or None if now."""
        entry = self._read().get(service)
        if not isinstance(entry, dict):
            return None
        try:
            deadline = float(entry.get("retry_at", 0.0))
        except (TypeError, ValueError):
            return None
        return deadline if deadline > now else None

    def record_denial(self, service: str, now: float) -> float:
        payload = self._read()
        entry = payload.get(service)
        denials = 0
        if isinstance(entry, dict):
            try:
                denials = max(0, int(entry.get("denials", 0)))
            except (TypeError, ValueError):
                denials = 0
        index = min(denials, len(_COOLDOWN_SECONDS) - 1)
        deadline = now + _COOLDOWN_SECONDS[index]
        payload[service] = {"denials": denials + 1, "retry_at": deadline}
        try:
            atomic_private_write(self.path, json.dumps(payload, separators=(",", ":")))
        except OSError:
            pass
        return deadline

    def record_success(self, service: str, now: float) -> None:
        """A consented read succeeded: remember the standing grant.

        macOS makes later reads by this same signed binary silent once
        the user clicks Always Allow -- this stamp is what authorizes
        BACKGROUND re-reads (allow_prompt=False) to try at all. Cleared
        by a later denial, so a revoked grant falls back to
        foreground-only on the next refusal.
        """
        payload = self._read()
        payload[service] = {"granted_at": float(now)}
        try:
            atomic_private_write(self.path, json.dumps(payload, separators=(",", ":")))
        except OSError:
            pass

    def standing_grant(self, service: str) -> bool:
        entry = self._read().get(service)
        if not isinstance(entry, dict):
            return False
        try:
            return float(entry.get("granted_at", 0.0)) > 0.0
        except (TypeError, ValueError):
            return False

    def clear(self, service: str) -> None:
        payload = self._read()
        if payload.pop(service, None) is None:
            return
        try:
            atomic_private_write(self.path, json.dumps(payload, separators=(",", ":")))
        except OSError:
            pass


def _run_security(item: KeychainItem) -> subprocess.CompletedProcess:
    arguments = ["/usr/bin/security", "find-generic-password", "-w", "-s", item.service]
    if item.account:
        arguments += ["-a", item.account]
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=_SECURITY_TIMEOUT_SECONDS,
        check=False,
    )


def read_keychain_secret(
    item: KeychainItem,
    *,
    allow_prompt: bool,
    ledger: KeychainConsentLedger | None = None,
    now: float | None = None,
    runner=None,
) -> CredentialResult:
    """Read one Keychain secret.

    `allow_prompt` must be True only on a path the user just initiated. It is
    a parameter rather than a policy check inside this function so that the
    call site has to state, in code, that a dialog is expected right now.
    """
    reference = time.time() if now is None else float(now)
    if not allow_prompt and not (
        ledger is not None and ledger.standing_grant(item.service)
    ):
        # No standing grant on record: a background read could surprise
        # the user with a Keychain dialog, so it is refused outright.
        return CredentialResult(CredentialOutcome.PROMPT_NOT_ALLOWED)
    if ledger is not None:
        retry_at = ledger.retry_at(item.service, reference)
        if retry_at is not None:
            return CredentialResult(CredentialOutcome.COOLING_DOWN, retry_at=retry_at)

    try:
        completed = (runner or _run_security)(item)
    except (OSError, subprocess.SubprocessError):
        return CredentialResult(CredentialOutcome.UNAVAILABLE)

    if completed.returncode == 0:
        secret = (completed.stdout or "").strip()
        if not secret:
            return CredentialResult(CredentialOutcome.NOT_FOUND)
        if ledger is not None:
            ledger.record_success(item.service, reference)
        return CredentialResult(CredentialOutcome.OK, secret=secret)
    if completed.returncode == _SECURITY_USER_CANCELED:
        deadline = (
            ledger.record_denial(item.service, reference)
            if ledger is not None
            else None
        )
        return CredentialResult(CredentialOutcome.DENIED, retry_at=deadline)
    return CredentialResult(CredentialOutcome.NOT_FOUND)


def _run_security_write(item: KeychainItem, secret: str) -> subprocess.CompletedProcess:
    arguments = [
        "/usr/bin/security",
        "add-generic-password",
        "-U",
        "-s",
        item.service,
        "-w",
        secret,
    ]
    if item.account:
        arguments += ["-a", item.account]
    return subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=_SECURITY_TIMEOUT_SECONDS,
        check=False,
    )


def write_keychain_secret(
    item: KeychainItem,
    secret: str,
    *,
    runner=None,
) -> bool:
    """Update one Keychain item in place (`add-generic-password -U`).

    Exists for exactly one caller today: writing Claude Code's ROTATED
    OAuth tokens back after a refresh, so `claude` itself keeps working
    -- the CodexBar contract. The secret rides argv to the system
    `security` tool, the same channel the read path already uses.
    """
    if not isinstance(secret, str) or not secret:
        return False
    try:
        completed = (runner or _run_security_write)(item, secret)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


@dataclass(frozen=True, slots=True)
class CodexTokens:
    access_token: str
    account_id: str | None
    refresh_token: str | None
    last_refresh: str | None

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"CodexTokens(account_id={self.account_id!r}, tokens=<redacted>)"


def default_codex_auth_path() -> Path:
    override = os.environ.get("CODEX_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".codex"
    return base / "auth.json"


def read_codex_tokens(path: Path | None = None) -> CodexTokens | None:
    """Read Codex's OAuth tokens from its own config. No prompt, no Keychain."""
    target = default_codex_auth_path() if path is None else Path(path)
    try:
        payload = json.loads(read_private_text(target, max_bytes=CODEX_AUTH_MAX_BYTES))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None
    account_id = tokens.get("account_id")
    refresh_token = tokens.get("refresh_token")
    last_refresh = payload.get("last_refresh")
    return CodexTokens(
        access_token=access_token.strip(),
        account_id=account_id if isinstance(account_id, str) else None,
        refresh_token=refresh_token if isinstance(refresh_token, str) else None,
        last_refresh=last_refresh if isinstance(last_refresh, str) else None,
    )
