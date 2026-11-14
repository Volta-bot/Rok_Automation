import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.automation.context import FarmContext
from rok_automation.automation.gmail_switcher import GmailIdentity, GmailRotation, GMAIL_PROGRESS_UPDATED


class CharacterProgressTests(unittest.TestCase):
    def test_progress_round_trip_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            rotation = GmailRotation.load(path)
            first = GmailIdentity(email='a@gmail.com', uid='123')
            second = GmailIdentity(email='b@gmail.com')
            rotation.set_character_progress(first, 11, 12, -1)
            rotation.set_character_progress(second, 0, 12, 1)
            saved = GmailRotation.load(path)
            self.assertEqual(saved.get_character_progress(GmailIdentity(email='a@gmail.com'))['index'], 11)
            self.assertEqual(saved.get_character_progress(second)['direction'], 1)
            GmailRotation.reset_progress(path)
            self.assertIsNone(GmailRotation.load(path).get_character_progress(first))

    def test_resumes_pending_character_and_preserves_initial_skipped_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            identity = GmailIdentity(email='a@gmail.com')
            rotation = GmailRotation.load(path)
            rotation.set_character_progress(identity, 11, 12, -1)
            entry = rotation.get_character_progress(identity)
            entry.update(resume_ready=True, next_index=10, skipped_index=2)
            rotation.save()
            controller = FarmingController(MagicMock(), detector=MagicMock(), log=lambda _: None)
            controller._current_gmail_for_progress = identity
            controller._progress_rotation = rotation
            switcher = MagicMock()
            def switch(traversal, **kwargs):
                if 'resume_target' in kwargs:
                    self.assertEqual((kwargs['resume_target'], kwargs['completed_index']), (10, 11))
                    self.assertEqual(traversal.skipped_index, 2)
                    traversal.current_index = 10
                    return 'resumed'
                saved = GmailRotation.load(path).get_character_progress(identity)
                self.assertEqual((saved['index'], saved['next_index'], saved['skipped_index']), (10, 9, 2))
                return None
            switcher.switch_to_next.side_effect = switch
            with patch.object(controller, '_run_one_account', return_value=FarmContext(account_name='resumed')) as farm, \
                 patch('rok_automation.automation.controller.AccountSwitcher', return_value=switcher):
                controller._farm_linked_characters()
            farm.assert_called_once_with(expected_account_name='resumed')

    def test_initial_completion_saves_bottom_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            identity = GmailIdentity(email='a@gmail.com')
            controller = FarmingController(MagicMock(), detector=MagicMock(), log=lambda _: None)
            controller._current_gmail_for_progress = identity
            controller._progress_rotation = GmailRotation.load(path)
            switcher = MagicMock()
            def interrupted(traversal, *, on_initialized):
                traversal.current_index = traversal.skipped_index = 2
                traversal.direction = -1
                traversal.total_accounts = 12
                on_initialized(2, 12, -1)
                raise RuntimeError('stopped during switch')
            switcher.switch_to_next.side_effect = interrupted
            with patch.object(controller, '_run_one_account', return_value=FarmContext(account_name='first')), \
                 patch('rok_automation.automation.controller.AccountSwitcher', return_value=switcher), \
                 self.assertRaisesRegex(RuntimeError, 'stopped during switch'):
                controller._farm_linked_characters()
            saved = GmailRotation.load(path).get_character_progress(identity)
            self.assertEqual((saved['index'], saved['next_index'], saved['skipped_index']), (2, 11, 2))

    def test_completed_characters_are_saved_before_next_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            identity = GmailIdentity(email='a@gmail.com')
            controller = FarmingController(MagicMock(), detector=MagicMock(),
                gmail_state_path=path, signed_in_accounts_path=Path(tmp) / 'accounts.json',
                session_summary_path=Path(tmp) / 'summary.json', log=lambda _: None)
            controller._current_gmail_for_progress = identity
            controller._progress_rotation = GmailRotation.load(path)
            events = []
            controller._log = events.append
            switcher = MagicMock()
            def switch(traversal, *, on_initialized):
                on_initialized(11, 12, -1)
                traversal.direction = -1
                traversal.total_accounts = 12
                traversal.current_index = 10
                traversal.anchored_to_top = True
                self.assertEqual(GmailRotation.load(path).get_character_progress(identity)['index'], 11)
                return 'next'
            switcher.switch_to_next.side_effect = switch
            def farm(**kwargs):
                if kwargs.get('expected_account_name'):
                    # Stop after the second character has finished and its progress is saved.
                    return FarmContext(account_name='next')
                return FarmContext(account_name='first')
            with patch.object(controller, '_run_one_account', side_effect=farm), \
                    patch('rok_automation.automation.controller.AccountSwitcher', return_value=switcher):
                # The second switch sees the completed second character.
                def second_switch(traversal, *, on_initialized):
                    self.assertEqual(GmailRotation.load(path).get_character_progress(identity)['index'], 10)
                    return None
                switcher.switch_to_next.side_effect = lambda traversal, **kw: (switch if traversal.direction is None else second_switch)(traversal, **kw)
                controller._farm_linked_characters()
            self.assertEqual(events.count(GMAIL_PROGRESS_UPDATED), 2)
