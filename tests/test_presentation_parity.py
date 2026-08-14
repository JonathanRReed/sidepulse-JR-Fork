from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sidepulse.accessibility_display import AccessibilityDisplayPreferences
from sidepulse.attention import (
    AttentionProjection,
    LifecycleMode,
    ProjectedAgentRow,
    SignalKind,
    TransientSignal,
)
from sidepulse.capacity_types import SourceKey
from sidepulse.led_status import LedDisplayState, LedStatusWrite
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.operator_state import (
    CanonicalOperatorEvent,
    InterruptionClass,
    SemanticEventKey,
    TransitionKind,
)
from sidepulse.presentation_policy import (
    CapacityGlance,
    GlanceOverrideReason,
    GlanceSemantic,
    MotionClass,
    SemanticGlyph,
    compose_presentation_program,
)
from sidepulse.provider_facts import (
    EventToken,
    ProviderWatermark,
    RequestIdentifier,
    RequestKey,
    SourceFreshness,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
)
from sidepulse.status_bar import (
    HardwareWriteRequest,
    HardwareWriteResult,
    StatusBarController,
    StatusBarDevice,
    hardware_presentation_sync_for_result,
)
from sidepulse.virtual_device import VirtualStatusDevice

_SOURCE = SourceKey("codex", "hooks", "local:test", "live_agent_events")
_WORK_KEY = WorkKey(_SOURCE, WorkIdentifier("work:test"))
_REQUEST_KEY = RequestKey(_WORK_KEY, RequestIdentifier("request:test"))


def _projection(
    lifecycle: LifecycleMode,
    *,
    actionable: bool = False,
    failure_event: SemanticEventKey | None = None,
) -> AttentionProjection:
    status = AgentStatus(
        provider="codex",
        agent_id="agent:test",
        display_name="Codex test",
        mode=AgentMode.UNKNOWN,
        updated_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        event_name="Canonical",
        work_key=_WORK_KEY,
        request_key=_REQUEST_KEY if actionable else None,
    )
    row = ProjectedAgentRow(
        agent_id=status.agent_id,
        provider=status.provider,
        display_name=status.display_name,
        lifecycle_mode=lifecycle,
        actionable=actionable,
        is_subagent=False,
        updated_at=status.updated_at,
        source_status=status,
        work_key=_WORK_KEY,
        request_key=_REQUEST_KEY if actionable else None,
    )
    return AttentionProjection(
        lifecycle_mode=lifecycle,
        actionable_attention=(row,) if actionable else (),
        visible_rows=(row,),
        transient_signals=(
            (
                TransientSignal(
                    failure_event,
                    SignalKind.FAILURE,
                    2,
                    None,
                ),
            )
            if failure_event is not None
            else ()
        ),
        dominant_provider="codex",
        click_target_agent_id=status.agent_id if actionable else None,
    )


def _event(kind: TransitionKind, token: str) -> CanonicalOperatorEvent:
    watermark = ProviderWatermark(
        _SOURCE,
        WatermarkBasis.PROVIDER_SEQUENCE,
        1_800_000_000.0,
        EventToken(token),
        1,
        1,
    )
    key = SemanticEventKey(_WORK_KEY, kind, watermark)
    interruption = {
        TransitionKind.FAILED: InterruptionClass.IMPORTANT_OUTCOME,
        TransitionKind.COMPLETED: InterruptionClass.COURTESY,
    }[kind]
    return CanonicalOperatorEvent(
        key,
        _WORK_KEY,
        kind,
        interruption,
        1_800_000_000.0,
        SourceFreshness.FRESH,
    )


