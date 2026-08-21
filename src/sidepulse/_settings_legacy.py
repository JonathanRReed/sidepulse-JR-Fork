from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .battery import DEFAULT_POWER_CHANGE_PREVIEW_SECONDS
from .capacity_calibration import (
    ForecastReleaseAuthority,
    forecast_release_authority_from_payload,
    forecast_release_authority_to_payload,
)
from .colors import ColorSettings
from .led_status import DEFAULT_CHANNEL_GAIN, normalize_channel_gain
from .providers import PROVIDER_REGISTRY
from .signals import (
    DEFAULT_ALERT_BURST,
    DEFAULT_QUOTA_THRESHOLDS,
    FOCUS_SIGNAL_POLICIES,
    normalize_alert_burst,
)
from .private_io import (
    atomic_private_write,
    ensure_private_directory,
    ensure_private_file,
    read_private_text,
)
from .remote_peers import RemotePeerSettings
from .session_actions import SESSION_OPEN_CHOICES

LED_DISPLAY_AGENT = "agent"
LED_DISPLAY_BATTERY = "battery"
LED_DISPLAY_TIMER = "timer"
LED_DISPLAY_STUDIO = "studio"
LED_DISPLAY_QUOTA_RUNWAY = "quota_runway"
LED_DISPLAY_CHOICES = (
    LED_DISPLAY_AGENT,
    LED_DISPLAY_BATTERY,
    LED_DISPLAY_TIMER,
    LED_DISPLAY_STUDIO,
    LED_DISPLAY_QUOTA_RUNWAY,
)
SETTINGS_SCHEMA_VERSION = 1
# The consent generation the Claude plan-limits opt-in was granted under.
# 0.2.1 shipped a build that PERSISTED `claude_plan_limits_enabled` while the
# code documented the flag as inert, so a `true` sitting in a settings file
# today may never have been a decision to present a Keychain credential to
# api.anthropic.com. Only a value stamped with this generation is consent;
# anything older is re-asked. Bump this whenever what the opt-in permits
# changes.
CLAUDE_PLAN_LIMITS_CONSENT_VERSION = 1
CALIBRATION_PROFILE_SLOTS = ("Day", "Night", "Travel")
BRACKET_STYLE_CHOICES = ("auto", "spatial", "identity")

