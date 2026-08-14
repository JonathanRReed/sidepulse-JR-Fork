from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from enum import Enum

from .attention import LifecycleMode
from .mailbox import (
    AgentMailboxProjection,
    LegacyAgentMailboxProjection,
    MailboxRow,
    MailboxSection,
    MailboxSectionKind,
)
from .provider_facts import WorkKey, WorkLifecycle


class MailboxPreferenceMode(str, Enum):
    DEFAULT = "default"
    WATCHED = "watched"
    PINNED = "pinned"


@dataclass(frozen=True, slots=True)
class MailboxPreference:
    work_key: WorkKey
    mode: MailboxPreferenceMode = MailboxPreferenceMode.DEFAULT
    pin_order: int | None = None
    snoozed_at: float | None = None
    snoozed_until: float | None = None
    last_visited_at: float | None = None


@dataclass(frozen=True, slots=True)
class MailboxPreferenceProjection:
    projection: AgentMailboxProjection
    retained_preferences: tuple[MailboxPreference, ...]
    next_wake_epoch: float | None
    woke_work_keys: tuple[WorkKey, ...]


@dataclass(frozen=True, slots=True)
class LegacyMailboxPreference:
    agent_id: str
    mode: MailboxPreferenceMode = MailboxPreferenceMode.DEFAULT
    pin_order: int | None = None
    snoozed_at: float | None = None
    snoozed_until: float | None = None
    last_visited_at: float | None = None


@dataclass(frozen=True, slots=True)
class LegacyMailboxPreferenceProjection:
    projection: AgentMailboxProjection
    retained_preferences: tuple[LegacyMailboxPreference, ...]
    next_wake_epoch: float | None
    woke_agent_ids: tuple[str, ...]


_MAX_PREFERENCES = 100
_MAX_SNOOZE_SECONDS = 366.0 * 86_400.0
_MAX_PIN_ORDER = 2_147_483_647
_SAFE_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,255}")
_TERMINAL_MODES = {
    LifecycleMode.FAILED_VISIBLE,
    LifecycleMode.COMPLETED_RECENTLY,
}
_TERMINAL_LIFECYCLES = {WorkLifecycle.FAILED, WorkLifecycle.COMPLETED}


def apply_mailbox_preferences(
    projection: AgentMailboxProjection,
    preferences,
    *,
    now: float,
) -> MailboxPreferenceProjection | LegacyMailboxPreferenceProjection:
    """Apply preferences using exact WorkKey authority, with a legacy adapter."""
    try:
        values = tuple(preferences)
    except TypeError:
        values = ()
    if isinstance(projection, LegacyAgentMailboxProjection):
        return _apply_legacy_mailbox_preferences(projection, values, now=now)
    return _apply_work_key_preferences(projection, values, now=now)


def _apply_work_key_preferences(
    projection: AgentMailboxProjection,
    preferences: tuple[object, ...],
    *,
    now: float,
) -> MailboxPreferenceProjection:
    current_epoch = _finite_epoch(now)
    if current_epoch is None:
        current_epoch = 0.0

    rows, locations, mailbox_order = _work_rows(projection)
    known_order = _work_known_order(projection, rows, mailbox_order)
    canonical = _work_preferences(
        preferences,
        known_keys=frozenset(known_order),
        now=current_epoch,
    )

    hidden: set[WorkKey] = set()
    woke: dict[WorkKey, float] = {}
    next_wake: float | None = None
    for work_key, row in rows.items():
        preference = canonical.get(work_key)
        if preference is None or preference.snoozed_at is None:
            continue
        is_hidden, trigger = _work_snooze_result(row, preference, now=current_epoch)
        if is_hidden:
            hidden.add(work_key)
            deadline = preference.snoozed_until
            if deadline is not None and (next_wake is None or deadline < next_wake):
                next_wake = deadline
            continue
        if trigger is None:
            continue
        if preference.last_visited_at is None or preference.last_visited_at < trigger:
            woke[work_key] = trigger
        else:
            canonical[work_key] = replace(
                preference,
                snoozed_at=None,
                snoozed_until=None,
            )

    sections = _work_sections(
        projection,
        rows=rows,
        locations=locations,
        preferences=canonical,
        hidden=hidden,
        mailbox_order=mailbox_order,
    )
    retained = _work_retained_preferences(
        canonical,
        rows=rows,
        woke_keys=frozenset(woke),
        known_order=known_order,
        now=current_epoch,
    )
    woke_keys = tuple(sorted(woke, key=lambda key: mailbox_order[key]))
    projected = AgentMailboxProjection(
        sections=sections,
        active_count=_section_total(sections, MailboxSectionKind.NEEDS_YOU)
        + _section_total(sections, MailboxSectionKind.IN_PROGRESS),
        needs_you_count=_section_total(sections, MailboxSectionKind.NEEDS_YOU),
        ready_count=_section_total(sections, MailboxSectionKind.READY_FOR_REVIEW),
        retained_order=_work_retained_order(projection),
    )
    return MailboxPreferenceProjection(projected, retained, next_wake, woke_keys)


