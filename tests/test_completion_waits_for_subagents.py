"""A main agent is not done while its own workers are still running.

The owner's definition: done means the main agent is asking, blocked,
or finished with NO sub-agents running. One main session can fan out to
100+ workers (200 observed), so celebrating its turn-end mid-fan-out is
both wrong and, at that scale, relentless -- which is exactly how a
notification light earns itself turned off.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sidepulse.completions import SUBAGENT_HOLD_SECONDS, detect_completion_batch
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


def _stale_worker(index: int, mode: AgentMode) -> AgentStatus:
    """A worker the collector has already demoted to stale_statuses."""
    return AgentStatus(
        provider="claude",
        agent_id=f"claude:agent:dead{index}",
        display_name=f"worker {index}",
        mode=mode,
        updated_at=datetime.now(timezone.utc) - timedelta(hours=3),
        event_name="PreToolUse",
        session_id="main",
        stale=True,
    )


def _silent_worker(index: int, mode: AgentMode, *, age_seconds: float) -> AgentStatus:
    """Not flagged stale yet, but has not reported in a long time."""
    return AgentStatus(
        provider="claude",
        agent_id=f"claude:agent:quiet{index}",
        display_name=f"worker {index}",
        mode=mode,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        event_name="PreToolUse",
        session_id="main",
    )


def test_a_reaped_worker_does_not_mute_its_parent_forever() -> None:
    """The bug this gate could otherwise cause, permanently.

    track_completions is fed the FULL timeline -- live and stale -- on
    purpose, to fix a missed-celebration bug. So a worker that is killed
    or crashes never emits a terminal event and stays non-COMPLETED for
    the life of the process. Counting it would retire this parent from
    ever completing again: no celebration, no notification, silently.
    """
    statuses = [
        _main(AgentMode.COMPLETED),
        *(_worker(i, AgentMode.COMPLETED) for i in range(20)),
        _stale_worker(1, AgentMode.WORKING),
    ]
    batch = detect_completion_batch(_previous(), statuses, datetime.now(timezone.utc))
    assert [status.agent_id for status in batch.statuses] == ["claude:session:main"], (
        "one dead worker suppressed the parent's completion"
    )


def test_a_worker_gone_quiet_past_the_hold_window_releases_its_parent() -> None:
    statuses = [
        _main(AgentMode.COMPLETED),
        _silent_worker(1, AgentMode.WORKING, age_seconds=SUBAGENT_HOLD_SECONDS + 60),
    ]
    batch = detect_completion_batch(_previous(), statuses, datetime.now(timezone.utc))
    assert [status.agent_id for status in batch.statuses] == ["claude:session:main"]


def test_a_worker_still_reporting_holds_its_parent() -> None:
    """The hold window must not become a way to celebrate early."""
    statuses = [
        _main(AgentMode.COMPLETED),
        _silent_worker(1, AgentMode.WORKING, age_seconds=SUBAGENT_HOLD_SECONDS / 2),
    ]
    batch = detect_completion_batch(_previous(), statuses, datetime.now(timezone.utc))
    assert batch.statuses == (), "released a parent whose worker is still alive"