WEBHOOK_EVENT_KEYS = (
    "completion",
    "weather",
    "timebox",
)
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
LID_ANIMATION_CLOSED_ACTIVE = "closed_active"
LID_ANIMATION_OPEN_ACTIVE = "open_active"
LID_ANIMATION_CHOICES = (
    LID_ANIMATION_CLOSED,
    LID_ANIMATION_OPEN,
    LID_ANIMATION_CLOSED_ACTIVE,
    LID_ANIMATION_OPEN_ACTIVE,
)
# Distinct out-of-the-box looks for "agents were RUNNING when the lid
# moved" -- the whole point is that you can TELL without looking twice.
DEFAULT_LID_CLOSED_ACTIVE_PROGRAM = (
    "#FF9F0A 300ms pulse\n#FF9F0A 250ms cosine\n#5A3A00 350ms cosine\n#1A1200 600ms cosine"
)
DEFAULT_LID_OPEN_ACTIVE_PROGRAM = (
    "#12E3B0 200ms pulse\n#00E5FF 300ms cosine\n#00E5FF 700ms pulse"
)

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
    # A faint ember on every LED even when a segment is "off" -- makes
    # the dots read as physical objects. 0 = classic full-dark.
    resting_glow: float = 0.0
    # Per-device blend-mode override (None = the global Colors-window
    # choice) -- the Pro can run Spatial Split while the Dot relays.
    blend_mode: str | None = None
    # Story #16: pin this device to one provider ("claude"/"codex");
    # None = aggregate. A pinned device shows only its provider's
    # sessions and rests dark when none are live.
    provider_pin: str | None = None
    # Per-device courtesy-signal muting: "asks_only" keeps this device
    # to agent status + asks/escalation (and weather/low-battery, which
    # are never muted); None = every signal. The per-Focus policy's
    # per-DEVICE sibling.
    signal_policy: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.device_id,
            "name": self.name,
            "path": self.path,
            "led_display": (
                LED_DISPLAY_AGENT
                if self.led_display == LED_DISPLAY_QUOTA_RUNWAY
                else self.led_display
            ),
            "brightness": self.brightness,
            "auto_brightness_enabled": self.auto_brightness_enabled,
            "red_gain": self.red_gain,
            "green_gain": self.green_gain,
            "blue_gain": self.blue_gain,
            "blend_mode": self.blend_mode,
            "provider_pin": self.provider_pin,
            "signal_policy": self.signal_policy,
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
    # On battery, holding caffeinate -ims drains the machine for work the
    # user may not be watching. Default keeps the historical behavior (hold
    # everywhere); turning it off releases the hold whenever the Mac reports
    # it is unplugged. An unknown power state never disables the hold.
    keep_awake_on_battery: bool = True
    lid_closed_animation: LedAnimationSetting = field(
        default_factory=lambda: default_lid_animation(LID_ANIMATION_CLOSED)
    )
    lid_open_animation: LedAnimationSetting = field(
        default_factory=lambda: default_lid_animation(LID_ANIMATION_OPEN)
    )
    lid_closed_active_animation: LedAnimationSetting = field(
        default_factory=lambda: LedAnimationSetting(
            program=DEFAULT_LID_CLOSED_ACTIVE_PROGRAM, duration_seconds=1.5
        )
    )
    lid_open_active_animation: LedAnimationSetting = field(
        default_factory=lambda: LedAnimationSetting(
            program=DEFAULT_LID_OPEN_ACTIVE_PROGRAM, duration_seconds=1.2
        )
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
    # Sweep the bar in the finishing agent's color the moment ANY
    # session completes -- the aggregate hides completions whenever
    # another agent is still working.
    completion_sweep_enabled: bool = True
    # Calm purple glow starting this many minutes before a calendar
    # event. Off by default: enabling it presents the system Calendars
    # permission prompt (see calendar_watch.py).
    calendar_alerts_enabled: bool = False
    calendar_lead_minutes: float = 5.0
    # Amber glow when a Reminder comes due. Off by default: enabling it
    # presents the system Reminders prompt (see reminders_watch.py).
    reminder_alerts_enabled: bool = False
    # Severe/Extreme weather warnings (NWS). Location: manual lat/lon
    # if set, otherwise a one-shot IP geolocation -- deliberately not
    # CoreLocation (a Location prompt would cost another bundle
    # re-sign and another lost FDA grant).
    weather_alerts_enabled: bool = False
    weather_latitude: float | None = None
    weather_longitude: float | None = None
    # Named calibration/brightness profiles (Day/Night/Travel slots),
    # switchable from the dropdown -- slot -> {device_id: snapshot}.
    calibration_profiles: dict[str, dict] = field(default_factory=dict)
    # "Working timer" LED display: fill = elapsed working time against
    # this many expected minutes. Honest: it's a TIMER, not task
    # progress -- hooks deliver no truthful progress fraction.
    timer_expected_minutes: float = 10.0
    # The Studio: the user's own hand-written LED program (see the
    # Studio card in the Animations pane). Persisted verbatim.
    studio_program: str = ""
    # Focus -> profile automation: when a Focus activates, apply this
    # calibration/brightness profile slot (focus id -> slot name).
    focus_profile_rules: dict[str, str] = field(default_factory=dict)
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
    # How the Alcove bracket colors itself: "auto" mirrors the physical
    # LEDs whenever at least two are lit and collapses to one identity
    # hue otherwise; "spatial" always mirrors; "identity" always
    # collapses. Auto keeps the ripple in sync with the light bar.
    screen_bar_bracket_style: str = "auto"
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
    tips_enabled: bool = True
    menu_bar_label_enabled: bool = False
    # The Screen Bar's dim floor as a USER dial. 0 = pitch black: only
    # the moving signal shows (the relay dot ticking round, the timer
    # filling). 0.25 preserves the pre-dial behavior.
    screen_bar_min_glow: float = 0.25
    # Story #14: wing tips as standing micro-gauges (quota ember left,
    # unseen-done green right). Screen-Bar-only luxury; off by default.
    # One light language across both surfaces: the Screen Bar renders
    # with the SAME animation the hardware is running, so the notch and
    # the LEDs are never two different opinions about the same moment.
    # On by default -- a user who owns the hardware wants them to agree,
    # and a user who does not never notices the setting exists.
    link_screen_bar_to_hardware: bool = True
    screen_bar_gauges_enabled: bool = False
    # Follow Alcove's visible capsule width (alpha-measured) so an
    # expanded live activity never outgrows the bracket. On by default;
    # manual wing lengths always win over it.
    screen_bar_follow_alcove: bool = True
    # Full-screen spaces hide the menu bar; a status bar floating over a
    # full-screen VIDEO reads as a glitch, not a feature. Off by default;
    # the switch exists for people who want the bar everywhere.
    screen_bar_show_in_full_screen: bool = False
    # Off by default and opt-in by policy: reading consumer Claude limits
    # means presenting the user's own subscription credential.
    claude_plan_limits_enabled: bool = False
    # Which consent generation the flag above was granted under. Zero means
    # "no consent this build recognises" -- the state a settings file written
    # by an older build is in, and the state a hand-forged dataclass is in.
    claude_plan_limits_consent_version: int = 0
    # Still legacy and still inert: threshold alerts are an outbound effect
    # and have no authority-fed producer yet.
    quota_alerts_enabled: bool = False
    # Capacity retention is a separate, explicit consent boundary. Existing
    # transcript and broad usage settings never enable either history stream.
    capacity_history_enabled: bool = False
    capacity_history_retention_days: int = 7
    local_activity_history_enabled: bool = False
    # The second Mac. Off by default in every direction: nothing is
    # discovered, nothing is fetched, nothing about this desk is written
    # where a peer could read it, and a peer's row may not take a light
    # here until the owner unmutes that machine by name.
    remote_peers: RemotePeerSettings = field(default_factory=RemotePeerSettings)
    # The loopback ingest port for off-machine agents (a cloud code
    # review posting its own lifecycle). Off by default: it opens a
    # listening socket, and "local" is not "trusted".
    cloud_ingest_enabled: bool = False
    # Zero is the explicit off state. Broad or legacy history consent never
    # enables the metadata-only operator ledger.
    operator_history_retention_days: int = 0
    forecast_release_authority: ForecastReleaseAuthority = field(
        default_factory=ForecastReleaseAuthority.withheld
    )
    usage_graph_days: int = 7
    # "tokens" leads with token counts (cost approximated in parens,
    # the CodexBar presentation); "cost" leads with dollars.
    usage_display_mode: str = "tokens"
    usage_graph_providers: tuple[str, ...] = ("claude", "codex")
    codex_percent_enabled: bool = True
    escalation_webhook_url: str = ""
    # Named Studio programs -- a shelf of looks.
    studio_library: tuple[tuple[str, str], ...] = ()
    night_warmth_enabled: bool = False
    # One master dial over EVERY surface's brightness -- strip and
    # Screen Bar alike -- reachable from the dropdown. Composes with
    # per-device brightness, auto-brightness, idle and Focus dimming.
    global_brightness_scale: float = 1.0
    focus_signal_policy: dict[str, str] = field(default_factory=dict)
    # A macOS notification banner when a main session finishes. New
    # installations remain off until the user opts in and explicitly
    # handles the macOS permission action. Existing settings files that
    # predate this policy preserve their prior effective choice once.
    completion_notification_enabled: bool = False
    notification_policy_version: int = 1
    # Webhook bridge: which non-capacity moment events (beyond stage-3
    # escalation, which always fires when the URL is set) also POST.
    webhook_events: tuple[str, ...] = ()
    # Story #10: timebox preset -> (start Shortcut, end Shortcut). Keys
    # are the preset minutes as strings ("25"); either name may be "".
    timebox_shortcuts: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Sub-agent asks can't be answered (their parent handles them), so
    # by default only MAIN sessions may ring the Ask signal.
    subagent_asks_alert: bool = False
    # The owner's defaults: a nudge at 90, a real warning at 95.
    quota_alert_thresholds: tuple[float, ...] = DEFAULT_QUOTA_THRESHOLDS
    # The interrupt budget's one dial: how many repetitions a COURTESY
    # signal gets before it goes back to normal. Three, per the locked
    # law. Critical signals ignore it -- they blink until dealt with.
    alert_burst: int = DEFAULT_ALERT_BURST
    dismissed_tips: tuple[str, ...] = ()
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

    def with_transcript_provider(self, provider: str, enabled: bool) -> AgentMonitorSettings:
        if provider == "codex":
            return replace(self, codex_transcripts_enabled=enabled)
        if provider == "claude":
            return replace(self, claude_transcripts_enabled=enabled)
        raise ValueError(f"Unknown transcript provider: {provider}")

    def with_led_display(self, display: str) -> AgentMonitorSettings:
        if display not in LED_DISPLAY_CHOICES:
            raise ValueError(f"Unknown LED display: {display}")
        if display == LED_DISPLAY_QUOTA_RUNWAY:
            display = LED_DISPLAY_AGENT
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
    ) -> AgentMonitorSettings:
        if display not in LED_DISPLAY_CHOICES:
            raise ValueError(f"Unknown LED display: {display}")
        if display == LED_DISPLAY_QUOTA_RUNWAY:
            display = LED_DISPLAY_AGENT

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
        brightness: float,
        *,
        name: str | None = None,
        path: str | None = None,
    ) -> AgentMonitorSettings:
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
    ) -> AgentMonitorSettings:
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
    ) -> AgentMonitorSettings:
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

    def with_device_channel_gains_reset(self, device_id: str) -> AgentMonitorSettings:
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
    ) -> AgentMonitorSettings:
        return self.with_device_display(
            device_id,
            self.display_for_device(device_id),
            name=name,
            path=path,
        )

    def without_device(self, device_id: str) -> AgentMonitorSettings:
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
    ) -> AgentMonitorSettings:
        if action not in SESSION_OPEN_CHOICES:
            raise ValueError(f"Unknown session open action: {action}")
        key = session_open_preference_key(provider, origin)
        preferences = dict(self.session_open_preferences)
        preferences[key] = action
        return replace(self, session_open_preferences=preferences)

    def with_provider_session_open_action(
        self, provider: str, action: str
    ) -> AgentMonitorSettings:
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

    def with_battery_full_charge_watts(self, watts: float | None) -> AgentMonitorSettings:
        if watts is not None and watts <= 0:
            watts = None
        return replace(self, battery_full_charge_watts=watts)

    def with_battery_power_change_preview(
        self,
        *,
        enabled: bool | None = None,
        seconds: float | None = None,
    ) -> AgentMonitorSettings:
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
        if kind == LID_ANIMATION_CLOSED_ACTIVE:
            return self.lid_closed_active_animation
        if kind == LID_ANIMATION_OPEN_ACTIVE:
            return self.lid_open_active_animation
        raise ValueError(f"Unknown lid animation: {kind}")

    def lid_animation_for_context(self, closed: bool, agents_active: bool) -> str:
        """Which lid animation KIND applies right now: the agent-aware
        variant when any main agent was live at the transition."""
        if closed:
            return LID_ANIMATION_CLOSED_ACTIVE if agents_active else LID_ANIMATION_CLOSED
        return LID_ANIMATION_OPEN_ACTIVE if agents_active else LID_ANIMATION_OPEN

    def with_closed_lid_awake_policy(self, policy: str) -> AgentMonitorSettings:
        if policy not in CLOSED_LID_AWAKE_CHOICES:
            raise ValueError(f"Unknown closed-lid awake policy: {policy}")
        return replace(self, closed_lid_awake_policy=policy)

    def with_closed_lid_system_override(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, closed_lid_system_override_enabled=bool(enabled))

    def with_closed_lid_grace_minutes(self, minutes: float) -> AgentMonitorSettings:
        return replace(self, closed_lid_grace_minutes=normalize_closed_lid_grace_minutes(minutes))

    def with_keep_awake_on_battery(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, keep_awake_on_battery=bool(enabled))

    def with_lid_animation(
        self,
        kind: str,
        *,
        program: str,
        duration_seconds: float,
    ) -> AgentMonitorSettings:
        animation = LedAnimationSetting(
            program=program,
            duration_seconds=normalize_animation_duration(duration_seconds),
        )
        if kind == LID_ANIMATION_CLOSED:
            return replace(self, lid_closed_animation=animation)
        if kind == LID_ANIMATION_CLOSED_ACTIVE:
            return replace(self, lid_closed_active_animation=animation)
        if kind == LID_ANIMATION_OPEN_ACTIVE:
            return replace(self, lid_open_active_animation=animation)
        if kind == LID_ANIMATION_OPEN:
            return replace(self, lid_open_animation=animation)
        raise ValueError(f"Unknown lid animation: {kind}")

    def with_setup_screen_completed(self, completed: bool = True) -> AgentMonitorSettings:
        return replace(self, setup_screen_completed=bool(completed))

    def with_virtual_status_device(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, virtual_status_device_enabled=bool(enabled))

    def with_virtual_status_device_wraps_menu_bar(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, virtual_status_device_wraps_menu_bar=bool(enabled))

    def with_colors(self, colors: ColorSettings) -> AgentMonitorSettings:
        return replace(self, colors=colors)

    def with_idle_dim_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, idle_dim_enabled=bool(enabled))

    def with_idle_dim_after_minutes(self, minutes: float) -> AgentMonitorSettings:
        return replace(self, idle_dim_after_minutes=normalize_idle_dim_after_minutes(minutes))

    def with_idle_dim_fraction(self, fraction: float) -> AgentMonitorSettings:
        return replace(self, idle_dim_fraction=normalize_idle_dim_fraction(fraction))

    def with_subagent_asks_alert(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, subagent_asks_alert=bool(enabled))

    def with_night_warmth_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, night_warmth_enabled=bool(enabled))

    def with_completion_notification_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(
            self,
            completion_notification_enabled=bool(enabled),
            notification_policy_version=1,
        )

    def with_webhook_event(self, key: str, enabled: bool) -> AgentMonitorSettings:
        if key not in WEBHOOK_EVENT_KEYS:
            raise ValueError(f"Unknown webhook event: {key}")
        events = tuple(k for k in self.webhook_events if k != key)
        if enabled:
            events = (*events, key)
        return replace(self, webhook_events=events)

    def timebox_shortcut_pair(self, preset_key: str) -> tuple[str, str]:
        pair = self.timebox_shortcuts.get(preset_key)
        if not pair:
            return ("", "")
        return (str(pair[0]), str(pair[1]))

    def with_timebox_shortcut(
        self, preset_key: str, on_name: str, off_name: str
    ) -> AgentMonitorSettings:
        """Both names empty removes the mapping -- unmapped presets
        behave exactly as before the handshake existed."""
        mapping = dict(self.timebox_shortcuts)
        on_clean = str(on_name).strip()
        off_clean = str(off_name).strip()
        if not on_clean and not off_clean:
            mapping.pop(str(preset_key), None)
        else:
            mapping[str(preset_key)] = (on_clean, off_clean)
        return replace(self, timebox_shortcuts=mapping)

    def with_global_brightness_scale(self, value: float) -> AgentMonitorSettings:
        """The master dial. Clamped 0.05..1.0 -- dim is never "off in
        disguise"; turning surfaces off is a different, explicit act."""
        return replace(
            self,
            global_brightness_scale=max(0.05, min(1.0, float(value))),
        )

    def with_focus_signal_policy(self, identifier: str, policy: str) -> AgentMonitorSettings:
        if policy not in FOCUS_SIGNAL_POLICIES:
            raise ValueError(f"unknown focus signal policy: {policy}")
        rules = dict(self.focus_signal_policy)
        if policy == "all":
            rules.pop(identifier, None)
        else:
            rules[identifier] = policy
        return replace(self, focus_signal_policy=rules)

    def with_studio_saved_look(self, name: str, program: str) -> AgentMonitorSettings:
        cleaned = str(name).strip()
        if not cleaned:
            raise ValueError("a saved look needs a name")
        library = tuple(
            (existing, existing_program)
            for existing, existing_program in self.studio_library
            if existing != cleaned
        )
        return replace(self, studio_library=(*library, (cleaned, str(program))))

    def without_studio_look(self, name: str) -> AgentMonitorSettings:
        return replace(
            self,
            studio_library=tuple(
                (existing, program)
                for existing, program in self.studio_library
                if existing != name
            ),
        )

    def with_escalation_webhook_url(self, url: str) -> AgentMonitorSettings:
        return replace(self, escalation_webhook_url=str(url).strip())

    def with_usage_display_mode(self, mode: str) -> AgentMonitorSettings:
        if mode not in ("tokens", "cost", "sessions", "percent"):
            raise ValueError(
                "usage display mode is tokens, cost, sessions, or percent"
            )
        return replace(self, usage_display_mode=mode)

    def with_usage_graph_providers(
        self,
        provider_ids: tuple[str, ...],
    ) -> AgentMonitorSettings:
        allowed = {"claude", "codex"}
        if (
            type(provider_ids) is not tuple
            or not provider_ids
            or len(provider_ids) != len(set(provider_ids))
            or any(provider_id not in allowed for provider_id in provider_ids)
        ):
            raise ValueError("usage graph providers must be selected and supported")
        return replace(self, usage_graph_providers=provider_ids)

    def with_codex_percent_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, codex_percent_enabled=bool(enabled))

    def with_usage_graph_days(self, days: int) -> AgentMonitorSettings:
        if int(days) not in (7, 30, 90, 365):
            raise ValueError("graph range is 7, 30, 90 or 365 days")
        return replace(self, usage_graph_days=int(days))

    def with_quota_alerts_enabled(self, enabled: bool) -> AgentMonitorSettings:
        # Fails closed on purpose. Threshold effects are only allowed to reach
        # the user through the capacity AUTHORITY layer (select_binding_lanes),
        # which refuses stale, model-inapplicable and unknown-source readings.
        # Until this is wired to that layer -- rather than to raw provider
        # percentages -- enabling it would let an unauthoritative reading blink
        # the lights. quota_alerts.QuotaThresholdDetector is built and tested
        # and waiting for exactly that wiring.
        del enabled
        return replace(self, quota_alerts_enabled=False)

    def with_quota_alert_thresholds(self, thresholds) -> AgentMonitorSettings:
        del thresholds
        return replace(self, quota_alert_thresholds=DEFAULT_QUOTA_THRESHOLDS)

    def with_alert_burst(self, burst: object) -> AgentMonitorSettings:
        """Honours its argument: unlike the threshold effects above, the
        burst budget is not a raw-provider-percentage route to the
        hardware -- it only says how many times an already-authorised
        courtesy signal may repeat."""
        return replace(self, alert_burst=normalize_alert_burst(burst))

    def with_claude_plan_limits_enabled(self, enabled: bool) -> AgentMonitorSettings:
        # Honours its argument now that the consumer policy declares real
        # lanes and claude/quota/oauth negotiates them, so there is something
        # for capacity_authority.select_binding_lanes to authorise. It stays
        # OFF by default: the policy is opt_in_required, and the opt-in is the
        # user's consent to present their subscription credential at all.
        #
        # Only this mutator stamps consent, and only a stamp from THIS
        # generation survives `load_settings`. That is what stops a `true`
        # written by a build where the flag did nothing from turning into a
        # credentialed network read on first launch after upgrading.
        return replace(
            self,
            claude_plan_limits_enabled=bool(enabled),
            claude_plan_limits_consent_version=(
                CLAUDE_PLAN_LIMITS_CONSENT_VERSION if enabled else 0
            ),
        )

    def with_link_screen_bar_to_hardware(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, link_screen_bar_to_hardware=bool(enabled))

    def with_screen_bar_gauges_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, screen_bar_gauges_enabled=bool(enabled))

    def with_screen_bar_follow_alcove(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, screen_bar_follow_alcove=bool(enabled))

    def with_screen_bar_show_in_full_screen(
        self, enabled: bool
    ) -> AgentMonitorSettings:
        return replace(self, screen_bar_show_in_full_screen=bool(enabled))

    def with_screen_bar_min_glow(self, fraction: float) -> AgentMonitorSettings:
        return replace(
            self, screen_bar_min_glow=max(0.0, min(1.0, float(fraction)))
        )

    def with_device_resting_glow(self, device_id: str, fraction: float) -> AgentMonitorSettings:
        clamped = max(0.0, min(0.35, float(fraction)))
        devices = tuple(
            replace(device, resting_glow=clamped)
            if device.device_id == device_id
            else device
            for device in self.devices
        )
        return replace(self, devices=devices)

    def resting_glow_for_device(self, device_id: str) -> float:
        for device in self.devices:
            if device.device_id == device_id:
                return device.resting_glow
        return 0.0

    def with_menu_bar_label_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, menu_bar_label_enabled=bool(enabled))

    def with_tips_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, tips_enabled=bool(enabled))

    def with_dismissed_tip(self, tip_text: str) -> AgentMonitorSettings:
        text = str(tip_text).strip()
        if not text or text in self.dismissed_tips:
            return self
        return replace(self, dismissed_tips=(*self.dismissed_tips, text))

    def with_focus_sync_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, focus_sync_enabled=bool(enabled))

    def with_screen_bar_gap_width(self, width: float | None) -> AgentMonitorSettings:
        if width is not None:
            width = max(120.0, min(1200.0, float(width)))
        return replace(self, screen_bar_gap_width=width)

    def with_screen_bar_wing_length(self, length: float | None) -> AgentMonitorSettings:
        if length is not None:
            length = max(0.0, min(400.0, float(length)))
        return replace(self, screen_bar_wing_length=length)

    def with_screen_bar_bracket_style(self, style: str) -> AgentMonitorSettings:
        if style not in BRACKET_STYLE_CHOICES:
            raise ValueError(f"Unknown bracket style: {style}")
        return replace(self, screen_bar_bracket_style=style)

    def device_blend_mode(self, device_id: str) -> str | None:
        for device in self.devices:
            if device.device_id == device_id:
                return device.blend_mode
        return None

    def device_signal_policy(self, device_id: str) -> str | None:
        for device in self.devices:
            if device.device_id == device_id:
                return device.signal_policy
        return None

    def with_device_signal_policy(
        self, device_id: str, policy: str | None
    ) -> AgentMonitorSettings:
        """policy=None restores every signal."""
        if policy is not None and policy != "asks_only":
            raise ValueError(f"Unknown device signal policy: {policy}")
        devices = tuple(
            replace(device, signal_policy=policy)
            if device.device_id == device_id
            else device
            for device in self.devices
        )
        return replace(self, devices=devices)

    def device_provider_pin(self, device_id: str) -> str | None:
        for device in self.devices:
            if device.device_id == device_id:
                return device.provider_pin
        return None

    def with_device_provider_pin(
        self, device_id: str, provider: str | None
    ) -> AgentMonitorSettings:
        """provider=None restores the aggregate view."""
        if provider is not None and provider not in PROVIDER_REGISTRY:
            # Every REGISTERED provider is pinnable -- the old
            # claude/codex whitelist predates the other eight.
            raise ValueError(f"Unknown provider pin: {provider}")
        devices = tuple(
            replace(device, provider_pin=provider)
            if device.device_id == device_id
            else device
            for device in self.devices
        )
        return replace(self, devices=devices)

    def with_device_blend_mode(self, device_id: str, mode: str | None) -> AgentMonitorSettings:
        """mode=None restores the global Colors-window blend choice."""
        devices = tuple(
            replace(device, blend_mode=mode) if device.device_id == device_id else device
            for device in self.devices
        )
        return replace(self, devices=devices)

    def with_saved_calibration_profile(self, slot: str) -> AgentMonitorSettings:
        """Snapshots every known device's brightness + channel gains
        into a named profile slot."""
        payload = {
            device.device_id: {
                "brightness": device.brightness,
                "red_gain": device.red_gain,
                "resting_glow": device.resting_glow,
                "green_gain": device.green_gain,
                "blue_gain": device.blue_gain,
            }
            for device in self.devices
        }
        profiles = dict(self.calibration_profiles)
        profiles[slot] = payload
        return replace(self, calibration_profiles=profiles)

    def with_applied_calibration_profile(self, slot: str) -> AgentMonitorSettings:
        """Applies a saved profile onto matching devices; unknown ids in
        the profile are ignored, devices missing from it are untouched."""
        profile = self.calibration_profiles.get(slot)
        if not isinstance(profile, dict):
            return self
        devices = []
        for device in self.devices:
            entry = profile.get(device.device_id)
            if isinstance(entry, dict):
                device = replace(
                    device,
                    brightness=normalize_brightness(entry.get("brightness", device.brightness)),
                    red_gain=normalize_channel_gain(entry.get("red_gain", device.red_gain)),
                    green_gain=normalize_channel_gain(entry.get("green_gain", device.green_gain)),
                    blue_gain=normalize_channel_gain(entry.get("blue_gain", device.blue_gain)),
                )
            devices.append(device)
        return replace(self, devices=tuple(devices))

    def with_low_battery_alert_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, low_battery_alert_enabled=bool(enabled))

    def with_low_battery_threshold_percent(self, percent: float) -> AgentMonitorSettings:
        return replace(self, low_battery_threshold_percent=max(1.0, min(50.0, float(percent))))

    def with_completion_sweep_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, completion_sweep_enabled=bool(enabled))

    def with_calendar_alerts_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, calendar_alerts_enabled=bool(enabled))

    def with_reminder_alerts_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, reminder_alerts_enabled=bool(enabled))

    def with_weather_alerts_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, weather_alerts_enabled=bool(enabled))

    def with_capacity_history_enabled(self, enabled: bool) -> AgentMonitorSettings:
        return replace(self, capacity_history_enabled=bool(enabled))

    def with_capacity_history_retention_days(self, days: int) -> AgentMonitorSettings:
        """Only the three retentions the store will actually accept.

        `HistoryRetentionPolicy` raises on anything else, and a settings
        value that makes the store refuse to construct turns capacity
        history off without ever saying so.
        """
        value = int(days)
        if value not in (7, 30, 90):
            raise ValueError("unsupported capacity history retention")
        return replace(self, capacity_history_retention_days=value)

    def with_studio_program(self, program: str) -> AgentMonitorSettings:
        return replace(self, studio_program=str(program))

    def with_focus_profile_rule(self, focus_identifier: str, slot: str | None) -> AgentMonitorSettings:
        """slot=None removes the rule."""
        if slot is not None and slot not in CALIBRATION_PROFILE_SLOTS:
            raise ValueError(f"Unknown profile slot: {slot}")
        rules = dict(self.focus_profile_rules)
        if slot is None:
            rules.pop(focus_identifier, None)
        else:
            rules[focus_identifier] = slot
        return replace(self, focus_profile_rules=rules)

    def with_timer_expected_minutes(self, minutes: float) -> AgentMonitorSettings:
        return replace(self, timer_expected_minutes=max(1.0, min(480.0, float(minutes))))

    def with_weather_location(
        self, latitude: float | None, longitude: float | None
    ) -> AgentMonitorSettings:
        """Both None = automatic IP geolocation."""
        if latitude is not None:
            latitude = max(-90.0, min(90.0, float(latitude)))
        if longitude is not None:
            longitude = max(-180.0, min(180.0, float(longitude)))
        return replace(self, weather_latitude=latitude, weather_longitude=longitude)

    def with_calendar_lead_minutes(self, minutes: float) -> AgentMonitorSettings:
        return replace(self, calendar_lead_minutes=max(1.0, min(60.0, float(minutes))))

    def with_escalation_tier(self, tier: str) -> AgentMonitorSettings:
        from .signals import ESCALATION_TIERS

        if tier not in ESCALATION_TIERS:
            raise ValueError(f"Unknown escalation tier: {tier}")
        return replace(self, escalation_tier=tier)

    def with_escalation_thresholds(
        self,
        ramp_seconds: float | None = None,
        menu_bar_seconds: float | None = None,
        final_seconds: float | None = None,
    ) -> AgentMonitorSettings:
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
        merged over the built-in default. Continuous signals never get
        a one-shot pattern -- it would flash once and leave the bar
        dark for the rest of a multi-hour condition."""
        from dataclasses import replace as _replace

        from .signals import (
            CONTINUOUS_SIGNALS,
            DEFAULT_SIGNAL_STYLES,
            ONE_SHOT_PATTERNS,
            PATTERN_BREATHE,
            SignalStyle,
        )

        fallback = DEFAULT_SIGNAL_STYLES[key]
        style = SignalStyle.from_dict(self.signal_styles.get(key), fallback)
        if key in CONTINUOUS_SIGNALS and style.pattern in ONE_SHOT_PATTERNS:
            style = _replace(style, pattern=PATTERN_BREATHE)
        return style

    def with_signal_style(self, key: str, style) -> AgentMonitorSettings:
        from .signals import DEFAULT_SIGNAL_STYLES

        if key not in DEFAULT_SIGNAL_STYLES:
            raise ValueError(f"Unknown signal: {key}")
        styles = dict(self.signal_styles)
        styles[key] = style.normalized().to_dict()
        return replace(self, signal_styles=styles)

    def focus_dim_fraction(self, mode_identifier: str) -> float:
        """The brightness fraction to apply while this Focus is active --
        its own rule if set, otherwise the shared idle-dim amount (the
        pre-per-Focus behavior)."""
        rule = self.focus_dim_rules.get(mode_identifier)
        if rule is None:
            return self.idle_dim_fraction
        return max(0.0, min(1.0, float(rule)))

    def with_focus_dim_rule(self, mode_identifier: str, fraction: float | None) -> AgentMonitorSettings:
        """fraction=None removes the rule (back to the shared default)."""
        rules = dict(self.focus_dim_rules)
        if fraction is None:
            rules.pop(mode_identifier, None)
        else:
            rules[mode_identifier] = max(0.0, min(1.0, float(fraction)))
        return replace(self, focus_dim_rules=rules)

    def with_remote_peers(self, remote: RemotePeerSettings) -> AgentMonitorSettings:
        """Replace the whole peer record, normalised on the way in.

        Every bound (peer count, timeouts, the published path) is enforced
        by RemotePeerSettings itself, so nothing that reaches the transport
        can have been widened by a settings-window control.
        """
        if type(remote) is not RemotePeerSettings:
            return self
        return replace(self, remote_peers=remote.normalized())

    def with_remote_machine_muted(self, machine: str, muted: bool) -> AgentMonitorSettings:
        """Mute or unmute ONE peer machine's rows in the interrupt budget.

        The ledger keeps showing that machine either way -- this only
        decides whether its rows may take a light on this desk.
        """
        current = self.remote_peers.normalized()
        policy = current.interrupt_policy().with_machine_muted(str(machine), bool(muted))
        return self.with_remote_peers(
            replace(
                current,
                unmuted_machines=tuple(sorted(policy.unmuted_machines)),
                muted_machines=tuple(sorted(policy.muted_machines)),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "settings_schema_version": SETTINGS_SCHEMA_VERSION,
            "led_display": (
                LED_DISPLAY_AGENT
                if self.led_display == LED_DISPLAY_QUOTA_RUNWAY
                else self.led_display
            ),
            "devices": [device.to_dict() for device in self.devices],
            "virtual_status_device_enabled": self.virtual_status_device_enabled,
            "virtual_status_device_wraps_menu_bar": self.virtual_status_device_wraps_menu_bar,
            "screen_bar_gap_width": self.screen_bar_gap_width,
            "screen_bar_wing_length": self.screen_bar_wing_length,
            "screen_bar_bracket_style": self.screen_bar_bracket_style,
            "closed_lid_awake_policy": self.closed_lid_awake_policy,
            "closed_lid_system_override_enabled": self.closed_lid_system_override_enabled,
            "closed_lid_grace_minutes": self.closed_lid_grace_minutes,
            "keep_awake_on_battery": self.keep_awake_on_battery,
            "lid_closed_animation": self.lid_closed_animation.to_dict(),
            "lid_closed_active_animation": self.lid_closed_active_animation.to_dict(),
            "lid_open_active_animation": self.lid_open_active_animation.to_dict(),
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
            "completion_sweep_enabled": self.completion_sweep_enabled,
            "calendar_alerts_enabled": self.calendar_alerts_enabled,
            "calendar_lead_minutes": self.calendar_lead_minutes,
            "reminder_alerts_enabled": self.reminder_alerts_enabled,
            "weather_alerts_enabled": self.weather_alerts_enabled,
            "weather_latitude": self.weather_latitude,
            "weather_longitude": self.weather_longitude,
            "calibration_profiles": dict(sorted(self.calibration_profiles.items())),
            "timer_expected_minutes": self.timer_expected_minutes,
            "focus_profile_rules": dict(sorted(self.focus_profile_rules.items())),
            "studio_program": self.studio_program,
            "signal_styles": dict(
                sorted(
                    (key, value)
                    for key, value in self.signal_styles.items()
                    if key != "notification"
                )
            ),
            "escalation_tier": self.escalation_tier,
            "escalation_ramp_seconds": self.escalation_ramp_seconds,
            "escalation_menu_bar_seconds": self.escalation_menu_bar_seconds,
            "escalation_final_seconds": self.escalation_final_seconds,
            "session_open_preferences": dict(sorted(self.session_open_preferences.items())),
            "setup_screen_completed": self.setup_screen_completed,
            "colors": self.colors.to_dict(),
            "idle_dim_enabled": self.idle_dim_enabled,
            "idle_dim_after_minutes": self.idle_dim_after_minutes,
            "idle_dim_fraction": self.idle_dim_fraction,
            "focus_sync_enabled": self.focus_sync_enabled,
            "tips_enabled": self.tips_enabled,
            "menu_bar_label_enabled": self.menu_bar_label_enabled,
            "screen_bar_min_glow": self.screen_bar_min_glow,
            "link_screen_bar_to_hardware": self.link_screen_bar_to_hardware,
            "screen_bar_gauges_enabled": self.screen_bar_gauges_enabled,
            "screen_bar_follow_alcove": self.screen_bar_follow_alcove,
            "screen_bar_show_in_full_screen": self.screen_bar_show_in_full_screen,
            "claude_plan_limits_enabled": self.claude_plan_limits_enabled,
            "claude_plan_limits_consent_version": (
                self.claude_plan_limits_consent_version
                if type(self.claude_plan_limits_consent_version) is int
                and self.claude_plan_limits_enabled
                else 0
            ),
            "capacity_history_enabled": self.capacity_history_enabled,
            "capacity_history_retention_days": (
                self.capacity_history_retention_days
                if type(self.capacity_history_retention_days) is int
                and self.capacity_history_retention_days in (7, 30, 90)
                else 7
            ),
            "local_activity_history_enabled": self.local_activity_history_enabled,
            "remote_peers": self.remote_peers.to_dict(),
            "cloud_ingest_enabled": self.cloud_ingest_enabled,
            "operator_history_retention_days": (
                self.operator_history_retention_days
                if type(self.operator_history_retention_days) is int
                and self.operator_history_retention_days in (0, 7, 30, 90)
                else 0
            ),
            "forecast_release_authority": forecast_release_authority_to_payload(
                self.forecast_release_authority
            ),
            "usage_graph_days": self.usage_graph_days,
            "usage_display_mode": self.usage_display_mode,
            "usage_graph_providers": list(self.usage_graph_providers),
            "codex_percent_enabled": self.codex_percent_enabled,
            "escalation_webhook_url": self.escalation_webhook_url,
            "studio_library": [list(item) for item in self.studio_library],
            "night_warmth_enabled": self.night_warmth_enabled,
            "global_brightness_scale": self.global_brightness_scale,
            "focus_signal_policy": dict(self.focus_signal_policy),
            "completion_notification_enabled": self.completion_notification_enabled,
            "notification_policy_version": 1,
            "webhook_events": [
                key for key in self.webhook_events if key in WEBHOOK_EVENT_KEYS
            ],
            "timebox_shortcuts": {
                key: list(pair) for key, pair in self.timebox_shortcuts.items()
            },
            "subagent_asks_alert": self.subagent_asks_alert,
            "alert_burst": normalize_alert_burst(self.alert_burst),
            "dismissed_tips": list(self.dismissed_tips),
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


def _escalation_thresholds(data: dict) -> dict[str, float]:
    """Clamped AND ordered (ramp <= menu bar <= final) -- a hand-edited
    file with ramp=600, menu=10 must not jump straight to the finale."""
    ramp = max(5.0, min(600.0, _float_setting(data.get("escalation_ramp_seconds"), 30.0)))
    menu = max(ramp, min(1800.0, _float_setting(data.get("escalation_menu_bar_seconds"), 120.0)))
    final = max(menu, min(3600.0, _float_setting(data.get("escalation_final_seconds"), 300.0)))
    return {
        "escalation_ramp_seconds": ramp,
        "escalation_menu_bar_seconds": menu,
        "escalation_final_seconds": final,
    }


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
        if key == "notification":
            continue
        fallback = DEFAULT_SIGNAL_STYLES.get(key)
        if fallback is None:
            continue
        result[key] = SignalStyle.from_dict(value, fallback).to_dict()
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


def _preserve_corrupt_settings(target: Path) -> None:
    """A parse failure must never silently cost the user their
    calibration profiles, studio library, and colors: returning
    defaults means the very next auto-save (device remembering runs on
    every LED sync) overwrites the evidence. Move the corrupt file
    aside so it stays recoverable by hand; one backup, not a litter of
    them -- repeated startups against the same corruption keep the
    FIRST capture."""
    try:
        ensure_private_directory(target.parent)
        ensure_private_file(target)
        backup = target.with_name(target.name + ".corrupt")
        if backup.is_symlink():
            return
        if backup.exists():
            ensure_private_file(backup)
            target.unlink(missing_ok=True)
            return
        os.replace(target, backup)
        ensure_private_file(backup)
    except OSError:
        pass


def load_settings(path: Path | None = None) -> AgentMonitorSettings:
    target = (path or default_settings_path()).expanduser()
    try:
        target.lstat()
        ensure_private_directory(target.parent)
        data = json.loads(read_private_text(target))
    except FileNotFoundError:
        return AgentMonitorSettings()
    except OSError:
        return AgentMonitorSettings()
    except Exception:
        _preserve_corrupt_settings(target)
        return AgentMonitorSettings()

    if not isinstance(data, dict):
        _preserve_corrupt_settings(target)
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
        screen_bar_bracket_style=(
            data.get("screen_bar_bracket_style")
            if data.get("screen_bar_bracket_style") in BRACKET_STYLE_CHOICES
            else "auto"
        ),
        closed_lid_awake_policy=_closed_lid_awake_policy(
            data.get("closed_lid_awake_policy"),
        ),
        closed_lid_system_override_enabled=_bool_setting(
            data.get("closed_lid_system_override_enabled"),
            False,
        ),
        keep_awake_on_battery=_bool_setting(
            data.get("keep_awake_on_battery"), True
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
        lid_closed_active_animation=_lid_animation_setting(
            data.get("lid_closed_active_animation"),
            LedAnimationSetting(
                program=DEFAULT_LID_CLOSED_ACTIVE_PROGRAM, duration_seconds=1.5
            ),
        ),
        lid_open_active_animation=_lid_animation_setting(
            data.get("lid_open_active_animation"),
            LedAnimationSetting(
                program=DEFAULT_LID_OPEN_ACTIVE_PROGRAM, duration_seconds=1.2
            ),
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
        completion_sweep_enabled=_bool_setting(data.get("completion_sweep_enabled"), True),
        calendar_alerts_enabled=_bool_setting(data.get("calendar_alerts_enabled"), False),
        calendar_lead_minutes=max(
            1.0, min(60.0, _float_setting(data.get("calendar_lead_minutes"), 5.0))
        ),
        reminder_alerts_enabled=_bool_setting(data.get("reminder_alerts_enabled"), False),
        weather_alerts_enabled=_bool_setting(data.get("weather_alerts_enabled"), False),
        weather_latitude=_optional_dimension(data.get("weather_latitude"), -90.0, 90.0),
        weather_longitude=_optional_dimension(data.get("weather_longitude"), -180.0, 180.0),
        calibration_profiles=(
            {
                str(slot): dict(entry)
                for slot, entry in data.get("calibration_profiles", {}).items()
                if isinstance(entry, dict)
            }
            if isinstance(data.get("calibration_profiles"), dict)
            else {}
        ),
        timer_expected_minutes=max(
            1.0, min(480.0, _float_setting(data.get("timer_expected_minutes"), 10.0))
        ),
        studio_program=(
            data.get("studio_program") if isinstance(data.get("studio_program"), str) else ""
        ),
        focus_profile_rules=(
            {
                str(focus_id): slot
                for focus_id, slot in data.get("focus_profile_rules", {}).items()
                if isinstance(slot, str) and slot in CALIBRATION_PROFILE_SLOTS
            }
            if isinstance(data.get("focus_profile_rules"), dict)
            else {}
        ),
        signal_styles=_signal_styles(data.get("signal_styles")),
        escalation_tier=_escalation_tier(data.get("escalation_tier")),
        **_escalation_thresholds(data),
        session_open_preferences=_session_open_preferences(data.get("session_open_preferences")),
        setup_screen_completed=_bool_setting(data.get("setup_screen_completed"), False),
        colors=ColorSettings.from_dict(data.get("colors")),
        idle_dim_enabled=_bool_setting(data.get("idle_dim_enabled"), True),
        idle_dim_after_minutes=normalize_idle_dim_after_minutes(
            data.get("idle_dim_after_minutes"), default=DEFAULT_IDLE_DIM_AFTER_MINUTES
        ),
        idle_dim_fraction=normalize_idle_dim_fraction(
            data.get("idle_dim_fraction"), default=DEFAULT_IDLE_DIM_FRACTION
        ),
        focus_sync_enabled=_bool_setting(data.get("focus_sync_enabled"), False),
        tips_enabled=_bool_setting(data.get("tips_enabled"), True),
        menu_bar_label_enabled=_bool_setting(data.get("menu_bar_label_enabled"), False),
        screen_bar_min_glow=_fraction_setting(data.get("screen_bar_min_glow"), 0.25),
        link_screen_bar_to_hardware=_bool_setting(
            data.get("link_screen_bar_to_hardware"), True
        ),
        screen_bar_gauges_enabled=_bool_setting(data.get("screen_bar_gauges_enabled"), False),
        screen_bar_follow_alcove=_bool_setting(data.get("screen_bar_follow_alcove"), True),
        screen_bar_show_in_full_screen=_bool_setting(
            data.get("screen_bar_show_in_full_screen"), False
        ),
        # A stored `true` is honoured only when it carries this build's
        # consent stamp. 0.2.1 persisted this key from a build whose own
        # comments called the flag inert, so on those machines the value is
        # not a decision to hand a Keychain credential to api.anthropic.com on
        # a 5-minute timer -- and an upgrade must not read it as one. Absent
        # or older stamp: off, and the user is asked again.
        claude_plan_limits_enabled=_claude_plan_limits_consented(data),
        claude_plan_limits_consent_version=(
            CLAUDE_PLAN_LIMITS_CONSENT_VERSION
            if _claude_plan_limits_consented(data)
            else 0
        ),
        # Not loaded: nothing consumes an authorised threshold crossing yet,
        # so a stored True would be a promise the app cannot keep.
        quota_alerts_enabled=False,
        capacity_history_enabled=_bool_setting(
            data.get("capacity_history_enabled"), False
        ),
        capacity_history_retention_days=(
            data.get("capacity_history_retention_days")
            if type(data.get("capacity_history_retention_days")) is int
            and data.get("capacity_history_retention_days") in (7, 30, 90)
            else 7
        ),
        local_activity_history_enabled=_bool_setting(
            data.get("local_activity_history_enabled"), False
        ),
        # RemotePeerSettings.from_dict never raises and normalises every
        # bound itself, so a hand-edited or truncated block degrades to
        # the off-by-default record rather than to an exception on launch.
        remote_peers=RemotePeerSettings.from_dict(data.get("remote_peers")),
        cloud_ingest_enabled=_bool_setting(data.get("cloud_ingest_enabled"), False),
        operator_history_retention_days=(
            data.get("operator_history_retention_days")
            if type(data.get("operator_history_retention_days")) is int
            and data.get("operator_history_retention_days") in (0, 7, 30, 90)
            else 0
        ),
        forecast_release_authority=forecast_release_authority_from_payload(
            data.get("forecast_release_authority")
        ),
        subagent_asks_alert=_bool_setting(data.get("subagent_asks_alert"), False),
        usage_display_mode=(
            data.get("usage_display_mode")
            if data.get("usage_display_mode") in ("tokens", "cost", "sessions")
            else "tokens"
        ),
        usage_graph_providers=(
            tuple(
                provider_id
                for provider_id in data.get("usage_graph_providers")
                if provider_id in {"claude", "codex"}
            )
            if isinstance(data.get("usage_graph_providers"), list)
            and any(
                provider_id in {"claude", "codex"}
                for provider_id in data.get("usage_graph_providers")
            )
            else ("claude", "codex")
        ),
        codex_percent_enabled=_bool_setting(data.get("codex_percent_enabled"), True),
        escalation_webhook_url=str(data.get("escalation_webhook_url") or "").strip(),
        night_warmth_enabled=_bool_setting(data.get("night_warmth_enabled"), False),
        global_brightness_scale=max(
            0.05, _fraction_setting(data.get("global_brightness_scale"), 1.0)
        ),
        focus_signal_policy=(
            {
                str(key): str(value)
                for key, value in data.get("focus_signal_policy").items()
                if str(value) in ("asks_only", "silent")
            }
            if isinstance(data.get("focus_signal_policy"), dict)
            else {}
        ),
        completion_notification_enabled=_bool_setting(
            data.get("completion_notification_enabled"),
            "notification_policy_version" not in data,
        ),
        notification_policy_version=1,
        webhook_events=tuple(
            key
            for key in (data.get("webhook_events") or [])
            if isinstance(key, str) and key in WEBHOOK_EVENT_KEYS
        )
        if isinstance(data.get("webhook_events"), list)
        else (),
        timebox_shortcuts=(
            {
                str(key): (str(pair[0]), str(pair[1]))
                for key, pair in data.get("timebox_shortcuts").items()
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            }
            if isinstance(data.get("timebox_shortcuts"), dict)
            else {}
        ),
        studio_library=tuple(
            (str(item[0]), str(item[1]))
            for item in (data.get("studio_library") or [])
            if isinstance(item, (list, tuple))
            and len(item) == 2
            and str(item[0]).strip()
        ),
        usage_graph_days=(
            int(data.get("usage_graph_days"))
            if data.get("usage_graph_days") in (7, 30, 90, 365)
            else 7
        ),
        quota_alert_thresholds=DEFAULT_QUOTA_THRESHOLDS,
        alert_burst=(
            normalize_alert_burst(data.get("alert_burst"))
            if "alert_burst" in data
            else DEFAULT_ALERT_BURST
        ),
        dismissed_tips=tuple(
            str(item)
            for item in (data.get("dismissed_tips") or [])
            if isinstance(item, str) and item.strip()
        ),
        focus_dim_rules=_focus_dim_rules(data.get("focus_dim_rules")),
    )


def save_settings(
    settings: AgentMonitorSettings,
    path: Path | None = None,
) -> Path:
    target = (path or default_settings_path()).expanduser()
    # Atomic: a crash mid-write must never truncate the file -- a
    # truncated settings.json silently loads as ALL defaults, losing
    # every color, device, and rule. (Also written from the LED worker
    # thread via remember_connected_devices, so in-place truncation had
    # a real interleaving window, not just a power-loss one.)
    payload = json.dumps(settings.to_dict(), indent=2, sort_keys=True) + "\n"
    return atomic_private_write(target, payload)


def _fraction_setting(value: object, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return default


def _bool_setting(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _claude_plan_limits_consented(data: dict) -> bool:
    """Whether a persisted opt-in is consent THIS build may act on.

    `type(...) is int` on purpose: JSON `true` compares equal to 1, so an
    `is int` test is the difference between requiring a real stamp and letting
    any truthy leftover pass for one.
    """
    stamp = data.get("claude_plan_limits_consent_version")
    return (
        _bool_setting(data.get("claude_plan_limits_enabled"), False)
        and type(stamp) is int
        and stamp == CLAUDE_PLAN_LIMITS_CONSENT_VERSION
    )


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
    if value == LED_DISPLAY_QUOTA_RUNWAY:
        return LED_DISPLAY_AGENT
    if isinstance(value, str) and value in LED_DISPLAY_CHOICES:
        return value
    return default


def _closed_lid_awake_policy(value: object) -> str:
    if isinstance(value, str) and value in CLOSED_LID_AWAKE_CHOICES:
        return value
    return CLOSED_LID_AWAKE_NEVER


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
                blend_mode=_device_blend_mode_setting(item.get("blend_mode")),
                provider_pin=(
                    # The setter accepts every registered provider; a
                    # loader that only kept claude/codex silently erased
                    # a Cursor or Grok pin on relaunch (audit: the
                    # SP-AUD-003 silent-settings-loss class).
                    item.get("provider_pin")
                    if item.get("provider_pin") in PROVIDER_REGISTRY
                    else None
                ),
                signal_policy=(
                    item.get("signal_policy")
                    if item.get("signal_policy") == "asks_only"
                    else None
                ),
                resting_glow=_fraction_setting(item.get("resting_glow"), 0.0),
            )
        )
        seen.add(device_id)
    return tuple(devices)


def _device_blend_mode_setting(raw: object) -> str | None:
    """Only real blend modes survive the load -- an unknown string here
    used to reach ColorSettings.with_blend_mode at render time and
    raise inside every refresh cycle."""
    from .colors import BLEND_MODE_CHOICES

    if isinstance(raw, str) and raw in BLEND_MODE_CHOICES:
        return raw
    return None


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
