"""Loopback-only ingest for agents that are not running on this machine.

The owner runs code-review agents in the cloud (Claude / GPT) and wants them
in the same ledger as the local ones. There is no hook process on those hosts
to append to a private log, so they need a socket to talk to -- and a socket
that any local process can reach is a socket that can lie about agent state.

Four rules shape everything below.

**One event model.** A cloud agent produces the same canonical `HookEvent`
this app already reduces (`models.HookEvent`), through the same event-name
vocabulary (`providers.canonical_event_name`) and the same explicit-status and
origin channels (`sidepulse_status`, `agent_origin`) the local hooks use. No
second shape, no second reducer, no second authority.

**Loopback or nothing.** The listener binds 127.0.0.1 and refuses to be
constructed with any other host. The peer address, the `Host` header, and the
absence of an `Origin` header are each re-checked per request, so neither a
route change, a DNS-rebinding page, nor a browser form can reach it.

**A shared secret, because "local" is not "trusted".** Any process running as
this user can connect to a loopback port. Injecting a fake `Stop` into the
ledger would make the owner believe a review finished. So every request
carries a 256-bit bearer token generated into the state dir with the same
0600/0700 discipline as everything else here, compared in constant time, and
never written to a log, a repr, or a response body.

**Bounds before buffers.** Body bytes, events per second, distinct sessions and
queue depth are all capped, and every cap rejects rather than grows.

Content discipline: the wire accepts a status, a session identity and a
display name. Unknown fields are refused outright -- not ignored -- so a future
sender cannot quietly start shipping transcripts through this door.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import queue
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .models import HookEvent, parse_datetime
from .private_io import atomic_private_write, ensure_private_file, read_private_text
from .providers import HOOK_PROVIDERS, canonical_event_name, default_state_dir

# ---------------------------------------------------------------------------
# Wire surface
# ---------------------------------------------------------------------------

WIRE_VERSION = 1
INGEST_PATH = "/v1/agent-event"
LOOPBACK_HOST = "127.0.0.1"
TOKEN_FILE_NAME = "cloud-ingest.token"
TOKEN_BYTES = 32
CLOUD_INGEST_ENV_VAR = "SIDEPULSE_CLOUD_INGEST"
ORIGIN_SOURCE = "cloud-ingest"
BEARER_PREFIX = "bearer "

REQUIRED_FIELDS = frozenset({"version", "provider", "session_id", "event"})
OPTIONAL_FIELDS = frozenset(
    {"agent_id", "status", "display_name", "origin", "occurred_at"}
)
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

# Matches provider_contracts._OPAQUE_SOURCE_IDENTIFIER and the 64-char bound
# in provider_facts, so an identity this module accepts is always an identity
# the canonical plane can key work on. Validating it here rather than letting
# the reducer drop it keeps the rejection visible to the sender.
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]*\Z")
MAX_IDENTIFIER_CHARS = 64
MAX_DISPLAY_NAME_CHARS = 96
MAX_ORIGIN_CHARS = 48


class IngestReason(str, Enum):
    """Why one request ended the way it did. Never carries payload content."""

    ACCEPTED = "accepted"
    DISABLED = "disabled"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    FORBIDDEN_PEER = "forbidden_peer"
    FORBIDDEN_HOST = "forbidden_host"
    FORBIDDEN_ORIGIN = "forbidden_origin"
    UNAUTHENTICATED = "unauthenticated"
    LENGTH_REQUIRED = "length_required"
    BODY_TOO_LARGE = "body_too_large"
    MALFORMED = "malformed"
    UNKNOWN_FIELD = "unknown_field"
    UNKNOWN_PROVIDER = "unknown_provider"
    UNKNOWN_EVENT = "unknown_event"
    INVALID_IDENTITY = "invalid_identity"
    INVALID_TIME = "invalid_time"
    RATE_LIMITED = "rate_limited"
    SESSION_LIMIT = "session_limit"
    QUEUE_FULL = "queue_full"


_STATUS_FOR_REASON: dict[IngestReason, int] = {
    IngestReason.ACCEPTED: 202,
    IngestReason.DISABLED: 503,
    IngestReason.NOT_FOUND: 404,
    IngestReason.METHOD_NOT_ALLOWED: 405,
    IngestReason.FORBIDDEN_PEER: 403,
    IngestReason.FORBIDDEN_HOST: 403,
    IngestReason.FORBIDDEN_ORIGIN: 403,
    IngestReason.UNAUTHENTICATED: 401,
    IngestReason.LENGTH_REQUIRED: 411,
    IngestReason.BODY_TOO_LARGE: 413,
    IngestReason.MALFORMED: 400,
    IngestReason.UNKNOWN_FIELD: 400,
    IngestReason.UNKNOWN_PROVIDER: 400,
    IngestReason.UNKNOWN_EVENT: 400,
    IngestReason.INVALID_IDENTITY: 400,
    IngestReason.INVALID_TIME: 400,
    IngestReason.RATE_LIMITED: 429,
    IngestReason.SESSION_LIMIT: 429,
    IngestReason.QUEUE_FULL: 503,
}


@dataclass(frozen=True, slots=True)
class IngestResponse:
    reason: IngestReason

    @property
    def status(self) -> int:
        return _STATUS_FOR_REASON[self.reason]

    @property
    def accepted(self) -> bool:
        return self.reason is IngestReason.ACCEPTED

    def body(self) -> bytes:
        return json.dumps(
            {"accepted": self.accepted, "reason": self.reason.value},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CloudIngestLimits:
    """Every one of these rejects. None of them grows a buffer."""

    max_body_bytes: int = 2048
    max_events_per_second: float = 10.0
    burst_events: int = 20
    max_sessions: int = 64
    session_idle_seconds: float = 3600.0
    max_queue_events: int = 256
    max_future_skew_seconds: float = 300.0
    max_past_skew_seconds: float = 86400.0

    def __post_init__(self) -> None:
        if not (
            1 <= self.max_body_bytes <= 1024 * 1024
            and self.max_events_per_second > 0
            and self.burst_events >= 1
            and self.max_sessions >= 1
            and self.session_idle_seconds > 0
            and self.max_queue_events >= 1
            and self.max_future_skew_seconds >= 0
            and self.max_past_skew_seconds >= 0
        ):
            raise ValueError("invalid cloud ingest limits")


@dataclass(frozen=True, slots=True)
class CloudIngestConfig:
    """Off unless someone says otherwise, in code, at construction."""

    enabled: bool = False
    host: str = LOOPBACK_HOST
    port: int = 0
    limits: CloudIngestLimits = field(default_factory=CloudIngestLimits)


def cloud_ingest_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Explicit opt-in. Absent, empty and unrecognised all mean off."""
    active = os.environ if env is None else env
    value = str(active.get(CLOUD_INGEST_ENV_VAR, "")).strip().casefold()
    return value in {"1", "true", "yes", "on"}


