"""Pure, fail-closed planning for a Creator Micro 2 JR-Bar key layer.

The base plan claims only the 13-key matrix. It preserves dial and joystick
mappings so a later, separately previewed setup option can claim those inputs.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

_MAX_JSON_BYTES = 64 * 1024
_MAX_JSON_DEPTH = 64
_KEYMAP_ROW_LENGTHS = (2, 4, 4, 3)


@dataclass(frozen=True, slots=True)
class KeymapPlan:
    original_json: str
    proposed_json: str
    original_digest: str
    proposed_digest: str
    changes: tuple[str, ...]
    profile_index: int
    layer_index: int


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key {key!r}")
        value[key] = item
    return value


def _check_depth(value: Any) -> None:
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > _MAX_JSON_DEPTH:
            raise ValueError(f"JSON structure exceeds maximum depth {_MAX_JSON_DEPTH}")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def _load_keymap_json(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise ValueError("keymap JSON must be text")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("keymap JSON must be valid UTF-8") from exc
    if len(encoded) > _MAX_JSON_BYTES:
        raise ValueError("keymap JSON exceeds the 64 KiB limit")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except RecursionError as exc:
        raise ValueError("JSON structure exceeds maximum depth") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("invalid keymap JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("keymap JSON root must be an object")
    _check_depth(value)
    return value


def _canonical_json(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    try:
        canonical.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("keymap JSON must contain valid UTF-8 text") from exc
    return canonical


def keymap_digest(raw: str) -> str:
    """Hash a safely parsed keymap independent of whitespace and object order."""

    canonical = _canonical_json(_load_keymap_json(raw)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


canonical_keymap_digest = keymap_digest


def _active_profile(config: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    profile_index = config.get("activeProfileId")
    profiles = config.get("profiles")
    if type(profile_index) is not int or not isinstance(profiles, list):
        raise ValueError("activeProfileId must be an integer profile index")
    if profile_index < 0 or profile_index >= len(profiles):
        raise ValueError("activeProfileId is outside the available profile indexes")
    if not all(isinstance(profile, dict) for profile in profiles):
        raise ValueError("profiles must contain JSON objects")

    profiles_with_ids = [index for index, profile in enumerate(profiles) if "id" in profile]
    if profiles_with_ids:
        if len(profiles_with_ids) != len(profiles):
            raise ValueError("ambiguous mixed profile id layout")
        if any(type(profile["id"]) is not int for profile in profiles):
            raise ValueError("explicit profile ids must be integers")
        matching_ids = [index for index, profile in enumerate(profiles) if profile["id"] == profile_index]
        if matching_ids != [profile_index]:
            raise ValueError("ambiguous or conflicting active profile id layout")
    return profile_index, profiles[profile_index]


def _active_layer(profile: dict[str, Any], status: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if not isinstance(status, dict) or "layer_index" not in status:
        raise ValueError("device status.layer_index is required")
    reported_index = status["layer_index"]
    if type(reported_index) is not int:
        raise ValueError("device status.layer_index must be an integer")
    if reported_index < 1:
        raise ValueError("device status.layer_index must be 1-based")
    layers = profile.get("layers")
    if not isinstance(layers, list) or reported_index > len(layers):
        raise ValueError("device status.layer_index is outside the available layers")
    layer = layers[reported_index - 1]
    if not isinstance(layer, dict):
        raise ValueError("active layer must be a JSON object")
    return reported_index - 1, layer


def _keymap(layer: dict[str, Any]) -> list[list[str]]:
    layout = layer.get("layout")
    if not isinstance(layout, dict):
        raise ValueError("active layer layout must be a JSON object")
    keymap = layout.get("keymap")
    if (
        not isinstance(keymap, list)
        or len(keymap) != len(_KEYMAP_ROW_LENGTHS)
        or any(not isinstance(row, list) or len(row) != width for row, width in zip(keymap, _KEYMAP_ROW_LENGTHS))
        or any(not isinstance(key, str) for row in keymap for key in row)
    ):
        raise ValueError("unsupported Creator Micro 2 keymap shape; expected [2, 4, 4, 3] string rows")
    return keymap


def plan_keymap(raw: str, status: dict[str, Any]) -> KeymapPlan:
    """Plan AG00..AG12 without I/O, preserving dial and joystick mappings."""

    original = _load_keymap_json(raw)
    proposed = copy.deepcopy(original)
    profile_index, profile = _active_profile(proposed)
    layer_index, layer = _active_layer(profile, status)
    keymap = _keymap(layer)

    changes: list[str] = []
    key_index = 0
    for row in keymap:
        for column, old_keycode in enumerate(row):
            new_keycode = f"KV_OAI_AG{key_index:02d}"
            if old_keycode != new_keycode:
                changes.append(
                    f"Key {key_index}: {old_keycode} -> {new_keycode}; "
                    "replaces its normal keystroke with a JR-Bar device input."
                )
                row[column] = new_keycode
            key_index += 1

    proposed_json = _canonical_json(proposed)
    if len(proposed_json.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError("proposed keymap JSON exceeds the 64 KiB limit")
    original_digest = keymap_digest(raw)
    proposed_digest = hashlib.sha256(proposed_json.encode("utf-8")).hexdigest()
    return KeymapPlan(
        original_json=raw,
        proposed_json=proposed_json,
        original_digest=original_digest,
        proposed_digest=proposed_digest,
        changes=tuple(changes),
        profile_index=profile_index,
        layer_index=layer_index,
    )
