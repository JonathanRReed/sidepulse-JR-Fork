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
from .device_projection import light_rows_for_provider, projection_for_provider
from .performance_metrics import PerformanceRegistry, PerformanceSnapshot
from .transcript_runtime import TranscriptFallbackBatch, TranscriptFallbackService
from .webhook_delivery import (
    WebhookValidationError,
    deliver_webhook,
    validate_webhook_url,
)

_LegacyStatusBarController = _legacy.StatusBarController


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
            if (
                getattr(self, "_runtime_started", False)
                and observation != previous
            ):
                self.refresh_(None)

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

        def _capture_hardware_render_colors(self) -> None:
            """Read AppKit-dependent preview state on the main thread only."""
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

        def post_webhook(self, payload_dict: dict) -> None:
            """Deliver privacy-minimized JSON with no redirects or private targets."""
            url = (self.settings.escalation_webhook_url or "").strip()
            if not url or not isinstance(payload_dict, dict):
                return
            payload = dict(payload_dict)
            event_name = str(payload.get("event") or "sidepulse.event")[:64]

            def _post() -> None:
                try:
                    endpoint = validate_webhook_url(url)
                except WebhookValidationError as exc:
                    _legacy.log_status_bar(
                        f"webhook refused ({event_name}): {exc.reason.value}"
                    )
                    return
                result = deliver_webhook(endpoint, payload)
                if result.delivered:
                    _legacy.log_status_bar(f"webhook delivered: {event_name}")
                else:
                    _legacy.log_status_bar(
                        f"webhook failed ({event_name}): {result.reason.value}"
                    )

            threading.Thread(
                target=_post,
                name="SidePulseWebhookDelivery",
                daemon=True,
            ).start()

        def applicationWillTerminate_(self, notification):
            battery_service = getattr(self, "_production_battery_service", None)
            if battery_service is not None:
                battery_service.close()
            transcript_service = getattr(
                self,
                "_production_transcript_service",
                None,
            )
            if transcript_service is not None:
                transcript_service.close()
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
