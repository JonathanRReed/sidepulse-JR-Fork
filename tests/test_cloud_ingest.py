"""Loopback cloud-agent ingest: admission, bounds, and reachability.

Every test here was proved by deletion -- the guard it covers was removed from
`cloud_ingest.py`, the test was watched to fail, and the guard restored. The
deletion notes on each test name the exact line that must die for it to fail.
"""

from __future__ import annotations

import http.client
import json
import os
import socketserver
import stat
import threading
from datetime import datetime, timedelta, timezone

import pytest

from sidepulse import cloud_ingest
from sidepulse.cloud_ingest import (
    INGEST_PATH,
    WIRE_VERSION,
    CloudAgentEvent,
    CloudIngest,
    CloudIngestConfig,
    CloudIngestLimits,
    CloudIngestServer,
    IngestReason,
    cloud_ingest_enabled,
    default_token_path,
    ensure_ingest_token,
    hook_event_from_cloud_event,
    is_loopback_host,
    read_ingest_token,
    rotate_ingest_token,
    start_cloud_ingest,
)
from sidepulse.collector import (
    LiveAgentMonitor,
    metadata_for_record,
    status_from_event,
)
from sidepulse.models import AgentMode, HookEvent
from sidepulse.provider_adapters import minimize_hook_event
from sidepulse.providers import negotiated_provider_sources, parse_log_line

TOKEN = "cloud-ingest-test-token-0123456789abcdef"


def _limits(**overrides) -> CloudIngestLimits:
    return CloudIngestLimits(**overrides)


def _config(**overrides) -> CloudIngestConfig:
    base = {"enabled": True}
    base.update(overrides)
    return CloudIngestConfig(**base)


def _ingest(**config_overrides) -> CloudIngest:
    return CloudIngest(token=TOKEN, config=_config(**config_overrides))


def _document(**overrides) -> dict:
    document = {
        "version": WIRE_VERSION,
        "provider": "claude",
        "session_id": "cloud-session-1",
        "event": "UserPromptSubmit",
    }
    document.update(overrides)
    return document


def _headers(body: bytes, *, token: str = TOKEN, **overrides) -> dict:
    headers = {
        "Host": "127.0.0.1:8765",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    headers.update(overrides)
    return {key: value for key, value in headers.items() if value is not None}


def _post(
    ingest: CloudIngest,
    document: dict | None = None,
    *,
    body: bytes | None = None,
    method: str = "POST",
    path: str = INGEST_PATH,
    peer_host: str = "127.0.0.1",
    now: float | None = None,
    header_pairs: list[tuple[str, str]] | None = None,
    **header_overrides,
):
    payload = (
        body
        if body is not None
        else json.dumps(_document() if document is None else document).encode("utf-8")
    )
    headers = _headers(payload, **header_overrides)
    if header_pairs is not None:
        headers = _MultiHeaders(header_pairs)
    reads: list[int] = []

    def read_body(length: int) -> bytes:
        reads.append(length)
        return payload[:length]

    response = ingest.handle(
        method=method,
        path=path,
        headers=headers,
        peer_host=peer_host,
        read_body=read_body,
        now=now,
    )
    return _Posted(response, reads)


class _Posted:
    """One handled request plus the body reads it actually performed."""

    def __init__(self, response, reads: list[int]) -> None:
        self.response = response
        self.reads = reads

    @property
    def reason(self):
        return self.response.reason

    @property
    def status(self) -> int:
        return self.response.status

    def body(self) -> bytes:
        return self.response.body()


class _MultiHeaders:
    """A header mapping that can repeat a name, the way HTTP can."""

    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self._pairs = pairs

    def items(self):
        return list(self._pairs)


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------


def test_ingest_is_off_until_explicitly_enabled():
    """Deletion: drop the `if not self.config.enabled` gate in `_admit`."""
    off = CloudIngest(token=TOKEN, config=CloudIngestConfig())
    response = _post(off)
    assert response.reason is IngestReason.DISABLED
    assert response.status == 503
    assert off.drain() == ()


def test_server_refuses_to_start_while_disabled():
    """Deletion: drop the `if not self.config.enabled: raise` in the ctor."""
    with pytest.raises(ValueError):
        CloudIngestServer(lambda _event: None, token=TOKEN, config=CloudIngestConfig())


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("maybe", False),
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
    ],
)
def test_env_opt_in_is_explicit(value, expected):
    env = {} if value is None else {cloud_ingest.CLOUD_INGEST_ENV_VAR: value}
    assert cloud_ingest_enabled(env) is expected


