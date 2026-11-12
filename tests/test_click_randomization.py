"""Unit tests for randomized mouse click coordinates."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.game.interface import DesktopInterface, TimingConfig
from rok_automation.game.desktop import EmulatorWindow, click_window_point

_TEST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent
_ARTIFACTS_DIR = _PROJECT_ROOT / "artifacts"


class TestClickRandomization(unittest.TestCase):
    """Test suite ensuring mouse clicks apply small randomized offsets."""

    def test_click_applies_randomized_offsets_across_calls(self) -> None:
        """Verify repeated calls do not click the exact same pixel every time."""
        window = EmulatorWindow(handle=12345, title="LDPlayer")
        interface = DesktopInterface(window, timing=TimingConfig(click_offset_max=3))

        # 100x60 button at (200, 100) -> reference center is (250, 130)
        bounds = (200, 100, 100, 60)
        ref_x = 200 + 100 // 2  # 250
        ref_y = 100 + 60 // 2   # 130
        screenshot = _ARTIFACTS_DIR / "test_960.png"

        clicked_points: list[tuple[int, int]] = []

        def mock_click_point(win, point, frame_size, *, offset_range=0):
            clicked_points.append(point)

        with patch("rok_automation.game.interface.click_window_point", side_effect=mock_click_point):
            for _ in range(30):
                interface.click(bounds, screenshot)

        # All points must be within narrow offset range (+/- 3px) from reference
        for cx, cy in clicked_points:
            self.assertLessEqual(abs(cx - ref_x), 3)
            self.assertLessEqual(abs(cy - ref_y), 3)

        # Verify not all clicked points are identical (at least 2 unique points in 30 clicks)
        unique_points = set(clicked_points)
        self.assertGreater(len(unique_points), 1, "Click coordinates were identical across all calls!")

    def test_click_stays_strictly_inside_narrow_element(self) -> None:
        """Verify small elements constrain the offset range so clicks remain inside."""
        window = EmulatorWindow(handle=12345, title="LDPlayer")
        interface = DesktopInterface(window, timing=TimingConfig(click_offset_max=5))

        # Narrow element: width=6, height=6 at (50, 50) -> reference center (53, 53)
        bounds = (50, 50, 6, 6)
        ref_x = 53
        ref_y = 53
        screenshot = _ARTIFACTS_DIR / "test_960.png"

        clicked_points: list[tuple[int, int]] = []

        def mock_click_point(win, point, frame_size, *, offset_range=0):
            clicked_points.append(point)

        with patch("rok_automation.game.interface.click_window_point", side_effect=mock_click_point):
            for _ in range(20):
                interface.click(bounds, screenshot)

        # For width=6, offset should be constrained to min(5, 6//6=1) -> max +/- 1 pixel
        for cx, cy in clicked_points:
            self.assertLessEqual(abs(cx - ref_x), 1)
            self.assertLessEqual(abs(cy - ref_y), 1)

    def test_click_window_point_randomizes_coordinates(self) -> None:
        """Verify click_window_point applies offset around target reference point."""
        window = EmulatorWindow(handle=12345, title="LDPlayer")
        ref_point = (400, 300)
        frame_size = (720, 1280)

        mock_user32 = MagicMock()
        mock_user32.IsWindow.return_value = True
        mock_user32.IsWindowVisible.return_value = True
        mock_user32.IsIconic.return_value = False
        mock_user32.GetWindowRect.return_value = True
        mock_user32.SetCursorPos.return_value = True

        recorded_cursor_positions: list[tuple[int, int]] = []

        def mock_set_cursor(x, y):
            recorded_cursor_positions.append((x, y))
            return True

        mock_user32.SetCursorPos.side_effect = mock_set_cursor

        with patch("rok_automation.game.desktop.find_render_window_handle", return_value=67890), \
             patch("ctypes.WinDLL", return_value=mock_user32), \
             patch("time.sleep"):

            coords: list[tuple[int, int]] = []
            for _ in range(30):
                pt = click_window_point(window, ref_point, frame_size, offset_range=3)
                coords.append(pt)

            for cx, cy in coords:
                self.assertLessEqual(abs(cx - ref_point[0]), 3)
                self.assertLessEqual(abs(cy - ref_point[1]), 3)

            unique_coords = set(coords)
            self.assertGreater(len(unique_coords), 1)


if __name__ == "__main__":
    unittest.main()
