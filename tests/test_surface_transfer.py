"""One logical colour, two surfaces, the same light.

The reported symptom was "the colours flashing on the hardware are not the
same as the colours flashing on the screen above", and the cause was not the
palette and not the channel gain. It was that the same 8-bit code meant two
different amounts of light:

    LED strip   light proportional to (code/255)**1.0   -- linear PWM
    Screen Bar  light proportional to (code/255)**1.89  -- a per-channel tone
                map (LED_GAMMA 0.86) plus 1.22/1.46 boosts that clipped

With every gain neutral the two surfaces already disagreed by dE2000 8.4 at
idle (the strip 12.9x brighter relative to full), 17.6 at ask -- red on screen,
orange on the strip -- 9.3 at done and 18.5 at calendar. A "50% fade ceiling"
emitted 50% of full light on the strip and 21% on screen: the same dial, two
different animations.

The fix is one shared statement of what a palette hex MEANS (the light an sRGB
display emits for it) and one translation per surface, applied at the last
step. The strip decodes into its own linear PWM units; the screen, already
being sRGB, does nothing at all, which it could only do once the tone map was
gone.

What is deliberately NOT changed: the stored channel gains and their
arithmetic. The owner matched (255, 97, 255) to a white reference patch by eye,
and under linear PWM the drive byte IS the light, so his multiply was already
in the right domain. Every test here that touches a gain exists to keep it
that way.
"""

from __future__ import annotations

import pytest

from sidepulse.colors import relative_luminance
from sidepulse.led_status import (
    NEUTRAL_CHANNEL_GAINS,
    STRIP_CODE_TO_LIGHT_EXPONENT,
    STRIP_MIN_LIT_DRIVE,
    apply_brightness,
    apply_channel_gain_to_hex,
    apply_strip_transfer_to_hex,
    apply_strip_transfer_to_program,
    linear_to_srgb,
    scale_hex_brightness,
    srgb_to_linear,
    strip_drive_code,
)
from sidepulse.virtual_device import (
    LED_CORE_BOOST,
    LED_HOTLINE_BOOST,
    tone_mapped_led_color,
)

# The physical device's own calibration, read off the owner's machine, and the
# byte the old maths drove green to at white: round(255 * 0.380417).
OWNER_GAINS = (1.0, 0.38041666666666674, 1.0)
OWNER_WHITE_DRIVE_CODE = 97

# Every state colour the app can put on either surface.
STATE_COLORS = {
    "idle": "#020204",
    "working": "#00E5FF",
    "done": "#00FF66",
    "ask": "#FF3A00",
    "low_battery": "#E01010",
    "calendar": "#A45CFF",
    "notification": "#34C759",
    "reminders": "#FFB340",
    "weather": "#FF2D55",
    "quota": "#FFB020",
}

# One 8-bit step of linear PWM. Neither surface can be more precise than the
# coarser of the two, so this is the floor on any agreement between them.
ONE_DRIVE_STEP = 1.0 / 255.0


def _codes(hex_color: str) -> tuple[int, int, int]:
    cleaned = hex_color.lstrip("#")
    return tuple(int(cleaned[index : index + 2], 16) for index in (0, 2, 4))


def _strip_light(hex_color: str, gains=NEUTRAL_CHANNEL_GAINS) -> tuple[float, ...]:
    """Relative light the strip emits, through the real transform."""
    drives = _codes(apply_strip_transfer_to_hex(hex_color, gains))
    return tuple(
        (drive / 255.0) ** STRIP_CODE_TO_LIGHT_EXPONENT for drive in drives
    )


def _screen_light(hex_color: str, boost: float = LED_CORE_BOOST) -> tuple[float, ...]:
    """Relative light the notch emits, through the real draw-path transform."""
    red, green, blue = (code / 255.0 for code in _codes(hex_color))
    painted = tone_mapped_led_color(red, green, blue, 1.0, boost=boost)
    return tuple(srgb_to_linear(channel) for channel in painted[:3])


def _as_hex(light: tuple[float, ...]) -> str:
    """The sRGB hex that emits this light -- i.e. what the surface looks like."""
    return "#" + "".join(f"{round(linear_to_srgb(c) * 255.0):02X}" for c in light)


# --- the reconciliation ----------------------------------------------------


