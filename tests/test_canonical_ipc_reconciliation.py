from __future__ import annotations

import json
import socket
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sidepulse.capacity_types import SourceKey
from sidepulse.collector import LiveAgentMonitor
from sidepulse.hook import hook_log_main
from sidepulse.ipc import HookEventServer, ProviderRefreshHint, send_refresh_hint
from sidepulse.provider_facts import EventToken, WorkLifecycle

SOURCE = SourceKey("codex", "hooks", "global", "live_agent_events")


def _hint(token: str = "event:hint") -> ProviderRefreshHint:
    return ProviderRefreshHint(SOURCE, EventToken(token))


def _short_socket_root() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="sp-hint-", dir="/tmp")


def _codex_line(*, sequence: int = 1, event_name: str = "PreToolUse") -> str:
    return json.dumps(
        {
            "hook_event_name": event_name,
            "session_id": "work:ipc",
            "event_id": f"event:{sequence}",
            "sequence": sequence,
            "prompt": "PRIVATE PROMPT SENTINEL",
            "cwd": "/private/project",
            "message": "PRIVATE MESSAGE SENTINEL",
            "account_label": "private@example.com",
            "transcript_path": "/private/transcript.jsonl",
            "authorization": "Bearer SECRET-VALUE",
        }
    )


def _legacy_codex_line(*, sequence: int = 1) -> str:
    return json.dumps(
        {
            "logged_at": (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat(),
            "event": json.loads(_codex_line(sequence=sequence)),
        }
    )


def test_hint_before_publication_cannot_create_state_then_log_reconciles_once(
    tmp_path: Path,
) -> None:
    log = tmp_path / "codex.jsonl"
    monitor = LiveAgentMonitor()

    monitor.reconcile_refresh_hint(_hint("event:1"), log_path=log)
    before = monitor.snapshot()
    log.write_text(_legacy_codex_line(sequence=1) + "\n")
    monitor.reconcile_refresh_hint(_hint("event:1"), log_path=log)
    created = monitor.snapshot()
    monitor.reconcile_refresh_hint(_hint("event:1"), log_path=log)
    repeated = monitor.snapshot()

    assert before.operator_state is not None
    assert before.operator_state.works == ()
    assert created.operator_state is not None
    assert created.operator_state.works[0].lifecycle is WorkLifecycle.ACTIVE
    assert len(created.operator_events) == 1
    assert repeated.operator_events == ()


def test_duplicate_out_of_order_and_forged_hints_never_author_truth(
    tmp_path: Path,
) -> None:
    empty_log = tmp_path / "empty.jsonl"
    empty_log.write_text("")
    monitor = LiveAgentMonitor()

    for token in ("event:999", "event:2", "event:2", "event:1"):
        monitor.reconcile_refresh_hint(_hint(token), log_path=empty_log)

    snapshot = monitor.snapshot()
    assert snapshot.operator_state is not None
    assert snapshot.operator_state.works == ()
    assert snapshot.operator_events == ()
    assert snapshot.statuses == ()


def test_invalid_hint_classes_recover_when_a_valid_hint_follows() -> None:
    with _short_socket_root() as directory:
        socket_path = Path(directory) / "state" / "events.sock"
        received: list[ProviderRefreshHint] = []
        received_event = threading.Event()

        def observe_hint(hint: ProviderRefreshHint) -> None:
            received.append(hint)
            received_event.set()

        server = HookEventServer(observe_hint, socket_path=socket_path)
        server.start()
        invalid_documents = (
            b"not-json",
            b'{}',
            b'{"version":1,"provider_id":"codex","adapter_id":"hooks",'
            b'"source_instance_id":"global","capability_id":"live_agent_events",'
            b'"event_token":"event:bad","extra":true}',
        )
        try:
            for document in invalid_documents:
                peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                peer.connect(str(socket_path))
                peer.sendall(document)
                peer.close()
            assert send_refresh_hint(_hint("event:valid"), socket_path=socket_path)
            assert received_event.wait(1.0)
            assert received == [_hint("event:valid")]
        finally:
            server.stop()


def test_hook_persists_normalized_content_free_record_before_hint(
    tmp_path: Path,
) -> None:
    log = tmp_path / "codex.jsonl"
    observed_at_send: list[str] = []

    def observe_send(_hint_value, **_kwargs) -> bool:
        observed_at_send.append(log.read_text())
        return True

    with (
        patch("sidepulse.hook.sys.stdin.read", return_value=_codex_line()),
        patch("sidepulse.hook.send_refresh_hint", side_effect=observe_send),
    ):
        assert hook_log_main("codex", log) == 0

    assert len(observed_at_send) == 1
    payload = json.loads(observed_at_send[0])
    serialized = json.dumps(payload, sort_keys=True)
    assert payload["record_kind"] == "normalized"
    forbidden = (
        "PRIVATE PROMPT SENTINEL",
        "/private/project",
        "PRIVATE MESSAGE SENTINEL",
        "private@example.com",
        "/private/transcript.jsonl",
        "SECRET-VALUE",
        "raw",
        "navigation",
        "account_label",
    )
    assert all(value not in serialized for value in forbidden)


def test_inert_hook_record_is_persisted_before_its_refresh_hint(
    tmp_path: Path,
) -> None:
    log = tmp_path / "codex.jsonl"
    observed: list[tuple[str, ProviderRefreshHint]] = []

    def observe_send(hint_value, **_kwargs) -> bool:
        observed.append((log.read_text(), hint_value))
        return True

    with (
        patch(
            "sidepulse.hook.sys.stdin.read",
            return_value=_codex_line(event_name="Notification"),
        ),
        patch("sidepulse.hook.send_refresh_hint", side_effect=observe_send),
    ):
        assert hook_log_main("codex", log) == 0

    assert len(observed) == 1
    persisted, hint = observed[0]
    assert json.loads(persisted)["record_kind"] == "inert"
    assert hint.source_key == SOURCE


def test_legacy_raw_log_is_minimized_before_reduction(tmp_path: Path) -> None:
    log = tmp_path / "codex.jsonl"
    log.write_text(_legacy_codex_line() + "\n")
    monitor = LiveAgentMonitor()

    monitor.reconcile_refresh_hint(_hint("event:1"), log_path=log)
    snapshot = monitor.snapshot()

    status = snapshot.statuses[0]
    assert status.work_key is not None
    assert status.cwd is status.message is status.tool_name is status.origin is None
    assert "PRIVATE" not in repr(snapshot.operator_state)


def test_invalid_normalized_row_is_not_reinterpreted_as_legacy_authority(
    tmp_path: Path,
) -> None:
    log = tmp_path / "codex.jsonl"
    disguised = json.loads(_legacy_codex_line())
    disguised["record_kind"] = "normalized"
    disguised["version"] = {"major": 1, "minor": 0}
    log.write_text(json.dumps(disguised) + "\n")
    monitor = LiveAgentMonitor()

    monitor.reconcile_refresh_hint(_hint("event:1"), log_path=log)
    snapshot = monitor.snapshot()

    assert snapshot.operator_state is not None
    assert snapshot.operator_state.works == ()
    assert snapshot.operator_events == ()


def test_legacy_hook_is_reported_not_silently_dropped() -> None:
    """Version skew must never be silent.

    A hook script lives in the user's provider config and can lag the
    app by any amount. When one still speaks the pre-hint raw-event
    wire format, its payload is not authoritative and cannot be
    ingested -- but the app must still learn the provider is stale so
    it can say so, instead of going quietly deaf to live events.
    """
    with _short_socket_root() as directory:
        socket_path = Path(directory) / "state" / "events.sock"
        hints: list[ProviderRefreshHint] = []
        legacy: list[str] = []
        hint_received = threading.Event()
        legacy_received = threading.Event()

        def observe_hint(hint: ProviderRefreshHint) -> None:
            hints.append(hint)
            hint_received.set()

        def observe_legacy(provider: str) -> None:
            legacy.append(provider)
            legacy_received.set()

        server = HookEventServer(
            observe_hint,
            socket_path=socket_path,
            on_legacy_hook=observe_legacy,
        )
        server.start()
        try:
            raw_event = json.dumps(
                {
                    "provider": "claude",
                    "line": {"hook_event_name": "Stop", "session_id": "s1"},
                }
            ).encode("utf-8")
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            peer.connect(str(socket_path))
            peer.sendall(raw_event)
            peer.shutdown(socket.SHUT_WR)
            peer.close()
            assert legacy_received.wait(1.0)
            # The stale payload is still refused as an ingestion source.
            assert hints == []
            # A modern hook on the same socket keeps working.
            assert send_refresh_hint(_hint("event:valid"), socket_path=socket_path)
            assert hint_received.wait(1.0)
        finally:
            server.stop()
