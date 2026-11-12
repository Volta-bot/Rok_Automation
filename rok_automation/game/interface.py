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
from rok_automation.vision.screen import load_gray_frame


class GameInterface(Protocol):
    """The small surface the automation controller needs from a game client."""

    timing: TimingConfig

    def screenshot(self) -> Path: ...

    def click(
        self,
        bounds: tuple[int, int, int, int],
        screenshot_path: str | Path,
        *,
        offset_range: int | None = None,
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
    click_delay: float = 0.015
    jitter_min: float = 0.0
    jitter_max: float = 0.0
    poll_interval: float = 0.040
    transition_timeout: float = 12.0
    click_offset_max: int = 3


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
        *,
        offset_range: int | None = None,
    ) -> None:
        frame = load_gray_frame(screenshot_path)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {screenshot_path}")
        x, y, width, height = bounds
        ref_x = x + width // 2
        ref_y = y + height // 2

        # Apply a small, randomized offset to target coordinates (both X and Y)
        # while keeping strictly inside the intended button/element bounds.
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

        click_ldplayer_point(
            self.window,
            (target_x, target_y),
            frame.shape,
            offset_range=0,
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
        frame = load_gray_frame(screenshot_path)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {screenshot_path}")
        drag_ldplayer_points(self.window, start, end, frame.shape)
        self.wait(self.timing.click_delay)

    @staticmethod
    def wait(seconds: float) -> None:
        time.sleep(max(0.0, seconds))
