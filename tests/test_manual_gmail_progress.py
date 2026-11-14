"""Manual Gmail marks must affect both the UI and farming decisions."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.automation.context import FarmContext
from rok_automation.automation.gmail_switcher import (
    GmailIdentity, GmailRotation, GmailSwitchResult, GmailSwitcher, _DropdownEntry,
)
from rok_automation.ui.app import ROKAutomationApp


class TestManualProgress(unittest.TestCase):
    def test_ui_toggle_persists_and_refreshes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = ROKAutomationApp.__new__(ROKAutomationApp)
            app._is_running = False
            app.gmail_state_path = Path(tmp) / "progress.json"
            app.refresh_signed_in_accounts = MagicMock()
            app.log_info = MagicMock()
            identity = GmailIdentity(email="marked@gmail.com")
            app._toggle_gmail_progress(identity)
            self.assertTrue(GmailRotation.load(app.gmail_state_path).is_farmed(identity))
            app._toggle_gmail_progress(identity)
            self.assertFalse(GmailRotation.load(app.gmail_state_path).is_farmed(identity))
            self.assertEqual(app.refresh_signed_in_accounts.call_count, 2)
            app._is_running = True
            app._toggle_gmail_progress(identity)
            self.assertFalse(GmailRotation.load(app.gmail_state_path).is_farmed(identity))

    def test_picker_selects_bottom_even_when_manually_marked(self):
        marked = GmailIdentity(email="marked@gmail.com")
        rotation = GmailRotation(farmed=[marked])
        switcher = GmailSwitcher(MagicMock(), MagicMock(), log=lambda _: None)
        entries = [
            _DropdownEntry("pending@gmail.com", None, (0, 100, 100, 20)),
            _DropdownEntry("marked@gmail.com", None, (0, 200, 100, 20)),
        ]
        chosen, _ = switcher._choose_target(rotation, None, entries)
        self.assertEqual(chosen.email, "marked@gmail.com")

    def test_current_marked_account_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "progress.json"
            identity = GmailIdentity(email="marked@gmail.com")
            GmailRotation(path=path).set_manual_farmed(identity, True)
            controller = FarmingController(MagicMock(), detector=MagicMock(),
                gmail_state_path=path, signed_in_accounts_path=Path(tmp) / "accounts.json",
                session_summary_path=Path(tmp) / "summary.json", log=lambda _: None)
            switcher = MagicMock()
            switcher.switch_to_next.side_effect = [
                GmailSwitchResult(identity=identity, run_complete=False),
                GmailSwitchResult(identity=identity, run_complete=True),
            ]
            with patch("rok_automation.automation.controller.GmailSwitcher", return_value=switcher), \
                    patch.object(controller, "_farm_linked_characters") as farm:
                controller.run()
            farm.assert_not_called()
            self.assertTrue(switcher.switch_to_next.call_args_list[0].kwargs["observe_only"])

    def test_switches_through_marked_account_before_farming_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "progress.json"
            first = GmailIdentity(email="first@gmail.com")
            marked = GmailIdentity(email="marked@gmail.com")
            pending = GmailIdentity(email="pending@gmail.com")
            GmailRotation(first=first, farmed=[marked], path=path).save()
            controller = FarmingController(
                MagicMock(), detector=MagicMock(), gmail_state_path=path,
                signed_in_accounts_path=Path(tmp) / "accounts.json",
                session_summary_path=Path(tmp) / "summary.json", log=lambda _: None,
            )
            switcher = MagicMock()
            switcher.switch_to_next.side_effect = [
                GmailSwitchResult(identity=first, run_complete=False),
                GmailSwitchResult(identity=marked, run_complete=False),
                GmailSwitchResult(identity=pending, run_complete=False),
                GmailSwitchResult(identity=first, run_complete=True),
            ]
            with patch("rok_automation.automation.controller.GmailSwitcher", return_value=switcher), \
                    patch.object(controller, "_farm_linked_characters", return_value=(FarmContext(), 1, 0)) as farm:
                controller.run()
            self.assertEqual(farm.call_count, 2)
            self.assertEqual(switcher.switch_to_next.call_count, 4)
