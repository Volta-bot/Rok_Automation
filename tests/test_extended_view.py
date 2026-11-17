import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.game.interface import TimingConfig
from rok_automation.game.popups import close_extended_view
from rok_automation.vision.screen import ScreenDetector, ScreenDetection, ScreenState, TemplateHit


class ExtendedViewTests(unittest.TestCase):
    def test_attached_popup_taps_city_toggle(self):
        # The supplied left-edge crop is padded to a landscape game frame.
        screenshot = Path(__file__).parent / "fixtures/extended_view.png"
        game = MagicMock()
        self.assertTrue(close_extended_view(game, ScreenDetector(), screenshot, log=lambda _: None))
        x, y, w, h = game.click.call_args.args[0]
        self.assertTrue(30 < x + w / 2 < 90)
        self.assertTrue(620 < y + h / 2 < 690)
        game.click.assert_called_once()

    def test_no_selector_does_not_toggle_normal_map(self):
        game, detector = MagicMock(), MagicMock()
        detector.locate_template.return_value = None
        self.assertFalse(close_extended_view(game, detector, Path("frame"), log=lambda _: None))
        game.click.assert_not_called()

    def test_selector_without_toggle_does_not_click(self):
        game, detector = MagicMock(), MagicMock()
        detector.locate_template.side_effect = [TemplateHit(0.99, (144, 448, 57, 59)), None, None]
        self.assertFalse(close_extended_view(game, detector, Path("frame"), log=lambda _: None))
        game.click.assert_not_called()

    def test_world_map_is_read_again_after_closing_popup(self):
        game = MagicMock()
        game.timing = TimingConfig()
        game.screenshot.side_effect = [Path("popup"), Path("normal")]
        controller = FarmingController(game, detector=MagicMock(), log=lambda _: None)
        world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "city button")
        with patch.object(controller, "_classify_dispatch_screen", return_value=(world, False)), \
             patch("rok_automation.game.popups.close_extended_view", side_effect=[True, False]) as close:
            screenshot, detection = controller._read_screen()
        self.assertEqual(screenshot, Path("normal"))
        self.assertEqual(detection.state, ScreenState.WORLD_MAP)
        self.assertEqual(close.call_count, 2)
