"""Safe, deterministic data-only effect packs.

Packs are deliberately boring JSON data.  They are never imported, executed,
or resolved as plugins, which makes them suitable for sharing and previewing.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse

from .effect_registry import EffectDefinition, EffectRegistry, EffectRegistryError

CURRENT_PACK_VERSION = 2
MAX_PACK_BYTES = 256_000
MAX_EFFECTS = 128
_CODE_KEYS = frozenset(
    {
        "callback",
        "code",
        "command",
        "entrypoint",
        "executable",
        "handler",
        "hook",
        "import",
        "module",
        "plugin",
        "script",
    }
)
_CODE_MARKERS = (
    re.compile(
        r"(?<![A-Za-z0-9_])(?:python(?:\d+(?:\.\d+)*)?|javascript|bash|sh|eval|exec|subprocess)(?![A-Za-z0-9_])",
        re.I,
    ),
    re.compile(r"(?<![A-Za-z0-9_])import(?:\s+[A-Za-z_]|\s*[\"'])", re.I),
    re.compile(r"(?<![A-Za-z0-9_])require\s*\(", re.I),
    re.compile(r"(?<![A-Za-z0-9_])/bin/", re.I),
)
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
_SPDX_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*\Z")
_EFFECT_METADATA_KEYS = frozenset(
    {
        "id",
        "label",
        "description",
        "meaning",
        "surfaces",
        "safety",
        "energy",
        "reduce_motion_fallback",
    }
)


class EffectPackError(ValueError):
    """Raised when a pack is malformed or contains executable content."""


def _freeze_json(value: Any) -> Any:
    """Detach and recursively freeze already validated JSON-like data."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    """Return detached JSON containers for serialization and UI projection."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return deepcopy(value)


@dataclass(frozen=True, slots=True)
class LicenseMetadata:
    """Attribution facts for a pack, kept separate from executable pack data."""

    spdx_id: str
    label: str
    source_url: str | None = None
    attribution_url: str | None = None

    @property
    def spdx_identifier(self) -> str:
        return self.spdx_id

    @property
    def human_label(self) -> str:
        return self.label


# This remains a data contract, rather than a network-backed SPDX resolver.
_LICENSE_KEYS = frozenset({"spdx_id", "label", "source_url", "attribution_url"})
_MAX_LICENSE_URL_CHARACTERS = 2048