class _TokenBucket:
    def __init__(self, rate: float, burst: int) -> None:
        self._rate = float(rate)
        self._burst = float(burst)
        self._tokens = float(burst)
        self._updated: float | None = None

    def take(self, now: float) -> bool:
        if self._updated is None:
            self._updated = now
        elapsed = max(0.0, now - self._updated)
        self._updated = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True


class _SessionTable:
    """Bounded set of live cloud session identities.

    Expiry is checked at admission rather than on a timer: an ingest that is
    never called again must not keep a thread alive, and an ingest that is
    hammered must not grow. A new identity arriving at capacity is refused --
    evicting a live session to make room would silently drop a real agent.
    """

    def __init__(self, max_sessions: int, idle_seconds: float) -> None:
        self._max = int(max_sessions)
        self._idle = float(idle_seconds)
        self._seen: dict[str, float] = {}

    def admit(self, key: str, now: float) -> bool:
        for stale in [
            known
            for known, last in self._seen.items()
            if now - last > self._idle and known != key
        ]:
            del self._seen[stale]
        if key not in self._seen and len(self._seen) >= self._max:
            return False
        self._seen[key] = now
        return True

    def __len__(self) -> int:
        return len(self._seen)


# ---------------------------------------------------------------------------
# Shared secret
# ---------------------------------------------------------------------------


def default_token_path(home: Path | None = None) -> Path:
    return default_state_dir(home) / TOKEN_FILE_NAME


