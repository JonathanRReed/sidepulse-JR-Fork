"""A fresh child event is evidence of the parent's presence.

Extracted from the collector monolith (byte ratchet) -- see
``_reconcile_delegating_parents`` for the story.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .freshness import bounded_age_seconds
from .models import AgentMode, AgentStatus


def status_counts_active(status: AgentStatus) -> bool:
    return status.mode not in {
        AgentMode.COMPLETED,
        AgentMode.IDLE_READY,
        AgentMode.ENDED_UNCONFIRMED,
    }


#: How long a child event keeps vouching for its parent's presence.
#: Sub-agents emit tool events every few seconds while genuinely
#: working; ten quiet minutes means the delegation is over or wedged,
#: and the ordinary silence semantics take back over.
DELEGATION_CHILD_FRESH_SECONDS = 600.0


def _reconcile_delegating_parents(
    statuses: tuple[AgentStatus, ...],
    collected_at: datetime,
) -> tuple[AgentStatus, ...]:
    """A fresh child event is evidence of the parent's presence.

    Claude fires Stop the moment the main turn ends, even mid-
    delegation: a main's own thread can be silent for an hour while its
    workers stream events under it. Refresh the parent's clock from its
    freshest active child and call it WORKING -- BEFORE the presence
    horizon and the staleness windows read that clock (2026-08-27 owner
    report: two delegating mains aged out entirely; the count said one
    and the strip painted orphan murk). Quiet modes only: an ask keeps
    asking, a failure stays named.
    """
    freshest_child: dict[str, datetime] = {}
    for status in statuses:
        if not status.is_subagent or not status_counts_active(status):
            continue
        parent_id = status.parent_agent_id
        if parent_id is None:
            continue
        if (
            bounded_age_seconds(collected_at, status.updated_at)
            > DELEGATION_CHILD_FRESH_SECONDS
        ):
            continue
        current = freshest_child.get(parent_id)
        if current is None or status.updated_at > current:
            freshest_child[parent_id] = status.updated_at
    if not freshest_child:
        return statuses
    quiet_modes = {
        AgentMode.COMPLETED,
        AgentMode.IDLE_READY,
        AgentMode.UNKNOWN,
    }
    return tuple(
        replace(
            status,
            mode=AgentMode.WORKING,
            updated_at=max(status.updated_at, freshest_child[status.agent_id]),
        )
        if (
            not status.is_subagent
            and status.agent_id in freshest_child
            and status.mode in quiet_modes
        )
        else status
        for status in statuses
    )


__all__ = [
    "DELEGATION_CHILD_FRESH_SECONDS",
    "_reconcile_delegating_parents",
]
