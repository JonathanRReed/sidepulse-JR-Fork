from __future__ import annotations

import sys
from types import SimpleNamespace

import sidepulse.deck_actions_macos as deck_actions_macos
from sidepulse.deck_actions import DeckAction
from sidepulse.deck_actions_macos import MacDeckActionExecutor


class FakeMacBridge:
    def __init__(self) -> None:
        self.open_result = "opened"
        self.shortcut_result = "sent"
        self.opened: list[str] = []
        self.shortcuts: list[tuple[str, int, tuple[str, ...]]] = []

    def open_app(self, bundle_id: str) -> str:
        self.opened.append(bundle_id)
        return self.open_result

    def post_shortcut(self, bundle_id: str, key_code: int, modifiers: tuple[str, ...]) -> str:
        self.shortcuts.append((bundle_id, key_code, modifiers))
        return self.shortcut_result


def test_open_app_uses_bundle_identifier_boundary() -> None:
    bridge = FakeMacBridge()
    executor = MacDeckActionExecutor(bridge=bridge)

    receipt = executor.execute(DeckAction(kind="open_app", bundle_id="com.apple.Terminal"))

    assert receipt.success is True
    assert receipt.code == "opened"
    assert bridge.opened == ["com.apple.Terminal"]


def test_shortcut_passes_only_explicit_target_and_bounded_chord() -> None:
    bridge = FakeMacBridge()
    executor = MacDeckActionExecutor(bridge=bridge)

    receipt = executor.execute(
        DeckAction(kind="shortcut", bundle_id="com.openai.codex", key_code=45, modifiers=("command", "shift"))
    )

    assert receipt.success is True
    assert receipt.code == "sent"
    assert bridge.shortcuts == [("com.openai.codex", 45, ("command", "shift"))]


def test_native_refusal_code_is_returned_without_claiming_success() -> None:
    bridge = FakeMacBridge()
    bridge.shortcut_result = "target_not_frontmost"

    receipt = MacDeckActionExecutor(bridge=bridge).execute(
        DeckAction(kind="shortcut", bundle_id="com.openai.codex", key_code=45, modifiers=("command",))
    )

    assert receipt.success is False
    assert receipt.code == "target_not_frontmost"


def test_jr_bar_action_invokes_only_its_configured_callback() -> None:
    calls: list[str] = []
    executor = MacDeckActionExecutor(
        bridge=FakeMacBridge(),
        reveal_current_ask=lambda: calls.append("reveal"),
        open_agent_browser=lambda: calls.append("browser"),
        open_usage=lambda: calls.append("usage"),
    )

    receipt = executor.execute(DeckAction(kind="reveal_current_ask"))

    assert receipt.success is True
    assert receipt.code == "revealed_current_ask"
    assert calls == ["reveal"]


def test_missing_or_failing_callback_returns_robust_receipt() -> None:
    missing = MacDeckActionExecutor(bridge=FakeMacBridge()).execute(DeckAction(kind="open_usage"))

    def fail() -> None:
        raise RuntimeError("window unavailable")

    failed = MacDeckActionExecutor(bridge=FakeMacBridge(), open_usage=fail).execute(DeckAction(kind="open_usage"))

    assert missing.success is False
    assert missing.code == "callback_unavailable"
    assert failed.success is False
    assert failed.code == "callback_failed"


def test_native_boundary_exception_is_contained() -> None:
    class BrokenBridge(FakeMacBridge):
        def open_app(self, bundle_id: str) -> str:
            raise RuntimeError("AppKit unavailable")

    receipt = MacDeckActionExecutor(bridge=BrokenBridge()).execute(
        DeckAction(kind="open_app", bundle_id="com.apple.Terminal")
    )

    assert receipt.success is False
    assert receipt.code == "native_error"


class FakeRunningApp:
    def __init__(self, bundle_id: str, pid: int) -> None:
        self._bundle_id = bundle_id
        self._pid = pid

    def bundleIdentifier(self) -> str:
        return self._bundle_id

    def processIdentifier(self) -> int:
        return self._pid


