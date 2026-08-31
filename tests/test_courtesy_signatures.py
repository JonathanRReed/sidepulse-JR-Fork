from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from sidepulse.courtesy_signatures import (
    COURTESY_SIGNATURES,
    DEFAULT_COURTESY_SIGNATURE_REGISTRY,
    MAX_CADENCE_HZ,
    CadencePulse,
    CadenceSignature,
    CourtesyPresentation,
    CourtesySemantic,
    CourtesySignatureError,
    CourtesySignatureRegistry,
    GeometrySignature,
    plan_courtesy_signature,
    signature_for_semantic,
)


def test_registry_covers_each_semantic_once_with_stable_identifiers() -> None:
    assert tuple(signature.semantic for signature in COURTESY_SIGNATURES) == (
        CourtesySemantic.COMPLETION,
        CourtesySemantic.RECOVERY,
        CourtesySemantic.HANDOFF,
        CourtesySemantic.INTERRUPTION,
        CourtesySemantic.FAILURE,
        CourtesySemantic.QUOTA_RESET,
        CourtesySemantic.CALENDAR,
        CourtesySemantic.REMINDER,
        CourtesySemantic.BATTERY,
        CourtesySemantic.WEATHER,
        CourtesySemantic.GENERIC_NOTIFICATION,
    )
    assert tuple(signature.identifier for signature in COURTESY_SIGNATURES) == (
        "jrbar.courtesy.completion.v1",
        "jrbar.courtesy.recovery.v1",
        "jrbar.courtesy.handoff.v1",
        "jrbar.courtesy.interruption.v1",
        "jrbar.courtesy.failure.v1",
        "jrbar.courtesy.quota-reset.v1",
        "jrbar.courtesy.calendar.v1",
        "jrbar.courtesy.reminder.v1",
        "jrbar.courtesy.battery.v1",
        "jrbar.courtesy.weather.v1",
        "jrbar.courtesy.generic-notification.v1",
    )


def test_registry_lookup_is_exact_and_preserves_canonical_order() -> None:
    registry = DEFAULT_COURTESY_SIGNATURE_REGISTRY
    completion = COURTESY_SIGNATURES[0]

    assert registry.list() == COURTESY_SIGNATURES
    assert registry.get(completion.identifier) is completion
    assert registry.require(completion.identifier) is completion
    assert registry.get("jrbar.courtesy.missing.v1") is None
    with pytest.raises(KeyError):
        registry.require("jrbar.courtesy.missing.v1")


def test_every_signature_is_finite_and_never_exceeds_two_hertz() -> None:
    for signature in COURTESY_SIGNATURES:
        assert 1 <= len(signature.geometry.frames) <= 5
        assert all(frame for frame in signature.geometry.frames)
        assert 1 <= signature.cadence.pulse_count <= 3
        assert signature.cadence.duration_ms > 0
        assert signature.cadence.peak_hz <= MAX_CADENCE_HZ == 2.0


def test_semantics_are_distinguishable_without_color_or_motion() -> None:
    geometry_keys = {
        signature.geometry.fingerprint for signature in COURTESY_SIGNATURES
    }
    cadence_keys = {
        signature.cadence.fingerprint for signature in COURTESY_SIGNATURES
    }
    static_keys = {
        signature.geometry.static_slots for signature in COURTESY_SIGNATURES
    }
    spatial_descriptions = {
        signature.geometry.spatial_description for signature in COURTESY_SIGNATURES
    }
    accessibility_labels = {
        signature.accessibility_label for signature in COURTESY_SIGNATURES
    }

    assert len(geometry_keys) == len(COURTESY_SIGNATURES)
    assert len(cadence_keys) == len(COURTESY_SIGNATURES)
    assert len(static_keys) == len(COURTESY_SIGNATURES)
    assert len(spatial_descriptions) == len(COURTESY_SIGNATURES)
    assert len(accessibility_labels) == len(COURTESY_SIGNATURES)


def test_motion_plan_projects_the_canonical_finite_signature() -> None:
    signature = signature_for_semantic(CourtesySemantic.HANDOFF)

    plan = plan_courtesy_signature(CourtesySemantic.HANDOFF, reduce_motion=False)

    assert plan.identifier == "jrbar.courtesy.handoff.v1"
    assert plan.semantic is CourtesySemantic.HANDOFF
    assert plan.presentation is CourtesyPresentation.FINITE
    assert plan.frames == signature.geometry.frames
    assert plan.cadence is signature.cadence
    assert plan.static_slots == signature.geometry.static_slots
    assert plan.accessibility_label == "Handoff ready"
    assert plan.reduce_motion_substituted is False
    assert plan.has_motion is True


