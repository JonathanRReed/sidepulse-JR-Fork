from __future__ import annotations

from dataclasses import replace

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.navigation_policy import (
    NavigationCandidate,
    NavigationResolution,
    NavigationResolutionKind,
    OperatorActionKind,
    OperatorLocalActionState,
    build_operator_actions,
    resolve_navigation,
)
from sidepulse.operator_state import (
    AcknowledgementEligibility,
    CanonicalRequestTruth,
    CanonicalWorkTruth,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
)
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderWatermark,
    RequestIdentifier,
    RequestKey,
    RequestKind,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
)


def _source(instance: str = "local:01", *, provider: str = "codex") -> SourceKey:
    return SourceKey(provider, "hooks", instance, "live_agent_events")


def _work_key(
    value: str = "work:01",
    *,
    source: SourceKey | None = None,
) -> WorkKey:
    return WorkKey(source or _source(), WorkIdentifier(value))


def _watermark(key: WorkKey, *, token: str = "event:01") -> ProviderWatermark:
    return ProviderWatermark(
        source_key=key.source_key,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=1_800_000_000.0,
        event_token=EventToken(token),
        sequence=None,
        tie_break_rank=10,
    )


def _work_truth(key: WorkKey | None = None) -> CanonicalWorkTruth:
    actual_key = key or _work_key()
    return CanonicalWorkTruth(
        key=actual_key,
        lifecycle=WorkLifecycle.ACTIVE,
        watermark=_watermark(actual_key),
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.FRESH,
        next_actor=NextActor.PROVIDER,
        safe_label="Codex 01",
        parent_key=None,
        request_keys=(),
        timing_uncertain=False,
    )


def _request_truth(
    work_key: WorkKey,
    *,
    phase: RequestPhase = RequestPhase.LIVE_UNACKNOWLEDGED,
    eligibility: AcknowledgementEligibility = AcknowledgementEligibility.ELIGIBLE,
    freshness: SourceFreshness = SourceFreshness.FRESH,
) -> CanonicalRequestTruth:
    key = RequestKey(work_key, RequestIdentifier("request:01"))
    watermark = _watermark(work_key, token="request-event:01")
    return CanonicalRequestTruth(
        key=key,
        phase=phase,
        request_kind=RequestKind.PERMISSION,
        next_actor=NextActor.USER,
        watermark=watermark,
        source_freshness=freshness,
        acknowledgement_eligibility=eligibility,
        semantic_event_key=SemanticEventKey(
            subject_key=key,
            transition_kind=TransitionKind.REQUEST_OPENED,
            provider_watermark=watermark,
        ),
        opened_at_epoch=1_800_000_000.0,
        eligible_elapsed_seconds=30.0,
    )


def _candidate(
    key: WorkKey | None = None,
    *,
    generation: int = 7,
    action_id: str = "open:primary",
    target_kind: str = "url",
    target_value: str = "codex://threads/session-01",
    freshness: SourceFreshness = SourceFreshness.FRESH,
    authority: bool = True,
) -> NavigationCandidate:
    return NavigationCandidate(
        work_key=key or _work_key(),
        source_generation=generation,
        action_id=action_id,
        target_kind=target_kind,
        target_value=target_value,
        source_freshness=freshness,
        navigation_authority=authority,
    )


def _local(
    *,
    watched: bool = False,
    pinned: bool = False,
    snoozed: bool = False,
    acknowledged: bool = False,
    pin_position: int | None = None,
    pin_count: int = 0,
) -> OperatorLocalActionState:
    return OperatorLocalActionState(
        watched=watched,
        pinned=pinned,
        snoozed=snoozed,
        acknowledged=acknowledged,
        pin_position=pin_position,
        pin_count=pin_count,
    )


def _kinds(actions: tuple[object, ...]) -> tuple[OperatorActionKind, ...]:
    return tuple(action.kind for action in actions)  # type: ignore[attr-defined]