def _valid_token(value: object) -> str | None:
    if type(value) is not str:
        return None
    text = value.strip()
    if not 16 <= len(text) <= 512 or any(
        character.isspace() or not character.isprintable() for character in text
    ):
        return None
    return text


def read_ingest_token(path: Path) -> str | None:
    """Read the shared secret, or None. Never raises the content into an error."""
    try:
        raw = read_private_text(Path(path), max_bytes=4096)
    except (OSError, UnicodeError, ValueError):
        return None
    return _valid_token(raw)


def ensure_ingest_token(path: Path) -> str:
    """Return this install's secret, generating one on first use.

    Written through `atomic_private_write` and then re-tightened with
    `ensure_private_file`, so the secret is never briefly world-readable and
    never lands in a partially written file another process could read.
    """
    target = Path(path)
    existing = read_ingest_token(target)
    if existing is not None:
        ensure_private_file(target)
        return existing
    return rotate_ingest_token(target)


def rotate_ingest_token(path: Path) -> str:
    target = Path(path)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    atomic_private_write(target, token + "\n")
    ensure_private_file(target)
    return token


# ---------------------------------------------------------------------------
# Loopback checks
# ---------------------------------------------------------------------------


def host_without_port(value: str) -> str:
    text = value.strip()
    if text.startswith("["):
        closing = text.find("]")
        return text[1:closing] if closing > 0 else text
    if text.count(":") == 1:
        return text.split(":", 1)[0]
    return text


def is_loopback_host(value: object) -> bool:
    """True only for the local machine's own address literals.

    `localhost` is allowed because a sender legitimately writes it; every
    other name is refused, which is what stops a DNS-rebinding page (whose
    `Host` header carries the attacker's domain) from reaching this port.
    """
    if type(value) is not str or not value.strip():
        return False
    text = host_without_port(value)
    if text.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Canonical event
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CloudAgentEvent:
    provider: str
    session_id: str
    agent_id: str | None
    event_name: str
    status: str | None
    display_name: str | None
    origin: str
    occurred_at: datetime

    @property
    def status_key(self) -> str:
        if self.agent_id:
            return f"{self.provider}:agent:{self.agent_id}"
        return f"{self.provider}:session:{self.session_id}"

    @property
    def session_key(self) -> str:
        return f"{self.provider}:session:{self.session_id}"


def _sanitized_label(value: object, *, max_chars: int) -> str | None:
    if type(value) is not str:
        return None
    collapsed = " ".join(
        "".join(character for character in value if character.isprintable()).split()
    )
    if not collapsed:
        return None
    return collapsed[:max_chars].rstrip() or None


def _valid_identifier(value: object) -> str | None:
    if type(value) is not str:
        return None
    text = value.strip()
    if not 1 <= len(text) <= MAX_IDENTIFIER_CHARS or _IDENTIFIER.fullmatch(text) is None:
        return None
    return text


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("invalid cloud ingest document")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid cloud ingest document")


def decode_cloud_document(body: bytes) -> object | None:
    """Strict JSON: no duplicate keys, no NaN/Infinity, no non-string keys."""
    try:
        return json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, UnicodeError, ValueError, RecursionError):
        return None