def _work_rows(
    projection: AgentMailboxProjection,
) -> tuple[
    dict[WorkKey, MailboxRow],
    dict[WorkKey, MailboxSectionKind],
    dict[WorkKey, int],
]:
    selected: dict[WorkKey, tuple[MailboxRow, MailboxSectionKind, int]] = {}
    position = 0
    for section in projection.sections:
        for row in section.rows:
            if type(row) is not MailboxRow or type(row.work_key) is not WorkKey:
                position += 1
                continue
            existing = selected.get(row.work_key)
            if existing is None or _prefer_work_row(row, existing[0]):
                selected[row.work_key] = (row, section.kind, position)
            position += 1
    return (
        {key: item[0] for key, item in selected.items()},
        {key: item[1] for key, item in selected.items()},
        {key: item[2] for key, item in selected.items()},
    )


def _prefer_work_row(candidate: MailboxRow, existing: MailboxRow) -> bool:
    if candidate.updated_at_epoch != existing.updated_at_epoch:
        return candidate.updated_at_epoch > existing.updated_at_epoch
    priority = {
        WorkLifecycle.ACTIVE: 5,
        WorkLifecycle.IDLE: 4,
        WorkLifecycle.UNKNOWN: 3,
        WorkLifecycle.COMPLETED: 2,
        WorkLifecycle.FAILED: 1,
        WorkLifecycle.WAITING: 0,
    }
    return (
        not candidate.actionable,
        priority[candidate.lifecycle],
        _work_key_sort_key(candidate.work_key),
    ) > (
        not existing.actionable,
        priority[existing.lifecycle],
        _work_key_sort_key(existing.work_key),
    )


def _work_known_order(
    projection: AgentMailboxProjection,
    rows: dict[WorkKey, MailboxRow],
    mailbox_order: dict[WorkKey, int],
) -> dict[WorkKey, int]:
    known = {
        key: index
        for index, key in enumerate(sorted(rows, key=lambda item: mailbox_order[item]))
    }
    retained = [
        (key, order)
        for key, order in projection.retained_order
        if type(key) is WorkKey
        and key not in known
        and _valid_order(order)
    ]
    for key, _order in sorted(
        retained,
        key=lambda item: (item[1], _work_key_sort_key(item[0])),
    ):
        if key not in known:
            known[key] = len(known)
    return known


def _work_preferences(
    preferences: tuple[object, ...],
    *,
    known_keys: frozenset[WorkKey],
    now: float,
) -> dict[WorkKey, MailboxPreference]:
    canonical: dict[WorkKey, MailboxPreference] = {}
    for raw in preferences:
        preference = _normalize_work_preference(raw, known_keys=known_keys, now=now)
        if preference is None:
            continue
        existing = canonical.get(preference.work_key)
        if existing is None or _preference_choice_key(
            preference,
            now=now,
        ) > _preference_choice_key(existing, now=now):
            canonical[preference.work_key] = preference
    return canonical


