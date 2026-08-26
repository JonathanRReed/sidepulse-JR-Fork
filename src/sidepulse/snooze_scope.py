"""One snooze rule for every surface beyond the dropdown.

Snoozing a session from the mailbox used to filter ONLY the dropdown:
the same session kept claiming the LEDs and kept delivering completion
banners. Decision (2026-08-26): a snoozed session is silent everywhere
-- lights and notification delivery included -- EXCEPT a genuine ask
(WAITING_FOR_INPUT with PermissionRequest/Notification, the raised
hand that already wakes the dropdown), which breaks through every
surface. The Agent Browser window deliberately keeps showing snoozed
sessions; the mailbox keeps its own richer wake semantics in
mailbox_preferences. This module owns only the lights/notifications
scope, so the rule lives once for both consumers.
"""

from __future__ import annotations

import math

from .mailbox_preferences import LegacyMailboxPreference, MailboxPreference
from .provider_facts import WorkKey


def _active_snooze(preference, now: float) -> bool:
    snoozed_at = preference.snoozed_at
    snoozed_until = preference.snoozed_until
    for value in (snoozed_at, snoozed_until):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(float(value)):
            return False
    return float(snoozed_at) < float(snoozed_until) and float(snoozed_until) > now


def _snoozed_scopes(
    preferences,
    now: float,
) -> tuple[frozenset[WorkKey], frozenset[tuple[str, str]], frozenset[str]]:
    """Currently snoozed (work keys, (provider, family id) pairs, legacy
    agent ids). The family pair covers live-path statuses whose own work
    key is a child of (or older than) the snoozed family key: a status's
    session_id is its family's work id."""
    try:
        values = tuple(preferences)
    except TypeError:
        return frozenset(), frozenset(), frozenset()
    work_keys: set[WorkKey] = set()
    family_ids: set[tuple[str, str]] = set()
    agent_ids: set[str] = set()
    for preference in values:
        if isinstance(preference, MailboxPreference):
            if type(preference.work_key) is WorkKey and _active_snooze(preference, now):
                work_keys.add(preference.work_key)
                family_ids.add(
                    (
                        preference.work_key.source_key.provider_id,
                        preference.work_key.work_id.value,
                    )
                )
        elif isinstance(preference, LegacyMailboxPreference):
            if isinstance(preference.agent_id, str) and _active_snooze(preference, now):
                agent_ids.add(preference.agent_id)
    return frozenset(work_keys), frozenset(family_ids), frozenset(agent_ids)


def _status_covered(status, work_keys, family_ids, agent_ids) -> bool:
    if getattr(status, "is_hard_ask", False):
        # The raised-hand override: a live ask outranks any snooze on
        # every surface, exactly as it already wakes the dropdown.
        return False
    work_key = getattr(status, "work_key", None)
    if type(work_key) is WorkKey and work_key in work_keys:
        return True
    provider = getattr(status, "provider", None)
    session_id = getattr(status, "session_id", None)
    if (
        isinstance(provider, str)
        and isinstance(session_id, str)
        and (provider, session_id) in family_ids
    ):
        return True
    return getattr(status, "agent_id", None) in agent_ids


def status_snoozed(status, preferences, *, now: float) -> bool:
    """Whether this status is silenced for lights and notifications."""
    scopes = _snoozed_scopes(preferences, float(now))
    if not any(scopes):
        return False
    return _status_covered(status, *scopes)


def filter_snoozed_statuses(statuses, preferences, *, now: float):
    """Statuses minus currently snoozed sessions; asks break through.

    Returns the ORIGINAL tuple object when nothing is snoozed, so
    callers can cheaply detect "no filtering happened" by identity.
    """
    values = statuses if type(statuses) is tuple else tuple(statuses)
    scopes = _snoozed_scopes(preferences, float(now))
    if not any(scopes):
        return values
    kept = tuple(
        status for status in values if not _status_covered(status, *scopes)
    )
    return values if len(kept) == len(values) else kept


__all__ = ["filter_snoozed_statuses", "status_snoozed"]
