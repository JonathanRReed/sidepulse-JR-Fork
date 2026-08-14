from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from .collector import MonitorSnapshot
from .freshness import bounded_age_seconds
from .models import AgentMode, AgentStatus
from .operator_state import (
    CanonicalOperatorEvent,
    CanonicalOperatorState,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
)
from .provider_facts import NextActor, RequestKey, WorkKey, WorkLifecycle
from .settings import AgentMonitorSettings


class LifecycleMode(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    WAITING = "waiting"
    COMPLETED_RECENTLY = "completed_recently"
    FAILED_VISIBLE = "failed_visible"
    UNKNOWN = "unknown"


class SignalKind(str, Enum):
    FAILURE = "failure"


@dataclass(frozen=True)
class ProjectedAgentRow:
    agent_id: str
    provider: str
    display_name: str
    lifecycle_mode: LifecycleMode
    actionable: bool
    is_subagent: bool
    updated_at: datetime
    # Retained for later color/detail adapters. Semantic consumers use the
    # projected fields above, rather than reclassifying raw status values.
    source_status: AgentStatus
    work_key: WorkKey | None = None
    request_key: RequestKey | None = None


@dataclass(frozen=True)
class TransientSignal:
    event_key: str | SemanticEventKey
    kind: SignalKind
    repetitions: int
    source_agent_id: str | None


@dataclass(frozen=True)
class AttentionProjection:
    lifecycle_mode: LifecycleMode
    actionable_attention: tuple[ProjectedAgentRow, ...]
    visible_rows: tuple[ProjectedAgentRow, ...]
    transient_signals: tuple[TransientSignal, ...]
    dominant_provider: str | None
    click_target_agent_id: str | None


_LIFECYCLE_PRIORITY = {
    LifecycleMode.WAITING: 0,
    LifecycleMode.ACTIVE: 1,
    LifecycleMode.FAILED_VISIBLE: 2,
    LifecycleMode.COMPLETED_RECENTLY: 3,
    LifecycleMode.IDLE: 4,
    LifecycleMode.UNKNOWN: 5,
}

_FAILURE_EVENTS = {
    "PostToolUseFailure",
    "StopFailure",
    "PermissionDenied",
}


def stable_event_key(status: AgentStatus) -> str:
    """Identifies one provider event without depending on object identity."""
    return "\0".join(
        (
            status.provider,
            status.agent_id,
            _semantic_event_name(status),
            status.updated_at.isoformat(),
        )
    )


def actionable_request(status: AgentStatus, settings: AgentMonitorSettings) -> bool:
    if status.is_subagent and not settings.subagent_asks_alert:
        return False
    return status.mode == AgentMode.WAITING_FOR_INPUT and status.event_name in {
        "PermissionRequest",
        "Notification",
    }


def project_attention(
    snapshot: MonitorSnapshot,
    settings: AgentMonitorSettings,
    consumed_event_keys: tuple[str, ...] = (),
) -> AttentionProjection:
    """Purely project a fresh monitor snapshot into shared UI semantics."""
    consumed = frozenset(consumed_event_keys)
    collected_at = getattr(snapshot, "collected_at", datetime.now(timezone.utc))
    plausible_statuses = tuple(
        status
        for status in snapshot.statuses
        if bounded_age_seconds(collected_at, status.updated_at) != float("inf")
    )
    rows = tuple(_project_row(status, settings) for status in plausible_statuses)
    actionable = tuple(
        sorted(
            (row for row in rows if row.actionable),
            key=lambda row: (row.updated_at, row.agent_id),
        )
    )
    signals: list[TransientSignal] = []
    emitted = set(consumed)
    for status in plausible_statuses:
        event_key = stable_event_key(status)
        if not _is_failure_event(status) or event_key in emitted:
            continue
        signals.append(
            TransientSignal(
                event_key=event_key,
                kind=SignalKind.FAILURE,
                repetitions=2,
                source_agent_id=status.agent_id,
            )
        )
        emitted.add(event_key)
    representative = min(
        rows,
        key=lambda row: (
            _LIFECYCLE_PRIORITY[row.lifecycle_mode],
            -row.updated_at.timestamp(),
            row.agent_id,
        ),
        default=None,
    )
    return AttentionProjection(
        lifecycle_mode=(
            representative.lifecycle_mode if representative is not None else LifecycleMode.IDLE
        ),
        actionable_attention=actionable,
        visible_rows=rows,
        transient_signals=tuple(signals),
        dominant_provider=(
            actionable[0].provider
            if actionable
            else representative.provider if representative is not None else None
        ),
        click_target_agent_id=actionable[0].agent_id if actionable else None,
    )


def project_attention_from_operator_state(
    state: CanonicalOperatorState,
    events: tuple[CanonicalOperatorEvent, ...],
    settings: AgentMonitorSettings,
) -> AttentionProjection:
    """Project canonical truth without reclassifying status text or modes."""
    if type(state) is not CanonicalOperatorState:
        raise ValueError("invalid canonical operator state")
    if type(events) is not tuple or not all(
        type(event) is CanonicalOperatorEvent for event in events
    ):
        raise ValueError("invalid canonical operator events")
    request_by_key = {request.key: request for request in state.requests}
    rows: list[ProjectedAgentRow] = []
    for work in state.works:
        requests = tuple(
            request_by_key[key]
            for key in work.request_keys
            if key in request_by_key
        )
        actionable_requests = tuple(
            request
            for request in requests
            if request.phase
            in {
                RequestPhase.LIVE_UNACKNOWLEDGED,
                RequestPhase.LIVE_ACKNOWLEDGED,
            }
            and request.next_actor is NextActor.USER
        )
        actionable = bool(actionable_requests)
        if work.parent_key is not None and not settings.subagent_asks_alert:
            actionable = False
        request_key = (
            min(actionable_requests, key=lambda request: request.key).key
            if actionable
            else None
        )
        lifecycle = {
            WorkLifecycle.IDLE: LifecycleMode.IDLE,
            WorkLifecycle.ACTIVE: LifecycleMode.ACTIVE,
            WorkLifecycle.WAITING: (
                LifecycleMode.WAITING if actionable else LifecycleMode.UNKNOWN
            ),
            WorkLifecycle.COMPLETED: LifecycleMode.COMPLETED_RECENTLY,
            WorkLifecycle.FAILED: LifecycleMode.FAILED_VISIBLE,
            WorkLifecycle.UNKNOWN: LifecycleMode.UNKNOWN,
        }[work.lifecycle]
        source_status = AgentStatus(
            provider=work.key.source_key.provider_id,
            agent_id=(
                f"{work.key.source_key.provider_id}:"
                f"{'agent' if work.parent_key is not None else 'session'}:"
                f"{work.key.work_id.value}"
            ),
            display_name=work.safe_label,
            mode=AgentMode.UNKNOWN,
            updated_at=datetime.fromtimestamp(
                work.watermark.occurred_at_epoch,
                timezone.utc,
            ),
            event_name="Canonical",
            session_id=(
                work.parent_key.work_id.value
                if work.parent_key is not None
                else work.key.work_id.value
            ),
            work_key=work.key,
            request_key=request_key,
        )
        rows.append(
            ProjectedAgentRow(
                agent_id=source_status.agent_id,
                provider=source_status.provider,
                display_name=work.safe_label,
                lifecycle_mode=lifecycle,
                actionable=actionable,
                is_subagent=work.parent_key is not None,
                updated_at=source_status.updated_at,
                source_status=source_status,
                work_key=work.key,
                request_key=request_key,
            )
        )
    ordered_rows = tuple(
        sorted(rows, key=lambda row: (row.updated_at, row.work_key))
    )
    actionable_rows = tuple(row for row in ordered_rows if row.actionable)
    failure_signals = tuple(
        TransientSignal(
            event_key=event.key,
            kind=SignalKind.FAILURE,
            repetitions=2,
            source_agent_id=None,
        )
        for event in events
        if event.kind is TransitionKind.FAILED
    )
    representative = min(
        ordered_rows,
        key=lambda row: (
            _LIFECYCLE_PRIORITY[row.lifecycle_mode],
            -row.updated_at.timestamp(),
            row.work_key,
        ),
        default=None,
    )
    return AttentionProjection(
        lifecycle_mode=(
            LifecycleMode.IDLE
            if representative is None
            else representative.lifecycle_mode
        ),
        actionable_attention=actionable_rows,
        visible_rows=ordered_rows,
        transient_signals=failure_signals,
        dominant_provider=(
            actionable_rows[0].provider
            if actionable_rows
            else representative.provider if representative is not None else None
        ),
        click_target_agent_id=None,
    )


def _project_row(
    status: AgentStatus,
    settings: AgentMonitorSettings,
) -> ProjectedAgentRow:
    actionable = actionable_request(status, settings)
    return ProjectedAgentRow(
        agent_id=status.agent_id,
        provider=status.provider,
        display_name=status.display_name,
        lifecycle_mode=_lifecycle_mode(status, actionable),
        actionable=actionable,
        is_subagent=status.is_subagent,
        updated_at=status.updated_at,
        source_status=status,
        work_key=status.work_key,
        request_key=status.request_key,
    )


def _lifecycle_mode(status: AgentStatus, actionable: bool) -> LifecycleMode:
    if actionable:
        return LifecycleMode.WAITING
    if status.mode in {
        AgentMode.WORKING,
        AgentMode.TOOL_RUNNING,
        AgentMode.LONG_TASK_PROGRESS,
    }:
        return LifecycleMode.ACTIVE
    if status.mode == AgentMode.BLOCKED_ERROR:
        return LifecycleMode.FAILED_VISIBLE
    if status.mode == AgentMode.COMPLETED:
        return LifecycleMode.COMPLETED_RECENTLY
    if status.mode == AgentMode.IDLE_READY:
        return LifecycleMode.IDLE
    return LifecycleMode.UNKNOWN


def _is_failure_event(status: AgentStatus) -> bool:
    return not status.stale and (
        status.event_name in _FAILURE_EVENTS
        or (
            status.event_name == "PostToolUse"
            and status.mode == AgentMode.BLOCKED_ERROR
        )
    )


def _semantic_event_name(status: AgentStatus) -> str:
    if status.event_name == "PostToolUseFailure" or (
        status.event_name == "PostToolUse" and status.mode == AgentMode.BLOCKED_ERROR
    ):
        return "PostToolUseFailure"
    return status.event_name