def _normalize_work_preference(
    raw: object,
    *,
    known_keys: frozenset[WorkKey],
    now: float,
) -> MailboxPreference | None:
    if not isinstance(raw, MailboxPreference) or type(raw.work_key) is not WorkKey:
        return None
    if raw.work_key not in known_keys:
        return None
    try:
        mode = MailboxPreferenceMode(raw.mode)
    except (TypeError, ValueError):
        mode = MailboxPreferenceMode.DEFAULT
    pin_order = raw.pin_order
    if mode is not MailboxPreferenceMode.PINNED or not _valid_pin_order(pin_order):
        pin_order = None
    snoozed_at = _finite_epoch(raw.snoozed_at)
    snoozed_until = _finite_epoch(raw.snoozed_until)
    if (
        snoozed_at is None
        or snoozed_until is None
        or snoozed_until <= snoozed_at
        or snoozed_until > now + _MAX_SNOOZE_SECONDS
    ):
        snoozed_at = None
        snoozed_until = None
    return MailboxPreference(
        raw.work_key,
        mode,
        pin_order,
        snoozed_at,
        snoozed_until,
        _finite_epoch(raw.last_visited_at),
    )


def _preference_choice_key(
    preference: MailboxPreference | LegacyMailboxPreference,
    *,
    now: float,
) -> tuple[float, bool, float, int, tuple[float, float, float]]:
    epochs = (
        epoch
        for epoch in (preference.snoozed_at, preference.last_visited_at)
        if epoch is not None
    )
    meaningful_epoch = max(epochs, default=-math.inf)
    future_snooze = (
        preference.snoozed_until is not None and preference.snoozed_until > now
    )
    pin_order = (
        float(preference.pin_order)
        if _valid_pin_order(preference.pin_order)
        else math.inf
    )
    mode_priority = {
        MailboxPreferenceMode.DEFAULT: 0,
        MailboxPreferenceMode.WATCHED: 1,
        MailboxPreferenceMode.PINNED: 2,
    }[preference.mode]
    deterministic_epochs = tuple(
        epoch if epoch is not None else -math.inf
        for epoch in (
            preference.snoozed_at,
            preference.snoozed_until,
            preference.last_visited_at,
        )
    )
    return (
        meaningful_epoch,
        not future_snooze,
        -pin_order,
        mode_priority,
        deterministic_epochs,
    )


def _work_snooze_result(
    row: MailboxRow,
    preference: MailboxPreference,
    *,
    now: float,
) -> tuple[bool, float | None]:
    snoozed_at = preference.snoozed_at
    snoozed_until = preference.snoozed_until
    if snoozed_at is None or snoozed_until is None:
        return False, None
    trigger: float | None = None
    if row.actionable:
        trigger = max(row.updated_at_epoch, snoozed_at)
    elif row.lifecycle in _TERMINAL_LIFECYCLES and row.updated_at_epoch > snoozed_at:
        trigger = row.updated_at_epoch
    if snoozed_until > now and trigger is None:
        return True, None
    if snoozed_until <= now:
        return False, min(snoozed_until, trigger or math.inf)
    return False, trigger


def _work_sections(
    projection: AgentMailboxProjection,
    *,
    rows: dict[WorkKey, MailboxRow],
    locations: dict[WorkKey, MailboxSectionKind],
    preferences: dict[WorkKey, MailboxPreference],
    hidden: set[WorkKey],
    mailbox_order: dict[WorkKey, int],
) -> tuple[MailboxSection, ...]:
    projected = []
    for section in projection.sections:
        visible = [
            row
            for key, row in rows.items()
            if locations[key] is section.kind and key not in hidden
        ]
        visible.sort(
            key=lambda row: _work_preference_order_key(
                row,
                preferences.get(row.work_key),
                mailbox_order=mailbox_order,
            )
        )
        projected.append(MailboxSection(section.kind, tuple(visible), section.overflow_count))
    return tuple(projected)


def _work_preference_order_key(
    row: MailboxRow,
    preference: MailboxPreference | None,
    *,
    mailbox_order: dict[WorkKey, int],
) -> tuple[int, float, int, tuple]:
    existing_order = mailbox_order[row.work_key]
    key = _work_key_sort_key(row.work_key)
    if preference is not None and preference.mode is MailboxPreferenceMode.PINNED:
        if _valid_pin_order(preference.pin_order):
            return (0, float(preference.pin_order), existing_order, key)
        return (1, 0.0, existing_order, key)
    if preference is not None and preference.mode is MailboxPreferenceMode.WATCHED:
        return (2, 0.0, existing_order, key)
    return (3, 0.0, existing_order, key)


