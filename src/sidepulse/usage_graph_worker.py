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


def _build_payload(settings) -> tuple[dict, str | None]:
    """(chart model, scan summary line) for the CURRENT metric."""
    days = int(getattr(settings, "usage_graph_days", 7) or 7)
    mode = str(getattr(settings, "usage_display_mode", "tokens") or "tokens")
    if mode == "percent":
        return (
            usage_percent_history.shared_percent_graph_model(
                days=days,
                period_label=usage_stats.usage_period_label(days),
            ),
            None,
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
    model = usage_stats.usage_graph_model(
        totals.records,
        days=days,
        metric=mode,
        provider_ids=settings.usage_graph_providers,
    )
    claude_tokens = (
        totals.input_tokens + totals.cached_input_tokens + totals.output_tokens
    )
    summary = (
        f"{usage_stats.usage_period_label(days)}: Claude "
        f"{usage_stats.compact_token_count(claude_tokens)} tokens · Codex "
        f"{usage_stats.compact_token_count(totals.codex_tokens)} tokens · "
        f"{len(totals.sessions)} sessions"
    )
    return model, summary


def build_usage_graph_model(settings) -> dict:
    """The chart model for the CURRENT metric, straight from the sources."""
    return _build_payload(settings)[0]


def _scanning_placeholder(settings) -> dict:
    days = int(getattr(settings, "usage_graph_days", 7) or 7)
    return {
        "days": days,
        "period_label": usage_stats.usage_period_label(days),
        "metric": str(getattr(settings, "usage_display_mode", "tokens")),
        "labels": (),
        "series": (),
        "scale_max": 1.0,
        "empty_text": "Scanning local activity…",
    }


def refresh_usage_graph(target) -> None:
    """Fire-and-forget rebuild; lands on main via NSOperationQueue."""
    if getattr(target, _IN_FLIGHT_ATTR, False):
        return
    setattr(target, _IN_FLIGHT_ATTR, True)

    # A year-deep cold scan takes tens of seconds; until it lands the
    # chart must say SCANNING, never "No activity in this range".
    graph = getattr(target, "settings_fields", {}).get("profile_usage_graph")
    if graph is not None and getattr(target, "usage_graph_model", None) is None:
        try:
            graph.setModel_(_scanning_placeholder(target.settings))
        except Exception:
            pass

    def _work() -> None:
        model = None
        summary = None
        try:
            model, summary = _build_payload(target.settings)
        except Exception:
            model = None
        finally:
            def _apply() -> None:
                setattr(target, _IN_FLIGHT_ATTR, False)
                if model is None:
                    return
                target.usage_graph_model = model
                target._usage_local_scan_complete = True
                fields = getattr(target, "settings_fields", {}) or {}
                view = fields.get("profile_usage_graph")
                if view is not None:
                    try:
                        view.setModel_(model)
                    except Exception:
                        pass
                # The old acceptance-gated path could leave "Loading local
                # usage history…" forever; scan-derived truth resolves it
                # whenever that path has said nothing.
                if summary and not getattr(target, "usage_summary_text", None):
                    label = fields.get("profile_usage_label")
                    if label is not None:
                        try:
                            label.setStringValue_(summary)
                        except Exception:
                            pass

            try:
                # AppHelper.callAfter is the PyObjC-blessed main-thread
                # dispatch; NSOperationQueue.addOperationWithBlock_ accepted
                # the Python callable but the block NEVER FIRED in-app --
                # the stuck "Loading local usage history…" card was this.
                from PyObjCTools import AppHelper

                AppHelper.callAfter(_apply)
            except Exception:
                _apply()

    threading.Thread(
        target=_work, name="SidePulseUsageGraph", daemon=True
    ).start()


__all__ = ["build_usage_graph_model", "refresh_usage_graph"]
