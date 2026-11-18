import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.automation.city_tasks import CityTaskRunner
from rok_automation.game.adb import AdbError
from rok_automation.vision.screen import ScreenDetection, ScreenState, TemplateHit


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
        game.screenshot.side_effect = ["first.png", "check.png", "verified.png"]
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

    def test_resource_scan_rejects_bounds_crossing_chat_region(self):
        detector = MagicMock()
        detector._locate_in_frame.side_effect = [
            TemplateHit(0.95, (180, 490, 20, 20)), None,
            TemplateHit(0.94, (550, 490, 20, 20)), None,
            None, None,
        ]
        runner = CityTaskRunner(MagicMock(), detector, log=lambda _: None)
        frame = np.full((720, 1280), 255, dtype=np.uint8)
        self.assertEqual(runner._scan_resource_markers(frame, {"Gold"}), [
            ("Gold", (642, 593, 20, 20)),
        ])
        search_area = detector._locate_in_frame.call_args_list[0].args[0]
        self.assertEqual(search_area[520, 300], 0)
        self.assertEqual(search_area[520, 550], 255)

    def test_chat_after_double_check_is_closed_and_resources_are_rescanned(self):
        game, detector = MagicMock(), MagicMock()
        game.screenshot.side_effect = ["first", "second", "chat", "resumed", "verified"]
        clear = ScreenDetection(ScreenState.UNKNOWN, 0.0, "")
        close_bounds = (700, 340, 32, 38)
        detector.detect.side_effect = [
            clear, clear, ScreenDetection(ScreenState.CHAT, 0.99, "", close_bounds),
            clear, clear,
        ]
        runner = CityTaskRunner(game, detector, log=lambda _: None)
        marker = ("Gold", (610, 420, 20, 20))
        with patch("rok_automation.automation.city_tasks.load_gray_frame"), \
             patch.object(runner, "_scan_resource_markers", return_value=[marker]):
            self.assertTrue(runner._collect_visible_resources({"Gold"}))
        self.assertEqual([call.args for call in game.click.call_args_list], [
            (marker[1], "first"), (marker[1], "second"),
            (close_bounds, "chat"), (marker[1], "resumed"),
        ])

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

    def _forge_runner(self, kind="idle", *, card_missing=False, button_missing=False):
        game, detector = MagicMock(), MagicMock()
        game.screenshot.side_effect = [f"frame-{i}.png" for i in range(10)]
        icon = TemplateHit(0.95, (500, 200, 40, 40))
        button = TemplateHit(0.9, (700, 400, 40, 40))
        card = TemplateHit(0.9, (800, 200, 60, 60))
        hits = [icon if name == kind else None
                for name in ("idle", "leather", "iron", "wood", "bone")]
        detector.locate_template.side_effect = (
            hits[:1] + ([None] * 6 if button_missing else [button])
            + ([None] * 6 if card_missing else [card])
        )
        runner = CityTaskRunner(game, detector, log=lambda _: None)
        runner._locate_forge_material = MagicMock(side_effect=hits[1:])
        return runner, game, detector, button, card

    def test_forge_material_outputs_claim_then_open_and_select_five_times(self):
        for kind in ("leather", "iron", "wood", "bone"):
            with self.subTest(kind=kind):
                runner, game, detector, button, card = self._forge_runner(kind)
                with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280))), \
                     patch.object(runner, "_dismiss_material_menu") as dismiss:
                    self.assertTrue(runner._claim_and_start_forge("leather"))
                self.assertEqual(game.click.call_count, 8)
                self.assertEqual(game.click.call_args_list[0].args, ((520, 280, 1, 1), "frame-0.png"))
                self.assertEqual(game.click.call_args_list[1].args, ((520, 280, 1, 1), "frame-0.png"))
                self.assertEqual(game.click.call_args_list[2].args[0], button.bounds)
                self.assertTrue(all(call.args[0] == card.bounds for call in game.click.call_args_list[3:]))
                self.assertEqual(sum(call.args == (0.08,) for call in game.wait.call_args_list), 4)
                templates = [call.args[1].name for call in runner._locate_forge_material.call_args_list]
                self.assertEqual(templates, ["material_leather.png", "material_iron.png", "material_wood.png", "material_bone.png"])
                dismiss.assert_called_once()

    def test_idle_forge_opens_once_before_material_selection(self):
        runner, game, detector, button, card = self._forge_runner()
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280))), \
             patch.object(runner, "_dismiss_material_menu"):
            self.assertTrue(runner._claim_and_start_forge("leather"))
        self.assertEqual(game.click.call_count, 7)
        self.assertEqual(game.click.call_args_list[0].args[0], (520, 280, 1, 1))
        self.assertEqual(game.click.call_args_list[1].args[0], button.bounds)
        self.assertTrue(all(call.args[0] == card.bounds for call in game.click.call_args_list[2:]))

    def test_missing_icon_does_not_click(self):
        runner, game, _, _, _ = self._forge_runner("missing")
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280))):
            self.assertFalse(runner._claim_and_start_forge("leather"))
        game.click.assert_not_called()

    def test_forge_stops_if_material_card_or_button_is_missing(self):
        for button_missing in (False, True):
            with self.subTest(button_missing=button_missing):
                runner, game, _, _, _ = self._forge_runner(card_missing=True, button_missing=button_missing)
                with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280))):
                    self.assertTrue(runner._claim_and_start_forge("leather"))
                self.assertEqual(game.click.call_count, 1 if button_missing else 2)

    def test_forge_target_outside_frame_does_not_click(self):
        runner, game, _, _, _ = self._forge_runner()
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((250, 1280))):
            self.assertFalse(runner._claim_and_start_forge("leather"))
        game.click.assert_not_called()

    def test_unreadable_forge_frame_does_not_scan_or_click(self):
        runner, game, detector, _, _ = self._forge_runner()
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=None):
            self.assertFalse(runner._claim_and_start_forge("leather"))
        detector.locate_template.assert_not_called()
        game.click.assert_not_called()

    def test_real_zoomed_out_forge_icon(self):
        from pathlib import Path
        from rok_automation.vision.screen import ScreenDetector, load_gray_frame
        fixture = Path(__file__).parent / "fixtures" / "forge_zoomed_out.png"
        template = Path(__file__).resolve().parents[1] / "templates" / "city" / "forge_icon.png"
        frame = load_gray_frame(fixture)
        self.assertIsNone(ScreenDetector._locate_in_frame(frame, template, confidence_threshold=0.80))
        hit = ScreenDetector._locate_in_frame(
            frame, template, confidence_threshold=0.72, minimum_scale=0.15, refine_scale=True,
        )
        self.assertIsNotNone(hit)
        x, y, w, h = hit.bounds
        self.assertTrue(210 <= x <= 218 and 234 <= y <= 242, hit)
        self.assertTrue(35 <= w <= 43 and 34 <= h <= 42, hit)
        # Remove the forge region: unrelated city icons must not pass.
        without_forge = frame.copy()
        without_forge[230:290, 200:265] = 0
        self.assertIsNone(ScreenDetector._locate_in_frame(
            without_forge, template, confidence_threshold=0.72,
            minimum_scale=0.15, refine_scale=True,
        ))

    def test_idle_icon_wins_over_small_recipe_icons(self):
        runner, game, detector, _, _ = self._forge_runner(button_missing=True)
        with patch("rok_automation.automation.city_tasks.load_gray_frame", return_value=np.zeros((720, 1280))):
            runner._claim_and_start_forge("bone")
        self.assertEqual(game.click.call_count, 1)
        self.assertEqual(detector.locate_template.call_count, 7)

    def test_real_bone_reward_ignores_recipe_label_and_toolbar(self):
        from pathlib import Path
        from rok_automation.automation.city_tasks import _MATERIAL_TEMPLATES
        from rok_automation.vision.screen import ScreenDetector, load_gray_frame
        fixture = Path(__file__).parent / "fixtures" / "forge_bone_zoomed_out.png"
        frame = load_gray_frame(fixture)
        game = MagicMock()
        game.screenshot.return_value = fixture
        runner = CityTaskRunner(game, ScreenDetector(), log=lambda _: None)
        for kind, template in _MATERIAL_TEMPLATES.items():
            hit = runner._locate_forge_material(frame, template, kind)
            if kind == "bone":
                self.assertIsNotNone(hit)
                self.assertGreater(hit.confidence, 0.90)
                self.assertEqual(hit.bounds, (158, 242, 22, 14))
            else:
                self.assertIsNone(hit, kind)
        # Removing only the reward leaves the small bone recipe label intact.
        without_reward = frame.copy()
        without_reward[230:280, 145:195] = 0
        self.assertIsNone(runner._locate_forge_material(
            without_reward, _MATERIAL_TEMPLATES["bone"], "bone",
        ))
        self.assertTrue(runner._claim_and_start_forge("bone"))
        self.assertEqual(game.click.call_count, 2)
        for call in game.click.call_args_list:
            self.assertEqual(call.args[0], (169, 286, 1, 1))

    def test_all_materials_match_small_synthetic_overhead_bubbles(self):
        import cv2
        from rok_automation.automation.city_tasks import _MATERIAL_TEMPLATES
        from rok_automation.vision.screen import ScreenDetector
        runner = CityTaskRunner(MagicMock(), ScreenDetector(), log=lambda _: None)
        for kind, template in _MATERIAL_TEMPLATES.items():
            with self.subTest(material=kind):
                frame = np.full((720, 1280), 60, dtype=np.uint8)
                card = cv2.imread(str(template), cv2.IMREAD_GRAYSCALE)
                card = cv2.resize(card, (round(card.shape[1] * .22), round(card.shape[0] * .22)), interpolation=cv2.INTER_AREA)
                h, w = card.shape
                frame[297:300+h+round(w*.6), 396:400+w+4] = 235
                frame[300:300+h, 400:400+w] = card
                hit = runner._locate_forge_material(frame, template, kind)
                self.assertIsNotNone(hit)
                self.assertLessEqual(abs(hit.bounds[0] - 400), 1)
                self.assertLessEqual(abs(hit.bounds[1] - 300), 1)

    def test_stacked_bone_reward_keeps_bubble_validation(self):
        from pathlib import Path
        from rok_automation.automation.city_tasks import _MATERIAL_TEMPLATES
        from rok_automation.vision.screen import ScreenDetector, load_gray_frame
        frame = load_gray_frame(Path(__file__).parent / "fixtures" / "forge_bone_stacked.png")
        runner = CityTaskRunner(MagicMock(), ScreenDetector(), log=lambda _: None)
        for kind, template in _MATERIAL_TEMPLATES.items():
            hit = runner._locate_forge_material(frame, template, kind)
            if kind == "bone":
                self.assertIsNotNone(hit)
                self.assertGreater(hit.confidence, 0.90)
                self.assertEqual(hit.bounds, (183, 199, 22, 14))
            else:
                self.assertIsNone(hit, kind)
        # Keep the progress label; remove only the overhead reward stack.
        without_reward = frame.copy()
        without_reward[185:235, 175:225] = 0
        self.assertIsNone(runner._locate_forge_material(
            without_reward, _MATERIAL_TEMPLATES["bone"], "bone",
        ))
