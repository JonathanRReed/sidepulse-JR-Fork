from __future__ import annotations

import builtins
import math
import os
import subprocess
import threading
import time
from dataclasses import FrozenInstanceError
from itertools import product

import pytest

from sidepulse.presentation_scheduler import (
    ALCOVE_INTERVAL_SECONDS,
    ALCOVE_TOLERANCE_SECONDS,
    FRAME_FALLBACK_INTERVAL_SECONDS,
    POINTER_INTERVAL_SECONDS,
    POINTER_TOLERANCE_SECONDS,
    PresentationSchedulePlan,
    PresentationSchedulerInputs,
    PresentationSchedulerState,
    plan_presentation_schedule,
    plan_presentation_timers,
)
from sidepulse.runtime_scheduler import (
    RUNTIME_TIMER_SELECTOR,
    AppKitTimerRegistry,
    RuntimeFeature,
)

NOW = 100.0


def _inputs(
    *,
    screen_bar_enabled: bool = True,
    visible: bool = True,
    display_asleep: bool = False,
    app_terminating: bool = False,
    animation_active: bool = False,
    next_visual_change_at: float | None = None,
    alcove_enabled: bool = False,
    alcove_relevant: bool = False,
    pointer_interaction_relevant: bool = False,
) -> PresentationSchedulerInputs:
    return PresentationSchedulerInputs(
        screen_bar_enabled=screen_bar_enabled,
        visible=visible,
        display_asleep=display_asleep,
        app_terminating=app_terminating,
        animation_active=animation_active,
        next_visual_change_at=next_visual_change_at,
        alcove_enabled=alcove_enabled,
        alcove_relevant=alcove_relevant,
        pointer_interaction_relevant=pointer_interaction_relevant,
    )


def _features(plan: PresentationSchedulePlan) -> tuple[RuntimeFeature, ...]:
    return tuple(intent.feature for intent in plan.intents)


@pytest.mark.parametrize(
    (
        "screen_bar_enabled",
        "visible",
        "display_asleep",
        "app_terminating",
        "animation_active",
        "alcove_enabled",
        "alcove_relevant",
        "pointer_interaction_relevant",
        "deadline_kind",
    ),
    tuple(
        (*flags, deadline_kind)
        for flags in product((False, True), repeat=8)
        for deadline_kind in ("none", "future", "elapsed")
    ),
)
def test_all_relevance_and_lifecycle_combinations_follow_the_timer_matrix(
    screen_bar_enabled: bool,
    visible: bool,
    display_asleep: bool,
    app_terminating: bool,
    animation_active: bool,
    alcove_enabled: bool,
    alcove_relevant: bool,
    pointer_interaction_relevant: bool,
    deadline_kind: str,
) -> None:
    """Catches any lifecycle or feature flag leaking a forbidden timer."""
    deadline = {
        "none": None,
        "future": NOW + 7.25,
        "elapsed": NOW - 0.25,
    }[deadline_kind]
    inputs = _inputs(
        screen_bar_enabled=screen_bar_enabled,
        visible=visible,
        display_asleep=display_asleep,
        app_terminating=app_terminating,
        animation_active=animation_active,
        next_visual_change_at=deadline,
        alcove_enabled=alcove_enabled,
        alcove_relevant=alcove_relevant,
        pointer_interaction_relevant=pointer_interaction_relevant,
    )

    plan = plan_presentation_schedule(inputs, now=NOW)

    active_surface = screen_bar_enabled and visible and not display_asleep and not app_terminating
    expected = set()
    if active_surface and animation_active and deadline_kind != "elapsed":
        expected.add(RuntimeFeature.PRESENTATION_FRAME_FALLBACK)
    if active_surface and deadline_kind == "future":
        expected.add(RuntimeFeature.PRESENTATION_STATIC_DEADLINE)
    if active_surface and alcove_enabled and alcove_relevant:
        expected.add(RuntimeFeature.ALCOVE_OBSERVATION)
    if active_surface and pointer_interaction_relevant:
        expected.add(RuntimeFeature.POINTER_PEEK)
    expected_features = tuple(feature for feature in RuntimeFeature if feature in expected)

    assert _features(plan) == expected_features
    assert plan.reconcile_immediately is (active_surface and deadline_kind == "elapsed")
    assert plan_presentation_timers(inputs, now=NOW) == plan.intents


