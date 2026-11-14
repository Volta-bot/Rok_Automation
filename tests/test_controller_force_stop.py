"""Unit tests for force stop and resource summary handling in FarmingController."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController
from rok_automation.automation.states import FarmState
from rok_automation.logic.resource_selector import Resource, SearchTarget


class TestControllerForceStop(unittest.TestCase):
    """Test suite for force stop resource tracking and summaries."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.summary_path = Path(self.tmp_dir.name) / "state" / "session_summary.json"
        self.mock_game = MagicMock()
        self.mock_detector = MagicMock()
        self.controller = FarmingController(
            self.mock_game,
            detector=self.mock_detector,
            switch_gmail=False,
            gmail_state_path=Path(self.tmp_dir.name) / "gmail_rotation.json",
            session_summary_path=self.summary_path,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_save_session_summary_creates_valid_json(self) -> None:
        """Verify _save_session_summary persists accurate target and yield data."""
        self.controller.session_targets = [
            SearchTarget(resource=Resource.FOOD, level=6),
            SearchTarget(resource=Resource.WOOD, level=7),
        ]
        self.controller._save_session_summary(status="running")

        self.assertTrue(self.summary_path.is_file())
        data = json.loads(self.summary_path.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["armies_dispatched"], 2)
        self.assertEqual(data["totals"]["Food"], 1_260_000)
        self.assertEqual(data["totals"]["Wood"], 1_417_500)
        self.assertEqual(data["grand_total"], 2_677_500)

    def test_log_resource_summary_stopped_early_header(self) -> None:
        """Verify _log_resource_summary with stopped_early uses force-stop banner."""
        logged_lines: list[str] = []
        self.controller.log = logged_lines.append
        self.controller.session_targets = [
            SearchTarget(resource=Resource.GOLD, level=8),
        ]

        self.controller._log_resource_summary(
            total_characters=1,
            gmail_accounts=1,
            stopped_early=True,
        )

        full_log = "\n".join(logged_lines)
        self.assertIn("TOTAL RESOURCES FARMED BEFORE FORCE STOP", full_log)
        self.assertIn("Gold (Vàng)", full_log)
        self.assertIn("GRAND TOTAL", full_log)
        self.assertIn("Force stopped by user after 1 RoK character(s)", full_log)

    def test_run_catches_keyboard_interrupt(self) -> None:
        """Verify run() catches KeyboardInterrupt, saves state, and outputs summary."""
        logged_lines: list[str] = []
        self.controller.log = logged_lines.append
        self.controller.session_targets = [
            SearchTarget(resource=Resource.STONE, level=6),
        ]

        with patch("rok_automation.automation.controller.GmailSwitcher"), patch.object(
            self.controller,
            "_farm_linked_characters",
            side_effect=KeyboardInterrupt,
        ):
            context = self.controller.run()

        self.assertEqual(context.last_error, "Force stopped by user")
        self.assertEqual(context.armies_dispatched, 0)
        self.assertEqual(len(context.dispatched_targets), 1)

        # Check session file was saved with stopped status
        data = json.loads(self.summary_path.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "stopped_by_user")
        self.assertEqual(data["grand_total"], 945_000)

        # Check summary was logged
        full_log = "\n".join(logged_lines)
        self.assertIn("TOTAL RESOURCES FARMED BEFORE FORCE STOP", full_log)

    def test_run_persists_error_status_after_an_exception(self) -> None:
        with patch("rok_automation.automation.controller.GmailSwitcher"), patch.object(
            self.controller, "_farm_linked_characters", side_effect=RuntimeError("capture failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "capture failed"):
                self.controller.run()
        data = json.loads(self.summary_path.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "error")


if __name__ == "__main__":
    unittest.main()
