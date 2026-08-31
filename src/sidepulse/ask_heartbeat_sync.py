"""Pure cadence synchronization for simultaneous actionable asks.

Ask Heartbeat Sync phase-locks light presentations that arrive in one short,
anchored burst.  It never combines request routing, answer state, or content:
every canonical announcer identity remains a distinct immutable member for the
Screen Bar and alert stack to identify.

This module owns no timers, rendering, firmware, controller state, or I/O.
Callers supply already-canonical presentation facts and consume a bounded plan.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .accessibility_display import AccessibilityDisplayPreferences
from .announcer_stack import AnnouncerAlertIdentity

ASK_HEARTBEAT_BURST_SECONDS: Final = 2.0
MAX_ASK_HEARTBEAT_PRESENTATIONS: Final = 64

# This is a protocol signature, not a user-selectable provider animation.  The
# exact name keeps it distinct from the general-purpose ``heartbeat`` motion.
ASK_HEARTBEAT_SIGNATURE: Final = "sidepulse.ask-heartbeat-sync:v1"

# One familiar lub-dub, followed by a long rest.  Two onsets in one second is
# the absolute rate, so the reserved cadence stays at the existing 2 Hz limit.
ASK_HEARTBEAT_PULSE_ON_SECONDS: Final = 0.18
ASK_HEARTBEAT_INTER_PULSE_SECONDS: Final = 0.12
ASK_HEARTBEAT_REST_SECONDS: Final = 0.52
ASK_HEARTBEAT_CYCLE_SECONDS: Final = 1.0
ASK_HEARTBEAT_PULSE_COUNT: Final = 2

_MAX_ESCALATION_STAGE: Final = 3


class AskHeartbeatValidationError(ValueError):
    """An unsafe or non-canonical value crossed the planning boundary."""


class AskHeartbeatPresentationMode(str, Enum):
    SYNCED_HEARTBEAT = "synced_heartbeat"
    STATIC_ATTENTION = "static_attention"


def _finite_nonnegative(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and value >= 0.0
    )


def _digest(*parts: str) -> str:
    encoded = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, order=True, slots=True)
class AskHeartbeatSyncIdentity:
    """Opaque identity for one phase-locked cadence cohort."""

    value: str

    def __post_init__(self) -> None:
        if not (
            type(self.value) is str
            and self.value.startswith("ask-heartbeat-sync:v1:")
            and len(self.value) == len("ask-heartbeat-sync:v1:") + 64
            and all(character in "0123456789abcdef" for character in self.value[-64:])
        ):
            raise AskHeartbeatValidationError("invalid ask heartbeat sync identity")


@dataclass(frozen=True, order=True, slots=True)
class AskHeartbeatPresentationIdentity:
    """Exact identity for one request presentation, never an answer route."""

    value: str

    def __post_init__(self) -> None:
        if not (
            type(self.value) is str
            and self.value.startswith("ask-heartbeat-presentation:v1:")
            and len(self.value) == len("ask-heartbeat-presentation:v1:") + 64
            and all(character in "0123456789abcdef" for character in self.value[-64:])
        ):
            raise AskHeartbeatValidationError(
                "invalid ask heartbeat presentation identity"
            )


@dataclass(frozen=True, slots=True)
class AskHeartbeatPresentation:
    """A content-free request edge eligible to share a light cadence."""

    request_identity: AnnouncerAlertIdentity
    presented_at_epoch: float
    escalation_stage: int = 0

    def __post_init__(self) -> None:
        if type(self.request_identity) is not AnnouncerAlertIdentity:
            raise AskHeartbeatValidationError("invalid canonical request identity")
        if not _finite_nonnegative(self.presented_at_epoch):
            raise AskHeartbeatValidationError("invalid ask presentation time")
        if not (
            type(self.escalation_stage) is int
            and 0 <= self.escalation_stage <= _MAX_ESCALATION_STAGE
        ):
            raise AskHeartbeatValidationError("invalid ask escalation stage")
        object.__setattr__(self, "presented_at_epoch", float(self.presented_at_epoch))

    @property
    def identity(self) -> AskHeartbeatPresentationIdentity:
        digest = _digest(
            ASK_HEARTBEAT_SIGNATURE,
            self.request_identity.value,
            self.presented_at_epoch.hex(),
            str(self.escalation_stage),
        )
        return AskHeartbeatPresentationIdentity(
            f"ask-heartbeat-presentation:v1:{digest}"
        )


@dataclass(frozen=True, slots=True)
class AskHeartbeatCadence:
    """Reserved, bounded output semantics for one synchronization plan."""

    signature: str
    mode: AskHeartbeatPresentationMode
    pulse_count: int
    pulse_on_seconds: float
    inter_pulse_seconds: float
    rest_seconds: float
    cycle_seconds: float | None

    def __post_init__(self) -> None:
        cycle = self.cycle_seconds
        if not (
            self.signature == ASK_HEARTBEAT_SIGNATURE
            and type(self.mode) is AskHeartbeatPresentationMode
            and type(self.pulse_count) is int
            and 0 <= self.pulse_count <= ASK_HEARTBEAT_PULSE_COUNT
            and _finite_nonnegative(self.pulse_on_seconds)
            and _finite_nonnegative(self.inter_pulse_seconds)
            and _finite_nonnegative(self.rest_seconds)
            and (cycle is None or (_finite_nonnegative(cycle) and cycle >= 1.0))
        ):
            raise AskHeartbeatValidationError("invalid ask heartbeat cadence")
        if self.mode is AskHeartbeatPresentationMode.STATIC_ATTENTION:
            if not (
                self.pulse_count == 0
                and cycle is None
                and float(self.pulse_on_seconds) == 0.0
                and float(self.inter_pulse_seconds) == 0.0
                and float(self.rest_seconds) == 0.0
            ):
                raise AskHeartbeatValidationError(
                    "static ask attention cannot retain a motion cycle"
                )
        elif cycle is None or not (
            self.pulse_count == ASK_HEARTBEAT_PULSE_COUNT
            and float(self.pulse_on_seconds) == ASK_HEARTBEAT_PULSE_ON_SECONDS
            and float(self.inter_pulse_seconds)
            == ASK_HEARTBEAT_INTER_PULSE_SECONDS
            and float(self.rest_seconds) == ASK_HEARTBEAT_REST_SECONDS
            and float(cycle) == ASK_HEARTBEAT_CYCLE_SECONDS
            and self.pulse_count / float(cycle) <= 2.0
        ):
            raise AskHeartbeatValidationError(
                "synchronized ask cadence must use the reserved safe signature"
            )
        object.__setattr__(self, "pulse_on_seconds", float(self.pulse_on_seconds))
        object.__setattr__(
            self, "inter_pulse_seconds", float(self.inter_pulse_seconds)
        )
        object.__setattr__(self, "rest_seconds", float(self.rest_seconds))
        object.__setattr__(
            self,
            "cycle_seconds",
            None if cycle is None else float(cycle),
        )


_MOTION_CADENCE: Final = AskHeartbeatCadence(
    signature=ASK_HEARTBEAT_SIGNATURE,
    mode=AskHeartbeatPresentationMode.SYNCED_HEARTBEAT,
    pulse_count=ASK_HEARTBEAT_PULSE_COUNT,
    pulse_on_seconds=ASK_HEARTBEAT_PULSE_ON_SECONDS,
    inter_pulse_seconds=ASK_HEARTBEAT_INTER_PULSE_SECONDS,
    rest_seconds=ASK_HEARTBEAT_REST_SECONDS,
    cycle_seconds=ASK_HEARTBEAT_CYCLE_SECONDS,
)
_STATIC_CADENCE: Final = AskHeartbeatCadence(
    signature=ASK_HEARTBEAT_SIGNATURE,
    mode=AskHeartbeatPresentationMode.STATIC_ATTENTION,
    pulse_count=0,
    pulse_on_seconds=0.0,
    inter_pulse_seconds=0.0,
    rest_seconds=0.0,
    cycle_seconds=None,
)


@dataclass(frozen=True, slots=True)
class AskHeartbeatMember:
    """One exact request retained inside a shared cadence cohort."""

    request_identity: AnnouncerAlertIdentity
    presentation_identity: AskHeartbeatPresentationIdentity
    presented_at_epoch: float
    escalation_stage: int

    def __post_init__(self) -> None:
        if not (
            type(self.request_identity) is AnnouncerAlertIdentity
            and type(self.presentation_identity) is AskHeartbeatPresentationIdentity
            and _finite_nonnegative(self.presented_at_epoch)
            and type(self.escalation_stage) is int
            and 0 <= self.escalation_stage <= _MAX_ESCALATION_STAGE
        ):
            raise AskHeartbeatValidationError("invalid ask heartbeat member")
        object.__setattr__(self, "presented_at_epoch", float(self.presented_at_epoch))


@dataclass(frozen=True, slots=True)
class AskHeartbeatCohort:
    """One cadence anchor and its still-distinct request presentations."""

    sync_identity: AskHeartbeatSyncIdentity
    anchored_at_epoch: float
    escalation_stage: int
    members: tuple[AskHeartbeatMember, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.sync_identity) is AskHeartbeatSyncIdentity
            and _finite_nonnegative(self.anchored_at_epoch)
            and type(self.escalation_stage) is int
            and 0 <= self.escalation_stage <= _MAX_ESCALATION_STAGE
            and type(self.members) is tuple
            and self.members
            and all(type(member) is AskHeartbeatMember for member in self.members)
            and all(
                member.escalation_stage == self.escalation_stage
                for member in self.members
            )
            and all(
                member.presented_at_epoch >= float(self.anchored_at_epoch)
                for member in self.members
            )
            and len({member.request_identity for member in self.members})
            == len(self.members)
        ):
            raise AskHeartbeatValidationError("invalid ask heartbeat cohort")
        object.__setattr__(self, "anchored_at_epoch", float(self.anchored_at_epoch))

    @property
    def request_identities(self) -> tuple[AnnouncerAlertIdentity, ...]:
        return tuple(member.request_identity for member in self.members)


@dataclass(frozen=True, slots=True)
class AskHeartbeatPlan:
    """Immutable light-only plan with explicit accessible fallback semantics."""

    cohorts: tuple[AskHeartbeatCohort, ...]
    cadence: AskHeartbeatCadence
    request_count: int
    accessibility_label: str
    accessibility_value: str
    accessibility_help: str

    def __post_init__(self) -> None:
        if not (
            type(self.cohorts) is tuple
            and all(type(cohort) is AskHeartbeatCohort for cohort in self.cohorts)
        ):
            raise AskHeartbeatValidationError("invalid ask heartbeat plan")
        member_count = sum(len(cohort.members) for cohort in self.cohorts)
        all_members = tuple(member for cohort in self.cohorts for member in cohort.members)
        if not (
            len({cohort.sync_identity for cohort in self.cohorts})
            == len(self.cohorts)
            and type(self.cadence) is AskHeartbeatCadence
            and type(self.request_count) is int
            and self.request_count == member_count
            and len({member.request_identity for member in all_members})
            == member_count
            and all(
                type(text) is str and text.isprintable() and text
                for text in (
                    self.accessibility_label,
                    self.accessibility_value,
                    self.accessibility_help,
                )
            )
        ):
            raise AskHeartbeatValidationError("invalid ask heartbeat plan")

    @property
    def request_identities(self) -> tuple[AnnouncerAlertIdentity, ...]:
        return tuple(
            member.request_identity
            for cohort in self.cohorts
            for member in cohort.members
        )


def _sync_identity(anchor: AskHeartbeatPresentation) -> AskHeartbeatSyncIdentity:
    # The anchor alone owns phase.  Adding another ask inside its burst does not
    # rename or restart the cadence, while a new stage intentionally does.
    digest = _digest(
        ASK_HEARTBEAT_SIGNATURE,
        anchor.request_identity.value,
        anchor.presented_at_epoch.hex(),
        str(anchor.escalation_stage),
    )
    return AskHeartbeatSyncIdentity(f"ask-heartbeat-sync:v1:{digest}")


def _member(presentation: AskHeartbeatPresentation) -> AskHeartbeatMember:
    return AskHeartbeatMember(
        request_identity=presentation.request_identity,
        presentation_identity=presentation.identity,
        presented_at_epoch=presentation.presented_at_epoch,
        escalation_stage=presentation.escalation_stage,
    )


def _cohorts(
    presentations: tuple[AskHeartbeatPresentation, ...],
    burst_window_seconds: float,
) -> tuple[AskHeartbeatCohort, ...]:
    ordered = sorted(
        presentations,
        key=lambda item: (
            item.presented_at_epoch,
            item.escalation_stage,
            item.request_identity.value,
        ),
    )
    mutable: list[tuple[AskHeartbeatPresentation, list[AskHeartbeatPresentation]]] = []
    for presentation in ordered:
        matching = next(
            (
                cohort
                for cohort in reversed(mutable)
                if cohort[0].escalation_stage == presentation.escalation_stage
                and presentation.presented_at_epoch - cohort[0].presented_at_epoch
                < burst_window_seconds
            ),
            None,
        )
        if matching is None:
            mutable.append((presentation, [presentation]))
            continue

        members = matching[1]
        if all(
            member.request_identity != presentation.request_identity
            for member in members
        ):
            members.append(presentation)

    return tuple(
        AskHeartbeatCohort(
            sync_identity=_sync_identity(anchor),
            anchored_at_epoch=anchor.presented_at_epoch,
            escalation_stage=anchor.escalation_stage,
            members=tuple(_member(member) for member in members),
        )
        for anchor, members in mutable
    )


def _accessible_text(
    request_count: int,
    cohort_count: int,
    mode: AskHeartbeatPresentationMode,
) -> tuple[str, str, str]:
    if request_count == 0:
        return (
            "Ask heartbeat",
            "No asking sessions need attention.",
            "Screen Bar and the alert stack identify sessions when input is needed.",
        )

    ask_noun = "ask" if request_count == 1 else "asks"
    group_noun = "group" if cohort_count == 1 else "groups"
    if mode is AskHeartbeatPresentationMode.STATIC_ATTENTION:
        value = (
            f"{request_count} {ask_noun} use static attention across "
            f"{cohort_count} synchronized {group_noun} because Reduce Motion is on."
        )
    else:
        value = (
            f"{request_count} {ask_noun} share the reserved safe heartbeat across "
            f"{cohort_count} synchronized {group_noun}."
        )
    return (
        "Synchronized ask attention",
        value,
        "Open Screen Bar or the alert stack to identify and answer each asking session.",
    )


def plan_ask_heartbeat_sync(
    presentations: Iterable[AskHeartbeatPresentation],
    *,
    accessibility_preferences: AccessibilityDisplayPreferences,
    burst_window_seconds: float = ASK_HEARTBEAT_BURST_SECONDS,
) -> AskHeartbeatPlan:
    """Coalesce light timing while retaining every canonical request identity.

    The burst window is anchored to its first presentation and never extends as
    later asks arrive.  Escalation stages form separate cohorts so a stage
    change can deliberately re-announce without inheriting stale phase.
    """

    if not (
        type(accessibility_preferences) is AccessibilityDisplayPreferences
        and type(accessibility_preferences.reduce_motion) is bool
    ):
        raise AskHeartbeatValidationError("invalid accessibility preferences")
    if not (
        _finite_nonnegative(burst_window_seconds)
        and 0.1 <= float(burst_window_seconds) <= 10.0
    ):
        raise AskHeartbeatValidationError("invalid ask heartbeat burst window")

    items = tuple(presentations)
    if not (
        len(items) <= MAX_ASK_HEARTBEAT_PRESENTATIONS
        and all(type(item) is AskHeartbeatPresentation for item in items)
    ):
        raise AskHeartbeatValidationError("invalid ask heartbeat presentations")

    cohorts = _cohorts(items, float(burst_window_seconds))
    # The same active request must never be represented in two light cohorts in
    # one plan.  Stage transitions are separate presentation plans, not mixed
    # historical input.
    identities = tuple(
        member.request_identity
        for cohort in cohorts
        for member in cohort.members
    )
    if len(set(identities)) != len(identities):
        raise AskHeartbeatValidationError(
            "one request cannot occupy multiple ask heartbeat cohorts"
        )

    cadence = (
        _STATIC_CADENCE
        if accessibility_preferences.reduce_motion
        else _MOTION_CADENCE
    )
    label, value, help_text = _accessible_text(
        len(identities), len(cohorts), cadence.mode
    )
    return AskHeartbeatPlan(
        cohorts=cohorts,
        cadence=cadence,
        request_count=len(identities),
        accessibility_label=label,
        accessibility_value=value,
        accessibility_help=help_text,
    )


__all__ = [
    "ASK_HEARTBEAT_BURST_SECONDS",
    "ASK_HEARTBEAT_CYCLE_SECONDS",
    "ASK_HEARTBEAT_INTER_PULSE_SECONDS",
    "ASK_HEARTBEAT_PULSE_COUNT",
    "ASK_HEARTBEAT_PULSE_ON_SECONDS",
    "ASK_HEARTBEAT_REST_SECONDS",
    "ASK_HEARTBEAT_SIGNATURE",
    "MAX_ASK_HEARTBEAT_PRESENTATIONS",
    "AskHeartbeatCadence",
    "AskHeartbeatCohort",
    "AskHeartbeatMember",
    "AskHeartbeatPlan",
    "AskHeartbeatPresentation",
    "AskHeartbeatPresentationIdentity",
    "AskHeartbeatPresentationMode",
    "AskHeartbeatSyncIdentity",
    "AskHeartbeatValidationError",
    "plan_ask_heartbeat_sync",
]
