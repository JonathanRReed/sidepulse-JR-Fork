"""Two Macs, one ledger: an optional, bounded, read-only peer transport.

The owner runs agents on more than one Mac and wants both visible from one
pair of eyes. This module is the whole of that transport, and it is a
VIEWER -- it reads a peer's published ledger document and nothing else.

Five laws hold this module together, each one earned by a defect this
project already paid for:

1. OPTIONAL AND INERT. Tailscale is not a dependency. With the CLI absent
   `discover_peers()` returns `()`, `refresh_peers(())` returns an empty
   result, and `merge_ledger()` with no peers returns exactly the local
   rows. Nothing about the local ledger changes when this is switched off.

2. READ ONLY, AND NOT A REMOTE SHELL. The default reader speaks the SSH
   *SFTP subsystem* (`/usr/bin/sftp host:path local`). It never runs a
   command on the peer -- not `cat`, not our own CLI, nothing. There is no
   argv shape this module can produce that carries a remote command, and a
   test asserts it. Agent control across machines is not a feature here and
   cannot be reached by mistake.

3. BOUNDED EVERYWHERE. Peer count, rows per peer, payload bytes, bytes on
   the wire (an sftp bandwidth limit whose product with the timeout is a
   hard ceiling), per-peer timeout, whole-refresh deadline, and a per-peer
   circuit breaker. An unreachable peer degrades to a stale row; it can
   never stall a refresh.

4. NO CREDENTIALS, EVER. `BatchMode=yes`, password and keyboard-interactive
   auth explicitly disabled, zero password prompts, no `-i` identity file,
   and a fixed environment allowlist. Authentication is whatever the user's
   own ssh agent and `~/.ssh/config` already do. This module never reads,
   stores, prompts for, or logs a secret -- and it never surfaces a
   subprocess's stderr, because `tailscale`'s stderr can contain `tskey-`
   auth keys and node names.

5. NO CAPACITY NUMBERS ON THE WIRE. A capacity reading may only reach a
   user-visible consumer through `capacity_authority.select_binding_lanes`,
   and a remote provider's raw percentage has no binding lane on this
   machine. The v1 wire schema therefore has no capacity field at all, and
   its exact-field validation rejects any document that grows one. Remote
   *capacity* is a later wave with an authority story; remote *agent state*
   is this one.

Sub-agents are absent from the payload entirely -- dropped by the publisher
and dropped again by the parser. They are never a row, never a light, never
an interrupt; they matter only in that they hold their parent's completion
open, which the publishing machine has already accounted for.

Remote rows are MUTED in the interrupt budget by default. A light on this
desk must mean something on this desk unless the owner asks otherwise, and
`RemoteInterruptPolicy` is where that is asked.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Protocol

from .freshness import bounded_age_seconds
from .models import MODE_PRIORITY, AgentMode, AgentStatus
from .private_io import atomic_private_write, ensure_private_directory, read_private_text
from .providers import default_state_dir
from .remote_observation import (
    RemoteObservationBatch,
    RemoteObservationReceiver,
    collect_remote_observations,
)

# --- Bounds -----------------------------------------------------------
# Every number here is a ceiling, not a target. This app has been bitten
# by unbounded state twice; assume it will be again.

#: How many peers a single refresh will ever touch.
MAX_PEERS: Final = 8
#: Rows one peer may publish. 1-5 main agents per machine is the real
#: scale; 64 is slack for a bad day, not an invitation.
MAX_ROWS_PER_PEER: Final = 64
#: Remote rows the merged ledger will ever carry, across all peers.
MAX_MERGED_REMOTE_ROWS: Final = 64
#: Ceiling on one peer's ledger document.
MAX_PAYLOAD_BYTES: Final = 64 * 1024
#: Ceiling on `tailscale status --json` output we will parse.
MAX_DISCOVERY_BYTES: Final = 512 * 1024
#: Peer entries we will consider before giving up on a huge tailnet.
MAX_DISCOVERY_ENTRIES: Final = 512

DEFAULT_DISCOVERY_TIMEOUT_SECONDS: Final = 3.0
DEFAULT_FETCH_TIMEOUT_SECONDS: Final = 4.0
#: Whole-refresh deadline. Eight peers at four seconds each would be
#: thirty-two seconds of stall; this is the number that forbids it.
DEFAULT_REFRESH_DEADLINE_SECONDS: Final = 8.0
#: Below this much remaining budget a fetch is not worth starting.
MIN_FETCH_BUDGET_SECONDS: Final = 0.5

PEER_BREAKER_TRIP_AFTER: Final = 3
PEER_BREAKER_COOLDOWN_SECONDS: Final = 120.0

#: A peer's document older than this makes its rows stale. Remote rows are
#: never proof of a live ask once they are stale.
REMOTE_STALE_AFTER_SECONDS: Final = 120.0

#: Multiplier on the derived sftp bandwidth limit so a legitimate payload
#: always completes well inside the timeout.
TRANSFER_HEADROOM: Final = 4.0
#: Hard ceiling on bytes a hostile or broken peer can land on local disk
#: within one fetch: limit_kbits * 1024 / 8 * timeout. Asserted by test.
MAX_TRANSFER_BYTES: Final = 8 * MAX_PAYLOAD_BYTES

# --- Wire document ----------------------------------------------------

REMOTE_LEDGER_DOCUMENT: Final = "sidepulse-remote-ledger"
REMOTE_LEDGER_VERSION: Final = 1
REMOTE_LEDGER_FILE_NAME: Final = "remote-ledger.json"
DEFAULT_REMOTE_LEDGER_PATH: Final = (
    "~/.local/state/sidepulse/agent-monitor/remote-ledger.json"
)

_DOCUMENT_FIELDS: Final = frozenset(
    {"document", "version", "machine", "generated_at", "rows", "truncated_rows"}
)
_ROW_FIELDS: Final = frozenset(
    {
        "agent_id",
        "display_name",
        "event_name",
        "message",
        "mode",
        "provider",
        "tool_name",
        "updated_at",
    }
)

MAX_MACHINE_CHARS: Final = 64
MAX_AGENT_ID_CHARS: Final = 200
MAX_DISPLAY_NAME_CHARS: Final = 120
MAX_PROVIDER_CHARS: Final = 32
MAX_EVENT_NAME_CHARS: Final = 64
MAX_TOOL_NAME_CHARS: Final = 64
MAX_MESSAGE_CHARS: Final = 200

# --- Closed failure vocabulary ---------------------------------------
# Failures are named from this set and nothing else. No subprocess stderr,
# no exception text, no hostname-bearing error strings ever reach a caller
# from here: `tailscale`'s stderr can carry `tskey-` auth keys.

FAILURE_UNSAFE_HOST: Final = "unsafe_host"
FAILURE_NO_TRANSPORT: Final = "no_transport"
FAILURE_BREAKER_OPEN: Final = "breaker_open"
FAILURE_DEADLINE_EXCEEDED: Final = "deadline_exceeded"
FAILURE_UNREACHABLE: Final = "unreachable"
FAILURE_TIMED_OUT: Final = "timed_out"
FAILURE_TOO_LARGE: Final = "too_large"
FAILURE_MALFORMED: Final = "malformed"
FAILURE_UNSUPPORTED_VERSION: Final = "unsupported_version"

PEER_FAILURES: Final = frozenset(
    {
        FAILURE_UNSAFE_HOST,
        FAILURE_NO_TRANSPORT,
        FAILURE_BREAKER_OPEN,
        FAILURE_DEADLINE_EXCEEDED,
        FAILURE_UNREACHABLE,
        FAILURE_TIMED_OUT,
        FAILURE_TOO_LARGE,
        FAILURE_MALFORMED,
        FAILURE_UNSUPPORTED_VERSION,
    }
)


class RemotePeerError(Exception):
    """One peer failed, named only from the closed failure vocabulary."""

    def __init__(self, failure: str) -> None:
        if failure not in PEER_FAILURES:
            raise ValueError("unknown peer failure")
        super().__init__(failure)
        self.failure = failure


# --- Host and path validation ----------------------------------------
# A hostname reaches argv. `-oProxyCommand=...` is a hostname-shaped
# argument that runs a local command, and `a;b` is a hostname-shaped
# argument that matters the moment anyone reintroduces a shell. Both are
# rejected here, before anything can use them.

_LABEL: Final = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME_RE: Final = re.compile(rf"{_LABEL}(?:\.{_LABEL})*\Z")
_MACHINE_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}\Z")
MAX_HOSTNAME_CHARS: Final = 253
MAX_REMOTE_PATH_CHARS: Final = 512

# Reserved by sftp's own `host:path` argument parsing, or by remote glob
# expansion, which would turn one read into many.
_REMOTE_PATH_FORBIDDEN: Final = frozenset(':*?[]"\'\\ \t')


def peer_host_is_safe(host: object) -> bool:
    """Whether a hostname may be placed in argv at all."""
    if type(host) is not str or not host or len(host) > MAX_HOSTNAME_CHARS:
        return False
    if host.startswith("-") or host.endswith("."):
        return False
    return _HOSTNAME_RE.fullmatch(host) is not None


def machine_name_is_safe(machine: object) -> bool:
    """Whether a display machine name is safe to store and render."""
    if type(machine) is not str or not machine:
        return False
    if len(machine) > MAX_MACHINE_CHARS:
        return False
    return _MACHINE_RE.fullmatch(machine) is not None


def remote_path_is_safe(remote_path: object) -> bool:
    """Whether a remote path may be requested from a peer.

    Absolute or `~`-anchored only, no whitespace, no glob metacharacters
    (one read must stay one read), no `:` (sftp splits `host:path` on it),
    and no `..` traversal segment.
    """
    if type(remote_path) is not str or not remote_path:
        return False
    if len(remote_path) > MAX_REMOTE_PATH_CHARS:
        return False
    if not (remote_path.startswith("/") or remote_path.startswith("~/")):
        return False
    if any(character in _REMOTE_PATH_FORBIDDEN for character in remote_path):
        return False
    if any(_is_control(character) for character in remote_path):
        return False
    return ".." not in remote_path.split("/")


def _is_control(character: str) -> bool:
    return unicodedata.category(character) in {"Cc", "Cf", "Cs", "Co", "Cn"}


def _strict_timestamp(value: object) -> datetime:
    """Parse an ISO-8601 instant, or refuse.

    `models.parse_datetime` falls back to *now* for unparseable input.
    That is right for a local restore and wrong here: a peer's garbage
    timestamp would land as "this instant" and a stale row would render
    fresh. A remote timestamp we cannot read is a malformed document.
    """
    if type(value) is not str or not value:
        raise ValueError("invalid timestamp")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError("invalid timestamp") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sanitize_text(value: object, *, limit: int) -> str:
    """Collapse a peer-supplied string to printable, bounded text."""
    if type(value) is not str:
        return ""
    cleaned = "".join(" " if _is_control(character) else character for character in value)
    return " ".join(cleaned.split())[:limit]


# --- Tailscale discovery ---------------------------------------------
# Tailscale's status schema is an EXTERNAL contract that changes without
# us. Parsing here is tolerant of unknown fields and strict about the
# handful it reads -- the opposite of the wire document below, which is
# ours and is validated exactly.

TAILSCALE_CLI_CANDIDATES: Final = (
    Path("/usr/local/bin/tailscale"),
    Path("/opt/homebrew/bin/tailscale"),
    Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
    Path("/usr/bin/tailscale"),
)
#: Peer platforms whose agents we can read. Phones and tablets do not run
#: agents and would only ever be a failed fetch.
PEER_OPERATING_SYSTEMS: Final = frozenset({"macos", "linux"})


@dataclass(frozen=True, slots=True)
class TailscalePeer:
    """One reachable peer, already validated for argv use."""

    host: str
    machine: str
    operating_system: str

    def __post_init__(self) -> None:
        if not peer_host_is_safe(self.host) or not machine_name_is_safe(self.machine):
            raise ValueError("invalid tailscale peer")


def tailscale_cli_path() -> Path | None:
    """The user's own tailscale binary, or None when it is not installed."""
    for candidate in TAILSCALE_CLI_CANDIDATES:
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        except OSError:
            continue
    return None


