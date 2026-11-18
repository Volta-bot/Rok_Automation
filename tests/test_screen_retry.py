import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.game.screen_retry import screen_scan_attempts
from rok_automation.game.interface import TimingConfig
from rok_automation.automation.controller import FarmingController, FarmingError
from rok_automation.automation.daily_reward import DailyRewardClaimer
from rok_automation.vision.screen import ScreenDetection, ScreenState


class ScreenRetryTests(unittest.TestCase):
    def test_expired_deadline_still_allows_initial_scan_plus_five_rescans(self):
        self.assertEqual(list(screen_scan_attempts(0, clock=lambda: 1)), list(range(1, 7)))

    def test_deadline_can_allow_more_than_five_rescans(self):
        clock = iter([0] * 8 + [10])
        self.assertEqual(len(list(screen_scan_attempts(5, clock=lambda: next(clock)))), 8)

    def test_daily_rewards_can_find_menu_on_fifth_rescan_after_timeout(self):
        game = MagicMock()
        claimer = DailyRewardClaimer(game, detector=MagicMock(), log=lambda _: None)
        frames = [object() for _ in range(6)]
        condition = MagicMock(side_effect=[False] * 5 + [True])
        with patch.object(claimer, "_capture", side_effect=[(Path(str(i)), f, (1280, 720)) for i, f in enumerate(frames)]), \
             patch("rok_automation.automation.daily_reward.time.monotonic", side_effect=range(100)), \
             patch("rok_automation.game.popups.close_mailbox", return_value=False):
            self.assertTrue(claimer._wait_for_condition(condition, timeout=0))
        self.assertEqual([call.args[0] for call in condition.call_args_list], frames)
        game.click.assert_not_called()

    def test_daily_rewards_failure_rescans_without_repeating_clicks(self):
        game = MagicMock()
        claimer = DailyRewardClaimer(game, detector=MagicMock(), log=lambda _: None)
        with patch.object(claimer, "_capture", return_value=(Path("frame"), np.zeros((1, 1, 3)), (1, 1))) as capture, \
             patch("rok_automation.automation.daily_reward.time.monotonic", side_effect=range(100)), \
             patch("rok_automation.game.popups.close_mailbox", return_value=False):
            self.assertFalse(claimer._wait_for_condition(lambda _: False, timeout=0))
        self.assertEqual(capture.call_count, 6)
        game.click.assert_not_called()

    def test_farming_wait_rescans_even_when_capture_exceeds_timeout(self):
        game = MagicMock(timing=TimingConfig())
        detector = MagicMock()
        detector.detect.return_value = ScreenDetection(ScreenState.UNKNOWN, 0, "loading")
        controller = FarmingController(game, detector=detector, log=lambda _: None)
        with patch("rok_automation.automation.controller.time.monotonic", side_effect=range(0, 1000, 100)):
            with self.assertRaises(FarmingError):
                controller._wait_for_states({ScreenState.RESOURCE_SEARCH})
        self.assertEqual(game.screenshot.call_count, 6)

    def test_failed_campaign_menu_does_not_proceed_to_next_step(self):
        claimer = DailyRewardClaimer(MagicMock(), detector=MagicMock(), log=lambda _: None)
        with patch.object(claimer, "_capture", return_value=(Path("frame"), np.zeros((720, 1280, 3)), (1280, 720))), \
             patch.object(claimer, "_locate_chien_dich", return_value=(1, 2, 3, 4)), \
             patch.object(claimer, "_wait_for_condition", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Chiến dịch menu did not open"):
                claimer._open_chien_dich_menu((1280, 720))
