"""Final native-provider wrapper around SidePulse's retained AppKit host."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from . import status_bar as _host
from .provider_credential_store import ProviderCredentialStore
from .provider_usage_event_store import (
    load_seen_reset_events,
    save_seen_reset_events,
)
from .provider_usage_menu import project_usage_menu
from .provider_usage_qol import detect_reset_events, threshold_crossings
from .provider_usage_runtime import ProviderUsageService, ProviderUsageState
from .provider_usage_settings import load_provider_usage_settings
from .provider_usage_store import load_provider_usage_state, save_provider_usage_state

_legacy = getattr(_host, "_legacy", _host)
_BaseStatusBarController = _legacy.StatusBarController
_original_build_menu = _legacy.build_menu


def _disabled_item(title: str):
    item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title,
        None,
        "",
    )
    item.setEnabled_(False)
    return item


def _menu_index(menu, title: str) -> int:
    for index in range(menu.numberOfItems()):
        item = menu.itemAtIndex_(index)
        if str(item.title() or "") == title:
            return index
    return -1


def _remove_legacy_usage_item(menu, target) -> None:
    item = getattr(target, "_usage_menu_item", None)
    if item is None:
        return
    try:
        index = menu.indexOfItem_(item)
    except Exception:
        index = -1
    if index >= 0:
        menu.removeItemAtIndex_(index)
    target._usage_menu_item = None
    target._usage_menu_view = None


def _native_usage_menu_item(target):
    state = getattr(
        target,
        "_sidepulse_provider_usage_state",
        ProviderUsageState((), None, None, False),
    )
    projection = project_usage_menu(state, now=time.time())
    item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        projection.title,
        None,
        "",
    )
    submenu = _legacy.NSMenu.alloc().init()
    submenu.setAutoenablesItems_(False)
    if not projection.rows:
        submenu.addItem_(
            _disabled_item("Run `sidepulse providers refresh` to collect usage")
        )
    for row in projection.rows:
        provider_item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            row.title,
            None,
            "",
        )
        provider_menu = _legacy.NSMenu.alloc().init()
        provider_menu.setAutoenablesItems_(False)
        if row.detail:
            provider_menu.addItem_(_disabled_item(row.detail))
        if row.usage_detail:
            provider_menu.addItem_(_disabled_item(row.usage_detail))
        if row.action_label:
            provider_menu.addItem_(_legacy.NSMenuItem.separatorItem())
            action = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                row.action_label,
                "performProviderUsageAction:",
                "",
            )
            action.setTarget_(target)
            action.setRepresentedObject_(
                {"provider_id": row.provider_id, "action": row.action_label}
            )
            provider_menu.addItem_(action)
        provider_item.setSubmenu_(provider_menu)
        submenu.addItem_(provider_item)
    submenu.addItem_(_legacy.NSMenuItem.separatorItem())
    open_center = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Open Usage Center…",
        "openProviderUsageCenter:",
        "",
    )
    open_center.setTarget_(target)
    submenu.addItem_(open_center)
    refresh = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Refresh Usage",
        "refreshProviderUsage:",
        "",
    )
    refresh.setTarget_(target)
    submenu.addItem_(refresh)
    item.setSubmenu_(submenu)
    target._sidepulse_provider_usage_menu_item = item
    return item


def build_menu(snapshot, state, target):
    menu = _original_build_menu(snapshot, state, target)
    _remove_legacy_usage_item(menu, target)
    native_item = _native_usage_menu_item(target)
    index = _menu_index(menu, "Devices")
    if index < 0:
        index = _menu_index(menu, "Profiles")
    if index < 0:
        index = min(4, menu.numberOfItems())
    menu.insertItem_atIndex_(native_item, index)
    if index + 1 < menu.numberOfItems():
        next_item = menu.itemAtIndex_(index + 1)
        if not next_item.isSeparatorItem():
            menu.insertItem_atIndex_(_legacy.NSMenuItem.separatorItem(), index + 1)
    return menu


if _BaseStatusBarController.__name__ == "JRProviderUsageStatusBarController":
    JRProviderUsageStatusBarController = _BaseStatusBarController
else:

    class JRProviderUsageStatusBarController(_BaseStatusBarController):
        """Native usage accounting, compact menu, and finite reset cues."""

        def _provider_usage_service(self) -> ProviderUsageService:
            service = getattr(self, "_sidepulse_provider_usage_service", None)
            if type(service) is ProviderUsageService:
                return service
            service = ProviderUsageService(
                settings_loader=load_provider_usage_settings,
                credentials=ProviderCredentialStore(),
                home=Path.home(),
                state_loader=load_provider_usage_state,
                state_saver=save_provider_usage_state,
            )
            self._sidepulse_provider_usage_service = service
            self._sidepulse_provider_usage_state = service.snapshot()
            return service

        def _request_provider_usage(self, *, force: bool = False) -> None:
            service = self._provider_usage_service()
            current = service.request(
                callback=self._provider_usage_ready,
                force=force,
            )
            self._sidepulse_provider_usage_state = current

        def _provider_usage_ready(self, state: ProviderUsageState) -> None:
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyProviderUsageState:",
                    state,
                    False,
                )
            except Exception:
                return

        @_legacy.objc.IBAction
        def applyProviderUsageState_(self, state) -> None:
            if type(state) is not ProviderUsageState:
                return
            previous_state = getattr(
                self,
                "_sidepulse_provider_usage_state",
                ProviderUsageState((), None, None, False),
            )
            self._sidepulse_provider_usage_state = state
            seen = set(getattr(self, "_sidepulse_seen_reset_events", ()))
            reset_events = detect_reset_events(
                previous_state.snapshots,
                state.snapshots,
                seen_event_ids=frozenset(seen),
            )
            settings = load_provider_usage_settings().settings
            enabled_resets = {
                preference.provider_id
                for preference in settings.providers
                if preference.reset_celebrations
            }
            reset_events = tuple(
                event for event in reset_events if event.provider_id in enabled_resets
            )
            if reset_events:
                seen.update(event.event_id for event in reset_events)
                self._sidepulse_seen_reset_events = tuple(sorted(seen))[-512:]
                persisted = self._sidepulse_seen_reset_events
                threading.Thread(
                    target=lambda: save_seen_reset_events(persisted),
                    name="SidePulseResetEventStore",
                    daemon=True,
                ).start()
                self.quota_blink_until = max(
                    float(getattr(self, "quota_blink_until", 0.0) or 0.0),
                    time.monotonic() + 4.0,
                )
            thresholds = {
                preference.provider_id: preference.threshold_remaining
                for preference in settings.providers
            }
            self._sidepulse_provider_threshold_crossings = threshold_crossings(
                previous_state.snapshots,
                state.snapshots,
                thresholds,
            )
            controller = getattr(self, "_sidepulse_provider_usage_window", None)
            if controller is not None:
                controller.refresh(state)
            self._menu_signature = None
            if previous_state != state and getattr(self, "_runtime_started", False):
                self.schedule_event_refresh()

        @_legacy.objc.IBAction
        def refreshProviderUsage_(self, _sender) -> None:
            self._request_provider_usage(force=True)

        @_legacy.objc.IBAction
        def openProviderUsageCenter_(self, _sender) -> None:
            from .provider_usage_window import ProviderUsageWindowController

            controller = getattr(self, "_sidepulse_provider_usage_window", None)
            if controller is None:
                controller = ProviderUsageWindowController()
                self._sidepulse_provider_usage_window = controller
            controller.show(
                getattr(
                    self,
                    "_sidepulse_provider_usage_state",
                    ProviderUsageState((), None, None, False),
                )
            )

        @_legacy.objc.IBAction
        def performProviderUsageAction_(self, sender) -> None:
            payload = sender.representedObject()
            provider_id = payload.get("provider_id") if isinstance(payload, dict) else None
            if provider_id == "claude":
                self._connect_claude_usage()
                return
            self.openProviderUsageCenter_(sender)

        def _connect_claude_usage(self) -> None:
            try:
                from .claude_quota import credential_from_keychain_payload
                from .credentials import (
                    CLAUDE_CODE_KEYCHAIN,
                    KeychainConsentLedger,
                    read_keychain_secret,
                )
                from .providers import default_state_dir

                result = read_keychain_secret(
                    CLAUDE_CODE_KEYCHAIN,
                    allow_prompt=True,
                    ledger=KeychainConsentLedger(
                        default_state_dir() / "keychain-consent.json"
                    ),
                )
                credential = (
                    credential_from_keychain_payload(result.secret)
                    if result.ok
                    else None
                )
                if credential is None:
                    self.openProviderUsageCenter_(None)
                    return
                ProviderCredentialStore().set(
                    "claude",
                    "oauth-token",
                    credential.access_token,
                )
                self._request_provider_usage(force=True)
            except Exception:
                self.openProviderUsageCenter_(None)

        def applicationDidFinishLaunching_(self, notification):
            result = _BaseStatusBarController.applicationDidFinishLaunching_(
                self,
                notification,
            )
            self._sidepulse_seen_reset_events = load_seen_reset_events()
            self._request_provider_usage(force=True)
            return result

        @_legacy.objc.IBAction
        def refresh_(self, sender):
            self._request_provider_usage(force=False)
            return _BaseStatusBarController.refresh_(self, sender)

        def why_panel_body(self) -> str:
            body = _BaseStatusBarController.why_panel_body(self)
            state = getattr(
                self,
                "_sidepulse_provider_usage_state",
                ProviderUsageState((), None, None, False),
            )
            lines = ["Native provider usage"]
            projection = project_usage_menu(state, now=time.time())
            lines.append(projection.title)
            for row in projection.rows:
                lines.append(f"  {row.title}")
                if row.action_label:
                    lines.append(f"    Action: {row.action_label}")
            return f"{body}\n\n" + "\n".join(lines)

        def applicationWillTerminate_(self, notification):
            service = getattr(self, "_sidepulse_provider_usage_service", None)
            if service is not None:
                service.close()
            return _BaseStatusBarController.applicationWillTerminate_(
                self,
                notification,
            )

    _legacy.StatusBarController = JRProviderUsageStatusBarController


_legacy.build_menu = build_menu


def main() -> int:
    return _host.main()


__all__ = ["JRProviderUsageStatusBarController", "main"]