def _work_retained_preferences(
    preferences: dict[WorkKey, MailboxPreference],
    *,
    rows: dict[WorkKey, MailboxRow],
    woke_keys: frozenset[WorkKey],
    known_order: dict[WorkKey, int],
    now: float,
) -> tuple[MailboxPreference, ...]:
    meaningful = [
        preference
        for preference in preferences.values()
        if _preference_is_meaningful(preference)
        or preference.work_key in woke_keys
    ]

    def retention_key(preference: MailboxPreference) -> tuple:
        row = rows.get(preference.work_key)
        recent = max(
            (
                epoch
                for epoch in (preference.last_visited_at, preference.snoozed_at)
                if epoch is not None
            ),
            default=-math.inf,
        )
        return (
            not (row is not None and row.actionable),
            preference.mode is MailboxPreferenceMode.DEFAULT,
            not (
                preference.snoozed_until is not None
                and preference.snoozed_until > now
            ),
            -recent,
            known_order[preference.work_key],
            _work_key_sort_key(preference.work_key),
        )

    return tuple(sorted(meaningful, key=retention_key)[:_MAX_PREFERENCES])


def _work_retained_order(
    projection: AgentMailboxProjection,
) -> tuple[tuple[WorkKey, int], ...]:
    retained: dict[WorkKey, int] = {}
    for key, order in projection.retained_order:
        if (
            type(key) is WorkKey
            and key not in retained
            and _valid_order(order)
        ):
            retained[key] = order
    return tuple(
        sorted(
            retained.items(),
            key=lambda item: (item[1], _work_key_sort_key(item[0])),
        )
    )


def _work_key_sort_key(key: WorkKey) -> tuple:
    source = key.source_key
    return (
        source.provider_id,
        source.adapter_id,
        source.source_instance_id,
        source.capability_id,
        key.work_id.value,
    )


def _apply_legacy_mailbox_preferences(
    projection: AgentMailboxProjection,
    preferences,
    *,
    now: float,
) -> LegacyMailboxPreferenceProjection:
    """Apply bounded stable ordering and fail-visible snooze state."""
    current_epoch = _finite_epoch(now)
    if current_epoch is None:
        current_epoch = 0.0

    canonical_rows, row_locations, mailbox_order = _canonical_rows(projection)
    known_order = _known_mailbox_order(projection, canonical_rows, mailbox_order)
    known_ids = frozenset(known_order)
    canonical_preferences = _canonical_preferences(
        preferences,
        known_ids=known_ids,
        now=current_epoch,
    )

    hidden_ids: set[str] = set()
    woke_triggers: dict[str, float] = {}
    next_wake_epoch: float | None = None
    for agent_id, row in canonical_rows.items():
        preference = canonical_preferences.get(agent_id)
        if preference is None or preference.snoozed_at is None:
            continue
        hidden, trigger = _snooze_result(row, preference, now=current_epoch)
        if hidden:
            hidden_ids.add(agent_id)
            deadline = preference.snoozed_until
            if deadline is not None and (
                next_wake_epoch is None or deadline < next_wake_epoch
            ):
                next_wake_epoch = deadline
            continue
        if trigger is None:
            continue
        last_visited = preference.last_visited_at
        if last_visited is None or last_visited < trigger:
            woke_triggers[agent_id] = trigger
        else:
            canonical_preferences[agent_id] = replace(
                preference,
                snoozed_at=None,
                snoozed_until=None,
            )

    sections = _project_sections(
        projection,
        canonical_rows=canonical_rows,
        row_locations=row_locations,
        preferences=canonical_preferences,
        hidden_ids=hidden_ids,
        mailbox_order=mailbox_order,
    )
    retained_preferences = _retained_preferences(
        canonical_preferences,
        canonical_rows=canonical_rows,
        woke_ids=frozenset(woke_triggers),
        known_order=known_order,
        now=current_epoch,
    )
    woke_agent_ids = tuple(
        sorted(woke_triggers, key=lambda agent_id: mailbox_order[agent_id])
    )
    projected = LegacyAgentMailboxProjection(
        sections=sections,
        active_count=_section_total(sections, MailboxSectionKind.NEEDS_YOU)
        + _section_total(sections, MailboxSectionKind.IN_PROGRESS),
        needs_you_count=_section_total(sections, MailboxSectionKind.NEEDS_YOU),
        ready_count=_section_total(sections, MailboxSectionKind.READY_FOR_REVIEW),
        retained_order=_canonical_retained_order(projection),
    )
    return LegacyMailboxPreferenceProjection(
        projection=projected,
        retained_preferences=retained_preferences,
        next_wake_epoch=next_wake_epoch,
        woke_agent_ids=woke_agent_ids,
    )


