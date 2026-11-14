import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.automation.context import FarmContext
from rok_automation.automation.gmail_switcher import GmailIdentity, GmailRotation
from rok_automation.game.skip import consume_skip, request_skip


class SkipTests(unittest.TestCase):
    def test_request_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'request'
            request_skip('gmail', path)
            self.assertEqual(consume_skip(path), 'gmail')
            self.assertIsNone(consume_skip(path))

    def test_skip_rok_uses_normal_switch_and_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            identity = GmailIdentity(email='a@example.com')
            controller = FarmingController(MagicMock(), detector=MagicMock(), log=lambda _: None)
            controller._current_gmail_for_progress = identity
            controller._progress_rotation = GmailRotation.load(path)
            switcher = MagicMock()
            def switch(traversal, *, on_initialized):
                on_initialized(0, 2, -1)
                self.assertEqual(GmailRotation.load(path).get_character_progress(identity)['index'], 0)
                return None
            switcher.switch_to_next.side_effect = switch
            with patch('rok_automation.game.skip.consume_skip', return_value='rok'), \
                 patch.object(controller, '_run_one_account') as farm, \
                 patch('rok_automation.automation.controller.AccountSwitcher', return_value=switcher):
                _, count, _ = controller._farm_linked_characters()
            self.assertEqual(count, 1)
            farm.assert_not_called()
            switcher.switch_to_next.assert_called_once()

    def test_skip_gmail_stops_remaining_characters(self):
        controller = FarmingController(MagicMock(), detector=MagicMock(), log=lambda _: None)
        with patch('rok_automation.game.skip.consume_skip', return_value='gmail'), \
             patch.object(controller, '_run_one_account') as farm, \
             patch('rok_automation.automation.controller.AccountSwitcher') as switcher:
            context, count, _ = controller._farm_linked_characters()
        self.assertEqual(count, 0)
        self.assertIsNone(context.last_error)
        farm.assert_not_called()
        switcher.assert_not_called()

    def test_standalone_gmail_skip_marks_current_before_switch(self):
        from rok_automation.automation.skip_test import run_skip_test
        from rok_automation.automation.gmail_switcher import GmailSwitchResult
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            current = GmailIdentity(email='a@example.com')
            next_gmail = GmailIdentity(email='b@example.com')
            switcher = MagicMock()
            def switch(rotation, *, observe_only=False):
                if observe_only:
                    return GmailSwitchResult(identity=current, run_complete=False)
                self.assertTrue(rotation.is_farmed(current))
                return GmailSwitchResult(identity=next_gmail, run_complete=False)
            switcher.switch_to_next.side_effect = switch
            def register(rotation, identity):
                rotation.mark_farmed(identity)
            switcher._register_current.side_effect = register
            with patch('rok_automation.automation.skip_test.GmailSwitcher', return_value=switcher):
                run_skip_test(MagicMock(), 'gmail', path)
            self.assertTrue(GmailRotation.load(path).is_farmed(current))

    def test_standalone_rok_skip_remembers_current_gmail(self):
        from rok_automation.automation.skip_test import run_skip_test
        from rok_automation.automation.gmail_switcher import GmailSwitchResult
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            current = GmailIdentity(email='a@example.com')
            gmail_switcher = MagicMock()
            gmail_switcher.switch_to_next.return_value = GmailSwitchResult(
                identity=current, run_complete=False)
            rok_switcher = MagicMock()
            def select(traversal, *, on_initialized):
                if traversal.direction is None:
                    traversal.direction = -1
                    traversal.current_index = 2
                    traversal.total_accounts = 3
                    traversal.skipped_index = 0
                    on_initialized(0, 3, -1)
                else:
                    self.assertEqual(traversal.current_index, 2)
                    traversal.current_index = 1
                return 'next'
            rok_switcher.switch_to_next.side_effect = select
            with patch('rok_automation.automation.skip_test.GmailSwitcher', return_value=gmail_switcher), \
                 patch('rok_automation.automation.skip_test.AccountSwitcher', return_value=rok_switcher):
                run_skip_test(MagicMock(), 'rok', path)
                run_skip_test(MagicMock(), 'rok', path)
            gmail_switcher.switch_to_next.assert_called_once()
            self.assertTrue(GmailRotation.load(path).current.matches(current))
            self.assertEqual(GmailRotation.load(path).get_character_progress(current)['index'], 2)

    def test_gmail_skip_remembers_newly_selected_account(self):
        from rok_automation.automation.skip_test import run_skip_test
        from rok_automation.automation.gmail_switcher import GmailSwitchResult
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            first = GmailIdentity(email='a@example.com')
            second = GmailIdentity(email='b@example.com')
            switcher = MagicMock()
            switcher.switch_to_next.side_effect = [
                GmailSwitchResult(identity=first, run_complete=False),
                GmailSwitchResult(identity=second, run_complete=False),
            ]
            with patch('rok_automation.automation.skip_test.GmailSwitcher', return_value=switcher):
                run_skip_test(MagicMock(), 'gmail', path)
            self.assertTrue(GmailRotation.load(path).current.matches(second))

    def test_unavailable_skip_is_ignored(self):
        controller = FarmingController(MagicMock(), detector=MagicMock(),
                                       switch_gmail=False, log=lambda _: None)
        with patch('rok_automation.game.skip.consume_skip', return_value='gmail'):
            controller._check_skip_request()


if __name__ == '__main__':
    unittest.main()
