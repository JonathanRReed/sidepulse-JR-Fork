"""Stable production layer for SidePulse's historical AppKit controller.

The original controller remains the compatibility runtime while production
boundaries are extracted into small, testable modules. The facade preserves
the public ``sidepulse.status_bar`` contract, including test monkeypatches and
source introspection. Runtime mutation is owned by
``sidepulse.application_composition`` and never happens merely by importing
this module.
"""

from __future__ import annotations

import sys
import threading
import time
from types import ModuleType

from . import status_bar_legacy as _legacy
from .ambient_effect_consumer import (
    active_hardware_ambient_presentation,
    active_screen_bar_ambient_presentation,
)
from .battery_runtime import BatteryObservation, BatteryObservationService
from .core_state import CoreDomain, CoreStateStore, StateDelta
from .device_projection import light_rows_for_provider, projection_for_provider
from .effect_studio_physical_preview import (
    EffectStudioPhysicalPreviewAdapter,
    PreviewReleaseReason,
)
from .effect_studio_window import EffectStudioWindowController
from .hardware_write_policy import hardware_coalesce_key
from .intake_runtime import IntakeProbeResult, IntakeProbeService
from .ledger_runtime import LedgerPublishResult, RemoteLedgerPublisher
from .local_health import LocalHealthMonitor, LocalHealthSnapshot, format_local_health
from .performance_metrics import PerformanceRegistry, PerformanceSnapshot
from .refresh_admission import RefreshAdmission, admit_refresh
from .screen_bar_pipeline import DEFAULT_PRESENTATION_METRICS
from .transcript_runtime import TranscriptFallbackBatch, TranscriptFallbackService
from .webhook_delivery import (
    WebhookDeliveryReceipt,
    WebhookDeliveryService,
)
from .why_light_context import OutputTimingSource
from .why_light_runtime import project_current_why_light_context

# Keep production admission aligned with the retained controller's normal
# cadence. A shorter override made chatty agent events rebuild the AppKit menu
# and output state on the main thread far more often than the UI can usefully
# display them.
EVENT_COALESCE_SECONDS = _legacy.EVENT_REFRESH_FLOOR_SECONDS
FULL_REFRESH_HEARTBEAT_SECONDS = _legacy.STATUS_BAR_REFRESH_SECONDS

_LegacyStatusBarController = _legacy._AppKitStatusBarController


def _release_effect_studio_preview(
    controller,
    reason: PreviewReleaseReason,
) -> bool:
    adapter = getattr(
        controller,
        "_effect_studio_physical_preview_adapter",
        None,
    )
    if adapter is None:
        return False
    active = getattr(adapter, "active_session", None)
    session_id = getattr(active, "session_id", None)
    try:
        released = bool(adapter.release(reason))
    except Exception:
        released = False
    if not released:
        return False
    window = getattr(controller, "_effect_studio_window_controller", None)
    callback = getattr(window, "physicalPreviewDidRelease_", None)
    if callable(callback):
        callback({"session_id": session_id, "reason": reason.value})
    return True


def _terminate_controller(controller, notification, *, legacy_terminate=None):
    if getattr(controller, "_runtime_termination_started", False):
        return None
    started = time.perf_counter()
    outcome = "ok"
    terminate = (
        _LegacyStatusBarController.applicationWillTerminate_
        if legacy_terminate is None
        else legacy_terminate
    )
    try:
        preview_released = _release_effect_studio_preview(
            controller,
            PreviewReleaseReason.APP_TERMINATION,
        )
        writer = getattr(controller, "_hardware_write_worker", None)
        if preview_released and writer is not None:
            wait_idle = getattr(writer, "wait_idle", None)
            if callable(wait_idle):
                wait_idle(timeout_seconds=1.0)
        for attribute in (
            "_production_battery_service",
            "_production_transcript_service",
            "_production_intake_service",
            "_production_ledger_publisher",
            "_production_webhook_service",
        ):
            service = getattr(controller, attribute, None)
            if service is not None:
                service.close()
        return terminate(controller, notification)
    except BaseException:
        outcome = "error"
        raise
    finally:
        controller._performance().record(
            "shutdown",
            (time.perf_counter() - started) * 1000.0,
            outcome=outcome,
        )


_existing_controller = globals().get("JRStatusBarController")
if (
    isinstance(_existing_controller, type)
    and _existing_controller.__name__ == "JRStatusBarController"
):
    JRStatusBarController = _existing_controller