@pytest.mark.parametrize(
    "name", [key for key in STATE_COLORS if key != "idle"]
)
def test_both_surfaces_emit_the_same_relative_light_for_the_same_hex(
    name: str,
) -> None:
    """The headline. Every state, both surfaces, channel by channel."""
    hex_color = STATE_COLORS[name]
    strip = _strip_light(hex_color)
    screen = _screen_light(hex_color)
    for emitted, painted in zip(strip, screen):
        assert emitted == pytest.approx(painted, abs=1.5 * ONE_DRIVE_STEP)
    # And therefore they look like each other, and like the hex itself.
    assert relative_luminance(_as_hex(strip)) == pytest.approx(
        relative_luminance(hex_color), abs=0.01
    )
    assert relative_luminance(_as_hex(screen)) == pytest.approx(
        relative_luminance(hex_color), abs=0.01
    )


def test_the_notch_no_longer_turns_the_ask_colour_orange() -> None:
    """#FF3A00 read as #FF5700 on screen: red was already at full so the 1.22
    core boost clipped it while green took its whole 22%, and the hue slid.
    That is not bloom, it is a hue error, and it is the single most visible
    instance of the mismatch."""
    assert _as_hex(_screen_light("#FF3A00")) == "#FF3A00"
    # Every layer of the glow, not just the core: a shared scale may change a
    # LEVEL but may never change the ratio between the channels, which is what
    # "the same colour" means.
    for boost in (LED_CORE_BOOST, LED_HOTLINE_BOOST, 1.46, 0.82, 0.64):
        red, green, blue, _alpha = tone_mapped_led_color(
            1.0, 0x3A / 255.0, 0.0, 1.0, boost=boost
        )
        assert green / red == pytest.approx(0x3A / 255.0, rel=1e-9)
        assert blue == 0.0
        assert red <= 1.0  # and nothing may clip, which is how the ratio broke


def test_a_fade_ceiling_is_the_same_breath_on_both_surfaces() -> None:
    """0.5 emitted 50% of full light on the strip and 21% on screen -- the
    same dial driving two different animations."""
    for hex_color in ("#00E5FF", "#FF3A00", "#00FF66"):
        half = scale_hex_brightness(hex_color, 0.5)
        strip_fraction = sum(_strip_light(half)) / sum(_strip_light(hex_color))
        screen_fraction = sum(_screen_light(half)) / sum(_screen_light(hex_color))
        assert strip_fraction == pytest.approx(screen_fraction, abs=0.01)
        # ...and it is the honest number for "half of an sRGB code", not 50%.
        assert 0.19 < strip_fraction < 0.24


def test_device_brightness_dims_both_surfaces_by_the_same_amount() -> None:
    """The firmware multiplies the DRIVE bytes by N/255, so on the strip
    brightness is a scale on light, while the Screen Bar's engine multiplies
    the encoded code. Decoding N as well is what makes them agree."""
    program = apply_brightness("#00E5FF 1600ms pulse", 128)
    assert "brightness 128" in program
    transferred = apply_strip_transfer_to_program(program)
    strip_scale = strip_drive_code(128) / 255.0

    # Screen: the engine multiplies the code, then the panel decodes.
    screen_scale = srgb_to_linear(round(255 * (128 / 255.0)) / 255.0)
    assert f"brightness {round(strip_scale * 255)}" in transferred
    assert strip_scale == pytest.approx(screen_scale, abs=0.01)


# --- the strip's assumption, stated once and correctable -------------------


def test_the_strips_response_is_one_named_constant() -> None:
    """It is an INFERENCE from LEDS_FORMAT.md ("Brightness scales the RGB
    values", no gamma anywhere in the DSL spec), not a measurement. If someone
    holds the strip beside a #808080 patch and they match, the firmware
    decodes sRGB itself and the whole transform has to collapse to identity --
    which it does, from this constant alone, with no other edit."""
    import sidepulse._led_status_legacy as led_status

    assert STRIP_CODE_TO_LIGHT_EXPONENT == 1.0
    assert strip_drive_code(128) == 55  # linear PWM: half the code, a fifth of full

    original = led_status.STRIP_CODE_TO_LIGHT_EXPONENT
    try:
        led_status.STRIP_CODE_TO_LIGHT_EXPONENT = 2.4
        # An sRGB-decoding firmware wants the code back essentially unchanged,
        # and gets it: within 4% of full scale, against the 57% cut the linear
        # reading applies. 128 comes back as 135, not 55. (The residual is the
        # standard's linear toe against a pure 2.4 power; if a measurement ever
        # lands here, invert with linear_to_srgb rather than widening this.)
        assert strip_drive_code(255) == 255
        for code in (200, 128, 64):
            assert abs(strip_drive_code(code) - code) <= 10
    finally:
        led_status.STRIP_CODE_TO_LIGHT_EXPONENT = original
    assert strip_drive_code(128) == 55  # and the lever is back where it was


