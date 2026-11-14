"""The interface that reads frames from the emulator's own renderer.

Every emulator family that can read its own framebuffer hands back a
:class:`~rok_automation.game.emulators.FrameSource`; this module turns
that into the same :class:`~rok_automation.game.interface.GameInterface`
the automation expects, so the farming loop does not care where the
frames came from.

Native capture is the only capture path: it reads the emulator's
rendered framebuffer directly, which is roughly an order of magnitude
faster than pulling frames over ADB, and it keeps working when the
emulator window is minimised, hidden or covered. Input still travels
through the Android input pipeline, so the host mouse is never moved.
"""

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
    EmulatorFamily,
    EmulatorInstance,
    select_family,
    select_instance,
)
from rok_automation.game.frame import normalize_capture
from rok_automation.game.interface import TimingConfig
from rok_automation.vision.screen import cache_captured_frame


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


class NativeInterface(AdbInput):
    """Capture frames natively; deliver input over ADB.

    Frames come from the emulator's own renderer, so detection is never
    confused by an overlapping window and no frame ever has to travel
    over ADB. Input still travels through the Android input pipeline,
    which keeps the host mouse untouched.
    """

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

    def screenshot(self) -> Path:
        """Capture the emulator and store a canonical 1280x720 frame."""

        frame = self.source.capture()
        if isinstance(self.source, AdbFrameSource):
            # ADB input uses the current display orientation, while wm size
            # can report the device's unrotated (portrait) dimensions.
            height, width = frame.shape[:2]
            self._guest_size = (width, height)
        normalized = normalize_capture(frame)
        destination = self.screenshot_path.expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved = destination.resolve()
        cv2.imwrite(str(resolved), normalized)
        cache_captured_frame(resolved, normalized)
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
    """Open the emulator's own framebuffer and return the interface.

    The emulator family is decided by the family registry, which is
    looked up in :mod:`rok_automation.game.emulators`.
    """

    timing = timing or TimingConfig()
    family, saved_index = select_family(emulator)
    if index is None:
        index = saved_index

    if family.key == "mumu":
        instances = family.discover()
        instance = next((item for item in instances if item.running and
                         (index is None or item.index == index)), None)
        if instance is None:
            raise AdbError("No matching running MuMu instance was found. Start MuMu and enable ADB.")
        adb_path = adb_path or find_adb_executable(family)
        if serial is None:
            serial = f"127.0.0.1:{instance.details['adb_port']}"
            _run_global(adb_path, ["connect", serial], timeout=25)
        source = AdbFrameSource(adb_path, serial)
    else:
        instances = family.discover()
        instance = select_instance(instances, index, family=family.label or family.key)
        source = family.open_capture(instance)

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
