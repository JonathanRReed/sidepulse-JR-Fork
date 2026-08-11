from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime
from pathlib import Path

try:
    import objc
    from AppKit import (
        NSApp,
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSBezierPath,
        NSBezelStyleRounded,
        NSButton,
        NSButtonTypeSwitch,
        NSColor,
        NSColorPanel,
        NSCompositingOperationSourceOver,
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
        NSScrollView,
        NSSavePanel,
        NSSlider,
        NSSplitView,
        NSSplitViewDividerStyleThin,
        NSStatusBar,
        NSTextField,
        NSTextView,
        NSView,
        NSViewController,
        NSWorkspace,
        NSWindow,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskResizable,
        NSWindowStyleMaskTitled,
        NSVariableStatusItemLength,
    )
    from Foundation import NSIndexSet, NSObject, NSString, NSTimer, NSURL
except ImportError as exc:  # pragma: no cover - only exercised on non-macOS setups.
    raise SystemExit(
        "The status-bar app requires PyObjC/AppKit:\n"
        "  python3 -m pip install pyobjc-framework-Cocoa"
    ) from exc

from .battery import (
    BatteryLedController,
    BatterySnapshot,
    format_watts,
    program_for_battery,
    read_battery_snapshot,
)
from . import display_brightness
from . import focus_sync
from . import native_ui
from .audit import (
    default_status_audit_log_path,
    export_status_audit_csv,
    export_status_audit_html,
)
from .collector import LiveAgentMonitor, SourceSpec, read_recent_lines
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
from .keep_awake import KEEPALIVE_FILE_NAME, KeepAwakeController
from .ipc import HookEventServer, default_event_socket_path, default_latest_state_path
from .install import (
    install_provider_hooks,
    uninstall_provider_hooks,
)
from .led_status import (
    ANIMATION_STYLE_CHOICES,
    MAX_CHANNEL_GAIN,
    MIN_CHANNEL_GAIN,
    AgentLedController,
    apply_brightness,
    brightness_percent,
    normalize_brightness,
    normalized_device_name,
    write_mode_to_leds,
)
from . import colors as colors_module
from .colors import (
    ANIMATION_MODE_KEYS,
    BLEND_MODE_CHOICES,
    BLEND_MODE_CYCLE,
    BLEND_MODE_DESCRIPTIONS,
    BLEND_MODE_LABELS,
    BLEND_MODE_ROUND_ROBIN,
    CURATED_PALETTE,
    FADE_MODE_KEYS,
    MODE_COLOR_KEYS,
    ColorSettings,
    program_for_snapshot,
)
from .virtual_device import VIRTUAL_DEVICE_ID, VIRTUAL_DEVICE_NAME, VirtualStatusDevice, monotonic_ms
from .led_wasm import LedWasmUnavailableError, SdLedWasmController
from .lid_sleep import (
    LID_POLL_SECONDS,
    ClosedLidAwakeController,
    read_lid_closed,
    sleep_helper_install_command,
    sleep_helper_installed,
)
from .models import AgentMode, AgentStatus, MODE_LABELS
from .providers import (
    HOOK_PROVIDERS,
    PROVIDER_SPECS,
    ProviderConfig,
    detect_log_path,
    default_state_dir,
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
    provider_session_opener_providers,
    session_open_action_label,
    session_open_target,
)
from .settings import (
    ALCOVE_COMPAT_ALWAYS,
    ALCOVE_COMPAT_AUTO,
    ALCOVE_COMPAT_CHOICES,
    ALCOVE_COMPAT_NEVER,
    CLOSED_LID_AWAKE_AGENTS,
    CLOSED_LID_AWAKE_ALWAYS,
    CLOSED_LID_AWAKE_CHOICES,
    CLOSED_LID_AWAKE_NEVER,
    LED_DISPLAY_AGENT,
    LED_DISPLAY_BATTERY,
    LID_ANIMATION_CLOSED,
    LID_ANIMATION_OPEN,
    LedAnimationSetting,
    default_settings_path,
    default_lid_animation,
    load_settings,
    normalize_animation_duration,
    save_settings,
)
from .status_bar_launch import install_launch_agent, launch_agent_installed


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
STATUS_BAR_REFRESH_SECONDS = 15.0
STATUS_BAR_DEVICE_POLL_SECONDS = 2.0
SCREEN_BAR_FEATURE_ENABLED = True
STATUS_BAR_MAX_LINES_PER_SOURCE = 500
STATUS_BAR_STARTUP_REPLAY_LINES = 200
LID_ANIMATION_RESTORE_FUDGE_SECONDS = 0.15
LID_ANIMATION_LABELS = {
    LID_ANIMATION_CLOSED: "Lid Closed",
    LID_ANIMATION_OPEN: "Lid Open",
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
        self.setup_fields = {}
        self.setup_buttons = {}
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
        self.current_state = STATE_IDLE
        # None until set_status() actually confirms Idle -- avoids assuming
        # "idle since launch" if the real initial state turns out to be
        # something else once the first real snapshot arrives.
        self.idle_since_monotonic: float | None = None
        self.led_controller = AgentLedController()
        self.battery_led_controller = BatteryLedController()
        self.agent_led_controllers_by_device = {}
        self.battery_led_controllers_by_device = {}
        self.last_led_display_kind_by_device = {}
        self.device_errors = {}
        self.leds_enabled = True
        self.led_sync_in_flight = False
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
        return self

    def applicationDidFinishLaunching_(self, _notification):
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
        self.show_setup_window_if_needed()
        if SCREEN_BAR_FEATURE_ENABLED and self.settings.virtual_status_device_enabled:
            self.virtual_status_device.show()
        else:
            self.virtual_status_device.hide()

    @objc.IBAction
    def refresh_(self, _sender):
        try:
            snapshot = self.monitor.snapshot(include_stale=False)
        except Exception as exc:
            log_status_bar(f"refresh error: {exc}")
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
        state = state_for_mode(snapshot.aggregate.mode)
        self.observe_connected_devices()
        self.set_status(state)
        self.sync_keep_awake(snapshot.aggregate.mode)
        self.sync_leds(
            snapshot.aggregate.mode,
            battery_snapshot,
            self.active_led_display_kind(battery_snapshot),
            snapshot.statuses,
        )
        if self.status_item is not None:
            self.status_item.setMenu_(build_menu(snapshot, state, self))

    @objc.IBAction
    def forceRefresh_(self, _sender):
        self.refresh_(None)

    @objc.IBAction
    def openDeepLink_(self, sender):
        url = sender.representedObject()
        if not url:
            return
        open_url(str(url))

    @objc.IBAction
    def resumeSession_(self, sender):
        command = sender.representedObject()
        if command:
            open_terminal_command(str(command))

    @objc.IBAction
    def openSession_(self, sender):
        self.open_session(sender.representedObject(), None, remember=False)

    @objc.IBAction
    def openSessionPrimary_(self, sender):
        self.open_session(
            sender.representedObject(),
            None,
            remember=False,
        )
        self.close_status_menu()

    @objc.IBAction
    def openSessionOptions_(self, sender):
        status = sender.representedObject()
        if not isinstance(status, AgentStatus):
            return
        menu = build_session_options_menu(status, datetime.now().astimezone(), self)
        try:
            height = sender.bounds().size.height
        except Exception:
            try:
                height = sender.bounds()[1][1]
            except Exception:
                height = 0
        menu.popUpMenuPositioningItem_atLocation_inView_(None, (0, height), sender)

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
    def toggleDeviceConnection_(self, _sender):
        if self.device_connected():
            self.disconnect_device()
        else:
            self.connect_device()
        self.refresh_(None)

    @objc.IBAction
    def toggleKeepAwake_(self, _sender):
        self.keep_awake.set_enabled(not self.keep_awake.enabled)
        log_status_bar(f"keep_awake={'on' if self.keep_awake.enabled else 'off'}")
        self.refresh_(None)

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
    def toggleCodexTranscripts_(self, sender):
        self.set_transcript_monitoring("codex", sender.state() == NSOnState)

    @objc.IBAction
    def toggleClaudeTranscripts_(self, sender):
        self.set_transcript_monitoring("claude", sender.state() == NSOnState)

    @objc.IBAction
    def toggleBatteryLedDisplay_(self, _sender):
        self.set_battery_led_display(self.settings.led_display != LED_DISPLAY_BATTERY)

    @objc.IBAction
    def setBatteryLedDisplayFromCheckbox_(self, sender):
        self.set_battery_led_display(sender.state() == NSOnState)

    @objc.IBAction
    def toggleBatteryPowerPreview_(self, _sender):
        self.set_battery_power_preview(not self.settings.battery_show_on_power_change)

    @objc.IBAction
    def setBatteryPowerPreviewFromCheckbox_(self, sender):
        self.set_battery_power_preview(sender.state() == NSOnState)

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
        self.set_device_brightness(str(device_id), sender.doubleValue())

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
        popover.showRelativeToRect_ofView_preferredEdge_(sender.bounds(), sender, NSMaxYEdge)
        self._device_calibration_popover = popover
        self.device_settings_controls.setdefault(device_id, {}).update(controls)
        self.refresh_device_settings_controls(device_id, controls)

    @objc.IBAction
    def toggleVirtualStatusDevice_(self, _sender):
        if not SCREEN_BAR_FEATURE_ENABLED:
            self.set_virtual_status_device(False)
            return
        self.set_virtual_status_device(not self.settings.virtual_status_device_enabled)

    @objc.IBAction
    def saveLidAnimations_(self, _sender):
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
        NSApp.terminate_(self)

    def applicationWillTerminate_(self, _notification):
        self.stop_event_server()
        self.closed_lid_awake.release()
        self.keep_awake.release()

    def set_status(self, state: StatusBarState) -> None:
        previous = self.current_state
        self.current_state = state
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
        button.setTitle_(f" {state.label}")
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

    def reload_monitor(self) -> None:
        self.monitor = self.build_monitor()

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
        self.refresh_(None)

    def replay_debug_logs(self) -> None:
        replayed = replay_recent_debug_logs(self.monitor)
        if replayed:
            log_status_bar(f"startup_replay events={replayed}")

    def show_settings_window(self) -> None:
        if self.settings_window is None:
            self.settings_window = build_settings_window(self)
            if self.settings_sidebar_table is not None:
                self.settings_sidebar_table.selectRowIndexes_byExtendingSelection_(
                    NSIndexSet.indexSetWithIndex_(0), False
                )
        self.refresh_settings_window()
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
        _key, label = SETTINGS_SIDEBAR_ITEMS[row]
        return native_ui.sidebar_cell_view(label)

    def tableView_isGroupRow_(self, _table_view, _row) -> bool:
        return False

    def tableViewSelectionDidChange_(self, notification):
        table = notification.object()
        row = table.selectedRow()
        if row < 0 or row >= len(SETTINGS_SIDEBAR_ITEMS):
            return
        selected_key = SETTINGS_SIDEBAR_ITEMS[row][0]
        for key, pane in self.settings_panes.items():
            pane.setHidden_(key != selected_key)

    @objc.IBAction
    def openColorsWindow_(self, _sender):
        self.show_colors_window()

    def show_colors_window(self) -> None:
        if self.colors_window is None:
            self.colors_window = build_colors_window(self)
        self.refresh_colors_window()
        self.colors_window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def refresh_colors_window(self) -> None:
        if self.colors_window is None:
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
        animation_popups = self.color_fields.get("animation_popups") or {}
        for key, animation_popup in animation_popups.items():
            select_animation_style(animation_popup, colors.animation_style(key))
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
            1.0 / 20.0, self, "animateColorsPreviewTick:", None, True
        )

    def stop_colors_preview_animation(self) -> None:
        if self.color_preview_timer is not None:
            self.color_preview_timer.invalidate()
            self.color_preview_timer = None

    @objc.IBAction
    def animateColorsPreviewTick_(self, _sender):
        self.animate_colors_preview_once()

    def animate_colors_preview_once(self) -> None:
        if self.colors_window is None or not self.colors_window.isVisible():
            self.stop_colors_preview_animation()
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
        panel = NSColorPanel.sharedColorPanel()
        panel.setColor_(nscolor_from_hex(current_hex))
        panel.setTarget_(self)
        panel.setAction_("applyCustomColorFromPanel:")
        panel.orderFront_(None)

    @objc.IBAction
    def applyCustomColorFromPanel_(self, sender):
        if self.active_color_target is None:
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
    def toggleDoneCelebration_(self, sender):
        colors = self.settings.colors.with_done_celebration_enabled(checkbox_is_on(sender))
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
        self.stop_colors_preview_animation()
        if self.colors_window is not None:
            self.colors_window.performClose_(None)

    @objc.IBAction
    def setAlcoveCompatibilityMode_(self, sender):
        payload = sender.selectedItem().representedObject() if sender.selectedItem() else None
        if not payload or "alcove_mode" not in payload:
            return
        try:
            self.settings = self.settings.with_alcove_compatibility_mode(payload["alcove_mode"])
        except ValueError:
            return
        save_settings(self.settings)
        self.reposition_virtual_status_device_now()
        self.set_settings_message(f"Alcove compatibility: {payload['alcove_mode']}")

    @objc.IBAction
    def toggleScreenBarWrapsMenuBar_(self, sender):
        enabled = checkbox_is_on(sender)
        self.settings = self.settings.with_virtual_status_device_wraps_menu_bar(enabled)
        save_settings(self.settings)
        self.reposition_virtual_status_device_now()
        self.set_settings_message(
            "Screen Bar now extends along the menu bar." if enabled else "Screen Bar back to notch width only."
        )

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
        self.virtual_status_device.set_alcove_compatibility_mode(self.settings.alcove_compatibility_mode)
        self.virtual_status_device.set_wraps_menu_bar(self.settings.virtual_status_device_wraps_menu_bar)
        self.virtual_status_device.reposition()

    def show_setup_window_if_needed(self) -> None:
        if should_show_setup_window(self.settings):
            self.show_setup_window()

    def show_setup_window(self) -> None:
        if self.setup_window is None:
            self.setup_window = build_setup_window(self)
        self.refresh_setup_window()
        self.setup_window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

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
        self.set_setup_checkbox("launch", True, enabled=not launch_installed)
        self.set_setup_checkbox("eject_guard", True, enabled=not eject_installed)
        self.set_setup_checkbox("sleep_helper", True, enabled=not sleep_installed)
        eject_uninstall = self.setup_buttons.get("eject_guard_uninstall")
        if eject_uninstall is not None:
            eject_uninstall.setEnabled_(eject_installed)

    def set_setup_checkbox(self, key: str, checked: bool, *, enabled: bool) -> None:
        button = self.setup_buttons.get(key)
        if button is None:
            return
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

        for provider in HOOK_PROVIDERS:
            set_field_value(
                self.settings_fields.get(f"{provider}_hook_status"),
                hook_status_text(provider_spec(provider).detector(None)),
            )
        set_field_value(
            self.settings_fields.get("settings_path"),
            f"Settings: {default_settings_path()}",
        )
        set_field_value(
            self.settings_fields.get("debug_log_status"),
            debug_log_status_text(),
        )
        alcove_popup = self.settings_fields.get("alcove_compat_popup")
        if alcove_popup is not None:
            select_alcove_compat_mode(alcove_popup, self.settings.alcove_compatibility_mode)
        set_checkbox_state(
            self.settings_buttons.get("screen_bar_wraps_menu_bar"),
            self.settings.virtual_status_device_wraps_menu_bar,
        )
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
        for provider in HOOK_PROVIDERS:
            popup = self.settings_fields.get(f"{provider}_session_opener")
            if popup is not None:
                select_popup_action(
                    popup,
                    self.settings.session_open_action(provider)
                    or default_provider_open_action(provider),
                )
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
        set_checkbox_state(
            controls.get("auto_brightness_checkbox"),
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

    def set_settings_message(self, message: str) -> None:
        set_field_value(self.settings_fields.get("message"), message)
        if message:
            log_status_bar(f"settings: {message}")

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
        try:
            result = (
                install_provider_hooks(provider)
                if install
                else uninstall_provider_hooks(provider)
            )
        except Exception as exc:
            self.set_settings_message(f"{provider.title()} hooks failed: {exc}")
            self.refresh_settings_window()
            return

        action = "installed" if install else "removed"
        if not result.changed:
            action = "already installed" if install else "already removed"
        self.set_settings_message(f"{provider.title()} hooks {action}.")
        self.reload_monitor()
        self.refresh_settings_window()
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

    def set_device_display(self, device_id: str | None, display: str) -> None:
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
            self.settings = self.settings.with_device_display(
                str(device_id),
                display,
                name=device.name if device else None,
                path=str(device.root) if device else None,
            )
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save device display: {exc}")
            self.settings = load_settings()
            return

        self.reset_led_controllers_for_device(str(device_id))
        label = "Battery Level" if display == LED_DISPLAY_BATTERY else "Agent Status"
        self.set_settings_message(f"{device.name if device else device_id}: {label}.")
        self.refresh_settings_window()
        self.refresh_(None)

    def set_device_brightness(self, device_id: str | None, brightness: int | float) -> None:
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
        value = normalize_brightness(brightness)
        try:
            self.settings = self.settings.with_device_brightness(
                str(device_id),
                value,
                name=device.name if device else None,
                path=str(device.root) if device else None,
            )
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save brightness: {exc}")
            self.settings = load_settings()
            return

        self.reset_led_controllers_for_device(str(device_id))
        self.set_settings_message(
            f"{device.name if device else device_id}: brightness {brightness_percent(value)}%."
        )
        self.refresh_settings_window()
        if self.last_snapshot is not None:
            self.sync_leds(
                self.last_snapshot.aggregate.mode,
                self.last_battery_snapshot,
                self.active_led_display_kind(self.last_battery_snapshot),
            )

    def set_device_auto_brightness(self, device_id: str | None, enabled: bool) -> None:
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
            self.settings = self.settings.with_device_auto_brightness(
                str(device_id),
                enabled,
                name=device.name if device else None,
                path=str(device.root) if device else None,
            )
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save auto-brightness: {exc}")
            self.settings = load_settings()
            return

        self.reset_led_controllers_for_device(str(device_id))
        label = "on" if enabled else "off"
        self.set_settings_message(
            f"{device.name if device else device_id}: auto-brightness {label}."
        )
        self.refresh_settings_window()
        if self.last_snapshot is not None:
            self.sync_leds(
                self.last_snapshot.aggregate.mode,
                self.last_battery_snapshot,
                self.active_led_display_kind(self.last_battery_snapshot),
            )

    def set_device_channel_gain(self, device_id: str | None, channel: str, value: float) -> None:
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
            self.settings = self.settings.with_device_channel_gain(
                str(device_id),
                channel,
                value,
                name=device.name if device else None,
                path=str(device.root) if device else None,
            )
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not save color calibration: {exc}")
            self.settings = load_settings()
            return

        self.reset_led_controllers_for_device(str(device_id))
        gain = self.settings.channel_gains_for_device(str(device_id))
        red, green, blue = gain
        self.set_settings_message(
            f"{device.name if device else device_id}: calibration R{round(red * 100)}% "
            f"G{round(green * 100)}% B{round(blue * 100)}%."
        )
        self.refresh_settings_window()
        if self.last_snapshot is not None:
            self.sync_leds(
                self.last_snapshot.aggregate.mode,
                self.last_battery_snapshot,
                self.active_led_display_kind(self.last_battery_snapshot),
            )

    def set_device_channel_gains_reset(self, device_id: str | None) -> None:
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
            self.settings = self.settings.with_device_channel_gains_reset(str(device_id))
            save_settings(self.settings)
        except Exception as exc:
            self.set_settings_message(f"Could not reset color calibration: {exc}")
            self.settings = load_settings()
            return

        self.reset_led_controllers_for_device(str(device_id))
        self.set_settings_message(f"{device.name if device else device_id}: calibration reset.")
        self.refresh_settings_window()
        if self.last_snapshot is not None:
            self.sync_leds(
                self.last_snapshot.aggregate.mode,
                self.last_battery_snapshot,
                self.active_led_display_kind(self.last_battery_snapshot),
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
        return controller

    def battery_controller_for_device(self, device: StatusBarDevice) -> BatteryLedController:
        controller = self.battery_led_controllers_by_device.get(device.device_id)
        if controller is None:
            controller = BatteryLedController(device_path=device.target)
            self.battery_led_controllers_by_device[device.device_id] = controller
        controller.device_path = device.target
        controller.brightness = self.effective_brightness_for_device(device)
        controller.channel_gains = device.channel_gains
        return controller

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
        return normalize_brightness(base * self.idle_dim_scale_factor() * self.focus_sync_scale_factor())

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
        try:
            active = focus_sync.is_focus_active()
        except focus_sync.FocusSyncUnavailableError:
            return 1.0
        return self.settings.idle_dim_fraction if active else 1.0

    def status_bar_devices(self, *, remember: bool = True) -> list[StatusBarDevice]:
        entries_by_id: dict[str, StatusBarDevice] = {}
        try:
            candidates = discover_devices()
        except Exception as exc:
            log_status_bar(f"device discovery error: {exc}")
            candidates = []

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
                channel_gains=self.settings.channel_gains_for_device(device_id),
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
                        channel_gains=device.channel_gains(),
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
                channel_gains=device.channel_gains(),
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

    def active_led_display_kind_for_device(
        self,
        device: StatusBarDevice,
        battery_snapshot: BatterySnapshot | None,
    ) -> str:
        if device.display == LED_DISPLAY_BATTERY:
            return LED_DISPLAY_BATTERY
        if battery_snapshot is not None and time.monotonic() < self.battery_preview_until:
            return LED_DISPLAY_BATTERY
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
            return
        self.led_sync_in_flight = True
        thread = threading.Thread(
            target=self.sync_leds_worker,
            args=(mode, battery_snapshot, display_kind, statuses),
            daemon=True,
        )
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
        self.virtual_status_device.set_alcove_compatibility_mode(
            self.settings.alcove_compatibility_mode
        )
        self.virtual_status_device.set_wraps_menu_bar(
            self.settings.virtual_status_device_wraps_menu_bar
        )
        device = next(
            (
                item for item in self.status_bar_devices(remember=False)
                if item.device_id == VIRTUAL_DEVICE_ID
            ),
            None,
        )
        if device is None:
            return
        display = self.active_led_display_kind_for_device(device, battery_snapshot)
        if display == LED_DISPLAY_BATTERY and battery_snapshot is not None:
            self.virtual_status_device.set_program(
                program_for_battery(
                    battery_snapshot,
                    led_count=8,
                    brightness=device.brightness,
                ),
                started_at=started_at,
            )
        else:
            _, program = program_for_snapshot(
                statuses,
                led_count=8,
                colors=self.settings.colors,
                brightness=device.brightness,
                fallback_mode=mode,
            )
            self.virtual_status_device.set_program(program, started_at=started_at)

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

            if device_display_kind == LED_DISPLAY_BATTERY and battery_snapshot is not None:
                result = self.battery_controller_for_device(device).sync_snapshot(battery_snapshot)
                label = (
                    f"{device.name} Battery {battery_snapshot.percent}% "
                    f"{format_watts(battery_snapshot.adapter_power)}"
                )
            else:
                result = self.agent_controller_for_device(device).sync_snapshot(
                    statuses, self.settings.colors, fallback_mode=mode
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
        try:
            closed = read_lid_closed()
        except Exception as exc:
            error = str(exc)
            if error != self.last_lid_error:
                self.last_lid_error = error
                log_status_bar(f"lid_state error: {error}")
            return

        if closed is None:
            return
        self.last_lid_error = None
        if self.last_lid_closed is None:
            self.last_lid_closed = closed
            return
        if closed == self.last_lid_closed:
            return

        self.last_lid_closed = closed
        kind = LID_ANIMATION_CLOSED if closed else LID_ANIMATION_OPEN
        log_status_bar(f"lid_state={'closed' if closed else 'open'}")
        self.play_lid_animation(kind)

    @objc.IBAction
    def pollDevices_(self, _sender):
        self.poll_devices_once()

    def poll_devices_once(self) -> None:
        if not self.observe_connected_devices():
            return
        if self.last_snapshot is not None:
            self.refresh_(None)

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
        read_any = False
        for target in self.status_keepalive_targets():
            status_path = self.keep_awake.poke_status_file(target)
            if status_path is not None:
                read_any = True
                log_status_bar(f"sd_keepalive touch={status_path}")
        if not read_any and self.keep_awake.last_status_error != self.last_status_read_error:
            self.last_status_read_error = self.keep_awake.last_status_error
            if self.last_status_read_error:
                log_status_bar(f"sd_keepalive error: {self.last_status_read_error}")

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


def build_menu(snapshot, state: StatusBarState, target: StatusBarController) -> NSMenu:
    menu = NSMenu.alloc().init()

    menu.addItem_(disabled_menu_item("SidePulse"))
    menu.addItem_(NSMenuItem.separatorItem())

    menu.addItem_(disabled_menu_item("Agents"))

    statuses = recent_statuses(snapshot)
    if not statuses:
        menu.addItem_(disabled_menu_item("No recent sessions"))
    else:
        for status in statuses:
            menu.addItem_(build_session_menu_item(status, snapshot.collected_at, target))

    menu.addItem_(NSMenuItem.separatorItem())
    menu.addItem_(disabled_menu_item("Devices"))
    devices = target.status_bar_devices()
    if devices:
        for device in devices:
            menu.addItem_(build_device_menu_item(device, target))
    else:
        menu.addItem_(disabled_menu_item("No devices"))
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
    menu.addItem_(disabled_menu_item("Keep Awake With Lid Closed"))
    for policy in CLOSED_LID_AWAKE_CHOICES:
        menu.addItem_(build_closed_lid_awake_policy_item(policy, target))
    if target.closed_lid_awake.last_error:
        menu.addItem_(disabled_menu_item(f"Sleep warning: {target.closed_lid_awake.last_error}"))

    menu.addItem_(NSMenuItem.separatorItem())
    setup = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Setup...",
        "openSetup:",
        "",
    )
    setup.setTarget_(target)
    menu.addItem_(setup)

    colors_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Customize Colors...",
        "openColorsWindow:",
        "",
    )
    colors_item.setTarget_(target)
    menu.addItem_(colors_item)

    settings = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Settings...",
        "openSettings:",
        ",",
    )
    settings.setTarget_(target)
    menu.addItem_(settings)

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


