from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sidepulse.dnd_policy import DisplayAdmission
from sidepulse.fleet_arrival_departure import (
    DEFAULT_CUE_HOLD_SECONDS,
    DEFAULT_DEPARTURE_SETTLE_SECONDS,
    DEFAULT_JOIN_SETTLE_SECONDS,
    MAX_CUE_HOLD_SECONDS,
    MAX_SETTLE_SECONDS,
    STATIC_HIGHLIGHT_DURATION_MS,
    WINK_DURATION_MS,
    FleetArrivalDeparturePolicy,
    FleetArrivalDepartureState,
    FleetCueDisposition,
    FleetCueSuppressionReason,
    FleetEndpointRole,
    FleetObservationDisposition,
    FleetObservationRefusal,
    FleetPresenceTransition,
    RemoteMachineLiveness,
    RemoteMachineTrust,
    TrustedRemoteMachineLiveness,
    observe_fleet_arrival_departure,
)
from sidepulse.semantic_effect_router import CourtesySuppression


def _observation(
    liveness: RemoteMachineLiveness,
    observed_at: float,
    *,
    episode: str,
    machine: str = "machine:private-01",
    trust: RemoteMachineTrust = RemoteMachineTrust.TRUSTED,
) -> TrustedRemoteMachineLiveness:
    return TrustedRemoteMachineLiveness(
        machine_identity=machine,
        liveness_identity=episode,
        liveness=liveness,
        observed_at=observed_at,
        trust=trust,
    )


def _baseline(
    liveness: RemoteMachineLiveness = RemoteMachineLiveness.ONLINE,
    *,
    observed_at: float = 0.0,
    episode: str = "episode:baseline",
) -> FleetArrivalDepartureState:
    decision = observe_fleet_arrival_departure(
        _observation(liveness, observed_at, episode=episode)
    )
    assert decision.disposition is FleetObservationDisposition.BASELINED
    assert decision.state is not None
    assert decision.cue is None
    return decision.state


def _settle_transition(
    state: FleetArrivalDepartureState,
    liveness: RemoteMachineLiveness,
    *,
    episode: str,
    started_at: float,
    confirmed_at: float,
    **policy_inputs: object,
):
    started = observe_fleet_arrival_departure(
        _observation(liveness, started_at, episode=episode),
        state,
        **policy_inputs,
    )
    assert started.disposition is FleetObservationDisposition.SETTLING
    assert started.state is not None
    return observe_fleet_arrival_departure(
        _observation(liveness, confirmed_at, episode=episode),
        started.state,
        **policy_inputs,
    )


def test_first_trusted_observation_establishes_a_silent_baseline() -> None:
    decision = observe_fleet_arrival_departure(
        _observation(
            RemoteMachineLiveness.ONLINE,
            100.0,
            episode="episode:already-online",
        )
    )

    assert decision.accepted is True
    assert decision.disposition is FleetObservationDisposition.BASELINED
    assert decision.cue is None
    assert decision.refusal is None
    assert decision.state is not None
    assert decision.state.stable_liveness is RemoteMachineLiveness.ONLINE
    assert decision.state.stable_liveness_identity == "episode:already-online"


def test_confirmed_departure_yields_one_quiet_finite_endpoint_wink() -> None:
    decision = _settle_transition(
        _baseline(),
        RemoteMachineLiveness.OFFLINE,
        episode="episode:offline-01",
        started_at=1.0,
        confirmed_at=1.0 + DEFAULT_DEPARTURE_SETTLE_SECONDS,
    )

    assert decision.disposition is FleetObservationDisposition.TRANSITION_CONFIRMED
    assert decision.cue is not None
    assert decision.cue.identity.machine_identity == "machine:private-01"
    assert decision.cue.identity.liveness_identity == "episode:offline-01"
    assert decision.cue.identity.transition is FleetPresenceTransition.DEPARTURE
    assert decision.cue.endpoint_role is FleetEndpointRole.DEPARTURE_ENDPOINT
    assert decision.cue.disposition is FleetCueDisposition.WINK
    assert decision.cue.duration_ms == WINK_DURATION_MS
    assert decision.cue.passes == 1
    assert decision.cue.loops == 0
    assert decision.cue.returns_to_baseline is True
    assert decision.cue.emits is True
    assert decision.cue.animated is True
    assert decision.state is not None
    assert decision.state.stable_liveness is RemoteMachineLiveness.OFFLINE
    assert decision.state.candidate_liveness is None


