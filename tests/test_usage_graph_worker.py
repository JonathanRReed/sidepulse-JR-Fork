"""usage_graph_worker: the settings chart must never lie about scanning.

The live failure this file exists to prevent (seen 2026-08-26): the
Overview chart showed "No activity in this range" with a degenerate axis
for the entire cold year scan, the summary label sat on "Loading local
usage history…", a mid-scan range change was silently dropped, and the
default-QoS scan thread made the whole app feel laggy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

from sidepulse import usage_graph_worker
from sidepulse.t3_compat import project_t3_read_only_policy


class FakeView:
    def __init__(self):
        self.models = []

    def setModel_(self, model):
        self.models.append(dict(model))


class FakeLabel:
    def __init__(self):
        self.values = []

    def setStringValue_(self, value):
        self.values.append(str(value))


class FakeHeatmapView:
    def __init__(self):
        self.heatmaps = []

    def setHeatmap_(self, heatmap):
        self.heatmaps.append(heatmap)


def make_target(days=7, mode="tokens", providers=("claude", "codex")):
    target = SimpleNamespace()
    target.settings = SimpleNamespace(
        usage_graph_days=days,
        usage_display_mode=mode,
        usage_graph_providers=tuple(providers),
    )
    target.settings_fields = {
        "profile_usage_graph": FakeView(),
        "profile_usage_heatmap": FakeHeatmapView(),
        "profile_usage_label": FakeLabel(),
    }
    target.usage_graph_model = None
    target.usage_summary_text = None
    return target


def _force_inline_apply(monkeypatch):
    """PyObjC IS installed in the dev venv, so callAfter would schedule
    onto a run loop no test ever spins. Poisoning the module import
    routes the worker onto its own documented inline fallback."""
    import sys

    monkeypatch.setitem(sys.modules, "PyObjCTools", None)


@pytest.fixture
def synchronous_worker(monkeypatch):
    """Run the worker thread inline and _apply directly (no AppKit)."""

    class InlineThread:
        def __init__(self, *, target, name, daemon):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(threading, "Thread", InlineThread)
    _force_inline_apply(monkeypatch)
    monkeypatch.setattr(
        usage_graph_worker, "_drop_to_utility_qos", lambda: None
    )


def model_for(settings, marker="built"):
    return (
        {
            "days": int(settings.usage_graph_days),
            "metric": str(settings.usage_display_mode),
            "labels": ("x",),
            "series": ({"provider": "claude"},),
            "scale_max": 10.0,
            "marker": marker,
        },
        "1 sessions summary",
    )


def test_scan_lands_model_and_resolves_loading_label(
    synchronous_worker, monkeypatch
):
    target = make_target()
    monkeypatch.setattr(usage_graph_worker, "_build_payload", model_for)

    usage_graph_worker.refresh_usage_graph(target)

    view = target.settings_fields["profile_usage_graph"]
    assert view.models[-1]["marker"] == "built"
    assert target.usage_graph_model["marker"] == "built"
    assert target._usage_local_scan_complete is True
    label = target.settings_fields["profile_usage_label"]
    assert label.values == ["1 sessions summary"]
    assert getattr(target, "_usage_graph_worker_in_flight") is False


def test_scan_lands_the_same_immutable_heatmap_payload(monkeypatch, synchronous_worker):
    from sidepulse.usage_heatmap import build_usage_heatmap

    target = make_target(providers=("claude", "codex"))
    stamp = datetime.now().timestamp()
    heatmap = build_usage_heatmap(
        [("claude", "session", "model", stamp, 10, 2, 3, 4, "message")],
        provider_ids=("claude", "codex"),
        days=7,
    )
    model, summary = model_for(target.settings)
    monkeypatch.setattr(
        usage_graph_worker,
        "_build_payload",
        lambda _settings: ({**model, "heatmap": heatmap}, summary),
    )

    usage_graph_worker.refresh_usage_graph(target)

    view = target.settings_fields["profile_usage_heatmap"]
    assert view.heatmaps == [heatmap]
    assert view.heatmaps[0].providers["codex"].data_status == "unavailable"


def test_scan_uses_one_settings_snapshot_for_key_and_payload(
    synchronous_worker, monkeypatch
):
    """A settings update cannot split the cache key from the chart payload."""

    class FlippingSettings:
        def __init__(self):
            self._values = {
                "usage_graph_days": (7, 365),
                "usage_display_mode": ("tokens", "sessions"),
                "usage_graph_providers": (("claude", "codex"), ("grok",)),
            }

        def _next(self, name):
            current, next_value = self._values[name]
            self._values[name] = (next_value, next_value)
            return current

        @property
        def usage_graph_days(self):
            return self._next("usage_graph_days")

        @property
        def usage_display_mode(self):
            return self._next("usage_display_mode")

        @property
        def usage_graph_providers(self):
            return self._next("usage_graph_providers")

    target = make_target()
    target.settings = FlippingSettings()

    built_settings = []

    def build(settings):
        built_settings.append(settings)
        return model_for(settings)

    monkeypatch.setattr(usage_graph_worker, "_build_payload", build)

    usage_graph_worker.refresh_usage_graph(target)

    assert built_settings[0].usage_graph_providers == ("claude", "codex")
    assert target.usage_graph_model["days"] == 7
    assert target.usage_graph_model["metric"] == "tokens"


def test_range_change_shows_scanning_not_the_old_chart(
    synchronous_worker, monkeypatch
):
    """A landed 7-day model must not keep rendering while a 365-day
    scan runs: the person who just picked Year sees SCANNING."""
    target = make_target(days=7)
    monkeypatch.setattr(usage_graph_worker, "_build_payload", model_for)
    usage_graph_worker.refresh_usage_graph(target)

    target.settings.usage_graph_days = 365
    seen_placeholder = {}
    original_build = usage_graph_worker._build_payload

    def slow_build(settings):
        # Capture what the view shows at the moment the scan STARTS.
        seen_placeholder["model"] = dict(
            target.settings_fields["profile_usage_graph"].models[-1]
        )
        return original_build(settings)

    monkeypatch.setattr(usage_graph_worker, "_build_payload", slow_build)
    usage_graph_worker.refresh_usage_graph(target)

    assert seen_placeholder["model"]["empty_text"] == "Scanning local activity…"
    assert seen_placeholder["model"]["days"] == 365
    view = target.settings_fields["profile_usage_graph"]
    assert view.models[-1]["days"] == 365


def test_mid_scan_request_is_remembered_not_dropped(monkeypatch):
    """The in-flight flag used to swallow a range change entirely; now
    it re-fires the scan when the running one lands."""
    target = make_target(days=7)
    builds = []
    started = []

    class DeferredThread:
        def __init__(self, *, target, name, daemon):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", DeferredThread)
    _force_inline_apply(monkeypatch)
    monkeypatch.setattr(usage_graph_worker, "_drop_to_utility_qos", lambda: None)

    def build(settings):
        builds.append(int(settings.usage_graph_days))
        return model_for(settings)

    monkeypatch.setattr(usage_graph_worker, "_build_payload", build)

    usage_graph_worker.refresh_usage_graph(target)  # scan 1 queued
    target.settings.usage_graph_days = 365
    usage_graph_worker.refresh_usage_graph(target)  # mid-scan: pending
    assert len(started) == 1
    assert getattr(target, "_usage_graph_rescan_pending") is True

    # Scan 1 runs from the snapshot captured when it was requested. The
    # pending re-fire then captures the updated settings and builds 365.
    started[0]()
    assert builds == [7]
    assert len(started) == 2
    started[1]()
    assert builds == [7, 365]
    assert target.usage_graph_model["days"] == 365
    view = target.settings_fields["profile_usage_graph"]
    assert view.models[-1]["days"] == 365


def test_recent_identical_result_is_reused_without_a_second_scan(
    synchronous_worker, monkeypatch
):
    target = make_target()
    calls = []

    def build(settings):
        calls.append(1)
        return model_for(settings)

    monkeypatch.setattr(usage_graph_worker, "_build_payload", build)
    usage_graph_worker.refresh_usage_graph(target)
    # A pane rebuild replaces the view but not the inputs.
    target.settings_fields["profile_usage_graph"] = FakeView()
    target.settings_fields["profile_usage_label"] = FakeLabel()
    usage_graph_worker.refresh_usage_graph(target)

    assert len(calls) == 1
    view = target.settings_fields["profile_usage_graph"]
    assert view.models[-1]["marker"] == "built"
    assert target.settings_fields["profile_usage_label"].values == ["1 sessions summary"]


def test_recent_result_cache_uses_injected_monotonic_boundary(
    synchronous_worker, monkeypatch
):
    target = make_target()
    calls = []
    now = [100.0]

    def build(settings):
        calls.append(now[0])
        return model_for(settings, marker=f"built-{len(calls)}")

    monkeypatch.setattr(usage_graph_worker, "_build_payload", build)
    def monotonic():
        return now[0]

    usage_graph_worker.refresh_usage_graph(target, monotonic=monotonic)
    now[0] = 159.999
    usage_graph_worker.refresh_usage_graph(target, monotonic=monotonic)
    assert calls == [100.0]

    now[0] = 160.0
    usage_graph_worker.refresh_usage_graph(target, monotonic=monotonic)
    assert calls == [100.0, 160.0]
    assert target.usage_graph_model["marker"] == "built-2"


def test_scan_period_start_is_pinned_to_injected_calendar_day() -> None:
    now = datetime(2026, 8, 29, 23, 59, 59)

    assert usage_graph_worker._period_start(7, now=now) == datetime(
        2026,
        8,
        23,
    )


def test_build_failure_shows_unavailable_instead_of_scanning_and_can_retry(
    synchronous_worker, monkeypatch
):
    target = make_target()
    target.usage_summary_text = "An older provider summary"

    def broken(_settings):
        raise RuntimeError("private transcript path must not appear in the UI")

    monkeypatch.setattr(usage_graph_worker, "_build_payload", broken)
    usage_graph_worker.refresh_usage_graph(target)

    assert getattr(target, "_usage_graph_worker_in_flight") is False
    assert target.usage_graph_model is None
    view = target.settings_fields["profile_usage_graph"]
    assert view.models[-1]["empty_text"] == "Local activity couldn't be loaded. Reopen Activity to retry."
    assert target.settings_fields["profile_usage_label"].values[-1] == view.models[-1]["empty_text"]
    assert target.settings_fields["profile_usage_heatmap"].heatmaps[-1].aggregate.data_status == "unavailable"
    assert target._usage_local_scan_complete is False

    monkeypatch.setattr(usage_graph_worker, "_build_payload", model_for)
    usage_graph_worker.refresh_usage_graph(target)
    assert view.models[-1]["marker"] == "built"
    assert target._usage_local_scan_complete is True
    assert target.settings_fields["profile_usage_label"].values[-1] == "1 sessions summary"

def test_scan_opencode_records_parses_messages(tmp_path):
    db_file = tmp_path / "opencode.db"
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE message (id TEXT, session_id TEXT, time_created INT, data TEXT)")
    msg_data = {
        "tokens": {"input": 1000, "output": 200, "cache": {"read": 50, "write": 10}},
        "model": "gemini-flash"
    }
    con.execute("INSERT INTO message VALUES ('msg1', 'ses1', 1700000000000, ?)", (json.dumps(msg_data),))
    con.commit()
    con.close()

    records = usage_graph_worker._scan_opencode_records(db_file, 1690000000.0)
    assert len(records) == 1
    assert records[0][0] == "opencode"
    assert records[0][1] == "ses1"
    assert records[0][4] == 1000
    assert records[0][7] == 200


def test_activity_does_not_touch_deselected_provider_sources(monkeypatch):
    settings = make_target(mode="tokens", providers=("devin",)).settings

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a deselected provider source was touched")

    monkeypatch.setattr(usage_graph_worker.usage_stats, "build_usage_inventory", forbidden)
    monkeypatch.setattr(usage_graph_worker, "_scan_opencode_records", forbidden)

    model, _summary = usage_graph_worker._build_payload(settings)

    assert model["series"] == ()
    assert tuple(model["heatmap"].providers) == ("devin",)


def test_scan_t3code_records_parses_activities(tmp_path):
    db_file = tmp_path / "state.sqlite"
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE projection_thread_activities (activity_id TEXT, thread_id TEXT, kind TEXT, created_at TEXT, payload_json TEXT)")
    act_data = {
        "usage": {"total_tokens": 3500, "input_tokens": 3000, "output_tokens": 500}
    }
    con.execute("INSERT INTO projection_thread_activities VALUES ('act1', 'th1', 'task.completed', '2026-03-20T10:00:00Z', ?)", (json.dumps(act_data),))
    con.commit()
    con.close()

    records = usage_graph_worker._scan_t3code_records(db_file, 1700000000.0)
    assert len(records) == 1
    assert records[0][0] == "t3code"
    assert records[0][1] == "th1"
    assert records[0][4] == 3000
    assert records[0][7] == 500


def test_t3_activity_scan_is_bounded_to_the_newest_records(tmp_path) -> None:
    db_file = tmp_path / "state.sqlite"
    connection = sqlite3.connect(db_file)
    connection.execute(
        "CREATE TABLE projection_thread_activities "
        "(activity_id TEXT, thread_id TEXT, kind TEXT, created_at TEXT, payload_json TEXT)"
    )
    payload = json.dumps(
        {"usage": {"total_tokens": 10, "input_tokens": 8, "output_tokens": 2}}
    )
    connection.executemany(
        "INSERT INTO projection_thread_activities VALUES (?, ?, ?, ?, ?)",
        (
            ("old", "thread-old", "task.completed", "2026-03-20T10:00:00Z", payload),
            ("middle", "thread-middle", "task.completed", "2026-03-20T11:00:00Z", payload),
            ("new", "thread-new", "task.completed", "2026-03-20T12:00:00Z", payload),
        ),
    )
    connection.commit()
    connection.close()

    records = usage_graph_worker._scan_t3code_records(
        db_file,
        1_700_000_000.0,
        maximum_records=2,
    )

    assert [record[1] for record in records] == ["thread-new", "thread-middle"]


def test_t3_activity_scan_rejects_oversized_sqlite_values_before_json_decode(
    tmp_path,
    monkeypatch,
) -> None:
    db_file = tmp_path / "state.sqlite"
    connection = sqlite3.connect(db_file)
    connection.execute(
        "CREATE TABLE projection_thread_activities "
        "(activity_id TEXT, thread_id TEXT, kind TEXT, created_at TEXT, payload_json TEXT)"
    )
    oversized = json.dumps(
        {
            "usage": {"total_tokens": 10},
            "padding": "x" * 262_189,
        }
    )
    connection.execute(
        "INSERT INTO projection_thread_activities VALUES (?, ?, ?, ?, ?)",
        ("large", "thread-large", "task.completed", "2026-03-20T12:00:00Z", oversized),
    )
    connection.commit()
    connection.close()
    decoded = []
    real_loads = json.loads

    def bounded_loads(value):
        decoded.append(len(value.encode("utf-8")))
        return real_loads(value)

    monkeypatch.setattr(usage_graph_worker.json, "loads", bounded_loads)

    records = usage_graph_worker._scan_t3code_records(db_file, 1_700_000_000.0)

    assert records == []
    assert decoded == []


@pytest.mark.parametrize(
    ("fixture_kind", "expected_status"),
    (("valid", "complete"), ("oversized", "partial"), ("missing", "missing")),
)
def test_t3_activity_scan_reports_coverage_status(
    tmp_path,
    fixture_kind,
    expected_status,
) -> None:
    db_file = tmp_path / "state.sqlite"
    if fixture_kind != "missing":
        connection = sqlite3.connect(db_file)
        connection.execute(
            "CREATE TABLE projection_thread_activities "
            "(activity_id TEXT, thread_id TEXT, kind TEXT, created_at TEXT, payload_json TEXT)"
        )
        payload = json.dumps(
            {
                "usage": {"total_tokens": 10},
                "padding": "x" * (262_189 if fixture_kind == "oversized" else 0),
            }
        )
        connection.execute(
            "INSERT INTO projection_thread_activities VALUES (?, ?, ?, ?, ?)",
            ("activity", "thread", "task.completed", "2026-03-20T12:00:00Z", payload),
        )
        connection.commit()
        connection.close()
    statuses = []

    usage_graph_worker._scan_t3code_records(
        db_file,
        1_700_000_000.0,
        coverage_reporter=statuses.append,
    )

    assert statuses == [expected_status]


def test_t3_activity_scan_rejects_oversized_identifier_before_returning_row(
    tmp_path,
) -> None:
    db_file = tmp_path / "state.sqlite"
    connection = sqlite3.connect(db_file)
    connection.execute(
        "CREATE TABLE projection_thread_activities "
        "(activity_id TEXT, thread_id TEXT, kind TEXT, created_at TEXT, payload_json TEXT)"
    )
    connection.execute(
        "INSERT INTO projection_thread_activities VALUES (?, ?, ?, ?, ?)",
        (
            "activity",
            "x" * 262_189,
            "task.completed",
            "2026-03-20T12:00:00Z",
            json.dumps({"usage": {"total_tokens": 10}}),
        ),
    )
    connection.commit()
    connection.close()

    records = usage_graph_worker._scan_t3code_records(
        db_file,
        1_700_000_000.0,
    )

    assert records == []


def test_t3_activity_scan_sets_sqlite_allocation_limit_before_query(
    tmp_path,
    monkeypatch,
) -> None:
    db_file = tmp_path / "state.sqlite"
    connection = sqlite3.connect(db_file)
    connection.execute(
        "CREATE TABLE projection_thread_activities "
        "(activity_id TEXT, thread_id TEXT, kind TEXT, created_at TEXT, payload_json TEXT)"
    )
    connection.commit()
    connection.close()

    class LimitedConnection:
        def __init__(self, path):
            self._connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            self._limited = False

        def setlimit(self, category, limit):
            assert category == sqlite3.SQLITE_LIMIT_LENGTH
            assert limit == usage_graph_worker.T3_SQLITE_MAX_VALUE_BYTES
            self._limited = True
            return self._connection.setlimit(category, limit)

        def execute(self, *args, **kwargs):
            assert self._limited, "SQLite length limit must precede untrusted queries"
            return self._connection.execute(*args, **kwargs)

        def set_progress_handler(self, *args, **kwargs):
            return self._connection.set_progress_handler(*args, **kwargs)

        def close(self):
            return self._connection.close()

    monkeypatch.setattr(
        usage_graph_worker,
        "_open_read_only",
        lambda path: LimitedConnection(path),
    )

    assert usage_graph_worker._scan_t3code_records(db_file, 0.0) == []


def test_t3_activity_scan_stops_before_aggregate_payload_budget(tmp_path) -> None:
    db_file = tmp_path / "state.sqlite"
    connection = sqlite3.connect(db_file)
    connection.execute(
        "CREATE TABLE projection_thread_activities "
        "(activity_id TEXT, thread_id TEXT, kind TEXT, created_at TEXT, payload_json TEXT)"
    )
    payload = json.dumps(
        {"usage": {"total_tokens": 10}, "padding": "x" * 900}
    )
    connection.executemany(
        "INSERT INTO projection_thread_activities VALUES (?, ?, ?, ?, ?)",
        (
            ("new", "thread-new", "task.completed", "2026-03-20T12:00:00Z", payload),
            ("old", "thread-old", "task.completed", "2026-03-20T11:00:00Z", payload),
        ),
    )
    connection.commit()
    connection.close()

    records = usage_graph_worker._scan_t3code_records(
        db_file,
        1_700_000_000.0,
        maximum_payload_bytes=2_000,
        maximum_total_payload_bytes=1_100,
    )

    assert [record[1] for record in records] == ["thread-new"]


def test_t3_activity_scan_aborts_real_query_when_progress_budget_expires(
    tmp_path,
    monkeypatch,
) -> None:
    db_file = tmp_path / "state.sqlite"
    connection = sqlite3.connect(db_file)
    connection.execute(
        "CREATE TABLE projection_thread_activities "
        "(activity_id TEXT, thread_id TEXT, kind TEXT, created_at TEXT, payload_json TEXT)"
    )
    payload = json.dumps({"usage": {"total_tokens": 10}})
    connection.executemany(
        "INSERT INTO projection_thread_activities VALUES (?, ?, ?, ?, ?)",
        (
            (
                f"activity-{index}",
                f"thread-{index}",
                "task.completed",
                "2026-03-20T12:00:00Z",
                payload,
            )
            for index in range(2_000)
        ),
    )
    connection.commit()
    connection.close()
    clock_values = iter((0.0, 1.0))

    def expired_clock():
        return next(clock_values, 1.0)

    def forbidden_decode(_value):
        raise AssertionError("an expired query must not decode payloads")

    monkeypatch.setattr(usage_graph_worker.json, "loads", forbidden_decode)

    records = usage_graph_worker._scan_t3code_records(
        db_file,
        1_700_000_000.0,
        monotonic=expired_clock,
    )

    assert records == []


def test_t3_activity_scan_does_not_sum_cumulative_progress_rows(tmp_path) -> None:
    db_file = tmp_path / "state.sqlite"
    connection = sqlite3.connect(db_file)
    connection.execute(
        "CREATE TABLE projection_thread_activities "
        "(activity_id TEXT, thread_id TEXT, kind TEXT, created_at TEXT, payload_json TEXT)"
    )
    connection.executemany(
        "INSERT INTO projection_thread_activities VALUES (?, ?, ?, ?, ?)",
        (
            (
                "progress",
                "thread-1",
                "task.progress",
                "2026-03-20T10:00:00Z",
                json.dumps({"taskId": "task-1", "usage": {"total_tokens": 90}}),
            ),
            (
                "completed",
                "thread-1",
                "task.completed",
                "2026-03-20T10:01:00Z",
                json.dumps({"taskId": "task-1", "usage": {"total_tokens": 100}}),
            ),
        ),
    )
    connection.commit()
    connection.close()

    records = usage_graph_worker._scan_t3code_records(
        db_file,
        1_700_000_000.0,
    )

    assert len(records) == 1
    assert records[0][4] == 100


def test_t3_activity_statistics_do_not_resolve_a_path_without_opt_in() -> None:
    policy = project_t3_read_only_policy(
        SimpleNamespace(t3code_enabled=True, t3code_base_dir="/configured/t3")
    )
    calls = []

    def resolve_path(_base_dir):
        calls.append("path")
        raise AssertionError("T3 path must not be resolved")

    def scan(_path, _since_epoch):
        calls.append("scan")
        raise AssertionError("T3 SQLite must not be scanned")

    records = usage_graph_worker.scan_t3_activity_statistics(
        policy,
        1_700_000_000.0,
        path_resolver=resolve_path,
        scanner=scan,
    )

    assert records == []
    assert calls == []


def test_usage_graph_does_not_scan_t3_for_observability_only(
    monkeypatch,
) -> None:
    policy = project_t3_read_only_policy(
        SimpleNamespace(t3code_enabled=True, t3code_base_dir="/configured/t3")
    )
    calls = []
    monkeypatch.setattr(
        usage_graph_worker.usage_stats,
        "scan_usage",
        lambda *_args, **_kwargs: usage_graph_worker.usage_stats.UsageTotals(),
    )
    monkeypatch.setattr(usage_graph_worker, "_scan_opencode_records", lambda *_args: [])
    monkeypatch.setattr(usage_graph_worker, "_scan_antigravity_records", lambda *_args: [])

    def scan_t3(*_args, **_kwargs):
        calls.append("scan")
        return []

    monkeypatch.setattr(usage_graph_worker, "scan_t3_activity_statistics", scan_t3)

    usage_graph_worker._build_payload(make_target().settings, t3_policy=policy)

    assert calls == []


def test_usage_graph_refresh_forwards_the_explicit_t3_policy(
    synchronous_worker,
    monkeypatch,
) -> None:
    target = make_target()
    policy = project_t3_read_only_policy(
        SimpleNamespace(t3code_enabled=True, t3code_base_dir="/configured/t3"),
        activity_statistics_enabled=True,
    )
    seen = []

    def build(settings, *, t3_policy=None):
        seen.append(t3_policy)
        return model_for(settings)

    monkeypatch.setattr(usage_graph_worker, "_build_payload", build)

    usage_graph_worker.refresh_usage_graph(target, t3_policy=policy)

    assert seen == [policy]


def test_usage_graph_refresh_uses_the_runtime_t3_policy_on_the_target(
    synchronous_worker,
    monkeypatch,
) -> None:
    target = make_target()
    target._t3_read_only_policy = project_t3_read_only_policy(
        SimpleNamespace(
            t3code_enabled=True,
            t3code_activity_statistics_enabled=True,
        )
    )
    seen = []

    def build(settings, *, t3_policy=None):
        seen.append(t3_policy)
        return model_for(settings)

    monkeypatch.setattr(usage_graph_worker, "_build_payload", build)

    usage_graph_worker.refresh_usage_graph(target)

    assert seen == [target._t3_read_only_policy]


def test_t3_activity_opt_in_change_invalidates_the_usage_graph_cache(
    synchronous_worker,
    monkeypatch,
) -> None:
    target = make_target()
    integration = SimpleNamespace(
        t3code_enabled=True,
        t3code_base_dir="/configured/t3",
    )
    observability_only = project_t3_read_only_policy(integration)
    with_statistics = project_t3_read_only_policy(
        integration,
        activity_statistics_enabled=True,
    )
    builds = []

    def build(settings, *, t3_policy=None):
        builds.append(t3_policy)
        return model_for(settings, marker=f"build-{len(builds)}")

    monkeypatch.setattr(usage_graph_worker, "_build_payload", build)

    usage_graph_worker.refresh_usage_graph(target, t3_policy=observability_only)
    usage_graph_worker.refresh_usage_graph(target, t3_policy=with_statistics)

    assert builds == [observability_only, with_statistics]
    assert target.usage_graph_model["marker"] == "build-2"


def test_cost_graph_discloses_api_equivalent_semantics(monkeypatch) -> None:
    settings = make_target(mode="cost").settings
    monkeypatch.setattr(
        usage_graph_worker.usage_stats,
        "scan_usage",
        lambda *_args, **_kwargs: usage_graph_worker.usage_stats.UsageTotals(),
    )
    monkeypatch.setattr(usage_graph_worker, "_scan_opencode_records", lambda *_args: [])
    monkeypatch.setattr(usage_graph_worker, "_scan_antigravity_records", lambda *_args: [])

    model, summary = usage_graph_worker._build_payload(settings)

    assert model["cost_semantics"] == "api_equivalent_estimate"
    assert summary is not None
    assert "API-equivalent estimate, not subscription spend" in summary


@pytest.mark.parametrize("mode", ["tokens", "cost", "sessions", "percent"])
def test_activity_selection_applies_to_chart_heatmap_and_summary(monkeypatch, tmp_path, mode):
    from sidepulse import session_history, usage_percent_history
    from sidepulse.private_io import atomic_private_write

    now = datetime.now()
    selected = ("devin",) if mode in ("sessions", "percent") else ("claude",)
    records = [
        ("claude", "c1", "claude-opus-4", now.timestamp(), 10, 2, 3, 4, "c1"),
        ("codex", "x1", "gpt-5", now.timestamp(), 900, 0, 0, 100, "x1"),
    ]
    monkeypatch.setattr(
        usage_graph_worker.usage_stats, "scan_usage",
        lambda *_args, **_kwargs: usage_graph_worker.usage_stats.UsageTotals(records=list(records)),
    )
    monkeypatch.setattr(
        usage_graph_worker, "_scan_opencode_records",
        lambda *_args: [("opencode", "o1", "model", now.timestamp(), 500, 0, 0, 0, "o1")],
    )
    monkeypatch.setattr(usage_graph_worker, "_scan_antigravity_records", lambda *_args: [])
    monkeypatch.setattr(session_history, "ledger_session_days", lambda *_args, **_kwargs: {
        "devin": {now.date().isoformat(): 2}, "grok": {now.date().isoformat(): 500},
    })
    history = tmp_path / "percent.jsonl"
    atomic_private_write(history, "".join(json.dumps({
        "provider_id": provider, "lane_id": "weekly", "remaining_percent": 40,
        "observed_at_epoch": now.timestamp(),
    }) + "\n" for provider in ("devin", "grok")))
    monkeypatch.setattr(usage_percent_history, "default_percent_history_path", lambda: history)

    model, summary = usage_graph_worker._build_payload(make_target(mode=mode, providers=selected).settings)

    assert tuple(series["provider_id"] for series in model["series"]) == selected
    assert tuple(model["heatmap"].providers) == selected
    assert "Codex" not in summary and "OpenCode" not in summary and "Grok" not in summary
    if mode in ("tokens", "cost"):
        assert "Claude 19" in summary
        assert "1 sessions" in summary
        assert model["heatmap"].aggregate.totals.tokens == 19
    elif mode == "sessions":
        assert "Devin 2 session-days" in summary


def test_antigravity_steps_contribute_sessions_but_not_measured_token_heatmap(monkeypatch):
    from sidepulse import session_history

    now = datetime.now().timestamp()
    monkeypatch.setattr(usage_graph_worker.usage_stats, "scan_usage", lambda *_args, **_kwargs:
                        usage_graph_worker.usage_stats.UsageTotals())
    monkeypatch.setattr(usage_graph_worker, "_scan_opencode_records", lambda *_args: [])
    monkeypatch.setattr(usage_graph_worker, "_scan_antigravity_records", lambda *_args: [
        ("antigravity", "a1", "gemini", now, 0, 0, 0, 0, "a1"),
        ("antigravity", "a2", "gemini", now, 0, 0, 0, 0, "a2"),
    ])
    monkeypatch.setattr(session_history, "ledger_session_days", lambda *_args, **_kwargs: {})

    model, summary = usage_graph_worker._build_payload(
        make_target(mode="sessions", providers=("antigravity",)).settings,
    )

    assert len(model["series"]) == 1
    assert sum(model["series"][0]["values"]) == 2
    assert "Antigravity 2 session-days" in summary
    assert model["heatmap"].providers["antigravity"].data_status == "unavailable"


@pytest.mark.parametrize("selected, partial", [("codex", True), ("claude", False)])
def test_activity_discloses_partial_selected_history(monkeypatch, selected, partial):
    stats = usage_graph_worker.usage_stats
    coverage = stats.UsageSourceCoverage(
        provider_id="codex", status=stats.UsageSourceStatus.PARTIAL,
        root_present=True, root_walked=True, files_discovered=2,
        files_read=1, cache_hits=0, malformed_lines=0, unreadable_files=0,
        skipped_symlinks=0, duplicate_physical_files=0, truncated_files=1,
    )
    monkeypatch.setattr(stats, "scan_usage", lambda *_args, **_kwargs:
                        stats.UsageTotals(source_coverage={"codex": coverage}))
    monkeypatch.setattr(usage_graph_worker, "_scan_opencode_records", lambda *_args: [])

    _model, summary = usage_graph_worker._build_payload(make_target(providers=(selected,)).settings)

    assert ("Partial local history: Codex" in summary) is partial


def test_explicit_t3_statistics_choice_is_shared_by_chart_and_heatmap(monkeypatch):
    monkeypatch.setattr(usage_graph_worker.usage_stats, "scan_usage", lambda *_args, **_kwargs:
                        usage_graph_worker.usage_stats.UsageTotals())
    monkeypatch.setattr(usage_graph_worker, "_scan_opencode_records", lambda *_args: [])
    def scan_t3(*_args, coverage_reporter=None, **_kwargs):
        coverage_reporter("complete")
        return [
            ("t3code", "t1", "model", datetime.now().timestamp(), 10, 0, 0, 5, "t1"),
        ]

    monkeypatch.setattr(usage_graph_worker, "scan_t3_activity_statistics", scan_t3)
    policy = project_t3_read_only_policy(
        SimpleNamespace(t3code_enabled=True), activity_statistics_enabled=True,
    )

    model, summary = usage_graph_worker._build_payload(make_target().settings, t3_policy=policy)

    assert [series["provider_id"] for series in model["series"]] == ["t3code"]
    assert model["heatmap"].providers["t3code"].totals.tokens == 15
    assert "T3 Code 15" in summary


@pytest.mark.parametrize("coverage_status", ("partial", "missing"))
def test_t3_incomplete_coverage_is_exposed_in_model_and_summary(
    monkeypatch,
    coverage_status,
):
    monkeypatch.setattr(
        usage_graph_worker.usage_stats,
        "scan_usage",
        lambda *_args, **_kwargs: usage_graph_worker.usage_stats.UsageTotals(),
    )
    monkeypatch.setattr(usage_graph_worker, "_scan_opencode_records", lambda *_args: [])

    def partial_scan(_policy, _since_epoch, *, coverage_reporter=None):
        coverage_reporter(coverage_status)
        return []

    monkeypatch.setattr(
        usage_graph_worker,
        "scan_t3_activity_statistics",
        partial_scan,
    )
    policy = project_t3_read_only_policy(
        SimpleNamespace(t3code_enabled=True),
        activity_statistics_enabled=True,
    )

    model, summary = usage_graph_worker._build_payload(
        make_target(providers=()).settings,
        t3_policy=policy,
    )

    assert model["partial_provider_ids"] == ("t3code",)
    assert "Partial local history: T3 Code" in summary


def test_scan_antigravity_records_parses_summaries(tmp_path):
    import sqlite3
    agy_dir = tmp_path / "antigravity-cli"
    agy_dir.mkdir()
    db_file = agy_dir / "conversation_summaries.db"
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE conversation_summaries (conversation_id TEXT, step_count INT, last_modified_time TEXT)")
    con.execute("INSERT INTO conversation_summaries VALUES ('conv1', 10, '2026-09-01T12:00:00')")
    con.commit()
    con.close()

    records = usage_graph_worker._scan_antigravity_records(tmp_path, 1700000000.0)
    assert len(records) == 1
    assert records[0][0] == "antigravity"
    assert records[0][1] == "conv1"
    # Step counts establish activity, not a measured token count.
    assert sum(records[0][4:8]) == 0
