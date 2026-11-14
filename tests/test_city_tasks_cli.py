import sys
import unittest
from unittest.mock import MagicMock, patch

from rok_automation.__main__ import main


class TestCityTasksCLI(unittest.TestCase):
    def test_forge_test_runs_only_selected_material(self):
        with patch.object(sys, "argv", ["rok_automation", "--test-forge-material", "Iron"]), \
             patch("rok_automation.__main__._build_game", return_value=MagicMock()), \
             patch("rok_automation.__main__.clear_paused"), \
             patch("rok_automation.__main__.FarmingController") as controller:
            self.assertEqual(main(), 0)
        controller.return_value._run_city_tasks_from_current_screen.assert_called_once_with(
            collect_resources=False, forge_material="Iron")
        controller.return_value.run.assert_not_called()

    def test_city_tasks_and_rewards_without_farming(self):
        events = []
        with patch.object(sys, "argv", ["rok_automation", "--city-tasks-only", "--daily-reward-only"]), \
             patch("rok_automation.__main__._build_game", return_value=MagicMock()), \
             patch("rok_automation.__main__.clear_paused"), \
             patch("rok_automation.__main__.FarmingController") as controller, \
             patch("rok_automation.automation.daily_reward.DailyRewardClaimer") as claimer:
            controller.return_value._run_enabled_city_tasks.side_effect = lambda: events.append("city")
            claimer.return_value.claim_all.side_effect = lambda: events.append("rewards") or True
            self.assertEqual(main(), 0)
        self.assertEqual(events, ["city", "rewards"])
        controller.return_value.run.assert_not_called()
