from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum

from .collector import MonitorSnapshot
from .freshness import bounded_age_seconds
from .models import AgentMode, AgentStatus
from .operator_state import (
    COMPLETED_RECENT_SECONDS,
    CanonicalOperatorEvent,
    CanonicalOperatorState,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
    active_work_went_silent,
    completed_work_no_longer_recent,
    projection_now_epoch,
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


_LIFECYCLE_PRIORITY = {
    LifecycleMode.WAITING: 0,
    LifecycleMode.ACTIVE: 1,
    LifecycleMode.FAILED_VISIBLE: 2,
    LifecycleMode.COMPLETED_RECENTLY: 3,
    LifecycleMode.IDLE: 4,
    LifecycleMode.UNKNOWN: 5,
}


@dataclass(frozen=True)
class AttentionProjection:
    """The one place "which agents exist" is decided for every surface.

    ``visible_rows`` is MAIN AGENTS ONLY, structurally. A sub-agent is
    never a row, never a light, never an interrupt and never part of a
    count -- one main session fans out to 100+ Task workers (200
    observed), and a live snapshot ran 87 workers against 27 mains, so
    every consumer that read this field inherited a ~4x inflation. The
    menu bar said "Active: 34" and the dropdown said "24 active" with
    ONE main agent running, because each one filtered (or failed to
    filter) at its own call site and they disagreed.

    So the filter lives HERE, not at the call sites, and it is enforced
    in ``__post_init__`` rather than merely applied by the projectors:
    any construction that puts a worker in ``visible_rows`` -- including
    ``dataclasses.replace`` on a device-pinned copy, and including a
    test -- has it moved to ``worker_rows`` instead of silently lighting
    an LED.

    ``worker_rows`` exists because sub-agents matter in exactly one way:
    they hold their parent's completion open. The mailbox reads them to
    count a family's workers and to fold a worker's ask into its parent.
    Nothing else should touch them.
    """

    lifecycle_mode: LifecycleMode
    actionable_attention: tuple[ProjectedAgentRow, ...]
    visible_rows: tuple[ProjectedAgentRow, ...]
    transient_signals: tuple[TransientSignal, ...]
    dominant_provider: str | None
    click_target_agent_id: str | None
    worker_rows: tuple[ProjectedAgentRow, ...] = ()

    def __post_init__(self) -> None:
        if not any(row.is_subagent for row in self.visible_rows):
            return
        primary = tuple(row for row in self.visible_rows if not row.is_subagent)
        demoted = tuple(row for row in self.visible_rows if row.is_subagent)
        object.__setattr__(self, "visible_rows", primary)
        object.__setattr__(self, "worker_rows", (*self.worker_rows, *demoted))

    @property
    def light_rows(self) -> tuple[ProjectedAgentRow, ...]:
        """The rows the LEDs and the Screen Bar are allowed to paint.

        Main agents. When there are none at all, the single most urgent
        orphaned worker stands in for the whole background crowd -- one
        presence, never N, exactly as the mailbox collapses them into one
        "Background agents" row. Painting the crowd itself is how 87
        workers became 87 identity colours and left Claude's brand hue
        off the strip entirely.
        """
        if self.visible_rows:
            return self.visible_rows
        if not self.worker_rows:
            return ()
        return (
            min(
                self.worker_rows,
                key=lambda row: (
                    _LIFECYCLE_PRIORITY[row.lifecycle_mode],
                    -row.updated_at.timestamp(),
                    row.agent_id,
                ),
            ),
        )

    @property
    def all_rows(self) -> tuple[ProjectedAgentRow, ...]:
        """Every row at every depth, for the few callers entitled to one.

        The mailbox and the dropdown's per-family worker rollup, and
        nothing else. Asking for this is deliberately conspicuous: the
        default field is the safe one, and a consumer has to say out loud
        that it wants sub-agents.
        """
        return (*self.visible_rows, *self.worker_rows)


# Terminal, operator-facing failures only. A PostToolUseFailure is a tool
# the agent continues past (a failed grep, a nonzero exit) -- routine
# agentic work that must not fire the red failure blink; the same rule
# already governs mode mapping in the collector.
_FAILURE_EVENTS = {
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
    rows = tuple(
        _project_row(status, settings, now=collected_at)
        for status in plausible_statuses
    )
    rows = _promote_delegating_parents(rows)
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
    primary_rows = tuple(row for row in rows if not row.is_subagent)
    worker_rows = tuple(row for row in rows if row.is_subagent)
    representative = min(
        _light_driver_candidates(primary_rows, worker_rows),
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
        visible_rows=primary_rows,
        worker_rows=worker_rows,
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
    now_epoch = projection_now_epoch(state)
    # A main whose own thread paused while its sub-agents carry the work
    # is still WORKING: Claude fires Stop the moment the main turn ends,
    # even mid-delegation, and a live ledger showed a session "completed"
    # for 30+ minutes while its workers streamed tool events under it
    # (2026-08-27, owner report: three mains running, count said one).
    # Freshness gates the promotion -- children finishing or going
    # silent ends it, the mirror of the silence demotion below.
    delegating_parents = {
        work.parent_key
        for work in state.works
        if work.parent_key is not None
        and work.lifecycle is WorkLifecycle.ACTIVE
        and not active_work_went_silent(work, now_epoch)
    }
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
        # ACTIVE means HEARD FROM: a dead session's work stays
        # lifecycle-ACTIVE forever, and this projection drives the
        # LIGHTS -- the strip pulsed "working" long after the owner
        # watched the session finish. Silent-past-the-line demotes to
        # the idle whisper; the next real event resurrects it.
        if lifecycle is LifecycleMode.ACTIVE and active_work_went_silent(
            work, now_epoch
        ):
            lifecycle = LifecycleMode.IDLE
        # COMPLETED is a moment: after the recent window the row settles
        # back to the idle whisper instead of holding the done green (and
        # the COMPLETED aggregate) until the presence horizon drops it.
        if (
            lifecycle is LifecycleMode.COMPLETED_RECENTLY
            and completed_work_no_longer_recent(work, now_epoch)
        ):
            lifecycle = LifecycleMode.IDLE
        # The delegation promotion. Never touches WAITING or FAILED --
        # an ask must keep asking and a failure must stay named.
        if (
            work.key in delegating_parents
            and lifecycle
            in {
                LifecycleMode.IDLE,
                LifecycleMode.COMPLETED_RECENTLY,
                LifecycleMode.UNKNOWN,
            }
        ):
            lifecycle = LifecycleMode.ACTIVE
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
    primary_rows = tuple(row for row in ordered_rows if not row.is_subagent)
    worker_rows = tuple(row for row in ordered_rows if row.is_subagent)
    representative = min(
        _light_driver_candidates(primary_rows, worker_rows),
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
        visible_rows=primary_rows,
        worker_rows=worker_rows,
        transient_signals=failure_signals,
        dominant_provider=(
            actionable_rows[0].provider
            if actionable_rows
            else representative.provider if representative is not None else None
        ),
        click_target_agent_id=None,
    )


_PROMOTABLE_PARENT_LIFECYCLES = frozenset(
    {
        LifecycleMode.IDLE,
        LifecycleMode.COMPLETED_RECENTLY,
        LifecycleMode.UNKNOWN,
    }
)


def _promote_delegating_parents(
    rows: tuple[ProjectedAgentRow, ...],
) -> tuple[ProjectedAgentRow, ...]:
    """A main whose sub-agents are still working IS still working.

    Claude fires Stop the moment the main turn ends, even mid-delegation:
    a live ledger showed a session "completed" for 30+ minutes while its
    workers streamed tool events under it (2026-08-27, owner report --
    three mains running, the count said one). Only quiet lifecycles are
    promoted: an ask keeps asking, a failure stays named. The promotion
    ends by itself when the children finish or go stale, the same
    self-healing shape as the silence clock. The row's status mode is
    relabeled Working so the dropdown tells the same story as the light.
    """
    delegating_parent_ids = {
        row.source_status.parent_agent_id
        for row in rows
        if row.is_subagent
        and row.lifecycle_mode is LifecycleMode.ACTIVE
        and row.source_status.parent_agent_id
    }
    if not delegating_parent_ids:
        return rows
    return tuple(
        replace(
            row,
            lifecycle_mode=LifecycleMode.ACTIVE,
            source_status=replace(
                row.source_status,
                mode=AgentMode.WORKING,
            ),
        )
        if (
            not row.is_subagent
            and row.agent_id in delegating_parent_ids
            and row.lifecycle_mode in _PROMOTABLE_PARENT_LIFECYCLES
        )
        else row
        for row in rows
    )


def _light_driver_candidates(
    primary_rows: tuple[ProjectedAgentRow, ...],
    worker_rows: tuple[ProjectedAgentRow, ...],
) -> tuple[ProjectedAgentRow, ...]:
    """Which rows may decide what the light says.

    Main agents, and a main agent always outranks a worker however urgent
    the worker is. Choosing the most urgent row over EVERY depth is what
    handed the entire light language to a Task worker -- a live snapshot's
    representative was ``claude:agent:a70f42924b7bb211d``, so 35 busy
    workers under an idle main made the strip announce "working" about
    something the user cannot see, click, or answer.

    Workers are considered only when there is no main agent at all. That
    is the orphaned-worker case the mailbox already surfaces as one
    "Background agents" row: a worker blocked with nobody above it is
    still the only thing happening, and going dark would be a worse lie
    than naming it.
    """
    return primary_rows or worker_rows


def _project_row(
    status: AgentStatus,
    settings: AgentMonitorSettings,
    *,
    now: datetime | None = None,
) -> ProjectedAgentRow:
    actionable = actionable_request(status, settings)
    return ProjectedAgentRow(
        agent_id=status.agent_id,
        provider=status.provider,
        display_name=status.display_name,
        lifecycle_mode=_lifecycle_mode(status, actionable, now=now),
        actionable=actionable,
        is_subagent=status.is_subagent,
        updated_at=status.updated_at,
        source_status=status,
        work_key=status.work_key,
        request_key=status.request_key,
    )


def _lifecycle_mode(
    status: AgentStatus,
    actionable: bool,
    *,
    now: datetime | None = None,
) -> LifecycleMode:
    if actionable:
        return LifecycleMode.WAITING
    if status.mode in {
        AgentMode.WORKING,
        AgentMode.TOOL_RUNNING,
        AgentMode.LONG_TASK_PROGRESS,
    }:
        return LifecycleMode.ACTIVE
    if status.mode == AgentMode.BLOCKED_ERROR:
        if (
            now is not None
            and (now - status.updated_at).total_seconds()
            > COMPLETED_RECENT_SECONDS * 2
        ):
            return LifecycleMode.IDLE
        return LifecycleMode.FAILED_VISIBLE
    if status.mode == AgentMode.COMPLETED:
        # Done is a MOMENT (this is the LIVE path -- the canonical
        # variant below has the same rule): past the recent window the
        # row settles to the idle whisper instead of holding the done
        # green until collector staleness or the presence horizon.
        if (
            now is not None
            and (now - status.updated_at).total_seconds()
            > COMPLETED_RECENT_SECONDS
        ):
            return LifecycleMode.IDLE
        return LifecycleMode.COMPLETED_RECENTLY
    if status.mode in {AgentMode.IDLE_READY, AgentMode.ENDED_UNCONFIRMED}:
        # Ended-unconfirmed is a whisper, never a signal: the session is
        # probably over and nobody said so -- no light language for that
        # beyond the idle floor.
        return LifecycleMode.IDLE
    return LifecycleMode.UNKNOWN


def _is_failure_event(status: AgentStatus) -> bool:
    return not status.stale and status.event_name in _FAILURE_EVENTS


def _semantic_event_name(status: AgentStatus) -> str:
    if status.event_name == "PostToolUseFailure" or (
        status.event_name == "PostToolUse" and status.mode == AgentMode.BLOCKED_ERROR
    ):
        return "PostToolUseFailure"
    return status.event_name
