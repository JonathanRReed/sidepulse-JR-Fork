from __future__ import annotations

import errno
import json
import os
import stat
import threading
from datetime import datetime, timedelta, timezone

import pytest

from sidepulse.agent_deck_compat import (
    AgentDeckSnapshotService,
    AgentDeckState,
    CompatibilityReceipt,
    ReceiptReason,
    disabled_receipt,
    observation_to_status,
    parse_snapshot,
    prioritized_statuses,
    read_snapshot,
    validate_navigation_url,
)
from sidepulse.models import AgentMode

NOW = datetime(2026, 9, 4, 15, 30, tzinfo=timezone.utc)


def payload(*rows):
    sessions = {
        row["sessionId"]: row
        for row in (
            rows
            or (
                {
                    "capabilities": [],
                    "pinned": False,
                    "providerId": "claude",
                    "selected": True,
                    "sequence": 1,
                    "sessionId": "agent-1",
                    "state": "needs_input",
                    "title": "Review",
                    "unread": False,
                    "updatedAt": "2026-09-04T15:29:30Z",
                },
            )
        )
    }
    return {
        "activeProviderId": "claude",
        "device": {"connection": "wired", "owner": "native_passthrough"},
        "generation": 7,
        "providers": {
            "claude": {
                "capabilities": [],
                "connected": True,
                "providerId": "claude",
                "selectedSessionId": next(iter(sessions), None),
                "sessions": sessions,
                "slotOrder": list(sessions),
                "voice": "off",
            }
        },
        "updatedAt": "2026-09-04T15:29:30Z",
    }


def row(**updates):
    value = next(iter(payload()["providers"]["claude"]["sessions"].values()))
    return value | updates


def private_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def receipt(reason=ReceiptReason.UNAVAILABLE, observations=()):
    ok = reason is ReceiptReason.OK
    return CompatibilityReceipt(True, ok, ok, reason, observations=observations)


def test_disabled_reader_performs_no_filesystem_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "open", lambda *_args: (_ for _ in ()).throw(AssertionError("filesystem read")))
    assert read_snapshot(tmp_path / "missing", enabled=False, now=NOW) == disabled_receipt()


def test_typed_state_maps_to_canonical_status_and_work_key():
    row = parse_snapshot(payload(), now=NOW)[0]
    assert row.state is AgentDeckState.NEEDS_INPUT
    status = observation_to_status(row)
    assert (status.mode, status.updated_at) == (AgentMode.WAITING_FOR_INPUT, NOW - timedelta(seconds=30))
    assert (status.agent_id, status.session_id) == ("claude:session:agent-1", "agent-1")
    assert status.work_key.source_key.provider_id == "claude"
    assert status.work_key.work_id.value == "agent-1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("agent_id", ""),
        ("agent_id", "../escape"),
        ("agent_id", "secret-token"),
        ("agent_id", "a" * 65),
        ("provider", "Claude"),
        ("provider", "claude/x"),
        ("provider", "api_key"),
        ("provider", 4),
    ],
)
def test_parser_rejects_unsafe_identifiers(field, value):
    key = "sessionId" if field == "agent_id" else "providerId"
    changed = row(**{key: value})
    if key == "sessionId" and isinstance(value, str):
        document = payload(changed)
    else:
        document = payload(changed)
        document["providers"]["claude"]["providerId"] = value
    with pytest.raises(ValueError, match=field):
        parse_snapshot(document, now=NOW)


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-09-04 15:29:30Z",
        "2026-09-04T15:29Z",
        "2026-09-04T15:29:30",
        "2026-09-04T15:29:30z",
        "2026-09-04T15:29:30+0000",
        "2026-09-04T15:29:30.1234567Z",
        "2026-09-04T15:29:30+24:00",
    ],
)
def test_parser_requires_exact_rfc3339(stamp):
    changed = row(updatedAt=stamp)
    with pytest.raises(ValueError, match="updated_at"):
        parse_snapshot(payload(changed), now=NOW)


