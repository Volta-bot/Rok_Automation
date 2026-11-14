"""The CLI and UI must use the same Gmail progress file."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rok_automation import __main__ as cli
from rok_automation.automation.gmail_switcher import DEFAULT_GMAIL_STATE_PATH


class TestGmailProgressPath(unittest.TestCase):
    def test_farming_uses_default_and_custom_progress_paths(self):
        cases = [([], DEFAULT_GMAIL_STATE_PATH), (["--gmail-state", "custom.json"], Path("custom.json"))]
        for extra, expected in cases:
            with self.subTest(path=expected), patch.object(sys, "argv", ["rok_automation", "--farm", *extra]), patch.object(cli, "_build_game", return_value=MagicMock()), patch.object(cli, "clear_paused"), patch.object(cli, "FarmingController") as controller:
                controller.return_value.run.return_value.last_error = None
                self.assertEqual(cli.main(), 0)
                self.assertEqual(controller.call_args.kwargs["gmail_state_path"], expected)
