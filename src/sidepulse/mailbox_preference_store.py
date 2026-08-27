"""Private persistence and calendar presets for mailbox preferences."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from enum import Enum
from itertools import islice
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .mailbox_preferences import (
    LegacyMailboxPreference,
    MailboxPreference,
    MailboxPreferenceMode,
)
from .private_io import atomic_private_write, read_private_bytes, read_private_text
from .provider_facts import WorkKey, work_key_from_payload, work_key_to_payload

_STORE_VERSION = 1
_MAX_PREFERENCES = 100
_MAX_STORE_BYTES = 1_048_576
_MAX_PIN_ORDER = 2_147_483_647
_SAFE_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,255}")
_DOCUMENT_KEYS = frozenset({"preferences", "version"})
_PREFERENCE_KEYS = frozenset(
    {
        "agent_id",
        "last_visited_at",
        "mode",
        "pin_order",
        "snoozed_at",
        "snoozed_until",
    }
)
_V2_PREFERENCE_KEYS = (_PREFERENCE_KEYS - {"agent_id"}) | {"work_key"}


class MailboxSnoozePreset(str, Enum):
    ONE_HOUR = "one_hour"
    THREE_HOURS = "three_hours"
    THIS_EVENING = "this_evening"
    TOMORROW_MORNING = "tomorrow_morning"
    NEXT_MONDAY_MORNING = "next_monday_morning"


class _InvalidPreference(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MailboxPreferenceDocument:
    version: int
    preferences: tuple[MailboxPreference, ...]
    legacy_preferences: tuple[LegacyMailboxPreference, ...]
    degraded: bool


def load_mailbox_preference_document(path: Path) -> MailboxPreferenceDocument:
    """Decode only strict v1 legacy or source-scoped v2 documents."""
    degraded = MailboxPreferenceDocument(0, (), (), True)
    try:
        raw = read_private_text(Path(path), max_bytes=_MAX_STORE_BYTES)
        document = json.loads(
            raw,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, RecursionError, TypeError, UnicodeError, ValueError):
        return degraded
    if type(document) is not dict or frozenset(document) != _DOCUMENT_KEYS:
        return degraded
    version = document["version"]
    entries = document["preferences"]
    if not (
        type(version) is int
        and version in {1, 2}
        and type(entries) is list
        and len(entries) <= _MAX_PREFERENCES
    ):
        return degraded

    try:
        if version == 1:
            legacy = tuple(_preference_from_payload(entry) for entry in entries)
            if len({item.agent_id for item in legacy}) != len(legacy):
                raise _InvalidPreference
            return MailboxPreferenceDocument(1, (), legacy, False)
        preferences = tuple(_v2_preference_from_payload(entry) for entry in entries)
        if len({item.work_key for item in preferences}) != len(preferences):
            raise _InvalidPreference
        return MailboxPreferenceDocument(2, preferences, (), False)
    except _InvalidPreference:
        return degraded


def save_mailbox_preferences_v2(
    path: Path,
    preferences: Iterable[MailboxPreference],
) -> None:
    """Replace with strict v2, reread it, and restore prior bytes on mismatch."""
    try:
        iterator = iter(preferences)
    except TypeError as error:
        raise ValueError("invalid mailbox preferences") from error
    payloads: list[dict[str, object]] = []
    canonical: list[MailboxPreference] = []
    seen: set[WorkKey] = set()
    try:
        for preference in islice(iterator, _MAX_PREFERENCES):
            payload = _v2_payload_from_preference(preference)
            work_key = preference.work_key
            if work_key in seen:
                raise _InvalidPreference
            seen.add(work_key)
            canonical.append(
                MailboxPreference(
                    work_key,
                    preference.mode,
                    payload["pin_order"],  # type: ignore[arg-type]
                    payload["snoozed_at"],  # type: ignore[arg-type]
                    payload["snoozed_until"],  # type: ignore[arg-type]
                    payload["last_visited_at"],  # type: ignore[arg-type]
                )
            )
            payloads.append(payload)
    except _InvalidPreference as error:
        raise ValueError("invalid mailbox preference") from error

    serialized = json.dumps(
        {"version": 2, "preferences": payloads},
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    encoded = f"{serialized}\n".encode()
    if len(encoded) > _MAX_STORE_BYTES:
        raise ValueError("mailbox preference store exceeds maximum size")

    target = Path(path)
    try:
        previous = read_private_bytes(target, max_bytes=_MAX_STORE_BYTES)
    except FileNotFoundError:
        previous = None
    atomic_private_write(target, encoded)
    verified = load_mailbox_preference_document(target)
    expected = tuple(canonical)
    if not (
        verified.version == 2
        and not verified.degraded
        and verified.preferences == expected
        and verified.legacy_preferences == ()
    ):
        if previous is not None:
            atomic_private_write(target, previous)
        raise OSError("mailbox preference store verification failed")


def _strict_json_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _InvalidPreference
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise _InvalidPreference


def resolve_mailbox_snooze_preset(
    preset: MailboxSnoozePreset | str,
    *,
    now: float,
    local_timezone: tzinfo | None = None,
) -> float | None:
    """Resolve a product preset to an epoch without persisting timezone state."""
    current_epoch = _valid_epoch(now)
    if current_epoch is None:
        return None
    try:
        selected = MailboxSnoozePreset(preset)
    except (TypeError, ValueError):
        return None
    if selected == MailboxSnoozePreset.ONE_HOUR:
        return _finite_sum(current_epoch, 3_600.0)
    if selected == MailboxSnoozePreset.THREE_HOURS:
        return _finite_sum(current_epoch, 10_800.0)

    zone = (
        local_timezone
        if local_timezone is not None
        else _system_local_timezone(current_epoch)
    )
    try:
        current_local = datetime.fromtimestamp(current_epoch, zone)
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    if selected == MailboxSnoozePreset.THIS_EVENING:
        target_date = current_local.date()
        target_time = time(18, 0)
    elif selected == MailboxSnoozePreset.TOMORROW_MORNING:
        try:
            target_date = current_local.date() + timedelta(days=1)
        except OverflowError:
            return None
        target_time = time(9, 0)
    else:
        try:
            target_date = current_local.date() + timedelta(
                days=7 - current_local.weekday()
            )
        except OverflowError:
            return None
        target_time = time(9, 0)

    resolved = _resolve_local_epoch(target_date, target_time, zone)
    if resolved is None or resolved <= current_epoch:
        return None
    return resolved


def _preference_from_payload(payload: object) -> LegacyMailboxPreference:
    if type(payload) is not dict or frozenset(payload) != _PREFERENCE_KEYS:
        raise _InvalidPreference
    agent_id = _valid_agent_id(payload.get("agent_id"))
    if agent_id is None:
        raise _InvalidPreference
    try:
        mode = MailboxPreferenceMode(payload.get("mode"))
    except (TypeError, ValueError) as error:
        raise _InvalidPreference from error
    raw_pin_order = payload.get("pin_order")
    pin_order = _valid_pin_order(raw_pin_order)
    if raw_pin_order is not None and pin_order is None:
        raise _InvalidPreference
    if mode != MailboxPreferenceMode.PINNED and pin_order is not None:
        raise _InvalidPreference
    raw_snoozed_at = payload.get("snoozed_at")
    raw_snoozed_until = payload.get("snoozed_until")
    snoozed_at = _valid_epoch(raw_snoozed_at)
    snoozed_until = _valid_epoch(raw_snoozed_until)
    if raw_snoozed_at is not None and snoozed_at is None:
        raise _InvalidPreference
    if raw_snoozed_until is not None and snoozed_until is None:
        raise _InvalidPreference
    if (snoozed_at is None) != (snoozed_until is None):
        raise _InvalidPreference
    if snoozed_at is not None and snoozed_until <= snoozed_at:
        raise _InvalidPreference
    raw_last_visited = payload.get("last_visited_at")
    last_visited_at = _valid_epoch(raw_last_visited)
    if raw_last_visited is not None and last_visited_at is None:
        raise _InvalidPreference
    return LegacyMailboxPreference(
        agent_id=agent_id,
        mode=mode,
        pin_order=pin_order,
        snoozed_at=snoozed_at,
        snoozed_until=snoozed_until,
        last_visited_at=last_visited_at,
    )


def _v2_payload_from_preference(preference: object) -> dict[str, object]:
    if not isinstance(preference, MailboxPreference):
        raise _InvalidPreference
    if type(preference.work_key) is not WorkKey:
        raise _InvalidPreference
    if type(preference.mode) is not MailboxPreferenceMode:
        raise _InvalidPreference
    pin_order = _valid_pin_order(preference.pin_order)
    if preference.pin_order is not None and pin_order is None:
        raise _InvalidPreference
    if preference.mode is not MailboxPreferenceMode.PINNED and pin_order is not None:
        raise _InvalidPreference
    snoozed_at = _valid_epoch(preference.snoozed_at)
    snoozed_until = _valid_epoch(preference.snoozed_until)
    if preference.snoozed_at is not None and snoozed_at is None:
        raise _InvalidPreference
    if preference.snoozed_until is not None and snoozed_until is None:
        raise _InvalidPreference
    if (snoozed_at is None) != (snoozed_until is None):
        raise _InvalidPreference
    if snoozed_at is not None and snoozed_until <= snoozed_at:
        raise _InvalidPreference
    last_visited_at = _valid_epoch(preference.last_visited_at)
    if preference.last_visited_at is not None and last_visited_at is None:
        raise _InvalidPreference
    return {
        "work_key": work_key_to_payload(preference.work_key),
        "mode": preference.mode.value,
        "pin_order": pin_order,
        "snoozed_at": snoozed_at,
        "snoozed_until": snoozed_until,
        "last_visited_at": last_visited_at,
    }


def _v2_preference_from_payload(payload: object) -> MailboxPreference:
    if type(payload) is not dict or frozenset(payload) != _V2_PREFERENCE_KEYS:
        raise _InvalidPreference
    work_key = work_key_from_payload(payload["work_key"])
    if work_key is None:
        raise _InvalidPreference
    try:
        mode = MailboxPreferenceMode(payload["mode"])
    except (TypeError, ValueError) as error:
        raise _InvalidPreference from error
    raw_pin_order = payload["pin_order"]
    pin_order = _valid_pin_order(raw_pin_order)
    if raw_pin_order is not None and pin_order is None:
        raise _InvalidPreference
    if mode is not MailboxPreferenceMode.PINNED and pin_order is not None:
        raise _InvalidPreference
    raw_snoozed_at = payload["snoozed_at"]
    raw_snoozed_until = payload["snoozed_until"]
    snoozed_at = _valid_epoch(raw_snoozed_at)
    snoozed_until = _valid_epoch(raw_snoozed_until)
    if raw_snoozed_at is not None and snoozed_at is None:
        raise _InvalidPreference
    if raw_snoozed_until is not None and snoozed_until is None:
        raise _InvalidPreference
    if (snoozed_at is None) != (snoozed_until is None):
        raise _InvalidPreference
    if snoozed_at is not None and snoozed_until <= snoozed_at:
        raise _InvalidPreference
    raw_last_visited_at = payload["last_visited_at"]
    last_visited_at = _valid_epoch(raw_last_visited_at)
    if raw_last_visited_at is not None and last_visited_at is None:
        raise _InvalidPreference
    return MailboxPreference(
        work_key,
        mode,
        pin_order,
        snoozed_at,
        snoozed_until,
        last_visited_at,
    )


def _valid_agent_id(value: object) -> str | None:
    if not isinstance(value, str) or not _SAFE_AGENT_ID.fullmatch(value):
        return None
    return value


def _valid_pin_order(value: object) -> int | None:
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > _MAX_PIN_ORDER
    ):
        return None
    return value


def _valid_epoch(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        epoch = float(value)
    except OverflowError:
        return None
    return epoch if math.isfinite(epoch) else None


def _finite_sum(epoch: float, seconds: float) -> float | None:
    result = epoch + seconds
    return result if math.isfinite(result) and result > epoch else None


def _resolve_local_epoch(
    target_date: date,
    target_time: time,
    zone: tzinfo,
) -> float | None:
    target = datetime.combine(target_date, target_time)
    valid = _valid_local_epochs(target, zone)
    if valid:
        return min(valid)

    probe = target
    for _minute in range(3 * 24 * 60):
        probe += timedelta(minutes=1)
        valid = _valid_local_epochs(probe, zone)
        if not valid:
            continue
        previous_minute = probe - timedelta(minutes=1)
        for second in range(60):
            second_probe = previous_minute + timedelta(seconds=second)
            exact = _valid_local_epochs(second_probe, zone)
            if exact:
                return min(exact)
        return min(valid)
    return None


def _valid_local_epochs(local: datetime, zone: tzinfo) -> tuple[float, ...]:
    epochs: set[float] = set()
    for fold in (0, 1):
        try:
            aware = local.replace(tzinfo=zone, fold=fold)
            epoch = aware.timestamp()
            if not math.isfinite(epoch):
                continue
            round_trip = datetime.fromtimestamp(epoch, zone).replace(tzinfo=None)
        except (OSError, OverflowError, TypeError, ValueError):
            continue
        if round_trip == local:
            epochs.add(epoch)
    return tuple(sorted(epochs))


def _system_local_timezone(now: float) -> tzinfo:
    environment_zone = os.environ.get("TZ", "").lstrip(":")
    if environment_zone:
        try:
            return ZoneInfo(environment_zone)
        except (ValueError, ZoneInfoNotFoundError):
            pass
    try:
        with Path("/etc/localtime").open("rb") as stream:
            return ZoneInfo.from_file(stream)
    except (OSError, ValueError):
        pass
    try:
        return datetime.fromtimestamp(now).astimezone().tzinfo or timezone.utc
    except (OSError, OverflowError, ValueError):
        return timezone.utc
