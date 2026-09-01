"""Honest reconnect: probe, repair, and REPORT provider connections.

Reconnect used to be three different lies. Grok's button returned a
hard-coded sentence and mutated nothing. Claude's re-stored whatever
token the Keychain already held -- expired or not -- and announced
"connected." Codex had no branch at all, so the click opened a window.
And every message was routed to a sink that is empty until Settings has
been opened once, so even the lies were invisible.

The contract here (borrowed from CodexBar's probe records and T3 Code's
connection supervisor, the two ecosystems this fork tracks):

  * every reconnect ends in a RepairResult whose message the caller MUST
    render -- silence is impossible by construction;
  * background repair never prompts (the Keychain invariant in
    credentials.py stands: a dialog only on a path the user just took);
  * an auth failure is TERMINAL until the credential source visibly
    changes -- watch the file fingerprint, don't hammer the endpoint;
  * a transient failure backs off exponentially and is forgotten on the
    first success.

Everything in this module is deterministic and AppKit-free so it tests
without a host app.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .product_identity import PRODUCT_DISPLAY_NAME
from .provider_instances import ProviderInstanceKey

#: Where each provider's OWN tooling keeps its sign-in. These are the
#: files whose change means "the user just logged in somewhere" -- the
#: one event that should bypass every backoff gate.
CREDENTIAL_SOURCE_FILES: dict[str, tuple[str, ...]] = {
    "grok": (".grok/auth.json",),
    "codex": (".codex/auth.json",),
    "claude": (".claude/.credentials.json",),
}

#: Transient-failure ladder, seconds. CodexBar uses 5 min doubling to a
#: 6 h cap; this app repaints a menu, not a dashboard, so the cap stays
#: at an hour -- long enough to stop hammering, short enough that a
#: recovered outage is noticed the same afternoon.
TRANSIENT_BACKOFF_SECONDS: tuple[float, ...] = (300.0, 600.0, 1200.0, 2400.0, 3600.0)

CODEX_SESSIONS_SUBPATH = ".codex/sessions"
#: How recent a rollout has to be before "run Codex" advice would just
#: be noise. Mirrors CODEX_READING_STALE_SECONDS (6 h) in the collector.
CODEX_FRESH_SECONDS = 6 * 60 * 60.0


class RepairOutcome(Enum):
    """What one reconnect attempt actually did. Never silent."""

    REPAIRED = "repaired"  # a credential changed; a refresh will differ
    ALREADY_HEALTHY = "already_healthy"  # nothing to fix; refresh anyway
    NEEDS_PROVIDER_REFRESH = "needs_provider_refresh"  # provider owns renewal
    NEEDS_SIGN_IN = "needs_sign_in"  # only the user's own tool can fix it
    BLOCKED = "blocked"  # consent/cooldown stands in the way
    UNAVAILABLE = "unavailable"  # the probe itself failed


@dataclass(frozen=True, slots=True)
class RepairResult:
    provider_id: str
    outcome: RepairOutcome
    #: User-facing, complete sentence, safe to render verbatim.
    message: str
    #: True when a stored credential was added, replaced, or cleared --
    #: the caller should force a refresh of this provider.
    changed: bool = False


# ---------------------------------------------------------------------------
# Credential-source fingerprints: "did the user just sign in somewhere?"


#: Providers whose sign-in lives in the Keychain rather than a file.
#: Their fingerprint has to come from the item's ATTRIBUTES, or a
#: terminal gate can never lift (2026-08-27: this machine has no
#: ~/.claude/.credentials.json at all, so Claude's fingerprint was
#: None -> None forever and `claude login` was invisible to the gate).
KEYCHAIN_SOURCE_SERVICES: dict[str, str] = {"claude": "Claude Code-credentials"}

#: The attributes probe is cheap but not free; CodexBar throttles its
#: equivalent check to 60s and so do we.
_KEYCHAIN_FINGERPRINT_TTL_SECONDS = 60.0
_KEYCHAIN_FINGERPRINT_CACHE: dict[str, tuple[float, tuple]] = {}


def keychain_fingerprint(service: str, *, now: float | None = None) -> tuple:
    """Modification/creation stamps of a Keychain item, no secret read.

    `security find-generic-password` WITHOUT -w returns attributes only,
    so this raises no consent dialog and costs no password access.
    """
    import subprocess
    import time as _time

    stamp = _time.monotonic() if now is None else float(now)
    cached = _KEYCHAIN_FINGERPRINT_CACHE.get(service)
    if cached is not None and stamp - cached[0] < _KEYCHAIN_FINGERPRINT_TTL_SECONDS:
        return cached[1]
    parts: tuple = ()
    try:
        completed = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if completed.returncode == 0:
            found: dict[str, str] = {}
            for line in (completed.stdout or "").splitlines():
                stripped = line.strip()
                for key in ("mdat", "cdat"):
                    if stripped.startswith(f'"{key}"'):
                        _, _, value = stripped.partition("=")
                        found[key] = value.strip().strip('"')
            parts = (("keychain", service, found.get("mdat"), found.get("cdat")),)
    except (OSError, subprocess.SubprocessError):
        parts = ()
    _KEYCHAIN_FINGERPRINT_CACHE[service] = (stamp, parts)
    return parts


def credential_fingerprint(home: Path, provider_id: str) -> tuple | None:
    """A cheap identity for the provider's credential source.

    None means "no source exists at all". Any change in the tuple -- a
    file appearing, growing or being rewritten, or a Keychain item being
    re-saved by the tool that owns it -- is the signal that re-trying a
    terminally-failed provider is worth it again.
    """
    parts: list[tuple] = []
    for relative in CREDENTIAL_SOURCE_FILES.get(provider_id, ()):
        path = Path(home) / relative
        try:
            info = path.stat()
        except OSError:
            continue
        parts.append((relative, int(info.st_mtime_ns), int(info.st_size), int(info.st_ino)))
    service = KEYCHAIN_SOURCE_SERVICES.get(provider_id)
    if service:
        parts.extend(keychain_fingerprint(service))
    return tuple(parts) or None


# ---------------------------------------------------------------------------
# Grok: the CLI owns the sign-in; SidePulse's job is to read it honestly.


def grok_auth_status(home: Path, now: float) -> tuple[str, str | None]:
    """('ok'|'expired'|'missing', email) for ~/.grok/auth.json.

    'expired' means the file exists and parses but holds no entry that
    is still valid -- the one case where "run `grok login`" is the right
    advice. 'missing' covers absent AND unreadable, because both have
    the same remedy.
    """
    from .provider_usage_collectors import _read_grok_auth

    path = Path(home) / ".grok" / "auth.json"
    live = _read_grok_auth(Path(home), now)
    if live is not None:
        return "ok", live[1]
    try:
        exists = path.is_file() and not path.is_symlink()
    except OSError:
        exists = False
    return ("expired" if exists else "missing"), None


def repair_grok_credential(
    credential_store,
    *,
    home: Path,
    now: float,
    server_rejected: bool = False,
) -> RepairResult:
    """Make the collector's next read match reality. Background-safe.

    The historical wedge: once ~/.grok/auth.json aged out, the collector
    fell back to a stored `grok/token` copy that nothing could clear, so
    the same rejected token was retried forever. When the CLI holds a
    LIVE session, the stored copy is at best redundant and at worst that
    wedge -- clear it and let the file win.
    """
    status, email = grok_auth_status(home, now)
    if status == "ok":
        cleared = False
        try:
            cleared = bool(credential_store.delete("grok", "token"))
        except Exception:
            cleared = False
        who = f" as {email}" if email else ""
        if server_rejected:
            # The SERVER is the authority on a token, not the file's own
            # expiry stamp. This exact file token is what the collector
            # sends, and the collector's last answer was 401 -- so
            # "signed in, refreshing now" was a politer version of the
            # old lie (clicked live, three times, 2026-08-26). Only
            # `grok login` can mint a fresh one; the credential-file
            # watch retries the moment it changes.
            return RepairResult(
                "grok",
                RepairOutcome.NEEDS_SIGN_IN,
                f"The grok CLI holds a sign-in{who}, but the server is "
                "rejecting it (revoked or rotated). Run `grok login` in a "
                f"terminal — {PRODUCT_DISPLAY_NAME} retries automatically the moment the "
                "CLI saves a fresh sign-in.",
                changed=cleared,
            )
        return RepairResult(
            "grok",
            RepairOutcome.REPAIRED if cleared else RepairOutcome.ALREADY_HEALTHY,
            f"Grok is signed in via the grok CLI{who} — refreshing usage now.",
            changed=cleared,
        )
    if status == "expired":
        return RepairResult(
            "grok",
            RepairOutcome.NEEDS_SIGN_IN,
            "The grok CLI's sign-in has expired. Run `grok login` in a "
            f"terminal — {PRODUCT_DISPLAY_NAME} picks it up automatically within a minute.",
        )
    return RepairResult(
        "grok",
        RepairOutcome.NEEDS_SIGN_IN,
        "No grok CLI sign-in was found. Install the grok CLI and run "
        f"`grok login` — {PRODUCT_DISPLAY_NAME} reads its sign-in automatically.",
    )


# ---------------------------------------------------------------------------
# Claude: Claude Code owns renewal; JR Bar only copies a current access token.


def repair_claude_credential(
    credential_store,
    *,
    now: float,
    keychain_payload_reader: Callable[[], object],
    source_instance_id: str = "default",
) -> RepairResult:
    """User-initiated Claude reconnect that cannot claim a false success.

    `keychain_payload_reader` returns the raw Keychain blob (str), or a
    CredentialOutcome-shaped sentinel via exceptions handled by the
    caller; here it must return `str | None` where None means the read
    itself was refused (consent, cooldown, missing). The caller maps
    those refusals to messages BEFORE calling this -- this function owns
    only the "we hold a payload, is it a working sign-in?" question.
    """
    from .claude_quota import (
        credential_from_keychain_payload,
        credential_needs_sign_in,
    )

    raw = keychain_payload_reader()
    if raw is None:
        return RepairResult(
            "claude",
            RepairOutcome.UNAVAILABLE,
            "Claude Code's sign-in was not found in the Keychain.",
        )
    credential = credential_from_keychain_payload(raw)
    if credential_needs_sign_in(raw) or (
        credential is not None and credential.is_expired(now)
    ):
        return RepairResult(
            "claude",
            RepairOutcome.NEEDS_PROVIDER_REFRESH,
            "Claude Code owns this sign-in. Run `claude` once so Claude Code "
            "can mint a current access token, then click Reconnect Claude. "
            "JR Bar never consumes the refresh token or changes Claude "
            "Code's Keychain item.",
        )
    if credential is None:
        return RepairResult(
            "claude",
            RepairOutcome.NEEDS_SIGN_IN,
            "Claude Code's stored sign-in is empty or unreadable. Run "
            "`claude` and sign in there, then click Reconnect Claude.",
        )
    stored = None
    instance_key = None
    try:
        if source_instance_id == "default":
            read = credential_store.get("claude", "oauth-token")
        else:
            instance_key = ProviderInstanceKey("claude", source_instance_id)
            read = credential_store.get_for_instance(instance_key, "oauth-token")
        stored = read.secret if getattr(read, "available", False) else None
    except Exception:
        stored = None
    if stored == credential.access_token:
        return RepairResult(
            "claude",
            RepairOutcome.ALREADY_HEALTHY,
            "Claude usage was already connected with a current sign-in — "
            "refreshing now.",
        )
    if instance_key is None:
        credential_store.set("claude", "oauth-token", credential.access_token)
    else:
        credential_store.set_for_instance(
            instance_key,
            "oauth-token",
            credential.access_token,
        )
    _remember_claude_expiry(
        credential_store,
        credential.expires_at,
        source_instance_id=source_instance_id,
    )
    return RepairResult(
        "claude",
        RepairOutcome.REPAIRED,
        "Claude usage connected with a fresh sign-in — refreshing now.",
        changed=True,
    )


def _remember_claude_expiry(
    credential_store,
    expires_at: float | None,
    *,
    source_instance_id: str = "default",
) -> None:
    """Persist the access token's lifetime beside the token itself.

    Without it the collector cannot know the token is stale and only
    learns so from a 401 -- which surfaces to the owner as a "reconnect"
    row once every token lifetime (2026-08-27 report). Best effort: a
    store that refuses the write just returns us to reactive behavior.
    """
    if expires_at is None:
        return
    try:
        if source_instance_id == "default":
            credential_store.set(
                "claude", "oauth-expires-at", str(int(float(expires_at)))
            )
        else:
            credential_store.set_for_instance(
                ProviderInstanceKey("claude", source_instance_id),
                "oauth-expires-at",
                str(int(float(expires_at))),
            )
    except Exception:
        pass


def claude_token_is_stale(
    credential_store,
    *,
    now: float,
    margin: float = 300.0,
    source_instance_id: str = "default",
) -> bool:
    """True when the stored Claude token is expired or nearly so.

    A missing or unreadable stamp counts as stale so the collector may
    re-read Claude Code's item under an existing standing grant. It does
    not authorize JR Bar to renew or mutate that external credential.
    """
    try:
        if source_instance_id == "default":
            read = credential_store.get("claude", "oauth-expires-at")
        else:
            read = credential_store.get_for_instance(
                ProviderInstanceKey("claude", source_instance_id),
                "oauth-expires-at",
            )
        raw = read.secret if getattr(read, "available", False) else None
        if raw is None:
            return True
        return float(now) + float(margin) >= float(raw)
    except Exception:
        return True


def sync_claude_credential_in_background(
    credential_store,
    *,
    home: Path,
    now: float,
    source_instance_id: str = "default",
) -> bool:
    """Copy a current Claude access token under an existing grant.

    Reads the Keychain WITHOUT prompt allowance -- the read only runs
    when a prior consented read recorded a standing grant, so no dialog
    can surprise the user from a background thread. It never consumes a
    refresh token and never mutates Claude Code's Keychain item. True
    means JR Bar's own stored access token changed.
    """
    del home  # signature symmetry with the other background repairs
    try:
        from .credentials import (
            CLAUDE_CODE_KEYCHAIN,
            KeychainConsentLedger,
            read_keychain_secret,
        )
        from .providers import default_state_dir

        result = read_keychain_secret(
            CLAUDE_CODE_KEYCHAIN,
            allow_prompt=False,
            ledger=KeychainConsentLedger(
                default_state_dir() / "keychain-consent.json"
            ),
        )
        if not result.ok or result.secret is None:
            return False
        repair = repair_claude_credential(
            credential_store,
            now=now,
            keychain_payload_reader=lambda: result.secret,
            source_instance_id=source_instance_id,
        )
        return bool(repair.changed)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Codex: nothing to repair -- report what the evidence actually says.


def newest_codex_rollout_age(home: Path, now: float) -> float | None:
    """Seconds since the newest rollout transcript, or None when none exist.

    Walks only the dated tail of ~/.codex/sessions (year/month/day
    directories, newest first) so a years-deep archive costs three
    directory listings, not a full tree walk.
    """
    root = Path(home) / CODEX_SESSIONS_SUBPATH
    try:
        years = sorted(
            (entry for entry in root.iterdir() if entry.is_dir()),
            key=lambda entry: entry.name,
            reverse=True,
        )
    except OSError:
        return None
    for year in years[:2]:
        try:
            months = sorted(
                (entry for entry in year.iterdir() if entry.is_dir()),
                key=lambda entry: entry.name,
                reverse=True,
            )
        except OSError:
            continue
        for month in months[:2]:
            try:
                days = sorted(
                    (entry for entry in month.iterdir() if entry.is_dir()),
                    key=lambda entry: entry.name,
                    reverse=True,
                )
            except OSError:
                continue
            for day in days[:7]:
                newest: float | None = None
                try:
                    for candidate in day.iterdir():
                        if candidate.suffix != ".jsonl":
                            continue
                        try:
                            mtime = candidate.stat().st_mtime
                        except OSError:
                            continue
                        if newest is None or mtime > newest:
                            newest = mtime
                except OSError:
                    continue
                if newest is not None:
                    return max(0.0, now - newest)
    return None


def codex_activity_report(home: Path, now: float) -> str:
    """The honest sentence behind Codex's reconnect/refresh action.

    Codex usage is read from the CLI's own transcripts, so 'reconnect'
    can only ever mean 'go look again'. What broke trust was the app
    saying 'run Codex to refresh' to a user who had JUST run Codex --
    because their run never completed a turn, so no transcript was
    written. Name the newest evidence and its age instead.
    """
    root = Path(home) / CODEX_SESSIONS_SUBPATH
    if not root.is_dir():
        return (
            "No Codex CLI transcripts were found. Install the Codex CLI, "
            "run one prompt to completion, and Codex usage appears on the "
            "next refresh."
        )
    age = newest_codex_rollout_age(home, now)
    if age is None:
        return (
            "The Codex CLI is installed but has no completed sessions yet. "
            "Run one prompt to completion (opening and closing Codex is "
            "not enough — a reply must finish), then usage appears on the "
            "next refresh."
        )
    if age <= CODEX_FRESH_SECONDS:
        minutes = max(1, int(age // 60))
        return (
            f"Codex activity from {minutes} min ago was found — "
            "rescanning now."
        )
    hours = age / 3600.0
    when = f"{int(hours)} h" if hours < 48 else f"{int(hours // 24)} d"
    return (
        f"The newest completed Codex session is {when} old, so the usage "
        "shown is that old too. Run one Codex prompt to completion — a "
        f"reply must finish, not just open — and {PRODUCT_DISPLAY_NAME} rescans within "
        "two minutes."
    )


# ---------------------------------------------------------------------------
# Failure gates: terminal-until-credentials-change, transient-with-backoff.


@dataclass(frozen=True, slots=True)
class FailureGate:
    """Per-provider retry state. Immutable; the runtime swaps whole values."""

    #: Monotonic-ish wall clock before which background collects skip.
    retry_at: float = 0.0
    #: How many transient failures in a row (indexes the ladder).
    strikes: int = 0
    #: Fingerprint captured when the failure was TERMINAL (auth). While
    #: the source still matches, retrying cannot succeed; when it stops
    #: matching, the user signed in and the gate lifts immediately.
    terminal_fingerprint: tuple | None = None
    terminal: bool = False


def note_failure(
    gate: FailureGate,
    *,
    now: float,
    terminal: bool,
    fingerprint: tuple | None,
) -> FailureGate:
    if terminal:
        # An auth failure retries only when the credential source
        # changes -- but never goes completely quiet, in case the fix
        # happens somewhere fingerprints cannot see (the Keychain).
        return FailureGate(
            retry_at=now + TRANSIENT_BACKOFF_SECONDS[-1],
            strikes=gate.strikes,
            terminal_fingerprint=fingerprint,
            terminal=True,
        )
    strikes = min(gate.strikes + 1, len(TRANSIENT_BACKOFF_SECONDS))
    return FailureGate(
        retry_at=now + TRANSIENT_BACKOFF_SECONDS[strikes - 1],
        strikes=strikes,
    )


def should_collect(
    gate: FailureGate,
    *,
    now: float,
    fingerprint: tuple | None,
    forced: bool,
) -> bool:
    """May this provider's collector run right now?

    Forced (user-initiated) collects always run. A terminal gate lifts
    the moment the credential source fingerprint differs from the one
    captured at failure time. A transient gate waits out its ladder.
    """
    if forced:
        return True
    if gate.terminal:
        return fingerprint != gate.terminal_fingerprint
    return now >= gate.retry_at


# ---------------------------------------------------------------------------
# Connection-loss transitions: the edge that earns one attention cue.

_HEALTHY_STATES = frozenset({"ready", "stale"})
_LOST_STATES = frozenset({"needs_sign_in", "unavailable", "error", "rate_limited"})
#: A stale-served snapshot with one of these reason codes is a live
#: failure wearing old numbers: for any provider with last-known-good,
#: the runtime converts failures to STALE, so without this the
#: healthy->lost edge never exists for an established provider.
_LOST_STALE_REASONS = frozenset({"authentication_required"})


def connection_loss_transitions(
    previous_snapshots,
    snapshots,
    *,
    seen_keys: frozenset[str] = frozenset(),
) -> tuple[tuple[str, str, str], ...]:
    """(dedupe_key, provider_id, state_value) for each provider that WAS
    healthy and now is not. Edge-triggered: a provider that stays broken
    produces nothing new, and the dedupe key includes the failing state
    so sign-out and rate-limit each announce once."""
    def _lost_kind(snapshot) -> str | None:
        state = getattr(getattr(snapshot, "state", None), "value", None)
        if state in _LOST_STATES:
            return state
        if (
            state == "stale"
            and getattr(snapshot, "reason_code", None) in _LOST_STALE_REASONS
        ):
            return str(snapshot.reason_code)
        return None

    def _healthy(snapshot) -> bool:
        state = getattr(getattr(snapshot, "state", None), "value", None)
        return state in _HEALTHY_STATES and _lost_kind(snapshot) is None

    previous_by_id = {
        getattr(snapshot, "provider_id", None): snapshot
        for snapshot in previous_snapshots
    }
    events: list[tuple[str, str, str]] = []
    for snapshot in snapshots:
        provider_id = getattr(snapshot, "provider_id", None)
        lost = _lost_kind(snapshot)
        if provider_id is None or lost is None:
            continue
        before = previous_by_id.get(provider_id)
        if before is None or not _healthy(before):
            continue
        key = f"{provider_id}:{lost}"
        if key in seen_keys:
            continue
        events.append((key, provider_id, lost))
    return tuple(events)


# ---------------------------------------------------------------------------
# Codex live probe: `codex app-server` JSON-RPC, user-initiated only.


def codex_app_server_probe(
    *,
    codex_binary: str = "codex",
    timeout_seconds: float = 12.0,
    runner=None,
) -> dict | None:
    """Ask a read-only `codex app-server` for auth + rate limits.

    Returns {"authenticated": bool | None, "used_percent": float | None,
    "resets_at": float | None, "version": str | None} or None when the
    CLI is missing or the handshake fails. The caller owns cadence. The
    provider usage service runs this on its bounded background worker.
    """
    import queue
    import subprocess
    import threading
    import time

    requests = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "sidepulse", "version": "1.0"}
                },
            }
        )
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "initialized"})
        + "\n"
        + json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"}
        )
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "account/read"})
        + "\n"
    )

    def _default_runner() -> str | None:
        process = None
        try:
            process = subprocess.Popen(
                [codex_binary, "-s", "read-only", "-a", "never", "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            if process.stdin is None or process.stdout is None:
                return None
            process.stdin.write(requests)
            process.stdin.flush()

            lines: queue.Queue[str | None] = queue.Queue()

            def _read_stdout() -> None:
                try:
                    for line in process.stdout:
                        lines.put(line)
                finally:
                    lines.put(None)

            threading.Thread(
                target=_read_stdout,
                name="SidePulseCodexProbeRead",
                daemon=True,
            ).start()
            deadline = time.monotonic() + max(0.1, float(timeout_seconds))
            captured: list[str] = []
            reply_ids: set[int] = set()
            while time.monotonic() < deadline and not {2, 3}.issubset(reply_ids):
                remaining = max(0.01, deadline - time.monotonic())
                try:
                    line = lines.get(timeout=remaining)
                except queue.Empty:
                    break
                if line is None:
                    break
                captured.append(line)
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                identifier = payload.get("id") if isinstance(payload, dict) else None
                if isinstance(identifier, int):
                    reply_ids.add(identifier)
            return "".join(captured)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        finally:
            if process is not None:
                try:
                    if process.stdin is not None:
                        process.stdin.close()
                except Exception:
                    pass
                try:
                    process.terminate()
                    process.wait(timeout=2.0)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

    stdout = (runner or _default_runner)()
    if stdout is None:
        return None
    replies: dict[int, dict] = {}
    version: str | None = None
    for line in stdout.splitlines()[:200]:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        identifier = payload.get("id")
        if isinstance(identifier, int):
            result = payload.get("result")
            replies[identifier] = result if isinstance(result, dict) else {}
    if 1 not in replies:
        return None
    agent = replies[1].get("userAgent")
    if isinstance(agent, str) and "/" in agent:
        version = agent.split("/", 1)[1].split(" ", 1)[0] or None
    used_percent: float | None = None
    resets_at: float | None = None
    window_minutes: int | None = None
    limits = replies.get(2, {}).get("rateLimits")
    if isinstance(limits, dict):
        primary = limits.get("primary")
        if isinstance(primary, dict):
            candidate = primary.get("used_percent", primary.get("usedPercent"))
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                used_percent = float(candidate)
            reset = primary.get("resets_at", primary.get("resetsAt"))
            if isinstance(reset, (int, float)) and not isinstance(reset, bool):
                resets_at = float(reset)
            duration = primary.get(
                "window_minutes",
                primary.get("windowDurationMins"),
            )
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                window_minutes = max(1, int(duration))
    authenticated: bool | None = None
    account = replies.get(3)
    if account is not None:
        has_account = bool(account.get("account") or account.get("email"))
        requires = account.get("requiresOpenaiAuth")
        if has_account:
            authenticated = True
        elif isinstance(requires, bool) and requires:
            authenticated = False
    return {
        "authenticated": authenticated,
        "used_percent": used_percent,
        "resets_at": resets_at,
        "window_minutes": window_minutes,
        "version": version,
    }


__all__ = [
    "CODEX_FRESH_SECONDS",
    "CREDENTIAL_SOURCE_FILES",
    "TRANSIENT_BACKOFF_SECONDS",
    "FailureGate",
    "RepairOutcome",
    "RepairResult",
    "codex_activity_report",
    "codex_app_server_probe",
    "connection_loss_transitions",
    "credential_fingerprint",
    "grok_auth_status",
    "newest_codex_rollout_age",
    "note_failure",
    "repair_claude_credential",
    "repair_grok_credential",
    "should_collect",
]
