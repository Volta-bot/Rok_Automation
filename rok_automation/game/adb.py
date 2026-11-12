"""ADB-backed emulator control.

Captures the emulator's own framebuffer and sends input through the
Android input pipeline. Because nothing goes through the Windows desktop,
the emulator window may be minimised, hidden, or covered by another
window, and the host mouse cursor is never moved.

Frames are returned in the same canonical 1280x720 size the LDPlayer
backend uses, so every detector, template and OCR region behaves
identically whichever backend produced the frame.
"""

from __future__ import annotations

import os
import random
import shutil
import statistics
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

import cv2
import numpy as np

from rok_automation.game.interface import TimingConfig
from rok_automation.game.ldplayer import CANONICAL_CAPTURE_SIZE, compute_capture_crop
from rok_automation.game.pause import wait_while_paused
from rok_automation.vision.screen import cache_captured_frame, load_gray_frame


# The LDPlayer framebuffer arrives as BGRA bytes, matching OpenCV's order.
_FRAME_PIXEL_ORDER = "BGRA"
# Android's raw screencap header: width, height, format, colour space.
_SCREENCAP_HEADER_SIZE = 16
_FORMAT_RGBA_8888 = 1


class AdbError(RuntimeError):
    """Raised when the emulator cannot be reached over ADB."""


def find_adb_executable() -> str:
    """Locate an adb client, preferring LDPlayer's own bundled copy."""

    override = os.environ.get("ROK_ADB_PATH")
    if override and Path(override).is_file():
        return override

    on_path = shutil.which("adb")
    if on_path:
        return on_path

    candidates: list[Path] = []
    for base in (Path("C:/LDPlayer"), Path("C:/Program Files/LDPlayer"),
                 Path("C:/Program Files (x86)/LDPlayer")):
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


def find_adb_device(adb_path: str) -> str:
    """Return the serial of the single connected emulator."""

    result = subprocess.run(
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
            "connection in the LDPlayer settings and restart the emulator."
        )
    # Emulator serials look like emulator-5554; prefer one of those.
    for device in devices:
        if device.startswith("emulator-"):
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
        result = subprocess.run(
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


def parse_screencap(data: bytes) -> np.ndarray:
    """Decode a raw ADB screencap into a BGR frame."""

    if len(data) < _SCREENCAP_HEADER_SIZE:
        raise AdbError("The emulator returned a truncated screen capture.")
    width = int.from_bytes(data[0:4], "little")
    height = int.from_bytes(data[4:8], "little")
    fmt = int.from_bytes(data[8:12], "little")
    if width <= 0 or height <= 0:
        raise AdbError("The emulator reported an empty screen size.")

    expected = width * height * 4
    pixels = data[_SCREENCAP_HEADER_SIZE : _SCREENCAP_HEADER_SIZE + expected]
    if len(pixels) < expected:
        raise AdbError(
            f"The emulator returned {len(pixels)} of {expected} screen bytes."
        )
    frame = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4)
    if fmt == _FORMAT_RGBA_8888 and _FRAME_PIXEL_ORDER == "RGBA":
        return cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
    return np.ascontiguousarray(frame[:, :, :3])


class AdbInterface:
    """Drive the emulator through ADB instead of the Windows desktop."""

    def __init__(
        self,
        *,
        adb_path: str | None = None,
        serial: str | None = None,
        screenshot_path: Path = Path("artifacts/ldplayer_adb.png"),
        timing: TimingConfig = TimingConfig(),
        pause_flag_path: Path | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.adb_path = adb_path or find_adb_executable()
        self.serial = serial or find_adb_device(self.adb_path)
        self.screenshot_path = screenshot_path
        self.timing = timing
        self.pause_flag_path = pause_flag_path
        self.log = log
        self._guest_size = self._read_guest_size()
        self._measure_latency()

    # -- setup ---------------------------------------------------------- #

    def _read_guest_size(self) -> tuple[int, int]:
        """Read the guest display size, which is what input coordinates use."""

        output = _run(self.adb_path, self.serial, ["shell", "wm", "size"]).decode(
            "utf-8", "replace"
        )
        for line in output.splitlines():
            if "Physical size" in line:
                size = line.split(":", 1)[1].strip()
                width, height = (int(part) for part in size.split("x")[:2])
                return width, height
        raise AdbError(f"Could not read the emulator display size: {output.strip()}")

    def _measure_latency(self) -> None:
        """Time a few captures so the timeouts can match the real speed."""

        samples = []
        for _ in range(3):
            started = time.perf_counter()
            self._capture_raw()
            samples.append(time.perf_counter() - started)
        latency = statistics.median(samples)
        self.timing = replace(self.timing, capture_latency=latency)
        if self.log is not None:
            self.log(
                f"ADB capture latency {latency * 1000:.0f} ms; "
                f"screen timeouts scaled {self.timeout_scale:.1f}x"
            )

    @property
    def timeout_scale(self) -> float:
        """How much slower a frame is than the desktop capture path."""

        return self.timing.timeout_scale

    # -- capture -------------------------------------------------------- #

    def _capture_raw(self) -> np.ndarray:
        data = _run(self.adb_path, self.serial, ["exec-out", "screencap"], timeout=90.0)
        return parse_screencap(data)

    def screenshot(self) -> Path:
        """Capture the emulator and store a canonical 1280x720 frame."""

        frame = self._capture_raw()
        normalized = self._normalize(frame)
        destination = self.screenshot_path.expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved = destination.resolve()
        cv2.imwrite(str(resolved), normalized)
        cache_captured_frame(resolved, normalized)
        return resolved

    def _normalize(self, frame: np.ndarray) -> np.ndarray:
        """Scale a guest frame down to the canonical detection size."""

        from rok_automation.game.ldplayer import normalize_capture

        return normalize_capture(frame)

    # -- input ---------------------------------------------------------- #

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
        *,
        offset_range: int | None = None,
    ) -> None:
        """Tap the centre of a control, with a small random offset."""

        frame = load_gray_frame(screenshot_path)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {screenshot_path}")
        x, y, width, height = bounds
        ref_x = x + width // 2
        ref_y = y + height // 2

        max_limit = (
            offset_range
            if offset_range is not None
            else self.timing.click_offset_max
        )
        max_ox = min(max_limit, max(1, width // 6)) if width > 2 else min(max_limit, 1)
        max_oy = min(max_limit, max(1, height // 6)) if height > 2 else min(max_limit, 1)

        offset_x = random.randint(-max_ox, max_ox) if max_ox > 0 else 0
        offset_y = random.randint(-max_oy, max_oy) if max_oy > 0 else 0

        target_x = max(0, min(frame.shape[1] - 1, ref_x + offset_x))
        target_y = max(0, min(frame.shape[0] - 1, ref_y + offset_y))

        guest_x, guest_y = self._to_guest(target_x, target_y)
        _run(
            self.adb_path,
            self.serial,
            ["shell", "input", "tap", str(guest_x), str(guest_y)],
            timeout=30.0,
        )
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
        _run(
            self.adb_path,
            self.serial,
            [
                "shell",
                "input",
                "swipe",
                str(start_x),
                str(start_y),
                str(end_x),
                str(end_y),
                str(int(self.timing.drag_duration * 1000)),
            ],
            timeout=60.0,
        )
        self.wait(self.timing.click_delay)

    def wait(self, seconds: float) -> None:
        """Sleep, honouring a pause request from the user."""

        wait_while_paused(self.pause_flag_path, self.log)
        time.sleep(max(0.0, seconds))
