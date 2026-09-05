"""Native-provider layer for the retained AppKit host."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from .product_identity import PRODUCT_DISPLAY_NAME
from .provider_usage_status_bar_probe import (
    PROBE_IMPORT_MODE as _PROBE_IMPORT_MODE,
)
from .provider_usage_status_bar_probe import (
    ProbeHost as _ProbeHost,
)
from .provider_usage_status_bar_probe import (
    ProbeLegacyShim as _ProbeLegacyShim,
)
from .provider_usage_status_bar_probe import (
    probe_build_menu,
)

if _PROBE_IMPORT_MODE:
    _settings_navigation = None
    _host = _ProbeHost(product_display_name=PRODUCT_DISPLAY_NAME)
    _legacy = _ProbeLegacyShim()
    _BaseStatusBarController = object
    _original_build_menu = None
else:
    from . import settings_navigation as _settings_navigation
    from . import status_bar as _host
    from .provider_credential_store import ProviderCredentialStore
    from .provider_feature_settings import (
        ProviderPresentationSettings,
        project_presentation_settings,
    )
    from .provider_reset_events import (
        ResetDeliverySettings,
        ResetDeliveryState,
        begin_reset_delivery,
        next_reset_retry_delay,
        reset_event_is_terminal,
    )
    from .provider_usage_controller_actions import (
        apply_provider_usage_settings_snapshot,
        perform_provider_usage_action,
        profile_session_action,
        toggle_provider_menu_visibility,
    )
    from .provider_usage_event_store import (
        load_reset_delivery_state,
        save_reset_delivery_state,
    )
    from .provider_usage_feedback_actions import (
        alert_connection_loss,
        alert_new_critical_pace,
        celebrate_quota_resets,
        report_reconnect_outcome,
    )
    from .provider_usage_qol import (
        detect_reset_events,
        merged_edge_baseline,
        threshold_crossings,
    )
    from .provider_usage_runtime import (
        ProviderUsageApply,
        ProviderUsageService,
        ProviderUsageState,
    )
    from .provider_usage_settings import (
        ProviderUsageSettings,
        load_provider_usage_settings,
    )
    from .provider_usage_store import load_provider_usage_state, save_provider_usage_state
    from .provider_usage_sync_cache import refresh_cached_merged_sync
    from .settings_category_runtime import (
        ensure_category,
        refresh_native_usage_summary,
        requested_page_for_category,
        save_provider_instance_profile_setting,
        select_page,
        show_category,
    )
    from .settings_destination_refresh import refresh_settings_destination
    from .sparkle_updater import (
        BETA_CHANNEL,
        STABLE_CHANNEL,
        inject_software_update_submenu,
        start_sparkle_updater,
    )
    from .usage_event_hooks import (
        detect_usage_hook_events,
        hook_path_message,
        run_usage_hooks,
    )
    from .usage_menu_injection import (
        menu_index,
        native_usage_menu_item,
        remove_legacy_usage_item,
        remove_redundant_separators,
    )
    from .usage_percent_history import record_state_observations

    _legacy = getattr(_host, "_legacy", _host)
    from .deck_status_bar import install_deck_status_bar

    _BaseStatusBarController = install_deck_status_bar(_host.JRStatusBarController)
    _original_build_menu = _host.build_menu


def build_menu(snapshot, state, target):
    if _PROBE_IMPORT_MODE:
        return probe_build_menu(snapshot, state, target)
    menu = _original_build_menu(snapshot, state, target)
    remove_legacy_usage_item(menu, target)
    native_item = native_usage_menu_item(target)
    # Prefix match covers both the plain "Devices" title and the compact
    # facade's retitled "Devices · N connected" row.
    index = menu_index(menu, "Devices")
    if index < 0:
        index = menu_index(menu, "Hardware")
    if index < 0:
        index = min(4, menu.numberOfItems())
    menu.insertItem_atIndex_(native_item, index)
    if index + 1 < menu.numberOfItems():
        next_item = menu.itemAtIndex_(index + 1)
        if not next_item.isSeparatorItem():
            menu.insertItem_atIndex_(_legacy.NSMenuItem.separatorItem(), index + 1)
    inject_software_update_submenu(
        menu,
        target,
        getattr(target, "_sidepulse_sparkle_updater", None),
    )
    remove_redundant_separators(menu)
    return menu


def _settings_category_at_row(row: int):
    if _PROBE_IMPORT_MODE or _settings_navigation is None:
        return None
    if 0 <= row < len(_settings_navigation.SETTINGS_CATEGORIES):
        return _settings_navigation.SETTINGS_CATEGORIES[row]
    return None


_existing_controller = globals().get("JRProviderUsageStatusBarController")
if isinstance(_existing_controller, type) and _existing_controller.__name__ == "JRProviderUsageStatusBarController":
    JRProviderUsageStatusBarController = _existing_controller
else:

    class JRProviderUsageStatusBarController(_BaseStatusBarController):
        """Native provider usage controller."""

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

        def _refresh_settings_destination(self, page_key: str) -> None:
            refresh_settings_destination(self, page_key)

        def tableViewSelectionDidChange_(self, notification):
            table = notification.object()
            category = _settings_category_at_row(int(table.selectedRow()))
            if category is None:
                return
            requested = requested_page_for_category(self, category)
            self._settings_active_category = category.key
            show_category(self, category.key, requested)
            if self.settings_window is not None:
                self.settings_window.setTitle_(f"{PRODUCT_DISPLAY_NAME} Settings: {category.label}")
            self._refresh_settings_destination(self.current_settings_pane)

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
                self.settings_window.setTitle_(f"{PRODUCT_DISPLAY_NAME} Settings: {category.label}")
            self._refresh_settings_destination(page.key)

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
                    self.settings_window.setTitle_(f"{PRODUCT_DISPLAY_NAME} Settings: {category.label}")
                self._refresh_settings_destination(requested)

        def show_settings_window(self) -> None:
            desired = getattr(self, "current_settings_pane", None) or "profile"
            try:
                category = _settings_navigation.category_for_key(desired)
            except KeyError:
                category = _settings_navigation.SETTINGS_CATEGORIES[0]
                desired = category.default_page
            requested_page = desired if category.contains(desired) else category.default_page
            self._pending_settings_page = requested_page
            self.current_settings_pane = category.key
            self._settings_window_closing = False
            if self.settings_window is None:
                self.settings_window = _legacy.build_settings_window(self)
                if self.settings_sidebar_table is not None:
                    row = _settings_navigation.SETTINGS_CATEGORIES.index(category)
                    self.settings_sidebar_table.selectRowIndexes_byExtendingSelection_(
                        _legacy.NSIndexSet.indexSetWithIndex_(row),
                        False,
                    )
            # The table-selection callback consumes `_pending_settings_page`.
            # Keep the local value so opening Settings directly to Screen Bar,
            # Capacity, or another child cannot snap back to the category's
            # first page after the callback returns.
            show_category(self, category.key, requested_page)
            self._pending_settings_page = None
            self._settings_active_category = category.key
            self._refresh_settings_destination(requested_page)
            _legacy.present_window(self.settings_window)
            _legacy.activate_app()

        def refresh_setup_window(self) -> None:
            from .onboarding_runtime import refresh_setup_window

            refresh_setup_window(self, _legacy)

        def _open_setup_destination(self, page_key: str) -> None:
            if self.setup_window is not None:
                self.setup_window.performClose_(None)
            self.select_settings_pane(page_key)
            self.show_settings_window()

        @_legacy.objc.IBAction
        def openSetupPhysicalDevices_(self, _sender) -> None:
            self._open_setup_destination("devices")

        @_legacy.objc.IBAction
        def openSetupT3_(self, _sender) -> None:
            self._open_setup_destination("installed_agents")

        @_legacy.objc.IBAction
        def openSetupAlcove_(self, _sender) -> None:
            self._open_setup_destination("colors_screen_bar")

        @_legacy.objc.IBAction
        def openSetupAgentDeck_(self, _sender) -> None:
            self._open_setup_destination("installed_agents")

        def run_first_launch_setup(self) -> None:
            from .onboarding_runtime import run_first_launch_setup

            run_first_launch_setup(self, _legacy)

        @_legacy.objc.IBAction
        def toggleSleepDim_(self, sender) -> None:
            from .onboarding_runtime import set_sleep_dim

            set_sleep_dim(self, sender, _legacy)

        @_legacy.objc.IBAction
        def toggleIdleAutoOff_(self, sender) -> None:
            from .onboarding_runtime import set_idle_auto_off

            set_idle_auto_off(self, sender, _legacy)

        @_legacy.objc.IBAction
        def applySleepDimPercentage_(self, sender) -> None:
            from .onboarding_runtime import set_sleep_dim_percentage

            set_sleep_dim_percentage(self, sender, _legacy)

        @_legacy.objc.IBAction
        def applyIdleAutoOffTimeout_(self, sender) -> None:
            from .onboarding_runtime import set_idle_auto_off_timeout

            set_idle_auto_off_timeout(self, sender, _legacy)

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
            providers: tuple[str | tuple[str, str], ...] | None = None,
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
            service = self._provider_usage_service()
            settings = service.settings_snapshot()
            if settings is None:
                return
            refresh_cached_merged_sync(state)
            payload = ProviderUsageApply(
                state,
                project_presentation_settings(settings),
                settings,
            )
            try:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyProviderUsageState:",
                    payload,
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
            self.settings = self.settings.with_usage_event_hook_path(str(sender.stringValue() or ""))
            _legacy.save_settings(self.settings)
            self.set_settings_message(hook_path_message(self.settings.usage_event_hook_path))

        @_legacy.objc.IBAction
        def applyProviderUsageState_(self, payload) -> None:
            if type(payload) is not ProviderUsageApply:
                return
            state = payload.state
            presentation = payload.settings
            if type(payload.usage_settings) is ProviderUsageSettings:
                apply_provider_usage_settings_snapshot(
                    self,
                    payload.usage_settings,
                )
            self._sidepulse_provider_presentation_settings = presentation
            settings = presentation
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
            # Last COMPARABLE reading per provider -- a degraded
            # (vendor-incident) publish must not wipe the pre-reset
            # baseline the detectors compare against.
            self._sidepulse_provider_usage_edge_baseline = merged_edge_baseline(previous_state, state)
            self._sidepulse_provider_usage_state = state
            # Percent history: every provider's "how much is left", so the
            # settings chart can show ALL of them.
            if (
                record_state_observations(
                self,
                state.snapshots,
                writer=self._persistence_writer,
                )
                is False
            ):
                self._provider_usage_log("usage percent history write not queued")
            delivery_state = getattr(self, "_sidepulse_reset_delivery_state", ResetDeliveryState())
            seen = {
                event.event_id
                for event in delivery_state.events
                if reset_event_is_terminal(delivery_state, event.event_id)
            }
            reset_events = detect_reset_events(
                previous_state.snapshots,
                state.snapshots,
                seen_event_ids=frozenset(seen),
            )
            reset_preferences = {preference.identity: preference for preference in settings.providers}
            if reset_events:
                for event in reset_events:
                    preference = reset_preferences.get((event.provider_id, event.source_instance_id))
                    enabled = bool(preference is not None and preference.reset_celebrations)
                    delivery_state = begin_reset_delivery(
                        delivery_state,
                        event,
                        ResetDeliverySettings(
                            overlay=enabled and preference.reset_overlay,
                            hardware=enabled and preference.reset_hardware,
                            notification=enabled and preference.reset_notification,
                            sound=enabled and preference.reset_sound,
                        ),
                        now=time.time(),
                )
                self._sidepulse_reset_delivery_state = delivery_state
                self._persist_reset_delivery_state()
            self._deliver_pending_reset_events()
            thresholds = {preference.identity: preference.threshold_remaining for preference in settings.providers}
            self._sidepulse_provider_threshold_crossings = threshold_crossings(
                previous_state.snapshots,
                state.snapshots,
                thresholds,
            )
            # Edge-triggered user hooks: transitions only, never states,
            # so a chime/webhook script needs no rate limiting of its own.
            hook_path = str(getattr(self.settings, "usage_event_hook_path", "") or "")
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
            # Fresh JR data must reach every surface that RENDERS it, or
            # "Refresh Capacity" fetches and the visible line never moves
            # until a pane switch (2026-08-27 audit). Both calls are pure
            # re-renders of state already in hand -- the Capacity pane's
            # no-implicit-provider-work law is untouched.
            try:
                self.refresh_capacity_settings_projection()
                plan_label = (getattr(self, "settings_fields", None) or {}).get("profile_plan_label")
                if plan_label is not None:
                    plan_label.setStringValue_(
                        self.jr_capacity_settings_text("claude") or getattr(self, "claude_plan_text", None) or ""
                    )
            except Exception as exc:
                self._provider_usage_log(f"usage projection refresh failed: {exc}")
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
            apply_provider_usage_settings_snapshot(
                self,
                updated,
                notify_service=True,
            )

        @_legacy.objc.IBAction
        def toggleUsageMenuProvider_(self, sender) -> None:
            try:
                toggle_provider_menu_visibility(self, sender)
            except Exception as exc:
                _legacy.log_status_bar(f"usage menu providers: {exc}")
                sender.setState_(0 if bool(sender.state()) else 1)

        @_legacy.objc.IBAction
        def toggleProviderResetSetting_(self, sender) -> None:
            from .provider_reset_settings_action import toggle_provider_reset_setting

            toggle_provider_reset_setting(self, sender, log=_legacy.log_status_bar)

        @_legacy.objc.IBAction
        def updateProviderInstanceProfile_(self, sender) -> None:
            save_provider_instance_profile_setting(
                self,
                sender,
                log=_legacy.log_status_bar,
            )

        # --- Tightest limit beside the menu-bar icon (Codex Bar parity)

        def _usage_menu_settings(self):
            """Immutable worker or explicit-action snapshot; never UI-path I/O."""
            settings = getattr(self, "_sidepulse_provider_presentation_settings", None)
            if type(settings) is not ProviderPresentationSettings:
                durable = getattr(
                    self,
                    "_sidepulse_provider_usage_settings_snapshot",
                    None,
                )
                if type(durable) is ProviderUsageSettings:
                    settings = project_presentation_settings(durable)
            return settings if type(settings) is ProviderPresentationSettings else None

        def set_status(self, state, *, ask_count: int = 0, done_badge: bool = False) -> None:
            _BaseStatusBarController.set_status(self, state, ask_count=ask_count, done_badge=done_badge)
            self._append_quota_guarded()

        def _apply_status_accessibility_text(self, glance, finite_cues) -> None:
            # This base call rewrites the button title BARE, and it also
            # fires outside set_status on every finite-cue advance --
            # which wiped the quota percent for seconds at a time exactly
            # while cues were animating. Re-append after every rewrite
            # (the substring dedup makes it idempotent).
            _BaseStatusBarController._apply_status_accessibility_text(self, glance, finite_cues)
            self._append_quota_guarded()

        def _append_quota_guarded(self, *, wall_clock: Callable[[], float] = time.time) -> None:
            try:
                self._append_quota_to_status_title(wall_clock=wall_clock)
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
            from .provider_usage_status_projection import screen_bar_quota_ember_level

            return screen_bar_quota_ember_level(self)

        def quota_runway_state(self):
            """The base withholds this LED (it collects no usage). The JR
            plane's gated lanes ARE the authority it waited for -- the same
            numbers the menu meters and the quota ember trust."""
            from .quota_runway import quota_runway_state_for_controller

            return quota_runway_state_for_controller(self)

        def _alert_new_critical_pace(self, previous_state, state) -> None:
            alert_new_critical_pace(self, previous_state, state, legacy=_legacy)

        def request_jr_usage_refresh(
            self,
            providers: tuple[str | tuple[str, str], ...],
            *,
            force=False,
            monotonic: Callable[[], float] = time.monotonic,
        ):
            now = float(monotonic())
            last = getattr(self, "_jr_usage_refresh_at", 0.0)
            if not force and now - last < 120.0:
                return
            self._jr_usage_refresh_at = now
            self._request_provider_usage(force=force, providers=tuple(providers))

        def jr_capacity_settings_text(
            self,
            provider_id,
            *,
            wall_clock: Callable[[], float] = time.time,
        ):
            from .provider_usage_status_projection import capacity_settings_text

            return capacity_settings_text(self, provider_id, wall_clock=wall_clock)

        def jr_plane_owns_usage_menu_item(self) -> bool:
            return True

        def jr_plane_owns_capacity(self, provider_id: str) -> bool:
            """Claude usage polling is owned here, not by the legacy scheduler."""
            return provider_id == "claude"

        def _report_reconnect_outcome(self, state) -> None:
            report_reconnect_outcome(self, state, legacy=_legacy)

        def _celebrate_quota_resets(self, events) -> None:
            celebrate_quota_resets(self, events, legacy=_legacy)

        def _persist_reset_delivery_state(self) -> None:
            state = getattr(self, "_sidepulse_reset_delivery_state", ResetDeliveryState())
            disposition = self._persistence_writer.submit(
                "provider-reset-events",
                lambda: save_reset_delivery_state(state),
                replace_pending=True,
            )
            if disposition.value.startswith("refused"):
                self._provider_usage_log("reset delivery state write not queued")

        def _deliver_pending_reset_events(self) -> None:
            from .provider_reset_settings_action import deliver_pending_reset_events

            deliver_pending_reset_events(self, legacy=_legacy)

        def _schedule_reset_delivery_retry(self, now: float) -> None:
            state = getattr(self, "_sidepulse_reset_delivery_state", ResetDeliveryState())
            delay = next_reset_retry_delay(state, now=now)
            timer = getattr(self, "_sidepulse_reset_delivery_timer", None)
            if delay is None:
                if timer is not None:
                    timer.invalidate()
                self._sidepulse_reset_delivery_timer = None
                return
            if timer is not None:
                return
            self._sidepulse_reset_delivery_timer = self._schedule_capacity_timer(
                max(0.05, delay),
                "retryPendingResetDeliveries:",
            )

        @_legacy.objc.IBAction
        def retryPendingResetDeliveries_(self, timer) -> None:
            if timer is not getattr(self, "_sidepulse_reset_delivery_timer", None):
                return
            self._sidepulse_reset_delivery_timer = None
            self._deliver_pending_reset_events()

        def _alert_connection_loss(self, previous_state, state) -> None:
            alert_connection_loss(self, previous_state, state, legacy=_legacy)

        def _active_usage_providers(self) -> frozenset[str]:
            """Providers with a MAIN session actually working right now --
            they own the menu-bar glance while they run."""
            from .provider_usage_status_projection import active_usage_providers

            return active_usage_providers(self, _legacy)

        def _active_usage_instances(self) -> frozenset[tuple[str, str]]:
            """Exact active account identities when session provenance matches."""

            from .provider_usage_status_projection import active_usage_instances

            return active_usage_instances(self, _legacy)

        def _append_quota_to_status_title(self, *, wall_clock: Callable[[], float] = time.time) -> None:
            from .provider_usage_status_projection import append_quota_to_status_title

            append_quota_to_status_title(self, wall_clock=wall_clock)

        def open_session(self, status, action: str | None, *, remember: bool) -> None:
            _BaseStatusBarController.open_session(
                self,
                status,
                profile_session_action(self, status, action),
                remember=remember,
            )

        @_legacy.objc.IBAction
        def openProviderUsageCenter_(self, _sender) -> None:
            from .provider_usage_window import ProviderUsageWindowController

            settings = self._usage_menu_settings()
            controller = getattr(self, "_sidepulse_provider_usage_window", None)
            if controller is None:
                controller = ProviderUsageWindowController(action_target=self)
                self._sidepulse_provider_usage_window = controller
            if settings is not None:
                controller.set_privacy_mode(settings.menu_display.privacy_mode)
            controller.show(self.provider_usage_state)

        @_legacy.objc.IBAction
        def usageCenterAction_(self, sender) -> None:
            perform_provider_usage_action(
                self,
                sender,
                open_center=False,
                log=_legacy.log_status_bar,
            )

        @_legacy.objc.IBAction
        def performProviderUsageAction_(self, sender) -> None:
            perform_provider_usage_action(
                self,
                sender,
                open_center=True,
                log=_legacy.log_status_bar,
            )

        @_legacy.objc.IBAction
        def checkForSoftwareUpdates_(self, sender) -> None:
            runtime = getattr(self, "_sidepulse_sparkle_updater", None)
            if runtime is not None:
                runtime.check_for_updates(sender)

        def _select_update_channel(self, channel: str) -> None:
            runtime = getattr(self, "_sidepulse_sparkle_updater", None)
            if runtime is not None and runtime.select_channel(channel):
                self._menu_signature = None

        @_legacy.objc.IBAction
        def selectStableUpdates_(self, _sender) -> None:
            self._select_update_channel(STABLE_CHANNEL)

        @_legacy.objc.IBAction
        def selectBetaUpdates_(self, _sender) -> None:
            self._select_update_channel(BETA_CHANNEL)

        def applicationDidFinishLaunching_(self, notification):
            if getattr(self, "_runtime_started", False) or getattr(self, "_runtime_termination_started", False):
                return None
            self._sidepulse_sparkle_updater = start_sparkle_updater()
            result = _BaseStatusBarController.applicationDidFinishLaunching_(
                self,
                notification,
            )
            from .optional_integration_runtime import (
                start_optional_integration_runtime,
            )

            self._sidepulse_optional_integration_runtime = start_optional_integration_runtime(self)
            self._sidepulse_reset_delivery_state = load_reset_delivery_state()
            self._deliver_pending_reset_events()
            # Seed the edge baseline from the persisted store: a reset
            # that passes while the app is down (or restarting) is still
            # an edge against the last persisted reading. An empty
            # launch baseline made the first publish blind.
            from .provider_usage_store import load_provider_usage_state

            self._sidepulse_provider_usage_edge_baseline = load_provider_usage_state()
            self._request_provider_usage(force=True)
            return result

        @_legacy.objc.IBAction
        def refresh_(self, sender):
            self._deliver_pending_reset_events()
            self._request_provider_usage(force=False)
            result = _BaseStatusBarController.refresh_(self, sender)
            runtime = getattr(self, "_sidepulse_optional_integration_runtime", None)
            snapshot = getattr(self, "last_snapshot", None)
            if runtime is not None and snapshot is not None:
                signal = (
                    "quota_exhausted"
                    if self.screen_bar_quota_ember_level() >= 1.0
                    else None
                )
                runtime.publish_creator_output(
                    self.display_aggregate_mode(snapshot),
                    signal=signal,
                )
            return result

        def why_panel_body(
            self,
            *,
            why_context=None,
            wall_clock: Callable[[], float] = time.time,
        ) -> str:
            body = _BaseStatusBarController.why_panel_body(
                self,
                why_context=why_context,
            )
            from .provider_usage_status_projection import provider_usage_why_panel_body

            return provider_usage_why_panel_body(
                self,
                body,
                wall_clock=wall_clock,
            )

        def applicationWillTerminate_(self, notification):
            if getattr(self, "_runtime_termination_started", False):
                return None
            from .deck_controller import stop_deck_runtime_reconfiguration

            stop_deck_runtime_reconfiguration(self)
            service = getattr(self, "_sidepulse_provider_usage_service", None)
            if service is not None:
                service.close()
            optional_runtime = getattr(
                self,
                "_sidepulse_optional_integration_runtime",
                None,
            )
            if optional_runtime is not None:
                optional_runtime.close()
            return _BaseStatusBarController.applicationWillTerminate_(
                self,
                notification,
            )


def install_provider_usage_status_bar():
    """Install the final provider controller and root-menu wrapper once."""
    if _PROBE_IMPORT_MODE:
        from . import status_bar_legacy as legacy

        legacy.StatusBarController = JRProviderUsageStatusBarController
        legacy.build_menu = build_menu
        return JRProviderUsageStatusBarController, build_menu
    _host.install_status_bar_facade()
    _legacy.StatusBarController = JRProviderUsageStatusBarController
    _legacy.build_menu = build_menu
    return JRProviderUsageStatusBarController, build_menu


def main() -> int:
    """Delegate to the one retained foreground main and composition boundary."""
    return _host.main()


__all__ = [
    "JRProviderUsageStatusBarController",
    "install_provider_usage_status_bar",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
