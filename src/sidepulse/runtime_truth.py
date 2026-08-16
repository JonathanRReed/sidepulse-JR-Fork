"""Pure truth classification for hook intake and SidePulse process ownership."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class HookTruthState(str, Enum):
    NOT_CONFIGURED = "not_configured"
    RELOAD_REQUIRED = "reload_required"
    AWAITING_SESSION = "awaiting_session"
    AWAITING_ACTIVITY = "awaiting_activity"
    IDLE = "idle"
    WORKING = "working"
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


class ProcessOwner(str, Enum):
    NONE = "none"
    FOREGROUND = "foreground"
    LAUNCH_AGENT = "launch_agent"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HookTruthInputs:
    provider: str
    hooks_installed: bool
    session_exists: bool
    session_started_at: float | None
    hooks_installed_at: float | None
    last_event_at: float | None
    last_event_name: str | None
    last_active_event_at: float | None
    lifecycle: str | None
    now: float
    stale_after: float = 120.0

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("provider must be a nonempty string")
        if type(self.hooks_installed) is not bool or type(self.session_exists) is not bool:
            raise ValueError("hook truth flags must be booleans")
        for name in (
            "session_started_at",
            "hooks_installed_at",
            "last_event_at",
            "last_active_event_at",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise ValueError(f"{name} must be a finite nonnegative timestamp")
        if (
            isinstance(self.now, bool)
            or not isinstance(self.now, (int, float))
            or not math.isfinite(float(self.now))
            or float(self.now) < 0.0
        ):
            raise ValueError("now must be a finite nonnegative timestamp")
        if (
            isinstance(self.stale_after, bool)
            or not isinstance(self.stale_after, (int, float))
            or not math.isfinite(float(self.stale_after))
            or float(self.stale_after) <= 0.0
        ):
            raise ValueError("stale_after must be positive")
        object.__setattr__(self, "provider", self.provider.strip().lower())
        object.__setattr__(self, "now", float(self.now))
        object.__setattr__(self, "stale_after", float(self.stale_after))


@dataclass(frozen=True, slots=True)
class HookTruth:
    state: HookTruthState
    summary: str
    action: str | None
    working: bool


@dataclass(frozen=True, slots=True)
class ProcessTruthInputs:
    plist_installed: bool
    launch_agent_loaded: bool
    launch_agent_pid: int | None
    foreground_pid: int | None
    socket_owner_pid: int | None

    def __post_init__(self) -> None:
        if type(self.plist_installed) is not bool or type(self.launch_agent_loaded) is not bool:
            raise ValueError("process truth flags must be booleans")
        for name in ("launch_agent_pid", "foreground_pid", "socket_owner_pid"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{name} must be a positive process id")


@dataclass(frozen=True, slots=True)
class ProcessTruth:
    owner: ProcessOwner
    summary: str
    action: str | None
    healthy: bool


def _provider_label(provider: str) -> str:
    labels = {
        "t3code": "T3 Code",
        "openai": "OpenAI",
        "codex": "Codex",
        "claude": "Claude",
        "grok": "Grok",
        "cursor": "Cursor",
        "devin": "Devin",
        "antigravity": "Antigravity",
    }
    return labels.get(provider, provider.replace("_", " ").title())


def _reload_required(inputs: HookTruthInputs) -> bool:
    if inputs.provider != "grok" or not inputs.session_exists:
        return False
    if inputs.session_started_at is None or inputs.hooks_installed_at is None:
        return False
    installed_mid_session = inputs.hooks_installed_at >= inputs.session_started_at
    observed_after_install = (
        inputs.last_active_event_at is not None
        and inputs.last_active_event_at >= inputs.hooks_installed_at
    )
    return installed_mid_session and not observed_after_install


def classify_hook_truth(inputs: HookTruthInputs) -> HookTruth:
    if type(inputs) is not HookTruthInputs:
        raise TypeError("inputs must be HookTruthInputs")
    label = _provider_label(inputs.provider)

    if not inputs.hooks_installed:
        return HookTruth(
            HookTruthState.NOT_CONFIGURED,
            f"{label} is not connected",
            f"Connect {label}",
            False,
        )

    if _reload_required(inputs):
        return HookTruth(
            HookTruthState.RELOAD_REQUIRED,
            "Hooks were installed after this Grok session started",
            "Reload Grok hooks",
            False,
        )

    if not inputs.session_exists:
        return HookTruth(
            HookTruthState.AWAITING_SESSION,
            f"{label} is connected and awaiting a session",
            None,
            False,
        )

    if (
        inputs.last_event_at is not None
        and inputs.now - float(inputs.last_event_at) > inputs.stale_after
    ):
        return HookTruth(
            HookTruthState.STALE,
            f"{label} hooks are silent or stale",
            f"Check {label} hooks",
            False,
        )

    lifecycle = (inputs.lifecycle or "unknown").strip().lower()
    if lifecycle in {"active", "working", "tool_running", "long_task_progress"}:
        return HookTruth(HookTruthState.WORKING, f"{label} is working", None, True)
    if lifecycle in {"waiting", "needs_input", "waiting_for_input"}:
        return HookTruth(
            HookTruthState.NEEDS_INPUT,
            f"{label} needs input",
            f"Open {label}",
            False,
        )
    if lifecycle in {"completed", "done"}:
        return HookTruth(
            HookTruthState.COMPLETED,
            f"{label} completed work",
            None,
            False,
        )
    if lifecycle in {"failed", "error", "blocked"}:
        return HookTruth(
            HookTruthState.FAILED,
            f"{label} reported a failure",
            f"Open {label}",
            False,
        )

    if inputs.last_active_event_at is None:
        return HookTruth(
            HookTruthState.AWAITING_ACTIVITY,
            f"{label} session exists; awaiting its first prompt or tool event",
            None,
            False,
        )

    return HookTruth(
        HookTruthState.IDLE,
        f"{label} is connected and idle",
        None,
        False,
    )


def classify_process_truth(inputs: ProcessTruthInputs) -> ProcessTruth:
    if type(inputs) is not ProcessTruthInputs:
        raise TypeError("inputs must be ProcessTruthInputs")

    launch_pid = inputs.launch_agent_pid if inputs.launch_agent_loaded else None
    foreground_pid = inputs.foreground_pid
    known_pids = {pid for pid in (launch_pid, foreground_pid) if pid is not None}

    if launch_pid is not None and foreground_pid is not None and launch_pid != foreground_pid:
        return ProcessTruth(
            ProcessOwner.CONFLICT,
            "Foreground SidePulse and the LaunchAgent are both running",
            "Choose one SidePulse process",
            False,
        )

    if inputs.socket_owner_pid is not None and known_pids and inputs.socket_owner_pid not in known_pids:
        return ProcessTruth(
            ProcessOwner.CONFLICT,
            "Another process owns the SidePulse event socket",
            "Choose one SidePulse process",
            False,
        )

    if launch_pid is not None:
        if inputs.socket_owner_pid not in (None, launch_pid):
            return ProcessTruth(
                ProcessOwner.CONFLICT,
                "The LaunchAgent does not own the SidePulse event socket",
                "Choose one SidePulse process",
                False,
            )
        return ProcessTruth(
            ProcessOwner.LAUNCH_AGENT,
            "SidePulse is running from the LaunchAgent",
            None,
            True,
        )

    if foreground_pid is not None:
        if inputs.socket_owner_pid not in (None, foreground_pid):
            return ProcessTruth(
                ProcessOwner.CONFLICT,
                "The foreground app does not own the SidePulse event socket",
                "Choose one SidePulse process",
                False,
            )
        return ProcessTruth(
            ProcessOwner.FOREGROUND,
            "SidePulse is running in the foreground",
            None,
            True,
        )

    if inputs.socket_owner_pid is not None:
        return ProcessTruth(
            ProcessOwner.UNKNOWN,
            "An unknown process owns the SidePulse event socket",
            "Stop the unknown SidePulse process",
            False,
        )

    if inputs.plist_installed and not inputs.launch_agent_loaded:
        return ProcessTruth(
            ProcessOwner.NONE,
            "Run at Login is installed but the LaunchAgent job is not loaded",
            "Start SidePulse at login",
            False,
        )

    return ProcessTruth(
        ProcessOwner.NONE,
        "SidePulse is not running",
        "Start SidePulse",
        False,
    )


__all__ = [
    "HookTruth",
    "HookTruthInputs",
    "HookTruthState",
    "ProcessOwner",
    "ProcessTruth",
    "ProcessTruthInputs",
    "classify_hook_truth",
    "classify_process_truth",
]
