import unittest
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.logic.resource_selector import Resource
from rok_automation.vision.screen import ScreenDetection, ScreenState


class TestPostFarmingCityTasks(unittest.TestCase):
    def test_city_tasks_follow_dispatch_and_return_to_city(self):
        events = []
        game, detector = MagicMock(), MagicMock()
        world = ScreenDetection(state=ScreenState.WORLD_MAP, confidence=1,
                                evidence="map", armies_out=4,
                                account_name="test", preferred_resource=Resource.FOOD)
        detector.detect.return_value = world
        controller = FarmingController(game, detector=detector, daily_rewards_flag_path=None,
                                       max_armies=1, log=lambda _: None)
        with patch.object(controller, "_open_world_map_directly", return_value=("frame", world)), \
             patch.object(controller, "_farm_one_army", side_effect=lambda *args: events.append("farm") or True), \
             patch("rok_automation.automation.controller.load_recent_farmed_nodes", return_value=[]), \
             patch("rok_automation.automation.controller.city_resources_enabled", return_value=True), \
             patch("rok_automation.automation.controller.forge_material_to_produce", return_value="leather"), \
             patch("rok_automation.automation.daily_reward.DailyRewardClaimer.ensure_city_view", side_effect=lambda **kwargs: events.append("city") or True), \
             patch.object(controller, "_run_city_tasks", side_effect=lambda **kwargs: events.append("chores")):
            controller._run_one_account()
        self.assertEqual(events, ["farm", "city", "chores"])

    def test_no_city_tasks_if_return_fails(self):
        controller = FarmingController(MagicMock(), detector=MagicMock(), log=lambda _: None)
        with patch("rok_automation.automation.controller.city_resources_enabled", return_value=True), \
             patch("rok_automation.automation.controller.forge_material_to_produce", return_value=None), \
             patch("rok_automation.automation.daily_reward.DailyRewardClaimer.ensure_city_view", return_value=False), \
             patch.object(controller, "_run_city_tasks") as chores:
            controller._run_enabled_city_tasks()
        chores.assert_not_called()
