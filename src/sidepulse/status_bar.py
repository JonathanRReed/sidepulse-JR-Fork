"""Final production facade for SidePulse's retained AppKit controller.

The previous production boundary remains in ``_status_bar_production``. This
wrapper contains final review corrections and optional external integrations
without widening the retained controller.
"""

from __future__ import annotations

import sys
import time
from types import ModuleType

from . import _status_bar_production as _production
from .codexbar_compat import CodexBarObservation, CodexBarSnapshotService
from .integration_settings import (
    LoadedIntegrationSettings,
    load_integration_settings,
)
from .t3_compat import T3Observation, T3SnapshotService

_legacy = _production._legacy
_ProductionStatusBarController = _legacy.StatusBarController


if _ProductionStatusBarController.__name__ == "JRFinalStatusBarController":
    JRFinalStatusBarController = _ProductionStatusBarController
else:

    class JRFinalStatusBarController(_ProductionStatusBarController):
        """Corrections and read-only integration adapters over the host."""

        def refresh_intake_report(self, *, force: bool = False):
            """Use the probe completion time as the cache age.

            A UI refresh must not make an old provider probe look newly
            observed. Only ``applyIntakeProbeResult_`` advances
            ``_intake_probed_at``.
            """
            now = time.monotonic()
            probes = getattr(self, "_intake_probes", None)
            probed_at = float(getattr(self, "_intake_probed_at", 0.0) or 0.0)
            if force or probes is None or now - probed_at > 30.0:
                self._intake_service().request(self._intake_probe_ready)
            if not probes:
                self.current_intake_report = None
                return None
            return _production._LegacyStatusBarController.refresh_intake_report(
                self,
                force=False,
            )

        def _observe_refresh_state(self):
            """Observe state using actual escalation and integration values."""
            monitor = getattr(self, "monitor", None)
            attention = getattr(self, "current_attention_projection", None)
            values = {
                _production.CoreDomain.AGENTS: (
                    getattr(monitor, "statuses_by_key", None),
                    getattr(self, "transcript_watermark", None),
                    getattr(self, "last_refresh_hint", None),
                    getattr(self, "_sidepulse_t3_observation", None),
                ),
                _production.CoreDomain.OPERATOR: (
                    getattr(self, "canonical_operator_state", None),
                    getattr(self, "activity_ledger", None),
                    getattr(self, "local_triage_state", None),
                ),
                _production.CoreDomain.ATTENTION: (
                    attention,
                    getattr(self, "active_signal", None),
                    getattr(self, "current_calendar_alert", None),
                    getattr(self, "current_reminder_alert", None),
                    getattr(self, "current_weather_alert", None),
                ),
                _production.CoreDomain.BATTERY: getattr(
                    self,
                    "_production_battery_observation",
                    None,
                ),
                _production.CoreDomain.SETTINGS: self.settings,
                _production.CoreDomain.REMOTE: (
                    getattr(self, "_remote_refresh", None),
                    getattr(self, "current_merged_ledger", None),
                ),
                _production.CoreDomain.PRESENTATION: (
                    getattr(self, "current_glance", None),
                    getattr(self, "active_finite_cue", None),
                    getattr(self, "completion_sweep", None),
                ),
                _production.CoreDomain.DEVICES: (
                    self.settings.devices,
                    getattr(self, "connected_devices", None),
                ),
                _production.CoreDomain.USAGE: (
                    getattr(self, "current_capacity_projection", None),
                    getattr(self, "current_usage_view", None),
                    getattr(self, "_sidepulse_codexbar_observation", None),
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
                getattr(attention, "requests", ())
                or getattr(attention, "failures", ())
                or escalation_stage > 0
            )
            return self._core_state_store().observe(
                values,
                urgent_domains=(
                    frozenset({_production.CoreDomain.ATTENTION})
                    if urgent
                    else frozenset()
                ),
            )

        def _integration_configuration(self) -> LoadedIntegrationSettings:
            loaded = getattr(self, "_sidepulse_integration_configuration", None)
            if type(loaded) is not LoadedIntegrationSettings:
                raise RuntimeError("integration configuration was not loaded")
            return loaded

        def _request_external_integrations(self, *, force: bool = False) -> None:
            loaded = getattr(self, "_sidepulse_integration_configuration", None)
            if type(loaded) is not LoadedIntegrationSettings:
                return
            settings = loaded.settings
            if settings.t3code_enabled:
                service = getattr(self, "_sidepulse_t3_service", None)
                if service is None:
                    service = T3SnapshotService(
                        base_dir=settings.t3code_base_dir,
                        environment_id=settings.t3code_environment_id,
                    )
                    self._sidepulse_t3_service = service
                observation = service.request(
                    self._t3_observation_ready,
                    force=force,
                )
                self._sidepulse_t3_observation = observation
            else:
                service = getattr(self, "_sidepulse_t3_service", None)
                if service is not None:
                    service.close()
                    self._sidepulse_t3_service = None
                monitor = getattr(self, "monitor", None)
                if hasattr(monitor, "replace_external_statuses"):
                    monitor.replace_external_statuses("t3code", ())

            if settings.codexbar_enabled:
                service = getattr(self, "_sidepulse_codexbar_service", None)
                if service is None:
                    service = CodexBarSnapshotService(
                        identity=settings.codexbar_identity,
                        connection_mode=settings.codexbar_connection_mode,
                    )
                    self._sidepulse_codexbar_service = service
                observation = service.request(
                    self._codexbar_observation_ready,
                    force=force,
                )
                self._sidepulse_codexbar_observation = observation
            else:
                service = getattr(self, "_sidepulse_codexbar_service", None)
                if service is not None:
                    service.close()
                    self._sidepulse_codexbar_service = None

        def _t3_observation_ready(self, observation: T3Observation) -> None:
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyT3Observation:",
                    observation,
                    False,
                )
            except Exception:
                return

        @_legacy.objc.IBAction
        def applyT3Observation_(self, observation) -> None:
            if type(observation) is not T3Observation:
                return
            previous = getattr(self, "_sidepulse_t3_observation", None)
            self._sidepulse_t3_observation = observation
            monitor = getattr(self, "monitor", None)
            if hasattr(monitor, "replace_external_statuses"):
                monitor.replace_external_statuses(
                    "t3code",
                    observation.statuses,
                )
                if hasattr(monitor, "statuses_by_key"):
                    monitor.statuses_by_key = monitor.current_statuses_by_key()
            if previous != observation and getattr(self, "_runtime_started", False):
                self.schedule_event_refresh()

        def _codexbar_observation_ready(
            self,
            observation: CodexBarObservation,
        ) -> None:
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyCodexBarObservation:",
                    observation,
                    False,
                )
            except Exception:
                return

        @_legacy.objc.IBAction
        def applyCodexBarObservation_(self, observation) -> None:
            if type(observation) is not CodexBarObservation:
                return
            previous = getattr(self, "_sidepulse_codexbar_observation", None)
            self._sidepulse_codexbar_observation = observation
            if previous != observation and getattr(self, "_runtime_started", False):
                self.schedule_event_refresh()

        def integration_diagnostics_text(self) -> str:
            loaded = getattr(self, "_sidepulse_integration_configuration", None)
            if type(loaded) is not LoadedIntegrationSettings:
                return "Integrations\nConfiguration unavailable."
            settings = loaded.settings
            lines = ["Integrations"]
            if loaded.compatibility.read_only:
                lines.append("Configuration: read-only newer or invalid schema")

            t3 = getattr(self, "_sidepulse_t3_observation", None)
            if not settings.t3code_enabled:
                lines.append("T3 Code: off")
            elif type(t3) is not T3Observation or t3.snapshot is None:
                reason = getattr(t3, "reason", None) or "starting"
                lines.append(f"T3 Code: unavailable ({reason})")
            else:
                snapshot = t3.snapshot
                state = "stale" if t3.reason else "healthy"
                lines.append(
                    "T3 Code: "
                    f"{state} · {len(snapshot.threads)} threads · "
                    f"{snapshot.needs_user_count} need you"
                )
                lines.append(
                    "  schema: "
                    f"{snapshot.schema_fingerprint[:24]} · "
                    f"SQLite {snapshot.sqlite_user_version}"
                )

            codexbar = getattr(self, "_sidepulse_codexbar_observation", None)
            if not settings.codexbar_enabled:
                lines.append("CodexBar: off")
            elif type(codexbar) is not CodexBarObservation or codexbar.snapshot is None:
                reason = getattr(codexbar, "reason", None) or "starting"
                lines.append(f"CodexBar: unavailable ({reason})")
            else:
                snapshot = codexbar.snapshot
                stale = "stale" if snapshot.stale or codexbar.reason else "healthy"
                lines.append(
                    "CodexBar: "
                    f"{stale} · {snapshot.connection_mode} · "
                    f"v{snapshot.codexbar_version or 'unknown'} · "
                    f"{len(snapshot.providers)} providers · "
                    f"{snapshot.error_count} errors"
                )
                constrained = snapshot.most_constrained
                if constrained is not None:
                    lines.append(
                        "  tightest: "
                        f"{constrained[0].name} "
                        f"{constrained[1]:.0f}% remaining"
                    )
            return "\n".join(lines)

        def applicationDidFinishLaunching_(self, notification):
            self._sidepulse_integration_configuration = (
                load_integration_settings()
            )
            result = _ProductionStatusBarController.applicationDidFinishLaunching_(
                self,
                notification,
            )
            self._request_external_integrations(force=True)
            return result

        @_legacy.objc.IBAction
        def refresh_(self, sender):
            self._request_external_integrations()
            return _ProductionStatusBarController.refresh_(self, sender)

        def why_panel_body(self) -> str:
            body = _ProductionStatusBarController.why_panel_body(self)
            return f"{body}\n\n{self.integration_diagnostics_text()}"

        def applicationWillTerminate_(self, notification):
            for attribute in (
                "_sidepulse_t3_service",
                "_sidepulse_codexbar_service",
            ):
                service = getattr(self, attribute, None)
                if service is not None:
                    service.close()
            return _ProductionStatusBarController.applicationWillTerminate_(
                self,
                notification,
            )

    _legacy.StatusBarController = JRFinalStatusBarController


JRStatusBarController = JRFinalStatusBarController


class _StatusBarFacade(ModuleType):
    """Forward reads and monkeypatches to the retained runtime module."""

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
