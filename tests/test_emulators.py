"""Unit tests for the emulator registry and the native capture.

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

    def test_families_expose_a_label(self) -> None:
        for family in families():
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


class TestNativeCapture(unittest.TestCase):
    """Verify the native capture is the only capture path."""

    def test_the_interface_is_built_from_the_native_frame_source(self) -> None:
        from rok_automation.game.native import create_native_interface

        source = MagicMock()
        instance = EmulatorInstance(
            0, "A", running=True, pid=1, width=1920, height=1080
        )
        interface = MagicMock()
        with patch(
            "rok_automation.game.native.select_family",
            return_value=(get_family("ldplayer"), None),
        ), patch(
            "rok_automation.game.emulators.ldplayer.LDPlayerFamily.discover",
            return_value=[instance],
        ), patch(
            "rok_automation.game.emulators.ldplayer.LDPlayerFamily.open_capture",
            return_value=source,
        ), patch(
            "rok_automation.game.native.NativeInterface",
            return_value=interface,
        ) as native_build:
            result = create_native_interface(emulator="ldplayer")

        self.assertIs(result, interface)
        native_build.assert_called_once()
        self.assertIs(native_build.call_args.kwargs["source"], source)
        self.assertIs(native_build.call_args.kwargs["instance"], instance)

    def test_a_missing_capture_library_is_reported(self) -> None:
        from rok_automation.game.native import create_native_interface

        with patch(
            "rok_automation.game.native.select_family",
            return_value=(get_family("ldplayer"), None),
        ), patch(
            "rok_automation.game.emulators.ldplayer.LDPlayerFamily.discover",
            return_value=[
                EmulatorInstance(0, "A", running=True, pid=1, width=1920, height=1080)
            ],
        ), patch(
            "rok_automation.game.emulators.ldplayer.LDPlayerFamily.open_capture",
            side_effect=EmulatorError("no capture library"),
        ):
            with self.assertRaises(EmulatorError):
                create_native_interface(emulator="ldplayer")


class TestMuMuAdbCapture(unittest.TestCase):
    def test_discovers_current_mumu_install_and_instance_ports(self) -> None:
        import tempfile

        family = get_family("mumu")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manager = root / "nx_main" / "MuMuManager.exe"
            adb = root / "nx_main" / "adb.exe"
            manager.parent.mkdir(parents=True)
            manager.touch()
            adb.touch()
            for index, port in ((0, 16384), (1, 16416)):
                config = root / "vms" / f"MuMuPlayerGlobal-15.0-{index}" / "configs" / "vm_config.json"
                config.parent.mkdir(parents=True)
                config.write_text(json.dumps({"vm": {"nat": {"port_forward": {"adb": {"host_port": str(port)}}}}}))
            with patch.object(type(family), "install_folders", return_value=[root]), \
                 patch.object(type(family), "_port_is_open", side_effect=lambda port: port == 16416):
                self.assertEqual(family.manager_path(), manager.parent)
                self.assertIn(adb, family.adb_executables())
                instances = family.discover()
            self.assertEqual([item.index for item in instances], [0, 1])
            self.assertEqual([item.running for item in instances], [False, True])
            self.assertEqual(instances[1].details["adb_port"], "16416")

    def test_decodes_adb_screenshot(self) -> None:
        import cv2
        from rok_automation.game.native import AdbFrameSource

        original = np.full((10, 20, 3), 42, dtype=np.uint8)
        ok, encoded = cv2.imencode(".png", original)
        self.assertTrue(ok)
        with patch("rok_automation.game.native._run", return_value=encoded.tobytes()) as run:
            result = AdbFrameSource("adb", "127.0.0.1:16384").capture()
        np.testing.assert_array_equal(result, original)
        self.assertEqual(run.call_args.args[2], ["exec-out", "screencap", "-p"])

    def test_landscape_screenshot_sets_touch_dimensions(self) -> None:
        import tempfile
        from rok_automation.game.native import AdbFrameSource, NativeInterface

        interface = NativeInterface.__new__(NativeInterface)
        interface.source = AdbFrameSource("adb", "127.0.0.1:16384")
        interface._guest_size = (1080, 1920)  # wm size in natural orientation
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(interface.source, "capture", return_value=np.zeros((1080, 1920, 3), dtype=np.uint8)):
            interface.screenshot_path = Path(folder) / "frame.png"
            interface.screenshot()
            self.assertEqual(interface._guest_size, (1920, 1080))
            x, y = interface._to_guest(50, 50)
            self.assertAlmostEqual(x, 75, delta=1)
            self.assertAlmostEqual(y, 75, delta=1)

    def test_connects_to_the_selected_mumu_instance(self) -> None:
        from rok_automation.game.native import create_native_interface

        instance = EmulatorInstance(2, "MuMu 2", running=True, details={"adb_port": "16416"})
        family = get_family("mumu")
        with patch("rok_automation.game.native.select_family", return_value=(family, None)), \
             patch.object(type(family), "discover", return_value=[instance]), \
             patch("rok_automation.game.native._run_global") as connect, \
             patch("rok_automation.game.native.NativeInterface") as interface:
            create_native_interface(emulator="mumu", index=2, adb_path="adb")
        connect.assert_called_once_with("adb", ["connect", "127.0.0.1:16416"], timeout=25)
        self.assertEqual(interface.call_args.kwargs["serial"], "127.0.0.1:16416")

    def test_default_is_mumu_without_a_saved_selection(self) -> None:
        with patch("rok_automation.game.emulators.load_selection", return_value=(None, None)), \
             patch("rok_automation.game.emulators.selection_from_environment", return_value=(None, None)):
            self.assertEqual(select_family()[0].key, "mumu")


class TestCaptureLibraryGuards(unittest.TestCase):
    """Verify the native capture refuses to run without its library."""

    def test_a_missing_library_is_reported(self) -> None:
        instance = EmulatorInstance(0, "A", running=True, pid=1, width=1920, height=1080)
        with self.assertRaises(EmulatorError):
            LDOpenGLCapture(instance, Path("artifacts/_does_not_exist.dll"))


if __name__ == "__main__":
    unittest.main()
