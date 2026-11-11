"""Unit tests for the ROKauto Graphical User Interface."""

import queue
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from rok_automation.ui.app import ModernColors, ROKAutomationApp


class TestROKAutomationApp(unittest.TestCase):
    """Test suite for the ROKAutomationApp UI."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError:
            raise unittest.SkipTest("Tkinter display not available in this environment")

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "root") and cls.root:
            cls.root.destroy()

    def setUp(self) -> None:
        self.app = ROKAutomationApp(self.root)

    def tearDown(self) -> None:
        if self.app._is_running:
            self.app._set_running_state(False)

    def test_initial_state_defaults(self) -> None:
        """Verify default option toggles and idle status."""
        self.assertTrue(self.app.opt_farm.get())
        self.assertTrue(self.app.opt_switch_gmail.get())
        self.assertFalse(self.app.opt_reset_gmail.get())

        # Planned options should default to False
        self.assertFalse(self.app.opt_daily_reward.get())
        self.assertFalse(self.app.opt_alliance.get())
        self.assertFalse(self.app.opt_train_troops.get())

        self.assertFalse(self.app._is_running)
        self.assertEqual(self.app.btn_start.cget("state"), tk.NORMAL)
        self.assertEqual(self.app.btn_stop.cget("state"), tk.DISABLED)

    def test_running_state_toggle(self) -> None:
        """Verify widget states switch correctly between idle and running."""
        self.app._set_running_state(True)
        self.assertTrue(self.app._is_running)
        self.assertEqual(self.app.btn_start.cget("state"), tk.DISABLED)
        self.assertEqual(self.app.btn_stop.cget("state"), tk.NORMAL)

        self.app._set_running_state(False)
        self.assertFalse(self.app._is_running)
        self.assertEqual(self.app.btn_start.cget("state"), tk.NORMAL)
        self.assertEqual(self.app.btn_stop.cget("state"), tk.DISABLED)

    def test_console_logging(self) -> None:
        """Verify logging writes text into the console widget."""
        self.app.clear_console()
        self.app.log_info("Testing info log message")
        self.app.log_success("Testing success log message")
        self.app.log_warning("Testing warning log message")
        self.app.log_error("Testing error log message")

        content = self.app.txt_console.get("1.0", tk.END)
        self.assertIn("Testing info log message", content)
        self.assertIn("Testing success log message", content)
        self.assertIn("Testing warning log message", content)
        self.assertIn("Testing error log message", content)

    def test_planned_options_toggle_and_triggers(self) -> None:
        """Verify toggling planned options produces informative console notices."""
        self.app.clear_console()
        self.app.opt_daily_reward.set(True)
        self.app._on_planned_option_toggled("Collect Daily Rewards", self.app.opt_daily_reward)

        content = self.app.txt_console.get("1.0", tk.END)
        self.assertIn("Collect Daily Rewards set to ENABLED", content)

        # Trigger planned action test button
        self.app.trigger_planned_feature_test("Collect Daily Rewards (incl. Daily VIP Chest)")
        content2 = self.app.txt_console.get("1.0", tk.END)
        self.assertIn("Collect Daily Rewards", content2)
        self.assertIn("planned", content2.lower())

    def test_copy_console_log(self) -> None:
        """Verify copying console log content to clipboard."""
        self.app.clear_console()
        self.app.log_info("Sample copy text")
        self.app.copy_console_log()
        clipboard_content = self.root.clipboard_get()
        self.assertIn("Sample copy text", clipboard_content)

    def test_queue_polling_drains_output(self) -> None:
        """Verify output queue messages are safely consumed and displayed."""
        self.app.clear_console()
        self.app._output_queue.put("Subprocess output line 1")
        self.app._output_queue.put("Subprocess output line 2")
        self.app._output_queue.put(None)  # Sentinel

        self.app._poll_output_queue()
        content = self.app.txt_console.get("1.0", tk.END)
        self.assertIn("Subprocess output line 1", content)
        self.assertIn("Subprocess output line 2", content)
        self.assertFalse(self.app._is_running)

    def test_force_stop_summary_with_farmed_resources(self) -> None:
        """Verify force stop displays the detailed resource summary table."""
        from rok_automation.logic.resource_selector import Resource, SearchTarget

        self.app.clear_console()
        self.app._session_targets = [
            SearchTarget(resource=Resource.FOOD, level=6),
            SearchTarget(resource=Resource.GOLD, level=8),
        ]
        self.app._display_farmed_resources_summary(stopped_early=True)

        content = self.app.txt_console.get("1.0", tk.END)
        self.assertIn("TOTAL RESOURCES FARMED BEFORE FORCE STOP", content)
        self.assertIn("Food (Ngô)", content)
        self.assertIn("Gold (Vàng)", content)
        self.assertIn("GRAND TOTAL", content)
        self.assertIn("Force stopped by user after 2 armies", content)

    def test_force_stop_summary_with_zero_armies(self) -> None:
        """Verify force stop with zero armies logs clear informative message."""
        self.app.clear_console()
        self.app._session_targets.clear()
        self.app._display_farmed_resources_summary(stopped_early=True)

        content = self.app.txt_console.get("1.0", tk.END)
        self.assertIn("No resource nodes were gathered", content)

    def test_queue_parses_dispatched_armies(self) -> None:
        """Verify output queue lines update session targets and farmed badge."""
        self.app.clear_console()
        self.app._session_targets.clear()
        self.app._output_queue.put(
            "[19:50:12] Army 1/5 dispatched to Food Lv6 (+1,260,000 resources)"
        )
        self.app._output_queue.put(
            "[19:50:30] Army 2/5 dispatched to Gold Lv8 (+525,000 resources)"
        )

        self.app._poll_output_queue()
        self.assertEqual(len(self.app._session_targets), 2)
        badge_text = self.app.lbl_session_farmed.cget("text")
        self.assertIn("Farmed:", badge_text)
        self.assertIn("2 armies", badge_text)


if __name__ == "__main__":
    unittest.main()
