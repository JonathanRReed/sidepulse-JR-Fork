"""Per-session snooze: a visibility overlay that breaks when it matters.

T3 Code's model, adapted: snoozing a noisy session hides it from the
menu rows, the lights, and the aggregate -- but the snooze auto-breaks
("raised hand") the moment the session actually needs its owner, on a
FRESH failure, or when a run finishes after the snooze. And a session
that is asking RIGHT NOW cannot be snoozed at all: hiding a pending
request defeats it. Snoozes are in-memory -- sessions live hours, and a
restart waking everything is the safe failure.
"""

from __future__ import annotations

from .models import AgentMode

#: Nothing stays hidden past this, raised hand or not.
SNOOZE_MAX_SECONDS = 8 * 3600.0

#: Modes that mean "the agent is waiting on YOU" -- never snoozable,
#: and any snooze breaks the moment a session enters one.
_RAISED_HAND_MODES = frozenset({AgentMode.WAITING_FOR_INPUT})


def can_snooze(status) -> bool:
    """A pending ask must stay visible; everything else may rest."""
    return status.mode not in _RAISED_HAND_MODES


def snooze_holds(status, snoozed_at_epoch: float, now_epoch: float) -> bool:
    """True while the snooze still hides this session."""
    if now_epoch - snoozed_at_epoch > SNOOZE_MAX_SECONDS:
        return False
    if status.mode in _RAISED_HAND_MODES:
        return False
    updated_epoch = status.updated_at.timestamp()
    if status.mode is AgentMode.BLOCKED_ERROR and updated_epoch > snoozed_at_epoch:
        # A FRESH failure wakes it; a session snoozed while already
        # failed stays snoozed -- "I saw it, not now."
        return False
    if status.mode is AgentMode.COMPLETED and updated_epoch > snoozed_at_epoch:
        return False
    return True


def filter_snoozed(
    statuses,
    snoozes: dict[str, float],
    now_epoch: float,
) -> tuple[tuple, dict[str, float]]:
    """(statuses minus held snoozes, snoozes minus broken ones).

    Broken snoozes are pruned so the session reappears once and STAYS
    visible; sessions no longer in the snapshot keep their entry until
    the cap expires (they may still be restored).
    """
    kept_snoozes = {
        agent_id: snoozed_at
        for agent_id, snoozed_at in snoozes.items()
        if now_epoch - snoozed_at <= SNOOZE_MAX_SECONDS
    }
    visible = []
    for status in statuses:
        snoozed_at = kept_snoozes.get(status.agent_id)
        if snoozed_at is None:
            visible.append(status)
            continue
        if snooze_holds(status, snoozed_at, now_epoch):
            continue
        del kept_snoozes[status.agent_id]
        visible.append(status)
    return tuple(visible), kept_snoozes


__all__ = ["SNOOZE_MAX_SECONDS", "can_snooze", "filter_snoozed", "snooze_holds"]
