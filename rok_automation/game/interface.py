"""Game-interface abstraction and visible-window LDPlayer implementation."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2

from rok_automation.game.ldplayer import (
    EmulatorWindow,
    capture_ldplayer_window,
    click_ldplayer_point,
    drag_ldplayer_points,
)


class GameInterface(Protocol):
    """The small surface the automation controller needs from a game client."""

    timing: TimingConfig

    def screenshot(self) -> Path: ...

    def click(
        self,
        bounds: tuple[int, int, int, int],
        screenshot_path: str | Path,
    ) -> None: ...

    def drag(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        screenshot_path: str | Path,
    ) -> None: ...

    def wait(self, seconds: float) -> None: ...


@dataclass(frozen=True)
class TimingConfig:
    click_delay: float = 0.040
    jitter_min: float = 0.0
    jitter_max: float = 0.0
    poll_interval: float = 0.120
    transition_timeout: float = 12.0


class LDPlayerInterface:
    """Capture and click controls in a visible LDPlayer render window."""

    def __init__(
        self,
        window: EmulatorWindow,
        *,
        screenshot_path: Path = Path("artifacts/ldplayer.png"),
        timing: TimingConfig = TimingConfig(),
    ) -> None:
        self.window = window
        self.screenshot_path = screenshot_path
        self.timing = timing

    def screenshot(self) -> Path:
        return capture_ldplayer_window(self.window, self.screenshot_path)

    def click(
        self,
        bounds: tuple[int, int, int, int],
        screenshot_path: str | Path,
    ) -> None:
        frame = cv2.imread(str(screenshot_path), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {screenshot_path}")
        x, y, width, height = bounds
        click_ldplayer_point(
            self.window,
            (x + width // 2, y + height // 2),
            frame.shape,
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
        frame = cv2.imread(str(screenshot_path), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {screenshot_path}")
        drag_ldplayer_points(self.window, start, end, frame.shape)
        self.wait(self.timing.click_delay)

    @staticmethod
    def wait(seconds: float) -> None:
        time.sleep(max(0.0, seconds))
