from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

try:
    import objc
    from AppKit import (
        NSAlert,
        NSAlertFirstButtonReturn,
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
        NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification,
    )
    from Foundation import (
        NSURL,
        NSIndexSet,
        NSMutableAttributedString,
        NSObject,
        NSRunLoop,
        NSRunLoopCommonModes,
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
    reminders_watch,
    usage_stats,
    weather_watch,
)
from . import colors as colors_module
from . import signals as signals_module
from .accessibility_display import (
    AccessibilityDisplayPreferences,
    refresh_accessibility_display_preferences,
)
from .agent_browser import (
    AgentBrowserQuery,
    build_agent_browser_documents,
    project_agent_browser,
)
from .agent_browser_window import (
    AgentBrowserActionPayload,
    AgentBrowserOpenPayload,
    AgentBrowserWindowController,
    build_agent_root_items,
)
from .app_bundle import default_app_bundle_path, running_inside_bundle
from .attention import AttentionProjection, LifecycleMode, project_attention
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
from .capacity_refresh import (
    CapacityRefreshCoordinator,
    RefreshCause,
    RefreshCommitKind,
    RefreshDecisionKind,
    RefreshFailureKind,
    RefreshSourceKey,
    RefreshSourceRegistration,
)
from .capacity_types import (
    CapacitySnapshot,
    CapacitySourceHealth,
    QuotaLaneObservation,
    SourceHealthKind,
    SourceKey,
)
from .collector import (
    CLAUDE_TRANSCRIPT_PROVIDER,
    CODEX_TRANSCRIPT_PROVIDER,
    COMPLETED_VISIBLE_SECONDS,
    AgentMonitor,
    LiveAgentMonitor,
    MonitorSnapshot,
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
    program_for_projection,
    program_for_snapshot,
)
from .completions import (
    COMPLETION_NOTIFY_FRESHNESS_SECONDS as COMPLETION_NOTIFY_FRESHNESS_SECONDS,
)
from .completions import canonical_current_statuses, detect_completion_batch
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
from .freshness import bounded_age_seconds, is_recent
from .install import (
    install_provider_hooks,
    uninstall_provider_hooks,
)
from .installed_agent_inventory import (
    InstalledAgentInventoryResult,
    default_inventory_roots,
    execute_inventory_command,
)
from .installed_agents import (
    SurfacePresence,
    SurfaceSupportLevel,
    installed_surface_registrations,
)
from .interruption_policy import (
    ActionTokenBinding,
    InterruptionRoute,
    action_token_metadata,
    generic_notification_copy,
    issue_action_token,
    resolve_action_token,
)
from .ipc import (
    HookEventServer,
    ProviderRefreshHint,
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
    LedStatusWrite,
    apply_brightness,
    apply_channel_gain_to_program,
    apply_resting_glow_to_program,
    brightness_percent,
    failure_signal_program,
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
from .local_triage import (
    LocalTriageMutationKind,
    LocalTriageState,
    apply_local_triage_mutation,
)
from .macos_notifications import (
    MacOSNotificationClient,
    NotificationAuthorizationState,
)
from .mailbox import (
    AgentMailboxProjection,
    MailboxRow,
    MailboxSectionKind,
    project_canonical_mailbox,
    project_mailbox,
)
from .mailbox_preference_store import (
    load_mailbox_preference_document,
    save_mailbox_preferences_v2,
)
from .mailbox_preferences import (
    MailboxPreference,
    MailboxPreferenceMode,
    MailboxPreferenceProjection,
    apply_mailbox_preferences,
)
from .menu_tracking import (
    ExactBoundarySchedule,
    MenuItemState,
    StableNativeMenuRegistry,
)
from .models import MODE_LABELS, AgentMode, AgentStatus
from .navigation_policy import (
    OperatorActionKind,
    OperatorLocalActionState,
    build_operator_actions,
    resolve_navigation,
)
from .operator_accessibility import status_item_accessibility
from .operator_export import (
    MAX_DEBUG_EXPORT_BYTES,
    MAX_HISTORY_EXPORT_BYTES,
    DebugExportV1,
    HistoryExportV1,
    encode_debug_export,
    encode_history_export,
)
from .operator_history import (
    HistoryEventKind,
    OperatorHistoryProjection,
    RuntimeHistoryEvent,
    aggregate_operator_history,
)
from .operator_history_store import (
    OperatorHistoryRestoreHealth,
    OperatorHistoryStore,
    default_operator_history_path,
    save_operator_history,
)
from .operator_state import (
    CanonicalOperatorEvent,
    CanonicalOperatorState,
    InterruptionClass,
    RequestPhase,
    SemanticEventKey,
    TransitionKind,
)
from .operator_triage_store import load_operator_triage, save_operator_triage
from .presentation_policy import (
    CapacityGlance,
    FiniteCue,
    FiniteCueState,
    GlanceInputs,
    GlanceOverrideReason,
    GlanceSemantic,
    MotionClass,
    ResolvedGlance,
    SemanticGlyph,
    compose_presentation_program,
    continuous_presentation_identity,
    resolve_glance,
    valid_finite_cue,
    valid_presentation_time,
)
from .presentation_scheduler import (
    PresentationSchedulerInputs,
    PresentationSchedulerState,
    plan_presentation_schedule,
)
from .private_export import write_private_export
from .provider_contracts import ProviderIdentifier
from .provider_facts import NextActor, RequestKey, SourceFreshness, WorkKey
from .providers import (
    HOOK_PROVIDERS,
    PROVIDER_SPECS,
    ProviderConfig,
    default_state_dir,
    detect_log_path,
    negotiated_provider_sources,
    parse_log_line,
    provider_spec,
)
from .refresh_policy import (
    DEFAULT_FRESH_SECONDS,
    LOW_POWER_FRESH_SECONDS,
    ProviderRefreshState,
    mark_refresh_failed,
    mark_refresh_started,
    mark_refresh_succeeded,
    plan_menu_open_refresh,
    retain_attempted_boundary_keys,
)
from .render_policy import runtime_render_environment
from .reset_policy import (
    DEFAULT_RESET_GRACE_SECONDS,
    MINIMUM_RESET_REFRESH_DELAY_SECONDS,
    ResetBoundaryPlan,
    evaluate_reset_continuity,
    next_countdown_deadline,
    plan_reset_boundary_refresh,
)
from .runtime_scheduler import (
    AppKitTimerRegistry,
    LatestWinsWorker,
    RuntimeFeature,
    RuntimeTimerIntent,
    RuntimeWorkCommand,
    RuntimeWorkerDomain,
    RuntimeWorkerRegistry,
    SubmissionDisposition,
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
    activate_navigation_resolution,
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
    AgentMonitorSettings,
    LedAnimationSetting,
    default_lid_animation,
    default_settings_path,
    load_settings,
    normalize_animation_duration,
    save_settings,
)
from .signal_coordinator import (
    ActiveSignal,
    FiniteCueCoordinator,
    FiniteSignalCoordinator,
)
from .status_bar_launch import (
    LAUNCH_AGENT_LABEL,
    TerminalLaunchPlan,
    install_launch_agent,
    launch_agent_installed,
    resolve_terminal_launch,
    terminal_launch_arguments,
)
from .trusted_tools import trusted_system_tool
from .usage_view import (
    LocalActivitySection,
    ProviderUsageViewModel,
    build_provider_usage_view,
    source_text_for_coverage,
)
from .virtual_device import (
    VIRTUAL_DEVICE_ID,
    VIRTUAL_DEVICE_NAME,
    VirtualLedView,
    VirtualStatusDevice,
    monotonic_ms,
    slot_width_for_screen,
    virtual_display_state_for_projection,
)
from .virtual_device import (
    WINDOW_HEIGHT as SCREEN_BAR_PREVIEW_HEIGHT,
)


def _capacity_source_keys_by_provider():
    rows = tuple(
        row.source_key
        for row in negotiated_provider_sources()
        if row.source_key.capability_id == "remote_quota_windows"
        and row.observation_invocation_allowed
    )
    keys = {source_key.provider_id: source_key for source_key in rows}
    if len(keys) != len(rows):
        raise RuntimeError("ambiguous capacity source registry")
    return keys


CAPACITY_SOURCE_KEYS_BY_PROVIDER = _capacity_source_keys_by_provider()


def _transcript_source_keys_by_provider():
    rows = tuple(
        row.source_key
        for row in negotiated_provider_sources()
        if row.source_key.capability_id == "transcript_usage"
        and row.observation_invocation_allowed
    )
    keys = {source_key.provider_id: source_key for source_key in rows}
    if len(keys) != len(rows):
        raise RuntimeError("ambiguous transcript usage source registry")
    return keys


TRANSCRIPT_SOURCE_KEYS_BY_PROVIDER = _transcript_source_keys_by_provider()
CAPACITY_REFRESH_DEADLINE_SECONDS = 30.0
CAPACITY_REFRESH_FAILURE_COPY = {
    RefreshFailureKind.FAILED: "Capacity refresh failed",
    RefreshFailureKind.TIMED_OUT: "Capacity refresh timed out",
    RefreshFailureKind.SIGN_IN_REQUIRED: "Sign in to refresh capacity",
    RefreshFailureKind.ACCESS_DENIED: "Capacity access denied",
    RefreshFailureKind.SOURCE_UNAVAILABLE: "Capacity source unavailable",
}


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


@dataclass(frozen=True, slots=True)
class LidObservationRequest:
    """Content-free authority to read the bounded system lid property."""


@dataclass(frozen=True, slots=True)
class LidObservationResult:
    ok: bool
    closed: bool | None
    error: str | None

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise ValueError("invalid lid observation status")
        if self.ok:
            if self.closed is not None and type(self.closed) is not bool:
                raise ValueError("invalid lid observation state")
            if self.error is not None:
                raise ValueError("successful lid observation cannot contain an error")
            return
        if self.closed is not None:
            raise ValueError("failed lid observation cannot contain a state")
        if type(self.error) is not str or not self.error or len(self.error) > 256:
            raise ValueError("invalid lid observation error")


@dataclass(frozen=True, slots=True)
class DisplayEnvironmentRequest:
    read_brightness: bool
    read_focus: bool
    read_accessibility: bool
    previous_accessibility_preferences: AccessibilityDisplayPreferences | None = None

    def __post_init__(self) -> None:
        if not all(
            type(value) is bool
            for value in (
                self.read_brightness,
                self.read_focus,
                self.read_accessibility,
            )
        ):
            raise ValueError("invalid display environment request")
        if self.previous_accessibility_preferences is not None and type(
            self.previous_accessibility_preferences
        ) is not AccessibilityDisplayPreferences:
            raise ValueError("invalid display accessibility preferences")


@dataclass(frozen=True, slots=True)
class DisplayEnvironmentResult:
    brightness: int | None
    active_focus_ids: tuple[str, ...] | None
    accessibility_preferences: AccessibilityDisplayPreferences | None

    def __post_init__(self) -> None:
        if self.brightness is not None and (
            type(self.brightness) is not int or not 0 <= self.brightness <= 255
        ):
            raise ValueError("invalid display brightness result")
        if self.active_focus_ids is not None and (
            type(self.active_focus_ids) is not tuple
            or len(self.active_focus_ids) > 64
            or any(
                type(identifier) is not str
                or not identifier
                or len(identifier.encode("utf-8")) > 256
                for identifier in self.active_focus_ids
            )
        ):
            raise ValueError("invalid display focus result")
        if self.accessibility_preferences is not None and type(
            self.accessibility_preferences
        ) is not AccessibilityDisplayPreferences:
            raise ValueError("invalid display accessibility result")


@dataclass(frozen=True, slots=True)
class CalendarObservationRequest:
    lead_minutes: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.lead_minutes, (int, float))
            or isinstance(self.lead_minutes, bool)
            or not math.isfinite(float(self.lead_minutes))
            or not 0.0 <= float(self.lead_minutes) <= 60.0
        ):
            raise ValueError("invalid calendar observation window")
        object.__setattr__(self, "lead_minutes", float(self.lead_minutes))


@dataclass(frozen=True, slots=True)
class CalendarObservationResult:
    available: bool
    starts_in_seconds: float | None

    def __post_init__(self) -> None:
        if type(self.available) is not bool:
            raise ValueError("invalid calendar availability")
        starts_in_seconds = self.starts_in_seconds
        if not self.available:
            if starts_in_seconds is not None:
                raise ValueError("unavailable calendar cannot contain an event")
            return
        if starts_in_seconds is None:
            return
        if (
            not isinstance(starts_in_seconds, (int, float))
            or isinstance(starts_in_seconds, bool)
            or not math.isfinite(float(starts_in_seconds))
            or not 0.0 <= float(starts_in_seconds) <= 3600.0
        ):
            raise ValueError("invalid calendar event offset")
        object.__setattr__(self, "starts_in_seconds", float(starts_in_seconds))


MAX_REMINDER_OBSERVATION_IDENTIFIERS = 256
REMINDERS_FETCH_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class RemindersObservationRequest:
    lookback_seconds: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.lookback_seconds, (int, float))
            or isinstance(self.lookback_seconds, bool)
            or not math.isfinite(float(self.lookback_seconds))
            or not 0.0 <= float(self.lookback_seconds) <= 3600.0
        ):
            raise ValueError("invalid reminders observation window")
        object.__setattr__(self, "lookback_seconds", float(self.lookback_seconds))


@dataclass(frozen=True, slots=True)
class RemindersObservationResult:
    available: bool
    identifiers: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.available) is not bool:
            raise ValueError("invalid reminders availability")
        if (
            type(self.identifiers) is not tuple
            or len(self.identifiers) > MAX_REMINDER_OBSERVATION_IDENTIFIERS
            or any(
                type(identifier) is not str
                or len(identifier) != 64
                or any(character not in "0123456789abcdef" for character in identifier)
                for identifier in self.identifiers
            )
        ):
            raise ValueError("invalid reminder identifiers")
        if not self.available and self.identifiers:
            raise ValueError("unavailable reminders cannot contain identifiers")


@dataclass(frozen=True, slots=True)
class WeatherObservationRequest:
    latitude: float | None
    longitude: float | None

    def __post_init__(self) -> None:
        latitude = self.latitude
        longitude = self.longitude
        if (latitude is None) != (longitude is None):
            raise ValueError("weather coordinates must be paired")
        if latitude is None:
            return
        if (
            not isinstance(latitude, (int, float))
            or isinstance(latitude, bool)
            or not math.isfinite(float(latitude))
            or not -90.0 <= float(latitude) <= 90.0
            or not isinstance(longitude, (int, float))
            or isinstance(longitude, bool)
            or not math.isfinite(float(longitude))
            or not -180.0 <= float(longitude) <= 180.0
        ):
            raise ValueError("invalid weather coordinates")
        object.__setattr__(self, "latitude", float(latitude))
        object.__setattr__(self, "longitude", float(longitude))


WEATHER_CLASSIFICATIONS = frozenset(
    {
        "Tornado warning",
        "Hurricane warning",
        "Flash flood warning",
        "Severe thunderstorm warning",
        "Extreme weather alert",
        "Severe weather alert",
    }
)


@dataclass(frozen=True, slots=True)
class WeatherObservationResult:
    available: bool
    active: bool
    classification: str | None

    def __post_init__(self) -> None:
        if type(self.available) is not bool or type(self.active) is not bool:
            raise ValueError("invalid weather observation state")
        if not self.available and (self.active or self.classification is not None):
            raise ValueError("unavailable weather cannot contain alert truth")
        if self.active != (self.classification is not None):
            raise ValueError("weather alert truth requires a classification")
        if (
            self.classification is not None
            and self.classification not in WEATHER_CLASSIFICATIONS
        ):
            raise ValueError("invalid weather classification")


def _weather_classification(severity: object, event: object) -> str:
    normalized = str(event).casefold()
    if "tornado" in normalized:
        return "Tornado warning"
    if "hurricane" in normalized:
        return "Hurricane warning"
    if "flash flood" in normalized:
        return "Flash flood warning"
    if "severe thunderstorm" in normalized:
        return "Severe thunderstorm warning"
    if str(severity).casefold() == "extreme":
        return "Extreme weather alert"
    return "Severe weather alert"


MAX_RUNTIME_PHYSICAL_DEVICES = 16


@dataclass(frozen=True, slots=True)
class DeviceInventoryRequest:
    """Content-free authority to enumerate supported SidePulse volumes."""


@dataclass(frozen=True, slots=True)
class DeviceInventoryResult:
    candidates: tuple[DeviceCandidate, ...]
    error: str | None = None

    def __post_init__(self) -> None:
        if type(self.candidates) is not tuple or len(self.candidates) > MAX_RUNTIME_PHYSICAL_DEVICES:
            raise ValueError("invalid device inventory")
        if not all(type(candidate) is DeviceCandidate for candidate in self.candidates):
            raise ValueError("invalid device inventory candidate")
        if self.error is not None and (
            type(self.error) is not str or not self.error or len(self.error) > 256
        ):
            raise ValueError("invalid device inventory error")


@dataclass(frozen=True, slots=True)
class HardwareWriteRequest:
    device: StatusBarDevice
    mode: AgentMode
    battery_snapshot: BatterySnapshot | None
    statuses: tuple[AgentStatus, ...]
    projection: AttentionProjection | None
    relay_elapsed_seconds: float
    accessibility_preferences: AccessibilityDisplayPreferences | None = None
    resolved_glance: ResolvedGlance | None = None
    presentation_time: float | None = None
    capacity_remaining_fraction: float | None = None

    def __post_init__(self) -> None:
        if (
            type(self.device) is not StatusBarDevice
            or not self.device.connected
            or self.device.device_id == VIRTUAL_DEVICE_ID
        ):
            raise ValueError("invalid hardware device")
        if not isinstance(self.mode, AgentMode):
            raise ValueError("invalid hardware mode")
        if type(self.statuses) is not tuple or not all(
            isinstance(status, AgentStatus) for status in self.statuses
        ):
            raise ValueError("invalid hardware statuses")
        if self.projection is not None and not isinstance(
            self.projection, AttentionProjection
        ):
            raise ValueError("invalid hardware projection")
        if (
            isinstance(self.relay_elapsed_seconds, bool)
            or not math.isfinite(float(self.relay_elapsed_seconds))
            or float(self.relay_elapsed_seconds) < 0.0
        ):
            raise ValueError("invalid relay time")
        if self.accessibility_preferences is not None and type(
            self.accessibility_preferences
        ) is not AccessibilityDisplayPreferences:
            raise ValueError("invalid accessibility display preferences")
        if self.resolved_glance is not None and type(
            self.resolved_glance
        ) is not ResolvedGlance:
            raise ValueError("invalid resolved presentation")
        if self.presentation_time is not None and not valid_presentation_time(
            self.presentation_time
        ):
            raise ValueError("invalid presentation time")
        if self.resolved_glance is not None and (
            self.presentation_time is None
            or float(self.presentation_time) < float(self.resolved_glance.relay_epoch)
        ):
            raise ValueError("invalid resolved presentation time")
        if self.capacity_remaining_fraction is not None and (
            isinstance(self.capacity_remaining_fraction, bool)
            or not math.isfinite(float(self.capacity_remaining_fraction))
            or not 0.0 <= float(self.capacity_remaining_fraction) <= 1.0
        ):
            raise ValueError("invalid presentation capacity")


@dataclass(frozen=True, slots=True)
class HardwareWriteResult:
    request: HardwareWriteRequest
    write: LedStatusWrite
    label: str
    agent_display_rendered: bool
    completed_at: float

    def __post_init__(self) -> None:
        if type(self.request) is not HardwareWriteRequest or type(self.write) is not LedStatusWrite:
            raise ValueError("invalid hardware write result")
        if type(self.label) is not str or len(self.label) > 256:
            raise ValueError("invalid hardware write label")
        if type(self.agent_display_rendered) is not bool:
            raise ValueError("invalid hardware display state")
        if (
            isinstance(self.completed_at, bool)
            or not math.isfinite(float(self.completed_at))
            or float(self.completed_at) < 0.0
        ):
            raise ValueError("invalid hardware completion time")


@dataclass(frozen=True, slots=True)
class HardwarePresentationSync:
    request: HardwareWriteRequest
    started_at: float | None


def hardware_presentation_sync_for_result(
    result: HardwareWriteResult,
) -> HardwarePresentationSync | None:
    if type(result) is not HardwareWriteResult:
        raise ValueError("invalid hardware write result")
    if not (
        result.write.changed
        or result.write.error is not None
        or result.agent_display_rendered
    ):
        return None
    return HardwarePresentationSync(
        request=result.request,
        started_at=(
            result.request.resolved_glance.relay_epoch
            if result.request.resolved_glance is not None
            else result.completed_at if result.write.changed else None
        ),
    )


def display_state_for_resolved_glance(resolved: ResolvedGlance) -> LedDisplayState:
    if type(resolved) is not ResolvedGlance:
        raise ValueError("invalid resolved presentation")
    return {
        GlanceSemantic.ATTENTION: LedDisplayState.ASK,
        GlanceSemantic.FRESH_FAILURE: LedDisplayState.FAILED,
        GlanceSemantic.FRESH_COMPLETION: LedDisplayState.DONE,
        GlanceSemantic.ACTIVE: LedDisplayState.WORKING,
        GlanceSemantic.UNRESOLVED_FAILURE: LedDisplayState.FAILED,
        GlanceSemantic.CAPACITY: LedDisplayState.WORKING,
        GlanceSemantic.REST: LedDisplayState.IDLE,
    }[resolved.semantic]


def color_for_resolved_glance(
    colors: ColorSettings,
    resolved: ResolvedGlance,
) -> str:
    if type(colors) is not ColorSettings or type(resolved) is not ResolvedGlance:
        raise ValueError("invalid resolved presentation color")
    mode_key = {
        GlanceSemantic.ATTENTION: "ask",
        GlanceSemantic.FRESH_FAILURE: "ask",
        GlanceSemantic.FRESH_COMPLETION: "done",
        GlanceSemantic.ACTIVE: "working",
        GlanceSemantic.UNRESOLVED_FAILURE: "ask",
        GlanceSemantic.CAPACITY: "working",
        GlanceSemantic.REST: "idle",
    }[resolved.semantic]
    return colors.mode_color(mode_key)


STATE_IDLE = StatusBarState("Idle", "circle", 4)
STATE_WORKING = StatusBarState("Working", "arrow.triangle.2.circlepath", 2)
STATE_DONE = StatusBarState("Done", "checkmark.circle", 3)
STATE_ASK = StatusBarState("Ask", "questionmark.circle", 1)
STATE_FAILED = StatusBarState("Failed", "exclamationmark.triangle", 2)
STATUS_BAR_DEVICE_PRIORITY = ("sidepulsepro", "sidepulsedot")
STATUS_BAR_KEEPALIVE_VOLUME_NAMES = (
    "SidePulsePro",
    "SidePulseDot",
)
# Runtime-only display kind (never a persisted per-device choice): the
# low-battery reminder takes over every display while active.
LED_DISPLAY_LOW_BATTERY = "low_battery"
LED_DISPLAY_FAILURE = "failure"
LED_DISPLAY_CALENDAR = "calendar"
LED_DISPLAY_ESCALATION = "escalation"
LED_DISPLAY_TEST = "signal_test"
SIGNAL_TEST_SECONDS = 5.0
SETTINGS_SIGNAL_PREVIEW_INTERVAL_SECONDS = 1.0 / 8.0
SETTINGS_COLOR_PREVIEW_INTERVAL_SECONDS = 1.0 / 12.0
SETUP_DEMO_INTERVAL_SECONDS = 1.0 / 30.0
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
WEATHER_FETCH_TIMEOUT_SECONDS = 30.0
WEATHER_WORKER_KEY = "weather-observation"
CALENDAR_WATCH_SECONDS = 30.0
CALENDAR_WATCH_RETRY_SECONDS = 300.0

STATUS_BAR_REFRESH_SECONDS = 15.0
MAX_NOTIFICATION_ACTION_BINDINGS = 256
NOTIFICATION_ACTION_TTL_SECONDS = 300.0
NOTIFICATION_FOREGROUND_PRESENTATION_OPTIONS = 1 << 2
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
SIGNAL_DISPLAY_KINDS = frozenset({LED_DISPLAY_FAILURE})
# Per-device "asks only" mutes exactly these courtesy moments -- never
# test, escalation, weather, or low battery (documented invariants:
# low battery is "every device at once"; blocked-on-you and emergencies
# always break through).
DEVICE_MUTABLE_SIGNAL_KINDS = frozenset(
    {
        LED_DISPLAY_QUOTA,
        LED_DISPLAY_REMINDERS,
        LED_DISPLAY_COMPLETION,
        LED_DISPLAY_ALL_CLEAR,
        LED_DISPLAY_CALENDAR,
        LED_DISPLAY_PEEK,
    }
)
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


def state_for_projection(projection: AttentionProjection) -> StatusBarState:
    return {
        LifecycleMode.IDLE: STATE_IDLE,
        LifecycleMode.ACTIVE: STATE_WORKING,
        LifecycleMode.WAITING: STATE_ASK,
        LifecycleMode.COMPLETED_RECENTLY: STATE_DONE,
        LifecycleMode.FAILED_VISIBLE: STATE_FAILED,
        LifecycleMode.UNKNOWN: STATE_IDLE,
    }[projection.lifecycle_mode]


def projection_for_statuses(statuses, settings) -> AttentionProjection:
    rows = tuple(statuses or ())
    snapshot = MonitorSnapshot(
        aggregate=aggregate_status(rows),
        statuses=rows,
        stale_statuses=(),
        sources=(),
        collected_at=datetime.now(timezone.utc),
    )
    return project_attention(snapshot, settings)


def eligible_mailbox_completion_statuses(
    snapshot,
    *,
    include_subagents: bool = False,
) -> tuple[AgentStatus, ...]:
    """Fresh completion rows that the mailbox may show and clear.

    Primary sessions remain the public default. The mailbox projection also
    requests terminal workers so a collapsed family preserves their outcome.
    """
    collected_at = snapshot.collected_at
    current_by_id: dict[str, AgentStatus] = {}
    for status in snapshot.statuses:
        existing = current_by_id.get(status.agent_id)
        if existing is None or (
            status.updated_at,
            status.mode != AgentMode.COMPLETED,
        ) > (
            existing.updated_at,
            existing.mode != AgentMode.COMPLETED,
        ):
            current_by_id[status.agent_id] = status

    def eligible(status: AgentStatus) -> bool:
        return (
            (include_subagents or not status.is_subagent)
            and status.mode == AgentMode.COMPLETED
            and status.event_name != "SessionEnd"
            and is_recent(
                collected_at,
                status.updated_at,
                COMPLETED_VISIBLE_SECONDS,
            )
        )

    eligible_by_id = {
        agent_id: status
        for agent_id, status in current_by_id.items()
        if eligible(status)
    }
    for status in getattr(snapshot, "stale_statuses", ()):
        if status.agent_id in current_by_id or not eligible(status):
            continue
        existing = eligible_by_id.get(status.agent_id)
        if existing is None or status.updated_at > existing.updated_at:
            eligible_by_id[status.agent_id] = status
    return tuple(
        sorted(
            eligible_by_id.values(),
            key=lambda status: (-status.updated_at.timestamp(), status.agent_id),
        )
    )


