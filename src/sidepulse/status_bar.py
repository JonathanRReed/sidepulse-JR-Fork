"""Final production facade for SidePulse's retained AppKit controller.

The previous production boundary remains in ``_status_bar_production``. This
last wrapper contains only corrections discovered during the final review, so
the historical runtime and its compatibility contract remain unchanged.
"""

from __future__ import annotations

import sys
import time
from types import ModuleType

from . import _status_bar_production as _production

_legacy = _production._legacy
_ProductionStatusBarController = _legacy.StatusBarController


if _ProductionStatusBarController.__name__ == "JRFinalStatusBarController":
    JRFinalStatusBarController = _ProductionStatusBarController
else:

    class JRFinalStatusBarController(_ProductionStatusBarController):
        """Corrections over the reviewed production-boundary controller."""

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
            """Observe state using the actual escalation stage, not its method."""
            monitor = getattr(self, "monitor", None)
            attention = getattr(self, "current_attention_projection", None)
            values = {
                _production.CoreDomain.AGENTS: (
                    getattr(monitor, "statuses_by_key", None),
                    getattr(self, "transcript_watermark", None),
                    getattr(self, "last_refresh_hint", None),
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

    _legacy.StatusBarController = JRFinalStatusBarController


# Keep the historical public class name available to tests and callers that
# used the first production facade directly.
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