def build_setup_window(target: StatusBarController) -> NSWindow:
    width = 620
    height = 330
    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
    )
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (width, height)),
        style,
        NSBackingStoreBuffered,
        False,
    )
    window.setTitle_("SidePulse Setup")
    window.setReleasedWhenClosed_(False)
    window.center()
    content = window.contentView()

    add_label(content, "SidePulse", 24, 282, 180, 28)
    add_label(content, "Finish setup for this Mac.", 24, 254, 340, 22)

    launch = add_checkbox(
        content,
        "Run at Login",
        32,
        206,
        190,
        24,
        target,
        "",
    )
    add_label(content, "Start the menu-bar app automatically.", 56, 184, 300, 20)
    launch_status = add_label(content, "", 380, 206, 140, 22)

    eject_guard = add_checkbox(
        content,
        SD_EJECT_GUARD_DISPLAY_NAME,
        32,
        146,
        300,
        24,
        target,
        "",
    )
    add_label(content, "Keep SidePulse Pro/SidePulse Dot available after sleep.", 56, 124, 390, 20)
    eject_status = add_label(content, "", 398, 146, 88, 22)
    eject_uninstall = add_button(content, "Uninstall", 498, 142, 92, 28, target, "uninstallSdEjectGuard:")

    sleep_helper = add_checkbox(
        content,
        "Closed-Lid Sleep Prevention",
        32,
        86,
        260,
        24,
        target,
        "",
    )
    add_label(content, "Open a one-time administrator setup in Terminal.", 56, 64, 360, 20)
    sleep_status = add_label(content, "", 398, 86, 190, 22)

    message = add_label(content, "", 24, 36, width - 48, 20)

    add_button(content, "Skip", 392, 8, 84, 28, target, "skipFirstLaunchSetup:")
    add_button(content, "Set Up", 490, 8, 100, 28, target, "runFirstLaunchSetup:")

    target.setup_fields = {
        "launch_status": launch_status,
        "eject_status": eject_status,
        "sleep_status": sleep_status,
        "message": message,
    }
    target.setup_buttons = {
        "launch": launch,
        "eject_guard": eject_guard,
        "eject_guard_uninstall": eject_uninstall,
        "sleep_helper": sleep_helper,
    }
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