def test_rfc3339_offset_and_time_bounds_use_injected_now():
    base = row()
    parsed = parse_snapshot(payload(base | {"updatedAt": "2026-09-04T10:29:30.125-05:00"}), now=NOW)[0]
    assert parsed.updated_at == datetime(2026, 9, 4, 15, 29, 30, 125000, tzinfo=timezone.utc)
    assert parse_snapshot(payload(base | {"updatedAt": "2026-09-03T15:30:00Z"}), now=NOW)
    assert parse_snapshot(payload(base | {"updatedAt": "2026-09-04T15:35:00Z"}), now=NOW)
    for stamp in ("2026-09-03T15:29:59Z", "2026-09-04T15:35:01Z"):
        with pytest.raises(ValueError, match="stale or future"):
            parse_snapshot(payload(base | {"updatedAt": stamp}), now=NOW)


@pytest.mark.parametrize(
    "url",
    [
        "https://x/session/agent-1",
        "agent-deck:///session/agent-1",
        "agent-deck://evil/agent-1",
        "agent-deck://session/../agent-1",
        "agent-deck://session/agent-1/extra",
        "agent-deck://session/agent-2",
        "agent-deck://user@session/agent-1",
        "agent-deck://session:42/agent-1",
        "agent-deck://session/agent-1?dispatch=true",
        "agent-deck://session/agent-1#x",
        "agent-deck://session/%61gent-1",
    ],
)
def test_navigation_requires_complete_allowlisted_uri_shape(url):
    with pytest.raises(ValueError, match="open_url"):
        validate_navigation_url(url, provider_id="claude", session_id="agent-1")


@pytest.mark.parametrize("scheme", ["agent-deck", "t3code", "alcove"])
def test_navigation_accepts_safe_schemes_and_matching_session(scheme):
    value = f"{scheme}://session/claude/agent-1"
    assert validate_navigation_url(value, provider_id="claude", session_id="agent-1") == value


def test_parser_rejects_unknown_fields_states_and_mismatched_session_keys():
    with pytest.raises(ValueError, match="snapshot field"):
        parse_snapshot(payload() | {"commands": []}, now=NOW)
    with pytest.raises(ValueError, match="state"):
        parse_snapshot(payload(row(state="approve")), now=NOW)
    document = payload()
    document["providers"]["claude"]["sessions"] = {"other": row()}
    with pytest.raises(ValueError, match="session"):
        parse_snapshot(document, now=NOW)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(activeProviderId="missing"),
        lambda value: value["providers"]["claude"].update(voice="speaking"),
        lambda value: value["providers"]["claude"].update(selectedSessionId="missing"),
        lambda value: value["device"].update(activeLayer=-1),
        lambda value: value["providers"]["claude"]["sessions"]["agent-1"].update(sequence=True),
    ],
)
def test_parser_rejects_internally_inconsistent_deck_snapshots(mutation):
    document = payload()
    mutation(document)
    with pytest.raises(ValueError):
        parse_snapshot(document, now=NOW)


def test_explicit_priority_order_is_input_failure_active_completion_idle():
    rows = [
        row(sessionId=f"a-{i}", state=state, selected=False)
        for i, state in enumerate(("idle", "complete_unread", "running", "error", "needs_input"))
    ]
    statuses = prioritized_statuses(parse_snapshot(payload(*rows), now=NOW))
    assert [s.mode for s in statuses] == [
        AgentMode.WAITING_FOR_INPUT,
        AgentMode.BLOCKED_ERROR,
        AgentMode.WORKING,
        AgentMode.COMPLETED,
        AgentMode.IDLE_READY,
    ]


def test_missing_source_is_unavailable_without_path_disclosure(tmp_path):
    result = read_snapshot(tmp_path / "private" / "snapshot.json", enabled=True, now=NOW)
    assert (result.reason, result.available, result.source) == (ReceiptReason.UNAVAILABLE, False, None)
    assert str(tmp_path) not in repr(result)


def test_opened_malformed_and_unsafe_sources_are_invalid(tmp_path):
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json")
    malformed.chmod(0o600)
    broad = tmp_path / "broad.json"
    private_json(broad, payload())
    broad.chmod(0o644)
    for path in (malformed, broad):
        result = read_snapshot(path, enabled=True, now=NOW)
        assert (result.reason, result.available, result.source) == (ReceiptReason.INVALID, True, None)


def test_symlink_is_invalid_and_open_uses_nofollow(monkeypatch, tmp_path):
    target, link = tmp_path / "target.json", tmp_path / "snapshot.json"
    private_json(target, payload())
    link.symlink_to(target)
    real_open, flags = os.open, []

    def recording_open(path, value):
        flags.append(value)
        return real_open(path, value)

    monkeypatch.setattr(os, "open", recording_open)
    assert read_snapshot(link, enabled=True, now=NOW).reason is ReceiptReason.INVALID
    assert flags[0] & os.O_NOFOLLOW