@pytest.mark.parametrize("semantic", tuple(CourtesySemantic))
def test_reduce_motion_preserves_meaning_with_a_static_spatial_substitute(
    semantic: CourtesySemantic,
) -> None:
    signature = signature_for_semantic(semantic)

    plan = plan_courtesy_signature(semantic, reduce_motion=True)

    assert plan.identifier == signature.identifier
    assert plan.semantic is semantic
    assert plan.presentation is CourtesyPresentation.STATIC
    assert plan.frames == (signature.geometry.static_slots,)
    assert plan.cadence is None
    assert plan.static_slots == signature.geometry.static_slots
    assert plan.accessibility_label == signature.accessibility_label
    assert plan.reduce_motion_substituted is True
    assert plan.has_motion is False


def test_registry_rejects_identifier_and_semantic_collisions() -> None:
    completion = COURTESY_SIGNATURES[0]
    recovery = COURTESY_SIGNATURES[1]

    with pytest.raises(CourtesySignatureError, match="identifier collision"):
        CourtesySignatureRegistry((completion, completion))
    with pytest.raises(CourtesySignatureError, match="semantic collision"):
        CourtesySignatureRegistry(
            (
                completion,
                replace(
                    recovery,
                    identifier="jrbar.courtesy.recovery-alternate.v1",
                    semantic=CourtesySemantic.COMPLETION,
                ),
            )
        )


@pytest.mark.parametrize(
    ("replacement", "message"),
    (
        ({"geometry": COURTESY_SIGNATURES[0].geometry}, "geometry collision"),
        ({"cadence": COURTESY_SIGNATURES[0].cadence}, "cadence collision"),
        (
            {
                "geometry": replace(
                    COURTESY_SIGNATURES[1].geometry,
                    static_slots=COURTESY_SIGNATURES[0].geometry.static_slots,
                )
            },
            "static geometry collision",
        ),
        (
            {"accessibility_label": COURTESY_SIGNATURES[0].accessibility_label},
            "accessibility label collision",
        ),
    ),
)
def test_registry_rejects_non_color_identity_collisions(
    replacement: dict[str, object],
    message: str,
) -> None:
    completion = COURTESY_SIGNATURES[0]
    recovery = replace(COURTESY_SIGNATURES[1], **replacement)

    with pytest.raises(CourtesySignatureError, match=message):
        CourtesySignatureRegistry((completion, recovery))


def test_unsafe_or_unbounded_cadence_metadata_is_rejected() -> None:
    with pytest.raises(CourtesySignatureError, match="2 Hz"):
        CadencePulse(active_ms=100, rest_ms=100)
    with pytest.raises(CourtesySignatureError, match="at most three"):
        CadenceSignature(
            name="too-many",
            pulses=(CadencePulse(250, 250),) * 4,
        )


@pytest.mark.parametrize(
    ("frames", "static_slots", "message"),
    (
        ((), (2,), "at least one frame"),
        (((0, 5),), (2,), "slot range"),
        (((0, 1),), (1, 0), "ordered unique slots"),
        (((0, 1),), (), "static geometry"),
    ),
)
def test_geometry_is_bounded_and_canonical(
    frames: tuple[tuple[int, ...], ...],
    static_slots: tuple[int, ...],
    message: str,
) -> None:
    with pytest.raises(CourtesySignatureError, match=message):
        GeometrySignature(
            frames=frames,
            static_slots=static_slots,
            spatial_description="A bounded spatial cue.",
        )


def test_plans_and_nested_signature_values_are_immutable() -> None:
    plan = plan_courtesy_signature(CourtesySemantic.COMPLETION)
    signature = signature_for_semantic(CourtesySemantic.COMPLETION)

    with pytest.raises(FrozenInstanceError):
        plan.presentation = CourtesyPresentation.STATIC  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        signature.geometry.static_slots = (2,)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        signature.cadence.name = "changed"  # type: ignore[misc]


def test_lookup_and_planning_reject_non_enum_semantics_and_non_boolean_policy() -> None:
    with pytest.raises(CourtesySignatureError, match="semantic must be"):
        signature_for_semantic("completion")  # type: ignore[arg-type]
    with pytest.raises(CourtesySignatureError, match="reduce motion must be"):
        plan_courtesy_signature(
            CourtesySemantic.COMPLETION,
            reduce_motion=1,  # type: ignore[arg-type]
        )
