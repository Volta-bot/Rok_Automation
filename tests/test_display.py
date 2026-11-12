"""Unit tests for the display-scaling and hidden-process helpers."""

from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from rok_automation.game.display import display_scale, enable_dpi_awareness
from rok_automation.game.process import hidden_creation_flags, run_hidden


class TestHiddenProcesses(unittest.TestCase):
    """Verify helper programs never open a console window."""

    def test_the_flag_is_used_on_windows(self) -> None:
        if sys.platform == "win32":
            self.assertEqual(
                hidden_creation_flags(),
                getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            self.assertEqual(hidden_creation_flags(), 0)

    def test_run_hidden_passes_the_flag(self) -> None:
        with patch("rok_automation.game.process.subprocess.run") as run:
            run.return_value = MagicMock(stdout=b"", returncode=0)
            run_hidden(["adb", "devices"], capture_output=True)

        kwargs = run.call_args.kwargs
        self.assertIn("creationflags", kwargs)
        self.assertEqual(kwargs["creationflags"], hidden_creation_flags())

    def test_an_explicit_flag_is_kept(self) -> None:
        with patch("rok_automation.game.process.subprocess.run") as run:
            run.return_value = MagicMock(stdout=b"", returncode=0)
            run_hidden(["adb", "devices"], creationflags=123)

        self.assertEqual(run.call_args.kwargs["creationflags"], 123)


class TestDisplayScale(unittest.TestCase):
    """Verify the display scale is read without breaking on odd systems."""

    def test_the_scale_is_at_least_one(self) -> None:
        # A scale below one would shrink the interface.
        self.assertGreaterEqual(display_scale(), 1.0)

    def test_enabling_awareness_returns_the_scale(self) -> None:
        scale = enable_dpi_awareness()
        self.assertGreaterEqual(scale, 1.0)

    def test_a_broken_display_is_tolerated(self) -> None:
        with patch(
            "rok_automation.game.display.gdi32",
            create=True,
        ) as gdi32:
            gdi32.GetDeviceCaps.return_value = 0
            self.assertEqual(display_scale(), 1.0)


if __name__ == "__main__":
    unittest.main()