def test_a_lit_led_holds_its_floor_only_when_it_can_hold_its_hue() -> None:
    """The per-CHANNEL floor survives (a channel that means light never
    rounds to nothing inside a color that can say its hue), but a WHOLE
    LED whose brightest drive lands below STRIP_HUE_HOLDING_DRIVE now
    goes honestly dark. At drive 1-2 the green die out-emits red and
    blue several times over, so the old always-lit floor painted
    'barely-visible white' as a clearly green glow -- photographed live
    2026-08-20: 'why is the SidePulse green when it should be off.'"""
    assert strip_drive_code(1) == STRIP_MIN_LIT_DRIVE
    assert strip_drive_code(2) == STRIP_MIN_LIT_DRIVE
    assert strip_drive_code(0) == 0
    # The whisper is below the hue-holding line: honest black now.
    assert apply_strip_transfer_to_hex("#020204", NEUTRAL_CHANNEL_GAINS) == "#000000"
    assert strip_drive_code(0, 0.0) == 0  # a zero gain is still off


# --- the calibration that must not move ------------------------------------


def test_the_owners_white_still_drives_the_byte_he_matched_by_eye() -> None:
    """The only ground truth in the whole pipeline: codes (255, 97, 255) look
    neutral on his strip. At the calibration reference every channel is at
    full, decode(1.0) == 1.0, and the transfer collapses to the same multiply
    he tuned -- so this byte is invariant under the change."""
    assert apply_strip_transfer_to_hex("#FFFFFF", OWNER_GAINS) == "#FF61FF"
    assert 0x61 == OWNER_WHITE_DRIVE_CODE
    assert strip_drive_code(255, OWNER_GAINS[1]) == OWNER_WHITE_DRIVE_CODE


def test_the_channel_gain_is_still_a_plain_multiply() -> None:
    """A guard against re-adopting the refuted "gamma-correct the gain" fix.

    Reading the stored 0.38 as a gamma-encoded number and decoding it to
    0.1196 preserves the drive byte at white and at black and nowhere else:
    only 19 of 256 green codes survive, ask loses 27% of its green, and codes
    2-4 lose their green channel entirely. Under linear PWM the drive byte IS
    the light, so the stored number is already a linear-light white balance
    and the multiply is already in the right domain.
    """
    assert apply_channel_gain_to_hex("#FFFFFF", OWNER_GAINS) == "#FF61FF"
    assert apply_channel_gain_to_hex("#00FF00", (1.0, 0.5, 1.0)) == "#008000"
    # The refuted alternative, written out so it cannot be reintroduced by
    # accident: decoding the gain drives ask's green to 16 instead of 22.
    decoded = srgb_to_linear(OWNER_GAINS[1])
    assert decoded == pytest.approx(0.11955457882894463)
    assert round(0x3A * decoded * 255 / 255) != round(0x3A * OWNER_GAINS[1])
    # And the OTHER tempting number, which drives white to 164 and undoes 69%
    # of a calibration verified by eye.
    assert round(255 * OWNER_GAINS[1] ** (1 / 2.2)) == 164


def test_the_white_balance_now_holds_all_the_way_down_the_fade() -> None:
    """A calibration is a promise about the RATIO of light between channels.
    Because the strip's drive byte is linear light, the ratio after the
    transfer is exactly the gain at every level -- which is what the old
    encoded multiply achieved only by accident, and what decoding the gain
    would have destroyed (0.38 at full, 0.15 by the 10% level).
    """
    for level in (1.0, 0.5, 0.25):
        code = round(255 * level)
        source = f"#{code:02X}{code:02X}{code:02X}"
        red, green, _blue = _codes(apply_strip_transfer_to_hex(source, OWNER_GAINS))
        assert green / red == pytest.approx(OWNER_GAINS[1], abs=0.02)


