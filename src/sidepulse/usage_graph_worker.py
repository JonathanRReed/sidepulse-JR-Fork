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
from .t3_compat import T3ReadOnlyPolicy, _open_read_only, t3_database_path
from .usage_heatmap import build_usage_heatmap

_IN_FLIGHT_ATTR = "_usage_graph_worker_in_flight"
_RESCAN_PENDING_ATTR = "_usage_graph_rescan_pending"
_LAST_BUILD_ATTR = "_usage_graph_last_build"

#: A landed model younger than this for the SAME (days, metric,
#: providers) is served from memory instead of rescanned. Pane
#: switches used to re-pay the full transcript scan (~9s warm, ~30s
#: cold, all of it GIL time the menus feel) for identical inputs.
_MODEL_REUSE_SECONDS = 60.0
T3_MAX_ACTIVITY_RECORDS = 10_000
T3_MAX_ACTIVITY_PAYLOAD_BYTES = 64 * 1024
T3_MAX_ACTIVITY_TOTAL_BYTES = 4 * 1024 * 1024
T3_MAX_ACTIVITY_IDENTIFIER_BYTES = 1_024
T3_MAX_ACTIVITY_TIMESTAMP_BYTES = 128
T3_SQLITE_MAX_VALUE_BYTES = 128 * 1024
T3_MAX_ACTIVITY_QUERY_STEPS = 2_000_000
T3_ACTIVITY_QUERY_PROGRESS_INTERVAL = 1_000
T3_ACTIVITY_QUERY_TIMEOUT_SECONDS = 0.5
API_EQUIVALENT_COST_DISCLOSURE = "API-equivalent estimate, not subscription spend"


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