class FakeEvent:
    def __init__(self, key_code: int, key_down: bool) -> None:
        self.key_code = key_code
        self.key_down = key_down
        self.flags = 0

    def setFlags_(self, flags: int) -> None:
        self.flags = flags


def _install_native_fakes(monkeypatch, *, trusted: bool, running: list[FakeRunningApp], frontmost: FakeRunningApp):
    posted: list[tuple[int, FakeEvent]] = []
    workspace = SimpleNamespace(frontmostApplication=lambda: frontmost)
    appkit = SimpleNamespace(
        NSRunningApplication=SimpleNamespace(runningApplicationsWithBundleIdentifier_=lambda bundle_id: running),
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
    )
    quartz = SimpleNamespace(
        AXIsProcessTrusted=lambda: trusted,
        CGEventCreateKeyboardEvent=lambda source, key_code, key_down: FakeEvent(key_code, key_down),
        CGEventSetFlags=lambda event, flags: event.setFlags_(flags),
        CGEventPostToPid=lambda pid, event: posted.append((pid, event)),
        kCGEventFlagMaskAlternate=1,
        kCGEventFlagMaskCommand=2,
        kCGEventFlagMaskControl=4,
        kCGEventFlagMaskShift=8,
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setattr(deck_actions_macos, "_is_accessibility_trusted", lambda: trusted)
    return posted


def test_native_shortcut_posts_paired_flagged_events_only_to_checked_frontmost_pid(monkeypatch) -> None:
    target = FakeRunningApp("com.openai.codex", 712)
    posted = _install_native_fakes(monkeypatch, trusted=True, running=[target], frontmost=target)

    receipt = MacDeckActionExecutor().execute(
        DeckAction(kind="shortcut", bundle_id="com.openai.codex", key_code=45, modifiers=("command", "shift"))
    )

    assert receipt == type(receipt)(code="sent", success=True)
    assert [(pid, event.key_code, event.key_down, event.flags) for pid, event in posted] == [
        (712, 45, True, 10),
        (712, 45, False, 10),
    ]


def test_native_shortcut_never_posts_when_accessibility_is_untrusted(monkeypatch) -> None:
    target = FakeRunningApp("com.openai.codex", 712)
    posted = _install_native_fakes(monkeypatch, trusted=False, running=[target], frontmost=target)

    receipt = MacDeckActionExecutor().execute(
        DeckAction(kind="shortcut", bundle_id="com.openai.codex", key_code=45, modifiers=("command",))
    )

    assert receipt.code == "accessibility_not_trusted"
    assert receipt.success is False
    assert posted == []


def test_native_shortcut_never_retargets_to_a_different_frontmost_app(monkeypatch) -> None:
    target = FakeRunningApp("com.openai.codex", 712)
    other = FakeRunningApp("com.apple.Terminal", 900)
    posted = _install_native_fakes(monkeypatch, trusted=True, running=[target], frontmost=other)

    receipt = MacDeckActionExecutor().execute(
        DeckAction(kind="shortcut", bundle_id="com.openai.codex", key_code=45, modifiers=("command",))
    )

    assert receipt.code == "target_not_frontmost"
    assert receipt.success is False
    assert posted == []


def test_native_shortcut_treats_missing_frontmost_app_as_a_refusal(monkeypatch) -> None:
    target = FakeRunningApp("com.openai.codex", 712)
    posted = _install_native_fakes(monkeypatch, trusted=True, running=[target], frontmost=None)

    receipt = MacDeckActionExecutor().execute(
        DeckAction(kind="shortcut", bundle_id="com.openai.codex", key_code=45, modifiers=("command",))
    )

    assert receipt.code == "target_not_frontmost"
    assert receipt.success is False
    assert posted == []


def test_native_shortcut_uses_quartz_flag_function_and_never_real_post(monkeypatch) -> None:
    import Quartz

    target = FakeRunningApp("com.openai.codex", 712)
    posted: list[tuple[int, object]] = []
    workspace = SimpleNamespace(frontmostApplication=lambda: target)
    appkit = SimpleNamespace(
        NSRunningApplication=SimpleNamespace(runningApplicationsWithBundleIdentifier_=lambda bundle_id: [target]),
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setattr(deck_actions_macos, "_is_accessibility_trusted", lambda: True)
    monkeypatch.setattr(Quartz, "CGEventPostToPid", lambda pid, event: posted.append((pid, event)))

    receipt = MacDeckActionExecutor().execute(
        DeckAction(kind="shortcut", bundle_id="com.openai.codex", key_code=45, modifiers=("command", "shift"))
    )

    expected_flags = Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskShift
    assert receipt.code == "sent"
    assert [(pid, Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)) for pid, event in posted] == [
        (712, 45),
        (712, 45),
    ]
    assert [Quartz.CGEventGetFlags(event) for _, event in posted] == [expected_flags, expected_flags]


def test_native_shortcut_rechecks_focus_after_event_construction(monkeypatch) -> None:
    target = FakeRunningApp("com.openai.codex", 712)
    other = FakeRunningApp("com.apple.Terminal", 900)
    frontmost_apps = iter((target, other))
    posted: list[tuple[int, FakeEvent]] = []
    workspace = SimpleNamespace(frontmostApplication=lambda: next(frontmost_apps))
    appkit = SimpleNamespace(
        NSRunningApplication=SimpleNamespace(runningApplicationsWithBundleIdentifier_=lambda bundle_id: [target]),
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
    )
    quartz = SimpleNamespace(
        AXIsProcessTrusted=lambda: True,
        CGEventCreateKeyboardEvent=lambda source, key_code, key_down: FakeEvent(key_code, key_down),
        CGEventSetFlags=lambda event, flags: event.setFlags_(flags),
        CGEventPostToPid=lambda pid, event: posted.append((pid, event)),
        kCGEventFlagMaskAlternate=1,
        kCGEventFlagMaskCommand=2,
        kCGEventFlagMaskControl=4,
        kCGEventFlagMaskShift=8,
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    receipt = MacDeckActionExecutor().execute(
        DeckAction(kind="shortcut", bundle_id="com.openai.codex", key_code=45, modifiers=("command",))
    )

    assert receipt.code == "target_not_frontmost"
    assert receipt.success is False
    assert posted == []


def test_native_shortcut_attempts_key_up_when_key_down_post_raises(monkeypatch) -> None:
    target = FakeRunningApp("com.openai.codex", 712)
    posted: list[tuple[int, FakeEvent]] = []
    workspace = SimpleNamespace(frontmostApplication=lambda: target)

    def post(pid: int, event: FakeEvent) -> None:
        posted.append((pid, event))
        if event.key_down:
            raise RuntimeError("post outcome unavailable")

    appkit = SimpleNamespace(
        NSRunningApplication=SimpleNamespace(runningApplicationsWithBundleIdentifier_=lambda bundle_id: [target]),
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
    )
    quartz = SimpleNamespace(
        CGEventCreateKeyboardEvent=lambda source, key_code, key_down: FakeEvent(key_code, key_down),
        CGEventSetFlags=lambda event, flags: event.setFlags_(flags),
        CGEventPostToPid=post,
        kCGEventFlagMaskAlternate=1,
        kCGEventFlagMaskCommand=2,
        kCGEventFlagMaskControl=4,
        kCGEventFlagMaskShift=8,
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setattr(deck_actions_macos, "_is_accessibility_trusted", lambda: True)

    receipt = MacDeckActionExecutor().execute(
        DeckAction(kind="shortcut", bundle_id="com.openai.codex", key_code=45, modifiers=("command",))
    )

    assert receipt.code == "native_error"
    assert receipt.success is False
    assert [(pid, event.key_down) for pid, event in posted] == [(712, True), (712, False)]