def _canonical_rows(
    projection: AgentMailboxProjection,
) -> tuple[
    dict[str, MailboxRow],
    dict[str, MailboxSectionKind],
    dict[str, int],
]:
    selected: dict[str, tuple[MailboxRow, MailboxSectionKind, int]] = {}
    position = 0
    for section in projection.sections:
        for row in section.rows:
            agent_id = _canonical_agent_id(row.agent_id)
            if agent_id is None:
                position += 1
                continue
            candidate = replace(row, agent_id=agent_id) if agent_id != row.agent_id else row
            existing = selected.get(agent_id)
            if existing is None or _prefer_row(candidate, existing[0]):
                selected[agent_id] = (candidate, section.kind, position)
            position += 1
    rows = {agent_id: item[0] for agent_id, item in selected.items()}
    locations = {agent_id: item[1] for agent_id, item in selected.items()}
    order = {agent_id: item[2] for agent_id, item in selected.items()}
    return rows, locations, order


def _prefer_row(candidate: MailboxRow, existing: MailboxRow) -> bool:
    candidate_epoch = _row_epoch(candidate)
    existing_epoch = _row_epoch(existing)
    if candidate_epoch != existing_epoch:
        return candidate_epoch > existing_epoch
    return _row_tie_key(candidate) > _row_tie_key(existing)


def _row_tie_key(row: MailboxRow) -> tuple[bool, int, str]:
    lifecycle_priority = {
        LifecycleMode.ACTIVE: 5,
        LifecycleMode.IDLE: 4,
        LifecycleMode.UNKNOWN: 3,
        LifecycleMode.COMPLETED_RECENTLY: 2,
        LifecycleMode.FAILED_VISIBLE: 1,
        LifecycleMode.WAITING: 0,
    }[row.lifecycle_mode]
    return (not row.actionable, lifecycle_priority, row.agent_id)


def _known_mailbox_order(
    projection: AgentMailboxProjection,
    canonical_rows: dict[str, MailboxRow],
    mailbox_order: dict[str, int],
) -> dict[str, int]:
    known = {
        agent_id: index
        for index, agent_id in enumerate(
            sorted(canonical_rows, key=lambda item: mailbox_order[item])
        )
    }
    next_order = len(known)
    retained_candidates: list[tuple[str, int]] = []
    for agent_id, order in projection.retained_order:
        canonical_id = _canonical_agent_id(agent_id)
        if canonical_id is None or canonical_id in known:
            continue
        if not _valid_order(order):
            continue
        retained_candidates.append((canonical_id, order))
    for agent_id, _order in sorted(retained_candidates, key=lambda item: (item[1], item[0])):
        if agent_id in known:
            continue
        known[agent_id] = next_order
        next_order += 1
    return known


def _canonical_preferences(
    preferences,
    *,
    known_ids: frozenset[str],
    now: float,
) -> dict[str, LegacyMailboxPreference]:
    canonical: dict[str, LegacyMailboxPreference] = {}
    try:
        iterator = iter(preferences)
    except TypeError:
        return canonical
    for raw_preference in iterator:
        preference = _normalize_preference(raw_preference, known_ids=known_ids, now=now)
        if preference is None:
            continue
        existing = canonical.get(preference.agent_id)
        if existing is None or _preference_choice_key(
            preference, now=now
        ) > _preference_choice_key(existing, now=now):
            canonical[preference.agent_id] = preference
    return canonical