def parse_cloud_event(
    document: object,
    *,
    limits: CloudIngestLimits,
    now_epoch: float,
) -> CloudAgentEvent | IngestReason:
    """Validate one wire document into a canonical cloud event, or say why not."""
    if type(document) is not dict:
        return IngestReason.MALFORMED
    keys = frozenset(document)
    if not keys <= ALLOWED_FIELDS:
        return IngestReason.UNKNOWN_FIELD
    if not REQUIRED_FIELDS <= keys:
        return IngestReason.MALFORMED
    # `type(...) is int` rather than isinstance: JSON `true` is a bool, and
    # bool == 1 would otherwise sail through the version gate.
    if type(document["version"]) is not int or document["version"] != WIRE_VERSION:
        return IngestReason.MALFORMED

    provider = document["provider"]
    if type(provider) is not str or provider not in HOOK_PROVIDERS:
        return IngestReason.UNKNOWN_PROVIDER

    event_name = canonical_event_name(document["event"])
    if event_name is None:
        return IngestReason.UNKNOWN_EVENT

    session_id = _valid_identifier(document["session_id"])
    if session_id is None:
        return IngestReason.INVALID_IDENTITY
    agent_id: str | None = None
    if document.get("agent_id") is not None:
        agent_id = _valid_identifier(document["agent_id"])
        if agent_id is None:
            return IngestReason.INVALID_IDENTITY

    status = document.get("status")
    if status is not None:
        status = _sanitized_label(status, max_chars=MAX_IDENTIFIER_CHARS)
        if status is None:
            return IngestReason.MALFORMED

    display_name = _sanitized_label(
        document.get("display_name"), max_chars=MAX_DISPLAY_NAME_CHARS
    )
    if document.get("display_name") is not None and display_name is None:
        return IngestReason.MALFORMED

    origin = _sanitized_label(document.get("origin"), max_chars=MAX_ORIGIN_CHARS)
    if document.get("origin") is not None and origin is None:
        return IngestReason.MALFORMED

    occurred_at = _event_time(document.get("occurred_at"), limits, now_epoch)
    if occurred_at is None:
        return IngestReason.INVALID_TIME

    return CloudAgentEvent(
        provider=provider,
        session_id=session_id,
        agent_id=agent_id,
        event_name=event_name,
        status=status,
        display_name=display_name,
        origin=origin or default_origin_label(provider),
        occurred_at=occurred_at,
    )


def default_origin_label(provider: str) -> str:
    return f"{provider.title()} Cloud"


def _event_time(
    value: object,
    limits: CloudIngestLimits,
    now_epoch: float,
) -> datetime | None:
    """Strict ISO-8601, bounded on both sides of this machine's clock.

    `models.parse_datetime` silently substitutes "now" for anything it cannot
    read, which is right for a log line we already trust and wrong for a
    stranger's request body -- so the string is validated here first. The skew
    bounds matter because a far-future timestamp is how a caller would pin a
    row at the top of the ledger forever.
    """
    if value is None:
        return datetime.fromtimestamp(now_epoch, timezone.utc)
    if type(value) is not str or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return None
    parsed = parse_datetime(value)
    try:
        delta = parsed.timestamp() - now_epoch
    except (OSError, OverflowError, ValueError):
        return None
    if delta > limits.max_future_skew_seconds or -delta > limits.max_past_skew_seconds:
        return None
    return parsed


def hook_event_from_cloud_event(event: CloudAgentEvent) -> HookEvent:
    """Project one cloud event onto the app's existing canonical event model.

    Every field below is a channel the local hook path already produces and
    the collector already reads:

    * `hook_event_name` / `session_id` / `agent_id` -- identity and lifecycle,
      including the sub-agent shape (`provider:agent:<id>`) whose only job is
      to hold its parent's completion open.
    * `sidepulse_status` -- the explicit-mode channel `collector.
      explicit_mode_for_record` already honours, so a cloud agent can state
      "blocked" without this module inventing a mode vocabulary.
    * `agent_origin*` -- the origin channel `origin.origin_label_from_payload`
      already reads, so a cloud row is labelled "Claude Cloud", not mistaken
      for a session on this Mac.
    * `prompt` -- the *only* session-naming channel in this codebase
      (`collector.title_from_event`), and it is read on `UserPromptSubmit`
      alone. A cloud sender therefore names its session by announcing it with
      `UserPromptSubmit`; the collector's per-session metadata carries that
      name onto every later event.
    """
    if type(event) is not CloudAgentEvent:
        raise ValueError("invalid cloud agent event")
    raw: dict[str, object] = {
        "hook_event_name": event.event_name,
        "session_id": event.session_id,
        "logged_at": event.occurred_at.isoformat(),
        "agent_origin": event.origin,
        "agent_origin_kind": f"{event.provider}_cloud",
        "agent_origin_source": ORIGIN_SOURCE,
        "agent_origin_confidence": "explicit",
        "source": ORIGIN_SOURCE,
    }
    if event.agent_id is not None:
        raw["agent_id"] = event.agent_id
    if event.status is not None:
        raw["sidepulse_status"] = event.status
    if event.display_name is not None:
        raw["prompt"] = event.display_name
    return HookEvent(
        provider=event.provider,
        logged_at=event.occurred_at,
        event_name=event.event_name,
        raw=raw,
        session_id=event.session_id,
        agent_id=event.agent_id,
        message=None,
        origin=event.origin,
    )


