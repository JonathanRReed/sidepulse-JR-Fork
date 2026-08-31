"""Pure debounce and presentation planning for remote fleet presence edges.

This module accepts caller-observed, explicitly trusted machine liveness. It
does not discover peers, read a clock, retain global state, persist anything,
render an effect, or write to a device. The caller owns observation and state
storage. This boundary only confirms a stable edge and describes one quiet,
finite endpoint cue.

The first trusted observation establishes a silent baseline. A contradictory
observation starts a candidate transition, and a later observation with the
same exact liveness identity must survive the configured settle window before
the transition becomes true. Returning to the stable state cancels the
candidate without a cue. A bounded post-cue hold prevents a noisy connection
from turning confirmed join and leave edges into repeated alerts.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .dnd_policy import DisplayAdmission
from .semantic_effect_router import CourtesySuppression

DEFAULT_JOIN_SETTLE_SECONDS: Final = 2.0
DEFAULT_DEPARTURE_SETTLE_SECONDS: Final = 5.0
DEFAULT_CUE_HOLD_SECONDS: Final = 20.0
MAX_SETTLE_SECONDS: Final = 30.0
MAX_CUE_HOLD_SECONDS: Final = 5 * 60.0
MAX_IDENTITY_LENGTH: Final = 128
WINK_DURATION_MS: Final = 650
STATIC_HIGHLIGHT_DURATION_MS: Final = 700

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class RemoteMachineTrust(str, Enum):
    """Whether the upstream boundary authenticated the observation source."""

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class RemoteMachineLiveness(str, Enum):
    """The two explicit presence facts accepted by the debounce boundary."""

    ONLINE = "online"
    OFFLINE = "offline"


class FleetPresenceTransition(str, Enum):
    """A confirmed change from the previous stable presence fact."""

    ARRIVAL = "arrival"
    DEPARTURE = "departure"


class FleetEndpointRole(str, Enum):
    """The semantic edge that receives the quiet cue."""

    ARRIVAL_ENDPOINT = "arrival_endpoint"
    DEPARTURE_ENDPOINT = "departure_endpoint"


class FleetCueDisposition(str, Enum):
    """Whether a confirmed transition is animated, static, or withheld."""

    WINK = "wink"
    STATIC = "static"
    SUPPRESS = "suppress"


class FleetCueSuppressionReason(str, Enum):
    """Content-free reasons a confirmed fleet cue did not appear."""

    HOLD_WINDOW = "hold_window"
    DND = "dnd"
    COURTESY_FOCUS = "courtesy_focus"
    COURTESY_SNOOZE = "courtesy_snooze"
    COURTESY_BUDGET = "courtesy_budget"
    FINITE_CUE_UNAVAILABLE = "finite_cue_unavailable"


class FleetObservationDisposition(str, Enum):
    """How one observation changed the pure debounce state."""

    BASELINED = "baselined"
    STABLE = "stable"
    SETTLING = "settling"
    FLAP_DEBOUNCED = "flap_debounced"
    TRANSITION_CONFIRMED = "transition_confirmed"
    REFUSED = "refused"


class FleetObservationRefusal(str, Enum):
    """Why an observation was ignored without changing trusted state."""

    UNTRUSTED = "untrusted"
    MACHINE_IDENTITY_MISMATCH = "machine_identity_mismatch"
    OUT_OF_ORDER = "out_of_order"
    CONFLICTING_TIMESTAMP = "conflicting_timestamp"


def _validated_identity(value: object, *, field: str) -> str:
    if type(value) is not str or _IDENTITY.fullmatch(value) is None:
        raise ValueError(f"{field} must be a bounded opaque identity")
    return value


def _finite_nonnegative(value: object, *, field: str) -> float:
    if (
        type(value) not in {int, float}
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise ValueError(f"{field} must be a finite nonnegative number")
    return float(value)


def _bounded_positive(value: object, *, field: str, maximum: float) -> float:
    normalized = _finite_nonnegative(value, field=field)
    if not 0.0 < normalized <= maximum:
        raise ValueError(f"{field} must be positive and within its bound")
    return normalized


@dataclass(frozen=True, slots=True)
class TrustedRemoteMachineLiveness:
    """One content-free liveness observation from an upstream trust owner.

    ``liveness_identity`` identifies the exact online or offline episode. It
    must remain stable across the repeated observations used to confirm that
    episode. A changed identity restarts settlement instead of borrowing time
    from a different connection attempt.
    """

    machine_identity: str
    liveness_identity: str
    liveness: RemoteMachineLiveness
    observed_at: float
    trust: RemoteMachineTrust = RemoteMachineTrust.TRUSTED

    def __post_init__(self) -> None:
        _validated_identity(self.machine_identity, field="machine identity")
        _validated_identity(self.liveness_identity, field="liveness identity")
        if type(self.liveness) is not RemoteMachineLiveness:
            raise ValueError("remote machine liveness must be known")
        if type(self.trust) is not RemoteMachineTrust:
            raise ValueError("remote machine trust must be known")
        object.__setattr__(
            self,
            "observed_at",
            _finite_nonnegative(self.observed_at, field="observation time"),
        )


@dataclass(frozen=True, slots=True)
class FleetArrivalDeparturePolicy:
    """Bounded stability and quiet-period policy for one machine."""

    join_settle_seconds: float = DEFAULT_JOIN_SETTLE_SECONDS
    departure_settle_seconds: float = DEFAULT_DEPARTURE_SETTLE_SECONDS
    cue_hold_seconds: float = DEFAULT_CUE_HOLD_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "join_settle_seconds",
            _bounded_positive(
                self.join_settle_seconds,
                field="join settle window",
                maximum=MAX_SETTLE_SECONDS,
            ),
        )
        object.__setattr__(
            self,
            "departure_settle_seconds",
            _bounded_positive(
                self.departure_settle_seconds,
                field="departure settle window",
                maximum=MAX_SETTLE_SECONDS,
            ),
        )
        object.__setattr__(
            self,
            "cue_hold_seconds",
            _bounded_positive(
                self.cue_hold_seconds,
                field="cue hold window",
                maximum=MAX_CUE_HOLD_SECONDS,
            ),
        )

    def settle_seconds_for(self, liveness: RemoteMachineLiveness) -> float:
        if type(liveness) is not RemoteMachineLiveness:
            raise ValueError("settle liveness must be known")
        if liveness is RemoteMachineLiveness.ONLINE:
            return self.join_settle_seconds
        return self.departure_settle_seconds


@dataclass(frozen=True, slots=True)
class FleetArrivalDepartureIdentity:
    """Exact, stable identity for one confirmed fleet-presence edge."""

    machine_identity: str
    liveness_identity: str
    transition: FleetPresenceTransition

    def __post_init__(self) -> None:
        _validated_identity(self.machine_identity, field="machine identity")
        _validated_identity(self.liveness_identity, field="liveness identity")
        if type(self.transition) is not FleetPresenceTransition:
            raise ValueError("fleet transition must be known")


@dataclass(frozen=True, slots=True)
class FleetArrivalDepartureState:
    """Caller-retained pure debounce state for exactly one machine."""

    machine_identity: str
    stable_liveness: RemoteMachineLiveness
    stable_liveness_identity: str
    last_observed_at: float
    candidate_liveness: RemoteMachineLiveness | None = None
    candidate_liveness_identity: str | None = None
    candidate_since: float | None = None
    cue_hold_until: float = 0.0
    last_cue_identity: FleetArrivalDepartureIdentity | None = None

    def __post_init__(self) -> None:
        _validated_identity(self.machine_identity, field="machine identity")
        _validated_identity(
            self.stable_liveness_identity,
            field="stable liveness identity",
        )
        if type(self.stable_liveness) is not RemoteMachineLiveness:
            raise ValueError("stable liveness must be known")
        last_observed = _finite_nonnegative(
            self.last_observed_at,
            field="last observation time",
        )
        hold_until = _finite_nonnegative(
            self.cue_hold_until,
            field="cue hold boundary",
        )
        object.__setattr__(self, "last_observed_at", last_observed)
        object.__setattr__(self, "cue_hold_until", hold_until)

        candidate_values = (
            self.candidate_liveness,
            self.candidate_liveness_identity,
            self.candidate_since,
        )
        if all(value is None for value in candidate_values):
            pass
        elif any(value is None for value in candidate_values):
            raise ValueError("fleet candidate state must be complete or absent")
        else:
            if type(self.candidate_liveness) is not RemoteMachineLiveness:
                raise ValueError("candidate liveness must be known")
            if self.candidate_liveness is self.stable_liveness:
                raise ValueError("candidate liveness must differ from stable liveness")
            _validated_identity(
                self.candidate_liveness_identity,
                field="candidate liveness identity",
            )
            candidate_since = _finite_nonnegative(
                self.candidate_since,
                field="candidate start time",
            )
            if candidate_since > last_observed:
                raise ValueError("candidate start cannot follow the last observation")
            object.__setattr__(self, "candidate_since", candidate_since)

        if (
            self.last_cue_identity is not None
            and type(self.last_cue_identity) is not FleetArrivalDepartureIdentity
        ):
            raise ValueError("last fleet cue identity must be typed")
        if (
            self.last_cue_identity is not None
            and self.last_cue_identity.machine_identity != self.machine_identity
        ):
            raise ValueError("last fleet cue must belong to the same machine")


@dataclass(frozen=True, slots=True)
class FleetArrivalDepartureAccessibility:
    """A content-free, non-color account of one fleet-presence edge."""

    label: str
    value: str
    announcement: str

    def __post_init__(self) -> None:
        for field, value in (
            ("accessibility label", self.label),
            ("accessibility value", self.value),
            ("accessibility announcement", self.announcement),
        ):
            if (
                type(value) is not str
                or not value
                or len(value) > 160
                or not value.isprintable()
            ):
                raise ValueError(f"{field} must be bounded printable text")


@dataclass(frozen=True, slots=True)
class FleetArrivalDepartureCue:
    """One internally consistent finite presentation or suppression plan."""

    identity: FleetArrivalDepartureIdentity
    endpoint_role: FleetEndpointRole
    disposition: FleetCueDisposition
    suppression_reason: FleetCueSuppressionReason | None
    duration_ms: int
    passes: int
    loops: int
    returns_to_baseline: bool
    accessibility: FleetArrivalDepartureAccessibility

    def __post_init__(self) -> None:
        if type(self.identity) is not FleetArrivalDepartureIdentity:
            raise ValueError("fleet cue identity must be typed")
        expected_endpoint = (
            FleetEndpointRole.ARRIVAL_ENDPOINT
            if self.identity.transition is FleetPresenceTransition.ARRIVAL
            else FleetEndpointRole.DEPARTURE_ENDPOINT
        )
        if self.endpoint_role is not expected_endpoint:
            raise ValueError("fleet cue endpoint must match its transition")
        if type(self.disposition) is not FleetCueDisposition:
            raise ValueError("fleet cue disposition must be known")
        if type(self.accessibility) is not FleetArrivalDepartureAccessibility:
            raise ValueError("fleet cue accessibility must be typed")
        expected = {
            FleetCueDisposition.WINK: (None, WINK_DURATION_MS, 1, 0, True),
            FleetCueDisposition.STATIC: (
                None,
                STATIC_HIGHLIGHT_DURATION_MS,
                1,
                0,
                True,
            ),
            FleetCueDisposition.SUPPRESS: (
                self.suppression_reason,
                0,
                0,
                0,
                False,
            ),
        }[self.disposition]
        actual = (
            self.suppression_reason,
            self.duration_ms,
            self.passes,
            self.loops,
            self.returns_to_baseline,
        )
        if actual != expected:
            raise ValueError("inconsistent fleet cue plan")
        if (
            self.disposition is FleetCueDisposition.SUPPRESS
            and self.suppression_reason is None
        ):
            raise ValueError("suppressed fleet cue needs a reason")

    @property
    def emits(self) -> bool:
        return self.disposition is not FleetCueDisposition.SUPPRESS

    @property
    def animated(self) -> bool:
        return self.disposition is FleetCueDisposition.WINK


@dataclass(frozen=True, slots=True)
class FleetArrivalDepartureDecision:
    """The next pure state plus at most one confirmed transition cue."""

    disposition: FleetObservationDisposition
    state: FleetArrivalDepartureState | None
    cue: FleetArrivalDepartureCue | None = None
    refusal: FleetObservationRefusal | None = None

    def __post_init__(self) -> None:
        if type(self.disposition) is not FleetObservationDisposition:
            raise ValueError("fleet observation disposition must be known")
        if self.state is not None and type(self.state) is not FleetArrivalDepartureState:
            raise ValueError("fleet observation state must be typed")
        if self.cue is not None and type(self.cue) is not FleetArrivalDepartureCue:
            raise ValueError("fleet observation cue must be typed")
        if self.refusal is not None and type(self.refusal) is not FleetObservationRefusal:
            raise ValueError("fleet observation refusal must be known")
        if self.disposition is FleetObservationDisposition.REFUSED:
            if self.refusal is None or self.cue is not None:
                raise ValueError("refused fleet observation needs only a refusal")
        elif self.refusal is not None:
            raise ValueError("accepted fleet observation cannot have a refusal")
        if self.disposition is FleetObservationDisposition.TRANSITION_CONFIRMED:
            if self.state is None or self.cue is None:
                raise ValueError("confirmed fleet transition needs state and cue")
        elif self.cue is not None:
            raise ValueError("only a confirmed transition can contain a cue")

    @property
    def accepted(self) -> bool:
        return self.disposition is not FleetObservationDisposition.REFUSED


_SUPPRESSION_ACCESSIBILITY: Final = {
    FleetCueSuppressionReason.HOLD_WINDOW: (
        "Remote fleet presence changed. The courtesy cue is withheld during "
        "the connection stability hold."
    ),
    FleetCueSuppressionReason.DND: (
        "Remote fleet presence changed. The courtesy cue is withheld by Do Not Disturb."
    ),
    FleetCueSuppressionReason.COURTESY_FOCUS: (
        "Remote fleet presence changed. The courtesy cue is withheld by the active "
        "focus policy."
    ),
    FleetCueSuppressionReason.COURTESY_SNOOZE: (
        "Remote fleet presence changed. The courtesy cue is snoozed."
    ),
    FleetCueSuppressionReason.COURTESY_BUDGET: (
        "Remote fleet presence changed. The courtesy cue budget is exhausted."
    ),
    FleetCueSuppressionReason.FINITE_CUE_UNAVAILABLE: (
        "Remote fleet presence changed. No finite courtesy cue slot is available."
    ),
}


def _transition_for(liveness: RemoteMachineLiveness) -> FleetPresenceTransition:
    if liveness is RemoteMachineLiveness.ONLINE:
        return FleetPresenceTransition.ARRIVAL
    return FleetPresenceTransition.DEPARTURE


def _endpoint_for(transition: FleetPresenceTransition) -> FleetEndpointRole:
    if transition is FleetPresenceTransition.ARRIVAL:
        return FleetEndpointRole.ARRIVAL_ENDPOINT
    return FleetEndpointRole.DEPARTURE_ENDPOINT


def _accessibility_for(
    transition: FleetPresenceTransition,
    *,
    disposition: FleetCueDisposition,
    suppression_reason: FleetCueSuppressionReason | None = None,
) -> FleetArrivalDepartureAccessibility:
    if disposition is FleetCueDisposition.SUPPRESS:
        if suppression_reason is None:
            raise ValueError("suppressed fleet accessibility needs a reason")
        announcement = _SUPPRESSION_ACCESSIBILITY[suppression_reason]
    elif transition is FleetPresenceTransition.ARRIVAL:
        announcement = (
            "A trusted remote machine joined the fleet. One quiet endpoint cue "
            "will appear, then normal status returns."
        )
    else:
        announcement = (
            "A trusted remote machine left the fleet. One quiet endpoint cue "
            "will appear, then normal status returns."
        )
    return FleetArrivalDepartureAccessibility(
        label="Remote fleet presence",
        value=(
            "Trusted remote machine joined"
            if transition is FleetPresenceTransition.ARRIVAL
            else "Trusted remote machine left"
        ),
        announcement=announcement,
    )


def _suppression_reason(
    *,
    now: float,
    cue_hold_until: float,
    dnd_display_admission: DisplayAdmission,
    courtesy_suppression: CourtesySuppression,
    finite_cue_available: bool,
) -> FleetCueSuppressionReason | None:
    if now < cue_hold_until:
        return FleetCueSuppressionReason.HOLD_WINDOW
    if dnd_display_admission is not DisplayAdmission.ALL:
        return FleetCueSuppressionReason.DND
    if courtesy_suppression.focus:
        return FleetCueSuppressionReason.COURTESY_FOCUS
    if courtesy_suppression.snoozed:
        return FleetCueSuppressionReason.COURTESY_SNOOZE
    if courtesy_suppression.budget_exhausted:
        return FleetCueSuppressionReason.COURTESY_BUDGET
    if not finite_cue_available:
        return FleetCueSuppressionReason.FINITE_CUE_UNAVAILABLE
    return None


def _cue(
    identity: FleetArrivalDepartureIdentity,
    *,
    reason: FleetCueSuppressionReason | None,
    reduce_motion: bool,
) -> FleetArrivalDepartureCue:
    endpoint = _endpoint_for(identity.transition)
    if reason is not None:
        return FleetArrivalDepartureCue(
            identity=identity,
            endpoint_role=endpoint,
            disposition=FleetCueDisposition.SUPPRESS,
            suppression_reason=reason,
            duration_ms=0,
            passes=0,
            loops=0,
            returns_to_baseline=False,
            accessibility=_accessibility_for(
                identity.transition,
                disposition=FleetCueDisposition.SUPPRESS,
                suppression_reason=reason,
            ),
        )
    disposition = (
        FleetCueDisposition.STATIC if reduce_motion else FleetCueDisposition.WINK
    )
    return FleetArrivalDepartureCue(
        identity=identity,
        endpoint_role=endpoint,
        disposition=disposition,
        suppression_reason=None,
        duration_ms=(
            STATIC_HIGHLIGHT_DURATION_MS
            if reduce_motion
            else WINK_DURATION_MS
        ),
        passes=1,
        loops=0,
        returns_to_baseline=True,
        accessibility=_accessibility_for(
            identity.transition,
            disposition=disposition,
        ),
    )


def _refused(
    previous: FleetArrivalDepartureState | None,
    reason: FleetObservationRefusal,
) -> FleetArrivalDepartureDecision:
    return FleetArrivalDepartureDecision(
        disposition=FleetObservationDisposition.REFUSED,
        state=previous,
        refusal=reason,
    )


def observe_fleet_arrival_departure(
    observation: TrustedRemoteMachineLiveness,
    previous: FleetArrivalDepartureState | None = None,
    *,
    policy: FleetArrivalDeparturePolicy = FleetArrivalDeparturePolicy(),
    dnd_display_admission: DisplayAdmission = DisplayAdmission.ALL,
    courtesy_suppression: CourtesySuppression = CourtesySuppression(),
    finite_cue_available: bool = True,
    reduce_motion: bool = False,
) -> FleetArrivalDepartureDecision:
    """Advance one machine's debounce state without reading time or doing I/O."""

    if type(observation) is not TrustedRemoteMachineLiveness:
        raise ValueError("fleet observation must be typed")
    if previous is not None and type(previous) is not FleetArrivalDepartureState:
        raise ValueError("previous fleet state must be typed")
    if type(policy) is not FleetArrivalDeparturePolicy:
        raise ValueError("fleet arrival policy must be typed")
    if type(dnd_display_admission) is not DisplayAdmission:
        raise ValueError("fleet arrival DND admission must be known")
    if type(courtesy_suppression) is not CourtesySuppression:
        raise ValueError("fleet arrival courtesy suppression must be typed")
    if type(finite_cue_available) is not bool or type(reduce_motion) is not bool:
        raise ValueError("fleet arrival presentation flags must be booleans")

    if observation.trust is not RemoteMachineTrust.TRUSTED:
        return _refused(previous, FleetObservationRefusal.UNTRUSTED)

    if previous is None:
        return FleetArrivalDepartureDecision(
            disposition=FleetObservationDisposition.BASELINED,
            state=FleetArrivalDepartureState(
                machine_identity=observation.machine_identity,
                stable_liveness=observation.liveness,
                stable_liveness_identity=observation.liveness_identity,
                last_observed_at=observation.observed_at,
            ),
        )

    if observation.machine_identity != previous.machine_identity:
        return _refused(
            previous,
            FleetObservationRefusal.MACHINE_IDENTITY_MISMATCH,
        )
    if observation.observed_at < previous.last_observed_at:
        return _refused(previous, FleetObservationRefusal.OUT_OF_ORDER)
    if observation.observed_at == previous.last_observed_at:
        exact_repeat = (
            observation.liveness is previous.stable_liveness
            and observation.liveness_identity == previous.stable_liveness_identity
            and previous.candidate_liveness is None
        ) or (
            observation.liveness is previous.candidate_liveness
            and observation.liveness_identity == previous.candidate_liveness_identity
        )
        if exact_repeat:
            return FleetArrivalDepartureDecision(
                disposition=FleetObservationDisposition.STABLE,
                state=previous,
            )
        return _refused(previous, FleetObservationRefusal.CONFLICTING_TIMESTAMP)

    if observation.liveness is previous.stable_liveness:
        disposition = (
            FleetObservationDisposition.FLAP_DEBOUNCED
            if previous.candidate_liveness is not None
            else FleetObservationDisposition.STABLE
        )
        return FleetArrivalDepartureDecision(
            disposition=disposition,
            state=FleetArrivalDepartureState(
                machine_identity=previous.machine_identity,
                stable_liveness=previous.stable_liveness,
                stable_liveness_identity=observation.liveness_identity,
                last_observed_at=observation.observed_at,
                cue_hold_until=previous.cue_hold_until,
                last_cue_identity=previous.last_cue_identity,
            ),
        )

    same_candidate = (
        observation.liveness is previous.candidate_liveness
        and observation.liveness_identity == previous.candidate_liveness_identity
    )
    if not same_candidate:
        return FleetArrivalDepartureDecision(
            disposition=FleetObservationDisposition.SETTLING,
            state=FleetArrivalDepartureState(
                machine_identity=previous.machine_identity,
                stable_liveness=previous.stable_liveness,
                stable_liveness_identity=previous.stable_liveness_identity,
                last_observed_at=observation.observed_at,
                candidate_liveness=observation.liveness,
                candidate_liveness_identity=observation.liveness_identity,
                candidate_since=observation.observed_at,
                cue_hold_until=previous.cue_hold_until,
                last_cue_identity=previous.last_cue_identity,
            ),
        )

    if previous.candidate_since is None:
        raise ValueError("complete candidate state requires a start time")
    settled_for = observation.observed_at - previous.candidate_since
    if settled_for < policy.settle_seconds_for(observation.liveness):
        return FleetArrivalDepartureDecision(
            disposition=FleetObservationDisposition.SETTLING,
            state=FleetArrivalDepartureState(
                machine_identity=previous.machine_identity,
                stable_liveness=previous.stable_liveness,
                stable_liveness_identity=previous.stable_liveness_identity,
                last_observed_at=observation.observed_at,
                candidate_liveness=previous.candidate_liveness,
                candidate_liveness_identity=previous.candidate_liveness_identity,
                candidate_since=previous.candidate_since,
                cue_hold_until=previous.cue_hold_until,
                last_cue_identity=previous.last_cue_identity,
            ),
        )

    transition = _transition_for(observation.liveness)
    identity = FleetArrivalDepartureIdentity(
        machine_identity=observation.machine_identity,
        liveness_identity=observation.liveness_identity,
        transition=transition,
    )
    reason = _suppression_reason(
        now=observation.observed_at,
        cue_hold_until=previous.cue_hold_until,
        dnd_display_admission=dnd_display_admission,
        courtesy_suppression=courtesy_suppression,
        finite_cue_available=finite_cue_available,
    )
    cue = _cue(identity, reason=reason, reduce_motion=reduce_motion)
    emitted = cue.emits
    next_hold = (
        observation.observed_at + policy.cue_hold_seconds
        if emitted
        else previous.cue_hold_until
    )
    return FleetArrivalDepartureDecision(
        disposition=FleetObservationDisposition.TRANSITION_CONFIRMED,
        state=FleetArrivalDepartureState(
            machine_identity=previous.machine_identity,
            stable_liveness=observation.liveness,
            stable_liveness_identity=observation.liveness_identity,
            last_observed_at=observation.observed_at,
            cue_hold_until=next_hold,
            last_cue_identity=(identity if emitted else previous.last_cue_identity),
        ),
        cue=cue,
    )


__all__ = [
    "DEFAULT_CUE_HOLD_SECONDS",
    "DEFAULT_DEPARTURE_SETTLE_SECONDS",
    "DEFAULT_JOIN_SETTLE_SECONDS",
    "MAX_CUE_HOLD_SECONDS",
    "MAX_IDENTITY_LENGTH",
    "MAX_SETTLE_SECONDS",
    "STATIC_HIGHLIGHT_DURATION_MS",
    "WINK_DURATION_MS",
    "FleetArrivalDepartureAccessibility",
    "FleetArrivalDepartureCue",
    "FleetArrivalDepartureDecision",
    "FleetArrivalDepartureIdentity",
    "FleetArrivalDeparturePolicy",
    "FleetArrivalDepartureState",
    "FleetCueDisposition",
    "FleetCueSuppressionReason",
    "FleetEndpointRole",
    "FleetObservationDisposition",
    "FleetObservationRefusal",
    "FleetPresenceTransition",
    "RemoteMachineLiveness",
    "RemoteMachineTrust",
    "TrustedRemoteMachineLiveness",
    "observe_fleet_arrival_departure",
]
