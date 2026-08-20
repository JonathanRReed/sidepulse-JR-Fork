"""The adversarial interaction sweep: every click must do something.

Walks the fully built dropdown (root and every submenu, in several fleet
states) and asserts that every enabled item carrying an action resolves to
a selector the controller actually implements. "Open Agent Browser..."
shipped dead once -- a stale generation fence swallowed the click -- and
nothing noticed, because nothing walked the menu asking "would this click
even dispatch?".
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from test_sidepulse import isolate_controller

from sidepulse.models import AgentMode, AgentStatus


def _status(provider, sid, mode, event="PreToolUse"):
    return AgentStatus(
        provider=provider,
        agent_id=f"{provider}:session:{sid}",
        display_name=f"{provider} {sid}",
        mode=mode,
        updated_at=datetime.now(timezone.utc),
        event_name=event,
        session_id=sid,
    )


_FLEETS = {
    "idle": (),
    "working": (
        _status("claude", "a", AgentMode.WORKING),
        _status("grok", "b", AgentMode.WORKING),
    ),
    "ask": (
        _status("codex", "ask", AgentMode.WAITING_FOR_INPUT, "PermissionRequest"),
        _status("claude", "w", AgentMode.WORKING),
    ),
    "mixed": (
        _status("claude", "done", AgentMode.COMPLETED, "Stop"),
        _status("grok", "fail", AgentMode.BLOCKED_ERROR, "StopFailure"),
        _status("codex", "w", AgentMode.WORKING),
    ),
}


class _Walker:
    def __init__(self, controller):
        self.controller = controller
        self.dead: list[str] = []
        self.seen = 0

    def walk(self, menu, path=""):
        for index in range(menu.numberOfItems()):
            item = menu.itemAtIndex_(index)
            title = str(item.title() or "").strip()
            where = f"{path}/{title or '<untitled>'}"
            submenu = item.submenu()
            if submenu is not None:
                # A submenu owner's action is AppKit's internal
                # submenuAction:; the clickable surface is its children.
                self.walk(submenu, where)
                continue
            action = item.action()
            if action is None or item.isSeparatorItem():
                continue
            if not item.isEnabled():
                continue
            self.seen += 1
            selector = str(action)
            target = item.target()
            responder = target if target is not None else self.controller
            method = selector.replace(":", "_")
            if not (
                callable(getattr(responder, method, None))
                or responder.respondsToSelector_(action)
            ):
                self.dead.append(f"{where} -> {selector}")


@pytest.mark.parametrize("fleet_name", sorted(_FLEETS))
def test_every_enabled_menu_action_resolves(request, fleet_name):
    case = SimpleNamespace(
        addCleanup=lambda fn, *a, **k: request.addfinalizer(lambda: fn(*a, **k)),
    )
    isolate_controller(case)
    controller = case.controller
    status_bar = case.status_bar
    statuses = _FLEETS[fleet_name]
    snapshot = SimpleNamespace(
        statuses=statuses,
        stale_statuses=(),
        collected_at=datetime.now(timezone.utc),
    )
    controller.last_snapshot = snapshot
    controller.update_attention_projection(snapshot)
    menu = status_bar.build_menu(
        snapshot,
        status_bar.STATE_WORKING if statuses else status_bar.STATE_IDLE,
        controller,
    )
    walker = _Walker(controller)
    walker.walk(menu)
    assert walker.seen > 0
    assert walker.dead == [], (
        f"{len(walker.dead)} dead menu actions in fleet {fleet_name!r}:\n"
        + "\n".join(walker.dead)
    )


def test_open_agent_browser_survives_stale_menus_and_missing_payloads(request):
    """The click that shipped dead: a payload from a stale menu (old
    generation), or no payload at all, must still open the browser."""
    from test_sidepulse import CanonicalAgentBrowserIntegrationTests

    case = SimpleNamespace(
        addCleanup=lambda fn, *a, **k: request.addfinalizer(lambda: fn(*a, **k)),
    )
    isolate_controller(case)
    controller = case.controller
    snapshot = CanonicalAgentBrowserIntegrationTests._canonical_snapshot(2)
    controller.last_snapshot = snapshot

    from sidepulse.agent_browser_window import AgentBrowserOpenPayload

    stale = SimpleNamespace(
        representedObject=lambda: AgentBrowserOpenPayload(999_999, None, None)
    )
    assert controller.openAgentBrowser_(stale) is True
    assert controller.agent_browser_controller is not None

    unpayloaded = SimpleNamespace(representedObject=lambda: None)
    assert controller.openAgentBrowser_(unpayloaded) is True


def test_sampler_serves_frames_from_one_batched_engine_call() -> None:
    """Wave 1: the bar's sampler must amortize JavaScriptCore round-trips
    by prefetching frame batches, with byte-identical output."""
    from sidepulse.led_wasm import LedWasmUnavailableError, SdLedWasmController
    from sidepulse.screen_bar_pipeline import ScreenBarSampler, TwoSampleBuffer

    try:
        raw = SdLedWasmController(led_count=8)
    except LedWasmUnavailableError:
        import pytest

        pytest.skip("JavaScriptCore unavailable")

    calls = {"step": 0, "batch": 0}

    class Counting:
        def parse(self, program, now_ms):
            return raw.parse(program, now_ms)

        def step(self, now_ms):
            calls["step"] += 1
            return raw.step(now_ms)

        def step_batch(self, start_ms, interval_ms, frames):
            calls["batch"] += 1
            return raw.step_batch(start_ms, interval_ms, frames)

    sampler = ScreenBarSampler(
        TwoSampleBuffer(), controller_factory=Counting, led_count=8
    )
    program = (
        "0:#6C3C2C 1600ms pulse 0ms; 1:#47474A 1600ms pulse 192ms; "
        "2:#6C3C2C 1600ms pulse 384ms; 3:#47474A 1600ms pulse 576ms; "
        "4:#6C3C2C 1600ms pulse 768ms; 5:#47474A 1600ms pulse 960ms; "
        "6:#6C3C2C 1600ms pulse 1152ms; 7:#47474A 1600ms pulse 1344ms\n"
        "repeat"
    )
    assert raw.parse(program, 1000).ok
    # The production cadence: GENTLE_MOTION_FPS accumulates a FLOAT
    # interval (1/30s), while batch stamps once stepped by the rounded
    # integer millisecond interval -- a ~1/3ms-per-frame drift that blew
    # the +/-1ms gate and silently discarded most of every batch.
    interval = 1.0 / 30.0
    sampled_at = 1.0
    pixels = sampler._pixels_for(Counting(), sampled_at, interval)
    assert pixels is not None and len(pixels) == 8
    # 23 more frames ride the same engine call.
    for _ in range(1, 24):
        sampled_at += interval
        served = sampler._pixels_for(Counting(), sampled_at, interval)
        assert served is not None
    assert calls["batch"] == 1, "a served batch must not be re-rendered"
    assert calls["step"] == 0
    sampler.close(timeout_seconds=2.0)


def test_every_hook_provider_has_install_and_uninstall_actions(request):
    """Settings and Setup build install/uninstall buttons for EVERY entry
    in HOOK_PROVIDERS via f"install{Provider}Hooks:" -- a provider added
    without its controller IBActions ships dead buttons (opencode,
    antigravity, and kiro did exactly that)."""
    case = SimpleNamespace(
        addCleanup=lambda fn, *a, **k: request.addfinalizer(lambda: fn(*a, **k)),
    )
    isolate_controller(case)
    controller = case.controller

    from sidepulse.providers import HOOK_PROVIDERS

    dead: list[str] = []
    for provider in HOOK_PROVIDERS:
        for prefix in ("install", "uninstall"):
            method = f"{prefix}{provider.title()}Hooks_"
            if not callable(getattr(controller, method, None)):
                dead.append(method)
    assert dead == [], f"hook buttons with no action: {dead}"