def mailbox_attention_statuses(snapshot) -> tuple[AgentStatus, ...]:
    """Current lifecycle rows plus eligible promoted stale completions."""
    eligible_completions = eligible_mailbox_completion_statuses(
        snapshot,
        include_subagents=True,
    )
    current_ids = {status.agent_id for status in snapshot.statuses}
    eligible_current_ids = {
        status.agent_id for status in eligible_completions
    } & current_ids
    current = tuple(
        status
        for status in snapshot.statuses
        if status.mode != AgentMode.COMPLETED
        or status.agent_id in eligible_current_ids
    )
    promoted = tuple(
        status
        for status in eligible_completions
        if status.agent_id not in current_ids
    )
    visible = (*current, *promoted)
    if visible:
        return visible
    # Preserve bounded stale-only visibility without presenting an old
    # Working state as current activity. The source identity and original
    # row metadata remain available, while the lifecycle projects to Recent.
    return tuple(
        dataclass_replace(status, mode=AgentMode.IDLE_READY)
        for status in recent_statuses(snapshot)
    )


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
        self.notification_client = None
        self.notification_authorization_state = (
            NotificationAuthorizationState.UNAVAILABLE
        )
        self._notification_authorization_checked = False
        self._notification_authorization_generation = 0
        self._notification_authorization_refresh_in_flight = False
        self._notification_action_bindings: dict[
            str,
            tuple[ActionTokenBinding, SemanticEventKey],
        ] = {}
        self._notification_events_by_work_key: dict[
            WorkKey,
            CanonicalOperatorEvent,
        ] = {}
        self._relay_epoch = time.monotonic()
        self.monitor = self.build_monitor()
        self.transcript_monitor = self.build_transcript_monitor()
        self.transcript_watermark = None
        self.event_server = None
        self.status_item = None
        self.timer = None
        self.settings_window = None
        self._settings_window_closing = False
        self.setup_window = None
        self.colors_window = None
        self.agent_browser_controller = None
        self.current_operator_state = None
        self.mailbox_preferences: tuple[MailboxPreference, ...] = ()
        self.mailbox_preferences_dirty = False
        self.local_triage_state = LocalTriageState(())
        self.local_triage_dirty = False
        self.operator_action_error: str | None = None
        self.mailbox_preferences_saver = self._save_mailbox_preferences
        self.operator_triage_saver = self._save_operator_triage
        self.navigation_candidates_by_work_key = {}
        self.native_agent_menu_registry = StableNativeMenuRegistry()
        self.mailbox_boundary_schedule = ExactBoundarySchedule()
        self.mailbox_boundary_timer = None
        self.settings_fields = {}
        self.settings_buttons = {}
        self.device_settings_controls = {}
        self.settings_sidebar_table = None
        self.settings_panes = {}
        self.operator_history_range_days = 1
        self.operator_history_reel: tuple[str, ...] = ()
        self.operator_history_store = OperatorHistoryStore(
            default_operator_history_path(),
            retention_days=self.settings.operator_history_retention_days,
        )
        self.operator_history_projection: OperatorHistoryProjection | None = None
        self._operator_history_retention_generation = 0
        self._operator_history_restore_started = False
        self._operator_history_restore_pending = False
        self.operator_history_restore_health = OperatorHistoryRestoreHealth.MISSING
        self._operator_history_operation_status = ""
        self._operator_history_lock = threading.RLock()
        self.semantic_text_scale_percent = 100
        self._device_calibration_popover = None
        # (device_id, hex) while a calibration test patch is lighting the
        # device; None otherwise. See startCalibrationTest_.
        self.calibration_test = None
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
        self.current_attention_projection: AttentionProjection | None = None
        self.current_mailbox_projection: AgentMailboxProjection | None = None
        self.mailbox_retained_order: dict[str, int] = {}
        self.mailbox_seen_completion_ids: set[str] = set()
        self.failure_signal_coordinator = FiniteSignalCoordinator()
        self.failure_signal_watermark_established = False
        self.failure_signal_timer = None
        self.failure_signal_timer_deadline: float | None = None
        self.status_cue_coordinator = FiniteCueCoordinator()
        self._status_finite_cues = FiniteCueState(None, None, None, False)
        self._status_cue_candidates = ()
        self._status_cue_deadline = None
        self._current_resolved_glance = None
        self._status_emphasis_accessibility_generation = None
        self.last_battery_snapshot = None
        self.last_battery_error = None
        self.last_power_connected = None
        self.battery_preview_until = 0.0
        self.quota_blink_until = 0.0
        self.quota_blink_color = None
        self.quota_blink_label = None
        self.quota_last_percents: dict[str, float] = {}
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
        self._reminders_permission_generation = 1
        self._reminders_permission_request_token: int | None = None
        self._reminders_permission_failed = False
        # Weather emergency: alert active right now (state, not moment).
        self.weather_alert_active = False
        self.weather_alert_event: str | None = None
        self.weather_watch_retry_at = 0.0
        self._weather_observation_generation = 1
        self.current_state = STATE_IDLE
        # None until set_status() actually confirms Idle -- avoids assuming
        # "idle since launch" if the real initial state turns out to be
        # something else once the first real snapshot arrives.
        self.idle_since_monotonic: float | None = None
        # Ask escalation: when the aggregate first entered ask/blocked
        # (None = not blocked), the last applied stage, and whether this
        # block episode already chimed.
        self.ask_blocked_since: float | None = None
        self.ask_blocked_by_agent: dict[str, float] = {}
        self.escalation_last_stage = 0
        self.escalation_chimed = False
        self.led_controller = AgentLedController()
        self.battery_led_controller = BatteryLedController()
        self.agent_led_controllers_by_device = {}
        self.battery_led_controllers_by_device = {}
        self.last_led_display_kind_by_device = {}
        self.device_errors = {}
        self.leds_enabled = True
        self.last_watched_brightness = None
        self.last_watched_focus_scale = None
        self._accessibility_display_preferences = None
        self._accessibility_generation = 0
        self._accessibility_observer_center = None
        self._accessibility_observer_generation = 0
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
        self.legacy_hook_providers: set[str] = set()
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
        self.lid_animation_thumbs = {}
        self.peek_until = 0.0
        self.quiet_until_monotonic = 0.0
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
        self.menu_last_opened_at = None
        self._pane_transition_generation = 0
        self._peek_hits = 0
        self._presentation_scheduler_state = PresentationSchedulerState()
        self._presentation_scheduler_inputs = None
        self._presentation_reconcile_active = False
        self._presentation_reconcile_pending = None
        self._presentation_monotonic = time.monotonic
        self._runtime_started = False
        self._lid_observation_active = False
        self._lid_observation_fire_at = None
        self._device_inventory_active = False
        self._device_inventory_fire_at = None
        self._device_inventory_candidates: tuple[DeviceCandidate, ...] = ()
        self._display_environment_active = False
        self._display_environment_fire_at = None
        self._calendar_observation_active = False
        self._calendar_observation_fire_at = None
        self._reminders_observation_active = False
        self._reminders_observation_fire_at = None
        self._weather_observation_active = False
        self._weather_observation_fire_at = None
        self._scheduled_reminders_cue_deadline = None
        self._scheduled_settings_message_deadline = None
        self._scheduled_timebox_deadline = None
        self._scheduled_escalation_deadline = None
        self._runtime_preview_fire_at: dict[RuntimeFeature, float] = {}
        self._settings_message_deadline_at = 0.0
        self._tip_highlight_view = None
        self._tip_highlight_until = 0.0
        self._os_poll_generation = 1
        self._installed_agent_inventory_generation = 1
        self._installed_agent_inventory_result = None
        self._hardware_write_active = False
        self._hardware_write_generation = 1
        self._hardware_device_keys: frozenset[str] = frozenset()
        (
            self._runtime_worker_registry,
            self._os_poll_worker,
        ) = self._build_runtime_worker_registry()
        self._runtime_timer_registry = self._build_presentation_timer_registry()
        self.virtual_status_device.set_presentation_schedule_reconciler(
            self.reconcile_presentation_timers
        )
        self._provider_probe_at = 0.0
        self._settings_pane_container = None
        self._setup_no_hooks_warned = False
        self._signal_card_rendered = None
        self._studio_validation_cache = None
        self._timebox_off_shortcut = None
        self._trailing_refresh_timer = None
        self._usage_provider_states = {
            provider_id: ProviderRefreshState(
                source_key=source_key,
                enabled=(
                    bool(self.settings.codex_percent_enabled)
                    if provider_id == "codex"
                    else provider_id != "claude"
                ),
            )
            for provider_id, source_key in CAPACITY_SOURCE_KEYS_BY_PROVIDER.items()
        }
        self._usage_transcript_states = {
            source_key: ProviderRefreshState(source_key=source_key)
            for source_key in TRANSCRIPT_SOURCE_KEYS_BY_PROVIDER.values()
        }
        self._capacity_refresh_keys_by_provider = {
            provider_id: RefreshSourceKey(
                source=source_key,
                pool="plan",
                account_discriminator=None,
            )
            for provider_id, source_key in CAPACITY_SOURCE_KEYS_BY_PROVIDER.items()
        }
        self._capacity_refresh_coordinator = CapacityRefreshCoordinator(
            tuple(
                RefreshSourceRegistration(
                    key=refresh_key,
                    enabled=refresh_key.source.provider_id != "claude",
                    supported=True,
                )
                for refresh_key in self._capacity_refresh_keys_by_provider.values()
            )
        )
        self._capacity_refresh_deadline_timers = {}
        self._capacity_refresh_retry_timers = {}
        self._usage_provider_models: dict[str, ProviderUsageViewModel] = {}
        self._usage_local_scan_complete = False
        self._usage_menu_item = None
        self._usage_menu_view = None
        self._usage_menu_header = None
        self._usage_menu_labels = {}
        self._usage_menu_secondary_labels = {}
        self._capacity_reset_timer = None
        self._capacity_reset_plan = ResetBoundaryPlan(None, (), ())
        self._capacity_reset_retry_deadline: float | None = None
        self._capacity_countdown_timer = None
        self._capacity_countdown_deadline: float | None = None
        self._capacity_reset_continuity = {}
        self._attempted_capacity_boundary_keys: tuple[str, ...] = ()
        return self

    def _install_accessibility_display_observer(self) -> None:
        if self._accessibility_observer_center is not None:
            return
        try:
            workspace = NSWorkspace.sharedWorkspace()
            center = workspace.notificationCenter()
            center.addObserver_selector_name_object_(
                self,
                "accessibilityDisplayOptionsDidChange:",
                NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification,
                None,
            )
        except Exception:
            return
        self._accessibility_observer_generation += 1
        self._accessibility_observer_center = center
        self._apply_accessibility_display_preferences(
            refresh_accessibility_display_preferences(
                self._accessibility_display_preferences,
                workspace,
            )
        )

    def _remove_accessibility_display_observer(self) -> None:
        center = self._accessibility_observer_center
        if center is None:
            return
        self._accessibility_observer_center = None
        self._accessibility_observer_generation += 1
        try:
            center.removeObserver_name_object_(
                self,
                NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification,
                None,
            )
        except Exception:
            pass

    def accessibilityDisplayOptionsDidChange_(self, _notification) -> None:
        if (
            not self._runtime_started
            or self._accessibility_observer_center is None
        ):
            return
        observer_generation = self._accessibility_observer_generation
        preferences = refresh_accessibility_display_preferences(
            self._accessibility_display_preferences,
            NSWorkspace.sharedWorkspace(),
        )
        if (
            not self._runtime_started
            or observer_generation != self._accessibility_observer_generation
        ):
            return
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "applyAccessibilityDisplayOptions:",
            (observer_generation, preferences),
            False,
        )

    @objc.IBAction
    def applyAccessibilityDisplayOptions_(self, payload) -> None:
        if (
            type(payload) is not tuple
            or len(payload) != 2
            or type(payload[0]) is not int
            or type(payload[1]) is not AccessibilityDisplayPreferences
            or not self._runtime_started
            or self._accessibility_observer_center is None
            or payload[0] != self._accessibility_observer_generation
        ):
            return
        self._apply_accessibility_display_preferences(payload[1])

    def _apply_accessibility_display_preferences(
        self,
        preferences: AccessibilityDisplayPreferences,
    ) -> bool:
        if (
            type(preferences) is not AccessibilityDisplayPreferences
            or preferences == self._accessibility_display_preferences
        ):
            return False
        self._accessibility_display_preferences = preferences
        self._accessibility_generation += 1
        if self._current_resolved_glance is not None:
            self._discard_status_emphasis_plan()
        apply_preferences = getattr(
            self.virtual_status_device,
            "set_accessibility_display_preferences",
            None,
        )
        if callable(apply_preferences):
            apply_preferences(
                preferences,
                generation=self._accessibility_generation,
            )
        self._reconcile_current_presentation_inputs()
        snapshot = self.last_snapshot
        if snapshot is not None:
            projection = self.current_attention_projection
            mode = (
                self.display_aggregate_mode(projection)
                if projection is not None
                else snapshot.aggregate.mode
            )
            self.sync_leds(
                mode,
                self.last_battery_snapshot,
                self.active_led_display_kind(self.last_battery_snapshot),
                tuple(snapshot.statuses),
                projection=projection,
            )
        return True

    def _notification_client_for_use(self):
        client = self.notification_client
        if client is None:
            client = MacOSNotificationClient()
            self.notification_client = client
        return client

    def notification_authorization_status_text(self) -> str:
        if not self._notification_authorization_checked:
            return "Checking permission\u2026"
        return {
            NotificationAuthorizationState.NOT_DETERMINED: "Not requested",
            NotificationAuthorizationState.DENIED: "Denied by macOS",
            NotificationAuthorizationState.AUTHORIZED: "Allowed by macOS",
            NotificationAuthorizationState.PROVISIONAL: "Provisionally allowed",
            NotificationAuthorizationState.UNAVAILABLE: "Unavailable in this runtime",
        }[self.notification_authorization_state]

    def refresh_notification_authorization_controls(self) -> None:
        set_field_value(
            self.settings_fields.get("notification_authorization_status"),
            self.notification_authorization_status_text(),
        )
        button = self.settings_buttons.get("notification_permission")
        if button is not None:
            button.setTitle_(
                "Retry Permission Check\u2026"
                if self._notification_authorization_checked
                and self.notification_authorization_state
                is NotificationAuthorizationState.UNAVAILABLE
                else "Enable Notifications\u2026"
            )
            button.setHidden_(
                not self._notification_authorization_checked
                or self.notification_authorization_state
                not in {
                    NotificationAuthorizationState.NOT_DETERMINED,
                    NotificationAuthorizationState.UNAVAILABLE,
                }
            )

    def start_notification_authorization_refresh(self) -> None:
        if (
            self._notification_authorization_checked
            or self._notification_authorization_refresh_in_flight
        ):
            self.refresh_notification_authorization_controls()
            return
        self._notification_authorization_generation += 1
        generation = self._notification_authorization_generation
        self._notification_authorization_refresh_in_flight = True

        def observe() -> None:
            state = self._notification_client_for_use().authorization_state()
            self._publish_notification_authorization_state(generation, state)

        threading.Thread(target=observe, daemon=True).start()

    def _publish_notification_authorization_state(
        self,
        generation: int,
        state: NotificationAuthorizationState,
    ) -> None:
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "applyNotificationAuthorizationState:",
            {"generation": generation, "state": state},
            False,
        )

    @objc.IBAction
    def applyNotificationAuthorizationState_(self, payload) -> None:
        if type(payload) is not dict or set(payload) != {"generation", "state"}:
            return
        generation = payload["generation"]
        state = payload["state"]
        if (
            type(generation) is not int
            or generation != self._notification_authorization_generation
            or type(state) is not NotificationAuthorizationState
        ):
            return
        self.notification_authorization_state = state
        self._notification_authorization_checked = True
        self._notification_authorization_refresh_in_flight = False
        self.refresh_notification_authorization_controls()

    @objc.IBAction
    def requestNotificationPermission_(self, _sender) -> None:
        if (
            self._notification_authorization_checked
            and self.notification_authorization_state
            is NotificationAuthorizationState.UNAVAILABLE
        ):
            self._notification_authorization_checked = False
            self._notification_authorization_refresh_in_flight = False
            self.refresh_notification_authorization_controls()
            self.start_notification_authorization_refresh()
            self.set_settings_message("Checking macOS notification permission.")
            return
        self._notification_authorization_generation += 1
        generation = self._notification_authorization_generation

        def completed(state: NotificationAuthorizationState) -> None:
            self._publish_notification_authorization_state(generation, state)

        if self._notification_client_for_use().request_authorization(completed):
            self.set_settings_message("Waiting for macOS notification permission.")
            return
        self._publish_notification_authorization_state(
            generation,
            NotificationAuthorizationState.UNAVAILABLE,
        )
        self.set_settings_message("Notifications are unavailable in this runtime.")

    def applicationDidFinishLaunching_(self, _notification):
        self._notification_client_for_use().set_delegate(self)
        self.start_notification_authorization_refresh()
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.load_operator_local_state()
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

        self._runtime_started = True
        self.refresh_installed_agent_inventory()
        self._install_accessibility_display_observer()
        self.reconcile_lid_observation()
        self.refresh_(None)
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            STATUS_BAR_REFRESH_SECONDS,
            self,
            "refresh:",
            None,
            True,
        )
        if not hasattr(self.virtual_status_device, "presentation_scheduler_inputs"):
            self.lid_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                LID_POLL_SECONDS,
                self,
                "pollLid:",
                None,
                True,
            )
        # NSTimer's FIRST fire is one full interval out -- an active
        # Tornado Warning must not stay dark for 10 minutes after launch.
        if self.settings.weather_alerts_enabled:
            self._weather_observation_timer_fired()
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
    def failureSignalExpired_(self, _timer):
        self.failure_signal_timer = None
        self.failure_signal_timer_deadline = None
        observed_at = time.monotonic()
        # Reconcile the finite cue before repainting. NSTimer may fire early or
        # late, so an early callback keeps the remaining lease while a late
        # callback can start the one bounded pending cue at its actual start.
        self.active_failure_signal(now=observed_at)
        self.schedule_failure_signal_refresh(observed_at)
        # A fresh snapshot is not required to restore the underlying
        # projection. This explicit timer invalidation repaints at the
        # exact end of the second visible pulse.
        self.refresh_(None)

    def update_attention_projection(
        self,
        snapshot,
        *,
        now: float | None = None,
    ) -> AttentionProjection:
        observed_at = time.monotonic() if now is None else float(now)
        attention_snapshot = SimpleNamespace(
            statuses=mailbox_attention_statuses(snapshot),
            collected_at=snapshot.collected_at,
        )
        projection = project_attention(attention_snapshot, self.settings)
        self.current_attention_projection = projection
        active_ids = {
            row.agent_id
            for row in projection.visible_rows
            if row.lifecycle_mode
            not in {LifecycleMode.COMPLETED_RECENTLY, LifecycleMode.FAILED_VISIBLE}
        }
        if active_ids:
            self.mailbox_seen_completion_ids.difference_update(active_ids)
        mailbox = project_mailbox_for_target(projection, self)
        self.current_mailbox_projection = mailbox
        self.mailbox_retained_order = dict(mailbox.retained_order)
        if not self.failure_signal_watermark_established:
            self.failure_signal_coordinator.establish_watermark(
                projection,
                now=observed_at,
            )
            self.failure_signal_watermark_established = True
        else:
            self.failure_signal_coordinator.observe(projection, observed_at)
        self.schedule_failure_signal_refresh(observed_at)
        return projection

    def active_failure_signal(self, *, now: float | None = None) -> ActiveSignal | None:
        observed_at = time.monotonic() if now is None else float(now)
        return self.failure_signal_coordinator.active(observed_at)

    def schedule_failure_signal_refresh(self, now: float) -> None:
        deadline = self.failure_signal_coordinator.next_deadline
        if deadline is None:
            if self.failure_signal_timer is not None:
                self.failure_signal_timer.invalidate()
            self.failure_signal_timer = None
            self.failure_signal_timer_deadline = None
            return
        if (
            self.failure_signal_timer is not None
            and self.failure_signal_timer_deadline == deadline
        ):
            return
        if self.failure_signal_timer is not None:
            self.failure_signal_timer.invalidate()
        self.failure_signal_timer_deadline = deadline
        self.failure_signal_timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                max(0.001, deadline - now),
                self,
                "failureSignalExpired:",
                None,
                False,
            )
        )

    def virtual_display_state(
        self,
        projection: AttentionProjection,
        active_signal: ActiveSignal | None,
    ) -> LedDisplayState:
        return virtual_display_state_for_projection(projection, active_signal)

    def resolve_presentation_glance(
        self,
        projection: AttentionProjection,
        *,
        operator_events: tuple,
        capacity: CapacityGlance | None,
        override_reason: GlanceOverrideReason = GlanceOverrideReason.NONE,
        override_semantic: GlanceSemantic | None = None,
        presentation_time: float,
    ) -> ResolvedGlance:
        """Resolve one canonical steady presentation before surface projection."""
        if (
            type(projection) is not AttentionProjection
            or type(operator_events) is not tuple
            or not all(type(event) is CanonicalOperatorEvent for event in operator_events)
        ):
            raise ValueError("invalid presentation projection")
        if capacity is not None and type(capacity) is not CapacityGlance:
            raise ValueError("invalid presentation capacity")
        preferences = self._accessibility_display_preferences
        if type(preferences) is not AccessibilityDisplayPreferences:
            preferences = AccessibilityDisplayPreferences(
                reduce_motion=True,
                reduce_transparency=True,
                increase_contrast=True,
                differentiate_without_color=True,
            )
        actionable_episode_key = None
        if projection.actionable_attention:
            row = projection.actionable_attention[0]
            if row.request_key is not None:
                actionable_episode_key = f"attention:{row.request_key.request_id.value}"
        failure = next(
            (event for event in operator_events if event.kind is TransitionKind.FAILED),
            None,
        )
        completion = next(
            (event for event in operator_events if event.kind is TransitionKind.COMPLETED),
            None,
        )
        return resolve_glance(
            GlanceInputs(
                actionable_episode_key=actionable_episode_key,
                fresh_failure=(
                    FiniteCue(
                        "failure:" + failure.key.provider_watermark.event_token.value,
                        GlanceSemantic.FRESH_FAILURE,
                        2,
                        0.4,
                    )
                    if failure is not None
                    else None
                ),
                fresh_completion=(
                    FiniteCue(
                        "completion:" + completion.key.provider_watermark.event_token.value,
                        GlanceSemantic.FRESH_COMPLETION,
                        2,
                        0.4,
                    )
                    if completion is not None
                    else None
                ),
                active=projection.lifecycle_mode is LifecycleMode.ACTIVE,
                unresolved_failure=(
                    projection.lifecycle_mode is LifecycleMode.FAILED_VISIBLE
                ),
                capacity=capacity,
                override_reason=override_reason,
                override_semantic=override_semantic,
            ),
            presentation_time=presentation_time,
            relay_epoch=self._relay_epoch,
            preferences=preferences,
        )

    def presentation_capacity_glance(self) -> CapacityGlance | None:
        """Withhold raw-percent capacity from the shared presentation resolver."""
        return None

    def projected_rows_for_device(
        self,
        projection: AttentionProjection,
        device: StatusBarDevice,
    ):
        pin = self.settings.device_provider_pin(device.device_id)
        if not pin:
            return projection.visible_rows
        return tuple(row for row in projection.visible_rows if row.provider == pin)

    def screen_bar_blend_override(self) -> str | None:
        """Which animation the Screen Bar should render with.

        Linked (the default), the notch borrows the animation the
        HARDWARE is running, so the two surfaces are never two different
        opinions about the same moment -- one light language, two
        places. Unlinked, the Screen Bar keeps its own per-device
        choice, which is what someone tuning the notch on its own
        expects.
        """
        if not self.settings.link_screen_bar_to_hardware:
            return self.settings.device_blend_mode(VIRTUAL_DEVICE_ID)
        for device in self.settings.devices:
            if device.device_id == VIRTUAL_DEVICE_ID:
                continue
            if device.blend_mode:
                return device.blend_mode
        # No hardware opinion: fall through to the global mode, which is
        # what the hardware itself would render.
        return None

    def should_render_multi_agent(
        self,
        resolved_glance,
        projection: AttentionProjection | None,
    ) -> bool:
        """Two agents working must LOOK like two agents.

        The single-color glance path renders one fleet-wide semantic as
        one hue, which is structurally incapable of showing a crowd --
        and it was winning unconditionally, so the multi-agent renderer
        (and with it every blend mode) was dead code in production: a
        Codex session and a Claude session lit the strip one color.

        Only ordinary ACTIVE work hands over. Attention, failures,
        completions, capacity and rest stay whole-strip moments -- they
        are deliberately designed as one unmistakable signal, and a
        crowd of colors would bury them.
        """
        if projection is None or len(projection.visible_rows) < 2:
            return False
        if resolved_glance is None:
            return True
        return getattr(resolved_glance, "semantic", None) is GlanceSemantic.ACTIVE

    def projection_for_device(
        self,
        projection: AttentionProjection,
        device: StatusBarDevice,
    ) -> AttentionProjection:
        # Actionable attention is global and must break through provider
        # pins. Stable lifecycle rows follow the pin.
        if projection.actionable_attention:
            return projection
        rows = self.projected_rows_for_device(projection, device)
        if not rows:
            return dataclass_replace(
                projection,
                lifecycle_mode=LifecycleMode.IDLE,
                visible_rows=(),
                dominant_provider=None,
                click_target_agent_id=None,
            )
        priority = {
            LifecycleMode.WAITING: 0,
            LifecycleMode.ACTIVE: 1,
            LifecycleMode.FAILED_VISIBLE: 2,
            LifecycleMode.COMPLETED_RECENTLY: 3,
            LifecycleMode.IDLE: 4,
            LifecycleMode.UNKNOWN: 5,
        }
        lifecycle = min(rows, key=lambda row: priority[row.lifecycle_mode]).lifecycle_mode
        return dataclass_replace(
            projection,
            lifecycle_mode=lifecycle,
            visible_rows=rows,
            dominant_provider=rows[0].provider,
            click_target_agent_id=None,
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
        if getattr(snapshot, "operator_state", None) is not None:
            self.current_operator_state = snapshot.operator_state
        self.observe_operator_history_events(
            snapshot.operator_events,
            self.current_operator_state,
        )
        projection = self.update_attention_projection(snapshot)
        battery_snapshot = self.read_battery_snapshot()
        display_mode = self.display_aggregate_mode(projection)
        state = state_for_projection(projection)
        self.observe_connected_devices()
        self.track_ask_blocked(projection)
        self.track_working(snapshot.statuses)
        # Completions need the FULL timeline: the collector demotes every
        # inactive status to stale_statuses the moment ANY session is
        # active, so a finishing session vanished from .statuses on the
        # very tick it completed -- the sweep/banner could only ever
        # fire for the LAST session alive (the missed-celebration bug,
        # reproduced live with an injected session). The transition
        # detector's own freshness gate keeps replays silent.
        self.track_completions(
            (*snapshot.statuses, *snapshot.stale_statuses),
            operator_events=snapshot.operator_events,
        )
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
        presentation_time = self._presentation_monotonic()
        capacity = self.presentation_capacity_glance()
        resolved_glance = self.resolve_presentation_glance(
            projection,
            operator_events=snapshot.operator_events,
            capacity=capacity,
            presentation_time=presentation_time,
        )
        self.set_status_emphasis_plan(
            resolved_glance,
            (resolved_glance.cue,) if resolved_glance.cue is not None else (),
        )
        self.set_status(
            state,
            ask_count=len(ask_statuses(projection)),
            done_badge=bool(unseen_completions(snapshot, self)),
        )
        self.sync_keep_awake(display_mode)
        self.sync_leds(
            display_mode,
            battery_snapshot,
            self.active_led_display_kind(battery_snapshot),
            snapshot.statuses,
            projection=projection,
            operator_events=snapshot.operator_events,
            capacity=capacity,
            presentation_time=presentation_time,
            resolved_glance=resolved_glance,
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
            native = _canonical_agent_root_snapshot(snapshot, self)
            if native is not None:
                states, _items = native
                self.native_agent_menu_registry.publish(states, tracking=True)
            self._menu_rebuild_pending = (snapshot, state)
            return
        signature = menu_content_signature(snapshot, state, self)
        if signature == getattr(self, "_menu_signature", None):
            return
        self._menu_signature = signature
        menu = build_menu(snapshot, state, self)
        menu.setDelegate_(self)
        self.status_item.setMenu_(menu)
        native = _canonical_agent_root_snapshot(snapshot, self, menu=menu)
        if native is not None:
            states, items = native
            self.native_agent_menu_registry.install(states, items)

    def maybe_refresh_usage_summary(self, *, reason: str | None = None) -> None:
        """Plan due provider work without putting transcript IO on AppKit."""
        now = time.monotonic()
        states = getattr(self, "_usage_provider_states", {})
        for provider_id, state in tuple(states.items()):
            enabled = (
                bool(self.settings.codex_percent_enabled)
                if provider_id == "codex"
                else provider_id != "claude"
            )
            states[provider_id] = dataclass_replace(
                state,
                enabled=enabled,
                visible=True,
            )
        transcript_states = getattr(self, "_usage_transcript_states", {})
        for source_key, state in tuple(transcript_states.items()):
            transcript_states[source_key] = dataclass_replace(
                state,
                enabled=True,
                visible=True,
            )
        self._usage_provider_states = states
        self._usage_transcript_states = transcript_states
        low_power = runtime_render_environment(visible=True).low_power
        plan = plan_menu_open_refresh(
            (*transcript_states.values(), *states.values()),
            now=now,
            low_power=low_power,
        )
        if plan.invocations:
            if reason is None:
                self.request_usage_refresh(plan.invocations)
            else:
                self.request_usage_refresh(plan.invocations, reason=reason)

    def request_usage_refresh(
        self,
        source_keys: tuple[SourceKey, ...],
        *,
        reason: str | None = None,
    ) -> tuple[SourceKey, ...]:
        """Reserve exact source generations before launching provider work."""
        now = time.monotonic()
        cause = {
            "menu-open": RefreshCause.MENU_OPEN,
            "manual": RefreshCause.MANUAL,
        }.get(reason, RefreshCause.AUTOMATIC)
        requests: dict[SourceKey, int] = {}
        states = getattr(self, "_usage_provider_states", {})
        transcript_states = getattr(self, "_usage_transcript_states", {})
        for source_key in dict.fromkeys(source_keys):
            if type(source_key) is not SourceKey:
                continue
            transcript_state = transcript_states.get(source_key)
            if transcript_state is not None:
                if (
                    not transcript_state.enabled
                    or not transcript_state.visible
                    or transcript_state.in_flight
                    or transcript_state.retry_not_before > now
                ):
                    continue
                started = mark_refresh_started(transcript_state)
                transcript_states[source_key] = started
                requests[source_key] = started.generation
                continue
            provider_id = source_key.provider_id
            state = states.get(provider_id)
            refresh_key = self._capacity_refresh_keys_by_provider.get(provider_id)
            if (
                state is None
                or not state.enabled
                or not state.visible
                or refresh_key is None
                or refresh_key.source != source_key
            ):
                continue
            decision = self._capacity_refresh_coordinator.request_refresh(
                refresh_key,
                cause,
                now,
            )
            if decision.kind is not RefreshDecisionKind.START:
                if cause is RefreshCause.MANUAL and decision.retry_at is not None:
                    self._schedule_capacity_refresh_retry(
                        refresh_key,
                        retry_at=decision.retry_at,
                        now=now,
                    )
                continue
            generation = decision.generation
            assert generation is not None
            self._register_capacity_refresh_start(
                refresh_key,
                generation,
                now=now,
            )
            requests[source_key] = generation
        if not requests:
            return ()
        self._usage_provider_states = states
        self._usage_transcript_states = transcript_states
        self.update_usage_menu_fields()
        threading.Thread(
            target=self._usage_refresh_worker,
            args=(requests,),
            daemon=True,
        ).start()
        return tuple(requests)

    def _register_capacity_refresh_start(
        self,
        refresh_key: RefreshSourceKey,
        generation: int,
        *,
        now: float,
    ) -> None:
        retry_timer = self._capacity_refresh_retry_timers.pop(refresh_key, None)
        if retry_timer is not None:
            retry_timer.invalidate()
        deadline = now + CAPACITY_REFRESH_DEADLINE_SECONDS
        self._capacity_refresh_coordinator.register_started(
            refresh_key,
            generation,
            deadline,
        )
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            CAPACITY_REFRESH_DEADLINE_SECONDS,
            self,
            "capacityRefreshDeadline:",
            (refresh_key, generation),
            False,
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        self._capacity_refresh_deadline_timers[refresh_key] = timer
        provider_id = refresh_key.source.provider_id
        states = getattr(self, "_usage_provider_states", {})
        state = states.get(provider_id)
        if state is None:
            state = ProviderRefreshState(source_key=refresh_key.source)
        states[provider_id] = mark_refresh_started(state, generation=generation)
        self._usage_provider_states = states

    def _schedule_capacity_refresh_retry(
        self,
        refresh_key: RefreshSourceKey,
        *,
        retry_at: float,
        now: float,
    ) -> None:
        timers = self._capacity_refresh_retry_timers
        if refresh_key in timers:
            return
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            max(0.05, retry_at - now),
            self,
            "capacityRefreshRetry:",
            refresh_key,
            False,
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        timers[refresh_key] = timer

    @objc.IBAction
    def capacityRefreshRetry_(self, timer):
        refresh_key = timer.userInfo()
        if type(refresh_key) is not RefreshSourceKey:
            return
        timers = getattr(self, "_capacity_refresh_retry_timers", {})
        if timers.get(refresh_key) is not timer:
            return
        timers.pop(refresh_key, None)
        now = time.monotonic()
        decision = self._capacity_refresh_coordinator.take_due_queued_refresh(
            refresh_key,
            now,
        )
        if decision.kind is not RefreshDecisionKind.START:
            if decision.retry_at is not None:
                self._schedule_capacity_refresh_retry(
                    refresh_key,
                    retry_at=decision.retry_at,
                    now=now,
                )
            return
        generation = decision.generation
        assert generation is not None
        self._register_capacity_refresh_start(
            refresh_key,
            generation,
            now=now,
        )
        self.update_usage_menu_fields(monotonic_now=now)
        threading.Thread(
            target=self._usage_refresh_worker,
            args=({refresh_key.source: generation},),
            daemon=True,
        ).start()

    @objc.IBAction
    def capacityRefreshDeadline_(self, timer):
        """Expire only the exact source generation owned by this timer."""
        user_info = timer.userInfo()
        if not (
            type(user_info) is tuple
            and len(user_info) == 2
            and type(user_info[0]) is RefreshSourceKey
            and type(user_info[1]) is int
        ):
            return
        refresh_key, generation = user_info
        timers = getattr(self, "_capacity_refresh_deadline_timers", {})
        if timers.get(refresh_key) is not timer:
            return
        commit = self._capacity_refresh_coordinator.expire_deadline(
            refresh_key,
            generation,
            time.monotonic(),
        )
        if commit.kind is RefreshCommitKind.NOT_DUE:
            return
        timers.pop(refresh_key, None)
        if commit.kind is not RefreshCommitKind.TIMED_OUT:
            return
        self._project_capacity_refresh_state(refresh_key, now=commit.committed_at)
        provider_id = refresh_key.source.provider_id
        models = getattr(self, "_usage_provider_models", {})
        old_model = models.get(provider_id)
        error_text = CAPACITY_REFRESH_FAILURE_COPY[RefreshFailureKind.TIMED_OUT]
        if old_model is not None:
            models[provider_id] = dataclass_replace(
                old_model,
                stale=True,
                refreshing=False,
                error_text=error_text,
            )
        else:
            models[provider_id] = build_provider_usage_view(
                provider_id,
                provider_id.title(),
                (),
                now=commit.committed_at,
                error_text=error_text,
            )
        self._usage_provider_models = models
        self.update_usage_menu_fields(monotonic_now=commit.committed_at)
        self.schedule_capacity_timers()

    def _capacity_refresh_state(self, refresh_key, *, now):
        return next(
            row
            for row in self._capacity_refresh_coordinator.snapshot_state(now).sources
            if row.key == refresh_key
        )

    def _project_capacity_refresh_state(self, refresh_key, *, now):
        row = self._capacity_refresh_state(refresh_key, now=now)
        provider_id = refresh_key.source.provider_id
        states = getattr(self, "_usage_provider_states", {})
        state = states.get(provider_id)
        if state is None:
            state = ProviderRefreshState(source_key=refresh_key.source)
        states[provider_id] = dataclass_replace(
            state,
            last_success_at=row.last_success_at,
            in_flight=row.in_flight,
            consecutive_failures=row.consecutive_failures,
            retry_not_before=row.retry_at or 0.0,
            error_text=(
                CAPACITY_REFRESH_FAILURE_COPY.get(row.last_failure)
                if row.last_failure is not None
                else None
            ),
            generation=row.generation,
        )
        self._usage_provider_states = states
        return row

    def _finish_capacity_refresh_timer(self, refresh_key) -> None:
        timer = self._capacity_refresh_deadline_timers.pop(refresh_key, None)
        if timer is not None:
            timer.invalidate()

    def invalidate_usage_providers(self, provider_ids: tuple[str, ...]) -> None:
        """Make selected providers due and obsolete any older publishers."""
        provider_ids = tuple(dict.fromkeys(provider_ids))
        self.clear_capacity_timers(clear_attempts=True)
        now = time.monotonic()
        states = getattr(self, "_usage_provider_states", {})
        models = getattr(self, "_usage_provider_models", {})
        for provider_id in provider_ids:
            state = states.get(provider_id)
            refresh_key = self._capacity_refresh_keys_by_provider.get(provider_id)
            if state is None or refresh_key is None:
                continue
            for timers in (
                self._capacity_refresh_deadline_timers,
                self._capacity_refresh_retry_timers,
            ):
                timer = timers.pop(refresh_key, None)
                if timer is not None:
                    timer.invalidate()
            refresh_state = self._capacity_refresh_coordinator.invalidate_source(
                refresh_key,
                now=now,
            )
            states[provider_id] = dataclass_replace(
                state,
                last_success_at=None,
                in_flight=False,
                consecutive_failures=0,
                retry_not_before=0.0,
                error_text=None,
                generation=refresh_state.generation,
            )
            models.pop(provider_id, None)
        self._usage_provider_states = states
        self._usage_provider_models = models

    def clear_capacity_timers(self, *, clear_attempts: bool) -> None:
        for name in ("_capacity_reset_timer", "_capacity_countdown_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.invalidate()
            setattr(self, name, None)
        self._capacity_reset_plan = ResetBoundaryPlan(None, (), ())
        self._capacity_reset_retry_deadline = None
        self._capacity_countdown_deadline = None
        if clear_attempts:
            self._attempted_capacity_boundary_keys = ()

    def _normal_capacity_refresh_deadline(
        self,
        *,
        monotonic_now: float,
        epoch_now: float,
    ) -> float | None:
        low_power = runtime_render_environment(visible=True).low_power
        fresh_seconds = LOW_POWER_FRESH_SECONDS if low_power else DEFAULT_FRESH_SECONDS
        deadlines = []
        for state in getattr(self, "_usage_provider_states", {}).values():
            if not state.enabled or not state.visible:
                continue
            if state.in_flight:
                deadlines.append(epoch_now)
                continue
            if state.last_success_at is None:
                due_monotonic = max(monotonic_now, float(state.retry_not_before))
            else:
                due_monotonic = float(state.last_success_at) + fresh_seconds
                due_monotonic = max(due_monotonic, float(state.retry_not_before))
            deadlines.append(epoch_now + max(0.0, due_monotonic - monotonic_now))
        return min(deadlines) if deadlines else None

    def _schedule_capacity_timer(self, delay: float, selector: str):
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            delay,
            self,
            selector,
            None,
            False,
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        return timer

    def schedule_capacity_timers(self, *, epoch_now: float | None = None) -> None:
        epoch_now = time.time() if epoch_now is None else float(epoch_now)
        monotonic_now = time.monotonic()
        models = getattr(self, "_usage_provider_models", {})
        plan = plan_reset_boundary_refresh(
            tuple(models.values()),
            now=epoch_now,
            normal_refresh_deadline=self._normal_capacity_refresh_deadline(
                monotonic_now=monotonic_now,
                epoch_now=epoch_now,
            ),
            attempted_boundary_keys=set(
                getattr(self, "_attempted_capacity_boundary_keys", ())
            ),
        )
        old_plan = getattr(
            self,
            "_capacity_reset_plan",
            ResetBoundaryPlan(None, (), ()),
        )
        reset_timer = getattr(self, "_capacity_reset_timer", None)
        retry_deadline = getattr(self, "_capacity_reset_retry_deadline", None)
        preserve_retry = False
        if reset_timer is not None and retry_deadline is not None and models:
            reference_now = (
                min(epoch_now, old_plan.deadline)
                - DEFAULT_RESET_GRACE_SECONDS
                - 0.001
            )
            current_boundary = plan_reset_boundary_refresh(
                tuple(models.values()),
                now=reference_now,
                normal_refresh_deadline=None,
            )
            preserve_retry = (
                current_boundary.provider_ids == old_plan.provider_ids
                and current_boundary.boundary_keys == old_plan.boundary_keys
            )
        if preserve_retry:
            pass
        elif plan.deadline is None:
            if reset_timer is not None:
                reset_timer.invalidate()
            self._capacity_reset_timer = None
            self._capacity_reset_plan = plan
            self._capacity_reset_retry_deadline = None
        elif reset_timer is None or plan != old_plan:
            if reset_timer is not None:
                reset_timer.invalidate()
            self._capacity_reset_plan = plan
            self._capacity_reset_retry_deadline = None
            self._capacity_reset_timer = self._schedule_capacity_timer(
                max(0.05, plan.deadline - epoch_now),
                "capacityResetBoundary:",
            )

        settings_window = getattr(self, "settings_window", None)
        settings_visible = bool(
            settings_window is not None
            and callable(getattr(settings_window, "isVisible", None))
            and settings_window.isVisible()
            and getattr(self, "current_settings_pane", None) == "profile"
        )
        countdown_relevant = bool(
            getattr(self, "status_menu_open", False) or settings_visible
        )
        reset_epochs = (
            window.reset_epoch
            for model in models.values()
            for window in model.windows
            if window.reset_known
        )
        countdown_deadline = (
            next_countdown_deadline(reset_epochs, now=epoch_now)
            if countdown_relevant
            else None
        )
        countdown_timer = getattr(self, "_capacity_countdown_timer", None)
        old_countdown_deadline = getattr(
            self, "_capacity_countdown_deadline", None
        )
        if countdown_deadline is None:
            if countdown_timer is not None:
                countdown_timer.invalidate()
            self._capacity_countdown_timer = None
            self._capacity_countdown_deadline = None
        elif countdown_timer is None or countdown_deadline != old_countdown_deadline:
            if countdown_timer is not None:
                countdown_timer.invalidate()
            self._capacity_countdown_deadline = countdown_deadline
            self._capacity_countdown_timer = self._schedule_capacity_timer(
                max(0.05, countdown_deadline - epoch_now),
                "capacityCountdown:",
            )

    def _schedule_capacity_reset_retry(
        self,
        plan: ResetBoundaryPlan,
        *,
        monotonic_now: float,
    ) -> None:
        delay = MINIMUM_RESET_REFRESH_DELAY_SECONDS
        states = getattr(self, "_usage_provider_states", {})
        for provider_id in plan.provider_ids:
            state = states.get(provider_id)
            if state is None:
                continue
            delay = max(
                delay,
                float(state.retry_not_before) - monotonic_now,
            )
        self._capacity_reset_plan = plan
        self._capacity_reset_retry_deadline = monotonic_now + delay
        self._capacity_reset_timer = self._schedule_capacity_timer(
            delay,
            "capacityResetBoundary:",
        )

    @objc.IBAction
    def capacityResetBoundary_(self, timer):
        if timer is not getattr(self, "_capacity_reset_timer", None):
            return
        self._capacity_reset_timer = None
        self._capacity_reset_retry_deadline = None
        plan = getattr(
            self,
            "_capacity_reset_plan",
            ResetBoundaryPlan(None, (), ()),
        )
        epoch_now = time.time()
        if plan.deadline is None or epoch_now + 0.05 < plan.deadline:
            self.schedule_capacity_timers(epoch_now=epoch_now)
            return

        models = getattr(self, "_usage_provider_models", {})
        if models:
            reference_now = (
                min(epoch_now, plan.deadline)
                - DEFAULT_RESET_GRACE_SECONDS
                - 0.001
            )
            current_plan = plan_reset_boundary_refresh(
                tuple(models.values()),
                now=reference_now,
                normal_refresh_deadline=None,
            )
            if (
                current_plan.provider_ids != plan.provider_ids
                or current_plan.boundary_keys != plan.boundary_keys
            ):
                self.schedule_capacity_timers(epoch_now=epoch_now)
                return

        monotonic_now = time.monotonic()
        states = getattr(self, "_usage_provider_states", {})
        boundary_states = [states.get(provider_id) for provider_id in plan.provider_ids]
        if any(
            state is None or not state.enabled or not state.visible
            for state in boundary_states
        ):
            self.schedule_capacity_timers(epoch_now=epoch_now)
            return
        if any(
            state.in_flight or state.retry_not_before > monotonic_now
            for state in boundary_states
        ):
            self._schedule_capacity_reset_retry(
                plan,
                monotonic_now=monotonic_now,
            )
            return

        previous_attempts = getattr(
            self, "_attempted_capacity_boundary_keys", ()
        )
        self._attempted_capacity_boundary_keys = retain_attempted_boundary_keys(
            previous_attempts,
            plan.boundary_keys,
        )
        source_keys = tuple(
            CAPACITY_SOURCE_KEYS_BY_PROVIDER[provider_id]
            for provider_id in plan.provider_ids
            if provider_id in CAPACITY_SOURCE_KEYS_BY_PROVIDER
        )
        started = self.request_usage_refresh(
            source_keys,
            reason="reset-boundary",
        )
        if set(started) != set(source_keys):
            self._attempted_capacity_boundary_keys = tuple(previous_attempts)
            self._schedule_capacity_reset_retry(
                plan,
                monotonic_now=time.monotonic(),
            )
            return
        self.schedule_capacity_timers(epoch_now=epoch_now)

    @objc.IBAction
    def capacityCountdown_(self, timer):
        if timer is not getattr(self, "_capacity_countdown_timer", None):
            return
        self._capacity_countdown_timer = None
        self._capacity_countdown_deadline = None
        epoch_now = time.time()
        self.update_usage_menu_fields(
            monotonic_now=time.monotonic(),
            epoch_now=epoch_now,
        )
        self.schedule_capacity_timers(epoch_now=epoch_now)

    def _usage_refresh_worker(self, requests: dict[SourceKey, int]) -> None:
        """Build one frozen local snapshot, then start every source independently."""
        shared: dict[str, list] = {}
        shared_error = None
        totals = None
        workers = []
        for source_key, generation in requests.items():
            if (
                type(source_key) is not SourceKey
                or source_key.capability_id == "transcript_usage"
            ):
                continue
            worker = threading.Thread(
                target=self._usage_refresh_source_worker,
                args=(source_key, generation, None, {}, None),
                daemon=True,
            )
            workers.append(worker)
            worker.start()
        local_sources = tuple(
            source_key
            for source_key in requests
            if source_key.capability_id == "transcript_usage"
        )
        if local_sources:
            try:
                graph_days = self.settings.usage_graph_days
                period_start = (
                    datetime.now() - timedelta(days=graph_days - 1)
                ).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                totals = usage_stats.scan_usage(
                    Path.home() / ".claude" / "projects",
                    default_state_dir() / "usage-scan-cache.json",
                    since_epoch=period_start.timestamp(),
                    codex_root=Path.home() / ".codex" / "sessions",
                )
                buckets = usage_stats.daily_buckets(
                    totals.records,
                    days=graph_days,
                )
                label_stride = max(1, graph_days // 7)
                shared["day_bars"] = [
                    {
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
                shared["hourly"] = usage_stats.hourly_session_counts(
                    totals.records
                )
                shared["usage_graph"] = usage_stats.usage_graph_model(
                    totals.records,
                    days=graph_days,
                    metric=self.settings.usage_display_mode,
                    provider_ids=self.settings.usage_graph_providers,
                )
            except Exception:
                shared_error = "local_activity_unavailable"
                log_status_bar("usage scan error: local_activity_unavailable")

        for source_key, generation in requests.items():
            if (
                type(source_key) is not SourceKey
                or source_key.capability_id != "transcript_usage"
            ):
                continue
            result = self._local_usage_result(
                source_key.provider_id,
                totals,
                period_label=usage_stats.usage_period_label(
                    self.settings.usage_graph_days
                ),
            )
            failure = (
                None
                if result is not None
                else RefreshFailureKind.SOURCE_UNAVAILABLE
            )
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyUsageSummary:",
                {
                    "requests": {source_key: generation},
                    "results": (
                        {source_key: result} if result is not None else {}
                    ),
                    "failures": (
                        {source_key: failure} if failure is not None else {}
                    ),
                    "shared": dict(shared),
                    "shared_error": shared_error,
                },
                False,
            )
        for worker in workers:
            join = getattr(worker, "join", None)
            if callable(join):
                join()

    def _local_usage_result(
        self,
        provider_id: str,
        totals,
        *,
        period_label: str = "Today",
    ) -> dict | None:
        """Project one provider's local aggregate without quota authority."""
        if totals is None:
            return None
        coverage = totals.source_coverage.get(provider_id)
        partial = bool(
            coverage is not None
            and coverage.status is usage_stats.UsageSourceStatus.PARTIAL
        )
        if provider_id == "codex":
            summary = None
            if totals.codex_sessions:
                count = len(totals.codex_sessions)
                session_word = "session" if count == 1 else "sessions"
                prefix = (
                    "Codex today"
                    if period_label == "Today"
                    else f"Codex, {period_label.lower()}"
                )
                summary = (
                    f"{prefix}: {count} {session_word} \u00b7 "
                    f"{usage_stats.compact_token_count(totals.codex_tokens)} "
                    "processed tokens"
                )
            return {
                "provider_id": "codex",
                "title": "Codex",
                "summary_text": summary,
                "detail_text": None,
                "partial": partial,
                "source_text": source_text_for_coverage(coverage),
            }
        if provider_id == "claude":
            return {
                "provider_id": "claude",
                "title": "Claude",
                "summary_text": usage_stats.usage_summary_line(
                    totals,
                    self.settings.usage_display_mode,
                    period_label=period_label,
                ),
                "detail_text": (
                    f"{totals.input_tokens:,} in \u00b7 "
                    f"{totals.cached_input_tokens:,} cached \u00b7 "
                    f"{totals.output_tokens:,} out"
                    if totals.sessions
                    else None
                ),
                "partial": partial,
                "source_text": source_text_for_coverage(coverage),
            }
        return None

    def _usage_refresh_source_worker(
        self,
        source_key: SourceKey,
        generation: int,
        totals,
        shared: dict,
        shared_error: str | None,
    ) -> None:
        """Run one source adapter and publish one exact generation."""
        provider_id = source_key.provider_id
        result = None
        failure = None
        if provider_id == "codex":
            windows = []
            try:
                limits = (
                    usage_stats.codex_rate_limits(totals)
                    if totals is not None
                    else usage_stats.cached_codex_rate_limits(
                        default_state_dir() / "usage-scan-cache.json"
                    )
                )
                windows = usage_stats.codex_windows_from_limits(limits)
                if not windows:
                    raise ValueError("local capacity evidence unavailable")
                if not self.settings.codex_percent_enabled:
                    windows = []
            except Exception:
                log_status_bar("codex usage unavailable: source_unavailable")
                failure = RefreshFailureKind.SOURCE_UNAVAILABLE
            result = {
                "provider_id": "codex",
                "title": "Codex",
                "windows": windows,
            }

        elif provider_id == "claude":
            windows = []
            if self.settings.claude_plan_limits_enabled:
                try:
                    windows = claude_quota.fetch_windows()
                except claude_quota.ClaudeQuotaUnavailableError:
                    log_status_bar("claude quota unavailable: source_unavailable")
                    failure = RefreshFailureKind.SOURCE_UNAVAILABLE
                except Exception:
                    log_status_bar("claude usage unavailable: source_unavailable")
                    failure = RefreshFailureKind.SOURCE_UNAVAILABLE
            result = {
                "provider_id": "claude",
                "title": "Claude",
                "windows": windows,
            }
        else:
            failure = RefreshFailureKind.SOURCE_UNAVAILABLE

        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "applyUsageSummary:",
            {
                "requests": {source_key: generation},
                "results": ({source_key: result} if result is not None else {}),
                "failures": ({source_key: failure} if failure is not None else {}),
                "shared": shared,
                "shared_error": shared_error,
            },
            False,
        )

    @objc.IBAction
    def applyUsageSummary_(self, payload):
        if not isinstance(payload, dict):
            return
        now = time.monotonic()
        reset_now = time.time()
        requests = payload.get("requests") or {}
        results = payload.get("results") or {}
        failures = payload.get("failures") or {}
        models = getattr(self, "_usage_provider_models", {})
        accepted = False
        for source_key, generation in requests.items():
            if type(source_key) is not SourceKey:
                continue
            provider_id = source_key.provider_id
            result = results.get(source_key)
            failure = failures.get(source_key)
            if failure is not None and type(failure) is not RefreshFailureKind:
                failure = RefreshFailureKind.FAILED
            if not isinstance(result, dict) and failure is None:
                failure = RefreshFailureKind.SOURCE_UNAVAILABLE
            if source_key.capability_id == "transcript_usage":
                transcript_states = getattr(self, "_usage_transcript_states", {})
                state = transcript_states.get(source_key)
                if not (
                    state is not None
                    and type(generation) is int
                    and generation == state.generation
                    and state.in_flight
                ):
                    continue
                if failure is None:
                    state = mark_refresh_succeeded(state, now=now)
                else:
                    state = mark_refresh_failed(
                        state,
                        now=now,
                        error_text="Local activity unavailable",
                    )
                transcript_states[source_key] = state
                self._usage_transcript_states = transcript_states
                old_model = models.get(provider_id)
                if failure is None:
                    activity = LocalActivitySection(
                        summary_text=result.get("summary_text"),
                        detail_text=result.get("detail_text"),
                        partial=bool(result.get("partial", False)),
                        source_text=result.get("source_text"),
                    )
                    if old_model is None:
                        models[provider_id] = build_provider_usage_view(
                            provider_id,
                            str(result.get("title") or provider_id.title()),
                            (),
                            last_success_at=state.last_success_at,
                            now=now,
                            reset_now=reset_now,
                            local_activity=activity,
                        )
                    else:
                        last_success_at = (
                            old_model.last_success_at
                            if old_model.windows
                            else state.last_success_at
                        )
                        models[provider_id] = dataclass_replace(
                            old_model,
                            last_success_at=last_success_at,
                            stale=(old_model.stale if old_model.windows else False),
                            missing=False,
                            refreshing=False,
                            local_activity=activity,
                        )
                elif old_model is None:
                    models[provider_id] = build_provider_usage_view(
                        provider_id,
                        provider_id.title(),
                        (),
                        last_success_at=state.last_success_at,
                        now=now,
                        reset_now=reset_now,
                        error_text="Local activity unavailable",
                    )
                else:
                    models[provider_id] = dataclass_replace(
                        old_model,
                        refreshing=False,
                    )
                accepted = True
                continue
            refresh_key = self._capacity_refresh_keys_by_provider.get(provider_id)
            if refresh_key is None or refresh_key.source != source_key:
                continue
            if failure is None:
                observations = result.get("capacity_observations") or ()
                if not (
                    type(observations) is tuple
                    and all(
                        type(observation) is QuotaLaneObservation
                        for observation in observations
                    )
                ):
                    failure = RefreshFailureKind.FAILED
                else:
                    health = CapacitySourceHealth(
                        source=source_key,
                        kind=SourceHealthKind.HEALTHY,
                        observed_at=reset_now,
                        last_attempt_at=reset_now,
                        retry_at=None,
                        reason_code=None,
                        has_last_known_good=False,
                    )
                    snapshot = CapacitySnapshot(
                        observed_at=reset_now,
                        lanes=observations,
                        source_health=(health,),
                    )
                    try:
                        commit = self._capacity_refresh_coordinator.register_success(
                            refresh_key,
                            generation,
                            snapshot,
                            now,
                        )
                    except ValueError:
                        failure = RefreshFailureKind.FAILED
            if failure is not None:
                retry_at = payload.get("retry_at")
                try:
                    commit = self._capacity_refresh_coordinator.register_failure(
                        refresh_key,
                        generation,
                        failure,
                        now,
                        retry_at,
                    )
                except ValueError:
                    continue
            if commit.kind in {
                RefreshCommitKind.STALE_GENERATION,
                RefreshCommitKind.NOT_DUE,
            }:
                continue
            if commit.kind is RefreshCommitKind.TIMED_OUT:
                failure = RefreshFailureKind.TIMED_OUT
            accepted = True
            self._finish_capacity_refresh_timer(refresh_key)
            row = self._project_capacity_refresh_state(refresh_key, now=now)
            state = self._usage_provider_states[provider_id]
            old_model = models.get(provider_id)
            if failure is not None:
                error_text = CAPACITY_REFRESH_FAILURE_COPY[failure]
                if isinstance(result, dict):
                    result_windows = result.get("windows") or ()
                    if not result_windows and old_model is not None:
                        result_windows = usage_window_payloads(old_model)
                    models[provider_id] = build_provider_usage_view(
                        provider_id,
                        str(result.get("title") or provider_id.title()),
                        result_windows,
                        last_success_at=state.last_success_at,
                        now=now,
                        reset_now=reset_now,
                        error_text=error_text,
                        summary_text=(
                            result.get("summary_text")
                            if result.get("summary_text") is not None
                            else getattr(old_model, "summary_text", None)
                        ),
                        detail_text=(
                            result.get("detail_text")
                            if result.get("detail_text") is not None
                            else getattr(old_model, "detail_text", None)
                        ),
                        partial=(
                            bool(result.get("partial"))
                            if "partial" in result
                            else bool(getattr(old_model, "partial", False))
                        ),
                        source_text=(
                            result.get("source_text")
                            if result.get("source_text") is not None
                            else getattr(old_model, "source_text", None)
                        ),
                    )
                elif old_model is not None:
                    models[provider_id] = dataclass_replace(
                        old_model,
                        stale=True,
                        refreshing=False,
                        error_text=error_text,
                    )
                else:
                    models[provider_id] = build_provider_usage_view(
                        provider_id,
                        provider_id.title(),
                        (),
                        last_success_at=state.last_success_at,
                        now=now,
                        reset_now=reset_now,
                        error_text=error_text,
                    )
            elif isinstance(result, dict):
                result_windows = result.get("windows") or ()
                capacity_observations = tuple(result.get("capacity_observations") or ())
                reset_decisions = {}
                source_generation = result.get("source_generation")
                for observation in capacity_observations:
                    previous_continuity = self._capacity_reset_continuity.get(
                        observation.key
                    )
                    decision = evaluate_reset_continuity(
                        previous_continuity,
                        observation,
                        source_generation=source_generation,
                    )
                    self._capacity_reset_continuity[observation.key] = decision.state
                    reset_decisions[observation.key] = decision
                models[provider_id] = build_provider_usage_view(
                    provider_id,
                    str(result.get("title") or provider_id.title()),
                    result_windows,
                    last_success_at=row.last_success_at,
                    now=now,
                    reset_now=reset_now,
                    summary_text=(
                        result.get("summary_text")
                        if result.get("summary_text") is not None
                        else getattr(old_model, "summary_text", None)
                    ),
                    detail_text=(
                        result.get("detail_text")
                        if result.get("detail_text") is not None
                        else getattr(old_model, "detail_text", None)
                    ),
                    partial=(
                        bool(result.get("partial"))
                        if "partial" in result
                        else bool(getattr(old_model, "partial", False))
                    ),
                    source_text=(
                        result.get("source_text")
                        if result.get("source_text") is not None
                        else getattr(old_model, "source_text", None)
                    ),
                    capacity_observations=capacity_observations,
                    reset_decisions=reset_decisions,
                )
        if not accepted:
            return
        self._usage_provider_models = models
        shared = payload.get("shared") or {}
        if "usage_graph" in shared or payload.get("shared_error") is not None:
            self._usage_local_scan_complete = True
        if "day_bars" in shared:
            self.usage_day_bars = shared.get("day_bars") or []
        if "hourly" in shared:
            self.usage_hourly = shared.get("hourly") or []
        if "usage_graph" in shared:
            self.usage_graph_model = shared["usage_graph"]
        claude_model = models.get("claude")
        codex_model = models.get("codex")
        self.usage_summary_text = getattr(claude_model, "summary_text", None)
        self.usage_detail_text = getattr(claude_model, "detail_text", None)
        self.codex_summary_text = (
            codex_model.settings_text if codex_model is not None else None
        )
        self.claude_plan_text = (
            claude_model.settings_text if claude_model is not None else None
        )
        fields = getattr(self, "settings_fields", None) or {}
        usage_label = fields.get("profile_usage_label")
        if usage_label is not None:
            usage_label.setStringValue_(
                self.usage_summary_text
                or (
                    "No Claude activity in this period."
                    if self._usage_local_scan_complete
                    else "Loading local usage history…"
                )
            )
        detail_label = fields.get("profile_usage_detail")
        if detail_label is not None:
            detail_label.setStringValue_(self.usage_detail_text or "")
        codex_label = fields.get("profile_codex_label")
        if codex_label is not None:
            codex_label.setStringValue_(self.codex_summary_text or "")
        # Stash for panes built AFTER this publish -- the first worker
        # cycle usually finishes before the settings window ever exists,
        # and the graph sat empty until the next 5-minute pass.
        graph = fields.get("profile_usage_graph")
        if graph is not None:
            graph.setModel_(
                getattr(self, "usage_graph_model", None) or {}
            )
        period_label = fields.get("profile_usage_period_label")
        if period_label is not None:
            period_label.setStringValue_(
                usage_stats.usage_period_label(self.settings.usage_graph_days)
            )
        plan_label = fields.get("profile_plan_label")
        if plan_label is not None:
            plan_label.setStringValue_(self.claude_plan_text or "")
        self.update_usage_menu_fields(monotonic_now=now, epoch_now=reset_now)
        self.schedule_capacity_timers(epoch_now=reset_now)

    def update_usage_menu_fields(
        self,
        *,
        monotonic_now: float | None = None,
        epoch_now: float | None = None,
    ) -> None:
        labels = getattr(self, "_usage_menu_labels", {}) or {}
        secondary_labels = (
            getattr(self, "_usage_menu_secondary_labels", {}) or {}
        )
        models = getattr(self, "_usage_provider_models", {}) or {}
        states = getattr(self, "_usage_provider_states", {}) or {}
        now = time.monotonic() if monotonic_now is None else float(monotonic_now)
        reset_now = time.time() if epoch_now is None else float(epoch_now)
        for provider_id in ("codex", "claude"):
            label = labels.get(provider_id)
            secondary_label = secondary_labels.get(provider_id)
            if label is None and secondary_label is None:
                continue
            model = models.get(provider_id)
            state = states.get(provider_id)
            if model is None:
                model = build_provider_usage_view(
                    provider_id,
                    provider_id.title(),
                    (),
                    now=now,
                    reset_now=reset_now,
                    refreshing=bool(state and state.in_flight),
                    error_text=getattr(state, "error_text", None),
                )
            elif state is not None and model.refreshing != state.in_flight:
                model = dataclass_replace(model, refreshing=state.in_flight)
            primary, secondary = capacity_menu_lines(
                model,
                monotonic_now=now,
                epoch_now=reset_now,
            )
            if label is not None:
                label.setStringValue_(primary)
            if secondary_label is not None:
                secondary_label.setStringValue_(secondary)

    def track_quota_thresholds(self, percents: dict) -> None:
        """Retain the legacy call shape while raw-percent effects stay disabled."""
        del percents
        self.quota_last_percents = {}
        self.quota_blink_until = 0.0
        self.quota_blink_color = None
        self.quota_blink_label = None

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
            self._settings_window_closing = True
            self.reconcile_device_runtime()
            self.release_preview_engines()
        elif notification.object() is getattr(self, "setup_window", None):
            self._runtime_preview_fire_at.pop(RuntimeFeature.SETUP_DEMO, None)
            self._runtime_timer_registry.invalidate(RuntimeFeature.SETUP_DEMO)

    def menuWillOpen_(self, _menu):
        self.status_menu_open = True
        self.virtual_status_device.set_pointer_interaction_relevant(False)
        snapshot = getattr(self, "last_snapshot", None)
        if snapshot is not None and getattr(snapshot, "operator_state", None) is not None:
            # build_menu projects this same state moments later; running
            # it here too was pure duplicate work on the click path.
            self.current_operator_state = snapshot.operator_state
        # Opening the menu is a "visit": it clears the unseen-done badge
        # (mirrors T3's lastVisitedAt read/unread model).
        self.menu_last_opened_at = datetime.now(timezone.utc)
        projection = getattr(self, "current_attention_projection", None)
        if projection is not None:
            completed = sorted(
                (
                    row
                    for row in projection.visible_rows
                    if row.lifecycle_mode == LifecycleMode.COMPLETED_RECENTLY
                    and row.source_status.event_name != "SessionEnd"
                ),
                key=lambda row: (-row.updated_at.timestamp(), row.agent_id),
            )
            current_ids = [row.agent_id for row in completed]
            retained_ids = sorted(
                self.mailbox_seen_completion_ids.difference(current_ids)
            )
            self.mailbox_seen_completion_ids = set(
                (*current_ids, *retained_ids)[:100]
            )
            mailbox = project_mailbox_for_target(projection, self)
            self.current_mailbox_projection = mailbox
            self.mailbox_retained_order = dict(mailbox.retained_order)
        self.schedule_capacity_timers()
        self.maybe_refresh_usage_summary(reason="menu-open")

    def menuDidClose_(self, _menu):
        self.status_menu_open = False
        settings_window = getattr(self, "settings_window", None)
        profile_settings_visible = bool(
            settings_window is not None
            and callable(getattr(settings_window, "isVisible", None))
            and settings_window.isVisible()
            and getattr(self, "current_settings_pane", None) == "profile"
        )
        if profile_settings_visible:
            self.schedule_capacity_timers()
        else:
            countdown_timer = getattr(self, "_capacity_countdown_timer", None)
            if countdown_timer is not None:
                countdown_timer.invalidate()
            self._capacity_countdown_timer = None
            self._capacity_countdown_deadline = None
        self.virtual_status_device.set_pointer_interaction_relevant(
            bool(
                SCREEN_BAR_FEATURE_ENABLED
                and self.settings.virtual_status_device_enabled
            )
        )
        self.native_agent_menu_registry.take_deferred_after_close()
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
        self.reconcile_lid_observation()
        if not enabled:
            self.calendar_glow_until = 0.0
            self.calendar_event_title = None
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
            self._calendar_observation_timer_fired()
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
        # TO the Signals pane re-rendered every card synchronously,
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
        preview_color = None
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
        if enabled and self._reminders_permission_request_token is not None:
            return
        self.settings = self.settings.with_reminder_alerts_enabled(enabled)
        save_settings(self.settings)
        self.reminders_watch_retry_at = 0.0
        self._reminders_permission_failed = False
        self.reconcile_lid_observation()
        if not enabled:
            self._reminders_permission_generation += 1
            self._reminders_permission_request_token = None
            self.reminders_glow_until = 0.0
            self._reconcile_current_presentation_inputs()
            self.set_settings_message("Reminder glow off.")
            self.refresh_(None)
            return
        try:
            status = reminders_watch.authorization_status()
        except reminders_watch.RemindersUnavailableError:
            self._mark_reminders_permission_failed()
            self.set_settings_message("Reminders access is unavailable on this system.")
            return
        if status == reminders_watch.AUTH_AUTHORIZED:
            self.set_settings_message("Reminder glow on.")
            self._reminders_observation_timer_fired()
        elif status == reminders_watch.AUTH_NOT_DETERMINED:
            self.set_settings_message("Reminder glow on — asking macOS for access…")
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError("Reminders permission request must run on main")
            token = self._reminders_permission_generation
            self._reminders_permission_request_token = token

            def _granted(ok):
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "reminderAccessResolved:",
                    {"token": token, "granted": bool(ok)},
                    False,
                )

            try:
                reminders_watch.request_access(_granted)
            except reminders_watch.RemindersUnavailableError:
                self._reminders_permission_request_token = None
                self._mark_reminders_permission_failed()
                self.set_settings_message(
                    "Reminders access is unavailable on this system."
                )
        else:
            self._mark_reminders_permission_failed()
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
            color=None,
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
        self._reconcile_current_presentation_inputs()
        self.set_settings_message("Studio: playing your program on everything for 12s.")

    @objc.IBAction
    def stopStudioProgram_(self, _sender):
        self.test_signal_until = 0.0
        self.test_signal_key = None
        self._reconcile_current_presentation_inputs()
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
        self._reconcile_current_presentation_inputs()
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
        else:
            self.set_settings_message("Weather warnings off.")
        self.reconcile_lid_observation()
        if enabled:
            self._weather_observation_timer_fired()

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
        self._advance_weather_observation_generation()
        self._weather_observation_timer_fired()
        if latitude is None:
            self.set_settings_message("Weather location: automatic (network address).")
        else:
            self.set_settings_message(
                f"Weather location set to {latitude:g}, {longitude:g}."
            )
        self.refresh_(None)

    @objc.IBAction
    def reminderAccessResolved_(self, granted):
        payload = granted if isinstance(granted, dict) else {}
        token = payload.get("token")
        if (
            type(token) is not int
            or token != self._reminders_permission_request_token
            or token != self._reminders_permission_generation
        ):
            return
        self._reminders_permission_request_token = None
        inputs = self._presentation_scheduler_inputs
        lifecycle_active = bool(
            self._runtime_started
            and self.settings.reminder_alerts_enabled
            and self._reminders_observation_active
            and inputs is not None
            and not inputs.display_asleep
            and not inputs.app_terminating
        )
        if not lifecycle_active:
            return
        if bool(payload.get("granted")):
            self._reminders_permission_failed = False
            self.set_settings_message("Reminders access granted.")
            self.reminders_watch_retry_at = 0.0
            self._reconcile_current_presentation_inputs()
            self._reminders_observation_timer_fired()
        else:
            self._mark_reminders_permission_failed()
            self.set_settings_message(
                "Reminders access was declined — the glow stays off until "
                "it's granted in Privacy & Security → Reminders."
            )

    @objc.IBAction
    def calendarAccessResolved_(self, granted):
        if granted:
            self.set_settings_message("Calendar access granted.")
            self.calendar_watch_retry_at = 0.0
            self.reconcile_lid_observation()
            self._calendar_observation_timer_fired()
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
        previous_generation = self._os_poll_generation
        self._os_poll_generation += 1
        self._os_poll_worker.cancel_generation(previous_generation)
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
            prior_view = self._tip_highlight_view
            if prior_view is not None and prior_view is not view:
                self._clear_tip_highlight()
            self._tip_highlight_view = view
            self._tip_highlight_until = time.monotonic() + 0.9
            self._reconcile_current_presentation_inputs()
        except Exception:
            pass

    @objc.IBAction
    def clearFlash_(self, timer):
        view = self._tip_highlight_view if timer is None else timer.userInfo()
        try:
            layer = view.layer()
            if layer is not None:
                layer.setBackgroundColor_(None)
        except Exception:
            pass
        if view is self._tip_highlight_view:
            self._tip_highlight_view = None
            self._tip_highlight_until = 0.0

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
        self.invalidate_usage_providers(("codex", "claude"))
        self.maybe_refresh_usage_summary()

    @objc.IBAction
    def toggleCodexPercent_(self, sender):
        self.settings = self.settings.with_codex_percent_enabled(checkbox_is_on(sender))
        save_settings(self.settings)
        self.invalidate_usage_providers(("codex",))
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
        self.invalidate_usage_providers(("codex", "claude"))
        self.maybe_refresh_usage_summary()

    @objc.IBAction
    def toggleUsageGraphProvider_(self, sender):
        provider_id = str(sender.identifier() or "")
        if provider_id not in {"claude", "codex"}:
            return
        selected = list(self.settings.usage_graph_providers)
        if checkbox_is_on(sender):
            if provider_id not in selected:
                selected.append(provider_id)
        else:
            selected = [value for value in selected if value != provider_id]
        if not selected:
            sender.setState_(1)
            self.set_settings_message("Keep at least one usage source selected.")
            return
        ordered = tuple(
            value for value in ("claude", "codex") if value in selected
        )
        self.settings = self.settings.with_usage_graph_providers(ordered)
        save_settings(self.settings)
        self.invalidate_usage_providers(("codex", "claude"))
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
        self.invalidate_usage_providers(("claude",))
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
                    str(trusted_system_tool("launchctl")),
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
        self._runtime_started = False
        previous_inventory_generation = self._installed_agent_inventory_generation
        self._installed_agent_inventory_generation += 1
        cancel_inventory = getattr(self._os_poll_worker, "cancel_generation", None)
        if callable(cancel_inventory):
            cancel_inventory(previous_inventory_generation)
        notification_client = getattr(self, "notification_client", None)
        if notification_client is not None:
            notification_client.set_delegate(None)
            close_notifications = getattr(notification_client, "close", None)
            if callable(close_notifications):
                close_notifications(timeout_seconds=1.0)
        self._notification_action_bindings.clear()
        self._notification_events_by_work_key.clear()
        self._remove_accessibility_display_observer()
        self._status_cue_deadline = None
        self._status_cue_candidates = ()
        self._status_finite_cues = FiniteCueState(None, None, None, False)
        self._current_resolved_glance = None
        self._status_emphasis_accessibility_generation = None
        self.virtual_status_device.terminate()
        self._set_lid_observation_active(False)
        self._set_display_environment_active(False)
        self._set_calendar_observation_active(False)
        self._set_reminders_observation_active(False)
        self._set_weather_observation_active(False)
        self._runtime_preview_fire_at.clear()
        self._reminders_permission_generation += 1
        self._reminders_permission_request_token = None
        self._runtime_timer_registry.invalidate_all()
        self._runtime_worker_registry.close_all(timeout_seconds=1.0)
        for name in (
            "timer",
            "failure_signal_timer",
            "_capacity_reset_timer",
            "_capacity_countdown_timer",
        ):
            active_timer = getattr(self, name, None)
            if active_timer is not None:
                active_timer.invalidate()
        self._capacity_reset_timer = None
        self._capacity_countdown_timer = None
        for name in (
            "_capacity_refresh_deadline_timers",
            "_capacity_refresh_retry_timers",
        ):
            timers = getattr(self, name, {})
            for active_timer in tuple(timers.values()):
                active_timer.invalidate()
            timers.clear()
        self._capacity_reset_plan = ResetBoundaryPlan(None, (), ())
        self._capacity_reset_retry_deadline = None
        self._capacity_countdown_deadline = None
        self._attempted_capacity_boundary_keys = ()
        self.release_preview_engines()
        monitor = getattr(self, "monitor", None)
        if monitor is not None and hasattr(monitor, "write_latest_state"):
            # The per-event path is debounced; flush the tail on quit.
            monitor.write_latest_state()
        self.stop_event_server()
        self.closed_lid_awake.release()
        self.keep_awake.release()

    # --- Ask escalation ------------------------------------------------

    def track_ask_blocked(self, projection) -> None:
        """Per-agent ask/blocked episode tracking. Escalation follows the
        OLDEST currently-unanswered ask -- aggregate-level tracking let a
        brand-new ask inherit a stage-3 chime from a different, already
        answered agent's long episode. Pass an empty tuple to clear
        (e.g. the refresh error path, where no state can be confirmed)."""
        now = time.monotonic()
        if not isinstance(projection, AttentionProjection):
            projection = projection_for_statuses(projection, self.settings)
        current = {
            row.agent_id
            for row in projection.actionable_attention
            if row.agent_id
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
        self.reconcile_lid_observation()

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

    def track_completions(self, statuses, *, operator_events=()) -> None:
        """Detect completion transitions once and route each channel."""
        statuses = tuple(statuses or ())
        operator_events = tuple(operator_events or ())
        if not all(type(event) is CanonicalOperatorEvent for event in operator_events):
            operator_events = ()
        completion_events = {
            event.subject_key: event
            for event in operator_events
            if event.kind is TransitionKind.COMPLETED
            and type(event.subject_key) is WorkKey
            and event.interruption_class
            in {
                InterruptionClass.IMPORTANT_OUTCOME,
                InterruptionClass.COURTESY,
            }
        }
        self._notification_events_by_work_key = dict(
            sorted(
                completion_events.items(),
                key=lambda item: item[1].occurred_at_epoch,
                reverse=True,
            )[:MAX_NOTIFICATION_ACTION_BINDINGS]
        )
        previous_modes = getattr(self, "last_agent_modes", {})
        statuses_by_id = canonical_current_statuses(statuses)
        current_modes = {
            agent_id: status.mode for agent_id, status in statuses_by_id.items()
        }
        self.last_agent_modes = current_modes
        batch = detect_completion_batch(
            previous_modes,
            tuple(statuses_by_id.values()),
            datetime.now(timezone.utc),
        )
        if not batch:
            return
        log_status_bar(
            "completion batch: "
            + ", ".join(status.agent_id[:60] for status in batch.statuses)
        )
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

        def completion_color(status) -> str:
            return (
                self.settings.colors.session_color(status.agent_id)
                or identity.get(status.agent_id)
                or self.settings.signal_style(
                    signals_module.SIGNAL_COMPLETION
                ).color
            )

        if self.settings.completion_notification_enabled:
            for status in batch.statuses:
                try:
                    self.post_completion_notification(status)
                except Exception as exc:
                    log_status_bar(
                        "completion notification routing failed "
                        f"({status.agent_id[:60]}): {exc}"
                    )

        if self.webhook_event_enabled("completion"):
            for status in batch.statuses:
                try:
                    self.post_webhook(
                        {
                            "event": "sidepulse.completion",
                            "provider": status.provider,
                            "label": status.display_name[:80],
                            "color": completion_color(status),
                        }
                    )
                except Exception as exc:
                    log_status_bar(
                        "completion webhook routing failed "
                        f"({status.agent_id[:60]}): {exc}"
                    )

        if not self.settings.completion_sweep_enabled:
            return

        visual_status = batch.statuses[0]
        self.completion_sweep_color = completion_color(visual_status)
        hold = signals_module.signal_hold_seconds(
            self.settings.signal_style(signals_module.SIGNAL_COMPLETION)
        )
        self.completion_sweep_until = time.monotonic() + hold
        # The Exhale: when the LAST working session just finished and
        # nothing needs you, the bar takes one slow warm breath after
        # the sweep -- "you're free" as a felt moment, not a light that
        # merely stops. Same freshness discipline as the sweep (we are
        # inside the edge-triggered, 2-minute-fresh path already).
        all_done = all(
            mode == AgentMode.COMPLETED for mode in current_modes.values()
        )
        projection = getattr(self, "current_attention_projection", None)
        no_asks = projection is None or not projection.actionable_attention
        if all_done and no_asks:
            self.all_clear_until = self.completion_sweep_until + 3.6
        self._reconcile_current_presentation_inputs()

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
        projection = getattr(self, "current_attention_projection", None)
        return bool(projection and projection.actionable_attention)

    def hard_ask_renders_on_device(self, device: StatusBarDevice) -> bool:
        """Would THIS device actually show the live ask? Weather may
        only yield to an ask the user can see on that surface --
        yielding on a device pinned to the other provider (or parked
        on an ambient Studio/Timer/Runway display) blanked the
        emergency while showing nothing blocked-on-you in its place."""
        if device.display != LED_DISPLAY_AGENT:
            return False
        projection = getattr(self, "current_attention_projection", None)
        if projection is None:
            return False
        pin = self.settings.device_provider_pin(device.device_id)
        return any(
            not pin or row.provider == pin
            for row in projection.actionable_attention
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
        cleared.update(
            status.agent_id
            for status in eligible_mailbox_completion_statuses(snapshot)
        )
        self.cleared_session_ids = cleared
        self.mailbox_retained_order = {
            agent_id: stable_order
            for agent_id, stable_order in self.mailbox_retained_order.items()
            if agent_id not in cleared
        }
        self.mailbox_seen_completion_ids.difference_update(cleared)
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
                    [str(trusted_system_tool("shortcuts")), "run", name],
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
        self.reconcile_lid_observation()
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
        self.reconcile_lid_observation()
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
        """Apply one semantic escalation stage and its finite deliveries."""
        stage = self.current_escalation_stage()
        changed = stage != self.escalation_last_stage
        self.escalation_last_stage = stage

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
                state = getattr(self, "current_operator_state", None)
                if type(state) is CanonicalOperatorState:
                    request = next(
                        (
                            candidate
                            for candidate in state.requests
                            if candidate.phase is RequestPhase.LIVE_UNACKNOWLEDGED
                            and candidate.next_actor is NextActor.USER
                            and candidate.source_freshness is SourceFreshness.FRESH
                        ),
                        None,
                    )
                    if request is not None:
                        self._deliver_semantic_notification(
                            request.semantic_event_key,
                            InterruptionClass.ACTION_REQUIRED,
                            prefix="attention",
                            request_key=request.key,
                        )
            self.fire_escalation_webhook(stage)

        if changed and allow_refresh:
            self.refresh_(None)

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

    def _prune_notification_action_bindings(self, *, now: float) -> None:
        state = getattr(self, "current_operator_state", None)
        current_generation = state.generation if type(state) is CanonicalOperatorState else -1
        retained = {
            token: value
            for token, value in self._notification_action_bindings.items()
            if value[0].expires_at_epoch > now
            and value[0].operator_generation == current_generation
        }
        if len(retained) > MAX_NOTIFICATION_ACTION_BINDINGS:
            ordered = sorted(
                retained.items(),
                key=lambda item: (
                    item[1][0].expires_at_epoch,
                    item[0],
                ),
                reverse=True,
            )
            retained = dict(ordered[:MAX_NOTIFICATION_ACTION_BINDINGS])
        self._notification_action_bindings = retained

    def _issue_notification_action(
        self,
        event_key: SemanticEventKey,
    ) -> ActionTokenBinding | None:
        state = getattr(self, "current_operator_state", None)
        if type(event_key) is not SemanticEventKey or type(state) is not CanonicalOperatorState:
            return None
        now = time.time()
        self._prune_notification_action_bindings(now=now)
        binding = issue_action_token(
            randomness=secrets.token_bytes(32),
            event_key=event_key,
            operator_generation=state.generation,
            now=now,
            ttl_seconds=NOTIFICATION_ACTION_TTL_SECONDS,
        )
        if len(self._notification_action_bindings) >= MAX_NOTIFICATION_ACTION_BINDINGS:
            oldest = min(
                self._notification_action_bindings,
                key=lambda token: (
                    self._notification_action_bindings[token][0].expires_at_epoch,
                    token,
                ),
            )
            self._notification_action_bindings.pop(oldest, None)
        self._notification_action_bindings[binding.token] = (binding, event_key)
        return binding

    def _deliver_semantic_notification(
        self,
        event_key: SemanticEventKey,
        interruption_class: InterruptionClass,
        *,
        prefix: str,
        request_key: RequestKey | None = None,
    ) -> bool:
        if (
            type(event_key) is not SemanticEventKey
            or type(interruption_class) is not InterruptionClass
            or type(prefix) is not str
            or prefix not in {"completion", "attention"}
            or (request_key is not None and type(request_key) is not RequestKey)
        ):
            return False
        binding = self._issue_notification_action(event_key)
        if binding is None:
            return False
        route = InterruptionRoute(
            event_key=event_key,
            interruption_class=interruption_class,
            request_key=request_key,
            deliveries=(),
            static_visibility_required=False,
        )
        copy = generic_notification_copy(route)
        delivered = self._notification_client_for_use().deliver(
            f"{prefix}.{binding.event_fingerprint}",
            copy.title,
            copy.body,
            action_token_metadata(binding),
        )
        if not delivered:
            self._notification_action_bindings.pop(binding.token, None)
        return delivered

    def _activate_notification_action(self, token: object) -> bool:
        if type(token) is not str:
            return False
        stored = self._notification_action_bindings.pop(token, None)
        state = getattr(self, "current_operator_state", None)
        if stored is None or type(state) is not CanonicalOperatorState:
            return False
        binding, event_key = stored
        resolved = resolve_action_token(
            binding,
            presented_token=token,
            candidate_event_keys=(event_key,),
            current_generation=state.generation,
            now=time.time(),
        )
        if resolved is None:
            return False
        subject = resolved.subject_key
        work_key = subject if type(subject) is WorkKey else subject.work_key
        snapshot = getattr(self, "last_snapshot", None)
        if snapshot is None:
            return False
        statuses = (*snapshot.statuses, *getattr(snapshot, "stale_statuses", ()))
        status = next(
            (
                candidate
                for candidate in statuses
                if getattr(candidate, "work_key", None) == work_key
            ),
            None,
        )
        if status is None:
            return False
        self.open_session(status, None, remember=False)
        return True

    def post_completion_notification(self, status) -> None:
        """Deliver one content-free banner for an exact canonical edge."""
        if (
            status is None
            or not self.settings.completion_notification_enabled
            or self.courtesy_signals_held()
        ):
            return
        work_key = getattr(status, "work_key", None)
        event = self._notification_events_by_work_key.get(work_key)
        if event is None:
            return
        self._deliver_semantic_notification(
            event.key,
            event.interruption_class,
            prefix="completion",
        )

    @objc.typedSelector(b"v@:@@@?")
    def userNotificationCenter_willPresentNotification_withCompletionHandler_(
        self,
        _center,
        _notification,
        completion_handler,
    ) -> None:
        completion_handler(NOTIFICATION_FOREGROUND_PRESENTATION_OPTIONS)

    @objc.typedSelector(b"v@:@@@?")
    def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
        self,
        _center,
        response,
        completion_handler,
    ) -> None:
        try:
            metadata = response.notification().request().content().userInfo() or {}
            token = metadata.get("action_token") if type(metadata) is dict else None
            self._activate_notification_action(token)
        except Exception:
            pass
        finally:
            completion_handler()

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
        projection = getattr(self, "current_attention_projection", None)
        asks = ask_statuses(projection) if projection is not None else []
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

    def reconcile_status_emphasis(
        self,
        glance: ResolvedGlance,
        cues: tuple,
        *,
        now: float,
        sleeping: bool,
    ) -> FiniteCueState:
        if (
            not self._runtime_started
            or type(glance) is not ResolvedGlance
            or type(cues) is not tuple
            or type(sleeping) is not bool
        ):
            return self._status_finite_cues
        preferences = self._accessibility_display_preferences
        play_motion = not sleeping and not bool(
            preferences is not None and preferences.reduce_motion
        )
        previous_deadline = self._status_cue_deadline
        finite_cues = self.status_cue_coordinator.observe(
            cues,
            now=now,
            play_motion=play_motion,
        )
        self._status_finite_cues = finite_cues
        self._status_cue_candidates = cues
        self._status_cue_deadline = finite_cues.next_deadline
        self._current_resolved_glance = glance
        self._status_emphasis_accessibility_generation = (
            self._accessibility_generation
        )
        self._apply_status_accessibility_text(glance, finite_cues)
        if finite_cues.next_deadline != previous_deadline:
            self._reconcile_current_presentation_inputs()
        return finite_cues

    def set_status_emphasis_plan(
        self,
        glance: ResolvedGlance,
        cues: tuple,
    ) -> bool:
        current = self._current_resolved_glance
        if (
            not self._runtime_started
            or type(glance) is not ResolvedGlance
            or not isinstance(glance.semantic, GlanceSemantic)
            or not isinstance(glance.glyph, SemanticGlyph)
            or not isinstance(glance.override_reason, GlanceOverrideReason)
            or not valid_presentation_time(glance.relay_epoch)
            or (
                glance.next_visual_change_at is not None
                and not valid_presentation_time(glance.next_visual_change_at)
            )
            or (glance.cue is not None and not valid_finite_cue(glance.cue))
            or type(cues) is not tuple
            or len(cues) > 2
            or any(type(cue) is not FiniteCue or not valid_finite_cue(cue) for cue in cues)
            or len({cue.event_key for cue in cues}) != len(cues)
            or (
                type(current) is ResolvedGlance
                and float(glance.relay_epoch) < float(current.relay_epoch)
            )
        ):
            return False
        self._current_resolved_glance = glance
        self._status_cue_candidates = cues
        self._status_emphasis_accessibility_generation = (
            self._accessibility_generation
        )
        return True

    def _discard_status_emphasis_plan(self) -> None:
        glance = self._current_resolved_glance
        finite_cues = self.status_cue_coordinator.observe(
            self._status_cue_candidates,
            now=self._presentation_monotonic(),
            play_motion=False,
        )
        self._status_finite_cues = finite_cues
        self._status_cue_deadline = None
        self._status_cue_candidates = ()
        self._current_resolved_glance = None
        self._status_emphasis_accessibility_generation = None
        if type(glance) is ResolvedGlance:
            self._apply_status_accessibility_text(glance, finite_cues)
        self._reconcile_current_presentation_inputs()

    def _apply_status_accessibility_text(
        self,
        glance: ResolvedGlance,
        finite_cues: FiniteCueState,
    ) -> None:
        state = self.current_operator_state
        if type(state) is not CanonicalOperatorState or self.status_item is None:
            return
        button = self.status_item.button()
        if button is None:
            return
        text = status_item_accessibility(
            state,
            glance,
            finite_cues=finite_cues,
        )
        button.setTitle_(f" {text.value}")
        for selector, value in (
            ("setAccessibilityLabel_", text.label),
            ("setAccessibilityValue_", text.value),
            ("setAccessibilityHelp_", text.help),
        ):
            setter = getattr(button, selector, None)
            if callable(setter):
                setter(value)
        button.setToolTip_(text.help)

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
        glance = self._current_resolved_glance
        if type(glance) is ResolvedGlance and (
            not self._runtime_started
            or self._status_emphasis_accessibility_generation
            != self._accessibility_generation
        ):
            self._discard_status_emphasis_plan()
            glance = None
        if type(glance) is ResolvedGlance:
            inputs = self._presentation_scheduler_inputs
            sleeping = bool(
                inputs is not None
                and (inputs.display_asleep or inputs.app_terminating)
            )
            self.reconcile_status_emphasis(
                glance,
                self._status_cue_candidates,
                now=self._presentation_monotonic(),
                sleeping=sleeping,
            )
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
        operator_state = self.current_operator_state
        if (
            type(glance) is ResolvedGlance
            and type(operator_state) is CanonicalOperatorState
        ):
            title = f" {status_item_accessibility(operator_state, glance, finite_cues=self._status_finite_cues).value}"
        button.setTitle_(title)
        button.setImage_(image_for_symbol(state.symbol, state.label))
        button.setToolTip_(f"SidePulse Agent Monitor: {state.label}")
        if type(glance) is ResolvedGlance:
            self._apply_status_accessibility_text(
                glance,
                self._status_finite_cues,
            )
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
        self.monitor.statuses_by_key = self.monitor.current_statuses_by_key()
        self.transcript_watermark = newest
        self.transcript_fallback_signature = signature

    def reload_monitor(self) -> None:
        self.monitor = self.build_monitor()
        self.transcript_monitor = self.build_transcript_monitor()
        self.transcript_watermark = None
        self.transcript_fallback_signature = None

    def start_event_server(self) -> None:
        self.stop_event_server()
        self.event_server = HookEventServer(
            self.handle_hook_event_message,
            on_legacy_hook=self.note_legacy_hook,
        )
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

    def note_legacy_hook(self, provider: str) -> None:
        """A hook predating the refresh-hint protocol just called us.

        Its wire payload is not authoritative (the registered log is),
        so it cannot be ingested directly -- but silence is the worst
        possible answer: an out-of-date hook once deafened the app to
        every live agent event for an hour while it logged nothing a
        user would ever see. Wake the monitor anyway (the log that hook
        wrote is still read on refresh) and remember the provider so
        the dropdown can say which hook needs reinstalling.
        """
        if type(provider) is not str or not provider:
            return
        if provider not in self.legacy_hook_providers:
            self.legacy_hook_providers.add(provider)
            log_status_bar(
                f"legacy hook protocol from {provider}: reinstall its hook from Setup"
            )
            self._menu_signature = None
        self.schedule_event_refresh()

    def handle_hook_event_message(self, hint: ProviderRefreshHint) -> None:
        """Reconcile one authenticated hint from the persisted normalized log.

        Hook processes deliberately send no lifecycle payload over IPC. The
        socket message is only a bounded wake-up hint; the registered private
        log remains the authority that the live monitor rereads here.
        """
        if type(hint) is not ProviderRefreshHint:
            return
        try:
            self.monitor.reconcile_refresh_hint(
                hint,
                log_path=detect_log_path(hint.source_key.provider_id),
            )
            self.schedule_event_refresh()
        except Exception:
            log_status_bar("event_server reconciliation error")

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
        if key == "led_behavior":
            self.refresh_notification_authorization_controls()
            self.start_notification_authorization_refresh()
        if key == "history":
            self.start_operator_history_restore()
            self.refresh_operator_history_projection()
        if key == "installed_agents":
            self.refresh_installed_agents_settings_projection()
            self.reconcile_installed_agent_inventory()
        if key == "capacity":
            self.refresh_capacity_settings_projection()

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
        self._settings_window_closing = False
        if self.settings_window is None:
            selected_key = self.current_settings_pane or DEFAULT_SETTINGS_PANE
            self.settings_window = build_settings_window(self)
            if self.settings_sidebar_table is not None:
                selected_row = next(
                    index
                    for index, (key, _label) in enumerate(SETTINGS_SIDEBAR_ITEMS)
                    if key == selected_key
                )
                self.settings_sidebar_table.selectRowIndexes_byExtendingSelection_(
                    NSIndexSet.indexSetWithIndex_(selected_row), False
                )
        self.refresh_settings_window()
        self.maybe_refresh_usage_summary()
        self.settings_window.makeKeyAndOrderFront_(None)
        self._reconcile_current_presentation_inputs()
        NSApp.activateIgnoringOtherApps_(True)

    @objc.IBAction
    def openAgentBrowser_(self, sender) -> bool:
        payload = sender.representedObject() if sender is not None else None
        if type(payload) is not AgentBrowserOpenPayload:
            return False
        snapshot = getattr(self, "last_snapshot", None)
        if snapshot is None:
            return False
        projection = _canonical_agent_browser_projection(
            snapshot,
            self,
            shelf=payload.shelf,
            family_key=payload.family_key,
        )
        if projection is None or projection.generation != payload.generation:
            return False
        controller = getattr(self, "agent_browser_controller", None)
        if controller is None:
            controller = AgentBrowserWindowController.alloc().init()
            self.agent_browser_controller = controller
        controller.action_handler = self.performAgentBrowserPayload_
        controller.visit_handler = self.recordAgentBrowserVisit_
        controller.worker_scope = payload.family_key
        controller.shelf_scope = payload.shelf
        controller.query_handler = lambda text, *, family_key, selected_work_key: (
            _canonical_agent_browser_projection(
                self.last_snapshot,
                self,
                text=text,
                shelf=payload.shelf if family_key is None else None,
                family_key=family_key,
                selected_work_key=selected_work_key,
            )
        )
        controller.open_with_projection(
            projection,
            actions_by_work_key=_canonical_operator_actions(
                self.current_operator_state,
                self,
            ),
            error_message=self.operator_action_error,
        )
        NSApp.activateIgnoringOtherApps_(True)
        return True

    @objc.IBAction
    def performBrowserAction_(self, sender) -> bool:
        payload = sender.representedObject() if sender is not None else None
        performed = self.performAgentBrowserPayload_(payload)
        if performed and type(payload) is AgentBrowserActionPayload:
            self.recordAgentBrowserVisit_(payload.work_key)
        return performed

    def performAgentBrowserPayload_(self, payload) -> bool:
        if type(payload) is not AgentBrowserActionPayload:
            return False
        state = getattr(self, "current_operator_state", None)
        if state is None or payload.generation != state.generation:
            return False
        work = next((item for item in state.works if item.key == payload.work_key), None)
        if work is None:
            return False
        descriptors = _canonical_operator_actions(state, self).get(payload.work_key, ())
        if not any(item.kind is payload.kind and item.enabled for item in descriptors):
            return False
        if payload.kind is OperatorActionKind.OPEN:
            resolution = resolve_navigation(
                work.key,
                "open:primary",
                getattr(self, "navigation_candidates_by_work_key", {}).get(
                    work.key,
                    (),
                ),
            )
            return activate_navigation_resolution(
                resolution,
                open_url=open_url,
                open_terminal_command=open_terminal_command,
            )
        if payload.kind in {
            OperatorActionKind.ACKNOWLEDGE,
            OperatorActionKind.RESUME_ESCALATION,
        }:
            return self._apply_triage_action(payload, state)
        return self._apply_preference_action(payload)

    def recordAgentBrowserVisit_(self, work_key) -> bool:
        state = getattr(self, "current_operator_state", None)
        if state is None or not any(work.key == work_key for work in state.works):
            return False
        family_key = _family_work_key(state, work_key)
        if family_key is None:
            return False
        preference = _preference_for_work_key(self.mailbox_preferences, family_key)
        updated = dataclass_replace(
            preference or MailboxPreference(family_key),
            last_visited_at=time.time(),
        )
        self._publish_mailbox_preferences(
            _replace_mailbox_preference(self.mailbox_preferences, updated)
        )
        return True

    def _apply_preference_action(self, payload: AgentBrowserActionPayload) -> bool:
        family_key = _family_work_key(
            self.current_operator_state,
            payload.work_key,
        )
        if family_key is None:
            return False
        preferences = self.mailbox_preferences
        existing = _preference_for_work_key(preferences, family_key)
        preference = existing or MailboxPreference(family_key)
        if payload.kind is OperatorActionKind.WATCH:
            preference = dataclass_replace(
                preference,
                mode=MailboxPreferenceMode.WATCHED,
                pin_order=None,
            )
        elif payload.kind is OperatorActionKind.UNWATCH:
            preference = dataclass_replace(
                preference,
                mode=MailboxPreferenceMode.DEFAULT,
            )
        elif payload.kind is OperatorActionKind.PIN:
            pin_orders = [
                item.pin_order
                for item in preferences
                if item.mode is MailboxPreferenceMode.PINNED
                and item.pin_order is not None
            ]
            preference = dataclass_replace(
                preference,
                mode=MailboxPreferenceMode.PINNED,
                pin_order=(max(pin_orders, default=-1) + 1),
            )
        elif payload.kind is OperatorActionKind.UNPIN:
            preference = dataclass_replace(
                preference,
                mode=MailboxPreferenceMode.DEFAULT,
                pin_order=None,
            )
        elif payload.kind in {
            OperatorActionKind.MOVE_PIN_UP,
            OperatorActionKind.MOVE_PIN_DOWN,
        }:
            moved = _move_pinned_preference(
                preferences,
                family_key,
                delta=-1 if payload.kind is OperatorActionKind.MOVE_PIN_UP else 1,
            )
            if moved is None:
                return False
            self._publish_mailbox_preferences(moved)
            return True
        elif payload.kind is OperatorActionKind.SNOOZE:
            duration = {
                "15-minutes": 900.0,
                "1-hour": 3_600.0,
                "tomorrow": 86_400.0,
            }.get(payload.snooze_preset)
            if duration is None:
                return False
            now = time.time()
            preference = dataclass_replace(
                preference,
                snoozed_at=now,
                snoozed_until=now + duration,
            )
        elif payload.kind is OperatorActionKind.UNSNOOZE:
            preference = dataclass_replace(
                preference,
                snoozed_at=None,
                snoozed_until=None,
            )
        else:
            return False
        self._publish_mailbox_preferences(
            _replace_mailbox_preference(preferences, preference)
        )
        return True

    def _apply_triage_action(self, payload, state) -> bool:
        request = _request_for_work(state, payload.work_key)
        if request is None:
            return False
        mutation = (
            LocalTriageMutationKind.ACKNOWLEDGE
            if payload.kind is OperatorActionKind.ACKNOWLEDGE
            else LocalTriageMutationKind.RESUME_ESCALATION
        )
        occurred_at = time.time()
        try:
            updated = apply_local_triage_mutation(
                self.local_triage_state,
                request=request,
                mutation=mutation,
                now=occurred_at,
            )
        except ValueError:
            return False
        self.local_triage_state = updated
        self.local_triage_dirty = True
        try:
            self.operator_triage_saver(updated)
        except OSError:
            self.operator_action_error = (
                "Could not save acknowledgement. SidePulse will retry."
            )
        else:
            self.local_triage_dirty = False
            self.operator_action_error = None
        self.observe_operator_history_triage(
            request,
            mutation,
            occurred_at=occurred_at,
            state=state,
        )
        self._republish_operator_surfaces()
        return True

    def _publish_mailbox_preferences(self, preferences) -> None:
        self.mailbox_preferences = tuple(preferences)
        self.mailbox_preferences_dirty = True
        try:
            self.mailbox_preferences_saver(self.mailbox_preferences)
        except OSError:
            self.operator_action_error = (
                "Could not save mailbox change. SidePulse will retry."
            )
        else:
            self.mailbox_preferences_dirty = False
            self.operator_action_error = None
        self._republish_operator_surfaces()

    def _republish_operator_surfaces(self) -> None:
        snapshot = getattr(self, "last_snapshot", None)
        if snapshot is None or getattr(snapshot, "operator_state", None) is None:
            return
        browser = getattr(self, "agent_browser_controller", None)
        if browser is not None:
            projection = _canonical_agent_browser_projection(
                snapshot,
                self,
                text=str(browser.search_field.stringValue()),
                shelf=(
                    browser.shelf_scope
                    if browser.worker_scope is None
                    else None
                ),
                family_key=browser.worker_scope,
                selected_work_key=browser.selected_work_key,
            )
            if projection is not None:
                browser.publish_projection(
                    projection,
                    actions_by_work_key=_canonical_operator_actions(
                        self.current_operator_state,
                        self,
                    ),
                    error_message=self.operator_action_error,
                )
        self._menu_signature = None
        if self.status_item is not None:
            self.update_status_menu(snapshot, self.current_state)

    def schedule_mailbox_boundary(self, deadline_epoch) -> None:
        if deadline_epoch is None:
            timer = self.mailbox_boundary_timer
            if timer is not None:
                timer.invalidate()
            self.mailbox_boundary_timer = None
            self.mailbox_boundary_schedule.clear()
            return
        if self.mailbox_boundary_schedule.deadline_epoch == deadline_epoch:
            return
        timer = self.mailbox_boundary_timer
        if timer is not None:
            timer.invalidate()
        token = self.mailbox_boundary_schedule.replace(deadline_epoch)
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            max(0.05, deadline_epoch - time.time()),
            self,
            "mailboxBoundary:",
            token,
            False,
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        self.mailbox_boundary_timer = timer

    @objc.IBAction
    def mailboxBoundary_(self, timer) -> None:
        token = timer.userInfo()
        if not self.mailbox_boundary_schedule.callback_due(
            token,
            now_epoch=time.time(),
        ):
            return
        self.mailbox_boundary_timer = None
        snapshot = getattr(self, "last_snapshot", None)
        if snapshot is None:
            return
        _canonical_agent_browser_projection(snapshot, self)
        self._menu_signature = None
        if self.status_item is not None:
            self.update_status_menu(snapshot, self.current_state)

    def load_operator_local_state(self) -> None:
        state_dir = default_state_dir()
        document = load_mailbox_preference_document(
            state_dir / "mailbox-preferences.json"
        )
        if document.version == 2 and not document.degraded:
            self.mailbox_preferences = document.preferences
        self.local_triage_state = load_operator_triage(
            state_dir / "operator-triage.json"
        )

    def _operator_history_timezone_offset_minutes(self) -> int:
        offset = datetime.now().astimezone().utcoffset()
        return 0 if offset is None else int(offset.total_seconds() // 60)

    def _set_operator_history_status(self, message: str) -> None:
        self._operator_history_operation_status = message
        set_field_value(
            self.settings_fields.get("history_operation_status"),
            message or "No history operation in progress.",
        )

    def refresh_operator_history_projection(self) -> None:
        projection = self.operator_history_store.project(
            range_days=self.operator_history_range_days,
            now=time.time(),
            timezone_offset_minutes=self._operator_history_timezone_offset_minutes(),
        )
        self.operator_history_projection = projection
        set_field_value(
            self.settings_fields.get("history_summary"),
            (
                "Operator history is being restored."
                if self._operator_history_restore_pending
                else " ".join(projection.summary_sentences)
            ),
        )
        set_field_value(
            self.settings_fields.get("history_health"),
            (
                "History loading"
                if self._operator_history_restore_pending
                else projection.health_label
            ),
        )
        set_field_value(
            self.settings_fields.get("history_semantic_reel"),
            "\n".join(self.operator_history_reel) or "No current-run events.",
        )
        retention_controls = self.settings_fields.get(
            "history_retention_controls",
            {},
        )
        for retention, control in retention_controls.items():
            control.setState_(
                1
                if retention == self.settings.operator_history_retention_days
                else 0
            )
        range_controls = self.settings_fields.get("history_range_controls", {})
        for range_days, control in range_controls.items():
            control.setState_(1 if range_days == self.operator_history_range_days else 0)

    def start_operator_history_restore(self) -> None:
        if (
            self._operator_history_restore_started
            or self.settings.operator_history_retention_days == 0
        ):
            return
        self._operator_history_restore_started = True
        self._operator_history_restore_pending = True
        self._operator_history_retention_generation += 1
        generation = self._operator_history_retention_generation
        path = self.operator_history_store.path

        def _restore() -> None:
            store = OperatorHistoryStore(
                path,
                retention_days=self.settings.operator_history_retention_days,
            )
            restored = store.restore()
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyOperatorHistoryRestore:",
                (generation, store, restored.health),
                False,
            )

        threading.Thread(
            target=_restore,
            daemon=True,
            name="sidepulse-operator-history-restore",
        ).start()

    @objc.IBAction
    def applyOperatorHistoryRestore_(self, payload) -> None:
        if not (
            type(payload) is tuple
            and len(payload) == 3
            and payload[0] == self._operator_history_retention_generation
            and type(payload[1]) is OperatorHistoryStore
            and type(payload[2]) is OperatorHistoryRestoreHealth
        ):
            return
        with self._operator_history_lock:
            self.operator_history_store = payload[1]
            self.operator_history_restore_health = payload[2]
            self._operator_history_restore_pending = False
        self.refresh_operator_history_projection()

    @objc.IBAction
    def changeOperatorHistoryRange_(self, sender) -> None:
        value = sender.representedObject() if sender is not None else None
        if type(value) is not int or value not in {1, 7, 30}:
            return
        self.operator_history_range_days = value
        self.refresh_operator_history_projection()

    @objc.IBAction
    def changeOperatorHistoryRetention_(self, sender) -> None:
        value = sender.representedObject() if sender is not None else None
        if type(value) is int:
            self.start_operator_history_retention_change(value)

    def start_operator_history_retention_change(self, retention_days: int) -> None:
        if type(retention_days) is not int or retention_days not in {0, 7, 30, 90}:
            return
        self._operator_history_retention_generation += 1
        generation = self._operator_history_retention_generation
        candidate_settings = dataclass_replace(
            self.settings,
            operator_history_retention_days=retention_days,
        )
        self._set_operator_history_status("Updating history retention.")

        def _change() -> None:
            try:
                with self._operator_history_lock:
                    if generation != self._operator_history_retention_generation:
                        return
                    current_state = self.operator_history_store.state
                    path = self.operator_history_store.path
                    save_settings(candidate_settings)
                    state = save_operator_history(
                        path,
                        current_state,
                        retention_days=retention_days,
                        now=time.time(),
                    )
                    store = OperatorHistoryStore(
                        path,
                        retention_days=retention_days,
                    )
                    store.state = state
                payload = (generation, retention_days, candidate_settings, store, None)
            except Exception:
                payload = (generation, retention_days, None, None, "History setting could not be saved.")
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyOperatorHistoryRetentionResult:",
                payload,
                False,
            )

        threading.Thread(
            target=_change,
            daemon=True,
            name="sidepulse-operator-history-retention",
        ).start()

    @objc.IBAction
    def applyOperatorHistoryRetentionResult_(self, payload) -> None:
        if not (
            type(payload) is tuple
            and len(payload) == 5
            and payload[0] == self._operator_history_retention_generation
        ):
            return
        _generation, retention, settings, store, error = payload
        self._operator_history_restore_pending = False
        if error is not None:
            self._set_operator_history_status(error)
            self.refresh_operator_history_projection()
            return
        self.settings = settings
        with self._operator_history_lock:
            self.operator_history_store = store
            self.operator_history_restore_health = (
                OperatorHistoryRestoreHealth.MISSING
                if retention == 0
                else OperatorHistoryRestoreHealth.HEALTHY
            )
        self._set_operator_history_status(
            "History is off."
            if retention == 0
            else f"History retention is {retention} days."
        )
        self.refresh_operator_history_projection()

    @objc.IBAction
    def confirmClearOperatorHistory_(self, _sender) -> bool:
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Clear operator history?")
        alert.setInformativeText_(
            "This removes only the private derived operator-history file. "
            "Mailbox preferences, capacity observations, and settings remain."
        )
        alert.addButtonWithTitle_("Clear History")
        alert.addButtonWithTitle_("Cancel")
        return alert.runModal() == NSAlertFirstButtonReturn

    @objc.IBAction
    def clearOperatorHistory_(self, sender) -> None:
        if not self.confirmClearOperatorHistory_(sender):
            return
        try:
            with self._operator_history_lock:
                self.operator_history_store.clear()
        except Exception:
            self._set_operator_history_status("History could not be cleared.")
            return
        self._set_operator_history_status("History cleared.")
        self.refresh_operator_history_projection()

    def append_operator_history_reel(self, phrase: str, _semantic_key=None) -> None:
        allowed = {
            "Agent became active",
            "Agent became idle",
            "Agent completed",
            "Agent failed",
            "Request opened",
            "Request resolved",
            "Source degraded",
            "Source recovered",
            "Request acknowledged locally",
            "Request escalation resumed",
        }
        if type(phrase) is not str or phrase not in allowed:
            return
        self.operator_history_reel = (*self.operator_history_reel, phrase)[-50:]
        set_field_value(
            self.settings_fields.get("history_semantic_reel"),
            "\n".join(self.operator_history_reel),
        )

    def _enqueue_operator_history_events(
        self,
        events: tuple[RuntimeHistoryEvent, ...],
    ) -> None:
        if not events or self.operator_history_store.retention_days == 0:
            return
        aggregated = aggregate_operator_history(
            events,
            timezone_offset_at=lambda _epoch: self._operator_history_timezone_offset_minutes(),
        )

        def _persist() -> None:
            try:
                with self._operator_history_lock:
                    self.operator_history_store.add_rows(aggregated)
                    self.operator_history_store.flush(now=time.time())
            except Exception:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyOperatorHistoryPersistenceFailure:",
                    None,
                    False,
                )
                return
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyOperatorHistoryProjection:",
                None,
                False,
            )

        threading.Thread(
            target=_persist,
            daemon=True,
            name="sidepulse-operator-history-write",
        ).start()

    def observe_operator_history_triage(
        self,
        request,
        mutation: LocalTriageMutationKind,
        *,
        occurred_at: float,
        state: CanonicalOperatorState,
    ) -> None:
        if mutation is LocalTriageMutationKind.ACKNOWLEDGE:
            phrase = "Request acknowledged locally"
            kind = HistoryEventKind.REQUEST_ACKNOWLEDGED
        elif mutation is LocalTriageMutationKind.RESUME_ESCALATION:
            phrase = "Request escalation resumed"
            kind = HistoryEventKind.REQUEST_RESUMED
        else:
            return
        event_key = request.semantic_event_key
        if event_key.subject_key != request.key:
            return
        primary_count = sum(work.parent_key is None for work in state.works)
        worker_count = len(state.works) - primary_count
        self.append_operator_history_reel(phrase, request.key)
        self._enqueue_operator_history_events(
            (
                RuntimeHistoryEvent(
                    event_key,
                    ProviderIdentifier(request.key.work_key.source_key.provider_id),
                    kind,
                    occurred_at,
                    None,
                    None,
                    primary_count,
                    worker_count,
                ),
            )
        )

    def observe_operator_history_events(
        self,
        events: tuple[CanonicalOperatorEvent, ...],
        state: CanonicalOperatorState | None,
    ) -> None:
        phrases = {
            TransitionKind.BECAME_ACTIVE: "Agent became active",
            TransitionKind.BECAME_IDLE: "Agent became idle",
            TransitionKind.REQUEST_OPENED: "Request opened",
            TransitionKind.REQUEST_RESOLVED: "Request resolved",
            TransitionKind.COMPLETED: "Agent completed",
            TransitionKind.FAILED: "Agent failed",
            TransitionKind.SOURCE_DEGRADED: "Source degraded",
            TransitionKind.SOURCE_RECOVERED: "Source recovered",
        }
        history_kinds = {
            TransitionKind.BECAME_ACTIVE: HistoryEventKind.STARTED,
            TransitionKind.REQUEST_OPENED: HistoryEventKind.NEEDS_USER,
            TransitionKind.COMPLETED: HistoryEventKind.COMPLETED,
            TransitionKind.FAILED: HistoryEventKind.FAILED,
            TransitionKind.SOURCE_DEGRADED: HistoryEventKind.SOURCE_DEGRADED,
            TransitionKind.SOURCE_RECOVERED: HistoryEventKind.SOURCE_RECOVERED,
        }
        primary_count = 0
        worker_count = 0
        if type(state) is CanonicalOperatorState:
            primary_count = sum(work.parent_key is None for work in state.works)
            worker_count = len(state.works) - primary_count
        rows: list[RuntimeHistoryEvent] = []
        for event in events:
            phrase = phrases.get(event.kind)
            if phrase is not None:
                self.append_operator_history_reel(phrase, event.key)
            kind = history_kinds.get(event.kind)
            if kind is None:
                continue
            subject = event.subject_key
            source = subject.source_key if type(subject) is WorkKey else subject.work_key.source_key
            rows.append(
                RuntimeHistoryEvent(
                    event.key,
                    ProviderIdentifier(source.provider_id),
                    kind,
                    event.occurred_at_epoch,
                    None,
                    None,
                    primary_count,
                    worker_count,
                )
            )
        self._enqueue_operator_history_events(tuple(rows))

    @objc.IBAction
    def applyOperatorHistoryPersistenceFailure_(self, _payload) -> None:
        self._set_operator_history_status("History could not be saved.")

    @objc.IBAction
    def applyOperatorHistoryProjection_(self, _payload) -> None:
        self.refresh_operator_history_projection()

    @staticmethod
    def _save_mailbox_preferences(preferences) -> None:
        save_mailbox_preferences_v2(
            default_state_dir() / "mailbox-preferences.json",
            preferences,
        )

    @staticmethod
    def _save_operator_triage(state) -> None:
        save_operator_triage(default_state_dir() / "operator-triage.json", state)

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
        if self.settings_window is not None:
            self.settings_window.setTitle_(
                f"SidePulse Settings: {dict(SETTINGS_SIDEBAR_ITEMS)[selected_key]}"
            )
        self.reconcile_device_runtime()
        self.reconcile_installed_agent_inventory()
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
        self._reconcile_current_presentation_inputs()

    def stop_colors_preview_animation(self) -> None:
        self._runtime_preview_fire_at.pop(
            RuntimeFeature.SETTINGS_COLOR_PREVIEW,
            None,
        )
        self._runtime_timer_registry.invalidate(
            RuntimeFeature.SETTINGS_COLOR_PREVIEW
        )

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
        description = self.color_fields.get("blend_description")
        if description is not None:
            description.setStringValue_(BLEND_MODE_DESCRIPTIONS.get(payload["blend_mode"], ""))
            description.setToolTip_(colors_module.BLEND_MODE_TOOLTIPS.get(payload["blend_mode"], ""))
        # Route through the shared commit like every sibling control:
        # it re-derives the preset chip. Hand-rolling the save left the
        # chip reading a preset the settings no longer matched, so the
        # obvious next move -- re-clicking that preset to "confirm" --
        # silently reapplied the whole package over the mode just set.
        self._commit_colors_and_refresh(colors)

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
        # Same shared commit, same reason as setBlendMode_.
        self._commit_colors_and_refresh(colors)

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
    def toggleLinkScreenBarToHardware_(self, sender):
        self.settings = self.settings.with_link_screen_bar_to_hardware(
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
        self.refresh_setup_window()
        self.setup_window.makeKeyAndOrderFront_(None)
        self._reconcile_current_presentation_inputs()
        NSApp.activateIgnoringOtherApps_(True)

    @objc.IBAction
    def redrawSetupDemo_(self, _sender):
        if self.setup_window is None or not self.setup_window.isVisible():
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
        if current_pane == "history":
            self.refresh_operator_history_projection()
        if current_pane == "installed_agents":
            self.refresh_installed_agents_settings_projection()
        if current_pane == "capacity":
            self.refresh_capacity_settings_projection()
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
            self.settings_buttons.get("link_screen_bar_to_hardware"),
            self.settings.link_screen_bar_to_hardware,
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
        self.refresh_notification_authorization_controls()
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
            self.settings_buttons.get("completion_sweep_enabled"),
            self.settings.completion_sweep_enabled,
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
            self._settings_message_deadline_at = (
                self._presentation_monotonic() + 3.5 if message else 0.0
            )
            self._reconcile_current_presentation_inputs()

    @objc.IBAction
    def dismissSettingsMessage_(self, _timer):
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

    @objc.IBAction
    def exportOperatorHistory_(self, _sender) -> None:
        path = choose_operator_export_path("history")
        if path is None:
            return
        try:
            payload = encode_history_export(
                HistoryExportV1(
                    time.time(),
                    self.settings.operator_history_retention_days,
                    self.operator_history_store.state.rows,
                )
            )
            write_private_export(
                path,
                payload,
                max_bytes=MAX_HISTORY_EXPORT_BYTES,
            )
        except Exception:
            self._set_operator_history_status("Export could not be saved.")
            return
        self._set_operator_history_status("History export saved as a local file.")

    def _operator_debug_export(self) -> DebugExportV1:
        try:
            from importlib.metadata import version as package_version

            app_version = package_version("sidepulse")
        except Exception:
            app_version = "dev"
        health_counts: dict[str, int] = {}
        state = self.current_operator_state
        if type(state) is CanonicalOperatorState:
            freshness_to_health = {
                SourceFreshness.FRESH: "healthy",
                SourceFreshness.RESTORED: "healthy",
                SourceFreshness.PARTIAL: "partial",
                SourceFreshness.TIMING_UNCERTAIN: "partial",
                SourceFreshness.STALE: "partial",
                SourceFreshness.UNAVAILABLE: "unavailable",
            }
            by_source = {
                work.key.source_key: work.source_freshness for work in state.works
            }
            for freshness in by_source.values():
                label = freshness_to_health[freshness]
                health_counts[label] = health_counts.get(label, 0) + 1
        device_counts = (
            (("write_failed", len(self.device_errors)),)
            if self.device_errors
            else ()
        )
        history_health = (
            "disabled"
            if self.settings.operator_history_retention_days == 0
            else self.operator_history_restore_health.value
        )
        return DebugExportV1(
            time.time(),
            app_version,
            "unknown" if running_inside_bundle() else "source_checkout",
            tuple(sorted(health_counts.items())),
            (),
            device_counts,
            history_health,
        )

    @objc.IBAction
    def exportOperatorDiagnostics_(self, _sender) -> None:
        path = choose_operator_export_path("diagnostics")
        if path is None:
            return
        try:
            payload = encode_debug_export(self._operator_debug_export())
            write_private_export(
                path,
                payload,
                max_bytes=MAX_DEBUG_EXPORT_BYTES,
            )
        except Exception:
            self._set_operator_history_status("Export could not be saved.")
            return
        self._set_operator_history_status(
            "Diagnostics export saved as a local file."
        )

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
            self.virtual_status_device.set_pointer_interaction_relevant(False)
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
            self.virtual_status_device.set_pointer_interaction_relevant(
                not self.status_menu_open
            )
            self.virtual_status_device.show()
        else:
            self.virtual_status_device.set_pointer_interaction_relevant(False)
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
        if getattr(self, "_runtime_started", False):
            if cached is not None and cached[0] is discover:
                return cached[2]
            return list(getattr(self, "_device_inventory_candidates", ()))
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
                LED_DISPLAY_FAILURE,
                lambda: self.active_failure_signal(now=now) is not None,
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
                lambda: (
                    device.display == LED_DISPLAY_QUOTA_RUNWAY
                    and self.quota_runway_state() is not None
                ),
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
        *,
        projection: AttentionProjection | None = None,
        operator_events: tuple[CanonicalOperatorEvent, ...] = (),
        capacity: CapacityGlance | None = None,
        presentation_time: float | None = None,
        resolved_glance: ResolvedGlance | None = None,
    ) -> None:
        if not self.leds_enabled:
            return

        if resolved_glance is not None and type(resolved_glance) is not ResolvedGlance:
            raise ValueError("invalid resolved presentation")
        if projection is not None and resolved_glance is None:
            if presentation_time is None:
                presentation_time = self._presentation_monotonic()
            resolved_glance = self.resolve_presentation_glance(
                projection,
                operator_events=operator_events,
                capacity=capacity,
                presentation_time=presentation_time,
            )
            cues = (
                (resolved_glance.cue,)
                if resolved_glance.cue is not None
                else ()
            )
            self.set_status_emphasis_plan(resolved_glance, cues)

        devices = tuple(
            sorted(
                (
                    device
                    for device in self.status_bar_devices(remember=False)
                    if device.connected and device.device_id != VIRTUAL_DEVICE_ID
                ),
                key=lambda device: device.device_id,
            )[:MAX_RUNTIME_PHYSICAL_DEVICES]
        )
        self.sync_virtual_status_device(
            mode,
            battery_snapshot,
            statuses,
            projection=projection,
            resolved_glance=resolved_glance,
            presentation_time=presentation_time,
            capacity_remaining_fraction=(
                capacity.remaining_fraction if capacity is not None else None
            ),
        )
        if not devices:
            return

        if time.monotonic() < self.led_animation_until_monotonic:
            return
        if not self._hardware_write_active:
            return

        relay_elapsed_seconds = max(0.0, time.monotonic() - self._relay_epoch)
        now = self._runtime_worker_monotonic()
        for device in devices:
            request = HardwareWriteRequest(
                device=device,
                mode=mode,
                battery_snapshot=battery_snapshot,
                statuses=statuses,
                projection=projection,
                relay_elapsed_seconds=relay_elapsed_seconds,
                accessibility_preferences=self._accessibility_display_preferences,
                resolved_glance=resolved_glance,
                presentation_time=presentation_time,
                capacity_remaining_fraction=(
                    capacity.remaining_fraction if capacity is not None else None
                ),
            )
            self._hardware_write_worker.submit(
                RuntimeWorkCommand(
                    domain=RuntimeWorkerDomain.HARDWARE_WRITE,
                    key=self._hardware_worker_key(device),
                    generation=self._hardware_write_generation,
                    deadline=now + max(5.0, STATUS_BAR_REFRESH_SECONDS * 2.0),
                    payload=request,
                )
            )

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
        projection: AttentionProjection | None = None,
        relay_elapsed_seconds: float | None = None,
        resolved_glance: ResolvedGlance | None = None,
        presentation_time: float | None = None,
        capacity_remaining_fraction: float | None = None,
    ) -> None:
        if not SCREEN_BAR_FEATURE_ENABLED:
            self.virtual_status_device.set_pointer_interaction_relevant(False)
            self.virtual_status_device.hide()
            return
        if not self.settings.virtual_status_device_enabled:
            self.virtual_status_device.set_pointer_interaction_relevant(False)
            return
        if relay_elapsed_seconds is None:
            relay_elapsed_seconds = max(0.0, time.monotonic() - self._relay_epoch)
        if resolved_glance is not None and started_at is None:
            started_at = resolved_glance.relay_epoch
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
        # The right tip may retain the independent completion gauge. Capacity
        # stays empty while release authority is withheld.
        if self.settings.screen_bar_gauges_enabled:
            snapshot = getattr(self, "last_snapshot", None)
            right_on = (
                bool(unseen_completions(snapshot, self))
                if snapshot is not None
                else False
            )
            self.virtual_status_device.set_standing_gauges(0.0, right_on)
        else:
            self.virtual_status_device.set_standing_gauges(0.0, False)
        # Click-to-answer: while an ask is live, the glowing bar itself
        # is the fastest route to the asking session -- click it and the
        # oldest unanswered ask's terminal comes forward. With no ask,
        # the window stays fully click-through (mouse events ignored),
        # exactly as before.
        self.virtual_status_device.set_pointer_interaction_relevant(
            not self.status_menu_open
        )
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

        def _set_virtual(program: str, presentation=None) -> None:
            # The Screen Bar honors ITS calibration exactly like a
            # physical device -- gains applied at the write boundary
            # (the Colors-window previews stay uncorrected "true" hex).
            def transform(value: str) -> str:
                return apply_channel_gain_to_program(
                    apply_resting_glow_to_program(value, device.resting_glow),
                    device.channel_gains,
                )

            continuity = (
                continuous_presentation_identity(presentation)
                if presentation is not None
                else None
            )
            playback_anchor = started_at
            if (
                presentation is not None
                and valid_presentation_time(presentation.playback_anchor)
            ):
                playback_anchor = float(presentation.playback_anchor)
            self.virtual_status_device.set_program(
                transform(program),
                started_at=playback_anchor,
                motion=(
                    presentation.motion
                    if presentation is not None
                    else MotionClass.CONTINUOUS
                ),
                static_fallback_program=(
                    transform(
                        apply_brightness(
                            presentation.static_fallback_dsl,
                            brightness,
                        )
                    )
                    if presentation is not None
                    else "off"
                ),
                next_visual_change_at=(
                    presentation.next_visual_change_at
                    if presentation is not None
                    else None
                ),
                dedupe_token=continuity,
            )

        projection = projection or getattr(self, "current_attention_projection", None)
        if display == LED_DISPLAY_FAILURE:
            active = self.active_failure_signal()
            if active is not None:
                _set_virtual(
                    failure_signal_program(
                        self.settings.colors.mode_color("ask"),
                        active,
                        brightness=brightness,
                        led_count=8,
                    )
                )
        elif display == LED_DISPLAY_TEST:
            _set_virtual(self.test_signal_program(brightness))
        elif display == LED_DISPLAY_ESCALATION:
            _set_virtual(self.escalation_takeover_program(brightness))
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
            override = self.screen_bar_blend_override()
            if override:
                colors_for_render = colors_for_render.with_blend_mode(override)
            presentation = None
            if resolved_glance is not None:
                preferences = self._accessibility_display_preferences
                if type(preferences) is not AccessibilityDisplayPreferences:
                    preferences = AccessibilityDisplayPreferences(
                        reduce_motion=True,
                        reduce_transparency=True,
                        increase_contrast=True,
                        differentiate_without_color=True,
                    )
                presentation = compose_presentation_program(
                    resolved_glance,
                    presentation_time=presentation_time,
                    led_count=8,
                    color=color_for_resolved_glance(
                        colors_for_render,
                        resolved_glance,
                    ),
                    preferences=preferences,
                    capacity_remaining_fraction=capacity_remaining_fraction,
                )
                program = apply_brightness(presentation.dsl, brightness)
            elif projection is not None:
                _, program = program_for_projection(
                    projection,
                    active_signal=self.active_failure_signal(),
                    led_count=8,
                    colors=colors_for_render,
                    brightness=brightness,
                    relay_elapsed_seconds=relay_elapsed_seconds,
                )
            else:
                _, program = program_for_snapshot(
                    statuses,
                    led_count=8,
                    colors=colors_for_render,
                    brightness=brightness,
                    fallback_mode=mode,
                    relay_elapsed_seconds=relay_elapsed_seconds,
                )
            _set_virtual(program, presentation)

    def schedule_screen_bar_sync(
        self,
        mode: AgentMode,
        battery_snapshot: BatterySnapshot | None,
        statuses: tuple[AgentStatus, ...],
        started_at: float | None,
        projection: AttentionProjection | None = None,
        relay_elapsed_seconds: float = 0.0,
    ) -> None:
        """Dispatch a physical episode's Screen Bar render to the main thread.

        ``started_at`` is the physical write's completion time when a write
        occurred. A deduped physical program passes ``None`` so the virtual
        surface can update independently without inventing a new hardware
        phase anchor.
        """
        payload = {
            "mode": mode,
            "battery_snapshot": battery_snapshot,
            "statuses": statuses,
            "started_at": started_at,
            "projection": projection,
            "relay_elapsed_seconds": relay_elapsed_seconds,
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
            projection=payload.get("projection"),
            relay_elapsed_seconds=payload.get("relay_elapsed_seconds", 0.0),
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

    def _build_presentation_timer_registry(
        self,
        *,
        timer_factory=None,
        monotonic=None,
    ) -> AppKitTimerRegistry:
        options = {}
        if timer_factory is not None:
            options["timer_factory"] = timer_factory
        options["monotonic"] = (
            self._presentation_monotonic if monotonic is None else monotonic
        )
        return AppKitTimerRegistry(
            {
                RuntimeFeature.PRESENTATION_FRAME_FALLBACK: (
                    self._presentation_frame_fallback_fired
                ),
                RuntimeFeature.PRESENTATION_STATIC_DEADLINE: (
                    self._presentation_static_deadline_fired
                ),
                RuntimeFeature.ALCOVE_OBSERVATION: (
                    self._presentation_alcove_observation_fired
                ),
                RuntimeFeature.POINTER_PEEK: self._presentation_pointer_peek_fired,
                RuntimeFeature.LID_OBSERVATION: (
                    self._lid_observation_timer_fired
                ),
                RuntimeFeature.DEVICE_INVENTORY: (
                    self._device_inventory_timer_fired
                ),
                RuntimeFeature.DISPLAY_ENVIRONMENT: (
                    self._display_environment_timer_fired
                ),
                RuntimeFeature.CALENDAR_OBSERVATION: (
                    self._calendar_observation_timer_fired
                ),
                RuntimeFeature.REMINDERS_OBSERVATION: (
                    self._reminders_observation_timer_fired
                ),
                RuntimeFeature.WEATHER_OBSERVATION: (
                    self._weather_observation_timer_fired
                ),
                RuntimeFeature.SETTINGS_SIGNAL_PREVIEW: (
                    self._settings_signal_preview_fired
                ),
                RuntimeFeature.SETTINGS_COLOR_PREVIEW: (
                    self._settings_color_preview_fired
                ),
                RuntimeFeature.SETUP_DEMO: self._setup_demo_fired,
                RuntimeFeature.SETTINGS_MESSAGE_DEADLINE: (
                    self._settings_message_deadline_fired
                ),
                RuntimeFeature.TEST_SIGNAL_DEADLINE: (
                    self._finite_ui_deadline_fired
                ),
                RuntimeFeature.TIMEBOX_DEADLINE: self._timebox_deadline_fired,
                RuntimeFeature.ESCALATION_DEADLINE: (
                    self._escalation_deadline_fired
                ),
            },
            **options,
        )

    def _build_runtime_worker_registry(
        self,
        *,
        monotonic=None,
        dispatch_main=None,
    ) -> tuple[RuntimeWorkerRegistry, LatestWinsWorker]:
        clock = time.monotonic if monotonic is None else monotonic
        self._runtime_worker_monotonic = clock
        dispatcher = (
            self._dispatch_runtime_worker_result
            if dispatch_main is None
            else dispatch_main
        )
        registry = RuntimeWorkerRegistry(monotonic=clock)
        worker = LatestWinsWorker(
            RuntimeWorkerDomain.OS_POLL,
            executor=self._execute_os_poll_command,
            result_handler=self._apply_os_poll_result,
            dispatch_main=dispatcher,
            monotonic=clock,
        )
        registry.register(RuntimeWorkerDomain.OS_POLL, worker)
        hardware_worker = LatestWinsWorker(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            executor=self._execute_hardware_write_command,
            result_handler=self._apply_hardware_write_result,
            dispatch_main=dispatcher,
            monotonic=clock,
        )
        registry.register(
            RuntimeWorkerDomain.HARDWARE_WRITE,
            hardware_worker,
        )
        self._hardware_write_worker = hardware_worker
        weather_worker = LatestWinsWorker(
            RuntimeWorkerDomain.WEATHER_FETCH,
            executor=self._execute_weather_command,
            result_handler=self._apply_weather_result,
            dispatch_main=dispatcher,
            monotonic=clock,
        )
        self._weather_worker = weather_worker
        self._weather_worker_registered = False
        return registry, worker

    def _dispatch_runtime_worker_result(self, drain) -> None:
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "drainRuntimeWorker:",
            drain,
            False,
        )

    @objc.IBAction
    def drainRuntimeWorker_(self, drain) -> None:
        if callable(drain):
            drain()

    def _installed_agents_pane_visible(self) -> bool:
        window = getattr(self, "settings_window", None)
        return bool(
            window is not None
            and not getattr(self, "_settings_window_closing", False)
            and callable(getattr(window, "isVisible", None))
            and window.isVisible()
            and getattr(self, "current_settings_pane", None) == "installed_agents"
        )

    @objc.IBAction
    def refreshInstalledAgents_(self, _sender) -> None:
        if self.refresh_installed_agent_inventory():
            fields = getattr(self, "settings_fields", None) or {}
            status = fields.get("installed_agents_refresh_status")
            if status is not None:
                status.setStringValue_("Checking installed coding agents…")

    @objc.IBAction
    def refreshCapacitySources_(self, _sender) -> None:
        self.maybe_refresh_usage_summary(reason="manual")

    def refresh_installed_agents_settings_projection(self) -> None:
        fields = getattr(self, "settings_fields", None) or {}
        status_fields = fields.get("installed_agent_status_fields") or {}
        if not status_fields:
            return
        result = getattr(self, "_installed_agent_inventory_result", None)
        observations = (
            {row.key: row for row in result.reduction.observations}
            if type(result) is InstalledAgentInventoryResult
            else {}
        )
        for registration in installed_surface_registrations():
            label = status_fields.get((registration.provider_id, registration.surface_id))
            if label is None:
                continue
            observation = observations.get(registration.key)
            if observation is None or observation.presence is SurfacePresence.ABSENT:
                presence = "Not detected"
            elif observation.presence is SurfacePresence.CONFIGURED:
                presence = "Configured"
            elif observation.presence is SurfacePresence.INSTALLED:
                presence = "Installed"
            elif observation.presence is SurfacePresence.MIGRATION_REQUIRED:
                presence = "Migration required"
            else:
                presence = "Unsupported version"
            if registration.support in {
                SurfaceSupportLevel.FULL,
                SurfaceSupportLevel.LIFECYCLE,
            }:
                monitoring = (
                    "Monitoring"
                    if observation is not None
                    and observation.presence is SurfacePresence.CONFIGURED
                    else "Monitoring available"
                )
            elif registration.support is SurfaceSupportLevel.CAPACITY:
                monitoring = "Capacity available"
            elif registration.support is SurfaceSupportLevel.INVENTORY:
                monitoring = "Detection only"
            else:
                monitoring = "Unsupported"
            label.setStringValue_(f"{presence} · {monitoring}")
        status = fields.get("installed_agents_refresh_status")
        if status is not None:
            status.setStringValue_(
                "Inventory ready"
                if type(result) is InstalledAgentInventoryResult
                else "Open this pane to check installed coding agents."
            )

    def refresh_capacity_settings_projection(self) -> None:
        fields = getattr(self, "settings_fields", None) or {}
        live_fields = fields.get("capacity_live_fields") or {}
        codex = live_fields.get("codex")
        if codex is not None:
            codex.setStringValue_(
                getattr(self, "codex_summary_text", None) or "Not observed yet"
            )
        claude = live_fields.get("claude")
        if claude is not None:
            claude.setStringValue_(
                getattr(self, "claude_plan_text", None) or "Not observed yet"
            )

    def reconcile_installed_agent_inventory(self) -> None:
        """Refresh only while the future Installed Agents pane is visible."""
        if self._installed_agents_pane_visible():
            self.refresh_installed_agent_inventory()

    def refresh_installed_agent_inventory(self) -> bool:
        """Submit one latest-wins, generation-fenced host inventory request."""
        if not self._runtime_started:
            return False
        # LatestWinsWorker cancellation is domain-wide, not key-local.  Keep
        # this pane's fence at or above the shared OS-poll generation and let
        # the worker's exact key replace older pending inventory requests.
        # Cancelling the pane's private generation here can otherwise advance
        # the shared watermark past it, silently refusing every later scan.
        self._installed_agent_inventory_generation = max(
            self._installed_agent_inventory_generation + 1,
            self._os_poll_generation,
        )
        roots_factory = getattr(
            self,
            "_installed_agent_inventory_roots",
            default_inventory_roots,
        )
        try:
            roots = roots_factory()
        except Exception:
            return False
        if type(roots) is not tuple:
            return False
        command = RuntimeWorkCommand(
            RuntimeWorkerDomain.OS_POLL,
            "installed-agent-inventory",
            self._installed_agent_inventory_generation,
            self._runtime_worker_monotonic() + 5.0,
            roots,
        )
        try:
            disposition = self._os_poll_worker.submit(command)
        except Exception:
            return False
        return disposition is not SubmissionDisposition.REFUSED

    def _execute_os_poll_command(self, command: RuntimeWorkCommand) -> object:
        if command.domain is not RuntimeWorkerDomain.OS_POLL:
            raise ValueError("invalid OS poll command")
        if command.key == "installed-agent-inventory":
            return execute_inventory_command(command)
        if command.key == "lid-observation" and type(command.payload) is LidObservationRequest:
            try:
                return LidObservationResult(
                    ok=True,
                    closed=read_lid_closed(),
                    error=None,
                )
            except Exception:
                return LidObservationResult(
                    ok=False,
                    closed=None,
                    error="lid observation unavailable",
                )
        if command.key == "device-inventory" and type(command.payload) is DeviceInventoryRequest:
            try:
                candidates = tuple(discover_devices())[:MAX_RUNTIME_PHYSICAL_DEVICES]
            except Exception:
                return DeviceInventoryResult((), "device inventory unavailable")
            return DeviceInventoryResult(candidates)
        if (
            command.key == "display-environment"
            and type(command.payload) is DisplayEnvironmentRequest
        ):
            request = command.payload
            brightness = None
            if request.read_brightness:
                try:
                    brightness = display_brightness.auto_led_brightness()
                except display_brightness.DisplayBrightnessUnavailableError:
                    pass
            active_focus_ids = None
            if request.read_focus:
                try:
                    active_focus_ids = tuple(
                        focus_sync.active_focus_mode_identifiers()
                    )
                except focus_sync.FocusSyncUnavailableError:
                    active_focus_ids = ()
            preferences = None
            if request.read_accessibility:
                preferences = refresh_accessibility_display_preferences(
                    request.previous_accessibility_preferences
                )
            return DisplayEnvironmentResult(
                brightness=brightness,
                active_focus_ids=active_focus_ids,
                accessibility_preferences=preferences,
            )
        if (
            command.key == "calendar-observation"
            and type(command.payload) is CalendarObservationRequest
        ):
            try:
                upcoming = calendar_watch.next_event_start(
                    command.payload.lead_minutes
                )
                if upcoming is None:
                    return CalendarObservationResult(
                        available=True,
                        starts_in_seconds=None,
                    )
                _private_title, starts_at = upcoming
                starts_in_seconds = max(
                    0.0,
                    (starts_at - datetime.now(timezone.utc)).total_seconds(),
                )
                return CalendarObservationResult(
                    available=True,
                    starts_in_seconds=starts_in_seconds,
                )
            except Exception:
                return CalendarObservationResult(
                    available=False,
                    starts_in_seconds=None,
                )
        if (
            command.key == "reminders-observation"
            and type(command.payload) is RemindersObservationRequest
        ):
            completed = threading.Event()
            observed: list[tuple[object, object]] = []

            def _capture(items) -> None:
                for item in items or ():
                    if len(observed) >= MAX_REMINDER_OBSERVATION_IDENTIFIERS:
                        break
                    try:
                        identifier, title = tuple(item)
                    except (TypeError, ValueError):
                        continue
                    observed.append((identifier, title))
                completed.set()

            try:
                reminders_watch.fetch_due(
                    command.payload.lookback_seconds,
                    _capture,
                )
                remaining = max(
                    0.0,
                    command.deadline - self._runtime_worker_monotonic(),
                )
                if not completed.wait(
                    min(REMINDERS_FETCH_TIMEOUT_SECONDS, remaining)
                ):
                    return RemindersObservationResult(False, ())
            except Exception:
                return RemindersObservationResult(False, ())
            identifiers = tuple(
                dict.fromkeys(
                    hashlib.sha256(str(identifier).encode("utf-8")).hexdigest()
                    for identifier, _title in observed
                    if str(identifier)
                )
            )
            return RemindersObservationResult(True, identifiers)
        raise ValueError("invalid OS poll command")

    def _execute_weather_command(
        self,
        command: RuntimeWorkCommand,
    ) -> WeatherObservationResult:
        if (
            command.domain is not RuntimeWorkerDomain.WEATHER_FETCH
            or command.key != WEATHER_WORKER_KEY
            or type(command.payload) is not WeatherObservationRequest
        ):
            raise ValueError("invalid weather command")
        request = command.payload
        try:
            if request.latitude is None:
                latitude, longitude = weather_watch.ip_location()
                location = WeatherObservationRequest(latitude, longitude)
            else:
                location = request
            alerts = weather_watch.active_alerts(
                location.latitude,
                location.longitude,
            )
            first = next(iter(alerts), None)
            if first is None:
                return WeatherObservationResult(True, False, None)
            _private_identifier, severity, event = first
            return WeatherObservationResult(
                True,
                True,
                _weather_classification(severity, event),
            )
        except Exception:
            return WeatherObservationResult(False, False, None)

    def _apply_weather_result(
        self,
        command: RuntimeWorkCommand,
        result: object,
    ) -> None:
        inputs = self._presentation_scheduler_inputs
        if (
            command.domain is RuntimeWorkerDomain.WEATHER_FETCH
            and command.key == WEATHER_WORKER_KEY
            and type(command.payload) is WeatherObservationRequest
            and type(result) is WeatherObservationResult
            and command.generation == self._weather_observation_generation
            and command.deadline > self._runtime_worker_monotonic()
            and self._weather_observation_active
            and self._runtime_started
            and self.settings.weather_alerts_enabled
            and inputs is not None
            and not inputs.display_asleep
            and not inputs.app_terminating
        ):
            self._apply_weather_observation_result(result)

    def _apply_weather_observation_result(
        self,
        result: WeatherObservationResult,
    ) -> None:
        was_active = self.weather_alert_active
        if not result.available:
            self.weather_watch_retry_at = (
                self._runtime_worker_monotonic() + WEATHER_WATCH_RETRY_SECONDS
            )
            self._weather_observation_fire_at = self.weather_watch_retry_at
            self.weather_alert_active = False
            self.weather_alert_event = None
            self._reconcile_current_presentation_inputs()
            if was_active:
                self.refresh_(None)
            return
        self.weather_watch_retry_at = 0.0
        self.weather_alert_active = result.active
        self.weather_alert_event = result.classification
        if self.weather_alert_active != was_active:
            if self.weather_alert_active and self.webhook_event_enabled("weather"):
                self.post_webhook(
                    {
                        "event": "sidepulse.weather",
                        "headline": self.weather_alert_event or "",
                    }
                )
            self.refresh_(None)

    def _apply_os_poll_result(
        self,
        command: RuntimeWorkCommand,
        result: object,
    ) -> None:
        if command.domain is not RuntimeWorkerDomain.OS_POLL:
            return
        if (
            command.key == "installed-agent-inventory"
            and type(command.payload) is tuple
            and type(result) is InstalledAgentInventoryResult
            and command.generation == self._installed_agent_inventory_generation
            and command.deadline > self._runtime_worker_monotonic()
            and self._runtime_started
        ):
            self._installed_agent_inventory_result = result
            self.refresh_installed_agents_settings_projection()
            return
        if (
            command.key == "lid-observation"
            and type(command.payload) is LidObservationRequest
            and type(result) is LidObservationResult
            and command.generation == self._os_poll_generation
            and self._lid_observation_active
        ):
            self._apply_lid_observation(result)
            return
        if (
            command.key == "device-inventory"
            and type(command.payload) is DeviceInventoryRequest
            and type(result) is DeviceInventoryResult
            and command.generation == self._os_poll_generation
            and self._device_inventory_active
        ):
            self._apply_device_inventory_result(result)
            return
        if (
            command.key == "display-environment"
            and type(command.payload) is DisplayEnvironmentRequest
            and type(result) is DisplayEnvironmentResult
            and command.generation == self._os_poll_generation
            and self._display_environment_active
        ):
            self._apply_display_environment_result(result)
            return
        inputs = self._presentation_scheduler_inputs
        if (
            command.key == "calendar-observation"
            and type(command.payload) is CalendarObservationRequest
            and type(result) is CalendarObservationResult
            and command.generation == self._os_poll_generation
            and command.deadline > self._runtime_worker_monotonic()
            and self._calendar_observation_active
            and self._runtime_started
            and self.settings.calendar_alerts_enabled
            and inputs is not None
            and not inputs.display_asleep
            and not inputs.app_terminating
        ):
            self._apply_calendar_observation_result(result)
            return
        if (
            command.key == "reminders-observation"
            and type(command.payload) is RemindersObservationRequest
            and type(result) is RemindersObservationResult
            and command.generation == self._os_poll_generation
            and command.deadline > self._runtime_worker_monotonic()
            and self._reminders_observation_active
            and self._runtime_started
            and self.settings.reminder_alerts_enabled
            and inputs is not None
            and not inputs.display_asleep
            and not inputs.app_terminating
        ):
            self._apply_reminders_observation_result(result)

    def _apply_reminders_observation_result(
        self,
        result: RemindersObservationResult,
    ) -> None:
        now = self._runtime_worker_monotonic()
        if not result.available:
            was_active = now < self.reminders_glow_until
            self.reminders_watch_retry_at = now + REMINDERS_WATCH_RETRY_SECONDS
            self.reminders_glow_until = 0.0
            self._reconcile_current_presentation_inputs()
            if was_active:
                self.refresh_(None)
            return
        horizon = now - REMINDERS_WATCH_SECONDS * 8.0
        retained = {
            identifier: seen_at
            for identifier, seen_at in self.reminders_seen.items()
            if seen_at >= horizon
        }
        fresh = tuple(
            identifier
            for identifier in result.identifiers
            if identifier not in retained
        )
        if not fresh:
            self.reminders_seen = retained
            return
        for identifier in fresh:
            retained[identifier] = now
        self.reminders_seen = dict(
            sorted(retained.items(), key=lambda item: (item[1], item[0]))[
                -MAX_REMINDER_OBSERVATION_IDENTIFIERS:
            ]
        )
        hold = signals_module.signal_hold_seconds(
            self.settings.signal_style(signals_module.SIGNAL_REMINDERS)
        )
        self.reminders_glow_until = now + hold
        self._reconcile_current_presentation_inputs()
        self.refresh_(None)

    def _reconcile_current_presentation_inputs(self) -> None:
        inputs = self._presentation_scheduler_inputs
        if inputs is not None:
            self._presentation_scheduler_inputs = None
            self.reconcile_presentation_timers(inputs)

    def _mark_reminders_permission_failed(self) -> None:
        was_active = self._runtime_worker_monotonic() < self.reminders_glow_until
        self._reminders_permission_failed = True
        self._reconcile_current_presentation_inputs()
        if was_active:
            self.refresh_(None)

    def _apply_calendar_observation_result(
        self,
        result: CalendarObservationResult,
    ) -> None:
        now = self._runtime_worker_monotonic()
        was_active = now < self.calendar_glow_until
        self.calendar_event_title = None
        if not result.available:
            self.calendar_watch_retry_at = now + CALENDAR_WATCH_RETRY_SECONDS
            self.calendar_glow_until = 0.0
            if was_active:
                self.refresh_(None)
            return
        if result.starts_in_seconds is None:
            self.calendar_glow_until = 0.0
            if was_active:
                self.refresh_(None)
            return
        self.calendar_glow_until = now + result.starts_in_seconds
        if not was_active:
            self.refresh_(None)

    def _apply_display_environment_result(
        self,
        result: DisplayEnvironmentResult,
    ) -> None:
        needs_refresh = False
        if result.brightness is not None:
            previous_brightness = self.last_watched_brightness
            self.last_watched_brightness = result.brightness
            if (
                previous_brightness is not None
                and abs(result.brightness - previous_brightness)
                >= BRIGHTNESS_WATCH_MIN_DELTA
            ):
                needs_refresh = True

        if result.active_focus_ids is not None:
            active_focus_ids = set(result.active_focus_ids)
            previous_focus_ids = set(self.last_active_focus_ids)
            self.last_active_focus_ids = active_focus_ids
            self._focus_ids_cache = (
                self._runtime_worker_monotonic(),
                list(result.active_focus_ids),
            )
            focus_scale = (
                min(
                    self.settings.focus_dim_fraction(identifier)
                    for identifier in active_focus_ids
                )
                if self.settings.focus_sync_enabled and active_focus_ids
                else 1.0
            )
            if (
                self.last_watched_focus_scale is not None
                and focus_scale != self.last_watched_focus_scale
            ):
                needs_refresh = True
            self.last_watched_focus_scale = focus_scale
            for focus_id in sorted(active_focus_ids - previous_focus_ids):
                slot = self.settings.focus_profile_rules.get(focus_id)
                if slot:
                    self.settings = self.settings.with_applied_calibration_profile(slot)
                    save_settings(self.settings)
                    self.set_settings_message(
                        f"Focus started — applied the {slot} profile."
                    )
                    needs_refresh = True
                    break

        if (
            result.accessibility_preferences is not None
            and result.accessibility_preferences
            != self._accessibility_display_preferences
        ):
            needs_refresh = self._apply_accessibility_display_preferences(
                result.accessibility_preferences
            ) or needs_refresh

        if needs_refresh:
            self.refresh_(None)

    def _apply_device_inventory_result(self, result: DeviceInventoryResult) -> None:
        if result.error is not None:
            log_status_bar(result.error)
            return
        same_candidates = result.candidates == self._device_inventory_candidates
        self._device_inventory_candidates = result.candidates
        self._device_discovery_cache = (
            discover_devices,
            time.monotonic(),
            list(result.candidates),
        )
        devices = tuple(
            sorted(
                (
                    device
                    for device in self.status_bar_devices(remember=False)
                    if device.connected and device.device_id != VIRTUAL_DEVICE_ID
                ),
                key=lambda device: device.device_id,
            )[:MAX_RUNTIME_PHYSICAL_DEVICES]
        )
        signature = device_connection_signature(list(devices))
        if same_candidates and signature == self.last_connected_device_signature:
            return
        previous_generation = self._hardware_write_generation
        self._hardware_write_generation += 1
        self._hardware_device_keys = frozenset(
            self._hardware_worker_key(device) for device in devices
        )
        self._hardware_write_worker.cancel_generation(previous_generation)
        if not self.observe_connected_devices():
            return
        self.rebuild_devices_pane()
        if self.last_snapshot is not None:
            self.refresh_(None)

    @staticmethod
    def _hardware_worker_key(device: StatusBarDevice) -> str:
        if (
            type(device) is not StatusBarDevice
            or not device.connected
            or device.device_id == VIRTUAL_DEVICE_ID
            or type(device.device_id) is not str
            or not device.device_id
            or len(device.device_id.encode("utf-8")) > 1024
            or "\x00" in device.device_id
            or device.target.name.upper() != DEFAULT_FILE_NAME
        ):
            raise ValueError("invalid hardware device identity")
        digest = hashlib.sha256(device.device_id.encode("utf-8")).hexdigest()[:32]
        return f"device-{digest}"

    def _execute_hardware_write_command(
        self,
        command: RuntimeWorkCommand,
    ) -> HardwareWriteResult:
        if (
            command.domain is not RuntimeWorkerDomain.HARDWARE_WRITE
            or type(command.payload) is not HardwareWriteRequest
            or command.key != self._hardware_worker_key(command.payload.device)
        ):
            raise ValueError("invalid hardware write command")
        if (
            not self._hardware_write_active
            or command.generation != self._hardware_write_generation
        ):
            raise ValueError("stale hardware write command")
        return self._sync_hardware_device(command.payload)

    def _sync_hardware_device(
        self,
        request: HardwareWriteRequest,
    ) -> HardwareWriteResult:
        device = request.device
        device_display_kind = self.active_led_display_kind_for_device(
            device,
            request.battery_snapshot,
        )
        if self.last_led_display_kind_by_device.get(device.device_id) != device_display_kind:
            self.reset_led_controllers_for_device(device.device_id)
            self.last_led_display_kind_by_device[device.device_id] = device_display_kind

        device_led_count = led_count_for_target(device.target)
        entry = self.signal_display_entries().get(device_display_kind)
        label = ""
        agent_display_rendered = False
        if entry is not None:
            factory, led_state, label_factory = entry
            controller = self.agent_controller_for_device(device)
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
            label = label_factory(device, request.battery_snapshot)
            if program is not None:
                write = controller.sync_program(program, led_state)
            else:
                write = LedStatusWrite(
                    state=led_state,
                    target=controller.last_target,
                    program="",
                    changed=False,
                )
        elif (
            device_display_kind == LED_DISPLAY_BATTERY
            and request.battery_snapshot is not None
        ):
            battery_write = self.battery_controller_for_device(device).sync_snapshot(
                request.battery_snapshot
            )
            write = LedStatusWrite(
                state=LedDisplayState.WORKING,
                target=battery_write.target,
                program=battery_write.program,
                changed=battery_write.changed,
                error=battery_write.error,
            )
            label = (
                f"{device.name} Battery {request.battery_snapshot.percent}% "
                f"{format_watts(request.battery_snapshot.adapter_power)}"
            )
        else:
            agent_display_rendered = True
            colors_for_render = self.agent_render_colors()
            override = self.settings.device_blend_mode(device.device_id)
            if override:
                colors_for_render = colors_for_render.with_blend_mode(override)
            statuses_for_device = request.statuses
            fallback_for_device = request.mode
            pin = self.settings.device_provider_pin(device.device_id)
            if pin:
                statuses_for_device = tuple(
                    status
                    for status in request.statuses
                    if status.provider == pin
                )
                if not statuses_for_device:
                    fallback_for_device = AgentMode.IDLE_READY
            controller = self.agent_controller_for_device(device)
            device_projection = (
                self.projection_for_device(request.projection, device)
                if request.projection is not None
                else None
            )
            if self.should_render_multi_agent(
                request.resolved_glance, device_projection
            ):
                write = controller.sync_projection(
                    device_projection,
                    colors_for_render,
                    active_signal=self.active_failure_signal(),
                    relay_elapsed_seconds=request.relay_elapsed_seconds,
                )
            elif request.resolved_glance is not None:
                preferences = request.accessibility_preferences
                if type(preferences) is not AccessibilityDisplayPreferences:
                    preferences = AccessibilityDisplayPreferences(
                        reduce_motion=True,
                        reduce_transparency=True,
                        increase_contrast=True,
                        differentiate_without_color=True,
                    )
                presentation = compose_presentation_program(
                    request.resolved_glance,
                    presentation_time=request.presentation_time,
                    led_count=device_led_count,
                    color=color_for_resolved_glance(
                        colors_for_render,
                        request.resolved_glance,
                    ),
                    preferences=preferences,
                    capacity_remaining_fraction=request.capacity_remaining_fraction,
                )
                continuity = continuous_presentation_identity(presentation)
                if continuity is not None:
                    continuity = (
                        *continuity,
                        controller.brightness,
                        controller.channel_gains,
                        getattr(controller, "resting_glow", 0.0),
                    )
                write = controller.sync_program(
                    apply_brightness(presentation.dsl, controller.brightness),
                    display_state_for_resolved_glance(request.resolved_glance),
                    dedupe_token=continuity,
                )
            elif device_projection is not None:
                write = controller.sync_projection(
                    device_projection,
                    colors_for_render,
                    active_signal=self.active_failure_signal(),
                    relay_elapsed_seconds=request.relay_elapsed_seconds,
                )
            else:
                write = controller.sync_snapshot(
                    statuses_for_device,
                    colors_for_render,
                    fallback_mode=fallback_for_device,
                    relay_elapsed_seconds=request.relay_elapsed_seconds,
                )
            label = f"{device.name} {write.label}"
        return HardwareWriteResult(
            request=request,
            write=write,
            label=label,
            agent_display_rendered=agent_display_rendered,
            completed_at=self._runtime_worker_monotonic(),
        )

    def _apply_hardware_write_result(
        self,
        command: RuntimeWorkCommand,
        result: object,
    ) -> None:
        if (
            command.domain is not RuntimeWorkerDomain.HARDWARE_WRITE
            or type(command.payload) is not HardwareWriteRequest
            or type(result) is not HardwareWriteResult
            or result.request != command.payload
            or command.generation != self._hardware_write_generation
            or not self._hardware_write_active
            or command.key != self._hardware_worker_key(result.request.device)
        ):
            return
        device = result.request.device
        write = result.write
        if write.error is not None:
            previous_error = self.device_errors.get(device.device_id)
            self.device_errors[device.device_id] = write.error
            if write.error != previous_error:
                log_status_bar(f"led error {device.name}: {write.error}")
        else:
            self.device_errors.pop(device.device_id, None)
            if write.changed:
                target = write.target if write.target is not None else "-"
                log_status_bar(f"leds={result.label} target={target}")
        self.last_led_error = next(iter(self.device_errors.values()), None)

        presentation_sync = hardware_presentation_sync_for_result(result)
        if (
            presentation_sync is not None
            and presentation_sync.request.resolved_glance is None
        ):
            request = presentation_sync.request
            self.schedule_screen_bar_sync(
                request.mode,
                request.battery_snapshot,
                request.statuses,
                presentation_sync.started_at,
                request.projection,
                request.relay_elapsed_seconds,
            )

    def _lid_observation_relevant(self) -> bool:
        if self.settings.closed_lid_awake_policy != CLOSED_LID_AWAKE_NEVER:
            return True
        if bool(getattr(self.keep_awake, "holding_requested", False)):
            return True
        if not self.leds_enabled:
            return False
        return any(
            bool(self.settings.lid_animation(kind).program.strip())
            for kind in (
                LID_ANIMATION_CLOSED,
                LID_ANIMATION_OPEN,
                LID_ANIMATION_CLOSED_ACTIVE,
                LID_ANIMATION_OPEN_ACTIVE,
            )
        )

    def _lid_observation_should_run(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> bool:
        return bool(
            self._runtime_started
            and not inputs.display_asleep
            and not inputs.app_terminating
            and self._lid_observation_relevant()
        )

    def _devices_pane_requests_inventory(self) -> bool:
        window = getattr(self, "settings_window", None)
        return bool(
            window is not None
            and not getattr(self, "_settings_window_closing", False)
            and window.isVisible()
            and getattr(self, "current_settings_pane", None) == "devices"
        )

    def _device_inventory_should_run(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> bool:
        return bool(
            self._runtime_started
            and not inputs.display_asleep
            and not inputs.app_terminating
            and (self.leds_enabled or self._devices_pane_requests_inventory())
        )

    def _hardware_write_should_run(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> bool:
        return bool(
            self._runtime_started
            and self.leds_enabled
            and not inputs.display_asleep
            and not inputs.app_terminating
        )

    def _display_environment_should_run(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> bool:
        setting_relevant = bool(
            any(device.auto_brightness_enabled for device in self.settings.devices)
            or self.settings.focus_sync_enabled
            or self.settings.focus_profile_rules
        )
        presentation_relevant = inputs.screen_bar_enabled and inputs.visible
        return bool(
            self._runtime_started
            and (setting_relevant or presentation_relevant)
            and not inputs.display_asleep
            and not inputs.app_terminating
        )

    def _calendar_observation_should_run(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> bool:
        return bool(
            self._runtime_started
            and self.settings.calendar_alerts_enabled
            and not inputs.display_asleep
            and not inputs.app_terminating
        )

    def _reminders_observation_should_run(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> bool:
        return bool(
            self._runtime_started
            and self.settings.reminder_alerts_enabled
            and not self._reminders_permission_failed
            and not inputs.display_asleep
            and not inputs.app_terminating
        )

    def _weather_observation_should_run(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> bool:
        return bool(
            self._runtime_started
            and self.settings.weather_alerts_enabled
            and not inputs.display_asleep
            and not inputs.app_terminating
        )

    def _preview_should_run(
        self,
        feature: RuntimeFeature,
        inputs: PresentationSchedulerInputs,
    ) -> bool:
        preferences = self._accessibility_display_preferences
        if (
            not self._runtime_started
            or inputs.display_asleep
            or inputs.app_terminating
            or bool(preferences is not None and preferences.reduce_motion)
        ):
            return False
        if feature is RuntimeFeature.SETUP_DEMO:
            window = self.setup_window
            return bool(window is not None and window.isVisible())
        if feature not in {
            RuntimeFeature.SETTINGS_SIGNAL_PREVIEW,
            RuntimeFeature.SETTINGS_COLOR_PREVIEW,
        }:
            raise ValueError("invalid preview runtime feature")
        window = self.settings_window
        if (
            window is None
            or self._settings_window_closing
            or not window.isVisible()
        ):
            return False
        pane = self.current_settings_pane
        if feature is RuntimeFeature.SETTINGS_SIGNAL_PREVIEW:
            return pane == "led_behavior"
        return pane in {"color_studio", "animations"}

    def _set_preview_active(
        self,
        feature: RuntimeFeature,
        active: bool,
        *,
        now: float,
        interval: float,
    ) -> None:
        was_active = feature in self._runtime_preview_fire_at
        if active:
            if not was_active:
                self._runtime_preview_fire_at[feature] = now + interval
            return
        self._runtime_preview_fire_at.pop(feature, None)
        if was_active and feature in {
            RuntimeFeature.SETTINGS_SIGNAL_PREVIEW,
            RuntimeFeature.SETTINGS_COLOR_PREVIEW,
        }:
            self.release_preview_engines()

    def _timebox_deadline(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> float | None:
        deadline = self.timebox_ends_at
        if (
            not self._runtime_started
            or inputs.display_asleep
            or inputs.app_terminating
            or not isinstance(deadline, (int, float))
            or isinstance(deadline, bool)
            or not math.isfinite(float(deadline))
            or float(deadline) <= 0.0
        ):
            return None
        return float(deadline)

    def _escalation_deadline(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> float | None:
        blocked_since = self.ask_blocked_since
        if (
            not self._runtime_started
            or inputs.display_asleep
            or inputs.app_terminating
            or not isinstance(blocked_since, (int, float))
            or isinstance(blocked_since, bool)
            or not math.isfinite(float(blocked_since))
        ):
            return None
        thresholds = [self.settings.escalation_ramp_seconds]
        if self.settings.escalation_tier != signals_module.ESCALATION_TIER_LIGHT:
            thresholds.append(self.settings.escalation_menu_bar_seconds)
        if self.settings.escalation_tier in (
            signals_module.ESCALATION_TIER_CHIME,
            signals_module.ESCALATION_TIER_TAKEOVER,
        ):
            thresholds.append(self.settings.escalation_final_seconds)
        now = self._presentation_monotonic()
        deadlines = (
            float(blocked_since) + float(threshold) for threshold in thresholds
        )
        return next((deadline for deadline in deadlines if deadline > now), None)

    def _settings_message_deadline(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> float | None:
        deadline = self._settings_message_deadline_at
        now = self._presentation_monotonic()
        if (
            isinstance(deadline, (int, float))
            and not isinstance(deadline, bool)
            and math.isfinite(float(deadline))
            and 0.0 < float(deadline) <= now
        ):
            self._settings_message_deadline_at = 0.0
            label = self.settings_fields.get("message")
            if label is not None:
                label.setAlphaValue_(0.0)
            return None
        if (
            not self._runtime_started
            or inputs.display_asleep
            or inputs.app_terminating
            or not isinstance(deadline, (int, float))
            or isinstance(deadline, bool)
            or not math.isfinite(float(deadline))
            or float(deadline) <= now
        ):
            return None
        window = self.settings_window
        if (
            window is None
            or self._settings_window_closing
            or not window.isVisible()
            or self.settings_fields.get("message") is None
        ):
            return None
        return float(deadline)

    def _clear_tip_highlight(self) -> None:
        view = self._tip_highlight_view
        self._tip_highlight_view = None
        self._tip_highlight_until = 0.0
        if view is None:
            return
        try:
            layer = view.layer()
            if layer is not None:
                layer.setBackgroundColor_(None)
        except Exception:
            pass

    def _retire_elapsed_ui_deadlines(
        self,
        inputs: PresentationSchedulerInputs,
        *,
        now: float,
    ) -> bool:
        changed = False
        withdraw_all = inputs.display_asleep or inputs.app_terminating
        for field_name in (
            "test_signal_until",
            "completion_sweep_until",
            "all_clear_until",
            "quota_blink_until",
            "battery_preview_until",
            "reminders_glow_until",
        ):
            deadline = getattr(self, field_name, 0.0)
            if isinstance(deadline, (int, float)) and not isinstance(deadline, bool):
                if float(deadline) > 0.0 and (withdraw_all or float(deadline) <= now):
                    setattr(self, field_name, 0.0)
                    changed = True
        if self.test_signal_until <= 0.0 and self.test_signal_key is not None:
            self.test_signal_key = None
            changed = True
        tip_until = self._tip_highlight_until
        if tip_until > 0.0 and (withdraw_all or tip_until <= now):
            self._clear_tip_highlight()
            changed = True
        return changed

    def _finite_ui_deadline(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> float | None:
        now = self._presentation_monotonic()
        self._retire_elapsed_ui_deadlines(inputs, now=now)
        glance = self._current_resolved_glance
        if (
            type(glance) is ResolvedGlance
            and (inputs.display_asleep or inputs.app_terminating)
        ):
            finite_cues = self.status_cue_coordinator.observe(
                self._status_cue_candidates,
                now=now,
                play_motion=False,
            )
            self._status_finite_cues = finite_cues
            self._status_cue_deadline = finite_cues.next_deadline
            self._apply_status_accessibility_text(glance, finite_cues)
        if not self._runtime_started or inputs.display_asleep or inputs.app_terminating:
            return None
        deadlines = [
            self.test_signal_until,
            self.completion_sweep_until,
            self.all_clear_until,
            self.quota_blink_until,
            self.battery_preview_until,
            self._tip_highlight_until,
            self._status_cue_deadline,
        ]
        if self.settings.reminder_alerts_enabled and not self._reminders_permission_failed:
            deadlines.append(self.reminders_glow_until)
        valid = (
            float(deadline)
            for deadline in deadlines
            if isinstance(deadline, (int, float))
            and not isinstance(deadline, bool)
            and math.isfinite(float(deadline))
            and float(deadline) > now
        )
        return min(valid, default=None)

    def _set_lid_observation_active(
        self,
        active: bool,
        *,
        now: float | None = None,
    ) -> None:
        desired = bool(active)
        if desired == self._lid_observation_active:
            return
        previous_generation = self._os_poll_generation
        self._lid_observation_active = desired
        self._lid_observation_fire_at = (
            (self._presentation_monotonic() if now is None else float(now))
            + LID_POLL_SECONDS
            if desired
            else None
        )
        self._os_poll_generation += 1
        self._os_poll_worker.cancel_generation(previous_generation)

    def _set_device_inventory_active(
        self,
        active: bool,
        *,
        now: float | None = None,
    ) -> None:
        desired = bool(active)
        if desired == self._device_inventory_active:
            return
        previous_generation = self._os_poll_generation
        self._device_inventory_active = desired
        self._device_inventory_fire_at = (
            (self._presentation_monotonic() if now is None else float(now))
            + STATUS_BAR_DEVICE_POLL_SECONDS
            if desired
            else None
        )
        self._os_poll_generation += 1
        self._os_poll_worker.cancel_generation(previous_generation)

    def _set_display_environment_active(
        self,
        active: bool,
        *,
        now: float | None = None,
    ) -> None:
        desired = bool(active)
        if desired == self._display_environment_active:
            return
        previous_generation = self._os_poll_generation
        self._display_environment_active = desired
        self._display_environment_fire_at = (
            (self._presentation_monotonic() if now is None else float(now))
            + BRIGHTNESS_WATCH_SECONDS
            if desired
            else None
        )
        self._os_poll_generation += 1
        self._os_poll_worker.cancel_generation(previous_generation)

    def _set_calendar_observation_active(
        self,
        active: bool,
        *,
        now: float | None = None,
    ) -> None:
        desired = bool(active)
        if desired == self._calendar_observation_active:
            return
        previous_generation = self._os_poll_generation
        self._calendar_observation_active = desired
        self._calendar_observation_fire_at = (
            (self._presentation_monotonic() if now is None else float(now))
            + CALENDAR_WATCH_SECONDS
            if desired
            else None
        )
        self._os_poll_generation += 1
        self._os_poll_worker.cancel_generation(previous_generation)

    def _set_reminders_observation_active(
        self,
        active: bool,
        *,
        now: float | None = None,
    ) -> None:
        desired = bool(active)
        if desired == self._reminders_observation_active:
            return
        previous_generation = self._os_poll_generation
        self._reminders_observation_active = desired
        if not desired:
            self.reminders_glow_until = 0.0
            self._scheduled_reminders_cue_deadline = None
        self._reminders_observation_fire_at = (
            (self._presentation_monotonic() if now is None else float(now))
            + REMINDERS_WATCH_SECONDS
            if desired
            else None
        )
        self._os_poll_generation += 1
        self._os_poll_worker.cancel_generation(previous_generation)

    def _set_weather_observation_active(
        self,
        active: bool,
        *,
        now: float | None = None,
    ) -> None:
        desired = bool(active)
        if desired == self._weather_observation_active:
            return
        was_alert_active = self.weather_alert_active
        if desired and not self._weather_worker_registered:
            self._runtime_worker_registry.register(
                RuntimeWorkerDomain.WEATHER_FETCH,
                self._weather_worker,
            )
            self._weather_worker_registered = True
        self._weather_observation_active = desired
        self._weather_observation_fire_at = (
            (self._presentation_monotonic() if now is None else float(now))
            + WEATHER_WATCH_SECONDS
            if desired
            else None
        )
        self._advance_weather_observation_generation()
        if not desired:
            self.weather_alert_active = False
            self.weather_alert_event = None
            if was_alert_active:
                self.refresh_(None)

    def _advance_weather_observation_generation(self) -> None:
        previous_generation = self._weather_observation_generation
        self._weather_observation_generation += 1
        self._weather_worker.cancel_generation(previous_generation)

    def _set_hardware_write_active(self, active: bool) -> None:
        desired = bool(active)
        if desired == self._hardware_write_active:
            return
        previous_generation = self._hardware_write_generation
        self._hardware_write_active = desired
        self._hardware_write_generation += 1
        self._hardware_write_worker.cancel_generation(previous_generation)

    def reconcile_lid_observation(self) -> None:
        if not hasattr(self.virtual_status_device, "presentation_scheduler_inputs"):
            return
        self.reconcile_presentation_timers(
            self.virtual_status_device.presentation_scheduler_inputs()
        )

    def reconcile_device_runtime(self) -> None:
        self.reconcile_lid_observation()

    def reconcile_presentation_timers(
        self,
        inputs: PresentationSchedulerInputs,
    ) -> None:
        if type(inputs) is not PresentationSchedulerInputs:
            raise ValueError("invalid presentation scheduler inputs")
        if self._presentation_reconcile_active:
            self._presentation_reconcile_pending = inputs
            return
        current_timebox_end = self.timebox_ends_at
        current_time = self._presentation_monotonic()
        if (
            self._runtime_started
            and not inputs.display_asleep
            and not inputs.app_terminating
            and current_timebox_end is not None
            and current_time >= current_timebox_end
        ):
            self._complete_timebox(current_time)
        lid_active = self._lid_observation_should_run(inputs)
        device_inventory_active = self._device_inventory_should_run(inputs)
        hardware_write_active = self._hardware_write_should_run(inputs)
        display_environment_active = self._display_environment_should_run(inputs)
        calendar_observation_active = self._calendar_observation_should_run(inputs)
        reminders_observation_active = self._reminders_observation_should_run(inputs)
        weather_observation_active = self._weather_observation_should_run(inputs)
        signal_preview_active = self._preview_should_run(
            RuntimeFeature.SETTINGS_SIGNAL_PREVIEW,
            inputs,
        )
        color_preview_active = self._preview_should_run(
            RuntimeFeature.SETTINGS_COLOR_PREVIEW,
            inputs,
        )
        setup_demo_active = self._preview_should_run(
            RuntimeFeature.SETUP_DEMO,
            inputs,
        )
        timebox_deadline = self._timebox_deadline(inputs)
        escalation_deadline = self._escalation_deadline(inputs)
        settings_message_deadline = self._settings_message_deadline(inputs)
        finite_ui_deadline = self._finite_ui_deadline(inputs)
        if (
            inputs == self._presentation_scheduler_inputs
            and lid_active == self._lid_observation_active
            and device_inventory_active == self._device_inventory_active
            and hardware_write_active == self._hardware_write_active
            and display_environment_active == self._display_environment_active
            and calendar_observation_active == self._calendar_observation_active
            and reminders_observation_active
            == self._reminders_observation_active
            and weather_observation_active == self._weather_observation_active
            and signal_preview_active
            == (
                RuntimeFeature.SETTINGS_SIGNAL_PREVIEW
                in self._runtime_preview_fire_at
            )
            and color_preview_active
            == (
                RuntimeFeature.SETTINGS_COLOR_PREVIEW
                in self._runtime_preview_fire_at
            )
            and setup_demo_active
            == (RuntimeFeature.SETUP_DEMO in self._runtime_preview_fire_at)
            and timebox_deadline == self._scheduled_timebox_deadline
            and escalation_deadline == self._scheduled_escalation_deadline
            and finite_ui_deadline
            == self._scheduled_reminders_cue_deadline
            and settings_message_deadline == getattr(
                self,
                "_scheduled_settings_message_deadline",
                None,
            )
        ):
            return

        self._presentation_reconcile_active = True
        current = inputs
        try:
            while current is not None:
                self._presentation_reconcile_pending = None
                current_lid_active = self._lid_observation_should_run(current)
                current_device_inventory_active = self._device_inventory_should_run(
                    current
                )
                current_hardware_write_active = self._hardware_write_should_run(current)
                current_display_environment_active = (
                    self._display_environment_should_run(current)
                )
                current_calendar_observation_active = (
                    self._calendar_observation_should_run(current)
                )
                current_reminders_observation_active = (
                    self._reminders_observation_should_run(current)
                )
                current_weather_observation_active = (
                    self._weather_observation_should_run(current)
                )
                current_signal_preview_active = self._preview_should_run(
                    RuntimeFeature.SETTINGS_SIGNAL_PREVIEW,
                    current,
                )
                current_color_preview_active = self._preview_should_run(
                    RuntimeFeature.SETTINGS_COLOR_PREVIEW,
                    current,
                )
                current_setup_demo_active = self._preview_should_run(
                    RuntimeFeature.SETUP_DEMO,
                    current,
                )
                current_timebox_deadline = self._timebox_deadline(current)
                current_escalation_deadline = self._escalation_deadline(current)
                current_settings_message_deadline = (
                    self._settings_message_deadline(current)
                )
                current_finite_ui_deadline = self._finite_ui_deadline(current)
                if (
                    current != self._presentation_scheduler_inputs
                    or current_lid_active != self._lid_observation_active
                    or current_device_inventory_active
                    != self._device_inventory_active
                    or current_hardware_write_active
                    != self._hardware_write_active
                    or current_display_environment_active
                    != self._display_environment_active
                    or current_calendar_observation_active
                    != self._calendar_observation_active
                    or current_reminders_observation_active
                    != self._reminders_observation_active
                    or current_weather_observation_active
                    != self._weather_observation_active
                    or current_signal_preview_active
                    != (
                        RuntimeFeature.SETTINGS_SIGNAL_PREVIEW
                        in self._runtime_preview_fire_at
                    )
                    or current_color_preview_active
                    != (
                        RuntimeFeature.SETTINGS_COLOR_PREVIEW
                        in self._runtime_preview_fire_at
                    )
                    or current_setup_demo_active
                    != (RuntimeFeature.SETUP_DEMO in self._runtime_preview_fire_at)
                    or current_timebox_deadline
                    != self._scheduled_timebox_deadline
                    or current_escalation_deadline
                    != self._scheduled_escalation_deadline
                    or current_finite_ui_deadline
                    != self._scheduled_reminders_cue_deadline
                    or current_settings_message_deadline
                    != getattr(
                        self,
                        "_scheduled_settings_message_deadline",
                        None,
                    )
                ):
                    now = self._presentation_monotonic()
                    plan = plan_presentation_schedule(
                        current,
                        now=now,
                        state=self._presentation_scheduler_state,
                    )
                    self._set_lid_observation_active(current_lid_active, now=now)
                    self._set_device_inventory_active(
                        current_device_inventory_active,
                        now=now,
                    )
                    self._set_hardware_write_active(current_hardware_write_active)
                    self._set_display_environment_active(
                        current_display_environment_active,
                        now=now,
                    )
                    self._set_calendar_observation_active(
                        current_calendar_observation_active,
                        now=now,
                    )
                    self._set_reminders_observation_active(
                        current_reminders_observation_active,
                        now=now,
                    )
                    self._set_weather_observation_active(
                        current_weather_observation_active,
                        now=now,
                    )
                    self._set_preview_active(
                        RuntimeFeature.SETTINGS_SIGNAL_PREVIEW,
                        current_signal_preview_active,
                        now=now,
                        interval=SETTINGS_SIGNAL_PREVIEW_INTERVAL_SECONDS,
                    )
                    self._set_preview_active(
                        RuntimeFeature.SETTINGS_COLOR_PREVIEW,
                        current_color_preview_active,
                        now=now,
                        interval=SETTINGS_COLOR_PREVIEW_INTERVAL_SECONDS,
                    )
                    self._set_preview_active(
                        RuntimeFeature.SETUP_DEMO,
                        current_setup_demo_active,
                        now=now,
                        interval=SETUP_DEMO_INTERVAL_SECONDS,
                    )
                    intents = plan.intents
                    if current_lid_active:
                        assert self._lid_observation_fire_at is not None
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=RuntimeFeature.LID_OBSERVATION,
                                fire_at=self._lid_observation_fire_at,
                                interval=LID_POLL_SECONDS,
                                tolerance=LID_POLL_SECONDS * 0.1,
                                common_modes=True,
                            ),
                        )
                    if current_device_inventory_active:
                        assert self._device_inventory_fire_at is not None
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=RuntimeFeature.DEVICE_INVENTORY,
                                fire_at=self._device_inventory_fire_at,
                                interval=STATUS_BAR_DEVICE_POLL_SECONDS,
                                tolerance=STATUS_BAR_DEVICE_POLL_SECONDS * 0.1,
                                common_modes=True,
                            ),
                        )
                    if current_display_environment_active:
                        assert self._display_environment_fire_at is not None
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=RuntimeFeature.DISPLAY_ENVIRONMENT,
                                fire_at=self._display_environment_fire_at,
                                interval=BRIGHTNESS_WATCH_SECONDS,
                                tolerance=0.25,
                                common_modes=True,
                            ),
                        )
                    if current_calendar_observation_active:
                        assert self._calendar_observation_fire_at is not None
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=RuntimeFeature.CALENDAR_OBSERVATION,
                                fire_at=self._calendar_observation_fire_at,
                                interval=CALENDAR_WATCH_SECONDS,
                                tolerance=CALENDAR_WATCH_SECONDS * 0.1,
                                common_modes=True,
                            ),
                        )
                    if current_reminders_observation_active:
                        assert self._reminders_observation_fire_at is not None
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=RuntimeFeature.REMINDERS_OBSERVATION,
                                fire_at=self._reminders_observation_fire_at,
                                interval=REMINDERS_WATCH_SECONDS,
                                tolerance=REMINDERS_WATCH_SECONDS * 0.1,
                                common_modes=True,
                            ),
                        )
                    if current_weather_observation_active:
                        assert self._weather_observation_fire_at is not None
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=RuntimeFeature.WEATHER_OBSERVATION,
                                fire_at=self._weather_observation_fire_at,
                                interval=WEATHER_WATCH_SECONDS,
                                tolerance=WEATHER_WATCH_SECONDS * 0.1,
                                common_modes=True,
                            ),
                        )
                    for feature, interval in (
                        (
                            RuntimeFeature.SETTINGS_SIGNAL_PREVIEW,
                            SETTINGS_SIGNAL_PREVIEW_INTERVAL_SECONDS,
                        ),
                        (
                            RuntimeFeature.SETTINGS_COLOR_PREVIEW,
                            SETTINGS_COLOR_PREVIEW_INTERVAL_SECONDS,
                        ),
                        (RuntimeFeature.SETUP_DEMO, SETUP_DEMO_INTERVAL_SECONDS),
                    ):
                        fire_at = self._runtime_preview_fire_at.get(feature)
                        if fire_at is None:
                            continue
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=feature,
                                fire_at=fire_at,
                                interval=interval,
                                tolerance=interval * 0.1,
                                common_modes=True,
                            ),
                        )
                    if current_timebox_deadline is not None:
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=RuntimeFeature.TIMEBOX_DEADLINE,
                                fire_at=current_timebox_deadline,
                                interval=None,
                                tolerance=0.0,
                                common_modes=True,
                            ),
                        )
                    if current_escalation_deadline is not None:
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=RuntimeFeature.ESCALATION_DEADLINE,
                                fire_at=current_escalation_deadline,
                                interval=None,
                                tolerance=0.0,
                                common_modes=True,
                            ),
                        )
                    if current_settings_message_deadline is not None:
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=RuntimeFeature.SETTINGS_MESSAGE_DEADLINE,
                                fire_at=current_settings_message_deadline,
                                interval=None,
                                tolerance=0.0,
                                common_modes=True,
                            ),
                        )
                    if current_finite_ui_deadline is not None:
                        intents = (
                            *intents,
                            RuntimeTimerIntent(
                                feature=RuntimeFeature.TEST_SIGNAL_DEADLINE,
                                fire_at=current_finite_ui_deadline,
                                interval=None,
                                tolerance=0.0,
                                common_modes=True,
                            ),
                        )
                    self._runtime_timer_registry.reconcile(
                        intents,
                        target=self,
                    )
                    self._presentation_scheduler_state = plan.next_state
                    self._presentation_scheduler_inputs = current
                    self._scheduled_timebox_deadline = current_timebox_deadline
                    self._scheduled_escalation_deadline = (
                        current_escalation_deadline
                    )
                    self._scheduled_reminders_cue_deadline = current_finite_ui_deadline
                    self._scheduled_settings_message_deadline = (
                        current_settings_message_deadline
                    )
                    if plan.reconcile_immediately:
                        self._presentation_static_deadline_fired()
                current = self._presentation_reconcile_pending
        finally:
            self._presentation_reconcile_pending = None
            self._presentation_reconcile_active = False

    def runtimeTimerFired_(self, timer) -> None:
        self._runtime_timer_registry.dispatch(timer)

    def _presentation_frame_fallback_fired(self) -> None:
        self.virtual_status_device.redraw_(None)

    def _presentation_static_deadline_fired(self) -> None:
        self.virtual_status_device.presentationStaticDeadline()

    def _presentation_alcove_observation_fired(self) -> None:
        self.virtual_status_device.presentationAlcoveObservation()

    def _presentation_pointer_peek_fired(self) -> None:
        self.peekTick_(None)

    def _settings_signal_preview_fired(self) -> None:
        inputs = self._presentation_scheduler_inputs
        if (
            inputs is not None
            and self._preview_should_run(
                RuntimeFeature.SETTINGS_SIGNAL_PREVIEW,
                inputs,
            )
        ):
            self.redrawSignalPreviews_(None)

    def _settings_color_preview_fired(self) -> None:
        inputs = self._presentation_scheduler_inputs
        if (
            inputs is not None
            and self._preview_should_run(
                RuntimeFeature.SETTINGS_COLOR_PREVIEW,
                inputs,
            )
        ):
            self.animate_colors_preview_once()

    def _setup_demo_fired(self) -> None:
        inputs = self._presentation_scheduler_inputs
        if (
            inputs is not None
            and self._preview_should_run(RuntimeFeature.SETUP_DEMO, inputs)
        ):
            self.redrawSetupDemo_(None)

    def _lid_observation_timer_fired(self) -> None:
        if not self._lid_observation_active:
            return
        now = self._runtime_worker_monotonic()
        self._os_poll_worker.submit(
            RuntimeWorkCommand(
                domain=RuntimeWorkerDomain.OS_POLL,
                key="lid-observation",
                generation=self._os_poll_generation,
                deadline=now + max(2.0, LID_POLL_SECONDS * 3.0),
                payload=LidObservationRequest(),
            )
        )

    def _device_inventory_timer_fired(self) -> None:
        if not self._device_inventory_active:
            return
        now = self._runtime_worker_monotonic()
        self._os_poll_worker.submit(
            RuntimeWorkCommand(
                domain=RuntimeWorkerDomain.OS_POLL,
                key="device-inventory",
                generation=self._os_poll_generation,
                deadline=now + max(4.0, STATUS_BAR_DEVICE_POLL_SECONDS * 3.0),
                payload=DeviceInventoryRequest(),
            )
        )

    def _display_environment_timer_fired(self) -> None:
        if not self._display_environment_active:
            return
        inputs = self._presentation_scheduler_inputs
        read_accessibility = bool(
            inputs is not None and inputs.screen_bar_enabled and inputs.visible
        )
        now = self._runtime_worker_monotonic()
        self._os_poll_worker.submit(
            RuntimeWorkCommand(
                domain=RuntimeWorkerDomain.OS_POLL,
                key="display-environment",
                generation=self._os_poll_generation,
                deadline=now + max(4.0, BRIGHTNESS_WATCH_SECONDS * 3.0),
                payload=DisplayEnvironmentRequest(
                    read_brightness=any(
                        device.auto_brightness_enabled
                        for device in self.settings.devices
                    ),
                    read_focus=bool(
                        self.settings.focus_sync_enabled
                        or self.settings.focus_profile_rules
                    ),
                    read_accessibility=read_accessibility,
                    previous_accessibility_preferences=(
                        self._accessibility_display_preferences
                    ),
                ),
            )
        )

    def _calendar_observation_timer_fired(self) -> None:
        inputs = self._presentation_scheduler_inputs
        if (
            not self._calendar_observation_active
            or not self._runtime_started
            or not self.settings.calendar_alerts_enabled
            or inputs is None
            or inputs.display_asleep
            or inputs.app_terminating
        ):
            return
        now = self._runtime_worker_monotonic()
        if now < self.calendar_watch_retry_at:
            return
        self._os_poll_worker.submit(
            RuntimeWorkCommand(
                domain=RuntimeWorkerDomain.OS_POLL,
                key="calendar-observation",
                generation=self._os_poll_generation,
                deadline=now + max(5.0, CALENDAR_WATCH_SECONDS * 3.0),
                payload=CalendarObservationRequest(
                    lead_minutes=self.settings.calendar_lead_minutes,
                ),
            )
        )

    def _reminders_observation_timer_fired(self) -> None:
        inputs = self._presentation_scheduler_inputs
        if (
            not self._reminders_observation_active
            or not self._runtime_started
            or not self.settings.reminder_alerts_enabled
            or inputs is None
            or inputs.display_asleep
            or inputs.app_terminating
        ):
            return
        now = self._runtime_worker_monotonic()
        if now < self.reminders_watch_retry_at:
            return
        self._os_poll_worker.submit(
            RuntimeWorkCommand(
                domain=RuntimeWorkerDomain.OS_POLL,
                key="reminders-observation",
                generation=self._os_poll_generation,
                deadline=now + max(5.0, REMINDERS_WATCH_SECONDS * 3.0),
                payload=RemindersObservationRequest(
                    lookback_seconds=REMINDERS_WATCH_SECONDS * 2.0,
                ),
            )
        )

    def _weather_observation_timer_fired(self) -> None:
        inputs = self._presentation_scheduler_inputs
        if (
            not self._weather_observation_active
            or not self._runtime_started
            or not self.settings.weather_alerts_enabled
            or inputs is None
            or inputs.display_asleep
            or inputs.app_terminating
        ):
            return
        now = self._runtime_worker_monotonic()
        if now < self.weather_watch_retry_at:
            return
        self._weather_worker.submit(
            RuntimeWorkCommand(
                domain=RuntimeWorkerDomain.WEATHER_FETCH,
                key=WEATHER_WORKER_KEY,
                generation=self._weather_observation_generation,
                deadline=now + WEATHER_FETCH_TIMEOUT_SECONDS,
                payload=WeatherObservationRequest(
                    self.settings.weather_latitude,
                    self.settings.weather_longitude,
                ),
            )
        )

    def _settings_message_deadline_fired(self) -> None:
        deadline = self._settings_message_deadline_at
        now = self._presentation_monotonic()
        self._scheduled_settings_message_deadline = None
        if deadline > now:
            self._reconcile_current_presentation_inputs()
            return
        self._settings_message_deadline_at = 0.0
        self.dismissSettingsMessage_(None)
        self._reconcile_current_presentation_inputs()

    def _finite_ui_deadline_fired(self) -> None:
        inputs = self._presentation_scheduler_inputs
        if inputs is None:
            return
        now = self._presentation_monotonic()
        self._scheduled_reminders_cue_deadline = None
        changed = self._retire_elapsed_ui_deadlines(inputs, now=now)
        glance = self._current_resolved_glance
        if type(glance) is ResolvedGlance:
            finite_cues = self.status_cue_coordinator.advance(now=now)
            self._status_finite_cues = finite_cues
            self._status_cue_deadline = finite_cues.next_deadline
            self._apply_status_accessibility_text(glance, finite_cues)
        self._reconcile_current_presentation_inputs()
        if changed:
            self.refresh_(None)

    def _timebox_deadline_fired(self) -> None:
        deadline = self.timebox_ends_at
        now = self._presentation_monotonic()
        if deadline is None:
            return
        if now < deadline:
            self._scheduled_timebox_deadline = None
            self.reconcile_lid_observation()
            return
        self._complete_timebox(now)
        self._scheduled_timebox_deadline = None
        self.reconcile_lid_observation()

    def _complete_timebox(self, now: float) -> None:
        self.timebox_ends_at = None
        self.timebox_total_seconds = 0.0
        self.timebox_overtime_since = now
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
        self.refresh_(None)

    def _escalation_deadline_fired(self) -> None:
        self._scheduled_escalation_deadline = None
        self.apply_escalation(allow_refresh=True)
        inputs = self._presentation_scheduler_inputs
        if inputs is not None:
            self.reconcile_presentation_timers(inputs)

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
        peek_deadline = getattr(self, "peek_until", 0.0)
        peeking = now < peek_deadline
        if peek_deadline > 0.0 and not peeking:
            self.peek_until = 0.0
            self.refresh_(None)
        if inside:
            self._peek_hits = getattr(self, "_peek_hits", 0) + 1
            if self._peek_hits == 3 and not peeking:
                self.peek_until = now + 4.0
                self.refresh_(None)
        else:
            self._peek_hits = 0
            if peeking:
                self.peek_until = 0.0
                self.refresh_(None)

    def peek_program(self, brightness: float, led_count: int = 8) -> str:
        """Show an active timebox, otherwise withhold legacy capacity."""
        if self.timebox_active():
            return timer_fill_program(
                self.timer_fill_fraction(),
                led_count=led_count,
                brightness=brightness,
                color=self.settings.colors.mode_colors.get("working", "#00E5FF"),
            )
        return "off"

    def screen_bar_click_status(self):
        """The session a Screen Bar click should open: the OLDEST
        unanswered hard ask (the same episode ordering escalation
        follows), or None when nothing needs you."""
        projection = getattr(self, "current_attention_projection", None)
        if projection is None or not projection.actionable_attention:
            return None
        tracked = getattr(self, "ask_blocked_by_agent", {})
        target_row = min(
            projection.actionable_attention,
            key=lambda row: (
                tracked.get(row.agent_id, float("inf")),
                row.updated_at,
                row.agent_id,
            ),
        )
        return next(
            (
                row.source_status
                for row in projection.actionable_attention
                if row.agent_id == target_row.agent_id
            ),
            None,
        )

    def quota_runway_state(self) -> tuple[float, str] | None:
        """Withhold every capacity-derived LED and hardware state this wave."""
        return None

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
            LED_DISPLAY_FAILURE: (
                lambda brightness, led_count: (
                    failure_signal_program(
                        self.settings.colors.mode_color("ask"),
                        active,
                        brightness=brightness,
                        led_count=led_count,
                    )
                    if (active := self.active_failure_signal()) is not None
                    else None
                ),
                LedDisplayState.FAILED,
                lambda device, _snapshot: f"{device.name} Failure",
            ),
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
        *,
        projection: AttentionProjection | None = None,
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

        relay_elapsed_seconds = max(0.0, time.monotonic() - self._relay_epoch)

        active_errors: dict[str, str] = {}
        agent_write_changed = False
        agent_write_failed = False
        agent_display_rendered = False
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
                agent_display_rendered = True
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
                if projection is not None:
                    result = self.agent_controller_for_device(device).sync_projection(
                        self.projection_for_device(projection, device),
                        colors_for_render,
                        active_signal=self.active_failure_signal(),
                        relay_elapsed_seconds=relay_elapsed_seconds,
                    )
                else:
                    result = self.agent_controller_for_device(device).sync_snapshot(
                        statuses_for_device,
                        colors_for_render,
                        fallback_mode=fallback_for_device,
                        relay_elapsed_seconds=relay_elapsed_seconds,
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
            self.schedule_screen_bar_sync(
                mode,
                battery_snapshot,
                statuses,
                time.monotonic(),
                projection,
                relay_elapsed_seconds,
            )
        elif agent_write_failed:
            # The physical write failed -- there's no real completion to
            # sync to, so fall back to an immediate (unsynced) update rather
            # than leaving the Screen Bar stuck showing stale state forever.
            payload = {
                "mode": mode,
                "battery_snapshot": battery_snapshot,
                "statuses": statuses,
                "started_at": None,
                "projection": projection,
                "relay_elapsed_seconds": relay_elapsed_seconds,
            }
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyScreenBarSync:",
                payload,
                False,
            )
        elif agent_display_rendered:
            # A smaller physical surface can dedupe even when an agent that
            # only appears on the 8-LED Screen Bar changed. Let the virtual
            # renderer perform its own program invalidation without claiming
            # that a new physical write supplied a phase anchor.
            self.schedule_screen_bar_sync(
                mode,
                battery_snapshot,
                statuses,
                None,
                projection,
                relay_elapsed_seconds,
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
                target = write_led_program(
                    program,
                    device_path=device.target,
                    preserve_existing_inode=True,
                )
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
        self.reconcile_lid_observation()
        self.status_bar_devices()
        self.reset_led_controllers_for_display_change()
        self.last_led_error = None
        self.last_status_read_error = None
        log_status_bar("device connect requested")

    def disconnect_device(self) -> None:
        self.leds_enabled = False
        self.reconcile_lid_observation()
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

    def _apply_lid_observation(self, result: LidObservationResult) -> None:
        if not result.ok:
            error = str(result.error)
            if error != self.last_lid_error:
                self.last_lid_error = error
                log_status_bar(f"lid_state error: {error}")
            return
        closed = result.closed
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

    def display_aggregate_mode(self, projection) -> AgentMode:
        """Legacy mode adapter for semantics already fixed by projection."""
        if not isinstance(projection, AttentionProjection):
            projection = project_attention(projection, self.settings)
        return {
            LifecycleMode.IDLE: AgentMode.IDLE_READY,
            LifecycleMode.ACTIVE: AgentMode.WORKING,
            LifecycleMode.WAITING: AgentMode.WAITING_FOR_INPUT,
            LifecycleMode.COMPLETED_RECENTLY: AgentMode.COMPLETED,
            LifecycleMode.FAILED_VISIBLE: AgentMode.BLOCKED_ERROR,
            LifecycleMode.UNKNOWN: AgentMode.UNKNOWN,
        }[projection.lifecycle_mode]

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
        self.reconcile_lid_observation()

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


def concise_usage_error(error: Exception) -> str:
    text = " ".join(str(error).split())
    return (text or "usage unavailable")[:80]


def usage_window_payloads(model: ProviderUsageViewModel) -> tuple[dict, ...]:
    return tuple(
        {
            "label": window.label,
            "used_percent": window.percent_used,
            "window_minutes": window.window_minutes,
            "resets_at": window.reset_at,
        }
        for window in model.windows
    )


def _capacity_age_text(model: ProviderUsageViewModel, *, monotonic_now: float) -> str | None:
    if model.last_success_at is None:
        return None
    age = max(0.0, float(monotonic_now) - float(model.last_success_at))
    if age < 60.0:
        return "updated just now"
    if age < 3_600.0:
        return f"updated {max(1, int(age // 60.0))}m ago"
    if age < 24 * 3_600.0:
        return f"updated {max(1, int(age // 3_600.0))}h ago"
    return f"updated {max(1, int(age // (24 * 3_600.0)))}d ago"


def capacity_menu_lines(
    model: ProviderUsageViewModel,
    *,
    monotonic_now: float,
    epoch_now: float,
) -> tuple[str, str]:
    """Return concise primary capacity and secondary reset/freshness copy."""
    if model.missing:
        return model.menu_line, ""

    primary_window = model.windows[0] if model.windows else None
    if primary_window is None:
        primary = model.menu_line
    else:
        semantic = primary_window.duration_text
        remaining = primary_window.percent_remaining
        amount = f" {remaining:.0f}% left" if remaining is not None else ""
        primary = f"{model.provider_title} · {semantic}{amount}"

    secondary_parts: list[str] = []
    if primary_window is not None:
        reset = primary_window.reset_text(epoch_now)
        if reset:
            secondary_parts.append(f"resets {reset}")
    age = _capacity_age_text(model, monotonic_now=monotonic_now)
    if age:
        secondary_parts.append(age)
    if model.refreshing:
        secondary_parts.append("refreshing")
    if model.partial:
        secondary_parts.append("partial")
    if model.source_text:
        secondary_parts.append(model.source_text)
    if model.stale:
        secondary_parts.append("stale")
    if model.error_text:
        secondary_parts.append(model.error_text)
    return primary, " · ".join(secondary_parts)


def _configure_usage_menu_label(field, *, size: float, secondary: bool) -> None:
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setFont_(
        NSFont.systemFontOfSize_(size)
        if secondary
        else NSFont.boldSystemFontOfSize_(size)
    )
    if secondary:
        field.setTextColor_(NSColor.secondaryLabelColor())


def build_usage_menu_item(target) -> NSMenuItem:
    """Host stable Capacity labels so publications never rebuild the menu."""
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
    view = NSView.alloc().initWithFrame_(((0, 0), (292, 110)))
    header = NSTextField.alloc().initWithFrame_(((14, 87), (264, 17)))
    _configure_usage_menu_label(header, size=11.0, secondary=False)
    header.setStringValue_("Capacity")
    view.addSubview_(header)

    labels = {}
    secondary_labels = {}
    models = getattr(target, "_usage_provider_models", {}) or {}
    states = getattr(target, "_usage_provider_states", {}) or {}
    now = time.monotonic()
    reset_now = time.time()
    for provider_id, primary_y, secondary_y in (
        ("codex", 64, 47),
        ("claude", 25, 8),
    ):
        field = NSTextField.alloc().initWithFrame_(((14, primary_y), (264, 18)))
        _configure_usage_menu_label(field, size=11.0, secondary=False)
        secondary_field = NSTextField.alloc().initWithFrame_(
            ((14, secondary_y), (264, 16))
        )
        _configure_usage_menu_label(secondary_field, size=10.0, secondary=True)
        model = models.get(provider_id)
        state = states.get(provider_id)
        if model is None:
            model = build_provider_usage_view(
                provider_id,
                provider_id.title(),
                (),
                now=now,
                reset_now=reset_now,
                refreshing=bool(state and state.in_flight),
                error_text=getattr(state, "error_text", None),
            )
        elif state is not None and model.refreshing != state.in_flight:
            model = dataclass_replace(model, refreshing=state.in_flight)
        primary, secondary = capacity_menu_lines(
            model,
            monotonic_now=now,
            epoch_now=reset_now,
        )
        field.setStringValue_(primary)
        secondary_field.setStringValue_(secondary)
        view.addSubview_(field)
        view.addSubview_(secondary_field)
        labels[provider_id] = field
        secondary_labels[provider_id] = secondary_field
    item.setView_(view)
    target._usage_menu_item = item
    target._usage_menu_view = view
    target._usage_menu_header = header
    target._usage_menu_labels = labels
    target._usage_menu_secondary_labels = secondary_labels
    return item


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


_MAILBOX_SECTION_TITLES = {
    MailboxSectionKind.NEEDS_YOU: "Needs You",
    MailboxSectionKind.IN_PROGRESS: "In Progress",
    MailboxSectionKind.READY_FOR_REVIEW: "Ready for Review",
    MailboxSectionKind.RECENT: "Recent",
}
_MAILBOX_MAX_WORKERS_PER_ROLLUP = 12


def mailbox_projection_rows(
    projection: AttentionProjection,
    target,
) -> tuple:
    """Return only the authoritative projected rows eligible for the mailbox."""
    cleared_ids = getattr(target, "cleared_session_ids", set())
    if not cleared_ids:
        return projection.visible_rows
    return tuple(
        row
        for row in projection.visible_rows
        if not (
            row.agent_id in cleared_ids
            and row.lifecycle_mode == LifecycleMode.COMPLETED_RECENTLY
        )
    )


def project_mailbox_for_target(
    projection: AttentionProjection,
    target,
) -> AgentMailboxProjection:
    mailbox_projection = dataclass_replace(
        projection,
        visible_rows=mailbox_projection_rows(projection, target),
    )
    return project_mailbox(
        mailbox_projection,
        previous_order=getattr(target, "mailbox_retained_order", None),
        seen_completion_ids=getattr(target, "mailbox_seen_completion_ids", set()),
    )


def mailbox_projection_for_menu(snapshot, target) -> AgentMailboxProjection:
    """Return the controller-owned projection, with a legacy test fallback."""
    current = getattr(target, "current_mailbox_projection", None)
    if isinstance(current, AgentMailboxProjection):
        return current
    attention = getattr(target, "current_attention_projection", None)
    if not isinstance(attention, AttentionProjection):
        attention = project_attention(
            SimpleNamespace(
                statuses=mailbox_attention_statuses(snapshot),
                collected_at=snapshot.collected_at,
            ),
            target.settings,
        )
    mailbox = project_mailbox_for_target(attention, target)
    target.current_attention_projection = attention
    target.current_mailbox_projection = mailbox
    target.mailbox_retained_order = dict(mailbox.retained_order)
    if not hasattr(target, "mailbox_seen_completion_ids"):
        target.mailbox_seen_completion_ids = set()
    return mailbox


def mailbox_content_signature(mailbox: AgentMailboxProjection) -> tuple:
    return (
        mailbox.active_count,
        mailbox.needs_you_count,
        mailbox.ready_count,
        tuple(
            (
                section.kind.value,
                tuple(_mailbox_row_content_signature(row) for row in section.rows),
                section.overflow_count,
            )
            for section in mailbox.sections
        ),
    )


def _mailbox_row_content_signature(row) -> tuple:
    if type(row) is MailboxRow:
        return (
            _work_key_menu_identity(row.work_key),
            row.safe_label,
            row.lifecycle.value,
            row.next_actor.value,
            row.source_freshness.value,
            row.actionable,
            row.worker_count,
            row.stable_order,
            row.timing_uncertain,
        )
    return (
        row.agent_id,
        row.provider,
        row.display_name,
        row.lifecycle_mode.value,
        row.activity_label,
        row.actionable,
        row.navigation_agent_id,
        row.worker_count,
        row.stable_order,
    )


def menu_content_signature(snapshot, state, target) -> tuple:
    """Everything the dropdown renders, hashed coarsely. Ages bucket to
    the minute (rows show "4m"), and a 30s monotonic bucket is a safety
    valve: anything this signature misses self-heals within 30s."""
    mailbox = mailbox_projection_for_menu(snapshot, target)
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
        mailbox_content_signature(mailbox),
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


def _mailbox_source_rows(projection: AttentionProjection) -> dict[str, AgentStatus]:
    sources: dict[str, AgentStatus] = {}
    for projected in projection.visible_rows:
        current = sources.get(projected.agent_id)
        if current is None or projected.updated_at >= current.updated_at:
            sources[projected.agent_id] = projected.source_status
    return sources


def _mailbox_workers_by_parent(
    sources: dict[str, AgentStatus],
) -> tuple[dict[str, list[AgentStatus]], list[AgentStatus]]:
    groups: dict[str, list[AgentStatus]] = {}
    orphans: list[AgentStatus] = []
    for status in sources.values():
        if not status.is_subagent:
            continue
        parent_id = status.parent_agent_id
        if parent_id is None or parent_id not in sources:
            orphans.append(status)
        else:
            groups.setdefault(parent_id, []).append(status)
    return groups, orphans


def _mailbox_row_suffix(row: MailboxRow) -> str:
    facts = []
    if row.activity_label:
        facts.append(row.activity_label)
    if row.worker_count == 1:
        facts.append("1 worker")
    elif row.worker_count > 1:
        facts.append(f"{row.worker_count} workers")
    return f" · {' · '.join(facts)}" if facts else ""


def _mailbox_display_status(
    row: MailboxRow,
    display_source: AgentStatus,
    navigation_source: AgentStatus,
) -> AgentStatus:
    mode_by_lifecycle = {
        LifecycleMode.IDLE: AgentMode.IDLE_READY,
        LifecycleMode.ACTIVE: AgentMode.WORKING,
        LifecycleMode.WAITING: AgentMode.WAITING_FOR_INPUT,
        LifecycleMode.COMPLETED_RECENTLY: AgentMode.COMPLETED,
        LifecycleMode.FAILED_VISIBLE: AgentMode.BLOCKED_ERROR,
        LifecycleMode.UNKNOWN: AgentMode.UNKNOWN,
    }
    return dataclass_replace(
        display_source,
        display_name=row.display_name,
        mode=mode_by_lifecycle[row.lifecycle_mode],
        event_name=(
            navigation_source.event_name if row.actionable else display_source.event_name
        ),
        cwd=None,
    )


def _add_mailbox_empty_teaching(menu: NSMenu, target) -> None:
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
        return
    connect_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Connect your agents in Setup…", "openSetup:", ""
    )
    connect_item.setTarget_(target)
    menu.addItem_(connect_item)


