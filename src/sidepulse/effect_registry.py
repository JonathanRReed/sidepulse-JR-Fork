"""Pure, versioned authority for JR-Bar visual effects.

This module deliberately contains no display, device, or AppKit code.  The
registry is a small data model that presentation backends can compile.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from . import colors as colors_module


class EffectRegistryError(ValueError):
    """Raised when an effect catalog contains an invalid or conflicting entry."""


_PARAMETER_TYPES = frozenset(
    {"boolean", "integer", "number", "choice", "color", "palette"}
)
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
MAX_SAFE_BLINK_HZ = 1000.0 / colors_module.MIN_FLASH_CYCLE_MS


@dataclass(frozen=True, slots=True)
class EffectParameter:
    """Typed, side-effect-free configuration metadata for one effect value."""

    name: str
    value_type: str
    default: object
    description: str
    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: tuple[str, ...] = ()
    minimum_items: int | None = None
    maximum_items: int | None = None
    allow_empty: bool = False
    unit: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not self.name
            or type(self.description) is not str
            or not self.description
        ):
            raise EffectRegistryError("parameter name and description are required")
        if self.value_type not in _PARAMETER_TYPES:
            raise EffectRegistryError(f"unknown parameter type: {self.value_type}")

        choices = tuple(self.choices)
        object.__setattr__(self, "choices", choices)
        if self.value_type == "choice":
            if (
                not choices
                or any(type(choice) is not str or not choice for choice in choices)
                or len(set(choices)) != len(choices)
            ):
                raise EffectRegistryError("choice parameters need unique choices")
        elif choices:
            raise EffectRegistryError("only choice parameters may declare choices")

        numeric = self.value_type in {"integer", "number"}
        if not numeric and (self.minimum is not None or self.maximum is not None):
            raise EffectRegistryError("only numeric parameters may declare numeric bounds")
        for bound in (self.minimum, self.maximum):
            if bound is not None and (
                isinstance(bound, bool)
                or not isinstance(bound, (int, float))
                or not math.isfinite(float(bound))
            ):
                raise EffectRegistryError("parameter bounds must be finite numbers")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise EffectRegistryError("parameter bounds must be ordered")

        palette = self.value_type == "palette"
        if not palette and (
            self.minimum_items is not None or self.maximum_items is not None
        ):
            raise EffectRegistryError("only palette parameters may declare item bounds")
        for bound in (self.minimum_items, self.maximum_items):
            if bound is not None and (type(bound) is not int or bound < 0):
                raise EffectRegistryError("palette bounds must be non-negative integers")
        if (
            self.minimum_items is not None
            and self.maximum_items is not None
            and self.minimum_items > self.maximum_items
        ):
            raise EffectRegistryError("palette bounds must be ordered")
        if type(self.allow_empty) is not bool or (self.allow_empty and not palette):
            raise EffectRegistryError("only palette parameters may allow an empty value")
        if self.unit is not None and (type(self.unit) is not str or not self.unit):
            raise EffectRegistryError("parameter unit must be a non-empty string")

        try:
            normalized_default = self.normalize(self.default)
        except EffectRegistryError as error:
            raise EffectRegistryError(
                f"parameter {self.name} default is invalid"
            ) from error
        object.__setattr__(self, "default", normalized_default)

    def normalize(self, value: object) -> object:
        """Validate and canonicalize one value without mutating the input."""

        if self.value_type == "boolean":
            if type(value) is not bool:
                raise EffectRegistryError(f"parameter {self.name} must be a boolean")
            return value
        if self.value_type == "integer":
            if type(value) is not int:
                raise EffectRegistryError(f"parameter {self.name} must be an integer")
            self._check_numeric_bounds(value)
            return value
        if self.value_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EffectRegistryError(f"parameter {self.name} must be a number")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise EffectRegistryError(f"parameter {self.name} must be finite")
            self._check_numeric_bounds(normalized)
            return normalized
        if self.value_type == "choice":
            if type(value) is not str or value not in self.choices:
                choices = ", ".join(self.choices)
                raise EffectRegistryError(
                    f"parameter {self.name} must be one of: {choices}"
                )
            return value
        if self.value_type == "color":
            return self._normalize_color(value)
        if not isinstance(value, (list, tuple)):
            raise EffectRegistryError(f"parameter {self.name} must be a color palette")
        if not value and self.allow_empty:
            return ()
        if self.minimum_items is not None and len(value) < self.minimum_items:
            raise EffectRegistryError(
                f"parameter {self.name} needs at least {self.minimum_items} colors"
            )
        if self.maximum_items is not None and len(value) > self.maximum_items:
            raise EffectRegistryError(
                f"parameter {self.name} allows at most {self.maximum_items} colors"
            )
        return tuple(self._normalize_color(item) for item in value)

    def _check_numeric_bounds(self, value: int | float) -> None:
        if self.minimum is not None and value < self.minimum:
            raise EffectRegistryError(
                f"parameter {self.name} must be at least {self.minimum}"
            )
        if self.maximum is not None and value > self.maximum:
            raise EffectRegistryError(
                f"parameter {self.name} must be at most {self.maximum}"
            )

    def _normalize_color(self, value: object) -> str:
        if type(value) is not str or _HEX_COLOR.fullmatch(value) is None:
            raise EffectRegistryError(
                f"parameter {self.name} colors must be #RRGGBB hex colors"
            )
        return value.upper()


@dataclass(frozen=True, slots=True)
class SurfaceAdaptation:
    """How an effect preserves its meaning on one presentation surface."""

    surface: str
    mode: str
    shared_segments: str
    description: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.surface,
                self.mode,
                self.shared_segments,
                self.description,
            )
        ):
            raise EffectRegistryError("surface adaptation fields are required")


@dataclass(frozen=True, slots=True)
class BlinkCadence:
    """A named hard-blink cadence that cannot exceed the existing 2 Hz limit."""

    identifier: str
    label: str
    on_ms: int
    off_ms: int
    pulses: int = 1
    rest_ms: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.identifier) is not str
            or not self.identifier
            or type(self.label) is not str
            or not self.label
        ):
            raise EffectRegistryError("blink cadence identifier and label are required")
        if type(self.on_ms) is not int or type(self.off_ms) is not int:
            raise EffectRegistryError("blink cadence phases must be integer milliseconds")
        if self.on_ms <= 0 or self.off_ms <= 0:
            raise EffectRegistryError("blink cadence phases must be positive")
        if type(self.pulses) is not int or self.pulses < 1:
            raise EffectRegistryError("blink cadence pulses must be positive")
        if type(self.rest_ms) is not int or self.rest_ms < 0:
            raise EffectRegistryError("blink cadence rest must be non-negative")
        if self.on_ms + self.off_ms < colors_module.MIN_FLASH_CYCLE_MS:
            raise EffectRegistryError("blink cadence exceeds the 2 Hz flash limit")

    @property
    def peak_flash_hz(self) -> float:
        return 1000.0 / (self.on_ms + self.off_ms)

    @property
    def duration_ms(self) -> int:
        return self.pulses * (self.on_ms + self.off_ms) + self.rest_ms


@dataclass(frozen=True, slots=True)
class EffectDefinition:
    identifier: str
    label: str
    description: str
    meaning: str
    surfaces: tuple[str, ...] = ("status_bar",)
    parameters: tuple[str, ...] = ()
    safety: str = "safe"
    energy: str = "low"
    reduce_motion_fallback: str | None = None
    version: int = 1
    compilation: tuple[tuple[str, str], ...] = ()
    catalog: str = "general"
    role: str = "general"
    parameter_metadata: tuple[EffectParameter, ...] = ()
    surface_adaptations: tuple[SurfaceAdaptation, ...] = ()

    def __post_init__(self) -> None:
        if not self.identifier or not self.label or not self.meaning:
            raise EffectRegistryError("effect identifier, label, and meaning are required")
        if self.version < 1:
            raise EffectRegistryError("effect version must be positive")
        if self.safety not in {"safe", "attention", "critical"}:
            raise EffectRegistryError(f"unknown safety level: {self.safety}")
        if self.energy not in {"low", "medium", "high"}:
            raise EffectRegistryError(f"unknown energy level: {self.energy}")
        if not self.surfaces or len(set(self.surfaces)) != len(self.surfaces):
            raise EffectRegistryError("effects need at least one unique supported surface")
        if len(set(self.parameters)) != len(self.parameters):
            raise EffectRegistryError("effect parameters must be unique")
        parameter_metadata = tuple(self.parameter_metadata)
        surface_adaptations = tuple(self.surface_adaptations)
        object.__setattr__(self, "parameter_metadata", parameter_metadata)
        object.__setattr__(self, "surface_adaptations", surface_adaptations)
        parameter_names = tuple(parameter.name for parameter in parameter_metadata)
        if len(set(parameter_names)) != len(parameter_names):
            raise EffectRegistryError("effect parameter metadata must be unique")
        if parameter_names:
            if self.parameters and self.parameters != parameter_names:
                raise EffectRegistryError(
                    "effect parameter names must match typed parameter metadata"
                )
            object.__setattr__(self, "parameters", parameter_names)
        if len({key for key, _ in self.compilation}) != len(self.compilation):
            raise EffectRegistryError("compilation metadata keys must be unique")
        if not self.catalog:
            raise EffectRegistryError("effect catalog is required")
        if not self.role:
            raise EffectRegistryError("effect role is required")
        adaptation_surfaces = tuple(
            adaptation.surface for adaptation in surface_adaptations
        )
        if len(set(adaptation_surfaces)) != len(adaptation_surfaces):
            raise EffectRegistryError("effect surface adaptations must be unique")
        if any(surface not in self.surfaces for surface in adaptation_surfaces):
            raise EffectRegistryError(
                "effect adaptations must reference a supported surface"
            )

    @property
    def parameter_defaults(self) -> Mapping[str, object]:
        return {
            parameter.name: parameter.default for parameter in self.parameter_metadata
        }

    def normalize_parameters(
        self, values: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Return a complete canonical parameter map in declared schema order."""

        if values is None:
            values = {}
        if not isinstance(values, Mapping):
            raise EffectRegistryError("effect parameters must be a mapping")
        if any(type(key) is not str for key in values):
            raise EffectRegistryError("effect parameter names must be strings")
        known = {parameter.name for parameter in self.parameter_metadata}
        unknown = sorted(set(values) - known)
        if unknown:
            raise EffectRegistryError(
                f"unknown parameters for {self.identifier}: {', '.join(unknown)}"
            )
        return {
            parameter.name: parameter.normalize(
                values.get(parameter.name, parameter.default)
            )
            for parameter in self.parameter_metadata
        }

    def surface_adaptation(self, surface: str) -> SurfaceAdaptation:
        for adaptation in self.surface_adaptations:
            if adaptation.surface == surface:
                return adaptation
        raise KeyError(surface)


