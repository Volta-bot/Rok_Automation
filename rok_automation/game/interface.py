"""Game-interface abstraction shared by the automation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class GameInterface(Protocol):
    """The small surface the automation controller needs from a game client."""

    timing: TimingConfig

    def screenshot(self) -> Path:
        """Return a frame handle; use load_color/gray_frame or save_frame."""
        ...

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

    def zoom_out_city(self) -> None: ...


# A capture plus a template match takes about this long. It is the
# reference the timeout scale is measured against.
_TYPICAL_DETECT_TIME = 0.060
_REFERENCE_POLL_TIME = 0.081 + 0.040


@dataclass(frozen=True)
class TimingConfig:
    click_delay: float = 0.015
    jitter_min: float = 0.0
    jitter_max: float = 0.0
    poll_interval: float = 0.040
    transition_timeout: float = 12.0
    # How long a single capture takes. The native path is measured at
    # about 21 ms; a slower capture scales the screen-wait budgets to
    # match so the controller never misses a transition.
    capture_latency: float = 0.021
    # Duration of a swipe gesture, in seconds.
    drag_duration: float = 0.30

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


