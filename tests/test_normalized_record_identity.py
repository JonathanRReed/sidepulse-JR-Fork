"""Normalized ledger records must keep their session identity.

The 0.4.0 slim-record migration stored identity as work ids
(provider_work_id / parent_work_id), but parse_log_line kept reading
only session_id/agent_id -- so EVERY session collapsed into one
provider:unknown row. One working agent per provider however many were
running, flapping completed whenever any of them fired Stop, and
identity colors with no session to attach to (2026-08-27 owner report).
"""

import json

from sidepulse.providers import parse_log_line


def _normalized(event_name: str, work_id: str, parent: str | None) -> str:
    return json.dumps(
        {
            "adapter_id": "hooks",
            "capability_id": "live_agent_events",
            "event_name": event_name,
            "event_token": "tok-" + work_id,
            "notification_kind": None,
            "occurred_at_epoch": 1787848382.0,
            "parent_work_id": parent,
            "provider_id": "claude",
            "provider_request_id": None,
            "provider_work_id": work_id,
            "record_kind": "normalized",
            "safe_label": f"Claude {work_id}",
            "sequence": None,
            "source_instance_id": "global",
            "version": {"major": 1, "minor": 0},
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def test_a_normalized_main_record_keys_by_its_session() -> None:
    record = parse_log_line(
        "claude", _normalized("pre_tool_use", "session-abc", None)
    )
    assert record is not None
    assert record.session_id == "session-abc"
    assert record.agent_id is None
    assert record.status_key == "claude:session:session-abc"


def test_a_normalized_subagent_record_keys_by_agent_under_its_parent() -> None:
    record = parse_log_line(
        "claude", _normalized("pre_tool_use", "worker-1", "session-abc")
    )
    assert record is not None
    assert record.agent_id == "worker-1"
    assert record.session_id == "session-abc"
    assert record.status_key == "claude:agent:worker-1"


def test_two_normalized_sessions_never_share_a_status_key() -> None:
    first = parse_log_line("claude", _normalized("stop", "session-a", None))
    second = parse_log_line(
        "claude", _normalized("pre_tool_use", "session-b", None)
    )
    assert first is not None and second is not None
    assert first.status_key != second.status_key
