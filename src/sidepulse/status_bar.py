from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass
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
        NSCompositingOperationSourceOver,
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSImage,
        NSMenu,
        NSMenuItem,
        NSOffState,
        NSOnState,
        NSPopUpButton,
        NSScrollView,
        NSSavePanel,
        NSSlider,
        NSStatusBar,
        NSTextField,
        NSTextView,
        NSView,
        NSWorkspace,
        NSWindow,
        NSWindowStyleMaskClosable,
        NSWindowStyleMaskMiniaturizable,
        NSWindowStyleMaskTitled,
        NSVariableStatusItemLength,
    )
    from Foundation import NSObject, NSString, NSTimer, NSURL
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
    AgentLedController,
    apply_brightness,
    brightness_percent,
    normalize_brightness,
    normalized_device_name,
    program_for_display_state,
    write_mode_to_leds,
    display_state_for_mode,
)
from .virtual_device import VIRTUAL_DEVICE_ID, VIRTUAL_DEVICE_NAME, VirtualStatusDevice
from .lid_sleep import (
    LID_POLL_SECONDS,
    ClosedLidAwakeController,
    read_lid_closed,
    sleep_helper_install_command,
    sleep_helper_installed,
)
from .models import AgentMode, AgentStatus
from .providers import (
    HOOK_PROVIDERS,
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
    session_open_action_label,
    session_open_target,
)
from .settings import (
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
        self.settings_fields = {}
        self.settings_buttons = {}
        self.setup_fields = {}
        self.setup_buttons = {}
        self.last_snapshot = None
        self.last_battery_snapshot = None
        self.last_battery_error = None
        self.last_power_connected = None
        self.battery_preview_until = 0.0
        self.current_state = STATE_IDLE
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
            self.sync_leds(AgentMode.BLOCKED_ERROR, None, LED_DISPLAY_AGENT)
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
        )
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
        self.refresh_settings_window()
        self.settings_window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

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
        controller.brightness = device.brightness
        return controller

    def battery_controller_for_device(self, device: StatusBarDevice) -> BatteryLedController:
        controller = self.battery_led_controllers_by_device.get(device.device_id)
        if controller is None:
            controller = BatteryLedController(device_path=device.target)
            self.battery_led_controllers_by_device[device.device_id] = controller
        controller.device_path = device.target
        controller.brightness = device.brightness
        return controller

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
        settings = self.settings
        for device in devices:
            if not device.connected:
                continue
            settings = settings.with_remembered_device(
                device_id=device.device_id,
                name=device.name,
                path=str(device.root),
            )
        if settings != self.settings:
            self.settings = settings
            try:
                save_settings(self.settings)
            except Exception as exc:
                log_status_bar(f"device remember error: {exc}")

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
    ) -> None:
        if not self.leds_enabled:
            return

        self.sync_virtual_status_device(mode, battery_snapshot)

        if time.monotonic() < self.led_animation_until_monotonic:
            return

        if self.led_sync_in_flight:
            return
        self.led_sync_in_flight = True
        thread = threading.Thread(
            target=self.sync_leds_worker,
            args=(mode, battery_snapshot, display_kind),
            daemon=True,
        )
        thread.start()

    def sync_virtual_status_device(
        self,
        mode: AgentMode,
        battery_snapshot: BatterySnapshot | None,
    ) -> None:
        if not SCREEN_BAR_FEATURE_ENABLED:
            self.virtual_status_device.hide()
            return
        if not self.settings.virtual_status_device_enabled:
            return
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
                )
            )
        else:
            self.virtual_status_device.set_program(
                program_for_display_state(
                    display_state_for_mode(mode),
                    led_count=8,
                    brightness=device.brightness,
                )
            )

    def sync_leds_worker(
        self,
        mode: AgentMode,
        battery_snapshot: BatterySnapshot | None,
        display_kind: str,
    ) -> None:
        try:
            self.sync_leds_now(mode, battery_snapshot, display_kind)
        finally:
            self.led_sync_in_flight = False

    def sync_leds_now(
        self,
        mode: AgentMode,
        battery_snapshot: BatterySnapshot | None,
        display_kind: str,
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
                result = self.agent_controller_for_device(device).sync_mode(mode)
                label = f"{device.name} {result.label}"

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


def build_settings_window(target: StatusBarController) -> NSWindow:
    width = 680
    height = 1022
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
    window.setTitle_("SidePulse Agent Monitor Settings")
    window.setReleasedWhenClosed_(False)
    window.center()
    content = window.contentView()

    add_label(content, "Agent Hooks", 24, 974, 200, 24)
    hook_statuses = {}
    for index, provider in enumerate(HOOK_PROVIDERS):
        y = 932 - index * 42
        label = provider_spec(provider).label
        add_label(content, label, 32, y, 80, 22)
        hook_statuses[provider] = add_label(content, "", 112, y, 270, 22)
        selector = provider.title()
        add_button(content, "Install", 432, y - 4, 90, 28, target, f"install{selector}Hooks:")
        add_button(content, "Uninstall", 532, y - 4, 100, 28, target, f"uninstall{selector}Hooks:")

    add_separator(content, 24, 776, width - 48)
    add_label(content, "Transcript Monitoring", 24, 742, 240, 24)
    add_label(content, "Open Sessions With", 352, 742, 240, 24)
    codex_transcripts = add_checkbox(
        content,
        "CLI fallback: Codex transcripts",
        32,
        708,
        260,
        24,
        target,
        "toggleCodexTranscripts:",
    )
    claude_transcripts = add_checkbox(
        content,
        "CLI fallback: Claude transcripts",
        32,
        676,
        260,
        24,
        target,
        "toggleClaudeTranscripts:",
    )
    add_label(content, "Codex", 360, 714, 62, 22)
    codex_opener = add_provider_opener_popup(content, "codex", 424, 712, target)
    add_label(content, "Claude", 360, 686, 62, 22)
    claude_opener = add_provider_opener_popup(content, "claude", 424, 684, target)
    add_label(content, "Grok", 360, 658, 62, 22)
    grok_opener = add_provider_opener_popup(content, "grok", 424, 658, target)

    add_separator(content, 24, 656, width - 48)
    add_label(content, "Debug Log", 24, 622, 240, 24)
    debug_log_status = add_label(content, "", 32, 592, 606, 22)
    add_button(content, "Export CSV", 32, 554, 110, 28, target, "exportDebugCsv:")
    add_button(content, "Export HTML", 152, 554, 120, 28, target, "exportDebugHtml:")

    add_separator(content, 24, 534, width - 48)
    add_label(content, "LED Display", 24, 500, 240, 24)
    battery_leds = add_checkbox(
        content,
        "Show battery on LEDs",
        32,
        466,
        260,
        24,
        target,
        "setBatteryLedDisplayFromCheckbox:",
    )
    battery_power_preview = add_checkbox(
        content,
        "Show battery for 7s on plug/unplug",
        32,
        434,
        320,
        24,
        target,
        "setBatteryPowerPreviewFromCheckbox:",
    )

    add_separator(content, 24, 414, width - 48)
    add_label(content, "Lid & Sleep", 24, 380, 240, 24)
    add_label(content, "Lid Closed", 32, 346, 120, 22)
    add_label(content, "Duration", 520, 346, 70, 22)
    closed_duration = add_editable_field(content, "", 590, 344, 48, 24)
    closed_program = add_text_view(content, "", 32, 254, 606, 82)
    add_button(content, "Preview", 32, 216, 90, 28, target, "previewLidClosedAnimation:")
    add_button(content, "Reset", 132, 216, 90, 28, target, "resetLidClosedAnimation:")

    add_label(content, "Lid Open", 32, 180, 120, 22)
    add_label(content, "Duration", 520, 180, 70, 22)
    open_duration = add_editable_field(content, "", 590, 178, 48, 24)
    open_program = add_text_view(content, "", 32, 88, 606, 82)
    add_button(content, "Preview", 32, 50, 90, 28, target, "previewLidOpenAnimation:")
    add_button(content, "Reset", 132, 50, 90, 28, target, "resetLidOpenAnimation:")
    add_button(content, "Save Animations", 492, 50, 146, 28, target, "saveLidAnimations:")

    message = add_label(content, "", 24, 22, width - 48, 22)
    settings_path = add_label(content, "", 24, 4, width - 48, 18)

    target.settings_fields = {
        **{f"{provider}_hook_status": status for provider, status in hook_statuses.items()},
        "debug_log_status": debug_log_status,
        "codex_session_opener": codex_opener,
        "claude_session_opener": claude_opener,
        "grok_session_opener": grok_opener,
        "closed_animation_program": closed_program,
        "closed_animation_duration": closed_duration,
        "open_animation_program": open_program,
        "open_animation_duration": open_duration,
        "message": message,
        "settings_path": settings_path,
    }
    target.settings_buttons = {
        "codex_transcripts": codex_transcripts,
        "claude_transcripts": claude_transcripts,
        "battery_leds": battery_leds,
        "battery_power_preview": battery_power_preview,
    }
    return window


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


def add_provider_opener_popup(parent, provider: str, x: int, y: int, target):
    popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
        ((x, y), (180, 26)), False
    )
    popup.setTarget_(target)
    popup.setAction_("setProviderOpenPreference:")
    for action in provider_open_actions(provider):
        popup.addItemWithTitle_(provider_open_action_label(provider, action))
        popup.lastItem().setRepresentedObject_(
            {"provider": provider, "action": action}
        )
    parent.addSubview_(popup)
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
        result.append(
            StatusBarDevice(
                device_id=device.device_id,
                name=f"{device.name} {suffix}" if suffix else device.name,
                root=device.root,
                target=device.target,
                connected=device.connected,
                display=device.display,
                brightness=device.brightness,
                reason=device.reason,
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
