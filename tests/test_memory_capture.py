import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from rok_automation.__main__ import main, _run_stockpile_check
from rok_automation.game.native import NativeInterface
from rok_automation.vision.screen import (
    ScreenDetection, ScreenState, _FRAME_CACHE,
    cache_captured_frame, load_color_frame, load_gray_frame, save_frame,
)


class MemoryCaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(_FRAME_CACHE.clear)
        self.path = Path(self.directory.name) / "capture.png"
        self.game = NativeInterface.__new__(NativeInterface)
        self.game.source = MagicMock()
        self.game.screenshot_path = self.path

    def test_capture_writes_latest_pixels_to_png_and_cache(self):
        cv2.imwrite(str(self.path), np.zeros((720, 1280, 3), np.uint8))
        frame = np.full((1080, 1920, 3), 72, np.uint8)
        self.game.source.capture.return_value = frame
        handle = self.game.screenshot()
        self.assertEqual(self.game._guest_size, (1920, 1080))
        self.assertEqual(load_color_frame(handle).shape, (720, 1280, 3))
        self.assertTrue(np.all(load_color_frame(handle) == 72))
        self.assertTrue(np.all(load_gray_frame(handle) == 72))
        self.assertTrue(np.all(cv2.imread(str(self.path)) == 72))

    def test_invalid_capture_reconnects_and_publishes_a_fresh_frame(self):
        self.game.source.capture.side_effect = [None, np.full((720, 1280, 3), 76, np.uint8)]
        with patch.object(self.game, "reconnect") as reconnect:
            handle = self.game.screenshot()
        reconnect.assert_called_once()
        self.assertTrue(np.all(load_color_frame(handle) == 76))

    def test_direct_connections_keep_capture_pixels_separate(self):
        from rok_automation.game.emulators import EmulatorInstance
        games = []
        with patch("rok_automation.game.native.AdbInput.__init__", return_value=None), \
             patch.object(NativeInterface, "log", None, create=True), \
             patch.object(NativeInterface, "_measure_latency"):
            for index in (0, 1, 0):
                game = NativeInterface(source=MagicMock(), instance=EmulatorInstance(index, str(index)))
                game.source.capture.return_value = np.full((720, 1280, 3), 40 + len(games), np.uint8)
                games.append(game)
        self.assertEqual(len({game.screenshot_path for game in games}), 3)
        # Keep test output temporary while preserving each generated instance/session path.
        for game in games:
            game.screenshot_path = Path(self.directory.name).joinpath(*game.screenshot_path.parts[-3:])
        handles = [game.screenshot() for game in games]
        _FRAME_CACHE.clear()
        for index, handle in enumerate(handles):
            self.assertTrue(np.all(load_color_frame(handle) == 40 + index))

    def test_export_then_capture_does_not_reuse_saved_pixels(self):
        self.game.source.capture.return_value = np.full((720, 1280, 3), 40, np.uint8)
        handle = self.game.screenshot()
        self.assertTrue(self.path.exists())
        save_frame(handle)
        self.assertTrue(np.all(cv2.imread(str(self.path)) == 40))
        self.game.source.capture.return_value = np.full((720, 1280, 3), 80, np.uint8)
        self.game.screenshot()
        self.assertTrue(np.all(load_color_frame(handle) == 80))
        export = self.path.with_name("diagnostic.png")
        save_frame(handle, export)
        self.assertTrue(np.all(cv2.imread(str(export)) == 80))
        # File-backed exports must also notice external changes.
        cv2.imwrite(str(export), np.zeros((10, 10, 3), np.uint8))
        self.assertEqual(load_color_frame(export).shape, (10, 10, 3))

    def test_live_frame_survives_loading_other_files(self):
        cache_captured_frame(self.path, np.full((10, 10, 3), 99, np.uint8), memory_only=True)
        for index in range(10):
            other = self.path.with_name(f"other_{index}.png")
            cv2.imwrite(str(other), np.zeros((10, 10, 3), np.uint8))
            load_gray_frame(other)
        self.assertTrue(np.all(load_color_frame(self.path) == 99))

    def test_cli_screenshot_exports_the_live_frame(self):
        self.game.source.capture.return_value = np.full((720, 1280, 3), 51, np.uint8)
        output = self.path.with_name("requested.png")
        with patch.object(sys, "argv", ["rok", "--screenshot", str(output)]), \
             patch("rok_automation.__main__._build_game", return_value=self.game), \
             patch("builtins.print"):
            self.assertEqual(main(), 0)
        self.assertTrue(np.all(cv2.imread(str(output)) == 51))

    def test_export_failure_is_reported(self):
        cache_captured_frame(self.path, np.zeros((10, 10, 3), np.uint8), memory_only=True)
        with patch("rok_automation.vision.screen.cv2.imwrite", return_value=False):
            with self.assertRaises(OSError):
                save_frame(self.path)

    def test_screen_diagnostic_detects_the_exported_frame(self):
        self.game.source.capture.return_value = np.full((720, 1280, 3), 61, np.uint8)
        output = self.path.with_name("detected.png")
        with patch.object(sys, "argv", ["rok", "--screenshot", str(output), "--detect-screen"]), \
             patch("rok_automation.__main__._build_game", return_value=self.game), \
             patch("rok_automation.__main__.ScreenDetector") as detector, \
             patch("builtins.print"):
            detector.return_value.detect.return_value = ScreenDetection(ScreenState.CITY, 1, "city")
            self.assertEqual(main(), 0)
            detector.return_value.detect.assert_called_once_with(
                output.resolve(), expected_resource_level=None,
            )
        self.assertTrue(np.all(cv2.imread(str(output)) == 61))

    def test_stockpile_diagnostic_still_saves_its_capture(self):
        self.game.source.capture.return_value = np.full((720, 1280, 3), 91, np.uint8)
        with patch("rok_automation.vision.screen.ScreenDetector") as detector, \
             patch("rok_automation.vision.resource_bar.read_resource_bar", return_value={}), \
             patch("builtins.print"):
            detector.return_value.detect.return_value = ScreenDetection(ScreenState.UNKNOWN, 0, "none")
            self.assertEqual(_run_stockpile_check(self.game), 0)
        self.assertTrue(np.all(cv2.imread(str(self.path)) == 91))
