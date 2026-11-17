import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.automation.account_switcher import AccountSwitcher, AccountTraversal
from rok_automation.vision.screen import ScreenState


class AccountLoginRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.game = MagicMock()
        self.game.timing.scaled.side_effect = lambda seconds: seconds
        self.game.timing.poll_interval = 0.2
        self.elapsed = 0.0
        self.game.wait.side_effect = self.advance_time
        self.clock = patch(
            'rok_automation.automation.account_switcher.time.monotonic',
            side_effect=lambda: self.elapsed,
        )
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.switcher = AccountSwitcher(self.game, MagicMock(), log=lambda _: None)
        self.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.switcher._capture = MagicMock(return_value=('frame', self.frame))
        self.yes_bounds = (690, 480, 240, 60)
        self.card_bounds = (300, 220, 230, 32)

    def advance_time(self, seconds):
        self.elapsed += seconds

    def select(self, target_index):
        self.switcher._select_card_and_confirm_login(
            AccountTraversal(), target_index=target_index, card_offset=1,
            initial_screenshot='initial', initial_bounds=self.card_bounds,
        )

    def test_delayed_login_during_menu_recovery_is_confirmed_without_scrolling(self):
        for target in (1, 7):
            with self.subTest(target=target):
                self.game.click.reset_mock()
                self.switcher._detect_menu_state = MagicMock(side_effect=[
                    ('governor_profile', (10, 10, 40, 40), 0.9),
                    ('login_confirmation', self.yes_bounds, 0.95),
                    ('login_confirmation', self.yes_bounds, 0.95),
                ])
                with patch.object(self.switcher, '_find_login_confirmation_yes',
                                  return_value=(self.yes_bounds, 0.96, 0.95)), \
                     patch.object(self.switcher, '_wait_for_menu_state_change'), \
                     patch.object(self.switcher, '_scroll_all_the_way') as scroll:
                    self.select(target)
                self.assertEqual(self.game.click.call_count, 2)
                self.game.click.assert_called_with(self.yes_bounds, 'frame')
                scroll.assert_not_called()

    def test_recovery_still_reselects_target_when_character_list_reopens(self):
        self.switcher._detect_menu_state = MagicMock(side_effect=[
            ('governor_profile', (10, 10, 40, 40), 0.9),
            ('character_list', None, 1.0),
            ('character_list', None, 1.0),
            ('login_confirmation', self.yes_bounds, 0.95),
        ])
        with patch.object(self.switcher, '_find_login_confirmation_yes',
                          return_value=(self.yes_bounds, 0.96, 0.95)), \
             patch.object(self.switcher, '_wait_for_menu_state_change'), \
             patch.object(self.switcher, '_scroll_all_the_way') as scroll, \
             patch.object(self.switcher, '_read_account_cards', return_value=[
                 ('first', (100, 220, 230, 32)), ('target', self.card_bounds),
             ]):
            self.select(7)
        scroll.assert_called_once()
        self.assertEqual(self.game.click.call_count, 3)
        self.game.click.assert_called_with(self.yes_bounds, 'frame')

    def test_load_wait_retries_yes_until_dialog_closes(self):
        with patch.object(self.switcher, '_find_login_confirmation_yes', side_effect=[
            (self.yes_bounds, 0.96, 0.95),
            (self.yes_bounds, 0.96, 0.95),
            None,
        ]), patch.object(self.switcher, '_find_dialog_confirmation', return_value=None), \
             patch.object(self.switcher, '_character_list_is_open', return_value=False):
            self.switcher.detector.detect.return_value.state = ScreenState.CITY
            self.switcher._wait_for_game('target')
        self.assertEqual(self.game.click.call_count, 2)
        self.game.click.assert_called_with(self.yes_bounds, 'frame')
        self.switcher.detector.detect.assert_called_once()


if __name__ == '__main__':
    unittest.main()
