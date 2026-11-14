"""Capture MuMu frames and send input over ADB."""

from __future__ import annotations

import statistics
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from rok_automation.game.adb import AdbError, AdbInput, _run, _run_global, find_adb_executable
from rok_automation.game.emulators import (
    EmulatorError,
    EmulatorFamily,
    EmulatorInstance,
    select_family,
)
from rok_automation.game.frame import normalize_capture
from rok_automation.game.interface import TimingConfig
from rok_automation.vision.screen import cache_captured_frame
from rok_automation.performance import timed


class AdbFrameSource:
    """Capture the Android display through the selected ADB device."""

    def __init__(self, adb_path: str, serial: str) -> None:
        self.adb_path = adb_path
        self.serial = serial

    def capture(self):
        data = _run(self.adb_path, self.serial, ["exec-out", "screencap", "-p"], timeout=25)
        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise AdbError("ADB returned an invalid screenshot. Check that MuMu is running.")
        return frame


class FallbackFrameSource:
    """Use the verified renderer until it fails, then continue over ADB."""

    def __init__(self, primary, fallback: AdbFrameSource, log=None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.log = log

    def capture(self):
        if self.primary is not None:
            try:
                return self.primary.capture()
            except (EmulatorError, OSError, ValueError, cv2.error) as exc:
                if self.log is not None:
                    self.log(f"Native capture failed ({exc}); switching to ADB screenshots")
                self.primary.close()
                self.primary = None
        return self.fallback.capture()


def _matching_displays(native_frame, adb_frame) -> bool:
    """Reject a different display, orientation or colour order before tapping."""
    if native_frame.shape != adb_frame.shape or native_frame.ndim != 3:
        return False
    native_small = cv2.resize(native_frame, (160, 90), interpolation=cv2.INTER_AREA)
    adb_small = cv2.resize(adb_frame, (160, 90), interpolation=cv2.INTER_AREA)
    return float(np.mean(cv2.absdiff(native_small, adb_small))) < 25.0


class NativeInterface(AdbInput):
    """Capture frames and send input over ADB."""

    def __init__(
        self,
        *,
        source,
        instance: EmulatorInstance,
        emulator: EmulatorFamily | None = None,
        adb_path: str | None = None,
        serial: str | None = None,
        screenshot_path: Path = Path("artifacts/emulator_native.png"),
        timing: TimingConfig = TimingConfig(),
        pause_flag_path: Path | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.source = source
        self.instance = instance
        super().__init__(
            emulator=emulator,
            adb_path=adb_path,
            serial=serial,
            timing=timing,
            pause_flag_path=pause_flag_path,
            log=log,
        )
        self.screenshot_path = screenshot_path
        self._measure_latency()

    def _measure_latency(self) -> None:
        """Time a few captures so the timeouts match the real speed."""

        samples = []
        for _ in range(3):
            started = time.perf_counter()
            self.source.capture()
            samples.append(time.perf_counter() - started)
        latency = statistics.median(samples)
        self.timing = replace(self.timing, capture_latency=latency)
        if self.log is not None:
            self.log(
                f"Capture latency {latency * 1000:.0f} ms; "
                f"screen timeouts scaled {self.timeout_scale:.1f}x"
            )

    @timed("capture")
    def screenshot(self) -> Path:
        """Capture into memory; the returned path is a handle for frame loaders.

        Use vision.screen.save_frame to materialize a diagnostic PNG.
        Like the old screenshot file, this handle refers to the latest capture.
        """

        was_native = isinstance(self.source, FallbackFrameSource) and self.source.primary is not None
        started = time.perf_counter()
        frame = self.source.capture()
        if was_native and self.source.primary is None:
            self.timing = replace(self.timing, capture_latency=time.perf_counter() - started)
            if self.log is not None:
                self.log(f"Screen timeouts scaled {self.timeout_scale:.1f}x for ADB capture")
        # ADB input follows the display orientation, unlike `wm size`.
        height, width = frame.shape[:2]
        self._guest_size = (width, height)
        normalized = normalize_capture(frame)
        destination = self.screenshot_path.expanduser()
        resolved = destination.resolve()
        cache_captured_frame(resolved, normalized, memory_only=True)
        return resolved


def create_native_interface(
    *,
    emulator: str | None = None,
    index: int | None = None,
    adb_path: str | None = None,
    serial: str | None = None,
    timing: TimingConfig | None = None,
    pause_flag_path: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> NativeInterface:
    """Open the emulator ADB connection and return the interface.

    The emulator family is decided by the family registry, which is
    looked up in :mod:`rok_automation.game.emulators`.
    """

    timing = timing or TimingConfig()
    family, saved_index = select_family(emulator)
    if index is None:
        index = saved_index

    instances = family.discover()
    instance = next((item for item in instances if item.running and
                     (index is None or item.index == index)), None)
    if instance is None:
        raise AdbError("No matching running MuMu instance was found. Start MuMu and enable ADB.")
    adb_path = adb_path or find_adb_executable(family)
    if serial is None:
        serial = f"127.0.0.1:{instance.details['adb_port']}"
        _run_global(adb_path, ["connect", serial], timeout=25)
    adb_source = AdbFrameSource(adb_path, serial)
    source = adb_source
    native_source = None
    try:
        native_source = family.open_capture(instance)
        if not _matching_displays(native_source.capture(), adb_source.capture()):
            raise EmulatorError("native and ADB frames do not match")
        source = FallbackFrameSource(native_source, adb_source, log)
        if log is not None:
            log("Using verified MuMu native renderer capture")
    except (EmulatorError, OSError, ValueError, cv2.error) as exc:
        if native_source is not None:
            native_source.close()
        if log is not None:
            log(f"MuMu native capture unavailable ({exc}); using ADB screenshots")

    if log is not None:
        log(
            f"Capture attached to {family.label} '{instance.name}' "
            f"(index {instance.index}, {instance.width}x{instance.height})"
        )

    return NativeInterface(
        source=source,
        instance=instance,
        emulator=family,
        adb_path=adb_path,
        serial=serial,
        screenshot_path=Path("artifacts/emulator_native.png"),
        timing=timing,
        pause_flag_path=pause_flag_path,
        log=log,
    )
