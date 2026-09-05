"""Read-only compatibility boundary for local Agent Deck snapshots."""

from __future__ import annotations

import errno
import json
import math
import os
import re
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from .capacity_types import SourceKey
from .models import AgentMode, AgentStatus
from .provider_facts import WorkIdentifier, WorkKey

MAX_SNAPSHOT_BYTES: Final = 1_048_576
MAX_OBSERVATIONS: Final = 128
MAX_AGE: Final = timedelta(hours=24)
MAX_FUTURE_SKEW: Final = timedelta(minutes=5)
MIN_CADENCE_SECONDS: Final = 0.1
MAX_CADENCE_SECONDS: Final = 300.0
SCHEMA_VERSION: Final = 1

_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._~-]{0,63}\Z")
_PROVIDER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_PRIVATE_COMPONENT = re.compile(
    r"(?:^|[._~-])(?:api[_-]?key|authorization|bearer|cookie|credential|"
    r"password|passwd|private[_-]?key|secret|token)(?:$|[._~-])",
    re.IGNORECASE,
)
_RFC3339 = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d{1,6})?(?P<zone>Z|[+-]\d{2}:\d{2})\Z"
)
_NAVIGATION_SCHEMES = frozenset({"agent-deck", "t3code", "alcove"})
_SNAPSHOT_FIELDS = frozenset({"activeProviderId", "device", "generation", "providers", "updatedAt"})
_DEVICE_FIELDS = frozenset({"activeLayer", "connection", "firmware", "model", "owner"})
_PROVIDER_FIELDS = frozenset(
    {"capabilities", "connected", "providerId", "selectedSessionId", "sessions", "slotOrder", "voice"}
)
_SESSION_FIELDS = frozenset(
    {
        "capabilities",
        "cwd",
        "parentId",
        "pinned",
        "providerId",
        "selected",
        "sequence",
        "sessionId",
        "state",
        "title",
        "unread",
        "updatedAt",
    }
)


class AgentDeckState(str, Enum):
    UNASSIGNED = "unassigned"
    IDLE = "idle"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    COMPLETE_UNREAD = "complete_unread"
    ERROR = "error"
    OFFLINE = "offline"


class ReceiptReason(str, Enum):
    DISABLED = "disabled"
    OK = "ok"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AgentObservation:
    agent_id: str
    provider: str
    state: AgentDeckState
    updated_at: datetime
    title: str | None = None
    open_url: str | None = None


