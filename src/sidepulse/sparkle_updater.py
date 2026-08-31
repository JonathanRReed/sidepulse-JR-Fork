"""Fail-closed runtime boundary for the embedded Sparkle framework.

Sparkle is not a Python dependency. The packaged application loads its reviewed
embedded framework with ``NSBundle`` and resolves the controller class through
the Objective-C runtime. Source runs and incomplete bundles remain usable, but
do not expose update controls.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .app_bundle import APP_BUNDLE_IDENTIFIER, APP_BUNDLE_NAME

STABLE_CHANNEL = "stable"
BETA_CHANNEL = "beta"
UPDATE_CHANNEL_DEFAULTS_KEY = "SidePulseUpdateChannel"
UPDATE_FEED_URL = (
    "https://github.com/JonathanRReed/sidepulse-JR-Fork/"
    "releases/download/updates/appcast.xml"
)
EXPECTED_PUBLIC_ED_KEY = "IlvZMoPh67naKxN2ZvlnfdHildsgGxPWeEi8IOhVQ+8="
SPARKLE_FRAMEWORK_RELATIVE_PATH = Path(
    "Contents",
    "Frameworks",
    "Sparkle.framework",
)


def _selected_channel(defaults: object) -> str:
    try:
        stored = defaults.stringForKey_(UPDATE_CHANNEL_DEFAULTS_KEY)
    except Exception:
        stored = None
    return BETA_CHANNEL if stored == BETA_CHANNEL else STABLE_CHANNEL


try:
    import objc as _objc
    from Foundation import NSObject, NSSet
except ImportError:  # pragma: no cover - SidePulse ships only on macOS.
    _objc = None
    NSObject = object  # type: ignore[assignment,misc]
    NSSet = None


class SparkleUpdaterDelegate(NSObject):
    """Small retained bridge for Sparkle's optional channel delegate method."""

    if _objc is not None:

        def initWithDefaults_(self, defaults):
            self = _objc.super(SparkleUpdaterDelegate, self).init()
            if self is None:
                return None
            self._sidepulse_defaults = defaults
            return self

    else:  # pragma: no cover - portable import fallback only.

        def __init__(self, defaults) -> None:
            self._sidepulse_defaults = defaults

    def allowedChannelsForUpdater_(self, _updater):
        channel = _selected_channel(self._sidepulse_defaults)
        if NSSet is None:  # pragma: no cover - portable import fallback only.
            return frozenset({BETA_CHANNEL}) if channel == BETA_CHANNEL else frozenset()
        if channel == BETA_CHANNEL:
            return NSSet.setWithObject_(BETA_CHANNEL)
        return NSSet.set()


def _new_delegate(defaults: object) -> SparkleUpdaterDelegate:
    if _objc is None:  # pragma: no cover - portable import fallback only.
        return SparkleUpdaterDelegate(defaults)
    return SparkleUpdaterDelegate.alloc().initWithDefaults_(defaults)


@dataclass(slots=True)
class SparkleUpdater:
    """Retained controller, delegate, and defaults for one updater lifetime."""

    controller: object | None
    delegate: object | None
    defaults: object | None
    unavailable_reason: str | None = None

    @classmethod
    def unavailable(cls, reason: str) -> SparkleUpdater:
        return cls(None, None, None, reason)

    @property
    def available(self) -> bool:
        return self.controller is not None and self.delegate is not None

    @property
    def selected_channel(self) -> str:
        if self.defaults is None:
            return STABLE_CHANNEL
        return _selected_channel(self.defaults)

    def _store_initial_channel(self) -> None:
        if self.defaults is None:
            return
        try:
            stored = self.defaults.stringForKey_(UPDATE_CHANNEL_DEFAULTS_KEY)
            if stored not in {STABLE_CHANNEL, BETA_CHANNEL}:
                self.defaults.setObject_forKey_(
                    STABLE_CHANNEL,
                    UPDATE_CHANNEL_DEFAULTS_KEY,
                )
        except Exception:
            return

    def _core_updater(self):
        if self.controller is None:
            return None
        try:
            return self.controller.updater()
        except Exception:
            return None

    def check_for_updates(self, sender: object = None) -> bool:
        if self.controller is None:
            return False
        try:
            self.controller.checkForUpdates_(sender)
        except Exception:
            return False
        return True

    def select_channel(self, channel: str) -> bool:
        if channel not in {STABLE_CHANNEL, BETA_CHANNEL}:
            raise ValueError(f"unsupported update channel: {channel!r}")
        if not self.available or self.defaults is None:
            return False
        if self.selected_channel == channel:
            return False
        try:
            updater = self._core_updater()
            if updater is None:
                return False
            self.defaults.setObject_forKey_(channel, UPDATE_CHANNEL_DEFAULTS_KEY)
            updater.resetUpdateCycleAfterShortDelay()
        except Exception:
            return False
        return True


def _default_main_bundle():
    from Foundation import NSBundle

    return NSBundle.mainBundle()


def _default_framework_bundle_factory(path: str):
    from Foundation import NSBundle

    return NSBundle.bundleWithPath_(path)


def _default_class_lookup(name: str):
    import objc

    return objc.lookUpClass(name)


def _default_defaults():
    from Foundation import NSUserDefaults

    return NSUserDefaults.standardUserDefaults()


def _default_is_main_thread() -> bool:
    from Foundation import NSThread

    return bool(NSThread.isMainThread())


