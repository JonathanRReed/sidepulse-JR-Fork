from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .attention import AttentionProjection, LifecycleMode, ProjectedAgentRow
from .models import AgentMode, AgentStatus
from .operator_state import (
    PRESENCE_HORIZON_SECONDS,
    CanonicalOperatorState,
    CanonicalRequestTruth,
    RequestPhase,
)
from .provider_facts import (
    NextActor,
    RequestKey,
    SourceFreshness,
    WorkKey,
    WorkLifecycle,
)


class MailboxSectionKind(str, Enum):
    NEEDS_YOU = "needs_you"
    IN_PROGRESS = "in_progress"
    READY_FOR_REVIEW = "ready_for_review"
    RECENT = "recent"


@dataclass(frozen=True, slots=True)
class MailboxRow:
    work_key: WorkKey
    request_key: RequestKey | None
    safe_label: str
    lifecycle: WorkLifecycle
    next_actor: NextActor
    source_freshness: SourceFreshness
    request_keys: tuple[RequestKey, ...]
    actionable: bool
    worker_count: int
    updated_at_epoch: float
    stable_order: int
    timing_uncertain: bool


@dataclass(frozen=True, slots=True)
class LegacyMailboxRow:
    """Temporary row for the reviewed AgentStatus compatibility projector."""

    agent_id: str
    provider: str
    display_name: str
    lifecycle_mode: LifecycleMode
    activity_label: str | None
    actionable: bool
    navigation_agent_id: str | None
    worker_count: int
    updated_at: datetime
    stable_order: int
    work_key: WorkKey | None = None
    request_key: RequestKey | None = None


@dataclass(frozen=True, slots=True)
class MailboxSection:
    kind: MailboxSectionKind
    rows: tuple[MailboxRow | LegacyMailboxRow, ...]
    overflow_count: int


@dataclass(frozen=True, slots=True)
class AgentMailboxProjection:
    sections: tuple[MailboxSection, ...]
    active_count: int
    needs_you_count: int
    ready_count: int
    retained_order: tuple[tuple[WorkKey | str, int], ...]


