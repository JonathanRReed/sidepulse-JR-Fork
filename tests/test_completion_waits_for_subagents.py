"""A main agent is not done while its own workers are still running.

The owner's definition: done means the main agent is asking, blocked,
or finished with NO sub-agents running. One main session can fan out to
100+ workers (200 observed), so celebrating its turn-end mid-fan-out is
both wrong and, at that scale, relentless -- which is exactly how a
notification light earns itself turned off.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sidepulse.completions import detect_completion_batch
from sidepulse.models import AgentMode, AgentStatus


def _main(mode: AgentMode) -> AgentStatus:
    return AgentStatus(
        provider="claude",
        agent_id="claude:session:main",
        display_name="main",
        mode=mode,
        updated_at=datetime.now(timezone.utc),
        event_name="Stop",
        session_id="main",
    )


def _worker(index: int, mode: AgentMode) -> AgentStatus:
    return AgentStatus(
        provider="claude",
        agent_id=f"claude:agent:w{index}",
        display_name=f"worker {index}",
        mode=mode,
        updated_at=datetime.now(timezone.utc),
        event_name="PreToolUse",
        # parent_agent_id derives from provider + session_id.
        session_id="main",
    )


def _previous() -> dict[str, AgentMode]:
    return {"claude:session:main": AgentMode.WORKING}


def test_no_celebration_while_workers_are_still_running() -> None:
    statuses = [_main(AgentMode.COMPLETED)] + [
        _worker(i, AgentMode.WORKING) for i in range(60)
    ]
    batch = detect_completion_batch(_previous(), statuses, datetime.now(timezone.utc))
    assert batch.statuses == ()


def test_celebration_once_the_whole_subtree_is_quiet() -> None:
    statuses = [_main(AgentMode.COMPLETED)] + [
        _worker(i, AgentMode.COMPLETED) for i in range(60)
    ]
    batch = detect_completion_batch(_previous(), statuses, datetime.now(timezone.utc))
    assert [status.agent_id for status in batch.statuses] == ["claude:session:main"]


def test_a_lone_main_agent_still_celebrates() -> None:
    batch = detect_completion_batch(
        _previous(), [_main(AgentMode.COMPLETED)], datetime.now(timezone.utc)
    )
    assert [status.agent_id for status in batch.statuses] == ["claude:session:main"]


def test_one_straggler_is_enough_to_hold_the_celebration() -> None:
    statuses = (
        [_main(AgentMode.COMPLETED)]
        + [_worker(i, AgentMode.COMPLETED) for i in range(99)]
        + [_worker(99, AgentMode.TOOL_RUNNING)]
    )
    batch = detect_completion_batch(_previous(), statuses, datetime.now(timezone.utc))
    assert batch.statuses == ()
