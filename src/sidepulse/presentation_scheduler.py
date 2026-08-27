"""Pure presentation timer relevance planning.

The planner consumes only immutable scalar presentation state and emits the
shared runtime scheduler's immutable timer intents. It owns no AppKit object,
worker, callback, provider projection, environment reader, or timer registry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from .runtime_scheduler import RuntimeFeature, RuntimeTimerIntent

FRAME_FALLBACK_INTERVAL_SECONDS: Final = 1.0 / 60.0
ALCOVE_INTERVAL_SECONDS: Final = 1.5
POINTER_INTERVAL_SECONDS: Final = 0.2
MAX_REPEATING_TOLERANCE_SECONDS: Final = 0.25
ALCOVE_TOLERANCE_SECONDS: Final = round(
    min(
        MAX_REPEATING_TOLERANCE_SECONDS,
        ALCOVE_INTERVAL_SECONDS * 0.1,
    ),
    9,
)
POINTER_TOLERANCE_SECONDS: Final = round(
    min(
        MAX_REPEATING_TOLERANCE_SECONDS,
        POINTER_INTERVAL_SECONDS * 0.1,
    ),
    9,
)


def _finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _require_bool(value: object, field_name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"invalid presentation scheduler {field_name}")


@dataclass(frozen=True, slots=True)
class PresentationSchedulerInputs:
    """Immutable presentation relevance assembled on the main thread."""

    screen_bar_enabled: bool
    visible: bool
    display_asleep: bool
    app_terminating: bool
    animation_active: bool
    next_visual_change_at: float | None
    alcove_enabled: bool
    alcove_relevant: bool
    pointer_interaction_relevant: bool
    #: The Screen Bar's REAL frame interval while animating (seconds).
    #: None keeps the historical 60 Hz fallback. Carrying the actual
    #: cadence here is the fix the virtual_device known-hole comment
    #: prescribed: the resting 30 fps breathe no longer wakes the app
    #: 60 times a second to render nothing.
    frame_interval: float | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "screen_bar_enabled",
            "visible",
            "display_asleep",
            "app_terminating",
            "animation_active",
            "alcove_enabled",
            "alcove_relevant",
            "pointer_interaction_relevant",
        ):
            _require_bool(getattr(self, field_name), field_name)
        deadline = self.next_visual_change_at
        if deadline is not None:
            if not _finite_number(deadline) or float(deadline) <= 0.0:
                raise ValueError("invalid presentation scheduler deadline")
            object.__setattr__(self, "next_visual_change_at", float(deadline))
        interval = self.frame_interval
        if interval is not None:
            if not _finite_number(interval) or float(interval) <= 0.0:
                raise ValueError("invalid presentation frame interval")
            object.__setattr__(self, "frame_interval", float(interval))


@dataclass(frozen=True, slots=True)
class PresentationSchedulerState:
    """Bounded token proving one elapsed deadline was already reconciled."""

    consumed_deadline: float | None = None

    def __post_init__(self) -> None:
        deadline = self.consumed_deadline
        if deadline is None:
            return
        if not _finite_number(deadline) or float(deadline) <= 0.0:
            raise ValueError("invalid consumed presentation deadline")
        object.__setattr__(self, "consumed_deadline", float(deadline))


@dataclass(frozen=True, slots=True)
class PresentationSchedulePlan:
    """Timer intents plus the one bounded immediate-reconciliation edge."""

    intents: tuple[RuntimeTimerIntent, ...]
    reconcile_immediately: bool
    next_state: PresentationSchedulerState

    def __post_init__(self) -> None:
        if (
            type(self.intents) is not tuple
            or any(type(intent) is not RuntimeTimerIntent for intent in self.intents)
            or len({intent.feature for intent in self.intents}) != len(self.intents)
        ):
            raise ValueError("invalid presentation timer plan")
        if type(self.reconcile_immediately) is not bool:
            raise ValueError("invalid presentation reconciliation edge")
        if type(self.next_state) is not PresentationSchedulerState:
            raise ValueError("invalid presentation scheduler next state")


def _validated_now(now: object) -> float:
    if not _finite_number(now) or float(now) < 0.0:
        raise ValueError("invalid presentation scheduler now")
    return float(now)


def _next_repeating_fire(now: float, interval: float) -> float:
    fire_at = now + interval
    if not math.isfinite(fire_at) or fire_at <= now:
        fire_at = math.nextafter(now, math.inf)
    if not math.isfinite(fire_at) or fire_at <= now:
        raise ValueError("presentation scheduler now cannot produce a future timer")
    return fire_at


def _repeating_intent(
    feature: RuntimeFeature,
    *,
    now: float,
    interval: float,
    tolerance: float,
) -> RuntimeTimerIntent:
    return RuntimeTimerIntent(
        feature=feature,
        fire_at=_next_repeating_fire(now, interval),
        interval=interval,
        tolerance=tolerance,
        common_modes=True,
    )


def _active_surface(inputs: PresentationSchedulerInputs) -> bool:
    return inputs.screen_bar_enabled and inputs.visible and not inputs.display_asleep and not inputs.app_terminating


def plan_presentation_schedule(
    inputs: PresentationSchedulerInputs,
    *,
    now: float,
    state: PresentationSchedulerState | None = None,
) -> PresentationSchedulePlan:
    """Plan presentation relevance without creating timers or executing work.

    A past deadline produces a separate immediate reconciliation edge once.
    It never becomes a synthetic near-zero timer. The caller feeds
    ``next_state`` into the next reconciliation and clears the real deadline
    after applying the semantic transition.
    """

    if type(inputs) is not PresentationSchedulerInputs:
        raise ValueError("invalid presentation scheduler inputs")
    current_state = PresentationSchedulerState() if state is None else state
    if type(current_state) is not PresentationSchedulerState:
        raise ValueError("invalid presentation scheduler state")
    clock = _validated_now(now)
    deadline = inputs.next_visual_change_at

    if deadline is None or deadline > clock:
        next_state = PresentationSchedulerState()
    else:
        next_state = current_state

    if not _active_surface(inputs):
        return PresentationSchedulePlan((), False, next_state)

    deadline_elapsed = deadline is not None and deadline <= clock
    reconcile_immediately = bool(deadline_elapsed and current_state.consumed_deadline != deadline)
    if reconcile_immediately:
        assert deadline is not None
        next_state = PresentationSchedulerState(deadline)

    intents: list[RuntimeTimerIntent] = []
    if inputs.animation_active and not deadline_elapsed:
        intents.append(
            _repeating_intent(
                RuntimeFeature.PRESENTATION_FRAME_FALLBACK,
                now=clock,
                interval=inputs.frame_interval or FRAME_FALLBACK_INTERVAL_SECONDS,
                tolerance=0.0,
            )
        )
    if deadline is not None and deadline > clock:
        intents.append(
            RuntimeTimerIntent(
                feature=RuntimeFeature.PRESENTATION_STATIC_DEADLINE,
                fire_at=deadline,
                interval=None,
                tolerance=0.0,
                common_modes=True,
            )
        )
    if inputs.alcove_enabled and inputs.alcove_relevant:
        intents.append(
            _repeating_intent(
                RuntimeFeature.ALCOVE_OBSERVATION,
                now=clock,
                interval=ALCOVE_INTERVAL_SECONDS,
                tolerance=ALCOVE_TOLERANCE_SECONDS,
            )
        )
    if inputs.pointer_interaction_relevant:
        intents.append(
            _repeating_intent(
                RuntimeFeature.POINTER_PEEK,
                now=clock,
                interval=POINTER_INTERVAL_SECONDS,
                tolerance=POINTER_TOLERANCE_SECONDS,
            )
        )

    return PresentationSchedulePlan(
        intents=tuple(intents),
        reconcile_immediately=reconcile_immediately,
        next_state=next_state,
    )