# ---------------------------------------------------------------------------
# The socket-free core
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CloudIngestStats:
    accepted: int
    rejected: int
    queued: int
    sessions: int
    reasons: tuple[tuple[str, int], ...]


class CloudIngest:
    """Admission control and a bounded queue. Binds nothing, logs nothing.

    Every check a request must pass lives here so it can be tested without a
    socket; `CloudIngestServer` is a thin HTTP adapter over `handle`.
    """

    def __init__(
        self,
        *,
        token: str,
        config: CloudIngestConfig | None = None,
    ) -> None:
        checked = _valid_token(token)
        if checked is None:
            raise ValueError("invalid cloud ingest token")
        self._token = checked.encode("utf-8")
        self.config = config or CloudIngestConfig()
        limits = self.config.limits
        self._bucket = _TokenBucket(limits.max_events_per_second, limits.burst_events)
        self._sessions = _SessionTable(limits.max_sessions, limits.session_idle_seconds)
        self._queue: queue.Queue[CloudAgentEvent] = queue.Queue(
            maxsize=limits.max_queue_events
        )
        self._lock = threading.Lock()
        self._accepted = 0
        self._reasons: dict[str, int] = {}

    def __repr__(self) -> str:  # pragma: no cover - defensive, but cheap
        # The secret never reaches a traceback, a test dump or a log line.
        return f"CloudIngest(enabled={self.config.enabled}, token=<redacted>)"

    # -- admission ---------------------------------------------------------

    def handle(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        peer_host: str,
        read_body: Callable[[int], bytes],
        now: float | None = None,
    ) -> IngestResponse:
        moment = time.time() if now is None else float(now)
        reason = self._admit(
            method=method,
            path=path,
            headers=headers,
            peer_host=peer_host,
            read_body=read_body,
            now=moment,
        )
        with self._lock:
            if reason is IngestReason.ACCEPTED:
                self._accepted += 1
            else:
                self._reasons[reason.value] = self._reasons.get(reason.value, 0) + 1
        return IngestResponse(reason)

    def _admit(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        peer_host: str,
        read_body: Callable[[int], bytes],
        now: float,
    ) -> IngestReason:
        if not self.config.enabled:
            return IngestReason.DISABLED
        # Defence in depth: the listener binds loopback, but a request that
        # somehow arrives from elsewhere is refused before anything is read.
        if not is_loopback_host(peer_host):
            return IngestReason.FORBIDDEN_PEER
        lookup = _HeaderLookup(headers)
        if method.upper() != "POST":
            return IngestReason.METHOD_NOT_ALLOWED
        if path.split("?", 1)[0] != INGEST_PATH:
            return IngestReason.NOT_FOUND
        if lookup.has_duplicate_security_header:
            return IngestReason.MALFORMED
        if not is_loopback_host(lookup.get("host") or ""):
            return IngestReason.FORBIDDEN_HOST
        # A browser attaches Origin to every cross-site request it is allowed
        # to make. We never answer one, and we never emit a CORS header.
        if lookup.get("origin"):
            return IngestReason.FORBIDDEN_ORIGIN
        if not self._authenticated(lookup.get("authorization")):
            return IngestReason.UNAUTHENTICATED

        declared = lookup.get("content-length")
        if declared is None or not declared.strip().isdigit():
            return IngestReason.LENGTH_REQUIRED
        length = int(declared.strip())
        if length > self.config.limits.max_body_bytes:
            return IngestReason.BODY_TOO_LARGE
        try:
            body = read_body(length)
        except (OSError, ValueError):
            return IngestReason.MALFORMED
        if type(body) is not bytes or len(body) != length:
            return IngestReason.MALFORMED

        if not self._bucket.take(now):
            return IngestReason.RATE_LIMITED

        document = decode_cloud_document(body)
        if document is None:
            return IngestReason.MALFORMED
        parsed = parse_cloud_event(
            document, limits=self.config.limits, now_epoch=now
        )
        if type(parsed) is not CloudAgentEvent:
            return parsed
        if not self._sessions.admit(parsed.session_key, now):
            return IngestReason.SESSION_LIMIT
        try:
            self._queue.put_nowait(parsed)
        except queue.Full:
            return IngestReason.QUEUE_FULL
        return IngestReason.ACCEPTED

    def _authenticated(self, header: str | None) -> bool:
        if type(header) is not str:
            return False
        text = header.strip()
        if text[: len(BEARER_PREFIX)].casefold() != BEARER_PREFIX:
            return False
        return hmac.compare_digest(
            text[len(BEARER_PREFIX) :].strip().encode("utf-8"), self._token
        )

    # -- delivery ----------------------------------------------------------

    def wait_for_event(self, timeout: float) -> CloudAgentEvent | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self, limit: int | None = None) -> tuple[CloudAgentEvent, ...]:
        drained: list[CloudAgentEvent] = []
        while limit is None or len(drained) < limit:
            try:
                drained.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return tuple(drained)

    def deliver(
        self,
        sink: Callable[[HookEvent], None],
        *,
        limit: int | None = None,
    ) -> int:
        delivered = 0
        for event in self.drain(limit):
            try:
                sink(hook_event_from_cloud_event(event))
            except Exception:
                continue
            delivered += 1
        return delivered

    def stats(self) -> CloudIngestStats:
        with self._lock:
            reasons = tuple(sorted(self._reasons.items()))
            accepted = self._accepted
            rejected = sum(count for _, count in reasons)
        return CloudIngestStats(
            accepted=accepted,
            rejected=rejected,
            queued=self._queue.qsize(),
            sessions=len(self._sessions),
            reasons=reasons,
        )