SETTINGS_SIDEBAR_ITEMS: tuple[tuple[str, str], ...] = (
    ("devices", "Devices"),
    ("colors_screen_bar", "Colors & Screen Bar"),
    ("closed_lid", "Closed-Lid Awake"),
    ("led_behavior", "LED Behavior"),
    ("hooks", "Agent Hooks"),
    ("sessions", "Sessions"),
    ("battery", "Battery"),
    ("lid_animations", "Lid Animations"),
    ("debug", "Debug"),
)


def _build_devices_pane(target: StatusBarController):
    stack = native_ui.make_stack(orientation="vertical", spacing=16.0)
    devices = target.status_bar_devices(remember=False)
    device_controls: dict[str, dict[str, object]] = {}
    if not devices:
        outer, inner = native_ui.make_card("Devices")
        inner.addArrangedSubview_(native_ui.make_label("No devices connected yet.", secondary=True))
        stack.addArrangedSubview_(outer)
    for device in devices:
        outer, inner = native_ui.make_card(device.name)

        brightness_slider = native_ui.make_slider(
            min_value=0.0,
            max_value=255.0,
            value=float(normalize_brightness(device.brightness)),
            target=target,
            action="setDeviceBrightness:",
            identifier=device.device_id,
        )
        native_ui.constrain_width(brightness_slider, 200)
        brightness_label = native_ui.make_label(f"{brightness_percent(device.brightness)}%", secondary=True)
        native_ui.constrain_width(brightness_label, 44)
        brightness_row_controls = native_ui.make_stack(orientation="horizontal", spacing=10.0)
        brightness_row_controls.addArrangedSubview_(brightness_slider)
        brightness_row_controls.addArrangedSubview_(brightness_label)
        inner.addArrangedSubview_(native_ui.make_row("Brightness", brightness_row_controls))

        calibrate_button = native_ui.make_button("Calibrate…", target, "openDeviceCalibrationPopover:")
        calibrate_button.setRepresentedObject_(device.device_id)
        red, green, blue = device.channel_gains
        calibration_label = native_ui.make_label(
            calibration_summary_text(device.auto_brightness_enabled, red, green, blue),
            secondary=True,
            size=11.0,
        )
        color_row_controls = native_ui.make_stack(orientation="horizontal", spacing=10.0)
        color_row_controls.addArrangedSubview_(calibrate_button)
        color_row_controls.addArrangedSubview_(calibration_label)
        inner.addArrangedSubview_(native_ui.make_row("Color", color_row_controls))

        stack.addArrangedSubview_(outer)
        device_controls[device.device_id] = {
            "brightness_slider": brightness_slider,
            "brightness_label": brightness_label,
            "calibrate_button": calibrate_button,
            "calibration_label": calibration_label,
        }
    return native_ui.wrap_in_scroll_pane(stack), device_controls


