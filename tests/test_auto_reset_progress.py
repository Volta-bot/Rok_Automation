import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation.automation.context import FarmContext
from rok_automation.automation.controller import FarmingController
from rok_automation.automation.gmail_switcher import GmailIdentity, GmailRotation, GmailSwitchResult


class AutoResetProgressTests(unittest.TestCase):
    def test_only_a_successful_full_rotation_resets_saved_progress(self):
        for outcome in ('complete', 'error', 'stop'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path = root / 'progress.json'
                first = GmailIdentity(email='first@example.com')
                current = GmailIdentity(email='current@example.com')
                rotation = GmailRotation(path=path)
                rotation.mark_farmed(first)
                rotation.set_character_progress(current, 0, 2, 1)
                logs = []
                controller = FarmingController(MagicMock(), detector=MagicMock(), log=logs.append,
                    gmail_state_path=path, signed_in_accounts_path=root / 'accounts.json',
                    session_summary_path=root / 'summary.json')
                switcher = MagicMock()
                switcher.switch_to_next.side_effect = [GmailSwitchResult(identity=current, run_complete=False),
                    GmailSwitchResult(identity=first, run_complete=True)]
                farm_result = (FarmContext(), 1, 0)
                if outcome == 'error':
                    farm_result = (FarmContext(last_error='capture failed'), 0, 0)
                with patch('rok_automation.automation.controller.GmailSwitcher', return_value=switcher), \
                     patch.object(controller, '_farm_linked_characters',
                                  side_effect=KeyboardInterrupt if outcome == 'stop' else None,
                                  return_value=farm_result):
                    controller.run()
                saved = GmailRotation.load(path, include_completed=True)
                if outcome == 'complete':
                    self.assertEqual(saved.farmed, [])
                    self.assertEqual(saved.character_progress, [])
                    remembered = saved.get_character_display_progress(current)
                    self.assertEqual((remembered['index'], remembered['total']), (0, 2))
                    self.assertEqual(saved.character_farmed_count(current), 1)
                    self.assertIsNone(saved.first)
                    self.assertFalse(saved.completed)
                    self.assertTrue(any('reset automatically' in line for line in logs))
                    self.assertEqual(json.loads((root / 'summary.json').read_text())['status'], 'complete')
                    self.assertTrue(controller.signed_in_accounts.load())
                else:
                    self.assertTrue(saved.is_farmed(first))
                    self.assertIsNotNone(saved.get_character_progress(current))
