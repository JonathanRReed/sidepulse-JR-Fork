from __future__ import annotations

import unittest

from examples.audio_monitor import (
    build_led_program,
    green_to_red_rgb,
    rms_to_level,
    smooth_level,
)
from sidepulse.device_writer import MAX_LED_BYTES, validate_led_text


class AudioMonitorExampleTests(unittest.TestCase):
    def test_dim_idle_gradient_stays_within_led_limits(self) -> None:
        program = build_led_program(0.0, led_count=8, idle_brightness=0.08)

        validate_led_text(program)
        self.assertLessEqual(len(program.encode("utf-8")), MAX_LED_BYTES)
        self.assertEqual(program.count("; "), 7)
        self.assertIn("0:#001400 90ms cosine", program)
        self.assertIn("7:#140000 90ms cosine", program)

    def test_full_level_brightens_green_to_red_bar(self) -> None:
        program = build_led_program(1.0, led_count=8, transition_ms=0)

        self.assertTrue(program.startswith("0:#00FF00; "))
        self.assertIn("3:#DBFF00", program)
        self.assertIn("4:#FFDB00", program)
        self.assertTrue(program.endswith("7:#FF0000"))

    def test_max_brightness_uses_firmware_brightness_command(self) -> None:
        program = build_led_program(1.0, led_count=8, max_brightness=0.5, transition_ms=0)

        validate_led_text(program)
        self.assertTrue(program.startswith("brightness 128\n"))
        self.assertIn("0:#00FF00", program)
        self.assertTrue(program.endswith("7:#FF0000"))

    def test_half_level_keeps_back_half_dim(self) -> None:
        program = build_led_program(0.5, led_count=8, idle_brightness=0.08, transition_ms=0)
        segments = program.split("; ")

        self.assertEqual(segments[0], "0:#00FF00")
        self.assertEqual(segments[3], "3:#DBFF00")
        self.assertEqual(segments[4], "4:#141200")
        self.assertEqual(segments[7], "7:#140000")

    def test_color_gradient_endpoints(self) -> None:
        self.assertEqual(green_to_red_rgb(0, 8), (0, 255, 0))
        self.assertEqual(green_to_red_rgb(7, 8), (255, 0, 0))

    def test_audio_mapping_uses_db_range(self) -> None:
        self.assertEqual(rms_to_level(0.0, noise_floor_db=-60, peak_db=0, curve=1), 0.0)
        self.assertAlmostEqual(
            rms_to_level(10 ** (-30 / 20), noise_floor_db=-60, peak_db=0, curve=1),
            0.5,
            places=6,
        )
        self.assertEqual(rms_to_level(1.0, noise_floor_db=-60, peak_db=0, curve=1), 1.0)

    def test_attack_is_faster_than_release(self) -> None:
        rising = smooth_level(0.0, 1.0, elapsed=0.05, attack_seconds=0.05, release_seconds=0.3)
        falling = smooth_level(1.0, 0.0, elapsed=0.05, attack_seconds=0.05, release_seconds=0.3)

        self.assertGreater(rising, 0.6)
        self.assertGreater(falling, 0.8)
        self.assertGreater(rising, 1.0 - falling)


if __name__ == "__main__":
    unittest.main()
