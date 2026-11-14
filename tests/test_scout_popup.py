import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np

from rok_automation.game.popups import close_scout_manager
from rok_automation.vision.screen import ScreenDetector


class TestScoutPopup(unittest.TestCase):
    def test_recognizes_scout_title_and_clicks_close(self):
        nav = Path(__file__).resolve().parents[1] / "templates" / "navigation"
        title = cv2.imread(str(nav / "scout_manager_title.png"))
        close = cv2.imread(str(nav / "scout_manager_close.png"))
        frame = np.full((720, 1280, 3), 120, dtype=np.uint8)
        frame[15:15 + title.shape[0], 460:460 + title.shape[1]] = title
        frame[10:10 + close.shape[0], 1190:1190 + close.shape[1]] = close
        with tempfile.TemporaryDirectory() as directory:
            screenshot = Path(directory) / "scout.png"
            cv2.imwrite(str(screenshot), frame)
            game = MagicMock()
            self.assertTrue(close_scout_manager(game, ScreenDetector(), screenshot, log=lambda _: None))
            x, y, w, h = game.click.call_args.args[0]
            self.assertTrue(1190 <= x + w // 2 <= 1233)
            self.assertTrue(10 <= y + h // 2 <= 52)

    def test_does_not_close_unrelated_menu_with_an_x(self):
        game, detector = MagicMock(), MagicMock()
        detector.locate_template.return_value = None
        self.assertFalse(close_scout_manager(game, detector, Path("frame.png"), log=lambda _: None))
        game.click.assert_not_called()