@pytest.mark.parametrize(
    "inactive",
    [
        {"screen_bar_enabled": False},
        {"visible": False},
        {"display_asleep": True},
        {"app_terminating": True},
    ],
)
def test_inactive_lifecycle_states_produce_no_timer_or_immediate_work(
    inactive: dict[str, bool],
) -> None:
    """Catches hidden or terminating surfaces retaining callbacks or work."""
    inputs = _inputs(
        animation_active=True,
        next_visual_change_at=NOW + 1.0,
        alcove_enabled=True,
        alcove_relevant=True,
        pointer_interaction_relevant=True,
        **inactive,
    )

    plan = plan_presentation_schedule(inputs, now=NOW)

    assert plan.intents == ()
    assert plan.reconcile_immediately is False


def test_static_state_has_no_frame_driver_and_uses_one_exact_future_deadline() -> None:
    """Catches static output polling continuously or rounding its deadline."""
    deadline = NOW + 0.375
    plan = plan_presentation_schedule(
        _inputs(next_visual_change_at=deadline),
        now=NOW,
    )

    assert len(plan.intents) == 1
    intent = plan.intents[0]
    assert intent.feature is RuntimeFeature.PRESENTATION_STATIC_DEADLINE
    assert intent.fire_at == deadline
    assert intent.interval is None
    assert intent.tolerance == 0.0
    assert intent.common_modes is True

    no_deadline = plan_presentation_schedule(_inputs(), now=NOW)
    assert no_deadline.intents == ()


def test_elapsed_deadline_requests_one_immediate_reconciliation_then_is_consumed() -> None:
    """Catches an elapsed one-shot becoming a zero-delay reconciliation loop."""
    deadline = NOW - 1.0
    first = plan_presentation_schedule(
        _inputs(animation_active=True, next_visual_change_at=deadline),
        now=NOW,
    )

    assert first.intents == ()
    assert first.reconcile_immediately is True
    assert first.next_state.consumed_deadline == deadline

    repeated = plan_presentation_schedule(
        _inputs(animation_active=True, next_visual_change_at=deadline),
        now=NOW + 0.5,
        state=first.next_state,
    )
    assert repeated.intents == ()
    assert repeated.reconcile_immediately is False
    assert repeated.next_state == first.next_state

    new_elapsed = plan_presentation_schedule(
        _inputs(animation_active=True, next_visual_change_at=deadline + 0.25),
        now=NOW + 0.5,
        state=repeated.next_state,
    )
    assert new_elapsed.reconcile_immediately is True
    assert new_elapsed.next_state.consumed_deadline == deadline + 0.25


def test_deadline_token_clears_only_after_the_deadline_is_withdrawn() -> None:
    """Catches a reused future episode being suppressed by an obsolete token."""
    deadline = NOW - 1.0
    consumed = plan_presentation_schedule(
        _inputs(next_visual_change_at=deadline),
        now=NOW,
    )
    cleared = plan_presentation_schedule(
        _inputs(next_visual_change_at=None),
        now=NOW,
        state=consumed.next_state,
    )
    replayed = plan_presentation_schedule(
        _inputs(next_visual_change_at=deadline),
        now=NOW,
        state=cleared.next_state,
    )

    assert cleared.next_state.consumed_deadline is None
    assert replayed.reconcile_immediately is True


def test_inactive_surface_does_not_consume_an_elapsed_deadline() -> None:
    """Catches hidden state silently discarding the reconciliation due on show."""
    deadline = NOW - 1.0
    hidden = plan_presentation_schedule(
        _inputs(visible=False, next_visual_change_at=deadline),
        now=NOW,
    )
    shown = plan_presentation_schedule(
        _inputs(visible=True, next_visual_change_at=deadline),
        now=NOW,
        state=hidden.next_state,
    )

    assert hidden.reconcile_immediately is False
    assert hidden.next_state.consumed_deadline is None
    assert shown.reconcile_immediately is True


