"""Unit tests for the runtime daily-reward flag and its use in the farm."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.game.flags import (
    clear_daily_rewards,
    daily_rewards_enabled,
    set_daily_rewards,
)


class TestDailyRewardFlag(unittest.TestCase):
    """The flag file is the channel between the interface and the worker."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "daily_reward.flag"
        self.addCleanup(self.tmp.cleanup)

    def test_the_flag_starts_off(self) -> None:
        self.assertFalse(daily_rewards_enabled(self.path))

    def test_setting_writes_the_flag(self) -> None:
        set_daily_rewards(True, self.path)
        self.assertTrue(self.path.is_file())
        self.assertTrue(daily_rewards_enabled(self.path))

    def test_clearing_removes_the_flag(self) -> None:
        set_daily_rewards(True, self.path)
        clear_daily_rewards(self.path)
        self.assertFalse(daily_rewards_enabled(self.path))

    def test_clearing_a_missing_flag_is_not_an_error(self) -> None:
        clear_daily_rewards(self.path)

    def test_setting_off_clears_the_flag(self) -> None:
        set_daily_rewards(True, self.path)
        set_daily_rewards(False, self.path)
        self.assertFalse(daily_rewards_enabled(self.path))


def _controller(flag_path: Path) -> FarmingController:
    """Build a controller that never touches the screen."""

    game = MagicMock()
    with patch.object(FarmingController, "__init__", lambda self, *a, **k: None):
        controller = FarmingController(game)
    controller.log = lambda *_args, **_kwargs: None
    controller._log = lambda *_args, **_kwargs: None
    controller.daily_rewards = False
    controller.daily_rewards_flag_path = flag_path
    controller._daily_rewards_enabled_before = False
    return controller


class TestDynamicDailyRewards(unittest.TestCase):
    """The option can be switched while a run is going."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "daily_reward.flag"
        self.addCleanup(self.tmp.cleanup)

    def test_off_until_the_flag_appears(self) -> None:
        controller = _controller(self.path)
        self.assertFalse(controller._should_claim_daily_rewards())
        set_daily_rewards(True, self.path)
        self.assertTrue(controller._should_claim_daily_rewards())

    def test_back_off_stops_the_claim(self) -> None:
        controller = _controller(self.path)
        set_daily_rewards(True, self.path)
        controller._should_claim_daily_rewards()
        clear_daily_rewards(self.path)
        self.assertFalse(controller._should_claim_daily_rewards())

    def test_an_explicit_cli_option_always_wins(self) -> None:
        controller = _controller(self.path)
        controller.daily_rewards = True
        clear_daily_rewards(self.path)
        self.assertTrue(controller._should_claim_daily_rewards())


if __name__ == "__main__":
    unittest.main()
