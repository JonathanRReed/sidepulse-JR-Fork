from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sidepulse.keep_awake import KeepAwakeController
from sidepulse.lid_sleep import ClosedLidAwakeController
from sidepulse.models import AgentMode
from sidepulse.settings import (
    CLOSED_LID_AWAKE_AGENTS,
    CLOSED_LID_AWAKE_ALWAYS,
    AgentMonitorSettings,
)


class _Process:
    def __init__(self, command: list[str]) -> None:
        self.command = tuple(command)
        self.terminated = False
        self.killed = False

    def poll(self):
        return 0 if self.terminated or self.killed else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


class _Factory:
    def __init__(self) -> None:
        self.processes: list[_Process] = []

    def __call__(self, command, **_kwargs) -> _Process:
        process = _Process(list(command))
        self.processes.append(process)
        return process


def _caffeinate_processes(factory: _Factory) -> list[_Process]:
    return [
        process
        for process in factory.processes
        if process.command and process.command[0] == "/usr/bin/caffeinate"
    ]


def test_live_agent_hold_replaces_only_the_stale_caffeinate_child() -> None:
    factory = _Factory()
    controller = KeepAwakeController(
        process_factory=factory,
        watch_current_process=False,
    )
    assert controller.update(AgentMode.WORKING)
    first = factory.processes[-1]
    assert first.command == ("/usr/bin/caffeinate", "-ims")

    controller.set_keep_display_awake(True)

    assert first.terminated is True
    assert len(factory.processes) == 2
    assert factory.processes[-1].command == ("/usr/bin/caffeinate", "-dims")
    assert controller.process is factory.processes[-1]


def test_closed_lid_display_change_preserves_helper_and_watchdog(
    tmp_path: Path,
) -> None:
    factory = _Factory()
    system_disable_calls: list[bool] = []
    controller = ClosedLidAwakeController(
        process_factory=factory,
        sleep_disabled_reader=lambda: False,
        sleep_disabled_setter=system_disable_calls.append,
        watch_current_process=False,
        use_system_disable=True,
        renewal_path=tmp_path / "renewal",
    )
    assert controller.update(CLOSED_LID_AWAKE_ALWAYS, agents_active=False)
    watchdog = factory.processes[0]
    first = _caffeinate_processes(factory)[0]
    assert first.command[:2] == ("/usr/bin/caffeinate", "-imsu")
    assert system_disable_calls == [True]
    assert controller.changed_system_disable is True

    controller.set_keep_display_awake(True)

    caffeinate = _caffeinate_processes(factory)
    assert first.terminated is True
    assert len(caffeinate) == 2
    assert caffeinate[-1].command[:2] == ("/usr/bin/caffeinate", "-dimsu")
    assert watchdog.terminated is False
    assert controller.watchdog_process is watchdog
    assert controller.renewal_path.is_file()
    assert controller.changed_system_disable is True
    assert controller.last_requested is True
    assert system_disable_calls == [True]


def test_disabling_ordinary_agent_hold_does_not_disable_closed_lid_policy(
    tmp_path: Path,
) -> None:
    ordinary = KeepAwakeController(
        process_factory=_Factory(),
        watch_current_process=False,
    )
    ordinary.set_enabled(False)
    assert not ordinary.update(AgentMode.WORKING)
    assert ordinary.holding_requested is True

    closed = ClosedLidAwakeController(
        process_factory=_Factory(),
        watch_current_process=False,
        renewal_path=tmp_path / "renewal",
    )
    assert closed.update(
        CLOSED_LID_AWAKE_AGENTS,
        agents_active=ordinary.holding_requested,
    )
    assert closed.last_policy == CLOSED_LID_AWAKE_AGENTS


def test_battery_release_does_not_rewrite_display_choice() -> None:
    factory = _Factory()
    controller = KeepAwakeController(
        process_factory=factory,
        watch_current_process=False,
    )
    controller.set_keep_display_awake(True)

    assert not controller.update(
        AgentMode.WORKING,
        on_battery=True,
        hold_on_battery=False,
    )
    assert controller.keep_display_awake is True
    assert controller.process is None

    assert controller.update(
        AgentMode.WORKING,
        on_battery=False,
        hold_on_battery=False,
    )
    assert factory.processes[-1].command == ("/usr/bin/caffeinate", "-dims")


def test_status_controller_reads_agent_and_display_choices_each_sync() -> None:
    from sidepulse import status_bar

    calls: list[tuple[str, object]] = []

    class OrdinaryHold:
        last_error = None
        last_status_error = None
        holding_requested = False

        @staticmethod
        def process_running() -> bool:
            return False

        @staticmethod
        def set_enabled(value: bool) -> None:
            calls.append(("agent", value))

        @staticmethod
        def set_keep_display_awake(value: bool) -> None:
            calls.append(("display", value))

        @staticmethod
        def set_grace_seconds(value: float) -> None:
            calls.append(("grace", value))

        @staticmethod
        def update(mode: AgentMode, **_kwargs) -> bool:
            calls.append(("mode", mode))
            return False

    settings = (
        AgentMonitorSettings()
        .with_agent_keep_awake_enabled(False)
        .with_keep_display_awake(True)
    )
    target = SimpleNamespace(
        keep_awake=OrdinaryHold(),
        closed_lid_awake=SimpleNamespace(
            set_keep_display_awake=lambda value: calls.append(("closed-display", value))
        ),
        settings=settings,
        _production_battery_observation=None,
        last_keep_awake_error=None,
        sync_closed_lid_awake=lambda: None,
        leds_enabled=False,
    )

    status_bar.StatusBarController.sync_keep_awake(target, AgentMode.WORKING)

    assert ("agent", False) in calls
    assert ("display", True) in calls
    assert ("closed-display", True) in calls