def test_repeating_feature_intervals_tolerances_and_relevance_are_exact() -> None:
    """Catches observation jitter policy or feature gating drifting."""
    deadline = NOW + 2.0
    plan = plan_presentation_schedule(
        _inputs(
            animation_active=True,
            next_visual_change_at=deadline,
            alcove_enabled=True,
            alcove_relevant=True,
            pointer_interaction_relevant=True,
        ),
        now=NOW,
    )
    by_feature = {intent.feature: intent for intent in plan.intents}

    frame = by_feature[RuntimeFeature.PRESENTATION_FRAME_FALLBACK]
    assert frame.fire_at == pytest.approx(NOW + FRAME_FALLBACK_INTERVAL_SECONDS)
    assert frame.interval == pytest.approx(FRAME_FALLBACK_INTERVAL_SECONDS)
    assert frame.tolerance == 0.0
    assert frame.common_modes is True

    static_deadline = by_feature[RuntimeFeature.PRESENTATION_STATIC_DEADLINE]
    assert static_deadline.fire_at == deadline
    assert static_deadline.interval is None
    assert static_deadline.tolerance == 0.0

    alcove = by_feature[RuntimeFeature.ALCOVE_OBSERVATION]
    assert alcove.fire_at == pytest.approx(NOW + ALCOVE_INTERVAL_SECONDS)
    assert alcove.interval == ALCOVE_INTERVAL_SECONDS
    assert alcove.tolerance == ALCOVE_TOLERANCE_SECONDS == 0.15
    assert alcove.tolerance == pytest.approx(min(0.25, alcove.interval * 0.1))

    pointer = by_feature[RuntimeFeature.POINTER_PEEK]
    assert pointer.fire_at == pytest.approx(NOW + POINTER_INTERVAL_SECONDS)
    assert pointer.interval == POINTER_INTERVAL_SECONDS
    assert pointer.tolerance == POINTER_TOLERANCE_SECONDS == 0.02
    assert pointer.tolerance == pytest.approx(min(0.25, pointer.interval * 0.1))

    alcove_disabled = plan_presentation_schedule(
        _inputs(alcove_enabled=False, alcove_relevant=True),
        now=NOW,
    )
    pointer_irrelevant = plan_presentation_schedule(
        _inputs(pointer_interaction_relevant=False),
        now=NOW,
    )
    assert RuntimeFeature.ALCOVE_OBSERVATION not in _features(alcove_disabled)
    assert RuntimeFeature.POINTER_PEEK not in _features(pointer_irrelevant)


def test_inputs_plan_and_consumed_state_are_frozen_and_validate_scalar_boundaries() -> None:
    """Catches mutable canonical payloads or malformed clocks entering planning."""
    inputs = _inputs()
    state = PresentationSchedulerState()
    plan = plan_presentation_schedule(inputs, now=NOW, state=state)

    with pytest.raises(FrozenInstanceError):
        inputs.visible = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.reconcile_immediately = True  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        state.consumed_deadline = 1.0  # type: ignore[misc]
    with pytest.raises(TypeError):
        inputs.canonical_state = object()  # type: ignore[attr-defined]

    for field in (
        "screen_bar_enabled",
        "visible",
        "display_asleep",
        "app_terminating",
        "animation_active",
        "alcove_enabled",
        "alcove_relevant",
        "pointer_interaction_relevant",
    ):
        values = {
            "screen_bar_enabled": True,
            "visible": True,
            "display_asleep": False,
            "app_terminating": False,
            "animation_active": False,
            "next_visual_change_at": None,
            "alcove_enabled": False,
            "alcove_relevant": False,
            "pointer_interaction_relevant": False,
        }
        values[field] = 1
        with pytest.raises(ValueError, match=field):
            PresentationSchedulerInputs(**values)  # type: ignore[arg-type]

    for deadline in (0.0, -1.0, math.nan, math.inf, -math.inf, True, "soon"):
        with pytest.raises(ValueError, match="deadline"):
            _inputs(next_visual_change_at=deadline)  # type: ignore[arg-type]
    for now in (-1.0, math.nan, math.inf, -math.inf, True, "now"):
        with pytest.raises(ValueError, match="now"):
            plan_presentation_schedule(inputs, now=now)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="inputs"):
        plan_presentation_schedule(object(), now=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="state"):
        plan_presentation_schedule(inputs, now=NOW, state=object())  # type: ignore[arg-type]


class _FakeTimer:
    def __init__(
        self,
        *,
        delay: float,
        interval: float | None,
        target: object,
        selector: str,
        user_info: RuntimeFeature,
    ) -> None:
        self.delay = delay
        self.interval = interval
        self.target = target
        self.selector = selector
        self._user_info = user_info
        self.tolerance: float | None = None
        self.invalidations = 0

    def userInfo(self) -> RuntimeFeature:
        return self._user_info

    def setTolerance_(self, tolerance: float) -> None:
        self.tolerance = tolerance

    def invalidate(self) -> None:
        self.invalidations += 1


class _FakeAppKitFactory:
    def __init__(self) -> None:
        self.created: list[_FakeTimer] = []
        self.registrations: list[tuple[_FakeTimer, bool]] = []

    def create_timer(
        self,
        *,
        delay: float,
        interval: float | None,
        target: object,
        selector: str,
        user_info: RuntimeFeature,
    ) -> _FakeTimer:
        timer = _FakeTimer(
            delay=delay,
            interval=interval,
            target=target,
            selector=selector,
            user_info=user_info,
        )
        self.created.append(timer)
        return timer

    def register_timer(self, timer: _FakeTimer, *, common_modes: bool) -> None:
        self.registrations.append((timer, common_modes))

    @property
    def live_timer_count(self) -> int:
        return sum(timer.invalidations == 0 for timer in self.created)