# --- the write boundary ----------------------------------------------------


def test_the_transform_rewrites_hexes_and_brightness_and_nothing_else() -> None:
    program = "brightness 128\noff 160ms cosine\n0:#00E5FF 760ms pulse 0ms\nrepeat"
    result = apply_strip_transfer_to_program(program, NEUTRAL_CHANNEL_GAINS)
    assert "#00E5FF" not in result
    assert "brightness 128" not in result
    assert "off 160ms cosine" in result
    assert "760ms pulse 0ms" in result
    assert result.endswith("repeat")
    assert result.count("#") == 1


def test_neutral_gains_still_change_the_program_because_the_surface_differs() -> None:
    """The transfer is not a no-op at neutral gains, and that is the point:
    an uncalibrated strip was just as mismatched as a calibrated one."""
    program = "#00E5FF 1600ms pulse"
    assert apply_strip_transfer_to_program(program, NEUTRAL_CHANNEL_GAINS) != program
    # Full-scale channels are the fixed points of the transform, so a pure
    # primary is untouched -- which is why the old bug hid at white.
    assert apply_strip_transfer_to_hex("#FF00FF", NEUTRAL_CHANNEL_GAINS) == "#FF00FF"


def test_the_controller_writes_transferred_bytes_to_the_device(tmp_path) -> None:
    """The one that matters: what actually lands in LEDS.LED."""
    from sidepulse.led_status import AgentLedController
    from sidepulse.models import AgentMode

    device = tmp_path / "SidePulsePro"
    device.mkdir()
    (device / "LEDS.LED").touch()

    controller = AgentLedController(device_path=device)
    controller.channel_gains = OWNER_GAINS
    controller.sync_mode(AgentMode.WORKING)
    written = (device / "LEDS.LED").read_text()

    assert "#00E5FF" not in written  # the nominal colour never reaches the strip
    assert apply_strip_transfer_to_hex("#00E5FF", OWNER_GAINS) in written
    # ...and specifically not the old encoded-gain-only bytes.
    assert apply_channel_gain_to_hex("#00E5FF", OWNER_GAINS) not in written


def test_the_dedupe_identity_goes_through_the_same_transform(tmp_path) -> None:
    """The identity used for write-dedup has to be post-processed exactly like
    the live program, or a calibration change stops invalidating it."""
    from sidepulse.led_status import AgentLedController

    controller = AgentLedController(device_path=tmp_path, dry_run=True)
    controller.channel_gains = NEUTRAL_CHANNEL_GAINS
    neutral = controller._for_strip("#00E5FF 1600ms pulse")
    controller.channel_gains = OWNER_GAINS
    calibrated = controller._for_strip("#00E5FF 1600ms pulse")
    assert neutral != calibrated


# --- the transfer itself ---------------------------------------------------


def test_srgb_transfer_matches_the_standard_at_its_named_points() -> None:
    assert srgb_to_linear(0.0) == 0.0
    assert srgb_to_linear(1.0) == pytest.approx(1.0)
    # The piecewise knee, where the linear segment hands over to the power
    # segment. Both branches must agree there or the curve has a step in it.
    assert srgb_to_linear(0.04045) == pytest.approx(0.0031308, rel=1e-4)
    assert linear_to_srgb(0.0031308) == pytest.approx(0.04045, rel=1e-4)
    # Mid grey: the canonical sanity check that this is a real decode and not
    # a linear pass-through.
    assert srgb_to_linear(0.5) == pytest.approx(0.21404, rel=1e-4)


@pytest.mark.parametrize("code", range(0, 256))
def test_every_8_bit_code_survives_a_decode_encode_round_trip(code: int) -> None:
    """No colour may drift just for passing through the shared maths."""
    restored = round(linear_to_srgb(srgb_to_linear(code / 255.0)) * 255.0)
    assert restored == code


@pytest.mark.parametrize("code", range(0, 256))
def test_the_strip_transform_is_monotonic_and_bounded(code: int) -> None:
    drive = strip_drive_code(code)
    assert 0 <= drive <= code  # linear PWM of an sRGB code can only go down
    assert drive >= strip_drive_code(max(0, code - 1))
