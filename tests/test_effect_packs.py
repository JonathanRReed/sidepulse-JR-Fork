import json

import pytest

from sidepulse.effect_packs import (
    EffectPack,
    EffectPackError,
    LicenseMetadata,
    effect_definitions_from_pack,
    export_pack,
    migrate_pack,
    preview_pack,
    registry_with_pack,
    validate_pack,
)
from sidepulse.effect_registry import EffectRegistry


def pack(**overrides):
    value = {"id": "ambient", "name": "Ambient", "version": 2, "effects": [{"id": "pulse", "label": "Pulse", "speed": 2}], "safety": {"data_only": True, "network": False}, "accessibility": {"reduced_motion": True, "high_contrast": True}}
    value.update(overrides)
    return value


def test_validates_and_previews_data_only_pack():
    result = validate_pack(pack())
    assert result.pack_id == "ambient"
    assert preview_pack(pack())[0]["label"] == "Pulse"


def test_migrates_v1_defaults():
    migrated = migrate_pack({"id": "old", "name": "Old", "version": 1, "effects": []})
    assert migrated["version"] == 2
    assert validate_pack(migrated).safety["data_only"] is True


def test_rejects_executable_keys_and_markers():
    with pytest.raises(EffectPackError):
        validate_pack(pack(effects=[{"id": "x", "label": "x", "script": "pulse()"}]))
    with pytest.raises(EffectPackError):
        validate_pack(pack(effects=[{"id": "x", "label": "run python now"}]))


def test_allows_benign_marker_words_and_http_urls():
    validated = validate_pack(
        pack(
            effects=[
                {
                    "id": "x",
                    "label": "shimmer",
                    "description": "https://example.com/bin/shimmer",
                }
            ]
        )
    )
    assert validated.effects[0]["label"] == "shimmer"


def test_rejects_code_markers_with_token_boundaries():
    for marker in ("python3 -c 'pass'", "javascript:alert(1)", "sh -c true"):
        with pytest.raises(EffectPackError):
            validate_pack(pack(effects=[{"id": "x", "label": marker}]))


def test_requires_safety_and_accessibility_contract():
    with pytest.raises(EffectPackError):
        validate_pack(pack(safety={"data_only": True, "network": True}))
    with pytest.raises(EffectPackError):
        validate_pack(pack(accessibility={"reduced_motion": True}))


def test_export_is_deterministic_json():
    first = export_pack(pack())
    assert first == export_pack(json.loads(first))
    assert b"\n" not in first


def test_license_metadata_is_optional_canonical_and_round_trips():
    payload = pack(
        license={
            "spdx_id": "CC-BY-4.0",
            "label": "Creative Commons Attribution 4.0",
            "source_url": "https://example.com/source",
            "attribution_url": "https://example.com/attribution",
        }
    )

    validated = validate_pack(payload)
    assert validated.license == LicenseMetadata(
        spdx_id="CC-BY-4.0",
        label="Creative Commons Attribution 4.0",
        source_url="https://example.com/source",
        attribution_url="https://example.com/attribution",
    )
    encoded = export_pack(validated)
    assert json.loads(encoded)["license"] == payload["license"]
    assert validate_pack(json.loads(encoded)).license == validated.license


def test_license_metadata_rejects_invalid_or_unsafe_values():
    with pytest.raises(EffectPackError, match="SPDX"):
        validate_pack(pack(license={"spdx_id": "not an SPDX id", "label": "License"}))
    with pytest.raises(EffectPackError, match=r"http\(s\)"):
        validate_pack(pack(license={"spdx_id": "MIT", "label": "MIT", "source_url": "file:///tmp/license"}))
    for field in ("source_url", "attribution_url"):
        with pytest.raises(EffectPackError, match="credentials"):
            validate_pack(
                pack(
                    license={
                        "spdx_id": "MIT",
                        "label": "MIT",
                        field: "https://user:password@example.com/license",
                    }
                )
            )
    with pytest.raises(EffectPackError, match="unsupported fields"):
        validate_pack(pack(license={"spdx_id": "MIT", "label": "MIT", "unknown": "value"}))


def test_license_metadata_is_absent_for_legacy_manifests():
    validated = validate_pack(pack())
    assert validated.license is None
    assert "license" not in json.loads(export_pack(validated))


def test_pack_and_preview_data_are_recursively_copy_isolated():
    payload = pack(
        effects=[
            {
                "id": "pulse",
                "label": "Pulse",
                "settings": {"colors": ["red", "blue"]},
            }
        ]
    )
    validated = validate_pack(payload)
    original_export = export_pack(validated)

    payload["effects"][0]["settings"]["colors"].append("green")
    assert export_pack(validated) == original_export

    with pytest.raises(TypeError):
        validated.effects[0]["settings"]["colors"] = ("green",)
    with pytest.raises(AttributeError):
        validated.effects[0]["settings"]["colors"].append("green")

    preview = preview_pack(validated)
    preview[0]["parameters"]["settings"]["colors"].append("green")
    assert export_pack(validated) == original_export


def test_adapts_data_only_pack_to_namespaced_registry_definitions():
    payload = pack(
        effects=[
            {"id": "steady", "label": "Steady", "brightness": 0.4},
            {
                "id": "wave",
                "label": "Wave",
                "description": "A slow wave.",
                "surfaces": ["screen_bar", "settings_preview"],
                "reduce_motion_fallback": "steady",
                "speed": 2,
            },
        ]
    )

    definitions = effect_definitions_from_pack(payload)
    registry = registry_with_pack(EffectRegistry(), payload)

    assert tuple(definition.identifier for definition in definitions) == (
        "pack:ambient:steady",
        "pack:ambient:wave",
    )
    assert definitions[1].parameters == ("speed",)
    assert definitions[1].surfaces == ("screen_bar", "settings_preview")
    assert registry.reduced_motion("pack:ambient:wave").identifier == (
        "pack:ambient:steady"
    )


def test_rejects_duplicate_effects_unknown_fallbacks_and_registry_collisions():
    with pytest.raises(EffectPackError, match="duplicate effect identifier"):
        validate_pack(
            pack(
                effects=[
                    {"id": "pulse", "label": "Pulse"},
                    {"id": "pulse", "label": "Other"},
                ]
            )
        )
    with pytest.raises(EffectPackError, match="unknown reduced-motion fallback"):
        effect_definitions_from_pack(
            pack(
                effects=[
                    {
                        "id": "wave",
                        "label": "Wave",
                        "reduce_motion_fallback": "missing",
                    }
                ]
            )
        )

    registry = registry_with_pack(EffectRegistry(), pack())
    with pytest.raises(EffectPackError, match="already registered"):
        registry_with_pack(registry, pack())


def test_adapter_revalidates_effect_pack_instances_and_rejects_executable_content():
    unsafe = EffectPack(
        pack_id="ambient",
        name="Ambient",
        version=2,
        effects=({"id": "pulse", "label": "Pulse", "handler": "run"},),
        safety={"data_only": True, "network": False},
        accessibility={"reduced_motion": True, "high_contrast": True},
    )

    with pytest.raises(EffectPackError, match="executable content"):
        effect_definitions_from_pack(unsafe)
