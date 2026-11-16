import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.automation.context import FarmContext
from rok_automation.automation.gmail_switcher import GmailIdentity, GmailRotation, GMAIL_PROGRESS_UPDATED


class CharacterProgressTests(unittest.TestCase):
    def test_completed_resume_goes_back_to_gmail_rotation_without_farming_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = GmailIdentity(email='a@gmail.com')
            rotation = GmailRotation.load(Path(tmp) / 'progress.json')
            rotation.set_character_progress(identity, 0, 12, 1)
            rotation.get_character_progress(identity).update(resume_ready=True, next_index=1)
            rotation.save()
            controller = FarmingController(MagicMock(), detector=MagicMock(), log=lambda _: None)
            controller._current_gmail_for_progress = identity
            controller._progress_rotation = rotation
            switcher = MagicMock()
            switcher.switch_to_next.return_value = None
            with patch('rok_automation.automation.controller.AccountSwitcher', return_value=switcher), \
                 patch.object(controller, '_run_one_account') as farm, \
                 patch.object(controller, '_open_world_map_directly') as open_map:
                context, accounts, dispatched = controller._farm_linked_characters()
            farm.assert_not_called()
            open_map.assert_not_called()
            self.assertIsNone(context.last_error)
            self.assertEqual((accounts, dispatched), (0, 0))

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
            remembered = GmailRotation.load(path).get_character_display_progress(first)
            self.assertEqual((remembered['index'], remembered['total'], remembered['direction']), (11, 12, -1))

    def test_completed_run_history_survives_new_run_and_multiple_resets(self):
        identity = GmailIdentity(email='a@gmail.com', uid='123')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            rotation = GmailRotation(path=path)
            rotation.set_character_progress(identity, 11, 12, 1)
            rotation.get_character_progress(identity).update(farmed_count=12, resume_ready=True, next_index=None)
            rotation.completed = True
            rotation.save()
            fresh = GmailRotation.load(path)
            self.assertIsNone(fresh.get_character_progress(identity))
            self.assertEqual(fresh.character_farmed_count(identity), 12)
            fresh.save()
            for _ in range(2):
                GmailRotation.reset_progress(path)
            fresh = GmailRotation.load(path)
            remembered = fresh.get_character_display_progress(GmailIdentity(uid='123'))
            self.assertEqual((remembered['index'], remembered['total']), (11, 12))
            # A new run's count replaces the remembered count instead of adding to it.
            fresh.set_character_progress(identity, 11, 12, -1)
            fresh.get_character_progress(identity)['farmed_count'] = 1
            fresh.save()
            self.assertEqual(GmailRotation.load(path).character_farmed_count(identity), 1)

    def test_legacy_progress_is_archived_when_reset_without_history_field(self):
        import json

        identity = GmailIdentity(email='a@gmail.com')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            path.write_text(json.dumps({'character_progress': [
                {'identity': identity.to_dict(), 'index': 0, 'total': 12, 'direction': -1}
            ]}), encoding='utf-8')
            GmailRotation.reset_progress(path)
            rotation = GmailRotation.load(path)
            self.assertIsNone(rotation.get_character_progress(identity))
            self.assertEqual(rotation.character_farmed_count(identity), 12)

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

    def test_initial_completion_saves_closest_endpoint_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.json'
            identity = GmailIdentity(email='a@gmail.com')
            controller = FarmingController(MagicMock(), detector=MagicMock(), log=lambda _: None)
            controller._current_gmail_for_progress = identity
            controller._progress_rotation = GmailRotation.load(path)
            switcher = MagicMock()
            def interrupted(traversal, *, on_initialized):
                traversal.current_index = traversal.skipped_index = 2
                traversal.direction = 1
                traversal.total_accounts = 12
                on_initialized(2, 12, 1)
                raise RuntimeError('stopped during switch')
            switcher.switch_to_next.side_effect = interrupted
            with patch.object(controller, '_run_one_account', return_value=FarmContext(account_name='first')), \
                 patch('rok_automation.automation.controller.AccountSwitcher', return_value=switcher), \
                 self.assertRaisesRegex(RuntimeError, 'stopped during switch'):
                controller._farm_linked_characters()
            saved = GmailRotation.load(path).get_character_progress(identity)
            self.assertEqual((saved['index'], saved['next_index'], saved['skipped_index']), (2, 0, 2))
            self.assertEqual(saved['direction'], 1)
            self.assertEqual(saved['farmed_count'], 1)

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
                self.assertEqual(GmailRotation.load(path).character_farmed_count(identity), 1)
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
                    self.assertEqual(GmailRotation.load(path).character_farmed_count(identity), 2)
                    return None
                switcher.switch_to_next.side_effect = lambda traversal, **kw: (switch if traversal.direction is None else second_switch)(traversal, **kw)
                controller._farm_linked_characters()
            self.assertEqual(events.count(GMAIL_PROGRESS_UPDATED), 3)

    def test_legacy_counts_include_initial_character_outside_traversed_range(self):
        identity = GmailIdentity(email='a@gmail.com')
        for index, direction, skipped, expected in [
            (0, 1, None, 1), (11, -1, None, 1),
            (0, 1, 2, 2), (11, -1, 2, 2),
            (2, 1, 2, 1), (11, 1, 2, 12), (0, -1, 2, 12),
        ]:
            with self.subTest(index=index, direction=direction, skipped=skipped):
                rotation = GmailRotation()
                rotation.set_character_progress(identity, index, 12, direction)
                rotation.get_character_progress(identity)['skipped_index'] = skipped
                self.assertEqual(rotation.character_farmed_count(identity), expected)
