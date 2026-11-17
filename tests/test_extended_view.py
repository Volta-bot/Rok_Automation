import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.__main__ import main
from rok_automation.game.interface import TimingConfig
from rok_automation.game.popups import close_extended_view
from rok_automation.vision.screen import ScreenDetector, ScreenDetection, ScreenState, TemplateHit


class ExtendedViewTests(unittest.TestCase):
    def test_diagnostic_checks_popup_only_in_world_view_without_clicking(self):
        for state, visible in ((ScreenState.WORLD_MAP, True),
                               (ScreenState.WORLD_MAP, False),
                               (ScreenState.CITY, True),
                               (ScreenState.UNKNOWN, True)):
            with self.subTest(state=state, visible=visible):
                game = MagicMock(screenshot_path="frame.png")
                detection = ScreenDetection(state, 0.95, "test screen")
                hit = TemplateHit(0.95, (3, 600, 106, 111)) if visible else None
                with patch.object(sys, "argv", ["rok_automation", "--detect-screen"]), \
                     patch("rok_automation.__main__._build_game", return_value=game), \
                     patch("rok_automation.__main__.save_frame", return_value=Path("frame.png")), \
                     patch("rok_automation.__main__.ScreenDetector") as detector, \
                     patch("rok_automation.game.popups.locate_extended_view", return_value=hit) as locate, \
                     patch("rok_automation.__main__.print") as output:
                    detector.return_value.detect.return_value = detection
                    self.assertEqual(main(), 0)
                if state is ScreenState.WORLD_MAP:
                    locate.assert_called_once_with(detector.return_value, Path("frame.png"))
                    output.assert_any_call("Extended view popup: " + ("detected" if visible else "not detected"))
                else:
                    locate.assert_not_called()
                    self.assertFalse(any("Extended view popup" in str(call) for call in output.call_args_list))
                game.click.assert_not_called()

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
