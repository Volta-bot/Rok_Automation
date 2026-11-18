"""Shared minimum observation budget for delayed game menus."""

from collections.abc import Callable, Iterator

MIN_SCREEN_RESCANS = 5
MIN_SCREEN_SCANS = 1 + MIN_SCREEN_RESCANS


def screen_scan_attempts(deadline: float, *, clock: Callable[[], float]) -> Iterator[int]:
    """Allow the initial scan plus five fresh rescans, even after timeout.

    Callers capture a new frame each iteration and return early on success.
    This controls observations only; it does not issue or authorize clicks.
    """
    scans = 0
    while clock() < deadline or scans < MIN_SCREEN_SCANS:
        scans += 1
        yield scans
