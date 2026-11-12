"""Unit tests for the cooperative pause mechanism."""

from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rok_automation.game.pause import (
    clear_paused,
    is_paused,
    set_paused,
    wait_while_paused,
)


class TestPauseFlag(unittest.TestCase):
    """Verify the pause flag file round-trips."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.flag = Path(self._tmp.name) / "state" / "pause.flag"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_not_paused_by_default(self) -> None:
        self.assertFalse(is_paused(self.flag))

    def test_set_and_clear(self) -> None:
        set_paused(self.flag)
        self.assertTrue(is_paused(self.flag))
        clear_paused(self.flag)
        self.assertFalse(is_paused(self.flag))

    def test_clear_is_safe_when_not_paused(self) -> None:
        clear_paused(self.flag)
        self.assertFalse(is_paused(self.flag))

    def test_set_creates_missing_directories(self) -> None:
        nested = Path(self._tmp.name) / "a" / "b" / "c" / "pause.flag"
        set_paused(nested)
        self.assertTrue(nested.exists())


class TestWaitWhilePaused(unittest.TestCase):
    """Verify a paused run blocks and then resumes."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.flag = Path(self._tmp.name) / "pause.flag"

    def tearDown(self) -> None:
        clear_paused(self.flag)
        self._tmp.cleanup()

    def test_returns_immediately_when_not_paused(self) -> None:
        started = time.monotonic()
        paused = wait_while_paused(self.flag)
        self.assertFalse(paused)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_blocks_until_the_flag_is_cleared(self) -> None:
        set_paused(self.flag)
        released = threading.Event()
        finished = threading.Event()

        def worker():
            wait_while_paused(self.flag)
            finished.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        # The worker must still be blocked while the flag exists.
        self.assertFalse(finished.wait(timeout=1.0))
        self.assertTrue(released.wait(0.1) is False)

        clear_paused(self.flag)
        self.assertTrue(finished.wait(timeout=5.0))

    def test_logs_the_pause_and_resume(self) -> None:
        messages: list[str] = []
        set_paused(self.flag)

        def worker():
            wait_while_paused(self.flag, messages.append)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        time.sleep(0.3)
        clear_paused(self.flag)
        thread.join(timeout=5.0)

        self.assertTrue(any("Paused" in message for message in messages))
        self.assertTrue(any("Resumed" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
