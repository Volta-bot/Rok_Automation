import unittest
from itertools import count
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import cv2
import numpy as np

from rok_automation.automation.controller import FarmingController, FarmingError, _SEARCH_TEMPLATES
from rok_automation.game.interface import TimingConfig
from rok_automation.logic.resource_selector import Resource
from rok_automation.vision.screen import (
    ScreenDetection, ScreenDetector, ScreenState, SearchSlider, TemplateHit, _POSITION_CACHE,
)


class SearchPositionCacheTests(unittest.TestCase):
    def test_reuses_position_and_keeps_resource_context_separate(self):
        game = MagicMock()
        game.timing.scaled.return_value = 2.5
        detector = MagicMock()
        detector.locate_template.side_effect = lambda *args, **kwargs: TemplateHit(
            0.9, (300, 200, 40, 20) if kwargs["context_key"] is Resource.WOOD else (100, 200, 40, 20))
        controller = FarmingController(game, detector=detector, log=lambda _: None)
        template = _SEARCH_TEMPLATES / "submit_button.png"
        screenshot = Path("frame.png")
        for resource in (Resource.FOOD, Resource.FOOD, Resource.WOOD, Resource.FOOD):
            controller._selected_resource = resource
            controller._click_template(template, expected_state=ScreenState.RESOURCE_SEARCH, initial_screenshot=screenshot)
        self.assertEqual(detector.locate_template.call_count, 4)
        self.assertEqual([call.args[0] for call in game.click.call_args_list], [
            (100, 200, 40, 20), (100, 200, 40, 20),
            (300, 200, 40, 20), (100, 200, 40, 20),
        ])

    def test_low_confidence_positions_are_not_remembered(self):
        game = MagicMock()
        game.timing.scaled.return_value = 2.5
        detector = MagicMock()
        detector.locate_template.return_value = TemplateHit(0.7, (100, 200, 40, 20))
        controller = FarmingController(game, detector=detector, log=lambda _: None)
        controller._selected_resource = Resource.FOOD
        for _ in range(2):
            controller._click_template(_SEARCH_TEMPLATES / "submit_button.png", expected_state=ScreenState.RESOURCE_SEARCH, initial_screenshot=Path("frame.png"))
        self.assertEqual(detector.locate_template.call_count, 2)

    def test_invalidation_only_removes_selected_resource(self):
        food = (720, 1280, "level_plus.png", Resource.FOOD)
        wood = (720, 1280, "level_plus.png", Resource.WOOD)
        try:
            _POSITION_CACHE[food] = (1, 2, 3, 4, 100)
            _POSITION_CACHE[wood] = (5, 6, 3, 4, 100)
            ScreenDetector.invalidate_position("level_plus.png", context_key=Resource.FOOD)
            self.assertNotIn(food, _POSITION_CACHE)
            self.assertIn(wood, _POSITION_CACHE)
        finally:
            _POSITION_CACHE.pop(food, None)
            _POSITION_CACHE.pop(wood, None)

class SearchSliderTests(unittest.TestCase):
    @staticmethod
    def _reading(level, resource=Resource.WOOD):
        slider = SearchSlider(level, TemplateHit(.98, (484, 404, 29, 29)),
                              TemplateHit(.98, (765, 404, 29, 29)))
        return ScreenDetection(
            ScreenState.RESOURCE_SEARCH, .95, "panel", (564, 455, 153, 72),
            resource_level=level, preferred_resource=resource, search_slider=slider,
        )

    def test_detects_each_slider_step_and_rejects_a_disconnected_search_button(self):
        detector = ScreenDetector()
        frame = np.full((720, 1280, 3), 195, dtype=np.uint8)

        def draw(name, x, y):
            template = cv2.imread(str(_SEARCH_TEMPLATES / name))
            scaled = cv2.resize(template, None, fx=.65, fy=.65)
            h, w = scaled.shape[:2]
            frame[y:y+h, x:x+w] = scaled

        draw("submit_button.png", 946, 455)  # Similar map element with no slider.

        def detect():
            with patch("rok_automation.vision.screen.load_gray_frame",
                       return_value=cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)), \
                 patch("rok_automation.vision.screen.load_color_frame", return_value=frame):
                return detector.detect(
                    "frame", candidate_states={ScreenState.RESOURCE_SEARCH},
                    context_key=Resource.GOLD, read_account=False,
                )

        self.assertEqual(detect().state, ScreenState.UNKNOWN)
        draw("submit_button.png", 564, 455)
        draw("level_minus.png", 484, 404)
        draw("level_plus.png", 765, 404)
        stable = frame.copy()
        handle = tuple(int(channel) for channel in cv2.cvtColor(
            np.array([[[96, 110, 230]]], dtype=np.uint8), cv2.COLOR_HSV2BGR,
        )[0, 0])
        for level in range(1, 9):
            with self.subTest(level=level):
                frame[:] = stable
                x = round(530 + (level - 1) * 31)
                cv2.rectangle(frame, (x - 8, 404), (x + 8, 435), handle, cv2.FILLED)
                detection = detect()
                self.assertEqual(detection.preferred_resource, Resource.WOOD)
                self.assertEqual(detection.resource_level, level)
                self.assertEqual(detection.search_slider.plus.bounds, (765, 404, 29, 29))

    def test_delayed_tab_switch_only_clicks_verified_wood_level_seven(self):
        game = MagicMock(timing=TimingConfig())
        detector = MagicMock()
        controller = FarmingController(game, detector=detector, log=lambda _: None)
        controller._selected_resource = Resource.WOOD
        initial, wood, updated = map(Path, ("gold", "wood", "updated"))
        game.screenshot.side_effect = [wood, updated]
        detector.detect.side_effect = [
            self._reading(1, Resource.GOLD), self._reading(7), self._reading(8),
        ]
        with patch("rok_automation.automation.controller.time.monotonic", side_effect=count()):
            self.assertEqual(controller._set_search_level(8, initial_screenshot=initial)[0], updated)
        game.click.assert_called_once_with((765, 404, 29, 29), wood)
        detector.locate_template.assert_not_called()

    def test_large_level_change_reads_fresh_slider_position_before_each_tap(self):
        game = MagicMock(timing=TimingConfig())
        detector = MagicMock()
        controller = FarmingController(game, detector=detector, log=lambda _: None)
        controller._selected_resource = Resource.WOOD
        frames = [Path(f"level-{level}") for level in range(1, 9)]
        game.screenshot.side_effect = frames
        detector.detect.side_effect = [self._reading(level) for level in range(1, 9)]
        self.assertEqual(controller._set_search_level(8)[0], frames[-1])
        self.assertEqual(game.click.call_args_list, [
            call((765, 404, 29, 29), frame) for frame in frames[:-1]
        ])

    def test_disappearing_search_panel_prevents_further_level_taps(self):
        game = MagicMock(timing=TimingConfig())
        detector = MagicMock()
        controller = FarmingController(game, detector=detector, log=lambda _: None)
        controller._selected_resource = Resource.WOOD
        game.screenshot.return_value = Path("closed")
        detector.detect.side_effect = lambda shot, **_: (
            self._reading(1) if shot == Path("open")
            else ScreenDetection(ScreenState.UNKNOWN, 0, "closed")
        )
        with patch("rok_automation.automation.controller.time.monotonic", side_effect=count()):
            with self.assertRaisesRegex(FarmingError, "Search menu did not become ready"):
                controller._set_search_level(8, initial_screenshot=Path("open"))
        game.click.assert_called_once_with((765, 404, 29, 29), Path("open"))
