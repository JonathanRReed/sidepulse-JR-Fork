from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .battery import DEFAULT_POWER_CHANGE_PREVIEW_SECONDS
from .colors import ColorSettings
from .led_status import DEFAULT_CHANNEL_GAIN, normalize_channel_gain
from .session_actions import SESSION_OPEN_CHOICES


LED_DISPLAY_AGENT = "agent"
LED_DISPLAY_BATTERY = "battery"
LED_DISPLAY_CHOICES = (LED_DISPLAY_AGENT, LED_DISPLAY_BATTERY)
# Notification blink defaults: each app's own brand color, so the blink
# says WHICH app without reading anything.
NOTIFICATION_APP_IMESSAGE = "com.apple.MobileSMS"
NOTIFICATION_APP_WHATSAPP = "net.whatsapp.WhatsApp"
NOTIFICATION_APP_TELEGRAM = "ru.keepcoder.Telegram"
DEFAULT_NOTIFICATION_APP_COLORS: dict[str, str] = {
    NOTIFICATION_APP_IMESSAGE: "#34C759",
    NOTIFICATION_APP_WHATSAPP: "#25D366",
    NOTIFICATION_APP_TELEGRAM: "#2AABEE",
}
ALCOVE_COMPAT_AUTO = "auto"
ALCOVE_COMPAT_ALWAYS = "always"
ALCOVE_COMPAT_NEVER = "never"
ALCOVE_COMPAT_CHOICES = (ALCOVE_COMPAT_AUTO, ALCOVE_COMPAT_ALWAYS, ALCOVE_COMPAT_NEVER)
CLOSED_LID_AWAKE_NEVER = "never"
CLOSED_LID_AWAKE_AGENTS = "agents"
CLOSED_LID_AWAKE_ALWAYS = "always"
CLOSED_LID_AWAKE_CHOICES = (
    CLOSED_LID_AWAKE_NEVER,
    CLOSED_LID_AWAKE_AGENTS,
    CLOSED_LID_AWAKE_ALWAYS,
)
LID_ANIMATION_CLOSED = "closed"
LID_ANIMATION_OPEN = "open"
LID_ANIMATION_CHOICES = (LID_ANIMATION_CLOSED, LID_ANIMATION_OPEN)

DEFAULT_IDLE_DIM_AFTER_MINUTES = 10.0
MIN_IDLE_DIM_AFTER_MINUTES = 1.0
MAX_IDLE_DIM_AFTER_MINUTES = 180.0
DEFAULT_IDLE_DIM_FRACTION = 0.3
MIN_IDLE_DIM_FRACTION = 0.05
MAX_IDLE_DIM_FRACTION = 1.0

# Matches keep_awake.AWAKE_GRACE_SECONDS's own long-standing default (300s)
# exactly, so making this adjustable doesn't change anyone's existing
# behavior until they actually touch the setting.
DEFAULT_CLOSED_LID_GRACE_MINUTES = 5.0
MIN_CLOSED_LID_GRACE_MINUTES = 0.0
MAX_CLOSED_LID_GRACE_MINUTES = 60.0
DEFAULT_LID_CLOSED_ANIMATION_PROGRAM = "\n".join(
    [
        "off 90ms cosine",
        (
            "0:#FF7A00 180ms ease; 7:#FF7A00 180ms ease; "
            "1:#FF7A00 180ms ease 80ms; 6:#FF7A00 180ms ease 80ms"
        ),
        (
            "2:#FF4A00 180ms ease; 5:#FF4A00 180ms ease; "
            "3:#FF3000 180ms ease 80ms; 4:#FF3000 180ms ease 80ms"
        ),
        "off 360ms ease-out",
    ]
)
DEFAULT_LID_OPEN_ANIMATION_PROGRAM = "\n".join(
    [
        "off 90ms cosine",
        (
            "3:#00E5FF 180ms ease; 4:#00E5FF 180ms ease; "
            "2:#00E5FF 180ms ease 80ms; 5:#00E5FF 180ms ease 80ms"
        ),
        (
            "1:#00FFB0 180ms ease; 6:#00FFB0 180ms ease; "
            "0:#00FF66 180ms ease 80ms; 7:#00FF66 180ms ease 80ms"
        ),
        "#00FF66 220ms ease",
        "off 320ms ease-out",
    ]
)
DEFAULT_LID_CLOSED_ANIMATION_SECONDS = 0.9
DEFAULT_LID_OPEN_ANIMATION_SECONDS = 1.0


@dataclass(frozen=True)
class LedAnimationSetting:
    program: str
    duration_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "program": self.program,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class DeviceDisplaySetting:
    device_id: str
    name: str
    path: str
    led_display: str = LED_DISPLAY_AGENT
    brightness: int = 255
    # When on, `brightness` above stays as the manual fallback/baseline, but
    # actual LED writes use a brightness derived from the current screen
    # brightness instead (see display_brightness.py) -- dim to match a dark
    # room, brighten in daylight. Off by default: it depends on an
    # undocumented technique, so it's opt-in rather than silently changing
    # existing brightness behavior.
    auto_brightness_enabled: bool = False
    # Per-channel gain correction for this specific physical device's own
    # LED die response (e.g. an over-bright green die making blues read
    # greenish) -- applied only when writing to this device, never to the
    # "true" hex shown in the Colors window or the Screen Bar preview. All
    # default to 1.0 (no correction) so an uncalibrated device behaves
    # exactly as before this existed. See led_status.apply_channel_gain_to_program.
    red_gain: float = DEFAULT_CHANNEL_GAIN
    green_gain: float = DEFAULT_CHANNEL_GAIN
    blue_gain: float = DEFAULT_CHANNEL_GAIN

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.device_id,
            "name": self.name,
            "path": self.path,
            "led_display": self.led_display,
            "brightness": self.brightness,
            "auto_brightness_enabled": self.auto_brightness_enabled,
            "red_gain": self.red_gain,
            "green_gain": self.green_gain,
            "blue_gain": self.blue_gain,
        }

    def channel_gains(self) -> tuple[float, float, float]:
        return (self.red_gain, self.green_gain, self.blue_gain)


