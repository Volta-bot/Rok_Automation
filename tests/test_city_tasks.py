import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.automation.city_tasks import CityTaskRunner
from rok_automation.game.adb import AdbError
from rok_automation.vision.screen import TemplateHit


class TestCityTasks(unittest.TestCase):
    def test_resource_search_excludes_edges_and_translates_coordinates(self):
        game, detector = MagicMock(), MagicMock()
        game.screenshot.return_value = "city.png"
        detector._locate_in_frame.side_effect = [
            TemplateHit(0.9, (200, 400, 20, 20)), None
        ]
        runner = CityTaskRunner(game, detector, log=lambda _: None)
        remaining = {"Food"}
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280), dtype=np.uint8)):
            runner._collect_visible_resources(remaining)
        self.assertEqual(detector._locate_in_frame.call_args_list[0].args[0].shape, (539, 1160))
        game.click.assert_called_once_with((292, 503, 20, 20), "city.png")
        self.assertEqual(remaining, set())

    def test_forge_continues_when_zoom_is_unavailable(self):
        game = MagicMock()
        game.zoom_out_city.side_effect = AdbError("A multitouch input device is unavailable")
        logs = []
        runner = CityTaskRunner(game, MagicMock(), log=logs.append)
        with patch.object(runner, "_is_still_in_city", return_value=True), \
             patch.object(runner, "_claim_and_start_forge", return_value=True) as forge:
            runner.run(collect_resources=False, forge_material="Leather")
        forge.assert_called_once_with("leather")
        self.assertTrue(any("scanning the current city view" in line for line in logs))

    def test_forge_opens_near_claimed_output_and_selects_five_times(self):
        game, detector = MagicMock(), MagicMock()
        game.screenshot.return_value = "city.png"
        forge = TemplateHit(0.9, (500, 200, 100, 100))
        button = TemplateHit(0.9, (700, 400, 40, 40))
        card = TemplateHit(0.9, (800, 200, 60, 60))
        detector.locate_template.side_effect = [forge, None, button, card]
        detector._locate_in_frame.return_value = TemplateHit(0.9, (50, 50, 40, 30))
        runner = CityTaskRunner(game, detector, log=lambda _: None)
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280), dtype=np.uint8)), \
             patch.object(runner, "_dismiss_material_menu"):
            runner._claim_and_start_forge("leather")
        self.assertEqual(game.click.call_count, 8)
        self.assertEqual(game.click.call_args_list[0].args[0], (517, 150, 40, 30))
        self.assertEqual(game.click.call_args_list[1].args[0], forge.bounds)
        self.assertTrue(all(call.args[0] == card.bounds for call in game.click.call_args_list[3:]))
        self.assertEqual(detector.locate_template.call_count, 4)
        self.assertEqual(sum(call.args == (0.08,) for call in game.wait.call_args_list), 4)

    def test_forge_stops_if_material_card_is_missing(self):
        game, detector = MagicMock(), MagicMock()
        game.screenshot.return_value = "city.png"
        hit = TemplateHit(0.9, (500, 200, 40, 30))
        detector.locate_template.side_effect = [hit, None, hit, None]
        detector._locate_in_frame.return_value = hit
        logs = []
        runner = CityTaskRunner(game, detector, log=logs.append)
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280), dtype=np.uint8)):
            runner._claim_and_start_forge("leather")
        self.assertEqual(game.click.call_count, 3)
        self.assertTrue(any("Could not find the leather material card" in line for line in logs))

    def test_forge_opens_and_selects_material_without_completed_output(self):
        game, detector = MagicMock(), MagicMock()
        game.screenshot.return_value = "city.png"
        forge = TemplateHit(0.9, (500, 200, 100, 100))
        button = TemplateHit(0.9, (700, 400, 40, 40))
        card = TemplateHit(0.9, (800, 200, 60, 60))
        detector.locate_template.side_effect = [forge, None, button, card]
        detector._locate_in_frame.return_value = None
        runner = CityTaskRunner(game, detector, log=lambda _: None)
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280), dtype=np.uint8)), \
             patch.object(runner, "_dismiss_material_menu"):
            self.assertTrue(runner._claim_and_start_forge("leather"))
        self.assertEqual(game.click.call_count, 7)
        self.assertEqual(game.click.call_args_list[0].args[0], forge.bounds)
        self.assertEqual(game.click.call_args_list[1].args[0], button.bounds)
        self.assertTrue(all(call.args[0] == card.bounds for call in game.click.call_args_list[2:]))

    def test_forge_uses_alternate_building_reference(self):
        game, detector = MagicMock(), MagicMock()
        game.screenshot.return_value = "city.png"
        forge = TemplateHit(0.95, (150, 200, 100, 80))
        detector.locate_template.side_effect = [None, forge, None]
        detector._locate_in_frame.return_value = TemplateHit(0.9, (70, 50, 25, 18))
        runner = CityTaskRunner(game, detector, log=lambda _: None)
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280), dtype=np.uint8)):
            runner._claim_and_start_forge("leather")
        self.assertEqual(game.click.call_args_list[1].args[0], forge.bounds)
