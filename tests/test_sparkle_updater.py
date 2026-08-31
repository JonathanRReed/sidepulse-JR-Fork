from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from sidepulse import sparkle_updater


def test_sparkle_runtime_boundary_exists() -> None:
    assert importlib.util.find_spec("sidepulse.sparkle_updater") is not None


def test_sparkle_runtime_boundary_exposes_the_reviewed_contract() -> None:
    assert all(
        hasattr(sparkle_updater, name)
        for name in (
            "BETA_CHANNEL",
            "STABLE_CHANNEL",
            "SparkleUpdater",
            "start_sparkle_updater",
            "inject_software_update_submenu",
        )
    )


VALID_PUBLIC_KEY = sparkle_updater.EXPECTED_PUBLIC_ED_KEY


@dataclass
class _FakeMainBundle:
    path: Path
    info: dict[str, object]

    def bundlePath(self) -> str:
        return str(self.path)

    def bundleIdentifier(self) -> str | None:
        value = self.info.get("CFBundleIdentifier")
        return value if isinstance(value, str) else None

    def infoDictionary(self) -> dict[str, object]:
        return dict(self.info)


class _FakeFrameworkBundle:
    def __init__(self, load_result: bool = True) -> None:
        self.load_result = load_result
        self.load_calls = 0

    def load(self) -> bool:
        self.load_calls += 1
        return self.load_result


class _FakeDefaults:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = dict(values or {})
        self.writes: list[tuple[object, str]] = []

    def stringForKey_(self, key: str) -> str | None:
        value = self.values.get(key)
        return value if isinstance(value, str) else None

    def setObject_forKey_(self, value: object, key: str) -> None:
        self.values[key] = value
        self.writes.append((value, key))


class _FakeSparkleCoreUpdater:
    def __init__(self) -> None:
        self.reset_calls = 0
        self.background_check_calls = 0

    def resetUpdateCycleAfterShortDelay(self) -> None:
        self.reset_calls += 1

    def checkForUpdatesInBackground(self) -> None:
        self.background_check_calls += 1


def _controller_class():
    class FakeController:
        instances: ClassVar[list[FakeController]] = []

        @classmethod
        def alloc(cls):
            instance = cls()
            cls.instances.append(instance)
            return instance

        def __init__(self) -> None:
            self.core_updater = _FakeSparkleCoreUpdater()
            self.initialization: tuple[bool, object, object] | None = None
            self.manual_check_senders: list[object] = []

        def initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
            self,
            starting: bool,
            updater_delegate: object,
            user_driver_delegate: object,
        ):
            self.initialization = (
                starting,
                updater_delegate,
                user_driver_delegate,
            )
            return self

        def updater(self) -> _FakeSparkleCoreUpdater:
            return self.core_updater

        def checkForUpdates_(self, sender: object) -> None:
            self.manual_check_senders.append(sender)

    return FakeController


def _valid_info() -> dict[str, object]:
    return {
        "CFBundleIdentifier": "io.sidepulse.app",
        "SUFeedURL": sparkle_updater.UPDATE_FEED_URL,
        "SUPublicEDKey": VALID_PUBLIC_KEY,
        "SURequireSignedFeed": True,
        "SUVerifyUpdateBeforeExtraction": True,
    }


def _valid_bundle(tmp_path: Path) -> _FakeMainBundle:
    path = tmp_path / "SidePulse.app"
    (path / "Contents" / "Frameworks" / "Sparkle.framework").mkdir(
        parents=True
    )
    return _FakeMainBundle(path, _valid_info())


def _start_valid_runtime(
    tmp_path: Path,
    *,
    defaults: _FakeDefaults | None = None,
):
    bundle = _valid_bundle(tmp_path)
    framework_bundle = _FakeFrameworkBundle()
    framework_paths: list[str] = []
    looked_up: list[str] = []
    controller_class = _controller_class()

    def framework_bundle_factory(path: str):
        framework_paths.append(path)
        return framework_bundle

    def class_lookup(name: str):
        looked_up.append(name)
        return controller_class

    runtime = sparkle_updater.start_sparkle_updater(
        bundle=bundle,
        framework_bundle_factory=framework_bundle_factory,
        class_lookup=class_lookup,
        defaults=defaults or _FakeDefaults(),
        is_main_thread=lambda: True,
    )
    return (
        runtime,
        framework_bundle,
        framework_paths,
        looked_up,
        controller_class,
    )