def build_agent_mailbox_menu_item(snapshot, target) -> NSMenuItem:
    mailbox = mailbox_projection_for_menu(snapshot, target)
    attention = getattr(target, "current_attention_projection", None)
    if not isinstance(attention, AttentionProjection):
        attention = project_attention(snapshot, target.settings)
    sources = _mailbox_source_rows(attention)
    workers_by_parent, orphan_workers = _mailbox_workers_by_parent(sources)
    display_sources = [status for status in sources.values() if not status.is_subagent]
    identity: dict[str, str] = {}
    if len(display_sources) > 1:
        menu_colors = getattr(getattr(target, "settings", None), "colors", None)
        identity = colors_module.identity_colors_for_agents(
            [status.agent_id for status in display_sources],
            groups=colors_module.identity_groups_for_statuses(
                display_sources, menu_colors
            ),
        )

    summary = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        (
            f"Agent Mailbox · {mailbox.active_count} active · "
            f"{mailbox.needs_you_count} need you · {mailbox.ready_count} ready"
        ),
        None,
        "",
    )
    mailbox_menu = NSMenu.alloc().init()
    mailbox_menu.setAutoenablesItems_(False)
    has_rows = any(section.rows for section in mailbox.sections)
    for section in mailbox.sections:
        shelf_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            _MAILBOX_SECTION_TITLES[section.kind], None, ""
        )
        shelf_menu = NSMenu.alloc().init()
        shelf_menu.setAutoenablesItems_(False)
        if not section.rows:
            if not has_rows and section.kind == MailboxSectionKind.RECENT:
                _add_mailbox_empty_teaching(shelf_menu, target)
        provider_order: list[str] = []
        for row in section.rows:
            if row.provider not in provider_order:
                provider_order.append(row.provider)
        show_provider_headers = len(provider_order) > 1
        rows = (
            tuple(
                row
                for provider in provider_order
                for row in section.rows
                if row.provider == provider
            )
            if show_provider_headers
            else section.rows
        )
        last_provider_header = None
        for row in rows:
            if show_provider_headers and row.provider != last_provider_header:
                last_provider_header = row.provider
                try:
                    provider_title = provider_spec(row.provider).label
                except ValueError:
                    provider_title = row.provider.title()
                shelf_menu.addItem_(disabled_menu_item(provider_title))
            display_source = sources.get(row.agent_id)
            navigation_source = sources.get(row.navigation_agent_id or "")
            dot_color = None
            if display_source is None:
                display_source = navigation_source
            if navigation_source is None:
                navigation_source = display_source
            if display_source is None or navigation_source is None:
                synthetic = disabled_menu_item(
                    f"{row.display_name}{_mailbox_row_suffix(row)}"
                )
                shelf_menu.addItem_(synthetic)
            else:
                rendered = _mailbox_display_status(
                    row, display_source, navigation_source
                )
                if getattr(target, "settings", None) is not None:
                    dot_color = target.settings.colors.session_color(row.agent_id)
                row_item = build_session_menu_item(
                    rendered,
                    snapshot.collected_at,
                    target,
                    identity_color=dot_color or identity.get(row.agent_id),
                    title_suffix=_mailbox_row_suffix(row),
                )
                row_item.setRepresentedObject_(navigation_source)
                shelf_menu.addItem_(row_item)
            children = (
                orphan_workers
                if row.agent_id == "sidepulse:mailbox:background-agents"
                else workers_by_parent.get(row.agent_id, [])
            )
            if children:
                shelf_menu.addItem_(
                    build_worker_rollup_item(
                        children,
                        snapshot.collected_at,
                        target,
                        dot_color or identity.get(row.agent_id),
                        max_visible=_MAILBOX_MAX_WORKERS_PER_ROLLUP,
                    )
                )
        if section.overflow_count:
            shelf_menu.addItem_(disabled_menu_item(f"{section.overflow_count} more"))
        shelf_item.setSubmenu_(shelf_menu)
        mailbox_menu.addItem_(shelf_item)
    summary.setSubmenu_(mailbox_menu)
    return summary


