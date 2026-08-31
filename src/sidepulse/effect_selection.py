"""Pure catalogs and validation plans for user-selectable light effects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from . import colors as colors_module
from .colors import ColorSettings
from .effect_packs import EffectPack, effect_definitions_from_pack
from .effect_registry import provider_animation_effects
from .providers import PROVIDER_SPECS


@dataclass(frozen=True, slots=True)
class EffectOption:
    """One persisted effect value and its user-facing copy."""

    value: str
    label: str
    description: str = ""


class EffectSelectionDisposition(str, Enum):
    """Whether a validated selection should be ignored or applied."""

    INVALID = "invalid"
    NO_CHANGE = "no_change"
    APPLY = "apply"


@dataclass(frozen=True, slots=True)
class EffectSelectionPlan:
    """The pure result consumed by an AppKit action boundary."""

    disposition: EffectSelectionDisposition
    value: str | None
    colors: ColorSettings
    provider: str | None = None


BLEND_MODE_OPTIONS: tuple[EffectOption, ...] = tuple(
    EffectOption(
        value=mode,
        label=colors_module.BLEND_MODE_LABELS[mode],
        description=colors_module.BLEND_MODE_DESCRIPTIONS[mode],
    )
    for mode in colors_module.BLEND_MODE_CHOICES
)

COLOR_PRESET_OPTIONS: tuple[EffectOption, ...] = (
    EffectOption(
        value=colors_module.PRESET_CUSTOM,
        label=colors_module.PRESET_LABELS[colors_module.PRESET_CUSTOM],
    ),
    *(
        EffectOption(
            value=preset,
            label=colors_module.PRESET_LABELS[preset],
            description=colors_module.PRESET_DESCRIPTIONS[preset],
        )
        for preset in colors_module.PRESET_CHOICES
    ),
)

PREVIEW_SCENARIO_OPTIONS: tuple[EffectOption, ...] = tuple(
    EffectOption(
        value=scenario,
        label=colors_module.PREVIEW_SCENARIO_LABELS[scenario],
    )
    for scenario in colors_module.PREVIEW_SCENARIO_CHOICES
)

PROVIDER_ANIMATION_OPTIONS: tuple[EffectOption, ...] = tuple(
    EffectOption(
        value=effect.identifier,
        label=effect.label,
        description=effect.description,
    )
    for effect in provider_animation_effects()
)

KNOWN_PROVIDERS = frozenset(spec.provider for spec in PROVIDER_SPECS)


def effect_pack_options(
    payload: EffectPack | Mapping[str, Any],
) -> tuple[EffectOption, ...]:
    """Project a validated data-only pack into safe preview choices."""

    return tuple(
        EffectOption(
            value=effect.identifier,
            label=effect.label,
            description=effect.description,
        )
        for effect in effect_definitions_from_pack(payload)
    )


def selected_option_index(
    options: tuple[EffectOption, ...], value: str
) -> int | None:
    """Return the first option index matching a persisted value."""

    if type(value) is not str:
        return None
    for index, option in enumerate(options):
        if option.value == value:
            return index
    return None


def _payload_value(payload: object, key: str, options: tuple[EffectOption, ...]) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    if type(value) is not str:
        return None
    return value if selected_option_index(options, value) is not None else None


def preview_scenario_from_payload(payload: object) -> str | None:
    """Validate a preview scenario without changing color settings."""

    return _payload_value(payload, "scenario", PREVIEW_SCENARIO_OPTIONS)


def _invalid(colors: ColorSettings) -> EffectSelectionPlan:
    return EffectSelectionPlan(EffectSelectionDisposition.INVALID, None, colors)


def plan_color_preset_selection(
    colors: ColorSettings, payload: object
) -> EffectSelectionPlan:
    preset = _payload_value(payload, "preset", COLOR_PRESET_OPTIONS)
    if preset is None:
        return _invalid(colors)
    if preset == colors_module.PRESET_CUSTOM:
        return EffectSelectionPlan(EffectSelectionDisposition.NO_CHANGE, preset, colors)
    try:
        selected = colors_module.apply_preset(colors, preset)
    except (AttributeError, TypeError, ValueError):
        return _invalid(colors)
    return EffectSelectionPlan(EffectSelectionDisposition.APPLY, preset, selected)


def plan_blend_mode_selection(
    colors: ColorSettings, payload: object
) -> EffectSelectionPlan:
    blend_mode = _payload_value(payload, "blend_mode", BLEND_MODE_OPTIONS)
    if blend_mode is None:
        return _invalid(colors)
    try:
        selected = colors.with_blend_mode(blend_mode)
    except (AttributeError, TypeError, ValueError):
        return _invalid(colors)
    return EffectSelectionPlan(EffectSelectionDisposition.APPLY, blend_mode, selected)


def plan_provider_animation_selection(
    colors: ColorSettings, payload: object
) -> EffectSelectionPlan:
    motion = _payload_value(payload, "motion", PROVIDER_ANIMATION_OPTIONS)
    if not isinstance(payload, dict) or motion is None:
        return _invalid(colors)
    provider = payload.get("provider")
    if type(provider) is not str or provider not in KNOWN_PROVIDERS:
        return _invalid(colors)
    try:
        selected = colors.with_agent_animation(provider, motion)
    except (AttributeError, TypeError, ValueError):
        return _invalid(colors)
    return EffectSelectionPlan(
        EffectSelectionDisposition.APPLY,
        motion,
        selected,
        provider,
    )


__all__ = [
    "BLEND_MODE_OPTIONS",
    "COLOR_PRESET_OPTIONS",
    "PREVIEW_SCENARIO_OPTIONS",
    "PROVIDER_ANIMATION_OPTIONS",
    "EffectOption",
    "EffectSelectionDisposition",
    "EffectSelectionPlan",
    "effect_pack_options",
    "plan_blend_mode_selection",
    "plan_color_preset_selection",
    "plan_provider_animation_selection",
    "preview_scenario_from_payload",
    "selected_option_index",
]
