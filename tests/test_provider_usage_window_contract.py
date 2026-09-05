from __future__ import annotations

import ast
import importlib
import inspect
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "sidepulse" / "provider_usage_window.py"


class _FakeView:
    pass


class _FakeStack:
    def __init__(self) -> None:
        self.views: list[object] = []

    def arrangedSubviews(self):
        return tuple(self.views)

    def addArrangedSubview_(self, view) -> None:
        self.views.append(view)


class _FakeWindow:
    def __init__(self) -> None:
        self.title = None

    def setTitle_(self, title) -> None:
        self.title = title


def _load_controller_module(monkeypatch: pytest.MonkeyPatch):
    """Load the AppKit host without requiring macOS frameworks in CI."""
    fake_appkit = SimpleNamespace(
        NSBackingStoreBuffered=0,
        NSBezierPath=object,
        NSButton=object,
        NSColor=object,
        NSFont=object,
        NSLayoutConstraint=object,
        NSScrollView=object,
        NSStackView=object,
        NSTextField=object,
        NSUserInterfaceLayoutOrientationHorizontal=0,
        NSUserInterfaceLayoutOrientationVertical=1,
        NSView=_FakeView,
        NSWindow=object,
        NSWindowStyleMaskClosable=1,
        NSWindowStyleMaskMiniaturizable=2,
        NSWindowStyleMaskResizable=4,
        NSWindowStyleMaskTitled=8,
    )
    fake_objc = SimpleNamespace(super=super)
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)
    monkeypatch.setitem(sys.modules, "objc", fake_objc)
    monkeypatch.delitem(sys.modules, "sidepulse.provider_usage_window", raising=False)
    return importlib.import_module("sidepulse.provider_usage_window")


def _controller_for_refresh(module, *, wall_clock, monotonic_clock):
    controller = module.ProviderUsageWindowController.__new__(
        module.ProviderUsageWindowController
    )
    controller._wall_clock = wall_clock
    controller._monotonic_clock = monotonic_clock
    controller.stack = _FakeStack()
    controller.window = _FakeWindow()
    controller._message = ""
    controller._message_until = 0.0
    controller._refresh_timer = None
    controller._privacy_mode = False
    controller.action_target = None
    return controller


def test_usage_window_is_a_thin_appkit_projection_host():
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    assert "ProviderUsageWindowController" in names
    assert "refresh" in names
    assert "show" in names
    assert "project_usage_center" in source
    for forbidden in (
        "urlopen(",
        "subprocess.run(",
        "sqlite3.connect(",
        "read_text(",
        "read_bytes(",
    ):
        assert forbidden not in source


def test_usage_window_passes_cached_merged_sync_and_never_fetches():
    # The "across synced Macs" line renders from LOCAL cached documents
    # only (2026-08-26): the window imports the worker-refreshed in-memory
    # cache, not the local document loader, SFTP runtime, or transport.
    source = MODULE.read_text(encoding="utf-8")
    assert "merged_sync=cached_merged_sync(state)" in source
    assert "provider_usage_sync_cache" in source
    assert "provider_usage_sync_runtime" not in source
    assert "provider_usage_sync_transport" not in source


def test_usage_window_cache_lookup_is_memory_only_and_worker_refreshed():
    cache = ROOT / "src" / "sidepulse" / "provider_usage_sync_cache.py"
    source = cache.read_text(encoding="utf-8")
    tree = ast.parse(source)

    def calls(name: str) -> tuple[str, ...]:
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        return tuple(
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        )

    assert "load_cached_merged_sync" not in calls("cached_merged_sync")
    assert "load_cached_merged_sync" in calls("refresh_cached_merged_sync")


def test_usage_window_survives_its_own_close_button():
    # A code-created NSWindow defaults to released-when-closed; this
    # window is cached and refreshed forever, so closing it once made
    # every later Connect click a dead-object SIGTRAP (2026-08-20, three
    # crash reports in one morning).
    source = MODULE.read_text(encoding="utf-8")
    assert "setReleasedWhenClosed_(False)" in source


