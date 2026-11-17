"""Regression coverage for temporary Windows progress-file locks."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rok_automation.automation.gmail_switcher import GmailRotation


class TestGmailSaveRetry(unittest.TestCase):
    def test_save_succeeds_on_fifth_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gmail_rotation.json"
            path.write_text('{"previous": true}', encoding="utf-8")
            replace = Path.replace
            calls = []

            def locked_replace(source, destination):
                calls.append(source)
                if len(calls) <= 5:
                    raise PermissionError("File is temporarily locked")
                return replace(source, destination)

            with patch.object(Path, "replace", locked_replace), patch(
                "rok_automation.automation.gmail_switcher.time.sleep"
            ) as sleep:
                GmailRotation(path=path).save()

            self.assertEqual(len(calls), 6)
            self.assertEqual(sleep.call_count, 5)
            sleep.assert_called_with(0.2)
            self.assertIn("updated_at", json.loads(path.read_text(encoding="utf-8")))
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_persistent_denial_preserves_previous_save_and_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gmail_rotation.json"
            previous = '{"previous": true}'
            path.write_text(previous, encoding="utf-8")
            with patch.object(Path, "replace", side_effect=PermissionError) as replace, patch(
                "rok_automation.automation.gmail_switcher.time.sleep"
            ) as sleep:
                with self.assertRaises(PermissionError):
                    GmailRotation(path=path).save()
            self.assertEqual(replace.call_count, 6)
            self.assertEqual(sleep.call_count, 5)
            self.assertEqual(path.read_text(encoding="utf-8"), previous)

    def test_other_errors_are_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gmail_rotation.json"
            with patch.object(Path, "replace", side_effect=FileNotFoundError) as replace, patch(
                "rok_automation.automation.gmail_switcher.time.sleep"
            ) as sleep:
                with self.assertRaises(FileNotFoundError):
                    GmailRotation(path=path).save()
            replace.assert_called_once()
            sleep.assert_not_called()