def calibration_summary_text(auto_brightness_enabled: bool, red: float, green: float, blue: float) -> str:
    auto = "on" if auto_brightness_enabled else "off"
    return f"Auto-Brightness {auto} · R{round(red * 100)}% G{round(green * 100)}% B{round(blue * 100)}%"


def build_calibration_popover_content(device: StatusBarDevice, target: StatusBarController):
    """The content shown inside the "Calibrate…" popover for one device:
    Auto-Brightness + the three per-channel gain sliders + Reset. Kept out
    of the main Devices pane row (where three bare "R"/"G"/"B" sliders
    previously sat inline all the time) -- calibration is something you
    set once and rarely touch again, not something that should occupy
    permanent space in the list.
    """
    stack = native_ui.make_stack(orientation="vertical", spacing=14.0)
    native_ui.constrain_width(stack, 300.0)

    auto_checkbox = native_ui.make_checkbox(
        "Auto-Brightness (matches screen)", target, "toggleDeviceAutoBrightness:"
    )
    auto_checkbox.setRepresentedObject_(device.device_id)
    auto_checkbox.setState_(1 if device.auto_brightness_enabled else 0)
    stack.addArrangedSubview_(auto_checkbox)

    native_ui.add_separator(stack)
    stack.addArrangedSubview_(native_ui.make_label("Color Calibration", bold=True, size=13.0))

    controls: dict[str, object] = {"auto_brightness_checkbox": auto_checkbox}
    red, green, blue = device.channel_gains
    for label, channel, gain, action, tint in (
        ("Red", "red", red, "setDeviceRedGain:", NSColor.systemRedColor()),
        ("Green", "green", green, "setDeviceGreenGain:", NSColor.systemGreenColor()),
        ("Blue", "blue", blue, "setDeviceBlueGain:", NSColor.systemBlueColor()),
    ):
        slider = native_ui.make_slider(
            min_value=MIN_CHANNEL_GAIN * 100.0,
            max_value=MAX_CHANNEL_GAIN * 100.0,
            value=gain * 100.0,
            target=target,
            action=action,
            identifier=device.device_id,
        )
        native_ui.constrain_width(slider, 200.0)
        try:
            slider.setTrackFillColor_(tint)
        except Exception:
            pass
        stack.addArrangedSubview_(native_ui.make_row(label, slider))
        controls[f"{channel}_slider"] = slider

    reset_button = native_ui.make_button("Reset to Default", target, "resetDeviceColorCalibration:")
    reset_button.setRepresentedObject_(device.device_id)
    stack.addArrangedSubview_(reset_button)
    controls["reset_button"] = reset_button

    return stack, controls


def _build_colors_screen_bar_pane(target: StatusBarController):
    stack = native_ui.make_stack(orientation="vertical", spacing=16.0)
    outer, inner = native_ui.make_card("Colors & Screen Bar")

    inner.addArrangedSubview_(
        native_ui.make_row("Colors", native_ui.make_button("Customize Colors…", target, "openColorsWindow:"))
    )
    alcove_popup = make_alcove_compat_popup(target)
    inner.addArrangedSubview_(native_ui.make_row("Alcove Compatibility", alcove_popup))
    native_ui.add_separator(inner)
    wraps_checkbox = native_ui.make_checkbox(
        "Extend glow along the menu bar", target, "toggleScreenBarWrapsMenuBar:"
    )
    inner.addArrangedSubview_(wraps_checkbox)

    stack.addArrangedSubview_(outer)
    fields = {"alcove_compat_popup": alcove_popup}
    buttons = {"screen_bar_wraps_menu_bar": wraps_checkbox}
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def _build_closed_lid_pane(target: StatusBarController):
    stack = native_ui.make_stack(orientation="vertical", spacing=16.0)
    outer, inner = native_ui.make_card("Closed-Lid Awake")

    policy_popup = make_closed_lid_awake_policy_popup(target)
    inner.addArrangedSubview_(native_ui.make_row("Policy", policy_popup))

    grace_field = native_ui.make_field(f"{target.settings.closed_lid_grace_minutes:g}")
    native_ui.constrain_width(grace_field, 56.0)
    grace_controls = native_ui.make_stack(orientation="horizontal", spacing=8.0)
    grace_controls.addArrangedSubview_(grace_field)
    grace_controls.addArrangedSubview_(native_ui.make_label("min", secondary=True))
    grace_controls.addArrangedSubview_(native_ui.make_button("Apply", target, "applyClosedLidGraceMinutes:"))
    inner.addArrangedSubview_(
        native_ui.make_row(
            "Wait before releasing",
            grace_controls,
            help_text=(
                "A buffer against a false “done” reading -- e.g. a command still "
                "running with no events for a stretch -- closing the lid into sleep."
            ),
        )
    )

    stack.addArrangedSubview_(outer)
    fields = {"closed_lid_awake_policy_popup": policy_popup, "closed_lid_grace_field": grace_field}
    return native_ui.wrap_in_scroll_pane(stack), fields


