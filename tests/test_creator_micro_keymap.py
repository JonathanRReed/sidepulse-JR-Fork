from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from sidepulse.creator_micro_keymap import keymap_digest, plan_keymap


def _stock_config() -> dict[str, object]:
    return {
        "schema": 7,
        "activeProfileId": 0,
        "profiles": [
            {
                "name": "Work",
                "macros": [{"name": "keep", "steps": ["A", {"delay": 10}]}],
                "layers": [
                    {
                        "name": "Base",
                        "layout": {
                            "keymap": [
                                ["KC_F13", "KC_F14"],
                                ["KC_F15", "KC_F16", "KC_F17", "KC_F18"],
                                ["KC_F19", "KC_F20", "KC_F21", "KC_F22"],
                                ["KC_F23", "KC_NONE", "KC_F24"],
                            ],
                            "encoders": [["KC_VOLU", "KC_VOLD", "KC_MPLY"]],
                            "joystick": {
                                "type": "RADIAL",
                                "sectors": [{"k": "KC_P6", "a1": 0.9375, "a2": 0.0625}],
                            },
                            "vendorExtension": {"enabled": True},
                        },
                    },
                    {"name": "Other", "layout": {"keymap": [["untouched"]]}},
                ],
            },
            {"name": "Gaming", "layers": [{"layout": {"keymap": [["also untouched"]]}}]},
        ],
        "topLevelExtension": {"keep": [1, 2, 3]},
    }


def test_plan_replaces_only_the_active_matrix_and_describes_lost_key_behavior() -> None:
    original = _stock_config()
    raw = json.dumps(original, indent=2)

    plan = plan_keymap(raw, {"layer_index": 1, "battery": 82})

    proposed = json.loads(plan.proposed_json)
    assert proposed["profiles"][0]["layers"][0]["layout"]["keymap"] == [
        ["KV_OAI_AG00", "KV_OAI_AG01"],
        ["KV_OAI_AG02", "KV_OAI_AG03", "KV_OAI_AG04", "KV_OAI_AG05"],
        ["KV_OAI_AG06", "KV_OAI_AG07", "KV_OAI_AG08", "KV_OAI_AG09"],
        ["KV_OAI_AG10", "KV_OAI_AG11", "KV_OAI_AG12"],
    ]
    assert proposed["profiles"][0]["layers"][0]["layout"]["encoders"] == original["profiles"][0]["layers"][0]["layout"]["encoders"]
    assert proposed["profiles"][0]["layers"][0]["layout"]["joystick"] == original["profiles"][0]["layers"][0]["layout"]["joystick"]
    assert proposed["profiles"][0]["macros"] == original["profiles"][0]["macros"]
    assert proposed["profiles"][0]["layers"][1] == original["profiles"][0]["layers"][1]
    assert proposed["profiles"][1] == original["profiles"][1]
    assert proposed["topLevelExtension"] == original["topLevelExtension"]
    assert plan.original_json == raw
    assert plan.profile_index == 0
    assert plan.layer_index == 0
    assert len(plan.changes) == 13
    assert plan.changes[0] == (
        "Key 0: KC_F13 -> KV_OAI_AG00; replaces its normal keystroke with a JR-Bar device input."
    )
    assert plan.original_digest == keymap_digest(raw)
    assert plan.proposed_digest == keymap_digest(plan.proposed_json)


def test_plan_is_frozen_and_an_already_planned_map_has_no_changes() -> None:
    first = plan_keymap(json.dumps(_stock_config()), {"layer_index": 1})
    second = plan_keymap(first.proposed_json, {"layer_index": 1})

    assert second.changes == ()
    assert second.original_digest == second.proposed_digest
    with pytest.raises(FrozenInstanceError):
        second.profile_index = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ('{"activeProfileId":0,"activeProfileId":0,"profiles":[]}', "duplicate"),
        ('{"activeProfileId":NaN,"profiles":[]}', "non-finite"),
        ("[]", "object"),
    ],
)
def test_plan_rejects_unsafe_json(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        plan_keymap(raw, {"layer_index": 1})


def test_plan_rejects_oversize_and_deep_json_before_mutation() -> None:
    too_large = json.dumps({"padding": "x" * (64 * 1024)})
    deep: object = "leaf"
    for _ in range(70):
        deep = [deep]

    with pytest.raises(ValueError, match="64 KiB"):
        plan_keymap(too_large, {"layer_index": 1})
    with pytest.raises(ValueError, match="depth"):
        plan_keymap(json.dumps({"deep": deep}), {"layer_index": 1})


def test_plan_rejects_a_generated_document_over_64_kib() -> None:
    config = _stock_config()
    compact = json.dumps(config, separators=(",", ":"))
    config["padding"] = "x" * (64 * 1024 - len(compact.encode("utf-8")) - len(',"padding":""}'))
    raw = json.dumps(config, separators=(",", ":"))
    assert len(raw.encode("utf-8")) <= 64 * 1024

    with pytest.raises(ValueError, match=r"proposed.*64 KiB"):
        plan_keymap(raw, {"layer_index": 1})


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ({}, "layer_index"),
        ({"layer_index": 0}, "1-based"),
        ({"layer_index": True}, "integer"),
        ({"layer_index": 3}, "available layers"),
    ],
)
def test_plan_requires_an_in_range_one_based_status_layer(status: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        plan_keymap(json.dumps(_stock_config()), status)


def test_plan_rejects_ambiguous_profile_identifiers() -> None:
    config = _stock_config()
    config["activeProfileId"] = 0
    config["profiles"][0]["id"] = 9  # type: ignore[index]
    config["profiles"][1]["id"] = 0  # type: ignore[index]

    with pytest.raises(ValueError, match="ambiguous"):
        plan_keymap(json.dumps(config), {"layer_index": 1})


@pytest.mark.parametrize("profile_id", [False, 0.0, True, 1.0])
def test_plan_rejects_non_integer_explicit_profile_ids(profile_id: object) -> None:
    config = _stock_config()
    config["profiles"][0]["id"] = profile_id  # type: ignore[index]
    config["profiles"][1]["id"] = 1  # type: ignore[index]

    with pytest.raises(ValueError, match="profile ids must be integers"):
        plan_keymap(json.dumps(config), {"layer_index": 1})


@pytest.mark.parametrize(
    "active_profile",
    [True, "0", -1, 2],
)
def test_plan_rejects_non_index_active_profiles(active_profile: object) -> None:
    config = _stock_config()
    config["activeProfileId"] = active_profile

    with pytest.raises(ValueError, match="activeProfileId"):
        plan_keymap(json.dumps(config), {"layer_index": 1})


def test_plan_rejects_an_unsupported_matrix_shape() -> None:
    config = _stock_config()
    config["profiles"][0]["layers"][0]["layout"]["keymap"][3].append("extra")  # type: ignore[index,union-attr]

    with pytest.raises(ValueError, match=r"\[2, 4, 4, 3\]"):
        plan_keymap(json.dumps(config), {"layer_index": 1})


def test_plan_rejects_unpaired_unicode_surrogates_as_value_errors() -> None:
    config = _stock_config()
    config["label"] = "\ud800"

    with pytest.raises(ValueError, match="valid UTF-8"):
        plan_keymap(json.dumps(config), {"layer_index": 1})


def test_canonical_digest_ignores_object_order_and_whitespace_but_not_array_order() -> None:
    assert keymap_digest('{"b": 2, "a": [1, 3]}') == keymap_digest('{"a":[1,3],"b":2}')
    assert keymap_digest('{"a":[1,3],"b":2}') != keymap_digest('{"a":[3,1],"b":2}')
