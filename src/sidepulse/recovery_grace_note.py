"""Pure planning for one restrained, deduplicated source-recovery cue.

The planner accepts an already-confirmed canonical recovery edge. It does not
infer recovery from current health, poll a source, retain a timer, render an
effect, or write to a device. Runtime owners supply DND, courtesy, finite-cue,
and Reduce Motion facts and consume one immutable presentation plan.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .capacity_types import SourceKey
from .dnd_policy import DisplayAdmission
from .operator_state import (
    TIMING_RECOVERY_CONFIRMATIONS,
    CanonicalOperatorEvent,
    InterruptionClass,
    TransitionKind,
)
from .provider_facts import ProviderWatermark, SourceFreshness, SourceHealth
from .semantic_effect_router import CourtesySuppression

MAX_PRESENTED_RECOVERY_IDENTITIES: Final = 256
RECOVERY_WIPE_DURATION_SECONDS: Final = 1.2
RECOVERY_STATIC_DURATION_SECONDS: Final = 0.8


class RecoveryGraceDisposition(str, Enum):
    """Whether the recovery is animated, static, or withheld."""

    EMIT = "emit"
    STATIC = "static"
    SUPPRESS = "suppress"


class RecoveryGracePresentation(str, Enum):
    """The renderer-facing presentation language for the recovery moment."""

    NONE = "none"
    RESTRAINED_WIPE = "restrained_wipe"
    STATIC_HIGHLIGHT = "static_highlight"


class RecoveryGraceSuppressionReason(str, Enum):
    """Content-free reasons a confirmed recovery cue did not appear."""

    DUPLICATE = "duplicate"
    DND = "dnd"
    COURTESY_FOCUS = "courtesy_focus"
    COURTESY_SNOOZE = "courtesy_snooze"
    COURTESY_BUDGET = "courtesy_budget"
    FINITE_CUE_UNAVAILABLE = "finite_cue_unavailable"


@dataclass(frozen=True, slots=True)
class RecoveryGraceIdentity:
    """One source-level recovery identity shared by every affected work row."""

    watermark: ProviderWatermark

    def __post_init__(self) -> None:
        if type(self.watermark) is not ProviderWatermark:
            raise ValueError("recovery grace identity requires a provider watermark")

    @property
    def source_key(self) -> SourceKey:
        return self.watermark.source_key


@dataclass(frozen=True, slots=True)
class ConfirmedRecoveryEvidence:
    """Explicit canonical proof that one unhealthy source is healthy again.

    The canonical reducer owns recovery truth. This boundary accepts only its
    fresh ``SOURCE_RECOVERED`` courtesy edge plus the existing two-confirmation
    count. Current health alone can never manufacture a recovery cue here.
    """

    event: CanonicalOperatorEvent
    previous_health: SourceHealth
    current_health: SourceHealth
    recovery_confirmations: int

    def __post_init__(self) -> None:
        event = self.event
        if not (
            type(event) is CanonicalOperatorEvent
            and event.kind is TransitionKind.SOURCE_RECOVERED
            and event.interruption_class is InterruptionClass.COURTESY
            and event.source_freshness is SourceFreshness.FRESH
            and event.occurred_at_epoch == event.key.provider_watermark.occurred_at_epoch
            and type(self.previous_health) is SourceHealth
            and self.previous_health is not SourceHealth.HEALTHY
            and self.current_health is SourceHealth.HEALTHY
            and type(self.recovery_confirmations) is int
            and self.recovery_confirmations == TIMING_RECOVERY_CONFIRMATIONS
        ):
            raise ValueError("invalid confirmed recovery evidence")

    @property
    def dedupe_identity(self) -> RecoveryGraceIdentity:
        return RecoveryGraceIdentity(self.event.key.provider_watermark)


@dataclass(frozen=True, slots=True)
class RecoveryGracePlan:
    """One internally consistent renderer and accessibility contract."""

    dedupe_identity: RecoveryGraceIdentity
    disposition: RecoveryGraceDisposition
    presentation: RecoveryGracePresentation
    suppression_reason: RecoveryGraceSuppressionReason | None
    repetitions: int
    duration_seconds: float
    returns_to_normal: bool
    consumes_finite_cue: bool
    accessibility_text: str

    def __post_init__(self) -> None:
        if not (
            type(self.dedupe_identity) is RecoveryGraceIdentity
            and type(self.disposition) is RecoveryGraceDisposition
            and type(self.presentation) is RecoveryGracePresentation
            and (
                self.suppression_reason is None
                or type(self.suppression_reason) is RecoveryGraceSuppressionReason
            )
            and type(self.repetitions) is int
            and type(self.duration_seconds) is float
            and type(self.returns_to_normal) is bool
            and type(self.consumes_finite_cue) is bool
            and type(self.accessibility_text) is str
            and 1 <= len(self.accessibility_text) <= 256
            and self.accessibility_text.isprintable()
        ):
            raise ValueError("invalid recovery grace plan")

        expected = {
            RecoveryGraceDisposition.EMIT: (
                RecoveryGracePresentation.RESTRAINED_WIPE,
                None,
                1,
                RECOVERY_WIPE_DURATION_SECONDS,
                True,
                True,
            ),
            RecoveryGraceDisposition.STATIC: (
                RecoveryGracePresentation.STATIC_HIGHLIGHT,
                None,
                1,
                RECOVERY_STATIC_DURATION_SECONDS,
                True,
                True,
            ),
            RecoveryGraceDisposition.SUPPRESS: (
                RecoveryGracePresentation.NONE,
                self.suppression_reason,
                0,
                0.0,
                False,
                False,
            ),
        }[self.disposition]
        actual = (
            self.presentation,
            self.suppression_reason,
            self.repetitions,
            self.duration_seconds,
            self.returns_to_normal,
            self.consumes_finite_cue,
        )
        if actual != expected or (
            self.disposition is RecoveryGraceDisposition.SUPPRESS
            and self.suppression_reason is None
        ):
            raise ValueError("inconsistent recovery grace plan")

    @property
    def emits(self) -> bool:
        return self.disposition is not RecoveryGraceDisposition.SUPPRESS

    @property
    def animated(self) -> bool:
        return self.disposition is RecoveryGraceDisposition.EMIT


_SUPPRESSION_ACCESSIBILITY: Final = {
    RecoveryGraceSuppressionReason.DUPLICATE: (
        "Source recovery cue was already shown."
    ),
    RecoveryGraceSuppressionReason.DND: (
        "Source recovered. The courtesy cue is withheld by Do Not Disturb."
    ),
    RecoveryGraceSuppressionReason.COURTESY_FOCUS: (
        "Source recovered. The courtesy cue is withheld by the active focus policy."
    ),
    RecoveryGraceSuppressionReason.COURTESY_SNOOZE: (
        "Source recovered. The courtesy cue is snoozed."
    ),
    RecoveryGraceSuppressionReason.COURTESY_BUDGET: (
        "Source recovered. The courtesy cue budget is currently exhausted."
    ),
    RecoveryGraceSuppressionReason.FINITE_CUE_UNAVAILABLE: (
        "Source recovered. No finite cue slot is currently available."
    ),
}


def _suppressed(
    identity: RecoveryGraceIdentity,
    reason: RecoveryGraceSuppressionReason,
) -> RecoveryGracePlan:
    return RecoveryGracePlan(
        dedupe_identity=identity,
        disposition=RecoveryGraceDisposition.SUPPRESS,
        presentation=RecoveryGracePresentation.NONE,
        suppression_reason=reason,
        repetitions=0,
        duration_seconds=0.0,
        returns_to_normal=False,
        consumes_finite_cue=False,
        accessibility_text=_SUPPRESSION_ACCESSIBILITY[reason],
    )


def _presented_identity_set(
    value: Collection[RecoveryGraceIdentity],
) -> frozenset[RecoveryGraceIdentity]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Collection):
        raise ValueError("presented recovery identities must be a bounded collection")
    if len(value) > MAX_PRESENTED_RECOVERY_IDENTITIES:
        raise ValueError("presented recovery identities exceed the bounded limit")
    if not all(type(item) is RecoveryGraceIdentity for item in value):
        raise ValueError("presented recovery identities must be typed")
    return frozenset(value)


def plan_recovery_grace_note(
    evidence: ConfirmedRecoveryEvidence,
    *,
    dnd_display_admission: DisplayAdmission,
    courtesy_suppression: CourtesySuppression,
    finite_cue_available: bool,
    reduce_motion: bool,
    presented_identities: Collection[RecoveryGraceIdentity] = (),
) -> RecoveryGracePlan:
    """Plan one recovery moment without rendering, retaining, or replaying it."""

    if type(evidence) is not ConfirmedRecoveryEvidence:
        raise ValueError("recovery grace planning requires confirmed evidence")
    if type(dnd_display_admission) is not DisplayAdmission:
        raise ValueError("recovery grace DND admission must be known")
    if type(courtesy_suppression) is not CourtesySuppression:
        raise ValueError("recovery grace courtesy suppression must be typed")
    if type(finite_cue_available) is not bool or type(reduce_motion) is not bool:
        raise ValueError("recovery grace flags must be booleans")

    identity = evidence.dedupe_identity
    if identity in _presented_identity_set(presented_identities):
        return _suppressed(identity, RecoveryGraceSuppressionReason.DUPLICATE)

    if dnd_display_admission is not DisplayAdmission.ALL:
        return _suppressed(identity, RecoveryGraceSuppressionReason.DND)

    courtesy_reason = (
        RecoveryGraceSuppressionReason.COURTESY_FOCUS
        if courtesy_suppression.focus
        else (
            RecoveryGraceSuppressionReason.COURTESY_SNOOZE
            if courtesy_suppression.snoozed
            else (
                RecoveryGraceSuppressionReason.COURTESY_BUDGET
                if courtesy_suppression.budget_exhausted
                else None
            )
        )
    )
    if courtesy_reason is not None:
        return _suppressed(identity, courtesy_reason)

    if not finite_cue_available:
        return _suppressed(
            identity,
            RecoveryGraceSuppressionReason.FINITE_CUE_UNAVAILABLE,
        )

    if reduce_motion:
        return RecoveryGracePlan(
            dedupe_identity=identity,
            disposition=RecoveryGraceDisposition.STATIC,
            presentation=RecoveryGracePresentation.STATIC_HIGHLIGHT,
            suppression_reason=None,
            repetitions=1,
            duration_seconds=RECOVERY_STATIC_DURATION_SECONDS,
            returns_to_normal=True,
            consumes_finite_cue=True,
            accessibility_text=(
                "Source recovered. A brief static highlight replaces motion, then "
                "normal status returns."
            ),
        )

    return RecoveryGracePlan(
        dedupe_identity=identity,
        disposition=RecoveryGraceDisposition.EMIT,
        presentation=RecoveryGracePresentation.RESTRAINED_WIPE,
        suppression_reason=None,
        repetitions=1,
        duration_seconds=RECOVERY_WIPE_DURATION_SECONDS,
        returns_to_normal=True,
        consumes_finite_cue=True,
        accessibility_text=(
            "Source recovered. A restrained recovery cue will play once, then "
            "normal status returns."
        ),
    )


__all__ = [
    "MAX_PRESENTED_RECOVERY_IDENTITIES",
    "RECOVERY_STATIC_DURATION_SECONDS",
    "RECOVERY_WIPE_DURATION_SECONDS",
    "ConfirmedRecoveryEvidence",
    "RecoveryGraceDisposition",
    "RecoveryGraceIdentity",
    "RecoveryGracePlan",
    "RecoveryGracePresentation",
    "RecoveryGraceSuppressionReason",
    "plan_recovery_grace_note",
]