def test_non_symlink_open_failure_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "open", lambda *_args: (_ for _ in ()).throw(OSError(errno.EACCES, "denied")))
    assert read_snapshot(tmp_path / "x", enabled=True, now=NOW).reason is ReceiptReason.UNAVAILABLE


def test_bounded_fd_read_and_success(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.json"
    private_json(path, payload())
    assert read_snapshot(path, enabled=True, now=NOW).reason is ReceiptReason.OK
    monkeypatch.setattr(os, "read", lambda _fd, size: b"x" * size)
    assert read_snapshot(path, enabled=True, now=NOW).reason is ReceiptReason.INVALID


def test_disabled_service_calls_neither_reader_nor_clock():
    def forbidden():
        raise AssertionError("disabled work")

    service = AgentDeckSnapshotService(enabled=False, reader=forbidden, clock=forbidden, callback=lambda _u: None)
    assert service.start() is False
    service.close()


def test_service_keeps_last_good_statuses_but_marks_them_stale():
    row = parse_snapshot(payload(), now=NOW)[0]
    values = iter((receipt(ReceiptReason.OK, (row,)), receipt()))
    updates = []
    service = AgentDeckSnapshotService(
        enabled=True, reader=lambda: next(values), clock=lambda: 1.0, callback=updates.append, cadence_seconds=1.0
    )
    assert service.refresh_once() and service.refresh_once()
    assert updates[0].statuses[0].stale is False
    assert updates[1].statuses[0].stale is True
    assert (updates[1].receipt.reason, updates[1].generation) == (ReceiptReason.UNAVAILABLE, 2)
    service.close()


def test_successful_empty_snapshot_clears_last_known_good():
    row = parse_snapshot(payload(), now=NOW)[0]
    values = iter((receipt(ReceiptReason.OK, (row,)), receipt(ReceiptReason.OK)))
    updates = []
    service = AgentDeckSnapshotService(
        enabled=True, reader=lambda: next(values), clock=lambda: 1.0, callback=updates.append, cadence_seconds=1.0
    )
    assert service.refresh_once() and service.refresh_once()
    assert updates[1].receipt.reason is ReceiptReason.OK
    assert updates[1].statuses == ()
    service.close()


@pytest.mark.parametrize("cadence", [0, 0.099, 301, float("inf"), True])
def test_service_rejects_unbounded_cadence(cadence):
    with pytest.raises(ValueError, match="cadence"):
        AgentDeckSnapshotService(
            enabled=True, reader=receipt, clock=lambda: 0.0, callback=lambda _u: None, cadence_seconds=cadence
        )


def test_close_generation_fences_inflight_callback():
    entered, release, callbacks = threading.Event(), threading.Event(), []

    def reader():
        entered.set()
        assert release.wait(2)
        return receipt()

    service = AgentDeckSnapshotService(
        enabled=True, reader=reader, clock=lambda: 0.0, callback=callbacks.append, cadence_seconds=1.0
    )
    worker = threading.Thread(target=service.refresh_once)
    worker.start()
    assert entered.wait(2)
    service.close()
    release.set()
    worker.join(2)
    assert not worker.is_alive() and callbacks == []


def test_close_does_not_return_while_callback_is_still_running():
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def callback(_update):
        entered.set()
        assert release.wait(2)

    service = AgentDeckSnapshotService(
        enabled=True, reader=receipt, clock=lambda: 0.0, callback=callback, cadence_seconds=1.0
    )
    refresh = threading.Thread(target=service.refresh_once)
    refresh.start()
    assert entered.wait(2)
    closer = threading.Thread(target=lambda: (service.close(), closed.set()))
    closer.start()
    assert not closed.wait(0.05)
    release.set()
    refresh.join(2)
    closer.join(2)
    assert closed.is_set()


def test_background_start_is_single_use_and_close_stops_callbacks():
    called, callbacks = threading.Event(), []

    def callback(update):
        callbacks.append(update)
        called.set()

    service = AgentDeckSnapshotService(
        enabled=True, reader=receipt, clock=lambda: 0.0, callback=callback, cadence_seconds=0.1
    )
    assert service.start() is True
    assert called.wait(2)
    service.close()
    count = len(callbacks)
    assert service.start() is False
    assert not service.running and len(callbacks) == count
