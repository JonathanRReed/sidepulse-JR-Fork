"""Pure, bounded, content-free effect history and browser projection.

This is deliberately separate from :mod:`sidepulse.activity_ledger`. Activity
history answers what changed in an agent's work. Effect history answers what
SidePulse itself attempted to present, where it attempted it, and which
bounded policy outcome applied. It stores no provider text, prompt content,
session identity, path, URL, or navigation target.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

EFFECT_EVENT_VERSION: Final = 1
MAX_EFFECT_EVENTS: Final = 200
MAX_EFFECT_HISTORY_BYTES: Final = 64 * 1024
MAX_EFFECT_EVENT_ID_LENGTH: Final = 128
MAX_EFFECT_ID_LENGTH: Final = 96

# The store wraps the event array with a document version and seen watermark.
# Reserving its maximum practical envelope here lets this pure layer enforce
# the same byte invariant before persistence is involved.
_ENVELOPE_RESERVE_BYTES: Final = 512
_OPAQUE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")


class EffectHistoryValidationError(ValueError):
    """An effect-history value failed its content-free typed boundary."""


class EffectSemanticCategory(str, Enum):
    """Bounded meanings a light or presentation may communicate."""

    ATTENTION = "attention"
    COMPLETION = "completion"
    FAILURE = "failure"
    RECOVERY = "recovery"
    HANDOFF = "handoff"
    INTERRUPTION = "interruption"
    CAPACITY = "capacity"
    NOTIFICATION = "notification"
    AMBIENT = "ambient"
    SYSTEM = "system"


class EffectSurface(str, Enum):
    """Product-owned destinations that may present an effect."""

    SCREEN_BAR = "screen_bar"
    SCREEN_BAR_ORB = "screen_bar_orb"
    DOT = "dot"
    PRO_ENDPOINT = "pro_endpoint"
    GLANCE_LIGHT = "glance_light"
    ALCOVE = "alcove"
    AGENT_BROWSER = "agent_browser"
    MENU = "menu"
    MENU_ACCENT = "menu_accent"
    NOTIFICATION = "notification"
    SETTINGS_PREVIEW = "settings_preview"


class EffectOutcome(str, Enum):
    SHOWN = "shown"
    SUPPRESSED = "suppressed"
    ACKNOWLEDGED = "acknowledged"
    EXPIRED = "expired"


class EffectSuppressionReason(str, Enum):
    """Closed suppression vocabulary, never a free-form policy message."""

    DO_NOT_DISTURB = "do_not_disturb"
    FOCUS = "focus"
    SNOOZED = "snoozed"
    COURTESY_LIMIT = "courtesy_limit"
    LOW_POWER = "low_power"
    THERMAL = "thermal"
    REDUCE_MOTION = "reduce_motion"
    HIGHER_PRIORITY = "higher_priority"
    DESTINATION_UNAVAILABLE = "destination_unavailable"
    EXPIRED = "expired"


class EffectAcknowledgementSource(str, Enum):
    """Closed set of local surfaces that can acknowledge a presentation."""

    SCREEN_BAR = "screen_bar"
    GLANCE_LIGHT = "glance_light"
    ALCOVE = "alcove"
    AGENT_BROWSER = "agent_browser"
    MENU = "menu"
    NOTIFICATION = "notification"


_CATEGORY_LABELS: Final[dict[EffectSemanticCategory, str]] = {
    EffectSemanticCategory.ATTENTION: "Attention",
    EffectSemanticCategory.COMPLETION: "Completion",
    EffectSemanticCategory.FAILURE: "Failure",
    EffectSemanticCategory.RECOVERY: "Recovery",
    EffectSemanticCategory.HANDOFF: "Handoff",
    EffectSemanticCategory.INTERRUPTION: "Interruption",
    EffectSemanticCategory.CAPACITY: "Capacity",
    EffectSemanticCategory.NOTIFICATION: "Notification",
    EffectSemanticCategory.AMBIENT: "Ambient",
    EffectSemanticCategory.SYSTEM: "System",
}
_SURFACE_LABELS: Final[dict[EffectSurface, str]] = {
    EffectSurface.SCREEN_BAR: "Screen Bar",
    EffectSurface.SCREEN_BAR_ORB: "Screen Bar Orb",
    EffectSurface.DOT: "Dot",
    EffectSurface.PRO_ENDPOINT: "Pro Endpoint",
    EffectSurface.GLANCE_LIGHT: "Glance Light",
    EffectSurface.ALCOVE: "Alcove",
    EffectSurface.AGENT_BROWSER: "Agent Browser",
    EffectSurface.MENU: "Menu",
    EffectSurface.MENU_ACCENT: "Menu Accent",
    EffectSurface.NOTIFICATION: "Notification",
    EffectSurface.SETTINGS_PREVIEW: "Settings Preview",
}
_ACKNOWLEDGEMENT_LABELS: Final[dict[EffectAcknowledgementSource, str]] = {
    EffectAcknowledgementSource.SCREEN_BAR: "Screen Bar",
    EffectAcknowledgementSource.GLANCE_LIGHT: "Glance Light",
    EffectAcknowledgementSource.ALCOVE: "Alcove",
    EffectAcknowledgementSource.AGENT_BROWSER: "Agent Browser",
    EffectAcknowledgementSource.MENU: "Menu",
    EffectAcknowledgementSource.NOTIFICATION: "Notification",
}
_SUPPRESSION_LABELS: Final[dict[EffectSuppressionReason, str]] = {
    EffectSuppressionReason.DO_NOT_DISTURB: "Do Not Disturb",
    EffectSuppressionReason.FOCUS: "Focus",
    EffectSuppressionReason.SNOOZED: "Snooze",
    EffectSuppressionReason.COURTESY_LIMIT: "Courtesy Limit",
    EffectSuppressionReason.LOW_POWER: "Low Power",
    EffectSuppressionReason.THERMAL: "Thermal Policy",
    EffectSuppressionReason.REDUCE_MOTION: "Reduce Motion",
    EffectSuppressionReason.HIGHER_PRIORITY: "Higher Priority Effect",
    EffectSuppressionReason.DESTINATION_UNAVAILABLE: "Unavailable Destination",
    EffectSuppressionReason.EXPIRED: "Expiration",
}


def _finite_nonnegative(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and float(value) >= 0.0
    )


def _opaque_identifier(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and _OPAQUE_IDENTIFIER.fullmatch(value) is not None
    )


@dataclass(frozen=True, slots=True)
class EffectEvent:
    """One immutable presentation fact with exact product-owned identity."""

    event_id: str
    occurred_at_epoch: float
    effect_id: str
    semantic_category: EffectSemanticCategory
    surface: EffectSurface
    outcome: EffectOutcome
    suppression_reason: EffectSuppressionReason | None = None
    acknowledgement_source: EffectAcknowledgementSource | None = None
    version: int = EFFECT_EVENT_VERSION

    def __post_init__(self) -> None:
        if not (
            type(self.version) is int
            and self.version == EFFECT_EVENT_VERSION
            and _opaque_identifier(self.event_id, MAX_EFFECT_EVENT_ID_LENGTH)
            and _finite_nonnegative(self.occurred_at_epoch)
            and _opaque_identifier(self.effect_id, MAX_EFFECT_ID_LENGTH)
            and type(self.semantic_category) is EffectSemanticCategory
            and type(self.surface) is EffectSurface
            and type(self.outcome) is EffectOutcome
            and (
                self.suppression_reason is None
                or type(self.suppression_reason) is EffectSuppressionReason
            )
            and (
                self.acknowledgement_source is None
                or type(self.acknowledgement_source)
                is EffectAcknowledgementSource
            )
        ):
            raise EffectHistoryValidationError("invalid effect event")

        suppressed = self.outcome is EffectOutcome.SUPPRESSED
        acknowledged = self.outcome is EffectOutcome.ACKNOWLEDGED
        if suppressed != (self.suppression_reason is not None):
            raise EffectHistoryValidationError(
                "suppression outcome requires exactly one bounded reason"
            )
        if acknowledged != (self.acknowledgement_source is not None):
            raise EffectHistoryValidationError(
                "acknowledgement outcome requires exactly one bounded source"
            )
        if suppressed and self.acknowledgement_source is not None:
            raise EffectHistoryValidationError("suppression cannot acknowledge")
        if acknowledged and self.suppression_reason is not None:
            raise EffectHistoryValidationError("acknowledgement cannot suppress")
        object.__setattr__(self, "occurred_at_epoch", float(self.occurred_at_epoch))

    @property
    def identity(self) -> tuple[object, ...]:
        """Exact delivery identity used for idempotence, never a fuzzy key."""

        return (
            self.version,
            self.event_id,
            self.occurred_at_epoch,
            self.effect_id,
            self.semantic_category.value,
            self.surface.value,
            self.outcome.value,
            None
            if self.suppression_reason is None
            else self.suppression_reason.value,
            None
            if self.acknowledgement_source is None
            else self.acknowledgement_source.value,
        )


def effect_event_to_payload(event: EffectEvent) -> dict[str, object]:
    """Return the only persisted shape accepted for an effect event."""

    if type(event) is not EffectEvent:
        raise EffectHistoryValidationError("invalid effect event")
    return {
        "version": event.version,
        "event_id": event.event_id,
        "occurred_at_epoch": event.occurred_at_epoch,
        "effect_id": event.effect_id,
        "semantic_category": event.semantic_category.value,
        "surface": event.surface.value,
        "outcome": event.outcome.value,
        "suppression_reason": (
            None
            if event.suppression_reason is None
            else event.suppression_reason.value
        ),
        "acknowledgement_source": (
            None
            if event.acknowledgement_source is None
            else event.acknowledgement_source.value
        ),
    }


def _event_encoded_bytes(event: EffectEvent) -> int:
    return len(
        json.dumps(
            effect_event_to_payload(event),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ) + 1


def _event_sort_key(event: EffectEvent) -> tuple[object, ...]:
    return (
        -event.occurred_at_epoch,
        event.event_id,
        event.surface.value,
        event.outcome.value,
        event.effect_id,
        event.semantic_category.value,
        event.suppression_reason.value if event.suppression_reason else "",
        event.acknowledgement_source.value if event.acknowledgement_source else "",
    )


def _events_encoded_bytes(events: tuple[EffectEvent, ...]) -> int:
    return sum(_event_encoded_bytes(event) for event in events)


def bounded_effect_events(
    events: tuple[EffectEvent, ...],
) -> tuple[EffectEvent, ...]:
    """Retain newest exact events under both count and encoded-byte caps."""

    if not (
        type(events) is tuple
        and all(type(event) is EffectEvent for event in events)
    ):
        raise EffectHistoryValidationError("invalid effect event batch")

    unique: dict[tuple[object, ...], EffectEvent] = {}
    for event in events:
        unique.setdefault(event.identity, event)
    candidates = tuple(sorted(unique.values(), key=_event_sort_key))[:MAX_EFFECT_EVENTS]
    budget = MAX_EFFECT_HISTORY_BYTES - _ENVELOPE_RESERVE_BYTES
    total = 0
    for index, event in enumerate(candidates):
        total += _event_encoded_bytes(event)
        if total > budget:
            return candidates[:index]
    return candidates


@dataclass(frozen=True, slots=True)
class EffectHistory:
    """Newest-first presentation facts plus a monotonic browser watermark."""

    events: tuple[EffectEvent, ...] = ()
    last_seen_epoch: float = 0.0

    def __post_init__(self) -> None:
        if not (
            type(self.events) is tuple
            and all(type(event) is EffectEvent for event in self.events)
            and _finite_nonnegative(self.last_seen_epoch)
        ):
            raise EffectHistoryValidationError("invalid effect history")
        if len(self.events) > MAX_EFFECT_EVENTS:
            raise EffectHistoryValidationError("effect history exceeds event cap")
        if len({event.identity for event in self.events}) != len(self.events):
            raise EffectHistoryValidationError("duplicate exact effect event")
        if (
            _events_encoded_bytes(self.events)
            > MAX_EFFECT_HISTORY_BYTES - _ENVELOPE_RESERVE_BYTES
        ):
            raise EffectHistoryValidationError("effect history exceeds byte cap")
        ordered = tuple(sorted(self.events, key=_event_sort_key))
        if ordered != self.events:
            object.__setattr__(self, "events", ordered)
        object.__setattr__(self, "last_seen_epoch", float(self.last_seen_epoch))

    @property
    def unseen(self) -> tuple[EffectEvent, ...]:
        return tuple(
            event
            for event in self.events
            if event.occurred_at_epoch > self.last_seen_epoch
        )


def record_effect_event(history: EffectHistory, event: EffectEvent) -> EffectHistory:
    """Record one exact fact idempotently while retaining the newest bounds."""

    if type(history) is not EffectHistory or type(event) is not EffectEvent:
        raise EffectHistoryValidationError("invalid effect history record input")
    if any(existing.identity == event.identity for existing in history.events):
        return history
    return EffectHistory(
        bounded_effect_events((event, *history.events)),
        history.last_seen_epoch,
    )


def record_effect_events(
    history: EffectHistory,
    events: tuple[EffectEvent, ...],
) -> EffectHistory:
    """Fold a bounded batch without inventing generic event-stream semantics."""

    if type(history) is not EffectHistory or type(events) is not tuple:
        raise EffectHistoryValidationError("invalid effect history record batch")
    result = history
    for event in events:
        result = record_effect_event(result, event)
    return result


def mark_effect_history_seen(
    history: EffectHistory,
    seen_at_epoch: float,
) -> EffectHistory:
    """Advance the browser watermark, never moving it backwards."""

    if type(history) is not EffectHistory or not _finite_nonnegative(seen_at_epoch):
        raise EffectHistoryValidationError("invalid effect history watermark")
    value = float(seen_at_epoch)
    if value <= history.last_seen_epoch:
        return history
    return EffectHistory(history.events, value)


@dataclass(frozen=True, slots=True)
class EffectBrowserRow:
    """Presentation-ready content-free explanation for the Agent Browser."""

    event_id: str
    occurred_at_epoch: float
    effect_id: str
    semantic_category: EffectSemanticCategory
    surface: EffectSurface
    outcome: EffectOutcome
    explanation: str
    unseen: bool

    def __post_init__(self) -> None:
        if not (
            _opaque_identifier(self.event_id, MAX_EFFECT_EVENT_ID_LENGTH)
            and _finite_nonnegative(self.occurred_at_epoch)
            and _opaque_identifier(self.effect_id, MAX_EFFECT_ID_LENGTH)
            and type(self.semantic_category) is EffectSemanticCategory
            and type(self.surface) is EffectSurface
            and type(self.outcome) is EffectOutcome
            and type(self.explanation) is str
            and 1 <= len(self.explanation) <= 160
            and self.explanation.isprintable()
            and type(self.unseen) is bool
        ):
            raise EffectHistoryValidationError("invalid effect browser row")
        object.__setattr__(self, "occurred_at_epoch", float(self.occurred_at_epoch))


def _event_explanation(event: EffectEvent) -> str:
    surface = _SURFACE_LABELS[event.surface]
    if event.outcome is EffectOutcome.SHOWN:
        return f"Shown on {surface} for {_CATEGORY_LABELS[event.semantic_category]}."
    if event.outcome is EffectOutcome.SUPPRESSED:
        assert event.suppression_reason is not None
        return (
            f"Suppressed on {surface} by "
            f"{_SUPPRESSION_LABELS[event.suppression_reason]}."
        )
    if event.outcome is EffectOutcome.ACKNOWLEDGED:
        assert event.acknowledgement_source is not None
        return (
            f"Acknowledged from "
            f"{_ACKNOWLEDGEMENT_LABELS[event.acknowledgement_source]} "
            f"for {surface}."
        )
    return f"Expired before {surface} could present it."


def project_effect_history(history: EffectHistory) -> tuple[EffectBrowserRow, ...]:
    """Project exact history into stable browser rows without adding content."""

    if type(history) is not EffectHistory:
        raise EffectHistoryValidationError("invalid effect history projection input")
    return tuple(
        EffectBrowserRow(
            event_id=event.event_id,
            occurred_at_epoch=event.occurred_at_epoch,
            effect_id=event.effect_id,
            semantic_category=event.semantic_category,
            surface=event.surface,
            outcome=event.outcome,
            explanation=_event_explanation(event),
            unseen=event.occurred_at_epoch > history.last_seen_epoch,
        )
        for event in history.events
    )


__all__ = [
    "EFFECT_EVENT_VERSION",
    "MAX_EFFECT_EVENTS",
    "MAX_EFFECT_HISTORY_BYTES",
    "EffectAcknowledgementSource",
    "EffectBrowserRow",
    "EffectEvent",
    "EffectHistory",
    "EffectHistoryValidationError",
    "EffectOutcome",
    "EffectSemanticCategory",
    "EffectSuppressionReason",
    "EffectSurface",
    "bounded_effect_events",
    "effect_event_to_payload",
    "mark_effect_history_seen",
    "project_effect_history",
    "record_effect_event",
    "record_effect_events",
]
