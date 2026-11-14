"""Unit tests for the ROKauto Graphical User Interface."""

import queue
import subprocess
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation.game.pause import clear_paused, set_paused
from rok_automation.game.display import display_scale
from rok_automation.ui.app import ModernColors, ROKAutomationApp
from rok_automation.automation.gmail_switcher import GMAIL_PROGRESS_UPDATED


class TestLiveGmailProgress(unittest.TestCase):
    def test_progress_notification_refreshes_before_process_exit(self):
        app = ROKAutomationApp.__new__(ROKAutomationApp)
        app.root = MagicMock()
        app._output_queue = queue.Queue()
        app.refresh_signed_in_accounts = MagicMock()
        app.log = MagicMock()
        app._output_queue.put(f"[12:00:00] {GMAIL_PROGRESS_UPDATED}")

        app._poll_output_queue()

        app.refresh_signed_in_accounts.assert_called_once_with()
        app.log.assert_not_called()


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
        # The daily-reward flag must never be written to the real state
        # file by a test.
        self.flag_path = Path(tempfile.gettempdir()) / "rokauto_test_daily.flag"
        try:
            self.flag_path.unlink()
        except FileNotFoundError:
            pass
        self.app = ROKAutomationApp(
            self.root,
            session_summary_path=None,
            display_scale=display_scale(),
            daily_reward_flag_path=self.flag_path,
        )

    def tearDown(self) -> None:
        if self.app._is_running:
            self.app._set_running_state(False)

    def test_initial_state_defaults(self) -> None:
        """Verify default option toggles and idle status."""
        self.assertTrue(self.app.opt_farm.get())
        self.assertTrue(self.app.opt_switch_gmail.get())

        # Planned options should default to False
        self.assertFalse(self.app.opt_daily_reward.get())
        self.assertFalse(self.app.opt_alliance.get())
        self.assertFalse(self.app.opt_train_troops.get())

        self.assertFalse(self.app._is_running)
        self.assertEqual(self.app.btn_start.cget("state"), tk.NORMAL)
        self.assertEqual(self.app.btn_stop.cget("state"), tk.DISABLED)

    def test_default_window_fits_the_content(self) -> None:
        """The default window must be wide enough to show every control.

        The option cards sit side by side and need about 1160px, so a
        narrower window cuts off the right-hand card.
        """
        self.app.root.update_idletasks()
        geometry = self.app.root.geometry()
        width = int(geometry.split("x")[0])
        self.assertGreaterEqual(
            width,
            self.app.root.winfo_reqwidth(),
            f"default window is {width}px but the content needs "
            f"{self.app.root.winfo_reqwidth()}px",
        )

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

    def test_the_daily_reward_toggle_never_touches_the_real_flag(self) -> None:
        """A test toggling the option must not enable it for real runs."""

        from rok_automation.game.flags import DEFAULT_DAILY_REWARD_FLAG_PATH

        self.app.opt_daily_reward.set(True)
        self.app._on_planned_option_toggled("Collect Daily Rewards", self.app.opt_daily_reward)
        self.assertFalse(DEFAULT_DAILY_REWARD_FLAG_PATH.is_file())
        self.assertTrue(self.flag_path.is_file())

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

    def test_get_python_cli_executable(self) -> None:
        """Verify _get_python_cli_executable returns python.exe."""
        from rok_automation.ui.app import _get_python_cli_executable
        exe = _get_python_cli_executable()
        self.assertTrue(exe.lower().endswith("python.exe") or "python" in exe.lower())

    def test_hide_console_window_does_not_crash(self) -> None:
        """Verify hide_console_window runs safely without exception."""
        from rok_automation.ui.app import hide_console_window
        hide_console_window()  # Should not raise

    def test_check_stockpiles_button_launches_the_cli(self) -> None:
        """The stockpile button runs the --check-stockpiles CLI diagnostic."""
        self.app.clear_console()
        with patch.object(self.app, "_start_worker") as mock_start:
            self.app.run_check_stockpiles()

        mock_start.assert_called_once()
        cmd = mock_start.call_args[0][0]
        self.assertIn("--check-stockpiles", cmd)
        self.assertIn("--emulator", cmd)
        content = self.app.txt_console.get("1.0", tk.END)
        self.assertIn("top-bar stockpiles", content)

    def test_check_stockpiles_is_blocked_while_running(self) -> None:
        """The stockpile diagnostic must not run during automation."""
        self.app._set_running_state(True)
        with patch.object(self.app, "_start_worker") as mock_start:
            self.app.run_check_stockpiles()
        mock_start.assert_not_called()


