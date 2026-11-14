"""Unit tests for deterministic click coordinates.

The centre of the detected bounds is mapped from the canonical frame
onto the guest display and used for every tap.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from rok_automation.game.adb import AdbInput
from rok_automation.game.interface import TimingConfig

_ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"


class TestClickCoordinates(unittest.TestCase):
    """Ensure clicks always use the centre of the detected bounds."""

    GUEST_SIZE = (1920, 1080)

    def _interface(self) -> AdbInput:
        interface = AdbInput.__new__(AdbInput)
        interface.adb_path = "adb"
        interface.serial = "emulator-5554"
        interface.timing = TimingConfig()
        interface.pause_flag_path = None
        interface.log = None
        interface._guest_size = self.GUEST_SIZE
        return interface

    def _write_frame(self, name: str) -> Path:
        path = _ARTIFACTS_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        import cv2

        cv2.imwrite(str(path), np.zeros((720, 1280, 3), dtype=np.uint8))
        return path

    def _tap_points(self, interface: AdbInput, bounds, screenshot) -> list[tuple[int, int]]:
        """Collect the guest pixels the interface taps."""

        tapped: list[tuple[int, int]] = []

        def fake_run(adb_path, serial, args, **kwargs):
            if "tap" in args:
                tapped.append((int(args[-2]), int(args[-1])))
            return b""

        with patch("rok_automation.game.adb._run", side_effect=fake_run), \
             patch("rok_automation.game.adb.time.sleep"):
            for _ in range(30):
                interface.click(bounds, str(screenshot))
        return tapped

    def test_click_uses_the_same_centre_across_calls(self) -> None:
        """Verify repeated calls tap exactly the same centre pixel."""

        interface = self._interface()
        # 100x60 button at (200, 100) -> reference center is (250, 130)
        bounds = (200, 100, 100, 60)
        ref_x = 200 + 100 // 2  # 250
        ref_y = 100 + 60 // 2   # 130
        screenshot = self._write_frame("_test_random_click.png")

        tapped_points = self._tap_points(interface, bounds, screenshot)
        self.assertTrue(tapped_points)

        guest_ref_x, guest_ref_y = interface._to_guest(ref_x, ref_y)

        for cx, cy in tapped_points:
            self.assertEqual((cx, cy), (guest_ref_x, guest_ref_y))

        self.assertEqual(set(tapped_points), {(guest_ref_x, guest_ref_y)})

    def test_click_uses_the_centre_of_a_narrow_element(self) -> None:
        """Verify narrow controls also use their exact centre."""

        interface = self._interface()
        # Narrow element: width=6, height=6 at (50, 50) -> reference center (53, 53)
        bounds = (50, 50, 6, 6)
        ref_x = 53
        ref_y = 53
        screenshot = self._write_frame("_test_random_click_narrow.png")

        tapped_points = self._tap_points(interface, bounds, screenshot)
        self.assertTrue(tapped_points)

        guest_ref_x, guest_ref_y = interface._to_guest(ref_x, ref_y)

        for cx, cy in tapped_points:
            self.assertEqual((cx, cy), (guest_ref_x, guest_ref_y))


if __name__ == "__main__":
    unittest.main()