@dataclass(frozen=True, slots=True)
class LegacyAgentMailboxProjection(AgentMailboxProjection):
    """Explicit marker for the temporary AgentStatus compatibility projector."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    agent_id: str
    provider: str
    display_name: str
    lifecycle_mode: LifecycleMode
    activity_label: str | None
    actionable: bool
    navigation_agent_id: str | None
    worker_count: int
    updated_at: datetime
    completion_ids: frozenset[str]


_SECTION_ORDER = (
    MailboxSectionKind.NEEDS_YOU,
    MailboxSectionKind.IN_PROGRESS,
    MailboxSectionKind.READY_FOR_REVIEW,
    MailboxSectionKind.RECENT,
)
_ORPHAN_WORKERS_ID = "sidepulse:mailbox:background-agents"
_UNKNOWN_ACTIVITY_MAX_LENGTH = 48
_DISPLAY_WORKER_FRESHNESS = frozenset(
    {
        SourceFreshness.FRESH,
        SourceFreshness.PARTIAL,
        SourceFreshness.TIMING_UNCERTAIN,
    }
)

_READ_TOOLS = {
    "open_file",
    "read",
    "read_file",
    "read_text_file",
    "readfile",
    "view_file",
}
_EDIT_TOOLS = {
    "apply_patch",
    "edit",
    "edit_file",
    "multiedit",
    "notebookedit",
    "write",
    "write_file",
}
_SEARCH_TOOLS = {
    "find",
    "glob",
    "grep",
    "list_directory",
    "rg",
    "search",
    "search_files",
}
_SHELL_TOOLS = {
    "bash",
    "exec_command",
    "powershell",
    "run_terminal_command",
    "shell",
    "terminal",
    "zsh",
}
_THINKING_TOOLS = {"reason", "think", "thinking"}
_SENSITIVE_TOOL_FRAGMENTS = {
    "auth",
    "bearer",
    "cookie",
    "credential",
    "key",
    "password",
    "secret",
    "token",
}


def project_mailbox(
    projection: AttentionProjection,
    *,
    previous_order: Mapping[str, int] | None = None,
    seen_completion_ids: AbstractSet[str] = frozenset(),
    max_rows_per_section: int = 12,
    max_primary_agents: int = 100,
) -> AgentMailboxProjection:
    """Project authoritative attention rows into stable, bounded mailbox shelves."""
    if max_rows_per_section < 0:
        raise ValueError("max_rows_per_section must be non-negative")
    if max_primary_agents < 0:
        raise ValueError("max_primary_agents must be non-negative")

    prior = _valid_previous_order(previous_order)
    # The mailbox is the one consumer that needs workers: it counts a
    # family's fan-out and folds a worker's ask into its parent's row.
    # It reads them from ``worker_rows`` because ``visible_rows`` is
    # structurally main-agents-only (see AttentionProjection).
    candidates = _primary_candidates(projection.all_rows)
    order_by_id = dict(prior)
    next_order = max(order_by_id.values(), default=-1) + 1
    new_candidates = sorted(
        (candidate for candidate in candidates if candidate.agent_id not in order_by_id),
        key=lambda candidate: (candidate.updated_at, candidate.agent_id),
    )
    for candidate in new_candidates:
        order_by_id[candidate.agent_id] = next_order
        next_order += 1

    section_rows: dict[MailboxSectionKind, list[LegacyMailboxRow]] = {
        kind: [] for kind in _SECTION_ORDER
    }
    candidate_sections: dict[str, MailboxSectionKind] = {}
    candidate_updated_at: dict[str, datetime] = {}
    for candidate in candidates:
        kind = _section_for(candidate, seen_completion_ids)
        candidate_sections[candidate.agent_id] = kind
        candidate_updated_at[candidate.agent_id] = candidate.updated_at
        section_rows[kind].append(
            LegacyMailboxRow(
                agent_id=candidate.agent_id,
                provider=candidate.provider,
                display_name=candidate.display_name,
                lifecycle_mode=candidate.lifecycle_mode,
                activity_label=candidate.activity_label,
                actionable=candidate.actionable,
                navigation_agent_id=candidate.navigation_agent_id,
                worker_count=candidate.worker_count,
                updated_at=candidate.updated_at,
                stable_order=order_by_id[candidate.agent_id],
            )
        )

    section_rows[MailboxSectionKind.NEEDS_YOU].sort(
        key=lambda row: (row.updated_at, row.agent_id)
    )
    for kind in _SECTION_ORDER[1:]:
        section_rows[kind].sort(key=lambda row: (row.stable_order, row.agent_id))

    sections = tuple(
        MailboxSection(
            kind=kind,
            rows=tuple(section_rows[kind][:max_rows_per_section]),
            overflow_count=max(0, len(section_rows[kind]) - max_rows_per_section),
        )
        for kind in _SECTION_ORDER
    )
    retained_order = _bounded_retained_order(
        order_by_id,
        prior_ids=frozenset(prior),
        candidate_sections=candidate_sections,
        candidate_updated_at=candidate_updated_at,
        limit=max_primary_agents,
    )
    needs_you_count = len(section_rows[MailboxSectionKind.NEEDS_YOU])
    in_progress_count = len(section_rows[MailboxSectionKind.IN_PROGRESS])
    return LegacyAgentMailboxProjection(
        sections=sections,
        active_count=needs_you_count + in_progress_count,
        needs_you_count=needs_you_count,
        ready_count=len(section_rows[MailboxSectionKind.READY_FOR_REVIEW]),
        retained_order=retained_order,
    )


def project_canonical_mailbox(
    state: CanonicalOperatorState,
    *,
    previous_order: Mapping[WorkKey, int] | None = None,
    max_rows_per_section: int = 12,
    max_primary_agents: int = 100,
) -> AgentMailboxProjection:
    """Project exact canonical operator truth into bounded family shelves."""
    if type(state) is not CanonicalOperatorState:
        raise ValueError("invalid canonical operator state")
    if type(max_rows_per_section) is not int or max_rows_per_section < 0:
        raise ValueError("max_rows_per_section must be non-negative")
    if type(max_primary_agents) is not int or max_primary_agents < 0:
        raise ValueError("max_primary_agents must be non-negative")

    prior = _valid_canonical_previous_order(previous_order)
    work_by_key = {work.key: work for work in state.works}
    children_by_parent: dict[WorkKey, list] = defaultdict(list)
    primary_works = []
    for work in state.works:
        if work.parent_key is None:
            primary_works.append(work)
            continue
        parent = work_by_key.get(work.parent_key)
        if (
            parent is not None
            and parent.parent_key is None
            and work.source_freshness in _DISPLAY_WORKER_FRESHNESS
        ):
            children_by_parent[parent.key].append(work)

    request_by_key = {request.key: request for request in state.requests}
    candidate_data: list[tuple[object, tuple, tuple[CanonicalRequestTruth, ...]]] = []
    for work in primary_works:
        family = (work, *children_by_parent.get(work.key, ()))
        family_request_keys = {
            request_key for member in family for request_key in member.request_keys
        }
        joined_requests = tuple(
            request
            for request in state.requests
            if request.key in family_request_keys
            and request_by_key.get(request.key) is request
        )
        candidate_data.append((work, family, joined_requests))

    order_by_key = dict(prior)
    next_order = max(order_by_key.values(), default=-1) + 1
    new_candidates = sorted(
        (
            item
            for item in candidate_data
            if item[0].key not in order_by_key
        ),
        key=lambda item: (
            _canonical_candidate_epoch(item[0], item[2]),
            _work_key_sort_key(item[0].key),
        ),
    )
    for work, _family, _requests in new_candidates:
        order_by_key[work.key] = next_order
        next_order += 1

    section_rows: dict[MailboxSectionKind, list[MailboxRow]] = {
        kind: [] for kind in _SECTION_ORDER
    }
    candidate_sections: dict[WorkKey, MailboxSectionKind] = {}
    candidate_epochs: dict[WorkKey, float] = {}
    for work, family, requests in candidate_data:
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
        if actionable:
            authoritative_request = min(
                actionable_requests,
                key=lambda request: (
                    _request_epoch(request),
                    _request_key_sort_key(request.key),
                ),
            )
            authoritative_work = work_by_key[authoritative_request.key.work_key]
            lifecycle = WorkLifecycle.WAITING
            next_actor = NextActor.USER
            source_freshness = authoritative_work.source_freshness
            updated_at_epoch = _request_epoch(authoritative_request)
        else:
            lifecycle = work.lifecycle
            next_actor = work.next_actor
            source_freshness = work.source_freshness
            updated_at_epoch = work.watermark.occurred_at_epoch
        row = MailboxRow(
            work_key=work.key,
            request_key=(authoritative_request.key if actionable else None),
            safe_label=work.safe_label,
            lifecycle=lifecycle,
            next_actor=next_actor,
            source_freshness=source_freshness,
            request_keys=tuple(request.key for request in requests),
            actionable=actionable,
            worker_count=len(family) - 1,
            updated_at_epoch=updated_at_epoch,
            stable_order=order_by_key[work.key],
            timing_uncertain=any(member.timing_uncertain for member in family),
        )
        section = _canonical_section_for(
            row, now_epoch=state.last_clock.wall_epoch if state.last_clock else None
        )
        candidate_sections[work.key] = section
        candidate_epochs[work.key] = updated_at_epoch
        section_rows[section].append(row)

    section_rows[MailboxSectionKind.NEEDS_YOU].sort(
        key=lambda row: (row.updated_at_epoch, _work_key_sort_key(row.work_key))
    )
    for kind in _SECTION_ORDER[1:]:
        section_rows[kind].sort(
            key=lambda row: (row.stable_order, _work_key_sort_key(row.work_key))
        )
    sections = tuple(
        MailboxSection(
            kind=kind,
            rows=tuple(section_rows[kind][:max_rows_per_section]),
            overflow_count=max(0, len(section_rows[kind]) - max_rows_per_section),
        )
        for kind in _SECTION_ORDER
    )
    retained_order = _bounded_canonical_retained_order(
        order_by_key,
        prior_keys=frozenset(prior),
        candidate_sections=candidate_sections,
        candidate_epochs=candidate_epochs,
        limit=max_primary_agents,
    )
    needs_you_count = len(section_rows[MailboxSectionKind.NEEDS_YOU])
    in_progress_count = len(section_rows[MailboxSectionKind.IN_PROGRESS])
    return AgentMailboxProjection(
        sections=sections,
        active_count=needs_you_count + in_progress_count,
        needs_you_count=needs_you_count,
        ready_count=len(section_rows[MailboxSectionKind.READY_FOR_REVIEW]),
        retained_order=retained_order,
    )


def _source_key_sort_key(source) -> tuple[str, str, str, str]:
    return (
        source.provider_id,
        source.adapter_id,
        source.source_instance_id,
        source.capability_id,
    )


def _work_key_sort_key(key: WorkKey) -> tuple[tuple[str, str, str, str], str]:
    return (_source_key_sort_key(key.source_key), key.work_id.value)


def _request_key_sort_key(
    key: RequestKey,
) -> tuple[tuple[tuple[str, str, str, str], str], str]:
    return (_work_key_sort_key(key.work_key), key.request_id.value)


def _request_epoch(request: CanonicalRequestTruth) -> float:
    return (
        request.opened_at_epoch
        if request.opened_at_epoch is not None
        else request.watermark.occurred_at_epoch
    )


def _canonical_candidate_epoch(work, requests: tuple[CanonicalRequestTruth, ...]) -> float:
    actionable_epochs = tuple(
        _request_epoch(request)
        for request in requests
        if request.phase
        in {
            RequestPhase.LIVE_UNACKNOWLEDGED,
            RequestPhase.LIVE_ACKNOWLEDGED,
        }
        and request.next_actor is NextActor.USER
    )
    return min(actionable_epochs, default=work.watermark.occurred_at_epoch)


def _canonical_section_for(
    row: MailboxRow, *, now_epoch: float | None = None
) -> MailboxSectionKind:
    # Presence horizon: a family heard NOTHING for an hour is history.
    # Without this gate an hour-dead ACTIVE work sat "In Progress" (and
    # counted active) until 24h retention -- while the Agent Browser
    # beside it already called the same work stale.
    if (
        now_epoch is not None
        and now_epoch - row.updated_at_epoch > PRESENCE_HORIZON_SECONDS
    ):
        return MailboxSectionKind.RECENT
    if row.actionable:
        return MailboxSectionKind.NEEDS_YOU
    if row.lifecycle in {WorkLifecycle.ACTIVE, WorkLifecycle.WAITING}:
        return MailboxSectionKind.IN_PROGRESS
    if row.lifecycle in {WorkLifecycle.COMPLETED, WorkLifecycle.FAILED}:
        return MailboxSectionKind.READY_FOR_REVIEW
    return MailboxSectionKind.RECENT


def _valid_canonical_previous_order(
    previous_order: Mapping[WorkKey, int] | None,
) -> dict[WorkKey, int]:
    if previous_order is None:
        return {}
    return {
        key: order
        for key, order in previous_order.items()
        if type(key) is WorkKey
        and type(order) is int
        and order >= 0
    }


def _bounded_canonical_retained_order(
    order_by_key: Mapping[WorkKey, int],
    *,
    prior_keys: AbstractSet[WorkKey],
    candidate_sections: Mapping[WorkKey, MailboxSectionKind],
    candidate_epochs: Mapping[WorkKey, float],
    limit: int,
) -> tuple[tuple[WorkKey, int], ...]:
    if limit == 0:
        return ()

    def retention_key(
        item: tuple[WorkKey, int],
    ) -> tuple[float, float, tuple[tuple[str, str, str, str], str]]:
        work_key, stable_order = item
        section = candidate_sections.get(work_key)
        if section in {MailboxSectionKind.NEEDS_YOU, MailboxSectionKind.IN_PROGRESS}:
            return (0.0, float(stable_order), _work_key_sort_key(work_key))
        if section is MailboxSectionKind.READY_FOR_REVIEW:
            return (1.0, float(stable_order), _work_key_sort_key(work_key))
        if section is MailboxSectionKind.RECENT:
            return (
                2.0,
                -candidate_epochs[work_key],
                _work_key_sort_key(work_key),
            )
        if work_key in prior_keys:
            return (3.0, -float(stable_order), _work_key_sort_key(work_key))
        return (4.0, math.inf, _work_key_sort_key(work_key))

    retained = dict(sorted(order_by_key.items(), key=retention_key)[:limit])
    return tuple(
        sorted(
            retained.items(),
            key=lambda item: (item[1], _work_key_sort_key(item[0])),
        )
    )


def normalized_activity_label(status: AgentStatus) -> str | None:
    """Return a bounded semantic label without consulting private status payloads."""
    if status.mode == AgentMode.WAITING_FOR_INPUT:
        return "Waiting for approval"
    if status.mode in {AgentMode.WORKING, AgentMode.LONG_TASK_PROGRESS}:
        return "Thinking"
    if status.mode != AgentMode.TOOL_RUNNING:
        return None

    tool_name = status.tool_name
    if not isinstance(tool_name, str) or not tool_name.strip():
        return "Using tool"
    semantic_name = _semantic_tool_name(tool_name)
    if semantic_name in _READ_TOOLS:
        return "Reading files"
    if semantic_name in _EDIT_TOOLS:
        return "Editing files"
    if semantic_name in _SEARCH_TOOLS:
        return "Searching files"
    if semantic_name in _SHELL_TOOLS:
        return "Running command"
    if semantic_name in _THINKING_TOOLS:
        return "Thinking"
    return _bounded_unknown_tool_label(tool_name)


def _primary_candidates(rows: Sequence[ProjectedAgentRow]) -> tuple[_Candidate, ...]:
    canonical_rows: dict[str, ProjectedAgentRow] = {}
    for row in rows:
        existing = canonical_rows.get(row.agent_id)
        if existing is None or _preferred_duplicate_row(row, existing):
            canonical_rows[row.agent_id] = row

    mains: dict[str, ProjectedAgentRow] = {}
    workers: list[ProjectedAgentRow] = []
    for row in canonical_rows.values():
        if row.is_subagent:
            workers.append(row)
            continue
        mains[row.agent_id] = row

    workers_by_parent: dict[str, list[ProjectedAgentRow]] = defaultdict(list)
    orphan_workers: list[ProjectedAgentRow] = []
    for worker in workers:
        parent_id = worker.source_status.parent_agent_id
        if parent_id is not None and parent_id in mains:
            workers_by_parent[parent_id].append(worker)
        else:
            orphan_workers.append(worker)

    candidates = [
        _candidate_for_main(main, workers_by_parent.get(agent_id, ()))
        for agent_id, main in sorted(mains.items())
    ]
    if orphan_workers:
        candidates.append(_candidate_for_orphan_workers(orphan_workers))
    return tuple(candidates)


def _preferred_duplicate_row(
    candidate: ProjectedAgentRow,
    existing: ProjectedAgentRow,
) -> bool:
    if candidate.updated_at != existing.updated_at:
        return candidate.updated_at > existing.updated_at
    return (
        not candidate.actionable,
        _duplicate_lifecycle_priority(candidate.lifecycle_mode),
        candidate.provider,
        candidate.display_name,
    ) > (
        not existing.actionable,
        _duplicate_lifecycle_priority(existing.lifecycle_mode),
        existing.provider,
        existing.display_name,
    )


def _duplicate_lifecycle_priority(lifecycle_mode: LifecycleMode) -> int:
    return {
        LifecycleMode.ACTIVE: 5,
        LifecycleMode.IDLE: 4,
        LifecycleMode.UNKNOWN: 3,
        LifecycleMode.COMPLETED_RECENTLY: 2,
        LifecycleMode.FAILED_VISIBLE: 1,
        LifecycleMode.WAITING: 0,
    }[lifecycle_mode]


def _candidate_for_main(
    main: ProjectedAgentRow,
    workers: Sequence[ProjectedAgentRow],
) -> _Candidate:
    actionable_rows = [row for row in (main, *workers) if _row_is_actionable(row)]
    if actionable_rows:
        attention_row = min(
            actionable_rows,
            key=lambda row: (row.updated_at, row.agent_id),
        )
        lifecycle_mode = LifecycleMode.WAITING
        actionable = True
        navigation_agent_id = attention_row.agent_id
        updated_at = attention_row.updated_at
        activity_label = normalized_activity_label(attention_row.source_status)
    else:
        lifecycle_mode = main.lifecycle_mode
        actionable = False
        navigation_agent_id = main.agent_id
        updated_at = main.updated_at
        activity_label = normalized_activity_label(main.source_status)
    return _Candidate(
        agent_id=main.agent_id,
        provider=main.provider,
        display_name=main.display_name,
        lifecycle_mode=lifecycle_mode,
        activity_label=activity_label,
        actionable=actionable,
        navigation_agent_id=navigation_agent_id,
        worker_count=len(workers),
        updated_at=updated_at,
        completion_ids=(
            frozenset((main.agent_id,))
            if main.lifecycle_mode == LifecycleMode.COMPLETED_RECENTLY
            else frozenset()
        ),
    )


def _candidate_for_orphan_workers(workers: Sequence[ProjectedAgentRow]) -> _Candidate:
    ordered = sorted(workers, key=lambda row: (row.updated_at, row.agent_id))
    actionable_rows = [row for row in ordered if _row_is_actionable(row)]
    if actionable_rows:
        representative = actionable_rows[0]
        lifecycle_mode = LifecycleMode.WAITING
        actionable = True
        navigation_agent_id = representative.agent_id
    else:
        representative = min(
            ordered,
            key=lambda row: (_orphan_lifecycle_priority(row.lifecycle_mode), row.agent_id),
        )
        lifecycle_mode = representative.lifecycle_mode
        actionable = False
        navigation_agent_id = None
    providers = {row.provider for row in ordered}
    return _Candidate(
        agent_id=_ORPHAN_WORKERS_ID,
        provider=next(iter(providers)) if len(providers) == 1 else "sidepulse",
        display_name="Background agents",
        lifecycle_mode=lifecycle_mode,
        activity_label=normalized_activity_label(representative.source_status),
        actionable=actionable,
        navigation_agent_id=navigation_agent_id,
        worker_count=len(ordered),
        updated_at=representative.updated_at,
        completion_ids=frozenset(
            row.agent_id
            for row in ordered
            if row.lifecycle_mode == LifecycleMode.COMPLETED_RECENTLY
        ),
    )


def _row_is_actionable(row: ProjectedAgentRow) -> bool:
    return row.actionable and row.lifecycle_mode != LifecycleMode.FAILED_VISIBLE


def _orphan_lifecycle_priority(lifecycle_mode: LifecycleMode) -> int:
    return {
        LifecycleMode.ACTIVE: 0,
        LifecycleMode.FAILED_VISIBLE: 1,
        LifecycleMode.COMPLETED_RECENTLY: 2,
        LifecycleMode.IDLE: 3,
        LifecycleMode.UNKNOWN: 4,
        LifecycleMode.WAITING: 5,
    }[lifecycle_mode]


def _section_for(
    candidate: _Candidate,
    seen_completion_ids: AbstractSet[str],
) -> MailboxSectionKind:
    if candidate.actionable:
        return MailboxSectionKind.NEEDS_YOU
    if candidate.lifecycle_mode == LifecycleMode.ACTIVE:
        return MailboxSectionKind.IN_PROGRESS
    if candidate.lifecycle_mode == LifecycleMode.FAILED_VISIBLE:
        return MailboxSectionKind.READY_FOR_REVIEW
    if candidate.lifecycle_mode == LifecycleMode.COMPLETED_RECENTLY:
        if candidate.completion_ids and candidate.completion_ids <= seen_completion_ids:
            return MailboxSectionKind.RECENT
        return MailboxSectionKind.READY_FOR_REVIEW
    return MailboxSectionKind.RECENT


def _valid_previous_order(previous_order: Mapping[str, int] | None) -> dict[str, int]:
    if previous_order is None:
        return {}
    return {
        agent_id: order
        for agent_id, order in previous_order.items()
        if isinstance(agent_id, str)
        and ":agent:" not in agent_id
        and isinstance(order, int)
        and not isinstance(order, bool)
        and order >= 0
    }


def _bounded_retained_order(
    order_by_id: Mapping[str, int],
    *,
    prior_ids: AbstractSet[str],
    candidate_sections: Mapping[str, MailboxSectionKind],
    candidate_updated_at: Mapping[str, datetime],
    limit: int,
) -> tuple[tuple[str, int], ...]:
    if limit == 0:
        return ()

    def retention_key(item: tuple[str, int]) -> tuple[float, float, str]:
        agent_id, stable_order = item
        section = candidate_sections.get(agent_id)
        if section in {MailboxSectionKind.NEEDS_YOU, MailboxSectionKind.IN_PROGRESS}:
            return (0.0, float(stable_order), agent_id)
        if section == MailboxSectionKind.READY_FOR_REVIEW:
            return (1.0, float(stable_order), agent_id)
        if section == MailboxSectionKind.RECENT:
            timestamp = candidate_updated_at[agent_id].timestamp()
            return (2.0, -timestamp, agent_id)
        if agent_id in prior_ids:
            return (3.0, -float(stable_order), agent_id)
        return (4.0, math.inf, agent_id)

    retained = dict(sorted(order_by_id.items(), key=retention_key)[:limit])
    return tuple(sorted(retained.items(), key=lambda item: (item[1], item[0])))


def _semantic_tool_name(tool_name: str) -> str:
    last_namespace_component = tool_name.strip().rsplit("__", 1)[-1]
    return re.sub(r"[^a-z0-9]+", "_", last_namespace_component.lower()).strip("_")


def _bounded_unknown_tool_label(tool_name: str) -> str:
    stripped = tool_name.strip()
    lowered = stripped.lower()
    if (
        len(stripped) > 80
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", stripped)
        or any(fragment in lowered for fragment in _SENSITIVE_TOOL_FRAGMENTS)
    ):
        return "Using tool"
    safe_component = stripped.rsplit("__", 1)[-1]
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", safe_component)
    words = re.sub(r"[_.-]+", " ", words)
    display_name = " ".join(word.capitalize() for word in words.split())
    if not display_name:
        return "Using tool"
    available = _UNKNOWN_ACTIVITY_MAX_LENGTH - len("Using ")
    if len(display_name) > available:
        display_name = display_name[:available].rstrip()
    return f"Using {display_name}"