def test_exact_fresh_authorized_candidate_is_ready() -> None:
    """Dropping the exact candidate checks would open a guessed target."""
    key = _work_key()

    result = resolve_navigation(
        key,
        "open:primary",
        (_candidate(key),),
        expected_source_generation=7,
    )

    assert result.kind is NavigationResolutionKind.READY
    assert result.work_key == key
    assert result.action_id == "open:primary"
    assert result.target_kind == "url"
    assert result.target_value == "codex://threads/session-01"
    assert result.source_generation == 7
    assert result.reason is None


@pytest.mark.parametrize(
    ("candidates", "expected_kind", "expected_reason"),
    (
        ((), NavigationResolutionKind.MISSING, "Not available"),
        (
            (_candidate(freshness=SourceFreshness.STALE),),
            NavigationResolutionKind.STALE,
            "Source is stale",
        ),
        (
            (_candidate(authority=False),),
            NavigationResolutionKind.DISABLED,
            "Not available",
        ),
        (
            (_candidate(), _candidate()),
            NavigationResolutionKind.AMBIGUOUS,
            "Multiple sources match",
        ),
        (
            (_candidate(target_kind="file"),),
            NavigationResolutionKind.DISABLED,
            "Not available",
        ),
    ),
)
def test_nonexecutable_candidate_states_refuse_with_product_copy(
    candidates: tuple[NavigationCandidate, ...],
    expected_kind: NavigationResolutionKind,
    expected_reason: str,
) -> None:
    """Treating any non-ready result as executable would cross the authority boundary."""
    result = resolve_navigation(_work_key(), "open:primary", candidates)

    assert result.kind is expected_kind
    assert result.target_kind is None
    assert result.target_value is None
    assert result.reason == expected_reason


def test_source_instance_collision_does_not_match_by_display_identity() -> None:
    """Matching only provider and work ID would cross two source instances."""
    requested = _work_key(source=_source("local:01"))
    other = _work_key(source=_source("local:02"))

    result = resolve_navigation(
        requested,
        "open:primary",
        (_candidate(other),),
    )

    assert result.kind is NavigationResolutionKind.MISSING
    assert result.reason == "Not available"


def test_activation_generation_mismatch_refuses_changed_target() -> None:
    """Ignoring the activation generation would execute a replacement target."""
    result = resolve_navigation(
        _work_key(),
        "open:primary",
        (_candidate(generation=8),),
        expected_source_generation=7,
    )

    assert result.kind is NavigationResolutionKind.STALE
    assert result.target_kind is None
    assert result.target_value is None
    assert result.source_generation == 8
    assert result.reason == "Target changed"


def test_action_id_must_match_exactly() -> None:
    """Falling back from a missing action ID would open a different surface."""
    result = resolve_navigation(
        _work_key(),
        "open:worker",
        (_candidate(action_id="open:primary"),),
    )

    assert result.kind is NavigationResolutionKind.MISSING


def test_oversized_target_is_disabled_without_exposing_it() -> None:
    """Passing an oversized target through would bypass the bounded opener contract."""
    candidate = _candidate(target_value="codex://threads/" + "a" * 4096)

    result = resolve_navigation(_work_key(), "open:primary", (candidate,))

    assert result.kind is NavigationResolutionKind.DISABLED
    assert result.target_value is None
    assert result.reason == "Not available"


@pytest.mark.parametrize(
    "candidate",
    (
        _candidate(target_value="https://example.com/session"),
        _candidate(target_value="codex://threads/session-01;open /tmp/other"),
        _candidate(
            target_kind="terminal",
            target_value="cd /tmp/project && bash -c 'open /tmp/other'",
        ),
    ),
)
def test_url_and_terminal_targets_are_provider_allowlisted(
    candidate: NavigationCandidate,
) -> None:
    """Accepting arbitrary schemes or executables would add command authority."""
    result = resolve_navigation(_work_key(), "open:primary", (candidate,))

    assert result.kind is NavigationResolutionKind.DISABLED
    assert result.reason == "Not available"


