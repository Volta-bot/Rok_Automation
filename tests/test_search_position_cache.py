import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.automation.controller import FarmingController, _SEARCH_TEMPLATES
from rok_automation.logic.resource_selector import Resource
from rok_automation.vision.screen import ScreenDetection, ScreenDetector, ScreenState, TemplateHit, _POSITION_CACHE


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
        with patch("rok_automation.automation.controller.load_gray_frame", return_value=np.zeros((720, 1280), dtype=np.uint8)):
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
        with patch("rok_automation.automation.controller.load_gray_frame", return_value=np.zeros((720, 1280), dtype=np.uint8)):
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

    def test_level_rechecks_after_partial_batch_and_stale_position(self):
        game, detector = MagicMock(), MagicMock()
        controller = FarmingController(game, detector=detector, log=lambda _: None)
        controller._selected_resource = Resource.FOOD
        controller._resource_levels[Resource.FOOD] = 8
        def reading(level):
            return Path("frame.png"), ScreenDetection(state=ScreenState.RESOURCE_SEARCH,
                confidence=1, evidence="menu", resource_level=level)
        # Initial batch partially registers; the second fails entirely and
        # forces a fresh lookup before the final successful attempt.
        readings = [reading(n) for n in (1, 4, 4, 4, 4, 4, 4, 8)]
        with patch.object(controller, "_wait_for_search_menu_level", side_effect=readings), \
             patch.object(controller, "_click_template") as click:
            controller._set_search_level(8)
        self.assertEqual([c.kwargs["repeat"] for c in click.call_args_list], [7, 4, 4])
        detector.invalidate_position.assert_called_once_with(
            _SEARCH_TEMPLATES / "level_plus.png", context_key=Resource.FOOD)