else:

    class JRStatusBarController(_LegacyStatusBarController):
        """Production boundary around the retained controller implementation."""

        def _performance(self) -> PerformanceRegistry:
            registry = getattr(self, "_production_performance_registry", None)
            if registry is None:
                registry = PerformanceRegistry()
                self._production_performance_registry = registry
            return registry

        def performance_snapshot(self) -> PerformanceSnapshot:
            return self._performance().snapshot()

        def _effect_studio_preview_runtime(
            self,
        ) -> EffectStudioPhysicalPreviewAdapter:
            adapter = getattr(
                self,
                "_effect_studio_physical_preview_adapter",
                None,
            )
            if not isinstance(adapter, EffectStudioPhysicalPreviewAdapter):
                adapter = EffectStudioPhysicalPreviewAdapter(self)
                self._effect_studio_physical_preview_adapter = adapter
            return adapter

        @_legacy.objc.IBAction
        def openEffectStudio_(self, _sender) -> None:
            """Open the Studio through one retained AppKit owner and runtime cache."""

            controller = getattr(self, "_effect_studio_window_controller", None)
            if not isinstance(controller, EffectStudioWindowController):
                controller = EffectStudioWindowController.alloc().init()
                self._effect_studio_window_controller = controller
            controller.open(
                assignment_cache=getattr(
                    self,
                    "_effect_assignment_cache",
                    None,
                ),
                physical_preview=self._effect_studio_preview_runtime(),
            )

        def _schedule_effect_studio_preview_timeout(
            self,
            session_id: str,
            duration_seconds: float,
        ) -> None:
            self._cancel_effect_studio_preview_timeout()
            self._effect_studio_preview_timer = (
                _legacy.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    min(30.0, max(0.001, float(duration_seconds))),
                    self,
                    "effectStudioPreviewExpired:",
                    session_id,
                    False,
                )
            )

        def _cancel_effect_studio_preview_timeout(self) -> None:
            timer = getattr(self, "_effect_studio_preview_timer", None)
            self._effect_studio_preview_timer = None
            if timer is not None:
                timer.invalidate()

        @_legacy.objc.IBAction
        def effectStudioPreviewExpired_(self, timer) -> None:
            session_id = str(timer.userInfo() or "")
            adapter = self._effect_studio_preview_runtime()
            active = adapter.active_session
            if active is None or active.session_id != session_id:
                return
            _release_effect_studio_preview(self, PreviewReleaseReason.TIMEOUT)

        @_legacy.objc.IBAction
        def effectStudioPreviewWriteFailed_(self, payload) -> None:
            adapter = self._effect_studio_preview_runtime()
            active = adapter.active_session
            hardware_device_id = str(
                payload.get("hardware_device_id") or ""
            ) if payload else ""
            session_id = str(payload.get("session_id") or "") if payload else ""
            if (
                active is None
                or not session_id
                or active.session_id != session_id
                or active.hardware_device_id != hardware_device_id
            ):
                return
            _release_effect_studio_preview(self, PreviewReleaseReason.ERROR)

        def _restore_effect_studio_physical_output(self, device_id: str) -> None:
            self.reset_led_controllers_for_device(device_id)
            snapshot = getattr(self, "last_snapshot", None)
            if snapshot is None:
                self.refresh_(None)
                return
            projection = getattr(self, "current_attention_projection", None)
            self.sync_leds(
                snapshot.aggregate.mode,
                self.last_battery_snapshot,
                self.active_led_display_kind(self.last_battery_snapshot),
                tuple(snapshot.statuses),
                projection=projection,
            )

        def dndWorkspaceWillSleep_(self, notification) -> None:
            _release_effect_studio_preview(self, PreviewReleaseReason.SLEEP)
            return _LegacyStatusBarController.dndWorkspaceWillSleep_(
                self,
                notification,
            )

        def dndScreensDidSleep_(self, notification) -> None:
            _release_effect_studio_preview(self, PreviewReleaseReason.SLEEP)
            return _LegacyStatusBarController.dndScreensDidSleep_(
                self,
                notification,
            )

        def local_health_snapshot(
            self,
            *,
            performance: PerformanceSnapshot | None = None,
        ) -> LocalHealthSnapshot:
            monitor = getattr(self, "_production_local_health_monitor", None)
            if monitor is None:
                monitor = LocalHealthMonitor()
                self._production_local_health_monitor = monitor

            registry = getattr(self, "_runtime_worker_registry", None)
            try:
                workers = tuple(registry.snapshot()) if registry is not None else ()
            except Exception:
                workers = ()

            source_ages: list[float] = []
            statuses = getattr(getattr(self, "last_snapshot", None), "statuses", ())
            for status in statuses:
                age_seconds = getattr(status, "age_seconds", None)
                if not callable(age_seconds):
                    continue
                try:
                    source_ages.append(age_seconds())
                except Exception:
                    continue

            return monitor.observe(
                presentation=DEFAULT_PRESENTATION_METRICS.snapshot(),
                performance=(
                    performance
                    if type(performance) is PerformanceSnapshot
                    else self._performance().snapshot()
                ),
                workers=workers,
                source_ages_seconds=source_ages,
                dnd_projection=(
                    self.current_dnd_projection()
                    if callable(getattr(self, "current_dnd_projection", None))
                    else None
                ),
            )

        def _why_light_context_from_health(self, health: LocalHealthSnapshot):
            screen_bar_active = bool(
                _legacy.SCREEN_BAR_FEATURE_ENABLED
                and getattr(
                    getattr(self, "settings", None),
                    "virtual_status_device_enabled",
                    False,
                )
            )
            physical_surfaces_active = bool(
                getattr(self, "leds_enabled", False)
                and getattr(self, "_device_inventory_candidates", ())
            )
            timing = (
                getattr(health, "screen_bar_renderer_latency", None)
                if screen_bar_active
                else None
            )
            timing_source = (
                OutputTimingSource.SCREEN_BAR_RENDERER
                if timing is not None
                else OutputTimingSource.UNAVAILABLE
            )
            if timing is None and physical_surfaces_active:
                timing = getattr(health, "hardware_write_latency", None)
                if timing is not None:
                    timing_source = OutputTimingSource.PHYSICAL_HARDWARE_WRITE
            return project_current_why_light_context(
                self,
                screen_bar_feature_enabled=_legacy.SCREEN_BAR_FEATURE_ENABLED,
                focus_observation_ttl_seconds=_legacy.BRIGHTNESS_WATCH_SECONDS + 1.0,
                source_age=health.source_freshness_seconds,
                renderer_sample_count=(timing.count if timing is not None else 0),
                renderer_latest_ms=(timing.latest_ms if timing is not None else 0.0),
                renderer_p50_ms=(timing.p50_ms if timing is not None else 0.0),
                renderer_p95_ms=(timing.p95_ms if timing is not None else 0.0),
                renderer_timing_source=timing_source,
            )

        def current_why_light_context(self):
            """Add existing active-renderer timing to the cached explanation."""
            return self._why_light_context_from_health(
                self.local_health_snapshot()
            )

        def performance_diagnostics_text(
            self,
            *,
            health: LocalHealthSnapshot | None = None,
            report: PerformanceSnapshot | None = None,
        ) -> str:
            if type(report) is not PerformanceSnapshot:
                report = self._performance().snapshot()
            if type(health) is not LocalHealthSnapshot:
                health = JRStatusBarController.local_health_snapshot(
                    self,
                    performance=report,
                )
            lines = [
                format_local_health(health),
                "",
                "Detailed timings (current run)",
            ]
            if not report.metrics:
                lines.append("No timing observations in this run.")
                return "\n".join(lines)
            for metric in report.metrics:
                lines.append(
                    f"{metric.name}: P50 {metric.p50_ms:.1f} ms · "
                    f"P95 {metric.p95_ms:.1f} ms · max {metric.maximum_ms:.1f} ms · "
                    f"n={metric.count}"
                )
            return "\n".join(lines)

        def projected_rows_for_device(self, projection, device):
            provider_pin = self.settings.device_provider_pin(device.device_id)
            return light_rows_for_provider(projection, provider_pin)

        def projection_for_device(self, projection, device):
            provider_pin = self.settings.device_provider_pin(device.device_id)
            return projection_for_provider(projection, provider_pin)

        def _battery_service(self) -> BatteryObservationService:
            service = getattr(self, "_production_battery_service", None)
            if service is None:
                service = BatteryObservationService()
                self._production_battery_service = service
            return service

        def read_battery_snapshot(self):
            """Return cached battery state and start a bounded probe if due."""
            observation = self._battery_service().request(
                full_charge_watts=self.settings.battery_full_charge_watts,
                callback=self._battery_observation_ready,
            )
            self._production_battery_observation = observation
            return observation.snapshot

        def _battery_observation_ready(
            self,
            observation: BatteryObservation,
        ) -> None:
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyBatteryObservation:",
                    observation,
                    False,
                )
            except Exception:
                return

        @_legacy.objc.IBAction
        def applyBatteryObservation_(self, observation) -> None:
            if not isinstance(observation, BatteryObservation):
                return
            previous = getattr(self, "_production_battery_observation", None)
            self._production_battery_observation = observation
            if getattr(self, "_runtime_started", False) and observation != previous:
                self.schedule_event_refresh()

        def _transcript_service(self) -> TranscriptFallbackService:
            service = getattr(self, "_production_transcript_service", None)
            if service is None:
                service = TranscriptFallbackService()
                self._production_transcript_service = service
            return service

        def ingest_transcript_fallback(self) -> None:
            """Schedule transcript discovery; never walk or sort files on AppKit."""
            monitor = getattr(self, "transcript_monitor", None)
            if monitor is None:
                return
            self._transcript_service().request(
                monitor,
                known_signature=getattr(
                    self,
                    "transcript_fallback_signature",
                    None,
                ),
                callback=self._transcript_batch_ready,
            )

        def _transcript_batch_ready(self, batch: TranscriptFallbackBatch) -> None:
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyTranscriptFallbackBatch:",
                    batch,
                    False,
                )
            except Exception:
                return

        @_legacy.objc.IBAction
        def applyTranscriptFallbackBatch_(self, batch) -> None:
            if not isinstance(batch, TranscriptFallbackBatch):
                return
            monitor = getattr(self, "transcript_monitor", None)
            if monitor is None or batch.monitor_identity != id(monitor):
                return
            if not batch.accepted:
                _legacy.log_status_bar(
                    f"transcript fallback unavailable: {batch.reason}"
                )
                return
            watermark = getattr(self, "transcript_watermark", None)
            newest = watermark
            accepted = 0
            for record in batch.records:
                logged_at = getattr(record, "logged_at", None)
                if logged_at is None or (
                    watermark is not None and logged_at <= watermark
                ):
                    continue
                try:
                    self.monitor.ingest_record(record)
                except Exception:
                    _legacy.log_status_bar(
                        "transcript fallback record refused: ingest_failed"
                    )
                    continue
                accepted += 1
                if newest is None or logged_at > newest:
                    newest = logged_at
            if accepted:
                self.monitor.statuses_by_key = self.monitor.current_statuses_by_key()
                self.transcript_watermark = newest
            self.transcript_fallback_signature = batch.signature
            if accepted and getattr(self, "_runtime_started", False):
                self.schedule_event_refresh()

        def _intake_service(self) -> IntakeProbeService:
            service = getattr(self, "_production_intake_service", None)
            if service is None:
                service = IntakeProbeService(_legacy.probe_providers)
                self._production_intake_service = service
            return service

        def refresh_intake_report(self, *, force: bool = False):
            """Recompute from cached facts while provider probing runs off-main."""
            now = time.monotonic()
            probes = getattr(self, "_intake_probes", None)
            probed_at = float(getattr(self, "_intake_probed_at", 0.0) or 0.0)
            if force or probes is None or now - probed_at > 30.0:
                self._intake_service().request(self._intake_probe_ready)
                # A synchronously delivered result (tests, warm caches) is
                # usable immediately; a real off-main probe lands later.
                probes = getattr(self, "_intake_probes", None)
            if not probes:
                self.current_intake_report = None
                return None
            # Only a completed probe may renew the freshness stamp; renewing
            # it here would let cached recomputation starve the real probe.
            # The delivery half is recomputed directly so a stale cache never
            # triggers the legacy path's synchronous main-thread probe.
            try:
                report = _legacy.build_intake_report(
                    probes,
                    accepted_by_provider=_legacy.accepted_epochs_by_provider(
                        getattr(self, "current_operator_state", None)
                    ),
                    now_epoch=time.time(),
                )
            except Exception:
                return self.current_intake_report
            self.current_intake_report = report
            return report

        def _intake_probe_ready(self, result: IntakeProbeResult) -> None:
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyIntakeProbeResult:",
                    result,
                    False,
                )
            except Exception:
                return

        @_legacy.objc.IBAction
        def applyIntakeProbeResult_(self, result) -> None:
            if not isinstance(result, IntakeProbeResult):
                return
            self._intake_probed_at = time.monotonic()
            if result.accepted:
                self._intake_probes = result.probes
                _LegacyStatusBarController.refresh_intake_report(
                    self,
                    force=False,
                )
                if getattr(self, "_runtime_started", False):
                    self.schedule_event_refresh()
            else:
                _legacy.log_status_bar(
                    f"intake probe unavailable: {result.reason}"
                )

        def _ledger_publisher(self) -> RemoteLedgerPublisher:
            publisher = getattr(self, "_production_ledger_publisher", None)
            if publisher is None:
                publisher = RemoteLedgerPublisher(_legacy.publish_local_ledger)
                self._production_ledger_publisher = publisher
            return publisher

        def publish_local_ledger_now(self, statuses, *, generated_at=None):
            """Publish the optional peer ledger off-main with latest-wins bounds."""
            remote = self.settings.remote_peers
            if not remote.publish_enabled:
                self._published_ledger_signature = None
                self._ledger_publish_pending_signature = None
                return None
            normalized_statuses = tuple(statuses)
            signature = _legacy.local_ledger_signature(normalized_statuses)
            now = time.monotonic()
            last_at = getattr(self, "_published_ledger_at", None)
            if (
                signature == getattr(self, "_published_ledger_signature", None)
                and last_at is not None
                and now - last_at < _legacy.REMOTE_PUBLISH_HEARTBEAT_SECONDS
            ):
                return getattr(self, "_published_ledger_path", None)
            if signature == getattr(
                self,
                "_ledger_publish_pending_signature",
                None,
            ):
                return getattr(self, "_published_ledger_path", None)
            self._ledger_publish_pending_signature = signature
            self._ledger_publisher().request(
                statuses=normalized_statuses,
                generated_at=generated_at,
                settings=remote,
                signature=signature,
                callback=self._ledger_publish_ready,
            )
            return getattr(self, "_published_ledger_path", None)

        def _ledger_publish_ready(self, result: LedgerPublishResult) -> None:
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyLedgerPublishResult:",
                    result,
                    False,
                )
            except Exception:
                return

        @_legacy.objc.IBAction
        def applyLedgerPublishResult_(self, result) -> None:
            if not isinstance(result, LedgerPublishResult):
                return
            if getattr(self, "_ledger_publish_pending_signature", None) != (
                result.request.signature
            ):
                return
            self._ledger_publish_pending_signature = None
            if not result.accepted:
                _legacy.log_status_bar(
                    f"remote_peers publish error: {result.reason}"
                )
                return
            self._published_ledger_signature = result.request.signature
            self._published_ledger_at = time.monotonic()
            self._published_ledger_path = result.path

        def _capture_hardware_render_colors(self) -> None:
            """Read AppKit-dependent preview state on the main thread only."""
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError("hardware render context must be captured on main")
            try:
                colors = _LegacyStatusBarController.agent_render_colors(self)
            except Exception:
                colors = self.settings.colors
            self._frozen_hardware_render_colors = colors

        def agent_render_colors(self):
            """Worker-safe immutable render input, with no AppKit access."""
            return getattr(
                self,
                "_frozen_hardware_render_colors",
                self.settings.colors,
            )

        def _ambient_bar(self, setter, brightness) -> bool:
            preferences = self._accessibility_display_preferences
            ambient = active_screen_bar_ambient_presentation(
                self,
                reduce_motion=bool(getattr(preferences, "reduce_motion", False)),
                brightness=brightness,
            )
            if ambient is None:
                return False
            presentation = ambient.screen_bar_program
            setter(
                _legacy.apply_brightness(presentation.dsl, brightness),
                presentation,
            )
            self._ambient_accessibility_text = ambient.accessibility_text
            return True

        def sync_leds(self, *args, **kwargs):
            self._capture_hardware_render_colors()
            return _LegacyStatusBarController.sync_leds(self, *args, **kwargs)

        def _sync_hardware_device(self, request):
            started = time.perf_counter()
            outcome = "ok"
            try:
                if request.override_program is not None:
                    controller = self.agent_controller_for_device(request.device)
                    write = controller.sync_program(
                        request.override_program,
                        request.override_state,
                    )
                    return _legacy.HardwareWriteResult(
                        request=request,
                        write=write,
                        label=(
                            f"{request.device.name} Ambient effect"
                            if request.coalesce_identity.startswith("ambient-")
                            else (
                                f"{request.device.name} Effect Studio preview"
                                if request.coalesce_identity
                                == "preview-effect-studio"
                                else f"{request.device.name} Calibration preview"
                            )
                        ),
                        agent_display_rendered=False,
                        completed_at=self._runtime_worker_monotonic(),
                    )
                if request.display_kind == _legacy.LED_DISPLAY_AGENT:
                    controller = self.agent_controller_for_device(request.device)
                    preferences = request.accessibility_preferences
                    ambient = active_hardware_ambient_presentation(
                        self,
                        device_id=request.device.device_id,
                        led_count=_legacy.led_count_for_target(request.device.target),
                        reduce_motion=bool(
                            getattr(preferences, "reduce_motion", False)
                        ),
                        brightness=controller.brightness,
                    )
                    if ambient is not None:
                        write = controller.sync_program(
                            ambient.program,
                            ambient.led_state,
                        )
                        return _legacy.HardwareWriteResult(
                            request=request,
                            write=write,
                            label=f"{request.device.name} Ambient effect",
                            agent_display_rendered=False,
                            completed_at=self._runtime_worker_monotonic(),
                        )
                return _LegacyStatusBarController._sync_hardware_device(
                    self,
                    request,
                )
            except BaseException:
                outcome = "error"
                if request.coalesce_identity == "preview-effect-studio":
                    try:
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "effectStudioPreviewWriteFailed:",
                            {
                                "hardware_device_id": request.device.device_id,
                                "session_id": request.preview_session_id,
                            },
                            False,
                        )
                    except Exception:
                        pass
                raise
            finally:
                self._performance().record(
                    "hardware_render",
                    (time.perf_counter() - started) * 1000.0,
                    outcome=outcome,
                )

        def _apply_hardware_write_result(self, command, result) -> None:
            applied = _LegacyStatusBarController._apply_hardware_write_result(
                self,
                command,
                result,
            )
            if type(result) is not _legacy.HardwareWriteResult:
                return applied
            adapter = getattr(
                self,
                "_effect_studio_physical_preview_adapter",
                None,
            )
            if adapter is None:
                return applied
            preview_session_id = result.request.preview_session_id
            if adapter.handle_write_result(
                result.request,
                error=result.write.error,
            ):
                window = getattr(
                    self,
                    "_effect_studio_window_controller",
                    None,
                )
                callback = getattr(window, "physicalPreviewDidRelease_", None)
                if callable(callback):
                    callback(
                        {
                            "session_id": preview_session_id,
                            "reason": PreviewReleaseReason.ERROR.value,
                        }
                    )
            return applied

        def _send_calibration_test(self) -> None:
            calibration = self.calibration_test
            if calibration is None or calibration[0] == _legacy.VIRTUAL_DEVICE_ID:
                return _LegacyStatusBarController._send_calibration_test(self)
            if not getattr(self, "_hardware_write_active", False):
                return
            device_id, hex_color = calibration
            device = next(
                (
                    entry
                    for entry in self.status_bar_devices(remember=False)
                    if entry.device_id == device_id and entry.connected
                ),
                None,
            )
            if device is None:
                return
            controller = self.agent_controller_for_device(device)
            program = _legacy.apply_brightness(
                f"{hex_color} 500ms\nrepeat",
                controller.brightness,
            )
            snapshot = self.last_snapshot
            request = _legacy.HardwareWriteRequest(
                device=device,
                mode=(
                    snapshot.aggregate.mode
                    if snapshot is not None
                    else _legacy.AgentMode.IDLE_READY
                ),
                battery_snapshot=self.last_battery_snapshot,
                statuses=(snapshot.statuses if snapshot is not None else ()),
                projection=self.current_attention_projection,
                relay_elapsed_seconds=max(
                    0.0,
                    time.monotonic() - self._relay_epoch,
                ),
                accessibility_preferences=self._accessibility_display_preferences,
                display_kind=_legacy.LED_DISPLAY_TEST,
                write_priority=_legacy.RuntimeWorkPriority.EXPLICIT,
                coalesce_identity="preview-calibration",
                override_program=program,
                override_state=_legacy.LedDisplayState.ASK,
            )
            worker_key = self._hardware_worker_key(device)
            prefix = f"{worker_key}:"
            self._hardware_write_worker.discard_pending_prefix(prefix)
            preview_key = hardware_coalesce_key(
                worker_key,
                request.coalesce_identity,
            )
            self._active_calibration_preview_key = preview_key
            now = self._runtime_worker_monotonic()
            self._hardware_write_worker.submit(
                _legacy.RuntimeWorkCommand(
                    domain=_legacy.RuntimeWorkerDomain.HARDWARE_WRITE,
                    key=worker_key,
                    generation=self._hardware_write_generation,
                    deadline=now + 30.0,
                    payload=request,
                    priority=_legacy.RuntimeWorkPriority.EXPLICIT,
                    coalesce_key=preview_key,
                )
            )

        def play_transition_flourish(self, label, animation) -> None:
            if not self.leds_enabled:
                return _LegacyStatusBarController.play_transition_flourish(
                    self,
                    label,
                    animation,
                )
            try:
                _legacy.validate_lid_animation(animation)
            except _legacy.DeviceWriteError:
                return _LegacyStatusBarController.play_transition_flourish(
                    self,
                    label,
                    animation,
                )
            previous_generation = self._hardware_write_generation
            self._hardware_write_generation += 1
            self._hardware_write_worker.cancel_generation(previous_generation)
            return _LegacyStatusBarController.play_transition_flourish(
                self,
                label,
                animation,
            )

        def play_transition_flourish_worker(
            self,
            label,
            animation,
            devices,
            token,
        ) -> None:
            if not self._hardware_write_worker.wait_idle(timeout_seconds=1.0):
                _legacy.log_status_bar(
                    f"animation refused {label}: hardware writer remained active"
                )
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "restoreLedDisplay:",
                    str(token),
                    False,
                )
                return
            return _LegacyStatusBarController.play_transition_flourish_worker(
                self,
                label,
                animation,
                devices,
                token,
            )

        def popoverDidClose_(self, notification):
            calibration = self.calibration_test
            preview_key = getattr(
                self,
                "_active_calibration_preview_key",
                None,
            )
            result = _LegacyStatusBarController.popoverDidClose_(self, notification)
            if calibration is None or calibration[0] == _legacy.VIRTUAL_DEVICE_ID:
                return result
            if type(preview_key) is str:
                self._hardware_write_worker.discard_pending(preview_key)
            self._active_calibration_preview_key = None
            return result

        def _core_state_store(self) -> CoreStateStore:
            store = getattr(self, "_production_core_state", None)
            if store is None:
                store = CoreStateStore()
                self._production_core_state = store
            return store

        def _observe_refresh_state(self) -> StateDelta:
            monitor = getattr(self, "monitor", None)
            attention = getattr(self, "current_attention_projection", None)
            values = {
                CoreDomain.AGENTS: (
                    getattr(monitor, "statuses_by_key", None),
                    getattr(self, "transcript_watermark", None),
                    getattr(self, "last_refresh_hint", None),
                ),
                CoreDomain.OPERATOR: (
                    getattr(self, "canonical_operator_state", None),
                    getattr(self, "activity_ledger", None),
                    getattr(self, "local_triage_state", None),
                ),
                CoreDomain.ATTENTION: (
                    attention,
                    getattr(self, "active_signal", None),
                    getattr(self, "current_calendar_alert", None),
                    getattr(self, "current_reminder_alert", None),
                    getattr(self, "current_weather_alert", None),
                ),
                CoreDomain.BATTERY: getattr(
                    self,
                    "_production_battery_observation",
                    None,
                ),
                CoreDomain.SETTINGS: self.settings,
                CoreDomain.REMOTE: (
                    getattr(self, "_remote_refresh", None),
                    getattr(self, "current_merged_ledger", None),
                ),
                CoreDomain.PRESENTATION: (
                    getattr(self, "current_glance", None),
                    getattr(self, "active_finite_cue", None),
                    getattr(self, "completion_sweep", None),
                ),
                CoreDomain.DEVICES: (
                    self.settings.devices,
                    getattr(self, "connected_devices", None),
                ),
                CoreDomain.USAGE: (
                    getattr(self, "current_capacity_projection", None),
                    getattr(self, "current_usage_view", None),
                ),
            }
            stage_reader = getattr(self, "current_escalation_stage", 0)
            try:
                escalation_stage = (
                    stage_reader()
                    if callable(stage_reader)
                    else int(stage_reader or 0)
                )
            except (TypeError, ValueError):
                escalation_stage = 0
            urgent = bool(
                getattr(attention, "actionable_attention", ())
                or getattr(attention, "transient_signals", ())
                or escalation_stage > 0
            )
            return self._core_state_store().observe(
                values,
                urgent_domains=(
                    frozenset({CoreDomain.ATTENTION}) if urgent else frozenset()
                ),
            )

        def _dynamic_display_requires_refresh(self) -> bool:
            return bool(
                getattr(self, "active_finite_cue", None)
                or getattr(self, "completion_sweep", None)
                or self.settings.led_display
                in {
                    getattr(_legacy, "LED_DISPLAY_TIMER", "timer"),
                    getattr(_legacy, "LED_DISPLAY_BATTERY", "battery"),
                }
            )

        def refresh_why_panel(self) -> bool:
            if getattr(self, "_production_refresh_active", False):
                self._production_why_panel_refresh_pending = True
                return False
            return _LegacyStatusBarController.refresh_why_panel(self)

        @_legacy.objc.IBAction
        def refresh_(self, sender):
            # Any completed refresh satisfies pending event wake-ups, no
            # matter which path invoked it; without this, a dropped
            # refreshFromEvent: dispatch left the pending flag latched.
            self.event_refresh_pending = False
            if getattr(self, "_production_refresh_active", False):
                self._production_refresh_pending = True
                self._performance().record(
                    "refresh_coalesced",
                    0.0,
                    outcome="coalesced",
                )
                return None

            store = self._core_state_store()
            first_observation = not store.snapshot.fingerprints
            delta = self._observe_refresh_state()
            now = time.monotonic()
            heartbeat_due = (
                now - float(getattr(self, "_production_last_full_refresh", 0.0))
                >= FULL_REFRESH_HEARTBEAT_SECONDS
            )
            forced = bool(getattr(self, "_production_force_refresh", False))
            self._production_force_refresh = False
            admission: RefreshAdmission = admit_refresh(
                delta,
                first_observation=first_observation,
                heartbeat_due=heartbeat_due,
                dynamic_display=self._dynamic_display_requires_refresh(),
                forced=forced,
            )
            if not admission.admitted:
                self._performance().record(
                    "refresh_skipped",
                    0.0,
                    outcome=admission.reason,
                )
                return None

            self._production_refresh_active = True
            started = time.perf_counter()
            outcome = "ok"
            try:
                return _LegacyStatusBarController.refresh_(self, sender)
            except BaseException:
                outcome = "error"
                raise
            finally:
                self._performance().record(
                    "refresh",
                    (time.perf_counter() - started) * 1000.0,
                    outcome=outcome,
                )
                self._production_last_full_refresh = time.monotonic()
                self._production_refresh_active = False
                why_panel_pending = bool(
                    getattr(self, "_production_why_panel_refresh_pending", False)
                )
                self._production_why_panel_refresh_pending = False
                try:
                    if why_panel_pending:
                        _LegacyStatusBarController.refresh_why_panel(self)
                    else:
                        self.local_health_snapshot()
                except Exception:
                    pass
                pending = bool(
                    getattr(self, "_production_refresh_pending", False)
                )
                self._production_refresh_pending = False
                if pending and getattr(self, "_runtime_started", False):
                    try:
                        self.performSelectorOnMainThread_withObject_waitUntilDone_(
                            "refresh:",
                            None,
                            False,
                        )
                    except Exception:
                        pass

        @_legacy.objc.IBAction
        def refreshFromEvent_(self, _sender):
            self.event_refresh_pending = False
            now = time.monotonic()
            last = float(getattr(self, "_last_event_refresh_at", 0.0) or 0.0)
            if now - last < EVENT_COALESCE_SECONDS:
                if getattr(self, "_trailing_refresh_timer", None) is None:
                    self._trailing_refresh_timer = (
                        _legacy.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                            EVENT_COALESCE_SECONDS,
                            self,
                            "trailingRefreshFire:",
                            None,
                            False,
                        )
                    )
                return
            self._last_event_refresh_at = now
            self.refresh_(None)

        @_legacy.objc.IBAction
        def trailingRefreshFire_(self, _timer):
            self._trailing_refresh_timer = None
            self._last_event_refresh_at = time.monotonic()
            self.refresh_(None)

        def applicationDidFinishLaunching_(self, notification):
            if (
                getattr(self, "_runtime_started", False)
                or getattr(self, "_runtime_termination_started", False)
            ):
                return None
            started = time.perf_counter()
            outcome = "ok"
            try:
                return _LegacyStatusBarController.applicationDidFinishLaunching_(
                    self,
                    notification,
                )
            except BaseException:
                outcome = "error"
                raise
            finally:
                self._performance().record(
                    "warm_launch",
                    (time.perf_counter() - started) * 1000.0,
                    outcome=outcome,
                )

        def menuWillOpen_(self, menu):
            started = time.perf_counter()
            outcome = "ok"
            try:
                return _LegacyStatusBarController.menuWillOpen_(self, menu)
            except BaseException:
                outcome = "error"
                raise
            finally:
                self._performance().record(
                    "menu_open",
                    (time.perf_counter() - started) * 1000.0,
                    outcome=outcome,
                )

        def update_status_menu(self, snapshot, state) -> None:
            started = time.perf_counter()
            outcome = "ok"
            try:
                return _LegacyStatusBarController.update_status_menu(
                    self,
                    snapshot,
                    state,
                )
            except BaseException:
                outcome = "error"
                raise
            finally:
                self._performance().record(
                    "menu_apply",
                    (time.perf_counter() - started) * 1000.0,
                    outcome=outcome,
                )

        def ensure_settings_pane(self, key: str) -> None:
            started = time.perf_counter()
            outcome = "ok"
            try:
                return _LegacyStatusBarController.ensure_settings_pane(self, key)
            except BaseException:
                outcome = "error"
                raise
            finally:
                self._performance().record(
                    "settings_pane_build",
                    (time.perf_counter() - started) * 1000.0,
                    outcome=outcome,
                )

        def refresh_settings_window(self) -> None:
            started = time.perf_counter()
            outcome = "ok"
            try:
                return _LegacyStatusBarController.refresh_settings_window(self)
            except BaseException:
                outcome = "error"
                raise
            finally:
                self._performance().record(
                    "settings_refresh",
                    (time.perf_counter() - started) * 1000.0,
                    outcome=outcome,
                )

        def why_panel_body(self, *, why_context=None) -> str:
            report = self._performance().snapshot()
            health = self.local_health_snapshot(performance=report)
            context = (
                self._why_light_context_from_health(health)
                if why_context is None
                else why_context
            )
            body = _LegacyStatusBarController.why_panel_body(
                self,
                why_context=context,
            )
            diagnostics = self.performance_diagnostics_text(
                health=health,
                report=report,
            )
            return f"{body}\n\n{diagnostics}"

        @_legacy.objc.IBAction
        def applyEscalationWebhook_(self, sender):
            url = str(sender.stringValue()).strip()
            if url and not url.casefold().startswith("https://"):
                self.set_settings_message(
                    "Webhook delivery requires HTTPS. Local and cleartext URLs are refused."
                )
                return
            self.settings = self.settings.with_escalation_webhook_url(url)
            _legacy.save_settings(self.settings)
            self.set_settings_message(
                "Secure webhook set." if url else "Stage-3 webhook off."
            )

        def _webhook_service(self) -> WebhookDeliveryService:
            service = getattr(self, "_production_webhook_service", None)
            if service is None:
                service = WebhookDeliveryService()
                self._production_webhook_service = service
            return service

        def _webhook_delivery_finished(
            self,
            receipt: WebhookDeliveryReceipt,
        ) -> None:
            event_name = receipt.request.event
            if receipt.result.delivered:
                _legacy.log_status_bar(f"webhook delivered: {event_name}")
            else:
                _legacy.log_status_bar(
                    f"webhook failed ({event_name}): {receipt.result.reason.value}"
                )

        def post_webhook(self, payload_dict: dict) -> None:
            """Queue privacy-minimized JSON on one bounded delivery worker."""
            url = (self.settings.escalation_webhook_url or "").strip()
            if (
                not url
                or not isinstance(payload_dict, dict)
                or not self.webhook_effect_allowed(payload_dict)
            ):
                return
            reason = self._webhook_service().submit(
                url,
                payload_dict,
                callback=self._webhook_delivery_finished,
            )
            if reason is not None:
                event_name = str(
                    payload_dict.get("event") or "sidepulse.event"
                )[:64]
                _legacy.log_status_bar(
                    f"webhook refused ({event_name}): {reason.value}"
                )

        def applicationWillTerminate_(self, notification):
            return _terminate_controller(self, notification)