def _scan_t3code_records(
    db_path: Path,
    since_epoch: float,
    *,
    maximum_records: int = T3_MAX_ACTIVITY_RECORDS,
    maximum_payload_bytes: int = T3_MAX_ACTIVITY_PAYLOAD_BYTES,
    maximum_total_payload_bytes: int = T3_MAX_ACTIVITY_TOTAL_BYTES,
    maximum_query_steps: int = T3_MAX_ACTIVITY_QUERY_STEPS,
    query_timeout_seconds: float = T3_ACTIVITY_QUERY_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    coverage_reporter: Callable[[str], None] | None = None,
) -> list[tuple]:
    if not db_path.is_file():
        if coverage_reporter is not None:
            coverage_reporter("missing")
        return []
    records = []
    con = None
    coverage_status = "complete"
    try:
        con = _open_read_only(db_path)
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, T3_SQLITE_MAX_VALUE_BYTES)
        limit = min(T3_MAX_ACTIVITY_RECORDS, max(1, int(maximum_records)))
        payload_limit = min(T3_MAX_ACTIVITY_PAYLOAD_BYTES, max(1, int(maximum_payload_bytes)))
        total_limit = min(T3_MAX_ACTIVITY_TOTAL_BYTES, max(1, int(maximum_total_payload_bytes)))
        query_step_limit = min(
            T3_MAX_ACTIVITY_QUERY_STEPS,
            max(T3_ACTIVITY_QUERY_PROGRESS_INTERVAL, int(maximum_query_steps)),
        )
        deadline = monotonic() + min(
            T3_ACTIVITY_QUERY_TIMEOUT_SECONDS,
            max(0.001, float(query_timeout_seconds)),
        )
        progress_calls = 0

        def query_budget_exhausted() -> int:
            nonlocal progress_calls
            progress_calls += 1
            return int(
                progress_calls * T3_ACTIVITY_QUERY_PROGRESS_INTERVAL > query_step_limit
                or monotonic() >= deadline
            )

        con.set_progress_handler(query_budget_exhausted, T3_ACTIVITY_QUERY_PROGRESS_INTERVAL)
        iso_since = (
            datetime.fromtimestamp(since_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            if since_epoch > 0
            else ""
        )
        range_clause = " AND created_at >= ?" if since_epoch > 0 else ""
        range_values = (iso_since,) if since_epoch > 0 else ()
        oversized = con.execute(
            "SELECT 1 FROM projection_thread_activities "
            f"WHERE kind = ?{range_clause} AND ("
            "length(CAST(activity_id AS BLOB)) > ? OR "
            "length(CAST(thread_id AS BLOB)) > ? OR "
            "length(CAST(created_at AS BLOB)) > ? OR "
            "length(CAST(payload_json AS BLOB)) > ?) LIMIT 1",
            (
                "task.completed",
                *range_values,
                T3_MAX_ACTIVITY_IDENTIFIER_BYTES,
                T3_MAX_ACTIVITY_IDENTIFIER_BYTES,
                T3_MAX_ACTIVITY_TIMESTAMP_BYTES,
                payload_limit,
            ),
        ).fetchone()
        if oversized is not None:
            coverage_status = "partial"
        if since_epoch > 0:
            cursor = con.execute(
                "SELECT rowid, activity_id, thread_id, created_at "
                "FROM projection_thread_activities "
                "WHERE kind = ? AND created_at >= ? "
                "AND length(CAST(activity_id AS BLOB)) <= ? "
                "AND length(CAST(thread_id AS BLOB)) <= ? "
                "AND length(CAST(created_at AS BLOB)) <= ? "
                "AND length(CAST(payload_json AS BLOB)) <= ? AND payload_json LIKE ? "
                "ORDER BY created_at DESC, activity_id DESC LIMIT ?",
                (
                    "task.completed",
                    iso_since,
                    T3_MAX_ACTIVITY_IDENTIFIER_BYTES,
                    T3_MAX_ACTIVITY_IDENTIFIER_BYTES,
                    T3_MAX_ACTIVITY_TIMESTAMP_BYTES,
                    payload_limit,
                    '%"usage"%',
                    limit + 1,
                ),
            )
        else:
            cursor = con.execute(
                "SELECT rowid, activity_id, thread_id, created_at "
                "FROM projection_thread_activities "
                "WHERE kind = ? "
                "AND length(CAST(activity_id AS BLOB)) <= ? "
                "AND length(CAST(thread_id AS BLOB)) <= ? "
                "AND length(CAST(created_at AS BLOB)) <= ? "
                "AND length(CAST(payload_json AS BLOB)) <= ? AND payload_json LIKE ? "
                "ORDER BY created_at DESC, activity_id DESC LIMIT ?",
                (
                    "task.completed",
                    T3_MAX_ACTIVITY_IDENTIFIER_BYTES,
                    T3_MAX_ACTIVITY_IDENTIFIER_BYTES,
                    T3_MAX_ACTIVITY_TIMESTAMP_BYTES,
                    payload_limit,
                    '%"usage"%',
                    limit + 1,
                ),
            )
        payload_bytes_read = 0
        for index, row in enumerate(cursor):
            if index >= limit:
                coverage_status = "partial"
                break
            rowid, aid, tid, cat = row
            payload_row = con.execute(
                "SELECT payload_json, length(CAST(payload_json AS BLOB)) "
                "FROM projection_thread_activities "
                "WHERE rowid = ? AND length(CAST(payload_json AS BLOB)) <= ?",
                (rowid, payload_limit),
            ).fetchone()
            if payload_row is None:
                coverage_status = "partial"
                continue
            payload_str, payload_bytes = payload_row
            payload_bytes_read += int(payload_bytes or 0)
            if payload_bytes_read > total_limit:
                coverage_status = "partial"
                break
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
                coverage_status = "partial"
    except sqlite3.DataError:
        coverage_status = "partial"
    except sqlite3.OperationalError as error:
        coverage_status = "partial" if "interrupted" in str(error).lower() else "failed"
    except Exception:
        coverage_status = "partial" if records else "failed"
    finally:
        if con is not None:
            con.close()
        if coverage_reporter is not None:
            coverage_reporter(coverage_status)
    return records


def scan_t3_activity_statistics(
    policy: T3ReadOnlyPolicy,
    since_epoch: float,
    *,
    path_resolver: Callable[[Path | str | None], Path] = t3_database_path,
    scanner: Callable[[Path, float], list[tuple]] | None = None,
    coverage_reporter: Callable[[str], None] | None = None,
) -> list[tuple]:
    """Read optional T3 activity only after both admission checks pass."""
    if not policy.may_scan_activity_statistics:
        return []
    database = path_resolver(policy.base_dir)
    if scanner is not None:
        records = scanner(database, since_epoch)
        if coverage_reporter is not None:
            coverage_reporter("complete")
        return records
    return _scan_t3code_records(
        database,
        since_epoch,
        coverage_reporter=coverage_reporter,
    )


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
                # A step count proves activity, not tokens or API-equivalent
                # spend. These rows are used only for session-day counts.
                records.append(("antigravity", cid, "gemini", epoch, 0, 0, 0, 0, f"antigravity:{cid}"))
            except Exception:
                pass
        con.close()
    except Exception:
        pass
    return records

def _build_payload(
    settings,
    *,
    t3_policy: T3ReadOnlyPolicy | None = None,
) -> tuple[dict, str | None]:
    """(chart model, scan summary line) for the CURRENT metric."""
    settings = _settings_snapshot(settings)
    days = settings.usage_graph_days
    mode = settings.usage_display_mode
    provider_ids = tuple(settings.usage_graph_providers)
    period_start = _period_start(days)
    totals = usage_stats.scan_usage(
        Path.home() / ".claude" / "projects",
        default_state_dir() / "usage-scan-cache.json",
        since_epoch=period_start.timestamp(),
        codex_root=Path.home() / ".codex" / "sessions",
        provider_ids=provider_ids,
    )
    opencode_records = (
        _scan_opencode_records(
            Path.home() / ".local" / "share" / "opencode" / "opencode.db",
            period_start.timestamp(),
        )
        if "opencode" in provider_ids
        else []
    )
    t3_coverage: list[str] = []
    t3code_records = (
        scan_t3_activity_statistics(
            t3_policy,
            period_start.timestamp(),
            coverage_reporter=t3_coverage.append,
        )
        if t3_policy is not None and t3_policy.may_scan_activity_statistics
        else []
    )
    totals.records.extend(opencode_records)
    totals.records.extend(t3code_records)

    # T3 statistics have their own explicit opt-in, separate from the provider
    # switches. Merely discovering another source must not re-enable its line.
    if t3_coverage and "t3code" not in provider_ids:
        provider_ids = (*provider_ids, "t3code")
    records = [record for record in totals.records if record[0] in provider_ids]

    extra_sessions: dict[str, dict[str, int]] | None = None
    if mode == "sessions":
        # Hook ledgers can supply daily session counts without token records.
        from .session_history import ledger_session_days

        try:
            # default_state_dir() already ENDS in agent-monitor; the
            # doubled path looked in .../agent-monitor/agent-monitor and
            # silently emptied the Sessions graph for every hook-ledger
            # provider (2026-08-27 readiness audit).
            extra_sessions = ledger_session_days(
                default_state_dir(),
                since_epoch=period_start.timestamp(),
                provider_ids=provider_ids,
            )
        except Exception:
            extra_sessions = {}
        if "antigravity" in provider_ids:
            activity_days: dict[str, set[str]] = {}
            for record in _scan_antigravity_records(Path.home() / ".gemini", period_start.timestamp()):
                day = datetime.fromtimestamp(record[3]).date().isoformat()
                activity_days.setdefault(day, set()).add(record[1])
            counts = extra_sessions.setdefault("antigravity", {})
            for day, sessions in activity_days.items():
                counts[day] = max(counts.get(day, 0), len(sessions))
    if mode == "percent":
        model = usage_percent_history.shared_percent_graph_model(
            days=days,
            period_label=usage_stats.usage_period_label(days),
        )
        model = {
            **model,
            "series": tuple(series for series in model["series"] if series["provider_id"] in provider_ids),
            "heatmap": build_usage_heatmap(records, days=days, provider_ids=provider_ids),
        }
    else:
        model = usage_stats.usage_graph_model(
            records,
            days=days,
            metric=mode,
            provider_ids=provider_ids,
            extra_sessions=extra_sessions,
        )
    heatmap = model["heatmap"]
    if mode == "cost":
        model = {**model, "cost_semantics": "api_equivalent_estimate"}
    partial_provider_ids = tuple(
        provider_id
        for provider_id in provider_ids
        if provider_id == "t3code"
        and t3_coverage
        and t3_coverage[-1] != "complete"
    )
    model = {**model, "partial_provider_ids": partial_provider_ids}
    from .provider_usage_platform import provider_descriptors

    labels = {descriptor.provider_id: descriptor.label for descriptor in provider_descriptors()}
    labels["t3code"] = "T3 Code"
    if mode == "sessions":
        parts = [
            f"{labels.get(series['provider_id'], series['provider_id'])} "
            f"{int(sum(series['values']))} session-days"
            for series in model["series"]
        ]
        detail = " · ".join(parts) or "No recorded sessions"
    elif mode == "percent":
        detail = "Remaining quota for " + ", ".join(labels.get(provider, provider) for provider in provider_ids)
    else:
        parts = [
            f"{labels.get(provider, provider)} {usage_stats.compact_token_count(value.totals.tokens)}"
            for provider, value in heatmap.providers.items() if value.totals.tokens > 0
        ]
        detail = (" · ".join(parts) or "No recorded tokens") + f" · {heatmap.aggregate.totals.sessions} sessions"
    summary = f"{usage_stats.usage_period_label(days)}: {detail}"
    partial = [
        labels.get(provider, provider)
        for provider, coverage in totals.source_coverage.items()
        if provider in provider_ids and coverage.status != usage_stats.UsageSourceStatus.OK
    ]
    partial.extend(
        labels.get(provider, provider)
        for provider in partial_provider_ids
        if labels.get(provider, provider) not in partial
    )
    if partial:
        summary += " · Partial local history: " + ", ".join(partial)
    if mode == "cost":
        summary += f" · {API_EQUIVALENT_COST_DISCLOSURE}"
    return model, summary


def build_usage_graph_model(
    settings,
    *,
    t3_policy: T3ReadOnlyPolicy | None = None,
) -> dict:
    """The chart model for the CURRENT metric, straight from the sources."""
    snapshot = _settings_snapshot(settings)
    if t3_policy is None:
        return _build_payload(snapshot)[0]
    return _build_payload(snapshot, t3_policy=t3_policy)[0]


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


def _build_key(
    settings,
    t3_policy: T3ReadOnlyPolicy | None = None,
) -> tuple:
    settings = _settings_snapshot(settings)
    t3_key = (
        False,
        False,
        None,
    )
    if t3_policy is not None:
        t3_key = (
            t3_policy.enabled,
            t3_policy.may_scan_activity_statistics,
            t3_policy.base_dir,
        )
    return (
        settings.usage_graph_days,
        settings.usage_display_mode,
        settings.usage_graph_providers,
        t3_key,
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
    t3_policy: T3ReadOnlyPolicy | None = None,
) -> None:
    """Fire-and-forget rebuild; lands on main via AppHelper.callAfter."""
    if t3_policy is None:
        candidate = getattr(target, "_t3_read_only_policy", None)
        if type(candidate) is T3ReadOnlyPolicy:
            t3_policy = candidate
    fields = getattr(target, "settings_fields", {}) or {}
    graph = fields.get("profile_usage_graph")
    heatmap_view = fields.get("profile_usage_heatmap")
    settings = _settings_snapshot(target.settings)
    key = _build_key(settings, t3_policy)

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
        if heatmap_view is not None and model.get("heatmap") is not None:
            try:
                heatmap_view.setHeatmap_(model["heatmap"])
            except Exception:
                pass
        label = fields.get("profile_usage_label")
        if label is not None and model.get("summary"):
            try:
                label.setStringValue_(model["summary"])
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
            if t3_policy is None:
                model, summary = _build_payload(settings)
            else:
                model, summary = _build_payload(settings, t3_policy=t3_policy)
            model = {**model, "summary": summary}
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
                    heatmap_view = fields.get("profile_usage_heatmap")
                    if heatmap_view is not None and model.get("heatmap") is not None:
                        try:
                            heatmap_view.setHeatmap_(model["heatmap"])
                        except Exception:
                            pass
                    # This label describes the local chart, not the separate
                    # provider summary. Replace loading and prior retry errors.
                    if summary:
                        label = fields.get("profile_usage_label")
                        if label is not None:
                            try:
                                label.setStringValue_(summary)
                            except Exception:
                                pass
                else:
                    # A failed worker is no longer scanning. Clear any old
                    # result so reopening the page retries instead of reusing it.
                    target.usage_graph_model = None
                    target._usage_local_scan_complete = False
                    setattr(target, _LAST_BUILD_ATTR, None)
                    message = "Local activity couldn't be loaded. Reopen Activity to retry."
                    failed_model = {**scanning_placeholder(settings), "empty_text": message}
                    unavailable = build_usage_heatmap(
                        [], days=settings.usage_graph_days,
                        provider_ids=settings.usage_graph_providers,
                    )
                    fields = getattr(target, "settings_fields", {}) or {}
                    for name, method, value in (
                        ("profile_usage_graph", "setModel_", failed_model),
                        ("profile_usage_heatmap", "setHeatmap_", unavailable),
                        ("profile_usage_label", "setStringValue_", message),
                    ):
                        field = fields.get(name)
                        if field is not None:
                            try:
                                getattr(field, method)(value)
                            except Exception:
                                pass
                if pending:
                    refresh_usage_graph(
                        target,
                        monotonic=monotonic,
                        t3_policy=t3_policy,
                    )

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
    "scan_t3_activity_statistics",
    "scanning_placeholder",
]