def test_active_projection_resolves_once_before_literal_dot_pro_and_screen_bar_plans() -> None:
    preferences = AccessibilityDisplayPreferences()
    controller = SimpleNamespace(
        _relay_epoch=100.0,
        _accessibility_display_preferences=preferences,
    )
    projection = AttentionProjection(
        lifecycle_mode=LifecycleMode.ACTIVE,
        actionable_attention=(),
        visible_rows=(),
        transient_signals=(),
        dominant_provider="codex",
        click_target_agent_id=None,
    )

    resolved = StatusBarController.resolve_presentation_glance(
        controller,
        projection,
        operator_events=(),
        capacity=None,
        presentation_time=100.8,
    )
    plans = tuple(
        compose_presentation_program(
            resolved,
            presentation_time=100.8,
            led_count=led_count,
            color="#FFFFFF",
            preferences=preferences,
        )
        for led_count in (2, 8, 8)
    )

    assert (
        resolved.semantic,
        resolved.glyph,
        resolved.cue,
        resolved.override_reason,
        resolved.relay_epoch,
        resolved.next_visual_change_at,
    ) == (
        GlanceSemantic.ACTIVE,
        SemanticGlyph.CENTER_PAIR,
        None,
        GlanceOverrideReason.NONE,
        100.0,
        None,
    )
    assert tuple(
        (
            plan.semantic,
            plan.glyph,
            plan.motion,
            plan.relay_epoch,
            plan.static_fallback_dsl,
        )
        for plan in plans
    ) == (
        (
            GlanceSemantic.ACTIVE,
            SemanticGlyph.CENTER_PAIR,
            MotionClass.CONTINUOUS,
            100.0,
            "0:#A6A6A6; 1:#A6A6A6",
        ),
        (
            GlanceSemantic.ACTIVE,
            SemanticGlyph.CENTER_PAIR,
            MotionClass.CONTINUOUS,
            100.0,
            "0:#0D0D0D; 1:#0D0D0D; 2:#0D0D0D; 3:#A6A6A6; "
            "4:#A6A6A6; 5:#0D0D0D; 6:#0D0D0D; 7:#0D0D0D",
        ),
        (
            GlanceSemantic.ACTIVE,
            SemanticGlyph.CENTER_PAIR,
            MotionClass.CONTINUOUS,
            100.0,
            "0:#0D0D0D; 1:#0D0D0D; 2:#0D0D0D; 3:#A6A6A6; "
            "4:#A6A6A6; 5:#0D0D0D; 6:#0D0D0D; 7:#0D0D0D",
        ),
    )