_SECURITY_HEADERS = frozenset({"authorization", "host", "origin", "content-length"})


class _HeaderLookup:
    """Case-insensitive read over whatever mapping the adapter hands us.

    Duplicates are tracked, not merged. HTTP allows repeated headers and
    different parsers disagree about which one wins -- so a request that sends
    `Authorization` or `Content-Length` twice is refused rather than
    interpreted, which is the entire class of request-smuggling confusion.
    """

    def __init__(self, headers: Mapping[str, str]) -> None:
        values: dict[str, str] = {}
        duplicated: set[str] = set()
        for key, value in headers.items() if headers else ():
            name = str(key).casefold()
            if name in values:
                duplicated.add(name)
                continue
            values[name] = value
        self._values = values
        self.duplicated = frozenset(duplicated)

    @property
    def has_duplicate_security_header(self) -> bool:
        return bool(self.duplicated & _SECURITY_HEADERS)

    def get(self, name: str) -> str | None:
        value = self._values.get(name.casefold())
        return value if type(value) is str else None


# ---------------------------------------------------------------------------
# The HTTP adapter
# ---------------------------------------------------------------------------

_HANDLER_TIMEOUT_SECONDS = 5.0
_MAX_CONCURRENT_CONNECTIONS = 8
_DISPATCH_POLL_SECONDS = 0.1
_SERVER_STOP_TIMEOUT_SECONDS = 2.0


class _IngestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    timeout = _HANDLER_TIMEOUT_SECONDS
    server_version = "SidePulseCloudIngest"
    sys_version = ""

    def log_message(self, *_args, **_kwargs) -> None:
        """Silence. The default writes every request line to stderr, and an
        ingest endpoint's request lines are exactly what must not be logged."""

    def do_POST(self) -> None:
        self._respond()

    def do_GET(self) -> None:
        self._respond()

    def do_PUT(self) -> None:
        self._respond()

    def do_DELETE(self) -> None:
        self._respond()

    def _respond(self) -> None:
        ingest: CloudIngest = self.server.ingest  # type: ignore[attr-defined]
        response = ingest.handle(
            method=self.command or "",
            path=self.path or "",
            headers=self.headers,
            peer_host=str(self.client_address[0]) if self.client_address else "",
            read_body=self.rfile.read,
        )
        payload = response.body()
        try:
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            return