def test_source_run_fails_closed_before_attempting_to_load_sparkle(
    tmp_path: Path,
) -> None:
    source_bundle = _FakeMainBundle(tmp_path / "python", {})
    load_attempts: list[str] = []

    runtime = sparkle_updater.start_sparkle_updater(
        bundle=source_bundle,
        framework_bundle_factory=lambda path: load_attempts.append(path),
        class_lookup=lambda _name: pytest.fail("source run looked up Sparkle"),
        defaults=_FakeDefaults(),
        is_main_thread=lambda: True,
    )

    assert runtime.available is False
    assert runtime.controller is None
    assert runtime.delegate is None
    assert load_attempts == []


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("SUFeedURL", None),
        ("SUFeedURL", "http://updates.invalid/appcast.xml"),
        ("SUPublicEDKey", None),
        ("SUPublicEDKey", "not-a-32-byte-base64-key"),
        ("SUPublicEDKey", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="),
        ("SURequireSignedFeed", False),
        ("SUVerifyUpdateBeforeExtraction", False),
    ),
)
def test_malformed_update_metadata_fails_before_framework_load(
    tmp_path: Path,
    key: str,
    value: object,
) -> None:
    bundle = _valid_bundle(tmp_path)
    if value is None:
        bundle.info.pop(key)
    else:
        bundle.info[key] = value
    load_attempts: list[str] = []

    runtime = sparkle_updater.start_sparkle_updater(
        bundle=bundle,
        framework_bundle_factory=lambda path: load_attempts.append(path),
        class_lookup=lambda _name: pytest.fail("malformed bundle looked up Sparkle"),
        defaults=_FakeDefaults(),
        is_main_thread=lambda: True,
    )

    assert runtime.available is False
    assert load_attempts == []


def test_updater_starts_on_the_main_thread_from_the_exact_embedded_framework(
    tmp_path: Path,
) -> None:
    runtime, framework, paths, lookups, controller_class = _start_valid_runtime(
        tmp_path
    )

    assert runtime.available is True
    assert paths == [
        str(
            tmp_path
            / "SidePulse.app"
            / "Contents"
            / "Frameworks"
            / "Sparkle.framework"
        )
    ]
    assert framework.load_calls == 1
    assert lookups == ["SPUStandardUpdaterController"]
    assert len(controller_class.instances) == 1
    assert runtime.controller is controller_class.instances[0]
    assert runtime.delegate is not None
    assert runtime.controller.initialization == (True, runtime.delegate, None)


def test_off_main_thread_start_is_unavailable_without_loading_framework(
    tmp_path: Path,
) -> None:
    bundle = _valid_bundle(tmp_path)
    load_attempts: list[str] = []

    runtime = sparkle_updater.start_sparkle_updater(
        bundle=bundle,
        framework_bundle_factory=lambda path: load_attempts.append(path),
        class_lookup=lambda _name: pytest.fail("off-main start looked up Sparkle"),
        defaults=_FakeDefaults(),
        is_main_thread=lambda: False,
    )

    assert runtime.available is False
    assert load_attempts == []


def test_framework_load_failure_remains_unavailable_and_skips_class_lookup(
    tmp_path: Path,
) -> None:
    bundle = _valid_bundle(tmp_path)
    framework = _FakeFrameworkBundle(load_result=False)
    lookup_calls: list[str] = []

    runtime = sparkle_updater.start_sparkle_updater(
        bundle=bundle,
        framework_bundle_factory=lambda _path: framework,
        class_lookup=lambda name: lookup_calls.append(name),
        defaults=_FakeDefaults(),
        is_main_thread=lambda: True,
    )

    assert runtime.available is False
    assert framework.load_calls == 1
    assert lookup_calls == []


