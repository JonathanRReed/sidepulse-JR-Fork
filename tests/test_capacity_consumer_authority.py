from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sidepulse.capacity_authority import select_binding_lanes
from sidepulse.capacity_calibration import ForecastReleaseAuthority
from sidepulse.capacity_types import (
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ExecutionContext,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.settings import LED_DISPLAY_QUOTA_RUNWAY, AgentMonitorSettings
from tests.test_sidepulse import isolate_controller

NOW = 1_000.0


def _source(provider_id: str = "codex") -> SourceKey:
    return SourceKey(
        provider_id=provider_id,
        adapter_id="fixture",
        source_instance_id="local",
        capability_id="remote_quota_windows",
    )


def _observation(
    *,
    source: SourceKey | None = None,
    effect: QuotaEffect = QuotaEffect.ALL_WORKLOADS,
    model: str | None = None,
    value_state: ObservationState = ObservationState.OBSERVED,
    remaining: float = 5.0,
    reset_state: ResetState = ResetState.FUTURE,
    health_kind: SourceHealthKind = SourceHealthKind.HEALTHY,
    has_last_known_good: bool = False,
) -> QuotaLaneObservation:
    source = source or _source()
    reset_epoch = NOW + 300.0
    if reset_state in {ResetState.UNKNOWN, ResetState.UNAVAILABLE}:
        reset_epoch = None
    health = CapacitySourceHealth(
        source=source,
        kind=health_kind,
        observed_at=NOW,
        last_attempt_at=NOW,
        retry_at=None,
        reason_code=("source_failed" if health_kind is not SourceHealthKind.HEALTHY else None),
        has_last_known_good=has_last_known_good,
    )
    return QuotaLaneObservation(
        key=QuotaLaneKey(
            source=source,
            opaque_scope="all",
            pool="plan",
            model=model,
            window="session",
            effect=effect,
        ),
        semantic_name="Session window",
        horizon=QuotaHorizon.SHORT,
        value=CapacityValue(
            unit=CapacityUnit.PERCENT_REMAINING,
            remaining=remaining,
            state=value_state,
        ),
        reset=ResetFact(
            state=reset_state,
            reset_epoch=reset_epoch,
            window_minutes=300.0,
            observed_at=NOW,
        ),
        observed_at=NOW,
        source_health=health,
        account_discriminator="account-1",
    )


# The exact refusal each adversary must earn. Naming the CODE, not just
# "something was refused", is what stops this from passing vacuously: two of
# these cases used to be excluded from the only assertion that touched the
# gate, because including them would have failed.
_EXPECTED_REFUSAL = {
    "stale_last_known_good": "source_failed",
    "model_inapplicable": "model_mismatch",
    "unknown_source": "source_out_of_context",
    "missing_reset": "reset_not_credible",
    "partial_observation": "usage_partial",
    # The one legitimate reading. It binds, and that is the point: a gate that
    # refused everything would prove nothing about the five above.
    "withheld_forecast_authority": None,
}
# Whether the lane may still be SHOWN. A refused BINDING is not the same as a
# hidden row: a stale number and a window with no reset are true things that
# happened, while an inapplicable, foreign or partial reading has no honest
# number to render at all.
_EXPECTED_PRESENTABLE = {
    "stale_last_known_good": True,
    "model_inapplicable": False,
    "unknown_source": False,
    "missing_reset": True,
    "partial_observation": False,
    "withheld_forecast_authority": True,
}


def _canonical_case(case_name: str) -> tuple[CapacitySnapshot | None, ExecutionContext]:
    context = ExecutionContext(("codex",), ("local",), "gpt-5", None)
    if case_name == "raw_percent":
        return None, context
    if case_name == "stale_last_known_good":
        observation = _observation(
            value_state=ObservationState.LAST_KNOWN_GOOD,
            reset_state=ResetState.STALE,
            health_kind=SourceHealthKind.FAILED,
            has_last_known_good=True,
        )
    elif case_name == "model_inapplicable":
        observation = _observation(
            effect=QuotaEffect.MODEL,
            model="gpt-4.1",
        )
    elif case_name == "unknown_source":
        observation = _observation(source=_source("mystery"))
    elif case_name == "missing_reset":
        observation = _observation(reset_state=ResetState.UNKNOWN)
    elif case_name == "partial_observation":
        observation = _observation(value_state=ObservationState.PARTIAL)
    elif case_name == "withheld_forecast_authority":
        observation = _observation()
    else:
        raise AssertionError(f"unknown adversary: {case_name}")
    return (
        CapacitySnapshot(
            observed_at=NOW,
            lanes=(observation,),
            source_health=(observation.source_health,),
        ),
        context,
    )


@pytest.fixture
def controller(request):
    class ControllerCase:
        def __init__(self) -> None:
            self._cleanups = []

        def addCleanup(self, callback) -> None:
            self._cleanups.append(callback)

        def skipTest(self, reason: str) -> None:
            pytest.skip(reason)

        def close(self) -> None:
            for callback in reversed(self._cleanups):
                callback()

    case = ControllerCase()
    isolate_controller(case)
    request.addfinalizer(case.close)
    return case.controller, case.status_bar


@pytest.mark.parametrize(
    "case_name",
    (
        "raw_percent",
        "stale_last_known_good",
        "model_inapplicable",
        "unknown_source",
        "missing_reset",
        "partial_observation",
        "withheld_forecast_authority",
    ),
)
def test_unauthoritative_capacity_cannot_reach_any_consumer(
    case_name: str,
    controller,
) -> None:
    """Legacy raw capacity cannot escape even when old preferences were enabled."""
    target, status_bar = controller
    snapshot, context = _canonical_case(case_name)
    if snapshot is not None:
        projection = select_binding_lanes(
            snapshot, context, NOW, allow_unbound_legacy=True
        )
        (authority,) = projection.detail_lanes
        expected = _EXPECTED_REFUSAL[case_name]

        assert authority.refusal_code == expected
        assert authority.presentable is _EXPECTED_PRESENTABLE[case_name]
        assert projection.binding_lanes == (() if expected else (authority,))

    assert ForecastReleaseAuthority.withheld().permitted_claim_classes == ()
    target.settings = replace(
        AgentMonitorSettings(),
        quota_alerts_enabled=True,
        quota_alert_thresholds=(50.0, 90.0),
        escalation_webhook_url="https://example.invalid/capacity",
        webhook_events=("quota_threshold", "quota_sunrise"),
    )
    target.post_webhook = MagicMock()
    target.post_completion_notification = MagicMock()
    before_queue = target._capacity_refresh_coordinator.snapshot_state(NOW)

    with patch.object(target, "refresh_") as refresh:
        target.track_quota_thresholds({f"{case_name} window": 40.0})
        target.track_quota_thresholds({f"{case_name} window": 95.0})

    assert target.post_completion_notification.call_count == 0
    assert target.post_webhook.call_count == 0
    assert target._capacity_refresh_coordinator.snapshot_state(NOW) == before_queue
    assert target.quota_blink_until == 0.0
    assert target.completion_sweep_until == 0.0
    refresh.assert_not_called()
    # The LEGACY capacity plane still cannot feed the runway LED: this
    # base controller's producer stays None. Since 2026-08-26 the
    # provider-usage facade overrides it with the JR plane's gated
    # lanes (quota_runway.py) -- a different, authoritative producer,
    # exactly what this test's contract demanded.
    assert target.quota_runway_state() is None
    runway_factory = target.signal_display_entries()[LED_DISPLAY_QUOTA_RUNWAY][0]
    assert runway_factory(255, 8) is None

    device = status_bar.StatusBarDevice(
        device_id="isolated-capacity-device",
        name="Isolated Capacity Device",
        root=Path("/private/tmp/isolated-capacity-device"),
        target=Path("/private/tmp/isolated-capacity-device/LEDS.LED"),
        connected=True,
        display=LED_DISPLAY_QUOTA_RUNWAY,
    )
    assert target.active_led_display_kind_for_device(device, None) != LED_DISPLAY_QUOTA_RUNWAY


def test_raw_percentage_cannot_become_a_presentation_capacity_glance(controller) -> None:
    """The shared presentation resolver cannot receive a legacy used percent."""
    target, _status_bar = controller
    target.quota_last_percents = {"Codex weekly": 99.0}

    assert target.presentation_capacity_glance() is None


def test_raw_percentage_cannot_populate_screen_bar_capacity_gauge(controller) -> None:
    """The completion gauge remains usable while the capacity side stays empty."""
    target, status_bar = controller
    virtual = SimpleNamespace(
        set_wraps_menu_bar=MagicMock(),
        set_geometry_overrides=MagicMock(),
        set_bracket_style=MagicMock(),
        set_min_glow=MagicMock(),
        set_follow_alcove=MagicMock(),
        set_standing_gauges=MagicMock(),
        set_click_handler=MagicMock(),
        set_pointer_interaction_relevant=MagicMock(),
        set_program=MagicMock(),
    )
    target.virtual_status_device = virtual
    target.settings = (
        target.settings.with_virtual_status_device(True).with_screen_bar_gauges_enabled(
            True
        )
    )
    target.quota_last_percents = {"Codex weekly": 95.0}
    device = status_bar.StatusBarDevice(
        device_id=status_bar.VIRTUAL_DEVICE_ID,
        name=status_bar.VIRTUAL_DEVICE_NAME,
        root=Path("/private/tmp/isolated-capacity-screen-bar"),
        target=Path("/private/tmp/isolated-capacity-screen-bar/virtual"),
        connected=True,
        display=status_bar.LED_DISPLAY_AGENT,
    )

    with (
        patch.object(target, "status_bar_devices", return_value=[device]),
        patch.object(target, "effective_brightness_for_device", return_value=255),
        patch.object(target, "screen_bar_click_status", return_value=None),
    ):
        target.sync_virtual_status_device(status_bar.AgentMode.IDLE_READY, None)

    virtual.set_standing_gauges.assert_called_once_with(0.0, False)


def test_raw_percentage_cannot_populate_peek_hardware_program(controller) -> None:
    """Peek can show a real timebox, but no legacy capacity horizon."""
    target, _status_bar = controller
    target.quota_last_percents = {"Claude weekly": 20.0, "Codex weekly": 95.0}

    assert target.peek_program(255, led_count=8) == "off"
