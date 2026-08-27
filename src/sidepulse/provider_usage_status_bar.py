"""Final native-provider wrapper around SidePulse's retained AppKit host."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from . import settings_navigation as _settings_navigation
from . import settings_window as _settings_window
from . import status_bar as _host
from .provider_browser_access import run_provider_usage_action
from .provider_credential_store import ProviderCredentialStore
from .provider_usage_event_store import (
    load_seen_reset_events,
    save_seen_reset_events,
)
from .provider_usage_menu import menu_bar_quota_glance, project_usage_menu
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
from .status_feeds import incident_row_title, shared_status_feed_poller
from .usage_event_hooks import (
    detect_usage_hook_events,
    hook_path_message,
    run_usage_hooks,
)
from .usage_menu_injection import (
    menu_index,
    remove_legacy_usage_item,
    remove_redundant_separators,
)
from .usage_percent_history import record_state_observations

_legacy = getattr(_host, "_legacy", _host)
install_settings_navigation(_legacy, _settings_window)
install_screen_bar_runtime()

_BaseStatusBarController = _legacy.StatusBarController
_original_build_menu = _legacy.build_menu


def _disabled_item(title: str, *, alert: bool = False):
    item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title,
        None,
        "",
    )
    item.setEnabled_(False)
    if alert:
        # A lane past its low-remaining threshold gets amber, not just a
        # shorter meter -- peripheral vision again.
        try:
            from AppKit import (
                NSAttributedString,
                NSColor,
                NSFont,
                NSFontAttributeName,
                NSForegroundColorAttributeName,
            )

            item.setAttributedTitle_(
                NSAttributedString.alloc().initWithString_attributes_(
                    title,
                    {
                        NSForegroundColorAttributeName: NSColor.systemOrangeColor(),
                        NSFontAttributeName: NSFont.menuFontOfSize_(13.0),
                    },
                )
            )
        except Exception:
            pass
    return item


def _native_usage_menu_item(target):
    state = getattr(
        target,
        "_sidepulse_provider_usage_state",
        ProviderUsageState((), None, None, False),
    )
    try:
        settings = load_provider_usage_settings().settings
        display = settings.menu_display
        hidden = settings.hidden_menu_providers()
        thresholds = {
            preference.provider_id: preference.threshold_remaining
            for preference in settings.providers
        }
    except Exception:
        display, hidden, thresholds = None, frozenset(), None
    projection = project_usage_menu(
        state,
        now=time.time(),
        display=display,
        hidden_providers=hidden,
        thresholds=thresholds,
    )
    item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        projection.title,
        None,
        "",
    )
    submenu = _legacy.NSMenu.alloc().init()
    submenu.setAutoenablesItems_(False)
    # Vendor incidents outrank everything below: "the provider is down"
    # must never read as "your quota fetch broke".
    poller = shared_status_feed_poller()
    poller.start()
    incidents = poller.current()
    for provider_id in sorted(incidents):
        incident = incidents[provider_id]
        row = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            incident_row_title(incident), "openProviderStatusPage:", ""
        )
        row.setTarget_(target)
        row.setRepresentedObject_(incident.page_url)
        submenu.addItem_(row)
    if incidents:
        submenu.addItem_(_legacy.NSMenuItem.separatorItem())
    if not projection.rows:
        # An empty submenu has two very different causes: nothing is
        # connected, or everything is curated out. Diagnosing the wrong
        # one ("connect sources" when sources ARE collecting) sends the
        # user to the wrong fix.
        if state.snapshots and hidden:
            submenu.addItem_(
                _disabled_item(
                    "All providers hidden — choose some in Settings → Usage"
                )
            )
        else:
            submenu.addItem_(
                _disabled_item("Open Usage Center to connect provider sources")
            )
    for row in projection.rows:
        row_title = row.title
        if row.provider_id in incidents:
            row_title = f"{row_title} · ⚠ vendor incident"
        provider_item = _legacy.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            row_title,
            None,
            "",
        )
        provider_menu = _legacy.NSMenu.alloc().init()
        provider_menu.setAutoenablesItems_(False)
        lane_lines = getattr(row, "lane_lines", ())
        alert_indexes = set(getattr(row, "alert_lane_indexes", ()))
        if lane_lines:
            for index, line in enumerate(lane_lines):
                provider_menu.addItem_(
                    _disabled_item(line, alert=index in alert_indexes)
                )
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


def build_menu(snapshot, state, target):
    menu = _original_build_menu(snapshot, state, target)
    remove_legacy_usage_item(menu, target)
    native_item = _native_usage_menu_item(target)
    # Prefix match covers both the plain "Devices" title and the compact
    # facade's retitled "Devices · N connected" row.
    index = menu_index(menu, "Devices")
    if index < 0:
        index = min(4, menu.numberOfItems())
    menu.insertItem_atIndex_(native_item, index)
    if index + 1 < menu.numberOfItems():
        next_item = menu.itemAtIndex_(index + 1)
        if not next_item.isSeparatorItem():
            menu.insertItem_atIndex_(_legacy.NSMenuItem.separatorItem(), index + 1)
    remove_redundant_separators(menu)
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

        def _request_provider_usage(
            self,
            *,
            force: bool = False,
            providers: tuple[str, ...] | None = None,
        ) -> None:
            service = self._provider_usage_service()
            current = service.request(
                callback=self._provider_usage_ready,
                force=force,
                providers=providers,
            )
            self._sidepulse_provider_usage_state = current
            refresh_native_usage_summary(self)

        def _provider_usage_log(self, message: str) -> None:
            _legacy.log_status_bar(message)

        def _show_provider_usage_feedback(self, message: str) -> None:
            from .provider_usage_feedback import show_provider_usage_feedback

            show_provider_usage_feedback(self, message)

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
        def openTodayTarget_(self, sender) -> None:
            from .today_menu import open_today_target

            open_today_target(str(sender.representedObject() or ""))

        @_legacy.objc.IBAction
        def openProviderStatusPage_(self, sender) -> None:
            url = str(sender.representedObject() or "")
            if url.startswith("https://"):
                from AppKit import NSURL, NSWorkspace

                NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))

        @_legacy.objc.IBAction
        def applyUsageEventHook_(self, sender) -> None:
            self.settings = self.settings.with_usage_event_hook_path(
                str(sender.stringValue() or "")
            )
            _legacy.save_settings(self.settings)
            self.set_settings_message(
                hook_path_message(self.settings.usage_event_hook_path)
            )

        @_legacy.objc.IBAction
        def applyProviderUsageState_(self, state) -> None:
            if type(state) is not ProviderUsageState:
                return
            # The edge BASELINE is owned by this method alone. It used
            # to read _sidepulse_provider_usage_state, which every 15s
            # tick overwrites with the service's current state -- so a
            # tick landing between the worker's publish and this apply
            # made previous == current and blinded EVERY edge detector
            # (resets, thresholds, pace, hooks, connection loss).
            previous_state = getattr(
                self,
                "_sidepulse_provider_usage_edge_baseline",
                ProviderUsageState((), None, None, False),
            )
            self._sidepulse_provider_usage_edge_baseline = state
            self._sidepulse_provider_usage_state = state
            # Percent history: every provider's "how much is left", so the
            # settings chart can show ALL of them.
            record_state_observations(self, state.snapshots)
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
                self._celebrate_quota_resets(reset_events)
            thresholds = {
                preference.provider_id: preference.threshold_remaining
                for preference in settings.providers
            }
            self._sidepulse_provider_threshold_crossings = threshold_crossings(
                previous_state.snapshots,
                state.snapshots,
                thresholds,
            )
            # Edge-triggered user hooks: transitions only, never states,
            # so a chime/webhook script needs no rate limiting of its own.
            hook_path = str(
                getattr(self.settings, "usage_event_hook_path", "") or ""
            )
            if hook_path:
                run_usage_hooks(
                    hook_path,
                    detect_usage_hook_events(
                        previous_state.snapshots,
                        state.snapshots,
                        thresholds=thresholds,
                    ),
                )
            # Pace as an interruption, not just a color: a lane that
            # JUST became projected-to-run-dry-before-reset earns one
            # content-free banner per window, through the same gates as
            # every other quota effect.
            self._alert_new_critical_pace(previous_state, state)
            self._alert_connection_loss(previous_state, state)
            self._report_reconnect_outcome(state)
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
        def toggleUsageMenuElement_(self, sender) -> None:
            from .provider_usage_settings import save_provider_usage_settings

            flag = str(sender.identifier() or "")
            loaded = load_provider_usage_settings()
            try:
                updated = loaded.settings.with_menu_flag(flag, bool(sender.state()))
                save_provider_usage_settings(updated, loaded=loaded)
            except Exception as exc:
                _legacy.log_status_bar(f"usage menu display: {exc}")
                # The checkbox flipped BEFORE the action fired; a failed
                # save must flip it back or the pane lies forever.
                sender.setState_(0 if bool(sender.state()) else 1)
                return
            self._menu_signature = None
            self._sidepulse_usage_menu_settings_cache = None

        @_legacy.objc.IBAction
        def toggleUsageMenuProvider_(self, sender) -> None:
            from .provider_usage_settings import save_provider_usage_settings

            provider_id = str(sender.identifier() or "")
            loaded = load_provider_usage_settings()
            try:
                updated = loaded.settings.with_menu_visible(
                    provider_id, bool(sender.state())
                )
                save_provider_usage_settings(updated, loaded=loaded)
            except Exception as exc:
                _legacy.log_status_bar(f"usage menu providers: {exc}")
                sender.setState_(0 if bool(sender.state()) else 1)
                return
            self._menu_signature = None
            self._sidepulse_usage_menu_settings_cache = None

        # --- Tightest limit beside the menu-bar icon (Codex Bar parity)

        def _usage_menu_settings(self):
            """Loaded usage settings with a short TTL -- set_status runs
            on every presentation tick and must not hit the disk each
            time. The toggles above clear the cache on change."""
            cached = getattr(self, "_sidepulse_usage_menu_settings_cache", None)
            now = time.monotonic()
            if cached is not None and now - cached[0] < 15.0:
                return cached[1]
            try:
                settings = load_provider_usage_settings().settings
            except Exception:
                settings = None
            self._sidepulse_usage_menu_settings_cache = (now, settings)
            return settings

        def set_status(
            self, state, *, ask_count: int = 0, done_badge: bool = False
        ) -> None:
            _BaseStatusBarController.set_status(
                self, state, ask_count=ask_count, done_badge=done_badge
            )
            self._append_quota_guarded()

        def _apply_status_accessibility_text(self, glance, finite_cues) -> None:
            # This base call rewrites the button title BARE, and it also
            # fires outside set_status on every finite-cue advance --
            # which wiped the quota percent for seconds at a time exactly
            # while cues were animating. Re-append after every rewrite
            # (the substring dedup makes it idempotent).
            _BaseStatusBarController._apply_status_accessibility_text(
                self, glance, finite_cues
            )
            self._append_quota_guarded()

        def _append_quota_guarded(self) -> None:
            try:
                self._append_quota_to_status_title()
            except Exception as exc:
                # The status title is agent truth first; a quota suffix
                # failure must never take the tick down with it -- but a
                # PERSISTENT failure must not be silent either.
                if not getattr(self, "_quota_suffix_error_logged", False):
                    self._quota_suffix_error_logged = True
                    try:
                        _legacy.log_status_bar(f"menu-bar quota suffix: {exc}")
                    except Exception:
                        pass

        def screen_bar_quota_ember_level(self) -> float:
            """The base's 0.0 answered with the real reading: how far
            the tightest visible lane has sunk below its provider's
            threshold, 0 at-threshold to 1 fully out."""
            try:
                from .provider_usage_platform import (
                    ProviderSourceState,
                    most_constrained_lane,
                )

                settings = self._usage_menu_settings()
                if settings is None:
                    return 0.0
                hidden = settings.hidden_menu_providers()
                thresholds = {
                    preference.provider_id: preference.threshold_remaining
                    for preference in settings.providers
                }
                worst = 0.0
                for snapshot in self.provider_usage_state.snapshots:
                    if snapshot.provider_id in hidden:
                        continue
                    if snapshot.state not in {
                        ProviderSourceState.READY,
                        ProviderSourceState.STALE,
                    }:
                        continue
                    lane = most_constrained_lane(snapshot)
                    if lane is None or lane.remaining_percent is None:
                        continue
                    threshold = thresholds.get(snapshot.provider_id, 20.0)
                    if threshold <= 0.0:
                        continue
                    if lane.remaining_percent <= threshold:
                        worst = max(
                            worst, 1.0 - lane.remaining_percent / threshold
                        )
                return max(0.0, min(1.0, worst))
            except Exception:
                return 0.0

        def quota_runway_state(self):
            """The base withholds this LED (it collects no usage). The JR
            plane's gated lanes ARE the authority it waited for -- the same
            numbers the menu meters and the quota ember trust."""
            from .quota_runway import quota_runway_state_for_controller

            return quota_runway_state_for_controller(self)

        def _alert_new_critical_pace(self, previous_state, state) -> None:
            from .provider_usage_feedback import alert_new_critical_pace

            alert_new_critical_pace(
                self,
                previous_state,
                state,
                log=_legacy.log_status_bar,
                signal_kind=getattr(
                    _legacy.signals_module, "SIGNAL_QUOTA", None
                ),
            )

        def jr_plane_owns_usage_menu_item(self) -> bool:
            # The legacy build constructs its usage card only to have the
            # wrapper above remove it -- measured dead weight in every
            # full rebuild. This facade owns the usage row.
            return True

        def jr_plane_owns_capacity(self, provider_id: str) -> bool:
            """The JR usage plane polls the Claude usage ENDPOINT
            itself (ProviderUsageService), so the legacy capacity
            scheduler must not hit the same remote with the same token
            on a second cadence -- the double-poll was the documented
            429 mechanism. Claude only for now: codex's legacy source
            is a LOCAL read (no rate-limit hazard) that still feeds the
            Overview labels until coalescence step 3 migrates them."""
            return provider_id == "claude"

        def _report_reconnect_outcome(self, state) -> None:
            from .provider_usage_feedback import report_reconnect_outcome

            report_reconnect_outcome(self, state, log=_legacy.log_status_bar)

        def _celebrate_quota_resets(self, events) -> None:
            from .provider_usage_feedback import celebrate_quota_resets

            celebrate_quota_resets(
                self,
                events,
                log=_legacy.log_status_bar,
                signal_kind=getattr(
                    _legacy.signals_module, "SIGNAL_QUOTA", None
                ),
            )

        def _alert_connection_loss(self, previous_state, state) -> None:
            from .provider_usage_feedback import alert_connection_loss

            alert_connection_loss(
                self,
                previous_state,
                state,
                log=_legacy.log_status_bar,
                signal_kind=getattr(
                    _legacy.signals_module, "SIGNAL_NOTIFICATION", None
                ),
            )

        def _active_usage_providers(self) -> frozenset[str]:
            """Providers with a MAIN session actually working right now --
            they own the menu-bar glance while they run."""
            snapshot = getattr(self, "last_snapshot", None)
            if snapshot is None:
                return frozenset()
            busy = {
                _legacy.AgentMode.WORKING,
                _legacy.AgentMode.TOOL_RUNNING,
                _legacy.AgentMode.LONG_TASK_PROGRESS,
            }
            return frozenset(
                status.provider
                for status in snapshot.statuses
                if not status.is_subagent and status.mode in busy
            )

        def _append_quota_to_status_title(self) -> None:
            settings = self._usage_menu_settings()
            if settings is None or not settings.menu_display.show_menu_bar_percent:
                return
            item = getattr(self, "status_item", None)
            button = item.button() if item is not None else None
            if button is None:
                return
            glance = menu_bar_quota_glance(
                self.provider_usage_state,
                hidden_providers=settings.hidden_menu_providers(),
                active_providers=self._active_usage_providers(),
                now=time.time(),
            )
            if glance is None:
                return
            title = str(button.title() or "")
            if glance.text in title:
                return
            prefix = f"{title} · " if title.strip() else " "
            full = f"{prefix}{glance.text}"
            button.setTitle_(full)
            # At-a-glance pace: the percent turns amber when this lane is
            # being spent too fast and red when it will run dry before
            # (or already ran out at) its reset. Colorless means fine.
            color_name = {
                "fast": "systemOrangeColor",
                "critical": "systemRedColor",
                "out": "systemRedColor",
            }.get(glance.verdict or "")
            if color_name is None:
                return
            try:
                from AppKit import (
                    NSColor,
                    NSFontAttributeName,
                    NSForegroundColorAttributeName,
                    NSMutableAttributedString,
                )

                styled = NSMutableAttributedString.alloc().initWithString_attributes_(
                    full,
                    {
                        NSForegroundColorAttributeName: NSColor.labelColor(),
                        NSFontAttributeName: button.font(),
                    },
                )
                styled.addAttribute_value_range_(
                    NSForegroundColorAttributeName,
                    getattr(NSColor, color_name)(),
                    (len(prefix), len(glance.text)),
                )
                button.setAttributedTitle_(styled)
            except Exception:
                pass

        @_legacy.objc.IBAction
        def openProviderUsageCenter_(self, _sender) -> None:
            from .provider_usage_window import ProviderUsageWindowController

            controller = getattr(self, "_sidepulse_provider_usage_window", None)
            if controller is None:
                controller = ProviderUsageWindowController(action_target=self)
                self._sidepulse_provider_usage_window = controller
            controller.show(self.provider_usage_state)

        def _provider_action_label(self, provider_id: str) -> str:
            state = getattr(self, "provider_usage_state", None)
            snapshot = next(
                (
                    item
                    for item in getattr(state, "snapshots", ())
                    if item.provider_id == provider_id
                ),
                None,
            )
            return str(getattr(snapshot, "action_label", "") or "")

        def _claude_action_wants_connect(self) -> bool:
            """Only a Connect/Reconnect click earns the Keychain flow.

            The short-circuit used to fire for EVERY Claude action, so
            clicking "Retry later" on a rate-limited card ran a
            synchronous `security` read (30s ceiling) on the main
            thread -- a beachball nobody asked for."""
            label = self._provider_action_label("claude").lower()
            return "connect" in label or not label

        @_legacy.objc.IBAction
        def usageCenterAction_(self, sender) -> None:
            """A card's action button: identifier carries the provider."""
            provider_id = str(sender.identifier() or "")
            if provider_id == "claude" and self._claude_action_wants_connect():
                self._connect_claude_usage()
                return
            # Staged browser-access flow (enable -> import -> organization);
            # a plain refresh remains the fallback for other actions.
            if provider_id and run_provider_usage_action(self, provider_id):
                return
            # Scoped to this provider: a full-fleet force here let one
            # "Retry" click march every provider through its backoff
            # gate at once.
            self._request_provider_usage(
                force=True,
                providers=(provider_id,) if provider_id else None,
            )

        @_legacy.objc.IBAction
        def performProviderUsageAction_(self, sender) -> None:
            payload = sender.representedObject()
            provider_id = payload.get("provider_id") if isinstance(payload, dict) else None
            if provider_id == "claude" and self._claude_action_wants_connect():
                self._connect_claude_usage()
                return
            if provider_id and run_provider_usage_action(self, provider_id):
                return
            # The fallthrough used to open a window and change nothing --
            # the definition of a dead button. At minimum, look again.
            if provider_id:
                self._request_provider_usage(force=True, providers=(provider_id,))
            self.openProviderUsageCenter_(sender)

        def _connect_claude_usage(self) -> None:
            from .provider_usage_feedback import connect_claude_usage

            connect_claude_usage(self, log=_legacy.log_status_bar)

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