@dataclass(frozen=True)
class AgentMonitorSettings:
    codex_transcripts_enabled: bool = False
    claude_transcripts_enabled: bool = False
    led_display: str = LED_DISPLAY_AGENT
    devices: tuple[DeviceDisplaySetting, ...] = ()
    virtual_status_device_enabled: bool = False
    closed_lid_awake_policy: str = CLOSED_LID_AWAKE_NEVER
    closed_lid_system_override_enabled: bool = False
    # How long (minutes) to keep holding the lid-closed awake state after
    # agent activity *looks* like it stopped, before actually letting the
    # policy release -- a buffer against a false "done" reading (e.g. a
    # command still running with no events emitted for a stretch) closing
    # the lid into sleep and losing the agent's work. See
    # keep_awake.KeepAwakeController.should_hold_for_mode.
    closed_lid_grace_minutes: float = DEFAULT_CLOSED_LID_GRACE_MINUTES
    lid_closed_animation: LedAnimationSetting = field(
        default_factory=lambda: default_lid_animation(LID_ANIMATION_CLOSED)
    )
    lid_open_animation: LedAnimationSetting = field(
        default_factory=lambda: default_lid_animation(LID_ANIMATION_OPEN)
    )
    battery_full_charge_watts: float | None = None
    battery_show_on_power_change: bool = True
    battery_power_change_preview_seconds: float = DEFAULT_POWER_CHANGE_PREVIEW_SECONDS
    # Below this percent while unplugged, every display switches to the
    # calm slow-red "plug me in" breathe (led_status.low_battery_program)
    # until power returns or the level recovers. Default on: a dying
    # battery is the one signal that should outrank agent status.
    low_battery_alert_enabled: bool = True
    low_battery_threshold_percent: float = 5.0
    # Blink the LEDs briefly in an app's own color when it delivers a
    # notification, then return to agent status. Reads the Notification
    # Center store, so it needs Full Disk Access (the same grant the
    # Focus rules use) and stays silently inert without it.
    notification_blinks_enabled: bool = True
    notification_app_colors: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_NOTIFICATION_APP_COLORS)
    )
    # Calm purple glow starting this many minutes before a calendar
    # event. Off by default: enabling it presents the system Calendars
    # permission prompt (see calendar_watch.py).
    calendar_alerts_enabled: bool = False
    calendar_lead_minutes: float = 5.0
    # Amber glow when a Reminder comes due. Off by default: enabling it
    # presents the system Reminders prompt (see reminders_watch.py).
    reminder_alerts_enabled: bool = False
    # Per-signal look overrides (Signal Engine). Keys/values validated
    # by signals.SignalStyle; absent keys mean the built-in defaults.
    signal_styles: dict[str, dict] = field(default_factory=dict)
    # Ask escalation: how loud an ignored "agent needs you" may get
    # (the tier is a ceiling) and when each stage kicks in.
    escalation_tier: str = "menu_bar"
    escalation_ramp_seconds: float = 30.0
    escalation_menu_bar_seconds: float = 120.0
    escalation_final_seconds: float = 300.0
    session_open_preferences: dict[str, str] = field(default_factory=dict)
    setup_screen_completed: bool = False
    colors: ColorSettings = field(default_factory=ColorSettings.defaults)
    alcove_compatibility_mode: str = ALCOVE_COMPAT_AUTO
    # Extends the Screen Bar's glow beyond the notch's own width, reaching
    # toward the menu bar's edges on both sides -- an opt-in look (default
    # off) since how much room is actually safe to use depends on how
    # cluttered the user's own menu bar is (see virtual_device.wing_width_
    # for_screen, which measures the real per-user safe area rather than
    # assuming a fixed amount).
    virtual_status_device_wraps_menu_bar: bool = False
    # Manual Screen Bar geometry, both None = Automatic. Jonathan's ask,
    # and the durable answer to notch-adjacent apps (Alcove) whose visual
    # width changes at runtime, plus future Macs with different notches:
    # the gap is the treated-as-notch span between the two risers; the
    # wing length is each horizontal stroke's reach beyond the gap.
    screen_bar_gap_width: float | None = None
    screen_bar_wing_length: float | None = None
    # After this many continuous minutes with nothing active (idle), LED
    # brightness scales down by idle_dim_fraction -- a long-idle Mac
    # shouldn't keep a bright light going on the desk. Default on: dimming
    # further while genuinely idle is a straightforward improvement with
    # no real downside, unlike a change that could surprise someone
    # mid-work.
    idle_dim_enabled: bool = True
    idle_dim_after_minutes: float = DEFAULT_IDLE_DIM_AFTER_MINUTES
    idle_dim_fraction: float = DEFAULT_IDLE_DIM_FRACTION
    # Dims/quiets the LEDs while a macOS Focus (Do Not Disturb, Work,
    # Sleep, etc.) is active, matching the same "don't be a nagging light"
    # spirit as idle dimming. Off by default -- detecting the active Focus
    # requires reading a TCC-protected file the user must grant Full Disk
    # Access to (see focus_sync.py); silently defaulting this on would
    # look "broken" (no visible effect) for anyone who hasn't done that.
    focus_sync_enabled: bool = False
    # Per-Focus dim rules, keyed by the Focus mode identifier (e.g.
    # "com.apple.donotdisturb.mode.default"): 1.0 = don't dim, 0.0 = LEDs
    # fully off while that Focus is active. A Focus with no rule falls
    # back to idle_dim_fraction, the pre-per-Focus behavior. Only
    # meaningful while focus_sync_enabled is on.
    focus_dim_rules: dict[str, float] = field(default_factory=dict)

    def transcript_enabled(self, provider: str) -> bool:
        if provider == "codex":
            return self.codex_transcripts_enabled
        if provider == "claude":
            return self.claude_transcripts_enabled
        return False

    def with_transcript_provider(self, provider: str, enabled: bool) -> "AgentMonitorSettings":
        if provider == "codex":
            return replace(self, codex_transcripts_enabled=enabled)
        if provider == "claude":
            return replace(self, claude_transcripts_enabled=enabled)
        raise ValueError(f"Unknown transcript provider: {provider}")

    def with_led_display(self, display: str) -> "AgentMonitorSettings":
        if display not in LED_DISPLAY_CHOICES:
            raise ValueError(f"Unknown LED display: {display}")
        return replace(self, led_display=display)

    def display_for_device(self, device_id: str) -> str:
        for device in self.devices:
            if device.device_id == device_id:
                return device.led_display
        return self.led_display

    def brightness_for_device(self, device_id: str) -> int:
        for device in self.devices:
            if device.device_id == device_id:
                return normalize_brightness(device.brightness)
        return 255

    def with_device_display(
        self,
        device_id: str,
        display: str,
        *,
        name: str | None = None,
        path: str | None = None,
    ) -> "AgentMonitorSettings":
        if display not in LED_DISPLAY_CHOICES:
            raise ValueError(f"Unknown LED display: {display}")

        devices: list[DeviceDisplaySetting] = []
        updated = False
        for device in self.devices:
            if device.device_id == device_id:
                # replace(device, ...) carries over every field not
                # explicitly overridden (e.g. auto_brightness_enabled) --
                # rebuilding from scratch here previously dropped any field
                # added after this method was first written.
                devices.append(
                    replace(
                        device,
                        name=name or device.name,
                        path=path or device.path,
                        led_display=display,
                    )
                )
                updated = True
            else:
                devices.append(device)
        if not updated:
            devices.append(
                DeviceDisplaySetting(
                    device_id=device_id,
                    name=name or device_id,
                    path=path or device_id,
                    led_display=display,
                    brightness=self.brightness_for_device(device_id),
                )
            )
        return replace(self, devices=tuple(devices))

    def with_device_brightness(
        self,
        device_id: str,
        brightness: int | float,
        *,
        name: str | None = None,
        path: str | None = None,
    ) -> "AgentMonitorSettings":
        value = normalize_brightness(brightness)
        devices: list[DeviceDisplaySetting] = []
        updated = False
        for device in self.devices:
            if device.device_id == device_id:
                devices.append(
                    replace(
                        device,
                        name=name or device.name,
                        path=path or device.path,
                        brightness=value,
                    )
                )
                updated = True
            else:
                devices.append(device)
        if not updated:
            devices.append(
                DeviceDisplaySetting(
                    device_id=device_id,
                    name=name or device_id,
                    path=path or device_id,
                    led_display=self.display_for_device(device_id),
                    brightness=value,
                )
            )
        return replace(self, devices=tuple(devices))

    def auto_brightness_enabled_for_device(self, device_id: str) -> bool:
        for device in self.devices:
            if device.device_id == device_id:
                return device.auto_brightness_enabled
        return False

    def with_device_auto_brightness(
        self,
        device_id: str,
        enabled: bool,
        *,
        name: str | None = None,
        path: str | None = None,
    ) -> "AgentMonitorSettings":
        devices: list[DeviceDisplaySetting] = []
        updated = False
        for device in self.devices:
            if device.device_id == device_id:
                devices.append(
                    replace(
                        device,
                        name=name or device.name,
                        path=path or device.path,
                        auto_brightness_enabled=bool(enabled),
                    )
                )
                updated = True
            else:
                devices.append(device)
        if not updated:
            devices.append(
                DeviceDisplaySetting(
                    device_id=device_id,
                    name=name or device_id,
                    path=path or device_id,
                    led_display=self.display_for_device(device_id),
                    brightness=self.brightness_for_device(device_id),
                    auto_brightness_enabled=bool(enabled),
                )
            )
        return replace(self, devices=tuple(devices))

    def channel_gains_for_device(self, device_id: str) -> tuple[float, float, float]:
        for device in self.devices:
            if device.device_id == device_id:
                return device.channel_gains()
        return (DEFAULT_CHANNEL_GAIN, DEFAULT_CHANNEL_GAIN, DEFAULT_CHANNEL_GAIN)

    def with_device_channel_gain(
        self,
        device_id: str,
        channel: str,
        value: float,
        *,
        name: str | None = None,
        path: str | None = None,
    ) -> "AgentMonitorSettings":
        """Sets one of "red"/"green"/"blue" gain for a device, leaving the
        other two untouched. Three separate calls (one per slider) rather
        than one method taking all three, since each slider in the menu
        fires its own action independently."""
        field_name = {"red": "red_gain", "green": "green_gain", "blue": "blue_gain"}.get(channel)
        if field_name is None:
            raise ValueError(f"Unknown channel: {channel}")
        gain = normalize_channel_gain(value)
        devices: list[DeviceDisplaySetting] = []
        updated = False
        for device in self.devices:
            if device.device_id == device_id:
                devices.append(
                    replace(
                        device,
                        name=name or device.name,
                        path=path or device.path,
                        **{field_name: gain},
                    )
                )
                updated = True
            else:
                devices.append(device)
        if not updated:
            devices.append(
                DeviceDisplaySetting(
                    device_id=device_id,
                    name=name or device_id,
                    path=path or device_id,
                    led_display=self.display_for_device(device_id),
                    brightness=self.brightness_for_device(device_id),
                    auto_brightness_enabled=self.auto_brightness_enabled_for_device(device_id),
                    **{field_name: gain},
                )
            )
        return replace(self, devices=tuple(devices))

    def with_device_channel_gains_reset(self, device_id: str) -> "AgentMonitorSettings":
        """Back to (1.0, 1.0, 1.0) -- a no-op correction, i.e. write the
        true hex unmodified."""
        devices = tuple(
            replace(device, red_gain=DEFAULT_CHANNEL_GAIN, green_gain=DEFAULT_CHANNEL_GAIN, blue_gain=DEFAULT_CHANNEL_GAIN)
            if device.device_id == device_id
            else device
            for device in self.devices
        )
        return replace(self, devices=devices)

    def with_remembered_device(
        self,
        *,
        device_id: str,
        name: str,
        path: str,
    ) -> "AgentMonitorSettings":
        return self.with_device_display(
            device_id,
            self.display_for_device(device_id),
            name=name,
            path=path,
        )

    def without_device(self, device_id: str) -> "AgentMonitorSettings":
        devices = tuple(device for device in self.devices if device.device_id != device_id)
        if devices == self.devices:
            return self
        return replace(self, devices=devices)

    def session_open_action(self, provider: str, origin: str | None = None) -> str | None:
        if origin:
            action = self.session_open_preferences.get(
                session_open_preference_key(provider, origin)
            )
            if action in SESSION_OPEN_CHOICES:
                return action
            action = self.session_open_preferences.get(
                f"origin:{normalize_session_origin_key(origin)}"
            )
            if action in SESSION_OPEN_CHOICES:
                return action

        action = self.session_open_preferences.get(provider.lower())
        if action in SESSION_OPEN_CHOICES:
            return action
        return None

    def with_session_open_action(
        self,
        provider: str,
        action: str,
        origin: str | None = None,
    ) -> "AgentMonitorSettings":
        if action not in SESSION_OPEN_CHOICES:
            raise ValueError(f"Unknown session open action: {action}")
        key = session_open_preference_key(provider, origin)
        preferences = dict(self.session_open_preferences)
        preferences[key] = action
        return replace(self, session_open_preferences=preferences)

    def with_provider_session_open_action(
        self, provider: str, action: str
    ) -> "AgentMonitorSettings":
        """Set the provider-wide opener and discard older per-origin overrides."""
        if action not in SESSION_OPEN_CHOICES:
            raise ValueError(f"Unknown session open action: {action}")
        provider_key = provider.lower()
        prefix = f"origin:{provider_key}:"
        preferences = {
            key: value
            for key, value in self.session_open_preferences.items()
            if not key.startswith(prefix)
        }
        preferences[provider_key] = action
        return replace(self, session_open_preferences=preferences)

    def with_battery_full_charge_watts(self, watts: float | None) -> "AgentMonitorSettings":
        if watts is not None and watts <= 0:
            watts = None
        return replace(self, battery_full_charge_watts=watts)

    def with_battery_power_change_preview(
        self,
        *,
        enabled: bool | None = None,
        seconds: float | None = None,
    ) -> "AgentMonitorSettings":
        preview_seconds = self.battery_power_change_preview_seconds
        if seconds is not None:
            preview_seconds = max(0.0, float(seconds))
        return replace(
            self,
            battery_show_on_power_change=(
                self.battery_show_on_power_change if enabled is None else enabled
            ),
            battery_power_change_preview_seconds=preview_seconds,
        )

    def lid_animation(self, kind: str) -> LedAnimationSetting:
        if kind == LID_ANIMATION_CLOSED:
            return self.lid_closed_animation
        if kind == LID_ANIMATION_OPEN:
            return self.lid_open_animation
        raise ValueError(f"Unknown lid animation: {kind}")

    def with_closed_lid_awake_policy(self, policy: str) -> "AgentMonitorSettings":
        if policy not in CLOSED_LID_AWAKE_CHOICES:
            raise ValueError(f"Unknown closed-lid awake policy: {policy}")
        return replace(self, closed_lid_awake_policy=policy)

    def with_closed_lid_system_override(self, enabled: bool) -> "AgentMonitorSettings":
        return replace(self, closed_lid_system_override_enabled=bool(enabled))

    def with_closed_lid_grace_minutes(self, minutes: float) -> "AgentMonitorSettings":
        return replace(self, closed_lid_grace_minutes=normalize_closed_lid_grace_minutes(minutes))

    def with_lid_animation(
        self,
        kind: str,
        *,
        program: str,
        duration_seconds: float,
    ) -> "AgentMonitorSettings":
        animation = LedAnimationSetting(
            program=program,
            duration_seconds=normalize_animation_duration(duration_seconds),
        )
        if kind == LID_ANIMATION_CLOSED:
            return replace(self, lid_closed_animation=animation)
        if kind == LID_ANIMATION_OPEN:
            return replace(self, lid_open_animation=animation)
        raise ValueError(f"Unknown lid animation: {kind}")

    def with_setup_screen_completed(self, completed: bool = True) -> "AgentMonitorSettings":
        return replace(self, setup_screen_completed=bool(completed))

    def with_virtual_status_device(self, enabled: bool) -> "AgentMonitorSettings":
        return replace(self, virtual_status_device_enabled=bool(enabled))

    def with_virtual_status_device_wraps_menu_bar(self, enabled: bool) -> "AgentMonitorSettings":
        return replace(self, virtual_status_device_wraps_menu_bar=bool(enabled))

    def with_colors(self, colors: ColorSettings) -> "AgentMonitorSettings":
        return replace(self, colors=colors)

    def with_alcove_compatibility_mode(self, mode: str) -> "AgentMonitorSettings":
        if mode not in ALCOVE_COMPAT_CHOICES:
            raise ValueError(f"Unknown Alcove compatibility mode: {mode}")
        return replace(self, alcove_compatibility_mode=mode)

    def with_idle_dim_enabled(self, enabled: bool) -> "AgentMonitorSettings":
        return replace(self, idle_dim_enabled=bool(enabled))

    def with_idle_dim_after_minutes(self, minutes: float) -> "AgentMonitorSettings":
        return replace(self, idle_dim_after_minutes=normalize_idle_dim_after_minutes(minutes))

    def with_idle_dim_fraction(self, fraction: float) -> "AgentMonitorSettings":
        return replace(self, idle_dim_fraction=normalize_idle_dim_fraction(fraction))

    def with_focus_sync_enabled(self, enabled: bool) -> "AgentMonitorSettings":
        return replace(self, focus_sync_enabled=bool(enabled))

    def with_screen_bar_gap_width(self, width: float | None) -> "AgentMonitorSettings":
        if width is not None:
            width = max(120.0, min(1200.0, float(width)))
        return replace(self, screen_bar_gap_width=width)

    def with_screen_bar_wing_length(self, length: float | None) -> "AgentMonitorSettings":
        if length is not None:
            length = max(0.0, min(400.0, float(length)))
        return replace(self, screen_bar_wing_length=length)

    def with_low_battery_alert_enabled(self, enabled: bool) -> "AgentMonitorSettings":
        return replace(self, low_battery_alert_enabled=bool(enabled))

    def with_low_battery_threshold_percent(self, percent: float) -> "AgentMonitorSettings":
        return replace(self, low_battery_threshold_percent=max(1.0, min(50.0, float(percent))))

    def with_notification_blinks_enabled(self, enabled: bool) -> "AgentMonitorSettings":
        return replace(self, notification_blinks_enabled=bool(enabled))

    def with_calendar_alerts_enabled(self, enabled: bool) -> "AgentMonitorSettings":
        return replace(self, calendar_alerts_enabled=bool(enabled))

    def with_reminder_alerts_enabled(self, enabled: bool) -> "AgentMonitorSettings":
        return replace(self, reminder_alerts_enabled=bool(enabled))

    def with_calendar_lead_minutes(self, minutes: float) -> "AgentMonitorSettings":
        return replace(self, calendar_lead_minutes=max(1.0, min(60.0, float(minutes))))

    def with_escalation_tier(self, tier: str) -> "AgentMonitorSettings":
        from .signals import ESCALATION_TIERS

        if tier not in ESCALATION_TIERS:
            raise ValueError(f"Unknown escalation tier: {tier}")
        return replace(self, escalation_tier=tier)

    def with_escalation_thresholds(
        self,
        ramp_seconds: float | None = None,
        menu_bar_seconds: float | None = None,
        final_seconds: float | None = None,
    ) -> "AgentMonitorSettings":
        def clamp(value, low, high):
            return max(low, min(high, float(value)))

        ramp = clamp(ramp_seconds, 5.0, 600.0) if ramp_seconds is not None else self.escalation_ramp_seconds
        menu = clamp(menu_bar_seconds, ramp, 1800.0) if menu_bar_seconds is not None else max(
            self.escalation_menu_bar_seconds, ramp
        )
        final = clamp(final_seconds, menu, 3600.0) if final_seconds is not None else max(
            self.escalation_final_seconds, menu
        )
        return replace(
            self,
            escalation_ramp_seconds=ramp,
            escalation_menu_bar_seconds=menu,
            escalation_final_seconds=final,
        )

    def signal_style(self, key: str):
        """The effective SignalStyle for a signal: the user's override
        merged over the built-in default."""
        from .signals import DEFAULT_SIGNAL_STYLES, SignalStyle

        fallback = DEFAULT_SIGNAL_STYLES[key]
        return SignalStyle.from_dict(self.signal_styles.get(key), fallback)

    def with_signal_style(self, key: str, style) -> "AgentMonitorSettings":
        from .signals import DEFAULT_SIGNAL_STYLES

        if key not in DEFAULT_SIGNAL_STYLES:
            raise ValueError(f"Unknown signal: {key}")
        styles = dict(self.signal_styles)
        styles[key] = style.normalized().to_dict()
        return replace(self, signal_styles=styles)

    def with_notification_app_color(self, bundle_id: str, color: str | None) -> "AgentMonitorSettings":
        """color=None removes the app from the blink list entirely."""
        apps = dict(self.notification_app_colors)
        if color is None:
            apps.pop(bundle_id, None)
        else:
            normalized = _hex_color(color)
            if normalized is not None:
                apps[bundle_id] = normalized
        return replace(self, notification_app_colors=apps)

    def focus_dim_fraction(self, mode_identifier: str) -> float:
        """The brightness fraction to apply while this Focus is active --
        its own rule if set, otherwise the shared idle-dim amount (the
        pre-per-Focus behavior)."""
        rule = self.focus_dim_rules.get(mode_identifier)
        if rule is None:
            return self.idle_dim_fraction
        return max(0.0, min(1.0, float(rule)))

    def with_focus_dim_rule(self, mode_identifier: str, fraction: float | None) -> "AgentMonitorSettings":
        """fraction=None removes the rule (back to the shared default)."""
        rules = dict(self.focus_dim_rules)
        if fraction is None:
            rules.pop(mode_identifier, None)
        else:
            rules[mode_identifier] = max(0.0, min(1.0, float(fraction)))
        return replace(self, focus_dim_rules=rules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "led_display": self.led_display,
            "devices": [device.to_dict() for device in self.devices],
            "virtual_status_device_enabled": self.virtual_status_device_enabled,
            "virtual_status_device_wraps_menu_bar": self.virtual_status_device_wraps_menu_bar,
            "screen_bar_gap_width": self.screen_bar_gap_width,
            "screen_bar_wing_length": self.screen_bar_wing_length,
            "closed_lid_awake_policy": self.closed_lid_awake_policy,
            "closed_lid_system_override_enabled": self.closed_lid_system_override_enabled,
            "closed_lid_grace_minutes": self.closed_lid_grace_minutes,
            "lid_closed_animation": self.lid_closed_animation.to_dict(),
            "lid_open_animation": self.lid_open_animation.to_dict(),
            "transcript_monitoring": {
                "codex": self.codex_transcripts_enabled,
                "claude": self.claude_transcripts_enabled,
            },
            "battery_monitoring": {
                "full_charge_watts": self.battery_full_charge_watts,
                "show_on_power_change": self.battery_show_on_power_change,
                "power_change_preview_seconds": self.battery_power_change_preview_seconds,
                "low_battery_alert_enabled": self.low_battery_alert_enabled,
                "low_battery_threshold_percent": self.low_battery_threshold_percent,
            },
            "notification_blinks_enabled": self.notification_blinks_enabled,
            "notification_app_colors": dict(sorted(self.notification_app_colors.items())),
            "calendar_alerts_enabled": self.calendar_alerts_enabled,
            "calendar_lead_minutes": self.calendar_lead_minutes,
            "reminder_alerts_enabled": self.reminder_alerts_enabled,
            "signal_styles": dict(sorted(self.signal_styles.items())),
            "escalation_tier": self.escalation_tier,
            "escalation_ramp_seconds": self.escalation_ramp_seconds,
            "escalation_menu_bar_seconds": self.escalation_menu_bar_seconds,
            "escalation_final_seconds": self.escalation_final_seconds,
            "session_open_preferences": dict(sorted(self.session_open_preferences.items())),
            "setup_screen_completed": self.setup_screen_completed,
            "colors": self.colors.to_dict(),
            "alcove_compatibility_mode": self.alcove_compatibility_mode,
            "idle_dim_enabled": self.idle_dim_enabled,
            "idle_dim_after_minutes": self.idle_dim_after_minutes,
            "idle_dim_fraction": self.idle_dim_fraction,
            "focus_sync_enabled": self.focus_sync_enabled,
            "focus_dim_rules": dict(sorted(self.focus_dim_rules.items())),
        }