def test_shared_actions_have_one_stable_order_for_eligible_request() -> None:
    """Per-surface ordering would make destructive-looking local actions inconsistent."""
    work = _work_truth()
    navigation = resolve_navigation(work.key, "open:primary", (_candidate(work.key),))

    actions = build_operator_actions(
        work=work,
        request=_request_truth(work.key),
        local=_local(),
        navigation=navigation,
    )

    assert _kinds(actions) == (
        OperatorActionKind.OPEN,
        OperatorActionKind.ACKNOWLEDGE,
        OperatorActionKind.WATCH,
        OperatorActionKind.PIN,
    )
    assert tuple(action.title for action in actions) == (
        "Open",
        "I'm on It",
        "Watch",
        "Pin",
    )


@pytest.mark.parametrize(
    ("phase", "eligibility", "local_acknowledged", "expected"),
    (
        (
            RequestPhase.LIVE_UNACKNOWLEDGED,
            AcknowledgementEligibility.ELIGIBLE,
            False,
            OperatorActionKind.ACKNOWLEDGE,
        ),
        (
            RequestPhase.LIVE_ACKNOWLEDGED,
            AcknowledgementEligibility.ALREADY_ACKNOWLEDGED,
            True,
            OperatorActionKind.RESUME_ESCALATION,
        ),
        (
            RequestPhase.STALE_HOLD,
            AcknowledgementEligibility.STALE_HOLD,
            False,
            None,
        ),
        (
            RequestPhase.RESOLVED,
            AcknowledgementEligibility.RESOLVED,
            False,
            None,
        ),
        (
            RequestPhase.UNKNOWN_EXPIRED,
            AcknowledgementEligibility.NOT_ACTIONABLE,
            False,
            None,
        ),
    ),
)
def test_request_phase_and_eligibility_control_local_acknowledgement(
    phase: RequestPhase,
    eligibility: AcknowledgementEligibility,
    local_acknowledged: bool,
    expected: OperatorActionKind | None,
) -> None:
    """Inferring acknowledgement from a non-actionable phase would author provider truth."""
    work = _work_truth()
    navigation = resolve_navigation(work.key, "open:primary", (_candidate(work.key),))
    actions = build_operator_actions(
        work=work,
        request=_request_truth(work.key, phase=phase, eligibility=eligibility),
        local=_local(acknowledged=local_acknowledged),
        navigation=navigation,
    )
    acknowledgement_kinds = {
        OperatorActionKind.ACKNOWLEDGE,
        OperatorActionKind.RESUME_ESCALATION,
    }

    actual = tuple(action.kind for action in actions if action.kind in acknowledgement_kinds)
    assert actual == (() if expected is None else (expected,))


def test_actionable_request_never_exposes_snooze_even_if_local_state_is_forged() -> None:
    """Offering Snooze for a current ask could hide work that still needs the user."""
    work = _work_truth()
    navigation = resolve_navigation(work.key, "open:primary", (_candidate(work.key),))

    actions = build_operator_actions(
        work=work,
        request=_request_truth(work.key),
        local=_local(snoozed=True),
        navigation=navigation,
    )

    assert OperatorActionKind.SNOOZE not in _kinds(actions)
    assert OperatorActionKind.UNSNOOZE not in _kinds(actions)


def test_live_nonactionable_request_does_not_block_visibility_only_snooze() -> None:
    """Treating every live phase as actionable would disable safe local organization."""
    work = _work_truth()
    navigation = resolve_navigation(work.key, "open:primary", (_candidate(work.key),))

    actions = build_operator_actions(
        work=work,
        request=_request_truth(
            work.key,
            eligibility=AcknowledgementEligibility.NOT_ACTIONABLE,
        ),
        local=_local(),
        navigation=navigation,
    )

    assert OperatorActionKind.SNOOZE in _kinds(actions)


