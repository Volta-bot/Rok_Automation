"""Unit tests for the shared input helpers.

These cover the click jitter and the swipe timing that both the ADB and
the desktop backends rely on.
"""

from __future__ import annotations

import unittest

from rok_automation.game.adb import swipe_duration_ms
from rok_automation.game.interface import jittered_click_point


class TestJitteredClickPoint(unittest.TestCase):
    """Verify the click point stays inside the control it belongs to."""

    def test_stays_inside_the_bounds(self) -> None:
        for _ in range(50):
            x, y = jittered_click_point((100, 200, 80, 40), (720, 1280), 3)
            self.assertGreaterEqual(x, 100)
            self.assertLess(x, 180)
            self.assertGreaterEqual(y, 200)
            self.assertLess(y, 240)

    def test_never_leaves_the_frame(self) -> None:
        x, y = jittered_click_point((0, 0, 4, 4), (720, 1280), 3)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_falls_back_to_the_centre_without_a_jitter(self) -> None:
        x, y = jittered_click_point((100, 200, 80, 40), (720, 1280), 0)
        self.assertEqual((x, y), (140, 220))

    def test_a_tiny_control_still_gets_a_single_pixel_offset(self) -> None:
        for _ in range(50):
            x, y = jittered_click_point((10, 10, 2, 2), (720, 1280), 3)
            self.assertGreaterEqual(x, 10)
            self.assertLess(x, 12)
            self.assertGreaterEqual(y, 10)
            self.assertLess(y, 12)

    def test_a_control_at_the_frame_edge_stays_inside(self) -> None:
        for _ in range(50):
            x, y = jittered_click_point((0, 0, 6, 6), (720, 1280), 3)
            self.assertGreaterEqual(x, 0)
            self.assertLess(x, 6)
            self.assertGreaterEqual(y, 0)
            self.assertLess(y, 6)


class TestSwipeDuration(unittest.TestCase):
    """Verify a drag is stretched so the game reads it as a drag."""

    def test_a_short_drag_uses_the_base_duration(self) -> None:
        self.assertEqual(swipe_duration_ms(10.0, 0.30), 300)

    def test_a_long_drag_is_stretched(self) -> None:
        self.assertEqual(swipe_duration_ms(1000.0, 0.30), 800)

    def test_the_duration_is_capped(self) -> None:
        self.assertEqual(swipe_duration_ms(100000.0, 0.30), 1200)

    def test_a_zero_distance_still_uses_the_base_duration(self) -> None:
        self.assertEqual(swipe_duration_ms(0.0, 0.25), 250)


if __name__ == "__main__":
    unittest.main()
