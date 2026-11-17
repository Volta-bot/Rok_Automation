import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from rok_automation.runtime import data_root, worker_command


class PortableRuntimeTests(unittest.TestCase):
    def test_frozen_worker_uses_sibling_executable_without_python_flags(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", str(Path("release with spaces") / "ROKauto.exe")
        ):
            self.assertEqual(worker_command(), [str(Path("release with spaces") / "ROKauto-worker.exe")])

    def test_frozen_data_is_outside_installation(self):
        with patch.object(sys, "frozen", True, create=True), patch.dict(
            "os.environ", {"LOCALAPPDATA": str(Path("user data"))}
        ):
            self.assertEqual(data_root(), Path("user data") / "ROKauto")

    def test_source_worker_uses_python_module(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertEqual(worker_command()[1:], ["-u", "-m", "rok_automation"])