@pytest.mark.parametrize(
    (
        "case",
        "projection",
        "operator_events",
        "capacity",
        "override_reason",
        "override_semantic",
        "preferences",
        "expected",
    ),
    (
        (
            "attention",
            _projection(LifecycleMode.WAITING, actionable=True),
            (),
            None,
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(),
            (
                "attention",
                "full_anchor",
                "attention:request:test",
                "none",
                100.0,
                101.28,
            ),
        ),
        (
            "fresh-failure",
            _projection(LifecycleMode.FAILED_VISIBLE),
            (_event(TransitionKind.FAILED, "event:failed"),),
            None,
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(),
            (
                "fresh_failure",
                "left_anchor",
                "failure:event:failed",
                "none",
                100.0,
                101.6,
            ),
        ),
        (
            "fresh-completion",
            _projection(LifecycleMode.COMPLETED_RECENTLY),
            (_event(TransitionKind.COMPLETED, "event:completed"),),
            None,
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(),
            (
                "fresh_completion",
                "right_anchor",
                "completion:event:completed",
                "none",
                100.0,
                101.6,
            ),
        ),
        (
            "active",
            _projection(LifecycleMode.ACTIVE),
            (),
            None,
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(),
            ("active", "center_pair", None, "none", 100.0, None),
        ),
        (
            "unresolved-failure",
            _projection(LifecycleMode.FAILED_VISIBLE),
            (),
            None,
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(),
            (
                "unresolved_failure",
                "left_anchor",
                None,
                "none",
                100.0,
                None,
            ),
        ),
        (
            "capacity",
            _projection(LifecycleMode.IDLE),
            (),
            CapacityGlance("codex", 0.25),
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(),
            ("capacity", "capacity_fill", None, "none", 100.0, None),
        ),
        (
            "rest",
            _projection(LifecycleMode.IDLE),
            (),
            None,
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(),
            ("rest", "rest", None, "none", 100.0, None),
        ),
        (
            "safety-override",
            _projection(LifecycleMode.WAITING, actionable=True),
            (),
            None,
            GlanceOverrideReason.SAFETY_SIGNAL,
            GlanceSemantic.REST,
            AccessibilityDisplayPreferences(),
            ("rest", "rest", None, "safety_signal", 100.0, None),
        ),
        (
            "device-mode-override",
            _projection(LifecycleMode.WAITING, actionable=True),
            (),
            None,
            GlanceOverrideReason.EXPLICIT_DEVICE_MODE,
            GlanceSemantic.REST,
            AccessibilityDisplayPreferences(),
            ("rest", "rest", None, "explicit_device_mode", 100.0, None),
        ),
        (
            "provider-pin-override",
            _projection(LifecycleMode.WAITING, actionable=True),
            (),
            None,
            GlanceOverrideReason.PROVIDER_PIN,
            GlanceSemantic.ACTIVE,
            AccessibilityDisplayPreferences(),
            ("active", "center_pair", None, "provider_pin", 100.0, None),
        ),
        (
            "focus-override",
            _projection(LifecycleMode.WAITING, actionable=True),
            (),
            None,
            GlanceOverrideReason.FOCUS,
            GlanceSemantic.REST,
            AccessibilityDisplayPreferences(),
            ("rest", "rest", None, "focus", 100.0, None),
        ),
        (
            "privacy-override",
            _projection(LifecycleMode.WAITING, actionable=True),
            (),
            None,
            GlanceOverrideReason.SHARED_SPACE_PRIVACY,
            GlanceSemantic.REST,
            AccessibilityDisplayPreferences(),
            ("rest", "rest", None, "shared_space_privacy", 100.0, None),
        ),
        (
            "unavailable-override",
            _projection(LifecycleMode.WAITING, actionable=True),
            (),
            None,
            GlanceOverrideReason.UNAVAILABLE,
            GlanceSemantic.REST,
            AccessibilityDisplayPreferences(),
            ("rest", "rest", None, "unavailable", 100.0, None),
        ),
        (
            "reduce-motion-consumes-cue",
            _projection(LifecycleMode.COMPLETED_RECENTLY),
            (_event(TransitionKind.COMPLETED, "event:reduced"),),
            None,
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(reduce_motion=True),
            ("fresh_completion", "right_anchor", None, "none", 100.0, None),
        ),
        (
            "reduce-transparency-keeps-cue",
            _projection(LifecycleMode.COMPLETED_RECENTLY),
            (_event(TransitionKind.COMPLETED, "event:transparent"),),
            None,
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(reduce_transparency=True),
            (
                "fresh_completion",
                "right_anchor",
                "completion:event:transparent",
                "none",
                100.0,
                101.6,
            ),
        ),
        (
            "increase-contrast-keeps-priority",
            _projection(LifecycleMode.ACTIVE),
            (),
            None,
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(increase_contrast=True),
            ("active", "center_pair", None, "none", 100.0, None),
        ),
        (
            "without-color-keeps-capacity",
            _projection(LifecycleMode.IDLE),
            (),
            CapacityGlance("codex", 0.25),
            GlanceOverrideReason.NONE,
            None,
            AccessibilityDisplayPreferences(differentiate_without_color=True),
            ("capacity", "capacity_fill", None, "none", 100.0, None),
        ),
    ),
)
def test_golden_semantic_matrix_is_literal_before_all_three_surface_projections(
    case,
    projection,
    operator_events,
    capacity,
    override_reason,
    override_semantic,
    preferences,
    expected,
) -> None:
    controller = SimpleNamespace(
        _relay_epoch=100.0,
        _accessibility_display_preferences=preferences,
    )

    resolved = StatusBarController.resolve_presentation_glance(
        controller,
        projection,
        operator_events=operator_events,
        capacity=capacity,
        override_reason=override_reason,
        override_semantic=override_semantic,
        presentation_time=100.8,
    )

    assert (
        resolved.semantic.value,
        resolved.glyph.value,
        None if resolved.cue is None else resolved.cue.event_key,
        resolved.override_reason.value,
        resolved.relay_epoch,
        resolved.next_visual_change_at,
    ) == expected, case
    for led_count in (2, 8, 8):
        plan = compose_presentation_program(
            resolved,
            presentation_time=100.8,
            led_count=led_count,
            color="#FFFFFF",
            preferences=preferences,
            capacity_remaining_fraction=(
                None if capacity is None else capacity.remaining_fraction
            ),
        )
        assert plan.semantic is resolved.semantic, case
        assert plan.glyph is resolved.glyph, case
        assert plan.relay_epoch == 100.0, case
        assert plan.motion.value == {
            "attention": "finite",
            "fresh-failure": "finite",
            "fresh-completion": "finite",
            "active": "continuous",
            "unresolved-failure": "static",
            "capacity": "static",
            "rest": "static",
            "safety-override": "static",
            "device-mode-override": "static",
            "provider-pin-override": "continuous",
            "focus-override": "static",
            "privacy-override": "static",
            "unavailable-override": "static",
            "reduce-motion-consumes-cue": "static",
            "reduce-transparency-keeps-cue": "finite",
            "increase-contrast-keeps-priority": "continuous",
            "without-color-keeps-capacity": "static",
        }[case]


def _request() -> HardwareWriteRequest:
    return HardwareWriteRequest(
        device=StatusBarDevice(
            device_id="sidepulse-test",
            name="SidePulse Test",
            root=Path("/Volumes/SidePulseTest"),
            target=Path("/Volumes/SidePulseTest/LEDS.LED"),
            connected=True,
            display="agent",
        ),
        mode=AgentMode.WORKING,
        battery_snapshot=None,
        statuses=(),
        projection=None,
        relay_elapsed_seconds=1.6,
    )


