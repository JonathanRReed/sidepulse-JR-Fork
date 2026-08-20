"""Final native-provider wrapper around SidePulse's retained AppKit host."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from . import settings_navigation as _settings_navigation
from . import settings_window as _settings_window
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
from .screen_bar_runtime import install_screen_bar_runtime
from .settings_category_runtime import (
    ensure_category,
    install_settings_navigation,
    refresh_native_usage_summary,
    requested_page_for_category,
    select_page,
    show_category,
)

_legacy = getattr(_host, "_legacy", _host)
install_settings_navigation(_legacy, _settings_window)
install_screen_bar_runtime()

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
    else:
        # The compact facade may have grouped the legacy view under its own
        # "Usage · …" parent row; removing only the nested item would leave
        # a second, empty usage row behind. Remove the whole parent.
        for parent_index in range(menu.numberOfItems()):
            parent = menu.itemAtIndex_(parent_index)
            submenu = parent.submenu()
            if submenu is None:
                continue
            try:
                nested = submenu.indexOfItem_(item)
            except Exception:
                nested = -1
            if nested >= 0:
                menu.removeItemAtIndex_(parent_index)
                break
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
            _disabled_item("Open Usage Center to connect provider sources")
        )
    for row in projection.rows:
        provider_item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            row.title,
            None,
            "",
        )
        provider_menu = _legacy.NSMenu.alloc().init()
        provider_menu.setAutoenablesItems_(False)
        lane_lines = getattr(row, "lane_lines", ())
        if lane_lines:
            for line in lane_lines:
                provider_menu.addItem_(_disabled_item(line))
        elif row.detail:
            provider_menu.addItem_(_disabled_item(row.detail))
        if row.usage_detail:
            if lane_lines or row.detail:
                provider_menu.addItem_(_legacy.NSMenuItem.separatorItem())
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


def _remove_redundant_separators(menu) -> None:
    index = menu.numberOfItems() - 1
    while index >= 0:
        item = menu.itemAtIndex_(index)
        previous = menu.itemAtIndex_(index - 1) if index > 0 else None
        if item.isSeparatorItem() and (
            index == 0
            or index == menu.numberOfItems() - 1
            or (previous is not None and previous.isSeparatorItem())
        ):
            menu.removeItemAtIndex_(index)
        index -= 1


def build_menu(snapshot, state, target):
    menu = _original_build_menu(snapshot, state, target)
    _remove_legacy_usage_item(menu, target)
    native_item = _native_usage_menu_item(target)
    index = _menu_index(menu, "Devices")
    if index < 0:
        # The compact facade retitles the devices row ("Devices · N
        # connected"); anchor on the prefix before falling back.
        index = next(
            (
                position
                for position in range(menu.numberOfItems())
                if str(menu.itemAtIndex_(position).title() or "").startswith(
                    "Devices"
                )
            ),
            -1,
        )
    if index < 0:
        index = min(4, menu.numberOfItems())
    menu.insertItem_atIndex_(native_item, index)
    if index + 1 < menu.numberOfItems():
        next_item = menu.itemAtIndex_(index + 1)
        if not next_item.isSeparatorItem():
            menu.insertItem_atIndex_(_legacy.NSMenuItem.separatorItem(), index + 1)
    _remove_redundant_separators(menu)
    return menu


def _settings_category_at_row(row: int):
    if 0 <= row < len(_settings_navigation.SETTINGS_CATEGORIES):
        return _settings_navigation.SETTINGS_CATEGORIES[row]
    return None


if _BaseStatusBarController.__name__ == "JRProviderUsageStatusBarController":
    JRProviderUsageStatusBarController = _BaseStatusBarController
else:

    class JRProviderUsageStatusBarController(_BaseStatusBarController):
        """Native usage accounting, consolidated Settings, and finite reset cues."""

        @property
        def provider_usage_state(self) -> ProviderUsageState:
            return getattr(
                self,
                "_sidepulse_provider_usage_state",
                ProviderUsageState((), None, None, False),
            )

        # --- Seven-category Settings navigation -------------------------

        def numberOfRowsInTableView_(self, _table_view) -> int:
            return len(_settings_navigation.SETTINGS_CATEGORIES)

        def tableView_viewForTableColumn_row_(self, _table_view, _column, row):
            category = _settings_category_at_row(int(row))
            if category is None:
                return None
            return _legacy.native_ui.sidebar_cell_view(category.label, category.icon)

        def tableView_shouldSelectRow_(self, _table_view, row) -> bool:
            return _settings_category_at_row(int(row)) is not None

        def tableView_isGroupRow_(self, _table_view, _row) -> bool:
            return False

        def ensure_settings_pane(self, key: str) -> None:
            try:
                category = _settings_navigation.category_for_key(key)
            except KeyError:
                return
            requested = key if category.contains(key) else None
            ensure_category(self, category.key, requested)

        def ensure_all_settings_panes(self) -> None:
            previous = getattr(self, "current_settings_pane", None)
            for category in _settings_navigation.SETTINGS_CATEGORIES:
                ensure_category(self, category.key, category.default_page)
                for page in category.pages:
                    select_page(self, category.key, page.key)
            if previous:
                try:
                    category = _settings_navigation.category_for_key(previous)
                except KeyError:
                    category = _settings_navigation.SETTINGS_CATEGORIES[0]
                show_category(self, category.key, previous)
            self.refresh_settings_window()

        def tableViewSelectionDidChange_(self, notification):
            table = notification.object()
            category = _settings_category_at_row(int(table.selectedRow()))
            if category is None:
                return
            requested = requested_page_for_category(self, category)
            self._settings_active_category = category.key
            show_category(self, category.key, requested)
            if self.settings_window is not None:
                self.settings_window.setTitle_(f"SidePulse Settings: {category.label}")
            self.reconcile_device_runtime()
            self.reconcile_installed_agent_inventory()
            if self.current_settings_pane == "color_studio":
                self.refresh_colors_window()
            self.refresh_settings_window()

        @_legacy.objc.IBAction
        def selectSettingsCategoryPage_(self, sender) -> None:
            try:
                category = _settings_navigation.SETTINGS_CATEGORIES[int(sender.tag())]
                page = category.pages[int(sender.selectedSegment())]
            except (AttributeError, IndexError, TypeError, ValueError):
                return
            show_category(self, category.key, page.key)
            self._settings_active_category = category.key
            if self.settings_window is not None:
                self.settings_window.setTitle_(f"SidePulse Settings: {category.label}")
            self.reconcile_device_runtime()
            self.reconcile_installed_agent_inventory()
            if page.key == "color_studio":
                self.refresh_colors_window()
            self.refresh_settings_window()

        def select_settings_pane(self, pane_key: str) -> None:
            try:
                category = _settings_navigation.category_for_key(pane_key)
            except KeyError:
                return
            requested = pane_key if category.contains(pane_key) else category.default_page
            self._pending_settings_page = requested
            if self.settings_sidebar_table is None:
                self.current_settings_pane = requested
                return
            row = _settings_navigation.SETTINGS_CATEGORIES.index(category)
            already_selected = int(self.settings_sidebar_table.selectedRow()) == row
            self.settings_sidebar_table.selectRowIndexes_byExtendingSelection_(
                _legacy.NSIndexSet.indexSetWithIndex_(row),
                False,
            )
            if already_selected:
                show_category(self, category.key, requested)
                self._settings_active_category = category.key
                if self.settings_window is not None:
                    self.settings_window.setTitle_(f"SidePulse Settings: {category.label}")
                self.refresh_settings_window()

        def show_settings_window(self) -> None:
            desired = getattr(self, "current_settings_pane", None) or "profile"
            try:
                category = _settings_navigation.category_for_key(desired)
            except KeyError:
                category = _settings_navigation.SETTINGS_CATEGORIES[0]
                desired = category.default_page
            requested_page = (
                desired if category.contains(desired) else category.default_page
            )
            self._pending_settings_page = requested_page
            self.current_settings_pane = category.key
            _BaseStatusBarController.show_settings_window(self)
            # The table-selection callback consumes `_pending_settings_page`.
            # Keep the local value so opening Settings directly to Screen Bar,
            # Capacity, or another child cannot snap back to the category's
            # first page after the callback returns.
            show_category(self, category.key, requested_page)
            self._pending_settings_page = None
            self._settings_active_category = category.key

        # --- Native provider usage --------------------------------------

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
            refresh_native_usage_summary(self)

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
            refresh_native_usage_summary(self)
            self._menu_signature = None
            if previous_state != state and getattr(self, "_runtime_started", False):
                self.schedule_event_refresh()

        @_legacy.objc.IBAction
        def refreshProviderUsage_(self, _sender) -> None:
            self._request_provider_usage(force=True)

        @_legacy.objc.IBAction
        def refreshNativeProviderUsage_(self, sender) -> None:
            self.refreshProviderUsage_(sender)

        @_legacy.objc.IBAction
        def openProviderUsageCenter_(self, _sender) -> None:
            from .provider_usage_window import ProviderUsageWindowController

            controller = getattr(self, "_sidepulse_provider_usage_window", None)
            if controller is None:
                controller = ProviderUsageWindowController()
                self._sidepulse_provider_usage_window = controller
            controller.show(self.provider_usage_state)

        @_legacy.objc.IBAction
        def performProviderUsageAction_(self, sender) -> None:
            payload = sender.representedObject()
            provider_id = payload.get("provider_id") if isinstance(payload, dict) else None
            if provider_id == "claude":
                self._connect_claude_usage()
                return
            self.openProviderUsageCenter_(sender)

        def _connect_claude_usage(self) -> None:
            """Connect, or say EXACTLY why not.

            Every failure used to fall through to 'open the Usage Center'
            -- which read as "it just opens a page and doesn't actually
            connect anything." The live failure on this Mac was real and
            reportable: Claude Code's Keychain entry existed but held an
            EMPTY accessToken (signed out / cleared by an update)."""
            message = "Claude usage connected."
            try:
                from .claude_quota import credential_from_keychain_payload
                from .credentials import (
                    CLAUDE_CODE_KEYCHAIN,
                    CredentialOutcome,
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
                if not result.ok:
                    message = {
                        CredentialOutcome.DENIED: (
                            "Keychain access was declined -- click Connect "
                            "again and choose Allow."
                        ),
                        CredentialOutcome.COOLING_DOWN: (
                            "Keychain access was declined recently -- try "
                            "again in a few minutes."
                        ),
                    }.get(
                        result.outcome,
                        "Claude Code's sign-in was not found in the Keychain.",
                    )
                else:
                    credential = credential_from_keychain_payload(result.secret)
                    if credential is None:
                        message = (
                            "Claude Code's stored sign-in is EMPTY -- run "
                            "`claude` in a terminal and log in, then click "
                            "Connect Claude usage again."
                        )
                    else:
                        ProviderCredentialStore().set(
                            "claude",
                            "oauth-token",
                            credential.access_token,
                        )
                        self._request_provider_usage(force=True)
            except Exception as exc:
                message = f"Could not read the Claude Code sign-in: {exc}"
            try:
                self.set_settings_message(message)
                _legacy.log_status_bar(f"claude usage connect: {message}")
            except Exception:
                pass
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
            lines = ["Native provider usage"]
            projection = project_usage_menu(self.provider_usage_state, now=time.time())
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
