"""The Screen Bar and the strip must show the same colour.

Reported by the owner: "the colors flashing on the hardware are not the same
as the colors flashing on the screen above."

Channel gain corrects one physical strip's LED die response and was applied
only on the way to hardware. On this device that correction is green x0.38,
which drives white as #FF61FF, yellow as #FF6100 and cyan as #0061FF -- so
the on-screen preview and the strip were rendering different colours from
the same signal.
"""

from __future__ import annotations

from sidepulse.led_status import NEUTRAL_CHANNEL_GAINS, apply_channel_gain_to_program
from sidepulse.settings import AgentMonitorSettings, DeviceDisplaySetting
from sidepulse.status_bar import VIRTUAL_DEVICE_ID, StatusBarController


HARDWARE_GAINS = (1.0, 0.38041666666666674, 1.0)


def _device(device_id: str, gains: tuple[float, float, float]) -> DeviceDisplaySetting:
    red, green, blue = gains
    return DeviceDisplaySetting(
        device_id=device_id,
        name=device_id,
        path=device_id,
        led_display="agent",
        red_gain=red,
        green_gain=green,
        blue_gain=blue,
    )


class _Probe:
    def __init__(self, *, linked: bool) -> None:
        self.settings = AgentMonitorSettings(
            devices=(
                _device("/Volumes/SidePulse", HARDWARE_GAINS),
                _device(VIRTUAL_DEVICE_ID, NEUTRAL_CHANNEL_GAINS),
            ),
        ).with_link_screen_bar_to_hardware(linked)

    @property
    def virtual(self) -> DeviceDisplaySetting:
        return next(
            device
            for device in self.settings.devices
            if device.device_id == VIRTUAL_DEVICE_ID
        )


def test_linked_screen_bar_previews_what_the_strip_is_driven_with() -> None:
    probe = _Probe(linked=True)
    gains = StatusBarController.screen_bar_channel_gains(probe, probe.virtual)
    assert gains == HARDWARE_GAINS

    # The property that actually matters: same input, same rendered colour.
    for nominal in ("#FFFFFF", "#FFFF00", "#00FFFF", "#00FF00"):
        hardware = apply_channel_gain_to_program(nominal, HARDWARE_GAINS)
        screen = apply_channel_gain_to_program(nominal, gains)
        assert screen == hardware, f"{nominal} still renders differently"


def test_unlinked_screen_bar_keeps_its_own_calibration() -> None:
    """Someone tuning the notch on its own must not inherit the strip."""
    probe = _Probe(linked=False)
    gains = StatusBarController.screen_bar_channel_gains(probe, probe.virtual)
    assert gains == NEUTRAL_CHANNEL_GAINS


def test_an_uncalibrated_strip_does_not_override_the_screen_bar() -> None:
    """A neutral hardware device carries no opinion worth borrowing."""

    class _Neutral(_Probe):
        def __init__(self) -> None:
            self.settings = AgentMonitorSettings(
                devices=(
                    _device("/Volumes/SidePulse", NEUTRAL_CHANNEL_GAINS),
                    _device(VIRTUAL_DEVICE_ID, (1.0, 0.9, 1.0)),
                ),
            ).with_link_screen_bar_to_hardware(True)

    probe = _Neutral()
    gains = StatusBarController.screen_bar_channel_gains(probe, probe.virtual)
    assert gains == (1.0, 0.9, 1.0)