def _valid_public_key(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return False
    return len(decoded) == 32 and base64.b64encode(decoded).decode("ascii") == value


def _validated_bundle(bundle: object) -> tuple[Path, Mapping[str, object]]:
    path = Path(str(bundle.bundlePath()))
    raw_info = bundle.infoDictionary()
    if not isinstance(raw_info, Mapping):
        raise ValueError("main bundle Info.plist is unavailable")
    info = dict(raw_info)
    if path.name != APP_BUNDLE_NAME:
        raise ValueError("updater requires the packaged SidePulse.app")
    if bundle.bundleIdentifier() != APP_BUNDLE_IDENTIFIER:
        raise ValueError("updater bundle identifier is invalid")
    if info.get("CFBundleIdentifier") != APP_BUNDLE_IDENTIFIER:
        raise ValueError("updater Info.plist bundle identifier is invalid")
    if info.get("SUFeedURL") != UPDATE_FEED_URL:
        raise ValueError("updater feed URL is missing or invalid")
    if (
        not _valid_public_key(info.get("SUPublicEDKey"))
        or info.get("SUPublicEDKey") != EXPECTED_PUBLIC_ED_KEY
    ):
        raise ValueError("updater public EdDSA key is missing or invalid")
    if info.get("SURequireSignedFeed") is not True:
        raise ValueError("signed Sparkle feeds are required")
    if info.get("SUVerifyUpdateBeforeExtraction") is not True:
        raise ValueError("pre-extraction Sparkle verification is required")
    framework_path = path / SPARKLE_FRAMEWORK_RELATIVE_PATH
    if not framework_path.is_dir():
        raise ValueError("embedded Sparkle.framework is missing")
    return framework_path, info


def start_sparkle_updater(
    *,
    bundle: object | None = None,
    framework_bundle_factory: Callable[[str], object] | None = None,
    class_lookup: Callable[[str], object] | None = None,
    defaults: object | None = None,
    is_main_thread: Callable[[], bool] | None = None,
    delegate_factory: Callable[[object], object] | None = None,
) -> SparkleUpdater:
    """Load and start Sparkle only from a complete production app bundle."""
    main_thread_check = is_main_thread or _default_is_main_thread
    if not main_thread_check():
        return SparkleUpdater.unavailable("Sparkle must start on the AppKit main thread")

    try:
        main_bundle = bundle if bundle is not None else _default_main_bundle()
        framework_path, _info = _validated_bundle(main_bundle)
        make_framework_bundle = (
            framework_bundle_factory or _default_framework_bundle_factory
        )
        framework_bundle = make_framework_bundle(str(framework_path))
        if framework_bundle is None or not bool(framework_bundle.load()):
            raise ValueError("embedded Sparkle.framework could not be loaded")

        lookup = class_lookup or _default_class_lookup
        controller_class = lookup("SPUStandardUpdaterController")
        if controller_class is None:
            raise ValueError("SPUStandardUpdaterController is unavailable")

        defaults_store = defaults if defaults is not None else _default_defaults()
        make_delegate = delegate_factory or _new_delegate
        delegate = make_delegate(defaults_store)
        if delegate is None:
            raise ValueError("Sparkle updater delegate could not be created")
        controller = (
            controller_class.alloc()
            .initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
                True,
                delegate,
                None,
            )
        )
        if controller is None:
            raise ValueError("Sparkle updater controller could not be created")
    except Exception as exc:
        return SparkleUpdater.unavailable(str(exc) or type(exc).__name__)

    runtime = SparkleUpdater(controller, delegate, defaults_store)
    runtime._store_initial_channel()
    return runtime


def _default_menu_factory():
    from AppKit import NSMenu

    return NSMenu.alloc().init()


def _default_item_factory(title: str, action: str | None):
    from AppKit import NSMenuItem

    return NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")


def inject_software_update_submenu(
    menu: object,
    target: object,
    runtime: SparkleUpdater | None,
    *,
    menu_factory: Callable[[], object] | None = None,
    item_factory: Callable[[str, str | None], object] | None = None,
):
    """Insert the available updater controls into the final status menu."""
    if runtime is None or not runtime.available:
        return None

    make_menu = menu_factory or _default_menu_factory
    make_item = item_factory or _default_item_factory
    submenu = make_menu()
    submenu.setAutoenablesItems_(False)
    rows = (
        ("Check for Updates...", "checkForSoftwareUpdates:", 0),
        (
            "Stable Updates",
            "selectStableUpdates:",
            int(runtime.selected_channel == STABLE_CHANNEL),
        ),
        (
            "Beta Updates",
            "selectBetaUpdates:",
            int(runtime.selected_channel == BETA_CHANNEL),
        ),
    )
    for title, action, state in rows:
        item = make_item(title, action)
        item.setTarget_(target)
        item.setState_(state)
        submenu.addItem_(item)

    parent = make_item("Software Update", None)
    parent.setSubmenu_(submenu)
    insert_at = menu.numberOfItems()
    for index in range(menu.numberOfItems()):
        try:
            title = str(menu.itemAtIndex_(index).title() or "")
        except Exception:
            continue
        if title == "Software Update":
            return menu.itemAtIndex_(index)
        if title.startswith("Quit "):
            insert_at = index
            break
    menu.insertItem_atIndex_(parent, insert_at)
    return parent


__all__ = [
    "BETA_CHANNEL",
    "EXPECTED_PUBLIC_ED_KEY",
    "SPARKLE_FRAMEWORK_RELATIVE_PATH",
    "STABLE_CHANNEL",
    "UPDATE_CHANNEL_DEFAULTS_KEY",
    "UPDATE_FEED_URL",
    "SparkleUpdater",
    "SparkleUpdaterDelegate",
    "inject_software_update_submenu",
    "start_sparkle_updater",
]