def test_usage_window_scroll_document_uses_top_origin():
    """AppKit scroll documents otherwise open at their bottom edge.

    The Usage Center must start with its heading and first cards at the top
    instead of showing a large empty region above them.
    """
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    flipped = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FlippedStackView"
    )
    method = next(
        node
        for node in flipped.body
        if isinstance(node, ast.FunctionDef) and node.name == "isFlipped"
    )

    assert isinstance(method.body[0], ast.Return)
    assert method.body[0].value.value is True
    assert "self.stack = FlippedStackView.alloc().init()" in source


def test_usage_window_accepts_injected_wall_and_monotonic_clocks(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load_controller_module(monkeypatch)
    parameters = inspect.signature(module.ProviderUsageWindowController).parameters

    assert parameters["wall_clock"].default is time.time
    assert parameters["monotonic_clock"].default is time.monotonic


def test_usage_window_accepts_explicit_privacy_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load_controller_module(monkeypatch)
    parameters = inspect.signature(module.ProviderUsageWindowController).parameters

    assert parameters["privacy_mode"].default is False


def test_usage_window_privacy_mode_can_follow_live_settings(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load_controller_module(monkeypatch)
    controller = _controller_for_refresh(
        module,
        wall_clock=lambda: 500.0,
        monotonic_clock=lambda: 100.0,
    )

    from sidepulse.provider_usage_runtime import ProviderUsageState

    state = ProviderUsageState((), None, None, False)
    controller._last_state = state
    refreshes = []
    monkeypatch.setattr(controller, "refresh", refreshes.append)

    controller.set_privacy_mode(True)

    assert controller._privacy_mode is True
    assert refreshes == [state]


def test_usage_window_projection_uses_injected_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load_controller_module(monkeypatch)
    from sidepulse.provider_usage_runtime import ProviderUsageState

    wall_reads: list[str] = []
    controller = _controller_for_refresh(
        module,
        wall_clock=lambda: (wall_reads.append("read") or 1234.5),
        monotonic_clock=lambda: 900.0,
    )
    projection_calls: list[float] = []
    monkeypatch.setattr(module, "cached_merged_sync", lambda _state: None)
    monkeypatch.setattr(
        module,
        "project_usage_center",
        lambda _state, *, now, merged_sync, visual, privacy_mode: (
            projection_calls.append(now)
            or SimpleNamespace(subtitle="subtitle", aggregate_metrics=(), sections=())
        ),
    )
    monkeypatch.setattr(module, "_label", lambda text, **_kwargs: text)

    controller.refresh(ProviderUsageState((), None, None, False))

    assert wall_reads == ["read"]
    assert projection_calls == [1234.5]


@pytest.mark.parametrize(
    ("monotonic_now", "banner_visible"),
    ((11.999, True), (12.0, False)),
)
def test_usage_window_message_expires_at_exact_monotonic_deadline(
    monkeypatch: pytest.MonkeyPatch,
    monotonic_now: float,
    banner_visible: bool,
):
    module = _load_controller_module(monkeypatch)
    from sidepulse.provider_usage_runtime import ProviderUsageState

    controller = _controller_for_refresh(
        module,
        wall_clock=lambda: 500.0,
        monotonic_clock=lambda: monotonic_now,
    )
    controller._message = "Connected"
    controller._message_until = 12.0
    monkeypatch.setattr(module, "cached_merged_sync", lambda _state: None)
    monkeypatch.setattr(
        module,
        "project_usage_center",
        lambda _state, *, now, merged_sync, visual, privacy_mode: SimpleNamespace(
            subtitle="subtitle", aggregate_metrics=(), sections=()
        ),
    )
    monkeypatch.setattr(module, "_label", lambda text, **_kwargs: text)

    controller.refresh(ProviderUsageState((), None, None, False))

    assert ("Connected" in controller.stack.views) is banner_visible


def test_usage_window_message_deadline_uses_injected_monotonic_clock(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load_controller_module(monkeypatch)
    from sidepulse.provider_usage_runtime import ProviderUsageState

    monotonic_reads: list[str] = []
    controller = _controller_for_refresh(
        module,
        wall_clock=lambda: 500.0,
        monotonic_clock=lambda: (monotonic_reads.append("read") or 100.0),
    )
    controller._last_state = ProviderUsageState((), None, None, False)
    monkeypatch.setattr(controller, "refresh", lambda _state: None)

    controller.show_message("Connected")

    assert monotonic_reads == ["read"]
    assert controller._message_until == 112.0


def test_usage_window_passes_only_the_privacy_safe_visual_projection(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load_controller_module(monkeypatch)
    from sidepulse.provider_feature_settings import (
        ProviderInstancePolicyProjection,
        ProviderInstanceRetentionProjection,
        ProviderInstanceSessionActionProjection,
        ProviderInstanceSharingProjection,
        ProviderInstanceVisualProjection,
    )
    from sidepulse.provider_usage_runtime import ProviderUsageState

    visual = ProviderInstanceVisualProjection(())
    policies = ProviderInstancePolicyProjection(
        visual=visual,
        retention=ProviderInstanceRetentionProjection(()),
        sharing=ProviderInstanceSharingProjection(()),
        session_action=ProviderInstanceSessionActionProjection(()),
    )
    controller = _controller_for_refresh(
        module,
        wall_clock=lambda: 500.0,
        monotonic_clock=lambda: 100.0,
    )
    controller.action_target = SimpleNamespace(
        _sidepulse_provider_instance_policies=policies,
    )
    received: list[object] = []
    monkeypatch.setattr(module, "cached_merged_sync", lambda _state: None)
    monkeypatch.setattr(
        module,
        "project_usage_center",
        lambda _state, *, now, merged_sync, visual, privacy_mode: (
            received.append(visual)
            or SimpleNamespace(subtitle="subtitle", aggregate_metrics=(), sections=())
        ),
    )
    monkeypatch.setattr(module, "_label", lambda text, **_kwargs: text)

    controller.refresh(ProviderUsageState((), None, None, False))

    assert received == [visual]


def test_usage_window_passes_privacy_mode_to_safe_identity_projection(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load_controller_module(monkeypatch)
    from sidepulse.provider_usage_runtime import ProviderUsageState

    controller = _controller_for_refresh(
        module,
        wall_clock=lambda: 500.0,
        monotonic_clock=lambda: 100.0,
    )
    controller._privacy_mode = True
    received: list[bool] = []
    monkeypatch.setattr(module, "cached_merged_sync", lambda _state: None)
    monkeypatch.setattr(
        module,
        "project_usage_center",
        lambda _state, *, now, merged_sync, visual, privacy_mode: (
            received.append(privacy_mode)
            or SimpleNamespace(subtitle="subtitle", aggregate_metrics=(), sections=())
        ),
    )
    monkeypatch.setattr(module, "_label", lambda text, **_kwargs: text)

    controller.refresh(ProviderUsageState((), None, None, False))

    assert received == [True]


def test_usage_window_never_reprocesses_safe_projected_account_labels():
    source = MODULE.read_text(encoding="utf-8")

    assert "def _account_display(" not in source
    assert "_label(section.account" in source


def test_usage_window_meter_prefers_exact_profile_color(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load_controller_module(monkeypatch)
    section = SimpleNamespace(provider_id="claude", color_override="#112233")
    lane = SimpleNamespace(provider_id="claude")

    assert module._usage_meter_color(section, lane) == "#112233"

    section.color_override = None
    assert module._usage_meter_color(section, lane) == module.default_agent_color(
        "claude"
    )


def test_usage_window_refresh_pulse_keeps_the_sixty_second_deadline(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load_controller_module(monkeypatch)

    class FakeTimer:
        calls: ClassVar[list[tuple[float, object, str, object, bool]]] = []

        @classmethod
        def scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            cls, interval, target, selector, user_info, repeats
        ):
            cls.calls.append((interval, target, selector, user_info, repeats))
            return SimpleNamespace(invalidate=lambda: None)

    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(NSTimer=FakeTimer))
    target = SimpleNamespace(respondsToSelector_=lambda _selector: True)
    controller = _controller_for_refresh(
        module,
        wall_clock=lambda: 500.0,
        monotonic_clock=lambda: 100.0,
    )
    controller.action_target = target

    controller._start_refresh_pulse()

    assert [call[0] for call in FakeTimer.calls] == [60.0]
