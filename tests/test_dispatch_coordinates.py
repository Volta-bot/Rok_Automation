import unittest
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.logic.resource_selector import Resource, SearchTarget
from rok_automation.vision.screen import ScreenDetection, ScreenState


class TestDispatchCoordinates(unittest.TestCase):
    def test_dispatch_logs_read_coordinates_or_explicitly_reports_failure(self):
        for coordinates, expected in (((1082, 194), "X:1082 Y:194"),
                                      (None, "unreadable; duplicate check unavailable")):
            with self.subTest(coordinates=coordinates):
                logs = []
                game = MagicMock()
                game.timing.scaled.return_value = 20
                controller = FarmingController(game, detector=MagicMock(), log=logs.append)
                troop = ScreenDetection(ScreenState.TROOP_COMPOSITION, 0.95, "march", (1, 2, 3, 4))
                world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "world")
                with patch.object(controller, "_classify_dispatch_screen", return_value=(troop, False)), \
                     patch.object(controller, "_wait_for_dispatch_change", return_value=("map.png", world, False)), \
                     patch.object(controller, "_save_session_summary"):
                    self.assertTrue(controller._dispatch_army(SearchTarget(Resource.GOLD, 7), 3,
                                                              coordinates=coordinates))
                dispatch = next(line for line in logs if "dispatched to" in line)
                self.assertIn("Army 3/5 dispatched to Gold Lv7", dispatch)
                self.assertIn("node coordinates: " + expected, dispatch)