def tailscale_available() -> bool:
    return tailscale_cli_path() is not None


def _dns_label(value: object) -> str:
    """The first label of a tailscale DNSName, stripped of its trailing dot."""
    if type(value) is not str:
        return ""
    return value.strip().rstrip(".").split(".")[0]


def _peer_from_entry(entry: object, self_labels: frozenset[str]) -> TailscalePeer | None:
    if not isinstance(entry, Mapping):
        return None
    if entry.get("Online") is not True:
        return None
    operating_system = entry.get("OS")
    if type(operating_system) is not str:
        return None
    normalized_os = operating_system.strip().lower()
    if normalized_os not in PEER_OPERATING_SYSTEMS:
        return None
    label = _dns_label(entry.get("DNSName"))
    host_name = entry.get("HostName")
    machine = _sanitize_text(host_name, limit=MAX_MACHINE_CHARS) or label
    if not label or label in self_labels:
        return None
    if not peer_host_is_safe(label) or not machine_name_is_safe(machine):
        return None
    return TailscalePeer(host=label, machine=machine, operating_system=normalized_os)


def parse_tailscale_status(
    document: object,
    *,
    limit: int = MAX_PEERS,
) -> tuple[TailscalePeer, ...]:
    """Select online agent-capable peers from `tailscale status --json`.

    Self is excluded by DNS-label match, deterministically ordered by host,
    and capped. Unparseable input yields no peers rather than an error:
    peer discovery failing must never be louder than peers being absent.
    """
    if type(document) is str:
        try:
            document = json.loads(document)
        except (RecursionError, ValueError):
            return ()
    if not isinstance(document, Mapping):
        return ()

    self_entry = document.get("Self")
    self_labels = set()
    if isinstance(self_entry, Mapping):
        for key in ("DNSName", "HostName"):
            label = _dns_label(self_entry.get(key))
            if label:
                self_labels.add(label)

    peers = document.get("Peer")
    if not isinstance(peers, Mapping):
        return ()

    selected: dict[str, TailscalePeer] = {}
    for index, entry in enumerate(peers.values()):
        if index >= MAX_DISCOVERY_ENTRIES:
            break
        peer = _peer_from_entry(entry, frozenset(self_labels))
        if peer is not None and peer.host not in selected:
            selected[peer.host] = peer
    ordered = sorted(selected.values(), key=lambda peer: peer.host)
    return tuple(ordered[: max(0, int(limit))])


