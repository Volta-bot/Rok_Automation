"""Unit tests for the emulator registry and the native capture backend.

The emulator is not required: the instance list is parsed from recorded
``ldconsole.exe list2`` output, and the framebuffer handling is checked
on synthetic frames.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.game.backends import (
    BACKEND_ADB,
    BACKEND_DESKTOP,
    BACKEND_NATIVE,
    create_game_interface,
)
from rok_automation.game.emulators import (
    EmulatorError,
    EmulatorInstance,
    families,
    get_family,
    load_selection,
    save_selection,
    select_family,
    select_instance,
)
from rok_automation.game.emulators.ldplayer import (
    LDOpenGLCapture,
    parse_instance_list,
    to_image_order,
)


class TestParseInstanceList(unittest.TestCase):
    """Verify the ldconsole.exe list2 output is parsed correctly."""

    def test_parses_a_normal_row(self) -> None:
        output = "0,LDPlayer,4523898,4327240,1,23400,2860,1920,1080,280\n"
        instances = parse_instance_list(output)
        self.assertEqual(len(instances), 1)
        instance = instances[0]
        self.assertEqual(instance.index, 0)
        self.assertEqual(instance.name, "LDPlayer")
        self.assertTrue(instance.running)
        self.assertEqual(instance.pid, 23400)
        self.assertEqual(instance.width, 1920)
        self.assertEqual(instance.height, 1080)
        self.assertEqual(instance.details["top_window"], "4523898")
        self.assertEqual(instance.details["dpi"], "280")

    def test_parses_several_rows(self) -> None:
        output = (
            "0,LDPlayer,4523898,4327240,1,23400,2860,1920,1080,280\n"
            "1,LDPlayer-1,0,0,0,-1,-1,1280,720,240\n"
        )
        instances = parse_instance_list(output)
        self.assertEqual([item.index for item in instances], [0, 1])
        self.assertTrue(instances[0].is_usable)
        self.assertFalse(instances[1].is_usable)

    def test_survives_a_name_containing_a_comma(self) -> None:
        output = "0,My,Player,4523898,4327240,1,23400,2860,1920,1080,280\n"
        instances = parse_instance_list(output)
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].name, "My,Player")
        self.assertEqual(instances[0].width, 1920)
        self.assertEqual(instances[0].height, 1080)
        self.assertEqual(instances[0].details["dpi"], "280")

    def test_skips_malformed_rows(self) -> None:
        output = "garbage\n0,LDPlayer,1,2,1,3,4,1920,1080,280\n"
        instances = parse_instance_list(output)
        self.assertEqual(len(instances), 1)

    def test_ignores_blank_lines(self) -> None:
        self.assertEqual(parse_instance_list("\n\n"), [])


class TestSelectInstance(unittest.TestCase):
    """Verify the emulator instance to drive is picked correctly."""

    def _instances(self) -> list[EmulatorInstance]:
        return [
            EmulatorInstance(0, "A", running=False, pid=-1, width=1920, height=1080),
            EmulatorInstance(1, "B", running=True, pid=23400, width=1920, height=1080),
        ]

    def test_picks_the_running_instance(self) -> None:
        instance = select_instance(self._instances())
        self.assertEqual(instance.index, 1)

    def test_honours_an_explicit_index(self) -> None:
        instances = [
            EmulatorInstance(0, "A", running=True, pid=100, width=1920, height=1080),
            EmulatorInstance(1, "B", running=True, pid=300, width=1920, height=1080),
        ]
        instance = select_instance(instances, index=1)
        self.assertEqual(instance.index, 1)

    def test_rejects_an_index_that_does_not_exist(self) -> None:
        with self.assertRaises(EmulatorError):
            select_instance(self._instances(), index=7)

    def test_rejects_an_explicit_index_that_is_not_running(self) -> None:
        with self.assertRaises(EmulatorError):
            select_instance(self._instances(), index=0)

    def test_reports_when_nothing_is_running(self) -> None:
        with self.assertRaises(EmulatorError):
            select_instance(self._instances()[:1])


class TestFrameOrder(unittest.TestCase):
    """Verify the bottom-up framebuffer is flipped into image order."""

    def test_flips_rows_into_top_down_order(self) -> None:
        frame = np.zeros((4, 2, 3), dtype=np.uint8)
        frame[0] = 10  # bottom row of the raw buffer
        frame[3] = 40  # top row of the raw buffer
        flipped = to_image_order(frame)
        self.assertEqual(int(flipped[0, 0, 0]), 40)
        self.assertEqual(int(flipped[3, 0, 0]), 10)

    def test_returns_a_contiguous_copy(self) -> None:
        frame = np.zeros((3, 3, 3), dtype=np.uint8)
        flipped = to_image_order(frame)
        self.assertTrue(flipped.flags["C_CONTIGUOUS"])
        self.assertIsNot(flipped, frame)


class TestEmulatorRegistry(unittest.TestCase):
    """Verify the emulator family registry behaves."""

    def test_the_built_in_families_are_registered(self) -> None:
        keys = {family.key for family in families()}
        self.assertIn("ldplayer", keys)
        self.assertIn("mumu", keys)

    def test_families_expose_their_window_terms(self) -> None:
        for family in families():
            self.assertTrue(family.window_terms, f"{family.key} has no window terms")
            self.assertTrue(family.label, f"{family.key} has no label")

    def test_get_family_rejects_an_unknown_key(self) -> None:
        with self.assertRaises(EmulatorError):
            get_family("not-an-emulator")

    def test_get_family_returns_the_registered_family(self) -> None:
        self.assertEqual(get_family("ldplayer").key, "ldplayer")

    def test_only_ldplayer_claims_native_capture(self) -> None:
        # MuMu's capture library has not been verified yet, so it must not
        # claim support for it.
        self.assertTrue(get_family("ldplayer").has_native_capture)
        self.assertFalse(get_family("mumu").has_native_capture)

    def test_selection_round_trips_through_the_state_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "emulator.json"
            save_selection("mumu", index=2, path=path)
            self.assertEqual(load_selection(path), ("mumu", 2))
            self.assertEqual(json.loads(path.read_text())["family"], "mumu")

    def test_a_missing_state_file_selects_nothing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "emulator.json"
            self.assertEqual(load_selection(path), (None, None))

    def test_select_family_prefers_the_requested_family(self) -> None:
        family, _ = select_family("mumu")
        self.assertEqual(family.key, "mumu")


class TestBackendFallback(unittest.TestCase):
    """Verify the capture backends fall back to each other."""

    def test_rejects_an_unknown_backend(self) -> None:
        with self.assertRaises(ValueError):
            create_game_interface("nonsense")

    def test_native_falls_back_to_adb(self) -> None:
        adb_interface = MagicMock()
        with patch(
            "rok_automation.game.backends._native_interface",
            side_effect=EmulatorError("no capture library"),
        ), patch(
            "rok_automation.game.backends._adb_interface",
            return_value=adb_interface,
        ) as adb_build:
            result = create_game_interface(BACKEND_NATIVE, emulator="ldplayer")
        self.assertIs(result, adb_interface)
        adb_build.assert_called_once()

    def test_native_falls_back_to_the_desktop_window(self) -> None:
        desktop_interface = MagicMock()
        with patch(
            "rok_automation.game.backends._native_interface",
            side_effect=EmulatorError("no capture library"),
        ), patch(
            "rok_automation.game.backends._adb_interface",
            side_effect=RuntimeError("no adb"),
        ), patch(
            "rok_automation.game.backends._desktop_interface",
            return_value=desktop_interface,
        ):
            result = create_game_interface(BACKEND_NATIVE, emulator="ldplayer")
        self.assertIs(result, desktop_interface)

    def test_adb_backend_does_not_try_native_capture(self) -> None:
        adb_interface = MagicMock()
        with patch(
            "rok_automation.game.backends._native_interface",
            side_effect=AssertionError("native must not be used"),
        ), patch(
            "rok_automation.game.backends._adb_interface",
            return_value=adb_interface,
        ):
            result = create_game_interface(BACKEND_ADB, emulator="ldplayer")
        self.assertIs(result, adb_interface)

    def test_desktop_backend_is_the_last_resort(self) -> None:
        desktop_interface = MagicMock()
        with patch(
            "rok_automation.game.backends._adb_interface",
            side_effect=RuntimeError("no adb"),
        ), patch(
            "rok_automation.game.backends._desktop_interface",
            return_value=desktop_interface,
        ):
            result = create_game_interface(BACKEND_ADB, emulator="ldplayer")
        self.assertIs(result, desktop_interface)

    def test_reports_when_every_backend_fails(self) -> None:
        with patch(
            "rok_automation.game.backends._adb_interface",
            side_effect=RuntimeError("no adb"),
        ), patch(
            "rok_automation.game.backends._desktop_interface",
            side_effect=RuntimeError("no window"),
        ):
            with self.assertRaises(RuntimeError):
                create_game_interface(BACKEND_ADB, emulator="ldplayer")

    def test_an_unusable_emulator_falls_back_to_the_next_one(self) -> None:
        # MuMu is not installed here, so its native capture must not be
        # attempted and the ADB backend must take over.
        desktop_interface = MagicMock()
        with patch(
            "rok_automation.game.backends._adb_interface",
            side_effect=RuntimeError("no adb"),
        ), patch(
            "rok_automation.game.backends._desktop_interface",
            return_value=desktop_interface,
        ):
            result = create_game_interface(BACKEND_NATIVE, emulator="mumu")
        self.assertIs(result, desktop_interface)


class TestCaptureLibraryGuards(unittest.TestCase):
    """Verify the native capture refuses to run without its library."""

    def test_a_missing_library_is_reported(self) -> None:
        instance = EmulatorInstance(0, "A", running=True, pid=1, width=1920, height=1080)
        with self.assertRaises(EmulatorError):
            LDOpenGLCapture(instance, Path("artifacts/_does_not_exist.dll"))


if __name__ == "__main__":
    unittest.main()