class TestPauseButton(unittest.TestCase):
    """Verify the pause button toggles between PAUSE and PLAY."""

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
        # The daily-reward flag must never be written to the real state
        # file by a test.
        self.flag_path = Path(tempfile.gettempdir()) / "rokauto_test_daily.flag"
        try:
            self.flag_path.unlink()
        except FileNotFoundError:
            pass
        self.app = ROKAutomationApp(
            self.root,
            session_summary_path=None,
            display_scale=display_scale(),
            daily_reward_flag_path=self.flag_path,
        )
        self.flag = Path("state/_test_pause.flag")
        clear_paused(self.flag)

    def tearDown(self) -> None:
        clear_paused(self.flag)
        if self.app._is_running:
            self.app._set_running_state(False)

    def test_pause_button_disabled_while_idle(self) -> None:
        self.assertEqual(self.app.btn_pause.cget("state"), tk.DISABLED)
        self.assertIn("PAUSE", self.app.btn_pause.cget("text"))

    def test_action_buttons_are_evenly_sized_and_spaced(self) -> None:
        """START keeps the most room; PAUSE and STOP share the rest evenly.

        They used to be packed by hand, which left START absorbing all
        the spare width and the gaps between the buttons unequal. PAUSE
        and STOP are narrower on purpose: START is the button people
        look for, the other two are secondary controls.
        """
        self.app.root.update_idletasks()
        buttons = [self.app.btn_pause, self.app.btn_stop]
        widths = {w.winfo_width() for w in buttons}
        self.assertEqual(
            len(widths),
            1,
            f"pause/stop buttons have different widths: "
            f"{[w.winfo_width() for w in buttons]}",
        )
        # PAUSE and STOP are the secondary controls: a smaller font and
        # tighter padding than START keeps them visibly narrower.
        import tkinter.font as tkfont

        for button in buttons:
            font = tkfont.Font(font=button.cget("font"))
            self.assertEqual(
                (font.actual("family"), font.actual("size"), font.actual("weight")),
                ("Segoe UI", 10, "bold"),
            )
            self.assertEqual(int(button.cget("padx")), 10)
        font = tkfont.Font(font=self.app.btn_start.cget("font"))
        self.assertEqual(
            (font.actual("family"), font.actual("size"), font.actual("weight")),
            ("Segoe UI", 11, "bold"),
        )
        self.assertEqual(int(self.app.btn_start.cget("padx")), 16)

        # The order must read START, PAUSE, STOP from left to right.
        columns = [int(w.grid_info()["column"]) for w in buttons]
        columns.insert(0, int(self.app.btn_start.grid_info()["column"]))
        self.assertEqual(columns, [0, 1, 2])

        # START takes twice the room of the two equal secondary columns.
        start = self.app.btn_box.grid_columnconfigure(0)
        self.assertEqual(start["weight"], 2)
        for column in (1, 2):
            settings = self.app.btn_box.grid_columnconfigure(column)
            self.assertEqual(settings["weight"], 1)
            self.assertEqual(settings["uniform"], "action_button")

    def test_pause_button_enabled_while_running(self) -> None:
        self.app._set_running_state(True)
        self.assertEqual(self.app.btn_pause.cget("state"), tk.NORMAL)

    def test_toggle_pause_switches_button_to_play(self) -> None:
        self.app._set_running_state(True)
        with patch("rok_automation.ui.app.set_paused") as mock_pause:
            self.app.toggle_pause()
            mock_pause.assert_called_once()

        self.assertTrue(self.app._is_paused)
        self.assertIn("PLAY", self.app.btn_pause.cget("text"))
        self.assertIn("Paused", self.app.lbl_run_state.cget("text"))

    def test_toggle_pause_again_resumes(self) -> None:
        self.app._set_running_state(True)
        with patch("rok_automation.ui.app.set_paused"), \
             patch("rok_automation.ui.app.clear_paused") as mock_clear:
            self.app.toggle_pause()
            self.app.toggle_pause()
            mock_clear.assert_called_once()

        self.assertFalse(self.app._is_paused)
        self.assertIn("PAUSE", self.app.btn_pause.cget("text"))
        self.assertIn("Running", self.app.lbl_run_state.cget("text"))

    def test_pause_does_nothing_when_not_running(self) -> None:
        self.app.toggle_pause()
        self.assertFalse(self.app._is_paused)

    def test_stopping_a_paused_run_clears_the_flag(self) -> None:
        self.app._set_running_state(True)
        set_paused(self.flag)
        self.app._is_paused = True
        with patch("rok_automation.ui.app.clear_paused") as mock_clear:
            self.app.stop_automation()
            mock_clear.assert_called()
        self.assertFalse(self.app._is_paused)


class TestStopReliability(unittest.TestCase):
    """Verify STOP always terminates the worker, even on the first press."""

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
        # The daily-reward flag must never be written to the real state
        # file by a test.
        self.flag_path = Path(tempfile.gettempdir()) / "rokauto_test_daily.flag"
        try:
            self.flag_path.unlink()
        except FileNotFoundError:
            pass
        self.app = ROKAutomationApp(
            self.root,
            session_summary_path=None,
            display_scale=display_scale(),
            daily_reward_flag_path=self.flag_path,
        )

    def test_process_handle_is_available_before_the_worker_thread_starts(self) -> None:
        """The handle must exist as soon as START returns.

        Creating the process inside the reader thread left STOP with no
        handle to kill when the user clicked straight after START.
        """
        spawned = MagicMock()
        spawned.stdout = None
        spawned.poll.return_value = None
        with patch.object(self.app, "_spawn_process", return_value=spawned) as mock_spawn:
            with patch("rok_automation.ui.app.threading.Thread") as mock_thread:
                mock_thread.return_value.start = MagicMock()
                self.app._start_worker(["python", "-c", "pass"])

            mock_spawn.assert_called_once()
            self.assertIs(self.app._current_process, spawned)

    def test_terminate_confirms_the_process_exited(self) -> None:
        """STOP must wait for the exit instead of assuming it happened."""
        proc = MagicMock()
        proc.poll.return_value = None
        # The first wait times out, so the direct-kill fallback is used.
        proc.wait.side_effect = [subprocess.TimeoutExpired("cmd", 5), None]

        self.app._terminate_process(proc)

        proc.kill.assert_called_once()

    def test_terminate_returns_early_for_a_finished_process(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = 0
        self.app._terminate_process(proc)
        proc.kill.assert_not_called()

    def test_terminate_tolerates_a_missing_process(self) -> None:
        self.app._terminate_process(None)


if __name__ == "__main__":
    unittest.main()
