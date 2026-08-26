"""Provider-neutral AI agent status monitoring.

Exports resolve lazily (PEP 562). Importing this package used to cost ~117 ms
because it eagerly pulled in `battery`, `collector`, `led_status` and their
transitive graph -- and the single hottest importer is `hook_entry`, which runs
as its own short-lived process on *every* hook event and needs none of it.
`from sidepulse import X` and `sidepulse.X` both still work; they just pay for
the one module that actually defines X.
"""

from importlib import import_module
from typing import TYPE_CHECKING

_LAZY_EXPORTS: dict[str, str] = {
    "BatteryLedController": "battery",
    "BatterySnapshot": "battery",
    "program_for_battery": "battery",
    "read_battery_snapshot": "battery",
    "AgentMonitor": "collector",
    "LiveAgentMonitor": "collector",
    "MonitorSnapshot": "collector",
    "SourceSpec": "collector",
    "HookEventServer": "ipc",
    "default_event_socket_path": "ipc",
    "default_latest_state_path": "ipc",
    "send_hook_event": "ipc",
    "AgentLedController": "led_status",
    "LedDisplayState": "led_status",
    "display_state_for_mode": "led_status",
    "program_for_display_state": "led_status",
    "write_mode_to_leds": "led_status",
    "ClosedLidAwakeController": "lid_sleep",
    "install_sleep_helper": "lid_sleep",
    "read_lid_closed": "lid_sleep",
    "read_sleep_disabled": "lid_sleep",
    "run_sudo_pmset_disablesleep": "lid_sleep",
    "sleep_helper_install_command": "lid_sleep",
    "sleep_helper_installed": "lid_sleep",
    "uninstall_sleep_helper": "lid_sleep",
    "AgentMode": "models",
    "AgentStatus": "models",
    "AggregateStatus": "models",
    "HookEvent": "models",
}

if TYPE_CHECKING:  # pragma: no cover - import-time cost is the whole point
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


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value  # Resolve once; later lookups skip __getattr__.
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


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

__version__ = "0.4.0"
