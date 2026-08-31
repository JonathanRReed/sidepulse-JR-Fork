"""Pure completion visibility and acknowledgement decisions."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from datetime import datetime, timezone

from .clear_agents import (
    CompletionPresentationKey,
    completion_presentation_key,
)
from .freshness import is_recent
from .models import AgentMode, AgentStatus
from .provider_facts import WorkKey


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _current_rows_by_id(
    statuses: Iterable[AgentStatus],
) -> tuple[dict[str, AgentStatus], tuple[str, ...]]:
    selected: dict[str, AgentStatus] = {}
    ordered_ids: list[str] = []
    for status in statuses:
        existing = selected.get(status.agent_id)
        if existing is None:
            ordered_ids.append(status.agent_id)
        if existing is None or (
            _as_utc(status.updated_at),
            status.mode != AgentMode.COMPLETED,
        ) > (
            _as_utc(existing.updated_at),
            existing.mode != AgentMode.COMPLETED,
        ):
            selected[status.agent_id] = status
    return selected, tuple(ordered_ids)


def _source_bound_identity(status: AgentStatus) -> tuple[object | None, str]:
    """Keep source instances distinct while retaining a safe unkeyed fallback."""

    work_key = status.work_key
    source_key = (
        work_key.source_key
        if type(work_key) is WorkKey
        and work_key.source_key.provider_id == status.provider
        else None
    )
    return source_key, status.agent_id


def _current_rows_by_source_identity(
    statuses: Iterable[AgentStatus],
) -> tuple[dict[tuple[object | None, str], AgentStatus], tuple[tuple[object | None, str], ...]]:
    selected: dict[tuple[object | None, str], AgentStatus] = {}
    ordered_keys: list[tuple[object | None, str]] = []
    for status in statuses:
        identity = _source_bound_identity(status)
        existing = selected.get(identity)
        if existing is None:
            ordered_keys.append(identity)
        if existing is None or (
            _as_utc(status.updated_at),
            status.mode != AgentMode.COMPLETED,
        ) > (
            _as_utc(existing.updated_at),
            existing.mode != AgentMode.COMPLETED,
        ):
            selected[identity] = status
    return selected, tuple(ordered_keys)


def _completion_is_eligible(
    status: AgentStatus,
    *,
    collected_at: datetime,
    within_seconds: float,
    include_subagents: bool,
) -> bool:
    return (
        (include_subagents or not status.is_subagent)
        and status.mode == AgentMode.COMPLETED
        and status.event_name != "SessionEnd"
        and is_recent(collected_at, status.updated_at, within_seconds)
    )


def select_clearable_completions(
    current_statuses: Iterable[AgentStatus],
    stale_statuses: Iterable[AgentStatus],
    *,
    collected_at: datetime,
    within_seconds: float,
    include_subagents: bool = False,
) -> tuple[AgentStatus, ...]:
    """Select one recent clearable completion per identity.

    Any current row shadows every stale row with the same identity, even when
    the current row is not itself eligible. This prevents an old completion
    from reappearing after that session has resumed.
    """

    current_by_identity, _ = _current_rows_by_source_identity(current_statuses)
    eligible_by_identity = {
        identity: status
        for identity, status in current_by_identity.items()
        if _completion_is_eligible(
            status,
            collected_at=collected_at,
            within_seconds=within_seconds,
            include_subagents=include_subagents,
        )
    }
    stale_by_identity, _ = _current_rows_by_source_identity(stale_statuses)
    for identity, status in stale_by_identity.items():
        if identity in current_by_identity:
            continue
        if _completion_is_eligible(
            status,
            collected_at=collected_at,
            within_seconds=within_seconds,
            include_subagents=include_subagents,
        ):
            eligible_by_identity[identity] = status
    return tuple(
        sorted(
            eligible_by_identity.values(),
            key=lambda status: (
                -_as_utc(status.updated_at).timestamp(),
                status.agent_id,
            ),
        )
    )


def select_unseen_completions(
    current_statuses: Iterable[AgentStatus],
    stale_statuses: Iterable[AgentStatus],
    *,
    collected_at: datetime,
    within_seconds: float,
    menu_last_opened_at: datetime | None,
    acknowledged_keys: Collection[CompletionPresentationKey],
    attended_prompt_monotonic: Mapping[str, float],
    now_monotonic: float,
    attended_quiet_seconds: float,
) -> tuple[AgentStatus, ...]:
    """Select main-session completions not acknowledged by any surface."""

    current_by_identity, current_order = _current_rows_by_source_identity(
        current_statuses
    )
    stale_by_identity, stale_order = _current_rows_by_source_identity(stale_statuses)
    candidates = (
        *(current_by_identity[identity] for identity in current_order),
        *(
            stale_by_identity[identity]
            for identity in stale_order
            if identity not in current_by_identity
        ),
    )
    opened_at = (
        _as_utc(menu_last_opened_at)
        if menu_last_opened_at is not None
        else None
    )
    unseen: list[AgentStatus] = []
    for status in candidates:
        if not _completion_is_eligible(
            status,
            collected_at=collected_at,
            within_seconds=within_seconds,
            include_subagents=False,
        ):
            continue
        key = completion_presentation_key(status)
        if key is not None and key in acknowledged_keys:
            continue
        if opened_at is not None and _as_utc(status.updated_at) <= opened_at:
            continue
        prompted_at = attended_prompt_monotonic.get(status.agent_id)
        if (
            prompted_at is not None
            and float(now_monotonic) - float(prompted_at)
            <= float(attended_quiet_seconds)
        ):
            continue
        unseen.append(status)
    return tuple(unseen)


def plan_seen_completion_ids(
    visible_statuses: Iterable[AgentStatus],
    previously_seen_ids: Collection[str],
    *,
    limit: int = 100,
) -> tuple[str, ...]:
    """Plan bounded completion acknowledgement for one menu visit."""

    selected_by_id, _ = _current_rows_by_id(visible_statuses)
    visible_ids = [
        status.agent_id
        for status in sorted(
            (
                status
                for status in selected_by_id.values()
                if status.mode == AgentMode.COMPLETED
                and status.event_name != "SessionEnd"
            ),
            key=lambda status: (
                -_as_utc(status.updated_at).timestamp(),
                status.agent_id,
            ),
        )
    ]
    retained_ids = sorted(set(previously_seen_ids).difference(visible_ids))
    return tuple((*visible_ids, *retained_ids)[: max(0, int(limit))])
__all__ = [
    "plan_seen_completion_ids",
    "select_clearable_completions",
    "select_unseen_completions",
]
