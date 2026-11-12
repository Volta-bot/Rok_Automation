"""Game-interface abstraction and the visible-window desktop backend."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import cv2

from rok_automation.game.desktop import (
    EmulatorWindow,
    capture_emulator_window,
    click_window_point,
    drag_window_points,
)
from rok_automation.game.pause import wait_while_paused
from rok_automation.vision.screen import load_gray_frame


def jittered_click_point(
    bounds: tuple[int, int, int, int],
    frame_shape: tuple[int, ...],
    offset_range: int,
) -> tuple[int, int]:
    """Return the point to click for a control, nudged off its exact centre.

    A small randomized offset on both axes keeps repeated clicks from
    landing on the very same pixel. The offset is clamped so the point
    always stays inside the control it belongs to, however small that
    control is.
    """

    frame_height, frame_width = frame_shape[0], frame_shape[1]
    x, y, width, height = bounds
    ref_x = x + width // 2
    ref_y = y + height // 2

    max_ox = min(offset_range, max(1, width // 6)) if width > 2 else min(offset_range, 1)
    max_oy = min(offset_range, max(1, height // 6)) if height > 2 else min(offset_range, 1)

    offset_x = random.randint(-max_ox, max_ox) if max_ox > 0 else 0
    offset_y = random.randint(-max_oy, max_oy) if max_oy > 0 else 0

    # Clamp into the control first, so a tiny button cannot be missed.
    target_x = max(x, min(x + max(width - 1, 0), ref_x + offset_x))
    target_y = max(y, min(y + max(height - 1, 0), ref_y + offset_y))
    # Then keep it inside the frame.
    target_x = max(0, min(frame_width - 1, target_x))
    target_y = max(0, min(frame_height - 1, target_y))
    return target_x, target_y


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


# A capture plus a template match takes about this long on the desktop
# capture path. It is the reference the timeout scale is measured against.
_TYPICAL_DETECT_TIME = 0.060
_REFERENCE_POLL_TIME = 0.081 + 0.040


@dataclass(frozen=True)
class TimingConfig:
    click_delay: float = 0.015
    jitter_min: float = 0.0
    jitter_max: float = 0.0
    poll_interval: float = 0.040
    transition_timeout: float = 12.0
    click_offset_max: int = 3
    # How long a single capture takes. The desktop path is measured at about
    # 21 ms; the ADB path is much slower, so the screen-wait budgets are
    # scaled to match and the controller never misses a transition.
    capture_latency: float = 0.021
    # Duration of a swipe gesture, in seconds.
    drag_duration: float = 0.30
    # How long a single adb capture may take before the connection is
    # considered stale and is re-established.
    capture_timeout: float = 25.0

    @property
    def frame_time(self) -> float:
        """Approximate seconds for one capture plus detection cycle."""

        return self.capture_latency + _TYPICAL_DETECT_TIME

    @property
    def poll_iteration_time(self) -> float:
        """Seconds for one poll: capture, detect, then pause."""

        return self.frame_time + self.poll_interval

    @property
    def timeout_scale(self) -> float:
        """Multiplier that keeps the number of poll attempts constant.

        A slower capture means each poll costs more wall-clock time, so
        the screen-wait budgets are scaled to match. Without this the
        controller would only get one or two samples of a transition and
        would time out before the screen had finished changing.
        """

        return max(1.0, self.poll_iteration_time / _REFERENCE_POLL_TIME)

    def scaled(self, seconds: float) -> float:
        """Scale a screen-wait budget for the current capture speed."""

        return seconds * self.timeout_scale


class DesktopInterface:
    """Capture and click controls in a visible emulator render window."""

    def __init__(
        self,
        window: EmulatorWindow,
        *,
        screenshot_path: Path = Path("artifacts/emulator_desktop.png"),
        timing: TimingConfig = TimingConfig(),
        pause_flag_path: Path | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.window = window
        self.screenshot_path = screenshot_path
        self.timing = timing
        self.pause_flag_path = pause_flag_path
        self.log = log

    def screenshot(self) -> Path:
        return capture_emulator_window(self.window, self.screenshot_path)

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

        # Apply a small, randomized offset to target coordinates (both X and Y)
        # while keeping strictly inside the intended button/element bounds.
        target_x, target_y = jittered_click_point(
            bounds,
            frame.shape[:2],
            self.timing.click_offset_max if offset_range is None else offset_range,
        )

        click_window_point(
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
        drag_window_points(self.window, start, end, frame.shape)
        self.wait(self.timing.click_delay)

    def wait(self, seconds: float) -> None:
        """Sleep, honouring a pause request from the user.

        Every wait in the automation funnels through here, so blocking
        here freezes the whole run without interrupting an in-flight
        click or OCR pass.
        """

        wait_while_paused(self.pause_flag_path, self.log)
        time.sleep(max(0.0, seconds))
