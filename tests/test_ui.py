"""Unit tests for the ROKauto Graphical User Interface."""

import json
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
    def test_account_rows_show_current_position_direction_and_completed_count(self):
        from rok_automation.automation.gmail_switcher import GmailAccountRegistry, GmailIdentity, GmailRotation

        with tempfile.TemporaryDirectory() as folder:
            app = ROKAutomationApp.__new__(ROKAutomationApp)
            app._instance_index = 0
            app._is_running = True
            app.signed_in_accounts_rows = MagicMock()
            app.signed_in_accounts_rows.winfo_children.return_value = []
            app._resize_signed_in_accounts_scroll_region = MagicMock()
            app.signed_in_accounts = GmailAccountRegistry(Path(folder) / 'accounts.json')
            app.signed_in_accounts.add_manual('sduy@gmail.com')
            app.gmail_state_path = Path(folder) / 'progress.json'
            identity = GmailIdentity(email='sduy@gmail.com')
            rotation = GmailRotation.load(app.gmail_state_path)
            for index, direction, count, current, expected in [
                (0, 1, 1, 0, 'position 1↓ - farmed 1/12'),
                (11, -1, 1, 10, 'position 11↑ - farmed 1/12'),
                (10, -1, 2, 10, 'position 11↑ - farmed 2/12'),
            ]:
                rotation.set_character_progress(identity, index, 12, direction)
                rotation.get_character_progress(identity).update(farmed_count=count, current_index=current)
                rotation.save()
                with patch('rok_automation.ui.app.tk.Frame'), patch('rok_automation.ui.app.tk.Button'), \
                     patch('rok_automation.ui.app.tk.Label') as label:
                    app.refresh_signed_in_accounts()
                texts = [call.kwargs.get('text', '') for call in label.call_args_list]
                self.assertIn(f'1. sduy@gmail.com - {expected}', texts)
                GmailRotation.reset_progress(app.gmail_state_path)
                with patch('rok_automation.ui.app.tk.Frame'), patch('rok_automation.ui.app.tk.Button'), \
                     patch('rok_automation.ui.app.tk.Label') as label:
                    app.refresh_signed_in_accounts()
                texts = [call.kwargs.get('text', '') for call in label.call_args_list]
                reset_expected = expected.replace(f'farmed {count}/12', 'farmed 0/12').replace('↑', '').replace('↓', '')
                self.assertIn(f'1. sduy@gmail.com - {reset_expected}', texts)

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


class TestForgePreference(unittest.TestCase):
    def test_selection_persists_even_when_forging_is_disabled(self):
        from rok_automation.ui.app import _load_forge_preference

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "forge_preference.txt"
            app = ROKAutomationApp.__new__(ROKAutomationApp)
            app.opt_forge_material = MagicMock()
            app.opt_forge_material.get.return_value = "Iron"
            app.opt_auto_forge = MagicMock()
            app.opt_auto_forge.get.return_value = False
            app._instance_index = 0
            with patch("rok_automation.ui.app.FORGE_PREFERENCE_PATH", path):
                app._on_forge_material_changed()
                self.assertEqual(_load_forge_preference(), "Iron")
                path.write_text("invalid", encoding="utf-8")
                self.assertEqual(_load_forge_preference(), "Leather")