class EffectRegistry:
    """Immutable-by-convention registry with deterministic catalog queries."""

    def __init__(self, effects: Iterable[EffectDefinition] = ()) -> None:
        table: dict[str, EffectDefinition] = {}
        for effect in effects:
            if effect.identifier in table and table[effect.identifier] != effect:
                raise EffectRegistryError(f"conflicting effect identifier: {effect.identifier}")
            table[effect.identifier] = effect
        self._effects = table

    def get(self, identifier: str) -> EffectDefinition | None:
        return self._effects.get(identifier)

    def require(self, identifier: str) -> EffectDefinition:
        effect = self.get(identifier)
        if effect is None:
            raise KeyError(identifier)
        return effect

    def list(self, *, surface: str | None = None, safety: str | None = None) -> tuple[EffectDefinition, ...]:
        values = self._effects.values()
        if surface is not None:
            values = (effect for effect in values if surface in effect.surfaces)
        if safety is not None:
            values = (effect for effect in values if effect.safety == safety)
        return tuple(sorted(values, key=lambda effect: effect.identifier))

    def catalog(self, name: str) -> tuple[EffectDefinition, ...]:
        """Return one catalog in its declared insertion order."""

        return tuple(effect for effect in self._effects.values() if effect.catalog == name)

    def reduced_motion(self, identifier: str) -> EffectDefinition:
        effect = self.require(identifier)
        fallback = effect.reduce_motion_fallback
        return self.require(fallback) if fallback else effect

    def normalize_parameters(
        self,
        identifier: str,
        values: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return self.require(identifier).normalize_parameters(values)

    def as_mapping(self) -> Mapping[str, EffectDefinition]:
        return dict(self._effects)


def _effect(identifier: str, label: str, meaning: str, **kwargs: object) -> EffectDefinition:
    return EffectDefinition(  # type: ignore[arg-type]
        identifier,
        label,
        kwargs.pop("description", label),
        meaning,
        **kwargs,
    )


SAFE_BLINK_CADENCES: tuple[BlinkCadence, ...] = (
    BlinkCadence("calm", "Calm", on_ms=1100, off_ms=1100),
    BlinkCadence("deliberate", "Deliberate", on_ms=500, off_ms=500),
    BlinkCadence(
        "double",
        "Deliberate double",
        on_ms=300,
        off_ms=300,
        pulses=2,
        rest_ms=1400,
    ),
)
_BLINK_CADENCE_BY_ID = {
    cadence.identifier: cadence for cadence in SAFE_BLINK_CADENCES
}


def blink_cadence(identifier: str) -> BlinkCadence:
    """Return one of the named, prevalidated hard-blink cadences."""

    try:
        return _BLINK_CADENCE_BY_ID[identifier]
    except KeyError:
        raise KeyError(identifier) from None


def _number(
    name: str,
    default: float,
    description: str,
    minimum: float,
    maximum: float,
    *,
    unit: str | None = None,
) -> EffectParameter:
    return EffectParameter(
        name,
        "number",
        default,
        description,
        minimum=minimum,
        maximum=maximum,
        unit=unit,
    )


def _integer(
    name: str,
    default: int,
    description: str,
    minimum: int,
    maximum: int,
) -> EffectParameter:
    return EffectParameter(
        name,
        "integer",
        default,
        description,
        minimum=minimum,
        maximum=maximum,
    )


def _choice(
    name: str,
    default: str,
    description: str,
    choices: tuple[str, ...],
) -> EffectParameter:
    return EffectParameter(
        name,
        "choice",
        default,
        description,
        choices=choices,
    )


def _boolean(name: str, default: bool, description: str) -> EffectParameter:
    return EffectParameter(name, "boolean", default, description)


def _palette(
    name: str,
    description: str,
    *,
    maximum_items: int,
) -> EffectParameter:
    return EffectParameter(
        name,
        "palette",
        (),
        description,
        minimum_items=2,
        maximum_items=maximum_items,
        allow_empty=True,
    )


def _duration(*, minimum: float | None = None) -> EffectParameter:
    return _number(
        "duration_seconds",
        colors_module.DEFAULT_CYCLE_SPEED_SECONDS,
        "Length of one complete motion cycle.",
        colors_module.MIN_CYCLE_SPEED_SECONDS if minimum is None else minimum,
        colors_module.MAX_CYCLE_SPEED_SECONDS,
        unit="seconds",
    )


_DIRECTIONS = ("forward", "reverse")
_PASS_MODES = ("continuous", "once", "twice")
_PROVIDER_PARAMETER_METADATA: dict[str, tuple[EffectParameter, ...]] = {
    colors_module.PROVIDER_ANIMATION_AUTO: (
        _choice(
            "mapping_source",
            "state",
            "Choose the current state map or a Scene-specific map.",
            ("state", "scene"),
        ),
        _boolean(
            "urgent_overrides",
            True,
            "Keep reserved asking and failure motion overrides visible.",
        ),
    ),
    colors_module.MOTION_BREATHE: (
        _duration(),
        _number(
            "amplitude",
            1.0,
            "Relative distance between the luminous floor and crest.",
            0.1,
            1.0,
        ),
    ),
    colors_module.MOTION_DUOTONE: (
        _duration(),
        _number(
            "secondary_hue_offset_degrees",
            40.0,
            "Hue offset used when no explicit two-color palette is supplied.",
            -180.0,
            180.0,
            unit="degrees",
        ),
        _palette(
            "palette",
            "Optional validated identity and state colors; empty derives both tones.",
            maximum_items=2,
        ),
    ),
    colors_module.MOTION_CHASE: (
        _duration(),
        _choice("direction", "forward", "Direction of travel.", _DIRECTIONS),
        _integer("spacing", 1, "LED spacing between wave crests.", 1, 6),
        _number(
            "softness",
            1.0,
            "Edge softness of the travelling wave.",
            0.0,
            1.0,
        ),
    ),
    colors_module.MOTION_GRADIENT: (
        _duration(),
        _choice("direction", "forward", "Direction of gradient travel.", _DIRECTIONS),
        _number(
            "hue_span_degrees",
            48.0,
            "Derived palette span when no explicit endpoints are supplied.",
            0.0,
            120.0,
            unit="degrees",
        ),
        _palette(
            "palette",
            "Optional bounded gradient endpoints; empty derives them from identity color.",
            maximum_items=2,
        ),
        _boolean("smooth_morph", True, "Morph smoothly when state colors change."),
    ),
    colors_module.MOTION_HEARTBEAT: (
        _duration(minimum=0.5),
        _number(
            "rest_ratio",
            0.5,
            "Fraction of the cycle reserved after the decorative lub-dub.",
            0.35,
            0.8,
        ),
    ),
    colors_module.MOTION_SCANNER: (
        _duration(minimum=0.5),
        _integer("beam_width", 1, "Width of the bright scanning beam in LEDs.", 1, 8),
        _number("trail", 0.35, "Relative length of the fading trail.", 0.0, 1.0),
    ),
    colors_module.MOTION_KITT: (
        _duration(minimum=0.5),
        _integer("beam_width", 3, "Width of the overlapping mechanical eye.", 2, 10),
        _number("overlap", 0.5, "Overlap between the eye's light bands.", 0.1, 1.0),
    ),
    colors_module.MOTION_COMET: (
        _duration(minimum=0.5),
        _integer("head_width", 1, "Width of the bright comet head in LEDs.", 1, 6),
        _integer("trail_length", 3, "Length of the fading trail in LEDs.", 1, 12),
        _choice("direction", "forward", "Direction of comet travel.", _DIRECTIONS),
        _choice(
            "pass_mode",
            "continuous",
            "Continuous loop or a bounded transition pass count.",
            _PASS_MODES,
        ),
    ),
    colors_module.MOTION_FLICKER: (
        _duration(minimum=0.5),
        _integer("seed", 271, "Seed for deterministic luminance variation.", 0, 2_147_483_647),
        _number("luminance_floor", 0.35, "Lowest relative luminance.", 0.1, 0.8),
        _number("variation", 0.25, "Maximum deterministic luminance variation.", 0.0, 0.5),
    ),
    colors_module.MOTION_STACK: (
        _duration(minimum=0.5),
        _choice("fill_direction", "forward", "Direction in which LEDs accumulate.", _DIRECTIONS),
        _choice(
            "release_behavior",
            "all_at_once",
            "How a completed stack returns to its luminous floor.",
            ("all_at_once", "hold", "decay"),
        ),
        _choice(
            "data_mapping",
            "none",
            "Optional reliable count represented by the stack.",
            ("none", "queue", "milestone"),
        ),
    ),
    colors_module.MOTION_TWINKLE: (
        _duration(minimum=0.5),
        _number("density", 0.15, "Maximum fraction of LEDs sparkling at once.", 0.02, 0.3),
        _integer("seed", 271, "Seed for deterministic sparkle placement.", 0, 2_147_483_647),
        _integer("max_cluster", 1, "Largest allowed adjacent sparkle cluster.", 1, 2),
    ),
    colors_module.MOTION_DRIFT: (
        _duration(minimum=1.0),
        _number("detune", 0.08, "Phase variation between slow luminous swells.", 0.0, 0.25),
        _number(
            "sample_interval_seconds",
            0.25,
            "Minimum cadence for deterministic drift updates.",
            0.1,
            2.0,
            unit="seconds",
        ),
    ),
    colors_module.MOTION_CONVERGE: (
        _duration(minimum=0.5),
        _choice(
            "variant",
            "endpoints_to_center",
            "Convergence geometry used for merge or handoff meaning.",
            ("endpoints_to_center", "source_to_destination"),
        ),
    ),
    colors_module.MOTION_AURORA: (
        _duration(minimum=1.0),
        _palette(
            "palette",
            "Optional bounded aurora palette; empty derives tones from identity color.",
            maximum_items=4,
        ),
        _integer("wave_count", 2, "Number of slow layered waves.", 1, 4),
        _integer("seed", 617, "Seed for deterministic wave phases.", 0, 2_147_483_647),
    ),
    colors_module.MOTION_TIDE: (
        _duration(minimum=0.5),
        _number("fill_floor", 0.15, "Minimum filled fraction before the tide rises.", 0.0, 0.8),
        _number("fill_range", 0.85, "Additional filled fraction at full tide.", 0.1, 1.0),
    ),
    colors_module.MOTION_MARQUEE: (
        _duration(minimum=0.5),
        _integer("spacing", 1, "LED spacing between palette bands.", 1, 8),
        _choice("direction", "forward", "Direction of palette rotation.", _DIRECTIONS),
        _number(
            "palette_rotation_degrees",
            48.0,
            "Derived palette rotation when no explicit palette is supplied.",
            0.0,
            180.0,
            unit="degrees",
        ),
        _choice(
            "pass_mode",
            "continuous",
            "Continuous loop or a bounded transition pass count.",
            _PASS_MODES,
        ),
    ),
    colors_module.MOTION_STEADY: (
        _number("luminance", 1.0, "Relative persistent luminance.", 0.05, 1.0),
    ),
    colors_module.MOTION_BLINK: (
        _choice(
            "cadence",
            "calm",
            "Named cadence; arbitrary flash frequency is intentionally unsupported.",
            tuple(cadence.identifier for cadence in SAFE_BLINK_CADENCES),
        ),
        _boolean("repeat", True, "Repeat the selected named cadence."),
    ),
}

_PROVIDER_ROLES = {
    colors_module.PROVIDER_ANIMATION_AUTO: "adaptive",
    colors_module.MOTION_BREATHE: "ambient",
    colors_module.MOTION_DUOTONE: "identity_state",
    colors_module.MOTION_CHASE: "directional_flow",
    colors_module.MOTION_GRADIENT: "ambient",
    colors_module.MOTION_HEARTBEAT: "provider_identity",
    colors_module.MOTION_SCANNER: "mechanical",
    colors_module.MOTION_KITT: "mechanical",
    colors_module.MOTION_COMET: "transition",
    colors_module.MOTION_FLICKER: "ambient",
    colors_module.MOTION_STACK: "progress",
    colors_module.MOTION_TWINKLE: "ambient",
    colors_module.MOTION_DRIFT: "ambient",
    colors_module.MOTION_CONVERGE: "handoff",
    colors_module.MOTION_AURORA: "ambient",
    colors_module.MOTION_TIDE: "capacity",
    colors_module.MOTION_MARQUEE: "identity",
    colors_module.MOTION_STEADY: "persistent",
    colors_module.MOTION_BLINK: "attention",
}

_PROVIDER_ADAPTATION_MODES: dict[str, tuple[str, str]] = {
    colors_module.PROVIDER_ANIMATION_AUTO: ("resolve_current_mapping", "preserve_urgent_override"),
    colors_module.MOTION_BREATHE: ("phase_aligned_swell", "static_luminous_base"),
    colors_module.MOTION_DUOTONE: ("alternating_tones", "interleaved_tones"),
    colors_module.MOTION_CHASE: ("travelling_wave", "directional_wave"),
    colors_module.MOTION_GRADIENT: ("per_led_gradient", "bounded_gradient_flare"),
    colors_module.MOTION_HEARTBEAT: ("decorative_lub_dub", "decorative_lub_dub"),
    colors_module.MOTION_SCANNER: ("sweeping_beam", "narrow_flare"),
    colors_module.MOTION_KITT: ("wide_overlapping_eye", "narrow_flare"),
    colors_module.MOTION_COMET: ("head_and_trail", "narrow_flare"),
    colors_module.MOTION_FLICKER: ("seeded_luminance", "seeded_luminance"),
    colors_module.MOTION_STACK: ("sequential_fill", "hard_pile_on"),
    colors_module.MOTION_TWINKLE: ("sparse_seeded_sparkle", "sparse_seeded_sparkle"),
    colors_module.MOTION_DRIFT: ("detuned_swell", "reduced_sample_swell"),
    colors_module.MOTION_CONVERGE: ("meeting_fronts", "block_center_meet"),
    colors_module.MOTION_AURORA: ("layered_waves", "layered_luminous_base"),
    colors_module.MOTION_TIDE: ("rising_fill", "full_segment_swell"),
    colors_module.MOTION_MARQUEE: ("rotating_palette", "narrow_flare"),
    colors_module.MOTION_STEADY: ("persistent_hold", "persistent_hold"),
    colors_module.MOTION_BLINK: ("named_hard_blink", "named_hard_blink"),
}


def _surface_adaptations(identifier: str) -> tuple[SurfaceAdaptation, ...]:
    mode, shared_segments = _PROVIDER_ADAPTATION_MODES[identifier]
    label = colors_module.PROVIDER_ANIMATION_LABELS[identifier]
    return (
        SurfaceAdaptation(
            "screen_bar",
            mode,
            shared_segments,
            f"Render {label} across the available Screen Bar topology.",
        ),
        SurfaceAdaptation(
            "settings_preview",
            mode,
            shared_segments,
            f"Preview {label} deterministically without device access.",
        ),
    )


def _provider_animation_effect(identifier: str) -> EffectDefinition:
    return _effect(
        identifier,
        colors_module.PROVIDER_ANIMATION_LABELS[identifier],
        f"provider animation: {identifier}",
        description=colors_module.PROVIDER_ANIMATION_DESCRIPTIONS[identifier],
        surfaces=("screen_bar", "settings_preview"),
        energy=(
            "high"
            if identifier == colors_module.MOTION_AURORA
            else "low"
            if identifier in {
                colors_module.PROVIDER_ANIMATION_AUTO,
                colors_module.MOTION_BREATHE,
                colors_module.MOTION_DRIFT,
                colors_module.MOTION_STEADY,
            }
            else "medium"
        ),
        reduce_motion_fallback=colors_module.MOTION_STEADY,
        compilation=(("motion", identifier),),
        catalog="provider_animation",
        role=_PROVIDER_ROLES[identifier],
        parameter_metadata=_PROVIDER_PARAMETER_METADATA[identifier],
        surface_adaptations=_surface_adaptations(identifier),
    )


PROVIDER_ANIMATION_EFFECT_IDS: tuple[str, ...] = colors_module.PROVIDER_ANIMATION_CHOICES
PROVIDER_ANIMATION_EFFECTS: tuple[EffectDefinition, ...] = tuple(
    _provider_animation_effect(identifier)
    for identifier in PROVIDER_ANIMATION_EFFECT_IDS
)


EFFECT_REGISTRY = EffectRegistry(
    (
        *PROVIDER_ANIMATION_EFFECTS,
        _effect(
            "none",
            "No effect",
            "steady color",
            description="Hold the selected color steady.",
            surfaces=(
                "status_bar",
                "screen_bar",
                "sidepulse_pro",
                "sidepulse_dot",
                "glance_light",
                "settings_preview",
            ),
        ),
        _effect(
            "pulse",
            "Pulse",
            "periodic activity",
            description="A restrained brightness pulse.",
            surfaces=(
                "status_bar",
                "screen_bar",
                "sidepulse_pro",
                "sidepulse_dot",
                "glance_light",
                "settings_preview",
            ),
            energy="medium",
            reduce_motion_fallback="none",
        ),
        _effect(
            "rainbow",
            "Rainbow",
            "cycling color activity",
            description="Cycle through the selected palette.",
            surfaces=("status_bar", "screen_bar", "sidepulse_pro", "settings_preview"),
            energy="medium",
            reduce_motion_fallback="none",
        ),
        _effect(
            "alert",
            "Alert",
            "attention required",
            description="A high-visibility attention signal.",
            surfaces=(
                "status_bar",
                "screen_bar",
                "sidepulse_pro",
                "sidepulse_dot",
                "glance_light",
                "settings_preview",
            ),
            safety="attention",
            energy="high",
            reduce_motion_fallback="pulse",
        ),
        _effect(
            "notification",
            "Notification",
            "new event",
            description="A short notification flash.",
            surfaces=(
                "status_bar",
                "screen_bar",
                "sidepulse_pro",
                "sidepulse_dot",
                "glance_light",
                "settings_preview",
            ),
            safety="attention",
            reduce_motion_fallback="none",
        ),
    )
)


def get_effect(identifier: str) -> EffectDefinition | None:
    return EFFECT_REGISTRY.get(identifier)


def list_effects(*, surface: str | None = None, safety: str | None = None) -> tuple[EffectDefinition, ...]:
    return EFFECT_REGISTRY.list(surface=surface, safety=safety)


def reduced_motion_effect(identifier: str) -> EffectDefinition:
    return EFFECT_REGISTRY.reduced_motion(identifier)


def normalize_effect_parameters(
    identifier: str,
    values: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Validate and normalize user parameters against the built-in registry."""

    return EFFECT_REGISTRY.normalize_parameters(identifier, values)


def provider_animation_effects() -> tuple[EffectDefinition, ...]:
    """Return the authoritative provider-animation choices in UI order."""

    return EFFECT_REGISTRY.catalog("provider_animation")


__all__ = [
    "EFFECT_REGISTRY",
    "MAX_SAFE_BLINK_HZ",
    "PROVIDER_ANIMATION_EFFECTS",
    "PROVIDER_ANIMATION_EFFECT_IDS",
    "SAFE_BLINK_CADENCES",
    "BlinkCadence",
    "EffectDefinition",
    "EffectParameter",
    "EffectRegistry",
    "EffectRegistryError",
    "SurfaceAdaptation",
    "blink_cadence",
    "get_effect",
    "list_effects",
    "normalize_effect_parameters",
    "provider_animation_effects",
    "reduced_motion_effect",
]
