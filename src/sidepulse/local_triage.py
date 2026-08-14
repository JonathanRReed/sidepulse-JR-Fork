"""Pure, exact-key local acknowledgement state.

Local acknowledgement is SidePulse-owned triage. It never mutates provider or
canonical operator truth.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .operator_state import (
    AcknowledgementEligibility,
    CanonicalRequestTruth,
    RequestPhase,
)
from .provider_facts import RequestKey

MAX_LOCAL_ACKNOWLEDGEMENTS: Final = 512


class LocalTriageValidationError(ValueError):
    """A local triage mutation failed closed."""


@dataclass(frozen=True, slots=True)
class LocalAcknowledgement:
    request_key: RequestKey
    acknowledged_at: float

    def __post_init__(self) -> None:
        if type(self.request_key) is not RequestKey or not _valid_epoch(
            self.acknowledged_at
        ):
            raise LocalTriageValidationError("invalid local acknowledgement")
        object.__setattr__(self, "acknowledged_at", float(self.acknowledged_at))


@dataclass(frozen=True, slots=True)
class LocalTriageState:
    acknowledgements: tuple[LocalAcknowledgement, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.acknowledgements) is tuple
            and len(self.acknowledgements) <= MAX_LOCAL_ACKNOWLEDGEMENTS
            and all(
                isinstance(item, LocalAcknowledgement) for item in self.acknowledgements
            )
            and len({item.request_key for item in self.acknowledgements})
            == len(self.acknowledgements)
        ):
            raise LocalTriageValidationError("invalid local triage state")


class LocalTriageMutationKind(str, Enum):
    ACKNOWLEDGE = "acknowledge"
    RESUME_ESCALATION = "resume-escalation"


def apply_local_triage_mutation(
    state: LocalTriageState,
    *,
    request: CanonicalRequestTruth,
    mutation: LocalTriageMutationKind,
    now: float,
) -> LocalTriageState:
    """Apply one exact, execution-time-validated local triage mutation."""
    if type(state) is not LocalTriageState:
        raise LocalTriageValidationError("invalid local triage state")
    if type(request) is not CanonicalRequestTruth:
        raise LocalTriageValidationError("invalid canonical request")
    if type(mutation) is not LocalTriageMutationKind:
        raise LocalTriageValidationError("invalid local triage mutation")
    if not _valid_epoch(now):
        raise LocalTriageValidationError("invalid local triage time")

    existing = next(
        (
            item
            for item in state.acknowledgements
            if item.request_key == request.key
        ),
        None,
    )
    if mutation is LocalTriageMutationKind.RESUME_ESCALATION:
        if existing is None:
            return state
        return LocalTriageState(
            tuple(item for item in state.acknowledgements if item is not existing)
        )

    if not (
        request.phase is RequestPhase.LIVE_UNACKNOWLEDGED
        and request.acknowledgement_eligibility
        is AcknowledgementEligibility.ELIGIBLE
    ):
        raise LocalTriageValidationError("request is not eligible for acknowledgement")
    if existing is not None:
        return state
    if len(state.acknowledgements) >= MAX_LOCAL_ACKNOWLEDGEMENTS:
        raise LocalTriageValidationError("local triage capacity reached")
    return LocalTriageState(
        (
            *state.acknowledgements,
            LocalAcknowledgement(request.key, float(now)),
        )
    )


def reconcile_local_triage(
    state: LocalTriageState,
    requests: Iterable[CanonicalRequestTruth],
) -> LocalTriageState:
    """Prune only exact requests with established terminal canonical truth."""
    if type(state) is not LocalTriageState:
        raise LocalTriageValidationError("invalid local triage state")
    try:
        iterator = iter(requests)
    except TypeError as error:
        raise LocalTriageValidationError("invalid canonical requests") from error

    phase_by_key: dict[RequestKey, RequestPhase] = {}
    for request in iterator:
        if type(request) is not CanonicalRequestTruth:
            raise LocalTriageValidationError("invalid canonical requests")
        existing = phase_by_key.get(request.key)
        if existing is not None:
            raise LocalTriageValidationError("duplicate canonical request")
        phase_by_key[request.key] = request.phase

    retained = tuple(
        item
        for item in state.acknowledgements
        if phase_by_key.get(item.request_key)
        not in {RequestPhase.RESOLVED, RequestPhase.UNKNOWN_EXPIRED}
    )
    return state if retained == state.acknowledgements else LocalTriageState(retained)


def _valid_epoch(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and float(value) >= 0.0
    )
