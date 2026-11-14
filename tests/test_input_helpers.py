"""Unit tests for shared swipe timing helpers.

These cover swipe timing that both the ADB and desktop backends rely on.
"""

from __future__ import annotations

import unittest

from rok_automation.game.adb import swipe_duration_ms
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