def _canonical_agent_browser_projection(
    snapshot,
    target,
    *,
    text: str = "",
    shelf: MailboxSectionKind | None = None,
    family_key=None,
    selected_work_key=None,
):
    state = getattr(snapshot, "operator_state", None)
    if state is None:
        state = getattr(target, "current_operator_state", None)
    if state is None:
        return None
    target.current_operator_state = state
    # Opening the dropdown ran this entire pipeline -- canonical mailbox
    # projection + preference application + agent-browser document build,
    # over the WHOLE operator state -- three to four times per click, and
    # the extra passes only overwrote each other's results. Memoize per
    # (state, query): the state object is held strongly so its identity
    # cannot be recycled underneath the cache, and every call still has
    # its side effects applied by the pass that actually computed.
    memo_key = (text, shelf, family_key, selected_work_key)
    memo = getattr(target, "_mailbox_projection_memo", None)
    if memo is not None and memo[0] is state and memo[1] == memo_key:
        return memo[2]
    previous_order = {
        key: order
        for key, order in getattr(target, "mailbox_retained_order", {}).items()
        if type(key) is not str and type(order) is int and order >= 0
    }
    mailbox = project_canonical_mailbox(
        state,
        previous_order=previous_order,
    )
    preferences = apply_mailbox_preferences(
        mailbox,
        getattr(target, "mailbox_preferences", ()),
        now=time.time(),
    )
    if type(preferences) is not MailboxPreferenceProjection:
        return None
    target.current_mailbox_projection = preferences.projection
    target.mailbox_retained_order = dict(preferences.projection.retained_order)
    scheduler = getattr(target, "schedule_mailbox_boundary", None)
    if callable(scheduler):
        scheduler(preferences.next_wake_epoch)
    documents = build_agent_browser_documents(
        state,
        preferences.projection,
        preferences,
        getattr(target, "local_triage_state", LocalTriageState(())),
    )
    projected = project_agent_browser(
        documents,
        AgentBrowserQuery(text, shelf=shelf, family_key=family_key),
        generation=state.generation,
        selected_work_key=selected_work_key or family_key,
    )
    target._mailbox_projection_memo = (state, memo_key, projected)
    return projected