def default_config_dir(home: Path | None = None) -> Path:
    if home is None:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config_home:
            return Path(xdg_config_home).expanduser() / "sidepulse" / "agent-monitor"

    base = home or Path.home()
    return base / ".config" / "sidepulse" / "agent-monitor"


def default_settings_path(home: Path | None = None) -> Path:
    return default_config_dir(home) / "settings.json"


def _hex_color(raw: object) -> str | None:
    """'#RRGGBB' (normalized upper-case) or None for anything else."""
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        return "#" + value.lstrip("#").upper()
    return None


def _escalation_tier(raw: object) -> str:
    from .signals import ESCALATION_TIERS

    if isinstance(raw, str) and raw in ESCALATION_TIERS:
        return raw
    return "menu_bar"


def _signal_styles(raw: object) -> dict[str, dict]:
    """Only known signals, each entry re-validated through SignalStyle
    so a hand-edited file can never smuggle a bad pattern or speed."""
    from .signals import DEFAULT_SIGNAL_STYLES, SignalStyle

    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict] = {}
    for key, value in raw.items():
        fallback = DEFAULT_SIGNAL_STYLES.get(key)
        if fallback is None:
            continue
        result[key] = SignalStyle.from_dict(value, fallback).to_dict()
    return result


def _notification_app_colors(raw: object) -> dict[str, str]:
    """Absent -> the defaults; present -> exactly what was saved (an
    empty dict is a legitimate "no apps" choice), each color validated."""
    if not isinstance(raw, dict):
        return dict(DEFAULT_NOTIFICATION_APP_COLORS)
    result: dict[str, str] = {}
    for bundle_id, color in raw.items():
        normalized = _hex_color(color)
        if isinstance(bundle_id, str) and bundle_id and normalized is not None:
            result[bundle_id] = normalized
    return result