def test_confirmed_arrival_uses_the_arrival_endpoint_and_exact_episode() -> None:
    decision = _settle_transition(
        _baseline(
            RemoteMachineLiveness.OFFLINE,
            episode="episode:initial-offline",
        ),
        RemoteMachineLiveness.ONLINE,
        episode="episode:online-22",
        started_at=8.0,
        confirmed_at=8.0 + DEFAULT_JOIN_SETTLE_SECONDS,
    )

    assert decision.cue is not None
    assert decision.cue.identity.liveness_identity == "episode:online-22"
    assert decision.cue.identity.transition is FleetPresenceTransition.ARRIVAL
    assert decision.cue.endpoint_role is FleetEndpointRole.ARRIVAL_ENDPOINT
    assert decision.state is not None
    assert decision.state.stable_liveness_identity == "episode:online-22"


def test_connection_flapping_cancels_candidates_without_any_cue() -> None:
    state = _baseline()

    offline = observe_fleet_arrival_departure(
        _observation(RemoteMachineLiveness.OFFLINE, 1.0, episode="episode:off-1"),
        state,
    )
    assert offline.state is not None
    online = observe_fleet_arrival_departure(
        _observation(RemoteMachineLiveness.ONLINE, 2.0, episode="episode:on-2"),
        offline.state,
    )
    assert online.disposition is FleetObservationDisposition.FLAP_DEBOUNCED
    assert online.cue is None
    assert online.state is not None
    assert online.state.candidate_liveness is None

    offline_again = observe_fleet_arrival_departure(
        _observation(RemoteMachineLiveness.OFFLINE, 3.0, episode="episode:off-3"),
        online.state,
    )
    assert offline_again.state is not None
    online_again = observe_fleet_arrival_departure(
        _observation(RemoteMachineLiveness.ONLINE, 4.0, episode="episode:on-4"),
        offline_again.state,
    )
    assert online_again.disposition is FleetObservationDisposition.FLAP_DEBOUNCED
    assert online_again.cue is None
    assert online_again.state is not None
    assert online_again.state.last_cue_identity is None


def test_changed_episode_identity_restarts_settlement_from_zero() -> None:
    state = _baseline()
    first = observe_fleet_arrival_departure(
        _observation(RemoteMachineLiveness.OFFLINE, 1.0, episode="episode:off-a"),
        state,
    )
    assert first.state is not None

    restarted = observe_fleet_arrival_departure(
        _observation(RemoteMachineLiveness.OFFLINE, 10.0, episode="episode:off-b"),
        first.state,
    )
    assert restarted.disposition is FleetObservationDisposition.SETTLING
    assert restarted.state is not None
    assert restarted.state.candidate_since == 10.0

    still_settling = observe_fleet_arrival_departure(
        _observation(RemoteMachineLiveness.OFFLINE, 14.9, episode="episode:off-b"),
        restarted.state,
    )
    assert still_settling.disposition is FleetObservationDisposition.SETTLING
    assert still_settling.cue is None
    assert still_settling.state is not None

    confirmed = observe_fleet_arrival_departure(
        _observation(RemoteMachineLiveness.OFFLINE, 15.0, episode="episode:off-b"),
        still_settling.state,
    )
    assert confirmed.disposition is FleetObservationDisposition.TRANSITION_CONFIRMED
    assert confirmed.cue is not None
    assert confirmed.cue.identity.liveness_identity == "episode:off-b"