def _child_environment() -> dict[str, str]:
    """A fixed allowlist. Nothing secret is added, nothing is inherited
    that we did not name."""
    allowed = ("HOME", "PATH", "USER", "LOGNAME", "SSH_AUTH_SOCK", "TMPDIR")
    return {name: os.environ[name] for name in allowed if name in os.environ}


def _run_tailscale_cli(arguments: Sequence[str], timeout: float) -> str:
    path = tailscale_cli_path()
    if path is None:
        raise RemotePeerError(FAILURE_NO_TRANSPORT)
    try:
        completed = subprocess.run(  # fixed absolute argv, shell=False, no remote command
            [str(path), *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=max(0.1, float(timeout)),
            check=False,
            env=_child_environment(),
        )
    except subprocess.TimeoutExpired as error:
        raise RemotePeerError(FAILURE_TIMED_OUT) from error
    except (OSError, ValueError) as error:
        raise RemotePeerError(FAILURE_NO_TRANSPORT) from error
    # completed.stderr is deliberately never read, logged, or attached to
    # an exception: tailscale writes `tskey-...` auth keys and node names
    # there, and this module's failures are a closed vocabulary anyway.
    if completed.returncode != 0:
        raise RemotePeerError(FAILURE_UNREACHABLE)
    stdout = completed.stdout or ""
    if len(stdout.encode("utf-8", "ignore")) > MAX_DISCOVERY_BYTES:
        raise RemotePeerError(FAILURE_TOO_LARGE)
    return stdout


def discover_peers(
    *,
    runner: object = None,
    timeout: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    limit: int = MAX_PEERS,
) -> tuple[TailscalePeer, ...]:
    """Enumerate peers, or return `()` when Tailscale is not installed.

    `runner(arguments, timeout) -> str` is injected so tests never touch
    the network or the CLI. Every failure is silent and empty: this
    feature is optional, and an absent Tailscale must be indistinguishable
    from an empty tailnet at the call site.
    """
    invoke = runner if callable(runner) else _run_tailscale_cli
    try:
        stdout = invoke(("status", "--json"), timeout)
    except RemotePeerError:
        return ()
    except Exception:  # discovery is best-effort by design
        return ()
    return parse_tailscale_status(stdout, limit=limit)


# --- Ledger rows ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """One row of the merged ledger, local or remote.

    Remote rows carry their machine, are marked remote, and are not
    locally actionable -- clicking through to a session on another Mac is
    not something this viewer can do, and pretending otherwise would put
    the same fact on two rungs.
    """

    status: AgentStatus
    machine: str
    is_remote: bool = False
    source_agent_id: str = ""

    def __post_init__(self) -> None:
        if type(self.status) is not AgentStatus or not machine_name_is_safe(self.machine):
            raise ValueError("invalid ledger row")
        if not self.source_agent_id:
            object.__setattr__(self, "source_agent_id", self.status.agent_id)

    @property
    def key(self) -> str:
        return self.status.agent_id

    @property
    def is_actionable_locally(self) -> bool:
        return not self.is_remote

    @property
    def remote_marker(self) -> str | None:
        """The badge text for this row, or None when it is local."""
        return "Remote" if self.is_remote else None

    @property
    def ledger_label(self) -> str:
        """What the ledger shows. Remote rows always name their machine."""
        if not self.is_remote:
            return self.status.display_name
        return f"{self.status.display_name} ({self.machine})"

    @property
    def priority(self) -> int:
        return self.status.priority


def remote_agent_id(machine: str, source_agent_id: str) -> str:
    """Namespace a peer's agent id so it can never collide with a local one."""
    return f"remote:{machine}:{source_agent_id}"


@dataclass(frozen=True, slots=True)
class RemoteLedger:
    """One peer's published ledger, already validated."""

    machine: str
    generated_at: datetime
    rows: tuple[LedgerRow, ...] = ()
    truncated_rows: int = 0
    dropped_rows: int = 0
    #: What the document called itself. Advisory only -- `machine` is the
    #: peer we actually connected to, and that is what labels the rows.
    claimed_machine: str = ""

    def __post_init__(self) -> None:
        if not machine_name_is_safe(self.machine):
            raise ValueError("invalid remote ledger")
        if not self.claimed_machine:
            object.__setattr__(self, "claimed_machine", self.machine)
        if type(self.generated_at) is not datetime or self.generated_at.tzinfo is None:
            raise ValueError("invalid remote ledger")
        if len(self.rows) > MAX_ROWS_PER_PEER:
            raise ValueError("invalid remote ledger")

    def is_stale(
        self,
        now: datetime,
        *,
        stale_after_seconds: float = REMOTE_STALE_AFTER_SECONDS,
    ) -> bool:
        return bounded_age_seconds(now, self.generated_at) > stale_after_seconds


# --- Publishing -------------------------------------------------------


def _row_payload(status: AgentStatus, *, include_messages: bool) -> dict[str, object]:
    return {
        "agent_id": _sanitize_text(status.agent_id, limit=MAX_AGENT_ID_CHARS),
        "display_name": _sanitize_text(status.display_name, limit=MAX_DISPLAY_NAME_CHARS),
        "event_name": _sanitize_text(status.event_name, limit=MAX_EVENT_NAME_CHARS),
        "message": (
            _sanitize_text(status.message, limit=MAX_MESSAGE_CHARS) or None
            if include_messages
            else None
        ),
        "mode": status.mode.value,
        "provider": _sanitize_text(status.provider, limit=MAX_PROVIDER_CHARS),
        "tool_name": _sanitize_text(status.tool_name, limit=MAX_TOOL_NAME_CHARS) or None,
        "updated_at": status.updated_at.astimezone(timezone.utc).isoformat(),
    }


def build_remote_ledger_document(
    *,
    machine: str,
    statuses: Iterable[AgentStatus],
    generated_at: datetime,
    include_messages: bool = False,
    max_rows: int = MAX_ROWS_PER_PEER,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> str:
    """Serialize this machine's publishable ledger.

    Sub-agents are dropped HERE, at the publisher, so they are absent from
    the payload entirely rather than filtered at a receiver that might
    forget to. Rows are ordered by urgency and truncated to fit both the
    row cap and the byte cap; the count that was dropped is published so a
    receiver can say "and N more" instead of silently lying.

    No capacity number is emitted. A remote provider's raw reading has no
    binding lane on the receiving machine, and
    `capacity_authority.select_binding_lanes` is the only path by which a
    capacity reading may reach a user-visible consumer.
    """
    if not machine_name_is_safe(machine):
        raise ValueError("invalid machine name")
    if type(generated_at) is not datetime:
        raise ValueError("invalid generated_at")
    row_cap = max(0, min(int(max_rows), MAX_ROWS_PER_PEER))
    byte_cap = max(1, min(int(max_bytes), MAX_PAYLOAD_BYTES))

    publishable = [
        status
        for status in statuses
        if type(status) is AgentStatus and not status.is_subagent
    ]
    publishable.sort(key=lambda status: (status.priority, status.agent_id))
    truncated = max(0, len(publishable) - row_cap)
    kept = publishable[:row_cap]

    stamp = generated_at.astimezone(timezone.utc).isoformat()
    while True:
        document = {
            "document": REMOTE_LEDGER_DOCUMENT,
            "generated_at": stamp,
            "machine": machine,
            "rows": [_row_payload(status, include_messages=include_messages) for status in kept],
            "truncated_rows": truncated,
            "version": REMOTE_LEDGER_VERSION,
        }
        encoded = json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True)
        payload = f"{encoded}\n"
        if len(payload.encode("utf-8")) <= byte_cap or not kept:
            return payload
        kept.pop()
        truncated += 1


def default_remote_ledger_path(home: Path | None = None) -> Path:
    """Where this machine publishes its ledger for peers to read."""
    return default_state_dir(home) / REMOTE_LEDGER_FILE_NAME


DEFAULT_MACHINE_NAME: Final = "this-mac"


def local_machine_name(fallback: str = DEFAULT_MACHINE_NAME) -> str:
    """This machine's short name, sanitized for the wire and for argv.

    A Mac's ComputerName is free text ("Jonathan's MacBook Pro"), so the
    short hostname label is used and then held to the same rules a peer's
    name is held to. A name we would refuse from a peer is a name we do
    not publish about ourselves.
    """
    try:
        raw = socket.gethostname()
    except OSError:
        raw = ""
    label = _sanitize_text(raw, limit=MAX_MACHINE_CHARS).rstrip(".").split(".")[0]
    return label if machine_name_is_safe(label) else fallback


def publish_local_ledger(
    statuses: Iterable[AgentStatus],
    *,
    machine: str | None = None,
    generated_at: datetime | None = None,
    path: Path | None = None,
    settings: RemotePeerSettings | None = None,
) -> Path | None:
    """Write this machine's ledger where peers can read it, or do nothing.

    Returns the written path, or None when publishing is off. Publishing
    is off by default: a second Mac reading this file is something the
    owner turns on, not something that starts happening.
    """
    active = (settings or RemotePeerSettings()).normalized()
    if not active.publish_enabled:
        return None
    payload = build_remote_ledger_document(
        machine=machine or local_machine_name(),
        statuses=statuses,
        generated_at=generated_at or datetime.now(timezone.utc),
        include_messages=active.include_messages,
    )
    target = path or default_remote_ledger_path()
    atomic_private_write(target, payload)
    return target


# --- Parsing ----------------------------------------------------------


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("invalid remote ledger document")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    """Refuse NaN/Infinity during decode, not after.

    Today every field is a str, int, list or dict, so the type checks
    below would catch a constant anyway. That stops being true the moment
    this schema grows a float field: `type(float("nan")) is float` is
    True, and a NaN would sail through its own type check. Refusing at
    the JSON layer is what makes the schema safe to extend.
    """
    raise ValueError("invalid remote ledger document")


def _exact_fields(payload: object, fields: frozenset[str]) -> dict[str, object] | None:
    """The payload as a dict when its field set matches EXACTLY, else None.

    Exact, not "at least": an unknown field is a rejection. That is what
    keeps a future capacity number from riding in on this wire and
    reaching the UI without passing capacity_authority.
    """
    if type(payload) is not dict or frozenset(payload) != fields:
        return None
    return payload


def _optional_string(value: object, *, limit: int) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("invalid remote ledger row")
    return _sanitize_text(value, limit=limit) or None


def _row_from_payload(payload: object, machine: str) -> LedgerRow | None:
    """One validated remote row, or None when it must be dropped."""
    row = _exact_fields(payload, _ROW_FIELDS)
    if row is None:
        raise ValueError("invalid remote ledger row")
    source_agent_id = _sanitize_text(row["agent_id"], limit=MAX_AGENT_ID_CHARS)
    display_name = _sanitize_text(row["display_name"], limit=MAX_DISPLAY_NAME_CHARS)
    provider = _sanitize_text(row["provider"], limit=MAX_PROVIDER_CHARS)
    event_name = _sanitize_text(row["event_name"], limit=MAX_EVENT_NAME_CHARS)
    if not source_agent_id or not display_name or not provider or not event_name:
        raise ValueError("invalid remote ledger row")
    if type(row["mode"]) is not str:
        raise ValueError("invalid remote ledger row")
    try:
        mode = AgentMode(row["mode"])
    except ValueError as error:
        raise ValueError("invalid remote ledger row") from error
    updated_at = _strict_timestamp(row["updated_at"])

    namespaced = remote_agent_id(machine, source_agent_id)
    status = AgentStatus(
        provider=provider,
        agent_id=namespaced,
        display_name=display_name,
        mode=mode,
        updated_at=updated_at,
        event_name=event_name,
        session_id=None,
        cwd=None,
        tool_name=_optional_string(row["tool_name"], limit=MAX_TOOL_NAME_CHARS),
        message=_optional_string(row["message"], limit=MAX_MESSAGE_CHARS),
        # `origin` is a LAUNCH origin ("Terminal", "VS Code") that drives
        # local open actions. A remote row has no local session to open,
        # so it stays empty and the machine lives on the row instead.
        origin=None,
    )
    if status.is_subagent:
        # Belt and braces: the publisher already dropped these. A peer on
        # an older build does not get to put a sub-agent in this ledger.
        return None
    return LedgerRow(
        status=status,
        machine=machine,
        is_remote=True,
        source_agent_id=source_agent_id,
    )


def parse_remote_ledger_document(
    text: object,
    *,
    max_bytes: int = MAX_PAYLOAD_BYTES,
    machine: str | None = None,
) -> RemoteLedger:
    """Validate one peer's ledger document, exactly.

    Raises `RemotePeerError` with a vocabulary failure. Unknown fields are
    a rejection, not a shrug: exact-field validation is what keeps a
    future capacity number from arriving on this wire and reaching the UI
    without a binding lane.

    `machine` names the peer we actually connected to. When given, it --
    not the document's self-declaration -- labels every row. Identity
    comes from the channel, never from the payload: otherwise one Mac
    could publish rows that appear in the ledger under another Mac's
    name, and the ledger would stop being a ledger. It also means the
    feature does not silently die when a peer's tailscale HostName and
    its local hostname disagree, which is common and harmless.
    """
    if type(text) is not str:
        raise RemotePeerError(FAILURE_MALFORMED)
    if len(text.encode("utf-8", "ignore")) > max(1, int(max_bytes)):
        raise RemotePeerError(FAILURE_TOO_LARGE)
    try:
        document = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (RecursionError, UnicodeError, ValueError) as error:
        raise RemotePeerError(FAILURE_MALFORMED) from error

    fields = _exact_fields(document, _DOCUMENT_FIELDS)
    if fields is None:
        raise RemotePeerError(FAILURE_MALFORMED)
    if fields["document"] != REMOTE_LEDGER_DOCUMENT:
        raise RemotePeerError(FAILURE_MALFORMED)
    if fields["version"] != REMOTE_LEDGER_VERSION or type(fields["version"]) is bool:
        raise RemotePeerError(FAILURE_UNSUPPORTED_VERSION)

    claimed = fields["machine"]
    if not machine_name_is_safe(claimed):
        raise RemotePeerError(FAILURE_MALFORMED)
    if machine is None:
        effective = claimed
    elif machine_name_is_safe(machine):
        effective = machine
    else:
        raise RemotePeerError(FAILURE_MALFORMED)

    rows_payload = fields["rows"]
    truncated = fields["truncated_rows"]
    if type(rows_payload) is not list or len(rows_payload) > MAX_ROWS_PER_PEER:
        raise RemotePeerError(FAILURE_TOO_LARGE)
    if type(truncated) is not int or not 0 <= truncated <= 10_000:
        raise RemotePeerError(FAILURE_MALFORMED)
    try:
        generated_at = _strict_timestamp(fields["generated_at"])
    except ValueError as error:
        raise RemotePeerError(FAILURE_MALFORMED) from error

    rows: list[LedgerRow] = []
    dropped = 0
    seen: set[str] = set()
    for payload in rows_payload:
        try:
            row = _row_from_payload(payload, effective)
        except ValueError as error:
            raise RemotePeerError(FAILURE_MALFORMED) from error
        if row is None:
            dropped += 1
            continue
        if row.key in seen:
            dropped += 1
            continue
        seen.add(row.key)
        rows.append(row)

    return RemoteLedger(
        machine=effective,
        generated_at=generated_at,
        rows=tuple(rows),
        truncated_rows=truncated,
        dropped_rows=dropped,
        claimed_machine=claimed,
    )


# --- Read-only transport ---------------------------------------------


class PeerReader(Protocol):
    """Injected so tests never touch the network."""

    def __call__(
        self,
        host: str,
        remote_path: str,
        *,
        timeout: float,
        max_bytes: int,
    ) -> str: ...


def transfer_limit_kbits(max_bytes: int, timeout_seconds: float) -> int:
    """The sftp bandwidth limit that bounds bytes-on-disk for one fetch.

    Worst case bytes = limit * 1024 / 8 * timeout, so choosing the limit
    from (max_bytes, timeout) turns the transfer into a hard ceiling
    rather than a hope. `TRANSFER_HEADROOM` keeps a legitimate payload
    comfortably inside the window.
    """
    seconds = max(0.1, float(timeout_seconds))
    needed_bits = float(max_bytes) * 8.0 * TRANSFER_HEADROOM
    return max(1, int(math.ceil(needed_bits / (1024.0 * seconds))))


SFTP_PATH: Final = Path("/usr/bin/sftp")
#: Options that make this a read with no ambient authority: no prompts, no
#: passwords, no keyboard-interactive, no port forwarding, no connection
#: multiplexing, and no identity file of our choosing (the user's own
#: agent and ssh_config decide authentication).
_SFTP_SAFETY_OPTIONS: Final = (
    "BatchMode=yes",
    "NumberOfPasswordPrompts=0",
    "PasswordAuthentication=no",
    "KbdInteractiveAuthentication=no",
    "ClearAllForwardings=yes",
    "ControlPath=none",
    "ServerAliveInterval=2",
    "ServerAliveCountMax=2",
)


def sftp_command(
    host: str,
    remote_path: str,
    destination: Path,
    *,
    timeout: float,
    max_bytes: int,
    sftp_path: Path = SFTP_PATH,
) -> tuple[str, ...]:
    """The exact argv for one read. Pure, so a test can assert its shape.

    There is no remote command in this argv and no way to put one there:
    `sftp host:path local` uses the SSH SFTP subsystem, which fetches a
    file and cannot run anything on the peer.
    """
    if not peer_host_is_safe(host):
        raise RemotePeerError(FAILURE_UNSAFE_HOST)
    if not remote_path_is_safe(remote_path):
        raise RemotePeerError(FAILURE_UNSAFE_HOST)
    connect_timeout = max(1, int(max(1.0, float(timeout))))
    options: list[str] = []
    for option in (*_SFTP_SAFETY_OPTIONS, f"ConnectTimeout={connect_timeout}"):
        options.extend(("-o", option))
    return (
        str(sftp_path),
        "-q",
        "-l",
        str(transfer_limit_kbits(max_bytes, timeout)),
        *options,
        f"{host}:{remote_path}",
        str(destination),
    )


def _validated_sftp_path(sftp_path: Path) -> Path:
    """Reject a tampered sftp binary before running it.

    Mirrors `trusted_tools.trusted_system_tool` for a tool that allowlist
    does not yet name; the wiring patch adds `"sftp"` to it and this falls
    through to the canonical check.
    """
    try:
        from .trusted_tools import TRUSTED_SYSTEM_TOOL_PATHS, trusted_system_tool

        if "sftp" in TRUSTED_SYSTEM_TOOL_PATHS:
            return trusted_system_tool("sftp")
    except (ImportError, OSError, ValueError) as error:
        raise RemotePeerError(FAILURE_NO_TRANSPORT) from error

    try:
        info = sftp_path.lstat()
    except OSError as error:
        raise RemotePeerError(FAILURE_NO_TRANSPORT) from error
    if (
        not sftp_path.is_absolute()
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & 0o022
        or not info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    ):
        raise RemotePeerError(FAILURE_NO_TRANSPORT)
    return sftp_path


@dataclass(frozen=True, slots=True)
class SecureShellPeerReader:
    """The default reader: one file, over SFTP, with no remote command.

    Each fetch lands in its own fresh 0700 directory, which is removed
    afterwards whatever happens -- a unique destination directory is what
    stops a pre-planted symlink from redirecting the write.
    """

    scratch_dir: Path | None = None
    sftp_path: Path = SFTP_PATH

    def _scratch_root(self) -> Path:
        root = self.scratch_dir or (default_state_dir() / "remote-peers")
        return ensure_private_directory(root)

    def __call__(
        self,
        host: str,
        remote_path: str,
        *,
        timeout: float,
        max_bytes: int,
    ) -> str:
        binary = _validated_sftp_path(self.sftp_path)
        try:
            root = self._scratch_root()
        except OSError as error:
            raise RemotePeerError(FAILURE_NO_TRANSPORT) from error

        digest = hashlib.sha256(host.encode("utf-8")).hexdigest()[:16]
        try:
            workspace = Path(tempfile.mkdtemp(prefix=f"peer-{digest}-", dir=str(root)))
        except OSError as error:
            raise RemotePeerError(FAILURE_NO_TRANSPORT) from error
        destination = workspace / "ledger.json"
        try:
            argv = sftp_command(
                host,
                remote_path,
                destination,
                timeout=timeout,
                max_bytes=max_bytes,
                sftp_path=binary,
            )
            try:
                completed = subprocess.run(  # fixed absolute argv, shell=False, no remote command
                    argv,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=max(0.5, float(timeout)),
                    check=False,
                    env=_child_environment(),
                )
            except subprocess.TimeoutExpired as error:
                raise RemotePeerError(FAILURE_TIMED_OUT) from error
            except (OSError, ValueError) as error:
                raise RemotePeerError(FAILURE_NO_TRANSPORT) from error
            # completed.stderr is never read: it can name hosts and keys,
            # and every failure here is already in the vocabulary.
            if completed.returncode != 0:
                raise RemotePeerError(FAILURE_UNREACHABLE)
            try:
                return read_private_text(destination, max_bytes=max_bytes)
            except FileNotFoundError as error:
                raise RemotePeerError(FAILURE_UNREACHABLE) from error
            except UnicodeError as error:
                raise RemotePeerError(FAILURE_MALFORMED) from error
            except OSError as error:
                raise RemotePeerError(FAILURE_TOO_LARGE) from error
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


# --- Per-peer breaker -------------------------------------------------


@dataclass(frozen=True, slots=True)
class PeerBreaker:
    """Pure breaker state for one host. Open means "do not even try"."""

    host: str
    consecutive_failures: int = 0
    open_until_monotonic: float = 0.0

    def allows(self, now: float) -> bool:
        return float(now) >= self.open_until_monotonic

    def is_open(self, now: float) -> bool:
        return not self.allows(now)


def record_peer_failure(
    breaker: PeerBreaker,
    *,
    now: float,
    trip_after: int = PEER_BREAKER_TRIP_AFTER,
    cooldown_seconds: float = PEER_BREAKER_COOLDOWN_SECONDS,
) -> PeerBreaker:
    failures = max(0, int(breaker.consecutive_failures)) + 1
    open_until = (
        float(now) + float(cooldown_seconds) if failures >= max(1, int(trip_after)) else 0.0
    )
    return replace(
        breaker,
        consecutive_failures=failures,
        open_until_monotonic=open_until,
    )


def record_peer_success(breaker: PeerBreaker) -> PeerBreaker:
    return replace(breaker, consecutive_failures=0, open_until_monotonic=0.0)


def breakers_by_host(breakers: Iterable[PeerBreaker]) -> dict[str, PeerBreaker]:
    return {breaker.host: breaker for breaker in breakers if type(breaker) is PeerBreaker}


# --- Bounded refresh --------------------------------------------------


@dataclass(frozen=True, slots=True)
class PeerHealth:
    """What the ledger may honestly say about one peer."""

    machine: str
    host: str
    reachable: bool
    failure: str | None = None
    breaker_open: bool = False
    row_count: int = 0

    def __post_init__(self) -> None:
        if self.failure is not None and self.failure not in PEER_FAILURES:
            raise ValueError("invalid peer failure")


@dataclass(frozen=True, slots=True)
class PeerRefreshResult:
    ledgers: tuple[RemoteLedger, ...] = ()
    health: tuple[PeerHealth, ...] = ()
    breakers: tuple[PeerBreaker, ...] = ()
    attempted: int = 0


def refresh_peers(
    peers: Sequence[TailscalePeer],
    *,
    reader: PeerReader | None = None,
    breakers: Iterable[PeerBreaker] = (),
    now_monotonic: object = time.monotonic,
    remote_path: str = DEFAULT_REMOTE_LEDGER_PATH,
    per_peer_timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
    overall_deadline_seconds: float = DEFAULT_REFRESH_DEADLINE_SECONDS,
    max_peers: int = MAX_PEERS,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> PeerRefreshResult:
    """Fetch every peer's ledger, bounded on every axis.

    A peer that is slow eats only its own share of the deadline; a peer
    that is unreachable trips its breaker and is skipped entirely until
    the cooldown expires. Once the deadline is spent, the remaining peers
    are reported as `deadline_exceeded` WITHOUT being contacted -- that is
    the property that stops one dead Mac from stalling the refresh.
    """
    clock = now_monotonic if callable(now_monotonic) else time.monotonic
    state = breakers_by_host(breakers)
    considered = tuple(peers)[: max(0, int(max_peers))]
    if reader is None or not remote_path_is_safe(remote_path):
        # An injected reader is not obliged to validate anything, so the
        # path is checked here too rather than only inside sftp_command.
        blanket = FAILURE_NO_TRANSPORT if reader is None else FAILURE_UNSAFE_HOST
        return PeerRefreshResult(
            health=tuple(
                PeerHealth(
                    machine=peer.machine,
                    host=peer.host,
                    reachable=False,
                    failure=blanket,
                )
                for peer in considered
            ),
            breakers=tuple(state.values()),
        )

    started = float(clock())
    deadline = started + max(0.0, float(overall_deadline_seconds))
    ledgers: list[RemoteLedger] = []
    health: list[PeerHealth] = []
    attempted = 0

    for peer in considered:
        breaker = state.get(peer.host, PeerBreaker(host=peer.host))
        now = float(clock())

        if not peer_host_is_safe(peer.host):
            health.append(_unreachable(peer, FAILURE_UNSAFE_HOST))
            continue
        if breaker.is_open(now):
            health.append(_unreachable(peer, FAILURE_BREAKER_OPEN, breaker_open=True))
            continue

        remaining = deadline - now
        if remaining < MIN_FETCH_BUDGET_SECONDS:
            health.append(_unreachable(peer, FAILURE_DEADLINE_EXCEEDED))
            continue

        timeout = min(max(0.1, float(per_peer_timeout_seconds)), remaining)
        attempted += 1
        try:
            payload = reader(peer.host, remote_path, timeout=timeout, max_bytes=max_bytes)
            ledger = parse_remote_ledger_document(
                payload,
                max_bytes=max_bytes,
                machine=peer.machine,
            )
        except RemotePeerError as error:
            state[peer.host] = record_peer_failure(breaker, now=float(clock()))
            health.append(_unreachable(peer, error.failure))
            continue
        except Exception:  # an injected reader may fail any way it likes
            state[peer.host] = record_peer_failure(breaker, now=float(clock()))
            health.append(_unreachable(peer, FAILURE_UNREACHABLE))
            continue

        state[peer.host] = record_peer_success(breaker)
        ledgers.append(ledger)
        health.append(
            PeerHealth(
                machine=peer.machine,
                host=peer.host,
                reachable=True,
                row_count=len(ledger.rows),
            )
        )

    return PeerRefreshResult(
        ledgers=tuple(ledgers),
        health=tuple(health),
        breakers=tuple(state.values()),
        attempted=attempted,
    )


def _unreachable(
    peer: TailscalePeer,
    failure: str,
    *,
    breaker_open: bool = False,
) -> PeerHealth:
    return PeerHealth(
        machine=peer.machine,
        host=peer.host,
        reachable=False,
        failure=failure,
        breaker_open=breaker_open,
    )


# --- Merged view ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MergedLedger:
    """The one ledger: this desk's agents and every peer's, marked."""

    local_machine: str
    rows: tuple[LedgerRow, ...] = ()
    health: tuple[PeerHealth, ...] = ()
    dropped_remote_rows: int = 0

    @property
    def local_rows(self) -> tuple[LedgerRow, ...]:
        return tuple(row for row in self.rows if not row.is_remote)

    @property
    def remote_rows(self) -> tuple[LedgerRow, ...]:
        return tuple(row for row in self.rows if row.is_remote)

    @property
    def machines(self) -> tuple[str, ...]:
        seen: list[str] = []
        for row in self.rows:
            if row.machine not in seen:
                seen.append(row.machine)
        return tuple(seen)


def merge_ledger(
    *,
    local_statuses: Iterable[AgentStatus],
    local_machine: str,
    peer_ledgers: Iterable[RemoteLedger] = (),
    health: Iterable[PeerHealth] = (),
    now: datetime | None = None,
    stale_after_seconds: float = REMOTE_STALE_AFTER_SECONDS,
    max_remote_rows: int = MAX_MERGED_REMOTE_ROWS,
) -> MergedLedger:
    """Merge local statuses with every peer's rows into one ordered ledger.

    Ordering is urgency first, then LOCAL BEFORE REMOTE at equal urgency:
    at the same priority, the thing on this desk is the thing the owner
    can act on. Rows from a stale peer document are marked stale, which is
    also what keeps them out of the interrupt path.
    """
    if not machine_name_is_safe(local_machine):
        raise ValueError("invalid local machine name")
    moment = now or datetime.now(timezone.utc)
    remote_cap = max(0, min(int(max_remote_rows), MAX_MERGED_REMOTE_ROWS))

    rows: list[LedgerRow] = [
        LedgerRow(status=status, machine=local_machine, is_remote=False)
        for status in local_statuses
        if type(status) is AgentStatus
    ]

    remote: list[LedgerRow] = []
    for ledger in peer_ledgers:
        if type(ledger) is not RemoteLedger:
            continue
        document_stale = ledger.is_stale(moment, stale_after_seconds=stale_after_seconds)
        for row in ledger.rows:
            marked = (
                row
                if not document_stale
                else replace(row, status=replace(row.status, stale=True))
            )
            remote.append(marked)

    remote.sort(key=_row_sort_key)
    dropped = max(0, len(remote) - remote_cap)
    rows.extend(remote[:remote_cap])
    rows.sort(key=_row_sort_key)

    return MergedLedger(
        local_machine=local_machine,
        rows=tuple(rows),
        health=tuple(item for item in health if type(item) is PeerHealth),
        dropped_remote_rows=dropped,
    )


def _row_sort_key(row: LedgerRow) -> tuple[int, int, int, str, str]:
    return (
        MODE_PRIORITY.get(row.status.mode, MODE_PRIORITY[AgentMode.UNKNOWN]),
        1 if row.status.stale else 0,
        1 if row.is_remote else 0,
        row.machine,
        row.status.display_name,
    )


# --- Interrupt mute policy -------------------------------------------


@dataclass(frozen=True, slots=True)
class RemoteInterruptPolicy:
    """Which machines may take a light on THIS desk.

    Muted by default, per machine settable in both directions. This is the
    only place remote rows are allowed to reach the interrupt budget, and
    it is deliberately not the ledger: the ledger shows everything, the
    LEDs are peripheral attention for the machine you are sitting at.
    """

    default_muted: bool = True
    unmuted_machines: frozenset[str] = field(default_factory=frozenset)
    muted_machines: frozenset[str] = field(default_factory=frozenset)

    def allows_machine(self, machine: object) -> bool:
        if type(machine) is not str:
            return False
        if machine in self.muted_machines:
            return False
        if machine in self.unmuted_machines:
            return True
        return not self.default_muted

    def with_machine_muted(self, machine: str, muted: bool) -> RemoteInterruptPolicy:
        return replace(
            self,
            unmuted_machines=(
                frozenset(self.unmuted_machines - {machine})
                if muted
                else frozenset(self.unmuted_machines | {machine})
            ),
            muted_machines=(
                frozenset(self.muted_machines | {machine})
                if muted
                else frozenset(self.muted_machines - {machine})
            ),
        )


def interrupt_eligible_rows(
    merged: MergedLedger,
    policy: RemoteInterruptPolicy | None = None,
) -> tuple[LedgerRow, ...]:
    """The rows permitted to reach the interrupt budget.

    Local rows always. Remote rows only when the owner has unmuted that
    machine, and never while stale -- a stale remote row is not proof that
    anything is still waiting.
    """
    active = policy if type(policy) is RemoteInterruptPolicy else RemoteInterruptPolicy()
    eligible: list[LedgerRow] = []
    for row in merged.rows:
        if not row.is_remote:
            eligible.append(row)
            continue
        if row.status.stale:
            continue
        if active.allows_machine(row.machine):
            eligible.append(row)
    return tuple(eligible)


def interrupt_eligible_statuses(
    merged: MergedLedger,
    policy: RemoteInterruptPolicy | None = None,
) -> tuple[AgentStatus, ...]:
    """The same selection, shaped for consumers that take statuses."""
    return tuple(row.status for row in interrupt_eligible_rows(merged, policy))


# --- Settings ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RemotePeerSettings:
    """Everything the owner can turn on, off, or dial. Off by default."""

    enabled: bool = False
    publish_enabled: bool = False
    include_messages: bool = False
    remote_interrupts_muted: bool = True
    unmuted_machines: tuple[str, ...] = ()
    muted_machines: tuple[str, ...] = ()
    max_peers: int = MAX_PEERS
    per_peer_timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS
    refresh_deadline_seconds: float = DEFAULT_REFRESH_DEADLINE_SECONDS
    remote_ledger_path: str = DEFAULT_REMOTE_LEDGER_PATH

    def normalized(self) -> RemotePeerSettings:
        return RemotePeerSettings(
            enabled=bool(self.enabled),
            publish_enabled=bool(self.publish_enabled),
            include_messages=bool(self.include_messages),
            remote_interrupts_muted=bool(self.remote_interrupts_muted),
            unmuted_machines=_machine_tuple(self.unmuted_machines),
            muted_machines=_machine_tuple(self.muted_machines),
            max_peers=max(0, min(int(self.max_peers), MAX_PEERS)),
            per_peer_timeout_seconds=_clamp(
                self.per_peer_timeout_seconds, 0.5, DEFAULT_FETCH_TIMEOUT_SECONDS * 4
            ),
            refresh_deadline_seconds=_clamp(
                self.refresh_deadline_seconds, 1.0, DEFAULT_REFRESH_DEADLINE_SECONDS * 4
            ),
            remote_ledger_path=(
                self.remote_ledger_path
                if remote_path_is_safe(self.remote_ledger_path)
                else DEFAULT_REMOTE_LEDGER_PATH
            ),
        )

    def interrupt_policy(self) -> RemoteInterruptPolicy:
        normalized = self.normalized()
        return RemoteInterruptPolicy(
            default_muted=normalized.remote_interrupts_muted,
            unmuted_machines=frozenset(normalized.unmuted_machines),
            muted_machines=frozenset(normalized.muted_machines),
        )

    def to_dict(self) -> dict[str, object]:
        normalized = self.normalized()
        return {
            "enabled": normalized.enabled,
            "publish_enabled": normalized.publish_enabled,
            "include_messages": normalized.include_messages,
            "remote_interrupts_muted": normalized.remote_interrupts_muted,
            "unmuted_machines": list(normalized.unmuted_machines),
            "muted_machines": list(normalized.muted_machines),
            "max_peers": normalized.max_peers,
            "per_peer_timeout_seconds": normalized.per_peer_timeout_seconds,
            "refresh_deadline_seconds": normalized.refresh_deadline_seconds,
            "remote_ledger_path": normalized.remote_ledger_path,
        }

    @classmethod
    def from_dict(cls, raw: object) -> RemotePeerSettings:
        if not isinstance(raw, Mapping):
            return cls()
        defaults = cls()
        try:
            return cls(
                enabled=bool(raw.get("enabled", defaults.enabled)),
                publish_enabled=bool(raw.get("publish_enabled", defaults.publish_enabled)),
                include_messages=bool(raw.get("include_messages", defaults.include_messages)),
                remote_interrupts_muted=bool(
                    raw.get("remote_interrupts_muted", defaults.remote_interrupts_muted)
                ),
                unmuted_machines=_machine_tuple(raw.get("unmuted_machines", ())),
                muted_machines=_machine_tuple(raw.get("muted_machines", ())),
                max_peers=int(raw.get("max_peers", defaults.max_peers)),
                per_peer_timeout_seconds=float(
                    raw.get("per_peer_timeout_seconds", defaults.per_peer_timeout_seconds)
                ),
                refresh_deadline_seconds=float(
                    raw.get("refresh_deadline_seconds", defaults.refresh_deadline_seconds)
                ),
                remote_ledger_path=str(
                    raw.get("remote_ledger_path", defaults.remote_ledger_path)
                ),
            ).normalized()
        except (TypeError, ValueError):
            return defaults


def _machine_tuple(values: object) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Iterable):
        return ()
    ordered: list[str] = []
    for value in values:
        if machine_name_is_safe(value) and value not in ordered:
            ordered.append(value)
        if len(ordered) >= MAX_PEERS:
            break
    return tuple(ordered)


def _clamp(value: object, low: float, high: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return low
    if not math.isfinite(number):
        return low
    return max(low, min(high, number))


# --- One-call facade --------------------------------------------------


def collect_remote_ledgers(
    *,
    reader: PeerReader | None = None,
    runner: object = None,
    breakers: Iterable[PeerBreaker] = (),
    settings: RemotePeerSettings | None = None,
    now_monotonic: object = time.monotonic,
) -> PeerRefreshResult:
    """Discover and fetch in one call, inert when disabled or absent.

    Returns an empty result -- not an error, not a partial ledger -- when
    the feature is off or Tailscale is not installed.
    """
    active = (settings or RemotePeerSettings()).normalized()
    if not active.enabled:
        return PeerRefreshResult()
    peers = discover_peers(runner=runner, limit=active.max_peers)
    if not peers:
        return PeerRefreshResult(breakers=tuple(breakers_by_host(breakers).values()))
    return refresh_peers(
        peers,
        reader=reader if reader is not None else SecureShellPeerReader(),
        breakers=breakers,
        now_monotonic=now_monotonic,
        remote_path=active.remote_ledger_path,
        per_peer_timeout_seconds=active.per_peer_timeout_seconds,
        overall_deadline_seconds=active.refresh_deadline_seconds,
        max_peers=active.max_peers,
    )


def collect_authenticated_remote_observations(
    *,
    event_stream: object | None,
    receiver: RemoteObservationReceiver,
    now: float | None = None,
    now_monotonic: Callable[[], float] = time.monotonic,
) -> RemoteObservationBatch:
    """Collect a bounded read-only event stream with no command fallback.

    The legacy ledger viewer and the live observation plane intentionally
    remain separate authorities. This facade exposes only the authenticated
    event-stream protocol. It has no remote command, shell, identity-file, or
    generic RPC parameter to accidentally widen later.
    """

    return collect_remote_observations(
        event_stream=event_stream,
        receiver=receiver,
        now=now,
        monotonic=now_monotonic,
    )