def _optional_dimension(raw: object, minimum: float, maximum: float) -> float | None:
    """None means Automatic; a number is clamped to a sane range."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return max(minimum, min(maximum, float(raw)))
    return None


def _focus_dim_rules(raw: object) -> dict[str, float]:
    """Sanitizes the persisted per-Focus dim rules: string identifiers to
    0.0-1.0 fractions, anything malformed dropped rather than guessed at."""
    if not isinstance(raw, dict):
        return {}
    rules: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            rules[key] = max(0.0, min(1.0, float(value)))
    return rules


def load_settings(path: Path | None = None) -> AgentMonitorSettings:
    target = (path or default_settings_path()).expanduser()
    if not target.exists():
        return AgentMonitorSettings()

    try:
        data = json.loads(target.read_text())
    except Exception:
        return AgentMonitorSettings()

    if not isinstance(data, dict):
        return AgentMonitorSettings()

    transcript = data.get("transcript_monitoring")
    if not isinstance(transcript, dict):
        transcript = {}

    battery = data.get("battery_monitoring")
    if not isinstance(battery, dict):
        battery = {}

    led_display = _led_display_setting(data.get("led_display"), LED_DISPLAY_AGENT)
    return AgentMonitorSettings(
        codex_transcripts_enabled=_bool_setting(transcript.get("codex"), False),
        claude_transcripts_enabled=_bool_setting(transcript.get("claude"), False),
        led_display=led_display,
        devices=_device_display_settings(data.get("devices"), led_display),
        virtual_status_device_enabled=_bool_setting(
            data.get("virtual_status_device_enabled"), False
        ),
        virtual_status_device_wraps_menu_bar=_bool_setting(
            data.get("virtual_status_device_wraps_menu_bar"), False
        ),
        screen_bar_gap_width=_optional_dimension(data.get("screen_bar_gap_width"), 120.0, 1200.0),
        screen_bar_wing_length=_optional_dimension(data.get("screen_bar_wing_length"), 0.0, 400.0),
        closed_lid_awake_policy=_closed_lid_awake_policy(
            data.get("closed_lid_awake_policy"),
        ),
        closed_lid_system_override_enabled=_bool_setting(
            data.get("closed_lid_system_override_enabled"),
            False,
        ),
        closed_lid_grace_minutes=normalize_closed_lid_grace_minutes(
            data.get("closed_lid_grace_minutes"), default=DEFAULT_CLOSED_LID_GRACE_MINUTES
        ),
        lid_closed_animation=_lid_animation_setting(
            data.get("lid_closed_animation"),
            default_lid_animation(LID_ANIMATION_CLOSED),
        ),
        lid_open_animation=_lid_animation_setting(
            data.get("lid_open_animation"),
            default_lid_animation(LID_ANIMATION_OPEN),
        ),
        battery_full_charge_watts=_optional_float_setting(
            battery.get("full_charge_watts"),
        ),
        battery_show_on_power_change=_bool_setting(
            battery.get("show_on_power_change"),
            True,
        ),
        battery_power_change_preview_seconds=_float_setting(
            battery.get("power_change_preview_seconds"),
            DEFAULT_POWER_CHANGE_PREVIEW_SECONDS,
        ),
        low_battery_alert_enabled=_bool_setting(battery.get("low_battery_alert_enabled"), True),
        low_battery_threshold_percent=max(
            1.0, min(50.0, _float_setting(battery.get("low_battery_threshold_percent"), 5.0))
        ),
        notification_blinks_enabled=_bool_setting(data.get("notification_blinks_enabled"), True),
        notification_app_colors=_notification_app_colors(data.get("notification_app_colors")),
        calendar_alerts_enabled=_bool_setting(data.get("calendar_alerts_enabled"), False),
        calendar_lead_minutes=max(
            1.0, min(60.0, _float_setting(data.get("calendar_lead_minutes"), 5.0))
        ),
        reminder_alerts_enabled=_bool_setting(data.get("reminder_alerts_enabled"), False),
        signal_styles=_signal_styles(data.get("signal_styles")),
        escalation_tier=_escalation_tier(data.get("escalation_tier")),
        escalation_ramp_seconds=max(
            5.0, min(600.0, _float_setting(data.get("escalation_ramp_seconds"), 30.0))
        ),
        escalation_menu_bar_seconds=max(
            5.0, min(1800.0, _float_setting(data.get("escalation_menu_bar_seconds"), 120.0))
        ),
        escalation_final_seconds=max(
            5.0, min(3600.0, _float_setting(data.get("escalation_final_seconds"), 300.0))
        ),
        session_open_preferences=_session_open_preferences(data.get("session_open_preferences")),
        setup_screen_completed=_bool_setting(data.get("setup_screen_completed"), False),
        colors=ColorSettings.from_dict(data.get("colors")),
        alcove_compatibility_mode=_alcove_compatibility_mode_setting(
            data.get("alcove_compatibility_mode")
        ),
        idle_dim_enabled=_bool_setting(data.get("idle_dim_enabled"), True),
        idle_dim_after_minutes=normalize_idle_dim_after_minutes(
            data.get("idle_dim_after_minutes"), default=DEFAULT_IDLE_DIM_AFTER_MINUTES
        ),
        idle_dim_fraction=normalize_idle_dim_fraction(
            data.get("idle_dim_fraction"), default=DEFAULT_IDLE_DIM_FRACTION
        ),
        focus_sync_enabled=_bool_setting(data.get("focus_sync_enabled"), False),
        focus_dim_rules=_focus_dim_rules(data.get("focus_dim_rules")),
    )


def save_settings(
    settings: AgentMonitorSettings,
    path: Path | None = None,
) -> Path:
    target = (path or default_settings_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: a crash mid-write must never truncate the file -- a
    # truncated settings.json silently loads as ALL defaults, losing
    # every color, device, and rule. (Also written from the LED worker
    # thread via remember_connected_devices, so in-place truncation had
    # a real interleaving window, not just a power-loss one.)
    payload = json.dumps(settings.to_dict(), indent=2, sort_keys=True) + "\n"
    scratch = target.with_name(target.name + ".tmp")
    scratch.write_text(payload)
    os.replace(scratch, target)
    return target


def _bool_setting(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def normalize_idle_dim_after_minutes(
    value: object, *, default: float = DEFAULT_IDLE_DIM_AFTER_MINUTES
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default
    return max(MIN_IDLE_DIM_AFTER_MINUTES, min(MAX_IDLE_DIM_AFTER_MINUTES, float(value)))


def normalize_idle_dim_fraction(value: object, *, default: float = DEFAULT_IDLE_DIM_FRACTION) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default
    return max(MIN_IDLE_DIM_FRACTION, min(MAX_IDLE_DIM_FRACTION, float(value)))


def normalize_closed_lid_grace_minutes(
    value: object, *, default: float = DEFAULT_CLOSED_LID_GRACE_MINUTES
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default
    return max(MIN_CLOSED_LID_GRACE_MINUTES, min(MAX_CLOSED_LID_GRACE_MINUTES, float(value)))


def _led_display_setting(value: object, default: str) -> str:
    if isinstance(value, str) and value in LED_DISPLAY_CHOICES:
        return value
    return default


def _closed_lid_awake_policy(value: object) -> str:
    if isinstance(value, str) and value in CLOSED_LID_AWAKE_CHOICES:
        return value
    return CLOSED_LID_AWAKE_NEVER


def _alcove_compatibility_mode_setting(value: object) -> str:
    if isinstance(value, str) and value in ALCOVE_COMPAT_CHOICES:
        return value
    return ALCOVE_COMPAT_AUTO


def _device_display_settings(value: object, default_display: str) -> tuple[DeviceDisplaySetting, ...]:
    if not isinstance(value, list):
        return ()

    devices: list[DeviceDisplaySetting] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        device_id = item.get("id")
        path = item.get("path")
        if not isinstance(device_id, str) or not device_id:
            continue
        if not isinstance(path, str) or not path:
            path = device_id
        if device_id in seen:
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            name = Path(path).name or device_id
        display = _led_display_setting(item.get("led_display"), default_display)
        brightness = normalize_brightness(item.get("brightness"))
        auto_brightness_enabled = _bool_setting(item.get("auto_brightness_enabled"), False)
        devices.append(
            DeviceDisplaySetting(
                device_id=device_id,
                name=name,
                path=path,
                led_display=display,
                brightness=brightness,
                auto_brightness_enabled=auto_brightness_enabled,
                red_gain=normalize_channel_gain(item.get("red_gain")),
                green_gain=normalize_channel_gain(item.get("green_gain")),
                blue_gain=normalize_channel_gain(item.get("blue_gain")),
            )
        )
        seen.add(device_id)
    return tuple(devices)


def _session_open_preferences(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for provider, action in value.items():
        if not isinstance(provider, str) or not isinstance(action, str):
            continue
        if action not in SESSION_OPEN_CHOICES:
            continue
        result[provider.lower()] = action
    return result


def session_open_preference_key(provider: str, origin: str | None = None) -> str:
    if origin:
        return f"origin:{provider.lower()}:{normalize_session_origin_key(origin)}"
    return provider.lower()


def normalize_session_origin_key(origin: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", origin.strip().lower()).strip("_")
    return normalized or "unknown"


def _optional_float_setting(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _float_setting(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def normalize_brightness(value: object) -> int:
    if value is None:
        return 255
    if isinstance(value, (int, float)):
        return max(0, min(255, int(round(float(value)))))
    return 255


def default_lid_animation(kind: str) -> LedAnimationSetting:
    if kind == LID_ANIMATION_CLOSED:
        return LedAnimationSetting(
            program=DEFAULT_LID_CLOSED_ANIMATION_PROGRAM,
            duration_seconds=DEFAULT_LID_CLOSED_ANIMATION_SECONDS,
        )
    if kind == LID_ANIMATION_OPEN:
        return LedAnimationSetting(
            program=DEFAULT_LID_OPEN_ANIMATION_PROGRAM,
            duration_seconds=DEFAULT_LID_OPEN_ANIMATION_SECONDS,
        )
    raise ValueError(f"Unknown lid animation: {kind}")


def normalize_animation_duration(value: object) -> float:
    if not isinstance(value, (int, float)):
        return 1.0
    return max(0.1, min(10.0, float(value)))


def _lid_animation_setting(
    value: object,
    default: LedAnimationSetting,
) -> LedAnimationSetting:
    if not isinstance(value, dict):
        return default
    program = value.get("program")
    if not isinstance(program, str) or not program.strip():
        program = default.program
    duration = value.get("duration_seconds")
    if not isinstance(duration, (int, float)):
        duration = default.duration_seconds
    return LedAnimationSetting(
        program=program,
        duration_seconds=normalize_animation_duration(duration),
    )