def _normalize_preference(
    raw_preference,
    *,
    known_ids: frozenset[str],
    now: float,
) -> LegacyMailboxPreference | None:
    if not isinstance(raw_preference, LegacyMailboxPreference):
        return None
    agent_id = _canonical_agent_id(raw_preference.agent_id)
    if agent_id is None or agent_id not in known_ids:
        return None
    try:
        mode = MailboxPreferenceMode(raw_preference.mode)
    except (TypeError, ValueError):
        mode = MailboxPreferenceMode.DEFAULT
    pin_order = raw_preference.pin_order
    if mode != MailboxPreferenceMode.PINNED or not _valid_pin_order(pin_order):
        pin_order = None
    snoozed_at = _finite_epoch(raw_preference.snoozed_at)
    snoozed_until = _finite_epoch(raw_preference.snoozed_until)
    if (
        snoozed_at is None
        or snoozed_until is None
        or snoozed_until <= snoozed_at
        or snoozed_until > now + _MAX_SNOOZE_SECONDS
    ):
        snoozed_at = None
        snoozed_until = None
    return LegacyMailboxPreference(
        agent_id=agent_id,
        mode=mode,
        pin_order=pin_order,
        snoozed_at=snoozed_at,
        snoozed_until=snoozed_until,
        last_visited_at=_finite_epoch(raw_preference.last_visited_at),
    )


def _preference_choice_key(
    preference: LegacyMailboxPreference,
    *,
    now: float,
) -> tuple[float, bool, float, int, tuple[float, float, float]]:
    epochs = (
        epoch
        for epoch in (preference.snoozed_at, preference.last_visited_at)
        if epoch is not None
    )
    meaningful_epoch = max(epochs, default=-math.inf)
    future_snooze = (
        preference.snoozed_until is not None and preference.snoozed_until > now
    )
    pin_order = (
        float(preference.pin_order)
        if _valid_pin_order(preference.pin_order)
        else math.inf
    )
    mode_priority = {
        MailboxPreferenceMode.DEFAULT: 0,
        MailboxPreferenceMode.WATCHED: 1,
        MailboxPreferenceMode.PINNED: 2,
    }[preference.mode]
    deterministic_epochs = tuple(
        epoch if epoch is not None else -math.inf
        for epoch in (
            preference.snoozed_at,
            preference.snoozed_until,
            preference.last_visited_at,
        )
    )
    return (
        meaningful_epoch,
        not future_snooze,
        -pin_order,
        mode_priority,
        deterministic_epochs,
    )


def _snooze_result(
    row: MailboxRow,
    preference: LegacyMailboxPreference,
    *,
    now: float,
) -> tuple[bool, float | None]:
    snoozed_at = preference.snoozed_at
    snoozed_until = preference.snoozed_until
    if snoozed_at is None or snoozed_until is None:
        return False, None
    event_trigger: float | None = None
    row_epoch = _row_epoch(row)
    if row.actionable:
        event_trigger = max(row_epoch, snoozed_at)
    elif row.lifecycle_mode in _TERMINAL_MODES and row_epoch > snoozed_at:
        event_trigger = row_epoch
    if snoozed_until > now and event_trigger is None:
        return True, None
    if snoozed_until <= now:
        return False, min(snoozed_until, event_trigger or math.inf)
    return False, event_trigger


def _project_sections(
    projection: AgentMailboxProjection,
    *,
    canonical_rows: dict[str, MailboxRow],
    row_locations: dict[str, MailboxSectionKind],
    preferences: dict[str, LegacyMailboxPreference],
    hidden_ids: set[str],
    mailbox_order: dict[str, int],
) -> tuple[MailboxSection, ...]:
    projected_sections: list[MailboxSection] = []
    for section in projection.sections:
        visible_rows = [
            row
            for agent_id, row in canonical_rows.items()
            if row_locations[agent_id] == section.kind and agent_id not in hidden_ids
        ]
        visible_rows.sort(
            key=lambda row: _row_preference_order_key(
                row,
                preferences.get(row.agent_id),
                mailbox_order=mailbox_order,
            )
        )
        projected_sections.append(
            MailboxSection(
                kind=section.kind,
                rows=tuple(visible_rows),
                overflow_count=max(0, section.overflow_count),
            )
        )
    return tuple(projected_sections)