def _optional_url(value: object, field: str) -> str | None:
    if value is None:
        return None
    url = _text(value, field, _MAX_LICENSE_URL_CHARACTERS)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EffectPackError(f"{field} must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise EffectPackError(f"{field} must not include URL credentials")
    if any(character.isspace() for character in url) or not url.isprintable():
        raise EffectPackError(f"{field} must be an absolute http(s) URL")
    return url


def _license_metadata(value: object) -> LicenseMetadata | None:
    if value is None:
        return None
    if isinstance(value, LicenseMetadata):
        value = {
            "spdx_id": value.spdx_id,
            "label": value.label,
            "source_url": value.source_url,
            "attribution_url": value.attribution_url,
        }
    if not isinstance(value, Mapping):
        raise EffectPackError("license metadata must be an object")
    unknown = set(value) - _LICENSE_KEYS
    if unknown:
        raise EffectPackError("license metadata contains unsupported fields")
    spdx_id = _text(value.get("spdx_id"), "license.spdx_id", 128)
    if _SPDX_IDENTIFIER.fullmatch(spdx_id) is None:
        raise EffectPackError("license.spdx_id must be an SPDX identifier")
    label = _text(value.get("label"), "license.label", 240)
    return LicenseMetadata(
        spdx_id=spdx_id,
        label=label,
        source_url=_optional_url(value.get("source_url"), "license.source_url"),
        attribution_url=_optional_url(
            value.get("attribution_url"), "license.attribution_url"
        ),
    )


@dataclass(frozen=True, slots=True)
class EffectPack:
    pack_id: str
    name: str
    version: int
    effects: tuple[Mapping[str, Any], ...]
    safety: Mapping[str, Any]
    accessibility: Mapping[str, Any]
    license: LicenseMetadata | None = None

    @property
    def license_metadata(self) -> LicenseMetadata | None:
        """Compatibility spelling for callers that use the descriptive name."""
        return self.license


def _pack_mapping(pack: EffectPack) -> dict[str, Any]:
    payload = {
        "accessibility": _thaw_json(pack.accessibility),
        "effects": [_thaw_json(effect) for effect in pack.effects],
        "id": pack.pack_id,
        "name": pack.name,
        "safety": _thaw_json(pack.safety),
        "version": pack.version,
    }
    if pack.license is not None:
        license_payload: dict[str, str] = {
            "label": pack.license.label,
            "spdx_id": pack.license.spdx_id,
        }
        if pack.license.source_url is not None:
            license_payload["source_url"] = pack.license.source_url
        if pack.license.attribution_url is not None:
            license_payload["attribution_url"] = pack.license.attribution_url
        payload["license"] = license_payload
    return payload


def _reject_code(value: object, path: str = "pack") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.lower() in _CODE_KEYS:
                raise EffectPackError(f"{path} contains executable content")
            _reject_code(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_code(child, f"{path}[{index}]")
    elif isinstance(value, str):
        parsed = urlparse(value)
        is_http_url = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and value.isprintable()
            and not any(character.isspace() for character in value)
        )
        if not is_http_url and any(marker.search(value) for marker in _CODE_MARKERS):
            raise EffectPackError(f"{path} contains executable content")


def _text(value: object, field: str, limit: int = 160) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise EffectPackError(f"{field} must be a non-empty bounded string")
    return value.strip()


def _identifier(value: object, field: str) -> str:
    identifier = _text(value, field, 80)
    if _IDENTIFIER.fullmatch(identifier) is None:
        raise EffectPackError(f"{field} must be a lowercase data identifier")
    return identifier


def _bounded_json_size(payload: Mapping[str, Any]) -> None:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as error:
        raise EffectPackError("pack must contain only JSON data") from error
    if len(encoded) > MAX_PACK_BYTES:
        raise EffectPackError("pack exceeds size limit")


def migrate_pack(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate supported legacy payloads into the current schema."""
    if not isinstance(payload, Mapping):
        raise EffectPackError("pack must be an object")
    version = payload.get("version", payload.get("schema_version", 1))
    if version == 1:
        result = deepcopy(dict(payload))
        result["version"] = CURRENT_PACK_VERSION
        result.setdefault("safety", {"data_only": True, "network": False})
        result.setdefault("accessibility", {"reduced_motion": True, "high_contrast": True})
        return result
    return deepcopy(dict(payload))


def validate_pack(payload: EffectPack | Mapping[str, Any]) -> EffectPack:
    """Validate and normalize a pack without performing any side effects."""
    if isinstance(payload, EffectPack):
        payload = _pack_mapping(payload)
    migrated = migrate_pack(payload)
    if isinstance(migrated.get("license"), LicenseMetadata):
        license_value = migrated["license"]
        migrated["license"] = {
            "spdx_id": license_value.spdx_id,
            "label": license_value.label,
            "source_url": license_value.source_url,
            "attribution_url": license_value.attribution_url,
        }
    _bounded_json_size(migrated)
    _reject_code(migrated)
    if migrated.get("version") != CURRENT_PACK_VERSION:
        raise EffectPackError("unsupported pack version")
    pack_id = _identifier(migrated.get("id", migrated.get("pack_id")), "id")
    name = _text(migrated.get("name"), "name")
    effects = migrated.get("effects")
    if not isinstance(effects, list) or len(effects) > MAX_EFFECTS:
        raise EffectPackError("effects must be a bounded list")
    normalized: list[Mapping[str, Any]] = []
    effect_ids: set[str] = set()
    for index, effect in enumerate(effects):
        if not isinstance(effect, Mapping):
            raise EffectPackError(f"effects[{index}] must be an object")
        effect_id = _identifier(effect.get("id"), f"effects[{index}].id")
        if effect_id in effect_ids:
            raise EffectPackError(f"duplicate effect identifier: {effect_id}")
        effect_ids.add(effect_id)
        label = _text(effect.get("label", effect_id), f"effects[{index}].label")
        normalized.append(
            _freeze_json({**dict(effect), "id": effect_id, "label": label})
        )
    safety = migrated.get("safety", {})
    accessibility = migrated.get("accessibility", {})
    if not isinstance(safety, Mapping) or safety.get("data_only") is not True or safety.get("network", False) is not False:
        raise EffectPackError("safety metadata must declare data_only and no network")
    if not isinstance(accessibility, Mapping):
        raise EffectPackError("accessibility metadata must be an object")
    required = ("reduced_motion", "high_contrast")
    if any(accessibility.get(key) is not True for key in required):
        raise EffectPackError("accessibility metadata must support reduced motion and high contrast")
    license_metadata = _license_metadata(migrated.get("license"))
    return EffectPack(
        pack_id,
        name,
        CURRENT_PACK_VERSION,
        tuple(normalized),
        _freeze_json(safety),
        _freeze_json(accessibility),
        license_metadata,
    )


def effect_definitions_from_pack(
    payload: EffectPack | Mapping[str, Any],
) -> tuple[EffectDefinition, ...]:
    """Adapt one validated data-only pack into namespaced registry definitions."""

    pack = validate_pack(_pack_mapping(payload) if isinstance(payload, EffectPack) else payload)
    local_ids = {str(effect["id"]) for effect in pack.effects}
    definitions: list[EffectDefinition] = []
    for effect in pack.effects:
        effect_id = str(effect["id"])
        fallback = effect.get("reduce_motion_fallback")
        if fallback is not None:
            fallback = _identifier(fallback, f"effects[{effect_id}].reduce_motion_fallback")
            if fallback not in local_ids:
                raise EffectPackError(
                    f"effect {effect_id} has an unknown reduced-motion fallback"
                )
        surfaces_value = effect.get("surfaces", ["settings_preview"])
        if not isinstance(surfaces_value, (list, tuple)) or not surfaces_value:
            raise EffectPackError(f"effect {effect_id} surfaces must be a non-empty list")
        surfaces = tuple(
            _identifier(surface, f"effects[{effect_id}].surfaces")
            for surface in surfaces_value
        )
        parameters = tuple(
            sorted(str(key) for key in effect if key not in _EFFECT_METADATA_KEYS)
        )
        identifier = f"pack:{pack.pack_id}:{effect_id}"
        try:
            definition = EffectDefinition(
                identifier=identifier,
                label=str(effect["label"]),
                description=_text(
                    effect.get("description", effect["label"]),
                    f"effects[{effect_id}].description",
                ),
                meaning=_text(
                    effect.get("meaning", f"{pack.name}: {effect['label']}"),
                    f"effects[{effect_id}].meaning",
                ),
                surfaces=surfaces,
                parameters=parameters,
                safety=str(effect.get("safety", "safe")),
                energy=str(effect.get("energy", "low")),
                reduce_motion_fallback=(
                    f"pack:{pack.pack_id}:{fallback}" if fallback is not None else None
                ),
                version=pack.version,
                compilation=(("pack", pack.pack_id), ("effect", effect_id)),
                catalog=f"pack:{pack.pack_id}",
            )
        except EffectRegistryError as error:
            raise EffectPackError(f"effect {effect_id} is not registry-safe") from error
        definitions.append(definition)
    return tuple(definitions)


def registry_with_pack(
    registry: EffectRegistry,
    payload: EffectPack | Mapping[str, Any],
) -> EffectRegistry:
    """Return a new registry containing a pack, rejecting every identifier collision."""

    definitions = effect_definitions_from_pack(payload)
    existing = registry.as_mapping()
    for definition in definitions:
        if definition.identifier in existing:
            raise EffectPackError(
                f"effect identifier already registered: {definition.identifier}"
            )
    return EffectRegistry((*existing.values(), *definitions))


def preview_pack(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return bounded, UI-ready effect descriptors for a safe preview."""
    pack = validate_pack(payload)
    return tuple(
        {
            "id": effect["id"],
            "label": effect["label"],
            "parameters": _thaw_json(effect),
        }
        for effect in pack.effects
    )


def export_pack(pack: EffectPack | Mapping[str, Any]) -> bytes:
    """Export canonical JSON with stable key ordering and no executable content."""
    validated = validate_pack(_pack_mapping(pack) if isinstance(pack, EffectPack) else pack)
    payload = _pack_mapping(validated)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_PACK_BYTES:
        raise EffectPackError("pack exceeds size limit")
    return encoded


__all__ = [
    "CURRENT_PACK_VERSION",
    "EffectPack",
    "EffectPackError",
    "LicenseMetadata",
    "effect_definitions_from_pack",
    "export_pack",
    "migrate_pack",
    "preview_pack",
    "registry_with_pack",
    "validate_pack",
]