@pytest.mark.parametrize(
    ("position", "count", "expected_moves"),
    (
        (0, 1, ()),
        (0, 3, (OperatorActionKind.MOVE_PIN_DOWN,)),
        (1, 3, (OperatorActionKind.MOVE_PIN_UP, OperatorActionKind.MOVE_PIN_DOWN)),
        (2, 3, (OperatorActionKind.MOVE_PIN_UP,)),
    ),
)
def test_pinned_move_actions_respect_exact_bounds(
    position: int,
    count: int,
    expected_moves: tuple[OperatorActionKind, ...],
) -> None:
    """An off-by-one move would publish a local action that cannot succeed."""
    work = _work_truth()
    navigation = resolve_navigation(work.key, "open:primary", (_candidate(work.key),))

    actions = build_operator_actions(
        work=work,
        request=None,
        local=_local(pinned=True, pin_position=position, pin_count=count),
        navigation=navigation,
    )
    move_kinds = {OperatorActionKind.MOVE_PIN_UP, OperatorActionKind.MOVE_PIN_DOWN}

    assert tuple(action.kind for action in actions if action.kind in move_kinds) == expected_moves


def test_nonactionable_local_actions_switch_to_inverse_actions() -> None:
    """Ignoring local state would make reversible organization actions one-way."""
    work = _work_truth()
    navigation = resolve_navigation(work.key, "open:primary", (_candidate(work.key),))

    actions = build_operator_actions(
        work=work,
        request=None,
        local=_local(watched=True, pinned=True, snoozed=True, pin_position=0, pin_count=1),
        navigation=navigation,
    )

    assert _kinds(actions) == (
        OperatorActionKind.OPEN,
        OperatorActionKind.UNWATCH,
        OperatorActionKind.UNPIN,
        OperatorActionKind.UNSNOOZE,
    )


def test_open_descriptor_keeps_exact_navigation_refusal_reason() -> None:
    """Replacing a precise refusal with an enabled Open would cause guessed navigation."""
    work = _work_truth()
    navigation = resolve_navigation(work.key, "open:primary", ())

    actions = build_operator_actions(
        work=work,
        request=None,
        local=_local(),
        navigation=navigation,
    )

    assert actions[0].kind is OperatorActionKind.OPEN
    assert actions[0].enabled is False
    assert actions[0].disabled_reason == "Not available"


def test_builder_rejects_cross_work_request_and_navigation() -> None:
    """Joining by a nonexact key would mix actions across two sessions."""
    work = _work_truth()
    other_work = _work_truth(_work_key("work:02"))
    other_navigation = resolve_navigation(
        other_work.key,
        "open:primary",
        (_candidate(other_work.key),),
    )

    with pytest.raises(ValueError, match="navigation"):
        build_operator_actions(
            work=work,
            request=None,
            local=_local(),
            navigation=other_navigation,
        )

    navigation = resolve_navigation(work.key, "open:primary", (_candidate(work.key),))
    with pytest.raises(ValueError, match="request"):
        build_operator_actions(
            work=work,
            request=_request_truth(other_work.key),
            local=_local(),
            navigation=navigation,
        )


def test_public_records_are_frozen_and_local_pin_state_is_strict() -> None:
    """Mutable or internally inconsistent records would invalidate later action guards."""
    candidate = _candidate()
    with pytest.raises(Exception):
        candidate.source_generation = 9  # type: ignore[misc]

    with pytest.raises(ValueError, match="pin"):
        replace(_local(), pinned=True, pin_position=None, pin_count=1)


def test_action_identity_is_bounded_product_owned_opaque_text() -> None:
    """Allowing path-shaped action IDs would leak source content into represented objects."""
    with pytest.raises(ValueError, match="candidate"):
        _candidate(action_id="open /tmp/private-project")


def test_ready_resolution_cannot_be_forged_with_a_nonallowlisted_target() -> None:
    """A forged ready record must not make a later shared descriptor executable."""
    with pytest.raises(ValueError, match="ready"):
        NavigationResolution(
            kind=NavigationResolutionKind.READY,
            work_key=_work_key(),
            action_id="open:primary",
            target_kind="url",
            target_value="https://example.com/private-session",
            source_generation=7,
            reason=None,
        )
