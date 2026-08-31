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
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import usage_percent_history, usage_stats
from .providers import default_state_dir

_IN_FLIGHT_ATTR = "_usage_graph_worker_in_flight"
_RESCAN_PENDING_ATTR = "_usage_graph_rescan_pending"
_LAST_BUILD_ATTR = "_usage_graph_last_build"

#: A landed model younger than this for the SAME (days, metric,
#: providers) is served from memory instead of rescanned. Pane
#: switches used to re-pay the full transcript scan (~9s warm, ~30s
#: cold, all of it GIL time the menus feel) for identical inputs.
_MODEL_REUSE_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class _UsageGraphSettingsSnapshot:
    usage_graph_days: int
    usage_display_mode: str
    usage_graph_providers: tuple[str, ...]


def _settings_snapshot(settings) -> _UsageGraphSettingsSnapshot:
    if isinstance(settings, _UsageGraphSettingsSnapshot):
        return settings
    return _UsageGraphSettingsSnapshot(
        usage_graph_days=int(getattr(settings, "usage_graph_days", 7) or 7),
        usage_display_mode=str(
            getattr(settings, "usage_display_mode", "tokens") or "tokens"
        ),
        usage_graph_providers=tuple(
            getattr(settings, "usage_graph_providers", ()) or ()
        ),
    )