def test_start_cloud_ingest_returns_none_without_opt_in(tmp_path):
    """Deletion: make `start_cloud_ingest` skip its opt-in check."""
    started = start_cloud_ingest(
        lambda _event: None,
        env={},
        token_path=tmp_path / "token",
    )
    assert started is None
    assert not (tmp_path / "token").exists()


# ---------------------------------------------------------------------------
# Loopback only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.20", "evil.example.com", "sidepulse.io", ""],
)
def test_server_refuses_non_loopback_host(host):
    """Deletion: drop the `is_loopback_host(self.config.host)` ctor guard."""
    with pytest.raises(ValueError):
        CloudIngestServer(
            lambda _event: None,
            token=TOKEN,
            config=_config(host=host),
        )


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.5", "::1", "localhost"])
def test_loopback_hosts_are_recognised(host):
    assert is_loopback_host(host) is True


def test_non_loopback_peer_is_refused():
    """Deletion: drop the `is_loopback_host(peer_host)` check in `_admit`."""
    ingest = _ingest()
    response = _post(ingest, peer_host="203.0.113.9")
    assert response.reason is IngestReason.FORBIDDEN_PEER
    assert response.status == 403
    assert ingest.drain() == ()


def test_non_loopback_host_header_is_refused():
    """A DNS-rebinding page resolves its own name to 127.0.0.1 and arrives
    from a loopback peer; only the Host header still names the attacker.

    Deletion: drop the Host-header check in `_admit`."""
    ingest = _ingest()
    response = _post(ingest, Host="agent-status.example.com")
    assert response.reason is IngestReason.FORBIDDEN_HOST
    assert ingest.drain() == ()


def test_browser_origin_is_refused():
    """Deletion: drop the `lookup.get("origin")` check in `_admit`."""
    ingest = _ingest()
    response = _post(ingest, Origin="https://example.com")
    assert response.reason is IngestReason.FORBIDDEN_ORIGIN
    assert ingest.drain() == ()


def test_wrong_method_and_path_never_reach_the_queue():
    ingest = _ingest()
    assert _post(ingest, method="GET").reason is IngestReason.METHOD_NOT_ALLOWED
    assert _post(ingest, path="/").reason is IngestReason.NOT_FOUND
    assert ingest.drain() == ()


# ---------------------------------------------------------------------------
# Shared secret
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer ",
        f"Bearer {TOKEN[:-1]}",
        f"Bearer {TOKEN}x",
        TOKEN,
        f"Basic {TOKEN}",
    ],
)
def test_missing_or_wrong_token_is_refused(authorization):
    """Deletion: make `_authenticated` return True unconditionally."""
    ingest = _ingest()
    response = _post(ingest, Authorization=authorization)
    assert response.reason is IngestReason.UNAUTHENTICATED
    assert response.status == 401
    assert ingest.drain() == ()


def test_correct_token_is_accepted_case_insensitively_on_the_scheme():
    ingest = _ingest()
    assert _post(ingest, Authorization=f"bearer {TOKEN}").reason is IngestReason.ACCEPTED
    assert len(ingest.drain()) == 1


def test_unauthenticated_request_body_is_never_read():
    """Auth precedes the body read, so an unauthenticated caller cannot make
    this process consume its bytes.

    Deletion: move the `_authenticated` check below the `read_body` call."""
    ingest = _ingest()
    response = _post(ingest, Authorization=None)
    assert response.reason is IngestReason.UNAUTHENTICATED
    assert response.reads == []


def test_token_is_written_privately_and_survives_restart(tmp_path):
    """Deletion: replace `atomic_private_write`/`ensure_private_file` in
    `rotate_ingest_token` with `mkdir` + `write_text`."""
    path = tmp_path / "state" / "cloud-ingest.token"
    token = ensure_ingest_token(path)
    assert len(token) >= 32

    # Checked before anything reads the file. `private_io` also tightens on
    # read, so asserting the mode after a read would pass even if the write
    # path left the secret world-readable in between -- which is exactly the
    # window that matters for a secret.
    assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.lstat(path.parent).st_mode) == 0o700

    assert read_ingest_token(path) == token
    assert ensure_ingest_token(path) == token