def _request_for_work(state, work_key):
    requests = {
        request.key: request
        for request in getattr(state, "requests", ())
        if request.key.work_key == work_key
    }
    work = next(
        (item for item in getattr(state, "works", ()) if item.key == work_key),
        None,
    )
    if work is None:
        return None
    return next(
        (requests[key] for key in work.request_keys if key in requests),
        None,
    )


def _family_work_key(state, work_key):
    works = {work.key: work for work in getattr(state, "works", ())}
    current = works.get(work_key)
    seen = set()
    while current is not None and current.parent_key is not None:
        if current.key in seen:
            return None
        seen.add(current.key)
        current = works.get(current.parent_key)
    return None if current is None else current.key


def _preference_for_work_key(preferences, work_key):
    return next(
        (
            item
            for item in preferences
            if type(item) is MailboxPreference and item.work_key == work_key
        ),
        None,
    )


def _replace_mailbox_preference(preferences, replacement):
    retained = [
        item
        for item in preferences
        if type(item) is MailboxPreference and item.work_key != replacement.work_key
    ]
    return tuple((*retained, replacement))


def _move_pinned_preference(preferences, work_key, *, delta):
    pinned = sorted(
        (
            item
            for item in preferences
            if type(item) is MailboxPreference
            and item.mode is MailboxPreferenceMode.PINNED
            and item.pin_order is not None
        ),
        key=lambda item: (item.pin_order, item.work_key),
    )
    index = next(
        (position for position, item in enumerate(pinned) if item.work_key == work_key),
        None,
    )
    if index is None or not 0 <= index + delta < len(pinned):
        return None
    other_index = index + delta
    moved = pinned[index]
    other = pinned[other_index]
    replacements = {
        moved.work_key: dataclass_replace(moved, pin_order=other.pin_order),
        other.work_key: dataclass_replace(other, pin_order=moved.pin_order),
    }
    return tuple(replacements.get(item.work_key, item) for item in preferences)


