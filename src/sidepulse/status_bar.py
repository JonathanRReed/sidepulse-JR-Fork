from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from pathlib import Path

try:
    import objc
    from AppKit import (
        NSApp,
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSBezelStyleRounded,
        NSBezierPath,
        NSButton,
        NSButtonTypeSwitch,
        NSColor,
        NSColorPanel,
        NSCompositingOperationSourceOver,
        NSEventTypeLeftMouseDragged,
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSImage,
        NSLayoutConstraint,
        NSMaxYEdge,
        NSMenu,
        NSMenuItem,
        NSOffState,
        NSOnState,
        NSPopover,
        NSPopoverBehaviorTransient,
        NSSavePanel,
        NSScreen,
        NSScrollView,
        NSSlider,
        NSStatusBar,
        NSSwitch,
        NSTextField,
        NSTextView,
        NSVariableStatusItemLength,
        NSView,
        NSViewController,
        NSWindow,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskTitled,
        NSWorkspace,
    )
    from Foundation import (
        NSURL,
        NSIndexSet,
        NSMutableAttributedString,
        NSObject,
        NSString,
        NSTimer,
    )
except ImportError as exc:  # pragma: no cover - only exercised on non-macOS setups.
    raise SystemExit(
        "The status-bar app requires PyObjC/AppKit:\n"
        "  python3 -m pip install pyobjc-framework-Cocoa"
    ) from exc

from . import (
    calendar_watch,
    claude_quota,
    display_brightness,
    focus_sync,
    native_ui,
    notification_watch,
    reminders_watch,
    usage_stats,
    weather_watch,
)
from . import colors as colors_module
from . import signals as signals_module
from .app_bundle import default_app_bundle_path, running_inside_bundle
from .audit import (
    default_status_audit_log_path,
    export_status_audit_csv,
    export_status_audit_html,
    trim_oversized_logs,
)
from .battery import (
    BatteryLedController,
    BatterySnapshot,
    format_watts,
    program_for_battery,
    read_battery_snapshot,
)
from .collector import (
    CLAUDE_TRANSCRIPT_PROVIDER,
    CODEX_TRANSCRIPT_PROVIDER,
    COMPLETED_VISIBLE_SECONDS,
    AgentMonitor,
    LiveAgentMonitor,
    SourceSpec,
    aggregate_status,
    default_sources,
    read_recent_lines,
)
from .colors import (
    BLEND_MODE_CHOICES,
    BLEND_MODE_CYCLE,
    BLEND_MODE_DESCRIPTIONS,
    BLEND_MODE_LABELS,
    BLEND_MODE_ROUND_ROBIN,
    FADE_MODE_KEYS,
    MODE_COLOR_KEYS,
    PRESET_CHOICES,
    PRESET_CUSTOM,
    PRESET_DESCRIPTIONS,
    PRESET_LABELS,
    ColorSettings,
    apply_preset,
    program_for_snapshot,
)
from .device_writer import (
    DEFAULT_FILE_NAME,
    MOUNT_ROOT,
    DeviceCandidate,
    DeviceWriteError,
    discover_devices,
    normalize_led_text,
    path_exists,
    target_from_device_path,
    validate_led_text,
    write_led_program,
)
from .install import (
    install_provider_hooks,
    uninstall_provider_hooks,
)
from .ipc import (
    HookEventServer,
    another_instance_alive,
    default_event_socket_path,
    default_latest_state_path,
)
from .keep_awake import KEEPALIVE_FILE_NAME, KeepAwakeController
from .led_status import (
    MAX_CHANNEL_GAIN,
    MIN_CHANNEL_GAIN,
    AgentLedController,
    LedDisplayState,
    apply_brightness,
    apply_channel_gain_to_program,
    apply_resting_glow_to_program,
    brightness_percent,
    led_count_for_target,
    normalize_brightness,
    normalized_device_name,
    quota_runway_program,
    scale_hex_brightness,
    style_to_program,
    timer_fill_program,
    write_mode_to_leds,
)
from .led_wasm import LedWasmUnavailableError, SdLedWasmController
from .lid_sleep import (
    LID_POLL_SECONDS,
    ClosedLidAwakeController,
    read_lid_closed,
    sleep_helper_install_command,
    sleep_helper_installed,
)
from .models import MODE_LABELS, AgentMode, AgentStatus
from .providers import (
    HOOK_PROVIDERS,
    PROVIDER_SPECS,
    ProviderConfig,
    default_state_dir,
    detect_log_path,
    parse_log_line,
    provider_spec,
)
from .sd_eject_guard_launch import (
    SD_EJECT_GUARD_DISPLAY_NAME,
    install_sd_eject_guard,
    sd_eject_guard_installed,
    uninstall_sd_eject_guard,
)
from .session_actions import (
    SESSION_OPEN_APP,
    SESSION_OPEN_TERMINAL,
    SESSION_OPEN_VSCODE,
    available_session_open_actions,
    default_session_open_action,
    session_open_action_label,
    session_open_target,
)
from .settings import (
    CALIBRATION_PROFILE_SLOTS,
    CLOSED_LID_AWAKE_AGENTS,
    CLOSED_LID_AWAKE_ALWAYS,
    CLOSED_LID_AWAKE_CHOICES,
    CLOSED_LID_AWAKE_NEVER,
    DEFAULT_NOTIFICATION_APP_COLORS,
    LED_DISPLAY_AGENT,
    LED_DISPLAY_BATTERY,
    LED_DISPLAY_CHOICES,
    LED_DISPLAY_QUOTA_RUNWAY,
    LED_DISPLAY_STUDIO,
    LED_DISPLAY_TIMER,
    LID_ANIMATION_CLOSED,
    LID_ANIMATION_CLOSED_ACTIVE,
    LID_ANIMATION_OPEN,
    LID_ANIMATION_OPEN_ACTIVE,
    LedAnimationSetting,
    default_lid_animation,
    default_settings_path,
    load_settings,
    normalize_animation_duration,
    save_settings,
)
from .status_bar_launch import (
    LAUNCH_AGENT_LABEL,
    install_launch_agent,
    launch_agent_installed,
)
from .virtual_device import (
    VIRTUAL_DEVICE_ID,
    VIRTUAL_DEVICE_NAME,
    VirtualLedView,
    VirtualStatusDevice,
    monotonic_ms,
    slot_width_for_screen,
)
from .virtual_device import (
    WINDOW_HEIGHT as SCREEN_BAR_PREVIEW_HEIGHT,
)


@dataclass(frozen=True)
class StatusBarState:
    label: str
    symbol: str
    priority: int


@dataclass(frozen=True)
class StatusBarDevice:
    device_id: str
    name: str
    root: Path
    target: Path
    connected: bool
    display: str
    brightness: int = 255
    auto_brightness_enabled: bool = False
    channel_gains: tuple[float, float, float] = (1.0, 1.0, 1.0)
    resting_glow: float = 0.0
    signal_policy: str | None = None
    reason: str = ""


STATE_IDLE = StatusBarState("Idle", "circle", 4)
STATE_WORKING = StatusBarState("Working", "arrow.triangle.2.circlepath", 2)
STATE_DONE = StatusBarState("Done", "checkmark.circle", 3)
STATE_ASK = StatusBarState("Ask", "questionmark.circle", 1)
STATUS_BAR_DEVICE_PRIORITY = ("sidepulsepro", "sidepulsedot")
STATUS_BAR_KEEPALIVE_VOLUME_NAMES = (
    "SidePulsePro",
    "SidePulseDot",
)
# Runtime-only display kind (never a persisted per-device choice): the
# low-battery reminder takes over every display while active.
LED_DISPLAY_LOW_BATTERY = "low_battery"
LED_DISPLAY_NOTIFICATION = "notification"
LED_DISPLAY_CALENDAR = "calendar"
LED_DISPLAY_ESCALATION = "escalation"
LED_DISPLAY_TEST = "signal_test"
SIGNAL_TEST_SECONDS = 5.0
LED_DISPLAY_COMPLETION = "completion"
LED_DISPLAY_REMINDERS = "reminders"
REMINDERS_WATCH_SECONDS = 60.0
REMINDERS_WATCH_RETRY_SECONDS = 300.0
LED_DISPLAY_WEATHER = "weather"
LED_DISPLAY_QUOTA = "quota_alert"
LED_DISPLAY_ALL_CLEAR = "all_clear"
LED_DISPLAY_PEEK = "peek"
WEATHER_WATCH_SECONDS = 600.0
WEATHER_WATCH_RETRY_SECONDS = 1800.0
CALENDAR_WATCH_SECONDS = 30.0
CALENDAR_WATCH_RETRY_SECONDS = 300.0
# How often the Notification Center store is polled (one indexed
# rec_id query on a small database), and how long to back off after a
# failed read (no Full Disk Access yet, database locked, ...).
NOTIFICATION_WATCH_SECONDS = 2.0
NOTIFICATION_WATCH_RETRY_SECONDS = 60.0

STATUS_BAR_REFRESH_SECONDS = 15.0
# How often the screen-brightness watcher samples (one cheap ctypes call)
# and the minimum 0-255 delta that counts as a real change -- small enough
# to feel continuous, large enough that sensor jitter never causes writes.
BRIGHTNESS_WATCH_SECONDS = 3.0
BRIGHTNESS_WATCH_MIN_DELTA = 3
STATUS_BAR_DEVICE_POLL_SECONDS = 2.0
# Event-driven refreshes run at most this often; bursts coalesce into
# one trailing refresh. Direct refresh_(None) calls stay synchronous.
EVENT_REFRESH_FLOOR_SECONDS = 0.25
MIN_ESCALATION_VISIBLE_BRIGHTNESS = 12
# Display kinds that are MOMENTS demanding attention -- they render at
# the device's configured brightness, not the auto/idle/Focus-dimmed
# ambient level ("the flashing light is very dim even though its
# brightness should be 100%"). An explicit per-Focus "Turn off" (scale
# exactly 0) still silences them.
SIGNAL_DISPLAY_KINDS = frozenset()
# Per-device "asks only" mutes exactly these courtesy moments -- never
# test, escalation, weather, or low battery (documented invariants:
# low battery is "every device at once"; blocked-on-you and emergencies
# always break through).
DEVICE_MUTABLE_SIGNAL_KINDS = frozenset(
    {
        LED_DISPLAY_NOTIFICATION,
        LED_DISPLAY_QUOTA,
        LED_DISPLAY_REMINDERS,
        LED_DISPLAY_COMPLETION,
        LED_DISPLAY_ALL_CLEAR,
        LED_DISPLAY_CALENDAR,
        LED_DISPLAY_PEEK,
    }
)
# A completion may only ring/sweep while this fresh; older ones repaint
# without celebrating (T3's TERMINAL_NOTIFICATION_FRESHNESS).
COMPLETION_NOTIFY_FRESHNESS_SECONDS = 120.0

# Story #8 (night warmth): per-channel multipliers applied 19:00-07:00
# when the toggle is on. Red full, green -13%, blue -30% -- warm enough
# to notice, subtle enough that status colors stay unambiguous.
NIGHT_WARMTH_GAINS = (1.0, 0.87, 0.70)
# Story #10: the timebox presets offered in the dropdown -- also the
# rows of the Focus-handshake card, so the two stay in lockstep.
TIMEBOX_PRESET_MINUTES = (15, 25, 45, 60)
# ioreg is a subprocess fork on the main thread and refresh_ runs on
# every hook event; power-state changes may lag by up to this TTL.
BATTERY_SNAPSHOT_CACHE_SECONDS = 5.0
# One /Volumes scan per second instead of 4-6 per refresh.
DEVICE_DISCOVERY_CACHE_SECONDS = 1.0
SCREEN_BAR_FEATURE_ENABLED = True
STATUS_BAR_MAX_LINES_PER_SOURCE = 500
STATUS_BAR_STARTUP_REPLAY_LINES = 200
LID_ANIMATION_RESTORE_FUDGE_SECONDS = 0.15
LID_ANIMATION_LABELS = {
    LID_ANIMATION_CLOSED: "Lid Closed",
    LID_ANIMATION_OPEN: "Lid Open",
    LID_ANIMATION_CLOSED_ACTIVE: "Lid Closed (agents running)",
    LID_ANIMATION_OPEN_ACTIVE: "Lid Open (agents running)",
}
CLOSED_LID_AWAKE_LABELS = {
    CLOSED_LID_AWAKE_NEVER: "Never",
    CLOSED_LID_AWAKE_AGENTS: "When Agents Work",
    CLOSED_LID_AWAKE_ALWAYS: "Always",
}


def state_for_mode(mode: AgentMode) -> StatusBarState:
    if mode in {AgentMode.WAITING_FOR_INPUT, AgentMode.BLOCKED_ERROR}:
        return STATE_ASK
    if mode in {
        AgentMode.WORKING,
        AgentMode.TOOL_RUNNING,
        AgentMode.LONG_TASK_PROGRESS,
    }:
        return STATE_WORKING
    if mode == AgentMode.COMPLETED:
        return STATE_DONE
    return STATE_IDLE


def replay_recent_debug_logs(
    monitor: LiveAgentMonitor,
    *,
    providers: tuple[str, ...] = HOOK_PROVIDERS,
    max_lines: int = STATUS_BAR_STARTUP_REPLAY_LINES,
) -> int:
    replayed = 0
    for provider in providers:
        try:
            path = detect_log_path(provider)
            lines = read_recent_lines(path, max_lines) if path.exists() else []
        except Exception as exc:
            log_status_bar(f"startup_replay {provider} error: {exc}")
            continue

        for line in lines:
            record = parse_log_line(provider, line)
            if record is None:
                continue
            monitor.ingest_record(record)
            replayed += 1
    return replayed


class StatusBarController(NSObject):
    def init(self):
        self = objc.super(StatusBarController, self).init()
        if self is None:
            return None

        self.settings = load_settings()
        self.monitor = self.build_monitor()
        self.transcript_monitor = self.build_transcript_monitor()
        self.transcript_watermark = None
        self.event_server = None
        self.status_item = None
        self.timer = None
        self.lid_timer = None
        self.device_timer = None
        self.settings_window = None
        self.setup_window = None
        self.colors_window = None
        self.settings_fields = {}
        self.settings_buttons = {}
        self.device_settings_controls = {}
        self.settings_sidebar_table = None
        self.settings_panes = {}
        self._device_calibration_popover = None
        # (device_id, hex) while a calibration test patch is lighting the
        # device; None otherwise. See startCalibrationTest_.
        self.calibration_test = None
        self.setup_fields = {}
        self.setup_buttons = {}
        self.setup_demo_timer = None
        self.color_swatches = {}
        self.color_hex_labels = {}
        self.color_fields = {}
        self.color_preview_rows = []
        # Native apps apply changes immediately -- picking a color that
        # doesn't visibly do anything until you find and flip a separate
        # toggle reads as broken. Live-apply defaults on; the checkbox is an
        # opt-out (e.g. to audition several changes before committing them
        # to the physical device), not an opt-in.
        self.color_preview_enabled = True
        # In-window animated preview: one WASM controller per device row
        # (led_count -> controller), stepped on a timer while the Colors
        # window is open, so Round-Robin/Cycle/pulse actually animate in
        # the preview instead of showing one static frame.
        self.color_preview_wasm = {}
        self.color_preview_programs = {}
        self.color_preview_timer = None
        # Which canned situation the preview shows -- Live Activity (the
        # default) prefers whatever's really running and only falls back to
        # a fixed demo when nothing is; any other choice always shows that
        # scenario regardless of real activity, so a new user with nothing
        # running yet (or a curious existing user) can see every blend mode
        # against a busy team, a lone agent, two sessions of the same
        # provider, etc. Transient UI state -- never persisted to settings.
        self.color_preview_scenario = colors_module.PREVIEW_SCENARIO_LIVE
        self.active_color_target = None
        self.last_snapshot = None
        self.last_battery_snapshot = None
        self.last_battery_error = None
        self.last_power_connected = None
        self.battery_preview_until = 0.0
        # Notification blinks: cursor into the Notification Center
        # store, the transient blink window, and a backoff so a missing
        # FDA grant costs one failed query a minute, not one per tick.
        self.notification_record_cursor: int | None = None
        self.notification_blink_until = 0.0
        self.quota_blink_until = 0.0
        self.quota_blink_color = None
        self.quota_blink_label = None
        self.quota_last_percents: dict[str, float] = {}
        self.notification_blink_color: str | None = None
        self.notification_watch_retry_at = 0.0
        # Calendar glow: monotonic deadline of the next event's start
        # (0.0 = no upcoming event inside the lead window).
        self.calendar_glow_until = 0.0
        self.calendar_event_title: str | None = None
        self.calendar_watch_retry_at = 0.0
        # Reminders glow: transient window plus the per-run set of
        # reminder ids that already glowed (a due-and-incomplete
        # reminder stays "due" every poll -- it must fire ONCE).
        self.reminders_glow_until = 0.0
        self.reminders_seen: dict[str, float] = {}
        self.reminders_watch_retry_at = 0.0
        # Weather emergency: alert active right now (state, not moment).
        self.weather_alert_active = False
        self.weather_alert_event: str | None = None
        self.weather_watch_retry_at = 0.0
        self.weather_fetch_in_flight = False
        self.current_state = STATE_IDLE
        # None until set_status() actually confirms Idle -- avoids assuming
        # "idle since launch" if the real initial state turns out to be
        # something else once the first real snapshot arrives.
        self.idle_since_monotonic: float | None = None
        # Ask escalation: when the aggregate first entered ask/blocked
        # (None = not blocked), the last applied stage, the menu-bar
        # flash timer, and whether this block episode already chimed.
        self.ask_blocked_since: float | None = None
        self.ask_blocked_by_agent: dict[str, float] = {}
        self.escalation_last_stage = 0
        self.escalation_flash_timer = None
        self.escalation_flash_on = False
        self.escalation_chimed = False
        self.led_controller = AgentLedController()
        self.battery_led_controller = BatteryLedController()
        self.agent_led_controllers_by_device = {}
        self.battery_led_controllers_by_device = {}
        self.last_led_display_kind_by_device = {}
        self.device_errors = {}
        self.leds_enabled = True
        self.led_sync_in_flight = False
        self.last_watched_brightness = None
        self.last_watched_focus_scale = None
        self.last_led_error = None
        self.last_led_display_kind = LED_DISPLAY_AGENT
        self.last_connected_device_signature = None
        self.keep_awake = KeepAwakeController()
        self.closed_lid_awake = ClosedLidAwakeController(
            use_system_disable=sleep_helper_installed(),
        )
        self.last_keep_awake_error = None
        self.last_closed_lid_awake_error = None
        self.last_status_read_error = None
        self.event_refresh_pending = False
        self.last_lid_closed = None
        self.last_lid_error = None
        self.led_animation_until_monotonic = 0.0
        self.led_animation_token = 0
        self.virtual_status_device = VirtualStatusDevice.alloc().init()
        # Backlog #20: every attribute handlers previously sprang
        # into existence via getattr now has ONE canonical default
        # here -- the getattr call sites keep working, but drift
        # between per-site defaults (current_settings_pane "" vs
        # None, lid thumbs {} vs None) can no longer happen.
        self.all_clear_until = 0.0
        self.cleared_session_ids = set()
        self.color_panel_signal_key = None
        self.colors_animation_thumbs = {}
        self.colors_preview_baseline = None
        self.completion_sweep_color = None
        self.completion_sweep_until = 0.0
        self.current_settings_pane = None
        self.display_claim_errors_logged = None
        self.escalation_webhooked = False
        self.hooks_update_in_flight = False
        self.last_active_focus_ids = set()
        self.last_agent_modes = {}
        self.led_sync_dropped = False
        self.lid_animation_thumbs = {}
        self.lid_poll_in_flight = False
        self.notification_poll_in_flight = False
        self.peek_until = 0.0
        self.quiet_until_monotonic = 0.0
        self.signal_preview_timer = None
        self.status_menu_open = False
        self.studio_editor = None
        self.studio_library_popup = None
        self.studio_preview_program = ""
        self.studio_save_name_field = None
        self.test_signal_key = None
        self.test_signal_until = 0.0
        self.timebox_ends_at = None
        self.timebox_overtime_since = None
        self.timebox_total_seconds = 0.0
        self.timer_minutes_field = None
        self.tip_anchor_views = {}
        self.transcript_fallback_signature = None
        self.working_since = None
        self.working_since_by_agent = {}
        self._battery_snapshot_cache = None
        self._device_discovery_cache = None
        self._discovery_revalidating = False
        self._focus_ids_cache = None
        self._focus_summary_cache = None
        self._keepalive_poke_in_flight = False
        self._last_event_refresh_at = 0.0
        self._menu_rebuild_pending = None
        self._menu_signature = None
        self._pane_transition_generation = 0
        self._peek_hits = 0
        self._peek_timer = None
        self._provider_probe_at = 0.0
        self._settings_message_timer = None
        self._settings_pane_container = None
        self._setup_no_hooks_warned = False
        self._signal_card_rendered = None
        self._studio_validation_cache = None
        self._timebox_off_shortcut = None
        self._trailing_refresh_timer = None
        self._usage_refresh_in_flight = False
        self._usage_refreshed_at = 0.0
        return self

    def applicationDidFinishLaunching_(self, _notification):
        try:
            from AppKit import NSUserNotificationCenter

            NSUserNotificationCenter.defaultUserNotificationCenter().setDelegate_(self)
        except Exception:
            pass
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        log_status_bar("launching status item")
        self.start_event_server()
        self.replay_debug_logs()

        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        button = self.status_item.button()
        button.setTitle_(" Idle")
        button.setImage_(image_for_symbol(STATE_IDLE.symbol, STATE_IDLE.label))
        button.setToolTip_("SidePulse Agent Monitor: Idle")
        log_status_bar("status item created")

        self.refresh_(None)
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            STATUS_BAR_REFRESH_SECONDS,
            self,
            "refresh:",
            None,
            True,
        )
        self.lid_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            LID_POLL_SECONDS,
            self,
            "pollLid:",
            None,
            True,
        )
        self.device_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            STATUS_BAR_DEVICE_POLL_SECONDS,
            self,
            "pollDevices:",
            None,
            True,
        )
        # Screen-brightness watcher: auto-brightness used to re-evaluate
        # only when an agent state change happened to trigger an LED
        # write, so dimming the screen during a steady state changed
        # nothing -- "doesn't react at all". This cheap poll (one ctypes
        # call) triggers a full re-sync only when the reading actually
        # moves, so tracking feels immediate without extra LED writes.
        self.brightness_watch_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            BRIGHTNESS_WATCH_SECONDS,
            self,
            "pollScreenBrightness:",
            None,
            True,
        )
        # Notification-blink watcher: silently inert until Full Disk
        # Access is granted (see notification_watch.py).
        self.notification_watch_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            NOTIFICATION_WATCH_SECONDS,
            self,
            "pollNotifications:",
            None,
            True,
        )
        # Calendar watcher: inert until the user enables the glow and
        # grants Calendars access (see calendar_watch.py).
        self.calendar_watch_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            CALENDAR_WATCH_SECONDS,
            self,
            "pollCalendar:",
            None,
            True,
        )
        # Reminders watcher: same contract, async fetches.
        self.reminders_watch_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            REMINDERS_WATCH_SECONDS,
            self,
            "pollReminders:",
            None,
            True,
        )
        # Weather watcher: network fetch on a worker thread, every 10min.
        self.weather_watch_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            WEATHER_WATCH_SECONDS,
            self,
            "pollWeather:",
            None,
            True,
        )
        # NSTimer's FIRST fire is one full interval out -- an active
        # Tornado Warning must not stay dark for 10 minutes after launch.
        if self.settings.weather_alerts_enabled:
            self.pollWeather_(None)
        # Rotate oversized hook/event logs off the main thread.
        threading.Thread(
            target=lambda: trim_oversized_logs(default_state_dir()),
            daemon=True,
        ).start()
        self.show_setup_window_if_needed()
        if SCREEN_BAR_FEATURE_ENABLED and self.settings.virtual_status_device_enabled:
            self.virtual_status_device.show()
        else:
            self.virtual_status_device.hide()

    @objc.IBAction
    def pollScreenBrightness_(self, _sender):
        """The brightness/Focus watcher's tick (see the timer above): two
        cheap readings, and a full re-sync only when something genuinely
        moved. Screen brightness and the active Focus share one watcher
        because they share one failure mode -- both used to apply only
        when an agent state change happened to trigger an LED write."""
        needs_refresh = False

        if any(device.auto_brightness_enabled for device in self.settings.devices):
            try:
                reading = display_brightness.auto_led_brightness()
            except display_brightness.DisplayBrightnessUnavailableError:
                reading = None
            if reading is not None:
                last = self.last_watched_brightness
                self.last_watched_brightness = reading
                if last is not None and abs(reading - last) >= BRIGHTNESS_WATCH_MIN_DELTA:
                    needs_refresh = True

        if self.settings.focus_sync_enabled:
            scale = self.focus_sync_scale_factor()
            if self.last_watched_focus_scale is not None and scale != self.last_watched_focus_scale:
                needs_refresh = True
            self.last_watched_focus_scale = scale

        # Focus -> profile automation: a NEWLY activated Focus with a
        # rule applies its profile exactly once per activation.
        if self.settings.focus_profile_rules:
            try:
                active_focus = set(focus_sync.active_focus_mode_identifiers())
            except focus_sync.FocusSyncUnavailableError:
                active_focus = set()
            previous_focus = getattr(self, "last_active_focus_ids", set())
            newly_active = active_focus - previous_focus
            self.last_active_focus_ids = active_focus
            for focus_id in sorted(newly_active):
                slot = self.settings.focus_profile_rules.get(focus_id)
                if slot:
                    self.settings = self.settings.with_applied_calibration_profile(slot)
                    save_settings(self.settings)
                    self.set_settings_message(f"Focus started — applied the {slot} profile.")
                    needs_refresh = True
                    break

        # Timebox: chime once and revert when the countdown hits zero.
        ends_at = getattr(self, "timebox_ends_at", None)
        if ends_at is not None and time.monotonic() >= ends_at:
            self.timebox_ends_at = None
            self.timebox_total_seconds = 0.0
            # Overtime ember: hold a deepening glow until Stop.
            self.timebox_overtime_since = time.monotonic()
            # Story #10: the drain ending gives the Focus back.
            self.fire_timebox_off_shortcut()
            if self.webhook_event_enabled("timebox"):
                self.post_webhook({"event": "sidepulse.timebox_finished"})
            try:
                from AppKit import NSSound

                sound = NSSound.soundNamed_("Glass")
                if sound is not None:
                    sound.play()
            except Exception:
                pass
            self.set_settings_message("Timebox finished.")
            needs_refresh = True

        # Escalation stages advance with TIME, not events -- this shared
        # watcher tick is what promotes an ignored ask to the next stage.
        self.apply_escalation(allow_refresh=True)

        if needs_refresh:
            self.refresh_(None)

    @objc.IBAction
    def pollNotifications_(self, _sender):
        """Blink the LEDs in an app's color when it delivers a
        notification, then return to agent status. All failure modes
        (no FDA, schema drift) back off quietly -- this signal must
        never cost the user anything when it can't work."""
        if not self.settings.notification_blinks_enabled:
            return
        if not self.settings.notification_app_colors:
            return
        now = time.monotonic()
        if now < self.notification_watch_retry_at:
            return
        # sqlite on the Notification Center store blocked the main run
        # loop every 2s (0.5s worst case when usernoted holds the DB) --
        # audit finding #3. Worker thread, always-post contract.
        if getattr(self, "notification_poll_in_flight", False):
            return
        self.notification_poll_in_flight = True
        primed_cursor = self.notification_record_cursor

        def _work():
            try:
                if primed_cursor is None:
                    # First successful read just primes the cursor --
                    # pre-existing notifications must not replay.
                    payload = {
                        "ok": True,
                        "cursor": notification_watch.latest_record_id(),
                        "identifiers": [],
                    }
                else:
                    cursor, identifiers = notification_watch.delivered_after(
                        primed_cursor
                    )
                    payload = {
                        "ok": True,
                        "cursor": cursor,
                        "identifiers": list(identifiers),
                    }
            except notification_watch.NotificationWatchUnavailableError as exc:
                payload = {"ok": False, "error": str(exc)}
            except Exception as exc:
                payload = {"ok": False, "error": f"unexpected: {exc!r}"}
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "notificationsChecked:", payload, False
            )

        threading.Thread(target=_work, daemon=True).start()

    @objc.IBAction
    def notificationsChecked_(self, payload):
        self.notification_poll_in_flight = False
        now = time.monotonic()
        if not payload.get("ok"):
            self.notification_watch_retry_at = now + NOTIFICATION_WATCH_RETRY_SECONDS
            return
        self.notification_record_cursor = payload.get("cursor")
        identifiers = payload.get("identifiers") or []
        if not identifiers:
            return
        colors_by_app = self.settings.notification_app_colors
        matched = [colors_by_app[app] for app in identifiers if app in colors_by_app]
        if not matched:
            return
        # Several at once: the newest app's color wins; the blink says
        # "something arrived", the menu bar says what.
        hold = signals_module.signal_hold_seconds(
            self.settings.signal_style(signals_module.SIGNAL_NOTIFICATION)
        )
        self.notification_blink_color = matched[-1]
        self.notification_blink_until = now + hold
        self.refresh_(None)
        # One-shot revert back to the agent display when the window ends.
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            hold + 0.1,
            self,
            "refresh:",
            None,
            False,
        )

    @objc.IBAction
    def pollCalendar_(self, _sender):
        """Keeps calendar_glow_until pointing at the next event's start
        while one is inside the lead window. All failure paths (access
        not granted, EventKit absent) back off quietly."""
        if not self.settings.calendar_alerts_enabled:
            return
        now = time.monotonic()
        if now < self.calendar_watch_retry_at:
            return
        try:
            upcoming = calendar_watch.next_event_start(self.settings.calendar_lead_minutes)
        except calendar_watch.CalendarUnavailableError:
            self.calendar_watch_retry_at = now + CALENDAR_WATCH_RETRY_SECONDS
            return
        was_active = now < self.calendar_glow_until
        if upcoming is None:
            self.calendar_glow_until = 0.0
            self.calendar_event_title = None
            if was_active:
                self.refresh_(None)
            return
        title, start = upcoming
        from datetime import datetime, timezone

        seconds_until_start = (start - datetime.now(timezone.utc)).total_seconds()
        self.calendar_event_title = title
        self.calendar_glow_until = now + max(0.0, seconds_until_start)
        if not was_active:
            self.refresh_(None)

    @objc.IBAction
    def pollWeather_(self, _sender):
        """Fetches NWS alerts on a worker thread (urllib would block the
        main thread for seconds on a slow network) and hops the verdict
        back to the main thread."""
        if not self.settings.weather_alerts_enabled:
            return
        now = time.monotonic()
        if now < self.weather_watch_retry_at or self.weather_fetch_in_flight:
            return
        self.weather_fetch_in_flight = True
        latitude = self.settings.weather_latitude
        longitude = self.settings.weather_longitude

        def _fetch():
            try:
                if latitude is not None and longitude is not None:
                    location = (latitude, longitude)
                else:
                    location = weather_watch.ip_location()
                alerts = weather_watch.active_alerts(*location)
                payload = {"ok": True, "alerts": alerts}
            except weather_watch.WeatherUnavailableError as exc:
                payload = {"ok": False, "error": str(exc)}
            except Exception as exc:
                # A surprise here must still post a payload -- otherwise
                # weather_fetch_in_flight stays True forever and weather
                # alerts silently stop until the next app restart.
                payload = {"ok": False, "error": f"unexpected: {exc!r}"}
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "weatherChecked:", payload, False
            )

        threading.Thread(target=_fetch, daemon=True).start()

    @objc.IBAction
    def weatherChecked_(self, payload):
        self.weather_fetch_in_flight = False
        if not payload.get("ok"):
            self.weather_watch_retry_at = time.monotonic() + WEATHER_WATCH_RETRY_SECONDS
            return
        alerts = list(payload.get("alerts") or [])
        was_active = self.weather_alert_active
        self.weather_alert_active = bool(alerts)
        self.weather_alert_event = str(alerts[0][2]) if alerts else None
        if self.weather_alert_active != was_active:
            if self.weather_alert_active and self.webhook_event_enabled("weather"):
                self.post_webhook(
                    {
                        "event": "sidepulse.weather",
                        "headline": self.weather_alert_event or "",
                    }
                )
            self.refresh_(None)

    @objc.IBAction
    def pollReminders_(self, _sender):
        """Fires the amber glow once per newly-due reminder. EventKit
        reminder fetches are async; results come back on an EventKit
        queue and hop to the main thread via remindersDue:."""
        if not self.settings.reminder_alerts_enabled:
            return
        now = time.monotonic()
        if now < self.reminders_watch_retry_at:
            return
        try:
            reminders_watch.fetch_due(
                REMINDERS_WATCH_SECONDS * 2.0,
                lambda items: self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "remindersDue:", list(items), False
                ),
            )
        except reminders_watch.RemindersUnavailableError:
            self.reminders_watch_retry_at = now + REMINDERS_WATCH_RETRY_SECONDS
            return

    @objc.IBAction
    def remindersDue_(self, items):
        now = time.monotonic()
        # Prune ids that fell out of the fetch lookback long ago -- the
        # seen-map must not grow for the process's whole life.
        horizon = now - REMINDERS_WATCH_SECONDS * 8.0
        self.reminders_seen = {
            identifier: seen_at
            for identifier, seen_at in self.reminders_seen.items()
            if seen_at >= horizon
        }
        fresh = [
            (identifier, title)
            for identifier, title in (tuple(item) for item in (items or []))
            if identifier not in self.reminders_seen
        ]
        if not fresh:
            return
        for identifier, _title in fresh:
            self.reminders_seen[identifier] = now
        hold = signals_module.signal_hold_seconds(
            self.settings.signal_style(signals_module.SIGNAL_REMINDERS)
        )
        self.reminders_glow_until = time.monotonic() + hold
        self.refresh_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            hold + 0.1, self, "refresh:", None, False
        )

    @objc.IBAction
    def refresh_(self, _sender):
        try:
            self.ingest_transcript_fallback()
            snapshot = self.monitor.snapshot()
        except Exception as exc:
            log_status_bar(f"refresh error: {exc}")
            # No confirmable agent state: clear any escalation episode
            # rather than letting a stale one keep chiming/stroboing.
            self.track_ask_blocked(())
            self.set_status(STATE_ASK)
            self.sync_keep_awake(AgentMode.BLOCKED_ERROR)
            self.sync_leds(AgentMode.BLOCKED_ERROR, None, LED_DISPLAY_AGENT, ())
            # status_item is None before applicationDidFinishLaunching_ has
            # run (e.g. a settings action invoked in a headless/test
            # context) -- everything else above is still meaningful to do,
            # only the actual menu bar UI needs the real status item.
            if self.status_item is not None:
                self.status_item.setMenu_(build_error_menu(exc))
            return

        self.last_snapshot = snapshot
        battery_snapshot = self.read_battery_snapshot()
        display_mode = self.display_aggregate_mode(snapshot)
        state = state_for_mode(display_mode)
        self.observe_connected_devices()
        self.track_ask_blocked(snapshot.statuses)
        self.track_working(snapshot.statuses)
        # Completions need the FULL timeline: the collector demotes every
        # inactive status to stale_statuses the moment ANY session is
        # active, so a finishing session vanished from .statuses on the
        # very tick it completed -- the sweep/banner could only ever
        # fire for the LAST session alive (the missed-celebration bug,
        # reproduced live with an injected session). The transition
        # detector's own freshness gate keeps replays silent.
        self.track_completions((*snapshot.statuses, *snapshot.stale_statuses))
        self.maybe_refresh_usage_summary()
        # A cleared session stays cleared until it comes back to LIFE --
        # removing only reactivated ids (rather than intersecting with
        # the currently-completed set) keeps a clear stable across the
        # stale flicker when a finished row ages out of the snapshot.
        cleared = getattr(self, "cleared_session_ids", set())
        if cleared:
            active_again = {
                status.agent_id
                for status in snapshot.statuses
                if status.mode != AgentMode.COMPLETED
            }
            self.cleared_session_ids = cleared - active_again
        self.set_status(
            state,
            ask_count=len(ask_statuses(snapshot)),
            done_badge=bool(unseen_completions(snapshot, self)),
        )
        self.sync_keep_awake(snapshot.aggregate.mode)
        self.sync_leds(
            display_mode,
            battery_snapshot,
            self.active_led_display_kind(battery_snapshot),
            snapshot.statuses,
        )
        if self.status_item is not None:
            self.update_status_menu(snapshot, state)

    def update_status_menu(self, snapshot, state) -> None:
        """Rebuild the dropdown only when its CONTENT changed, and never
        while it is open. Rebuilding on every hook event re-ran icon
        composites and row construction dozens of times a minute, and
        re-sorting under an open menu made rows jump beneath the cursor
        (T3's inbox keeps ordering static; signals carry the changes)."""
        if getattr(self, "status_menu_open", False):
            self._menu_rebuild_pending = (snapshot, state)
            return
        signature = menu_content_signature(snapshot, state, self)
        if signature == getattr(self, "_menu_signature", None):
            return
        self._menu_signature = signature
        menu = build_menu(snapshot, state, self)
        menu.setDelegate_(self)
        self.status_item.setMenu_(menu)

    def maybe_refresh_usage_summary(self) -> None:
        """Recompute the "Claude today" line on a worker thread at most
        every 5 minutes -- the scan is warm-cache cheap (T3's per-file
        cache) but never belongs on the main thread."""
        now = time.monotonic()
        if getattr(self, "_usage_refresh_in_flight", False):
            return
        if now - getattr(self, "_usage_refreshed_at", 0.0) < 300.0:
            return
        self._usage_refresh_in_flight = True

        def _work():
            try:
                midnight = datetime.now().replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                totals = usage_stats.scan_usage(
                    Path.home() / ".claude" / "projects",
                    default_state_dir() / "usage-scan-cache.json",
                    since_epoch=midnight.timestamp(),
                    codex_root=Path.home() / ".codex" / "sessions",
                )
                line = usage_stats.usage_summary_line(
                    totals, self.settings.usage_display_mode
                )
                codex_parts = []
                if totals.codex_sessions:
                    codex_parts.append(
                        f"Codex today: {len(totals.codex_sessions)} sessions "
                        f"\u00b7 {totals.codex_tokens / 1_000_000:.0f}M tokens"
                    )
                limits = usage_stats.codex_rate_limits(
                    Path.home() / ".codex" / "sessions"
                )
                percents = {}
                primary = (limits or {}).get("primary") or {}
                if primary.get("used_percent") is not None:
                    if self.settings.codex_percent_enabled:
                        codex_parts.append(
                            f"weekly limit {primary['used_percent']:.0f}% used"
                        )
                    percents["Codex weekly"] = float(primary["used_percent"])
                codex_line = " \u00b7 ".join(codex_parts) if codex_parts else None
                graph_days = self.settings.usage_graph_days
                buckets = usage_stats.daily_buckets(totals.records, days=graph_days)
                label_stride = max(1, graph_days // 7)
                day_bars = [
                    {
                        # Sparse labels at long ranges -- a year of 365
                        # date stamps is noise, not an axis.
                        "label": (
                            day[5:].replace("-", "/")
                            if index % label_stride == 0
                            else ""
                        ),
                        "claude_cost": bucket["claude_cost"],
                        "codex_tokens": bucket["codex_tokens"],
                    }
                    for index, (day, bucket) in enumerate(buckets.items())
                ]
                hourly = usage_stats.hourly_session_counts(totals.records)
                plan_line = None
                if self.settings.claude_plan_limits_enabled:
                    try:
                        plan_windows = claude_quota.fetch_windows()
                        plan_line = claude_quota.summary_line(plan_windows)
                        for window in plan_windows:
                            percents[f"Claude {window['label']}"] = float(
                                window["utilization"]
                            )
                    except claude_quota.ClaudeQuotaUnavailableError as exc:
                        log_status_bar(f"claude quota unavailable: {exc}")
                        plan_line = "Claude plan: unavailable right now."
                detail = (
                    f"{totals.input_tokens:,} in \u00b7 "
                    f"{totals.cached_input_tokens:,} cached \u00b7 "
                    f"{totals.output_tokens:,} out"
                    if totals.sessions
                    else None
                )
            except Exception as exc:
                log_status_bar(f"usage scan error: {exc}")
                line = None
                detail = None
                codex_line = None
                day_bars = []
                hourly = []
                plan_line = None
                percents = {}
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyUsageSummary:",
                (line, detail, codex_line, day_bars, hourly, plan_line, percents),
                False,
            )

        threading.Thread(target=_work, daemon=True).start()

    @objc.IBAction
    def applyUsageSummary_(self, payload):
        self._usage_refresh_in_flight = False
        self._usage_refreshed_at = time.monotonic()
        line = payload[0] if payload else None
        detail = payload[1] if payload and len(payload) > 1 else None
        self.usage_detail_text = detail
        self.usage_summary_text = line
        self.codex_summary_text = payload[2] if payload and len(payload) > 2 else None
        fields = getattr(self, "settings_fields", None) or {}
        usage_label = fields.get("profile_usage_label")
        if usage_label is not None:
            usage_label.setStringValue_(line or "No Claude activity yet today.")
        detail_label = fields.get("profile_usage_detail")
        if detail_label is not None:
            detail_label.setStringValue_(detail or "")
        codex_label = fields.get("profile_codex_label")
        if codex_label is not None:
            codex_label.setStringValue_(self.codex_summary_text or "")
        # Stash for panes built AFTER this publish -- the first worker
        # cycle usually finishes before the settings window ever exists,
        # and the graph sat empty until the next 5-minute pass.
        self.usage_day_bars = payload[3] if payload and len(payload) > 3 else []
        self.usage_hourly = payload[4] if payload and len(payload) > 4 else []
        graph = fields.get("profile_usage_graph")
        if graph is not None:
            graph.setData_hourly_(self.usage_day_bars, self.usage_hourly)
        self.claude_plan_text = (
            payload[5] if payload and len(payload) > 5 else None
        )
        plan_label = fields.get("profile_plan_label")
        if plan_label is not None:
            plan_label.setStringValue_(self.claude_plan_text or "")
        percents = payload[6] if payload and len(payload) > 6 else {}
        self.track_quota_thresholds(dict(percents or {}))

    def track_quota_thresholds(self, percents: dict) -> None:
        """Edge-triggered: a blink fires only when a window CROSSES a
        configured threshold upward -- first observations, restarts and
        repaints stay silent (the T3 transition rule)."""
        previous = self.quota_last_percents
        self.quota_last_percents = percents
        if not self.settings.quota_alerts_enabled or not percents:
            return
        # Quota sunrise: the moment a window RESETS, one upward sweep in
        # the provider's color -- "you're rich again" announces itself.
        resets = signals_module.quota_resets(previous, percents)
        if resets and not self.courtesy_signals_held():
            window_key = resets[-1]
            brand = {"Claude": "#D97757", "Codex": "#10A37F"}
            self.completion_sweep_color = next(
                (
                    hex_value
                    for name, hex_value in brand.items()
                    if window_key.startswith(name)
                ),
                "#00FF66",
            )
            hold = signals_module.signal_hold_seconds(
                self.settings.signal_style(signals_module.SIGNAL_COMPLETION)
            )
            self.completion_sweep_until = time.monotonic() + hold
            log_status_bar(f"quota sunrise: {window_key} reset")
            if self.webhook_event_enabled("quota_sunrise"):
                self.post_webhook(
                    {"event": "sidepulse.quota_sunrise", "window": window_key}
                )
            self.refresh_(None)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                hold + 0.1, self, "refresh:", None, False
            )
        fired = signals_module.quota_crossings(
            previous, percents, self.settings.quota_alert_thresholds
        )
        if not fired or self.courtesy_signals_held():
            return
        window_key, threshold = fired[-1]
        brand = {"Claude": "#D97757", "Codex": "#10A37F"}
        self.quota_blink_color = next(
            (hex_value for name, hex_value in brand.items() if window_key.startswith(name)),
            None,
        )
        self.quota_blink_label = f"{window_key} at {threshold:.0f}%"
        hold = signals_module.signal_hold_seconds(
            self.settings.signal_style(signals_module.SIGNAL_QUOTA)
        )
        self.quota_blink_until = time.monotonic() + hold
        log_status_bar(f"quota alert: {self.quota_blink_label}")
        if self.webhook_event_enabled("quota_threshold"):
            self.post_webhook(
                {
                    "event": "sidepulse.quota_threshold",
                    "window": window_key,
                    "threshold": threshold,
                }
            )
        self.refresh_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            hold + 0.1, self, "refresh:", None, False
        )

    def release_preview_engines(self) -> None:
        """Drop EVERY preview surface's WASM engine when settings goes
        away -- the first version swept 28 of ~98 engines (mode + lid
        thumbs only) and left the signal cards' contexts resident.
        Everything rebuilds lazily on next open."""
        groups = list(getattr(self, "colors_animation_thumbs", {}).values())
        singles = getattr(self, "lid_animation_thumbs", {})
        for thumbs in groups:
            for thumb in thumbs.values():
                thumb.wasm_controller = None
        for thumb in singles.values():
            thumb.wasm_controller = None
        for field_key, view in (getattr(self, "settings_fields", None) or {}).items():
            if field_key.startswith(("signal_preview:", "signal_color:")):
                view.wasm_controller = None
            elif field_key.startswith("signal_thumbs:"):
                for thumb in view.values():
                    thumb.wasm_controller = None
        # BOTH color-preview dicts together: clearing only the engines
        # leaves the program-match early-return handing back stale None
        # and silently killing the preview.
        self.color_preview_wasm = {}
        self.color_preview_programs = {}
        # Rendered-style memo too, so reopened cards re-render fresh.
        self._signal_card_rendered = {}

    def windowWillClose_(self, notification):
        if notification.object() is getattr(self, "settings_window", None):
            self.stop_colors_preview_animation()
            self.release_preview_engines()

    def menuWillOpen_(self, _menu):
        self.status_menu_open = True
        # Opening the menu is a "visit": it clears the unseen-done badge
        # (mirrors T3's lastVisitedAt read/unread model).
        self.menu_last_opened_at = datetime.now(timezone.utc)

    def menuDidClose_(self, _menu):
        self.status_menu_open = False
        pending = getattr(self, "_menu_rebuild_pending", None)
        self._menu_rebuild_pending = None
        if pending is not None:
            # Deferred: never swap the menu during tracking teardown.
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "refresh:", None, False
            )

    @objc.IBAction
    def forceRefresh_(self, _sender):
        self.refresh_(None)

    @objc.IBAction
    def openSessionPrimary_(self, sender):
        self.open_session(
            sender.representedObject(),
            None,
            remember=False,
        )
        self.close_status_menu()

    @objc.IBAction
    def openSessionWithAction_(self, sender):
        payload = sender.representedObject()
        if not isinstance(payload, dict):
            return
        self.open_session(payload.get("status"), payload.get("action"), remember=True)
        self.close_status_menu()

    @objc.IBAction
    def setProviderOpenPreference_(self, sender):
        selected = sender.selectedItem()
        payload = selected.representedObject() if selected is not None else None
        if not isinstance(payload, dict):
            return
        provider = payload.get("provider")
        action = payload.get("action")
        if not isinstance(provider, str) or not isinstance(action, str):
            return
        try:
            self.settings = self.settings.with_provider_session_open_action(provider, action)
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save {provider.title()} opener: {exc}")
            self.settings = load_settings()
            self.refresh_settings_window()
            return
        self.set_settings_message(
            f"{provider.title()} sessions: {provider_open_action_label(provider, action)}."
        )

    @objc.IBAction
    def setClosedLidAwakePolicy_(self, sender):
        self.set_closed_lid_awake_policy(sender.representedObject())

    @objc.IBAction
    def setClosedLidAwakePolicyFromPopup_(self, sender):
        payload = sender.selectedItem().representedObject() if sender.selectedItem() else None
        if not payload or "policy" not in payload:
            return
        self.set_closed_lid_awake_policy(payload["policy"])

    @objc.IBAction
    def applyClosedLidGraceMinutes_(self, _sender):
        field = self.settings_fields.get("closed_lid_grace_field")
        minutes = parse_seconds_field(field)
        if minutes is None:
            return
        try:
            self.settings = self.settings.with_closed_lid_grace_minutes(minutes)
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save grace period: {exc}")
            self.settings = load_settings()
            return
        self.refresh_settings_window()
        self.set_settings_message(f"Closed-lid grace period: {self.settings.closed_lid_grace_minutes:g} min.")

    @objc.IBAction
    def toggleIdleDim_(self, sender):
        self.settings = self.settings.with_idle_dim_enabled(checkbox_is_on(sender))
        save_settings(self.settings)
        self.refresh_settings_window()

    @objc.IBAction
    def applyIdleDimSettings_(self, _sender):
        minutes_field = self.settings_fields.get("idle_dim_minutes_field")
        fraction_field = self.settings_fields.get("idle_dim_fraction_field")
        minutes = parse_seconds_field(minutes_field)
        fraction_percent = parse_seconds_field(fraction_field)
        if minutes is None or fraction_percent is None:
            return
        try:
            settings = self.settings.with_idle_dim_after_minutes(minutes)
            settings = settings.with_idle_dim_fraction(fraction_percent / 100.0)
            self.settings = settings
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save idle dimming: {exc}")
            self.settings = load_settings()
            return
        self.refresh_settings_window()
        self.set_settings_message(
            f"Idle dimming: after {self.settings.idle_dim_after_minutes:g} min, "
            f"dim to {round(self.settings.idle_dim_fraction * 100)}%."
        )

    @objc.IBAction
    def toggleFocusSync_(self, sender):
        self.settings = self.settings.with_focus_sync_enabled(checkbox_is_on(sender))
        save_settings(self.settings)
        self.refresh_settings_window()

    @objc.IBAction
    def toggleNightWarmth_(self, sender):
        self.settings = self.settings.with_night_warmth_enabled(checkbox_is_on(sender))
        save_settings(self.settings)
        self.refresh_settings_window()
        self.refresh_(None)

    @objc.IBAction
    def setFocusDimRule_(self, sender):
        identifier = str(sender.identifier() or "")
        if not identifier:
            return
        item = sender.selectedItem()
        raw = str(item.representedObject() or "default") if item is not None else "default"
        fraction = None if raw == "default" else float(raw)
        self.settings = self.settings.with_focus_dim_rule(identifier, fraction)
        save_settings(self.settings)
        label = item.title() if item is not None else "Shared dim"
        self.set_settings_message(f"Focus rule saved: {label}.")
        self.refresh_(None)

    @objc.IBAction
    def applyTimeboxShortcuts_(self, _sender):
        settings = self.settings
        for preset_minutes in TIMEBOX_PRESET_MINUTES:
            on_field = self.settings_fields.get(f"timebox_on_field:{preset_minutes}")
            off_field = self.settings_fields.get(f"timebox_off_field:{preset_minutes}")
            if on_field is None or off_field is None:
                continue
            settings = settings.with_timebox_shortcut(
                str(preset_minutes),
                str(on_field.stringValue()),
                str(off_field.stringValue()),
            )
        self.settings = settings
        save_settings(self.settings)
        self.set_settings_message("Timebox Focus handshake saved.")

    @objc.IBAction
    def setFocusSignalPolicy_(self, sender):
        identifier = str(sender.identifier() or "")
        if not identifier:
            return
        item = sender.selectedItem()
        policy = str(item.representedObject() or "all") if item is not None else "all"
        self.settings = self.settings.with_focus_signal_policy(identifier, policy)
        save_settings(self.settings)
        label = item.title() if item is not None else "All signals"
        self.set_settings_message(f"Focus signals: {label.lower()}.")
        self._focus_summary_cache = None
        self.refresh_(None)

    @objc.IBAction
    def openFullDiskAccessSettings_(self, _sender):
        NSWorkspace.sharedWorkspace().openURL_(
            NSURL.URLWithString_("x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")
        )

    @objc.IBAction
    def revealFocusBinaryInFinder_(self, _sender):
        """Shows the exact interpreter binary Full Disk Access must be
        granted to -- users can drag it straight from this Finder window
        into the Privacy Settings list."""
        if running_inside_bundle():
            target_path = str(default_app_bundle_path())
        else:
            target_path = os.path.realpath(sys.executable or "")
        if target_path:
            NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_(
                [NSURL.fileURLWithPath_(target_path)]
            )

    @objc.IBAction
    def toggleCalendarAlerts_(self, sender):
        enabled = bool(sender.state())
        self.settings = self.settings.with_calendar_alerts_enabled(enabled)
        save_settings(self.settings)
        self.calendar_watch_retry_at = 0.0
        if not enabled:
            self.calendar_glow_until = 0.0
            self.set_settings_message("Calendar glow off.")
            self.refresh_(None)
            return
        try:
            status = calendar_watch.authorization_status()
        except calendar_watch.CalendarUnavailableError:
            self.set_settings_message("Calendar access is unavailable on this system.")
            return
        if status == calendar_watch.AUTH_AUTHORIZED:
            self.set_settings_message("Calendar glow on.")
            self.pollCalendar_(None)
        elif status == calendar_watch.AUTH_NOT_DETERMINED:
            self.set_settings_message("Calendar glow on — asking macOS for access…")

            def _granted(ok):
                # EventKit calls back off the main thread.
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "calendarAccessResolved:", bool(ok), False
                )

            calendar_watch.request_access(_granted)
        else:
            self.set_settings_message(
                "Calendar access is denied — enable SidePulse under "
                "Privacy & Security → Calendars."
            )

    # --- Signal style cards --------------------------------------------

    def _current_signal_style(self, key: str):
        return self.settings.signal_style(key)

    def _save_signal_style(self, key: str, style) -> None:
        # Mid-drag, preview only: committing per drag tick meant a disk
        # write + full refresh (menu rebuild, LED write, subprocesses)
        # per mouse movement -- the same rule every other continuous
        # slider in this file follows.
        if self._slider_event_is_drag():
            self._render_signal_card(key, style.normalized())
            return
        self.settings = self.settings.with_signal_style(key, style)
        save_settings(self.settings)
        self.refresh_signal_card(key)
        self.refresh_(None)

    def refresh_signal_card(self, key: str) -> None:
        # Card rendering builds WASM thumbnail programs -- skip entirely
        # when the style hasn't changed since the last render (switching
        # TO the Signals pane re-rendered all seven cards synchronously,
        # which read as "going between menus is so laggy").
        style = self.settings.signal_style(key)
        cache = getattr(self, "_signal_card_rendered", None)
        if cache is None:
            cache = {}
            self._signal_card_rendered = cache
        if cache.get(key) == style:
            return
        cache[key] = style
        self._render_signal_card(key, style)

    def _render_signal_card(self, key: str, style) -> None:
        """Re-renders one card's thumbnails, preview, and selection ring
        from the given style (saved, or transient mid-drag)."""
        preview_color = _signal_preview_color(self, key)
        thumbs = self.settings_fields.get(f"signal_thumbs:{key}")
        if isinstance(thumbs, dict):
            for pattern, thumb in thumbs.items():
                preview_style = signals_module.SignalStyle(
                    style.color, pattern, style.speed_seconds, style.intensity
                )
                thumb.setProgram_(style_to_program(preview_style, 255, color=preview_color))
            _apply_thumb_selection(thumbs, style.pattern)
        preview = self.settings_fields.get(f"signal_preview:{key}")
        if preview is not None:
            preview.setProgram_(style_to_program(style, 255, color=preview_color))
        swatch = self.settings_fields.get(f"signal_color:{key}")
        if swatch is not None:
            swatch.setProgram_(style.color)

    def _set_signal_color(self, key: str, new_color: str) -> None:
        if key not in signals_module.DEFAULT_SIGNAL_STYLES:
            return
        style = self._current_signal_style(key)
        self._save_signal_style(
            key,
            signals_module.SignalStyle(
                new_color, style.pattern, style.speed_seconds, style.intensity
            ),
        )

    @objc.IBAction
    def pickSignalSwatch_(self, sender):
        identifier = str(sender.identifier() or "")
        if "|" not in identifier:
            return
        key, hex_color = identifier.split("|", 1)
        self._set_signal_color(key, hex_color)

    @objc.IBAction
    def openSignalColorPanel_(self, sender):
        key = str(sender.identifier() or "")
        if key not in signals_module.DEFAULT_SIGNAL_STYLES:
            return
        self.color_panel_signal_key = key
        self.active_color_target = None
        panel = NSColorPanel.sharedColorPanel()
        panel.setTarget_(self)
        panel.setAction_("signalPanelColorChanged:")
        panel.setColor_(nscolor_from_hex(self._current_signal_style(key).color))
        panel.orderFront_(None)

    @objc.IBAction
    def signalPanelColorChanged_(self, sender):
        key = getattr(self, "color_panel_signal_key", None)
        if not key:
            return
        if self._slider_event_is_drag():
            return
        self._set_signal_color(key, hex_from_nscolor(sender.color()))

    @objc.IBAction
    def selectSignalPattern_(self, recognizer):
        view = recognizer.view()
        key = getattr(view, "signal_card_key", None)
        pattern = getattr(view, "signal_card_pattern", None)
        if not key or not pattern:
            return
        style = self._current_signal_style(key)
        self._save_signal_style(
            key,
            signals_module.SignalStyle(
                style.color, pattern, style.speed_seconds, style.intensity
            ),
        )
        self.set_settings_message(f"{key.replace('_', ' ').title()}: {pattern} pattern.")

    @objc.IBAction
    def setSignalSpeed_(self, sender):
        key = str(sender.identifier() or "")
        if key not in signals_module.DEFAULT_SIGNAL_STYLES:
            return
        style = self._current_signal_style(key)
        self._save_signal_style(
            key,
            signals_module.SignalStyle(
                style.color, style.pattern, float(sender.doubleValue()), style.intensity
            ),
        )

    @objc.IBAction
    def setSignalIntensity_(self, sender):
        key = str(sender.identifier() or "")
        if key not in signals_module.DEFAULT_SIGNAL_STYLES:
            return
        style = self._current_signal_style(key)
        self._save_signal_style(
            key,
            signals_module.SignalStyle(
                style.color, style.pattern, style.speed_seconds, float(sender.doubleValue())
            ),
        )

    @objc.IBAction
    def setEscalationTier_(self, sender):
        item = sender.selectedItem()
        tier = str(item.representedObject() or "") if item is not None else ""
        try:
            self.settings = self.settings.with_escalation_tier(tier)
        except ValueError:
            return
        save_settings(self.settings)
        self.apply_escalation()
        self.set_settings_message(f"Escalation ceiling: {item.title()}.")

    @objc.IBAction
    def applyEscalationThresholds_(self, _sender):
        def read(field_key, fallback):
            field = self.settings_fields.get(field_key)
            if field is None:
                return fallback
            try:
                return float(str(field.stringValue()).strip())
            except ValueError:
                return fallback

        self.settings = self.settings.with_escalation_thresholds(
            ramp_seconds=read("escalation_ramp_field", self.settings.escalation_ramp_seconds),
            menu_bar_seconds=read(
                "escalation_menu_bar_field", self.settings.escalation_menu_bar_seconds
            ),
            final_seconds=read("escalation_final_field", self.settings.escalation_final_seconds),
        )
        save_settings(self.settings)
        for field_key, value in (
            ("escalation_ramp_field", self.settings.escalation_ramp_seconds),
            ("escalation_menu_bar_field", self.settings.escalation_menu_bar_seconds),
            ("escalation_final_field", self.settings.escalation_final_seconds),
        ):
            field = self.settings_fields.get(field_key)
            if field is not None:
                field.setStringValue_(f"{value:g}")
        self.apply_escalation()
        self.set_settings_message("Escalation timing saved.")

    @objc.IBAction
    def redrawSignalPreviews_(self, _timer):
        if self.settings_window is None or not self.settings_window.isVisible():
            if getattr(self, "signal_preview_timer", None) is not None:
                self.signal_preview_timer.invalidate()
                self.signal_preview_timer = None
            return
        for field_key, view in self.settings_fields.items():
            if field_key.startswith("signal_preview:"):
                if not view.isHiddenOrHasHiddenAncestor() and view.visibleRect().size.width > 0:
                    view.setNeedsDisplay_(True)
            elif field_key.startswith("signal_thumbs:") and isinstance(view, dict):
                for thumb in view.values():
                    # Viewport culling: only thumbnails actually scrolled
                    # into view animate -- 50+ off-screen WASM steppers
                    # were the Settings lag.
                    if not thumb.isHiddenOrHasHiddenAncestor() and thumb.visibleRect().size.width > 0:
                        thumb.setNeedsDisplay_(True)

    @objc.IBAction
    def setFocusProfileRule_(self, sender):
        identifier = str(sender.identifier() or "")
        item = sender.selectedItem()
        slot = str(item.representedObject() or "") if item is not None else ""
        if not identifier:
            return
        try:
            self.settings = self.settings.with_focus_profile_rule(identifier, slot or None)
        except ValueError:
            return
        save_settings(self.settings)
        self.set_settings_message(
            f"Focus rule saved: {item.title() if item is not None else 'No profile'}."
        )

    @objc.IBAction
    def setSessionIdentityColor_(self, sender):
        payload = sender.representedObject() or {}
        agent_id = str(payload.get("agent_id") or "")
        if not agent_id:
            return
        color = payload.get("color")
        self.settings = self.settings.with_colors(
            self.settings.colors.with_session_color(
                agent_id, str(color) if color is not None else None
            )
        )
        save_settings(self.settings)
        self.refresh_(None)

    @objc.IBAction
    def toggleReminderAlerts_(self, sender):
        enabled = bool(sender.state())
        self.settings = self.settings.with_reminder_alerts_enabled(enabled)
        save_settings(self.settings)
        self.reminders_watch_retry_at = 0.0
        if not enabled:
            self.reminders_glow_until = 0.0
            self.set_settings_message("Reminder glow off.")
            self.refresh_(None)
            return
        try:
            status = reminders_watch.authorization_status()
        except reminders_watch.RemindersUnavailableError:
            self.set_settings_message("Reminders access is unavailable on this system.")
            return
        if status == reminders_watch.AUTH_AUTHORIZED:
            self.set_settings_message("Reminder glow on.")
        elif status == reminders_watch.AUTH_NOT_DETERMINED:
            self.set_settings_message("Reminder glow on — asking macOS for access…")

            def _granted(ok):
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "reminderAccessResolved:", bool(ok), False
                )

            reminders_watch.request_access(_granted)
        else:
            self.set_settings_message(
                "Reminders access is denied — enable SidePulse under "
                "Privacy & Security → Reminders."
            )

    def test_signal_program(self, brightness: float, led_count: int = 8) -> str:
        key = getattr(self, "test_signal_key", None) or signals_module.SIGNAL_LOW_BATTERY
        if key == "__studio__":
            program = getattr(self, "studio_preview_program", "") or "#00E5FF"
            return apply_brightness(program, brightness)
        return style_to_program(
            self.settings.signal_style(key),
            brightness,
            color=_signal_preview_color(self, key),
            led_count=led_count,
        )

    def _refresh_studio_library_popup(self) -> None:
        popup = getattr(self, "studio_library_popup", None)
        if popup is None:
            return
        popup.removeAllItems()
        popup.addItemWithTitle_("Saved looks\u2026")
        popup.lastItem().setRepresentedObject_("")
        for look_name, _program in self.settings.studio_library:
            popup.addItemWithTitle_(look_name)
            popup.lastItem().setRepresentedObject_(look_name)

    @objc.IBAction
    def captureStudioProgram_(self, _sender):
        """Snapshot the EXACT program rendering on the Screen Bar into
        the editor -- any live moment you love becomes an editable
        artifact. The baked `brightness` prefix is stripped so the look
        re-renders correctly under your live brightness."""
        view = getattr(self.virtual_status_device, "view", None)
        program = getattr(view, "current_program", None) if view is not None else None
        if not program:
            self.set_settings_message("Nothing is playing on the Screen Bar.")
            return
        lines = [
            line
            for line in str(program).splitlines()
            if not line.strip().lower().startswith("brightness")
        ]
        editor = getattr(self, "studio_editor", None)
        if editor is None:
            return
        editor.setString_("\n".join(lines).strip())
        self.set_settings_message("Captured. It's yours now -- edit away.")

    @objc.IBAction
    def saveStudioLook_(self, _sender):
        editor = getattr(self, "studio_editor", None)
        field = getattr(self, "studio_save_name_field", None)
        if editor is None or field is None:
            return
        name = str(field.stringValue()).strip()
        program = str(editor.string()).strip()
        if not name:
            self.set_settings_message("Name the look first.")
            return
        if not program:
            self.set_settings_message("Write a program first.")
            return
        try:
            normalized = normalize_led_text(program)
            validate_led_text(normalized)
        except Exception as exc:
            self.set_settings_message(f"Can't save: {exc}")
            return
        dsl_error = self.validate_studio_program(normalized)
        if dsl_error:
            self.set_settings_message(f"Can't save: {dsl_error}.")
            return
        self.settings = self.settings.with_studio_saved_look(name, program)
        save_settings(self.settings)
        self._refresh_studio_library_popup()
        self.set_settings_message(f"Saved \u201c{name}\u201d to your looks.")

    @objc.IBAction
    def loadStudioLook_(self, sender):
        item = sender.selectedItem()
        name = str(item.representedObject() or "") if item is not None else ""
        if not name:
            return
        for look_name, program in self.settings.studio_library:
            if look_name == name:
                editor = getattr(self, "studio_editor", None)
                if editor is not None:
                    editor.setString_(program)
                self.set_settings_message(f"Loaded \u201c{name}\u201d.")
                return

    @objc.IBAction
    def deleteStudioLook_(self, _sender):
        popup = getattr(self, "studio_library_popup", None)
        if popup is None:
            return
        item = popup.selectedItem()
        name = str(item.representedObject() or "") if item is not None else ""
        if not name:
            self.set_settings_message("Pick a saved look to delete.")
            return
        self.settings = self.settings.without_studio_look(name)
        save_settings(self.settings)
        self._refresh_studio_library_popup()
        self.set_settings_message(f"Deleted \u201c{name}\u201d.")

    @objc.IBAction
    def applyStudioAsPowerUp_(self, _sender):
        """Burns the Studio program into every connected device's
        INIT.LED so the hardware BOOTS wearing your light -- validated
        through the real firmware grammar first; the firmware confirms
        by playing it immediately (documented INIT.LED behavior)."""
        editor = getattr(self, "studio_editor", None)
        if editor is None:
            return
        program = str(editor.string()).strip()
        if not program:
            self.set_settings_message("Write a program first.")
            return
        try:
            normalized = normalize_led_text(program)
            validate_led_text(normalized)
        except Exception as exc:
            self.set_settings_message(f"Power-up look invalid: {exc}")
            return
        dsl_error = self.validate_studio_program(normalized, strict=True)
        if dsl_error:
            self.set_settings_message(f"Power-up look invalid: {dsl_error}.")
            return
        written = 0
        for device in self.status_bar_devices(remember=False):
            if not device.connected or device.device_id == VIRTUAL_DEVICE_ID:
                continue
            try:
                write_led_program(
                    normalized, device_path=device.root, file_name="INIT.LED"
                )
                written += 1
            except Exception as exc:
                log_status_bar(f"INIT.LED write failed for {device.name}: {exc}")
        if written:
            plural = "device" if written == 1 else "devices"
            self.set_settings_message(
                f"Power-up look written to {written} {plural} -- it plays now "
                "and every time the hardware boots."
            )
        else:
            self.set_settings_message(
                "No connected SidePulse hardware to write to."
            )

    @objc.IBAction
    def previewStudioProgram_(self, _sender):
        editor = getattr(self, "studio_editor", None)
        if editor is None:
            return
        program = str(editor.string()).strip()
        if not program:
            return
        try:
            normalized = normalize_led_text(program)
            validate_led_text(normalized)
        except Exception as exc:
            self.set_settings_message(f"Studio program error: {exc}")
            return
        dsl_error = self.validate_studio_program(normalized)
        if dsl_error:
            self.set_settings_message(f"Studio program error: {dsl_error}.")
            return
        self.settings = self.settings.with_studio_program(program)
        save_settings(self.settings)
        self.studio_preview_program = program
        self.test_signal_key = "__studio__"
        self.test_signal_until = time.monotonic() + 12.0
        self.refresh_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            12.1, self, "refresh:", None, False
        )
        self.set_settings_message("Studio: playing your program on everything for 12s.")

    @objc.IBAction
    def stopStudioProgram_(self, _sender):
        self.test_signal_until = 0.0
        self.test_signal_key = None
        self.refresh_(None)
        self.set_settings_message("Studio preview stopped.")

    @objc.IBAction
    def applyFadePreset_(self, sender):
        identifier = str(sender.identifier() or "")
        if "|" not in identifier:
            return
        floor_pct, ceiling_pct = identifier.split("|", 1)
        floor = max(0.0, min(1.0, float(floor_pct) / 100.0))
        ceiling = max(0.0, min(1.0, float(ceiling_pct) / 100.0))
        colors = self.settings.colors
        for mode_key in FADE_MODE_KEYS:
            colors = colors.with_fade_floor(mode_key, floor).with_fade_ceiling(
                mode_key, ceiling
            )
        self.settings = self.settings.with_colors(colors)
        save_settings(self.settings)
        self.refresh_colors_window()
        self.refresh_colors_preview()
        if self.color_preview_enabled:
            self.push_colors_preview_to_device()
        self.refresh_(None)
        self.set_settings_message(f"Fade preset: {sender.title()} ({floor_pct}–{ceiling_pct}%).")

    @objc.IBAction
    def testSignal_(self, sender):
        key = str(sender.identifier() or "")
        if key not in signals_module.DEFAULT_SIGNAL_STYLES:
            return
        self.test_signal_key = key
        self.test_signal_until = time.monotonic() + SIGNAL_TEST_SECONDS
        self.refresh_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            SIGNAL_TEST_SECONDS + 0.1, self, "refresh:", None, False
        )
        self.set_settings_message(
            f"Testing the {key.replace('_', ' ')} signal on every surface…"
        )

    @objc.IBAction
    def applyTimerMinutes_(self, sender):
        try:
            minutes = float(str(sender.stringValue()).strip())
        except ValueError:
            sender.setStringValue_(f"{self.settings.timer_expected_minutes:g}")
            return
        self.settings = self.settings.with_timer_expected_minutes(minutes)
        save_settings(self.settings)
        sender.setStringValue_(f"{self.settings.timer_expected_minutes:g}")
        self.refresh_(None)
        self.set_settings_message(
            f"Timer fill completes after {self.settings.timer_expected_minutes:g} minutes."
        )

    @objc.IBAction
    def setDeviceDisplay_(self, sender):
        device_id = str(sender.identifier() or "")
        item = sender.selectedItem()
        display = str(item.representedObject() or "") if item is not None else ""
        if not device_id or display not in LED_DISPLAY_CHOICES:
            return
        self.settings = self.settings.with_device_display(device_id, display)
        save_settings(self.settings)
        self.refresh_(None)
        self.set_settings_message(f"Display: {item.title()}.")

    @objc.IBAction
    def setDeviceSignalPolicy_(self, sender):
        device_id = str(sender.identifier() or "")
        if not device_id:
            return
        item = sender.selectedItem()
        policy = str(item.representedObject() or "") if item is not None else ""
        self.settings = self.settings.with_device_signal_policy(
            device_id, policy or None
        )
        save_settings(self.settings)
        label = item.title() if item is not None else "All signals"
        self.set_settings_message(f"Device signals: {label.lower()}.")
        self.refresh_(None)

    @objc.IBAction
    def setDeviceProviderPin_(self, sender):
        device_id = str(sender.identifier() or "")
        if not device_id:
            return
        item = sender.selectedItem()
        pin = str(item.representedObject() or "") if item is not None else ""
        self.settings = self.settings.with_device_provider_pin(device_id, pin or None)
        save_settings(self.settings)
        label = item.title() if item is not None else "All sessions"
        self.set_settings_message(f"Device sessions: {label.lower()}.")
        self.refresh_(None)

    @objc.IBAction
    def setDeviceBlendMode_(self, sender):
        device_id = str(sender.identifier() or "")
        item = sender.selectedItem()
        mode = str(item.representedObject() or "") if item is not None else ""
        if not device_id:
            return
        self.settings = self.settings.with_device_blend_mode(device_id, mode or None)
        save_settings(self.settings)
        self.refresh_(None)
        self.set_settings_message(
            f"{device_id} blend: {item.title() if item is not None else 'Global default'}."
        )

    @objc.IBAction
    def saveCalibrationProfile_(self, sender):
        slot = str(sender.representedObject() or "")
        if slot not in CALIBRATION_PROFILE_SLOTS:
            return
        self.settings = self.settings.with_saved_calibration_profile(slot)
        save_settings(self.settings)
        self.set_settings_message(f"Saved current calibration as {slot}.")

    @objc.IBAction
    def applyCalibrationProfile_(self, sender):
        slot = str(sender.representedObject() or "")
        if slot not in CALIBRATION_PROFILE_SLOTS:
            return
        profile = self.settings.calibration_profiles.get(slot)
        known_ids = {device.device_id for device in self.settings.devices}
        matched = (
            sum(1 for device_id in profile if device_id in known_ids)
            if isinstance(profile, dict)
            else 0
        )
        self.settings = self.settings.with_applied_calibration_profile(slot)
        save_settings(self.settings)
        self.refresh_settings_window()
        self.refresh_(None)
        if matched:
            plural = "device" if matched == 1 else "devices"
            self.set_settings_message(
                f"Applied the {slot} profile to {matched} {plural}."
            )
        else:
            # "Applied" with zero matches was a silent no-op that still
            # claimed success -- say what actually happened.
            self.set_settings_message(
                f"The {slot} profile has no devices matching this Mac's -- "
                "nothing changed."
            )

    @objc.IBAction
    def toggleWeatherAlerts_(self, sender):
        enabled = bool(sender.state())
        self.settings = self.settings.with_weather_alerts_enabled(enabled)
        save_settings(self.settings)
        self.weather_watch_retry_at = 0.0
        if enabled:
            self.set_settings_message("Weather warnings on — checking your area…")
            self.pollWeather_(None)
        else:
            self.weather_alert_active = False
            self.set_settings_message("Weather warnings off.")
            self.refresh_(None)

    @objc.IBAction
    def applyWeatherLocation_(self, _sender):
        lat_field = self.settings_fields.get("weather_latitude_field")
        lon_field = self.settings_fields.get("weather_longitude_field")
        if lat_field is None or lon_field is None:
            return

        def parsed(field):
            text = str(field.stringValue()).strip()
            if not text:
                return None, True
            try:
                return float(text), True
            except ValueError:
                return None, False

        latitude, lat_ok = parsed(lat_field)
        longitude, lon_ok = parsed(lon_field)
        if not (lat_ok and lon_ok):
            self.set_settings_message(
                "Weather location must be decimal degrees, like 41.88 and -87.63."
            )
            return
        if (latitude is None) != (longitude is None):
            self.set_settings_message(
                "Enter BOTH latitude and longitude, or leave both blank "
                "for automatic."
            )
            return
        self.settings = self.settings.with_weather_location(latitude, longitude)
        save_settings(self.settings)
        # Re-check alerts for the new location right away.
        self.weather_watch_retry_at = 0.0
        if latitude is None:
            self.set_settings_message("Weather location: automatic (network address).")
        else:
            self.set_settings_message(
                f"Weather location set to {latitude:g}, {longitude:g}."
            )
        self.refresh_(None)

    @objc.IBAction
    def reminderAccessResolved_(self, granted):
        if granted:
            self.set_settings_message("Reminders access granted.")
            self.reminders_watch_retry_at = 0.0
        else:
            self.set_settings_message(
                "Reminders access was declined — the glow stays off until "
                "it's granted in Privacy & Security → Reminders."
            )

    @objc.IBAction
    def calendarAccessResolved_(self, granted):
        if granted:
            self.set_settings_message("Calendar access granted.")
            self.calendar_watch_retry_at = 0.0
            self.pollCalendar_(None)
        else:
            self.set_settings_message(
                "Calendar access was declined — the glow stays off until "
                "it's granted in Privacy & Security → Calendars."
            )

    @objc.IBAction
    def applyCalendarLead_(self, _sender):
        field = self.settings_fields.get("calendar_lead_field")
        if field is None:
            return
        try:
            minutes = float(str(field.stringValue()).strip())
        except ValueError:
            field.setStringValue_(f"{self.settings.calendar_lead_minutes:g}")
            return
        self.settings = self.settings.with_calendar_lead_minutes(minutes)
        save_settings(self.settings)
        field.setStringValue_(f"{self.settings.calendar_lead_minutes:g}")
        self.calendar_watch_retry_at = 0.0
        self.set_settings_message(
            f"Calendar glow starts {self.settings.calendar_lead_minutes:g} minutes before events."
        )

    @objc.IBAction
    def toggleCompletionSweep_(self, sender):
        enabled = bool(sender.state())
        self.settings = self.settings.with_completion_sweep_enabled(enabled)
        save_settings(self.settings)
        self.set_settings_message(
            "Completion sweep on." if enabled else "Completion sweep off."
        )

    @objc.IBAction
    def toggleNotificationBlinks_(self, sender):
        enabled = bool(sender.state())
        self.settings = self.settings.with_notification_blinks_enabled(enabled)
        save_settings(self.settings)
        self.set_settings_message(
            "Notification blinks on." if enabled else "Notification blinks off."
        )

    @objc.IBAction
    def applyNotificationColors_(self, _sender):
        for bundle_id in DEFAULT_NOTIFICATION_APP_COLORS:
            field = self.settings_fields.get(f"notification_color:{bundle_id}")
            if field is None:
                continue
            raw = str(field.stringValue()).strip()
            self.settings = self.settings.with_notification_app_color(
                bundle_id, raw or None
            )
        save_settings(self.settings)
        # Reflect normalization (or a rejected value) back into the UI.
        for bundle_id in DEFAULT_NOTIFICATION_APP_COLORS:
            field = self.settings_fields.get(f"notification_color:{bundle_id}")
            if field is not None:
                field.setStringValue_(
                    self.settings.notification_app_colors.get(bundle_id, "")
                )
        self.set_settings_message("Notification colors saved.")

    @objc.IBAction
    def openTipPane_(self, sender):
        """Show Me lands on the exact pane -- and when the tip has a
        registered anchor, scrolls to it and flashes it so there is
        never a "took me somewhere but I couldn't find it" moment."""
        payload = sender.representedObject()
        if isinstance(payload, dict):
            pane = str(payload.get("pane") or "")
            anchor_key = str(payload.get("anchor") or "")
        else:
            pane, anchor_key = str(payload or ""), ""
        self.show_settings_window()
        if pane:
            self.select_settings_pane(pane)
        anchor = getattr(self, "tip_anchor_views", {}).get(anchor_key)
        if anchor is not None:
            anchor.scrollRectToVisible_(anchor.bounds())
            self.flash_view(anchor)

    def flash_view(self, view) -> None:
        """A brief highlight pulse so the eye lands on the right card."""
        try:
            view.setWantsLayer_(True)
            layer = view.layer()
            if layer is None:
                return
            from AppKit import NSColor

            accent = NSColor.controlAccentColor().colorWithAlphaComponent_(0.28)
            layer.setBackgroundColor_(accent.CGColor())
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.9, self, "clearFlash:", view, False
            )
        except Exception:
            pass

    @objc.IBAction
    def clearFlash_(self, timer):
        view = timer.userInfo()
        try:
            layer = view.layer()
            if layer is not None:
                layer.setBackgroundColor_(None)
        except Exception:
            pass

    @objc.IBAction
    def dismissTip_(self, sender):
        text = str(sender.representedObject() or "").strip()
        if not text:
            return
        self.settings = self.settings.with_dismissed_tip(text)
        save_settings(self.settings)
        self._menu_signature = None
        self.refresh_(None)

    @objc.IBAction
    def applyPalette_(self, sender):
        name = str(sender.identifier() or "")
        palette = colors_module.CURATED_PALETTES.get(
            name
        ) or colors_module.PROVIDER_PALETTES.get(name)
        if palette is None:
            return
        colors = self.settings.colors
        for mode_key, hex_value in palette["modes"].items():
            colors = colors.with_mode_color(mode_key, hex_value)
        for provider, hex_value in palette["agents"].items():
            colors = colors.with_agent_color(provider, hex_value)
        self.settings = self.settings.with_colors(colors)
        save_settings(self.settings)
        self.refresh_colors_window()
        self.refresh_(None)
        self.set_settings_message(f"Palette: {name}. Every light just changed outfits.")

    @objc.IBAction
    def openProjectPage_(self, _sender):
        from AppKit import NSWorkspace
        from Foundation import NSURL

        NSWorkspace.sharedWorkspace().openURL_(
            NSURL.URLWithString_("https://github.com/JonathanRReed/sidepulse-JR-Fork")
        )

    @objc.IBAction
    def setScreenBarMinGlow_(self, sender):
        fraction = max(0.0, min(1.0, float(sender.doubleValue()) / 100.0))
        # Track the thumb live on the pane's miniature; commit on release.
        preview = self.settings_fields.get("screen_bar_preview_view")
        if preview is not None:
            preview.setMinGlow_(fraction)
        event = NSApp.currentEvent()
        if event is not None and event.type() == NSEventTypeLeftMouseDragged:
            return
        self.settings = self.settings.with_screen_bar_min_glow(fraction)
        save_settings(self.settings)
        label = "pitch black" if fraction <= 0.004 else f"{round(fraction * 100)}%"
        self.set_settings_message(f"Screen Bar dim floor: {label}.")
        self.refresh_(None)

    @objc.IBAction
    def setDeviceRestingGlow_(self, sender):
        device_id = str(sender.identifier() or "")
        if not device_id:
            return
        fraction = max(0.0, min(0.35, float(sender.doubleValue()) / 100.0))
        self.settings = self.settings.with_device_resting_glow(device_id, fraction)
        save_settings(self.settings)
        self.reset_led_controllers_for_device(device_id)
        self.set_settings_message(
            "Resting glow off." if fraction <= 0.004 else f"Resting glow: {round(fraction * 100)}%."
        )
        self.refresh_(None)

    @objc.IBAction
    def setUsageDisplayMode_(self, sender):
        item = sender.selectedItem()
        if item is None:
            return
        try:
            self.settings = self.settings.with_usage_display_mode(
                str(item.representedObject())
            )
        except ValueError:
            return
        save_settings(self.settings)
        self._usage_refreshed_at = 0.0
        self.maybe_refresh_usage_summary()

    @objc.IBAction
    def toggleCodexPercent_(self, sender):
        self.settings = self.settings.with_codex_percent_enabled(checkbox_is_on(sender))
        save_settings(self.settings)
        self._usage_refreshed_at = 0.0
        self.maybe_refresh_usage_summary()

    @objc.IBAction
    def setUsageGraphRange_(self, sender):
        item = sender.selectedItem()
        if item is None:
            return
        try:
            self.settings = self.settings.with_usage_graph_days(int(item.representedObject()))
        except (TypeError, ValueError):
            return
        save_settings(self.settings)
        # Rescan now -- the warm cache makes a year-range rebuild cheap.
        self._usage_refreshed_at = 0.0
        self.maybe_refresh_usage_summary()

    @objc.IBAction
    def toggleWebhookEvent_(self, sender):
        event_key = str(sender.identifier() or "")
        if not event_key:
            return
        try:
            self.settings = self.settings.with_webhook_event(
                event_key, checkbox_is_on(sender)
            )
        except ValueError:
            return
        save_settings(self.settings)
        self.set_settings_message("Webhook events saved.")

    @objc.IBAction
    def applyEscalationWebhook_(self, sender):
        url = str(sender.stringValue()).strip()
        if url and not url.startswith(("http://", "https://")):
            self.set_settings_message("Webhook must be an http(s) URL.")
            return
        self.settings = self.settings.with_escalation_webhook_url(url)
        save_settings(self.settings)
        self.set_settings_message(
            "Stage-3 webhook set." if url else "Stage-3 webhook off."
        )

    @objc.IBAction
    def toggleCompletionNotification_(self, sender):
        self.settings = self.settings.with_completion_notification_enabled(
            checkbox_is_on(sender)
        )
        save_settings(self.settings)
        self.set_settings_message(
            "Completion notifications on."
            if self.settings.completion_notification_enabled
            else "Completion notifications off."
        )

    @objc.IBAction
    def toggleSubagentAsksAlert_(self, sender):
        self.settings = self.settings.with_subagent_asks_alert(checkbox_is_on(sender))
        save_settings(self.settings)
        self.refresh_(None)

    @objc.IBAction
    def toggleQuotaAlerts_(self, sender):
        self.settings = self.settings.with_quota_alerts_enabled(checkbox_is_on(sender))
        save_settings(self.settings)
        self.refresh_(None)

    @objc.IBAction
    def applyQuotaThresholds_(self, sender):
        raw = str(sender.stringValue())
        try:
            values = [float(part) for part in raw.replace(";", ",").split(",") if part.strip()]
            self.settings = self.settings.with_quota_alert_thresholds(values)
        except ValueError:
            self.set_settings_message(
                "Thresholds are percentages, like 50, 75, 90."
            )
            return
        save_settings(self.settings)
        pretty = ", ".join(f"{value:g}%" for value in self.settings.quota_alert_thresholds)
        self.set_settings_message(f"Quota blinks at {pretty}.")
        self.refresh_(None)

    @objc.IBAction
    def toggleClaudePlanLimits_(self, sender):
        self.settings = self.settings.with_claude_plan_limits_enabled(
            checkbox_is_on(sender)
        )
        save_settings(self.settings)
        # Refresh immediately so the line appears without the 5-min wait.
        self._usage_refreshed_at = 0.0
        self.maybe_refresh_usage_summary()
        self.refresh_(None)

    @objc.IBAction
    def toggleMenuBarLabel_(self, sender):
        self.settings = self.settings.with_menu_bar_label_enabled(checkbox_is_on(sender))
        save_settings(self.settings)
        self.set_status(self.current_state, ask_count=self.current_ask_count)
        self.refresh_(None)

    @objc.IBAction
    def disableTips_(self, _sender):
        self.settings = self.settings.with_tips_enabled(False)
        save_settings(self.settings)
        self.set_settings_message("Daily tips are off.")
        self._menu_signature = None
        self.refresh_(None)

    @objc.IBAction
    def openSettings_(self, _sender):
        self.show_settings_window()

    @objc.IBAction
    def openSetup_(self, _sender):
        self.show_setup_window()

    @objc.IBAction
    def runFirstLaunchSetup_(self, _sender):
        self.run_first_launch_setup()

    @objc.IBAction
    def skipFirstLaunchSetup_(self, _sender):
        self.complete_first_launch_setup("Setup skipped.")

    @objc.IBAction
    def uninstallSdEjectGuard_(self, _sender):
        self.uninstall_sd_eject_guard_from_setup()

    @objc.IBAction
    def installCodexHooks_(self, _sender):
        self.update_hooks("codex", install=True)

    @objc.IBAction
    def uninstallCodexHooks_(self, _sender):
        self.update_hooks("codex", install=False)

    @objc.IBAction
    def installClaudeHooks_(self, _sender):
        self.update_hooks("claude", install=True)

    @objc.IBAction
    def uninstallClaudeHooks_(self, _sender):
        self.update_hooks("claude", install=False)

    @objc.IBAction
    def installDevinHooks_(self, _sender):
        self.update_hooks("devin", install=True)

    @objc.IBAction
    def uninstallDevinHooks_(self, _sender):
        self.update_hooks("devin", install=False)

    @objc.IBAction
    def installGrokHooks_(self, _sender):
        self.update_hooks("grok", install=True)

    @objc.IBAction
    def uninstallGrokHooks_(self, _sender):
        self.update_hooks("grok", install=False)

    @objc.IBAction
    def installCursorHooks_(self, _sender):
        self.update_hooks("cursor", install=True)

    @objc.IBAction
    def uninstallCursorHooks_(self, _sender):
        self.update_hooks("cursor", install=False)

    @objc.IBAction
    def installHermesHooks_(self, _sender):
        self.update_hooks("hermes", install=True)

    @objc.IBAction
    def uninstallHermesHooks_(self, _sender):
        self.update_hooks("hermes", install=False)

    @objc.IBAction
    def installOpenclawHooks_(self, _sender):
        self.update_hooks("openclaw", install=True)

    @objc.IBAction
    def uninstallOpenclawHooks_(self, _sender):
        self.update_hooks("openclaw", install=False)

    @objc.IBAction
    def toggleCodexTranscripts_(self, sender):
        self.set_transcript_monitoring("codex", sender.state() == NSOnState)

    @objc.IBAction
    def toggleClaudeTranscripts_(self, sender):
        self.set_transcript_monitoring("claude", sender.state() == NSOnState)

    @objc.IBAction
    def setBatteryLedDisplayFromCheckbox_(self, sender):
        self.set_battery_led_display(sender.state() == NSOnState)

    @objc.IBAction
    def setBatteryPowerPreviewFromCheckbox_(self, sender):
        self.set_battery_power_preview(sender.state() == NSOnState)

    @objc.IBAction
    def toggleLowBatteryAlert_(self, sender):
        self.settings = self.settings.with_low_battery_alert_enabled(checkbox_is_on(sender))
        save_settings(self.settings)
        self.refresh_(None)

    @objc.IBAction
    def applyLowBatteryThreshold_(self, _sender):
        field = self.settings_fields.get("low_battery_threshold_field")
        if field is None:
            return
        try:
            percent = float(str(field.stringValue()).strip().rstrip("%"))
        except ValueError:
            self.set_settings_message("Low-battery threshold must be a number.")
            return
        self.settings = self.settings.with_low_battery_threshold_percent(percent)
        save_settings(self.settings)
        set_field_value(field, f"{self.settings.low_battery_threshold_percent:g}")
        self.set_settings_message(
            f"Charge reminder below {self.settings.low_battery_threshold_percent:g}%."
        )
        self.refresh_(None)

    @objc.IBAction
    def setDeviceDisplayAgent_(self, sender):
        self.set_device_display(sender.representedObject(), LED_DISPLAY_AGENT)

    @objc.IBAction
    def setDeviceDisplayBattery_(self, sender):
        self.set_device_display(sender.representedObject(), LED_DISPLAY_BATTERY)

    @objc.IBAction
    def setDeviceBrightness_(self, sender):
        device_id = sender.identifier()
        if device_id is None:
            return
        device_id = str(device_id)
        value = sender.doubleValue()
        controls = self.device_settings_controls.get(device_id)
        if controls is not None:
            self.set_brightness_preview_dots(controls.get("brightness_dots"), value)
        if device_id == VIRTUAL_DEVICE_ID:
            preview = self.settings_fields.get("screen_bar_preview_view")
            if preview is not None:
                preview.setPreviewWhiteBrightness_(value)
        event = NSApp.currentEvent()
        if event is not None and event.type() == NSEventTypeLeftMouseDragged:
            # A live drag tick -- the preview above already tracks it;
            # skip the expensive commit (settings save, hardware sync, a
            # full settings-window refresh) until the drag actually ends,
            # or every pixel of mouse movement would trigger all of that.
            return
        self.set_device_brightness(device_id, value)

    @objc.IBAction
    def toggleDeviceAutoBrightness_(self, sender):
        device_id = sender.representedObject()
        if device_id is None:
            return
        currently_enabled = self.settings.auto_brightness_enabled_for_device(str(device_id))
        self.set_device_auto_brightness(str(device_id), not currently_enabled)

    @objc.IBAction
    def setDeviceRedGain_(self, sender):
        device_id = sender.identifier()
        if device_id is None:
            return
        self.set_device_channel_gain(str(device_id), "red", sender.doubleValue() / 100.0)

    @objc.IBAction
    def setDeviceGreenGain_(self, sender):
        device_id = sender.identifier()
        if device_id is None:
            return
        self.set_device_channel_gain(str(device_id), "green", sender.doubleValue() / 100.0)

    @objc.IBAction
    def setDeviceBlueGain_(self, sender):
        device_id = sender.identifier()
        if device_id is None:
            return
        self.set_device_channel_gain(str(device_id), "blue", sender.doubleValue() / 100.0)

    @objc.IBAction
    def startCalibrationTest_(self, sender):
        payload = sender.representedObject()
        if not isinstance(payload, dict):
            return
        device_id = str(payload.get("device_id") or "")
        hex_color = str(payload.get("hex") or "")
        if not device_id or not hex_color:
            return
        self.calibration_test = (device_id, hex_color)
        self._send_calibration_test()

    def _send_calibration_test(self) -> None:
        """Lights the device under calibration with the chosen reference
        color, THROUGH the current channel gains -- so every gain-slider
        move re-lights it and the user sees convergence live."""
        if not self.calibration_test:
            return
        device_id, hex_color = self.calibration_test
        program = f"{hex_color} 500ms\nrepeat"
        if device_id == VIRTUAL_DEVICE_ID:
            self.virtual_status_device.set_program(
                apply_brightness(program, self.settings.brightness_for_device(device_id))
            )
            return
        device = next(
            (
                entry
                for entry in self.status_bar_devices(remember=False)
                if entry.device_id == device_id and entry.connected
            ),
            None,
        )
        if device is None:
            return
        controller = self.agent_controller_for_device(device)
        controller.sync_program(apply_brightness(program, controller.brightness), LedDisplayState.ASK)

    def popoverDidClose_(self, _notification):
        """NSPopover delegate: the calibration popover closed -- stop any
        test color and hand the device straight back to live status."""
        if self.calibration_test is not None:
            self.calibration_test = None
            self.refresh_(None)

    @objc.IBAction
    def resetDeviceColorCalibration_(self, sender):
        device_id = sender.representedObject()
        if device_id is None:
            return
        self.set_device_channel_gains_reset(str(device_id))

    @objc.IBAction
    def openDeviceCalibrationPopover_(self, sender):
        device_id = sender.representedObject()
        if device_id is None:
            return
        device_id = str(device_id)
        device = next(
            (d for d in self.status_bar_devices(remember=False) if d.device_id == device_id),
            None,
        )
        if device is None:
            return
        stack, controls = build_calibration_popover_content(device, self)
        content_view = NSView.alloc().init()
        content_view.addSubview_(stack)
        native_ui._pin_edges(stack, content_view, insets=(16.0, 16.0, 16.0, 16.0))
        view_controller = NSViewController.alloc().init()
        view_controller.setView_(content_view)
        popover = NSPopover.alloc().init()
        popover.setContentViewController_(view_controller)
        popover.setBehavior_(NSPopoverBehaviorTransient)
        # For popoverDidClose_ -- a dismissed calibration popover must
        # stop any test color and return the device to live status.
        popover.setDelegate_(self)
        popover.showRelativeToRect_ofView_preferredEdge_(sender.bounds(), sender, NSMaxYEdge)
        self._device_calibration_popover = popover
        self.device_settings_controls.setdefault(device_id, {}).update(controls)
        self.refresh_device_settings_controls(device_id, controls)
        # White-first: light every LED true white immediately so the
        # user starts matching without hunting for a patch to click.
        self.calibration_test = (device_id, "#FFFFFF")
        self._send_calibration_test()

    @objc.IBAction
    def toggleVirtualStatusDevice_(self, _sender):
        if not SCREEN_BAR_FEATURE_ENABLED:
            self.set_virtual_status_device(False)
            return
        self.set_virtual_status_device(not self.settings.virtual_status_device_enabled)

    @objc.IBAction
    def saveLidAnimations_(self, _sender):
        self.save_lid_animations_from_fields()

    def textDidEndEditing_(self, notification):
        """NSTextView delegate: the two lid-animation program editors are
        the only text views that set us as delegate -- committing when
        editing ends gives them the same instant-apply contract as every
        text field (see native_ui.make_field)."""
        editors = (
            self.settings_fields.get("closed_animation_program"),
            self.settings_fields.get("open_animation_program"),
        )
        if notification.object() in editors:
            self.save_lid_animations_from_fields()

    @objc.IBAction
    def previewLidClosedAnimation_(self, _sender):
        animation = self.lid_animation_from_fields(LID_ANIMATION_CLOSED)
        if animation is not None:
            self.play_lid_animation(LID_ANIMATION_CLOSED, animation=animation)

    @objc.IBAction
    def previewLidOpenAnimation_(self, _sender):
        animation = self.lid_animation_from_fields(LID_ANIMATION_OPEN)
        if animation is not None:
            self.play_lid_animation(LID_ANIMATION_OPEN, animation=animation)

    @objc.IBAction
    def resetLidClosedAnimation_(self, _sender):
        self.reset_lid_animation(LID_ANIMATION_CLOSED)

    @objc.IBAction
    def resetLidOpenAnimation_(self, _sender):
        self.reset_lid_animation(LID_ANIMATION_OPEN)

    @objc.IBAction
    def removeRememberedDevice_(self, sender):
        self.remove_remembered_device(sender.representedObject())

    @objc.IBAction
    def quit_(self, _sender):
        self.closed_lid_awake.release()
        self.keep_awake.release()
        # Under launchd (parent pid 1) the job has KeepAlive, so a plain
        # terminate would be resurrected instantly; boot the job out
        # instead -- launchd stops us and stays stopped until the next
        # login or explicit start. Anywhere else (dev --foreground run),
        # a normal terminate is correct.
        if os.getppid() == 1:
            subprocess.Popen(
                [
                    "launchctl",
                    "bootout",
                    f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            NSApp.terminate_(self)

    def applicationWillTerminate_(self, _notification):
        # Repeating NSTimers retain their target; left running they keep
        # the controller alive and firing through launchd teardown.
        for name in (
            "timer",
            "lid_timer",
            "device_timer",
            "brightness_watch_timer",
            "notification_watch_timer",
            "calendar_watch_timer",
            "reminders_watch_timer",
            "weather_watch_timer",
            "color_preview_timer",
            "signal_preview_timer",
            "setup_demo_timer",
        ):
            active_timer = getattr(self, name, None)
            if active_timer is not None:
                active_timer.invalidate()
        monitor = getattr(self, "monitor", None)
        if monitor is not None and hasattr(monitor, "write_latest_state"):
            # The per-event path is debounced; flush the tail on quit.
            monitor.write_latest_state()
        self.stop_event_server()
        self.closed_lid_awake.release()
        self.keep_awake.release()

    # --- Ask escalation ------------------------------------------------

    def track_ask_blocked(self, statuses) -> None:
        """Per-agent ask/blocked episode tracking. Escalation follows the
        OLDEST currently-unanswered ask -- aggregate-level tracking let a
        brand-new ask inherit a stage-3 chime from a different, already
        answered agent's long episode. Pass an empty tuple to clear
        (e.g. the refresh error path, where no state can be confirmed)."""
        now = time.monotonic()
        current = {
            status.agent_id
            for status in (statuses or ())
            # HARD asks from MAIN sessions only: soft question-heuristic
            # asks and sub-agent noise must never ramp toward a chime.
            if status.agent_id and status.is_hard_ask and not status.is_subagent
        }
        tracked = getattr(self, "ask_blocked_by_agent", {})
        updated = {agent_id: tracked.get(agent_id, now) for agent_id in current}
        self.ask_blocked_by_agent = updated
        if updated:
            oldest = min(updated.values())
            if self.ask_blocked_since != oldest:
                # A new oldest episode (fresh ask, or the previous oldest
                # was answered) gets a fresh one-chime latch (and a
                # fresh one-webhook latch).
                self.escalation_chimed = False
                self.escalation_webhooked = False
            self.ask_blocked_since = oldest
        else:
            self.ask_blocked_since = None
        self.apply_escalation()

    def track_working(self, statuses) -> None:
        """When the OLDEST currently-working agent started -- the clock
        the working-timer fill display counts against."""
        now = time.monotonic()
        working_modes = (
            AgentMode.WORKING,
            AgentMode.TOOL_RUNNING,
            AgentMode.LONG_TASK_PROGRESS,
        )
        current = {
            status.agent_id
            for status in (statuses or ())
            if status.mode in working_modes and status.agent_id
        }
        tracked = getattr(self, "working_since_by_agent", {})
        self.working_since_by_agent = {
            agent_id: tracked.get(agent_id, now) for agent_id in current
        }
        self.working_since = (
            min(self.working_since_by_agent.values())
            if self.working_since_by_agent
            else None
        )

    def track_completions(self, statuses) -> None:
        """Fires the completion sweep when an agent TRANSITIONS into
        Completed from an active state -- the aggregate hides these
        whenever another agent's Working outranks them. The sweep runs
        in the finishing agent's identity color when several sessions
        are live."""
        previous_modes = getattr(self, "last_agent_modes", {})
        current_modes = {
            status.agent_id: status.mode
            for status in (statuses or ())
            # Sweep on MAIN agents finishing; a Claude session can burn
            # through a dozen Task workers and the green sweep would
            # never stop firing.
            if status.agent_id and not status.is_subagent
        }
        self.last_agent_modes = current_modes
        if not self.settings.completion_sweep_enabled:
            return
        active_before = (
            AgentMode.WORKING,
            AgentMode.TOOL_RUNNING,
            AgentMode.LONG_TASK_PROGRESS,
            AgentMode.WAITING_FOR_INPUT,
            AgentMode.BLOCKED_ERROR,
        )
        statuses_by_id = {
            status.agent_id: status
            for status in (statuses or ())
            if status.agent_id and not status.is_subagent
        }
        finished = [
            agent_id
            for agent_id, mode in current_modes.items()
            if mode == AgentMode.COMPLETED
            and previous_modes.get(agent_id) in active_before
            # T3's freshness window: only a completion under 2 minutes
            # old may CELEBRATE -- replayed transcripts and restarts
            # repaint silently. (Display visibility is a separate,
            # longer clock: COMPLETED_VISIBLE_SECONDS in collector.)
            and statuses_by_id[agent_id].age_seconds()
            <= COMPLETION_NOTIFY_FRESHNESS_SECONDS
        ]
        if not finished:
            return
        log_status_bar(f"completion sweep: {finished[-1][:60]}")
        ordered_ids = sorted(current_modes)
        identity = (
            colors_module.identity_colors_for_agents(
                ordered_ids,
                groups=colors_module.identity_groups_for_statuses(
                    tuple(statuses_by_id.values()), self.settings.colors
                ),
            )
            if len(ordered_ids) > 1
            else {}
        )
        agent_id = finished[-1]
        self.completion_sweep_color = (
            self.settings.colors.session_color(agent_id)
            or identity.get(agent_id)
            or self.settings.signal_style(signals_module.SIGNAL_COMPLETION).color
        )
        hold = signals_module.signal_hold_seconds(
            self.settings.signal_style(signals_module.SIGNAL_COMPLETION)
        )
        self.completion_sweep_until = time.monotonic() + hold
        self.post_completion_notification(statuses_by_id.get(agent_id))
        if self.webhook_event_enabled("completion"):
            finished_status = statuses_by_id.get(agent_id)
            self.post_webhook(
                {
                    "event": "sidepulse.completion",
                    "provider": finished_status.provider if finished_status else "",
                    "label": (finished_status.display_name[:80] if finished_status else ""),
                    "color": self.completion_sweep_color,
                }
            )
        # The Exhale: when the LAST working session just finished and
        # nothing needs you, the bar takes one slow warm breath after
        # the sweep -- "you're free" as a felt moment, not a light that
        # merely stops. Same freshness discipline as the sweep (we are
        # inside the edge-triggered, 2-minute-fresh path already).
        all_done = all(
            mode == AgentMode.COMPLETED for mode in current_modes.values()
        )
        snapshot = getattr(self, "last_snapshot", None)
        no_asks = snapshot is None or not ask_statuses(snapshot)
        if all_done and no_asks:
            self.all_clear_until = self.completion_sweep_until + 3.6
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                hold + 3.8, self, "refresh:", None, False
            )
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            hold + 0.1, self, "refresh:", None, False
        )

    def active_focus_summary(self) -> str:
        """Plain-language "which Focus is on and what we're doing about
        it" -- cached briefly; shown in the Focus pane and the dropdown."""
        cached = getattr(self, "_focus_summary_cache", None)
        now = time.monotonic()
        if cached is not None and now - cached[0] < 5.0:
            return cached[1]
        try:
            active = focus_sync.active_focus_mode_identifiers()
            names = dict(focus_sync.configured_focus_modes())
        except focus_sync.FocusSyncUnavailableError:
            text = "Needs Full Disk Access (see Settings > Focus) to read Focus modes."
        else:
            if not active:
                text = "No Focus is active."
            elif not self.settings.focus_sync_enabled:
                text = "A Focus is active, but Focus reactions are off."
            else:
                parts = []
                for identifier in active:
                    name = names.get(identifier, identifier)
                    rule = self.settings.focus_dim_rules.get(identifier)
                    if rule is None:
                        effect = "shared dim"
                    elif rule <= 0.0:
                        effect = "lights off"
                    elif rule >= 1.0:
                        effect = "no dimming"
                    else:
                        effect = f"dim to {round(rule * 100)}%"
                    policy = self.settings.focus_signal_policy.get(identifier)
                    if policy == "asks_only":
                        effect += ", asks only"
                    elif policy == "silent":
                        effect += ", silent"
                    parts.append(f"{name} \u2014 {effect}")
                text = "; ".join(parts)
        self._focus_summary_cache = (now, text)
        return text

    def hard_ask_live(self) -> bool:
        """A tracked blocked-on-you request is waiting right now."""
        snapshot = getattr(self, "last_snapshot", None)
        if snapshot is None:
            return False
        return any(status.is_hard_ask for status in ask_statuses(snapshot))

    def hard_ask_renders_on_device(self, device: StatusBarDevice) -> bool:
        """Would THIS device actually show the live ask? Weather may
        only yield to an ask the user can see on that surface --
        yielding on a device pinned to the other provider (or parked
        on an ambient Studio/Timer/Runway display) blanked the
        emergency while showing nothing blocked-on-you in its place."""
        if device.display != LED_DISPLAY_AGENT:
            return False
        snapshot = getattr(self, "last_snapshot", None)
        if snapshot is None:
            return False
        pin = self.settings.device_provider_pin(device.device_id)
        return any(
            status.is_hard_ask and (not pin or status.provider == pin)
            for status in ask_statuses(snapshot)
        )

    def quiet_active(self) -> bool:
        """User-requested quiet: celebration, notification, calendar and
        reminder glows hold their tongue. Hard asks and weather always
        break through (T3: blocked-on-you outranks snooze)."""
        return time.monotonic() < getattr(self, "quiet_until_monotonic", 0.0)

    def active_focus_policy(self) -> str:
        """Story #12: the strictest per-Focus signal policy among the
        Focus modes active right now -- "silent" beats "asks_only" beats
        "all". Absent keys mean "all"; no Focus active means "all"."""
        policy_map = self.settings.focus_signal_policy
        if not policy_map:
            return "all"
        ranking = {"all": 0, "asks_only": 1, "silent": 2}
        best = "all"
        for identifier in self.active_focus_ids_cached():
            candidate = policy_map.get(identifier, "all")
            if ranking.get(candidate, 0) > ranking[best]:
                best = candidate
        return best

    def courtesy_signals_held(self) -> bool:
        """Quiet Hour OR an active Focus with a non-"all" policy: the
        courtesy glows (celebration, notification, quota, calendar,
        reminders) stay quiet. Hard asks and weather still break
        through -- only a "silent" policy hushes the escalation chime,
        and even that never hides the ask itself."""
        return self.quiet_active() or self.active_focus_policy() != "all"

    @objc.IBAction
    def toggleQuietHour_(self, _sender):
        if self.quiet_active():
            self.quiet_until_monotonic = 0.0
        else:
            self.quiet_until_monotonic = time.monotonic() + 3600.0
        self._menu_signature = None
        self.refresh_(None)

    @objc.IBAction
    def clearFinished_(self, _sender):
        """Settle the finished rows out of the dropdown -- they return
        automatically if the session comes back to life."""
        snapshot = self.last_snapshot
        if snapshot is None:
            return
        cleared = set(getattr(self, "cleared_session_ids", set()))
        for status in snapshot.statuses:
            if not status.is_subagent and status.mode == AgentMode.COMPLETED:
                cleared.add(status.agent_id)
        self.cleared_session_ids = cleared
        self._menu_signature = None
        self.refresh_(None)

    def timebox_overtime(self) -> bool:
        return getattr(self, "timebox_overtime_since", None) is not None

    def timebox_overtime_minutes(self) -> int:
        since = getattr(self, "timebox_overtime_since", None)
        if since is None:
            return 0
        return int((time.monotonic() - since) // 60)

    def timer_display_program(self, brightness: float, led_count: int) -> str:
        """The Timer display's three faces: the working-color fill, an
        AMBER final minute, and the post-zero overtime ember deepening
        toward red the longer you run over -- blowing through your own
        deadline is visible, not a silent nothing."""
        if self.timebox_overtime():
            depth = min(1.0, (time.monotonic() - self.timebox_overtime_since) / 600.0)
            green = round(159 * (1.0 - depth * 0.82))
            blue = round(10 * (1.0 - depth))
            ember = f"#FF{green:02X}{blue:02X}"
            dim = scale_hex_brightness(ember, 0.25)
            return apply_brightness(
                f"{ember} 1400ms pulse\n{dim} 1800ms cosine\nrepeat", brightness
            )
        remaining = (
            max(0.0, self.timebox_ends_at - time.monotonic())
            if getattr(self, "timebox_ends_at", None) is not None
            else None
        )
        color = self.settings.colors.mode_colors.get("working", "#00E5FF")
        if remaining is not None and remaining < 60.0:
            color = "#FFB340"
        return timer_fill_program(
            self.timer_fill_fraction(),
            led_count=led_count,
            brightness=brightness,
            color=color,
        )

    def timebox_active(self) -> bool:
        ends_at = getattr(self, "timebox_ends_at", None)
        return ends_at is not None and time.monotonic() < ends_at

    def timer_fill_fraction(self) -> float:
        # An active timebox owns the fill: it DRAINS toward zero (a
        # countdown reads as remaining time, not elapsed).
        if self.timebox_active():
            total = max(1.0, getattr(self, "timebox_total_seconds", 0.0))
            remaining = max(0.0, self.timebox_ends_at - time.monotonic())
            return min(1.0, remaining / total)
        if getattr(self, "working_since", None) is None:
            return 0.0
        expected_seconds = self.settings.timer_expected_minutes * 60.0
        if expected_seconds <= 0.0:
            return 1.0
        return min(1.0, (time.monotonic() - self.working_since) / expected_seconds)

    def run_shortcut_named(self, name: str) -> None:
        """`shortcuts run <name>` on a daemon thread (story #10) -- the
        CLI can block on the one-time per-shortcut permission toast, so
        it must never ride the main thread. Failures log quietly and
        never retry."""

        def _run() -> None:
            try:
                result = subprocess.run(
                    ["shortcuts", "run", name],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    log_status_bar(f"shortcut '{name}' exited {result.returncode}")
            except Exception as exc:
                log_status_bar(f"shortcut '{name}' failed: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    def fire_timebox_off_shortcut(self) -> None:
        """Pop-and-fire: the end-of-timebox Shortcut runs exactly once
        whether the drain hit zero or the user pressed Stop first."""
        name = getattr(self, "_timebox_off_shortcut", None)
        self._timebox_off_shortcut = None
        if name:
            self.run_shortcut_named(name)

    @objc.IBAction
    def startTimebox_(self, sender):
        minutes = float(sender.representedObject() or 25)
        self.timebox_total_seconds = minutes * 60.0
        self.timebox_ends_at = time.monotonic() + self.timebox_total_seconds
        # Story #10 (Focus handshake): a mapped preset flips its Focus
        # on now and off when the drain ends -- one click, whole ritual.
        on_name, off_name = self.settings.timebox_shortcut_pair(str(int(minutes)))
        self._timebox_off_shortcut = off_name or None
        if on_name:
            self.run_shortcut_named(on_name)
        self.refresh_(None)
        message = f"Timebox: {minutes:g} minutes on the bar."
        if on_name:
            message += f" Running \u201c{on_name}\u201d."
        self.set_settings_message(message)

    @objc.IBAction
    def stopTimebox_(self, _sender):
        self.timebox_overtime_since = None
        self.timebox_ends_at = None
        self.timebox_total_seconds = 0.0
        self.fire_timebox_off_shortcut()
        self.refresh_(None)
        self.set_settings_message("Timebox stopped.")

    def current_escalation_stage(self) -> int:
        elapsed = (
            time.monotonic() - self.ask_blocked_since
            if self.ask_blocked_since is not None
            else None
        )
        return signals_module.escalation_stage(
            elapsed,
            ramp_seconds=self.settings.escalation_ramp_seconds,
            menu_bar_seconds=self.settings.escalation_menu_bar_seconds,
            final_seconds=self.settings.escalation_final_seconds,
            tier=self.settings.escalation_tier,
        )

    def apply_escalation(self, *, allow_refresh: bool = False) -> None:
        """Starts/stops the stage-2 menu-bar flash, fires the stage-3
        chime once per episode, and (when invoked from the watcher tick,
        where recursion is impossible) triggers a resync so stage
        changes reach the light surfaces promptly."""
        stage = self.current_escalation_stage()
        changed = stage != self.escalation_last_stage
        self.escalation_last_stage = stage

        if stage >= 2:
            if self.escalation_flash_timer is None:
                self.escalation_flash_timer = (
                    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                        0.6, self, "escalationFlashTick:", None, True
                    )
                )
        elif self.escalation_flash_timer is not None:
            self.escalation_flash_timer.invalidate()
            self.escalation_flash_timer = None
            self.escalation_flash_on = False
            # Restore the honest title/icon.
            self.set_status(self.current_state)

        if (
            stage >= 3
            # Takeover INCLUDES the chime: picking the louder tier used
            # to silently lose the sound (the tiers are a ceiling, and
            # the chime only fired on exact equality).
            and self.settings.escalation_tier
            in (
                signals_module.ESCALATION_TIER_CHIME,
                signals_module.ESCALATION_TIER_TAKEOVER,
            )
            and not self.escalation_chimed
        ):
            self.escalation_chimed = True
            # Story #12: a "silent" Focus policy hushes the sound but
            # latches the chime stage normally -- the light, menu-bar
            # takeover and webhook still tell the story.
            if self.active_focus_policy() != "silent":
                try:
                    from AppKit import NSSound

                    sound = NSSound.soundNamed_("Glass")
                    if sound is not None:
                        sound.play()
                except Exception:
                    pass
            if self.active_focus_policy() != "silent":
                snapshot = getattr(self, "last_snapshot", None)
                asks = ask_statuses(snapshot) if snapshot is not None else []
                oldest = asks[0] if asks else None
                try:
                    deliver_macos_notification(
                        "Agent needs you",
                        (
                            f"{oldest.provider.title()}: {oldest.display_name[:100]}"
                            if oldest is not None
                            else "A session has been blocked on you for minutes."
                        ),
                        user_info=(
                            {"agent_id": oldest.agent_id} if oldest is not None else None
                        ),
                    )
                except Exception as exc:
                    log_status_bar(f"escalation notification failed: {exc}")
            self.fire_escalation_webhook(stage)

        if changed and allow_refresh:
            self.refresh_(None)

    @objc.IBAction
    def escalationFlashTick_(self, _timer):
        if self.status_item is None:
            return
        button = self.status_item.button()
        if button is None:
            return
        self.escalation_flash_on = not self.escalation_flash_on
        if self.escalation_flash_on:
            button.setTitle_(f" {STATE_ASK.label}")
            button.setImage_(image_for_symbol(STATE_ASK.symbol, STATE_ASK.label))
        else:
            button.setTitle_("")
            button.setImage_(image_for_symbol(self.current_state.symbol, self.current_state.label))

    def escalation_takeover_program(self, brightness: float, led_count: int = 8) -> str:
        """Fast full-bar strobe in the ask color -- the opt-in "don't
        let me miss this" finale."""
        ask_color = self.settings.colors.mode_colors.get("ask", "#FF3A00")
        style = signals_module.SignalStyle(ask_color, signals_module.PATTERN_BREATHE, 0.45, 1.0)
        return style_to_program(style, brightness, led_count=led_count)

    def agent_render_colors(self):
        """ColorSettings for agent rendering with the stage>=1
        quickening applied: blend cycles run at x0.75 speed, so the
        ramp stays visible even at full brightness where the boost
        alone would clamp away to nothing.

        Also honors "Preview live on device" being OFF: while the
        Colors window is open with preview disabled, hardware keeps
        rendering the colors from when the window opened -- edits
        used to leak to the device within one sync tick anyway."""
        colors = self.settings.colors
        settings_window = getattr(self, "settings_window", None)
        if (
            settings_window is not None
            and settings_window.isVisible()
            and getattr(self, "current_settings_pane", "") == "color_studio"
            and not getattr(self, "color_preview_enabled", True)
        ):
            if getattr(self, "colors_preview_baseline", None) is None:
                self.colors_preview_baseline = colors
            colors = self.colors_preview_baseline
        else:
            self.colors_preview_baseline = None
        if self.current_escalation_stage() >= 1:
            quickened = max(
                colors_module.MIN_CYCLE_SPEED_SECONDS, colors.cycle_speed_seconds * 0.75
            )
            colors = colors.with_cycle_speed(quickened)
        elif getattr(self, "working_since", None) is not None:
            # Deep-work patina: breathing slows the longer the oldest
            # session works uninterrupted -- skittish when fresh,
            # oceanic after an hour. Elif, never else: an ask's
            # quickening always wins over patina's calm.
            worked = time.monotonic() - self.working_since
            if worked > 900.0:
                factor = 1.0 + min(0.8, (worked - 900.0) / 2700.0 * 0.8)
                colors = colors.with_cycle_speed(colors.cycle_speed_seconds * factor)
        return colors

    def post_webhook(self, payload_dict: dict) -> None:
        """The outbound bridge: one JSON POST on a daemon thread to the
        configured URL. Failures log quietly and never retry -- same
        contract as every watcher. Callers own their own edge/latch
        semantics; this just delivers."""
        url = (self.settings.escalation_webhook_url or "").strip()
        if not url:
            return
        payload = json.dumps(payload_dict).encode("utf-8")
        event_name = str(payload_dict.get("event", "webhook"))

        def _post():
            try:
                import urllib.request

                request = urllib.request.Request(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(request, timeout=10).close()
                log_status_bar(f"webhook delivered: {event_name}")
            except Exception as exc:
                log_status_bar(f"webhook failed ({event_name}): {exc}")

        threading.Thread(target=_post, daemon=True).start()

    def post_completion_notification(self, status) -> None:
        """An opt-in macOS banner when a MAIN session finishes -- rides
        the same fresh, edge-triggered path as the sweep, so restarts
        and replays never post. Quiet Hour and Focus policies hold it
        like every other courtesy signal."""
        if (
            status is None
            or not self.settings.completion_notification_enabled
            or self.courtesy_signals_held()
        ):
            return
        try:
            deliver_macos_notification(
                f"{status.provider.title()} finished",
                status.display_name[:120],
                user_info={"agent_id": status.agent_id},
            )
        except Exception as exc:
            log_status_bar(f"completion notification failed: {exc}")

    def userNotificationCenter_shouldPresentNotification_(self, _center, _notification):
        # Present even while an app of ours is frontmost -- the whole
        # point is eyes-on-another-screen.
        return True

    def userNotificationCenter_didActivateNotification_(self, _center, notification):
        """Clicking a banner jumps to the session that posted it."""
        info = notification.userInfo() or {}
        agent_id = str(info.get("agent_id", ""))
        if not agent_id:
            return
        snapshot = getattr(self, "last_snapshot", None)
        if snapshot is None:
            return
        pool = (*snapshot.statuses, *getattr(snapshot, "stale_statuses", ()))
        status = next((s for s in pool if s.agent_id == agent_id), None)
        if status is not None:
            self.open_session(status, None, remember=False)

    def webhook_event_enabled(self, key: str) -> bool:
        """Moment events are opt-in per key; stage-3 escalation always
        fires when a URL is set (the pre-bridge contract)."""
        return key in self.settings.webhook_events

    def fire_escalation_webhook(self, stage: int) -> None:
        """One POST per ask episode when stage 3 lands (latched beside
        the chime): "an agent has been blocked on you for minutes" can
        find you in the kitchen -- an ntfy topic, Home Assistant,
        anything that takes JSON. Empty URL = off; failures log quietly
        and never retry into spam."""
        url = (self.settings.escalation_webhook_url or "").strip()
        if not url or getattr(self, "escalation_webhooked", False):
            return
        self.escalation_webhooked = True
        snapshot = getattr(self, "last_snapshot", None)
        asks = ask_statuses(snapshot) if snapshot is not None else []
        oldest_age = 0.0
        if getattr(self, "ask_blocked_since", None) is not None:
            oldest_age = max(0.0, time.monotonic() - self.ask_blocked_since)
        self.post_webhook(
            {
                "event": "sidepulse.escalation",
                "stage": stage,
                "ask_count": len(asks),
                "oldest_ask_seconds": round(oldest_age),
                "sessions": [
                    {"provider": status.provider, "label": status.display_name[:80]}
                    for status in asks[:5]
                ],
            }
        )

    def escalation_takeover_active(self) -> bool:
        return (
            self.settings.escalation_tier == signals_module.ESCALATION_TIER_TAKEOVER
            and self.current_escalation_stage() >= 3
        )

    def set_status(
        self, state: StatusBarState, *, ask_count: int = 0, done_badge: bool = False
    ) -> None:
        previous = self.current_state
        self.current_state = state
        self.current_ask_count = ask_count
        self.unseen_done_badge = done_badge
        if state == STATE_IDLE:
            if previous != STATE_IDLE or self.idle_since_monotonic is None:
                self.idle_since_monotonic = time.monotonic()
        else:
            self.idle_since_monotonic = None
        if self.status_item is None:
            return
        button = self.status_item.button()
        if button is None:
            return
        # The badge: with several sessions waiting at once, the count is
        # the difference between "check sometime" and "two are stuck".
        if getattr(self.settings, "menu_bar_label_enabled", False):
            title = (
                f" {state.label} ({ask_count})" if ask_count >= 2 else f" {state.label}"
            )
        else:
            # Icon-only, like native menu extras -- the symbol carries the
            # state; text appears only when it MEANS something (a count).
            title = f" ({ask_count})" if ask_count >= 2 else ""
        if done_badge and ask_count == 0:
            # "Something finished since you last looked" -- cleared the
            # moment the menu opens (the T3 lastVisitedAt read model).
            title = f"{title} \u2713" if title else " \u2713"
        button.setTitle_(title)
        button.setImage_(image_for_symbol(state.symbol, state.label))
        button.setToolTip_(f"SidePulse Agent Monitor: {state.label}")
        if previous != state:
            log_status_bar(f"state={state.label}")

    def build_monitor(self) -> LiveAgentMonitor:
        socket_path = default_event_socket_path()
        return LiveAgentMonitor(
            sources=(SourceSpec("event-bus", socket_path),),
            stale_after_seconds=3600,
            latest_state_path=default_latest_state_path(),
        )

    def build_transcript_monitor(self) -> AgentMonitor | None:
        """The transcript-fallback scanner behind the Agents pane's
        "Watch ... transcripts" switches. Those switches used to save a
        setting nothing in the app ever read -- transcript scanning
        lived only in the CLI's AgentMonitor, so a user without hooks
        saw "fallback enabled" and a menu bar that stayed idle forever.
        This restricted AgentMonitor reads ONLY the transcript sources;
        ingest_transcript_fallback feeds its records into the live
        monitor's own state machine on every refresh."""
        wanted = {
            provider
            for provider, enabled in (
                (CODEX_TRANSCRIPT_PROVIDER, self.settings.codex_transcripts_enabled),
                (CLAUDE_TRANSCRIPT_PROVIDER, self.settings.claude_transcripts_enabled),
            )
            if enabled
        }
        if not wanted:
            return None
        sources = tuple(
            source
            for source in default_sources(self.settings)
            if source.provider in wanted
        )
        if not sources:
            return None
        return AgentMonitor(sources=sources)

    def ingest_transcript_fallback(self) -> None:
        monitor = getattr(self, "transcript_monitor", None)
        if monitor is None:
            return
        try:
            signature = monitor.input_signature()
        except Exception:
            signature = None
        if signature is not None and signature == getattr(
            self, "transcript_fallback_signature", None
        ):
            # Nothing on disk changed -- skip the full sort-and-replay.
            return
        try:
            records = sorted(monitor.iter_records(), key=lambda record: record.logged_at)
        except Exception as exc:
            log_status_bar(f"transcript fallback error: {exc}")
            return
        watermark = getattr(self, "transcript_watermark", None)
        newest = watermark
        for record in records:
            if watermark is not None and record.logged_at <= watermark:
                continue
            self.monitor.ingest_record(record)
            if newest is None or record.logged_at > newest:
                newest = record.logged_at
        self.transcript_watermark = newest
        self.transcript_fallback_signature = signature

    def reload_monitor(self) -> None:
        self.monitor = self.build_monitor()
        self.transcript_monitor = self.build_transcript_monitor()
        self.transcript_watermark = None
        self.transcript_fallback_signature = None

    def start_event_server(self) -> None:
        self.stop_event_server()
        self.event_server = HookEventServer(self.handle_hook_event_message)
        try:
            socket_path = self.event_server.start()
            log_status_bar(f"event_server listening={socket_path}")
        except Exception as exc:
            self.event_server = None
            log_status_bar(f"event_server error: {exc}")

    def stop_event_server(self) -> None:
        if self.event_server is not None:
            self.event_server.stop()
            self.event_server = None

    def handle_hook_event_message(self, provider: str, line: dict) -> None:
        try:
            record = parse_log_line(
                provider,
                json.dumps(line, separators=(",", ":"), ensure_ascii=False),
            )
            if record is not None:
                self.monitor.ingest_record(record)
                self.schedule_event_refresh()
        except Exception as exc:
            log_status_bar(f"event_server ingest error: {exc}")

    def schedule_event_refresh(self) -> None:
        if self.event_refresh_pending:
            return
        self.event_refresh_pending = True
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "refreshFromEvent:",
            None,
            False,
        )

    @objc.IBAction
    def refreshFromEvent_(self, _sender):
        self.event_refresh_pending = False
        # Time floor: event_refresh_pending only dedups events that pile
        # up BEFORE the main thread runs; a chatty agent still drove one
        # full refresh per event, back to back. Leading edge runs
        # immediately; bursts get one trailing refresh instead.
        now = time.monotonic()
        last = getattr(self, "_last_event_refresh_at", 0.0)
        if now - last < EVENT_REFRESH_FLOOR_SECONDS:
            if getattr(self, "_trailing_refresh_timer", None) is None:
                self._trailing_refresh_timer = (
                    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                        EVENT_REFRESH_FLOOR_SECONDS,
                        self,
                        "trailingRefreshFire:",
                        None,
                        False,
                    )
                )
            return
        self._last_event_refresh_at = now
        self.refresh_(None)

    @objc.IBAction
    def trailingRefreshFire_(self, _timer):
        self._trailing_refresh_timer = None
        self._last_event_refresh_at = time.monotonic()
        self.refresh_(None)

    def replay_debug_logs(self) -> None:
        replayed = replay_recent_debug_logs(self.monitor)
        if replayed:
            log_status_bar(f"startup_replay events={replayed}")

    def ensure_settings_pane(self, key: str) -> None:
        """Build one settings pane on first visit (audit #5's lazy
        half). Installed hidden; the sidebar switcher unhides it."""
        panes = getattr(self, "settings_panes", None)
        container = getattr(self, "_settings_pane_container", None)
        if panes is None or container is None or key in panes:
            return
        try:
            pane, fields, buttons = _build_settings_pane(self, key)
        except KeyError:
            return
        container.addSubview_(pane)
        NSLayoutConstraint.activateConstraints_(
            [
                pane.topAnchor().constraintEqualToAnchor_(container.topAnchor()),
                pane.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
                pane.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
                pane.bottomAnchor().constraintEqualToAnchor_(container.bottomAnchor()),
            ]
        )
        pane.setHidden_(True)
        panes[key] = pane
        self.settings_fields.update(fields)
        self.settings_buttons.update(buttons)

    def ensure_all_settings_panes(self) -> None:
        """Every pane, built now -- for tests and any caller that needs
        the full control map rather than the lazy first-visit build."""
        for key, _label in SETTINGS_SIDEBAR_ITEMS:
            if not key.startswith("header:"):
                self.ensure_settings_pane(key)
        # Freshly built panes snapshot settings at construction; one
        # refresh pass brings every control to CURRENT state.
        self.refresh_settings_window()

    def show_settings_window(self) -> None:
        if self.settings_window is None:
            self.settings_window = build_settings_window(self)
            if self.settings_sidebar_table is not None:
                default_row = next(
                    index
                    for index, (key, _label) in enumerate(SETTINGS_SIDEBAR_ITEMS)
                    if key == DEFAULT_SETTINGS_PANE
                )
                self.settings_sidebar_table.selectRowIndexes_byExtendingSelection_(
                    NSIndexSet.indexSetWithIndex_(default_row), False
                )
        self.refresh_settings_window()
        # Animate the Signals pane's pattern thumbnails and previews
        # while the window is visible (self-invalidating on close, same
        # pattern as the welcome demo timer).
        if getattr(self, "signal_preview_timer", None) is None:
            # 12Hz, not 30: every tick steps ~24 WASM preview engines
            # (JavaScriptCore call + JSON round-trip each); pattern
            # thumbnails read fine at 12 and the CPU cost drops by ~60%.
            self.signal_preview_timer = (
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    1.0 / 8.0, self, "redrawSignalPreviews:", None, True
                )
            )
        self.settings_window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    # --- Settings sidebar (NSTableViewDataSource / NSTableViewDelegate) ---
    #
    # native_ui.build_sidebar_table() only builds the view; PyObjC's
    # Objective-C bridge dispatches dataSource/delegate calls via
    # respondsToSelector:, which only a real NSObject subclass (this one)
    # can satisfy -- see native_ui's module docstring.

    def numberOfRowsInTableView_(self, _table_view) -> int:
        return len(SETTINGS_SIDEBAR_ITEMS)

    def tableView_viewForTableColumn_row_(self, _table_view, _column, row):
        key, label = SETTINGS_SIDEBAR_ITEMS[row]
        if key.startswith("header:"):
            header = native_ui.make_label(label, secondary=True, size=10.0)
            holder = NSView.alloc().init()
            holder.setTranslatesAutoresizingMaskIntoConstraints_(False)
            header.setTranslatesAutoresizingMaskIntoConstraints_(False)
            holder.addSubview_(header)
            NSLayoutConstraint.activateConstraints_(
                [
                    header.leadingAnchor().constraintEqualToAnchor_constant_(
                        holder.leadingAnchor(), 12.0
                    ),
                    header.bottomAnchor().constraintEqualToAnchor_constant_(
                        holder.bottomAnchor(), -3.0
                    ),
                ]
            )
            return holder
        return native_ui.sidebar_cell_view(label, SIDEBAR_ICONS.get(key))

    def tableView_shouldSelectRow_(self, _table_view, row) -> bool:
        return not SETTINGS_SIDEBAR_ITEMS[row][0].startswith("header:")

    def tableView_isGroupRow_(self, _table_view, _row) -> bool:
        return False

    def tableViewSelectionDidChange_(self, notification):
        table = notification.object()
        row = table.selectedRow()
        if row < 0 or row >= len(SETTINGS_SIDEBAR_ITEMS):
            return
        selected_key = SETTINGS_SIDEBAR_ITEMS[row][0]
        if selected_key.startswith("header:"):
            return
        self.ensure_settings_pane(selected_key)
        outgoing_key = getattr(self, "current_settings_pane", None)
        self.current_settings_pane = selected_key
        # Crossfade instead of a hard swap -- and a generation counter so
        # rapid pane-hopping never queues stale hide callbacks.
        generation = getattr(self, "_pane_transition_generation", 0) + 1
        self._pane_transition_generation = generation
        from AppKit import NSAnimationContext

        for key, pane in self.settings_panes.items():
            if key == selected_key:
                pane.setAlphaValue_(0.0)
                pane.setHidden_(False)

                def _fade_in(context, view=pane):
                    context.setDuration_(0.16)
                    view.animator().setAlphaValue_(1.0)

                NSAnimationContext.runAnimationGroup_completionHandler_(_fade_in, None)
            elif key == outgoing_key and outgoing_key is not None:

                def _hide(view=pane, expected=generation):
                    if self._pane_transition_generation == expected:
                        view.setHidden_(True)
                    view.setAlphaValue_(1.0)

                def _fade_out(context, view=pane):
                    context.setDuration_(0.14)
                    view.animator().setAlphaValue_(0.0)

                NSAnimationContext.runAnimationGroup_completionHandler_(
                    _fade_out, _hide
                )
            else:
                pane.setHidden_(key != selected_key)
                pane.setAlphaValue_(1.0)
        if selected_key == "color_studio":
            self.refresh_colors_window()
        # Gated sections (provider probes, signal cards) refresh as
        # their pane appears rather than on every unrelated click.
        self.refresh_settings_window()

    @objc.IBAction
    def openColorsWindow_(self, _sender):
        self.show_colors_window()

    def show_colors_window(self) -> None:
        """Opens Settings at the Color Studio pane -- the standalone
        Colors window is retired; one app, one window."""
        self.show_settings_window()
        self.select_settings_pane("color_studio")
        self.refresh_colors_window()

    def select_settings_pane(self, pane_key: str) -> None:
        if self.settings_sidebar_table is None:
            return
        for index, (key, _label) in enumerate(SETTINGS_SIDEBAR_ITEMS):
            if key == pane_key:
                self.settings_sidebar_table.selectRowIndexes_byExtendingSelection_(
                    NSIndexSet.indexSetWithIndex_(index), False
                )
                return

    def refresh_colors_window(self) -> None:
        if getattr(self, "color_fields", None) is None:
            return
        scenario_popup = self.color_fields.get("preview_scenario_popup")
        if scenario_popup is not None:
            select_preview_scenario(scenario_popup, self.color_preview_scenario)
        colors = self.settings.colors
        for key in MODE_COLOR_KEYS:
            self.refresh_color_row(("mode", key), colors.mode_color(key))
        for spec in PROVIDER_SPECS:
            self.refresh_color_row(("agent", spec.provider), colors.agent_color(spec.provider))
        refresh_blend_and_speed_fields(self)
        fade_fields = self.color_fields.get("fade_fields") or {}
        for key, fields in fade_fields.items():
            floor, ceiling = colors.fade_range(key)
            set_field_value(fields.get("floor"), f"{round(floor * 100)}")
            set_field_value(fields.get("ceiling"), f"{round(ceiling * 100)}")
        # Animation Style thumbnails: re-ring the selected style and
        # re-bake each thumb's program -- presets/palettes change both
        # the style AND the mode colors the thumbs preview, and these
        # only got set at build time (the popups this loop used to sync
        # were replaced by thumbnails and never existed at runtime).
        for key, thumbs in getattr(self, "colors_animation_thumbs", {}).items():
            _apply_thumb_selection(thumbs, colors.animation_style(key))
            for style, thumb in thumbs.items():
                thumb.setProgram_(_mode_animation_thumb_program(self, key, style))
        self.refresh_colors_preview()

    def refresh_color_row(self, key: tuple[str, str], hex_value: str) -> None:
        for swatch_key, button in self.color_swatches.items():
            row_key, swatch_hex = swatch_key
            if row_key != key:
                continue
            is_selected = swatch_hex.upper() == hex_value.upper()
            set_swatch_selected(button, is_selected)
        label = self.color_hex_labels.get(key)
        if label is not None:
            label.setStringValue_(hex_value)

    def refresh_colors_preview(self) -> None:
        if not self.color_preview_rows:
            return
        if self.color_preview_scenario == colors_module.PREVIEW_SCENARIO_LIVE:
            snapshot = self.last_snapshot
            statuses = snapshot.statuses if snapshot is not None else ()
            is_live = bool(statuses)
            if not is_live:
                # Nothing real happening right now -- show a fixed demo
                # scenario instead of a blank/idle strip, so color and
                # blend-mode choices are always visible, not just when an
                # agent happens to be active.
                statuses = colors_module.demo_statuses_for_preview()
        else:
            # A specific scenario was picked -- always show it, regardless
            # of what's really running, so it can be compared side by side
            # with other choices on demand.
            statuses = colors_module.preview_statuses_for_scenario(self.color_preview_scenario)
            is_live = False
        legend_prefix = None
        if self.color_preview_scenario != colors_module.PREVIEW_SCENARIO_LIVE:
            label = colors_module.PREVIEW_SCENARIO_LABELS.get(self.color_preview_scenario, "Preview")
            legend_prefix = f"{label}: "
        for row in self.color_preview_rows:
            led_count = row["led_count"]
            legend = row["legend"]
            _, program = colors_module.program_for_snapshot(
                statuses, led_count=led_count, colors=self.settings.colors
            )
            controller = self.ensure_colors_preview_wasm(led_count, program)
            if controller is None:
                # WASM unavailable -- fall back to one static peak-color
                # frame rather than nothing (matches the Screen Bar's own
                # fallback for the same rare case).
                static_colors = colors_module.preview_led_colors(
                    statuses, led_count=led_count, colors=self.settings.colors
                )
                for dot, hex_color in zip(row["dots"], static_colors):
                    set_preview_dot_color(dot, hex_color)
            legend.setStringValue_(colors_legend_text(statuses, is_live=is_live, prefix=legend_prefix))
        self.animate_colors_preview_once()
        self.start_colors_preview_animation()

    def ensure_colors_preview_wasm(self, led_count: int, program: str):
        """Parses `program` into this row's WASM controller (creating it on
        first use), skipping the reparse if the program text hasn't changed
        so an in-progress breath doesn't restart on every refresh tick.
        Returns None if the WASM engine is unavailable."""
        if self.color_preview_programs.get(led_count) == program:
            return self.color_preview_wasm.get(led_count)
        self.color_preview_programs[led_count] = program
        controller = self.color_preview_wasm.get(led_count)
        if controller is None:
            try:
                controller = SdLedWasmController(led_count)
            except LedWasmUnavailableError:
                self.color_preview_wasm[led_count] = None
                return None
            self.color_preview_wasm[led_count] = controller
        try:
            controller.parse(program, monotonic_ms())
        except Exception:
            return None
        return controller

    def start_colors_preview_animation(self) -> None:
        if self.color_preview_timer is not None:
            return
        self.color_preview_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / 12.0, self, "animateColorsPreviewTick:", None, True
        )

    def stop_colors_preview_animation(self) -> None:
        if self.color_preview_timer is not None:
            self.color_preview_timer.invalidate()
            self.color_preview_timer = None

    @objc.IBAction
    def animateColorsPreviewTick_(self, _sender):
        self.animate_colors_preview_once()
        # The unified animation thumbnails animate on the same tick.
        # Only the pane on screen animates: with mode, lid and preview
        # thumbs all ticking regardless of pane, scrolling ANY pane
        # dragged a fleet of JavaScriptCore steps behind it.
        pane = getattr(self, "current_settings_pane", None)
        if pane == "color_studio":
            for thumbs in getattr(self, "colors_animation_thumbs", {}).values():
                for thumb in thumbs.values():
                    if not thumb.isHiddenOrHasHiddenAncestor() and thumb.visibleRect().size.width > 0:
                        thumb.setNeedsDisplay_(True)
        elif pane == "animations":
            for thumb in getattr(self, "lid_animation_thumbs", {}).values():
                if not thumb.isHiddenOrHasHiddenAncestor() and thumb.visibleRect().size.width > 0:
                    thumb.setNeedsDisplay_(True)

    def _refresh_lid_thumb_selection(self) -> None:
        """Re-ring the lid preset thumbnails against the CURRENT saved
        programs -- the ring used to be painted only at build time, so
        picking a new preset left the old one highlighted until app
        restart (and rendered in default black, not the accent)."""
        thumbs = getattr(self, "lid_animation_thumbs", None) or {}
        for (kind, name), view in thumbs.items():
            layer = view.layer()
            if layer is None:
                continue
            current = (self.settings.lid_animation(kind).program or "").strip()
            preset_program = next(
                (
                    program
                    for preset_name, _duration, program in LID_ANIMATION_PRESETS.get(kind, ())
                    if preset_name == name
                ),
                None,
            )
            if preset_program is not None and preset_program.strip() == current:
                layer.setBorderWidth_(2.0)
                layer.setBorderColor_(NSColor.controlAccentColor().CGColor())
            else:
                layer.setBorderWidth_(0.0)

    @objc.IBAction
    def selectLidPresetThumb_(self, recognizer):
        view = recognizer.view()
        kind = getattr(view, "lid_preset_kind", None)
        name = getattr(view, "lid_preset_name", None)
        if not kind or not name:
            return
        for preset_name, duration, program in LID_ANIMATION_PRESETS.get(kind, ()):
            if preset_name == name:
                self.settings = self.settings.with_lid_animation(
                    kind, program=program, duration_seconds=duration
                )
                save_settings(self.settings)
                self.refresh_settings_window()
                self.set_settings_message(f"Lid animation: {name}.")
                # Play it once so choosing feels like something happened.
                self.play_lid_animation(kind)
                return

    @objc.IBAction
    def selectModeAnimationThumb_(self, recognizer):
        view = recognizer.view()
        key = getattr(view, "mode_anim_key", None)
        style = getattr(view, "mode_anim_style", None)
        if not key or not style:
            return
        try:
            self.settings = self.settings.with_colors(
                self.settings.colors.with_mode_animation(key, style)
            )
        except ValueError:
            return
        save_settings(self.settings)
        thumbs = getattr(self, "colors_animation_thumbs", {}).get(key)
        if thumbs:
            _apply_thumb_selection(thumbs, style)
        self.refresh_colors_preview()
        if self.color_preview_enabled:
            self.push_colors_preview_to_device()
        self.refresh_(None)
        self.set_settings_message(
            f"{key.title()}: {ANIMATION_STYLE_DISPLAY_LABELS.get(style, style)} animation."
        )

    def animate_colors_preview_once(self) -> None:
        if self.settings_window is None or not self.settings_window.isVisible():
            self.stop_colors_preview_animation()
            self.release_preview_engines()
            return
        now_ms = monotonic_ms()
        for row in self.color_preview_rows:
            controller = self.color_preview_wasm.get(row["led_count"])
            if controller is None:
                continue
            try:
                pixels = controller.step(now_ms)
            except Exception:
                continue
            for dot, pixel in zip(row["dots"], pixels[: row["led_count"]]):
                set_preview_dot_rgb(dot, *pixel)

    def push_colors_preview_to_device(self) -> None:
        snapshot = self.last_snapshot
        statuses = snapshot.statuses if snapshot is not None else ()
        self.sync_leds(
            snapshot.aggregate.mode if snapshot is not None else AgentMode.IDLE_READY,
            None,
            LED_DISPLAY_AGENT,
            statuses,
        )

    @objc.IBAction
    def toggleColorPreviewLive_(self, sender):
        self.color_preview_enabled = checkbox_is_on(sender)
        if self.color_preview_enabled:
            self.push_colors_preview_to_device()

    @objc.IBAction
    def selectAgentColorSwatch_(self, sender):
        payload = sender.representedObject() or {}
        provider = payload.get("provider")
        hex_value = payload.get("hex")
        if not provider or not hex_value:
            return
        self.apply_color_change(("agent", provider), hex_value)

    @objc.IBAction
    def selectModeColorSwatch_(self, sender):
        payload = sender.representedObject() or {}
        key = payload.get("key")
        hex_value = payload.get("hex")
        if not key or not hex_value:
            return
        self.apply_color_change(("mode", key), hex_value)

    @objc.IBAction
    def openCustomAgentColor_(self, sender):
        payload = sender.representedObject() or {}
        provider = payload.get("provider")
        if not provider:
            return
        self.open_custom_color_panel(("agent", provider), self.settings.colors.agent_color(provider))

    @objc.IBAction
    def openCustomModeColor_(self, sender):
        payload = sender.representedObject() or {}
        key = payload.get("key")
        if not key:
            return
        self.open_custom_color_panel(("mode", key), self.settings.colors.mode_color(key))

    def open_custom_color_panel(self, target_key: tuple[str, str], current_hex: str) -> None:
        self.active_color_target = target_key
        # Route exclusively to the Colors window (not a signal card).
        self.color_panel_signal_key = None
        panel = NSColorPanel.sharedColorPanel()
        panel.setColor_(nscolor_from_hex(current_hex))
        panel.setTarget_(self)
        panel.setAction_("applyCustomColorFromPanel:")
        panel.orderFront_(None)

    @objc.IBAction
    def applyCustomColorFromPanel_(self, sender):
        if self.active_color_target is None:
            return
        # Commit on release only: the panel's continuous action fired a
        # settings save + full Colors-window rebuild per drag tick.
        if self._slider_event_is_drag():
            return
        hex_value = hex_from_nscolor(sender.color())
        self.apply_color_change(self.active_color_target, hex_value)

    def apply_color_change(self, target_key: tuple[str, str], hex_value: str) -> None:
        kind, key = target_key
        colors = self.settings.colors
        if kind == "agent":
            colors = colors.with_agent_color(key, hex_value)
        else:
            colors = colors.with_mode_color(key, hex_value)
        self.settings = self.settings.with_colors(colors)
        save_settings(self.settings)
        self.refresh_colors_window()
        if self.color_preview_enabled:
            self.push_colors_preview_to_device()

    @objc.IBAction
    def setPreviewScenario_(self, sender):
        payload = sender.selectedItem().representedObject() if sender.selectedItem() else None
        if not payload or "scenario" not in payload:
            return
        scenario = payload["scenario"]
        if scenario not in colors_module.PREVIEW_SCENARIO_CHOICES:
            return
        self.color_preview_scenario = scenario
        self.refresh_colors_preview()

    @objc.IBAction
    def setColorPreset_(self, sender):
        payload = sender.selectedItem().representedObject() if sender.selectedItem() else None
        if not payload or payload.get("preset") in (None, PRESET_CUSTOM):
            # Custom isn't a package to apply -- it's the honest label for
            # "you've tweaked things yourself".
            refresh_blend_and_speed_fields(self)
            return
        try:
            colors = apply_preset(self.settings.colors, payload["preset"])
        except ValueError:
            return
        self.settings = self.settings.with_colors(colors)
        save_settings(self.settings)
        # Full refresh, not just blend/speed: presets also change the
        # animation style and every fade floor/ceiling. Refreshing only
        # part of the window left those controls stale -- and the next
        # fade-field edit committed the stale values back, silently
        # reverting the preset the user just applied.
        self.refresh_colors_window()
        self.refresh_colors_preview()
        if self.color_preview_enabled:
            self.push_colors_preview_to_device()
        self.refresh_(None)
        self.set_settings_message(f"Preset applied: {PRESET_LABELS[payload['preset']]}.")

    @objc.IBAction
    def setBlendMode_(self, sender):
        payload = sender.selectedItem().representedObject() if sender.selectedItem() else None
        if not payload or "blend_mode" not in payload:
            return
        try:
            colors = self.settings.colors.with_blend_mode(payload["blend_mode"])
        except ValueError:
            return
        self.settings = self.settings.with_colors(colors)
        save_settings(self.settings)
        description = self.color_fields.get("blend_description")
        if description is not None:
            description.setStringValue_(BLEND_MODE_DESCRIPTIONS.get(payload["blend_mode"], ""))
            description.setToolTip_(colors_module.BLEND_MODE_TOOLTIPS.get(payload["blend_mode"], ""))
        self.refresh_colors_preview()
        if self.color_preview_enabled:
            self.push_colors_preview_to_device()

    @objc.IBAction
    def applyCycleSpeed_(self, _sender):
        field = self.color_fields.get("speed_field")
        seconds = parse_seconds_field(field)
        if seconds is None:
            return
        colors = self.settings.colors.with_cycle_speed(seconds)
        self._commit_colors_and_refresh(colors)

    @objc.IBAction
    def toggleUrgencyAlert_(self, sender):
        colors = self.settings.colors.with_round_robin_urgency_alert(checkbox_is_on(sender))
        self._commit_colors_and_refresh(colors)

    @objc.IBAction
    def toggleColorByProject_(self, sender):
        colors = self.settings.colors.with_color_by_project(checkbox_is_on(sender))
        self._commit_colors_and_refresh(colors)

    @objc.IBAction
    def toggleDoneCelebration_(self, sender):
        enabled = checkbox_is_on(sender)
        self.settings = self.settings.with_completion_sweep_enabled(enabled)
        colors = self.settings.colors.with_done_celebration_enabled(enabled)
        self._commit_colors_and_refresh(colors)

    @objc.IBAction
    def toggleRoundRobinUseGlobalSpeed_(self, sender):
        self._toggle_speed_override(BLEND_MODE_ROUND_ROBIN, use_global=checkbox_is_on(sender))

    @objc.IBAction
    def toggleCycleUseGlobalSpeed_(self, sender):
        self._toggle_speed_override(BLEND_MODE_CYCLE, use_global=checkbox_is_on(sender))

    def _toggle_speed_override(self, mode_key: str, *, use_global: bool) -> None:
        colors = self.settings.colors
        if use_global:
            colors = colors.with_global_speed_for_mode(mode_key)
        else:
            # Switching on: seed the override with the mode's current
            # effective speed (i.e. whatever the global was showing) rather
            # than silently jumping to some other default the moment the
            # checkbox is unchecked.
            colors = colors.with_speed_override(mode_key, colors.effective_speed_seconds(mode_key))
        self._commit_colors_and_refresh(colors)

    @objc.IBAction
    def applyRoundRobinSpeed_(self, _sender):
        self._apply_mode_speed(BLEND_MODE_ROUND_ROBIN, "round_robin_speed_field")

    @objc.IBAction
    def applyCycleModeSpeed_(self, _sender):
        self._apply_mode_speed(BLEND_MODE_CYCLE, "cycle_speed_field")

    def _apply_mode_speed(self, mode_key: str, field_key: str) -> None:
        field = self.color_fields.get(field_key)
        seconds = parse_seconds_field(field)
        if seconds is None:
            return
        colors = self.settings.colors.with_speed_override(mode_key, seconds)
        self._commit_colors_and_refresh(colors)

    def _commit_colors_and_refresh(self, colors: ColorSettings) -> None:
        self.settings = self.settings.with_colors(colors)
        save_settings(self.settings)
        refresh_blend_and_speed_fields(self)
        self.refresh_colors_preview()
        if self.color_preview_enabled:
            self.push_colors_preview_to_device()

    @objc.IBAction
    def setAnimationStyle_(self, sender):
        payload = sender.selectedItem().representedObject() if sender.selectedItem() else None
        if not payload or "mode_key" not in payload or "style" not in payload:
            return
        try:
            colors = self.settings.colors.with_mode_animation(payload["mode_key"], payload["style"])
        except ValueError:
            return
        self.settings = self.settings.with_colors(colors)
        save_settings(self.settings)
        self.refresh_colors_preview()
        if self.color_preview_enabled:
            self.push_colors_preview_to_device()

    @objc.IBAction
    def resetColorsToDefaults_(self, _sender):
        self.settings = self.settings.with_colors(ColorSettings.defaults())
        save_settings(self.settings)
        self.refresh_colors_window()
        if self.color_preview_enabled:
            self.push_colors_preview_to_device()

    @objc.IBAction
    def applyFadeIntensity_(self, _sender):
        fade_fields = self.color_fields.get("fade_fields") or {}
        colors = self.settings.colors
        for key, fields in fade_fields.items():
            floor_fraction = parse_percent_field(fields.get("floor"))
            ceiling_fraction = parse_percent_field(fields.get("ceiling"))
            if floor_fraction is not None:
                colors = colors.with_fade_floor(key, floor_fraction)
            if ceiling_fraction is not None:
                colors = colors.with_fade_ceiling(key, ceiling_fraction)
        self.settings = self.settings.with_colors(colors)
        save_settings(self.settings)
        self.refresh_colors_window()
        if self.color_preview_enabled:
            self.push_colors_preview_to_device()

    @objc.IBAction
    def closeColorsWindow_(self, _sender):
        # Retired with the standalone window; kept for selector compat.
        self.stop_colors_preview_animation()

    @objc.IBAction
    def setScreenBarGapWidth_(self, sender):
        self._apply_screen_bar_geometry_from_sliders(commit=not self._slider_event_is_drag())

    @objc.IBAction
    def setScreenBarWingLength_(self, sender):
        self._apply_screen_bar_geometry_from_sliders(commit=not self._slider_event_is_drag())

    def _slider_event_is_drag(self) -> bool:
        event = NSApp.currentEvent()
        return event is not None and event.type() == NSEventTypeLeftMouseDragged

    def _apply_screen_bar_geometry_from_sliders(self, *, commit: bool) -> None:
        """Live geometry while dragging (no disk writes per tick), the
        settings save on release -- the same feel as the brightness
        slider, so the bar visibly follows the thumb."""
        gap_slider = self.settings_fields.get("screen_bar_gap_slider")
        if gap_slider is None:
            return
        gap = float(gap_slider.doubleValue())
        if not commit:
            self.virtual_status_device.set_geometry_overrides(gap, None)
            self.virtual_status_device.reposition()
            return
        self.settings = self.settings.with_screen_bar_gap_width(gap)
        save_settings(self.settings)
        self.reposition_virtual_status_device_now()
        self.set_settings_message(f"Bar size: gap {gap:g} pt.")

    @objc.IBAction
    def resetScreenBarGeometry_(self, _sender):
        self.settings = self.settings.with_screen_bar_gap_width(None).with_screen_bar_wing_length(None)
        save_settings(self.settings)
        self.reposition_virtual_status_device_now()
        # Snap the sliders back to what Automatic computes right now.
        try:
            auto_gap = slot_width_for_screen(NSScreen.mainScreen())
        except Exception:
            auto_gap = 232.0
        gap_slider = self.settings_fields.get("screen_bar_gap_slider")
        if gap_slider is not None:
            gap_slider.setDoubleValue_(float(auto_gap))
        self.set_settings_message("Bar size back to Automatic.")

    @objc.IBAction
    def toggleScreenBarFollowAlcove_(self, sender):
        self.settings = self.settings.with_screen_bar_follow_alcove(
            checkbox_is_on(sender)
        )
        save_settings(self.settings)
        self.refresh_(None)

    @objc.IBAction
    def toggleScreenBarGauges_(self, sender):
        self.settings = self.settings.with_screen_bar_gauges_enabled(
            checkbox_is_on(sender)
        )
        save_settings(self.settings)
        self.refresh_(None)

    @objc.IBAction
    def toggleScreenBarWrapsMenuBar_(self, sender):
        enabled = checkbox_is_on(sender)
        self.settings = self.settings.with_virtual_status_device_wraps_menu_bar(enabled)
        save_settings(self.settings)
        self.reposition_virtual_status_device_now()
        self.refresh_screen_bar_preview()
        self.set_settings_message(
            "Screen Bar now extends along the menu bar." if enabled else "Screen Bar back to notch width only."
        )

    @objc.IBAction
    def setBracketStyle_(self, sender):
        item = sender.selectedItem()
        style = str(item.representedObject() or "auto") if item is not None else "auto"
        try:
            self.settings = self.settings.with_screen_bar_bracket_style(style)
        except ValueError:
            return
        save_settings(self.settings)
        self.virtual_status_device.set_bracket_style(style)
        self.refresh_(None)
        self.set_settings_message(f"Bracket colors: {item.title()}.")

    def reposition_virtual_status_device_now(self) -> None:
        """Alcove compatibility and wraps-menu-bar only change the Screen
        Bar's own geometry/drawing style -- they don't touch the LED
        program, so sync_virtual_status_device (the normal place these get
        read fresh) is never called again on its own here: with a physical
        device connected, that only runs when a real LED write happens
        (see schedule_screen_bar_sync), which -- especially now that agent
        layout no longer thrashes -- can be a long time after the setting
        was actually changed. Repositioning directly makes the toggle feel
        instant instead of "eventually, whenever something else happens to
        redraw it."
        """
        self.virtual_status_device.set_wraps_menu_bar(self.settings.virtual_status_device_wraps_menu_bar)
        self.virtual_status_device.set_geometry_overrides(
            self.settings.screen_bar_gap_width, self.settings.screen_bar_wing_length
        )
        self.virtual_status_device.reposition()

    def show_setup_window_if_needed(self) -> None:
        if should_show_setup_window(self.settings):
            self.show_setup_window()

    def show_setup_window(self) -> None:
        if self.setup_window is None:
            self.setup_window = build_setup_window(self)
        if self.setup_demo_timer is None:
            # Drives the welcome hero's live LED demo -- the view animates
            # itself from monotonic time on each draw, so the timer only
            # needs to mark it dirty, and only while the window shows.
            self.setup_demo_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0 / 30.0, self, "redrawSetupDemo:", None, True
            )
        self.refresh_setup_window()
        self.setup_window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    @objc.IBAction
    def redrawSetupDemo_(self, _sender):
        if self.setup_window is None or not self.setup_window.isVisible():
            # The window closed: stop the 30 Hz timer instead of ticking
            # forever (show_setup_window re-creates it next time).
            if self.setup_demo_timer is not None:
                self.setup_demo_timer.invalidate()
                self.setup_demo_timer = None
            return
        demo_view = self.setup_fields.get("demo_view")
        if demo_view is not None:
            demo_view.setNeedsDisplay_(True)

    def refresh_setup_window(self) -> None:
        if self.setup_window is None:
            return

        launch_installed = launch_agent_installed()
        eject_installed = sd_eject_guard_installed()
        sleep_installed = sleep_helper_installed()

        set_field_value(
            self.setup_fields.get("launch_status"),
            "Installed" if launch_installed else "Not installed",
        )
        set_field_value(
            self.setup_fields.get("eject_status"),
            "Installed" if eject_installed else "Not installed",
        )
        set_field_value(
            self.setup_fields.get("sleep_status"),
            "Installed" if sleep_installed else "Needs administrator setup",
        )
        # Enablement only -- never the checked STATE. Refresh runs after
        # every provider Install click, and forcing these back to
        # checked overrode a user's explicit opt-out (the sleep helper
        # opens a sudo Terminal; re-checking it behind their back is the
        # worst possible surprise). The initial checked state is set
        # once, at window build.
        self.set_setup_checkbox("launch", None, enabled=not launch_installed)
        self.set_setup_checkbox("eject_guard", None, enabled=not eject_installed)
        self.set_setup_checkbox("sleep_helper", None, enabled=not sleep_installed)
        eject_uninstall = self.setup_buttons.get("eject_guard_uninstall")
        if eject_uninstall is not None:
            eject_uninstall.setEnabled_(eject_installed)
            eject_uninstall.setHidden_(not eject_installed)

        fda_status = self.setup_fields.get("fda_status")
        fda_button = self.setup_buttons.get("fda_grant")
        if fda_status is not None or fda_button is not None:
            try:
                focus_sync.configured_focus_modes()
                fda_granted = True
            except focus_sync.FocusSyncUnavailableError:
                fda_granted = False
            if fda_status is not None:
                set_field_value(fda_status, "Granted ✓" if fda_granted else "Not granted")
            if fda_button is not None:
                fda_button.setHidden_(fda_granted)

        # The welcome window's provider rows mirror the Settings Agents
        # pane: one contextual action, honest status.
        for provider in HOOK_PROVIDERS:
            status_label = self.setup_fields.get(f"setup_{provider}_status")
            install_button = self.setup_buttons.get(f"setup_{provider}_install")
            if status_label is None and install_button is None:
                continue
            config = provider_spec(provider).detector(None)
            installed = provider_hooks_installed(config)
            if status_label is not None:
                set_field_value(status_label, "Connected ✓" if installed else "")
            if install_button is not None:
                install_button.setHidden_(installed)

    def set_setup_checkbox(self, key: str, checked: bool | None, *, enabled: bool) -> None:
        """checked=None leaves the user's current choice alone (the
        refresh path); a bool sets it (the build path)."""
        button = self.setup_buttons.get(key)
        if button is None:
            return
        if checked is not None:
            set_checkbox_state(button, checked)
        button.setEnabled_(enabled)

    def run_first_launch_setup(self) -> None:
        messages: list[str] = []
        errors: list[str] = []
        opened_sleep_installer = False

        if checkbox_is_on(self.setup_buttons.get("launch")) and not launch_agent_installed():
            try:
                result = install_launch_agent(start=False)
                messages.append("Run at Login installed." if result.changed else "Run at Login already installed.")
            except Exception as exc:
                errors.append(f"Run at Login failed: {exc}")

        if checkbox_is_on(self.setup_buttons.get("eject_guard")) and not sd_eject_guard_installed():
            try:
                result = install_sd_eject_guard(scope="auto", start=True)
                scope_label = "system" if result.scope == "system" else "user"
                messages.append(f"{SD_EJECT_GUARD_DISPLAY_NAME} installed ({scope_label}).")
            except Exception as exc:
                errors.append(f"{SD_EJECT_GUARD_DISPLAY_NAME} failed: {exc}")

        if checkbox_is_on(self.setup_buttons.get("sleep_helper")) and not sleep_helper_installed():
            try:
                path = open_terminal_setup_command(sleep_helper_install_command())
                messages.append(f"Sleep prevention installer opened: {path}")
                opened_sleep_installer = True
            except Exception as exc:
                errors.append(f"Sleep prevention installer failed: {exc}")

        if errors:
            set_field_value(self.setup_fields.get("message"), "  ".join(errors))
            log_status_bar(f"setup errors: {'; '.join(errors)}")
            self.refresh_setup_window()
            return

        if opened_sleep_installer:
            message = "Finish the Terminal setup, then click Set Up again."
            set_field_value(self.setup_fields.get("message"), message)
            log_status_bar(f"setup waiting: {message}")
            self.refresh_setup_window()
            return

        # Agent monitoring is the whole point: pressing the default
        # Set Up button with zero provider hooks installed used to mark
        # setup complete and close the window anyway. Hold the window
        # open ONCE with a plain explanation; a second press respects
        # the user's choice.
        try:
            any_hooks = any(
                provider_hooks_installed(provider_spec(provider).detector(None))
                for provider in HOOK_PROVIDERS
            )
        except Exception:
            any_hooks = True
        if not any_hooks and not getattr(self, "_setup_no_hooks_warned", False):
            self._setup_no_hooks_warned = True
            set_field_value(
                self.setup_fields.get("message"),
                "No agents connected yet -- sessions won't appear until "
                "you install a hook above. Set Up again to finish anyway.",
            )
            self.refresh_setup_window()
            return
        if not messages:
            messages.append("Nothing to install.")
        self.complete_first_launch_setup("  ".join(messages))

    def uninstall_sd_eject_guard_from_setup(self) -> None:
        try:
            results = uninstall_sd_eject_guard(scope="auto")
        except Exception as exc:
            set_field_value(
                self.setup_fields.get("message"),
                f"Could not uninstall {SD_EJECT_GUARD_DISPLAY_NAME}: {exc}",
            )
            self.refresh_setup_window()
            return

        removed = [path for result in results for path in result.removed_paths]
        skipped = [result.skipped for result in results if result.skipped]
        if skipped:
            message = "  ".join(str(item) for item in skipped)
        elif removed:
            message = f"{SD_EJECT_GUARD_DISPLAY_NAME} uninstalled."
        else:
            message = f"{SD_EJECT_GUARD_DISPLAY_NAME} is not installed."
        set_field_value(self.setup_fields.get("message"), message)
        log_status_bar(f"setup: {message}")
        self.refresh_setup_window()

    def complete_first_launch_setup(self, message: str) -> None:
        try:
            self.settings = self.settings.with_setup_screen_completed(True)
            save_settings(self.settings)
        except Exception as exc:
            set_field_value(self.setup_fields.get("message"), f"Could not save setup: {exc}")
            return

        log_status_bar(f"setup complete: {message}")
        set_field_value(self.setup_fields.get("message"), message)
        if self.setup_window is not None:
            self.setup_window.performClose_(None)
        self.refresh_(None)

    def refresh_settings_window(self) -> None:
        if self.settings_window is None:
            return

        current_pane = getattr(self, "current_settings_pane", None)
        now_probe = time.monotonic()
        probes_fresh = (
            now_probe - getattr(self, "_provider_probe_at", 0.0) < 20.0
        )
        if current_pane == "agents" and not probes_fresh:
            self._provider_probe_at = now_probe
            # Detector probes hit the filesystem per provider -- only
            # worth it when the Agents pane is on screen, at most every
            # 20s (install/uninstall actions reset the stamp).
            for provider in HOOK_PROVIDERS:
                config = provider_spec(provider).detector(None)
                set_field_value(self.settings_fields.get(f"{provider}_hook_status"), hook_status_text(config))
                installed = provider_hooks_installed(config)
                install_button = self.settings_fields.get(f"{provider}_hook_install")
                uninstall_button = self.settings_fields.get(f"{provider}_hook_uninstall")
                if install_button is not None:
                    install_button.setHidden_(installed)
                if uninstall_button is not None:
                    uninstall_button.setHidden_(not installed)
        set_field_value(
            self.settings_fields.get("settings_path"),
            f"Settings: {default_settings_path()}",
        )
        set_field_value(
            self.settings_fields.get("debug_log_status"),
            debug_log_status_text(),
        )
        set_checkbox_state(
            self.settings_buttons.get("screen_bar_wraps_menu_bar"),
            self.settings.virtual_status_device_wraps_menu_bar,
        )
        set_checkbox_state(
            self.settings_buttons.get("screen_bar_gauges"),
            self.settings.screen_bar_gauges_enabled,
        )
        set_checkbox_state(
            self.settings_buttons.get("screen_bar_follow_alcove"),
            self.settings.screen_bar_follow_alcove,
        )
        self.refresh_screen_bar_preview()
        closed_lid_policy_popup = self.settings_fields.get("closed_lid_awake_policy_popup")
        if closed_lid_policy_popup is not None:
            select_closed_lid_awake_policy(closed_lid_policy_popup, self.settings.closed_lid_awake_policy)
        set_field_value(
            self.settings_fields.get("closed_lid_grace_field"),
            f"{self.settings.closed_lid_grace_minutes:g}",
        )
        set_checkbox_state(
            self.settings_buttons.get("idle_dim_enabled"),
            self.settings.idle_dim_enabled,
        )
        set_field_value(
            self.settings_fields.get("idle_dim_minutes_field"),
            f"{self.settings.idle_dim_after_minutes:g}",
        )
        set_field_value(
            self.settings_fields.get("idle_dim_fraction_field"),
            f"{round(self.settings.idle_dim_fraction * 100)}",
        )
        set_checkbox_state(
            self.settings_buttons.get("focus_sync_enabled"),
            self.settings.focus_sync_enabled,
        )
        set_checkbox_state(
            self.settings_buttons.get("night_warmth_enabled"),
            self.settings.night_warmth_enabled,
        )
        set_checkbox_state(
            self.settings_buttons.get("completion_notification"),
            self.settings.completion_notification_enabled,
        )
        for device_id, controls in self.device_settings_controls.items():
            self.refresh_device_settings_controls(device_id, controls)
        set_checkbox_state(
            self.settings_buttons.get("codex_transcripts"),
            self.settings.codex_transcripts_enabled,
        )
        set_checkbox_state(
            self.settings_buttons.get("claude_transcripts"),
            self.settings.claude_transcripts_enabled,
        )
        set_checkbox_state(
            self.settings_buttons.get("battery_leds"),
            self.settings.led_display == LED_DISPLAY_BATTERY,
        )
        set_checkbox_state(
            self.settings_buttons.get("battery_power_preview"),
            self.settings.battery_show_on_power_change,
        )
        set_checkbox_state(
            self.settings_buttons.get("low_battery_alert"),
            self.settings.low_battery_alert_enabled,
        )
        set_field_value(
            self.settings_fields.get("low_battery_threshold_field"),
            f"{self.settings.low_battery_threshold_percent:g}",
        )
        set_checkbox_state(
            self.settings_buttons.get("notification_blinks_enabled"),
            self.settings.notification_blinks_enabled,
        )
        set_checkbox_state(
            self.settings_buttons.get("completion_sweep_enabled"),
            self.settings.completion_sweep_enabled,
        )
        for bundle_id in DEFAULT_NOTIFICATION_APP_COLORS:
            set_field_value(
                self.settings_fields.get(f"notification_color:{bundle_id}"),
                self.settings.notification_app_colors.get(bundle_id, ""),
            )
        set_checkbox_state(
            self.settings_buttons.get("calendar_alerts_enabled"),
            self.settings.calendar_alerts_enabled,
        )
        set_checkbox_state(
            self.settings_buttons.get("reminder_alerts_enabled"),
            self.settings.reminder_alerts_enabled,
        )
        set_checkbox_state(
            self.settings_buttons.get("weather_alerts_enabled"),
            self.settings.weather_alerts_enabled,
        )
        set_checkbox_state(
            self.settings_buttons.get("quota_alerts_enabled"),
            self.settings.quota_alerts_enabled,
        )
        set_checkbox_state(
            self.settings_buttons.get("subagent_asks_alert"),
            self.settings.subagent_asks_alert,
        )
        # Signals cards: re-render each from saved state, and sync the
        # escalation controls -- like every other control here, they must
        # reflect changes made outside their own handlers.
        if current_pane == "led_behavior":
            for signal_key in signals_module.DEFAULT_SIGNAL_STYLES:
                self.refresh_signal_card(signal_key)
        bracket_popup = self.settings_fields.get("bracket_style_popup")
        if bracket_popup is not None:
            for index in range(bracket_popup.numberOfItems()):
                item = bracket_popup.itemAtIndex_(index)
                if str(item.representedObject() or "") == self.settings.screen_bar_bracket_style:
                    bracket_popup.selectItem_(item)
                    break
        timer_field = getattr(self, "timer_minutes_field", None)
        if timer_field is not None:
            set_field_value(timer_field, f"{self.settings.timer_expected_minutes:g}")
        tier_popup = self.settings_fields.get("escalation_tier_popup")
        if tier_popup is not None:
            for index in range(tier_popup.numberOfItems()):
                item = tier_popup.itemAtIndex_(index)
                if str(item.representedObject() or "") == self.settings.escalation_tier:
                    tier_popup.selectItem_(item)
                    break
        for field_key, value in (
            ("escalation_ramp_field", self.settings.escalation_ramp_seconds),
            ("escalation_menu_bar_field", self.settings.escalation_menu_bar_seconds),
            ("escalation_final_field", self.settings.escalation_final_seconds),
        ):
            set_field_value(self.settings_fields.get(field_key), f"{value:g}")
        set_field_value(
            self.settings_fields.get("calendar_lead_field"),
            f"{self.settings.calendar_lead_minutes:g}",
        )
        focus_now = self.settings_fields.get("focus_now_label")
        if focus_now is not None:
            focus_now.setStringValue_(self.active_focus_summary())
        set_field_value(
            self.settings_fields.get("weather_latitude_field"),
            ""
            if self.settings.weather_latitude is None
            else f"{self.settings.weather_latitude:g}",
        )
        set_field_value(
            self.settings_fields.get("weather_longitude_field"),
            ""
            if self.settings.weather_longitude is None
            else f"{self.settings.weather_longitude:g}",
        )
        for identifier, fraction in self.settings.focus_dim_rules.items():
            popup = self.settings_fields.get(f"focus_rule_popup:{identifier}")
            if popup is not None:
                select_focus_dim_choice(popup, fraction)
        for provider in HOOK_PROVIDERS:
            popup = self.settings_fields.get(f"{provider}_session_opener")
            if popup is not None:
                select_popup_action(
                    popup,
                    self.settings.session_open_action(provider)
                    or default_provider_open_action(provider),
                )
        self._refresh_lid_thumb_selection()
        closed = self.settings.lid_closed_animation
        opened = self.settings.lid_open_animation
        set_text_control_value(
            self.settings_fields.get("closed_animation_program"),
            closed.program,
        )
        set_text_control_value(
            self.settings_fields.get("closed_animation_duration"),
            f"{closed.duration_seconds:g}",
        )
        set_text_control_value(
            self.settings_fields.get("open_animation_program"),
            opened.program,
        )
        set_text_control_value(
            self.settings_fields.get("open_animation_duration"),
            f"{opened.duration_seconds:g}",
        )

    def refresh_device_settings_controls(self, device_id: str, controls: dict[str, object]) -> None:
        """Keeps one device's Brightness/Auto-Brightness/Color Calibration
        block (in the Settings window) in sync with whatever the menu bar
        icon's own device submenu last set -- the same settings, two
        places to change them, so either one changing must be reflected
        in the other."""
        brightness = self.settings.brightness_for_device(device_id)
        slider = controls.get("brightness_slider")
        if slider is not None:
            slider.setDoubleValue_(float(normalize_brightness(brightness)))
        set_field_value(controls.get("brightness_label"), f"{brightness_percent(brightness)}%")
        self.set_brightness_preview_dots(controls.get("brightness_dots"), brightness)
        if device_id == VIRTUAL_DEVICE_ID:
            self.refresh_screen_bar_preview()
        set_checkbox_state(
            controls.get("auto_brightness_checkbox"),
            self.settings.auto_brightness_enabled_for_device(device_id),
        )
        set_checkbox_state(
            controls.get("auto_brightness_row_checkbox"),
            self.settings.auto_brightness_enabled_for_device(device_id),
        )
        red, green, blue = self.settings.channel_gains_for_device(device_id)
        auto_enabled = self.settings.auto_brightness_enabled_for_device(device_id)
        set_field_value(
            controls.get("calibration_label"),
            calibration_summary_text(auto_enabled, red, green, blue),
        )
        for key, gain in (("red_slider", red), ("green_slider", green), ("blue_slider", blue)):
            slider = controls.get(key)
            if slider is not None:
                slider.setDoubleValue_(gain * 100.0)
        # The Display and Blend popups must reflect changes made from
        # the menu-bar device submenu, like every other control here.
        display_popup = controls.get("display_popup")
        if display_popup is not None:
            wanted = self.settings.display_for_device(device_id)
            for index in range(display_popup.numberOfItems()):
                item = display_popup.itemAtIndex_(index)
                if str(item.representedObject() or "") == wanted:
                    display_popup.selectItem_(item)
                    break
        blend_popup = controls.get("blend_popup")
        if blend_popup is not None:
            wanted = self.settings.device_blend_mode(device_id) or ""
            for index in range(blend_popup.numberOfItems()):
                item = blend_popup.itemAtIndex_(index)
                if str(item.representedObject() or "") == wanted:
                    blend_popup.selectItem_(item)
                    break
        pin_popup = controls.get("pin_popup")
        if pin_popup is not None:
            wanted_pin = self.settings.device_provider_pin(device_id) or ""
            for index in range(pin_popup.numberOfItems()):
                item = pin_popup.itemAtIndex_(index)
                if str(item.representedObject() or "") == wanted_pin:
                    pin_popup.selectItem_(item)
                    break
        policy_popup = controls.get("signal_policy_popup")
        if policy_popup is not None:
            wanted_policy = self.settings.device_signal_policy(device_id) or ""
            for index in range(policy_popup.numberOfItems()):
                item = policy_popup.itemAtIndex_(index)
                if str(item.representedObject() or "") == wanted_policy:
                    policy_popup.selectItem_(item)
                    break

    def set_brightness_preview_dots(self, dots, brightness) -> None:
        """A plain white dot strip, one per LED, showing at a glance how
        bright the device will actually be at this value -- the slider
        alone is a control, not a preview of the effect it has."""
        if not dots:
            return
        value = int(round(normalize_brightness(brightness)))
        for dot in dots:
            set_preview_dot_rgb(dot, value, value, value)

    def refresh_screen_bar_preview(self) -> None:
        """Keeps the Colors & Screen Bar pane's live miniature in sync
        with brightness, Alcove Compatibility, and "extend glow along the
        menu bar" -- a fixed, representative notch/wing geometry rather
        than the real screen's own (Settings isn't necessarily open on
        the Mac's built-in display, and a fixed size keeps the preview
        from jumping around as the real window gets dragged between
        screens)."""
        preview = self.settings_fields.get("screen_bar_preview_view")
        container = self.settings_fields.get("screen_bar_preview_container")
        if preview is None or container is None:
            return
        preview.setPreviewWhiteBrightness_(self.settings.brightness_for_device(VIRTUAL_DEVICE_ID))
        preview.setMinGlow_(float(self.settings.screen_bar_min_glow))
        # The preview always shows the full wrap look -- Alcove handling
        # is automatic on the real bar and needs no demonstration here.
        preview.setCompactMode_(False)
        wing_width = SCREEN_BAR_PREVIEW_WING_WIDTH if self.settings.virtual_status_device_wraps_menu_bar else 0.0
        total_width = SCREEN_BAR_PREVIEW_NOTCH_WIDTH + 2.0 * wing_width
        # container's own fixed width is known at construction time (see
        # _build_colors_screen_bar_pane) -- reading it back via
        # container.frame() instead is timing-dependent (this can run
        # before the window's first real Auto Layout pass, when the
        # frame is still its construction-time default) and previously
        # centered the preview around a bogus width, clipping the left
        # wing off entirely.
        container_width = SCREEN_BAR_PREVIEW_NOTCH_WIDTH + 2.0 * SCREEN_BAR_PREVIEW_WING_WIDTH
        preview.setFrame_(
            (((container_width - total_width) / 2.0, 0.0), (total_width, SCREEN_BAR_PREVIEW_HEIGHT))
        )
        preview.setNotchWidth_(SCREEN_BAR_PREVIEW_NOTCH_WIDTH)

    def set_settings_message(self, message: str) -> None:
        label = self.settings_fields.get("message")
        set_field_value(label, message)
        # First run has only the Welcome window (settings_fields is
        # empty until Settings first opens): mirror there whenever it's
        # up, so hook install progress -- and especially failures --
        # are visible somewhere other than the log.
        setup_window = getattr(self, "setup_window", None)
        if setup_window is not None and setup_window.isVisible():
            set_field_value(self.setup_fields.get("message"), message)
        if message:
            log_status_bar(f"settings: {message}")
        # Toast semantics: the confirmation fades away after a beat
        # instead of sitting as a stale label forever. 102 call sites,
        # zero of them changed.
        if label is not None:
            label.setAlphaValue_(1.0)
            timer = getattr(self, "_settings_message_timer", None)
            if timer is not None:
                timer.invalidate()
            if message:
                self._settings_message_timer = (
                    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                        3.5, self, "dismissSettingsMessage:", None, False
                    )
                )

    @objc.IBAction
    def dismissSettingsMessage_(self, _timer):
        self._settings_message_timer = None
        label = self.settings_fields.get("message")
        if label is None:
            return
        from AppKit import NSAnimationContext

        def _animate(context):
            context.setDuration_(0.6)
            label.animator().setAlphaValue_(0.0)

        NSAnimationContext.runAnimationGroup_completionHandler_(_animate, None)

    @objc.IBAction
    def exportDebugCsv_(self, _sender):
        self.export_debug_log("csv")

    @objc.IBAction
    def exportDebugHtml_(self, _sender):
        self.export_debug_log("html")

    def export_debug_log(self, format_name: str) -> None:
        path = choose_debug_export_path(format_name)
        if path is None:
            return
        try:
            if format_name == "csv":
                count = export_status_audit_csv(path)
            else:
                count = export_status_audit_html(path)
        except Exception as exc:
            self.set_settings_message(f"Debug export failed: {exc}")
            return
        self.set_settings_message(f"Exported {count} debug events to {path}.")

    def update_hooks(self, provider: str, *, install: bool) -> None:
        """Installer runs on a worker thread: the Codex trust refresh
        spawns `codex app-server` and can wait ~8s per round-trip --
        running it inline beachballed the whole app for up to ~16s."""
        if getattr(self, "hooks_update_in_flight", False):
            return
        self.hooks_update_in_flight = True
        self.set_settings_message(
            f"{'Installing' if install else 'Removing'} {provider.title()} hooks…"
        )

        def _work():
            try:
                result = (
                    install_provider_hooks(provider)
                    if install
                    else uninstall_provider_hooks(provider)
                )
                payload = {"ok": True, "changed": bool(result.changed)}
            except Exception as exc:
                payload = {"ok": False, "error": str(exc)}
            payload["provider"] = provider
            payload["install"] = install
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "hooksUpdated:", payload, False
            )

        threading.Thread(target=_work, daemon=True).start()

    @objc.IBAction
    def hooksUpdated_(self, payload):
        self.hooks_update_in_flight = False
        provider = str(payload.get("provider") or "")
        install = bool(payload.get("install"))
        if not payload.get("ok"):
            self.set_settings_message(
                f"{provider.title()} hooks failed: {payload.get('error')}"
            )
            self.refresh_settings_window()
            # The Welcome window's Install button must not just sit
            # there after a failure -- refresh its row status too.
            self.refresh_setup_window()
            return
        action = "installed" if install else "removed"
        if not payload.get("changed"):
            action = "already installed" if install else "already removed"
        self.set_settings_message(f"{provider.title()} hooks {action}.")
        self.reload_monitor()
        self.refresh_settings_window()
        self.refresh_setup_window()
        self.refresh_(None)

    def set_transcript_monitoring(self, provider: str, enabled: bool) -> None:
        try:
            self.settings = self.settings.with_transcript_provider(provider, enabled)
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save settings: {exc}")
            self.settings = load_settings()
            self.refresh_settings_window()
            return

        self.reload_monitor()
        self.set_settings_message(
            f"{provider.title()} transcript CLI fallback {'enabled' if enabled else 'disabled'}."
        )
        self.refresh_settings_window()
        self.refresh_(None)

    def set_battery_led_display(self, enabled: bool) -> None:
        try:
            display = LED_DISPLAY_BATTERY if enabled else LED_DISPLAY_AGENT
            self.settings = self.settings.with_led_display(display)
            # Rendering resolves display PER DEVICE, and every connected
            # device is auto-remembered with a per-device entry that
            # shadows the global -- without this loop the switch worked
            # at most once, before the first device was remembered.
            # Only devices in the agent/battery pair follow the toggle:
            # a device the user deliberately set to Studio, Timer, or
            # Quota Runway keeps its choice.
            for device in self.settings.devices:
                if device.device_id == VIRTUAL_DEVICE_ID:
                    continue
                if device.led_display not in (LED_DISPLAY_AGENT, LED_DISPLAY_BATTERY):
                    continue
                self.settings = self.settings.with_device_display(
                    device.device_id, display
                )
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save settings: {exc}")
            self.settings = load_settings()
            self.refresh_settings_window()
            return

        self.reset_led_controllers_for_display_change()
        self.set_settings_message(f"LED display set to {self.settings.led_display}.")
        self.refresh_settings_window()
        self.refresh_(None)

    def _mutate_device_setting(
        self,
        device_id: str | None,
        mutate,
        describe,
        error_label: str,
        *,
        resync: str = "snapshot",
    ) -> None:
        """The shared skeleton of every per-device settings mutation
        (five near-identical ~35-line methods had already started to
        diverge): find the device for its display name/path, apply the
        mutator with rollback on failure, reset that device's LED
        controllers, toast, refresh the pane, re-sync. ``mutate(device)
        -> AgentMonitorSettings``; ``describe(device_label) -> str``
        runs AFTER the mutation so it may read the new settings.
        resync: "snapshot" replays the last snapshot; "refresh" runs a
        full refresh_ pass; "calibration" re-lights the mid-flight
        calibration test color instead of resuming live status."""
        if not device_id:
            return
        device = next(
            (
                entry
                for entry in self.status_bar_devices(remember=False)
                if entry.device_id == str(device_id)
            ),
            None,
        )
        try:
            self.settings = mutate(device)
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save {error_label}: {exc}")
            self.settings = load_settings()
            return
        self.reset_led_controllers_for_device(str(device_id))
        self.set_settings_message(describe(device.name if device else str(device_id)))
        self.refresh_settings_window()
        if resync == "refresh":
            self.refresh_(None)
            return
        if (
            resync == "calibration"
            and self.calibration_test is not None
            and self.calibration_test[0] == str(device_id)
        ):
            # Mid-calibration: re-light the test color through the new
            # gains instead of resuming live status under the user's
            # hands -- live status returns when the popover closes.
            self._send_calibration_test()
            return
        if self.last_snapshot is not None:
            self.sync_leds(
                self.last_snapshot.aggregate.mode,
                self.last_battery_snapshot,
                self.active_led_display_kind(self.last_battery_snapshot),
            )

    def set_device_display(self, device_id: str | None, display: str) -> None:
        label = {
            LED_DISPLAY_AGENT: "Agent Status",
            LED_DISPLAY_BATTERY: "Battery Level",
            LED_DISPLAY_TIMER: "Working Timer",
            LED_DISPLAY_STUDIO: "Studio Program",
            LED_DISPLAY_QUOTA_RUNWAY: "Quota Runway",
        }.get(display, display)
        self._mutate_device_setting(
            device_id,
            lambda device: self.settings.with_device_display(
                str(device_id),
                display,
                name=device.name if device else None,
                path=str(device.root) if device else None,
            ),
            lambda name: f"{name}: {label}.",
            "device display",
            resync="refresh",
        )

    def set_device_brightness(self, device_id: str | None, brightness: float) -> None:
        value = normalize_brightness(brightness)
        self._mutate_device_setting(
            device_id,
            lambda device: self.settings.with_device_brightness(
                str(device_id),
                value,
                name=device.name if device else None,
                path=str(device.root) if device else None,
            ),
            lambda name: f"{name}: brightness {brightness_percent(value)}%.",
            "brightness",
        )

    def set_device_auto_brightness(self, device_id: str | None, enabled: bool) -> None:
        self._mutate_device_setting(
            device_id,
            lambda device: self.settings.with_device_auto_brightness(
                str(device_id),
                enabled,
                name=device.name if device else None,
                path=str(device.root) if device else None,
            ),
            lambda name: f"{name}: auto-brightness {'on' if enabled else 'off'}.",
            "auto-brightness",
        )

    def set_device_channel_gain(self, device_id: str | None, channel: str, value: float) -> None:
        def describe(name: str) -> str:
            red, green, blue = self.settings.channel_gains_for_device(str(device_id))
            return (
                f"{name}: calibration R{round(red * 100)}% "
                f"G{round(green * 100)}% B{round(blue * 100)}%."
            )

        self._mutate_device_setting(
            device_id,
            lambda device: self.settings.with_device_channel_gain(
                str(device_id),
                channel,
                value,
                name=device.name if device else None,
                path=str(device.root) if device else None,
            ),
            describe,
            "color calibration",
            resync="calibration",
        )

    def set_device_channel_gains_reset(self, device_id: str | None) -> None:
        self._mutate_device_setting(
            device_id,
            lambda _device: self.settings.with_device_channel_gains_reset(str(device_id)),
            lambda name: f"{name}: calibration reset.",
            "color calibration",
        )

    def set_virtual_status_device(self, enabled: bool) -> None:
        if not SCREEN_BAR_FEATURE_ENABLED:
            try:
                self.settings = self.settings.with_virtual_status_device(False)
                save_settings(self.settings)
            except Exception as exc:
                self.set_settings_message(f"Could not disable Screen Bar: {exc}")
                return
            self.virtual_status_device.hide()
            self.set_settings_message("Screen Bar is disabled for now.")
            self.refresh_(None)
            return

        try:
            self.settings = self.settings.with_virtual_status_device(enabled)
            if enabled:
                self.settings = self.settings.with_remembered_device(
                    device_id=VIRTUAL_DEVICE_ID,
                    name=VIRTUAL_DEVICE_NAME,
                    path=VIRTUAL_DEVICE_ID,
                )
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save Screen Bar: {exc}")
            return
        if enabled:
            self.virtual_status_device.show()
        else:
            self.virtual_status_device.hide()
        self.refresh_(None)

    def set_closed_lid_awake_policy(self, policy: str | None) -> None:
        if policy not in CLOSED_LID_AWAKE_CHOICES:
            return
        try:
            self.settings = self.settings.with_closed_lid_awake_policy(str(policy))
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save lid sleep setting: {exc}")
            self.settings = load_settings()
            return

        self.set_settings_message(
            f"Closed-lid awake: {CLOSED_LID_AWAKE_LABELS[self.settings.closed_lid_awake_policy]}."
        )
        self.sync_closed_lid_awake()
        self.refresh_settings_window()
        self.refresh_(None)

    def lid_animation_from_fields(self, kind: str) -> LedAnimationSetting | None:
        program_field = self.settings_fields.get(f"{kind}_animation_program")
        duration_field = self.settings_fields.get(f"{kind}_animation_duration")
        current = self.settings.lid_animation(kind)
        program = text_control_value(program_field) or current.program
        duration_text = text_control_value(duration_field)
        try:
            duration = float(duration_text) if duration_text else current.duration_seconds
        except ValueError:
            self.set_settings_message(f"{LID_ANIMATION_LABELS[kind]} duration is not a number.")
            return None

        animation = LedAnimationSetting(
            program=normalize_led_text(program),
            duration_seconds=normalize_animation_duration(duration),
        )
        try:
            validate_lid_animation(animation)
        except DeviceWriteError as exc:
            self.set_settings_message(f"{LID_ANIMATION_LABELS[kind]} animation invalid: {exc}")
            return None
        return animation

    def save_lid_animations_from_fields(self) -> None:
        closed = self.lid_animation_from_fields(LID_ANIMATION_CLOSED)
        if closed is None:
            return
        opened = self.lid_animation_from_fields(LID_ANIMATION_OPEN)
        if opened is None:
            return
        try:
            self.settings = self.settings.with_lid_animation(
                LID_ANIMATION_CLOSED,
                program=closed.program,
                duration_seconds=closed.duration_seconds,
            )
            self.settings = self.settings.with_lid_animation(
                LID_ANIMATION_OPEN,
                program=opened.program,
                duration_seconds=opened.duration_seconds,
            )
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save lid animations: {exc}")
            self.settings = load_settings()
            self.refresh_settings_window()
            return

        self.set_settings_message("Lid animations saved.")
        self.refresh_settings_window()

    def reset_lid_animation(self, kind: str) -> None:
        animation = default_lid_animation(kind)
        try:
            self.settings = self.settings.with_lid_animation(
                kind,
                program=animation.program,
                duration_seconds=animation.duration_seconds,
            )
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not reset {LID_ANIMATION_LABELS[kind]}: {exc}")
            self.settings = load_settings()
            self.refresh_settings_window()
            return

        self.set_settings_message(f"{LID_ANIMATION_LABELS[kind]} reset.")
        self.refresh_settings_window()

    def remove_remembered_device(self, device_id: str | None) -> None:
        if not device_id:
            return
        device = next(
            (
                entry
                for entry in self.status_bar_devices(remember=False)
                if entry.device_id == str(device_id)
            ),
            None,
        )
        try:
            self.settings = self.settings.without_device(str(device_id))
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not remove device: {exc}")
            self.settings = load_settings()
            return

        self.reset_led_controllers_for_device(str(device_id))
        self.set_settings_message(f"{device.name if device else device_id}: removed.")
        self.refresh_settings_window()
        self.refresh_(None)

    def open_session(self, status: AgentStatus | object, action: str | None, *, remember: bool) -> None:
        if not isinstance(status, AgentStatus):
            return
        provider = status.provider.lower()
        requested_action = (
            action
            or self.settings.session_open_action(provider, status.origin)
            or default_session_open_action(status)
        )
        target = session_open_target(status, requested_action)
        if target is None:
            requested_action = default_session_open_action(status)
            target = session_open_target(status, requested_action)
        if target is None:
            self.set_settings_message(f"No open action available for {status.display_name}.")
            return

        kind, value = target
        if kind == "url":
            open_url(value)
        elif kind == "terminal":
            open_terminal_command(value)
        else:
            self.set_settings_message(f"Unknown open action for {status.display_name}.")
            return

        if remember:
            try:
                self.settings = self.settings.with_session_open_action(
                    provider,
                    requested_action,
                    status.origin,
                )
                save_settings(self.settings)
            except Exception as exc:
                self.set_settings_message(f"Could not save open preference: {exc}")
                self.settings = load_settings()

    def close_status_menu(self) -> None:
        try:
            menu = self.status_item.menu()
            if menu is not None:
                menu.cancelTracking()
        except Exception:
            pass

    def set_battery_power_preview(self, enabled: bool) -> None:
        try:
            self.settings = self.settings.with_battery_power_change_preview(enabled=enabled)
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save settings: {exc}")
            self.settings = load_settings()
            self.refresh_settings_window()
            return

        self.set_settings_message(
            f"Battery power-change preview {'enabled' if enabled else 'disabled'}."
        )
        self.refresh_settings_window()
        self.refresh_(None)

    def read_battery_snapshot(self) -> BatterySnapshot | None:
        cached = getattr(self, "_battery_snapshot_cache", None)
        now = time.monotonic()
        if cached is not None and now - cached[0] < BATTERY_SNAPSHOT_CACHE_SECONDS:
            return cached[1]
        snapshot = self._read_battery_snapshot_uncached()
        self._battery_snapshot_cache = (now, snapshot)
        return snapshot

    def _read_battery_snapshot_uncached(self) -> BatterySnapshot | None:
        try:
            snapshot = read_battery_snapshot(
                full_charge_watts=self.settings.battery_full_charge_watts,
            )
        except Exception as exc:
            error = str(exc)
            if error != self.last_battery_error:
                log_status_bar(f"battery error: {error}")
            self.last_battery_error = error
            return None

        self.last_battery_error = None
        self.last_battery_snapshot = snapshot
        self.update_battery_power_preview(snapshot)
        return snapshot

    def update_battery_power_preview(self, snapshot: BatterySnapshot) -> None:
        plugged = snapshot.is_plugged
        if self.last_power_connected is not None and self.last_power_connected != plugged:
            if self.settings.battery_show_on_power_change:
                self.battery_preview_until = (
                    time.monotonic()
                    + self.settings.battery_power_change_preview_seconds
                )
                log_status_bar(
                    f"battery preview power={'plugged' if plugged else 'unplugged'}"
                )
        self.last_power_connected = plugged

    def active_led_display_kind(self, snapshot: BatterySnapshot | None) -> str:
        if self.settings.led_display == LED_DISPLAY_BATTERY:
            return LED_DISPLAY_BATTERY
        if snapshot is not None and time.monotonic() < self.battery_preview_until:
            return LED_DISPLAY_BATTERY
        return LED_DISPLAY_AGENT

    def reset_led_controllers_for_display_change(self) -> None:
        self.led_controller.reset()
        self.battery_led_controller.reset()
        for controller in self.agent_led_controllers_by_device.values():
            controller.reset()
        for controller in self.battery_led_controllers_by_device.values():
            controller.reset()
        self.last_led_display_kind_by_device.clear()
        self.last_led_error = None

    def reset_led_controllers_for_device(self, device_id: str) -> None:
        agent_controller = self.agent_led_controllers_by_device.get(device_id)
        if agent_controller is not None:
            agent_controller.reset()
        battery_controller = self.battery_led_controllers_by_device.get(device_id)
        if battery_controller is not None:
            battery_controller.reset()
        self.last_led_display_kind_by_device.pop(device_id, None)
        self.device_errors.pop(device_id, None)
        self.last_led_error = None

    def agent_controller_for_device(self, device: StatusBarDevice) -> AgentLedController:
        controller = self.agent_led_controllers_by_device.get(device.device_id)
        if controller is None:
            controller = AgentLedController(device_path=device.target)
            self.agent_led_controllers_by_device[device.device_id] = controller
        controller.device_path = device.target
        controller.brightness = self.effective_brightness_for_device(device)
        controller.channel_gains = device.channel_gains
        controller.resting_glow = device.resting_glow
        return controller

    def battery_controller_for_device(self, device: StatusBarDevice) -> BatteryLedController:
        controller = self.battery_led_controllers_by_device.get(device.device_id)
        if controller is None:
            controller = BatteryLedController(device_path=device.target)
            self.battery_led_controllers_by_device[device.device_id] = controller
        controller.device_path = device.target
        controller.brightness = self.effective_brightness_for_device(device)
        controller.channel_gains = device.channel_gains
        controller.resting_glow = device.resting_glow
        return controller

    def effective_signal_brightness_for_device(self, device: StatusBarDevice) -> int:
        """Signal moments cut through ambient dimming: the user's
        configured brightness, boosted by escalation, dimmed by nothing
        -- except an explicit per-Focus "Turn off" rule, which wins."""
        if self.settings.focus_sync_enabled and self.focus_sync_scale_factor() <= 0.0:
            return 0
        boost = (
            signals_module.ESCALATION_RAMP_BRIGHTNESS_BOOST
            if self.current_escalation_stage() >= 1
            else 1.0
        )
        return normalize_brightness(max(device.brightness * boost, 1))

    def effective_brightness_for_device(self, device: StatusBarDevice) -> int:
        """The brightness to actually use for this write right now: the
        manually configured value, unless auto-brightness is on for this
        device and a screen-brightness reading is currently available (see
        display_brightness.py) -- an undocumented technique, so any failure
        falls back to the manual value rather than blocking the LED sync --
        further scaled down if idle-timeout dimming and/or Focus sync have
        kicked in (both multiply in, so if both happen to apply at once
        the effect compounds rather than one silently overriding the
        other).
        """
        if not device.auto_brightness_enabled:
            base = device.brightness
        else:
            try:
                base = display_brightness.auto_led_brightness()
            except display_brightness.DisplayBrightnessUnavailableError:
                base = device.brightness
        # Escalation stage >= 1: an ignored ask pushes THROUGH dimming --
        # a ramp that idle-dim could cancel out wouldn't be a ramp.
        boost = (
            signals_module.ESCALATION_RAMP_BRIGHTNESS_BOOST
            if self.current_escalation_stage() >= 1
            else 1.0
        )
        scaled = base * self.idle_dim_scale_factor() * self.focus_sync_scale_factor() * boost
        if boost > 1.0:
            # An escalation ramp must survive the darkest stack: with
            # idle-dim AND Focus both at their floors, multiplication
            # alone rounds to 0 -- an invisible "ramp".
            scaled = max(scaled, float(MIN_ESCALATION_VISIBLE_BRIGHTNESS))
        if device.device_id == VIRTUAL_DEVICE_ID and scaled > 0.0:
            # The Screen Bar gets a visibility FLOOR: shared Focus dim
            # stacked with nighttime auto-brightness multiplied it down
            # to a whisper and the bar read as "gone" ("I don't see the
            # screen bar anymore"). A rule that resolves to exactly 0
            # (School -> Turn off) still turns it fully off -- the floor
            # only guards against accidental invisibility, never intent.
            scaled = max(scaled, 255.0 * float(self.settings.screen_bar_min_glow))
        return normalize_brightness(scaled)

    def idle_dim_scale_factor(self) -> float:
        """1.0 normally; idle_dim_fraction once idle_dim_after_minutes of
        continuous Idle has elapsed -- a long-idle Mac shouldn't keep a
        bright light going on the desk. Uses set_status()'s own idle-since
        tracking rather than re-deriving "is it idle" here, so this stays
        correct regardless of which display kind (agent/battery) is
        actually showing."""
        if not self.settings.idle_dim_enabled or self.idle_since_monotonic is None:
            return 1.0
        threshold_seconds = self.settings.idle_dim_after_minutes * 60.0
        if time.monotonic() - self.idle_since_monotonic < threshold_seconds:
            return 1.0
        return self.settings.idle_dim_fraction

    def active_focus_ids_cached(self) -> list[str]:
        """1s-TTL cache over the Focus assertions read -- the LED path
        called the uncached read ~4.7x/second on the main thread when
        Focus sync was on. Unavailability (no FDA) is cached too, so an
        ungranted machine stops paying the OSError path every call."""
        cached = getattr(self, "_focus_ids_cache", None)
        now = time.monotonic()
        if cached is not None and now - cached[0] < 1.0:
            return cached[1]
        try:
            active = focus_sync.active_focus_mode_identifiers()
        except focus_sync.FocusSyncUnavailableError:
            active = []
        self._focus_ids_cache = (now, active)
        return active

    def night_warmth_active(self, hour: int | None = None) -> bool:
        """Story #8: between 19:00 and 07:00 local, warm every device --
        red untouched, green and blue eased down, the amber shift
        screens learned from Night Shift. Pure wall-clock schedule."""
        if not self.settings.night_warmth_enabled:
            return False
        if hour is None:
            hour = datetime.now().hour
        return hour >= 19 or hour < 7

    def apply_night_warmth(
        self, gains: tuple[float, float, float], hour: int | None = None
    ) -> tuple[float, float, float]:
        """Compose (never replace) the user's calibration gains with the
        warmth curve, so a calibrated device stays calibrated -- just
        warmer. Safe against persistence: with_remembered_device only
        stores id/name/path, never gains, so warmth cannot leak into
        the settings file."""
        if not self.night_warmth_active(hour=hour):
            return gains
        warm = NIGHT_WARMTH_GAINS
        return tuple(
            float(gain) * warm[index] for index, gain in enumerate(gains[:3])
        )

    def focus_sync_scale_factor(self) -> float:
        """1.0 normally; idle_dim_fraction (the same "how much to dim"
        amount idle-timeout dimming uses -- one shared dial rather than a
        second near-duplicate setting) while a macOS Focus is active and
        focus_sync_enabled is on. See focus_sync.py for what this depends
        on and why it can be legitimately unavailable (no Full Disk
        Access granted) -- that's treated as "not active" here, same as
        any other fail-safe in this file, never as an error that blocks
        the LED sync.
        """
        if not self.settings.focus_sync_enabled:
            return 1.0
        active = self.active_focus_ids_cached()
        if not active:
            return 1.0
        # With several Focuses somehow active at once, the strictest rule
        # wins -- a Sleep "off" rule must never be overridden by a Work
        # "dim a little" one.
        return min(self.settings.focus_dim_fraction(identifier) for identifier in active)

    def discover_device_candidates(self):
        """discover_devices() with a short TTL. Keyed by the function
        object itself so tests that patch sidepulse.status_bar.
        discover_devices never see a stale cache from a previous patch."""
        discover = discover_devices
        cached = getattr(self, "_device_discovery_cache", None)
        now = time.monotonic()
        if cached is not None and cached[0] is discover:
            if now - cached[1] >= DEVICE_DISCOVERY_CACHE_SECONDS and not getattr(
                self, "_discovery_revalidating", False
            ):
                # Stale-while-revalidate: the /Volumes stat can BLOCK for
                # seconds on a busy SD card, so after the first call the
                # main thread only ever reads the cache; a worker keeps
                # it fresh.
                self._discovery_revalidating = True

                def _revalidate():
                    try:
                        fresh = discover()
                    except Exception as exc:
                        log_status_bar(f"device discovery error: {exc}")
                        fresh = cached[2]
                    self._device_discovery_cache = (
                        discover,
                        time.monotonic(),
                        fresh,
                    )
                    self._discovery_revalidating = False

                threading.Thread(target=_revalidate, daemon=True).start()
            return cached[2]
        try:
            candidates = discover()
        except Exception as exc:
            log_status_bar(f"device discovery error: {exc}")
            candidates = []
        self._device_discovery_cache = (discover, now, candidates)
        return candidates

    def status_bar_devices(self, *, remember: bool = True) -> list[StatusBarDevice]:
        entries_by_id: dict[str, StatusBarDevice] = {}
        candidates = self.discover_device_candidates()

        for candidate in candidates:
            device_id = device_id_for_root(candidate.root)
            name = device_display_name(candidate.root.name)
            entries_by_id[device_id] = StatusBarDevice(
                device_id=device_id,
                name=name,
                root=candidate.root,
                target=candidate.target,
                connected=True,
                display=self.settings.display_for_device(device_id),
                brightness=self.settings.brightness_for_device(device_id),
                auto_brightness_enabled=self.settings.auto_brightness_enabled_for_device(device_id),
                channel_gains=self.apply_night_warmth(
                    self.settings.channel_gains_for_device(device_id)
                ),
                resting_glow=self.settings.resting_glow_for_device(device_id),
                signal_policy=self.settings.device_signal_policy(device_id),
                reason=candidate.reason,
            )

        for device in self.settings.devices:
            if device.device_id == VIRTUAL_DEVICE_ID:
                if (
                    SCREEN_BAR_FEATURE_ENABLED
                    and self.settings.virtual_status_device_enabled
                ):
                    entries_by_id[device.device_id] = StatusBarDevice(
                        device_id=device.device_id,
                        name=VIRTUAL_DEVICE_NAME,
                        root=Path(VIRTUAL_DEVICE_ID),
                        target=Path(VIRTUAL_DEVICE_ID),
                        connected=True,
                        display=device.led_display,
                        brightness=device.brightness,
                        auto_brightness_enabled=device.auto_brightness_enabled,
                        channel_gains=self.apply_night_warmth(device.channel_gains()),
                        resting_glow=device.resting_glow,
                        signal_policy=device.signal_policy,
                        reason="on-screen device",
                    )
                continue
            if device.device_id in entries_by_id:
                continue
            root = Path(device.path).expanduser()
            entries_by_id[device.device_id] = StatusBarDevice(
                device_id=device.device_id,
                name=device.name,
                root=root,
                target=target_from_device_path(root, DEFAULT_FILE_NAME),
                connected=False,
                display=device.led_display,
                brightness=device.brightness,
                auto_brightness_enabled=device.auto_brightness_enabled,
                channel_gains=self.apply_night_warmth(device.channel_gains()),
                resting_glow=device.resting_glow,
                signal_policy=device.signal_policy,
                reason="previously connected",
            )

        entries = sorted(
            entries_by_id.values(),
            key=lambda item: (not item.connected, normalized_device_name(item.name), str(item.root)),
        )
        entries = disambiguate_device_names(entries)
        if remember:
            self.remember_connected_devices(entries)
        return entries

    def observe_connected_devices(self) -> bool:
        devices = self.status_bar_devices()
        signature = device_connection_signature(devices)
        previous = self.last_connected_device_signature
        self.last_connected_device_signature = signature
        if previous is None or previous == signature:
            return False

        previous_by_id = {entry[0]: entry for entry in previous}
        current_by_id = {entry[0]: entry for entry in signature}
        reset_ids = {
            device_id
            for device_id, entry in current_by_id.items()
            if previous_by_id.get(device_id) != entry
        }
        reset_ids.update(set(previous_by_id) - set(current_by_id))
        for device_id in sorted(reset_ids):
            self.reset_led_controllers_for_device(device_id)

        connected_names = [
            device.name
            for device in devices
            if device.connected and device.device_id in reset_ids
        ]
        disconnected_ids = sorted(set(previous_by_id) - set(current_by_id))
        if connected_names:
            log_status_bar(f"device connected: {', '.join(connected_names)}")
        if disconnected_ids:
            log_status_bar(f"device disconnected: {', '.join(disconnected_ids)}")
        return True

    def remember_connected_devices(self, devices: list[StatusBarDevice]) -> None:
        """Optimistic compare-and-set rather than a blind read-modify-write:
        this runs on the background LED-sync worker thread (via
        status_bar_devices(remember=True)) and can otherwise race against a
        settings change made from a UI action on the main thread in the
        same narrow window -- whichever write landed last would silently
        win, discarding the other. AgentMonitorSettings is a frozen
        dataclass, so every with_*() call produces a new object; comparing
        `self.settings is before` (an atomic, GIL-protected check) reliably
        detects whether anything else reassigned self.settings while this
        was computing its own update, and retries against the fresh value
        instead of clobbering it.
        """
        for _ in range(5):
            before = self.settings
            updated = before
            for device in devices:
                if not device.connected:
                    continue
                updated = updated.with_remembered_device(
                    device_id=device.device_id,
                    name=device.name,
                    path=str(device.root),
                )
            if updated == before:
                return
            if self.settings is not before:
                continue  # something else changed settings mid-computation -- retry fresh
            self.settings = updated
            try:
                save_settings(self.settings)
            except Exception as exc:
                log_status_bar(f"device remember error: {exc}")
            return
        log_status_bar("device remember: skipped after repeated concurrent settings changes")

    def low_power_active(self, battery_snapshot: BatterySnapshot | None) -> bool:
        """True while the battery is below the low-power threshold and
        unplugged (and the reminder is enabled) -- the one condition that
        outranks every other display, on every device at once."""
        if not self.settings.low_battery_alert_enabled or battery_snapshot is None:
            return False
        if battery_snapshot.is_plugged or not battery_snapshot.battery_present:
            return False
        return battery_snapshot.percent <= self.settings.low_battery_threshold_percent

    def active_led_display_kind_for_device(
        self,
        device: StatusBarDevice,
        battery_snapshot: BatterySnapshot | None,
    ) -> str:
        # The Signal Engine's arbiter: one fixed precedence order, first
        # active claim wins (see the spec's precedence table). Agent is
        # the always-active default.
        now = time.monotonic()
        claims = (
            # A just-clicked Test button outranks everything briefly --
            # the user explicitly asked to SEE this signal right now.
            (
                LED_DISPLAY_TEST,
                lambda: (
                    getattr(self, "test_signal_key", None) is not None
                    and now < getattr(self, "test_signal_until", 0.0)
                ),
            ),
            # Opt-in stage-3 takeover outranks everything: the user
            # explicitly chose "don't let me miss this".
            (LED_DISPLAY_ESCALATION, self.escalation_takeover_active),
            # A Severe/Extreme weather warning outranks every routine
            # signal -- it is the definition of "emergency". But NWS
            # warnings run for hours, and an agent blocked on YOU is
            # the one signal this app exists for: while a hard ask is
            # live, the ask renders and the weather heartbeat waits.
            (
                LED_DISPLAY_WEATHER,
                lambda: (
                    self.settings.weather_alerts_enabled
                    and self.weather_alert_active
                    and not self.hard_ask_renders_on_device(device)
                ),
            ),
            (LED_DISPLAY_LOW_BATTERY, lambda: self.low_power_active(battery_snapshot)),
            (
                LED_DISPLAY_NOTIFICATION,
                lambda: (
                    self.settings.notification_blinks_enabled
                    and not self.courtesy_signals_held()
                    and self.notification_blink_color is not None
                    and now < self.notification_blink_until
                ),
            ),
            (
                LED_DISPLAY_QUOTA,
                lambda: (
                    self.settings.quota_alerts_enabled
                    and not self.courtesy_signals_held()
                    and now < self.quota_blink_until
                ),
            ),
            (
                LED_DISPLAY_REMINDERS,
                lambda: (
                    self.settings.reminder_alerts_enabled
                    and not self.courtesy_signals_held()
                    and now < self.reminders_glow_until
                ),
            ),
            (
                LED_DISPLAY_COMPLETION,
                lambda: (
                    self.settings.completion_sweep_enabled
                    and not self.courtesy_signals_held()
                    and now < getattr(self, "completion_sweep_until", 0.0)
                ),
            ),
            (
                LED_DISPLAY_PEEK,
                lambda: now < getattr(self, "peek_until", 0.0),
            ),
            (
                LED_DISPLAY_ALL_CLEAR,
                lambda: (
                    self.settings.completion_sweep_enabled
                    and not self.courtesy_signals_held()
                    and now >= getattr(self, "completion_sweep_until", 0.0)
                    and now < getattr(self, "all_clear_until", 0.0)
                ),
            ),
            (
                LED_DISPLAY_CALENDAR,
                lambda: (
                    self.settings.calendar_alerts_enabled
                    and not self.courtesy_signals_held()
                    and now < self.calendar_glow_until
                ),
            ),
            (
                LED_DISPLAY_BATTERY,
                lambda: (
                    device.display == LED_DISPLAY_BATTERY
                    or (battery_snapshot is not None and now < self.battery_preview_until)
                ),
            ),
            (
                LED_DISPLAY_TIMER,
                lambda: (
                    device.display == LED_DISPLAY_TIMER
                    or self.timebox_active()
                    or self.timebox_overtime()
                ),
            ),
            (
                LED_DISPLAY_STUDIO,
                lambda: device.display == LED_DISPLAY_STUDIO,
            ),
            (
                LED_DISPLAY_QUOTA_RUNWAY,
                lambda: device.display == LED_DISPLAY_QUOTA_RUNWAY,
            ),
        )
        # Per-device "asks only" (backlog #21): this device skips the
        # courtesy moments entirely -- agent status, asks/escalation,
        # weather and low battery still land. The per-Focus policy's
        # per-device sibling.
        muted = device.signal_policy == "asks_only"
        for key, active in claims:
            if muted and key in DEVICE_MUTABLE_SIGNAL_KINDS:
                continue
            try:
                if active():
                    return key
            except Exception as exc:
                # A buggy claim must not silently disable its signal
                # forever -- log the first failure per display kind so it
                # shows up in status-bar.err.log instead of vanishing.
                logged = getattr(self, "display_claim_errors_logged", None)
                if logged is None:
                    logged = set()
                    self.display_claim_errors_logged = logged
                if key not in logged:
                    logged.add(key)
                    print(
                        f"sidepulse: display claim {key!r} raised {exc!r}; "
                        "treating as inactive",
                        file=sys.stderr,
                    )
                continue
        return LED_DISPLAY_AGENT

    def sync_leds(
        self,
        mode: AgentMode,
        battery_snapshot: BatterySnapshot | None,
        display_kind: str,
        statuses: tuple[AgentStatus, ...] = (),
    ) -> None:
        if not self.leds_enabled:
            return

        if not self.has_connected_physical_device():
            # Nothing physical to stay phase-locked to -- update the Screen
            # Bar immediately, same as before this device had a fix for
            # syncing to real hardware. When a physical device IS connected,
            # this update is deferred until that write actually completes
            # (see sync_leds_now/schedule_screen_bar_sync) so the two clocks
            # start from the same real-world instant instead of drifting.
            self.sync_virtual_status_device(mode, battery_snapshot, statuses)

        if time.monotonic() < self.led_animation_until_monotonic:
            return

        if self.led_sync_in_flight:
            # Coalesce, don't drop: a slow device write used to swallow
            # the newest state entirely, leaving stale LEDs until the
            # next event or the 15s poll. The worker triggers one fresh
            # refresh when it finishes.
            self.led_sync_dropped = True
            return
        self.led_sync_in_flight = True
        thread = threading.Thread(
            target=self.sync_leds_worker,
            args=(mode, battery_snapshot, display_kind, statuses),
            daemon=True,
        )
        # Tracked so tests can JOIN it before tempdir teardown -- a
        # daemon write racing shutil.rmtree was a recurring test flake.
        self.led_worker_thread = thread
        thread.start()

    def has_connected_physical_device(self) -> bool:
        return any(
            device.connected and device.device_id != VIRTUAL_DEVICE_ID
            for device in self.status_bar_devices(remember=False)
        )

    def sync_virtual_status_device(
        self,
        mode: AgentMode,
        battery_snapshot: BatterySnapshot | None,
        statuses: tuple[AgentStatus, ...] = (),
        *,
        started_at: float | None = None,
    ) -> None:
        if not SCREEN_BAR_FEATURE_ENABLED:
            self.virtual_status_device.hide()
            return
        if not self.settings.virtual_status_device_enabled:
            return
        # Kept fresh here rather than scattered across every settings-mutation
        # call site -- always current right before any (re)positioning below.
        self.virtual_status_device.set_wraps_menu_bar(
            self.settings.virtual_status_device_wraps_menu_bar
        )
        self.virtual_status_device.set_geometry_overrides(
            self.settings.screen_bar_gap_width, self.settings.screen_bar_wing_length
        )
        self.virtual_status_device.set_bracket_style(self.settings.screen_bar_bracket_style)
        self.virtual_status_device.set_min_glow(self.settings.screen_bar_min_glow)
        self.virtual_status_device.set_follow_alcove(
            self.settings.screen_bar_follow_alcove
        )
        # Story #14: the wing tips as standing micro-gauges -- the left
        # tip's quota ember tracks the worst window (fades in past 50%),
        # the right tip holds unseen-done green until the menu opens.
        if self.settings.screen_bar_gauges_enabled:
            percents = getattr(self, "quota_last_percents", None) or {}
            worst = max(percents.values(), default=0.0)
            left_level = max(0.0, min(1.0, (float(worst) - 50.0) / 50.0))
            snapshot = getattr(self, "last_snapshot", None)
            right_on = (
                bool(unseen_completions(snapshot, self))
                if snapshot is not None
                else False
            )
            self.virtual_status_device.set_standing_gauges(left_level, right_on)
        else:
            self.virtual_status_device.set_standing_gauges(0.0, False)
        # Click-to-answer: while an ask is live, the glowing bar itself
        # is the fastest route to the asking session -- click it and the
        # oldest unanswered ask's terminal comes forward. With no ask,
        # the window stays fully click-through (mouse events ignored),
        # exactly as before.
        self.ensure_peek_timer()
        click_target = self.screen_bar_click_status()
        if click_target is not None:
            self.virtual_status_device.set_click_handler(
                lambda status=click_target: self.open_session(
                    status, None, remember=False
                )
            )
        else:
            self.virtual_status_device.set_click_handler(None)
        device = next(
            (
                item for item in self.status_bar_devices(remember=False)
                if item.device_id == VIRTUAL_DEVICE_ID
            ),
            None,
        )
        if device is None:
            return
        # Effective, not raw: auto-brightness (screen tracking), idle dim,
        # and Focus dim all apply to the Screen Bar exactly as they do to
        # physical devices -- baking the raw configured value into the
        # program here was why the most-visible surface never reacted to
        # any of them.
        brightness = self.effective_brightness_for_device(device)
        display = self.active_led_display_kind_for_device(device, battery_snapshot)
        if display not in (
            LED_DISPLAY_AGENT,
            LED_DISPLAY_BATTERY,
            LED_DISPLAY_TIMER,
            LED_DISPLAY_STUDIO,
            LED_DISPLAY_QUOTA_RUNWAY,
        ):
            brightness = self.effective_signal_brightness_for_device(device)

        def _set_virtual(program: str) -> None:
            # The Screen Bar honors ITS calibration exactly like a
            # physical device -- gains applied at the write boundary
            # (the Colors-window previews stay uncorrected "true" hex).
            self.virtual_status_device.set_program(
                apply_channel_gain_to_program(
                    # Ember first, calibration second -- same order as
                    # AgentLedController. Without this the Screen Bar's
                    # Resting glow slider was a silent no-op.
                    apply_resting_glow_to_program(program, device.resting_glow),
                    device.channel_gains,
                ),
                started_at=started_at,
            )

        if display == LED_DISPLAY_TEST:
            _set_virtual(self.test_signal_program(brightness))
        elif display == LED_DISPLAY_ESCALATION:
            _set_virtual(self.escalation_takeover_program(brightness))
        elif display == LED_DISPLAY_NOTIFICATION and self.notification_blink_color:
            _set_virtual(
                style_to_program(
                    self.settings.signal_style(signals_module.SIGNAL_NOTIFICATION),
                    brightness,
                    color=self.notification_blink_color,
                )
            )
        elif display == LED_DISPLAY_COMPLETION:
            _set_virtual(
                style_to_program(
                    self.settings.signal_style(signals_module.SIGNAL_COMPLETION),
                    brightness,
                    color=getattr(self, "completion_sweep_color", None),
                )
            )
        elif display == LED_DISPLAY_WEATHER:
            _set_virtual(
                style_to_program(
                    self.settings.signal_style(signals_module.SIGNAL_WEATHER), brightness
                )
            )
        elif display == LED_DISPLAY_PEEK:
            _set_virtual(self.peek_program(brightness))
        elif display == LED_DISPLAY_ALL_CLEAR:
            _set_virtual(
                apply_brightness("off 400ms cosine\n#F5EDE0 3200ms pulse", brightness)
            )
        elif display == LED_DISPLAY_QUOTA:
            _set_virtual(
                style_to_program(
                    self.settings.signal_style(signals_module.SIGNAL_QUOTA),
                    brightness,
                    color=self.quota_blink_color,
                )
            )
        elif display == LED_DISPLAY_REMINDERS:
            _set_virtual(
                style_to_program(
                    self.settings.signal_style(signals_module.SIGNAL_REMINDERS), brightness
                )
            )
        elif display == LED_DISPLAY_CALENDAR:
            _set_virtual(
                style_to_program(
                    self.settings.signal_style(signals_module.SIGNAL_CALENDAR), brightness
                )
            )
        elif display == LED_DISPLAY_LOW_BATTERY:
            _set_virtual(
                style_to_program(
                    self.settings.signal_style(signals_module.SIGNAL_LOW_BATTERY), brightness
                )
            )
        elif display == LED_DISPLAY_BATTERY and battery_snapshot is not None:
            _set_virtual(
                program_for_battery(
                    battery_snapshot,
                    led_count=8,
                    brightness=brightness,
                )
            )
        elif display == LED_DISPLAY_TIMER:
            _set_virtual(self.timer_display_program(brightness, 8))
        elif display == LED_DISPLAY_STUDIO and (
            studio_program := self.studio_display_program(brightness)
        ):
            _set_virtual(studio_program)
        elif display == LED_DISPLAY_QUOTA_RUNWAY and (
            runway := self.quota_runway_state()
        ):
            _set_virtual(
                quota_runway_program(
                    runway[0], led_count=8, brightness=brightness, color=runway[1]
                )
            )
        else:
            colors_for_render = self.agent_render_colors()
            override = self.settings.device_blend_mode(VIRTUAL_DEVICE_ID)
            if override:
                colors_for_render = colors_for_render.with_blend_mode(override)
            _, program = program_for_snapshot(
                statuses,
                led_count=8,
                colors=colors_for_render,
                brightness=brightness,
                fallback_mode=mode,
            )
            _set_virtual(program)

    def schedule_screen_bar_sync(
        self,
        mode: AgentMode,
        battery_snapshot: BatterySnapshot | None,
        statuses: tuple[AgentStatus, ...],
        started_at: float,
    ) -> None:
        """Called from the LED worker thread right after a real device write
        completes. Dispatches back to the main thread (Screen Bar is an
        NSView and must be touched there) with the write's own completion
        time as phase-zero, so the on-screen replica's animation starts in
        lockstep with the physical device instead of independently."""
        payload = {
            "mode": mode,
            "battery_snapshot": battery_snapshot,
            "statuses": statuses,
            "started_at": started_at,
        }
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "applyScreenBarSync:",
            payload,
            False,
        )

    @objc.IBAction
    def applyScreenBarSync_(self, payload):
        self.sync_virtual_status_device(
            payload["mode"],
            payload["battery_snapshot"],
            payload["statuses"],
            started_at=payload["started_at"],
        )

    def sync_leds_worker(
        self,
        mode: AgentMode,
        battery_snapshot: BatterySnapshot | None,
        display_kind: str,
        statuses: tuple[AgentStatus, ...] = (),
    ) -> None:
        try:
            self.sync_leds_now(mode, battery_snapshot, display_kind, statuses)
        finally:
            self.led_sync_in_flight = False
            if getattr(self, "led_sync_dropped", False):
                # A newer state arrived mid-write; re-derive the freshest
                # state on the main thread rather than replaying the
                # (already stale) dropped payload.
                self.led_sync_dropped = False
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "refresh:", None, False
                )

    def validate_studio_program(
        self, program: str, *, strict: bool = False
    ) -> str | None:
        """Real firmware-grammar validation via the same sdled.wasm the
        Screen Bar runs -- validate_led_text only checks size limits,
        and a program the FIRMWARE can't parse makes the device strobe
        red. Returns an error message, or None when the program parses.
        Parser unavailable: None for preview/save (graceful), but with
        strict=True an ERROR -- the INIT.LED burn replays at every
        hardware boot and must never fail open on a broken parser."""
        try:
            from .led_wasm import SdLedWasmController

            result = SdLedWasmController(led_count=8).parse(program, 0)
        except Exception:
            if strict:
                return (
                    "firmware parser unavailable -- refusing to burn an "
                    "unverified program"
                )
            return None
        if result.ok:
            return None
        return f"{result.error_name} at line {result.line}, column {result.column}"

    def ensure_peek_timer(self) -> None:
        """A 5Hz NSEvent.mouseLocation() poll (class method, zero
        permissions -- NOT an event tap) while the Screen Bar is shown:
        dwell on the bar for ~0.6s and it answers with a 4s vitals
        readout. An NSTrackingArea can't work here -- the window
        deliberately ignores mouse events."""
        if getattr(self, "_peek_timer", None) is not None:
            return
        self._peek_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.2, self, "peekTick:", None, True
        )

    @objc.IBAction
    def peekTick_(self, _timer):
        window = getattr(self.virtual_status_device, "window", None)
        if window is None or getattr(self, "status_menu_open", False):
            self._peek_hits = 0
            return
        from AppKit import NSEvent

        location = NSEvent.mouseLocation()
        frame = window.frame()
        inside = (
            frame.origin.x <= location.x <= frame.origin.x + frame.size.width
            and frame.origin.y <= location.y <= frame.origin.y + frame.size.height
        )
        now = time.monotonic()
        peeking = now < getattr(self, "peek_until", 0.0)
        if inside:
            self._peek_hits = getattr(self, "_peek_hits", 0) + 1
            if self._peek_hits == 3 and not peeking:
                self.peek_until = now + 4.0
                self.refresh_(None)
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                    4.1, self, "refresh:", None, False
                )
        else:
            self._peek_hits = 0
            if peeking:
                self.peek_until = 0.0
                self.refresh_(None)

    def peek_program(self, brightness: float, led_count: int = 8) -> str:
        """The vitals readout: an active timebox shows the timer fill;
        otherwise the left half is Claude runway, the right half Codex,
        each brand-colored and scaled by remaining headroom."""
        if self.timebox_active():
            return timer_fill_program(
                self.timer_fill_fraction(),
                led_count=led_count,
                brightness=brightness,
                color=self.settings.colors.mode_colors.get("working", "#00E5FF"),
            )
        percents = getattr(self, "quota_last_percents", None) or {}

        def _fraction(prefix):
            values = [v for k, v in percents.items() if k.startswith(prefix)]
            return 1.0 - (max(values) / 100.0) if values else None

        halves = (
            (_fraction("Claude"), "#D97757"),
            (_fraction("Codex"), "#10A37F"),
        )
        half = max(1, led_count // 2)
        segments = []
        for side, (fraction, color) in enumerate(halves):
            for position in range(half):
                index = side * half + position
                if index >= led_count:
                    break
                if fraction is None:
                    segments.append(f"{index}:#1A1A1A 60000ms linear")
                    continue
                level = max(0.0, min(1.0, fraction * half - position))
                lit = (
                    scale_hex_brightness(color, max(0.08, level) * (brightness / 255.0))
                    if level > 0.0
                    else "#000000"
                )
                segments.append(f"{index}:{lit} 60000ms linear")
        return "\n".join(["; ".join(segments), "repeat"])

    def screen_bar_click_status(self):
        """The session a Screen Bar click should open: the OLDEST
        unanswered hard ask (the same episode ordering escalation
        follows), or None when nothing needs you."""
        snapshot = getattr(self, "last_snapshot", None)
        if snapshot is None:
            return None
        asks = ask_statuses(snapshot)
        if not asks:
            return None
        episodes = getattr(self, "ask_blocked_by_agent", {})
        return min(
            asks,
            key=lambda status: episodes.get(status.agent_id, float("inf")),
        )

    def quota_runway_state(self) -> tuple[float, str] | None:
        """(fraction_left, brand color) for the WORST tracked quota
        window, or None before any data -- factory-None falls back to
        Agent display by the documented table contract."""
        percents = getattr(self, "quota_last_percents", None) or {}
        if not percents:
            return None
        worst_key = max(percents, key=lambda key: percents[key])
        used = max(0.0, min(100.0, float(percents[worst_key])))
        brand = {"Claude": "#D97757", "Codex": "#10A37F"}
        color = next(
            (hex_value for name, hex_value in brand.items() if worst_key.startswith(name)),
            "#10A37F",
        )
        return (1.0 - used / 100.0, color)

    def studio_display_program(self, brightness: float) -> str | None:
        """The persistent Studio render: the saved program, validated and
        brightness-scaled. None (fall back to Agent) when empty or
        invalid, so a typo can never strobe the firmware. Validation is
        cached by program text -- this runs on every LED sync."""
        program = (self.settings.studio_program or "").strip()
        if not program:
            return None
        cached = getattr(self, "_studio_validation_cache", None)
        if cached is None or cached[0] != program:
            try:
                normalized = normalize_led_text(program)
                validate_led_text(normalized)
                error = self.validate_studio_program(normalized)
            except Exception as exc:
                normalized, error = program, str(exc)
            cached = (program, error, normalized)
            self._studio_validation_cache = cached
        if cached[1] is not None:
            return None
        return apply_brightness(cached[2], brightness)

    def signal_display_entries(self):
        """display kind -> (program factory(brightness, led_count), LED
        state, label factory(device, battery_snapshot)). Adding a
        persistent signal is one row here plus a precedence claim in
        active_led_display_kind_for_device. A factory returning None
        means "not renderable right now" and falls back to Agent."""

        def styled(signal_key, *, color=None):
            def factory(brightness, led_count):
                return style_to_program(
                    self.settings.signal_style(signal_key),
                    brightness,
                    color=color() if callable(color) else color,
                    led_count=led_count,
                )

            return factory

        return {
            LED_DISPLAY_TEST: (
                lambda brightness, led_count: self.test_signal_program(
                    brightness, led_count=led_count
                ),
                LedDisplayState.ASK,
                lambda device, _snapshot: f"{device.name} Signal test",
            ),
            LED_DISPLAY_ESCALATION: (
                lambda brightness, led_count: self.escalation_takeover_program(
                    brightness, led_count=led_count
                ),
                LedDisplayState.ASK,
                lambda device, _snapshot: f"{device.name} Needs you (takeover)",
            ),
            LED_DISPLAY_NOTIFICATION: (
                lambda brightness, led_count: (
                    styled(
                        signals_module.SIGNAL_NOTIFICATION,
                        color=self.notification_blink_color,
                    )(brightness, led_count)
                    if self.notification_blink_color
                    else None
                ),
                LedDisplayState.ASK,
                lambda device, _snapshot: f"{device.name} Notification blink",
            ),
            LED_DISPLAY_COMPLETION: (
                lambda brightness, led_count: styled(
                    signals_module.SIGNAL_COMPLETION,
                    color=getattr(self, "completion_sweep_color", None),
                )(brightness, led_count),
                LedDisplayState.DONE,
                lambda device, _snapshot: f"{device.name} Completion sweep",
            ),
            LED_DISPLAY_WEATHER: (
                styled(signals_module.SIGNAL_WEATHER),
                LedDisplayState.ASK,
                lambda device, _snapshot: (
                    f"{device.name} Weather {self.weather_alert_event or 'alert'}"
                ),
            ),
            LED_DISPLAY_PEEK: (
                lambda brightness, led_count: self.peek_program(
                    brightness, led_count=led_count
                ),
                LedDisplayState.WORKING,
                lambda device, _snapshot: f"{device.name} Peek",
            ),
            LED_DISPLAY_ALL_CLEAR: (
                lambda brightness, led_count: apply_brightness(
                    "off 400ms cosine\n#F5EDE0 3200ms pulse", brightness
                ),
                LedDisplayState.DONE,
                lambda device, _snapshot: f"{device.name} All clear",
            ),
            LED_DISPLAY_QUOTA: (
                lambda brightness, led_count: styled(
                    signals_module.SIGNAL_QUOTA,
                    color=self.quota_blink_color,
                )(brightness, led_count),
                LedDisplayState.ASK,
                lambda device, _snapshot: (
                    f"{device.name} Quota {self.quota_blink_label or ''}"
                ),
            ),
            LED_DISPLAY_REMINDERS: (
                styled(signals_module.SIGNAL_REMINDERS),
                LedDisplayState.ASK,
                lambda device, _snapshot: f"{device.name} Reminder due",
            ),
            LED_DISPLAY_CALENDAR: (
                styled(signals_module.SIGNAL_CALENDAR),
                LedDisplayState.ASK,
                lambda device, _snapshot: (
                    f"{device.name} Calendar {self.calendar_event_title or 'event'}"
                ),
            ),
            LED_DISPLAY_LOW_BATTERY: (
                styled(signals_module.SIGNAL_LOW_BATTERY),
                LedDisplayState.ASK,
                lambda device, snapshot: (
                    f"{device.name} Low battery "
                    f"{snapshot.percent if snapshot else '?'}%"
                ),
            ),
            LED_DISPLAY_TIMER: (
                lambda brightness, led_count: self.timer_display_program(
                    brightness, led_count
                ),
                LedDisplayState.WORKING,
                lambda device, _snapshot: (
                    f"{device.name} Timer {round(self.timer_fill_fraction() * 100)}%"
                ),
            ),
            LED_DISPLAY_STUDIO: (
                lambda brightness, led_count: self.studio_display_program(brightness),
                LedDisplayState.WORKING,
                lambda device, _snapshot: f"{device.name} Studio program",
            ),
            LED_DISPLAY_QUOTA_RUNWAY: (
                lambda brightness, led_count: (
                    quota_runway_program(
                        self.quota_runway_state()[0],
                        led_count=led_count,
                        brightness=brightness,
                        color=self.quota_runway_state()[1],
                    )
                    if self.quota_runway_state() is not None
                    else None
                ),
                LedDisplayState.WORKING,
                lambda device, _snapshot: f"{device.name} Quota runway",
            ),
        }

    def sync_leds_now(
        self,
        mode: AgentMode,
        battery_snapshot: BatterySnapshot | None,
        display_kind: str,
        statuses: tuple[AgentStatus, ...] = (),
    ) -> None:
        devices = [
            device for device in self.status_bar_devices()
            if device.connected and device.device_id != VIRTUAL_DEVICE_ID
        ]
        if not devices:
            self.ensure_device_selection()
            devices = [
                device
                for device in self.status_bar_devices(remember=False)
                if device.connected and device.device_id != VIRTUAL_DEVICE_ID
            ]
        if not devices:
            self.last_led_error = None
            return

        active_errors: dict[str, str] = {}
        agent_write_changed = False
        agent_write_failed = False
        for device in devices:
            device_display_kind = self.active_led_display_kind_for_device(
                device,
                battery_snapshot,
            )
            if self.last_led_display_kind_by_device.get(device.device_id) != device_display_kind:
                self.reset_led_controllers_for_device(device.device_id)
                self.last_led_display_kind_by_device[device.device_id] = device_display_kind

            device_led_count = led_count_for_target(device.target)
            entry = self.signal_display_entries().get(device_display_kind)
            program = None
            label = ""
            if entry is not None:
                factory, led_state, label_factory = entry
                controller = self.agent_controller_for_device(device)
                # Ambient displays (timer/studio/runway) follow dimming;
                # true SIGNALS flash at configured brightness.
                ambient_kinds = (
                    LED_DISPLAY_TIMER,
                    LED_DISPLAY_STUDIO,
                    LED_DISPLAY_QUOTA_RUNWAY,
                )
                render_brightness = (
                    controller.brightness
                    if device_display_kind in ambient_kinds
                    else self.effective_signal_brightness_for_device(device)
                )
                program = factory(render_brightness, device_led_count)
                label = label_factory(device, battery_snapshot)
            if program is not None:
                result = controller.sync_program(program, led_state)
                if result.error:
                    agent_write_failed = True
                elif result.changed:
                    agent_write_changed = True
            elif device_display_kind == LED_DISPLAY_BATTERY and battery_snapshot is not None:
                result = self.battery_controller_for_device(device).sync_snapshot(battery_snapshot)
                label = (
                    f"{device.name} Battery {battery_snapshot.percent}% "
                    f"{format_watts(battery_snapshot.adapter_power)}"
                )
            else:
                colors_for_render = self.agent_render_colors()
                override = self.settings.device_blend_mode(device.device_id)
                if override:
                    colors_for_render = colors_for_render.with_blend_mode(override)
                # Story #16: a pinned device renders only its provider's
                # sessions and rests dark (IDLE_READY) when none are
                # live. Asks are NEVER partitioned -- escalation claims
                # were resolved in the display ladder above, before the
                # agent display was chosen for this device.
                statuses_for_device = statuses
                fallback_for_device = mode
                pin = self.settings.device_provider_pin(device.device_id)
                if pin:
                    statuses_for_device = tuple(
                        status for status in statuses if status.provider == pin
                    )
                    if not statuses_for_device:
                        fallback_for_device = AgentMode.IDLE_READY
                result = self.agent_controller_for_device(device).sync_snapshot(
                    statuses_for_device,
                    colors_for_render,
                    fallback_mode=fallback_for_device,
                )
                label = f"{device.name} {result.label}"
                if result.error:
                    agent_write_failed = True
                elif result.changed:
                    agent_write_changed = True
            if result.error:
                active_errors[device.device_id] = result.error
                previous_error = self.device_errors.get(device.device_id)
                if result.error != previous_error:
                    log_status_bar(f"led error {device.name}: {result.error}")
                continue

            self.device_errors.pop(device.device_id, None)
            if result.changed:
                target = result.target if result.target is not None else "-"
                log_status_bar(f"leds={label} target={target}")

        self.device_errors.update(active_errors)
        self.last_led_error = next(iter(self.device_errors.values()), None)

        if agent_write_changed:
            # The physical write(s) just completed on this worker thread --
            # use this instant as the Screen Bar's phase-zero instead of
            # letting it start independently on the next main-thread tick.
            self.schedule_screen_bar_sync(mode, battery_snapshot, statuses, time.monotonic())
        elif agent_write_failed:
            # The physical write failed -- there's no real completion to
            # sync to, so fall back to an immediate (unsynced) update rather
            # than leaving the Screen Bar stuck showing stale state forever.
            payload = {
                "mode": mode,
                "battery_snapshot": battery_snapshot,
                "statuses": statuses,
                "started_at": None,
            }
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyScreenBarSync:",
                payload,
                False,
            )

    def play_lid_animation(
        self,
        kind: str,
        *,
        animation: LedAnimationSetting | None = None,
    ) -> None:
        if not self.leds_enabled:
            return
        animation = animation or self.settings.lid_animation(kind)
        try:
            validate_lid_animation(animation)
        except DeviceWriteError as exc:
            self.set_settings_message(f"{LID_ANIMATION_LABELS[kind]} animation invalid: {exc}")
            return

        devices = [
            device for device in self.status_bar_devices()
            if device.connected and device.device_id != VIRTUAL_DEVICE_ID
        ]
        if not devices:
            return

        self.led_animation_token += 1
        token = self.led_animation_token
        duration = animation.duration_seconds + LID_ANIMATION_RESTORE_FUDGE_SECONDS
        self.led_animation_until_monotonic = time.monotonic() + duration
        thread = threading.Thread(
            target=self.play_lid_animation_worker,
            args=(kind, animation, devices, token),
            daemon=True,
        )
        thread.start()

    def play_lid_animation_worker(
        self,
        kind: str,
        animation: LedAnimationSetting,
        devices: list[StatusBarDevice],
        token: int,
    ) -> None:
        label = LID_ANIMATION_LABELS[kind]
        for device in devices:
            try:
                program = program_for_lid_animation(animation, brightness=device.brightness)
                target = write_led_program(program, device_path=device.target)
                log_status_bar(f"animation={label} device={device.name} target={target}")
            except Exception as exc:
                log_status_bar(f"animation error {label} {device.name}: {exc}")

        time.sleep(animation.duration_seconds + LID_ANIMATION_RESTORE_FUDGE_SECONDS)
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "restoreLedDisplay:",
            str(token),
            False,
        )

    @objc.IBAction
    def restoreLedDisplay_(self, token_value):
        restore_led_display(self, token_value)

    def connect_device(self) -> None:
        self.leds_enabled = True
        self.status_bar_devices()
        self.reset_led_controllers_for_display_change()
        self.last_led_error = None
        self.last_status_read_error = None
        log_status_bar("device connect requested")

    def disconnect_device(self) -> None:
        self.leds_enabled = False
        targets = self.current_led_targets()
        if not targets:
            targets = [
                device.target for device in self.status_bar_devices()
                if device.connected and device.device_id != VIRTUAL_DEVICE_ID
            ]
        for target in targets:
            try:
                result = write_mode_to_leds(AgentMode.IDLE_READY, device_path=target)
                log_status_bar(f"device disconnected target={result.target}")
            except Exception as exc:
                log_status_bar(f"device disconnect error: {exc}")
        self.reset_led_controllers_for_display_change()
        self.last_led_error = None
        self.last_status_read_error = None

    def device_connected(self) -> bool:
        connected = [
            device
            for device in self.status_bar_devices(remember=False)
            if device.connected
        ]
        return (
            self.leds_enabled
            and bool(self.current_led_targets() or connected)
            and self.last_led_error is None
            and not self.device_errors
        )

    def current_led_target(self) -> Path | None:
        targets = self.current_led_targets()
        return targets[0] if targets else None

    def current_led_targets(self) -> list[Path]:
        targets: list[Path] = []
        seen: set[str] = set()
        for controller in (
            *self.battery_led_controllers_by_device.values(),
            *self.agent_led_controllers_by_device.values(),
            self.battery_led_controller,
            self.led_controller,
        ):
            target = controller.last_target or controller.device_path
            if target is None:
                continue
            if not path_exists(Path(target).parent):
                continue
            key = str(target)
            if key in seen:
                continue
            targets.append(Path(target))
            seen.add(key)
        return targets

    def ensure_device_selection(self) -> None:
        selected = self.led_controller.device_path or self.battery_led_controller.device_path
        if selected is not None:
            target = target_from_device_path(
                Path(selected),
                DEFAULT_FILE_NAME,
            )
            if path_exists(target.parent):
                self.led_controller.device_path = target
                self.battery_led_controller.device_path = target
                return
            self.led_controller.device_path = None
            self.battery_led_controller.device_path = None
            self.reset_led_controllers_for_display_change()
            self.last_led_error = None

        try:
            candidates = discover_devices()
        except Exception as exc:
            log_status_bar(f"device selection error: {exc}")
            self.last_led_error = str(exc)
            return
        if not candidates:
            return
        target = preferred_status_bar_device(candidates).target
        self.led_controller.device_path = target
        self.battery_led_controller.device_path = target

    @objc.IBAction
    def pollLid_(self, _sender):
        # read_lid_closed forks ioreg -- this ran ON the main thread
        # every second for every user (the audit's #1 finding). Same
        # worker contract as pollWeather_: the thread must ALWAYS post
        # its payload or the in-flight flag strands.
        if getattr(self, "lid_poll_in_flight", False):
            return
        self.lid_poll_in_flight = True

        def _work():
            try:
                payload = {"ok": True, "closed": read_lid_closed()}
            except Exception as exc:
                payload = {"ok": False, "error": str(exc)}
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "lidChecked:", payload, False
            )

        threading.Thread(target=_work, daemon=True).start()

    @objc.IBAction
    def lidChecked_(self, payload):
        self.lid_poll_in_flight = False
        if not payload.get("ok"):
            error = str(payload.get("error"))
            if error != self.last_lid_error:
                self.last_lid_error = error
                log_status_bar(f"lid_state error: {error}")
            return
        closed = payload.get("closed")
        if closed is None:
            return
        self.last_lid_error = None
        if self.last_lid_closed is None:
            self.last_lid_closed = closed
            return
        if closed == self.last_lid_closed:
            return

        self.last_lid_closed = closed
        kind = self.settings.lid_animation_for_context(closed, self.agents_active_now())
        log_status_bar(
            f"lid_state={'closed' if closed else 'open'} animation={kind}"
        )
        self.play_lid_animation(kind)

    def display_aggregate_mode(self, snapshot) -> AgentMode:
        """The mode the lights and icon actually show. A SUB-agent ask
        can't be answered (its parent handles it), so by default only
        MAIN sessions may pull the aggregate into Ask/Blocked -- the
        blinking light was firing for workers nobody could reply to.
        settings.subagent_asks_alert restores the old behavior."""
        mode = snapshot.aggregate.mode
        if self.settings.subagent_asks_alert:
            return mode
        ask_modes = (AgentMode.WAITING_FOR_INPUT, AgentMode.BLOCKED_ERROR)
        if mode not in ask_modes:
            return mode
        mains = tuple(
            status for status in snapshot.statuses if not status.is_subagent
        )
        if any(status.mode in ask_modes for status in mains):
            return mode
        if not mains:
            return AgentMode.IDLE_READY
        return aggregate_status(mains, snapshot.stale_statuses).mode

    def agents_active_now(self) -> bool:
        """Any MAIN session actually working at this instant -- the lid
        animation says "your agents are still cooking" vs "all quiet"
        before the strip settles into its normal display."""
        snapshot = self.last_snapshot
        if snapshot is None:
            return False
        busy = (AgentMode.WORKING, AgentMode.TOOL_RUNNING, AgentMode.LONG_TASK_PROGRESS)
        return any(
            not status.is_subagent and status.mode in busy
            for status in snapshot.statuses
        )

    @objc.IBAction
    def pollDevices_(self, _sender):
        self.poll_devices_once()

    def poll_devices_once(self) -> None:
        if not self.observe_connected_devices():
            return
        self.rebuild_devices_pane()
        if self.last_snapshot is not None:
            self.refresh_(None)

    def rebuild_devices_pane(self) -> None:
        """A device plugged in while Settings is open used to appear in
        the menu-bar submenu but never in the Devices pane until app
        restart -- the pane snapshots devices when the window is built."""
        window = getattr(self, "settings_window", None)
        panes = getattr(self, "settings_panes", None)
        if window is None or panes is None:
            return
        old_pane = panes.get("devices")
        if old_pane is None:
            return
        container = old_pane.superview()
        if container is None:
            return
        was_hidden = old_pane.isHidden()
        new_pane, device_controls = _build_devices_pane(self)
        new_pane.setHidden_(was_hidden)
        container.addSubview_(new_pane)
        NSLayoutConstraint.activateConstraints_(
            [
                new_pane.topAnchor().constraintEqualToAnchor_(container.topAnchor()),
                new_pane.leadingAnchor().constraintEqualToAnchor_(
                    container.leadingAnchor()
                ),
                new_pane.trailingAnchor().constraintEqualToAnchor_(
                    container.trailingAnchor()
                ),
                new_pane.bottomAnchor().constraintEqualToAnchor_(
                    container.bottomAnchor()
                ),
            ]
        )
        old_pane.removeFromSuperview()
        panes["devices"] = new_pane
        self.device_settings_controls = device_controls

    def sync_keep_awake(self, mode: AgentMode) -> None:
        was_running = self.keep_awake.process_running()
        # Kept fresh here (rather than only at construction) so adjusting
        # the grace period in Settings takes effect on the very next poll
        # instead of needing a restart.
        self.keep_awake.set_grace_seconds(self.settings.closed_lid_grace_minutes * 60.0)
        self.keep_awake.update(mode)
        is_running = self.keep_awake.process_running()
        if was_running != is_running:
            log_status_bar(f"keep_awake={'active' if is_running else 'released'}")
        if self.keep_awake.last_error != self.last_keep_awake_error:
            self.last_keep_awake_error = self.keep_awake.last_error
            if self.last_keep_awake_error:
                log_status_bar(f"keep_awake error: {self.last_keep_awake_error}")

        self.sync_closed_lid_awake()

        if not self.leds_enabled:
            return
        # SD-card pokes were file I/O ON THE MAIN THREAD up to 4x/s --
        # a busy or slow card froze the whole UI ("clicked Devices, took
        # five seconds to register a scroll"). Worker thread, in-flight
        # guarded; the card can be as slow as it likes now.
        if getattr(self, "_keepalive_poke_in_flight", False):
            return
        targets = self.status_keepalive_targets()
        if not targets:
            return
        self._keepalive_poke_in_flight = True

        def _poke():
            try:
                read_any = False
                for target in targets:
                    status_path = self.keep_awake.poke_status_file(target)
                    if status_path is not None:
                        read_any = True
                        log_status_bar(f"sd_keepalive touch={status_path}")
                if (
                    not read_any
                    and self.keep_awake.last_status_error != self.last_status_read_error
                ):
                    self.last_status_read_error = self.keep_awake.last_status_error
                    if self.last_status_read_error:
                        log_status_bar(
                            f"sd_keepalive error: {self.last_status_read_error}"
                        )
            finally:
                self._keepalive_poke_in_flight = False

        threading.Thread(target=_poke, daemon=True).start()

    def sync_closed_lid_awake(self) -> None:
        was_active = self.closed_lid_awake.active()
        self.closed_lid_awake.set_use_system_disable(sleep_helper_installed())
        self.closed_lid_awake.update(
            self.settings.closed_lid_awake_policy,
            agents_active=self.keep_awake.holding_requested,
        )
        is_active = self.closed_lid_awake.active()
        if was_active != is_active:
            log_status_bar(
                f"closed_lid_awake={'active' if is_active else 'released'} "
                f"policy={self.settings.closed_lid_awake_policy}"
            )
        if self.closed_lid_awake.last_error != self.last_closed_lid_awake_error:
            self.last_closed_lid_awake_error = self.closed_lid_awake.last_error
            if self.last_closed_lid_awake_error:
                log_status_bar(
                    f"closed_lid_awake error: {self.last_closed_lid_awake_error}"
                )

    def status_keepalive_targets(self) -> list[Path]:
        targets = self.current_led_targets()
        if targets:
            return targets
        connected_targets = [
            device.target
            for device in self.status_bar_devices(remember=False)
            if device.connected and device.device_id != VIRTUAL_DEVICE_ID
        ]
        if connected_targets:
            return connected_targets
        return [MOUNT_ROOT / name / KEEPALIVE_FILE_NAME for name in STATUS_BAR_KEEPALIVE_VOLUME_NAMES]


# One per day, keyed to the calendar: each teaches a feature people
# don't find on their own. (text, settings pane key or None).
DAILY_TIPS: tuple[tuple[str, str | None, str | None], ...] = (
    ("Each agent gets its own color when several run at once", "color_studio", None),
    ("Give a session a permanent color from its row's Identity Color menu", None, None),
    ("The Screen Bar hugs your notch -- style it under Screen Bar", "colors_screen_bar", None),
    ("Timer fills your lights as working time passes -- try it below", None, None),
    ("Write your own light animation under Animations", "animations", None),
    ("Whites looking off? Calibrate each device under Devices", "devices", None),
    ("Day, Night, and Travel calibration profiles live under Profiles", None, None),
    ("Ignored asks can escalate: light, menu bar, chime, takeover", "led_behavior", None),
    ("Severe-weather warnings can flash your lights", "led_behavior", None),
    ("Calendar events and Reminders can glow before they're due", "led_behavior", None),
    ("Every signal card in Signals has a Test button -- try one", "led_behavior", None),
    ("A macOS Focus can dim or silence your lights automatically", "focus", None),
    ("A device can show Agent status, Battery, Timer, or your Studio program", "devices", None),
    (
        "Claude, OpenAI, Codex, and Gemini brand colors are the swatches on every Agent color row",
        "color_studio",
        "brand_colors",
    ),
    ("Celebrate when finished sweeps green the moment an agent completes", "color_studio", None),
)


def daily_tip(settings=None) -> tuple[str, str | None, str | None] | None:
    """The day's tip, skipping anything the user dismissed. None when
    every tip is dismissed or tips are off entirely."""
    if settings is not None and not getattr(settings, "tips_enabled", True):
        return None
    dismissed = set(getattr(settings, "dismissed_tips", ()) or ())
    tips = [tip for tip in DAILY_TIPS if tip[0] not in dismissed]
    if not tips:
        return None
    # Local calendar day: the tip changes overnight, like a calendar page.
    day = datetime.now().timetuple().tm_yday
    return tips[day % len(tips)]


def target_quiet_active(target) -> bool:
    quiet = getattr(target, "quiet_active", None)
    return bool(quiet()) if callable(quiet) else False


def session_row_suffix(
    status: AgentStatus,
    *,
    worker_count: int = 0,
    working_since: float | None = None,
    unseen_done: bool = False,
) -> str:
    """The row's trailing facts: plan-ready tag, live worker count,
    elapsed working time, and the unseen-done marker."""
    parts = []
    if status.is_plan_ready:
        parts.append("Plan ready")
    if worker_count == 1:
        parts.append("1 worker")
    elif worker_count > 1:
        parts.append(f"{worker_count} workers")
    if working_since is not None and status.mode in (
        AgentMode.WORKING,
        AgentMode.TOOL_RUNNING,
        AgentMode.LONG_TASK_PROGRESS,
    ):
        minutes = int(max(0.0, time.monotonic() - working_since) // 60)
        if minutes >= 60:
            parts.append(f"{minutes // 60}h {minutes % 60}m")
        elif minutes >= 1:
            parts.append(f"{minutes}m")
    if unseen_done and status.mode == AgentMode.COMPLETED:
        parts.append("new")
    if not parts:
        return ""
    return " \u00b7 " + " \u00b7 ".join(parts)


def menu_content_signature(snapshot, state, target) -> tuple:
    """Everything the dropdown renders, hashed coarsely. Ages bucket to
    the minute (rows show "4m"), and a 30s monotonic bucket is a safety
    valve: anything this signature misses self-heals within 30s."""
    now = snapshot.collected_at
    def row(status):
        return (
            status.agent_id,
            status.mode.value,
            status.display_name,
            int(status.age_seconds(now) // 60),
        )

    devices = tuple(
        (
            device.device_id,
            device.connected,
            device.display,
            round(float(device.brightness), 1),
        )
        for device in target.status_bar_devices(remember=False)
    )
    return (
        state.label,
        tuple(row(status) for status in ask_statuses(snapshot)),
        tuple(row(status) for status in recent_statuses(snapshot)),
        tuple(
            tuple(row(child) for child in children)
            for children in active_subagents_by_parent(snapshot).values()
        ),
        devices,
        round(target.timer_fill_fraction(), 2) if target.timebox_active() else None,
        target.settings.closed_lid_awake_policy,
        target.closed_lid_awake.last_error,
        target_quiet_active(target),
        (
            target.active_focus_summary()
            if callable(getattr(target, "active_focus_summary", None))
            else None
        ),
        tuple(sorted(getattr(target, "cleared_session_ids", set()))),
        tuple(sorted(u.agent_id for u in unseen_completions(snapshot, target))),
        getattr(target.settings, "tips_enabled", True),
        len(getattr(target.settings, "dismissed_tips", ()) or ()),
        int(time.monotonic() // 30),
    )


def build_menu(snapshot, state: StatusBarState, target: StatusBarController) -> NSMenu:
    """The status-item dropdown. Glanceability rules: sessions first
    (the thing you opened the menu to check), no self-titled header (you
    know what menu you clicked), and one row per secondary concern --
    the keep-awake policy is a submenu, not four inline rows."""
    menu = NSMenu.alloc().init()

    # The Ask Inbox: WHO needs you, pinned first -- the reason the menu
    # got opened. Rows are the same one-click session jumps as below.
    asks = ask_statuses(snapshot)
    summary_mains = recent_statuses(snapshot)
    summary_workers = sum(
        len(children) for children in active_subagents_by_parent(snapshot).values()
    )
    if summary_mains:
        session_word = "session" if len(summary_mains) == 1 else "sessions"
        summary_bits = [f"{len(summary_mains)} {session_word}"]
        if summary_workers:
            worker_word = "worker" if summary_workers == 1 else "workers"
            summary_bits.append(f"{summary_workers} {worker_word}")
        if asks:
            summary_bits.append(f"{len(asks)} needs you")
        menu.addItem_(disabled_menu_item(" \u00b7 ".join(summary_bits)))
        menu.addItem_(NSMenuItem.separatorItem())
    if asks:
        menu.addItem_(disabled_menu_item(f"Needs You ({len(asks)})"))
        ask_identity: dict[str, str] = {}
        if len(asks) > 1:
            menu_colors = getattr(getattr(target, "settings", None), "colors", None)
            ask_identity = colors_module.identity_colors_for_agents(
                [status.agent_id for status in asks],
                groups=colors_module.identity_groups_for_statuses(asks, menu_colors),
            )
        for status in asks:
            dot = None
            if getattr(target, "settings", None) is not None:
                dot = target.settings.colors.session_color(status.agent_id)
            menu.addItem_(
                build_session_menu_item(
                    status,
                    snapshot.collected_at,
                    target,
                    identity_color=dot or ask_identity.get(status.agent_id),
                )
            )
        menu.addItem_(NSMenuItem.separatorItem())

    menu.addItem_(disabled_menu_item("Agents"))

    statuses = recent_statuses(snapshot)
    if not statuses:
        # The empty state teaches instead of dead-ending: a brand-new
        # user's first open should say what will happen next. With zero
        # hooks installed the old teaching text ("start Claude Code --
        # sessions appear here") could never come true, so that case
        # gets an enabled row straight into Setup instead. The probe is
        # cached: detector() hits the filesystem per provider, and this
        # runs on every menu build.
        menu.addItem_(disabled_menu_item("No agents yet"))
        hooks_probe = getattr(target, "_menu_hooks_probe", None)
        now_probe = time.monotonic()
        if hooks_probe is None or now_probe - hooks_probe[0] > 30.0:
            try:
                any_hooks = any(
                    provider_hooks_installed(provider_spec(provider).detector(None))
                    for provider in HOOK_PROVIDERS
                )
            except Exception:
                any_hooks = True
            target._menu_hooks_probe = (now_probe, any_hooks)
        else:
            any_hooks = hooks_probe[1]
        if any_hooks:
            menu.addItem_(
                disabled_menu_item("Start Claude Code or Codex -- sessions appear here")
            )
        else:
            connect_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Connect your agents in Setup\u2026", "openSetup:", ""
            )
            connect_item.setTarget_(target)
            menu.addItem_(connect_item)
    else:
        # "Color = agent": with several sessions, each row leads with
        # its identity dot -- the same hue the LEDs and Screen Bar use
        # for that session -- so the mapping is learnable at a glance.
        identity: dict[str, str] = {}
        if len(statuses) > 1:
            menu_colors = getattr(getattr(target, "settings", None), "colors", None)
            identity = colors_module.identity_colors_for_agents(
                [status.agent_id for status in statuses],
                groups=colors_module.identity_groups_for_statuses(statuses, menu_colors),
            )
        subagent_groups = active_subagents_by_parent(snapshot)
        cleared_ids = getattr(target, "cleared_session_ids", set())
        unseen_ids = {
            unseen.agent_id for unseen in unseen_completions(snapshot, target)
        }
        working_since = getattr(target, "working_since_by_agent", {})
        # With SEVERAL providers live, small provider headers keep the
        # 2-Claude-plus-Codex reality scannable; a single provider
        # renders exactly as before.
        providers_live = []
        for status in statuses:
            if status.provider not in providers_live:
                providers_live.append(status.provider)
        show_provider_headers = len(providers_live) > 1
        if show_provider_headers:
            # Stable regroup: provider blocks in first-appearance order,
            # priority order preserved inside each block.
            statuses = [
                status
                for provider in providers_live
                for status in statuses
                if status.provider == provider
            ]
        last_provider_header = None
        for status in statuses:
            if status.mode == AgentMode.COMPLETED and status.agent_id in cleared_ids:
                continue
            if show_provider_headers and status.provider != last_provider_header:
                last_provider_header = status.provider
                menu.addItem_(disabled_menu_item(status.provider.title()))
            dot_color = None
            if getattr(target, "settings", None) is not None:
                dot_color = target.settings.colors.session_color(status.agent_id)
            dot_color = dot_color or identity.get(status.agent_id)
            children = subagent_groups.pop(status.agent_id, [])
            menu.addItem_(
                build_session_menu_item(
                    status,
                    snapshot.collected_at,
                    target,
                    identity_color=dot_color,
                    title_suffix=session_row_suffix(
                        status,
                        worker_count=len(children),
                        working_since=working_since.get(status.agent_id),
                        unseen_done=status.agent_id in unseen_ids,
                    ),
                )
            )
            # Its running sub-agents roll up into ONE submenu row --
            # three sessions each spawning a dozen workers used to
            # balloon the top level; now the top level is main
            # sessions, and the workers are one hover away, every row
            # still a real click-to-jump item in the family's color.
            if children:
                menu.addItem_(
                    build_worker_rollup_item(
                        children, snapshot.collected_at, target, dot_color
                    )
                )
        # Sub-agents whose parent session isn't visible (rare: parent
        # aged out while workers run on) still get a rollup.
        for children in subagent_groups.values():
            if children:
                menu.addItem_(
                    build_worker_rollup_item(
                        children, snapshot.collected_at, target, None
                    )
                )

    focus_summary = (
        target.active_focus_summary()
        if callable(getattr(target, "active_focus_summary", None))
        else "No Focus is active."
    )
    if "\u2014" in focus_summary:
        # A Focus is active with a concrete effect -- say so where the
        # user is already looking.
        menu.addItem_(disabled_menu_item(f"Focus: {focus_summary}"))

    menu.addItem_(NSMenuItem.separatorItem())
    menu.addItem_(disabled_menu_item("Devices"))
    # A device whose writes are failing must say so HERE, not only in
    # a log file -- the ENOSPC freeze sat invisible for 13 minutes
    # while the lights played a stale program.
    device_errors = getattr(target, "device_errors", None) or {}
    if device_errors:
        known_names = {
            device.device_id: device.name
            for device in (target.status_bar_devices() or [])
        }
        for device_id, error in sorted(device_errors.items()):
            menu.addItem_(
                disabled_menu_item(
                    f"\u26a0 {known_names.get(device_id, device_id)}: "
                    f"not updating \u2014 {str(error)[:48]}"
                )
            )
    # Calibration/brightness profiles: three named slots, applied or
    # saved in two clicks from here.
    profiles_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Profiles", None, "")
    profiles_menu = NSMenu.alloc().init()
    # Manual enablement: with the default autoenablesItems, AppKit
    # re-enables every targeted item and "Apply" for an empty slot
    # becomes a clickable lie.
    profiles_menu.setAutoenablesItems_(False)
    saved_profiles = getattr(target, "settings", None)
    saved = saved_profiles.calibration_profiles if saved_profiles is not None else {}
    for slot in CALIBRATION_PROFILE_SLOTS:
        apply_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Apply {slot}", "applyCalibrationProfile:", ""
        )
        apply_item.setTarget_(target)
        apply_item.setRepresentedObject_(slot)
        apply_item.setEnabled_(slot in saved)
        profiles_menu.addItem_(apply_item)
    profiles_menu.addItem_(NSMenuItem.separatorItem())
    for slot in CALIBRATION_PROFILE_SLOTS:
        save_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Save Current as {slot}", "saveCalibrationProfile:", ""
        )
        save_item.setTarget_(target)
        save_item.setRepresentedObject_(slot)
        profiles_menu.addItem_(save_item)
    profiles_item.setSubmenu_(profiles_menu)
    menu.addItem_(profiles_item)
    # Timebox: the bar as an ambient countdown.
    timebox_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Timer", None, "")
    timebox_menu = NSMenu.alloc().init()
    timebox_menu.setAutoenablesItems_(False)
    for minutes in TIMEBOX_PRESET_MINUTES:
        start_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Start {minutes} Minutes", "startTimebox:", ""
        )
        start_item.setTarget_(target)
        start_item.setRepresentedObject_(minutes)
        timebox_menu.addItem_(start_item)
    if getattr(target, "timebox_ends_at", None) is not None and target.timebox_active():
        timebox_menu.addItem_(NSMenuItem.separatorItem())
        remaining_minutes = max(0, round((target.timebox_ends_at - time.monotonic()) / 60.0))
        stop_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Stop (~{remaining_minutes} min left)", "stopTimebox:", ""
        )
        stop_item.setTarget_(target)
        timebox_menu.addItem_(stop_item)
    timebox_item.setSubmenu_(timebox_menu)
    menu.addItem_(timebox_item)
    devices = target.status_bar_devices()
    if devices:
        for device in devices:
            menu.addItem_(build_device_menu_item(device, target))
    else:
        menu.addItem_(disabled_menu_item("No devices yet"))
        menu.addItem_(
            disabled_menu_item("Plug in a SidePulse, or add the Screen Bar below")
        )
    if SCREEN_BAR_FEATURE_ENABLED:
        virtual_toggle = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            (
                "Add Screen Bar"
                if not target.settings.virtual_status_device_enabled
                else "Remove Screen Bar"
            ),
            "toggleVirtualStatusDevice:",
            "",
        )
        virtual_toggle.setTarget_(target)
        menu.addItem_(virtual_toggle)

    menu.addItem_(NSMenuItem.separatorItem())
    keep_awake_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Keep Awake With Lid Closed", None, ""
    )
    keep_awake_menu = NSMenu.alloc().init()
    for policy in CLOSED_LID_AWAKE_CHOICES:
        keep_awake_menu.addItem_(build_closed_lid_awake_policy_item(policy, target))
    keep_awake_item.setSubmenu_(keep_awake_menu)
    menu.addItem_(keep_awake_item)
    if target.closed_lid_awake.last_error:
        # Errors stay inline where they can't be missed -- never tucked
        # into the submenu they originate from.
        menu.addItem_(disabled_menu_item(f"Sleep warning: {target.closed_lid_awake.last_error}"))

    menu.addItem_(NSMenuItem.separatorItem())
    setup = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Setup...",
        "openSetup:",
        "",
    )
    setup.setTarget_(target)
    menu.addItem_(setup)

    settings = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Settings...",
        "openSettings:",
        ",",
    )
    settings.setTarget_(target)
    menu.addItem_(settings)

    quiet_title = (
        "End Quiet Hour" if target_quiet_active(target) else "Quiet for an Hour"
    )
    quiet_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        quiet_title,
        "toggleQuietHour:",
        "",
    )
    quiet_item.setTarget_(target)
    quiet_item.setState_(1 if target_quiet_active(target) else 0)
    menu.addItem_(quiet_item)
    completed_rows = [
        status
        for status in statuses
        if status.mode == AgentMode.COMPLETED
        and status.agent_id not in getattr(target, "cleared_session_ids", set())
    ]
    if completed_rows:
        clear_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Clear Finished ({len(completed_rows)})",
            "clearFinished:",
            "",
        )
        clear_item.setTarget_(target)
        menu.addItem_(clear_item)

    tip = daily_tip(getattr(target, "settings", None))
    if tip is not None:
        menu.addItem_(NSMenuItem.separatorItem())
        tip_text, tip_pane, tip_anchor = tip
        tip_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Tip: {tip_text}", None, ""
        )
        tip_menu = NSMenu.alloc().init()
        if tip_pane:
            show_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Show Me", "openTipPane:", ""
            )
            show_item.setTarget_(target)
            show_item.setRepresentedObject_(
                {"pane": tip_pane, "anchor": tip_anchor or "", "text": tip_text}
            )
            tip_menu.addItem_(show_item)
        dismiss_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Dismiss This Tip", "dismissTip:", ""
        )
        dismiss_item.setTarget_(target)
        dismiss_item.setRepresentedObject_(tip_text)
        tip_menu.addItem_(dismiss_item)
        off_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Turn Off Tips", "disableTips:", ""
        )
        off_item.setTarget_(target)
        tip_menu.addItem_(off_item)
        tip_item.setSubmenu_(tip_menu)
        menu.addItem_(tip_item)

    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit",
        "quit:",
        "q",
    )
    quit_item.setTarget_(target)
    menu.addItem_(quit_item)

    return menu


def build_closed_lid_awake_policy_item(policy: str, target: StatusBarController) -> NSMenuItem:
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        CLOSED_LID_AWAKE_LABELS[policy],
        "setClosedLidAwakePolicy:",
        "",
    )
    item.setTarget_(target)
    item.setRepresentedObject_(policy)
    item.setState_(1 if target.settings.closed_lid_awake_policy == policy else 0)
    return item


def build_device_menu_item(device: StatusBarDevice, target: StatusBarController) -> NSMenuItem:
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(device.name, None, "")
    item.setState_(1 if device.connected else 0)
    submenu = NSMenu.alloc().init()

    agent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Agent Status",
        "setDeviceDisplayAgent:",
        "",
    )
    agent.setTarget_(target)
    agent.setRepresentedObject_(device.device_id)
    agent.setState_(1 if device.display == LED_DISPLAY_AGENT else 0)
    submenu.addItem_(agent)

    battery = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Battery Level",
        "setDeviceDisplayBattery:",
        "",
    )
    battery.setTarget_(target)
    battery.setRepresentedObject_(device.device_id)
    battery.setState_(1 if device.display == LED_DISPLAY_BATTERY else 0)
    submenu.addItem_(battery)

    submenu.addItem_(NSMenuItem.separatorItem())
    submenu.addItem_(disabled_menu_item(f"Brightness {brightness_percent(device.brightness)}%"))
    submenu.addItem_(build_brightness_slider_item(device, target))
    auto_brightness = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Auto-Brightness (matches screen)",
        "toggleDeviceAutoBrightness:",
        "",
    )
    auto_brightness.setTarget_(target)
    auto_brightness.setRepresentedObject_(device.device_id)
    auto_brightness.setState_(1 if device.auto_brightness_enabled else 0)
    submenu.addItem_(auto_brightness)

    submenu.addItem_(NSMenuItem.separatorItem())
    red_gain, green_gain, blue_gain = device.channel_gains
    submenu.addItem_(
        disabled_menu_item(
            f"Color Calibration -- R{round(red_gain * 100)}% "
            f"G{round(green_gain * 100)}% B{round(blue_gain * 100)}%"
        )
    )
    submenu.addItem_(
        build_channel_gain_slider_item(device, target, "Red", red_gain, "setDeviceRedGain:")
    )
    submenu.addItem_(
        build_channel_gain_slider_item(device, target, "Green", green_gain, "setDeviceGreenGain:")
    )
    submenu.addItem_(
        build_channel_gain_slider_item(device, target, "Blue", blue_gain, "setDeviceBlueGain:")
    )
    reset_calibration = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Reset Calibration",
        "resetDeviceColorCalibration:",
        "",
    )
    reset_calibration.setTarget_(target)
    reset_calibration.setRepresentedObject_(device.device_id)
    submenu.addItem_(reset_calibration)

    if not device.connected:
        submenu.addItem_(NSMenuItem.separatorItem())
        submenu.addItem_(disabled_menu_item("Not connected"))
        remove = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Remove",
            "removeRememberedDevice:",
            "",
        )
        remove.setTarget_(target)
        remove.setRepresentedObject_(device.device_id)
        submenu.addItem_(remove)

    item.setSubmenu_(submenu)
    return item


def build_brightness_slider_item(
    device: StatusBarDevice,
    target: StatusBarController,
) -> NSMenuItem:
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
    view = NSView.alloc().initWithFrame_(((0, 0), (230, 34)))
    slider = NSSlider.alloc().initWithFrame_(((14, 6), (202, 22)))
    slider.setMinValue_(0.0)
    slider.setMaxValue_(255.0)
    slider.setDoubleValue_(float(normalize_brightness(device.brightness)))
    slider.setContinuous_(False)
    slider.setTarget_(target)
    slider.setAction_("setDeviceBrightness:")
    slider.setIdentifier_(device.device_id)
    view.addSubview_(slider)
    item.setView_(view)
    return item


def build_channel_gain_slider_item(
    device: StatusBarDevice,
    target: StatusBarController,
    label: str,
    current_gain: float,
    action_selector: str,
) -> NSMenuItem:
    """A compact labeled slider for one RGB channel's write-time gain
    correction (see led_status.apply_channel_gain_to_program) -- same
    layout convention as build_brightness_slider_item, just with a short
    channel-name label since three of these stack in the same submenu."""
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
    view = NSView.alloc().initWithFrame_(((0, 0), (230, 34)))
    label_field = NSTextField.alloc().initWithFrame_(((14, 8), (40, 18)))
    label_field.setStringValue_(label)
    label_field.setBezeled_(False)
    label_field.setDrawsBackground_(False)
    label_field.setEditable_(False)
    label_field.setSelectable_(False)
    label_field.setFont_(NSFont.systemFontOfSize_(11))
    view.addSubview_(label_field)
    slider = NSSlider.alloc().initWithFrame_(((54, 6), (162, 22)))
    slider.setMinValue_(MIN_CHANNEL_GAIN * 100.0)
    slider.setMaxValue_(MAX_CHANNEL_GAIN * 100.0)
    slider.setDoubleValue_(float(current_gain) * 100.0)
    slider.setContinuous_(False)
    slider.setTarget_(target)
    slider.setAction_(action_selector)
    slider.setIdentifier_(device.device_id)
    view.addSubview_(slider)
    item.setView_(view)
    return item


SETUP_DEMO_WIDTH = 420.0
SETUP_DEMO_HEIGHT = 30.0


def _setup_toggle_row(title: str, help_text: str | None = None):
    """A "Title ... status [switch]" row for the welcome window -- the
    switch carries no action (its state is read when Set Up runs), the
    status label reports installed-ness."""
    switch = NSSwitch.alloc().init()
    status = native_ui.make_label("", secondary=True, size=12.0)
    cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    cluster.addArrangedSubview_(status)
    cluster.addArrangedSubview_(switch)
    row = native_ui.make_row(title, cluster, help_text=help_text)
    return row, switch, status


def build_setup_window(target: StatusBarController) -> NSWindow:
    """The welcome window: what SidePulse is (shown live, not described),
    which agents to connect, and the Mac-level installs -- a first-run
    moment that should feel like the product, not a permissions form."""
    width, height = 680, 800
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (width, height)),
        style,
        NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("Welcome to SidePulse")
    window.setReleasedWhenClosed_(False)
    window.center()

    root = NSView.alloc().init()
    window.setContentView_(root)
    root.setTranslatesAutoresizingMaskIntoConstraints_(False)
    root.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    root.heightAnchor().constraintEqualToConstant_(height).setActive_(True)

    stack = native_ui.make_fill_stack(spacing=14.0)
    root.addSubview_(stack)
    NSLayoutConstraint.activateConstraints_(
        [
            stack.topAnchor().constraintEqualToAnchor_constant_(root.topAnchor(), 28.0),
            stack.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 28.0),
            stack.trailingAnchor().constraintEqualToAnchor_constant_(root.trailingAnchor(), -28.0),
        ]
    )

    # Hero: the product introduces itself by DOING the thing -- a live
    # LED strip playing the full-team demo, not a paragraph about LEDs.
    title = native_ui.make_label("SidePulse", size=27.0, bold=True)
    hero_title_holder = native_ui.make_stack(orientation="vertical", spacing=0.0)
    hero_title_holder.addArrangedSubview_(title)
    stack.addArrangedSubview_(hero_title_holder)
    subtitle = native_ui.make_label("Your agents, at a glance — as light.", secondary=True, size=14.0)
    subtitle_holder = native_ui.make_stack(orientation="vertical", spacing=0.0)
    subtitle_holder.addArrangedSubview_(subtitle)
    stack.addArrangedSubview_(subtitle_holder)

    demo_container = native_ui.make_fixed_area(SETUP_DEMO_WIDTH, SETUP_DEMO_HEIGHT)
    demo_view = VirtualLedView.alloc().initWithFrame_(((0.0, 0.0), (SETUP_DEMO_WIDTH, SETUP_DEMO_HEIGHT)))
    demo_view.setHasNotch_(False)
    demo_colors = getattr(getattr(target, "settings", None), "colors", None) or ColorSettings.defaults()
    _, demo_program = program_for_snapshot(
        colors_module.preview_statuses_for_scenario(colors_module.PREVIEW_SCENARIO_FULL_TEAM),
        led_count=8,
        colors=demo_colors,
        brightness=255,
    )
    demo_view.setProgram_startedAt_(demo_program, time.monotonic())
    demo_container.addSubview_(demo_view)
    stack.addArrangedSubview_(demo_container)

    # Connect Your Agents: the same contextual one-action rows the
    # Settings Agents pane uses, so first-run and settings agree.
    agents_outer, agents_inner = native_ui.make_card("Connect Your Agents")
    setup_fields: dict[str, object] = {"demo_view": demo_view}
    setup_buttons: dict[str, object] = {}
    for index, provider in enumerate(HOOK_PROVIDERS):
        status_label = native_ui.make_label("", secondary=True, size=12.0)
        install_button = native_ui.make_button("Install", target, f"install{provider.title()}Hooks:")
        cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
        cluster.addArrangedSubview_(status_label)
        cluster.addArrangedSubview_(install_button)
        agents_inner.addArrangedSubview_(native_ui.make_row(provider_spec(provider).label, cluster))
        if index < len(HOOK_PROVIDERS) - 1:
            native_ui.add_separator(agents_inner)
        setup_fields[f"setup_{provider}_status"] = status_label
        setup_buttons[f"setup_{provider}_install"] = install_button
    stack.addArrangedSubview_(agents_outer)

    # Set Up This Mac: the three system-level installs as switch rows.
    mac_outer, mac_inner = native_ui.make_card("Set Up This Mac")
    launch_row, launch, launch_status = _setup_toggle_row(
        "Run at Login", "Start the menu-bar app automatically."
    )
    mac_inner.addArrangedSubview_(launch_row)
    eject_row, eject_guard, eject_status = _setup_toggle_row(
        SD_EJECT_GUARD_DISPLAY_NAME,
        "Keep SidePulse Pro/SidePulse Dot available after sleep.",
    )
    mac_inner.addArrangedSubview_(eject_row)
    sleep_row, sleep_helper, sleep_status = _setup_toggle_row(
        "Closed-Lid Sleep Prevention",
        "Opens a one-time administrator setup in Terminal.",
    )
    mac_inner.addArrangedSubview_(sleep_row)
    # Full Disk Access unlocks Focus features (dimming per Focus mode).
    # It can't be granted programmatically -- the row states the status
    # and hands the user the Privacy pane.
    fda_status = native_ui.make_label("", secondary=True, size=12.0)
    fda_button = native_ui.make_button("Grant…", target, "openFullDiskAccessSettings:")
    fda_reveal = native_ui.make_button(
        "Reveal SidePulse" if running_inside_bundle() else "Reveal Program",
        target,
        "revealFocusBinaryInFinder:",
    )
    fda_cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    fda_cluster.addArrangedSubview_(fda_status)
    fda_cluster.addArrangedSubview_(fda_reveal)
    fda_cluster.addArrangedSubview_(fda_button)
    mac_inner.addArrangedSubview_(
        native_ui.make_row(
            "Focus Detection (Full Disk Access)",
            fda_cluster,
            help_text=(
                "Lets SidePulse see which macOS Focus is active, so LEDs "
                "can dim or turn off per Focus. Grant\u2026 opens the "
                "Privacy pane; click +, then pick the app Reveal shows "
                "you (macOS won't list it by itself). The full "
                "walkthrough lives in Settings > Focus."
            ),
        )
    )
    eject_uninstall = native_ui.make_button("Uninstall", target, "uninstallSdEjectGuard:")
    eject_uninstall.setHidden_(True)
    uninstall_cluster = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    uninstall_cluster.addArrangedSubview_(eject_uninstall)
    uninstall_cluster.addArrangedSubview_(native_ui.make_hspacer())
    mac_inner.addArrangedSubview_(uninstall_cluster)
    stack.addArrangedSubview_(mac_outer)

    # Footer: transient message + the two actions, pinned to the bottom.
    message = native_ui.make_label("", secondary=True, size=12.0)
    skip_button = native_ui.make_button("Skip for Now", target, "skipFirstLaunchSetup:")
    setup_button = native_ui.make_button("Set Up", target, "runFirstLaunchSetup:")
    setup_button.setKeyEquivalent_("\r")
    footer = native_ui.make_stack(orientation="horizontal", spacing=native_ui.SPACE_S)
    footer.addArrangedSubview_(message)
    footer.addArrangedSubview_(native_ui.make_hspacer())
    footer.addArrangedSubview_(skip_button)
    footer.addArrangedSubview_(setup_button)
    root.addSubview_(footer)
    NSLayoutConstraint.activateConstraints_(
        [
            footer.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 28.0),
            footer.trailingAnchor().constraintEqualToAnchor_constant_(root.trailingAnchor(), -28.0),
            footer.bottomAnchor().constraintEqualToAnchor_constant_(root.bottomAnchor(), -20.0),
        ]
    )

    setup_fields.update(
        {
            "launch_status": launch_status,
            "eject_status": eject_status,
            "sleep_status": sleep_status,
            "fda_status": fda_status,
            "message": message,
        }
    )
    setup_buttons["fda_grant"] = fda_button
    setup_buttons.update(
        {
            "launch": launch,
            "eject_guard": eject_guard,
            "eject_guard_uninstall": eject_uninstall,
            "sleep_helper": sleep_helper,
        }
    )
    # Recommended defaults, set ONCE here -- refresh_setup_window only
    # touches enablement, never the checked state, so a user's opt-out
    # survives every refresh (each provider Install click triggers one).
    for key in ("launch", "eject_guard", "sleep_helper"):
        set_checkbox_state(setup_buttons[key], True)
    target.setup_fields = setup_fields
    target.setup_buttons = setup_buttons
    return window


def choose_debug_export_path(format_name: str) -> Path | None:
    extension = "csv" if format_name == "csv" else "html"
    panel = NSSavePanel.savePanel()
    panel.setTitle_("Export SidePulse Debug Log")
    panel.setNameFieldStringValue_(f"sidepulse-agent-debug.{extension}")
    if hasattr(panel, "setAllowedFileTypes_"):
        panel.setAllowedFileTypes_([extension])
    if panel.runModal() != 1:
        return None
    url = panel.URL()
    if url is None:
        return None
    return Path(str(url.path()))


def debug_log_status_text() -> str:
    path = default_status_audit_log_path()
    try:
        size = path.stat().st_size
    except OSError:
        return f"Log: {path} (empty)"
    return f"Log: {path} ({format_byte_count(size)})"


def format_byte_count(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


# --- Settings window: sidebar + detail pane ---------------------------
#
# Rebuilt from a single hand-positioned scrolling column into a System-
# Settings-style shell: a translucent sidebar of categories next to a
# detail pane that shows only the one selected. Each pane is built from
# native_ui's NSStackView-based cards/rows and scrolls independently, so
# there's no shared "document height" formula left to keep in sync by
# hand -- the recurring source of every layout bug this window has had.

# Seven panes, each earning its slot: Agents = hooks + transcript
# fallback + session openers (all "how SidePulse talks to your agents"),
# Power = closed-lid awake + battery (both power management). The old
# nine-pane split left several panes holding two controls in an
# otherwise empty window.
# "header:" rows are unselectable section labels -- the sidebar had
# grown to a 12-item flat list nobody could scan.
SETTINGS_SIDEBAR_ITEMS: tuple[tuple[str, str], ...] = (
    ("header:you", "YOU"),
    ("profile", "Profile"),
    ("header:setup", "SET UP"),
    ("devices", "Devices"),
    ("agents", "Agents"),
    ("header:looks", "LOOKS"),
    ("color_studio", "Color Studio"),
    ("colors_screen_bar", "Screen Bar"),
    ("animations", "Animations"),
    ("header:behavior", "BEHAVIOR"),
    ("led_behavior", "Signals"),
    ("focus", "Focus"),
    ("power", "Power"),
    ("header:advanced", "ADVANCED"),
    ("debug", "Debug"),
)
DEFAULT_SETTINGS_PANE = "profile"
SIDEBAR_ICONS: dict[str, str] = {
    "profile": "person.crop.circle",
    "devices": "cpu",
    "agents": "sparkles",
    "color_studio": "paintpalette",
    "colors_screen_bar": "menubar.rectangle",
    "animations": "film",
    "led_behavior": "bell.badge",
    "focus": "moon",
    "power": "bolt",
    "debug": "wrench.and.screwdriver",
}


class UsageGraphView(NSView):
    """Seven day bars (Claude spend in Claude's brand orange, Codex
    tokens in OpenAI green, normalized per series) over an hourly
    session sparkline for today. Flat, no dependencies, redrawn only
    when data changes."""

    def initWithFrame_(self, frame):
        self = objc.super(UsageGraphView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.day_bars = []
        self.hourly = []
        return self

    def setData_hourly_(self, day_bars, hourly):
        self.day_bars = list(day_bars or [])
        self.hourly = list(hourly or [])
        self.setNeedsDisplay_(True)

    def isFlipped(self):
        return False

    def drawRect_(self, _rect):
        bounds = self.bounds().size
        width, height = bounds.width, bounds.height
        if width <= 0 or not self.day_bars:
            return
        spark_height = 24.0
        label_height = 14.0
        bars_bottom = spark_height + label_height + 6.0
        bars_height = max(10.0, height - bars_bottom - 16.0)
        count = len(self.day_bars)
        slot = width / count
        max_cost = max((bar["claude_cost"] for bar in self.day_bars), default=0.0) or 1.0
        max_tokens = max((bar["codex_tokens"] for bar in self.day_bars), default=0) or 1
        claude_color = nscolor_from_hex("#D97757")
        codex_color = nscolor_from_hex("#10A37F")
        label_attrs = {
            NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
        }
        # Baseline hairline (the T3/CodexBar look: bars sit ON a line).
        NSColor.tertiaryLabelColor().colorWithAlphaComponent_(0.35).setFill()
        NSBezierPath.bezierPathWithRect_(
            ((0.0, bars_bottom - 1.0), (width, 1.0))
        ).fill()
        # Series maxima as tiny axis captions in the top corners.
        cost_caption = NSString.stringWithString_(f"${max_cost:,.0f}")
        cost_caption.drawAtPoint_withAttributes_((2.0, height - 13.0), label_attrs)
        token_caption = NSString.stringWithString_(
            f"{max_tokens / 1_000_000_000:.1f}B tok"
            if max_tokens >= 1_000_000_000
            else f"{max_tokens / 1_000_000:.0f}M tok"
        )
        token_size = token_caption.sizeWithAttributes_(label_attrs)
        token_caption.drawAtPoint_withAttributes_(
            (width - token_size.width - 2.0, height - 13.0), label_attrs
        )
        # Bars: side-by-side down to ~4pt slots, overlaid single-pixel
        # columns below that -- a YEAR must render, not vanish (the old
        # half-minus-one math went NEGATIVE at 365 bars and drew nothing).
        paired = slot >= 4.0
        radius = min(2.0, slot * 0.2)
        for index, bar in enumerate(self.day_bars):
            left = slot * index
            claude_h = bars_height * (bar["claude_cost"] / max_cost)
            codex_h = bars_height * (bar["codex_tokens"] / max_tokens)
            if paired:
                series_width = max(1.0, (slot * 0.72) / 2.0)
                gap = min(1.0, slot * 0.06)
                start = left + (slot - (series_width * 2.0 + gap)) / 2.0
                if bar["claude_cost"] > 0:
                    claude_color.setFill()
                    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                        ((start, bars_bottom), (series_width, max(1.5, claude_h))),
                        radius,
                        radius,
                    ).fill()
                if bar["codex_tokens"] > 0:
                    codex_color.setFill()
                    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                        (
                            (start + series_width + gap, bars_bottom),
                            (series_width, max(1.5, codex_h)),
                        ),
                        radius,
                        radius,
                    ).fill()
            else:
                column = max(0.8, slot * 0.9)
                if bar["codex_tokens"] > 0:
                    codex_color.colorWithAlphaComponent_(0.75).setFill()
                    NSBezierPath.bezierPathWithRect_(
                        ((left, bars_bottom), (column, max(1.0, codex_h)))
                    ).fill()
                if bar["claude_cost"] > 0:
                    claude_color.setFill()
                    NSBezierPath.bezierPathWithRect_(
                        ((left, bars_bottom), (column, max(1.0, claude_h)))
                    ).fill()
            label_text = bar.get("label", "")
            if label_text:
                label = NSString.stringWithString_(label_text)
                size = label.sizeWithAttributes_(label_attrs)
                label.drawAtPoint_withAttributes_(
                    (
                        min(max(0.0, left + slot / 2.0 - size.width / 2.0), width - size.width),
                        spark_height + 2.0,
                    ),
                    label_attrs,
                )
        if self.hourly:
            peak = max(self.hourly) or 1
            step = width / len(self.hourly)
            NSColor.tertiaryLabelColor().setFill()
            for hour, value in enumerate(self.hourly):
                if value <= 0:
                    continue
                bar_h = max(1.5, (spark_height - 4.0) * value / peak)
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    ((step * hour + 1.0, 0.0), (max(1.5, step - 2.0), bar_h)),
                    1.0,
                    1.0,
                ).fill()


def add_color_swatch(parent, hex_color: str, x: int, y: int, target, selector: str, represented: dict):
    button = NSButton.alloc().initWithFrame_(((x, y), (COLOR_SWATCH_SIZE, COLOR_SWATCH_SIZE)))
    button.setTitle_("")
    button.setBordered_(False)
    button.setTarget_(target)
    button.setAction_(selector)
    button.setRepresentedObject_(dict(represented))
    try:
        button.setWantsLayer_(True)
        layer = button.layer()
        layer.setBackgroundColor_(nscolor_from_hex(hex_color).CGColor())
        layer.setCornerRadius_(COLOR_SWATCH_SIZE / 2.0)
        layer.setBorderWidth_(0.0)
    except Exception:
        pass
    parent.addSubview_(button)
    return button


def add_custom_color_swatch(parent, x: int, y: int, target, selector: str, represented: dict):
    button = NSButton.alloc().initWithFrame_(((x, y), (COLOR_SWATCH_SIZE, COLOR_SWATCH_SIZE)))
    button.setTitle_("+")
    button.setBordered_(False)
    button.setTarget_(target)
    button.setAction_(selector)
    button.setRepresentedObject_(dict(represented))
    try:
        button.setWantsLayer_(True)
        layer = button.layer()
        layer.setBackgroundColor_(NSColor.controlColor().CGColor())
        layer.setCornerRadius_(COLOR_SWATCH_SIZE / 2.0)
        layer.setBorderWidth_(1.0)
        layer.setBorderColor_(NSColor.separatorColor().CGColor())
    except Exception:
        pass
    parent.addSubview_(button)
    return button


def set_swatch_selected(button, selected: bool) -> None:
    try:
        button.setWantsLayer_(True)
        layer = button.layer()
        layer.setBorderWidth_(2.5 if selected else 0.0)
        layer.setBorderColor_(NSColor.controlAccentColor().CGColor())
    except Exception:
        pass


def nscolor_from_hex(hex_value: str) -> NSColor:
    red, green, blue = colors_module.hex_to_rgb(colors_module.normalize_hex(hex_value, "#000000"))
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(red / 255.0, green / 255.0, blue / 255.0, 1.0)


def hex_from_nscolor(nscolor) -> str:
    try:
        rgb = nscolor.colorUsingColorSpace_(NSColor.sRGBColorSpace())
    except Exception:
        rgb = nscolor
    # colorUsingColorSpace_ returns None (no exception) for catalog and
    # pattern colors -- fall back to the original rather than crashing
    # the color-panel action mid-drag.
    if rgb is None:
        rgb = nscolor
    return colors_module.rgb_to_hex(
        (rgb.redComponent() * 255.0, rgb.greenComponent() * 255.0, rgb.blueComponent() * 255.0)
    )


def make_blend_mode_popup(target):
    popup = native_ui.make_popup_button(target, "setBlendMode:")
    for mode in BLEND_MODE_CHOICES:
        popup.addItemWithTitle_(BLEND_MODE_LABELS[mode])
        popup.lastItem().setRepresentedObject_({"blend_mode": mode})
    return popup


def make_color_preset_popup(target):
    popup = native_ui.make_popup_button(target, "setColorPreset:")
    popup.addItemWithTitle_(PRESET_LABELS[PRESET_CUSTOM])
    popup.lastItem().setRepresentedObject_({"preset": PRESET_CUSTOM})
    for preset in PRESET_CHOICES:
        popup.addItemWithTitle_(PRESET_LABELS[preset])
        item = popup.lastItem()
        item.setRepresentedObject_({"preset": preset})
        item.setToolTip_(PRESET_DESCRIPTIONS[preset])
    return popup


def select_popup_item(popup, key: str, value) -> None:
    """Select the popup row whose representedObject dict carries
    payload[key] == value -- the one sync idiom behind every
    settings-window popup refresh."""
    for index in range(popup.numberOfItems()):
        payload = popup.itemAtIndex_(index).representedObject()
        if isinstance(payload, dict) and payload.get(key) == value:
            popup.selectItemAtIndex_(index)
            return


def select_color_preset(popup, preset: str) -> None:
    select_popup_item(popup, "preset", preset)


def select_blend_mode(popup, blend_mode: str) -> None:
    select_popup_item(popup, "blend_mode", blend_mode)


def make_preview_scenario_popup(target):
    popup = native_ui.make_popup_button(target, "setPreviewScenario:")
    for scenario in colors_module.PREVIEW_SCENARIO_CHOICES:
        popup.addItemWithTitle_(colors_module.PREVIEW_SCENARIO_LABELS[scenario])
        popup.lastItem().setRepresentedObject_({"scenario": scenario})
    return popup


def select_preview_scenario(popup, scenario: str) -> None:
    select_popup_item(popup, "scenario", scenario)


ANIMATION_STYLE_DISPLAY_LABELS: dict[str, str] = {
    "pulse": "Pulse (breathe)",
    "roll": "Roll (chase)",
    "solid": "Solid (no animation)",
    "blink": "Blink (hard on/off)",
}


def make_closed_lid_awake_policy_popup(target):
    """A Settings-window popup for the same policy the status-bar menu's
    build_closed_lid_awake_policy_item radio-style items control -- a
    separate control (not reused menu items) since it lives in a
    completely different part of the UI, but both write the same
    settings.closed_lid_awake_policy and take effect immediately either
    way."""
    popup = native_ui.make_popup_button(target, "setClosedLidAwakePolicyFromPopup:")
    for policy in CLOSED_LID_AWAKE_CHOICES:
        popup.addItemWithTitle_(CLOSED_LID_AWAKE_LABELS[policy])
        popup.lastItem().setRepresentedObject_({"policy": policy})
    return popup


def select_closed_lid_awake_policy(popup, policy: str) -> None:
    select_popup_item(popup, "policy", policy)


def add_preview_dot(parent, x: int, y: int):
    """A purely decorative colored circle. Deliberately a plain NSView, not
    a disabled NSButton -- a disabled NSControl's cell dims/grays its own
    drawing regardless of a custom layer background color, which made the
    first version of this effectively invisible. A plain layer-backed NSView
    has no control state to fight with."""
    dot = NSView.alloc().initWithFrame_(((x, y), (COLOR_SWATCH_SIZE, COLOR_SWATCH_SIZE)))
    dot.setWantsLayer_(True)
    layer = dot.layer()
    layer.setBackgroundColor_(NSColor.blackColor().CGColor())
    layer.setCornerRadius_(COLOR_SWATCH_SIZE / 2.0)
    layer.setBorderWidth_(1.0)
    layer.setBorderColor_(NSColor.separatorColor().CGColor())
    parent.addSubview_(dot)
    return dot


def set_preview_dot_color(dot, hex_color: str) -> None:
    try:
        dot.setWantsLayer_(True)
        dot.layer().setBackgroundColor_(nscolor_from_hex(hex_color).CGColor())
    except Exception:
        pass


def set_preview_dot_rgb(dot, red: int, green: int, blue: int) -> None:
    """Like set_preview_dot_color, but takes raw 0-255 ints -- the shape the
    WASM controller's step() returns, for the animated preview."""
    try:
        dot.setWantsLayer_(True)
        color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
            max(0, min(255, red)) / 255.0,
            max(0, min(255, green)) / 255.0,
            max(0, min(255, blue)) / 255.0,
            1.0,
        )
        dot.layer().setBackgroundColor_(color.CGColor())
    except Exception:
        pass


def colors_legend_text(statuses, *, is_live: bool, prefix: str | None = None) -> str:
    explicit_prefix = prefix is not None
    if prefix is None:
        prefix = "" if is_live else "Demo: "
    if not statuses:
        # A caller-supplied prefix (a named scenario) already says everything
        # there is to say about an empty roster -- appending the generic
        # "Idle -- no active agents" on top just repeats itself.
        if explicit_prefix and prefix:
            return prefix.rstrip(": ")
        return f"{prefix}Idle -- no active agents" if prefix else "Idle -- no active agents"
    parts = []
    for status in statuses:
        name = status.display_name or status.provider
        # Session-id fragments like "wtf (b5b4fbe4)" are debug detail --
        # a color-preview legend needs the agent and its mode, not a hash.
        name = re.sub(r"\s*\([0-9a-f]{6,}\)", "", str(name))
        parts.append(f"{name} ({MODE_LABELS.get(status.mode, status.mode.value)})")
    return prefix + " · ".join(parts)


def add_label(parent, text: str, x: int, y: int, width: int, height: int):
    label = NSTextField.alloc().initWithFrame_(((x, y), (width, height)))
    label.setStringValue_(text)
    label.setBezeled_(False)
    label.setDrawsBackground_(False)
    label.setEditable_(False)
    label.setSelectable_(False)
    parent.addSubview_(label)
    return label


def add_button(
    parent,
    title: str,
    x: int,
    y: int,
    width: int,
    height: int,
    target: StatusBarController,
    selector: str,
):
    button = NSButton.alloc().initWithFrame_(((x, y), (width, height)))
    button.setTitle_(title)
    button.setBezelStyle_(NSBezelStyleRounded)
    button.setTarget_(target)
    button.setAction_(selector)
    parent.addSubview_(button)
    return button


def add_checkbox(
    parent,
    title: str,
    x: int,
    y: int,
    width: int,
    height: int,
    target: StatusBarController | None,
    selector: str,
):
    checkbox = NSButton.alloc().initWithFrame_(((x, y), (width, height)))
    checkbox.setButtonType_(NSButtonTypeSwitch)
    checkbox.setTitle_(title)
    if target is not None:
        checkbox.setTarget_(target)
    if selector:
        checkbox.setAction_(selector)
    parent.addSubview_(checkbox)
    return checkbox


def make_provider_opener_popup(provider: str, target):
    popup = native_ui.make_popup_button(target, "setProviderOpenPreference:")
    for action in provider_open_actions(provider):
        popup.addItemWithTitle_(provider_open_action_label(provider, action))
        popup.lastItem().setRepresentedObject_(
            {"provider": provider, "action": action}
        )
    return popup


def provider_open_actions(provider: str) -> tuple[str, ...]:
    if provider == "claude":
        return (SESSION_OPEN_VSCODE, SESSION_OPEN_APP, SESSION_OPEN_TERMINAL)
    if provider == "codex":
        return (SESSION_OPEN_APP, SESSION_OPEN_TERMINAL)
    return (SESSION_OPEN_TERMINAL,)


def default_provider_open_action(provider: str) -> str:
    if provider == "claude":
        return SESSION_OPEN_VSCODE
    if provider == "codex":
        return SESSION_OPEN_APP
    return SESSION_OPEN_TERMINAL


def provider_open_action_label(provider: str, action: str) -> str:
    if action == SESSION_OPEN_VSCODE:
        return "VS Code"
    if action == SESSION_OPEN_TERMINAL:
        return "Terminal"
    return {"codex": "Codex", "claude": "Claude"}.get(provider, "App")


def select_popup_action(popup, action: str) -> None:
    select_popup_item(popup, "action", action)


def add_slider(
    parent,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    min_value: float,
    max_value: float,
    value: float,
    target: StatusBarController,
    action: str,
    identifier: str | None = None,
):
    """A plain NSSlider placed directly in a window's content view --
    unlike build_brightness_slider_item/build_channel_gain_slider_item,
    which wrap a slider in an NSView because NSMenuItem requires a custom
    view for anything beyond a title+action, a normal window content view
    can host the control directly. identifier is set so the existing
    setDeviceBrightness:/setDeviceRedGain:/etc. IBActions (which read
    sender.identifier() for the device id) work unmodified regardless of
    which container the slider lives in.
    """
    slider = NSSlider.alloc().initWithFrame_(((x, y), (width, height)))
    slider.setMinValue_(min_value)
    slider.setMaxValue_(max_value)
    slider.setDoubleValue_(value)
    slider.setContinuous_(False)
    slider.setTarget_(target)
    slider.setAction_(action)
    if identifier is not None:
        slider.setIdentifier_(identifier)
    parent.addSubview_(slider)
    return slider


def add_editable_field(parent, text: str, x: int, y: int, width: int, height: int):
    field = NSTextField.alloc().initWithFrame_(((x, y), (width, height)))
    field.setStringValue_(text)
    field.setEditable_(True)
    field.setSelectable_(True)
    parent.addSubview_(field)
    return field


def add_text_view(parent, text: str, x: int, y: int, width: int, height: int):
    scroll = NSScrollView.alloc().initWithFrame_(((x, y), (width, height)))
    scroll.setHasVerticalScroller_(True)
    scroll.setHasHorizontalScroller_(False)
    text_view = NSTextView.alloc().initWithFrame_(((0, 0), (width, height)))
    text_view.setString_(text)
    text_view.setVerticallyResizable_(True)
    text_view.setHorizontallyResizable_(False)
    try:
        text_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11.0, 0.0))
    except Exception:
        pass
    scroll.setDocumentView_(text_view)
    parent.addSubview_(scroll)
    return text_view


def add_separator(parent, x: int, y: int, width: int):
    separator = NSTextField.alloc().initWithFrame_(((x, y), (width, 1)))
    separator.setStringValue_("")
    separator.setBezeled_(False)
    separator.setEditable_(False)
    separator.setDrawsBackground_(True)
    parent.addSubview_(separator)
    return separator


def set_field_value(field, value: str) -> None:
    if field is not None:
        field.setStringValue_(value)


def set_text_control_value(control, value: str) -> None:
    if control is None:
        return
    if hasattr(control, "setString_"):
        control.setString_(value)
    else:
        control.setStringValue_(value)


def text_control_value(control) -> str:
    if control is None:
        return ""
    if hasattr(control, "string"):
        return str(control.string())
    return str(control.stringValue())


def parse_percent_field(control) -> float | None:
    """Parses an editable field's text as a 0-100 percent into a 0.0-1.0
    fraction. Returns None (leave the setting unchanged) for blank/invalid
    input rather than silently coercing it to 0%."""
    text = text_control_value(control).strip().rstrip("%")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return max(0.0, min(100.0, value)) / 100.0


def parse_seconds_field(control) -> float | None:
    """Parses an editable field's text as seconds. Returns None (leave the
    setting unchanged) for blank/invalid input; the actual clamp to
    [MIN_CYCLE_SPEED_SECONDS, MAX_CYCLE_SPEED_SECONDS] happens in
    ColorSettings.with_cycle_speed()."""
    text = text_control_value(control).strip().rstrip("s")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def set_checkbox_state(button, enabled: bool) -> None:
    if button is not None:
        button.setState_(NSOnState if enabled else NSOffState)


def checkbox_is_on(button) -> bool:
    return button is not None and button.state() == NSOnState


def should_show_setup_window(settings) -> bool:
    return not getattr(settings, "setup_screen_completed", False)


def open_terminal_setup_command(command: str, *, filename: str = "install-sleep-helper.command") -> Path:
    state_dir = default_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    script_path = state_dir / filename
    script = "\n".join(
        [
            "#!/bin/zsh",
            "clear",
            'echo "SidePulse Sleep Prevention Setup"',
            'echo ""',
            'echo "macOS may ask for your administrator password."',
            'echo ""',
            command,
            "status=$?",
            'echo ""',
            'if [ "$status" -eq 0 ]; then',
            '  echo "Done. You can close this window."',
            "else",
            '  echo "Setup failed. Leave this window open if you want to inspect it."',
            "fi",
            'echo ""',
            'read -k 1 "?Press any key to close this window. "',
            "",
        ]
    )
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o700)
    subprocess.Popen(
        ["/usr/bin/open", str(script_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return script_path


def validate_lid_animation(animation: LedAnimationSetting) -> None:
    program = normalize_led_text(animation.program)
    validate_led_text(program)
    validate_led_text(apply_brightness(program, 1))
    normalize_animation_duration(animation.duration_seconds)


def program_for_lid_animation(
    animation: LedAnimationSetting,
    *,
    brightness: float = 255,
) -> str:
    validate_lid_animation(animation)
    return apply_brightness(normalize_led_text(animation.program), brightness)


def restore_led_display(target, token_value) -> None:
    try:
        token = int(str(token_value))
    except ValueError:
        token = target.led_animation_token
    if token != target.led_animation_token:
        return
    target.led_animation_until_monotonic = 0.0
    target.reset_led_controllers_for_display_change()
    if target.last_snapshot is not None:
        target.sync_leds(
            target.last_snapshot.aggregate.mode,
            target.last_battery_snapshot,
            target.active_led_display_kind(target.last_battery_snapshot),
        )
    else:
        target.refresh_(None)


def provider_hooks_installed(config: ProviderConfig) -> bool:
    return bool(config.exists and config.hook_events)


def hook_status_text(config: ProviderConfig) -> str:
    if provider_hooks_installed(config):
        event_count = len(config.hook_events)
        suffix = "event" if event_count == 1 else "events"
        return f"Installed · {event_count} {suffix}"
    return "Not installed"


def device_id_for_root(root: Path) -> str:
    return str(root.expanduser())


def device_connection_signature(
    devices: list[StatusBarDevice],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (
                device.device_id,
                str(device.target),
                device_mount_key(device.root),
            )
            for device in devices
            if device.connected
        )
    )


def device_mount_key(root: Path) -> str:
    try:
        stat = root.stat()
    except OSError:
        return "missing"
    return f"{stat.st_dev}:{stat.st_ino}"


def device_display_name(name: str) -> str:
    normalized = normalized_device_name(name)
    if "sidepulsedot" in normalized:
        return "SidePulse Dot"
    if "sidepulsepro" in normalized:
        return "SidePulse Pro"
    return name or "SidePulse Device"


def device_display_label(display: str) -> str:
    if display == LED_DISPLAY_BATTERY:
        return "Battery Level"
    return "Agent Status"


def disabled_menu_item(title: str) -> NSMenuItem:
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
    item.setEnabled_(False)
    return item


def disambiguate_device_names(devices: list[StatusBarDevice]) -> list[StatusBarDevice]:
    counts: dict[str, int] = {}
    for device in devices:
        counts[device.name] = counts.get(device.name, 0) + 1
    if all(count == 1 for count in counts.values()):
        return devices

    result: list[StatusBarDevice] = []
    for device in devices:
        if counts.get(device.name, 0) <= 1:
            result.append(device)
            continue
        suffix = duplicate_device_suffix(device)
        # dataclass_replace() carries over every field not explicitly
        # overridden (e.g. channel_gains) -- rebuilding from scratch here
        # previously dropped any field added after this call site was
        # first written (the same bug class fixed for DeviceDisplaySetting
        # in settings.py; see with_device_display there).
        result.append(
            dataclass_replace(
                device,
                name=f"{device.name} {suffix}" if suffix else device.name,
            )
        )
    return result


def duplicate_device_suffix(device: StatusBarDevice) -> str:
    root_name = device.root.name
    normalized_root = normalized_device_name(root_name)
    normalized_name = normalized_device_name(device.name)
    if normalized_root.startswith(normalized_name):
        suffix = root_name[len(device.name) :].strip()
        return suffix
    return root_name


def preferred_status_bar_device(candidates: list[DeviceCandidate]) -> DeviceCandidate:
    return sorted(candidates, key=status_bar_device_sort_key)[0]


def status_bar_device_sort_key(candidate: DeviceCandidate) -> tuple[int, str]:
    name = normalized_device_name(candidate.root.name)
    for index, hint in enumerate(STATUS_BAR_DEVICE_PRIORITY):
        if hint in name:
            return (index, candidate.root.name.lower())
    return (len(STATUS_BAR_DEVICE_PRIORITY), candidate.root.name.lower())


def deliver_macos_notification(
    title: str, body: str, user_info: dict | None = None
) -> None:
    """One banner through Notification Center. A plain module function
    so tests can patch the seam -- ObjC class selectors can't be
    mock-patched (the un-patch delattr fails on the ObjC class).
    ``user_info`` rides along so clicking the banner can jump to the
    session that posted it."""
    from AppKit import NSUserNotification, NSUserNotificationCenter

    notification = NSUserNotification.alloc().init()
    notification.setTitle_(title)
    notification.setInformativeText_(body)
    if user_info:
        notification.setUserInfo_(user_info)
    NSUserNotificationCenter.defaultUserNotificationCenter().deliverNotification_(
        notification
    )


def build_worker_rollup_item(children, collected_at, target, dot_color):
    """One indented '\u21b3 N workers' row whose submenu holds EVERY
    worker as a real session item -- busiest first, active count in
    the title."""
    active = [c for c in children if c.mode not in (AgentMode.COMPLETED,)]
    title = f"\u21b3 {len(children)} worker" + ("s" if len(children) != 1 else "")
    if active and len(active) != len(children):
        title += f" \u00b7 {len(active)} active"
    rollup = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
    rollup.setIndentationLevel_(1)
    submenu = NSMenu.alloc().init()
    submenu.setAutoenablesItems_(False)
    ordered = sorted(
        children,
        key=lambda c: (c.mode == AgentMode.COMPLETED, -c.updated_at.timestamp()),
    )
    for child in ordered:
        submenu.addItem_(
            build_session_menu_item(
                child, collected_at, target, identity_color=dot_color
            )
        )
    rollup.setSubmenu_(submenu)
    return rollup


def build_session_menu_item(
    status: AgentStatus,
    now: datetime,
    target: StatusBarController,
    *,
    width: float | None = None,
    identity_color: str | None = None,
    indent: bool = False,
    title_suffix: str = "",
) -> NSMenuItem:
    # Sub-agent rows carry an explicit elbow -- indentationLevel alone
    # disappears next to the state-spinner and app-icon stack.
    prefix = "\u21b3 " if indent else ""
    base_title = f"{native_session_menu_title(status)}{title_suffix}"
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"{prefix}{base_title}",
        "openSessionPrimary:",
        "",
    )
    item.setTarget_(target)
    item.setRepresentedObject_(status)
    if indent:
        item.setIndentationLevel_(1)
    if identity_color is not None:
        # A colored bullet leading the title -- the session's identity
        # hue, matching what the LEDs show for it.
        title = f"{prefix}\u25cf {base_title}"
        attributed = NSMutableAttributedString.alloc().initWithString_(title)
        attributed.addAttribute_value_range_(
            NSForegroundColorAttributeName,
            nscolor_from_hex(identity_color),
            (len(prefix), 1),
        )
        item.setAttributedTitle_(attributed)
    image = session_row_icon_for_status(status)
    if image is not None:
        item.setImage_(image)
    return item


def native_session_menu_title(status: AgentStatus) -> str:
    title, project = session_title_parts(status)
    parts = [title]
    if project:
        parts.append(project)
    # An em dash, not a run of spaces -- "title — project" reads as two
    # deliberate fields instead of accidental whitespace.
    return " — ".join(parts)


def build_session_options_menu(
    status: AgentStatus,
    now: datetime,
    target: StatusBarController,
) -> NSMenu:
    menu = NSMenu.alloc().init()
    menu.addItem_(disabled_menu_item(flatten_menu_title(menu_title_for_status(status, now))))
    menu.addItem_(disabled_menu_item(session_detail_for_status(status, now)))
    menu.addItem_(NSMenuItem.separatorItem())

    if getattr(target, "settings", None) is not None:
        selected = target.settings.session_open_action(status.provider, status.origin)
    else:
        selected = None
    selected = selected or default_session_open_action(status)
    for action in available_session_open_actions(status):
        add_session_open_action_item(
            menu,
            session_open_action_label(status, action),
            status,
            action,
            target,
            selected=action == selected,
        )

    # Identity color override: the spec's "auto-assigned, OVERRIDABLE".
    # Auto + the eight palette hues, each shown as its own colored dot.
    menu.addItem_(NSMenuItem.separatorItem())
    identity_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Identity Color", None, ""
    )
    identity_menu = NSMenu.alloc().init()
    current_override = None
    if getattr(target, "settings", None) is not None:
        current_override = target.settings.colors.session_color(status.agent_id)
    choices: list[tuple[str, str | None]] = [("Automatic", None)]
    choices.extend(
        (hex_color, hex_color) for hex_color in colors_module.IDENTITY_PALETTE
    )
    for label, hex_color in choices:
        choice = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            label if hex_color is None else f"● {label}",
            "setSessionIdentityColor:",
            "",
        )
        choice.setTarget_(target)
        choice.setRepresentedObject_({"agent_id": status.agent_id, "color": hex_color})
        if hex_color is not None:
            attributed = NSMutableAttributedString.alloc().initWithString_(f"● {label}")
            attributed.addAttribute_value_range_(
                NSForegroundColorAttributeName, nscolor_from_hex(hex_color), (0, 1)
            )
            choice.setAttributedTitle_(attributed)
        if (hex_color is None and current_override is None) or (
            hex_color is not None and current_override == hex_color
        ):
            choice.setState_(NSOnState)
        identity_menu.addItem_(choice)
    identity_item.setSubmenu_(identity_menu)
    menu.addItem_(identity_item)
    return menu


def add_session_open_action_item(
    menu: NSMenu,
    title: str,
    status: AgentStatus,
    action: str,
    target: StatusBarController,
    *,
    selected: bool,
) -> None:
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title,
        "openSessionWithAction:",
        "",
    )
    item.setTarget_(target)
    item.setRepresentedObject_({"status": status, "action": action})
    item.setState_(1 if selected else 0)
    menu.addItem_(item)


def session_row_icon_for_status(status: AgentStatus):
    status_icon = status_icon_for_status(status)
    origin_icon = session_origin_icon_for_status(status)
    if origin_icon is None:
        return status_icon
    if status_icon is None:
        return origin_icon
    return horizontal_icon_pair(status_icon, origin_icon)


def status_icon_for_status(status: AgentStatus):
    state = state_for_mode(status.mode)
    return image_for_symbol(state.symbol, state.label)


def session_origin_icon_for_status(status: AgentStatus):
    provider_icon = provider_icon_for_status(status)
    host_icon = host_icon_for_origin(status.origin)
    if host_icon is None:
        return provider_icon
    return composite_app_icons(host_icon, provider_icon)


def provider_icon_for_status(status: AgentStatus):
    return provider_icon_for_provider(status.provider)


def provider_icon_for_provider(provider: str):
    provider = provider.lower()
    if provider == "codex":
        for path in ("/Applications/Codex.app", "/Applications/ChatGPT.app"):
            image = app_icon(path)
            if image is not None:
                return image
        return image_for_symbol("sparkles", "Codex")
    if provider == "claude":
        image = app_icon("/Applications/Claude.app")
        if image is not None:
            return image
        return image_for_symbol("brain.head.profile", "Claude")
    if provider == "grok":
        image = app_icon("/Applications/Grok.app")
        if image is not None:
            return image
        return grok_badge_icon()
    return image_for_symbol("terminal", provider.title() or "Agent")


def host_icon_for_origin(origin: str | None):
    normalized = normalized_origin_text(origin)
    if not normalized:
        return None
    if "vs code" in normalized or "vscode" in normalized or "visual studio code" in normalized:
        return first_app_icon(
            (
                "/Applications/Visual Studio Code.app",
                "/Applications/Visual Studio Code - Insiders.app",
            )
        ) or image_for_symbol("chevron.left.forwardslash.chevron.right", "VS Code")
    if "cursor" in normalized:
        return first_app_icon(("/Applications/Cursor.app",)) or image_for_symbol(
            "cursorarrow",
            "Cursor",
        )
    if "windsurf" in normalized:
        return first_app_icon(("/Applications/Windsurf.app",)) or image_for_symbol(
            "wind",
            "Windsurf",
        )
    if any(token in normalized for token in ("cli", "terminal", "command line")):
        return first_app_icon(
            (
                "/System/Applications/Utilities/Terminal.app",
                "/Applications/iTerm.app",
                "/Applications/iTerm2.app",
            )
        ) or image_for_symbol("terminal", "Terminal")
    if "transcript" in normalized:
        return image_for_symbol("doc.text", "Transcript")
    return None


def first_app_icon(paths: tuple[str, ...]):
    for path in paths:
        image = app_icon(path)
        if image is not None:
            return image
    return None


def composite_app_icons(host_icon, provider_icon):
    if provider_icon is None:
        return host_icon
    if host_icon is None:
        return provider_icon

    image = NSImage.alloc().initWithSize_((24.0, 18.0))
    try:
        image.lockFocus()
        host_icon.drawInRect_fromRect_operation_fraction_(
            ((0.0, 1.0), (15.5, 15.5)),
            image_source_rect(host_icon),
            NSCompositingOperationSourceOver,
            1.0,
        )
        provider_icon.drawInRect_fromRect_operation_fraction_(
            ((8.0, 1.0), (15.5, 15.5)),
            image_source_rect(provider_icon),
            NSCompositingOperationSourceOver,
            1.0,
        )
    finally:
        image.unlockFocus()
    image.setSize_((24.0, 18.0))
    return image


def horizontal_icon_pair(left_icon, right_icon):
    left_width = 15.5
    right_width = float(right_icon.size().width)
    width = left_width + 3.0 + right_width
    height = 18.0
    image = NSImage.alloc().initWithSize_((width, height))
    try:
        image.lockFocus()
        left_icon.drawInRect_fromRect_operation_fraction_(
            ((0.0, 1.25), (left_width, left_width)),
            image_source_rect(left_icon),
            NSCompositingOperationSourceOver,
            1.0,
        )
        right_icon.drawInRect_fromRect_operation_fraction_(
            ((left_width + 3.0, 0.0), (right_width, height)),
            image_source_rect(right_icon),
            NSCompositingOperationSourceOver,
            1.0,
        )
    finally:
        image.unlockFocus()
    image.setSize_((width, height))
    return image


def image_source_rect(image) -> tuple[tuple[float, float], tuple[float, float]]:
    size = image.size()
    return ((0.0, 0.0), (float(size.width), float(size.height)))


def normalized_origin_text(origin: str | None) -> str:
    return " ".join(str(origin or "").strip().lower().replace("-", " ").split())


_grok_badge_icon = None


def grok_badge_icon():
    global _grok_badge_icon
    if _grok_badge_icon is not None:
        return _grok_badge_icon

    image = NSImage.alloc().initWithSize_((18, 18))
    try:
        image.lockFocus()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.05, 0.055, 0.065, 1.0).set()
        badge = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((1.0, 1.0), (16.0, 16.0)),
            4.0,
            4.0,
        )
        badge.fill()

        attrs = {
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(12.0),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        NSString.stringWithString_("G").drawInRect_withAttributes_(
            ((4.6, 1.8), (10.0, 14.0)),
            attrs,
        )
    finally:
        image.unlockFocus()
    image.setSize_((18, 18))
    _grok_badge_icon = image
    return image


_app_icon_cache: dict[str, object] = {}


def app_icon(path: str):
    # NSWorkspace icon lookups ran once per session row on EVERY menu
    # rebuild (every hook event). Icons are static; cache positives
    # forever, re-probe missing paths (the app may get installed later).
    cached = _app_icon_cache.get(path)
    if cached is not None:
        return cached
    if not Path(path).exists():
        return None
    image = NSWorkspace.sharedWorkspace().iconForFile_(path)
    if image is not None:
        image.setSize_((18, 18))
        _app_icon_cache[path] = image
    return image


def flatten_menu_title(title: str) -> str:
    return " · ".join(part.strip() for part in title.splitlines() if part.strip())


def add_action_item(
    menu: NSMenu,
    title: str,
    selector: str,
    represented_object: str | None,
    target: StatusBarController,
) -> None:
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title if represented_object else f"{title} unavailable",
        selector,
        "",
    )
    item.setTarget_(target)
    item.setEnabled_(represented_object is not None)
    if represented_object is not None:
        item.setRepresentedObject_(represented_object)
    menu.addItem_(item)


def build_error_menu(exc: Exception) -> NSMenu:
    menu = NSMenu.alloc().init()
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        f"Agent monitor error: {exc}",
        None,
        "",
    )
    item.setEnabled_(False)
    menu.addItem_(item)
    return menu


def unseen_completions(snapshot, target) -> list[AgentStatus]:
    """Main-session completions the user has NOT seen: newer than the
    last time the dropdown was opened. Opening the menu is the visit
    that clears them -- modeled, not guessed (T3's lastVisitedAt)."""
    opened_at = getattr(target, "menu_last_opened_at", None)
    cleared = getattr(target, "cleared_session_ids", set())
    unseen = []
    # Both lists: with any session active the collector demotes every
    # completed one to stale_statuses instantly, which starved this of
    # exactly the completions it exists to surface. The visible-window
    # bound keeps ancient history from badging forever.
    candidates = (*snapshot.statuses, *getattr(snapshot, "stale_statuses", ()))
    for status in candidates:
        if status.is_subagent or status.mode != AgentMode.COMPLETED:
            continue
        if (
            snapshot.collected_at - status.updated_at
        ).total_seconds() > COMPLETED_VISIBLE_SECONDS:
            continue
        if status.agent_id in cleared:
            continue
        if opened_at is not None and status.updated_at <= opened_at:
            continue
        unseen.append(status)
    return unseen


def recent_statuses(snapshot) -> list[AgentStatus]:
    """Main sessions only -- sub-agents render indented under their
    parent (active_subagents_by_parent), not as top-level rows."""
    statuses = [status for status in snapshot.statuses if not status.is_subagent]
    # Freshly finished sessions stay visible (with their "new" tag)
    # even while other sessions keep working -- the collector demotes
    # them to stale the moment anything is active.
    seen_ids = {status.agent_id for status in statuses}
    for status in getattr(snapshot, "stale_statuses", ()):
        if (
            not status.is_subagent
            and status.agent_id not in seen_ids
            and status.mode == AgentMode.COMPLETED
            and (snapshot.collected_at - status.updated_at).total_seconds()
            <= COMPLETED_VISIBLE_SECONDS
        ):
            statuses.append(status)
    if not statuses:
        statuses = [
            status for status in snapshot.stale_statuses if not status.is_subagent
        ]
    statuses.sort(key=lambda status: (status.priority, -status.updated_at.timestamp()))
    return statuses[:12]


def active_subagents_by_parent(snapshot) -> dict[str, list[AgentStatus]]:
    """Fresh, still-running sub-agents grouped by their parent session.
    Finished sub-agents are noise and drop out immediately."""
    groups: dict[str, list[AgentStatus]] = {}
    for status in snapshot.statuses:
        if not status.is_subagent or status.mode == AgentMode.COMPLETED:
            continue
        parent = status.parent_agent_id
        if parent is None:
            continue
        groups.setdefault(parent, []).append(status)
    for children in groups.values():
        children.sort(key=lambda status: (status.priority, -status.updated_at.timestamp()))
    return groups


def ask_statuses(snapshot) -> list[AgentStatus]:
    """The sessions currently waiting on the user -- the Ask Inbox.
    Main sessions only: a sub-agent's ask is its parent's problem, and
    counting them made "Needs You" claim asks nobody could answer."""
    return [
        status
        for status in snapshot.statuses
        if not status.is_subagent
        and status.mode in (AgentMode.WAITING_FOR_INPUT, AgentMode.BLOCKED_ERROR)
    ]


def menu_title_for_status(status: AgentStatus, now: datetime) -> str:
    state = state_for_mode(status.mode)
    title, project = session_title_parts(status)
    origin = menu_origin_label(status)
    if origin:
        first_line = f"{state.label}  {origin}  {title}"
    else:
        first_line = f"{state.label}  {title}"
    if project:
        return f"{first_line}\n{project}"
    return first_line


def session_detail_for_status(status: AgentStatus, now: datetime) -> str:
    state = state_for_mode(status.mode)
    age = format_age(status.age_seconds(now))
    details = [state.label, age]
    if status.origin:
        details.append(status.origin)
    if status.tool_name:
        details.append(status.tool_name)
    return " · ".join(details)


def menu_origin_label(status: AgentStatus) -> str | None:
    if not status.origin:
        return None
    return status.origin


def primary_session_open_action(status: AgentStatus | object) -> str | None:
    if not isinstance(status, AgentStatus):
        return None
    return default_session_open_action(status)


def session_title_parts(status: AgentStatus) -> tuple[str, str | None]:
    project = project_name_from_cwd(status.cwd)
    title = strip_session_short_id(status.display_name, status.session_id)
    if project and title.startswith(f"{project}: "):
        title = title[len(project) + 2 :]
    elif ": " in title:
        maybe_project, maybe_title = title.split(": ", 1)
        if not project:
            project = maybe_project
        title = maybe_title
    if project and normalized_menu_part(project) == normalized_menu_part(title):
        project = None
    return title or status.display_name, project


def normalized_menu_part(text: str) -> str:
    return " ".join(text.replace("_", " ").replace("-", " ").split()).casefold()


def strip_session_short_id(display_name: str, session_id: str | None) -> str:
    text = display_name.strip()
    if session_id:
        suffix = f" ({session_id[:8]})"
        if text.endswith(suffix):
            return text[: -len(suffix)].strip()
    if text.endswith(")") and " (" in text:
        prefix, suffix = text.rsplit(" (", 1)
        token = suffix[:-1]
        if 6 <= len(token) <= 12 and all(char.isalnum() or char == "-" for char in token):
            return prefix.strip()
    return text


def project_name_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    path = Path(cwd)
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate.name or str(candidate)
    return path.name or cwd


def image_for_symbol(symbol: str, description: str):
    try:
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol,
            description,
        )
        if image is not None:
            image.setTemplate_(True)
    except Exception:
        image = None
    return image


def log_status_bar(message: str) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{timestamp} {message}", flush=True)


def compact_path(path: str, max_len: int = 48) -> str:
    if len(path) <= max_len:
        return path
    keep = max_len - 1
    return "." + path[-keep:]


def format_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def open_url(url: str) -> None:
    ns_url = NSURL.URLWithString_(url)
    if ns_url is not None:
        NSWorkspace.sharedWorkspace().openURL_(ns_url)


def open_terminal_command(command: str) -> None:
    script = "\n".join(
        [
            'tell application "Terminal"',
            "  activate",
            f"  do script {applescript_quote(command)}",
            "end tell",
        ]
    )
    subprocess.Popen(
        ["/usr/bin/osascript", "-e", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def applescript_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def run_status_bar() -> None:
    app = NSApplication.sharedApplication()
    controller = StatusBarController.alloc().init()
    app.setDelegate_(controller)
    app.run()


def main() -> int:
    # Backlog #15: a second instance used to steal events.sock, and
    # quitting it unlinked the socket and permanently deafened the
    # survivor. One live-owner probe; a stale socket file still reads
    # as dead and gets rebound over as before.
    if another_instance_alive():
        print("SidePulse is already running; this instance is exiting.")
        return 0
    run_status_bar()
    return 0



# --- Extraction seam (backlog #14): the Settings window's construction
# lives in settings_window.py, executing against THIS module's namespace
# (installed here) and re-exported below so controller methods, tests,
# and callers keep addressing status_bar.<name>. settings_window never
# imports status_bar, so no import cycle can exist.
from . import settings_window as _settings_window  # noqa: E402
from .virtual_device import LED_COUNT  # noqa: E402, F401 -- re-export; tests address status_bar.LED_COUNT

_settings_window._install(dict(globals()))
from .settings_window import (  # noqa: E402, F401 -- re-export: tests and
    # callers keep addressing status_bar.<name> after the #14 extraction
    BRAND_SWATCHES,
    CALIBRATION_TEST_PATCHES,
    COLOR_ROW_HEIGHT,
    COLOR_SWATCH_GAP,
    COLOR_SWATCH_SIZE,
    ESCALATION_TIER_LABELS,
    FOCUS_DIM_CHOICES,
    LID_ANIMATION_PRESETS,
    MODE_COLOR_DISPLAY_LABELS,
    SCREEN_BAR_PREVIEW_NOTCH_WIDTH,
    SCREEN_BAR_PREVIEW_WING_WIDTH,
    SIGNAL_PREVIEW_SIZE,
    SIGNAL_STYLE_CARDS,
    SIGNAL_THUMB_SIZE,
    SWATCH_BUTTON_SIZE,
    _add_studio_card,
    _apply_thumb_selection,
    _build_agent_or_mode_color_row,
    _build_agents_pane,
    _build_color_studio_pane,
    _build_colors_screen_bar_pane,
    _build_debug_pane,
    _build_devices_pane,
    _build_focus_pane,
    _build_led_behavior_pane,
    _build_lid_animations_pane,
    _build_lid_preset_row,
    _build_power_pane,
    _build_profile_pane,
    _build_settings_pane,
    _mini_led_view,
    _mode_animation_thumb_program,
    _signal_preview_color,
    _solid_swatch_image,
    build_calibration_popover_content,
    build_settings_window,
    calibration_summary_text,
    make_focus_dim_popup,
    make_signal_color_row,
    make_signal_style_card,
    refresh_blend_and_speed_fields,
    select_focus_dim_choice,
)

# Direct execution (python -m sidepulse.status_bar) must run AFTER the
# extraction seam above -- main() blocks for the app's whole life, so
# anything below the guard would never execute (the seam sat below it
# briefly, and every Settings path NameError'd under -m).
if __name__ == "__main__":
    raise SystemExit(main())