class _IngestHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], ingest: CloudIngest) -> None:
        self.ingest = ingest
        self._slots = threading.BoundedSemaphore(_MAX_CONCURRENT_CONNECTIONS)
        super().__init__(address, _IngestHandler)

    def process_request(self, request, client_address) -> None:
        # Reject rather than buffer: an unbounded thread-per-connection server
        # is a local denial of service with extra steps.
        if not self._slots.acquire(blocking=False):
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def shutdown_request(self, request) -> None:
        try:
            super().shutdown_request(request)
        finally:
            try:
                self._slots.release()
            except ValueError:
                pass

    def handle_error(self, request, client_address) -> None:
        """Never print a traceback: a request body could reach stderr."""


class CloudIngestServer:
    """Loopback HTTP listener plus one dispatcher thread.

    Construction refuses any non-loopback host, so a config typo or a future
    "make it reachable from the other Mac" edit cannot quietly expose agent
    state to the network -- that road goes through an explicit new transport,
    not through this constructor.
    """

    def __init__(
        self,
        sink: Callable[[HookEvent], None],
        *,
        token: str,
        config: CloudIngestConfig | None = None,
    ) -> None:
        self.config = config or CloudIngestConfig()
        if not is_loopback_host(self.config.host):
            raise ValueError(f"refusing non-loopback ingest host: {self.config.host!r}")
        if not self.config.enabled:
            raise ValueError("cloud ingest is off; enable it explicitly to start")
        self.sink = sink
        self.ingest = CloudIngest(token=token, config=self.config)
        self._server: _IngestHTTPServer | None = None
        self._accept_thread: threading.Thread | None = None
        self._dispatch_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.RLock()

    @property
    def address(self) -> tuple[str, int] | None:
        server = self._server
        return None if server is None else server.server_address[:2]

    def start(self) -> tuple[str, int]:
        with self._lock:
            if self._running or self._server is not None:
                raise OSError("cloud ingest server is already running")
            server = _IngestHTTPServer(
                (self.config.host, int(self.config.port)), self.ingest
            )
            bound = server.server_address[:2]
            if not is_loopback_host(str(bound[0])):  # pragma: no cover - defensive
                server.server_close()
                raise OSError("cloud ingest bound a non-loopback address")
            self._server = server
            self._running = True
            self._accept_thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": _DISPATCH_POLL_SECONDS},
                name="sidepulse-cloud-ingest-accept",
                daemon=True,
            )
            self._dispatch_thread = threading.Thread(
                target=self._dispatch,
                name="sidepulse-cloud-ingest-dispatch",
                daemon=True,
            )
            self._accept_thread.start()
            self._dispatch_thread.start()
            return (str(bound[0]), int(bound[1]))

    def stop(self) -> None:
        with self._lock:
            server = self._server
            accept_thread = self._accept_thread
            dispatch_thread = self._dispatch_thread
            self._running = False
            self._server = None
            self._accept_thread = None
            self._dispatch_thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        current = threading.current_thread()
        for worker in (accept_thread, dispatch_thread):
            if worker is not None and worker is not current:
                worker.join(_SERVER_STOP_TIMEOUT_SECONDS)
        # Anything still queued when the app goes away is dropped, not
        # replayed later: a stale "working" restored an hour after the fact
        # is worse than no row at all.
        self.ingest.drain()

    def _dispatch(self) -> None:
        while self._running:
            event = self.ingest.wait_for_event(_DISPATCH_POLL_SECONDS)
            if event is None:
                continue
            try:
                self.sink(hook_event_from_cloud_event(event))
            except Exception:
                continue


def start_cloud_ingest(
    sink: Callable[[HookEvent], None],
    *,
    env: Mapping[str, str] | None = None,
    token_path: Path | None = None,
    config: CloudIngestConfig | None = None,
) -> CloudIngestServer | None:
    """The one call an app makes. Returns None when the owner has not opted in.

    Off by default twice over: the env opt-in must be set *and* the resulting
    config must carry `enabled=True`.
    """
    base = config or CloudIngestConfig()
    if not (base.enabled or cloud_ingest_enabled(env)):
        return None
    resolved = base
    if not base.enabled:
        resolved = CloudIngestConfig(
            enabled=True,
            host=base.host,
            port=base.port,
            limits=base.limits,
        )
    token = ensure_ingest_token(token_path or default_token_path())
    server = CloudIngestServer(sink, token=token, config=resolved)
    server.start()
    return server
