"""Unit tests for the ADB backend.

The emulator is not required: the adb client is replaced by a fake that
returns recorded bytes, so the parsing, coordinate mapping and timeout
scaling can all be checked offline.
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.game.adb import (
    AdbError,
    AdbInput,
    AdbInterface,
    find_adb_device,
    parse_screencap,
)
from rok_automation.game.interface import TimingConfig


def make_raw_frame(width: int, height: int, value: int = 128) -> bytes:
    """Build a raw ADB screencap payload (header + BGRA pixels)."""

    header = struct.pack("<IIII", width, height, 1, 0)
    pixels = np.full((height, width, 4), value, dtype=np.uint8)
    return header + pixels.tobytes()


class TestParseScreencap(unittest.TestCase):
    """Verify the raw screencap payload is decoded correctly."""

    def test_parses_dimensions_and_pixels(self) -> None:
        data = make_raw_frame(1920, 1080, value=200)
        frame = parse_screencap(data)
        self.assertEqual(frame.shape, (1080, 1920, 3))
        self.assertEqual(int(frame[0, 0, 0]), 200)

    def test_rejects_truncated_payload(self) -> None:
        with self.assertRaises(AdbError):
            parse_screencap(b"\x00" * 8)

    def test_rejects_short_pixel_data(self) -> None:
        header = struct.pack("<IIII", 1920, 1080, 1, 0)
        with self.assertRaises(AdbError):
            parse_screencap(header + b"\x00" * 1000)

    def test_rejects_empty_size(self) -> None:
        header = struct.pack("<IIII", 0, 0, 1, 0)
        with self.assertRaises(AdbError):
            parse_screencap(header)


class TestDeviceDiscovery(unittest.TestCase):
    """Verify the connected emulator is picked out of adb devices."""

    def test_prefers_an_emulator_serial(self) -> None:
        output = "List of devices attached\nemulator-5554\tdevice\nother-1234\tdevice\n"
        with patch("rok_automation.game.adb.subprocess.run") as run:
            run.return_value = MagicMock(stdout=output, returncode=0)
            self.assertEqual(find_adb_device("adb"), "emulator-5554")

    def test_falls_back_to_the_only_device(self) -> None:
        output = "List of devices attached\nABCD1234\tdevice\n"
        with patch("rok_automation.game.adb.subprocess.run") as run:
            run.return_value = MagicMock(stdout=output, returncode=0)
            self.assertEqual(find_adb_device("adb"), "ABCD1234")

    def test_reports_when_nothing_is_connected(self) -> None:
        output = "List of devices attached\n\n"
        with patch("rok_automation.game.adb.subprocess.run") as run:
            run.return_value = MagicMock(stdout=output, returncode=0)
            with self.assertRaises(AdbError):
                find_adb_device("adb")


class TestAdbInterface(unittest.TestCase):
    """Verify coordinate mapping and the timeout scaling."""

    def _interface(self, guest=(1920, 1080)) -> AdbInterface:
        interface = AdbInterface.__new__(AdbInterface)
        interface.adb_path = "adb"
        interface.serial = "emulator-5554"
        interface.screenshot_path = Path("artifacts/_test.png")
        interface.timing = TimingConfig()
        interface.pause_flag_path = None
        interface.log = None
        interface._guest_size = guest
        return interface

    def test_canonical_point_maps_to_guest_pixels(self) -> None:
        interface = self._interface()
        # The canonical centre is the guest centre.
        self.assertEqual(interface._to_guest(640, 360), (960, 540))
        # The canonical top-left is the guest top-left.
        self.assertEqual(interface._to_guest(0, 0), (0, 0))
        # The canonical bottom-right is the guest bottom-right.
        self.assertEqual(interface._to_guest(1279, 719), (1919, 1079))

    def test_mapping_scales_with_the_guest_size(self) -> None:
        interface = self._interface(guest=(1280, 720))
        self.assertEqual(interface._to_guest(640, 360), (640, 360))
        self.assertEqual(interface._to_guest(1279, 719), (1279, 719))

    def test_mapping_is_clamped_inside_the_guest(self) -> None:
        interface = self._interface()
        x, y = interface._to_guest(99999, 99999)
        self.assertLessEqual(x, 1919)
        self.assertLessEqual(y, 1079)

    def test_click_uses_guest_coordinates(self) -> None:
        interface = self._interface()
        calls: list[list[str]] = []

        def fake_run(adb_path, serial, args, **kwargs):
            calls.append(list(args))
            return b""

        with patch("rok_automation.game.adb._run", side_effect=fake_run), \
             patch("rok_automation.game.adb.time.sleep"), \
             patch("rok_automation.game.adb.random.randint", return_value=0):
            # A synthetic 1280x720 screenshot file for the frame shape.
            import cv2

            path = Path("artifacts/_test_click.png")
            path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(path), np.zeros((720, 1280, 3), dtype=np.uint8))
            interface.click((600, 300, 80, 40), str(path))

        self.assertTrue(any("tap" in call for call in calls))
        tap = next(call for call in calls if "tap" in call)
        # Centre of the bounds is (640, 320) canonical -> (960, 480) guest.
        self.assertEqual(tap[-2:], ["960", "480"])


class TestStaleConnectionRecovery(unittest.TestCase):
    """Verify a stale adb connection is re-established instead of hanging."""

    def _interface(self, serial: str = "emulator-5554") -> AdbInterface:
        interface = AdbInterface.__new__(AdbInterface)
        interface.adb_path = "adb"
        interface.serial = serial
        interface.timing = TimingConfig()
        interface.pause_flag_path = None
        interface.log = None
        return interface

    def test_the_local_port_is_derived_from_the_serial(self) -> None:
        self.assertEqual(self._interface("emulator-5554").adb_port(), 5555)
        self.assertEqual(self._interface("emulator-5556").adb_port(), 5557)

    def test_a_loopback_serial_is_used_as_is(self) -> None:
        self.assertEqual(self._interface("127.0.0.1:16384").adb_port(), 16384)

    def test_an_unknown_serial_has_no_port(self) -> None:
        self.assertIsNone(self._interface("something-else").adb_port())

    def test_reconnect_connects_to_the_local_port(self) -> None:
        interface = self._interface()
        calls: list[list[str]] = []

        def fake_global(adb_path, args, **kwargs):
            calls.append(list(args))
            return b""

        with patch("rok_automation.game.adb._run_global", side_effect=fake_global), \
             patch("rok_automation.game.adb._run", return_value=b"device"), \
             patch("rok_automation.game.adb.time.sleep"):
            interface.reconnect()

        self.assertEqual(calls, [["connect", "127.0.0.1:5555"]])

    def test_a_stale_capture_is_retried_after_reconnecting(self) -> None:
        interface = self._interface()
        attempts = {"count": 0}

        def fake_run(adb_path, serial, args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise AdbError("adb command timed out: exec-out screencap")
            return make_raw_frame(1280, 720, value=90)

        with patch("rok_automation.game.adb._run", side_effect=fake_run), \
             patch.object(AdbInterface, "reconnect") as reconnect:
            frame = interface._capture_raw()

        reconnect.assert_called_once()
        self.assertEqual(frame.shape, (720, 1280, 3))

    def test_a_stale_tap_is_retried_after_reconnecting(self) -> None:
        interface = self._interface()
        calls: list[list[str]] = []

        def fake_run(adb_path, serial, args, **kwargs):
            calls.append(list(args))
            if len(calls) == 1:
                raise AdbError("adb command timed out")
            return b""

        with patch("rok_automation.game.adb._run", side_effect=fake_run), \
             patch.object(AdbInput, "reconnect") as reconnect, \
             patch("rok_automation.game.adb.time.sleep"):
            interface._input(["shell", "input", "tap", "10", "20"])

        reconnect.assert_called_once()
        self.assertEqual(len(calls), 2)


class TestTimeoutScaling(unittest.TestCase):
    """Verify slower captures scale the screen-wait budgets."""

    def test_desktop_path_does_not_scale(self) -> None:
        timing = TimingConfig()
        self.assertEqual(timing.timeout_scale, 1.0)

    def test_slow_capture_scales_the_budgets(self) -> None:
        timing = TimingConfig(capture_latency=0.354)
        self.assertGreater(timing.timeout_scale, 3.0)
        # A 0.70 s budget must grow to cover several slow polls.
        self.assertGreater(timing.scaled(0.70), 2.0)

    def test_scale_never_shrinks_a_budget(self) -> None:
        timing = TimingConfig(capture_latency=0.001)
        self.assertEqual(timing.timeout_scale, 1.0)


if __name__ == "__main__":
    unittest.main()
