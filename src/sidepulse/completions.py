from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .freshness import is_recent
from .models import AgentMode, AgentStatus
from .operator_state import CanonicalOperatorEvent, TransitionKind

COMPLETION_NOTIFY_FRESHNESS_SECONDS = 120.0

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


def detect_completion_batch(
    previous_modes: Mapping[str, AgentMode],
    statuses: Sequence[AgentStatus],
    now: datetime,
) -> CompletionBatch:
    """Return fresh main-session active-to-completed transitions."""
    current_by_agent = canonical_current_statuses(statuses)

    eligible_by_agent: dict[str, AgentStatus] = {}
    for status in current_by_agent.values():
        if (
            status.mode != AgentMode.COMPLETED
            or previous_modes.get(status.agent_id) not in _ACTIVE_MODES
            or status.event_name == "SessionEnd"
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
