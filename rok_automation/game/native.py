"""The interface that combines a native frame source with ADB input.

Every emulator family that can read its own framebuffer hands back a
:class:`~rok_automation.game.emulators.FrameSource`; this module turns
that into the same :class:`~rok_automation.game.interface.GameInterface`
the automation expects, so the farming loop does not care where the
frames came from.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

import cv2

from rok_automation.game.adb import AdbInput
from rok_automation.game.emulators import EmulatorInstance
from rok_automation.game.frame import normalize_capture
from rok_automation.game.interface import TimingConfig
from rok_automation.vision.screen import cache_captured_frame


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
                f"Native capture latency {latency * 1000:.0f} ms; "
                f"screen timeouts scaled {self.timeout_scale:.1f}x"
            )

    def screenshot(self) -> Path:
        """Capture the emulator and store a canonical 1280x720 frame."""

        frame = self.source.capture()
        normalized = normalize_capture(frame)
        destination = self.screenshot_path.expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved = destination.resolve()
        cv2.imwrite(str(resolved), normalized)
        cache_captured_frame(resolved, normalized)
        return resolved