def install_status_bar_production():
    """Install the production controller layer at the explicit app boundary."""
    _legacy.StatusBarController = JRStatusBarController
    return JRStatusBarController


class _StatusBarFacade(ModuleType):
    """Forward reads and test monkeypatches to the retained runtime module."""

    def __getattr__(self, name: str):
        return getattr(_legacy, name)

    def __setattr__(self, name: str, value) -> None:
        if name in {
            "__all__",
            "__class__",
            "__doc__",
            "__file__",
            "__loader__",
            "__name__",
            "__package__",
            "__path__",
            "__spec__",
        } or name.startswith("_facade_"):
            super().__setattr__(name, value)
            return
        setattr(_legacy, name, value)

    def __delattr__(self, name: str) -> None:
        if name in {"__all__", "__class__"} or name.startswith("_facade_"):
            super().__delattr__(name)
            return
        delattr(_legacy, name)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(dir(_legacy)))


__all__ = (
    *(name for name in dir(_legacy) if not name.startswith("_")),
    "JRStatusBarController",
    "install_status_bar_production",
)
_facade_module = sys.modules[__name__]
_facade_module.__class__ = _StatusBarFacade
_facade_module.__file__ = _legacy.__file__


if __name__ == "__main__":
    raise SystemExit(_legacy.main())
