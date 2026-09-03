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

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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



def _scan_opencode_records(db_path: Path, since_epoch: float) -> list[tuple]:
    if not db_path.is_file():
        return []
    records = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = con.execute(
            "SELECT id, session_id, time_created, data FROM message WHERE data LIKE ? AND time_created >= ?",
            ('%"tokens"%', int(since_epoch * 1000.0) if since_epoch < 1e11 else int(since_epoch)),
        )
        for row in cursor:
            mid, sid, tc, data_str = row
            try:
                data = json.loads(data_str)
                tokens = data.get("tokens") or {}
                inp = int(tokens.get("input", 0) or 0)
                out = int(tokens.get("output", 0) or 0)
                cache_data = tokens.get("cache") or {}
                c_read = int(cache_data.get("read", 0) or 0)
                c_write = int(cache_data.get("write", 0) or 0)
                if (inp + out + c_read + c_write) <= 0:
                    continue
                model = data.get("modelID") or data.get("model") or "opencode"
                epoch = tc / 1000.0 if tc > 1e11 else float(tc)
                records.append(("opencode", sid, model, epoch, inp, c_read, c_write, out, f"opencode:{mid}"))
            except Exception:
                pass
        con.close()
    except Exception:
        pass
    return records


def _scan_t3code_records(db_path: Path, since_epoch: float) -> list[tuple]:
    if not db_path.is_file():
        return []
    records = []
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        if since_epoch > 0:
            iso_since = datetime.fromtimestamp(since_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            cursor = con.execute(
                "SELECT activity_id, thread_id, created_at, payload_json FROM projection_thread_activities WHERE created_at >= ? AND payload_json LIKE ?",
                (iso_since, '%"usage"%'),
            )
        else:
            cursor = con.execute(
                "SELECT activity_id, thread_id, created_at, payload_json FROM projection_thread_activities WHERE payload_json LIKE ?",
                ('%"usage"%',),
            )
        for row in cursor:
            aid, tid, cat, payload_str = row
            try:
                data = json.loads(payload_str)
                usage = data.get("usage")
                if not usage or not isinstance(usage, dict):
                    continue
                total = int(usage.get("total_tokens", 0) or 0)
                inp = int(usage.get("input_tokens", 0) or 0)
                out = int(usage.get("output_tokens", 0) or 0)
                if total <= 0 and inp <= 0 and out <= 0:
                    continue
                if inp == 0 and out == 0 and total > 0:
                    inp = total
                dt = datetime.fromisoformat(cat.replace("Z", "+00:00"))
                epoch = dt.timestamp()
                if epoch < since_epoch:
                    continue
                records.append(("t3code", tid, "t3code", epoch, inp, 0, 0, out, f"t3code:{aid}"))
            except Exception:
                pass
        con.close()
    except Exception:
        pass
    return records


def _scan_antigravity_records(gemini_dir: Path, since_epoch: float) -> list[tuple]:
    summaries_db = gemini_dir / "antigravity-cli" / "conversation_summaries.db"
    if not summaries_db.is_file():
        return []
    records = []
    try:
        con = sqlite3.connect(f"file:{summaries_db}?mode=ro", uri=True)
        if since_epoch > 0:
            iso_since = datetime.fromtimestamp(since_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            cursor = con.execute(
                "SELECT conversation_id, step_count, last_modified_time FROM conversation_summaries WHERE last_modified_time >= ?",
                (iso_since,),
            )
        else:
            cursor = con.execute(
                "SELECT conversation_id, step_count, last_modified_time FROM conversation_summaries"
            )
        for row in cursor:
            cid, steps, lmt = row
            try:
                if not steps or steps <= 0:
                    continue
                dt = datetime.fromisoformat(lmt)
                epoch = dt.timestamp()
                if epoch < since_epoch:
                    continue
                est_tokens = steps * 350
                records.append(("antigravity", cid, "gemini", epoch, est_tokens, 0, 0, 0, f"antigravity:{cid}"))
            except Exception:
                pass
        con.close()
    except Exception:
        pass
    return records

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
    opencode_records = _scan_opencode_records(
        Path.home() / ".local" / "share" / "opencode" / "opencode.db",
        period_start.timestamp(),
    )
    t3code_records = _scan_t3code_records(
        Path.home() / ".t3" / "userdata" / "state.sqlite",
        period_start.timestamp(),
    )
    antigravity_records = _scan_antigravity_records(
        Path.home() / ".gemini",
        period_start.timestamp(),
    )
    totals.records.extend(opencode_records)
    totals.records.extend(t3code_records)
    totals.records.extend(antigravity_records)

    provider_ids = tuple(settings.usage_graph_providers)
    for rec in totals.records:
        rec_provider = rec[0]
        if rec_provider not in provider_ids:
            provider_ids = provider_ids + (rec_provider,)

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
    active_providers = []
    if claude_tokens > 0:
        active_providers.append(f"Claude {usage_stats.compact_token_count(claude_tokens)}")
    if totals.codex_tokens > 0:
        active_providers.append(f"Codex {usage_stats.compact_token_count(totals.codex_tokens)}")
    opencode_tokens = sum(r[4] + r[5] + r[6] + r[7] for r in opencode_records)
    if opencode_tokens > 0:
        active_providers.append(f"OpenCode {usage_stats.compact_token_count(opencode_tokens)}")
    t3_tokens = sum(r[4] + r[5] + r[6] + r[7] for r in t3code_records)
    if t3_tokens > 0:
        active_providers.append(f"T3 {usage_stats.compact_token_count(t3_tokens)}")
    agy_tokens = sum(r[4] + r[5] + r[6] + r[7] for r in antigravity_records)
    if agy_tokens > 0:
        active_providers.append(f"Antigravity {usage_stats.compact_token_count(agy_tokens)}")

    distinct_sessions = len(totals.sessions) + len({r[1] for r in opencode_records}) + len({r[1] for r in t3code_records}) + len({r[1] for r in antigravity_records})
    prov_str = " · ".join(active_providers) if active_providers else "No tokens"
    summary = (
        f"{usage_stats.usage_period_label(days)}: {prov_str} · "
        f"{distinct_sessions} sessions"
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