def test_token_rotation_invalidates_the_old_secret(tmp_path):
    path = tmp_path / "cloud-ingest.token"
    first = ensure_ingest_token(path)
    second = rotate_ingest_token(path)
    assert first != second
    assert read_ingest_token(path) == second

    ingest = CloudIngest(token=second, config=_config())
    assert _post(ingest, token=first).reason is IngestReason.UNAUTHENTICATED
    assert _post(ingest, token=second).reason is IngestReason.ACCEPTED


def test_secret_never_appears_in_repr_or_responses():
    """Deletion: remove `CloudIngest.__repr__`."""
    ingest = _ingest()
    assert TOKEN not in repr(ingest)
    assert "<redacted>" in repr(ingest)
    for response in (_post(ingest), _post(ingest, Authorization="Bearer nope")):
        assert TOKEN.encode("utf-8") not in response.body()


def test_default_token_path_lives_in_the_private_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = default_token_path()
    assert path.parent == tmp_path / "sidepulse" / "agent-monitor"
    assert path.name == cloud_ingest.TOKEN_FILE_NAME


def test_duplicate_security_headers_are_refused():
    """Two Authorization headers is a parser-disagreement attack, not a request.

    Deletion: drop the `has_duplicate_security_header` check in `_admit`."""
    body = json.dumps(_document()).encode("utf-8")
    ingest = _ingest()
    response = _post(
        ingest,
        header_pairs=[
            ("Host", "127.0.0.1"),
            ("Authorization", "Bearer wrong-token-wrong-token"),
            ("Authorization", f"Bearer {TOKEN}"),
            ("Content-Length", str(len(body))),
        ],
    )
    assert response.reason is IngestReason.MALFORMED
    assert ingest.drain() == ()


# ---------------------------------------------------------------------------
# Hard bounds
# ---------------------------------------------------------------------------


def test_oversize_body_is_refused_without_reading_it():
    """Deletion: drop the `length > max_body_bytes` check in `_admit`."""
    ingest = _ingest(limits=_limits(max_body_bytes=64))
    body = b"x" * 4096
    response = _post(ingest, body=body)
    assert response.reason is IngestReason.BODY_TOO_LARGE
    assert response.status == 413
    assert response.reads == []


def test_missing_content_length_is_refused():
    """Deletion: drop the `declared is None` branch in `_admit`."""
    ingest = _ingest()
    response = _post(ingest, **{"Content-Length": None})
    assert response.reason is IngestReason.LENGTH_REQUIRED


def test_short_body_is_refused():
    """A Content-Length that lies about the body must not be interpreted.

    Deletion: drop the `len(body) != length` check in `_admit`."""
    ingest = _ingest(limits=_limits(max_body_bytes=4096))
    response = _post(ingest, **{"Content-Length": "1500"})
    assert response.reason is IngestReason.MALFORMED


def test_rate_limit_rejects_a_burst_and_refills_over_time():
    """Deletion: make `_TokenBucket.take` return True unconditionally."""
    ingest = _ingest(limits=_limits(max_events_per_second=5.0, burst_events=3))
    start = 1_000.0
    accepted = [
        _post(ingest, _document(session_id=f"s{index}"), now=start).reason
        for index in range(3)
    ]
    assert accepted == [IngestReason.ACCEPTED] * 3

    blocked = _post(ingest, _document(session_id="s9"), now=start)
    assert blocked.reason is IngestReason.RATE_LIMITED
    assert blocked.status == 429

    later = _post(ingest, _document(session_id="s9"), now=start + 1.0)
    assert later.reason is IngestReason.ACCEPTED