class TestConnectionRetry(unittest.TestCase):
    def setUp(self):
        self.app = ROKAutomationApp.__new__(ROKAutomationApp)
        self.app.root = MagicMock()
        self.app.root.after.return_value = "retry-1"
        self.app.emulator_status_badge = MagicMock()
        self.app._instance_index = 1
        self.app._connection_retry_id = None
        self.app.selected_emulator = MagicMock(return_value="mumu")

    def test_retries_once_every_five_seconds_until_connected(self):
        from rok_automation.game.emulators import EmulatorInstance

        family = MagicMock()
        family.key = "mumu"
        family.discover.return_value = []
        with patch("rok_automation.ui.app.get_family", return_value=family), \
             patch("rok_automation.game.adb.find_adb_executable", return_value="adb"), \
             patch("rok_automation.game.adb._run_global"), \
             patch("rok_automation.game.adb.find_adb_device", return_value="127.0.0.1:16416"):
            self.assertFalse(self.app.check_emulator_connection())
            self.app.root.after.assert_called_once_with(5000, self.app._retry_emulator_connection)
            self.assertFalse(self.app.check_emulator_connection())
            self.app.root.after.assert_called_once()
            family.discover.return_value = [EmulatorInstance(1, "farm2", running=True, details={"adb_port": "16416"})]
            self.app._retry_emulator_connection()
            self.assertIsNone(self.app._connection_retry_id)
            self.assertIn("Connected", self.app.emulator_status_badge.config.call_args.kwargs["text"])
            self.app.root.after.assert_called_once()

    def test_no_selection_cancels_pending_retry(self):
        self.app._connection_retry_id = "retry-1"
        self.app._instance_index = None
        self.assertFalse(self.app.check_emulator_connection())
        self.app.root.after_cancel.assert_called_once_with("retry-1")
        self.assertIsNone(self.app._connection_retry_id)


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
        self.app._release_instance_lock()
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

    def test_instance_selection_scopes_accounts_and_blocks_duplicate_workers(self) -> None:
        from rok_automation.game.emulators import EmulatorInstance
        from rok_automation.automation.gmail_switcher import GmailAccountRegistry

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            self.app._base_accounts_path = base / "signed_in_gmail_accounts.json"
            self.app._base_gmail_state_path = base / "gmail_rotation.json"
            self.app._base_summary_path = base / "session_summary.json"
            self.app._base_daily_path = base / "daily_reward.flag"
            GmailAccountRegistry(self.app._base_accounts_path).add_manual("first@example.com")
            self.assertIsNone(self.app._instance_index)
            self.app._instances = {
                "farm1 (#0)": EmulatorInstance(0, "farm1"),
                "farm2 (#1)": EmulatorInstance(1, "farm2"),
            }
            with patch.object(self.app, "check_emulator_connection"):
                self.app.instance_choice.set("farm1 (#0)")
                self.app._select_instance()
                self.assertEqual(self.app.signed_in_accounts.load()[0].identity.email, "first@example.com")
                self.app.instance_choice.set("farm2 (#1)")
                self.app._select_instance()
                self.assertEqual(self.app.signed_in_accounts.load(), [])
                self.app.signed_in_accounts.add_manual("second@example.com")
                self.assertTrue(self.app._acquire_instance_lock())
                other = ROKAutomationApp.__new__(ROKAutomationApp)
                other._base_accounts_path = self.app._base_accounts_path
                other._instance_index = 1
                other._instance_lock = None
                other.log_warning = MagicMock()
                self.assertFalse(other._acquire_instance_lock())
                self.app._release_instance_lock()
                self.assertTrue(other._acquire_instance_lock())
                other._release_instance_lock()
                self.app.instance_choice.set("farm1 (#0)")
                self.app._select_instance()
                self.assertEqual(self.app.signed_in_accounts.load()[0].identity.email, "first@example.com")

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
        self.app._instance_index = 0
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
        self.app._instance_index = 0

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

    def test_farmed_total_survives_stop_and_next_start(self) -> None:
        self.app._instance_index = 0
        with patch.object(self.app, "_start_worker"), \
             patch("rok_automation.ui.app.set_daily_rewards"), \
             patch("rok_automation.ui.app.set_city_resources"), \
             patch("rok_automation.ui.app.set_forge_material"):
            self.app.start_automation()
            self.app._output_queue.put("Army 1/5 dispatched to Food Lv6")
            self.app._poll_output_queue()
            self.app._set_running_state(True)
            self.app.stop_automation()
            self.app.start_automation()
            self.assertIn("1 armies", self.app.lbl_session_farmed.cget("text"))
            self.app._output_queue.put("Army 1/5 dispatched to Gold Lv8")
            self.app._poll_output_queue()
        self.assertEqual(len(self.app._session_targets), 2)
        self.assertIn("2 armies", self.app.lbl_session_farmed.cget("text"))

    def test_city_tasks_run_without_farming(self) -> None:
        self.app._instance_index = 0
        self.app.opt_farm.set(False)
        self.app.opt_collect_city_resources.set(True)
        self.app.opt_daily_reward.set(True)
        with patch.object(self.app, "_start_worker") as start, \
             patch("rok_automation.ui.app.set_daily_rewards"), \
             patch("rok_automation.ui.app.set_city_resources") as resources, \
             patch("rok_automation.ui.app.set_forge_material"):
            self.app.start_automation(scope="rok")
        command = start.call_args.args[0]
        self.assertIn("--city-tasks-only", command)
        self.assertIn("--daily-reward-only", command)
        self.assertNotIn("--farm", command)
        resources.assert_called_once_with(True, self.app._state_path("city_resources.flag"))

    def test_city_task_test_buttons_run_independently(self) -> None:
        self.app.opt_forge_material.set("Iron")
        with patch.object(self.app, "_launch_utility_command") as launch:
            self.app._test_city_task(False)
            self.app._test_city_task(True)
        self.assertIn("--test-city-resources", launch.call_args_list[0].args[0])
        forge_command = launch.call_args_list[1].args[0]
        self.assertEqual(forge_command[-2:], ["--test-forge-material", "Iron"])

    def test_stop_summary_merges_current_run_disk_progress(self) -> None:
        from rok_automation.logic.resource_selector import Resource, SearchTarget

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            path.write_text(json.dumps({"targets": [
                {"resource": "Gold", "level": 8},
                {"resource": "Wood", "level": 6},
            ]}), encoding="utf-8")
            self.app.session_summary_path = path
            self.app._run_summary_enabled = True
            self.app._run_start_index = 1
            self.app._session_targets = [
                SearchTarget(resource=Resource.FOOD, level=6),
                SearchTarget(resource=Resource.GOLD, level=8),
            ]
            self.app._display_farmed_resources_summary()
            self.assertEqual([target.resource for target in self.app._session_targets],
                             [Resource.FOOD, Resource.GOLD, Resource.WOOD])
            self.assertIn("3 armies", self.app.lbl_session_farmed.cget("text"))

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
        self.app._instance_index = 0
        spawned = MagicMock()
        spawned.stdout = None
        spawned.poll.return_value = None
        with patch.object(self.app, "_spawn_process", return_value=spawned) as mock_spawn:
            with patch("rok_automation.ui.app.threading.Thread") as mock_thread:
                mock_thread.return_value.start = MagicMock()
                self.app._start_worker(["python", "-c", "pass"])

            mock_spawn.assert_called_once()
            self.assertIs(self.app._current_process, spawned)
            self.app._release_instance_lock()

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