def test_channel_delegate_and_actions_preserve_sparkles_update_scheduler(
    tmp_path: Path,
) -> None:
    defaults = _FakeDefaults()
    runtime, _framework, _paths, _lookups, _controller_class = (
        _start_valid_runtime(tmp_path, defaults=defaults)
    )
    controller = runtime.controller
    core_updater = controller.updater()

    assert runtime.selected_channel == sparkle_updater.STABLE_CHANNEL
    assert set(runtime.delegate.allowedChannelsForUpdater_(core_updater)) == set()
    assert defaults.writes == [
        (
            sparkle_updater.STABLE_CHANNEL,
            sparkle_updater.UPDATE_CHANNEL_DEFAULTS_KEY,
        )
    ]

    assert runtime.select_channel(sparkle_updater.BETA_CHANNEL) is True
    assert set(runtime.delegate.allowedChannelsForUpdater_(core_updater)) == {
        sparkle_updater.BETA_CHANNEL
    }
    assert core_updater.reset_calls == 1
    assert runtime.select_channel(sparkle_updater.BETA_CHANNEL) is False
    assert core_updater.reset_calls == 1

    sender = object()
    assert runtime.check_for_updates(sender) is True
    assert controller.manual_check_senders == [sender]
    assert core_updater.background_check_calls == 0
    assert all(key != "SUEnableAutomaticChecks" for _value, key in defaults.writes)

    assert runtime.select_channel(sparkle_updater.STABLE_CHANNEL) is True
    assert set(runtime.delegate.allowedChannelsForUpdater_(core_updater)) == set()
    assert core_updater.reset_calls == 2
    assert core_updater.background_check_calls == 0


def test_channel_choice_is_not_persisted_when_core_updater_is_unavailable() -> None:
    defaults = _FakeDefaults(
        {sparkle_updater.UPDATE_CHANNEL_DEFAULTS_KEY: sparkle_updater.STABLE_CHANNEL}
    )

    class ControllerWithoutUpdater:
        def updater(self):
            return None

    runtime = sparkle_updater.SparkleUpdater(
        ControllerWithoutUpdater(),
        object(),
        defaults,
    )

    assert runtime.select_channel(sparkle_updater.BETA_CHANNEL) is False
    assert runtime.selected_channel == sparkle_updater.STABLE_CHANNEL
    assert defaults.writes == []


class _FakeMenuItem:
    def __init__(self, title: str, action: str | None) -> None:
        self._title = title
        self._action = action
        self._target = None
        self._submenu = None
        self._state = 0

    def title(self) -> str:
        return self._title

    def action(self) -> str | None:
        return self._action

    def setTarget_(self, target: object) -> None:
        self._target = target

    def target(self):
        return self._target

    def setSubmenu_(self, submenu) -> None:
        self._submenu = submenu

    def submenu(self):
        return self._submenu

    def setState_(self, state: int) -> None:
        self._state = state

    def state(self) -> int:
        return self._state


class _FakeMenu:
    def __init__(self, items: list[_FakeMenuItem] | None = None) -> None:
        self.items = list(items or [])
        self.autoenables = True

    def numberOfItems(self) -> int:
        return len(self.items)

    def itemAtIndex_(self, index: int) -> _FakeMenuItem:
        return self.items[index]

    def addItem_(self, item: _FakeMenuItem) -> None:
        self.items.append(item)

    def insertItem_atIndex_(self, item: _FakeMenuItem, index: int) -> None:
        self.items.insert(index, item)

    def setAutoenablesItems_(self, value: bool) -> None:
        self.autoenables = value


def _item_factory(title: str, action: str | None) -> _FakeMenuItem:
    return _FakeMenuItem(title, action)


def test_available_runtime_injects_exact_update_submenu_before_quit(
    tmp_path: Path,
) -> None:
    runtime, *_rest = _start_valid_runtime(tmp_path)
    target = object()
    menu = _FakeMenu(
        [
            _FakeMenuItem("Devices", None),
            _FakeMenuItem("Quit JR Bar", "quit:"),
        ]
    )

    parent = sparkle_updater.inject_software_update_submenu(
        menu,
        target,
        runtime,
        menu_factory=_FakeMenu,
        item_factory=_item_factory,
    )

    assert parent is menu.itemAtIndex_(1)
    assert [item.title() for item in menu.items] == [
        "Devices",
        "Software Update",
        "Quit JR Bar",
    ]
    submenu = parent.submenu()
    assert submenu.autoenables is False
    assert [item.title() for item in submenu.items] == [
        "Check for Updates...",
        "Stable Updates",
        "Beta Updates",
    ]
    assert [item.action() for item in submenu.items] == [
        "checkForSoftwareUpdates:",
        "selectStableUpdates:",
        "selectBetaUpdates:",
    ]
    assert [item.target() for item in submenu.items] == [target, target, target]
    assert [item.state() for item in submenu.items] == [0, 1, 0]