def test_distinct_session_cap_rejects_rather_than_evicting():
    """Deletion: make `_SessionTable.admit` return True unconditionally."""
    ingest = _ingest(limits=_limits(max_sessions=2, burst_events=100))
    now = 5_000.0
    assert _post(ingest, _document(session_id="a"), now=now).reason is IngestReason.ACCEPTED
    assert _post(ingest, _document(session_id="b"), now=now).reason is IngestReason.ACCEPTED

    overflow = _post(ingest, _document(session_id="c"), now=now)
    assert overflow.reason is IngestReason.SESSION_LIMIT

    # A session already admitted keeps working -- the cap bounds identities,
    # not throughput, and a live agent is never dropped to make room.
    assert _post(ingest, _document(session_id="a"), now=now).reason is IngestReason.ACCEPTED


def test_idle_sessions_expire_so_the_cap_is_not_permanent():
    """Deletion: drop the idle-expiry loop in `_SessionTable.admit`."""
    ingest = _ingest(
        limits=_limits(max_sessions=1, session_idle_seconds=60.0, burst_events=100)
    )
    assert _post(ingest, _document(session_id="a"), now=0.0).reason is IngestReason.ACCEPTED
    assert (
        _post(ingest, _document(session_id="b"), now=1.0).reason
        is IngestReason.SESSION_LIMIT
    )
    assert (
        _post(ingest, _document(session_id="b"), now=1_000.0).reason
        is IngestReason.ACCEPTED
    )


def test_full_queue_rejects_rather_than_growing():
    """Deletion: replace the bounded `queue.Queue(maxsize=...)` with an
    unbounded one."""
    ingest = _ingest(limits=_limits(max_queue_events=2, burst_events=100))
    now = 10.0
    assert _post(ingest, _document(session_id="a"), now=now).reason is IngestReason.ACCEPTED
    assert _post(ingest, _document(session_id="b"), now=now).reason is IngestReason.ACCEPTED

    overflow = _post(ingest, _document(session_id="c"), now=now)
    assert overflow.reason is IngestReason.QUEUE_FULL
    assert overflow.status == 503
    assert len(ingest.drain()) == 2

    assert _post(ingest, _document(session_id="c"), now=now).reason is IngestReason.ACCEPTED


def test_limits_reject_nonsense_configuration():
    for bad in (
        {"max_body_bytes": 0},
        {"max_events_per_second": 0.0},
        {"burst_events": 0},
        {"max_sessions": 0},
        {"max_queue_events": 0},
        {"session_idle_seconds": 0.0},
    ):
        with pytest.raises(ValueError):
            CloudIngestLimits(**bad)


# ---------------------------------------------------------------------------
# Content discipline
# ---------------------------------------------------------------------------


def test_unknown_fields_are_refused_not_ignored():
    """The whole point of a content-free app is that a future sender cannot
    quietly start shipping transcripts through this door.

    Deletion: drop the `keys <= ALLOWED_FIELDS` check in `parse_cloud_event`."""
    ingest = _ingest()
    for extra in ("transcript", "tool_input", "message", "cwd", "prompt"):
        response = _post(ingest, _document(**{extra: "some content"}))
        assert response.reason is IngestReason.UNKNOWN_FIELD, extra
    assert ingest.drain() == ()


def test_display_name_is_sanitised_and_truncated():
    """Deletion: return the raw string from `_sanitized_label`."""
    ingest = _ingest()
    noisy = "Review PR\n\n 412  " + "x" * 400
    assert _post(ingest, _document(display_name=noisy)).reason is IngestReason.ACCEPTED
    (event,) = ingest.drain()
    assert "" not in event.display_name
    assert "\n" not in event.display_name
    assert event.display_name.startswith("Review PR 412 x")
    assert len(event.display_name) <= cloud_ingest.MAX_DISPLAY_NAME_CHARS