def _build_led_behavior_pane(target: StatusBarController):
    stack = native_ui.make_stack(orientation="vertical", spacing=16.0)
    outer, inner = native_ui.make_card("LED Behavior")

    idle_checkbox = native_ui.make_checkbox("Dim further after being idle", target, "toggleIdleDim:")
    inner.addArrangedSubview_(idle_checkbox)

    minutes_field = native_ui.make_field(f"{target.settings.idle_dim_after_minutes:g}")
    native_ui.constrain_width(minutes_field, 48.0)
    fraction_field = native_ui.make_field(f"{round(target.settings.idle_dim_fraction * 100)}")
    native_ui.constrain_width(fraction_field, 48.0)
    idle_controls = native_ui.make_stack(orientation="horizontal", spacing=6.0)
    idle_controls.addArrangedSubview_(native_ui.make_label("After", secondary=True))
    idle_controls.addArrangedSubview_(minutes_field)
    idle_controls.addArrangedSubview_(native_ui.make_label("min, dim to", secondary=True))
    idle_controls.addArrangedSubview_(fraction_field)
    idle_controls.addArrangedSubview_(native_ui.make_label("%", secondary=True))
    idle_controls.addArrangedSubview_(native_ui.make_button("Apply", target, "applyIdleDimSettings:"))
    inner.addArrangedSubview_(idle_controls)

    native_ui.add_separator(inner)

    focus_checkbox = native_ui.make_checkbox(
        "Dim while a macOS Focus is active",
        target,
        "toggleFocusSync:",
        help_text=(
            "Requires granting Full Disk Access to SidePulse's background process "
            "in System Settings -- otherwise this has no effect."
        ),
    )
    inner.addArrangedSubview_(focus_checkbox)

    stack.addArrangedSubview_(outer)
    fields = {"idle_dim_minutes_field": minutes_field, "idle_dim_fraction_field": fraction_field}
    buttons = {"idle_dim_enabled": idle_checkbox, "focus_sync_enabled": focus_checkbox}
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def _build_hooks_pane(target: StatusBarController):
    stack = native_ui.make_stack(orientation="vertical", spacing=16.0)
    outer, inner = native_ui.make_card("Agent Hooks")

    hook_statuses: dict[str, object] = {}
    for index, provider in enumerate(HOOK_PROVIDERS):
        status_label = native_ui.make_label("", secondary=True, size=12.0)
        inner.addArrangedSubview_(native_ui.make_row(provider_spec(provider).label, status_label))

        selector = provider.title()
        buttons_row = native_ui.make_stack(orientation="horizontal", spacing=8.0)
        buttons_row.addArrangedSubview_(native_ui.make_button("Install", target, f"install{selector}Hooks:"))
        buttons_row.addArrangedSubview_(native_ui.make_button("Uninstall", target, f"uninstall{selector}Hooks:"))
        indent_row = native_ui.make_stack(orientation="horizontal", spacing=12.0)
        spacer = native_ui.make_label("")
        native_ui.constrain_width(spacer, native_ui.ROW_LABEL_WIDTH)
        indent_row.addArrangedSubview_(spacer)
        indent_row.addArrangedSubview_(buttons_row)
        inner.addArrangedSubview_(indent_row)

        if index < len(HOOK_PROVIDERS) - 1:
            native_ui.add_separator(inner)
        hook_statuses[provider] = status_label

    stack.addArrangedSubview_(outer)
    fields = {f"{provider}_hook_status": status for provider, status in hook_statuses.items()}
    return native_ui.wrap_in_scroll_pane(stack), fields


def _build_sessions_pane(target: StatusBarController):
    stack = native_ui.make_stack(orientation="vertical", spacing=16.0)
    outer, inner = native_ui.make_card("Sessions")

    codex_checkbox = native_ui.make_checkbox(
        "CLI fallback: Codex transcripts", target, "toggleCodexTranscripts:"
    )
    inner.addArrangedSubview_(codex_checkbox)
    claude_checkbox = native_ui.make_checkbox(
        "CLI fallback: Claude transcripts", target, "toggleClaudeTranscripts:"
    )
    inner.addArrangedSubview_(claude_checkbox)

    native_ui.add_separator(inner)
    inner.addArrangedSubview_(native_ui.make_label("Open Sessions With", bold=True, size=14.0))

    provider_openers: dict[str, object] = {}
    for provider in provider_session_opener_providers():
        popup = make_provider_opener_popup(provider, target)
        inner.addArrangedSubview_(native_ui.make_row(provider_spec(provider).label, popup))
        provider_openers[provider] = popup

    stack.addArrangedSubview_(outer)
    fields = {f"{provider}_session_opener": popup for provider, popup in provider_openers.items()}
    buttons = {"codex_transcripts": codex_checkbox, "claude_transcripts": claude_checkbox}
    return native_ui.wrap_in_scroll_pane(stack), fields, buttons


def _build_battery_pane(target: StatusBarController):
    stack = native_ui.make_stack(orientation="vertical", spacing=16.0)
    outer, inner = native_ui.make_card("Battery")

    battery_leds = native_ui.make_checkbox("Show battery on LEDs", target, "setBatteryLedDisplayFromCheckbox:")
    inner.addArrangedSubview_(battery_leds)
    battery_power_preview = native_ui.make_checkbox(
        "Show battery for 7s on plug/unplug", target, "setBatteryPowerPreviewFromCheckbox:"
    )
    inner.addArrangedSubview_(battery_power_preview)

    stack.addArrangedSubview_(outer)
    buttons = {"battery_leds": battery_leds, "battery_power_preview": battery_power_preview}
    return native_ui.wrap_in_scroll_pane(stack), buttons


def _build_lid_animations_pane(target: StatusBarController):
    stack = native_ui.make_stack(orientation="vertical", spacing=16.0)

    closed_outer, closed_inner = native_ui.make_card("Lid Closed")
    closed_duration = native_ui.make_field("")
    native_ui.constrain_width(closed_duration, 60.0)
    closed_inner.addArrangedSubview_(native_ui.make_row("Duration (sec)", closed_duration))
    closed_scroll, closed_program = native_ui.make_text_editor("")
    closed_inner.addArrangedSubview_(closed_scroll)
    closed_buttons = native_ui.make_stack(orientation="horizontal", spacing=8.0)
    closed_buttons.addArrangedSubview_(native_ui.make_button("Preview", target, "previewLidClosedAnimation:"))
    closed_buttons.addArrangedSubview_(native_ui.make_button("Reset", target, "resetLidClosedAnimation:"))
    closed_inner.addArrangedSubview_(closed_buttons)
    stack.addArrangedSubview_(closed_outer)

    open_outer, open_inner = native_ui.make_card("Lid Open")
    open_duration = native_ui.make_field("")
    native_ui.constrain_width(open_duration, 60.0)
    open_inner.addArrangedSubview_(native_ui.make_row("Duration (sec)", open_duration))
    open_scroll, open_program = native_ui.make_text_editor("")
    open_inner.addArrangedSubview_(open_scroll)
    open_buttons = native_ui.make_stack(orientation="horizontal", spacing=8.0)
    open_buttons.addArrangedSubview_(native_ui.make_button("Preview", target, "previewLidOpenAnimation:"))
    open_buttons.addArrangedSubview_(native_ui.make_button("Reset", target, "resetLidOpenAnimation:"))
    open_buttons.addArrangedSubview_(native_ui.make_button("Save Animations", target, "saveLidAnimations:"))
    open_inner.addArrangedSubview_(open_buttons)
    stack.addArrangedSubview_(open_outer)

    fields = {
        "closed_animation_program": closed_program,
        "closed_animation_duration": closed_duration,
        "open_animation_program": open_program,
        "open_animation_duration": open_duration,
    }
    return native_ui.wrap_in_scroll_pane(stack), fields


def _build_debug_pane(target: StatusBarController):
    stack = native_ui.make_stack(orientation="vertical", spacing=16.0)
    outer, inner = native_ui.make_card("Debug Log")

    status_label = native_ui.make_label("", secondary=True, size=12.0)
    inner.addArrangedSubview_(status_label)
    buttons_row = native_ui.make_stack(orientation="horizontal", spacing=8.0)
    buttons_row.addArrangedSubview_(native_ui.make_button("Export CSV", target, "exportDebugCsv:"))
    buttons_row.addArrangedSubview_(native_ui.make_button("Export HTML", target, "exportDebugHtml:"))
    inner.addArrangedSubview_(buttons_row)

    stack.addArrangedSubview_(outer)
    fields = {"debug_log_status": status_label}
    return native_ui.wrap_in_scroll_pane(stack), fields


