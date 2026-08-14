from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .freshness import is_recent
from .models import AgentMode, AgentStatus
from .operator_state import CanonicalOperatorEvent, TransitionKind

COMPLETION_NOTIFY_FRESHNESS_SECONDS = 120.0

# How long a silent sub-agent keeps holding its parent's completion open.
# Generous enough to cover a genuinely long-running worker between events,
# short enough that one reaped worker cannot mute a parent for the rest of
# the session. The staleness flag alone is not enough here: it defaults to
# an hour, and an hour of suppressed completions reads as "it's broken".
SUBAGENT_HOLD_SECONDS = 300.0

_ACTIVE_MODES = frozenset(
    {
        AgentMode.WORKING,
        AgentMode.TOOL_RUNNING,
        AgentMode.LONG_TASK_PROGRESS,
        AgentMode.WAITING_FOR_INPUT,
        AgentMode.BLOCKED_ERROR,
    }
)


@dataclass(frozen=True)
class CompletionBatch:
    statuses: tuple[AgentStatus, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.statuses)


def completion_events(
    events: tuple[CanonicalOperatorEvent, ...],
) -> tuple[CanonicalOperatorEvent, ...]:
    """Return unique canonical completion edges in semantic-key order."""
    if type(events) is not tuple or not all(
        type(event) is CanonicalOperatorEvent for event in events
    ):
        raise ValueError("invalid canonical operator events")
    selected = {
        event.key: event
        for event in events
        if event.kind is TransitionKind.COMPLETED
    }
    return tuple(event for _key, event in sorted(selected.items()))


def canonical_current_statuses(
    statuses: Sequence[AgentStatus],
) -> dict[str, AgentStatus]:
    """Return one deterministic current main-session row per agent."""
    current_by_agent: dict[str, AgentStatus] = {}
    for status in statuses:
        if not status.agent_id or status.is_subagent:
            continue
        existing = current_by_agent.get(status.agent_id)
        status_key = (
            status.updated_at,
            status.mode != AgentMode.COMPLETED,
            status.event_name == "SessionEnd",
            status.event_name,
        )
        if existing is None or status_key > (
            existing.updated_at,
            existing.mode != AgentMode.COMPLETED,
            existing.event_name == "SessionEnd",
            existing.event_name,
        ):
            current_by_agent[status.agent_id] = status
    return {
        agent_id: current_by_agent[agent_id]
        for agent_id in sorted(current_by_agent)
    }


def parents_with_running_subagents(
    statuses: Sequence[AgentStatus],
    now: datetime | None = None,
) -> set[str]:
    """Main sessions that still have at least one sub-agent *plausibly alive*.

    Sub-agents are never shown and never signal on their own -- but a
    parent is not FINISHED while its own workers are still going. One
    main agent can fan out to 100+ workers, so celebrating the parent's
    turn-end mid-fan-out is both wrong and, at that scale, relentless.

    "Plausibly alive" is load-bearing, not defensive coding. This gate is
    fed the FULL timeline -- live *and* stale statuses -- because
    completions would otherwise be missed entirely (see the caller). A
    sub-agent that is killed, crashes, or has its process reaped never
    emits a terminal event, so it stays non-COMPLETED forever. Counting
    it would silently retire that parent from ever completing again: no
    celebration, no notification, for the rest of the session. At 100+
    workers per parent, at least one dying without a terminal event is
    the expected case, not the edge case.

    So a worker holds its parent open only while it is still reporting.
    Once it goes quiet past the hold window, the parent is released.
    """
    running: set[str] = set()
    for status in statuses:
        if not status.is_subagent or status.mode == AgentMode.COMPLETED:
            continue
        if getattr(status, "stale", False):
            continue
        if now is not None and not is_recent(
            now,
            status.updated_at,
            SUBAGENT_HOLD_SECONDS,
        ):
            continue
        parent = getattr(status, "parent_agent_id", None)
        if parent:
            running.add(parent)
    return running


def detect_completion_batch(
    previous_modes: Mapping[str, AgentMode],
    statuses: Sequence[AgentStatus],
    now: datetime,
) -> CompletionBatch:
    """Return fresh main-session active-to-completed transitions.

    "Done" means the MAIN agent is finished and nothing it spawned is
    still working -- the owner's definition, and the one that keeps a
    celebration meaningful instead of firing on every turn boundary
    while a hundred workers keep running.
    """
    current_by_agent = canonical_current_statuses(statuses)
    busy_parents = parents_with_running_subagents(statuses, now)

    eligible_by_agent: dict[str, AgentStatus] = {}
    for status in current_by_agent.values():
        if (
            status.mode != AgentMode.COMPLETED
            or previous_modes.get(status.agent_id) not in _ACTIVE_MODES
            or status.event_name == "SessionEnd"
            or status.agent_id in busy_parents
        ):
            continue
        if not is_recent(
            now,
            status.updated_at,
            COMPLETION_NOTIFY_FRESHNESS_SECONDS,
        ):
            continue
        eligible_by_agent[status.agent_id] = status
    return CompletionBatch(
        statuses=tuple(
            eligible_by_agent[agent_id] for agent_id in sorted(eligible_by_agent)
        )
    )
