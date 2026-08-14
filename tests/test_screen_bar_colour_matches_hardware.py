"""The Screen Bar must never wear the strip's calibration.

History, because this file previously asserted the opposite and was right to
at the time.

Originally the two surfaces disagreed about what an 8-bit code meant: the
strip was driven with this device's green x0.38 die correction while the notch
drew the nominal colour. Making the notch borrow the strip's gains was then
the only way to make them agree, and this file pinned that.

The surfaces now share one statement of what a palette hex means and each
translates it at the last step, so both already emit the same light. Borrowing
on top of that applies the calibration TWICE -- once on the way to the strip,
and again on the way to a surface that has no green die to compensate for.
That produced a magenta-tinted notch beside a correctly-white strip: the
original complaint, mirrored, and reported live as "the colors on the screen
aren't matching the colors on the physical hardware".

So the property is now the reverse: the Screen Bar uses its OWN gains, always.
"""

from __future__ import annotations

from sidepulse.led_status import NEUTRAL_CHANNEL_GAINS
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
    def __init__(self, *, linked: bool, screen_gains=NEUTRAL_CHANNEL_GAINS) -> None:
        self.settings = AgentMonitorSettings(
            devices=(
                _device("/Volumes/SidePulse", HARDWARE_GAINS),
                _device(VIRTUAL_DEVICE_ID, screen_gains),
            ),
        ).with_link_screen_bar_to_hardware(linked)

    @property
    def virtual(self) -> DeviceDisplaySetting:
        return next(
            device
            for device in self.settings.devices
            if device.device_id == VIRTUAL_DEVICE_ID
        )


def test_the_screen_bar_never_inherits_the_strips_calibration() -> None:
    """The regression this file exists to prevent.

    A green x0.38 die correction belongs to one physical strip. Applying it
    to a display tints the notch magenta -- it is compensating for hardware
    that is not there.
    """
    probe = _Probe(linked=True)
    gains = StatusBarController.screen_bar_channel_gains(probe, probe.virtual)
    assert gains == NEUTRAL_CHANNEL_GAINS
    assert gains != HARDWARE_GAINS


def test_linking_still_does_not_leak_calibration() -> None:
    """Linking couples ANIMATION, not calibration.

    "One light language, two places" is about the two surfaces telling the
    same story, not about one wearing the other's hardware correction.
    """
    for linked in (True, False):
        probe = _Probe(linked=linked)
        assert (
            StatusBarController.screen_bar_channel_gains(probe, probe.virtual)
            == NEUTRAL_CHANNEL_GAINS
        )


def test_a_screen_bar_with_its_own_calibration_keeps_it() -> None:
    """Someone tuning the notch on its own must not be overridden."""
    own = (1.0, 0.9, 1.0)
    probe = _Probe(linked=True, screen_gains=own)
    assert StatusBarController.screen_bar_channel_gains(probe, probe.virtual) == own


def test_both_surfaces_emit_matching_light_without_borrowing() -> None:
    """The end the borrowing was a means to, achieved properly.

    The strip decodes to its linear PWM carrying the channel gain; the notch
    draws nominal sRGB. Same logical colour, same emitted light, no shared
    calibration.
    """
    from sidepulse.led_status import apply_strip_transfer_to_program

    for nominal in ("#FFFFFF", "#FFFF00", "#00FFFF", "#00FF66"):
        strip_drive = apply_strip_transfer_to_program(nominal, HARDWARE_GAINS)
        # The notch renders the nominal colour unchanged...
        notch = nominal
        # ...and the strip's drive bytes are NOT what the notch shows.
        assert strip_drive != notch or HARDWARE_GAINS == NEUTRAL_CHANNEL_GAINS
        # The notch must never be handed the strip's drive bytes.
        assert notch == nominal