def _canonical_operator_actions(state, target):
    if state is None:
        return {}
    preferences = {
        item.work_key: item
        for item in getattr(target, "mailbox_preferences", ())
        if type(item) is MailboxPreference
    }
    pinned = sorted(
        (
            item
            for item in preferences.values()
            if item.mode is MailboxPreferenceMode.PINNED
            and item.pin_order is not None
        ),
        key=lambda item: (item.pin_order, item.work_key),
    )
    pin_positions = {item.work_key: index for index, item in enumerate(pinned)}
    acknowledged = {
        item.request_key
        for item in getattr(
            target,
            "local_triage_state",
            LocalTriageState(()),
        ).acknowledgements
    }
    candidates_by_work = getattr(target, "navigation_candidates_by_work_key", {})
    result = {}
    for work in state.works:
        family_key = _family_work_key(state, work.key)
        if family_key is None:
            continue
        request = _request_for_work(state, work.key)
        preference = preferences.get(family_key)
        navigation = resolve_navigation(
            work.key,
            "open:primary",
            candidates_by_work.get(work.key, ()),
        )
        result[work.key] = build_operator_actions(
            work=work,
            request=request,
            local=OperatorLocalActionState(
                watched=(
                    preference is not None
                    and preference.mode is MailboxPreferenceMode.WATCHED
                ),
                pinned=family_key in pin_positions,
                snoozed=(
                    preference is not None
                    and preference.snoozed_at is not None
                ),
                acknowledged=(
                    request is not None and request.key in acknowledged
                ),
                pin_position=pin_positions.get(family_key),
                pin_count=len(pinned),
            ),
            navigation=navigation,
        )
    return result