def test_origin_defaults_to_a_cloud_label():
    ingest = _ingest()
    assert _post(ingest).reason is IngestReason.ACCEPTED
    (event,) = ingest.drain()
    assert event.origin == "Claude Cloud"


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"provider": "not-a-provider"}, IngestReason.UNKNOWN_PROVIDER),
        ({"provider": 7}, IngestReason.UNKNOWN_PROVIDER),
        ({"event": "DefinitelyNotAHookEvent"}, IngestReason.UNKNOWN_EVENT),
        ({"event": 3}, IngestReason.UNKNOWN_EVENT),
        ({"session_id": ""}, IngestReason.INVALID_IDENTITY),
        ({"session_id": "has spaces"}, IngestReason.INVALID_IDENTITY),
        ({"session_id": "../../etc/passwd"}, IngestReason.INVALID_IDENTITY),
        ({"session_id": "s" * 200}, IngestReason.INVALID_IDENTITY),
        ({"agent_id": "not ok"}, IngestReason.INVALID_IDENTITY),
        ({"version": 2}, IngestReason.MALFORMED),
        ({"version": True}, IngestReason.MALFORMED),
        ({"occurred_at": "not-a-time"}, IngestReason.INVALID_TIME),
        ({"occurred_at": 17}, IngestReason.INVALID_TIME),
    ],
)
def test_invalid_documents_are_refused(overrides, reason):
    """Deletion: drop the matching validation branch in `parse_cloud_event`."""
    ingest = _ingest(limits=_limits(burst_events=200))
    assert _post(ingest, _document(**overrides)).reason is reason
    assert ingest.drain() == ()


def test_missing_required_fields_are_refused():
    ingest = _ingest()
    for missing in ("version", "provider", "session_id", "event"):
        document = _document()
        del document[missing]
        assert _post(ingest, document).reason is IngestReason.MALFORMED


@pytest.mark.parametrize(
    "body",
    [b"", b"not json", b"[]", b'"text"', b"123", b'{"version": NaN}'],
)
def test_malformed_bodies_are_refused(body):
    ingest = _ingest()
    assert _post(ingest, body=body).reason is IngestReason.MALFORMED


def test_duplicate_json_keys_are_refused():
    """Deletion: drop the `object_pairs_hook` from `decode_cloud_document`."""
    ingest = _ingest()
    body = (
        b'{"version":1,"provider":"claude","session_id":"a","event":"Stop",'
        b'"session_id":"b"}'
    )
    assert _post(ingest, body=body).reason is IngestReason.MALFORMED