@dataclass(frozen=True, slots=True)
class CompatibilityReceipt:
    enabled: bool
    available: bool
    compatible: bool
    reason: ReceiptReason
    source: None = None
    observations: tuple[AgentObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class SnapshotUpdate:
    generation: int
    sampled_at: float
    receipt: CompatibilityReceipt
    statuses: tuple[AgentStatus, ...]


def disabled_receipt() -> CompatibilityReceipt:
    return CompatibilityReceipt(False, False, False, ReceiptReason.DISABLED)


def _identifier(value: Any, field: str, *, provider: bool = False) -> str:
    pattern = _PROVIDER if provider else _IDENTIFIER
    if type(value) is not str or pattern.fullmatch(value) is None or _PRIVATE_COMPONENT.search(value):
        raise ValueError(f"invalid {field}")
    return value


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value or len(value) > 256 or any(ord(char) < 32 for char in value):
        raise ValueError(f"invalid {field}")
    return value


def _now(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: Any, *, now: datetime) -> datetime:
    if type(value) is not str or _RFC3339.fullmatch(value) is None:
        raise ValueError("invalid updated_at")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError as error:
        raise ValueError("invalid updated_at") from error
    age = now - parsed
    if age > MAX_AGE or age < -MAX_FUTURE_SKEW:
        raise ValueError("stale or future updated_at")
    return parsed


def validate_navigation_url(value: Any, *, provider_id: str, session_id: str) -> str:
    """Validate a navigation hint without opening it."""
    _identifier(provider_id, "provider", provider=True)
    _identifier(session_id, "agent_id")
    if type(value) is not str or len(value) > 256 or "\\" in value:
        raise ValueError("invalid open_url")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise ValueError("invalid open_url") from error
    if (
        parsed.scheme not in _NAVIGATION_SCHEMES
        or parsed.netloc != "session"
        or parsed.path != f"/{provider_id}/{session_id}"
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or "%" in value
    ):
        raise ValueError("invalid open_url")
    return value


def _bounded_string_list(value: Any, field: str, maximum: int = 64) -> tuple[str, ...]:
    if (
        type(value) is not list
        or len(value) > maximum
        or not all(type(item) is str and 1 <= len(item) <= 128 for item in value)
    ):
        raise ValueError(f"invalid {field}")
    return tuple(value)


def _observation(value: Any, *, now: datetime, provider_id: str, session_key: str, connected: bool) -> AgentObservation:
    if type(value) is not dict:
        raise ValueError("session must be an object")
    if set(value) - _SESSION_FIELDS:
        raise ValueError("unexpected session field")
    agent_id = _identifier(value.get("sessionId"), "agent_id")
    provider = _identifier(value.get("providerId"), "provider", provider=True)
    if agent_id != session_key or provider != provider_id:
        raise ValueError("session identity mismatch")
    _bounded_string_list(value.get("capabilities"), "session capabilities")
    if any(type(value.get(field)) is not bool for field in ("pinned", "selected", "unread")):
        raise ValueError("invalid session flags")
    if type(value.get("sequence")) is not int or value["sequence"] < 0:
        raise ValueError("invalid session sequence")
    try:
        state = AgentDeckState(value.get("state"))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid state") from error
    if not connected:
        state = AgentDeckState.OFFLINE
    updated_at = _timestamp(value.get("updatedAt"), now=now)
    return AgentObservation(
        agent_id=agent_id,
        provider=provider,
        state=state,
        updated_at=updated_at,
        title=_optional_text(value.get("title"), "title"),
    )


def parse_snapshot(payload: Any, *, now: datetime) -> tuple[AgentObservation, ...]:
    """Validate and flatten the public Agent Deck ``DeckSnapshot`` contract."""
    current = _now(now)
    if type(payload) is not dict:
        raise ValueError("snapshot must be an object")
    if set(payload) - _SNAPSHOT_FIELDS:
        raise ValueError("unexpected snapshot field")
    if type(payload.get("generation")) is not int or payload["generation"] < 0:
        raise ValueError("invalid snapshot generation")
    _timestamp(payload.get("updatedAt"), now=current)
    device = payload.get("device")
    if type(device) is not dict or set(device) - _DEVICE_FIELDS:
        raise ValueError("invalid device snapshot")
    if device.get("connection") not in {"bluetooth", "wired", "unknown", "simulated"}:
        raise ValueError("invalid device connection")
    if device.get("owner") not in {"closed", "probing", "deck_active", "releasing", "native_passthrough", "faulted"}:
        raise ValueError("invalid device owner")
    active_layer = device.get("activeLayer")
    if active_layer is not None and (type(active_layer) is not int or active_layer < 0):
        raise ValueError("invalid device active layer")
    providers = payload.get("providers")
    if type(providers) is not dict or len(providers) > 32:
        raise ValueError("invalid providers")
    observations: list[AgentObservation] = []
    for provider_key, provider_value in providers.items():
        provider_id = _identifier(provider_key, "provider", provider=True)
        if type(provider_value) is not dict or set(provider_value) - _PROVIDER_FIELDS:
            raise ValueError("invalid provider snapshot")
        if provider_value.get("providerId") != provider_id:
            raise ValueError("provider identity mismatch")
        connected = provider_value.get("connected")
        if type(connected) is not bool:
            raise ValueError("invalid provider connection")
        _bounded_string_list(provider_value.get("capabilities"), "provider capabilities")
        if provider_value.get("voice") not in {"off", "listening", "processing", "ready", "error"}:
            raise ValueError("invalid provider voice")
        sessions = provider_value.get("sessions")
        if type(sessions) is not dict or len(sessions) > MAX_OBSERVATIONS:
            raise ValueError("invalid sessions")
        raw_slot_order = provider_value.get("slotOrder")
        for session_key, session in sessions.items():
            safe_key = _identifier(session_key, "agent_id")
            observations.append(
                _observation(session, now=current, provider_id=provider_id, session_key=safe_key, connected=connected)
            )
            if len(observations) > MAX_OBSERVATIONS:
                raise ValueError("too many sessions")
        slot_order = _bounded_string_list(raw_slot_order, "slot order", MAX_OBSERVATIONS)
        if len(slot_order) != len(set(slot_order)) or any(item not in sessions for item in slot_order):
            raise ValueError("invalid slot order")
        selected = provider_value.get("selectedSessionId")
        if selected is not None and selected not in sessions:
            raise ValueError("invalid selected session")
    active_provider = payload.get("activeProviderId")
    if active_provider is not None and active_provider not in providers:
        raise ValueError("invalid active provider")
    return tuple(observations)


_MODE_BY_STATE: Final = {
    AgentDeckState.NEEDS_INPUT: AgentMode.WAITING_FOR_INPUT,
    AgentDeckState.ERROR: AgentMode.BLOCKED_ERROR,
    AgentDeckState.RUNNING: AgentMode.WORKING,
    AgentDeckState.COMPLETE_UNREAD: AgentMode.COMPLETED,
    AgentDeckState.IDLE: AgentMode.IDLE_READY,
    AgentDeckState.OFFLINE: AgentMode.UNKNOWN,
    AgentDeckState.UNASSIGNED: AgentMode.UNKNOWN,
}
_STATUS_PRIORITY: Final = {
    AgentDeckState.NEEDS_INPUT: 0,
    AgentDeckState.ERROR: 1,
    AgentDeckState.RUNNING: 2,
    AgentDeckState.COMPLETE_UNREAD: 3,
    AgentDeckState.IDLE: 4,
    AgentDeckState.OFFLINE: 5,
    AgentDeckState.UNASSIGNED: 6,
}


def observation_to_status(observation: AgentObservation) -> AgentStatus:
    """Project a validated observation into SidePulse's canonical model."""
    source = SourceKey(observation.provider, "agent-deck", "local", "session-observation")
    event_name = "PermissionRequest" if observation.state is AgentDeckState.NEEDS_INPUT else "AgentDeckObservation"
    return AgentStatus(
        provider=observation.provider,
        agent_id=f"{observation.provider}:session:{observation.agent_id}",
        display_name=observation.title or observation.agent_id,
        mode=_MODE_BY_STATE[observation.state],
        updated_at=observation.updated_at,
        event_name=event_name,
        session_id=observation.agent_id,
        origin="agent-deck",
        work_key=WorkKey(source, WorkIdentifier(observation.agent_id)),
    )


def prioritized_statuses(observations: tuple[AgentObservation, ...]) -> tuple[AgentStatus, ...]:
    ordered = sorted(observations, key=lambda item: (_STATUS_PRIORITY[item.state], item.provider, item.agent_id))
    return tuple(observation_to_status(item) for item in ordered)


def _invalid(*, available: bool) -> CompatibilityReceipt:
    return CompatibilityReceipt(
        True, available, False, ReceiptReason.INVALID if available else ReceiptReason.UNAVAILABLE
    )


def read_snapshot(path: Path | str | None, *, enabled: bool = False, now: datetime) -> CompatibilityReceipt:
    """Read a private regular file through a bounded no-follow descriptor."""
    if not enabled:
        return disabled_receipt()
    if path is None or not hasattr(os, "O_NOFOLLOW"):
        return CompatibilityReceipt(True, False, False, ReceiptReason.UNAVAILABLE)
    descriptor: int | None = None
    opened = False
    try:
        descriptor = os.open(os.fspath(path), os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        opened = True
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > MAX_SNAPSHOT_BYTES
        ):
            return _invalid(available=True)
        data = bytearray()
        while len(data) <= MAX_SNAPSHOT_BYTES:
            chunk = os.read(descriptor, MAX_SNAPSHOT_BYTES + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_SNAPSHOT_BYTES:
            return _invalid(available=True)
        observations = parse_snapshot(json.loads(bytes(data)), now=now)
        return CompatibilityReceipt(True, True, True, ReceiptReason.OK, observations=observations)
    except OSError as error:
        refused_link = error.errno in {errno.ELOOP, getattr(errno, "EMLINK", -1)}
        return _invalid(available=opened or refused_link)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return _invalid(available=True)
    finally:
        if descriptor is not None:
            os.close(descriptor)


class AgentDeckSnapshotService:
    """Opt-in polling service for an injected read-only snapshot reader."""

    def __init__(
        self,
        *,
        enabled: bool,
        reader: Callable[[], CompatibilityReceipt],
        clock: Callable[[], float],
        callback: Callable[[SnapshotUpdate], None],
        cadence_seconds: float = 5.0,
    ) -> None:
        if (
            type(cadence_seconds) not in {int, float}
            or not math.isfinite(cadence_seconds)
            or not MIN_CADENCE_SECONDS <= cadence_seconds <= MAX_CADENCE_SECONDS
        ):
            raise ValueError("cadence is outside the bounded range")
        if not all(callable(value) for value in (reader, clock, callback)):
            raise TypeError("reader, clock, and callback must be callable")
        self._enabled = enabled is True
        self._reader = reader
        self._clock = clock
        self._callback = callback
        self._cadence = float(cadence_seconds)
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._delivery_lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._closed = False
        self._last_good: tuple[AgentStatus, ...] = ()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive() and not self._closed

    def start(self) -> bool:
        if not self._enabled:
            return False
        with self._lock:
            if self._closed or self._thread is not None:
                return False
            thread = threading.Thread(target=self._run, name="sidepulse-agent-deck-snapshot", daemon=True)
            self._thread = thread
            thread.start()
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            if self._stop.wait(self._cadence):
                return

    def refresh_once(self) -> bool:
        if not self._enabled:
            return False
        with self._refresh_lock:
            with self._lock:
                if self._closed:
                    return False
                fence = self._generation
            try:
                result = self._reader()
                sampled_at = self._clock()
            except Exception:
                result = CompatibilityReceipt(True, False, False, ReceiptReason.UNAVAILABLE)
                sampled_at = 0.0
            if (
                type(result) is not CompatibilityReceipt
                or type(sampled_at) not in {int, float}
                or not math.isfinite(sampled_at)
            ):
                result = CompatibilityReceipt(True, False, False, ReceiptReason.INVALID)
                sampled_at = 0.0
            succeeded = result.reason is ReceiptReason.OK
            fresh = prioritized_statuses(result.observations) if succeeded else ()
            with self._lock:
                if self._closed or self._generation != fence:
                    return False
                self._generation += 1
                if succeeded:
                    self._last_good = fresh
                statuses = fresh if succeeded else tuple(replace(status, stale=True) for status in self._last_good)
                update = SnapshotUpdate(self._generation, float(sampled_at), result, statuses)
            with self._delivery_lock:
                with self._lock:
                    deliver = not self._closed and self._generation == update.generation
                if not deliver:
                    return False
                try:
                    self._callback(update)
                except Exception:
                    pass
            return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._stop.set()
            thread = self._thread
        # No callback can begin after ``_closed`` is set, and this barrier
        # ensures a callback already in progress finishes before close returns.
        with self._delivery_lock:
            pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=min(1.0, self._cadence + 0.1))


__all__ = [
    "AgentDeckSnapshotService",
    "AgentDeckState",
    "AgentObservation",
    "CompatibilityReceipt",
    "ReceiptReason",
    "SnapshotUpdate",
    "disabled_receipt",
    "observation_to_status",
    "parse_snapshot",
    "prioritized_statuses",
    "read_snapshot",
    "validate_navigation_url",
]
