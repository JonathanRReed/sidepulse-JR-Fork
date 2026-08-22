"""Build the settings usage chart directly, off-main, on demand.

The chart model used to ride the capacity-refresh acceptance protocol:
built inside the shared refresh worker, delivered only when that batch's
generations all matched, dropped wholesale on a stale generation. The
result was a chart that showed "No activity in this range" for data
that a direct scan finds in seconds -- the acceptance plane exists to
keep capacity READINGS honest, and the local activity chart never
needed it. This worker owns the chart now: scan (incremental, cached),
project, land on the main thread, done.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import usage_percent_history, usage_stats
from .providers import default_state_dir

_IN_FLIGHT_ATTR = "_usage_graph_worker_in_flight"


def build_usage_graph_model(settings) -> dict:
    """The chart model for the CURRENT metric, straight from the sources."""
    days = int(getattr(settings, "usage_graph_days", 7) or 7)
    mode = str(getattr(settings, "usage_display_mode", "tokens") or "tokens")
    if mode == "percent":
        return usage_percent_history.shared_percent_graph_model(
            days=days,
            period_label=usage_stats.usage_period_label(days),
        )
    period_start = (datetime.now() - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    totals = usage_stats.scan_usage(
        Path.home() / ".claude" / "projects",
        default_state_dir() / "usage-scan-cache.json",
        since_epoch=period_start.timestamp(),
        codex_root=Path.home() / ".codex" / "sessions",
    )
    return usage_stats.usage_graph_model(
        totals.records,
        days=days,
        metric=mode,
        provider_ids=settings.usage_graph_providers,
    )


def refresh_usage_graph(target) -> None:
    """Fire-and-forget rebuild; lands on main via NSOperationQueue."""
    if getattr(target, _IN_FLIGHT_ATTR, False):
        return
    setattr(target, _IN_FLIGHT_ATTR, True)

    def _work() -> None:
        model = None
        try:
            model = build_usage_graph_model(target.settings)
        except Exception:
            model = None
        finally:
            def _apply() -> None:
                setattr(target, _IN_FLIGHT_ATTR, False)
                if model is None:
                    return
                target.usage_graph_model = model
                graph = getattr(target, "settings_fields", {}).get(
                    "profile_usage_graph"
                )
                if graph is not None:
                    try:
                        graph.setModel_(model)
                    except Exception:
                        pass

            try:
                from Foundation import NSOperationQueue

                NSOperationQueue.mainQueue().addOperationWithBlock_(_apply)
            except Exception:
                _apply()

    threading.Thread(
        target=_work, name="SidePulseUsageGraph", daemon=True
    ).start()


__all__ = ["build_usage_graph_model", "refresh_usage_graph"]
