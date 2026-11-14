import unittest
from unittest.mock import MagicMock, patch
import numpy as np

from rok_automation.automation.account_switcher import AccountSwitcher, AccountTraversal


class AccountBottomTraversalTests(unittest.TestCase):
    def make_switcher(self, current_index):
        switcher = AccountSwitcher(MagicMock(), MagicMock(), log=lambda _: None)
        top = np.zeros((100, 100, 3), dtype=np.uint8)
        bottom = np.ones((100, 100, 3), dtype=np.uint8)
        top_cards = [(f'account {i}', (i, 0, 1, 1)) for i in range(6)]
        bottom_cards = [(f'account {i}', (i, 0, 1, 1)) for i in range(6, 12)]
        switcher._open_character_list = MagicMock()
        def read(traversal, *, initial=None, **kwargs):
            traversal.total_accounts = 12
            if initial is None:
                return 'top', top, top_cards, current_index if current_index < 6 else None
            return 'bottom', bottom, bottom_cards, current_index - 6 if current_index >= 6 else None
        switcher._read_page_and_active_position = MagicMock(side_effect=read)
        switcher._scroll_all_the_way = MagicMock(return_value=('bottom', bottom, bottom_cards))
        switcher._starred_accounts_header_bounds = lambda frame: (1,) if frame is top else None
        switcher._regular_accounts_header_bounds = lambda frame: (1,) if frame is bottom else None
        switcher._select_card_and_confirm_login = MagicMock()
        switcher._wait_for_game = MagicMock()
        return switcher

    def test_initial_switch_selects_last_and_records_already_farmed_account(self):
        switcher = self.make_switcher(1)
        traversal = AccountTraversal()
        initialized = MagicMock()
        self.assertEqual(switcher.switch_to_next(traversal, on_initialized=initialized), 'account 11')
        initialized.assert_called_once_with(1, 12, -1)
        self.assertEqual(traversal.skipped_index, 1)
        self.assertEqual(traversal.current_index, 11)
        self.assertTrue(switcher._scroll_all_the_way.call_args.kwargs['toward_list_end'])

    def test_if_last_account_is_current_selects_previous(self):
        switcher = self.make_switcher(11)
        traversal = AccountTraversal()
        self.assertEqual(switcher.switch_to_next(traversal), 'account 10')
        self.assertEqual(traversal.current_index, 10)

    def test_does_not_select_last_without_reaching_bottom(self):
        switcher = self.make_switcher(1)
        switcher._regular_accounts_header_bounds = lambda frame: None
        with self.assertRaisesRegex(RuntimeError, 'bottom'):
            switcher.switch_to_next(AccountTraversal())
        switcher._select_card_and_confirm_login.assert_not_called()

    def test_resume_switches_if_last_completed_character_is_still_active(self):
        switcher = self.make_switcher(1)
        traversal = AccountTraversal(direction=-1, current_index=1, total_accounts=12,
                                     skipped_index=1, anchored_to_top=True)
        self.assertEqual(switcher.switch_to_next(traversal, resume_target=11,
                                                 completed_index=1), 'account 11')
        switcher._select_card_and_confirm_login.assert_called_once()

    def test_resume_does_not_switch_if_target_already_active(self):
        switcher = self.make_switcher(11)
        traversal = AccountTraversal(direction=-1, current_index=1, total_accounts=12,
                                     skipped_index=1, anchored_to_top=True)
        closer = MagicMock()
        closer._find_game_menu_close.return_value = (1, 1, 1, 1)
        with patch('rok_automation.automation.gmail_switcher.GmailSwitcher', return_value=closer):
            self.assertEqual(switcher.switch_to_next(traversal, resume_target=11,
                                                     completed_index=1), 'account 11')
        switcher._select_card_and_confirm_login.assert_not_called()
        closer._close_lilith_ui.assert_called_once()

    def test_resume_rejects_unexpected_active_account(self):
        switcher = self.make_switcher(3)
        traversal = AccountTraversal(direction=-1, current_index=1, total_accounts=12,
                                     skipped_index=1, anchored_to_top=True)
        with self.assertRaisesRegex(RuntimeError, 'does not match saved progress'):
            switcher.switch_to_next(traversal, resume_target=11, completed_index=1)

    def test_up_from_seventh_selects_sixth_without_scrolling_down(self):
        switcher = self.make_switcher(6)
        traversal = AccountTraversal(direction=-1, current_index=6, total_accounts=12,
                                     anchored_to_top=True)
        self.assertEqual(switcher.switch_to_next(traversal), 'account 5')
        switcher._scroll_all_the_way.assert_not_called()

    def test_down_from_sixth_scrolls_to_seventh(self):
        switcher = self.make_switcher(5)
        traversal = AccountTraversal(direction=1, current_index=5, total_accounts=12,
                                     anchored_to_top=True)
        self.assertEqual(switcher.switch_to_next(traversal), 'account 6')
        self.assertTrue(switcher._scroll_all_the_way.call_args.kwargs['toward_list_end'])

    def test_missing_check_still_scans_for_target_on_bottom_page(self):
        switcher = self.make_switcher(8)
        traversal = AccountTraversal(direction=-1, current_index=8, total_accounts=12,
                                     anchored_to_top=True)
        self.assertEqual(switcher.switch_to_next(traversal), 'account 7')
        self.assertTrue(switcher._scroll_all_the_way.call_args.kwargs['toward_list_end'])


if __name__ == '__main__':
    unittest.main()
