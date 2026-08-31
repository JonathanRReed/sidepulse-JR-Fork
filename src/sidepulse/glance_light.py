"""Pure, content-free planning and persistence for Glance Light.

This module owns no runtime integration.  It does not read or write files,
open devices, import AppKit, use the network, or retain notification content.
Callers provide content-free notification facts and consume immutable surface
plans for Dot, a Pro endpoint, a Screen Bar orb, and a menu accent.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Final

GLANCE_LIGHT_DOCUMENT_SCHEMA: Final = "sidepulse.glance-light"
GLANCE_LIGHT_DOCUMENT_VERSION: Final = 1
MAX_GLANCE_NOTIFICATIONS: Final = 64
MAX_GLANCE_DOCUMENT_BYTES: Final = 64 * 1024

COMPLETED_UNSEEN_LIFETIME_SECONDS: Final = 15 * 60.0
INFORMATIONAL_LIFETIME_SECONDS: Final = 5 * 60.0

_NOTIFICATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DOCUMENT_FIELDS: Final = frozenset({"schema", "version", "notifications"})
_NOTIFICATION_FIELDS: Final = frozenset(
    {
        "id",
        "kind",
        "priority",
        "created_at_epoch",
        "expires_at_epoch",
        "acknowledged_at_epoch",
        "seen_at_epoch",
        "resolved_at_epoch",
        "privacy_class",
        "destinations",
    }
)
_DEFAULT_EXPIRY = object()


class GlanceLightValidationError(ValueError):
    """A Glance Light value crossed the pure boundary in an unsafe shape."""


class GlanceKind(str, Enum):
    UNANSWERED_ASK = "unanswered_ask"
    FAILURE = "failure"
    COMPLETED_UNSEEN = "completed_unseen"
    INFORMATIONAL = "informational"


class GlancePriority(str, Enum):
    INFORMATIONAL = "informational"
    COMPLETION = "completion"
    ACTION_REQUIRED = "action_required"
    FAILURE = "failure"


class GlancePrivacyClass(str, Enum):
    """Content-free handling classes, never notification body classifications."""

    CONTENT_FREE = "content_free"
    LOCAL_ONLY = "local_only"


class GlanceDestination(str, Enum):
    DOT = "dot"
    PRO_ENDPOINT = "pro_endpoint"
    SCREEN_BAR_ORB = "screen_bar_orb"
    MENU_ACCENT = "menu_accent"


GLANCE_LIGHT_DESTINATIONS: Final = (
    GlanceDestination.DOT,
    GlanceDestination.PRO_ENDPOINT,
    GlanceDestination.SCREEN_BAR_ORB,
    GlanceDestination.MENU_ACCENT,
)


class GlancePattern(str, Enum):
    DARK = "dark"
    DOUBLE_SOFT_PULSE = "double_soft_pulse"
    TRIPLE_FAILURE = "triple_failure"
    SHORT_WINK = "short_wink"
    STEADY_DIM = "steady_dim"
    STATIC_MARKER = "static_marker"


_PRIORITY_RANK: Final = {
    GlancePriority.INFORMATIONAL: 1,
    GlancePriority.COMPLETION: 2,
    GlancePriority.ACTION_REQUIRED: 3,
    GlancePriority.FAILURE: 4,
}
_DEFAULT_PRIORITY: Final = {
    GlanceKind.INFORMATIONAL: GlancePriority.INFORMATIONAL,
    GlanceKind.COMPLETED_UNSEEN: GlancePriority.COMPLETION,
    GlanceKind.UNANSWERED_ASK: GlancePriority.ACTION_REQUIRED,
    GlanceKind.FAILURE: GlancePriority.FAILURE,
}
_SURFACE_INTENSITY: Final = {
    GlanceDestination.DOT: 0.22,
    GlanceDestination.PRO_ENDPOINT: 0.18,
    GlanceDestination.SCREEN_BAR_ORB: 0.16,
    GlanceDestination.MENU_ACCENT: 0.14,
}


def _finite_nonnegative(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and value >= 0.0
    )


def _optional_epoch(value: object) -> float | None:
    if value is None:
        return None
    if not _finite_nonnegative(value):
        raise GlanceLightValidationError("invalid Glance Light epoch")
    return float(value)


def _valid_notification_id(value: object) -> bool:
    return type(value) is str and _NOTIFICATION_ID.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class GlanceCadence:
    """The safe, low-energy visual language for one semantic kind."""

    pattern: GlancePattern
    pulse_count: int
    pulse_on_seconds: float
    pulse_gap_seconds: float
    repeat_interval_seconds: float | None
    uses_identity_tint: bool
    positional_failure_signature: bool

    def __post_init__(self) -> None:
        repeat = self.repeat_interval_seconds
        if not (
            type(self.pattern) is GlancePattern
            and type(self.pulse_count) is int
            and 0 <= self.pulse_count <= 3
            and _finite_nonnegative(self.pulse_on_seconds)
            and float(self.pulse_on_seconds) <= 1.0
            and _finite_nonnegative(self.pulse_gap_seconds)
            and float(self.pulse_gap_seconds) <= 1.0
            and (repeat is None or (_finite_nonnegative(repeat) and float(repeat) >= 5.0))
            and type(self.uses_identity_tint) is bool
            and type(self.positional_failure_signature) is bool
        ):
            raise GlanceLightValidationError("invalid Glance Light cadence")
        object.__setattr__(self, "pulse_on_seconds", float(self.pulse_on_seconds))
        object.__setattr__(self, "pulse_gap_seconds", float(self.pulse_gap_seconds))
        object.__setattr__(
            self,
            "repeat_interval_seconds",
            None if repeat is None else float(repeat),
        )


_DEFAULT_CADENCE: Final = {
    GlanceKind.UNANSWERED_ASK: GlanceCadence(
        GlancePattern.DOUBLE_SOFT_PULSE,
        2,
        0.22,
        0.32,
        30.0,
        True,
        False,
    ),
    GlanceKind.FAILURE: GlanceCadence(
        GlancePattern.TRIPLE_FAILURE,
        3,
        0.30,
        0.30,
        20.0,
        True,
        True,
    ),
    GlanceKind.COMPLETED_UNSEEN: GlanceCadence(
        GlancePattern.SHORT_WINK,
        1,
        0.15,
        0.0,
        30.0,
        True,
        False,
    ),
    GlanceKind.INFORMATIONAL: GlanceCadence(
        GlancePattern.STEADY_DIM,
        0,
        0.0,
        0.0,
        None,
        True,
        False,
    ),
}


def default_glance_cadence(kind: GlanceKind) -> GlanceCadence:
    """Return the immutable default language for a known notification kind."""

    if type(kind) is not GlanceKind:
        raise GlanceLightValidationError("unknown Glance Light kind")
    return _DEFAULT_CADENCE[kind]


@dataclass(frozen=True, slots=True)
class GlanceNotification:
    """One durable notification identity with no content-bearing fields."""

    notification_id: str
    kind: GlanceKind
    priority: GlancePriority
    created_at_epoch: float
    expires_at_epoch: float | None
    acknowledged_at_epoch: float | None
    seen_at_epoch: float | None
    resolved_at_epoch: float | None
    privacy_class: GlancePrivacyClass
    destinations: tuple[GlanceDestination, ...]

    def __post_init__(self) -> None:
        if not _valid_notification_id(self.notification_id):
            raise GlanceLightValidationError("invalid Glance Light notification id")
        if type(self.kind) is not GlanceKind:
            raise GlanceLightValidationError("invalid Glance Light kind")
        if type(self.priority) is not GlancePriority:
            raise GlanceLightValidationError("invalid Glance Light priority")
        if not _finite_nonnegative(self.created_at_epoch):
            raise GlanceLightValidationError("invalid Glance Light creation time")
        created = float(self.created_at_epoch)
        expires = _optional_epoch(self.expires_at_epoch)
        acknowledged = _optional_epoch(self.acknowledged_at_epoch)
        seen = _optional_epoch(self.seen_at_epoch)
        resolved = _optional_epoch(self.resolved_at_epoch)
        if expires is not None and expires <= created:
            raise GlanceLightValidationError("Glance Light expiry must follow creation")
        if any(
            epoch is not None and epoch < created
            for epoch in (acknowledged, seen, resolved)
        ):
            raise GlanceLightValidationError("Glance Light receipt predates creation")
        if type(self.privacy_class) is not GlancePrivacyClass:
            raise GlanceLightValidationError("invalid Glance Light privacy class")
        if not (
            type(self.destinations) is tuple
            and 1 <= len(self.destinations) <= len(GLANCE_LIGHT_DESTINATIONS)
            and all(type(item) is GlanceDestination for item in self.destinations)
            and len(set(self.destinations)) == len(self.destinations)
        ):
            raise GlanceLightValidationError("invalid Glance Light destinations")
        object.__setattr__(self, "created_at_epoch", created)
        object.__setattr__(self, "expires_at_epoch", expires)
        object.__setattr__(self, "acknowledged_at_epoch", acknowledged)
        object.__setattr__(self, "seen_at_epoch", seen)
        object.__setattr__(self, "resolved_at_epoch", resolved)

    @property
    def acknowledged(self) -> bool:
        return self.acknowledged_at_epoch is not None

    @property
    def seen(self) -> bool:
        return self.seen_at_epoch is not None

    @property
    def resolved(self) -> bool:
        return self.resolved_at_epoch is not None

    def pending_at(self, now_epoch: float) -> bool:
        return (
            self.created_at_epoch <= now_epoch
            and (self.expires_at_epoch is None or now_epoch < self.expires_at_epoch)
            and not self.acknowledged
            and not self.seen
            and not self.resolved
        )


@dataclass(frozen=True, slots=True)
class GlanceLightState:
    notifications: tuple[GlanceNotification, ...] = ()

    def __post_init__(self) -> None:
        if not (
            type(self.notifications) is tuple
            and len(self.notifications) <= MAX_GLANCE_NOTIFICATIONS
            and all(type(item) is GlanceNotification for item in self.notifications)
        ):
            raise GlanceLightValidationError("invalid Glance Light state")
        identities = tuple(item.notification_id for item in self.notifications)
        if len(set(identities)) != len(identities):
            raise GlanceLightValidationError("duplicate Glance Light notification id")


@dataclass(frozen=True, slots=True)
class GlanceEnvironment:
    dnd_active: bool = False
    dim_asks_in_dnd: bool = False
    low_power: bool = False
    serious_thermal: bool = False

    def __post_init__(self) -> None:
        if not all(
            type(value) is bool
            for value in (
                self.dnd_active,
                self.dim_asks_in_dnd,
                self.low_power,
                self.serious_thermal,
            )
        ):
            raise GlanceLightValidationError("invalid Glance Light environment")


@dataclass(frozen=True, slots=True)
class GlanceSurfacePlan:
    destination: GlanceDestination
    active: bool
    notification_id: str | None
    pattern: GlancePattern
    pulse_count: int
    pulse_on_seconds: float
    pulse_gap_seconds: float
    repeat_interval_seconds: float | None
    intensity: float
    uses_identity_tint: bool
    positional_failure_signature: bool
    notification_count: int
    energy_class: str = "low"

    def __post_init__(self) -> None:
        repeat = self.repeat_interval_seconds
        valid_common = (
            type(self.destination) is GlanceDestination
            and type(self.active) is bool
            and type(self.pattern) is GlancePattern
            and type(self.pulse_count) is int
            and 0 <= self.pulse_count <= 3
            and _finite_nonnegative(self.pulse_on_seconds)
            and _finite_nonnegative(self.pulse_gap_seconds)
            and (repeat is None or _finite_nonnegative(repeat))
            and _finite_nonnegative(self.intensity)
            and float(self.intensity) <= 0.25
            and type(self.uses_identity_tint) is bool
            and type(self.positional_failure_signature) is bool
            and type(self.notification_count) is int
            and 0 <= self.notification_count <= MAX_GLANCE_NOTIFICATIONS
            and self.energy_class == "low"
        )
        if not valid_common:
            raise GlanceLightValidationError("invalid Glance Light surface plan")
        if self.active:
            if not _valid_notification_id(self.notification_id) or self.pattern is GlancePattern.DARK:
                raise GlanceLightValidationError("invalid active Glance Light surface plan")
        elif not (
            self.notification_id is None
            and self.pattern is GlancePattern.DARK
            and self.pulse_count == 0
            and float(self.pulse_on_seconds) == 0.0
            and float(self.pulse_gap_seconds) == 0.0
            and self.repeat_interval_seconds is None
            and float(self.intensity) == 0.0
            and not self.uses_identity_tint
            and not self.positional_failure_signature
        ):
            raise GlanceLightValidationError("inactive Glance Light plan must be dark")
        object.__setattr__(self, "pulse_on_seconds", float(self.pulse_on_seconds))
        object.__setattr__(self, "pulse_gap_seconds", float(self.pulse_gap_seconds))
        object.__setattr__(self, "intensity", float(self.intensity))
        object.__setattr__(
            self,
            "repeat_interval_seconds",
            None if repeat is None else float(repeat),
        )


@dataclass(frozen=True, slots=True)
class GlanceLightPlan:
    selected_notification_id: str | None
    notification_count: int
    count_summary: str
    surface_plans: tuple[GlanceSurfacePlan, ...]

    def __post_init__(self) -> None:
        expected_summary = _count_summary(self.notification_count)
        if not (
            (self.selected_notification_id is None or _valid_notification_id(self.selected_notification_id))
            and type(self.notification_count) is int
            and 0 <= self.notification_count <= MAX_GLANCE_NOTIFICATIONS
            and self.count_summary == expected_summary
            and type(self.surface_plans) is tuple
            and tuple(item.destination for item in self.surface_plans)
            == GLANCE_LIGHT_DESTINATIONS
            and all(type(item) is GlanceSurfacePlan for item in self.surface_plans)
            and all(item.notification_count == self.notification_count for item in self.surface_plans)
        ):
            raise GlanceLightValidationError("invalid Glance Light plan")
        active_ids = {
            item.notification_id for item in self.surface_plans if item.active
        }
        if self.selected_notification_id is None:
            if active_ids:
                raise GlanceLightValidationError("dark Glance Light plan has an active surface")
        elif active_ids != {self.selected_notification_id}:
            raise GlanceLightValidationError("Glance Light surfaces disagree on identity")

    @property
    def announcer_summary(self) -> str:
        """Content-free count copy shared by announcer and menu consumers."""

        return self.count_summary


def _count_summary(count: int) -> str:
    return f"{count} notification" if count == 1 else f"{count} notifications"


def _dark_surface(
    destination: GlanceDestination,
    *,
    notification_count: int,
) -> GlanceSurfacePlan:
    return GlanceSurfacePlan(
        destination=destination,
        active=False,
        notification_id=None,
        pattern=GlancePattern.DARK,
        pulse_count=0,
        pulse_on_seconds=0.0,
        pulse_gap_seconds=0.0,
        repeat_interval_seconds=None,
        intensity=0.0,
        uses_identity_tint=False,
        positional_failure_signature=False,
        notification_count=notification_count,
    )


def _dark_plan(notification_count: int = 0) -> GlanceLightPlan:
    return GlanceLightPlan(
        selected_notification_id=None,
        notification_count=notification_count,
        count_summary=_count_summary(notification_count),
        surface_plans=tuple(
            _dark_surface(destination, notification_count=notification_count)
            for destination in GLANCE_LIGHT_DESTINATIONS
        ),
    )


def make_glance_notification(
    *,
    notification_id: str,
    kind: GlanceKind,
    created_at_epoch: float,
    priority: GlancePriority | None = None,
    expires_at_epoch: float | object | None = _DEFAULT_EXPIRY,
    privacy_class: GlancePrivacyClass = GlancePrivacyClass.CONTENT_FREE,
    destinations: tuple[GlanceDestination, ...] = GLANCE_LIGHT_DESTINATIONS,
) -> GlanceNotification:
    """Create a notification with the P4.52 default lifetime and priority."""

    if type(kind) is not GlanceKind:
        raise GlanceLightValidationError("invalid Glance Light kind")
    if priority is None:
        priority = _DEFAULT_PRIORITY[kind]
    if expires_at_epoch is _DEFAULT_EXPIRY:
        if not _finite_nonnegative(created_at_epoch):
            raise GlanceLightValidationError("invalid Glance Light creation time")
        if kind is GlanceKind.COMPLETED_UNSEEN:
            expires_at_epoch = float(created_at_epoch) + COMPLETED_UNSEEN_LIFETIME_SECONDS
        elif kind is GlanceKind.INFORMATIONAL:
            expires_at_epoch = float(created_at_epoch) + INFORMATIONAL_LIFETIME_SECONDS
        else:
            expires_at_epoch = None
    return GlanceNotification(
        notification_id=notification_id,
        kind=kind,
        priority=priority,
        created_at_epoch=created_at_epoch,
        expires_at_epoch=expires_at_epoch,  # type: ignore[arg-type]
        acknowledged_at_epoch=None,
        seen_at_epoch=None,
        resolved_at_epoch=None,
        privacy_class=privacy_class,
        destinations=destinations,
    )


def expire_glance_notifications(
    state: GlanceLightState,
    *,
    now_epoch: float,
) -> GlanceLightState:
    """Remove notifications whose expiry is at or before the supplied clock."""

    if type(state) is not GlanceLightState or not _finite_nonnegative(now_epoch):
        return state if type(state) is GlanceLightState else GlanceLightState()
    now = float(now_epoch)
    return GlanceLightState(
        tuple(
            notification
            for notification in state.notifications
            if notification.expires_at_epoch is None
            or now < notification.expires_at_epoch
        )
    )


def _set_receipt(
    state: GlanceLightState,
    *,
    notification_id: object,
    at_epoch: object,
    field_name: str,
) -> GlanceLightState:
    if (
        type(state) is not GlanceLightState
        or not _valid_notification_id(notification_id)
        or not _finite_nonnegative(at_epoch)
    ):
        return state if type(state) is GlanceLightState else GlanceLightState()
    at = float(at_epoch)
    updated: list[GlanceNotification] = []
    for notification in state.notifications:
        if notification.notification_id != notification_id:
            updated.append(notification)
            continue
        if at < notification.created_at_epoch or getattr(notification, field_name) is not None:
            updated.append(notification)
            continue
        updated.append(replace(notification, **{field_name: at}))
    return GlanceLightState(tuple(updated))


def acknowledge_glance_notification(
    state: GlanceLightState,
    *,
    notification_id: str,
    acknowledged_at_epoch: float,
) -> GlanceLightState:
    """Acknowledge one exact identity, clearing it from every destination."""

    return _set_receipt(
        state,
        notification_id=notification_id,
        at_epoch=acknowledged_at_epoch,
        field_name="acknowledged_at_epoch",
    )


def mark_glance_notification_seen(
    state: GlanceLightState,
    *,
    notification_id: str,
    seen_at_epoch: float,
) -> GlanceLightState:
    """Record that an outcome was opened without claiming its work resolved."""

    return _set_receipt(
        state,
        notification_id=notification_id,
        at_epoch=seen_at_epoch,
        field_name="seen_at_epoch",
    )


def resolve_glance_notification(
    state: GlanceLightState,
    *,
    notification_id: str,
    resolved_at_epoch: float,
) -> GlanceLightState:
    """Record source resolution independently from seen or acknowledgement."""

    return _set_receipt(
        state,
        notification_id=notification_id,
        at_epoch=resolved_at_epoch,
        field_name="resolved_at_epoch",
    )


def _selected(
    notifications: tuple[GlanceNotification, ...],
) -> GlanceNotification:
    return min(
        notifications,
        key=lambda item: (
            -_PRIORITY_RANK[item.priority],
            item.created_at_epoch,
            item.notification_id,
        ),
    )


def _constrained_cadence(
    notification: GlanceNotification,
) -> GlanceCadence:
    base = default_glance_cadence(notification.kind)
    return GlanceCadence(
        GlancePattern.STATIC_MARKER,
        0,
        0.0,
        0.0,
        None,
        base.uses_identity_tint,
        base.positional_failure_signature,
    )


def plan_glance_light(
    state: GlanceLightState,
    *,
    now_epoch: float,
    environment: GlanceEnvironment = GlanceEnvironment(),
) -> GlanceLightPlan:
    """Select one identity and compile four deterministic low-energy plans."""

    if not (
        type(state) is GlanceLightState
        and _finite_nonnegative(now_epoch)
        and type(environment) is GlanceEnvironment
    ):
        return _dark_plan()
    now = float(now_epoch)
    pending = tuple(
        notification
        for notification in state.notifications
        if notification.pending_at(now)
    )
    count = len(pending)
    if not pending:
        return _dark_plan()
    if environment.dnd_active:
        if not environment.dim_asks_in_dnd:
            return _dark_plan(count)
        asks = tuple(
            notification
            for notification in pending
            if notification.kind is GlanceKind.UNANSWERED_ASK
        )
        if not asks:
            return _dark_plan(count)
        selected = _selected(asks)
        cadence = _constrained_cadence(selected)
        intensity_ceiling = 0.08
    else:
        selected = _selected(pending)
        constrained = environment.low_power or environment.serious_thermal
        cadence = (
            _constrained_cadence(selected)
            if constrained
            else default_glance_cadence(selected.kind)
        )
        intensity_ceiling = 0.08 if environment.serious_thermal else 0.12 if constrained else 0.25

    surface_plans: list[GlanceSurfacePlan] = []
    for destination in GLANCE_LIGHT_DESTINATIONS:
        if destination not in selected.destinations:
            surface_plans.append(_dark_surface(destination, notification_count=count))
            continue
        surface_plans.append(
            GlanceSurfacePlan(
                destination=destination,
                active=True,
                notification_id=selected.notification_id,
                pattern=cadence.pattern,
                pulse_count=cadence.pulse_count,
                pulse_on_seconds=cadence.pulse_on_seconds,
                pulse_gap_seconds=cadence.pulse_gap_seconds,
                repeat_interval_seconds=cadence.repeat_interval_seconds,
                intensity=min(_SURFACE_INTENSITY[destination], intensity_ceiling),
                uses_identity_tint=cadence.uses_identity_tint,
                positional_failure_signature=cadence.positional_failure_signature,
                notification_count=count,
            )
        )
    return GlanceLightPlan(
        selected_notification_id=selected.notification_id,
        notification_count=count,
        count_summary=_count_summary(count),
        surface_plans=tuple(surface_plans),
    )


def _notification_document(notification: GlanceNotification) -> dict[str, object]:
    return {
        "id": notification.notification_id,
        "kind": notification.kind.value,
        "priority": notification.priority.value,
        "created_at_epoch": notification.created_at_epoch,
        "expires_at_epoch": notification.expires_at_epoch,
        "acknowledged_at_epoch": notification.acknowledged_at_epoch,
        "seen_at_epoch": notification.seen_at_epoch,
        "resolved_at_epoch": notification.resolved_at_epoch,
        "privacy_class": notification.privacy_class.value,
        "destinations": [destination.value for destination in notification.destinations],
    }


def serialize_glance_light_document(state: object) -> str | None:
    """Return one canonical versioned JSON document, or refuse closed."""

    if type(state) is not GlanceLightState:
        return None
    try:
        encoded = json.dumps(
            {
                "schema": GLANCE_LIGHT_DOCUMENT_SCHEMA,
                "version": GLANCE_LIGHT_DOCUMENT_VERSION,
                "notifications": [
                    _notification_document(notification)
                    for notification in state.notifications
                ],
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return None
    if len(encoded.encode("utf-8")) > MAX_GLANCE_DOCUMENT_BYTES:
        return None
    return encoded


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _parse_enum(enum_type: type[Enum], value: object) -> Enum:
    if type(value) is not str:
        raise GlanceLightValidationError("invalid Glance Light enum")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise GlanceLightValidationError("invalid Glance Light enum") from exc


def _restore_notification(value: object) -> GlanceNotification:
    if type(value) is not dict or set(value) != _NOTIFICATION_FIELDS:
        raise GlanceLightValidationError("invalid Glance Light notification document")
    destinations_value = value["destinations"]
    if not (
        type(destinations_value) is list
        and 1 <= len(destinations_value) <= len(GLANCE_LIGHT_DESTINATIONS)
    ):
        raise GlanceLightValidationError("invalid Glance Light destinations document")
    destinations = tuple(
        _parse_enum(GlanceDestination, destination)
        for destination in destinations_value
    )
    return GlanceNotification(
        notification_id=value["id"],
        kind=_parse_enum(GlanceKind, value["kind"]),
        priority=_parse_enum(GlancePriority, value["priority"]),
        created_at_epoch=value["created_at_epoch"],
        expires_at_epoch=value["expires_at_epoch"],
        acknowledged_at_epoch=value["acknowledged_at_epoch"],
        seen_at_epoch=value["seen_at_epoch"],
        resolved_at_epoch=value["resolved_at_epoch"],
        privacy_class=_parse_enum(GlancePrivacyClass, value["privacy_class"]),
        destinations=destinations,
    )


def restore_glance_light_document(document: object) -> GlanceLightState | None:
    """Restore only the exact supported schema, never a partial document."""

    if type(document) is not str or not document:
        return None
    try:
        if len(document.encode("utf-8")) > MAX_GLANCE_DOCUMENT_BYTES:
            return None
        value = json.loads(
            document,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        if type(value) is not dict or set(value) != _DOCUMENT_FIELDS:
            return None
        if (
            value["schema"] != GLANCE_LIGHT_DOCUMENT_SCHEMA
            or type(value["version"]) is not int
            or value["version"] != GLANCE_LIGHT_DOCUMENT_VERSION
            or type(value["notifications"]) is not list
            or len(value["notifications"]) > MAX_GLANCE_NOTIFICATIONS
        ):
            return None
        return GlanceLightState(
            tuple(
                _restore_notification(notification)
                for notification in value["notifications"]
            )
        )
    except (
        GlanceLightValidationError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


__all__ = [
    "COMPLETED_UNSEEN_LIFETIME_SECONDS",
    "GLANCE_LIGHT_DESTINATIONS",
    "GLANCE_LIGHT_DOCUMENT_SCHEMA",
    "GLANCE_LIGHT_DOCUMENT_VERSION",
    "INFORMATIONAL_LIFETIME_SECONDS",
    "MAX_GLANCE_DOCUMENT_BYTES",
    "MAX_GLANCE_NOTIFICATIONS",
    "GlanceCadence",
    "GlanceDestination",
    "GlanceEnvironment",
    "GlanceKind",
    "GlanceLightPlan",
    "GlanceLightState",
    "GlanceLightValidationError",
    "GlanceNotification",
    "GlancePattern",
    "GlancePriority",
    "GlancePrivacyClass",
    "GlanceSurfacePlan",
    "acknowledge_glance_notification",
    "default_glance_cadence",
    "expire_glance_notifications",
    "make_glance_notification",
    "mark_glance_notification_seen",
    "plan_glance_light",
    "resolve_glance_notification",
    "restore_glance_light_document",
    "serialize_glance_light_document",
]