def test_post_cue_hold_suppresses_churn_without_freezing_liveness_truth() -> None:
    departed = _settle_transition(
        _baseline(),
        RemoteMachineLiveness.OFFLINE,
        episode="episode:off",
        started_at=1.0,
        confirmed_at=6.0,
    )
    assert departed.state is not None
    assert departed.state.cue_hold_until == 6.0 + DEFAULT_CUE_HOLD_SECONDS

    arrived = _settle_transition(
        departed.state,
        RemoteMachineLiveness.ONLINE,
        episode="episode:on",
        started_at=7.0,
        confirmed_at=9.0,
    )
    assert arrived.cue is not None
    assert arrived.cue.disposition is FleetCueDisposition.SUPPRESS
    assert arrived.cue.suppression_reason is FleetCueSuppressionReason.HOLD_WINDOW
    assert arrived.state is not None
    assert arrived.state.stable_liveness is RemoteMachineLiveness.ONLINE
    assert arrived.state.cue_hold_until == departed.state.cue_hold_until
    assert arrived.state.last_cue_identity == departed.state.last_cue_identity

    later_departure = _settle_transition(
        arrived.state,
        RemoteMachineLiveness.OFFLINE,
        episode="episode:off-later",
        started_at=30.0,
        confirmed_at=35.0,
    )
    assert later_departure.cue is not None
    assert later_departure.cue.disposition is FleetCueDisposition.WINK
    assert later_departure.cue.identity.liveness_identity == "episode:off-later"


@pytest.mark.parametrize(
    ("inputs", "reason"),
    (
        (
            {"dnd_display_admission": DisplayAdmission.NONE},
            FleetCueSuppressionReason.DND,
        ),
        (
            {"courtesy_suppression": CourtesySuppression(focus=True)},
            FleetCueSuppressionReason.COURTESY_FOCUS,
        ),
        (
            {"courtesy_suppression": CourtesySuppression(snoozed=True)},
            FleetCueSuppressionReason.COURTESY_SNOOZE,
        ),
        (
            {"courtesy_suppression": CourtesySuppression(budget_exhausted=True)},
            FleetCueSuppressionReason.COURTESY_BUDGET,
        ),
        (
            {"finite_cue_available": False},
            FleetCueSuppressionReason.FINITE_CUE_UNAVAILABLE,
        ),
    ),
)
def test_presentation_policy_withholds_courtesy_but_commits_confirmed_truth(
    inputs: dict[str, object],
    reason: FleetCueSuppressionReason,
) -> None:
    decision = _settle_transition(
        _baseline(),
        RemoteMachineLiveness.OFFLINE,
        episode="episode:policy-offline",
        started_at=1.0,
        confirmed_at=6.0,
        **inputs,
    )

    assert decision.cue is not None
    assert decision.cue.disposition is FleetCueDisposition.SUPPRESS
    assert decision.cue.suppression_reason is reason
    assert decision.cue.duration_ms == 0
    assert decision.cue.passes == 0
    assert decision.cue.loops == 0
    assert decision.state is not None
    assert decision.state.stable_liveness is RemoteMachineLiveness.OFFLINE
    assert decision.state.last_cue_identity is None


def test_reduce_motion_substitutes_one_finite_static_endpoint_highlight() -> None:
    decision = _settle_transition(
        _baseline(RemoteMachineLiveness.OFFLINE),
        RemoteMachineLiveness.ONLINE,
        episode="episode:static-arrival",
        started_at=1.0,
        confirmed_at=3.0,
        reduce_motion=True,
    )

    assert decision.cue is not None
    assert decision.cue.disposition is FleetCueDisposition.STATIC
    assert decision.cue.duration_ms == STATIC_HIGHLIGHT_DURATION_MS
    assert decision.cue.passes == 1
    assert decision.cue.loops == 0
    assert decision.cue.returns_to_baseline is True
    assert decision.cue.animated is False


