"""Pure two-LED planning for the SidePulse Dot binary heartbeat.

The Dot has only two physical LEDs, so this contract deliberately treats the
surface as a compact, documented code rather than a miniature animation strip.
LED 1 represents the highest-priority active semantic state. LED 2 represents
exactly one operator-selected secondary meaning: a bounded fleet-size band or
the presence of unseen notifications.

Color is never required to decode the plan. Asking uses a two-pulse cadence and
failure uses a steady marker. With Reduce Motion enabled, both become static
and remain distinguishable by their documented brightness levels. This module
performs no device access, timing, persistence, settings access, or other I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from .semantic_effect_router import SEMANTIC_PRIORITY, SemanticEventKind

DOT_LED_COUNT: Final = 2
MAX_ACTIVE_SEMANTICS: Final = 128
MAX_FLEET_SIZE: Final = 4_096
MAX_FLASH_HZ: Final = 2.0
LEGEND_VERSION: Final = 1


class DotBinaryHeartbeatError(ValueError):
    """A value crossed the pure Dot planning boundary in an unsafe shape."""


class DotLedMode(str, Enum):
    DARK = "dark"
    STEADY = "steady"
    PULSE = "pulse"


class DotSecondaryPolicy(str, Enum):
    """The explicit meaning assigned to physical LED 2."""

    FLEET_SIZE = "fleet_size"
    UNSEEN_NOTIFICATIONS = "unseen_notifications"


class FleetSizeBand(str, Enum):
    """Broad fleet bands that avoid implying a precise device count."""

    NONE = "none"
    SOLO = "solo"
    SMALL = "small"
    LARGE = "large"


@dataclass(frozen=True, slots=True)
class DotPulseCadence:
    """One bounded pulse phrase, repeated by a future hardware adapter.

    ``rest_ms`` follows the last off phase. The safety limit is calculated
    from the fastest on/off pair rather than the longer phrase duration, so a
    multi-pulse phrase cannot hide an unsafe instantaneous flash rate.
    """

    on_ms: int
    off_ms: int
    pulses: int
    rest_ms: int

    def __post_init__(self) -> None:
        if type(self.on_ms) is not int or type(self.off_ms) is not int:
            raise DotBinaryHeartbeatError("Dot cadence phases must be integer milliseconds")
        if self.on_ms <= 0 or self.off_ms <= 0:
            raise DotBinaryHeartbeatError("Dot cadence phases must be positive")
        if type(self.pulses) is not int or not 1 <= self.pulses <= 3:
            raise DotBinaryHeartbeatError("Dot cadence must contain one to three pulses")
        if type(self.rest_ms) is not int or self.rest_ms < 0:
            raise DotBinaryHeartbeatError("Dot cadence rest must be nonnegative")
        if self.peak_flash_hz > MAX_FLASH_HZ:
            raise DotBinaryHeartbeatError("Dot cadence exceeds the 2 Hz flash limit")

    @property
    def peak_flash_hz(self) -> float:
        return 1_000.0 / (self.on_ms + self.off_ms)

    @property
    def phrase_duration_ms(self) -> int:
        return self.pulses * (self.on_ms + self.off_ms) + self.rest_ms


@dataclass(frozen=True, slots=True)
class DotLedInstruction:
    """One immutable, color-independent physical LED instruction."""

    led_index: int
    mode: DotLedMode
    intensity: float
    meaning: str
    non_color_cue: str
    cadence: DotPulseCadence | None = None
    motion_suppressed: bool = False

    def __post_init__(self) -> None:
        if self.led_index not in {1, 2}:
            raise DotBinaryHeartbeatError("Dot instruction LED index must be 1 or 2")
        if type(self.mode) is not DotLedMode:
            raise DotBinaryHeartbeatError("Dot instruction mode must be known")
        if type(self.intensity) not in {int, float} or not 0.0 <= float(self.intensity) <= 1.0:
            raise DotBinaryHeartbeatError("Dot instruction intensity must be between zero and one")
        if type(self.meaning) is not str or not self.meaning:
            raise DotBinaryHeartbeatError("Dot instruction meaning is required")
        if type(self.non_color_cue) is not str or not self.non_color_cue:
            raise DotBinaryHeartbeatError("Dot instruction non-color cue is required")
        if type(self.motion_suppressed) is not bool:
            raise DotBinaryHeartbeatError("Dot motion suppression must be a boolean")
        if self.mode is DotLedMode.DARK:
            if float(self.intensity) != 0.0 or self.cadence is not None:
                raise DotBinaryHeartbeatError("a dark Dot instruction cannot carry output")
        elif self.mode is DotLedMode.STEADY:
            if float(self.intensity) <= 0.0 or self.cadence is not None:
                raise DotBinaryHeartbeatError("a steady Dot instruction requires static intensity")
        elif type(self.cadence) is not DotPulseCadence or float(self.intensity) <= 0.0:
            raise DotBinaryHeartbeatError("a pulse Dot instruction requires a safe cadence")
        object.__setattr__(self, "intensity", float(self.intensity))

    @property
    def relies_on_color(self) -> bool:
        """Color may decorate a renderer, but it is never part of this code."""

        return False


@dataclass(frozen=True, slots=True)
class DotLegendEntry:
    """One immutable row in the public Dot binary-heartbeat legend."""

    led_index: int
    code: str
    label: str
    normal_cue: str
    reduce_motion_cue: str
    meaning: str

    def __post_init__(self) -> None:
        if self.led_index not in {1, 2}:
            raise DotBinaryHeartbeatError("Dot legend LED index must be 1 or 2")
        if not all(
            type(value) is str and value
            for value in (
                self.code,
                self.label,
                self.normal_cue,
                self.reduce_motion_cue,
                self.meaning,
            )
        ):
            raise DotBinaryHeartbeatError("Dot legend fields must be non-empty text")


@dataclass(frozen=True, slots=True)
class DotAccessibilityContract:
    """Stable accessibility promises shared by previews and device adapters."""

    legend_version: int
    color_is_supplemental: bool
    reduce_motion_is_static: bool
    max_flash_hz: float
    asking_failure_distinction: str
    companion_ui_requirement: str


@dataclass(frozen=True, slots=True)
class DotBinaryHeartbeatPlan:
    """The complete two-LED output and its content-free explanation."""

    primary: DotLedInstruction
    secondary: DotLedInstruction
    selected_semantic: SemanticEventKind | None
    secondary_policy: DotSecondaryPolicy
    fleet_size_band: FleetSizeBand | None
    unseen_notification_present: bool | None
    reduce_motion: bool
    legend_version: int = LEGEND_VERSION

    def __post_init__(self) -> None:
        if self.primary.led_index != 1 or self.secondary.led_index != 2:
            raise DotBinaryHeartbeatError("Dot plan must contain LED 1 followed by LED 2")
        if self.selected_semantic is not None and type(self.selected_semantic) is not SemanticEventKind:
            raise DotBinaryHeartbeatError("Dot plan selected semantic must be known")
        if type(self.secondary_policy) is not DotSecondaryPolicy:
            raise DotBinaryHeartbeatError("Dot plan secondary policy must be known")
        if type(self.reduce_motion) is not bool:
            raise DotBinaryHeartbeatError("Dot plan Reduce Motion value must be a boolean")
        if self.legend_version != LEGEND_VERSION:
            raise DotBinaryHeartbeatError("Dot plan legend version is unsupported")
        if self.secondary_policy is DotSecondaryPolicy.FLEET_SIZE:
            if type(self.fleet_size_band) is not FleetSizeBand or self.unseen_notification_present is not None:
                raise DotBinaryHeartbeatError("fleet policy must expose only a fleet-size band")
        elif self.fleet_size_band is not None or type(self.unseen_notification_present) is not bool:
            raise DotBinaryHeartbeatError("unseen policy must expose only notification presence")

    @property
    def instructions(self) -> tuple[DotLedInstruction, DotLedInstruction]:
        return (self.primary, self.secondary)


_ASK_CADENCE: Final = DotPulseCadence(250, 250, 2, 1_500)
_NOTIFICATION_CADENCE: Final = DotPulseCadence(250, 750, 1, 2_000)
_HANDOFF_CADENCE: Final = DotPulseCadence(500, 500, 2, 1_000)
_COMPLETION_CADENCE: Final = DotPulseCadence(250, 250, 1, 3_500)
_RECOVERY_CADENCE: Final = DotPulseCadence(400, 600, 2, 3_000)


DOT_BINARY_HEARTBEAT_LEGEND: Final = (
    DotLegendEntry(1, "ask", "Asking", "two short pulses", "steady medium", "An answer or decision is needed."),
    DotLegendEntry(1, "failure", "Failure", "steady high", "steady high", "The highest-priority source failed."),
    DotLegendEntry(
        1,
        "notification",
        "Notification",
        "one short pulse, three-second phrase",
        "steady medium-low",
        "An active notification is the highest-priority state.",
    ),
    DotLegendEntry(1, "handoff", "Handoff", "two long pulses", "steady medium", "A supported handoff is active."),
    DotLegendEntry(1, "work", "Working", "steady low", "steady low", "Work is active."),
    DotLegendEntry(
        1,
        "completion",
        "Completion",
        "one short pulse, four-second phrase",
        "steady medium-low",
        "A completion is visible.",
    ),
    DotLegendEntry(1, "recovery", "Recovery", "two slow pulses", "steady low", "A source recovered."),
    DotLegendEntry(1, "environment", "Environment", "steady dim", "steady dim", "An ambient system state is active."),
    DotLegendEntry(1, "idle", "Idle", "steady faint", "steady faint", "JR Bar is alive and watching."),
    DotLegendEntry(1, "none", "No state", "dark", "dark", "No semantic state is active."),
    DotLegendEntry(2, "fleet:none", "Fleet: none", "dark", "dark", "No main fleet member is visible."),
    DotLegendEntry(2, "fleet:solo", "Fleet: solo", "steady dim", "steady dim", "One main fleet member is visible."),
    DotLegendEntry(2, "fleet:small", "Fleet: small", "steady medium", "steady medium", "Two or three members are visible."),
    DotLegendEntry(2, "fleet:large", "Fleet: large", "steady high", "steady high", "Four or more members are visible."),
    DotLegendEntry(2, "unseen:none", "Unseen: none", "dark", "dark", "No unseen notification is present."),
    DotLegendEntry(
        2,
        "unseen:present",
        "Unseen: present",
        "steady high",
        "steady high",
        "At least one unseen notification is present.",
    ),
)

DOT_BINARY_HEARTBEAT_ACCESSIBILITY: Final = DotAccessibilityContract(
    legend_version=LEGEND_VERSION,
    color_is_supplemental=True,
    reduce_motion_is_static=True,
    max_flash_hz=MAX_FLASH_HZ,
    asking_failure_distinction=(
        "Asking uses two short pulses and failure uses a steady high marker. "
        "Under Reduce Motion, asking becomes steady medium while failure remains steady high."
    ),
    companion_ui_requirement=(
        "The Agent Browser or Screen Bar must name the selected state and identity; "
        "the two LEDs are a glanceable summary, not the only accessible status surface."
    ),
)


def fleet_size_band(fleet_size: int) -> FleetSizeBand:
    """Collapse a validated fleet size into one of four disclosed bands."""

    if type(fleet_size) is not int or not 0 <= fleet_size <= MAX_FLEET_SIZE:
        raise DotBinaryHeartbeatError("Dot fleet size must be a bounded nonnegative integer")
    if fleet_size == 0:
        return FleetSizeBand.NONE
    if fleet_size == 1:
        return FleetSizeBand.SOLO
    if fleet_size <= 3:
        return FleetSizeBand.SMALL
    return FleetSizeBand.LARGE


def _dark(led_index: int, meaning: str) -> DotLedInstruction:
    return DotLedInstruction(led_index, DotLedMode.DARK, 0.0, meaning, "dark")


def _steady(
    led_index: int,
    intensity: float,
    meaning: str,
    cue: str,
    *,
    motion_suppressed: bool = False,
) -> DotLedInstruction:
    return DotLedInstruction(
        led_index,
        DotLedMode.STEADY,
        intensity,
        meaning,
        cue,
        motion_suppressed=motion_suppressed,
    )


def _pulse(
    intensity: float,
    meaning: str,
    cue: str,
    cadence: DotPulseCadence,
) -> DotLedInstruction:
    return DotLedInstruction(1, DotLedMode.PULSE, intensity, meaning, cue, cadence)


def _primary_instruction(
    semantic: SemanticEventKind | None,
    *,
    reduce_motion: bool,
) -> DotLedInstruction:
    if semantic is None:
        return _dark(1, "no active semantic state")

    static_levels = {
        SemanticEventKind.ASK: (0.70, "asking", "steady medium"),
        SemanticEventKind.FAILURE: (1.00, "failure", "steady high"),
        SemanticEventKind.NOTIFICATION: (0.60, "notification", "steady medium-low"),
        SemanticEventKind.HANDOFF: (0.75, "handoff", "steady medium"),
        SemanticEventKind.WORK: (0.40, "working", "steady low"),
        SemanticEventKind.COMPLETION: (0.55, "completion", "steady medium-low"),
        SemanticEventKind.RECOVERY: (0.35, "recovery", "steady low"),
        SemanticEventKind.ENVIRONMENT: (0.22, "environment", "steady dim"),
        SemanticEventKind.IDLE: (0.12, "idle", "steady faint"),
    }
    intensity, meaning, static_cue = static_levels[semantic]
    if reduce_motion:
        return _steady(1, intensity, meaning, static_cue, motion_suppressed=True)
    if semantic is SemanticEventKind.ASK:
        return _pulse(0.85, meaning, "two short pulses", _ASK_CADENCE)
    if semantic is SemanticEventKind.NOTIFICATION:
        return _pulse(0.65, meaning, "one short pulse, three-second phrase", _NOTIFICATION_CADENCE)
    if semantic is SemanticEventKind.HANDOFF:
        return _pulse(0.70, meaning, "two long pulses", _HANDOFF_CADENCE)
    if semantic is SemanticEventKind.COMPLETION:
        return _pulse(0.60, meaning, "one short pulse, four-second phrase", _COMPLETION_CADENCE)
    if semantic is SemanticEventKind.RECOVERY:
        return _pulse(0.45, meaning, "two slow pulses", _RECOVERY_CADENCE)
    return _steady(1, intensity, meaning, static_cue)


def _fleet_instruction(band: FleetSizeBand) -> DotLedInstruction:
    if band is FleetSizeBand.NONE:
        return _dark(2, "no visible fleet members")
    if band is FleetSizeBand.SOLO:
        return _steady(2, 0.28, "one visible fleet member", "steady dim")
    if band is FleetSizeBand.SMALL:
        return _steady(2, 0.56, "two or three visible fleet members", "steady medium")
    return _steady(2, 0.85, "four or more visible fleet members", "steady high")


def _unseen_instruction(unseen_notification_present: bool) -> DotLedInstruction:
    if not unseen_notification_present:
        return _dark(2, "no unseen notification")
    return _steady(2, 0.85, "unseen notification present", "steady high")


def plan_dot_binary_heartbeat(
    active_semantics: tuple[SemanticEventKind, ...],
    *,
    secondary_policy: DotSecondaryPolicy,
    fleet_size: int = 0,
    unseen_notification_present: bool = False,
    reduce_motion: bool = False,
) -> DotBinaryHeartbeatPlan:
    """Plan the complete two-LED Dot output without performing any I/O."""

    if type(active_semantics) is not tuple:
        raise DotBinaryHeartbeatError("Dot active semantics must be an immutable tuple")
    if len(active_semantics) > MAX_ACTIVE_SEMANTICS:
        raise DotBinaryHeartbeatError("Dot active semantics must remain bounded")
    if not all(type(semantic) is SemanticEventKind for semantic in active_semantics):
        raise DotBinaryHeartbeatError("Dot active semantics must be known")
    if type(secondary_policy) is not DotSecondaryPolicy:
        raise DotBinaryHeartbeatError("Dot secondary policy must be explicit")
    if type(unseen_notification_present) is not bool:
        raise DotBinaryHeartbeatError("Dot unseen-notification presence must be a boolean")
    if type(reduce_motion) is not bool:
        raise DotBinaryHeartbeatError("Dot Reduce Motion value must be a boolean")

    selected = (
        max(active_semantics, key=lambda semantic: SEMANTIC_PRIORITY[semantic])
        if active_semantics
        else None
    )
    primary = _primary_instruction(selected, reduce_motion=reduce_motion)
    if secondary_policy is DotSecondaryPolicy.FLEET_SIZE:
        band = fleet_size_band(fleet_size)
        secondary = _fleet_instruction(band)
        unseen = None
    else:
        if type(fleet_size) is not int or not 0 <= fleet_size <= MAX_FLEET_SIZE:
            raise DotBinaryHeartbeatError("Dot fleet size must be a bounded nonnegative integer")
        band = None
        unseen = unseen_notification_present
        secondary = _unseen_instruction(unseen_notification_present)

    return DotBinaryHeartbeatPlan(
        primary=primary,
        secondary=secondary,
        selected_semantic=selected,
        secondary_policy=secondary_policy,
        fleet_size_band=band,
        unseen_notification_present=unseen,
        reduce_motion=reduce_motion,
    )


__all__ = [
    "DOT_BINARY_HEARTBEAT_ACCESSIBILITY",
    "DOT_BINARY_HEARTBEAT_LEGEND",
    "DOT_LED_COUNT",
    "LEGEND_VERSION",
    "MAX_ACTIVE_SEMANTICS",
    "MAX_FLASH_HZ",
    "MAX_FLEET_SIZE",
    "DotAccessibilityContract",
    "DotBinaryHeartbeatError",
    "DotBinaryHeartbeatPlan",
    "DotLedInstruction",
    "DotLedMode",
    "DotLegendEntry",
    "DotPulseCadence",
    "DotSecondaryPolicy",
    "FleetSizeBand",
    "fleet_size_band",
    "plan_dot_binary_heartbeat",
]
