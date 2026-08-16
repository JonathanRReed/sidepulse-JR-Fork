from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sidepulse.collector import LiveAgentMonitor
from sidepulse.models import AgentMode, AgentStatus


def _status(agent_id: str, *, stale: bool = False) -> AgentStatus:
    return AgentStatus(
        provider="codex",
        agent_id=agent_id,
        display_name="T3 task",
        mode=AgentMode.WAITING_FOR_INPUT,
        updated_at=datetime.now(timezone.utc),
        event_name="PermissionRequest",
        session_id=agent_id.rsplit(":", 1)[-1],
        origin="T3 Code · SidePulse",
        stale=stale,
    )


def test_external_status_projection_is_reachable_in_the_canonical_snapshot() -> None:
    monitor = LiveAgentMonitor()
    status = _status("codex:session:t3-thread")

    monitor.replace_external_statuses("t3code", (status,))
    snapshot = monitor.snapshot()

    assert snapshot.statuses == (status,)
    assert snapshot.aggregate.mode is AgentMode.WAITING_FOR_INPUT
    assert snapshot.aggregate.active_count == 1


def test_replacing_or_disabling_one_external_source_is_atomic() -> None:
    monitor = LiveAgentMonitor()
    first = _status("codex:session:first")
    second = _status("codex:session:second")

    monitor.replace_external_statuses("t3code", (first,))
    monitor.replace_external_statuses("t3code", (second,))
    assert set(monitor.current_statuses_by_key()) == {second.agent_id}

    monitor.replace_external_statuses("t3code", ())
    assert monitor.current_statuses_by_key() == {}


def test_stale_external_last_known_good_stays_visible_as_stale() -> None:
    monitor = LiveAgentMonitor()
    status = _status("codex:session:stale", stale=True)

    monitor.replace_external_statuses("t3code", (status,))
    snapshot = monitor.snapshot()

    assert snapshot.statuses == ()
    assert snapshot.stale_statuses == (status,)


def test_external_status_boundary_rejects_duplicate_or_unbounded_rows() -> None:
    monitor = LiveAgentMonitor()
    status = _status("codex:session:duplicate")

    with pytest.raises(ValueError):
        monitor.replace_external_statuses("T3 Code", (status,))
    with pytest.raises(ValueError):
        monitor.replace_external_statuses("t3code", (status, status))
    with pytest.raises(ValueError):
        monitor.replace_external_statuses(
            "t3code",
            tuple(
                _status(f"codex:session:{index}")
                for index in range(1_025)
            ),
        )


def test_external_status_iterable_is_consumed_only_through_the_limit() -> None:
    monitor = LiveAgentMonitor()
    consumed = 0

    def rows():
        nonlocal consumed
        for index in range(10_000):
            consumed += 1
            yield _status(f"codex:session:bounded-{index}")

    with pytest.raises(ValueError):
        monitor.replace_external_statuses("t3code", rows())

    assert consumed == 1_025
