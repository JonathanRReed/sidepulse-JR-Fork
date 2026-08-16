"""Stable facade for SidePulse's historical AppKit controller.

The original controller remains the compatibility runtime while production
boundaries are extracted into small, testable modules. The facade preserves
the public ``sidepulse.status_bar`` contract, including test monkeypatches and
source introspection.
"""

from __future__ import annotations

import sys
import threading
import time
from types import ModuleType

from . import status_bar_legacy as _legacy
from .battery_runtime import BatteryObservation, BatteryObservationService
from .core_state import CoreDomain, CoreStateStore, StateDelta
from .device_projection import light_rows_for_provider, projection_for_provider
from .intake_runtime import IntakeProbeResult, IntakeProbeService
from .ledger_runtime import LedgerPublishResult, RemoteLedgerPublisher
from .performance_metrics import PerformanceRegistry, PerformanceSnapshot
from .refresh_admission import RefreshAdmission, admit_refresh
from .transcript_runtime import TranscriptFallbackBatch, TranscriptFallbackService
from .webhook_delivery import (
    WebhookDeliveryReceipt,
    WebhookDeliveryService,
)

EVENT_COALESCE_SECONDS = 0.05
FULL_REFRESH_HEARTBEAT_SECONDS = 1.0

_LegacyStatusBarController = _legacy.StatusBarController
_legacy._AppKitStatusBarController = _LegacyStatusBarController


if _legacy.StatusBarController.__name__ == "JRStatusBarController":
    JRStatusBarController = _legacy.StatusBarController
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

        def performance_diagnostics_text(self) -> str:
            report = self.performance_snapshot()
            if not report.metrics:
                return "Performance\nNo timing observations in this run."
            lines = ["Performance (current run)"]
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
            if not probes:
                self.current_intake_report = None
                return None
            self._intake_probed_at = now
            return _LegacyStatusBarController.refresh_intake_report(
                self,
                force=False,
            )

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

        def sync_leds(self, *args, **kwargs):
            self._capture_hardware_render_colors()
            return _LegacyStatusBarController.sync_leds(self, *args, **kwargs)

        def sync_leds_now(self, *args, **kwargs):
            self._capture_hardware_render_colors()
            return _LegacyStatusBarController.sync_leds_now(self, *args, **kwargs)

        def _sync_hardware_device(self, request):
            started = time.perf_counter()
            outcome = "ok"
            try:
                return _LegacyStatusBarController._sync_hardware_device(
                    self,
                    request,
                )
            except BaseException:
                outcome = "error"
                raise
            finally:
                self._performance().record(
                    "hardware_render",
                    (time.perf_counter() - started) * 1000.0,
                    outcome=outcome,
                )

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
            urgent = bool(
                getattr(attention, "requests", ())
                or getattr(attention, "failures", ())
                or getattr(self, "current_escalation_stage", 0)
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

        @_legacy.objc.IBAction
        def refresh_(self, sender):
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

        def why_panel_body(self) -> str:
            body = _LegacyStatusBarController.why_panel_body(self)
            return f"{body}\n\n{self.performance_diagnostics_text()}"

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
            if not url or not isinstance(payload_dict, dict):
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
            for attribute in (
                "_production_battery_service",
                "_production_transcript_service",
                "_production_intake_service",
                "_production_ledger_publisher",
                "_production_webhook_service",
            ):
                service = getattr(self, attribute, None)
                if service is not None:
                    service.close()
            return _LegacyStatusBarController.applicationWillTerminate_(
                self,
                notification,
            )

    _legacy.StatusBarController = JRStatusBarController


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


__all__ = tuple(name for name in dir(_legacy) if not name.startswith("_"))
_facade_module = sys.modules[__name__]
_facade_module.__class__ = _StatusBarFacade
_facade_module.__file__ = _legacy.__file__


if __name__ == "__main__":
    raise SystemExit(_legacy.main())
