"""usage_graph_worker: the settings chart must never lie about scanning.

The live failure this file exists to prevent (seen 2026-08-26): the
Overview chart showed "No activity in this range" with a degenerate axis
for the entire cold year scan, the summary label sat on "Loading local
usage history…", a mid-scan range change was silently dropped, and the
default-QoS scan thread made the whole app feel laggy.
"""

from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

from sidepulse import usage_graph_worker


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


def make_target(days=7, mode="tokens", providers=("claude", "codex")):
    target = SimpleNamespace()
    target.settings = SimpleNamespace(
        usage_graph_days=days,
        usage_display_mode=mode,
        usage_graph_providers=tuple(providers),
    )
    target.settings_fields = {
        "profile_usage_graph": FakeView(),
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
    usage_graph_worker.refresh_usage_graph(target)

    assert len(calls) == 1
    view = target.settings_fields["profile_usage_graph"]
    assert view.models[-1]["marker"] == "built"


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


def test_build_failure_clears_the_flag_and_keeps_the_view_scanning(
    synchronous_worker, monkeypatch
):
    target = make_target()

    def broken(_settings):
        raise RuntimeError("scan died")

    monkeypatch.setattr(usage_graph_worker, "_build_payload", broken)
    usage_graph_worker.refresh_usage_graph(target)

    assert getattr(target, "_usage_graph_worker_in_flight") is False
    assert target.usage_graph_model is None
    view = target.settings_fields["profile_usage_graph"]
    assert view.models[-1]["empty_text"] == "Scanning local activity…"