def build_settings_window(target: StatusBarController) -> NSWindow:
    width, height = 820, 560
    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskResizable
    )
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (width, height)), style, NSBackingStoreBuffered, False,
    )
    window.setTitle_("SidePulse Agent Monitor Settings")
    window.setReleasedWhenClosed_(False)
    window.setMinSize_((640, 420))
    window.center()

    root = NSView.alloc().init()
    window.setContentView_(root)

    split = NSSplitView.alloc().init()
    split.setVertical_(True)
    split.setDividerStyle_(NSSplitViewDividerStyleThin)
    split.setTranslatesAutoresizingMaskIntoConstraints_(False)
    root.addSubview_(split)

    sidebar_bg = native_ui.make_sidebar_background()
    sidebar_scroll, sidebar_table = native_ui.build_sidebar_table(width=220.0)
    sidebar_bg.addSubview_(sidebar_scroll)
    NSLayoutConstraint.activateConstraints_(
        [
            sidebar_scroll.topAnchor().constraintEqualToAnchor_(sidebar_bg.topAnchor()),
            sidebar_scroll.leadingAnchor().constraintEqualToAnchor_(sidebar_bg.leadingAnchor()),
            sidebar_scroll.trailingAnchor().constraintEqualToAnchor_(sidebar_bg.trailingAnchor()),
            sidebar_scroll.bottomAnchor().constraintEqualToAnchor_(sidebar_bg.bottomAnchor()),
        ]
    )
    sidebar_table.setDataSource_(target)
    sidebar_table.setDelegate_(target)

    content_container = NSView.alloc().init()
    content_container.setTranslatesAutoresizingMaskIntoConstraints_(False)

    split.addSubview_(sidebar_bg)
    split.addSubview_(content_container)
    sidebar_bg.widthAnchor().constraintEqualToConstant_(220.0).setActive_(True)
    sidebar_bg.widthAnchor().constraintGreaterThanOrEqualToConstant_(160.0).setActive_(True)
    split.setHoldingPriority_forSubviewAtIndex_(250, 0)

    footer = native_ui.make_stack(orientation="vertical", spacing=2.0)
    message = native_ui.make_label("", secondary=True, size=12.0)
    settings_path = native_ui.make_label("", secondary=True, size=11.0)
    footer.addArrangedSubview_(message)
    footer.addArrangedSubview_(settings_path)
    root.addSubview_(footer)

    NSLayoutConstraint.activateConstraints_(
        [
            split.topAnchor().constraintEqualToAnchor_(root.topAnchor()),
            split.leadingAnchor().constraintEqualToAnchor_(root.leadingAnchor()),
            split.trailingAnchor().constraintEqualToAnchor_(root.trailingAnchor()),
            split.bottomAnchor().constraintEqualToAnchor_constant_(footer.topAnchor(), -8.0),
            footer.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 16.0),
            footer.trailingAnchor().constraintEqualToAnchor_constant_(root.trailingAnchor(), -16.0),
            footer.bottomAnchor().constraintEqualToAnchor_constant_(root.bottomAnchor(), -10.0),
        ]
    )

    devices_pane, device_controls = _build_devices_pane(target)
    colors_pane, colors_fields, colors_buttons = _build_colors_screen_bar_pane(target)
    closed_lid_pane, closed_lid_fields = _build_closed_lid_pane(target)
    led_behavior_pane, led_behavior_fields, led_behavior_buttons = _build_led_behavior_pane(target)
    hooks_pane, hooks_fields = _build_hooks_pane(target)
    sessions_pane, sessions_fields, sessions_buttons = _build_sessions_pane(target)
    battery_pane, battery_buttons = _build_battery_pane(target)
    lid_animations_pane, lid_animations_fields = _build_lid_animations_pane(target)
    debug_pane, debug_fields = _build_debug_pane(target)

    panes = {
        "devices": devices_pane,
        "colors_screen_bar": colors_pane,
        "closed_lid": closed_lid_pane,
        "led_behavior": led_behavior_pane,
        "hooks": hooks_pane,
        "sessions": sessions_pane,
        "battery": battery_pane,
        "lid_animations": lid_animations_pane,
        "debug": debug_pane,
    }
    for key, pane in panes.items():
        content_container.addSubview_(pane)
        NSLayoutConstraint.activateConstraints_(
            [
                pane.topAnchor().constraintEqualToAnchor_(content_container.topAnchor()),
                pane.leadingAnchor().constraintEqualToAnchor_(content_container.leadingAnchor()),
                pane.trailingAnchor().constraintEqualToAnchor_(content_container.trailingAnchor()),
                pane.bottomAnchor().constraintEqualToAnchor_(content_container.bottomAnchor()),
            ]
        )
        pane.setHidden_(key != SETTINGS_SIDEBAR_ITEMS[0][0])

    target.settings_sidebar_table = sidebar_table
    target.settings_panes = panes

    target.settings_fields = {
        **hooks_fields,
        **sessions_fields,
        **lid_animations_fields,
        **debug_fields,
        **colors_fields,
        **closed_lid_fields,
        **led_behavior_fields,
        "message": message,
        "settings_path": settings_path,
    }
    target.settings_buttons = {
        **sessions_buttons,
        **battery_buttons,
        **colors_buttons,
        **led_behavior_buttons,
    }
    target.device_settings_controls = device_controls
    return window


MODE_COLOR_DISPLAY_LABELS: dict[str, str] = {
    "idle": "Idle",
    "working": "Working",
    "done": "Done",
    "ask": "Ask (waiting / blocked)",
}

COLOR_SWATCH_SIZE = 22
COLOR_SWATCH_GAP = 8
COLOR_ROW_HEIGHT = 40


def _build_agent_or_mode_color_row(
    row_key: tuple[str, str],
    row_label: str,
    current: str,
    palette: tuple[str, ...],
    target,
    swatch_selector: str,
    swatch_represented: dict,
    custom_selector: str,
    custom_represented: dict,
    swatches: dict,
    hex_labels: dict,
):
    """One "label ... [swatch swatch swatch ... + hex] " row shared by the
    Agent Colors and Mode Colors cards. The swatch strip is a fixed-size
    area (see native_ui.make_fixed_area) holding frame-positioned swatch
    buttons -- a palette grid is inherently a custom-drawn strip, not a
    column of stock controls, so this is the sanctioned escape hatch from
    Auto Layout rather than a rewrite of add_color_swatch/
    add_custom_color_swatch themselves."""
    width = len(palette) * (COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP) + COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP + 80
    container = native_ui.make_fixed_area(width, 24.0)
    x = 0
    for palette_hex in palette:
        button = add_color_swatch(
            container, palette_hex, x, 1, target, swatch_selector, {**swatch_represented, "hex": palette_hex}
        )
        set_swatch_selected(button, palette_hex.upper() == current.upper())
        swatches[(row_key, palette_hex)] = button
        x += COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP
    add_custom_color_swatch(container, x, 1, target, custom_selector, custom_represented)
    x += COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP
    hex_labels[row_key] = add_label(container, current, x, 3, 80, 18)
    return native_ui.make_row(row_label, container)


