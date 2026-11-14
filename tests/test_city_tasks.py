import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.automation.city_tasks import CityTaskRunner
from rok_automation.game.adb import AdbError
from rok_automation.vision.screen import TemplateHit


class TestCityTasks(unittest.TestCase):
    def test_resource_scan_finds_multiple_markers_and_translates_coordinates(self):
        detector = MagicMock()
        detector._locate_in_frame.side_effect = [
            TemplateHit(0.95, (200, 200, 20, 20)), None,
            TemplateHit(0.94, (260, 200, 20, 20)), None,
            None, None,
        ]
        runner = CityTaskRunner(MagicMock(), detector, log=lambda _: None)
        frame = np.full((720, 1280), 255, dtype=np.uint8)
        markers = runner._scan_resource_markers(frame, {"Gold"})
        self.assertEqual(markers, [
            ("Gold", (292, 303, 20, 20)), ("Gold", (352, 303, 20, 20)),
        ])
        first_scan = detector._locate_in_frame.call_args_list[0].args[0]
        second_scan = detector._locate_in_frame.call_args_list[2].args[0]
        self.assertEqual(first_scan.shape, (539, 1160))
        self.assertEqual(second_scan[210, 210], 0)

    def test_batches_all_clicks_then_rescans_and_retries_missed_markers(self):
        game = MagicMock()
        game.screenshot.side_effect = ["first.png", "check.png"]
        runner = CityTaskRunner(game, MagicMock(), log=lambda _: None)
        events = []
        def scan(frame, resources):
            events.append("scan")
            if len(events) == 1:
                return [("Gold", (10, 20, 10, 10)), ("Stone", (30, 40, 10, 10))]
            return [("Gold", (10, 20, 10, 10)), ("Wood", (50, 60, 10, 10))]
        game.click.side_effect = lambda *args: events.append("click")
        remaining = {"Gold", "Stone", "Wood"}
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280))), \
             patch.object(runner, "_scan_resource_markers", side_effect=scan):
            runner._collect_visible_resources(remaining)
        self.assertEqual(events, ["scan", "click", "click", "scan", "click", "click"])
        self.assertEqual(game.click.call_args_list[2].args, ((10, 20, 10, 10), "check.png"))
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
