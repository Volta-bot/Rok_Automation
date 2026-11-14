"""ADB-backed emulator input.

Sends input through the Android input pipeline. Because nothing goes
through the Windows desktop, the emulator window may be minimised,
hidden, or covered by another window, and the host mouse cursor is never
moved.

Screenshot capture uses the same ADB connection.
"""

from __future__ import annotations

import math
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Sequence

from rok_automation.game.emulators import EmulatorFamily
from rok_automation.game.frame import CANONICAL_CAPTURE_SIZE, compute_capture_crop
from rok_automation.game.interface import TimingConfig
from rok_automation.game.pause import wait_while_paused
from rok_automation.game.process import run_hidden
from rok_automation.vision.screen import load_gray_frame
from rok_automation.performance import record_event, timed


class AdbError(RuntimeError):
    """Raised when the emulator cannot be reached over ADB."""


def find_adb_executable(emulator: "EmulatorFamily | None" = None) -> str:
    """Locate an adb client, preferring the emulator's own bundled copy."""

    override = os.environ.get("ROK_ADB_PATH")
    if override and Path(override).is_file():
        return override

    if emulator is not None:
        for candidate in emulator.adb_executables():
            return str(candidate)

    on_path = shutil.which("adb")
    if on_path:
        return on_path

    candidates: list[Path] = []
    for base in (Path("C:/Program Files/Netease/MuMuPlayer-12.0/shell"),):
        if not base.is_dir():
            continue
        candidates.append(base / "adb.exe")
        candidates.extend(sorted(base.glob("*/adb.exe")))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    raise AdbError(
        "Could not find an adb executable. Install it, put it on PATH, or "
        "set ROK_ADB_PATH to its full path."
    )