def _row_preference_order_key(
    row: MailboxRow,
    preference: LegacyMailboxPreference | None,
    *,
    mailbox_order: dict[str, int],
) -> tuple[int, float, int, str]:
    existing_order = mailbox_order[row.agent_id]
    if preference is not None and preference.mode == MailboxPreferenceMode.PINNED:
        if _valid_pin_order(preference.pin_order):
            return (0, float(preference.pin_order), existing_order, row.agent_id)
        return (1, 0.0, existing_order, row.agent_id)
    if preference is not None and preference.mode == MailboxPreferenceMode.WATCHED:
        return (2, 0.0, existing_order, row.agent_id)
    return (3, 0.0, existing_order, row.agent_id)


def _retained_preferences(
    preferences: dict[str, LegacyMailboxPreference],
    *,
    canonical_rows: dict[str, MailboxRow],
    woke_ids: frozenset[str],
    known_order: dict[str, int],
    now: float,
) -> tuple[LegacyMailboxPreference, ...]:
    meaningful = [
        preference
        for preference in preferences.values()
        if _preference_is_meaningful(preference) or preference.agent_id in woke_ids
    ]

    def retention_key(preference: LegacyMailboxPreference) -> tuple:
        row = canonical_rows.get(preference.agent_id)
        actionable = row is not None and row.actionable
        recent_epoch = max(
            (
                epoch
                for epoch in (preference.last_visited_at, preference.snoozed_at)
                if epoch is not None
            ),
            default=-math.inf,
        )
        return (
            not actionable,
            preference.mode == MailboxPreferenceMode.DEFAULT,
            not (
                preference.snoozed_until is not None
                and preference.snoozed_until > now
            ),
            -recent_epoch,
            known_order[preference.agent_id],
            preference.agent_id,
        )

    return tuple(sorted(meaningful, key=retention_key)[:_MAX_PREFERENCES])


def _preference_is_meaningful(preference: LegacyMailboxPreference) -> bool:
    return (
        preference.mode != MailboxPreferenceMode.DEFAULT
        or preference.pin_order is not None
        or preference.snoozed_at is not None
        or preference.snoozed_until is not None
        or preference.last_visited_at is not None
    )


def _canonical_retained_order(
    projection: AgentMailboxProjection,
) -> tuple[tuple[str, int], ...]:
    retained: dict[str, int] = {}
    for agent_id, order in projection.retained_order:
        canonical_id = _canonical_agent_id(agent_id)
        if canonical_id is None or canonical_id in retained or not _valid_order(order):
            continue
        retained[canonical_id] = order
    return tuple(sorted(retained.items(), key=lambda item: (item[1], item[0])))


def _section_total(
    sections: tuple[MailboxSection, ...],
    kind: MailboxSectionKind,
) -> int:
    section = next((item for item in sections if item.kind == kind), None)
    if section is None:
        return 0
    return len(section.rows) + section.overflow_count


def _row_epoch(row: MailboxRow) -> float:
    try:
        epoch = row.updated_at.timestamp()
    except (AttributeError, OSError, OverflowError, ValueError):
        return -math.inf
    return epoch if math.isfinite(epoch) else -math.inf


def _canonical_agent_id(agent_id) -> str | None:
    if not isinstance(agent_id, str):
        return None
    canonical = agent_id.strip()
    if not _SAFE_AGENT_ID.fullmatch(canonical):
        return None
    return canonical


def _finite_epoch(epoch) -> float | None:
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return None
    value = float(epoch)
    return value if math.isfinite(value) else None


def _valid_pin_order(order) -> bool:
    return (
        isinstance(order, int)
        and not isinstance(order, bool)
        and 0 <= order <= _MAX_PIN_ORDER
    )


def _valid_order(order) -> bool:
    return isinstance(order, int) and not isinstance(order, bool) and order >= 0
