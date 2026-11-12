"""Unit tests for the native LDPlayer capture backend.

The emulator is not required: the instance list is parsed from recorded
``ldconsole.exe list2`` output, and the framebuffer handling is checked
on synthetic frames.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from rok_automation.game.backends import (
    BACKEND_ADB,
    BACKEND_DESKTOP,
    BACKEND_NATIVE,
    create_game_interface,
)
from rok_automation.game.ldopengl import (
    LDOpenGLError,
    LDPlayerInstance,
    parse_instance_list,
    select_instance,
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
        self.assertEqual(instance.top_window, 4523898)
        self.assertEqual(instance.bind_window, 4327240)
        self.assertTrue(instance.running)
        self.assertEqual(instance.player_pid, 23400)
        self.assertEqual(instance.vbox_pid, 2860)
        self.assertEqual(instance.width, 1920)
        self.assertEqual(instance.height, 1080)
        self.assertEqual(instance.dpi, 280)

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
        self.assertEqual(instances[0].top_window, 4523898)
        self.assertEqual(instances[0].width, 1920)
        self.assertEqual(instances[0].height, 1080)
        self.assertEqual(instances[0].dpi, 280)

    def test_skips_malformed_rows(self) -> None:
        output = "garbage\n0,LDPlayer,1,2,1,3,4,1920,1080,280\n"
        instances = parse_instance_list(output)
        self.assertEqual(len(instances), 1)

    def test_ignores_blank_lines(self) -> None:
        self.assertEqual(parse_instance_list("\n\n"), [])


class TestSelectInstance(unittest.TestCase):
    """Verify the emulator instance to drive is picked correctly."""

    def _instances(self) -> list[LDPlayerInstance]:
        return [
            LDPlayerInstance(0, "A", 1, 2, False, -1, -1, 1920, 1080, 280),
            LDPlayerInstance(1, "B", 3, 4, True, 23400, 2860, 1920, 1080, 280),
        ]

    def test_picks_the_running_instance(self) -> None:
        instance = select_instance(self._instances())
        self.assertEqual(instance.index, 1)

    def test_honours_an_explicit_index(self) -> None:
        instances = [
            LDPlayerInstance(0, "A", 1, 2, True, 100, 200, 1920, 1080, 280),
            LDPlayerInstance(1, "B", 3, 4, True, 300, 400, 1920, 1080, 280),
        ]
        instance = select_instance(instances, index=1)
        self.assertEqual(instance.index, 1)

    def test_rejects_an_index_that_does_not_exist(self) -> None:
        with self.assertRaises(LDOpenGLError):
            select_instance(self._instances(), index=7)

    def test_rejects_an_explicit_index_that_is_not_running(self) -> None:
        with self.assertRaises(LDOpenGLError):
            select_instance(self._instances(), index=0)

    def test_reports_when_nothing_is_running(self) -> None:
        with self.assertRaises(LDOpenGLError):
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


class TestBackendFallback(unittest.TestCase):
    """Verify the capture backends fall back to each other."""

    def test_rejects_an_unknown_backend(self) -> None:
        with self.assertRaises(ValueError):
            create_game_interface("nonsense")

    def test_native_falls_back_to_adb(self) -> None:
        adb_interface = MagicMock()
        with patch(
            "rok_automation.game.backends._native_interface",
            side_effect=LDOpenGLError("no capture library"),
        ), patch(
            "rok_automation.game.backends._adb_interface",
            return_value=adb_interface,
        ) as adb_build:
            result = create_game_interface(BACKEND_NATIVE)
        self.assertIs(result, adb_interface)
        adb_build.assert_called_once()

    def test_native_falls_back_to_the_desktop_window(self) -> None:
        desktop_interface = MagicMock()
        with patch(
            "rok_automation.game.backends._native_interface",
            side_effect=LDOpenGLError("no capture library"),
        ), patch(
            "rok_automation.game.backends._adb_interface",
            side_effect=RuntimeError("no adb"),
        ), patch(
            "rok_automation.game.backends._desktop_interface",
            return_value=desktop_interface,
        ):
            result = create_game_interface(BACKEND_NATIVE)
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
            result = create_game_interface(BACKEND_ADB)
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
            result = create_game_interface(BACKEND_ADB)
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
                create_game_interface(BACKEND_ADB)


if __name__ == "__main__":
    unittest.main()