def _work_key_menu_identity(work_key) -> str:
    source = work_key.source_key
    return ":".join(
        (
            source.provider_id,
            source.adapter_id,
            source.source_instance_id,
            source.capability_id,
            work_key.work_id.value,
        )
    )


def _menu_copy_size(title: str) -> tuple[int, int]:
    bounded = title[:256]
    size = NSString.stringWithString_(bounded).sizeWithAttributes_(
        {NSFontAttributeName: NSFont.menuFontOfSize_(0.0)}
    )
    return math.ceil(size.width), math.ceil(size.height)


def _native_item_state(
    item,
    *,
    item_key: str,
    parent_key: str | None,
    order: int,
    submenu_key: str | None,
    action_kind: OperatorActionKind | None,
) -> MenuItemState:
    title = str(item.title())
    width, height = _menu_copy_size(title)

    def text_value(selector: str, fallback: str = "") -> str:
        getter = getattr(item, selector, None)
        value = getter() if callable(getter) else None
        return value if type(value) is str else fallback

    if ":action:" in item_key:
        accessibility_label = "Agent action"
    elif ":urgent:" in item_key:
        accessibility_label = "Urgent agent row"
    elif ":overflow:" in item_key:
        accessibility_label = "More agents"
    elif ":browser:" in item_key:
        accessibility_label = "Open Agent Browser"
    else:
        accessibility_label = "Agent Mailbox summary"

    return MenuItemState(
        item_key=item_key,
        parent_key=parent_key,
        order=order,
        submenu_key=submenu_key,
        action_kind=action_kind,
        key_equivalent=str(item.keyEquivalent() or ""),
        title=title,
        enabled=bool(item.isEnabled()),
        state=int(item.state()),
        measured_width=width,
        measured_height=height,
        accessibility_label=text_value(
            "accessibilityLabel",
            accessibility_label,
        ),
        accessibility_value=text_value("accessibilityValue"),
        accessibility_help=text_value("accessibilityHelp"),
    )