def test_far_future_timestamps_cannot_pin_a_row():
    """Deletion: drop the skew comparison in `_event_time`."""
    ingest = _ingest(limits=_limits(burst_events=100))
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    epoch = now.timestamp()

    far = (now + timedelta(days=3650)).isoformat()
    assert (
        _post(ingest, _document(occurred_at=far), now=epoch).reason
        is IngestReason.INVALID_TIME
    )

    ancient = (now - timedelta(days=3650)).isoformat()
    assert (
        _post(ingest, _document(occurred_at=ancient), now=epoch).reason
        is IngestReason.INVALID_TIME
    )

    fresh = (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    assert (
        _post(ingest, _document(occurred_at=fresh), now=epoch).reason
        is IngestReason.ACCEPTED
    )


# ---------------------------------------------------------------------------
# One event model, not two
# ---------------------------------------------------------------------------


def test_cloud_event_becomes_the_app_s_own_hook_event():
    """Deletion: have `hook_event_from_cloud_event` return a bespoke dataclass
    instead of `models.HookEvent`."""
    event = CloudAgentEvent(
        provider="claude",
        session_id="cloud-1",
        agent_id=None,
        event_name="Stop",
        status=None,
        display_name="PR 412 review",
        origin="Claude Cloud",
        occurred_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
    )
    record = hook_event_from_cloud_event(event)
    assert type(record) is HookEvent
    assert record.status_key == "claude:session:cloud-1"

    source = next(
        candidate
        for candidate in negotiated_provider_sources()
        if candidate.source_key.provider_id == "claude"
        and candidate.source_key.adapter_id == "hooks"
        and candidate.source_key.capability_id == "live_agent_events"
    )
    normalized = minimize_hook_event(
        record,
        source_key=source.source_key,
        contract=source.contract,
        observation_authority=source.registration.observation_authority,
    )
    assert type(normalized).__name__ == "NormalizedProviderRecord"
    assert normalized.provider_work_id.value == "cloud-1"


def test_explicit_status_uses_the_existing_mode_channel():
    """A cloud agent says "blocked" through `sidepulse_status`, the same
    channel a local hook uses -- no second mode vocabulary.

    Deletion: stop writing `sidepulse_status` in
    `hook_event_from_cloud_event`."""
    ingest = _ingest(limits=_limits(burst_events=100))
    assert (
        _post(ingest, _document(event="PostToolUse", status="blocked")).reason
        is IngestReason.ACCEPTED
    )
    (event,) = ingest.drain()
    record = hook_event_from_cloud_event(event)
    status = status_from_event(record)
    assert status.mode is AgentMode.BLOCKED_ERROR


def test_cloud_origin_reaches_the_status_row():
    """Deletion: stop writing `agent_origin` in
    `hook_event_from_cloud_event`."""
    ingest = _ingest()
    assert _post(ingest, _document(origin="Claude Cloud Review")).reason is (
        IngestReason.ACCEPTED
    )
    (event,) = ingest.drain()
    status = status_from_event(hook_event_from_cloud_event(event))
    assert status.origin == "Claude Cloud Review"


def test_cloud_payload_round_trips_through_the_log_parser():
    """The raw payload must be a *self-describing* canonical hook line.

    It matters because two existing paths re-parse lines rather than trusting
    an in-memory record: `status_bar.replay_recent_debug_logs` at launch, and
    the legacy fallback inside `LiveAgentMonitor.reconcile_refresh_hint`. A
    payload that only makes sense alongside the `HookEvent` fields would go
    anonymous the moment it made a round trip through a log.

    Deletion: drop any of `session_id`, `agent_id`, `agent_origin`,
    `sidepulse_status` or `prompt` from the raw payload in
    `hook_event_from_cloud_event`."""
    ingest = _ingest()
    document = _document(
        session_id="parent-1",
        agent_id="worker-7",
        event="PostToolUse",
        status="blocked",
        display_name="PR 412 flaky test",
        origin="Claude Cloud Review",
    )
    assert _post(ingest, document).reason is IngestReason.ACCEPTED
    (event,) = ingest.drain()
    raw = hook_event_from_cloud_event(event).raw

    reparsed = parse_log_line("claude", json.dumps(raw))
    assert reparsed is not None
    assert reparsed.session_id == "parent-1"
    assert reparsed.agent_id == "worker-7"
    assert reparsed.status_key == "claude:agent:worker-7"
    assert reparsed.origin == "Claude Cloud Review"
    assert reparsed.raw["sidepulse_status"] == "blocked"

    metadata = metadata_for_record(reparsed, {}, {})
    restored = status_from_event(reparsed, metadata)
    assert restored.mode is AgentMode.BLOCKED_ERROR
    assert restored.origin == "Claude Cloud Review"


def test_sub_agents_arrive_as_sub_agents():
    """Sub-agents are never shown; they exist only to hold their parent's
    completion open. The cloud door must produce the same shape the local
    pipeline already folds away, not a new top-level row.

    Deletion: stop writing `agent_id` in `hook_event_from_cloud_event`."""
    ingest = _ingest()
    document = _document(session_id="parent-1", agent_id="worker-7", event="SubagentStop")
    assert _post(ingest, document).reason is IngestReason.ACCEPTED
    (event,) = ingest.drain()
    status = status_from_event(hook_event_from_cloud_event(event))
    assert status.agent_id == "claude:agent:worker-7"
    assert status.is_subagent is True
    assert status.parent_agent_id == "claude:session:parent-1"


# ---------------------------------------------------------------------------
# Reachability: a real socket, a real monitor, a real ledger row
# ---------------------------------------------------------------------------


@pytest.fixture
def running_server(tmp_path):
    monitor = LiveAgentMonitor()
    delivered = threading.Event()

    def sink(event):
        monitor.ingest_record(event)
        delivered.set()

    server = CloudIngestServer(
        sink,
        token=TOKEN,
        config=_config(port=0, limits=_limits(burst_events=200)),
    )
    host, port = server.start()
    try:
        yield server, monitor, host, port, delivered
    finally:
        server.stop()


def _http_post(host: str, port: int, document: dict, *, token: str = TOKEN, **headers):
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        body = json.dumps(document).encode("utf-8")
        merged = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        merged.update(headers)
        connection.request("POST", INGEST_PATH, body=body, headers=merged)
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _await(predicate, wake: threading.Event, *, timeout: float = 3.0):
    value = predicate()
    if value:
        return value
    wake.wait(timeout)
    return predicate()


def test_server_binds_loopback_on_an_ephemeral_port(running_server):
    _server, _monitor, host, port, _delivered = running_server
    assert host == cloud_ingest.LOOPBACK_HOST
    assert port > 0


def test_cloud_agent_reaches_the_ledger_over_a_real_socket(running_server):
    """The end-to-end proof: an HTTP POST from another host's agent becomes a
    row in the same `LiveAgentMonitor` the local hooks feed.

    Deletion: make `CloudIngestServer._dispatch` skip calling the sink."""
    _server, monitor, host, port, delivered = running_server

    status, payload = _http_post(
        host,
        port,
        _document(
            session_id="cloud-review-1",
            event="UserPromptSubmit",
            display_name="PR 412 flaky test",
        ),
    )
    assert status == 202
    assert payload == {"accepted": True, "reason": "accepted"}

    key = "claude:session:cloud-review-1"
    row = _await(lambda: monitor.current_statuses_by_key().get(key), delivered)
    assert row is not None
    assert row.mode is AgentMode.WORKING
    assert row.provider == "claude"
    # Origin survives the canonical projection (`preserve_details`), so the
    # ledger can tell a cloud reviewer apart from a session on this Mac.
    assert row.origin == "Claude Cloud"

    delivered.clear()
    status, _payload = _http_post(
        host, port, _document(session_id="cloud-review-1", event="Stop")
    )
    assert status == 202
    completed = _await(
        lambda: (
            monitor.current_statuses_by_key().get(key)
            if monitor.current_statuses_by_key().get(key) is not None
            and monitor.current_statuses_by_key()[key].mode is AgentMode.COMPLETED
            else None
        ),
        delivered,
    )
    assert completed is not None
    assert completed.origin == "Claude Cloud"


def test_display_name_reaches_the_collector_though_canonical_rows_self_label():
    """Two halves, both pinned, because only one of them ships today.

    The wire name *is* delivered: `collector.status_from_event` builds it from
    the `prompt` channel exactly as it does for a local `UserPromptSubmit`.
    The canonical projection then relabels the row with its content-free
    `safe_label`, because `ingest_record` sets `preserve_display_name` only for
    transcript-sourced records. Surfacing cloud names is a one-line collector
    change (reported as wiring), and this test is what will fail loudly when
    that change lands -- it must be updated deliberately, not drifted past.

    Deletion: stop writing `raw["prompt"]` in `hook_event_from_cloud_event`
    and the first half fails."""
    ingest = _ingest()
    assert _post(
        ingest,
        _document(session_id="named-1", display_name="PR 412 flaky test"),
    ).reason is IngestReason.ACCEPTED
    (event,) = ingest.drain()
    record = hook_event_from_cloud_event(event)

    # Exactly the two calls `LiveAgentMonitor.ingest_record` makes.
    metadata = metadata_for_record(record, {}, {})
    named = status_from_event(record, metadata)
    assert named.display_name == "PR 412 flaky test (named-1)"

    monitor = LiveAgentMonitor()
    monitor.ingest_record(record)
    row = monitor.current_statuses_by_key()["claude:session:named-1"]
    assert row.display_name == "Claude named-1"


def test_real_socket_rejects_a_wrong_token(running_server):
    _server, monitor, host, port, _delivered = running_server
    status, payload = _http_post(
        host, port, _document(session_id="intruder"), token="wrong-token-wrong-token"
    )
    assert status == 401
    assert payload["accepted"] is False
    assert "claude:session:intruder" not in monitor.current_statuses_by_key()


def test_real_socket_rejects_an_oversize_body(running_server):
    _server, _monitor, host, port, _delivered = running_server
    document = _document(session_id="big", display_name="x" * 8192)
    status, payload = _http_post(host, port, document)
    assert status == 413
    assert payload["reason"] == "body_too_large"


def test_real_socket_answers_no_cors_preflight(running_server):
    _server, _monitor, host, port, _delivered = running_server
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request("OPTIONS", INGEST_PATH)
        response = connection.getresponse()
        response.read()
        assert response.status == 501
        assert response.getheader("Access-Control-Allow-Origin") is None
    finally:
        connection.close()


def test_server_stop_is_deterministic_and_releases_the_port(tmp_path):
    """Deletion: drop `server.server_close()` (or the thread joins) in `stop`."""
    server = CloudIngestServer(
        lambda _event: None, token=TOKEN, config=_config(port=0)
    )
    host, port = server.start()
    assert _http_post(host, port, _document(session_id="x"))[0] == 202
    server.stop()

    assert server.address is None
    names = {thread.name for thread in threading.enumerate()}
    assert "sidepulse-cloud-ingest-accept" not in names
    assert "sidepulse-cloud-ingest-dispatch" not in names

    connection = http.client.HTTPConnection(host, port, timeout=1)
    with pytest.raises(OSError):
        connection.request("POST", INGEST_PATH, body=b"{}")
        connection.getresponse()
    connection.close()


def test_server_refuses_to_start_twice(tmp_path):
    server = CloudIngestServer(
        lambda _event: None, token=TOKEN, config=_config(port=0)
    )
    server.start()
    try:
        with pytest.raises(OSError):
            server.start()
    finally:
        server.stop()


def test_start_cloud_ingest_honours_the_env_opt_in(tmp_path):
    """Deletion: make `start_cloud_ingest` ignore `env`."""
    token_path = tmp_path / "cloud-ingest.token"
    server = start_cloud_ingest(
        lambda _event: None,
        env={cloud_ingest.CLOUD_INGEST_ENV_VAR: "1"},
        token_path=token_path,
        config=CloudIngestConfig(port=0),
    )
    assert server is not None
    try:
        assert server.address[0] == cloud_ingest.LOOPBACK_HOST
        assert read_ingest_token(token_path) is not None
    finally:
        server.stop()


def test_handler_never_logs_request_lines(capsys):
    """An ingest endpoint's request lines are exactly what must not be logged.

    Deletion: remove the `log_message` override on `_IngestHandler`."""
    handler = cloud_ingest._IngestHandler.__new__(cloud_ingest._IngestHandler)
    handler.log_message("%s", f"POST {INGEST_PATH} Bearer {TOKEN}")
    captured = capsys.readouterr()
    assert TOKEN not in captured.err
    assert captured.err == ""
    assert captured.out == ""


def test_concurrent_connections_are_capped_not_queued(monkeypatch):
    """Thread-per-connection with no cap is a local denial of service.

    Deletion: drop the `self._slots.acquire(blocking=False)` guard in
    `_IngestHTTPServer.process_request`."""
    dispatched: list[object] = []
    monkeypatch.setattr(
        socketserver.ThreadingMixIn,
        "process_request",
        lambda self, request, address: dispatched.append(request),
    )
    server = cloud_ingest._IngestHTTPServer.__new__(cloud_ingest._IngestHTTPServer)
    server._slots = threading.BoundedSemaphore(1)

    class _Request:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    first, second = _Request(), _Request()
    cloud_ingest._IngestHTTPServer.process_request(server, first, ("127.0.0.1", 1))
    assert dispatched == [first]
    assert first.closed is False

    cloud_ingest._IngestHTTPServer.process_request(server, second, ("127.0.0.1", 2))
    assert dispatched == [first]
    assert second.closed is True


def test_handler_errors_never_print_a_traceback(capsys):
    """A request body could reach stderr through the default handler.

    Deletion: remove the `handle_error` override on `_IngestHTTPServer`."""
    server = cloud_ingest._IngestHTTPServer.__new__(cloud_ingest._IngestHTTPServer)
    try:
        raise ValueError(f"body with a secret {TOKEN}")
    except ValueError:
        server.handle_error(None, ("127.0.0.1", 1))
    captured = capsys.readouterr()
    assert captured.err == ""
    assert TOKEN not in captured.out


def test_stats_count_outcomes_without_payload_content():
    ingest = _ingest(limits=_limits(burst_events=100))
    _post(ingest)
    _post(ingest, Authorization=None)
    _post(ingest, peer_host="203.0.113.9")
    stats = ingest.stats()
    assert stats.accepted == 1
    assert stats.rejected == 2
    assert dict(stats.reasons) == {"unauthenticated": 1, "forbidden_peer": 1}
    assert stats.queued == 1