def build_colors_window(target: StatusBarController) -> NSWindow:
    width, height = 640, 700
    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskResizable
    )
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (width, height)), style, NSBackingStoreBuffered, False,
    )
    window.setTitle_("SidePulse Colors")
    window.setReleasedWhenClosed_(False)
    window.setMinSize_((560, 420))
    window.center()

    root = NSView.alloc().init()
    window.setContentView_(root)
    # A window whose entire content view hierarchy is pure Auto Layout
    # (true here, unlike the Settings window's NSSplitView, which imposes
    # a real frame on its children outside the constraint solver) fits
    # its own size to that content's computed fitting size -- and for a
    # scroll view with nothing else bounding it, that "fit" is its full,
    # unscrolled document size: a several-thousand-point-tall window
    # instead of one that scrolls. Giving root an explicit, required size
    # here breaks that pull; anything below still just scrolls normally.
    root.setTranslatesAutoresizingMaskIntoConstraints_(False)
    root.widthAnchor().constraintGreaterThanOrEqualToConstant_(width).setActive_(True)
    root.heightAnchor().constraintEqualToConstant_(height).setActive_(True)

    # Live Preview stays pinned at the top, outside the scroll area -- it's
    # a continuous creative-feedback tool you check while adjusting colors
    # below, not a "setting" you visit once and move past.
    preview_outer, preview_inner = native_ui.make_card("Live Preview")
    root.addSubview_(preview_outer)

    preview_scenario_popup = make_preview_scenario_popup(target)
    preview_inner.addArrangedSubview_(native_ui.make_row("Scenario", preview_scenario_popup))

    preview_rows: list[dict[str, object]] = []
    for led_count, device_label in ((2, "SidePulse Dot (2 LEDs)"), (8, "SidePulse Pro (8 LEDs)")):
        device_stack = native_ui.make_stack(orientation="vertical", spacing=6.0)
        device_stack.addArrangedSubview_(native_ui.make_label(device_label, size=13.0))
        dots_width = led_count * (COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP) - COLOR_SWATCH_GAP
        dots_container = native_ui.make_fixed_area(dots_width, COLOR_SWATCH_SIZE)
        dots = []
        dot_x = 0
        for _index in range(led_count):
            dots.append(add_preview_dot(dots_container, dot_x, 0))
            dot_x += COLOR_SWATCH_SIZE + COLOR_SWATCH_GAP
        device_stack.addArrangedSubview_(dots_container)
        legend = native_ui.make_label("", secondary=True, size=11.0)
        device_stack.addArrangedSubview_(legend)
        preview_inner.addArrangedSubview_(device_stack)
        preview_rows.append({"led_count": led_count, "dots": dots, "legend": legend})

    # Everything else scrolls independently below the pinned preview.
    scroll_stack = native_ui.make_stack(orientation="vertical", spacing=16.0)

    behavior_outer, behavior_inner = native_ui.make_card("Blend Mode & Behavior")
    blend_popup = make_blend_mode_popup(target)
    behavior_inner.addArrangedSubview_(native_ui.make_row("Blend Mode", blend_popup))
    blend_description = native_ui.make_label("", secondary=True, size=12.0)
    behavior_inner.addArrangedSubview_(blend_description)
    urgency_alert_checkbox = native_ui.make_checkbox(
        "Alert when blocked or waiting",
        target,
        "toggleUrgencyAlert:",
        help_text=(
            "In Round-Robin/Cycle, a blocked or waiting agent shows the Ask "
            "color instead of its own, so it stands out."
        ),
    )
    behavior_inner.addArrangedSubview_(urgency_alert_checkbox)
    done_celebration_checkbox = native_ui.make_checkbox(
        "Celebrate when finished",
        target,
        "toggleDoneCelebration:",
        help_text="A brief twinkle plays before settling into the Done color.",
    )
    behavior_inner.addArrangedSubview_(done_celebration_checkbox)
    native_ui.add_separator(behavior_inner)

    speed_field = native_ui.make_field("")
    native_ui.constrain_width(speed_field, 56.0)
    speed_controls = native_ui.make_stack(orientation="horizontal", spacing=8.0)
    speed_controls.addArrangedSubview_(speed_field)
    speed_controls.addArrangedSubview_(native_ui.make_label("sec/breath", secondary=True))
    speed_controls.addArrangedSubview_(native_ui.make_button("Apply", target, "applyCycleSpeed:"))
    behavior_inner.addArrangedSubview_(native_ui.make_row("Global Speed", speed_controls))

    round_robin_use_global = native_ui.make_checkbox(
        "Round-Robin: use global", target, "toggleRoundRobinUseGlobalSpeed:"
    )
    round_robin_speed_field = native_ui.make_field("")
    native_ui.constrain_width(round_robin_speed_field, 56.0)
    round_robin_row = native_ui.make_stack(orientation="horizontal", spacing=8.0)
    round_robin_row.addArrangedSubview_(round_robin_use_global)
    round_robin_row.addArrangedSubview_(round_robin_speed_field)
    round_robin_row.addArrangedSubview_(native_ui.make_button("Apply", target, "applyRoundRobinSpeed:"))
    behavior_inner.addArrangedSubview_(round_robin_row)

    cycle_use_global = native_ui.make_checkbox("Cycle: use global", target, "toggleCycleUseGlobalSpeed:")
    cycle_speed_field = native_ui.make_field("")
    native_ui.constrain_width(cycle_speed_field, 56.0)
    cycle_row = native_ui.make_stack(orientation="horizontal", spacing=8.0)
    cycle_row.addArrangedSubview_(cycle_use_global)
    cycle_row.addArrangedSubview_(cycle_speed_field)
    cycle_row.addArrangedSubview_(native_ui.make_button("Apply", target, "applyCycleModeSpeed:"))
    behavior_inner.addArrangedSubview_(cycle_row)
    scroll_stack.addArrangedSubview_(behavior_outer)
    native_ui.stretch_to_stack_width(scroll_stack, behavior_outer)

    swatches: dict[tuple[tuple[str, str], str], object] = {}
    hex_labels: dict[tuple[str, str], object] = {}

    agent_outer, agent_inner = native_ui.make_card("Agent Colors")
    for spec in PROVIDER_SPECS:
        current = target.settings.colors.agent_color(spec.provider)
        agent_inner.addArrangedSubview_(
            _build_agent_or_mode_color_row(
                ("agent", spec.provider),
                spec.label,
                current,
                CURATED_PALETTE,
                target,
                "selectAgentColorSwatch:",
                {"provider": spec.provider},
                "openCustomAgentColor:",
                {"provider": spec.provider},
                swatches,
                hex_labels,
            )
        )
    scroll_stack.addArrangedSubview_(agent_outer)
    native_ui.stretch_to_stack_width(scroll_stack, agent_outer)

    mode_outer, mode_inner = native_ui.make_card("Mode Colors")
    for key in MODE_COLOR_KEYS:
        current = target.settings.colors.mode_color(key)
        mode_inner.addArrangedSubview_(
            _build_agent_or_mode_color_row(
                ("mode", key),
                MODE_COLOR_DISPLAY_LABELS[key],
                current,
                CURATED_PALETTE[:6],
                target,
                "selectModeColorSwatch:",
                {"key": key},
                "openCustomModeColor:",
                {"key": key},
                swatches,
                hex_labels,
            )
        )
    scroll_stack.addArrangedSubview_(mode_outer)
    native_ui.stretch_to_stack_width(scroll_stack, mode_outer)

    anim_outer, anim_inner = native_ui.make_card("Animation Style")
    animation_popups: dict[str, object] = {}
    for key in ANIMATION_MODE_KEYS:
        popup = make_animation_style_popup(target, key)
        select_animation_style(popup, target.settings.colors.animation_style(key))
        animation_popups[key] = popup
        anim_inner.addArrangedSubview_(native_ui.make_row(MODE_COLOR_DISPLAY_LABELS[key], popup))
    scroll_stack.addArrangedSubview_(anim_outer)
    native_ui.stretch_to_stack_width(scroll_stack, anim_outer)

    fade_outer, fade_inner = native_ui.make_card("Fade Intensity")
    fade_inner.addArrangedSubview_(
        native_ui.make_label(
            "How far each pulsing mode dims down and brightens up, as % of its color",
            secondary=True,
            size=11.0,
        )
    )
    fade_fields: dict[str, dict[str, object]] = {}
    for key in FADE_MODE_KEYS:
        floor, ceiling = target.settings.colors.fade_range(key)
        controls = native_ui.make_stack(orientation="horizontal", spacing=6.0)
        controls.addArrangedSubview_(native_ui.make_label("Floor", secondary=True, size=11.0))
        floor_field = native_ui.make_field(f"{round(floor * 100)}")
        native_ui.constrain_width(floor_field, 48.0)
        controls.addArrangedSubview_(floor_field)
        controls.addArrangedSubview_(native_ui.make_label("%", secondary=True, size=11.0))
        controls.addArrangedSubview_(native_ui.make_label("Ceiling", secondary=True, size=11.0))
        ceiling_field = native_ui.make_field(f"{round(ceiling * 100)}")
        native_ui.constrain_width(ceiling_field, 48.0)
        controls.addArrangedSubview_(ceiling_field)
        controls.addArrangedSubview_(native_ui.make_label("%", secondary=True, size=11.0))
        fade_fields[key] = {"floor": floor_field, "ceiling": ceiling_field}
        fade_inner.addArrangedSubview_(native_ui.make_row(MODE_COLOR_DISPLAY_LABELS[key], controls))
    fade_inner.addArrangedSubview_(native_ui.make_button("Apply Fade Intensity", target, "applyFadeIntensity:"))
    scroll_stack.addArrangedSubview_(fade_outer)
    native_ui.stretch_to_stack_width(scroll_stack, fade_outer)

    scroll_pane = native_ui.wrap_in_scroll_pane(scroll_stack)
    root.addSubview_(scroll_pane)

    # Reset/Preview-live/Done stay pinned at the bottom too, matching every
    # native macOS dialog's own convention of action buttons that don't
    # scroll away with the content above them.
    footer = NSView.alloc().init()
    footer.setTranslatesAutoresizingMaskIntoConstraints_(False)
    footer_left = native_ui.make_stack(orientation="horizontal", spacing=12.0)
    footer_left.addArrangedSubview_(native_ui.make_button("Reset to Defaults", target, "resetColorsToDefaults:"))
    live_toggle = native_ui.make_checkbox("Preview live on device", target, "toggleColorPreviewLive:")
    footer_left.addArrangedSubview_(live_toggle)
    done_button = native_ui.make_button("Done", target, "closeColorsWindow:")
    footer.addSubview_(footer_left)
    footer.addSubview_(done_button)
    NSLayoutConstraint.activateConstraints_(
        [
            footer_left.leadingAnchor().constraintEqualToAnchor_(footer.leadingAnchor()),
            footer_left.centerYAnchor().constraintEqualToAnchor_(footer.centerYAnchor()),
            done_button.trailingAnchor().constraintEqualToAnchor_(footer.trailingAnchor()),
            done_button.centerYAnchor().constraintEqualToAnchor_(footer.centerYAnchor()),
            footer.heightAnchor().constraintGreaterThanOrEqualToConstant_(28.0),
        ]
    )
    root.addSubview_(footer)

    NSLayoutConstraint.activateConstraints_(
        [
            preview_outer.topAnchor().constraintEqualToAnchor_constant_(root.topAnchor(), 16.0),
            preview_outer.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 16.0),
            preview_outer.trailingAnchor().constraintEqualToAnchor_constant_(root.trailingAnchor(), -16.0),
            scroll_pane.topAnchor().constraintEqualToAnchor_constant_(preview_outer.bottomAnchor(), 16.0),
            scroll_pane.leadingAnchor().constraintEqualToAnchor_(root.leadingAnchor()),
            scroll_pane.trailingAnchor().constraintEqualToAnchor_(root.trailingAnchor()),
            scroll_pane.bottomAnchor().constraintEqualToAnchor_constant_(footer.topAnchor(), -12.0),
            footer.leadingAnchor().constraintEqualToAnchor_constant_(root.leadingAnchor(), 16.0),
            footer.trailingAnchor().constraintEqualToAnchor_constant_(root.trailingAnchor(), -16.0),
            footer.bottomAnchor().constraintEqualToAnchor_constant_(root.bottomAnchor(), -14.0),
        ]
    )

    target.color_swatches = swatches
    target.color_hex_labels = hex_labels
    target.color_fields = {
        "preview_scenario_popup": preview_scenario_popup,
        "blend_mode_popup": blend_popup,
        "blend_description": blend_description,
        "urgency_alert_checkbox": urgency_alert_checkbox,
        "done_celebration_checkbox": done_celebration_checkbox,
        "speed_field": speed_field,
        "round_robin_use_global": round_robin_use_global,
        "round_robin_speed_field": round_robin_speed_field,
        "cycle_use_global": cycle_use_global,
        "cycle_speed_field": cycle_speed_field,
        "live_toggle": live_toggle,
        "fade_fields": fade_fields,
        "animation_popups": animation_popups,
    }
    target.color_preview_rows = preview_rows
    refresh_blend_and_speed_fields(target)
    return window


