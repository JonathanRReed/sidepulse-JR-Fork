"""Battery-aware keep-awake: a positive battery reading may release the
hold when the owner opted out; an unknown power state never does."""

from __future__ import annotations

from sidepulse.keep_awake import KeepAwakeController
from sidepulse.models import AgentMode


class _Process:
    def __init__(self, *args, **kwargs):
        self.terminated = False

    def poll(self):
        return 1 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def _controller() -> KeepAwakeController:
    return KeepAwakeController(process_factory=_Process, watch_current_process=False)


def test_on_battery_with_default_setting_still_holds() -> None:
    controller = _controller()
    assert controller.update(AgentMode.WORKING, on_battery=True, hold_on_battery=True)
    assert controller.process_running()


def test_on_battery_opt_out_releases_and_stays_released() -> None:
    controller = _controller()
    assert controller.update(AgentMode.WORKING, on_battery=False, hold_on_battery=False)
    assert not controller.update(
        AgentMode.WORKING, on_battery=True, hold_on_battery=False
    )
    assert not controller.process_running()


def test_unknown_power_state_never_releases() -> None:
    controller = _controller()
    assert controller.update(AgentMode.WORKING, on_battery=None, hold_on_battery=False)
    assert controller.process_running()


def test_settings_round_trip_keep_awake_on_battery(tmp_path) -> None:
    from sidepulse.settings import AgentMonitorSettings, load_settings, save_settings

    path = tmp_path / "settings.json"
    saved = AgentMonitorSettings().with_keep_awake_on_battery(False)
    save_settings(saved, path)
    assert load_settings(path).keep_awake_on_battery is False
