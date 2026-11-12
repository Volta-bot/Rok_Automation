"""Tests for canonical capture normalisation.

The UI templates are authored against a 1280x720 frame, so every capture
must be normalised to that size. Otherwise the template matcher would need
a scale beyond its ceiling whenever the emulator window is large.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from rok_automation.game.ldplayer import (
    CANONICAL_CAPTURE_SIZE,
    EmulatorWindow,
    click_ldplayer_point,
    compute_capture_crop,
    normalize_capture,
)
from rok_automation.vision.screen import ScreenDetector


class TestCaptureCrop(unittest.TestCase):
    """Verify the crop region that maps a render window to the frame."""

    def test_sixteen_by_nine_window_is_not_cropped(self) -> None:
        for width, height in [(1280, 720), (1920, 1080), (1278, 718), (640, 360)]:
            left, top, crop_width, crop_height = compute_capture_crop(width, height)
            self.assertEqual((left, top), (0, 0), f"{width}x{height}")
            self.assertEqual((crop_width, crop_height), (width, height))

    def test_wide_window_is_cropped_horizontally(self) -> None:
        left, top, crop_width, crop_height = compute_capture_crop(2560, 1080)
        self.assertEqual(crop_width, 1920)
        self.assertEqual(crop_height, 1080)
        self.assertEqual(left, (2560 - 1920) // 2)
        self.assertEqual(top, 0)

    def test_tall_window_is_cropped_vertically(self) -> None:
        left, top, crop_width, crop_height = compute_capture_crop(1920, 1200)
        self.assertEqual(crop_width, 1920)
        self.assertEqual(crop_height, 1080)
        self.assertEqual(top, (1200 - 1080) // 2)
        self.assertEqual(left, 0)

    def test_degenerate_size_is_handled(self) -> None:
        left, top, crop_width, crop_height = compute_capture_crop(0, 0)
        self.assertEqual((left, top, crop_width, crop_height), (0, 0, 0, 0))


class TestNormalizeCapture(unittest.TestCase):
    """Verify every capture is rescaled to the canonical detection size."""

    def test_all_capture_sizes_normalize_to_canonical(self) -> None:
        for width, height in [
            (1920, 1080),
            (1600, 900),
            (1280, 720),
            (1024, 576),
            (800, 450),
            (640, 360),
            (2560, 1440),
        ]:
            frame = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
            normalized = normalize_capture(frame)
            self.assertEqual(
                normalized.shape[:2][::-1],
                CANONICAL_CAPTURE_SIZE,
                f"{width}x{height} did not normalize to the canonical size",
            )

    def test_canonical_frame_is_returned_unchanged(self) -> None:
        frame = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
        normalized = normalize_capture(frame)
        self.assertIs(normalized, frame)

    def test_aspect_is_preserved_for_letterboxed_windows(self) -> None:
        """A window with the wrong aspect is cropped, never stretched."""
        frame = np.zeros((1200, 1920, 3), dtype=np.uint8)
        normalized = normalize_capture(frame)
        self.assertEqual(normalized.shape[:2], (720, 1280))
        # The crop keeps the centre of the frame, so a bright centre band
        # must survive into the normalised result.
        frame[500:700, 800:1100] = 255
        normalized = normalize_capture(frame)
        self.assertGreater(normalized[300:400, 500:700].mean(), 200)


class TestDetectionIsResolutionIndependent(unittest.TestCase):
    """Verify template matching no longer depends on the window size."""

    def test_same_score_across_capture_sizes(self) -> None:
        """A real frame must match its template equally at any window size."""
        source = cv2.imread("artifacts/account_list_latest.png")
        self.assertIsNotNone(source, "missing test artifact account_list_latest.png")
        template = cv2.imread(
            "templates/profile/starred_accounts_header.png",
            cv2.IMREAD_GRAYSCALE,
        )
        self.assertIsNotNone(template)

        scores = []
        for width in [1920, 1728, 1600, 1440, 1280, 1024]:
            height = round(width * source.shape[0] / source.shape[1])
            resized = cv2.resize(
                source,
                (width, height),
                interpolation=cv2.INTER_CUBIC
                if width > source.shape[1]
                else cv2.INTER_AREA,
            )
            normalized = normalize_capture(resized)
            score, _match = ScreenDetector._best_scale_match(
                cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY),
                template,
                minimum_scale=0.35,
                maximum_scale=1.35,
            )
            scores.append(score)

        # Every size must clear the confidence threshold the detector uses.
        self.assertTrue(
            all(score >= 0.85 for score in scores),
            f"detection became size dependent: {[round(s, 3) for s in scores]}",
        )
        # And the spread across sizes must stay small.
        self.assertLess(
            max(scores) - min(scores),
            0.15,
            f"detection varied too much by size: {[round(s, 3) for s in scores]}",
        )


class TestClickMappingAfterNormalization(unittest.TestCase):
    """Verify clicks still land on the same game content at any window size."""

    RENDER_RECTS = {
        "current 1278x718": (100, 200, 1378, 918),
        "1080p 1920x1080": (0, 0, 1920, 1080),
        "small 960x540": (50, 50, 1010, 590),
        "wide 2560x1440": (10, 10, 2570, 1450),
        "tall 1920x1200": (0, 0, 1920, 1200),
    }

    @staticmethod
    def _mock_user32(rect):
        left, top, right, bottom = rect
        mock = MagicMock()
        mock.IsWindow.return_value = True
        mock.IsWindowVisible.return_value = True
        mock.IsIconic.return_value = False
        mock.GetWindowRect.return_value = True
        captured: list[tuple[int, int]] = []

        def get_rect(_hwnd, pointer):
            pointer._obj.left = left
            pointer._obj.top = top
            pointer._obj.right = right
            pointer._obj.bottom = bottom
            return True

        def set_cursor(x, y):
            captured.append((x, y))
            return True

        mock.GetWindowRect.side_effect = get_rect
        mock.SetCursorPos.side_effect = set_cursor
        return mock, captured

    def _click(self, rect, point):
        mock, captured = self._mock_user32(rect)
        with patch(
            "rok_automation.game.ldplayer._find_render_window_handle",
            return_value=999,
        ), patch("ctypes.WinDLL", return_value=mock), patch("time.sleep"):
            click_ldplayer_point(
                EmulatorWindow(handle=1, title="LDPlayer"),
                point,
                CANONICAL_CAPTURE_SIZE[::-1],
                offset_range=0,
            )
        return captured[0]

    def test_centre_click_lands_on_render_centre(self) -> None:
        """The canonical centre must map to the centre of the render window."""
        centre_x, centre_y = CANONICAL_CAPTURE_SIZE[0] // 2, CANONICAL_CAPTURE_SIZE[1] // 2
        for label, rect in self.RENDER_RECTS.items():
            with self.subTest(label):
                left, top, right, bottom = rect
                screen_x, screen_y = self._click(rect, (centre_x, centre_y))
                self.assertEqual(
                    (screen_x, screen_y),
                    (left + (right - left) // 2, top + (bottom - top) // 2),
                )

    def test_click_relative_position_is_preserved(self) -> None:
        """A canonical point must keep its relative position in the window."""
        for label, rect in self.RENDER_RECTS.items():
            with self.subTest(label):
                left, top, right, bottom = rect
                render_width, render_height = right - left, bottom - top
                point = (320, 180)
                screen_x, screen_y = self._click(rect, point)
                crop_left, crop_top, crop_width, crop_height = compute_capture_crop(
                    render_width,
                    render_height,
                )
                expected_x = (
                    point[0] / CANONICAL_CAPTURE_SIZE[0] * crop_width + crop_left
                ) / render_width
                expected_y = (
                    point[1] / CANONICAL_CAPTURE_SIZE[1] * crop_height + crop_top
                ) / render_height
                # Allow one screen pixel of rounding slack.
                self.assertAlmostEqual(
                    (screen_x - left) / render_width,
                    expected_x,
                    delta=1.5 / render_width,
                )
                self.assertAlmostEqual(
                    (screen_y - top) / render_height,
                    expected_y,
                    delta=1.5 / render_height,
                )


if __name__ == "__main__":
    unittest.main()