def test_accessibility_contract_is_content_free_and_does_not_expose_identities() -> None:
    decision = _settle_transition(
        _baseline(),
        RemoteMachineLiveness.OFFLINE,
        episode="episode:private-liveness-secret",
        started_at=1.0,
        confirmed_at=6.0,
    )
    assert decision.cue is not None

    accessibility = decision.cue.accessibility
    combined = " ".join(
        (accessibility.label, accessibility.value, accessibility.announcement)
    )
    assert "machine:private-01" not in combined
    assert "episode:private-liveness-secret" not in combined
    assert accessibility.label == "Remote fleet presence"
    assert accessibility.value == "Trusted remote machine left"
    assert "one quiet endpoint cue" in accessibility.announcement.lower()


def test_untrusted_mismatched_and_stale_observations_never_mutate_state() -> None:
    state = _baseline(observed_at=10.0)

    untrusted = observe_fleet_arrival_departure(
        _observation(
            RemoteMachineLiveness.OFFLINE,
            11.0,
            episode="episode:untrusted",
            trust=RemoteMachineTrust.UNTRUSTED,
        ),
        state,
    )
    mismatch = observe_fleet_arrival_departure(
        _observation(
            RemoteMachineLiveness.OFFLINE,
            11.0,
            episode="episode:other-machine",
            machine="machine:other",
        ),
        state,
    )
    stale = observe_fleet_arrival_departure(
        _observation(
            RemoteMachineLiveness.OFFLINE,
            9.0,
            episode="episode:stale",
        ),
        state,
    )

    assert untrusted.refusal is FleetObservationRefusal.UNTRUSTED
    assert mismatch.refusal is FleetObservationRefusal.MACHINE_IDENTITY_MISMATCH
    assert stale.refusal is FleetObservationRefusal.OUT_OF_ORDER
    assert untrusted.state is state
    assert mismatch.state is state
    assert stale.state is state
    assert all(decision.cue is None for decision in (untrusted, mismatch, stale))


def test_same_timestamp_only_accepts_an_exact_repeat() -> None:
    state = _baseline(observed_at=10.0, episode="episode:exact")

    exact = observe_fleet_arrival_departure(
        _observation(
            RemoteMachineLiveness.ONLINE,
            10.0,
            episode="episode:exact",
        ),
        state,
    )
    conflict = observe_fleet_arrival_departure(
        _observation(
            RemoteMachineLiveness.OFFLINE,
            10.0,
            episode="episode:conflict",
        ),
        state,
    )

    assert exact.disposition is FleetObservationDisposition.STABLE
    assert exact.state is state
    assert conflict.refusal is FleetObservationRefusal.CONFLICTING_TIMESTAMP
    assert conflict.state is state


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("join_settle_seconds", 0.0),
        ("join_settle_seconds", MAX_SETTLE_SECONDS + 0.1),
        ("departure_settle_seconds", float("inf")),
        ("cue_hold_seconds", 0.0),
        ("cue_hold_seconds", MAX_CUE_HOLD_SECONDS + 0.1),
    ),
)
def test_settle_and_hold_windows_are_strictly_bounded(field: str, value: float) -> None:
    inputs = {
        "join_settle_seconds": DEFAULT_JOIN_SETTLE_SECONDS,
        "departure_settle_seconds": DEFAULT_DEPARTURE_SETTLE_SECONDS,
        "cue_hold_seconds": DEFAULT_CUE_HOLD_SECONDS,
    }
    inputs[field] = value

    with pytest.raises(ValueError, match="window"):
        FleetArrivalDeparturePolicy(**inputs)


def test_state_policy_and_decision_are_immutable() -> None:
    state = _baseline()
    policy = FleetArrivalDeparturePolicy()
    decision = observe_fleet_arrival_departure(
        _observation(RemoteMachineLiveness.OFFLINE, 1.0, episode="episode:off"),
        state,
        policy=policy,
    )

    with pytest.raises(FrozenInstanceError):
        state.stable_liveness = RemoteMachineLiveness.OFFLINE  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        policy.cue_hold_seconds = 1.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.cue = None  # type: ignore[misc]