def test_unavailable_runtime_does_not_add_an_update_menu() -> None:
    runtime = sparkle_updater.SparkleUpdater.unavailable("source run")
    menu = _FakeMenu([_FakeMenuItem("Quit JR Bar", "quit:")])

    parent = sparkle_updater.inject_software_update_submenu(
        menu,
        object(),
        runtime,
        menu_factory=_FakeMenu,
        item_factory=_item_factory,
    )

    assert parent is None
    assert [item.title() for item in menu.items] == ["Quit JR Bar"]


def test_final_status_menu_includes_the_available_update_submenu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from AppKit import NSMenu, NSMenuItem

    from sidepulse import provider_usage_status_bar as provider_host

    runtime, *_rest = _start_valid_runtime(tmp_path)
    target = provider_host.JRProviderUsageStatusBarController.alloc()
    target._sidepulse_sparkle_updater = runtime

    def base_menu(_snapshot, _state, _target):
        menu = NSMenu.alloc().init()
        menu.addItem_(
            NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Devices",
                None,
                "",
            )
        )
        menu.addItem_(
            NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit JR Bar",
                "quit:",
                "q",
            )
        )
        return menu

    monkeypatch.setattr(provider_host, "_original_build_menu", base_menu)
    monkeypatch.setattr(
        provider_host,
        "native_usage_menu_item",
        lambda _target: NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Usage",
            None,
            "",
        ),
    )

    menu = provider_host.build_menu(None, None, target)
    items = [menu.itemAtIndex_(index) for index in range(menu.numberOfItems())]
    update_item = next(
        (item for item in items if str(item.title() or "") == "Software Update"),
        None,
    )

    assert update_item is not None
    submenu = update_item.submenu()
    assert [
        str(submenu.itemAtIndex_(index).title() or "")
        for index in range(submenu.numberOfItems())
    ] == ["Check for Updates...", "Stable Updates", "Beta Updates"]


def test_final_controller_starts_updater_before_base_launch_and_first_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sidepulse import provider_usage_status_bar as provider_host
    from sidepulse import provider_usage_store

    events: list[tuple[str, object]] = []
    runtime = object()

    class FakeBase:
        @staticmethod
        def applicationDidFinishLaunching_(target, _notification):
            events.append(("base", target._sidepulse_sparkle_updater))
            return "launched"

    class SparkleLaunchTarget(provider_host.JRProviderUsageStatusBarController):
        def _request_provider_usage(self, *, force: bool) -> None:
            events.append(("usage", force))

    monkeypatch.setattr(provider_host, "_BaseStatusBarController", FakeBase)
    monkeypatch.setattr(
        provider_host,
        "start_sparkle_updater",
        lambda: events.append(("sparkle", runtime)) or runtime,
    )
    monkeypatch.setattr(provider_host, "load_seen_reset_events", lambda: ())
    monkeypatch.setattr(provider_usage_store, "load_provider_usage_state", lambda: None)

    target = SparkleLaunchTarget.alloc()
    result = provider_host.JRProviderUsageStatusBarController.applicationDidFinishLaunching_(
        target,
        None,
    )

    assert result == "launched"
    assert events[:2] == [("sparkle", runtime), ("base", runtime)]
    assert target._sidepulse_sparkle_updater is runtime


def test_final_controller_selectors_delegate_without_forcing_a_check() -> None:
    from sidepulse.provider_usage_status_bar import JRProviderUsageStatusBarController

    class Runtime:
        def __init__(self) -> None:
            self.checks: list[object] = []
            self.channels: list[str] = []

        def check_for_updates(self, sender: object) -> bool:
            self.checks.append(sender)
            return True

        def select_channel(self, channel: str) -> bool:
            self.channels.append(channel)
            return True

    runtime = Runtime()
    target = JRProviderUsageStatusBarController.alloc()
    target._sidepulse_sparkle_updater = runtime
    target._menu_signature = "rendered"
    sender = object()

    JRProviderUsageStatusBarController.checkForSoftwareUpdates_(target, sender)
    JRProviderUsageStatusBarController.selectBetaUpdates_(target, sender)
    JRProviderUsageStatusBarController.selectStableUpdates_(target, sender)

    assert runtime.checks == [sender]
    assert runtime.channels == [
        sparkle_updater.BETA_CHANNEL,
        sparkle_updater.STABLE_CHANNEL,
    ]
    assert target._menu_signature is None
