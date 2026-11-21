import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2

from rok_automation.automation.controller import FarmingController, FarmingError
from rok_automation.automation.city_tasks import CityTaskRunner
from rok_automation.automation.gmail_switcher import GmailIdentity, GmailRotation, GmailSwitcher, _LilithScreen
from rok_automation.logic.resource_selector import Resource
from rok_automation.game.frame import normalize_capture
from rok_automation.game.interface import TimingConfig
from rok_automation.game.popups import close_mailbox
from rok_automation.vision.screen import (
    FARMING_SCREEN_STATES, ScreenDetection, ScreenDetector, ScreenState, TemplateHit, load_color_frame,
)


class MailboxTests(unittest.TestCase):
    fixture = Path(__file__).parent / "fixtures/mailbox.png"
    city = Path(__file__).parent / "fixtures/forge_bone_stacked.png"

    def test_gmail_switch_closes_mailbox_then_resumes_from_city(self):
        game = MagicMock(timing=TimingConfig())
        switcher = GmailSwitcher(game, ScreenDetector(), log=lambda _: None)
        identity = GmailIdentity(email="example@gmail.com")
        user_center = Path("user-center.png")
        frames = [
            (self.fixture, load_color_frame(self.fixture)),
            (self.city, load_color_frame(self.city)),
            (user_center, object()),
        ]
        with patch.object(switcher, "_capture", side_effect=frames), \
             patch.object(switcher, "_read_lilith_screen", side_effect=[
                 None, None, _LilithScreen(state="user_center", current=identity),
             ]), patch.object(switcher, "_wait_for_state_change"), \
             patch.object(switcher, "_close_lilith_ui"):
            result = switcher.switch_to_next(GmailRotation(), observe_only=True)

        self.assertEqual(result.identity, identity)
        self.assertEqual(game.click.call_count, 2)
        bounds, shot = game.click.call_args_list[0].args
        self.assertEqual(shot, self.fixture)
        self.assertGreater(bounds[0], 1180)
        bounds, shot = game.click.call_args_list[1].args
        self.assertEqual(shot, self.city)
        self.assertLess(bounds[0], 40)

    def test_forge_closes_mailbox_before_retrying_from_city(self):
        game = MagicMock(timing=TimingConfig())
        game.screenshot.side_effect = [self.city, self.fixture, self.city, self.city]
        runner = CityTaskRunner(game, ScreenDetector(), log=lambda _: None)
        icon = TemplateHit(.95, (200, 220, 40, 40))
        materials = TemplateHit(.95, (500, 400, 40, 40))
        with patch.object(runner, "_find_forge_marker", side_effect=[
            ("bone", icon), ("idle", icon),
        ]) as markers, patch.object(runner, "_wait_for_menu_control", return_value=(self.city, materials)):
            self.assertEqual(runner._open_forge(), (self.city, materials))

        self.assertEqual([entry.args[0] for entry in markers.call_args_list], [self.city, self.city])
        self.assertEqual([entry.args[1] for entry in game.click.call_args_list],
                         [self.city, self.fixture, self.city])
        self.assertGreater(game.click.call_args_list[1].args[0][0], 1180)

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
        detection = controller.detector.detect(
            self.fixture, candidate_states=FARMING_SCREEN_STATES, read_account=False,
        )
        self.assertEqual(detection.state, ScreenState.MAILBOX)

    @patch("rok_automation.game.popups.close_extended_view", return_value=False)
    def test_screen_read_closes_mailbox_and_detects_fresh_frame(self, _close_extended):
        game = MagicMock(timing=TimingConfig())
        game.screenshot.side_effect = [self.fixture, Path("world")]
        controller = FarmingController(game, log=lambda _: None)
        mailbox = ScreenDetector().detect(self.fixture, read_account=False)
        world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "search button")
        with patch.object(controller.detector, "detect", side_effect=[mailbox, world]) as classify:
            self.assertEqual(controller._read_screen(), (Path("world"), world))
        game.click.assert_called_once()
        self.assertEqual(classify.call_args_list[1].args[0], Path("world"))


if __name__ == "__main__":
    unittest.main()
