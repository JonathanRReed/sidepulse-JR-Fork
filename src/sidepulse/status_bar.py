"""Stable facade for SidePulse's historical AppKit controller.

The original controller remains the compatibility runtime while production
boundaries are extracted into small, testable modules. The facade preserves
the public ``sidepulse.status_bar`` contract, including test monkeypatches and
source introspection.
"""

from __future__ import annotations

import sys
import threading
from types import ModuleType

from . import status_bar_legacy as _legacy
from .battery_runtime import BatteryObservation, BatteryObservationService
from .device_projection import light_rows_for_provider, projection_for_provider
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
            service = getattr(self, "_production_battery_service", None)
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