def _canonical_agent_root_snapshot(snapshot, target, *, menu=None):
    projection = _canonical_agent_browser_projection(snapshot, target)
    if projection is None:
        return None
    actions = _canonical_operator_actions(target.current_operator_state, target)
    prepared = build_agent_root_items(
        projection,
        actions_by_work_key=actions,
        target=target,
    )
    root_items = tuple(
        menu.itemAtIndex_(index) if menu is not None else prepared[index]
        for index in range(len(prepared))
    )
    states = []
    items = {}
    urgent_index = 0
    for order, item in enumerate(root_items):
        title = str(item.title())
        if order == 0:
            item_key = "agent-mailbox:summary"
            submenu_key = None
        elif item.submenu() is not None:
            row = projection.rows[urgent_index]
            urgent_index += 1
            identity = _work_key_menu_identity(row.work_key)
            item_key = f"agent-mailbox:urgent:{identity}"
            submenu_key = f"{item_key}:actions:g{projection.generation}"
        elif title.endswith("more..."):
            item_key = f"agent-mailbox:overflow:g{projection.generation}"
            submenu_key = None
        else:
            item_key = f"agent-mailbox:browser:g{projection.generation}"
            submenu_key = None
        items[item_key] = item
        states.append(
            _native_item_state(
                item,
                item_key=item_key,
                parent_key=None,
                order=order,
                submenu_key=submenu_key,
                action_kind=None,
            )
        )
        submenu = item.submenu()
        if submenu is None:
            continue
        for action_order in range(submenu.numberOfItems()):
            action = submenu.itemAtIndex_(action_order)
            payload = action.representedObject()
            if type(payload) is not AgentBrowserActionPayload:
                continue
            preset = f":{payload.snooze_preset}" if payload.snooze_preset else ""
            action_key = f"{item_key}:action:{payload.kind.value}{preset}"
            items[action_key] = action
            states.append(
                _native_item_state(
                    action,
                    item_key=action_key,
                    parent_key=item_key,
                    order=action_order,
                    submenu_key=None,
                    action_kind=payload.kind,
                )
            )
    return tuple(states), items


def build_menu(snapshot, state: StatusBarState, target: StatusBarController) -> NSMenu:
    """The status-item dropdown. Glanceability rules: sessions first
    (the thing you opened the menu to check), no self-titled header (you
    know what menu you clicked), and one row per secondary concern --
    the keep-awake policy is a submenu, not four inline rows."""
    menu = NSMenu.alloc().init()
    browser_projection = _canonical_agent_browser_projection(snapshot, target)
    if browser_projection is None:
        menu.addItem_(build_agent_mailbox_menu_item(snapshot, target))
    else:
        actions_by_work_key = _canonical_operator_actions(
            getattr(target, "current_operator_state", None),
            target,
        )
        for item in build_agent_root_items(
            browser_projection,
            actions_by_work_key=actions_by_work_key,
            target=target,
        ):
            menu.addItem_(item)
        action_error = getattr(target, "operator_action_error", None)
        if type(action_error) is str and action_error:
            menu.addItem_(disabled_menu_item(action_error))
    # An out-of-date hook cannot deliver live events. Say so where the
    # user already looks, with the one click that fixes it -- this
    # failure was invisible for an hour the first time it happened.
    legacy_hooks = sorted(getattr(target, "legacy_hook_providers", ()) or ())
    if legacy_hooks:
        names = ", ".join(provider.title() for provider in legacy_hooks)
        stale_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"\u26a0 {names} hooks are out of date \u2014 reinstall in Setup\u2026",
            "openSetup:",
            "",
        )
        stale_item.setTarget_(target)
        menu.addItem_(stale_item)
        menu.addItem_(NSMenuItem.separatorItem())
    statuses = recent_statuses(snapshot)

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
    menu.addItem_(build_usage_menu_item(target))
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
        "Quit SidePulse",
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


def choose_operator_export_path(kind: str) -> Path | None:
    if kind not in {"history", "diagnostics"}:
        return None
    panel = NSSavePanel.savePanel()
    if kind == "history":
        panel.setTitle_("Export SidePulse History")
        panel.setNameFieldStringValue_("sidepulse-history.json")
    else:
        panel.setTitle_("Export SidePulse Diagnostics")
        panel.setNameFieldStringValue_("sidepulse-diagnostics.json")
    if hasattr(panel, "setAllowedFileTypes_"):
        panel.setAllowedFileTypes_(["json"])
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
    ("history", "History"),
    ("capacity", "Capacity"),
    ("header:setup", "SET UP"),
    ("devices", "Devices"),
    ("agents", "Agents"),
    ("installed_agents", "Installed Agents"),
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
    "history": "clock.arrow.circlepath",
    "capacity": "gauge.with.dots.needle.67percent",
    "devices": "cpu",
    "agents": "sparkles",
    "installed_agents": "shippingbox",
    "color_studio": "paintpalette",
    "colors_screen_bar": "menubar.rectangle",
    "animations": "film",
    "led_behavior": "bell.badge",
    "focus": "moon",
    "power": "bolt",
    "debug": "wrench.and.screwdriver",
}


class UsageGraphView(NSView):
    """One calm shared-axis chart for the selected period and metric."""

    PROVIDER_COLORS: ClassVar[dict[str, str]] = {
        "claude": "#D97757",
        "codex": "#10A37F",
        "opencode": "#7C6CF2",
        "google": "#4285F4",
    }

    def initWithFrame_(self, frame):
        self = objc.super(UsageGraphView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.model = {}
        return self

    def setModel_(self, model):
        self.model = dict(model) if isinstance(model, dict) else {}
        self.setNeedsDisplay_(True)

    def setData_hourly_(self, day_bars, hourly):
        """Compatibility adapter for old tests and an in-flight old publish."""
        del hourly
        bars = list(day_bars or [])
        self.setModel_(
            {
                "metric": "tokens",
                "labels": tuple(bar.get("label", "") for bar in bars),
                "series": (
                    {
                        "provider_id": "codex",
                        "values": tuple(bar.get("codex_tokens", 0) for bar in bars),
                    },
                ),
                "scale_max": usage_stats.nice_usage_scale(
                    max((bar.get("codex_tokens", 0) for bar in bars), default=0)
                ),
            }
        )

    def isFlipped(self):
        return False

    def drawRect_(self, _rect):
        bounds = self.bounds().size
        width, height = bounds.width, bounds.height
        model = self.model
        series = tuple(model.get("series") or ())
        labels = tuple(model.get("labels") or ())
        if width <= 0 or height <= 0:
            return
        left = 50.0
        right = 10.0
        bottom = 22.0
        top = 18.0
        plot_width = max(1.0, width - left - right)
        plot_height = max(1.0, height - bottom - top)
        metric = str(model.get("metric") or "tokens")
        scale_max = max(1.0, float(model.get("scale_max") or 1.0))
        label_attrs = {
            NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
            NSFontAttributeName: NSFont.systemFontOfSize_(9.5),
        }
        for grid_index in range(4):
            fraction = grid_index / 3.0
            y = bottom + plot_height * fraction
            NSColor.separatorColor().colorWithAlphaComponent_(0.22).setFill()
            NSBezierPath.bezierPathWithRect_(((left, y), (plot_width, 0.5))).fill()
            value = scale_max * fraction
            label = NSString.stringWithString_(
                _format_usage_axis_value(value, metric)
            )
            label_size = label.sizeWithAttributes_(label_attrs)
            label.drawAtPoint_withAttributes_(
                (left - label_size.width - 7.0, y - label_size.height / 2.0),
                label_attrs,
            )

        if not series:
            empty = NSString.stringWithString_("No activity in this range")
            empty_size = empty.sizeWithAttributes_(label_attrs)
            empty.drawAtPoint_withAttributes_(
                (
                    left + (plot_width - empty_size.width) / 2.0,
                    bottom + (plot_height - empty_size.height) / 2.0,
                ),
                label_attrs,
            )
            return

        count = max(
            (len(tuple(provider_series.get("values") or ())) for provider_series in series),
            default=0,
        )
        if count <= 0:
            return
        step = plot_width / max(1, count - 1)
        for provider_series in series:
            provider_id = str(provider_series.get("provider_id") or "")
            values = tuple(provider_series.get("values") or ())
            if not values:
                continue
            color = nscolor_from_hex(
                self.PROVIDER_COLORS.get(provider_id, "#8B93A7")
            )
            line = NSBezierPath.bezierPath()
            area = NSBezierPath.bezierPath()
            for index, raw_value in enumerate(values):
                x = left + step * index
                y = bottom + plot_height * min(
                    1.0,
                    max(0.0, float(raw_value)) / scale_max,
                )
                if index == 0:
                    line.moveToPoint_((x, y))
                    area.moveToPoint_((x, bottom))
                    area.lineToPoint_((x, y))
                else:
                    line.lineToPoint_((x, y))
                    area.lineToPoint_((x, y))
            area.lineToPoint_((left + step * (len(values) - 1), bottom))
            area.closePath()
            color.colorWithAlphaComponent_(0.09).setFill()
            area.fill()
            color.colorWithAlphaComponent_(0.92).setStroke()
            line.setLineWidth_(2.0)
            line.stroke()

        for index, label_text in enumerate(labels):
            if not label_text or index >= count:
                continue
            label = NSString.stringWithString_(str(label_text))
            size = label.sizeWithAttributes_(label_attrs)
            x = left + step * index - size.width / 2.0
            label.drawAtPoint_withAttributes_(
                (min(max(left, x), width - right - size.width), 3.0),
                label_attrs,
            )


def _format_usage_axis_value(value: float, metric: str) -> str:
    if metric == "cost":
        return f"${value:,.0f}" if value >= 1.0 else f"${value:.2f}"
    if metric == "sessions":
        return f"{value:,.0f}"
    return usage_stats.compact_token_count(round(value))


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


# Motion names say what you see; the parenthetical glosses moved into
# the descriptions where they belong. "Pulse"/"Roll" were DSL primitive
# names leaking into the UI -- and the app already admitted the real
# words in its own parentheses.
ANIMATION_STYLE_DISPLAY_LABELS: dict[str, str] = {
    "pulse": "Breathe",
    "roll": "Chase",
    "solid": "Steady",
    "blink": "Blink",
}

ANIMATION_STYLE_DESCRIPTIONS: dict[str, str] = {
    "pulse": "Fades up and down, softly.",
    "roll": "A bright point runs along the strip and starts again.",
    "solid": "Stays lit. Never moves.",
    "blink": "Snaps on and off. No fading.",
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
    # Name the terminal explicitly. A bare `open` hands the .command file
    # to whatever app owns that file type -- on any Mac where Ghostty,
    # iTerm2 or a text editor claims it, the script is opened rather than
    # RUN, while the Setup window cheerfully reports success. Silent
    # no-op in the one flow that makes overnight agent runs work.
    launcher = [str(trusted_system_tool("open"))]
    terminal = _installed_terminal_application()
    if terminal is not None:
        launcher += ["-a", str(terminal)]
    subprocess.Popen(
        [*launcher, str(script_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return script_path


# Terminals that can actually RUN a .command script, most standard first.
# Terminal.app is last-resort-proof: it ships with macOS.
_TERMINAL_APPLICATION_PATHS: tuple[str, ...] = (
    "/System/Applications/Utilities/Terminal.app",
    "/Applications/Utilities/Terminal.app",
    "/Applications/iTerm.app",
    "/Applications/Ghostty.app",
    "/Applications/Warp.app",
    "/Applications/kitty.app",
    "/Applications/Alacritty.app",
)


def _installed_terminal_application() -> Path | None:
    """The first real terminal present, or None to fall back to `open`."""
    for candidate in _TERMINAL_APPLICATION_PATHS:
        path = Path(candidate)
        try:
            if path.is_dir():
                return path
        except OSError:
            continue
    return None


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


def build_worker_rollup_item(
    children,
    collected_at,
    target,
    dot_color,
    *,
    max_visible: int | None = None,
):
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
    visible = ordered if max_visible is None else ordered[: max(0, max_visible)]
    for child in visible:
        submenu.addItem_(
            build_session_menu_item(
                child, collected_at, target, identity_color=dot_color
            )
        )
    overflow_count = len(ordered) - len(visible)
    if overflow_count:
        submenu.addItem_(disabled_menu_item(f"{overflow_count} more"))
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
        if status.event_name == "SessionEnd":
            # The user closed that terminal themselves -- not news.
            continue
        if not is_recent(
            snapshot.collected_at,
            status.updated_at,
            COMPLETED_VISIBLE_SECONDS,
        ):
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
    finished_rows = [
        status
        for status in getattr(snapshot, "stale_statuses", ())
        if not status.is_subagent
        and status.agent_id not in seen_ids
        and status.mode == AgentMode.COMPLETED
        # Stop = a turn finished (news). SessionEnd = the user closed
        # that terminal (61 of those were badging as "new" at once).
        and status.event_name != "SessionEnd"
        and is_recent(
            snapshot.collected_at,
            status.updated_at,
            COMPLETED_VISIBLE_SECONDS,
        )
    ]
    finished_rows.sort(key=lambda status: -status.updated_at.timestamp())
    statuses.extend(finished_rows[:3])
    if not statuses:
        statuses = [
            status
            for status in snapshot.stale_statuses
            if not status.is_subagent
            and bounded_age_seconds(snapshot.collected_at, status.updated_at)
            != float("inf")
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


def ask_statuses(projection, settings=None) -> list[AgentStatus]:
    """Source rows for the projection's proven actionable requests."""
    if not isinstance(projection, AttentionProjection):
        projection = project_attention(
            projection,
            settings or AgentMonitorSettings(),
        )
    return [row.source_status for row in projection.actionable_attention]


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


def frontmost_terminal_bundle_identifier() -> str | None:
    """Return the exact frontmost application identity, if AppKit exposes one."""
    try:
        application = NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle_identifier = application.bundleIdentifier() if application is not None else None
    except Exception:
        return None
    if type(bundle_identifier) is not str:
        return None
    value = bundle_identifier.strip()
    return value or None


def open_terminal_command(
    command: str,
    *,
    terminal_bundle_identifier: str | None = None,
) -> TerminalLaunchPlan:
    requested_bundle_identifier = (
        terminal_bundle_identifier
        if terminal_bundle_identifier is not None
        else frontmost_terminal_bundle_identifier()
    )
    plan = resolve_terminal_launch(requested_bundle_identifier)
    subprocess.Popen(
        terminal_launch_arguments(plan, command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if plan.fallback_copy is not None:
        log_status_bar(plan.fallback_copy)
    return plan


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
    OPERATOR_HISTORY_FIELD_MANIFEST,
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
    _build_history_pane,
    _build_led_behavior_pane,
    _build_lid_animations_pane,
    _build_lid_preset_row,
    _build_power_pane,
    _build_profile_pane,
    _build_settings_pane,
    _mini_led_view,
    _mode_animation_thumb_program,
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
