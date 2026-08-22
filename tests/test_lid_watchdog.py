"""The lid-hold fail-safe: a hold whose renewals stop must die alone.

The burn: release depended entirely on the app's own sync tick, and a
tick that stops (display-asleep App Nap, a wedge, a bootout mid-hold)
left `pmset disablesleep 1` burning a closed laptop all night."""

from __future__ import annotations

from pathlib import Path

from sidepulse.lid_sleep import (
    CAFFEINATE_CLOSED_LID_COMMAND,
    RENEWAL_STALE_SECONDS,
    ClosedLidAwakeController,
    watchdog_script,
)
from sidepulse.settings import CLOSED_LID_AWAKE_AGENTS, CLOSED_LID_AWAKE_NEVER


class FakeProcess:
    def __init__(self, argv):
        self.argv = argv
        self.terminated = False

    def poll(self):
        return 1 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def _controller(tmp_path: Path, spawned: list):
    def factory(argv, **_kwargs):
        process = FakeProcess(argv)
        spawned.append(process)
        return process

    return ClosedLidAwakeController(
        process_factory=factory,
        sleep_disabled_reader=lambda: False,
        sleep_disabled_setter=lambda _enabled: None,
        use_system_disable=True,
        renewal_path=tmp_path / "lid-hold-renewal",
    )


def test_holding_writes_the_heartbeat_and_spawns_the_watchdog(tmp_path) -> None:
    spawned: list[FakeProcess] = []
    controller = _controller(tmp_path, spawned)
    controller.update(CLOSED_LID_AWAKE_AGENTS, agents_active=True)

    assert (tmp_path / "lid-hold-renewal").exists()
    watchdog = [p for p in spawned if p.argv[0] == "/bin/sh"]
    assert len(watchdog) == 1
    script = watchdog[0].argv[2]
    assert "pmset -a disablesleep 0" in script
    assert str(RENEWAL_STALE_SECONDS) in script
    # One watchdog per hold, not one per tick.
    controller.update(CLOSED_LID_AWAKE_AGENTS, agents_active=True)
    assert len([p for p in spawned if p.argv[0] == "/bin/sh"]) == 1


def test_clean_release_retires_the_heartbeat(tmp_path) -> None:
    spawned: list[FakeProcess] = []
    controller = _controller(tmp_path, spawned)
    controller.update(CLOSED_LID_AWAKE_AGENTS, agents_active=True)
    assert (tmp_path / "lid-hold-renewal").exists()

    controller.update(CLOSED_LID_AWAKE_NEVER, agents_active=False)
    assert not (tmp_path / "lid-hold-renewal").exists()


def test_caffeinate_hold_is_time_bounded() -> None:
    assert "-t" in CAFFEINATE_CLOSED_LID_COMMAND


def test_watchdog_script_is_selfcontained_and_quoted(tmp_path) -> None:
    script = watchdog_script(tmp_path / "weird name with spaces")
    assert "'" in script  # the path survived quoting
    assert "exit 0" in script
