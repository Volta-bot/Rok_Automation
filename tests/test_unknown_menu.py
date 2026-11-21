import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from rok_automation.__main__ import main
from rok_automation.automation.city_tasks import CityTaskRunner
from rok_automation.automation.controller import FarmingController
from rok_automation.automation.gmail_switcher import GmailSwitcher
from rok_automation.game.interface import TimingConfig
from rok_automation.game.popups import back_out_of_unknown_menu
from rok_automation.vision.screen import ScreenDetection, ScreenDetector, ScreenState


class UnknownMenuTests(unittest.TestCase):
    def test_diagnostic_reports_inset_x_without_clicking(self):
        fixtures = Path(__file__).parent / "fixtures"
        panel = fixtures / "unknown_menu_inset_close.png"
        city = fixtures / "forge_bone_stacked.png"
        back_corner = cv2.imread(str(fixtures / "unknown_menu_back.png"))
        frame = np.full((720, 1280, 3), 120, np.uint8)
        height, width = back_corner.shape[:2]
        frame[:height, :width] = back_corner
        with tempfile.TemporaryDirectory() as directory:
            back_only = Path(directory) / "back_only.png"
            cv2.imwrite(str(back_only), frame)
            for screenshot, expected_state, expected_close in (
                (panel, "unknown", True), (back_only, "unknown", False), (city, "city", False),
            ):
                with self.subTest(screenshot=screenshot.name):
                    game = MagicMock(screenshot_path=screenshot)
                    game.screenshot.return_value = screenshot
                    with patch.object(sys, "argv", ["rok_automation", "--detect-screen"]), \
                         patch("rok_automation.__main__._build_game", return_value=game), \
                         patch("rok_automation.__main__.save_frame", return_value=screenshot), \
                         patch("rok_automation.__main__.print") as output:
                        self.assertEqual(main(), 0)
                    lines = [call.args[0] for call in output.call_args_list]
                    self.assertTrue(any(line.startswith(f"Screen: {expected_state} ") for line in lines))
                    reported = [line for line in lines if line.startswith("Close X detected")]
                    if expected_close:
                        self.assertEqual(len(reported), 1)
                        self.assertRegex(reported[0], r"Close X detected at \(10\d\d, \d+\) \(confidence=0\.\d{3}\)")
                    else:
                        self.assertFalse(reported)
                    game.click.assert_not_called()

    def test_inset_gold_mine_close_recovers_and_rescans_city(self):
        fixtures = Path(__file__).parent / "fixtures"
        panel = fixtures / "unknown_menu_inset_close.png"
        city = fixtures / "forge_bone_stacked.png"

        for flow in ("farming", "city tasks", "Gmail"):
            with self.subTest(flow=flow):
                game = MagicMock(timing=TimingConfig())
                game.screenshot.side_effect = [panel, city]
                detector = ScreenDetector()
                if flow == "farming":
                    controller = FarmingController(game, detector=detector, log=lambda _: None)
                    shot, detection = controller._read_screen()
                    self.assertEqual(shot, city)
                    self.assertIs(detection.state, ScreenState.CITY)
                elif flow == "city tasks":
                    runner = CityTaskRunner(game, detector, log=lambda _: None)
                    self.assertEqual(runner._capture_without_interruptions(expect_city=True), (city, True))
                else:
                    switcher = GmailSwitcher(game, detector, log=lambda _: None)
                    switcher._wait_for_state_change("governor_profile")

                self.assertEqual(game.screenshot.call_count, 2)
                game.click.assert_called_once()
                bounds, screenshot = game.click.call_args.args
                self.assertEqual(screenshot, panel)
                x, y, w, h = bounds
                self.assertTrue(1080 <= x + w // 2 <= 1100 and 76 <= y + h // 2 <= 100)

    def test_close_x_variants_in_both_top_corners(self):
        fixtures = Path(__file__).parent / "fixtures"
        with tempfile.TemporaryDirectory() as directory:
            for name in ("event", "dlc"):
                corner = cv2.imread(str(fixtures / f"unknown_menu_{name}_close.png"))
                h, w = corner.shape[:2]
                for left, top, expected in ((0, 0, True), (1120, 0, True),
                                            (600, 0, False), (1120, 300, False)):
                    with self.subTest(variant=name, position=(left, top)):
                        frame = np.full((720, 1280, 3), 120, np.uint8)
                        frame[top:top+h, left:left+w] = corner
                        screenshot = Path(directory) / f"{name}_{left}_{top}.png"
                        cv2.imwrite(str(screenshot), frame)
                        game = MagicMock()
                        self.assertEqual(back_out_of_unknown_menu(
                            game, ScreenDetector(), screenshot, log=lambda _: None,
                        ), expected)
                        if expected:
                            game.click.assert_called_once()
                            x, y, bw, bh = game.click.call_args.args[0]
                            self.assertTrue(left <= x + bw // 2 < left + w)
                            self.assertTrue(top <= y + bh // 2 < top + h)
                        else:
                            game.click.assert_not_called()

    def test_event_menu_arrow_is_clicked_only_in_top_left(self):
        # Actual event-menu corner supplied by the user, including its background.
        corner = cv2.imread(str(Path(__file__).parent / "fixtures/unknown_menu_back.png"))
        with tempfile.TemporaryDirectory() as directory:
            for left, top, expected in ((0, 0, True), (600, 300, False)):
                with self.subTest(position=(left, top)):
                    frame = np.full((720, 1280, 3), 120, np.uint8)
                    frame[top:top + 86, left:left + 128] = corner
                    screenshot = Path(directory) / f"menu_{left}.png"
                    cv2.imwrite(str(screenshot), frame)
                    game = MagicMock()
                    found = back_out_of_unknown_menu(
                        game, ScreenDetector(), screenshot, log=lambda _: None,
                    )
                    self.assertEqual(found, expected)
                    if expected:
                        x, y, w, h = game.click.call_args.args[0]
                        self.assertTrue(14 <= x + w // 2 <= 71)
                        self.assertTrue(9 <= y + h // 2 <= 65)
                    else:
                        game.click.assert_not_called()

    def test_no_arrow_does_not_click(self):
        with tempfile.TemporaryDirectory() as directory:
            screenshot = Path(directory) / "blank.png"
            cv2.imwrite(str(screenshot), np.zeros((720, 1280, 3), np.uint8))
            game = MagicMock()
            self.assertFalse(back_out_of_unknown_menu(
                game, ScreenDetector(), screenshot, log=lambda _: None,
            ))
            game.click.assert_not_called()

    @patch("rok_automation.game.popups.close_extended_view", return_value=False)
    def test_recovery_recaptures_and_detects_again(self, _close_extended):
        game = MagicMock(timing=TimingConfig())
        game.screenshot.side_effect = [Path("event"), Path("world")]
        controller = FarmingController(game, log=lambda _: None)
        unknown = ScreenDetection(ScreenState.UNKNOWN, 0.0, "event menu")
        world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "search button")
        with patch.object(controller.detector, "detect", side_effect=[unknown, world]) as classify, patch(
            "rok_automation.game.popups.close_scout_manager", return_value=False,
        ), patch(
            "rok_automation.game.popups.back_out_of_unknown_menu", return_value=True,
        ) as back:
            self.assertEqual(controller._read_screen(), (Path("world"), world))
        back.assert_called_once()
        self.assertEqual(classify.call_args_list[1].args[0], Path("world"))

    @patch("rok_automation.game.popups.close_extended_view", return_value=False)
    def test_recovery_is_bounded_and_leaves_known_screens_alone(self, _close_extended):
        for unknown_count, expected_backs in ((0, 0), (4, 3)):
            with self.subTest(unknown_count=unknown_count):
                game = MagicMock(timing=TimingConfig())
                game.screenshot.return_value = Path("frame")
                controller = FarmingController(game, log=lambda _: None)
                unknown = ScreenDetection(ScreenState.UNKNOWN, 0.0, "menu")
                world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "search")
                with patch.object(controller.detector, "detect", side_effect=
                    [unknown] * unknown_count + [world],
                ), patch(
                    "rok_automation.game.popups.close_scout_manager", return_value=False,
                ), patch(
                    "rok_automation.game.popups.back_out_of_unknown_menu", return_value=True,
                ) as back:
                    self.assertEqual(controller._read_screen()[1], world)
                self.assertEqual(back.call_count, expected_backs)


if __name__ == "__main__":
    unittest.main()
