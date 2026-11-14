import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from rok_automation.automation.controller import FarmingController, FarmingError
from rok_automation.game.interface import TimingConfig
from rok_automation.logic.resource_selector import Resource
from rok_automation.performance import PerformanceStats, collect_performance, timed
from rok_automation.vision.screen import (
    ScreenDetection, ScreenDetector, ScreenState, _POSITION_CACHE, _SCALE_CACHE,
)


class ReadingCacheTests(unittest.TestCase):
    def test_level_cache_requires_identical_pixels_and_context(self):
        detector = ScreenDetector()
        frame = np.zeros((720, 1280), np.uint8)
        bounds = (700, 450, 120, 60)
        with patch.object(detector, "_read_search_level", side_effect=[8, 7, 6, 5]) as ocr:
            self.assertEqual(detector._cached_search_level(frame, bounds, Resource.GOLD), 8)
            frame[0, 0] = 255  # Outside the label crops.
            self.assertEqual(detector._cached_search_level(frame, bounds, Resource.GOLD), 8)
            ocr.assert_called_once()
            frame[370, 620] = 255  # Low-level label position in the fallback crop.
            self.assertEqual(detector._cached_search_level(frame, bounds, Resource.GOLD), 7)
            self.assertEqual(detector._cached_search_level(frame, bounds, Resource.WOOD), 6)
            detector.clear_reading_cache()
            self.assertEqual(detector._cached_search_level(frame, bounds, Resource.WOOD), 5)

    def test_unreadable_levels_are_retried(self):
        detector = ScreenDetector()
        frame = np.zeros((720, 1280), np.uint8)
        with patch.object(detector, "_read_search_level", side_effect=[None, 8]) as ocr:
            self.assertIsNone(detector._cached_search_level(frame, (700, 450, 120, 60), None))
            self.assertEqual(detector._cached_search_level(frame, (700, 450, 120, 60), None), 8)
            self.assertEqual(ocr.call_count, 2)

    def test_level_cache_includes_the_last_row_of_the_fallback_crop(self):
        detector = ScreenDetector()
        frame = np.zeros((720, 1280), np.uint8)
        bounds = (700, 450, 120, 46)
        with patch.object(detector, "_read_search_level", side_effect=[8, 7]):
            self.assertEqual(detector._cached_search_level(frame, bounds, None), 8)
            frame[429, 620] = 255
            self.assertEqual(detector._cached_search_level(frame, bounds, None), 7)

    def test_stockpiles_refresh_between_accounts_and_on_recovery(self):
        controller = FarmingController(MagicMock(), detector=ScreenDetector(), log=lambda _: None)
        amounts = {Resource.GOLD: 10, Resource.WOOD: 20}
        def farm(**kwargs):
            self.assertEqual(controller._read_stockpiles(Path("frame")), amounts)
            self.assertEqual(controller._read_stockpiles(Path("frame")), amounts)
            return controller.context
        with patch("rok_automation.automation.controller.load_color_frame", return_value=np.zeros((1, 1, 3))), \
             patch("rok_automation.automation.controller.read_resource_bar", return_value=amounts) as ocr, \
             patch.object(controller, "_save_session_summary"), \
             patch.object(controller, "_farm_account", side_effect=farm):
            controller._run_one_account()
            controller._run_one_account()
            self.assertEqual(ocr.call_count, 2)
            controller._read_stockpiles(Path("frame"))
            controller._invalidate_observations()
            controller._read_stockpiles(Path("frame"))
            self.assertEqual(ocr.call_count, 4)
            controller._stockpile_cache = (time.monotonic() - 31, amounts)
            controller._read_stockpiles(Path("frame"))
            self.assertEqual(ocr.call_count, 5)


