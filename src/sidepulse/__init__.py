"""Provider-neutral AI agent status monitoring."""

from .battery import (
    BatteryLedController,
    BatterySnapshot,
    program_for_battery,
    read_battery_snapshot,
)
from .collector import AgentMonitor, LiveAgentMonitor, MonitorSnapshot, SourceSpec
from .ipc import (
    HookEventServer,
    default_event_socket_path,
    default_latest_state_path,
    send_hook_event,
)
from .led_status import (
    AgentLedController,
    LedDisplayState,
    display_state_for_mode,
    program_for_display_state,
    write_mode_to_leds,
)
from .lid_sleep import (
    ClosedLidAwakeController,
    install_sleep_helper,
    read_lid_closed,
    read_sleep_disabled,
    run_sudo_pmset_disablesleep,
    sleep_helper_install_command,
    sleep_helper_installed,
    uninstall_sleep_helper,
)
from .models import AgentMode, AgentStatus, AggregateStatus, HookEvent

__all__ = [
    "AgentLedController",
    "AgentMode",
    "AgentMonitor",
    "AgentStatus",
    "AggregateStatus",
    "BatteryLedController",
    "BatterySnapshot",
    "ClosedLidAwakeController",
    "HookEvent",
    "HookEventServer",
    "LedDisplayState",
    "LiveAgentMonitor",
    "MonitorSnapshot",
    "SourceSpec",
    "default_event_socket_path",
    "default_latest_state_path",
    "display_state_for_mode",
    "install_sleep_helper",
    "program_for_battery",
    "program_for_display_state",
    "read_battery_snapshot",
    "read_lid_closed",
    "read_sleep_disabled",
    "run_sudo_pmset_disablesleep",
    "send_hook_event",
    "sleep_helper_install_command",
    "sleep_helper_installed",
    "uninstall_sleep_helper",
    "write_mode_to_leds",
]

__version__ = "0.1.0"
