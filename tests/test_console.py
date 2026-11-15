import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.game.console import format_log, split_log
from rok_automation.ui.app import ROKAutomationApp
from rok_automation.automation.gmail_switcher import GmailIdentity, GmailRotation, GmailSwitcher, _LilithScreen


class ConsoleTests(unittest.TestCase):
    def test_normalizes_legacy_prefixes_and_removes_duplicate_times(self):
        self.assertEqual(format_log("[21:34:48] [21:34:48] Clicking gather", "Troops"),
                         "[Troops] Clicking gather")
        self.assertEqual(format_log("[City Tasks] Collecting wood", "Farm"), "[City] Collecting wood")
        self.assertEqual(format_log("[ERROR] Missing frame"), "[Error] Missing frame")
        self.assertEqual(format_log("[Gmail] Signed in", "Farm"), "[Gmail] Signed in")
        self.assertEqual(format_log("[GMAIL_PROGRESS_UPDATED]"), "[GMAIL_PROGRESS_UPDATED]")
        self.assertEqual(split_log("[AC:U] Vàng8", "Account"), ("Account", "[AC:U] Vàng8"))

    def test_gui_renders_one_timestamp_and_a_separate_colored_tag(self):
        app = ROKAutomationApp.__new__(ROKAutomationApp)
        app.txt_console = MagicMock()
        app.opt_autoscroll = MagicMock()
        app.opt_autoscroll.get.return_value = False
        app.log("[21:34:48] [21:34:48] [Troops] Clicking march_button")
        inserts = app.txt_console.insert.call_args_list
        self.assertEqual(len(inserts), 3)
        self.assertEqual(inserts[0].args[2], "timestamp")
        self.assertEqual(inserts[1].args[1:], ("[Troops] ", "label_Troops"))
        self.assertEqual(inserts[2].args[1:], ("Clicking march_button\n", "normal"))

    def test_observe_logs_signed_in_identity_before_closing_account_menu(self):
        for state in ("user_center", "switch_popup", "dropdown_open"):
            with self.subTest(state=state):
                events = []
                identity = GmailIdentity(email="farm@example.com", uid="123")
                switcher = GmailSwitcher(MagicMock(), MagicMock(), log=events.append)
                screen = _LilithScreen(state=state, current=identity)
                center = _LilithScreen(state="user_center", current=identity)
                with patch.object(switcher, "_capture", return_value=(Path("frame"), np.zeros((1, 1, 3)))), \
                     patch.object(switcher, "_read_lilith_screen", return_value=screen), \
                     patch.object(switcher, "_read_user_center", return_value=center), \
                     patch.object(switcher, "_register_current") as register, \
                     patch.object(switcher, "_close_lilith_ui", side_effect=lambda *a: events.append("closed")):
                    result = switcher.switch_to_next(GmailRotation(), observe_only=True)
                self.assertEqual(result.identity, identity)
                self.assertEqual(events, ["[Gmail] Signed in as farm@example.com (UID 123)", "closed"])
                register.assert_not_called()