def _period_start(days: int, *, now: datetime | None = None) -> datetime:
    moment = datetime.now() if now is None else now
    return (moment - timedelta(days=int(days) - 1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _build_payload(settings) -> tuple[dict, str | None]:
    """(chart model, scan summary line) for the CURRENT metric."""
    settings = _settings_snapshot(settings)
    days = settings.usage_graph_days
    mode = settings.usage_display_mode
    if mode == "percent":
        return (
            usage_percent_history.shared_percent_graph_model(
                days=days,
                period_label=usage_stats.usage_period_label(days),
            ),
            None,
        )
    period_start = _period_start(days)
    totals = usage_stats.scan_usage(
        Path.home() / ".claude" / "projects",
        default_state_dir() / "usage-scan-cache.json",
        since_epoch=period_start.timestamp(),
        codex_root=Path.home() / ".codex" / "sessions",
    )
    provider_ids = tuple(settings.usage_graph_providers)
    extra_sessions: dict[str, dict[str, int]] | None = None
    if mode == "sessions":
        # Sessions is the one metric EVERY watched provider can answer:
        # the hook ledgers record session_start for grok, devin, and
        # friends. Chart whoever has data, not just the transcript two.
        from .session_history import ledger_session_days

        try:
            from .providers import HOOK_PROVIDERS

            registry_ids = tuple(HOOK_PROVIDERS)
        except Exception:
            registry_ids = provider_ids
        try:
            # default_state_dir() already ENDS in agent-monitor; the
            # doubled path looked in .../agent-monitor/agent-monitor and
            # silently emptied the Sessions graph for every hook-ledger
            # provider (2026-08-27 readiness audit).
            extra_sessions = ledger_session_days(
                default_state_dir(),
                since_epoch=period_start.timestamp(),
                provider_ids=registry_ids,
            )
        except Exception:
            extra_sessions = None
        if extra_sessions:
            provider_ids = provider_ids + tuple(
                provider_id
                for provider_id in extra_sessions
                if provider_id not in provider_ids
            )
    model = usage_stats.usage_graph_model(
        totals.records,
        days=days,
        metric=mode,
        provider_ids=provider_ids,
        extra_sessions=extra_sessions,
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
    return _build_payload(_settings_snapshot(settings))[0]


def scanning_placeholder(settings) -> dict:
    settings = _settings_snapshot(settings)
    days = settings.usage_graph_days
    return {
        "days": days,
        "period_label": usage_stats.usage_period_label(days),
        "metric": settings.usage_display_mode,
        "labels": (),
        "series": (),
        "scale_max": 1.0,
        "empty_text": "Scanning local activity…",
    }


def _build_key(settings) -> tuple:
    settings = _settings_snapshot(settings)
    return (
        settings.usage_graph_days,
        settings.usage_display_mode,
        settings.usage_graph_providers,
    )


def _drop_to_utility_qos() -> None:
    """The scan parses six figures of JSONL lines -- pure GIL time. At
    default QoS that thread competes with the main thread head-on and
    the whole app reads as laggy while the chart loads. The Screen Bar
    sampler solved this exact problem already; reuse its helper."""
    try:
        from .screen_bar_pipeline import _drop_current_thread_to_utility_qos

        _drop_current_thread_to_utility_qos()
    except Exception:
        pass


def refresh_usage_graph(
    target,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Fire-and-forget rebuild; lands on main via AppHelper.callAfter."""
    fields = getattr(target, "settings_fields", {}) or {}
    graph = fields.get("profile_usage_graph")
    settings = _settings_snapshot(target.settings)
    key = _build_key(settings)

    # Same inputs, recent result: serve the landed model without paying
    # the scan again (a pane switch rebuilds the view but not the data).
    last = getattr(target, _LAST_BUILD_ATTR, None)
    model = getattr(target, "usage_graph_model", None)
    if (
        model is not None
        and last is not None
        and last[0] == key
        and monotonic() - last[1] < _MODEL_REUSE_SECONDS
    ):
        if graph is not None:
            try:
                graph.setModel_(model)
            except Exception:
                pass
        return

    # Feedback FIRST, gating second: whatever else happens, the person
    # who just picked "Year" must see SCANNING, never a stale range and
    # never "No activity in this range". The old gate skipped this
    # whenever any model had ever landed, so a range change showed the
    # previous range's chart (or an empty one) for the whole scan.
    if graph is not None and (
        model is None
        or model.get("days") != key[0]
        or model.get("metric") != key[1]
    ):
        try:
            graph.setModel_(scanning_placeholder(settings))
        except Exception:
            pass

    if getattr(target, _IN_FLIGHT_ATTR, False):
        # A scan is running for OLDER settings. Dropping this request
        # silently was how a mid-scan range change landed the wrong
        # chart; remember it and re-fire when the current scan lands.
        setattr(target, _RESCAN_PENDING_ATTR, True)
        return
    setattr(target, _IN_FLIGHT_ATTR, True)

    def _work() -> None:
        _drop_to_utility_qos()
        model = None
        summary = None
        # Build from the same immutable scalar snapshot used for admission.
        # A settings update during the scan marks a pending refresh, which
        # captures a new snapshot after this one lands.
        built_key = key
        try:
            model, summary = _build_payload(settings)
        except Exception:
            model = None
        finally:
            def _apply() -> None:
                setattr(target, _IN_FLIGHT_ATTR, False)
                pending = bool(getattr(target, _RESCAN_PENDING_ATTR, False))
                setattr(target, _RESCAN_PENDING_ATTR, False)
                if model is not None:
                    target.usage_graph_model = model
                    target._usage_local_scan_complete = True
                    setattr(target, _LAST_BUILD_ATTR, (built_key, monotonic()))
                    fields = getattr(target, "settings_fields", {}) or {}
                    view = fields.get("profile_usage_graph")
                    if view is not None:
                        try:
                            view.setModel_(model)
                        except Exception:
                            pass
                    # The old acceptance-gated path could leave "Loading
                    # local usage history…" forever; scan-derived truth
                    # resolves it whenever that path has said nothing.
                    if summary and not getattr(target, "usage_summary_text", None):
                        label = fields.get("profile_usage_label")
                        if label is not None:
                            try:
                                label.setStringValue_(summary)
                            except Exception:
                                pass
                if pending:
                    refresh_usage_graph(target, monotonic=monotonic)

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


__all__ = [
    "build_usage_graph_model",
    "refresh_usage_graph",
    "scanning_placeholder",
]
