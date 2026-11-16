import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from rok_automation.automation.controller import FarmingController
from rok_automation.game.interface import TimingConfig
from rok_automation.game.popups import back_out_of_unknown_menu
from rok_automation.vision.screen import ScreenDetection, ScreenDetector, ScreenState


class UnknownMenuTests(unittest.TestCase):
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

    def test_recovery_recaptures_and_detects_again(self):
        game = MagicMock(timing=TimingConfig())
        game.screenshot.side_effect = [Path("event"), Path("world")]
        controller = FarmingController(game, log=lambda _: None)
        unknown = ScreenDetection(ScreenState.UNKNOWN, 0.0, "event menu")
        world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "search button")
        with patch.object(controller, "_classify_dispatch_screen", side_effect=[
            (unknown, False), (world, False),
        ]) as classify, patch(
            "rok_automation.game.popups.close_scout_manager", return_value=False,
        ), patch(
            "rok_automation.game.popups.back_out_of_unknown_menu", return_value=True,
        ) as back:
            self.assertEqual(controller._read_screen(), (Path("world"), world))
        back.assert_called_once()
        self.assertEqual(classify.call_args_list[1].args[0], Path("world"))

    def test_recovery_is_bounded_and_leaves_known_screens_alone(self):
        for unknown_count, expected_backs in ((0, 0), (4, 3)):
            with self.subTest(unknown_count=unknown_count):
                game = MagicMock(timing=TimingConfig())
                game.screenshot.return_value = Path("frame")
                controller = FarmingController(game, log=lambda _: None)
                unknown = ScreenDetection(ScreenState.UNKNOWN, 0.0, "menu")
                world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "search")
                with patch.object(controller, "_classify_dispatch_screen", side_effect=
                    [(unknown, False)] * unknown_count + [(world, False)],
                ), patch(
                    "rok_automation.game.popups.close_scout_manager", return_value=False,
                ), patch(
                    "rok_automation.game.popups.back_out_of_unknown_menu", return_value=True,
                ) as back:
                    self.assertEqual(controller._read_screen()[1], world)
                self.assertEqual(back.call_count, expected_backs)


if __name__ == "__main__":
    unittest.main()