def find_adb_device(
    adb_path: str,
    preferred: Sequence[str] = (),
) -> str:
    """Return the serial of the connected emulator.

    Serials the selected emulator is expected to use are preferred, then
    the usual ``emulator-5554`` form, then a loopback address.
    """

    result = run_hidden(
        [adb_path, "devices"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    devices: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if not devices:
        raise AdbError(
            "No ADB device is connected. Enable 'ADB debugging' with a local "
            "connection in the emulator settings and restart it."
        )
    for serial in preferred:
        if serial in devices:
            return serial
    # Emulator serials look like emulator-5554; prefer one of those.
    for device in devices:
        if device.startswith("emulator-"):
            return device
    # MuMu and other emulators appear as a loopback address.
    for device in devices:
        if device.startswith("127.0.0.1:"):
            return device
    return devices[0]


def _run(
    adb_path: str,
    serial: str,
    args: Sequence[str],
    *,
    timeout: float = 60.0,
) -> bytes:
    """Run an adb command and return its raw stdout."""

    command = [adb_path, "-s", serial, *args]
    try:
        result = run_hidden(
            command,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb command timed out: {' '.join(args)}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise AdbError(f"adb {' '.join(args)} failed: {message}")
    return result.stdout


def _run_global(
    adb_path: str,
    args: Sequence[str],
    *,
    timeout: float = 60.0,
) -> bytes:
    """Run an adb command that is not bound to a device."""

    try:
        result = run_hidden(
            [adb_path, *args],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb command timed out: {' '.join(args)}") from exc
    return result.stdout


def swipe_duration_ms(distance: float, base_duration: float) -> int:
    """Return how long a swipe should last for the distance it covers.

    A drag that is too quick is read by the game as a flick, and on a busy
    emulator sometimes as a plain tap. The gesture is therefore stretched
    with the distance it covers so it always registers as a drag.
    """

    return int(min(1200.0, max(base_duration * 1000.0, distance * 0.8)))


class AdbInput:
    """Deliver taps and drags to the emulator over ADB.

    Input travels through the Android input pipeline, so the host mouse
    cursor is never moved and the emulator window may be minimised,
    hidden or covered while the run continues.

    The native capture backend reads frames from the emulator's own
    renderer but sends its input here.
    """

    def __init__(
        self,
        *,
        adb_path: str | None = None,
        serial: str | None = None,
        emulator: EmulatorFamily | None = None,
        guest_size: tuple[int, int] | None = None,
        timing: TimingConfig = TimingConfig(),
        pause_flag_path: Path | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.emulator = emulator
        self.adb_path = adb_path or find_adb_executable(emulator)
        self.serial = serial or find_adb_device(
            self.adb_path,
            preferred=emulator.preferred_serials() if emulator is not None else (),
        )
        self.timing = timing
        self.pause_flag_path = pause_flag_path
        self.log = log
        self._guest_size = tuple(guest_size) if guest_size else self._read_guest_size()

    # -- setup ---------------------------------------------------------- #

    def _read_guest_size(self) -> tuple[int, int]:
        """Read the guest display size, which is what input coordinates use."""

        output = _run(self.adb_path, self.serial, ["shell", "wm", "size"]).decode(
            "utf-8", "replace"
        )
        physical: tuple[int, int] | None = None
        override: tuple[int, int] | None = None
        for line in output.splitlines():
            if "Physical size" in line:
                physical = self._parse_size(line)
            elif "Override size" in line:
                override = self._parse_size(line)
        # An override is the size the guest actually draws at, so it wins.
        size = override or physical
        if size is None:
            raise AdbError(f"Could not read the emulator display size: {output.strip()}")
        return size

    @staticmethod
    def _parse_size(line: str) -> tuple[int, int] | None:
        """Read a ``Physical size: 1920x1080`` style line."""

        try:
            value = line.split(":", 1)[1].strip()
            width, height = (int(part) for part in value.split("x")[:2])
        except (IndexError, ValueError):
            return None
        return width, height

    @property
    def timeout_scale(self) -> float:
        """How much slower a frame is than the reference capture speed."""

        return self.timing.timeout_scale

    # -- recovery ------------------------------------------------------- #

    def adb_port(self) -> int | None:
        """Return the local TCP port the emulator's adb listens on.

        ADB serials may be loopback addresses or emulator identifiers.
        """

        serial = self.serial
        if serial.startswith("127.0.0.1:"):
            try:
                return int(serial.rsplit(":", 1)[1])
            except ValueError:
                return None
        if serial.startswith("emulator-"):
            try:
                return int(serial.rsplit("-", 1)[1]) + 1
            except ValueError:
                return None
        return None

    def reconnect(self) -> None:
        """Bring the adb connection back after the device went stale.

        Emulators sometimes stop answering adb until the connection is
        re-established, which shows up as a command that never returns.
        """

        port = self.adb_port()
        try:
            if port is not None:
                _run_global(self.adb_path, ["connect", f"127.0.0.1:{port}"], timeout=30)
            else:
                _run_global(self.adb_path, ["reconnect"], timeout=30)
        except AdbError:
            pass

        # Give the device a moment to come back before it is used again.
        for _ in range(10):
            time.sleep(0.5)
            try:
                devices = _run(self.adb_path, self.serial, ["get-state"], timeout=10)
            except AdbError:
                continue
            if b"device" in devices:
                return

    # -- input ---------------------------------------------------------- #

    @timed("adb_input")
    def _input(self, args: Sequence[str], *, timeout: float = 30.0) -> None:
        """Send an input command, reconnecting once if the link went stale."""

        try:
            _run(self.adb_path, self.serial, args, timeout=timeout)
        except AdbError:
            record_event("adb_retry")
            if self.log is not None:
                self.log(f"ADB input failed ({' '.join(args)}); reconnecting.")
            self.reconnect()
            _run(self.adb_path, self.serial, args, timeout=timeout)

    def _to_guest(
        self,
        x: float,
        y: float,
    ) -> tuple[int, int]:
        """Map a canonical-frame point to guest display pixels.

        The point is treated as the centre of a canonical pixel, so the
        mapping stays accurate at both edges of the frame.
        """

        guest_width, guest_height = self._guest_size
        crop_left, crop_top, crop_width, crop_height = compute_capture_crop(
            guest_width,
            guest_height,
        )
        canonical_width, canonical_height = CANONICAL_CAPTURE_SIZE
        guest_x = crop_left + (x + 0.5) * crop_width / canonical_width - 0.5
        guest_y = crop_top + (y + 0.5) * crop_height / canonical_height - 0.5
        guest_x = max(0, min(guest_width - 1, int(round(guest_x))))
        guest_y = max(0, min(guest_height - 1, int(round(guest_y))))
        return guest_x, guest_y

    def click(
        self,
        bounds: tuple[int, int, int, int],
        screenshot_path: str | Path,
    ) -> None:
        """Tap the centre of a control."""

        frame = load_gray_frame(screenshot_path)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {screenshot_path}")
        x, y, width, height = bounds
        target_x = x + width // 2
        target_y = y + height // 2
        target_x = max(0, min(frame.shape[1] - 1, target_x))
        target_y = max(0, min(frame.shape[0] - 1, target_y))

        guest_x, guest_y = self._to_guest(target_x, target_y)
        self._input(["shell", "input", "tap", str(guest_x), str(guest_y)])
        self.wait(
            self.timing.click_delay
            + random.uniform(self.timing.jitter_min, self.timing.jitter_max)
        )

    def drag(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        screenshot_path: str | Path,
    ) -> None:
        """Swipe between two canonical-frame points."""

        frame = load_gray_frame(screenshot_path)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {screenshot_path}")
        start_x, start_y = self._to_guest(*start)
        end_x, end_y = self._to_guest(*end)

        # A drag that is too quick is read by the game as a flick, and on a
        # busy emulator sometimes as a plain tap.
        distance = math.hypot(end_x - start_x, end_y - start_y)
        duration_ms = swipe_duration_ms(distance, self.timing.drag_duration)

        self._input(
            [
                "shell",
                "input",
                "swipe",
                str(start_x),
                str(start_y),
                str(end_x),
                str(end_y),
                str(duration_ms),
            ],
            timeout=60.0,
        )
        self.wait(self.timing.click_delay)

    def zoom_out_city(self) -> None:
        """Send one short two-finger pinch to the emulator touchscreen."""
        listing = _run(self.adb_path, self.serial, ["shell", "getevent", "-pl"]).decode("utf-8", "replace")
        devices = re.split(r"(?=add device \d+: )", listing)
        selected = next(((m.group(1), block) for block in devices
                       if all(axis in block for axis in
                              ("ABS_MT_SLOT", "ABS_MT_TRACKING_ID", "ABS_MT_POSITION_X", "ABS_MT_POSITION_Y"))
                       and (m := re.search(r"add device \d+: (/dev/input/event\d+)", block))), None)
        if selected is None:
            raise AdbError("A multitouch input device is unavailable")
        device, block = selected
        # Linux touch axes follow the physical panel, while _to_guest returns
        # coordinates in the rotated Android display. Convert back to raw axes.
        def axis_range(axis: str) -> tuple[int, int] | None:
            match = re.search(rf"{axis}\s*:\s*value\s+-?\d+,\s*min\s+(-?\d+),\s*max\s+(-?\d+)", block)
            return (int(match.group(1)), int(match.group(2))) if match else None

        raw_x, raw_y = axis_range("ABS_MT_POSITION_X"), axis_range("ABS_MT_POSITION_Y")
        rotation = 0
        if raw_x and raw_y:
            dump = _run(self.adb_path, self.serial, ["shell", "dumpsys", "input"]).decode("utf-8", "replace")
            match = re.search(r"Viewport INTERNAL:.*?orientation=(\d)", dump)
            if match:
                rotation = int(match.group(1))

        def raw_point(x: int, y: int) -> tuple[int, int]:
            gx, gy = self._to_guest(x, y)
            width, height = self._guest_size
            u, v = gx / max(width - 1, 1), gy / max(height - 1, 1)
            if rotation == 1:
                u, v = 1 - v, u
            elif rotation == 2:
                u, v = 1 - u, 1 - v
            elif rotation == 3:
                u, v = v, 1 - u
            if raw_x and raw_y:
                return (round(raw_x[0] + u * (raw_x[1] - raw_x[0])),
                        round(raw_y[0] + v * (raw_y[1] - raw_y[0])))
            return gx, gy

        left = raw_point(530, 360)
        right = raw_point(750, 360)
        end_left = raw_point(555, 360)
        end_right = raw_point(725, 360)
        events = [
            (3, 47, 0), (3, 57, 101), (3, 53, left[0]), (3, 54, left[1]),
            (3, 47, 1), (3, 57, 102), (3, 53, right[0]), (3, 54, right[1]),
            (0, 0, 0),
        ]
        commands = [f"sendevent {device} {a} {b} {c}" for a, b, c in events]
        commands.append("sleep 0.1")
        for step in range(1, 6):
            x1, y1 = (round(left[i] + (end_left[i] - left[i]) * step / 5) for i in (0, 1))
            x2, y2 = (round(right[i] + (end_right[i] - right[i]) * step / 5) for i in (0, 1))
            commands.extend(f"sendevent {device} {a} {b} {c}" for a, b, c in
                            [(3, 47, 0), (3, 53, x1), (3, 54, y1), (3, 47, 1), (3, 53, x2), (3, 54, y2), (0, 0, 0)])
            commands.append("sleep 0.05")
        commands.extend(f"sendevent {device} {a} {b} {c}" for a, b, c in
                        [(3, 47, 0), (3, 57, -1), (3, 47, 1), (3, 57, -1), (0, 0, 0)])
        self._input(["shell", "sh", "-c", "set -e; " + "; ".join(commands)])

    @timed("wait")
    def wait(self, seconds: float) -> None:
        """Sleep, honouring a pause request from the user."""

        wait_while_paused(self.pause_flag_path, self.log)
        time.sleep(max(0.0, seconds))