def test_physical_success_and_failure_keep_virtual_semantics_but_only_success_anchors() -> None:
    request = _request()
    success = HardwareWriteResult(
        request=request,
        write=LedStatusWrite(
            state=LedDisplayState.WORKING,
            target=request.device.target,
            program="#00E5FF",
            changed=True,
        ),
        label="SidePulse Test Working",
        agent_display_rendered=True,
        completed_at=100.0,
    )
    failure = HardwareWriteResult(
        request=request,
        write=LedStatusWrite(
            state=LedDisplayState.WORKING,
            target=None,
            program="",
            changed=False,
            error="write verification failed",
        ),
        label="SidePulse Test Working",
        agent_display_rendered=True,
        completed_at=101.0,
    )

    success_sync = hardware_presentation_sync_for_result(success)
    failure_sync = hardware_presentation_sync_for_result(failure)

    assert success_sync is not None
    assert success_sync.request is request
    assert success_sync.started_at == 100.0
    assert failure_sync is not None
    assert failure_sync.request is request
    assert failure_sync.started_at is None
    assert failure.write.error == "write verification failed"


def test_hardware_latency_success_and_failure_keep_the_canonical_episode_epoch() -> None:
    resolved = StatusBarController.resolve_presentation_glance(
        SimpleNamespace(
            _relay_epoch=100.0,
            _accessibility_display_preferences=AccessibilityDisplayPreferences(),
        ),
        _projection(LifecycleMode.ACTIVE),
        operator_events=(),
        capacity=None,
        presentation_time=100.8,
    )
    request = replace(
        _request(),
        resolved_glance=resolved,
        presentation_time=100.8,
    )
    writes = (
        LedStatusWrite(
            state=LedDisplayState.WORKING,
            target=request.device.target,
            program="#00E5FF",
            changed=True,
        ),
        LedStatusWrite(
            state=LedDisplayState.WORKING,
            target=None,
            program="",
            changed=False,
            error="write verification failed",
        ),
    )

    syncs = tuple(
        hardware_presentation_sync_for_result(
            HardwareWriteResult(
                request=request,
                write=write,
                label="SidePulse Test Working",
                agent_display_rendered=True,
                completed_at=900.0 + index,
            )
        )
        for index, write in enumerate(writes)
    )

    assert tuple(sync.started_at for sync in syncs if sync is not None) == (
        100.0,
        100.0,
    )
    assert resolved.cue is None


def test_unchanged_nonagent_hardware_result_does_not_replay_virtual_surface() -> None:
    request = _request()
    unchanged = HardwareWriteResult(
        request=request,
        write=LedStatusWrite(
            state=LedDisplayState.WORKING,
            target=request.device.target,
            program="",
            changed=False,
        ),
        label="SidePulse Test Timer",
        agent_display_rendered=False,
        completed_at=100.0,
    )

    assert hardware_presentation_sync_for_result(unchanged) is None


def test_accessibility_preferences_repaint_each_changed_dimension_without_recreating_renderer() -> None:
    device = VirtualStatusDevice.alloc().init()
    window = SimpleNamespace(isVisible=lambda: True)
    view = MagicMock()
    sampler = object()
    device.window = window
    device.view = view
    device._sampler = sampler
    device._sampler_factory = MagicMock()

    snapshots = (
        AccessibilityDisplayPreferences(reduce_motion=True),
        AccessibilityDisplayPreferences(reduce_transparency=True),
        AccessibilityDisplayPreferences(increase_contrast=True),
        AccessibilityDisplayPreferences(differentiate_without_color=True),
    )
    for generation, preferences in enumerate(snapshots, start=1):
        assert device.set_accessibility_display_preferences(
            preferences,
            generation=generation,
        )
        assert device.window is window
        assert device.view is view
        assert device._sampler is sampler

    assert [item.args[0] for item in view.setAccessibilityDisplayPreferences_.call_args_list] == list(
        snapshots
    )
    assert view.setNeedsDisplay_.call_count == 4
    device._sampler_factory.assert_not_called()

    assert not device.set_accessibility_display_preferences(
        snapshots[-1],
        generation=4,
    )
    assert not device.set_accessibility_display_preferences(
        AccessibilityDisplayPreferences(),
        generation=3,
    )
    assert view.setNeedsDisplay_.call_count == 4


def test_physical_accessibility_snapshot_is_frozen_validated_and_semantically_neutral() -> None:
    baseline = _request()
    preferences = AccessibilityDisplayPreferences(
        reduce_motion=True,
        reduce_transparency=True,
        increase_contrast=True,
        differentiate_without_color=True,
    )
    planned = replace(baseline, accessibility_preferences=preferences)

    assert planned.accessibility_preferences is preferences
    assert replace(planned, accessibility_preferences=None) == baseline
    assert planned.mode is baseline.mode
    assert planned.projection is baseline.projection
    assert planned.statuses == baseline.statuses
    with pytest.raises(FrozenInstanceError):
        planned.accessibility_preferences = AccessibilityDisplayPreferences()
    with pytest.raises(ValueError, match="invalid accessibility display preferences"):
        replace(baseline, accessibility_preferences=object())
