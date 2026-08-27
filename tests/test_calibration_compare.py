"""The calibration stepper's A/B compare must never eat the tuning.

The popover is transient -- any outside click closes it. Closing while
the compare view is showing "before" used to persist the before gains
and strand the user's tuned gains in a RAM stash (2026-08-27 audit).
"""

import pytest

pytest.importorskip("AppKit")


@pytest.fixture
def controller(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    for target in (
        "sidepulse.settings.default_settings_path",
        "sidepulse.status_bar.default_settings_path",
    ):
        monkeypatch.setattr(target, lambda _p=settings_path: _p)
    monkeypatch.setattr(
        "sidepulse.status_bar.default_latest_state_path",
        lambda: tmp_path / "latest.json",
    )
    monkeypatch.setattr("sidepulse.status_bar.discover_devices", lambda: [])
    monkeypatch.setattr(
        "sidepulse.focus_sync.active_focus_mode_identifiers", lambda: []
    )
    from sidepulse import status_bar

    built = status_bar.StatusBarController.alloc().init()
    yield built
    worker = getattr(built, "led_worker_thread", None)
    if worker is not None and worker.is_alive():
        worker.join(timeout=5.0)


class _Sender:
    def __init__(self, payload):
        self._payload = payload

    def representedObject(self):
        return self._payload


DEVICE = "dev-under-test"
TUNED = (0.88, 1.0, 0.96)


def _tune(controller):
    for channel, value in zip(("red", "green", "blue"), TUNED):
        controller.set_device_channel_gain(DEVICE, channel, value)
    assert controller.settings.channel_gains_for_device(DEVICE) == TUNED


def _seed_popover_open_state(controller):
    # What openDeviceCalibrationPopover_ records: the before-snapshot.
    controller._calibration_compare_baseline = {DEVICE: (1.0, 1.0, 1.0)}
    controller._calibration_compare_stash = {}


def test_compare_round_trip_returns_the_tuned_gains(controller):
    _tune(controller)
    _seed_popover_open_state(controller)
    sender = _Sender(DEVICE)

    controller.toggleCalibrationCompare_(sender)
    assert controller.settings.channel_gains_for_device(DEVICE) == (1.0, 1.0, 1.0)

    controller.toggleCalibrationCompare_(sender)
    assert controller.settings.channel_gains_for_device(DEVICE) == TUNED
    assert not controller._calibration_compare_stash


def test_closing_mid_compare_restores_the_tuned_gains(controller):
    _tune(controller)
    _seed_popover_open_state(controller)

    controller.toggleCalibrationCompare_(_Sender(DEVICE))
    assert controller.settings.channel_gains_for_device(DEVICE) == (1.0, 1.0, 1.0)

    # A stray click dismisses the transient popover.
    controller.popoverDidClose_(None)

    assert controller.settings.channel_gains_for_device(DEVICE) == TUNED
    assert not controller._calibration_compare_stash
