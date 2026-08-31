import pytest

from sidepulse import colors as colors_module
from sidepulse.effect_registry import (
    SAFE_BLINK_CADENCES,
    EffectDefinition,
    EffectParameter,
    EffectRegistry,
    EffectRegistryError,
    blink_cadence,
    get_effect,
    list_effects,
    normalize_effect_parameters,
    provider_animation_effects,
    reduced_motion_effect,
)


def test_catalog_is_deterministic_and_lookup_is_exact() -> None:
    effects = list_effects()
    assert tuple(item.identifier for item in effects) == tuple(sorted(item.identifier for item in effects))
    assert get_effect("pulse").meaning == "periodic activity"
    assert get_effect("missing") is None


def test_surface_and_safety_filters() -> None:
    assert all("status_bar" in effect.surfaces for effect in list_effects(surface="status_bar"))
    assert all(effect.safety == "attention" for effect in list_effects(safety="attention"))


def test_reduced_motion_uses_declared_fallback() -> None:
    assert reduced_motion_effect("rainbow").identifier == "none"
    assert reduced_motion_effect("none").identifier == "none"


def test_provider_animation_catalog_is_authoritative_and_keeps_ui_order() -> None:
    effects = provider_animation_effects()

    assert tuple(effect.identifier for effect in effects) == (
        colors_module.PROVIDER_ANIMATION_CHOICES
    )
    assert tuple(effect.label for effect in effects) == tuple(
        colors_module.PROVIDER_ANIMATION_LABELS[identifier]
        for identifier in colors_module.PROVIDER_ANIMATION_CHOICES
    )
    assert tuple(effect.description for effect in effects) == tuple(
        colors_module.PROVIDER_ANIMATION_DESCRIPTIONS[identifier]
        for identifier in colors_module.PROVIDER_ANIMATION_CHOICES
    )


def test_provider_animation_reduced_motion_fallback_is_steady() -> None:
    for effect in provider_animation_effects():
        assert effect.reduce_motion_fallback == colors_module.MOTION_STEADY
        assert reduced_motion_effect(effect.identifier).identifier == "steady"


def test_provider_animation_metadata_is_structured_and_deterministic() -> None:
    effects = provider_animation_effects()

    assert all(effect.role != "general" for effect in effects)
    assert all(effect.parameter_metadata for effect in effects)
    assert all(
        tuple(adaptation.surface for adaptation in effect.surface_adaptations)
        == effect.surfaces
        for effect in effects
    )
    assert tuple(parameter.name for parameter in get_effect("chase").parameter_metadata) == (
        "duration_seconds",
        "direction",
        "spacing",
        "softness",
    )
    assert get_effect("aurora").energy == "high"
    assert get_effect("steady").energy == "low"


def test_parameter_normalization_applies_defaults_in_schema_order() -> None:
    normalized = normalize_effect_parameters(
        "chase",
        {"direction": "reverse", "duration_seconds": 3},
    )

    assert normalized == {
        "duration_seconds": 3.0,
        "direction": "reverse",
        "spacing": 1,
        "softness": 1.0,
    }
    assert tuple(normalized) == (
        "duration_seconds",
        "direction",
        "spacing",
        "softness",
    )


@pytest.mark.parametrize(
    ("effect_id", "parameters", "message"),
    [
        ("chase", {"unknown": 1}, "unknown parameters"),
        ("chase", {"duration_seconds": True}, "number"),
        ("chase", {"spacing": 0}, "at least"),
        ("chase", {"direction": "sideways"}, "one of"),
        ("gradient", {"palette": ["#112233"]}, "at least"),
        ("gradient", {"palette": ["#112233", "bad"]}, "hex color"),
    ],
)
def test_parameter_normalization_rejects_unknown_or_unsafe_values(
    effect_id: str,
    parameters: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(EffectRegistryError, match=message):
        normalize_effect_parameters(effect_id, parameters)


def test_palette_normalization_is_immutable_and_canonical() -> None:
    palette = ["#aabbcc", "#123456"]

    normalized = normalize_effect_parameters("gradient", {"palette": palette})
    palette.append("#FFFFFF")

    assert normalized["palette"] == ("#AABBCC", "#123456")


def test_blink_uses_only_named_cadences_below_the_existing_flash_ceiling() -> None:
    assert tuple(cadence.identifier for cadence in SAFE_BLINK_CADENCES) == (
        "calm",
        "deliberate",
        "double",
    )
    assert all(cadence.peak_flash_hz <= 2.0 for cadence in SAFE_BLINK_CADENCES)
    assert normalize_effect_parameters("blink", {})["cadence"] == "calm"
    assert blink_cadence("double").pulses == 2
    with pytest.raises(KeyError):
        blink_cadence("custom")


def test_parameter_definition_rejects_invalid_schema_defaults() -> None:
    with pytest.raises(EffectRegistryError, match="default"):
        EffectParameter(
            "direction",
            "choice",
            "sideways",
            "Direction of travel.",
            choices=("forward", "reverse"),
        )
    with pytest.raises(EffectRegistryError, match="bounds"):
        EffectParameter(
            "speed",
            "number",
            1.0,
            "Speed multiplier.",
            minimum=2.0,
            maximum=1.0,
        )


def test_duplicate_identifiers_must_not_conflict() -> None:
    item = EffectDefinition("x", "X", "x", "x")
    assert EffectRegistry((item, item)).get("x") == item
    with pytest.raises(EffectRegistryError):
        EffectRegistry((item, EffectDefinition("x", "Other", "x", "x")))


@pytest.mark.parametrize("kwargs", [{"safety": "danger"}, {"energy": "extreme"}, {"version": 0}])
def test_definition_validates_contract(kwargs: dict[str, object]) -> None:
    with pytest.raises(EffectRegistryError):
        EffectDefinition("x", "X", "x", "x", **kwargs)