def refresh_blend_and_speed_fields(target: StatusBarController) -> None:
    colors = target.settings.colors
    fields = target.color_fields
    popup = fields.get("blend_mode_popup")
    if popup is not None:
        select_blend_mode(popup, colors.blend_mode)
    description = fields.get("blend_description")
    if description is not None:
        description.setStringValue_(BLEND_MODE_DESCRIPTIONS.get(colors.blend_mode, ""))
        description.setToolTip_(colors_module.BLEND_MODE_TOOLTIPS.get(colors.blend_mode, ""))
    checkbox = fields.get("urgency_alert_checkbox")
    if checkbox is not None:
        set_checkbox_state(checkbox, colors.round_robin_urgency_alert)
    celebration_checkbox = fields.get("done_celebration_checkbox")
    if celebration_checkbox is not None:
        set_checkbox_state(celebration_checkbox, colors.done_celebration_enabled)
    speed_field = fields.get("speed_field")
    if speed_field is not None:
        set_field_value(speed_field, f"{colors.cycle_speed_seconds:g}")
    for mode_key, use_global_key, field_key in (
        (BLEND_MODE_ROUND_ROBIN, "round_robin_use_global", "round_robin_speed_field"),
        (BLEND_MODE_CYCLE, "cycle_use_global", "cycle_speed_field"),
    ):
        uses_global = colors.uses_global_speed(mode_key)
        use_global_checkbox = fields.get(use_global_key)
        mode_field = fields.get(field_key)
        if use_global_checkbox is not None:
            set_checkbox_state(use_global_checkbox, uses_global)
        if mode_field is not None:
            set_field_value(mode_field, f"{colors.effective_speed_seconds(mode_key):g}")
            mode_field.setEnabled_(not uses_global)


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


def nscolor_from_hex(hex_value: str) -> "NSColor":
    red, green, blue = colors_module.hex_to_rgb(colors_module.normalize_hex(hex_value, "#000000"))
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(red / 255.0, green / 255.0, blue / 255.0, 1.0)


def hex_from_nscolor(nscolor) -> str:
    try:
        rgb = nscolor.colorUsingColorSpace_(NSColor.sRGBColorSpace())
    except Exception:
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


def select_blend_mode(popup, blend_mode: str) -> None:
    for index in range(popup.numberOfItems()):
        payload = popup.itemAtIndex_(index).representedObject()
        if isinstance(payload, dict) and payload.get("blend_mode") == blend_mode:
            popup.selectItemAtIndex_(index)
            return


def make_preview_scenario_popup(target):
    popup = native_ui.make_popup_button(target, "setPreviewScenario:")
    for scenario in colors_module.PREVIEW_SCENARIO_CHOICES:
        popup.addItemWithTitle_(colors_module.PREVIEW_SCENARIO_LABELS[scenario])
        popup.lastItem().setRepresentedObject_({"scenario": scenario})
    return popup


def select_preview_scenario(popup, scenario: str) -> None:
    for index in range(popup.numberOfItems()):
        payload = popup.itemAtIndex_(index).representedObject()
        if isinstance(payload, dict) and payload.get("scenario") == scenario:
            popup.selectItemAtIndex_(index)
            return


ANIMATION_STYLE_DISPLAY_LABELS: dict[str, str] = {
    "pulse": "Pulse (breathe)",
    "roll": "Roll (chase)",
    "solid": "Solid (no animation)",
    "blink": "Blink (hard on/off)",
}


def make_animation_style_popup(target, mode_key: str):
    popup = native_ui.make_popup_button(target, "setAnimationStyle:")
    for style in ANIMATION_STYLE_CHOICES:
        popup.addItemWithTitle_(ANIMATION_STYLE_DISPLAY_LABELS.get(style, style.title()))
        popup.lastItem().setRepresentedObject_({"mode_key": mode_key, "style": style})
    return popup


def select_animation_style(popup, style: str) -> None:
    for index in range(popup.numberOfItems()):
        payload = popup.itemAtIndex_(index).representedObject()
        if isinstance(payload, dict) and payload.get("style") == style:
            popup.selectItemAtIndex_(index)
            return


def make_alcove_compat_popup(target):
    popup = native_ui.make_popup_button(target, "setAlcoveCompatibilityMode:")
    labels = {
        ALCOVE_COMPAT_AUTO: "Auto",
        ALCOVE_COMPAT_ALWAYS: "Always",
        ALCOVE_COMPAT_NEVER: "Never",
    }
    for mode in ALCOVE_COMPAT_CHOICES:
        popup.addItemWithTitle_(labels[mode])
        popup.lastItem().setRepresentedObject_({"alcove_mode": mode})
    return popup


def select_alcove_compat_mode(popup, mode: str) -> None:
    for index in range(popup.numberOfItems()):
        payload = popup.itemAtIndex_(index).representedObject()
        if isinstance(payload, dict) and payload.get("alcove_mode") == mode:
            popup.selectItemAtIndex_(index)
            return


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
    for index in range(popup.numberOfItems()):
        payload = popup.itemAtIndex_(index).representedObject()
        if isinstance(payload, dict) and payload.get("policy") == policy:
            popup.selectItemAtIndex_(index)
            return


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
    parts = [f"{status.display_name or status.provider} ({MODE_LABELS.get(status.mode, status.mode.value)})" for status in statuses]
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
    for index in range(popup.numberOfItems()):
        payload = popup.itemAtIndex_(index).representedObject()
        if isinstance(payload, dict) and payload.get("action") == action:
            popup.selectItemAtIndex_(index)
            return


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
    brightness: int | float = 255,
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


def hook_status_text(config: ProviderConfig) -> str:
    if not config.exists:
        return f"Not installed - config will be created at {config.config_path}"
    if config.hook_events:
        event_count = len(config.hook_events)
        suffix = "event" if event_count == 1 else "events"
        return f"Installed ({event_count} {suffix})"
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


def build_session_menu_item(
    status: AgentStatus,
    now: datetime,
    target: StatusBarController,
    *,
    width: float | None = None,
) -> NSMenuItem:
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        native_session_menu_title(status),
        "openSessionPrimary:",
        "",
    )
    item.setTarget_(target)
    item.setRepresentedObject_(status)
    image = session_row_icon_for_status(status)
    if image is not None:
        item.setImage_(image)
    return item


def native_session_menu_title(status: AgentStatus) -> str:
    title, project = session_title_parts(status)
    parts = [title]
    if project:
        parts.append(project)
    return "  ".join(parts)


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


def app_icon(path: str):
    if not Path(path).exists():
        return None
    image = NSWorkspace.sharedWorkspace().iconForFile_(path)
    if image is not None:
        image.setSize_((18, 18))
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


def recent_statuses(snapshot) -> list[AgentStatus]:
    statuses = list(snapshot.statuses)
    if not statuses:
        statuses = list(snapshot.stale_statuses)
    statuses.sort(key=lambda status: (status.priority, -status.updated_at.timestamp()))
    return statuses[:12]


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
    run_status_bar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
