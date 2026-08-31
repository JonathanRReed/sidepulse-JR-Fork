"""Pure, bounded state and presentation projection for the Screen Bar announcer."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum, IntEnum
from typing import Final

from .announcer_content import (
    ANNOUNCER_NAME_CAP,
    ANNOUNCER_QUESTION_CAP,
    ANNOUNCER_TEXT_CAP,
    _single_line,
)
from .attention import ProjectedAgentRow
from .models import AgentMode, AgentStatus
from .operator_state import CanonicalOperatorState, RequestPhase
from .provider_facts import (
    NextActor,
    RequestKey,
    RequestKind,
    WorkKey,
    request_key_to_payload,
    work_key_to_payload,
)


class AnnouncerAlertPriority(IntEnum):
    PERMISSION = 0
    APPROVAL = 1
    REVIEW = 2
    INPUT = 3
    UNKNOWN = 4


class AnnouncerStackVisibility(str, Enum):
    HIDDEN = "hidden"
    COLLAPSED = "collapsed"
    EXPANDED = "expanded"


class AnnouncerStackAction(str, Enum):
    EXPAND = "expand"
    COLLAPSE = "collapse"
    PREVIOUS = "previous"
    NEXT = "next"
    OPEN = "open"
    MARK_SEEN = "mark_seen"


@dataclass(frozen=True, order=True, slots=True)
class AnnouncerAlertIdentity:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or not 1 <= len(self.value) <= 4096 or not self.value.isprintable():
            raise ValueError("invalid announcer alert identity")


@dataclass(frozen=True, slots=True)
class AnnouncerAlert:
    identity: AnnouncerAlertIdentity
    agent_id: str
    provider: str
    source_label: str
    session_label: str
    question: str
    priority: AnnouncerAlertPriority
    first_seen_sequence: int
    seen_on_screen_bar: bool

    def __post_init__(self) -> None:
        if not (
            type(self.identity) is AnnouncerAlertIdentity
            and all(
                type(value) is str and value.isprintable() and value
                for value in (
                    self.agent_id,
                    self.provider,
                    self.source_label,
                    self.session_label,
                    self.question,
                )
            )
            and len(self.source_label) <= ANNOUNCER_NAME_CAP
            and len(self.question) <= ANNOUNCER_QUESTION_CAP
            and type(self.priority) is AnnouncerAlertPriority
            and type(self.first_seen_sequence) is int
            and self.first_seen_sequence >= 0
            and type(self.seen_on_screen_bar) is bool
        ):
            raise ValueError("invalid announcer alert")


@dataclass(frozen=True, slots=True)
class AnnouncerStackState:
    ordered_identities: tuple[AnnouncerAlertIdentity, ...]
    first_seen_sequences: tuple[tuple[AnnouncerAlertIdentity, int], ...]
    priorities: tuple[tuple[AnnouncerAlertIdentity, AnnouncerAlertPriority], ...]
    seen_identities: frozenset[AnnouncerAlertIdentity]
    selected_identity: AnnouncerAlertIdentity | None
    expanded: bool
    next_sequence: int
    generation: int

    def __post_init__(self) -> None:
        if not (
            type(self.ordered_identities) is tuple
            and all(type(item) is AnnouncerAlertIdentity for item in self.ordered_identities)
            and len(set(self.ordered_identities)) == len(self.ordered_identities)
            and type(self.first_seen_sequences) is tuple
            and all(
                type(item) is tuple
                and len(item) == 2
                and type(item[0]) is AnnouncerAlertIdentity
                and type(item[1]) is int
                and item[1] >= 0
                for item in self.first_seen_sequences
            )
            and len({item[0] for item in self.first_seen_sequences})
            == len(self.first_seen_sequences)
            and {item[0] for item in self.first_seen_sequences}
            == set(self.ordered_identities)
            and type(self.priorities) is tuple
            and all(
                type(item) is tuple
                and len(item) == 2
                and type(item[0]) is AnnouncerAlertIdentity
                and type(item[1]) is AnnouncerAlertPriority
                for item in self.priorities
            )
            and len({item[0] for item in self.priorities}) == len(self.priorities)
            and {item[0] for item in self.priorities} == set(self.ordered_identities)
            and type(self.seen_identities) is frozenset
            and all(type(item) is AnnouncerAlertIdentity for item in self.seen_identities)
            and self.seen_identities <= set(self.ordered_identities)
            and (self.selected_identity is None or type(self.selected_identity) is AnnouncerAlertIdentity)
            and (self.selected_identity is None or self.selected_identity in self.ordered_identities)
            and type(self.expanded) is bool
            and type(self.next_sequence) is int
            and self.next_sequence >= 0
            and type(self.generation) is int
            and self.generation >= 0
        ):
            raise ValueError("invalid announcer stack state")


@dataclass(frozen=True, slots=True)
class AnnouncerStackPlan:
    generation: int
    visibility: AnnouncerStackVisibility
    alerts: tuple[AnnouncerAlert, ...]
    selected_index: int | None
    total_actionable_count: int
    unseen_count: int
    highest_priority_source: str | None
    collapsed_text: str | None
    position_text: str | None
    accessibility_label: str
    accessibility_value: str
    accessibility_help: str
    can_previous: bool
    can_next: bool
    can_open: bool
    can_mark_seen: bool

    def __post_init__(self) -> None:
        if not (
            type(self.generation) is int
            and self.generation >= 0
            and type(self.visibility) is AnnouncerStackVisibility
            and type(self.alerts) is tuple
            and all(type(alert) is AnnouncerAlert for alert in self.alerts)
            and (self.selected_index is None or type(self.selected_index) is int)
            and (
                self.selected_index is None
                or 0 <= self.selected_index < len(self.alerts)
            )
            and type(self.total_actionable_count) is int
            and self.total_actionable_count >= 0
            and self.total_actionable_count == len(self.alerts)
            and type(self.unseen_count) is int
            and 0 <= self.unseen_count <= self.total_actionable_count
            and (
                self.highest_priority_source is None
                or type(self.highest_priority_source) is str
            )
            and (
                self.collapsed_text is None
                or type(self.collapsed_text) is str
            )
            and (
                self.position_text is None
                or type(self.position_text) is str
            )
            and type(self.accessibility_label) is str
            and type(self.accessibility_value) is str
            and type(self.accessibility_help) is str
            and all(
                type(value) is bool
                for value in (
                    self.can_previous,
                    self.can_next,
                    self.can_open,
                    self.can_mark_seen,
                )
            )
        ):
            raise ValueError("invalid announcer stack plan")


@dataclass(frozen=True, slots=True)
class AnnouncerStackIntent:
    action: AnnouncerStackAction
    generation: int
    selected_identity: AnnouncerAlertIdentity | None

    def __post_init__(self) -> None:
        if not (
            type(self.action) is AnnouncerStackAction
            and type(self.generation) is int
            and self.generation >= 0
            and (
                self.selected_identity is None
                or type(self.selected_identity) is AnnouncerAlertIdentity
            )
        ):
            raise ValueError("invalid announcer stack intent")


ANNOUNCER_PRIORITY_BY_KIND: Final[dict[RequestKind, AnnouncerAlertPriority]] = {
    RequestKind.PERMISSION: AnnouncerAlertPriority.PERMISSION,
    RequestKind.APPROVAL: AnnouncerAlertPriority.APPROVAL,
    RequestKind.REVIEW: AnnouncerAlertPriority.REVIEW,
    RequestKind.INPUT: AnnouncerAlertPriority.INPUT,
    RequestKind.UNKNOWN: AnnouncerAlertPriority.UNKNOWN,
}
_LEGACY_PRIORITY_BY_EVENT: Final[dict[str, AnnouncerAlertPriority]] = {
    "PermissionRequest": AnnouncerAlertPriority.PERMISSION,
    "PlanApproval": AnnouncerAlertPriority.APPROVAL,
    "PlanApprovalRequest": AnnouncerAlertPriority.APPROVAL,
    "ReviewRequest": AnnouncerAlertPriority.REVIEW,
    "ReviewRequested": AnnouncerAlertPriority.REVIEW,
    "Notification": AnnouncerAlertPriority.INPUT,
    "InputRequest": AnnouncerAlertPriority.INPUT,
    "AskUserQuestion": AnnouncerAlertPriority.INPUT,
}
_PRIORITY_LABEL: Final[dict[AnnouncerAlertPriority, str]] = {
    AnnouncerAlertPriority.PERMISSION: "Permission request",
    AnnouncerAlertPriority.APPROVAL: "Plan approval",
    AnnouncerAlertPriority.REVIEW: "Review request",
    AnnouncerAlertPriority.INPUT: "Input request",
    AnnouncerAlertPriority.UNKNOWN: "Action needed",
}


def empty_announcer_stack_state() -> AnnouncerStackState:
    return AnnouncerStackState((), (), (), frozenset(), None, False, 0, 0)


def announcer_alert_identity(request_key: RequestKey) -> AnnouncerAlertIdentity:
    if type(request_key) is not RequestKey:
        raise ValueError("invalid request key")
    payload = json.dumps(
        request_key_to_payload(request_key), sort_keys=True, separators=(",", ":")
    )
    return AnnouncerAlertIdentity(f"request:v1:{payload}")


def legacy_announcer_alert_identity(
    provider: object,
    agent_id: object,
    session_id: object,
    event_name: object,
    work_key: object,
    tool_name: object,
) -> AnnouncerAlertIdentity | None:
    values = (provider, agent_id, event_name)
    if not all(type(value) is str for value in values):
        return None
    if session_id is None:
        session_id = ""
    if type(session_id) is not str:
        return None
    if tool_name is None:
        tool_name = ""
    if type(tool_name) is not str:
        return None
    if work_key is not None and type(work_key) is not WorkKey:
        return None
    payload = {
        "agent_id": agent_id,
        "event": event_name,
        "provider": provider,
        "session": session_id,
        "tool": tool_name,
        "work": work_key_to_payload(work_key) if type(work_key) is WorkKey else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return AnnouncerAlertIdentity(f"legacy:v1:{encoded}")


def legacy_announcer_status_is_answerable(status: object) -> bool:
    return bool(
        type(status) is AgentStatus
        and status.mode is AgentMode.WAITING_FOR_INPUT
        and (
            status.tool_name == "ExitPlanMode"
            or status.event_name in _LEGACY_PRIORITY_BY_EVENT
        )
    )


def _text(value: object, cap: int, fallback: str = "") -> str:
    return _single_line(value)[:cap] or fallback


def _status_for(row: object) -> object | None:
    status = getattr(row, "source_status", None)
    return status if type(status) is AgentStatus else None


def _row_work_key(row: object) -> WorkKey | None:
    key = getattr(row, "work_key", None)
    if type(key) is WorkKey:
        return key
    request = getattr(row, "request_key", None)
    if type(request) is RequestKey:
        return request.work_key
    status = _status_for(row)
    key = getattr(status, "work_key", None)
    return key if type(key) is WorkKey else None


def _row_is_valid(row: object) -> bool:
    return (
        isinstance(row, ProjectedAgentRow)
        and type(getattr(row, "agent_id", None)) is str
        and bool(row.agent_id)
        and type(getattr(row, "provider", None)) is str
        and bool(row.provider)
        and type(getattr(row, "display_name", None)) is str
        and type(getattr(row, "is_subagent", None)) is bool
        and type(getattr(row, "actionable", None)) is bool
    )


def _usable_rows(actionable_rows: Iterable[object] | None) -> tuple[ProjectedAgentRow, ...]:
    if actionable_rows is None:
        return ()
    try:
        rows = tuple(actionable_rows)
    except TypeError:
        return ()
    return tuple(
        row
        for row in rows
        if _row_is_valid(row) and row.actionable and not row.is_subagent
    )


def _usable_statuses(statuses: Iterable[object] | None) -> tuple[AgentStatus, ...]:
    if statuses is None:
        return ()
    try:
        items = tuple(statuses)
    except TypeError:
        return ()
    return tuple(item for item in items if type(item) is AgentStatus)


def _question_for(request_key: RequestKey, statuses: tuple[AgentStatus, ...]) -> str:
    for status in statuses:
        if status.request_key == request_key:
            question = _text(status.message, ANNOUNCER_QUESTION_CAP)
            if question:
                return question
    return "Needs your input"


def _label_for(row: ProjectedAgentRow | None, work: object, provider: str) -> str:
    label = _text(getattr(row, "display_name", None), ANNOUNCER_NAME_CAP)
    if not label:
        label = _text(getattr(work, "safe_label", None), ANNOUNCER_NAME_CAP)
    return label or _text(provider, ANNOUNCER_NAME_CAP, "Agent").title()


def _session_for(row: ProjectedAgentRow | None, work: object) -> str:
    status = _status_for(row) if row is not None else None
    return _text(
        getattr(status, "session_id", None),
        ANNOUNCER_NAME_CAP,
    ) or _text(getattr(row, "agent_id", None), ANNOUNCER_NAME_CAP) or _text(
        getattr(getattr(work, "work_id", None), "value", None),
        ANNOUNCER_NAME_CAP,
        "Session",
    )


def _canonical_alerts(
    operator_state: CanonicalOperatorState,
    rows: tuple[ProjectedAgentRow, ...],
    statuses: tuple[AgentStatus, ...],
) -> tuple[AnnouncerAlert, ...]:
    work_rows: dict[WorkKey, ProjectedAgentRow] = {}
    for row in rows:
        work_key = _row_work_key(row)
        if work_key is not None and work_key not in work_rows:
            work_rows[work_key] = row
    works = {work.key: work for work in operator_state.works}
    result: list[AnnouncerAlert] = []
    for request in operator_state.requests:
        if not (
            request.phase
            in {RequestPhase.LIVE_UNACKNOWLEDGED, RequestPhase.LIVE_ACKNOWLEDGED}
            and request.next_actor is NextActor.USER
        ):
            continue
        row = work_rows.get(request.key.work_key)
        if row is None:
            continue
        work = works.get(request.key.work_key)
        source = request.key.work_key.source_key
        provider = _text(row.provider, ANNOUNCER_NAME_CAP, source.provider_id)
        result.append(
            AnnouncerAlert(
                identity=announcer_alert_identity(request.key),
                agent_id=row.agent_id,
                provider=provider,
                source_label=_label_for(row, work, provider),
                session_label=_session_for(row, work),
                question=_question_for(request.key, statuses),
                priority=ANNOUNCER_PRIORITY_BY_KIND[request.request_kind],
                first_seen_sequence=0,
                seen_on_screen_bar=False,
            )
        )
    return tuple(result)


def _legacy_alerts(
    rows: tuple[ProjectedAgentRow, ...], statuses: tuple[AgentStatus, ...]
) -> tuple[AnnouncerAlert, ...]:
    result: list[AnnouncerAlert] = []
    seen: set[AnnouncerAlertIdentity] = set()
    for row in rows:
        status = _status_for(row)
        request_key = getattr(row, "request_key", None)
        if type(request_key) is not RequestKey:
            request_key = getattr(status, "request_key", None)
        if type(request_key) is RequestKey:
            identity = announcer_alert_identity(request_key)
        else:
            identity = legacy_announcer_alert_identity(
                row.provider,
                row.agent_id,
                getattr(status, "session_id", None),
                getattr(status, "event_name", None),
                _row_work_key(row),
                getattr(status, "tool_name", None),
            )
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        event_name = getattr(status, "event_name", None)
        tool_name = getattr(status, "tool_name", None)
        priority = _LEGACY_PRIORITY_BY_EVENT.get(event_name, AnnouncerAlertPriority.UNKNOWN)
        if tool_name == "ExitPlanMode":
            priority = AnnouncerAlertPriority.APPROVAL
        question = (
            _question_for(request_key, statuses)
            if type(request_key) is RequestKey
            else "Needs your input"
        )
        result.append(
            AnnouncerAlert(
                identity=identity,
                agent_id=row.agent_id,
                provider=_text(row.provider, ANNOUNCER_NAME_CAP, "Agent"),
                source_label=_label_for(row, None, row.provider),
                session_label=_session_for(row, None),
                question=question,
                priority=priority,
                first_seen_sequence=0,
                seen_on_screen_bar=False,
            )
        )
    return tuple(result)


def _current_alerts(
    operator_state: CanonicalOperatorState | None,
    actionable_rows: Iterable[object] | None,
    statuses: Iterable[object] | None,
) -> tuple[AnnouncerAlert, ...]:
    rows = _usable_rows(actionable_rows)
    current_statuses = _usable_statuses(statuses)
    if type(operator_state) is CanonicalOperatorState:
        return _canonical_alerts(operator_state, rows, current_statuses)
    return _legacy_alerts(rows, current_statuses)


def _bump(state: AnnouncerStackState, **changes: object) -> AnnouncerStackState:
    return replace(state, **changes, generation=state.generation + 1)


def reconcile_announcer_stack(
    previous: AnnouncerStackState,
    operator_state: CanonicalOperatorState | None,
    actionable_rows: Iterable[object] | None,
    statuses: Iterable[object] | None,
) -> AnnouncerStackState:
    if type(previous) is not AnnouncerStackState:
        raise ValueError("invalid announcer stack state")
    current = _current_alerts(operator_state, actionable_rows, statuses)
    current_ids = tuple(alert.identity for alert in current)
    current_set = set(current_ids)
    old_order = tuple(identity for identity in previous.ordered_identities if identity in current_set)
    old_set = set(old_order)
    order = old_order + tuple(identity for identity in current_ids if identity not in old_set)
    sequence_map = dict(previous.first_seen_sequences)
    next_sequence = previous.next_sequence
    for identity in order:
        if identity not in sequence_map:
            sequence_map[identity] = next_sequence
            next_sequence += 1
    first_seen = tuple((identity, sequence_map[identity]) for identity in order)
    current_priority = {alert.identity: alert.priority for alert in current}
    priorities = tuple((identity, current_priority[identity]) for identity in order)
    seen = frozenset(identity for identity in previous.seen_identities if identity in current_set)
    if previous.selected_identity in current_set:
        selected = previous.selected_identity
    else:
        priority_map = dict(priorities)
        selected = min(
            (identity for identity in order if identity not in seen),
            key=lambda identity: (
                priority_map[identity],
                sequence_map[identity],
                identity.value,
            ),
            default=None,
        )
        if selected is None and order:
            selected = order[0]
    return AnnouncerStackState(
        ordered_identities=order,
        first_seen_sequences=first_seen,
        priorities=priorities,
        seen_identities=seen,
        selected_identity=selected,
        expanded=previous.expanded,
        next_sequence=next_sequence,
        generation=previous.generation + 1,
    )


def expand_announcer_stack(state: AnnouncerStackState) -> AnnouncerStackState:
    if type(state) is not AnnouncerStackState:
        raise ValueError("invalid announcer stack state")
    return _bump(state, expanded=True) if not state.expanded else state


def collapse_announcer_stack(state: AnnouncerStackState) -> AnnouncerStackState:
    if type(state) is not AnnouncerStackState:
        raise ValueError("invalid announcer stack state")
    return _bump(state, expanded=False) if state.expanded else state


def _navigate(state: AnnouncerStackState, delta: int) -> AnnouncerStackState:
    if not state.ordered_identities:
        return state
    if state.selected_identity not in state.ordered_identities:
        target = state.ordered_identities[0]
    else:
        index = state.ordered_identities.index(state.selected_identity)
        target = state.ordered_identities[(index + delta) % len(state.ordered_identities)]
    return _bump(state, selected_identity=target) if target != state.selected_identity else state


def select_previous_announcer_alert(state: AnnouncerStackState) -> AnnouncerStackState:
    if type(state) is not AnnouncerStackState:
        raise ValueError("invalid announcer stack state")
    return _navigate(state, -1)


def select_next_announcer_alert(state: AnnouncerStackState) -> AnnouncerStackState:
    if type(state) is not AnnouncerStackState:
        raise ValueError("invalid announcer stack state")
    return _navigate(state, 1)


def mark_selected_announcer_alert_seen(state: AnnouncerStackState) -> AnnouncerStackState:
    if type(state) is not AnnouncerStackState:
        raise ValueError("invalid announcer stack state")
    selected = state.selected_identity
    if selected is None or selected in state.seen_identities:
        return state
    seen = frozenset((*state.seen_identities, selected))
    sequence_map = dict(state.first_seen_sequences)
    priority_map = dict(state.priorities)
    next_selected = min(
        (identity for identity in state.ordered_identities if identity not in seen),
        key=lambda identity: (priority_map[identity], sequence_map[identity], identity.value),
        default=selected,
    )
    return _bump(state, seen_identities=seen, selected_identity=next_selected)


def reduce_announcer_stack_intent(
    state: AnnouncerStackState, intent: AnnouncerStackIntent
) -> AnnouncerStackState:
    if type(state) is not AnnouncerStackState:
        raise ValueError("invalid announcer stack intent")
    if type(intent) is not AnnouncerStackIntent:
        return state
    if intent.generation != state.generation or intent.selected_identity != state.selected_identity:
        return state
    if intent.action is AnnouncerStackAction.EXPAND:
        result = _bump(state, expanded=True)
    elif intent.action is AnnouncerStackAction.COLLAPSE:
        result = _bump(state, expanded=False)
    elif intent.action is AnnouncerStackAction.PREVIOUS:
        result = select_previous_announcer_alert(state)
    elif intent.action is AnnouncerStackAction.NEXT:
        result = select_next_announcer_alert(state)
    elif intent.action is AnnouncerStackAction.MARK_SEEN:
        result = mark_selected_announcer_alert_seen(state)
    else:
        result = replace(state, generation=state.generation + 1)
    return result if result.generation != state.generation else replace(
        result, generation=state.generation + 1
    )


# Explicit aliases keep the identity operation discoverable to adapters while
# retaining one implementation and one canonical wire format.
alert_identity_for_request = announcer_alert_identity
canonical_alert_identity = announcer_alert_identity
legacy_alert_identity = legacy_announcer_alert_identity


def _visible_count(value: int) -> str:
    return "99+" if value > 99 else str(value)


def _priority_source(alerts: tuple[AnnouncerAlert, ...]) -> AnnouncerAlert | None:
    return min(
        alerts,
        key=lambda alert: (alert.priority, alert.first_seen_sequence, alert.identity.value),
        default=None,
    )


def project_announcer_stack(
    state: AnnouncerStackState,
    operator_state: CanonicalOperatorState | None,
    actionable_rows: Iterable[object] | None,
    statuses: Iterable[object] | None,
) -> AnnouncerStackPlan:
    if type(state) is not AnnouncerStackState:
        raise ValueError("invalid announcer stack state")
    projected = _current_alerts(operator_state, actionable_rows, statuses)
    by_identity = {alert.identity: alert for alert in projected}
    sequence_map = dict(state.first_seen_sequences)
    alerts = tuple(
        replace(
            by_identity[identity],
            first_seen_sequence=sequence_map.get(identity, 0),
            seen_on_screen_bar=identity in state.seen_identities,
        )
        for identity in state.ordered_identities
        if identity in by_identity
    )
    unseen = tuple(alert for alert in alerts if not alert.seen_on_screen_bar)
    selected_index = next(
        (index for index, alert in enumerate(alerts) if alert.identity == state.selected_identity),
        None,
    )
    if selected_index is None and alerts:
        selected_index = alerts.index(_priority_source(unseen or alerts))
    selected = alerts[selected_index] if selected_index is not None else None
    headline = _priority_source(unseen or alerts)
    total = len(alerts)
    if not unseen:
        visibility = AnnouncerStackVisibility.HIDDEN
    elif state.expanded:
        visibility = AnnouncerStackVisibility.EXPANDED
    else:
        visibility = AnnouncerStackVisibility.COLLAPSED
    collapsed_text: str | None = None
    if visibility is AnnouncerStackVisibility.COLLAPSED:
        if total == 1:
            if headline.question == "Needs your input":
                collapsed_text = f"{headline.source_label} needs you"
            else:
                collapsed_text = f"{headline.source_label}: {headline.question}"
        else:
            collapsed_text = f"{headline.source_label} needs you · {_visible_count(total)} asks"
        collapsed_text = collapsed_text[:ANNOUNCER_TEXT_CAP]
    position_text = f"{selected_index + 1} of {total}" if selected_index is not None else None
    if selected is None:
        accessibility_value = "No actionable asks"
    else:
        accessibility_value = (
            f"{selected.source_label}, {_PRIORITY_LABEL[selected.priority]}, "
            f"{position_text}, {selected.question}; {total} actionable, {len(unseen)} unseen"
        )
    return AnnouncerStackPlan(
        generation=state.generation,
        visibility=visibility,
        alerts=alerts,
        selected_index=selected_index,
        total_actionable_count=total,
        unseen_count=len(unseen),
        highest_priority_source=headline.source_label if headline is not None else None,
        collapsed_text=collapsed_text,
        position_text=position_text,
        accessibility_label="Screen Bar announcer",
        accessibility_value=accessibility_value,
        accessibility_help=(
            "Click to open this asking session or expand the asks. "
            "Mark Seen affects only the Screen Bar; the LED notification remains active."
        ),
        can_previous=total > 1,
        can_next=total > 1,
        can_open=selected is not None,
        can_mark_seen=selected is not None and not selected.seen_on_screen_bar,
    )


__all__ = [
    "ANNOUNCER_PRIORITY_BY_KIND",
    "AnnouncerAlert",
    "AnnouncerAlertIdentity",
    "AnnouncerAlertPriority",
    "AnnouncerStackAction",
    "AnnouncerStackIntent",
    "AnnouncerStackPlan",
    "AnnouncerStackState",
    "AnnouncerStackVisibility",
    "alert_identity_for_request",
    "announcer_alert_identity",
    "canonical_alert_identity",
    "collapse_announcer_stack",
    "empty_announcer_stack_state",
    "expand_announcer_stack",
    "legacy_alert_identity",
    "legacy_announcer_alert_identity",
    "mark_selected_announcer_alert_seen",
    "project_announcer_stack",
    "reconcile_announcer_stack",
    "reduce_announcer_stack_intent",
    "select_next_announcer_alert",
    "select_previous_announcer_alert",
]