def _registry(
    factory: _FakeAppKitFactory,
) -> AppKitTimerRegistry:
    return AppKitTimerRegistry(
        handlers={feature: lambda: None for feature in RuntimeFeature},
        timer_factory=factory,
        monotonic=lambda: NOW,
    )


def test_same_plan_reconciles_one_timer_per_feature_and_termination_invalidates_all() -> None:
    """Catches a planner creating duplicate AppKit timers on stable input."""
    factory = _FakeAppKitFactory()
    registry = _registry(factory)
    target = object()
    plan = plan_presentation_schedule(
        _inputs(
            animation_active=True,
            next_visual_change_at=NOW + 5.0,
            alcove_enabled=True,
            alcove_relevant=True,
            pointer_interaction_relevant=True,
        ),
        now=NOW,
    )

    for _ in range(100):
        registry.reconcile(plan.intents, target=target)

    snapshot = registry.snapshot()
    assert snapshot.active_features == _features(plan)
    assert snapshot.created == len(plan.intents) == 4
    assert snapshot.invalidated == 0
    assert factory.live_timer_count == 4
    assert len(factory.registrations) == 4
    assert all(timer.target is target for timer in factory.created)
    assert all(timer.selector == RUNTIME_TIMER_SELECTOR for timer in factory.created)

    terminating = plan_presentation_schedule(
        _inputs(
            app_terminating=True,
            animation_active=True,
            next_visual_change_at=NOW + 5.0,
            alcove_enabled=True,
            alcove_relevant=True,
            pointer_interaction_relevant=True,
        ),
        now=NOW,
        state=plan.next_state,
    )
    registry.reconcile(terminating.intents, target=target)

    assert registry.snapshot().active_features == ()
    assert factory.live_timer_count == 0
    assert all(timer.invalidations == 1 for timer in factory.created)


def test_fifty_lifecycle_cycles_leave_no_timer_or_thread_growth() -> None:
    """Catches hide, wake, screen, or termination churn leaking runtime work."""
    factory = _FakeAppKitFactory()
    registry = _registry(factory)
    target = object()
    before_threads = tuple(thread.ident for thread in threading.enumerate())
    max_live = 0

    active = _inputs(
        animation_active=True,
        alcove_enabled=True,
        alcove_relevant=True,
        pointer_interaction_relevant=True,
    )
    for _ in range(50):
        active_plan = plan_presentation_schedule(active, now=NOW)
        registry.reconcile(active_plan.intents, target=target)
        max_live = max(max_live, factory.live_timer_count)

        # A screen reconciliation with unchanged relevance must not duplicate.
        registry.reconcile(
            plan_presentation_schedule(active, now=NOW).intents,
            target=target,
        )
        max_live = max(max_live, factory.live_timer_count)

        for lifecycle in (
            _inputs(visible=False, animation_active=True),
            _inputs(display_asleep=True, animation_active=True),
        ):
            registry.reconcile(
                plan_presentation_schedule(lifecycle, now=NOW).intents,
                target=target,
            )
            assert factory.live_timer_count == 0

        registry.reconcile(active_plan.intents, target=target)
        max_live = max(max_live, factory.live_timer_count)
        registry.reconcile(
            plan_presentation_schedule(
                _inputs(app_terminating=True, animation_active=True),
                now=NOW,
            ).intents,
            target=target,
        )
        assert factory.live_timer_count == 0

    assert max_live == 3
    assert registry.snapshot().active_features == ()
    assert registry.snapshot().created == registry.snapshot().invalidated == 300
    assert tuple(thread.ident for thread in threading.enumerate()) == before_threads


def test_planning_executes_no_poll_capture_file_subprocess_wait_or_thread_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches presentation relevance planning performing callback-forbidden work."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("forbidden presentation planning work")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(time, "sleep", forbidden)
    monkeypatch.setattr(threading.Condition, "wait", forbidden)
    monkeypatch.setattr(threading, "Thread", forbidden)

    plan = plan_presentation_schedule(
        _inputs(
            animation_active=True,
            next_visual_change_at=NOW + 1.0,
            alcove_enabled=True,
            alcove_relevant=True,
            pointer_interaction_relevant=True,
        ),
        now=NOW,
    )

    assert _features(plan) == (
        RuntimeFeature.PRESENTATION_FRAME_FALLBACK,
        RuntimeFeature.PRESENTATION_STATIC_DEADLINE,
        RuntimeFeature.ALCOVE_OBSERVATION,
        RuntimeFeature.POINTER_PEEK,
    )
