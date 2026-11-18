import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2

from rok_automation.automation.controller import FarmingController, FarmingError
from rok_automation.logic.resource_selector import Resource
from rok_automation.game.frame import normalize_capture
from rok_automation.game.interface import TimingConfig
from rok_automation.game.popups import close_mailbox
from rok_automation.vision.screen import ScreenDetection, ScreenDetector, ScreenState


class MailboxTests(unittest.TestCase):
    fixture = Path(__file__).parent / "fixtures/mailbox.png"

    def test_search_waits_close_mailbox_and_request_same_target_retry(self):
        for operation in ("result", "level", "button"):
            with self.subTest(operation=operation):
                game = MagicMock(timing=TimingConfig())
                game.screenshot.return_value = self.fixture
                controller = FarmingController(game, log=lambda _: None)
                with self.assertRaisesRegex(FarmingError, "Closed accidental mailbox"):
                    if operation == "result":
                        controller._wait_for_search_result(None)
                    elif operation == "level":
                        controller._wait_for_search_menu_level()
                    else:
                        controller._click_template(
                            Path("templates/search/submit_button.png"),
                            expected_state=ScreenState.RESOURCE_SEARCH,
                        )
                game.click.assert_called_once()
                x, y, w, h = game.click.call_args.args[0]
                self.assertGreater(x + w / 2, 1180)
                self.assertLess(y + h / 2, 60)

    def test_mailbox_returned_by_navigation_resumes_farming(self):
        game = MagicMock(timing=TimingConfig())
        controller = FarmingController(game, log=lambda _: None)
        controller.context.preferred_resource = Resource.GOLD
        world = ScreenDetection(ScreenState.WORLD_MAP, .95, "world")
        mailbox = ScreenDetection(ScreenState.MAILBOX, .95, "mailbox")
        troops = ScreenDetection(ScreenState.NEW_TROOPS_PROMPT, .95, "troops")
        levels = {resource: 8 for resource in Resource}
        with patch.object(controller, "_read_screen", side_effect=[
            (Path("world"), world), (Path("troops"), troops),
        ]) as read, patch.object(controller, "_press_and_wait", return_value=(self.fixture, mailbox)), \
             patch.object(controller, "_dispatch_army", return_value=True):
            self.assertTrue(controller._farm_one_army(1, levels, set()))
        self.assertEqual(read.call_count, 2)
        self.assertTrue(all(level == 8 for level in levels.values()))

    def test_detects_mailbox_and_closes_at_capture_sizes(self):
        original = cv2.imread(str(self.fixture))
        with tempfile.TemporaryDirectory() as directory:
            for name, frame in (("original", original),
                                ("canonical", normalize_capture(original))):
                with self.subTest(size=name):
                    screenshot = Path(directory) / f"{name}.png"
                    cv2.imwrite(str(screenshot), frame)
                    detector = ScreenDetector()
                    self.assertEqual(detector.detect(screenshot, read_account=False).state,
                                     ScreenState.MAILBOX)
                    game = MagicMock()
                    self.assertTrue(close_mailbox(game, detector, screenshot, log=lambda _: None))
                    x, y, w, h = game.click.call_args.args[0]
                    self.assertGreater(x + w // 2, frame.shape[1] * 0.94)
                    self.assertLess(y + h // 2, frame.shape[0] * 0.08)

    def test_requires_mailbox_and_visible_close(self):
        original = cv2.imread(str(self.fixture))
        with tempfile.TemporaryDirectory() as directory:
            for missing in ("tabs", "close"):
                with self.subTest(missing=missing):
                    frame = original.copy()
                    if missing == "tabs":
                        frame[:65, :485] = 120
                    else:
                        frame[:65, 1180:] = 120
                    screenshot = Path(directory) / f"no_{missing}.png"
                    cv2.imwrite(str(screenshot), frame)
                    game = MagicMock()
                    self.assertFalse(close_mailbox(
                        game, ScreenDetector(), screenshot, log=lambda _: None,
                    ))
                    game.click.assert_not_called()

    def test_dispatch_classifier_recognizes_mailbox(self):
        controller = FarmingController(MagicMock(timing=TimingConfig()), log=lambda _: None)
        detection, ambiguous = controller._classify_dispatch_screen(self.fixture)
        self.assertEqual(detection.state, ScreenState.MAILBOX)
        self.assertFalse(ambiguous)

    @patch("rok_automation.game.popups.close_extended_view", return_value=False)
    def test_screen_read_closes_mailbox_and_detects_fresh_frame(self, _close_extended):
        game = MagicMock(timing=TimingConfig())
        game.screenshot.side_effect = [self.fixture, Path("world")]
        controller = FarmingController(game, log=lambda _: None)
        mailbox = ScreenDetector().detect(self.fixture, read_account=False)
        world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "search button")
        with patch.object(controller, "_classify_dispatch_screen", side_effect=[
            (mailbox, False), (world, False),
        ]) as classify:
            self.assertEqual(controller._read_screen(), (Path("world"), world))
        game.click.assert_called_once()
        self.assertEqual(classify.call_args_list[1].args[0], Path("world"))


if __name__ == "__main__":
    unittest.main()