class FastDetectionTests(unittest.TestCase):
    def tearDown(self):
        _POSITION_CACHE.clear()
        _SCALE_CACHE.clear()

    def test_moved_control_falls_back_to_full_detection(self):
        _POSITION_CACHE.clear()
        _SCALE_CACHE.clear()
        path = Path("templates/navigation/world_search_button.png")
        template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        h, w = template.shape
        frame = np.zeros((720, 1280), np.uint8)
        frame[450:450+h, 10:10+w] = template
        detector = ScreenDetector()
        def locate(image, fast=False):
            return detector._locate_in_frame(image, path, confidence_threshold=0.78, fast_only=fast)
        self.assertIsNotNone(locate(frame))
        self.assertIsNotNone(locate(frame, fast=True))
        moved = np.zeros_like(frame)
        moved[530:530+h, 100:100+w] = template
        with patch.object(detector.__class__, "_best_scale_match", wraps=detector._best_scale_match) as broad:
            self.assertIsNone(locate(moved, fast=True))
            broad.assert_not_called()
        self.assertIsNotNone(locate(moved))
        self.assertIsNotNone(locate(moved, fast=True))

    def test_transition_grace_still_recovers_an_unexpected_screen(self):
        game = MagicMock()
        game.timing = TimingConfig()
        controller = FarmingController(game, detector=MagicMock(), log=lambda _: None)
        unknown = ScreenDetection(ScreenState.UNKNOWN, 0, "transition")
        city = ScreenDetection(ScreenState.CITY, 0.95, "unexpected city")
        ticks = iter(index * 0.04 for index in range(100))
        def classify(*args, fast_only=False, **kwargs):
            return (unknown if fast_only else city), False
        with patch("rok_automation.automation.controller.time.monotonic", side_effect=lambda: next(ticks)), \
             patch.object(controller, "_classify_dispatch_screen", side_effect=classify) as check:
            result = controller._wait_for_dispatch_change(
                ScreenState.RESOURCE_FOUND, None, timeout=2,
                expected_states=(ScreenState.NEW_TROOPS_PROMPT,),
            )
        self.assertEqual(result[1].state, ScreenState.CITY)
        self.assertTrue(any(call.kwargs["fast_only"] for call in check.call_args_list))
        self.assertFalse(check.call_args_list[-1].kwargs["fast_only"])

    def test_reuses_recent_world_frame_but_recaptures_after_delay(self):
        for age in (0.0, 2.0):
            with self.subTest(age=age):
                detector = MagicMock()
                controller = FarmingController(MagicMock(), detector=detector, log=lambda _: None)
                controller.context.preferred_resource = Resource.FOOD
                world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "map")
                found = ScreenDetection(ScreenState.RESOURCE_FOUND, 0.95, "node", resource_coordinates=(10, 20))
                controller._next_world_screen = (Path("map"), world, time.monotonic() - age)
                detector.detect.return_value = found
                with patch.object(controller, "_read_screen", return_value=(Path("fresh"), world)) as read, \
                     patch.object(controller, "_press_and_wait", return_value=(Path("node"), found)), \
                     patch.object(controller, "_dispatch_army", return_value=True), \
                     patch.object(controller, "_remember_farmed_node"):
                    self.assertTrue(controller._farm_one_army(2, {r: 8 for r in Resource}, set()))
                if age == 0:
                    read.assert_not_called()
                else:
                    read.assert_called_once_with(expected_states=(ScreenState.WORLD_MAP,))


class PerformanceStatsTests(unittest.TestCase):
    def test_nested_measurements_do_not_double_count_ocr(self):
        @timed("ocr")
        def ocr():
            return 8
        @timed("detection")
        def detect():
            return ocr()
        stats = PerformanceStats()
        with collect_performance(stats), \
             patch("rok_automation.performance.perf_counter", side_effect=[0, 1, 3, 5]):
            self.assertEqual(detect(), 8)
        self.assertEqual(stats.totals, {"ocr": 2, "detection": 3})
        detect()  # Collector is no longer active.
        self.assertEqual(stats.counts, {"ocr": 1, "detection": 1})

    def test_account_failure_keeps_timings_and_clears_observations(self):
        controller = FarmingController(MagicMock(), detector=ScreenDetector(), log=lambda _: None)
        @timed("capture")
        def fail(**kwargs):
            controller._stockpile_cache = (0, {Resource.GOLD: 1})
            raise FarmingError("missing screen")
        with patch.object(controller, "_farm_account", side_effect=fail), \
             patch.object(controller, "_save_session_summary"):
            with self.assertRaises(FarmingError):
                controller._run_one_account()
        self.assertIsNone(controller._stockpile_cache)
        self.assertEqual(controller.session_performance[0]["operations"]["capture"]["calls"], 1)
