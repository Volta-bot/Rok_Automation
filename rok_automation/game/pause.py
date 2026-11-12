"""Cooperative pause support for a running automation.

The GUI marks a run as paused by creating a flag file; the automation
worker checks that flag at every wait boundary and blocks until it is
removed. Pausing therefore freezes the run at a well-defined point
(between two actions) instead of interrupting an in-flight click, and
resuming continues from exactly where it left off.

A file is used rather than a signal because the worker runs as a separate
process, and a flag file works the same way on every platform.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PAUSE_FLAG_PATH = _PROJECT_ROOT / "state" / "pause.flag"
# How often the worker re-checks the flag while it is paused.
_POLL_INTERVAL = 0.1


def _resolve(flag_path: Path | None) -> Path:
    return flag_path if flag_path is not None else DEFAULT_PAUSE_FLAG_PATH


def is_paused(flag_path: Path | None = None) -> bool:
    """Return True when the run has been marked as paused."""

    try:
        return _resolve(flag_path).exists()
    except OSError:
        return False


def set_paused(flag_path: Path | None = None) -> None:
    """Mark the run as paused."""

    path = _resolve(flag_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("paused", encoding="utf-8")
    except OSError:
        pass


def clear_paused(flag_path: Path | None = None) -> None:
    """Mark the run as running again."""

    try:
        _resolve(flag_path).unlink(missing_ok=True)
    except OSError:
        pass


def wait_while_paused(
    flag_path: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Block while the run is paused and return whether it was paused.

    The worker calls this before every wait, so a pause takes effect
    within a fraction of a second and the game is left untouched while
    the run is frozen.
    """

    path = _resolve(flag_path)
    was_paused = False
    while is_paused(path):
        if not was_paused:
            was_paused = True
            if log is not None:
                log("Paused by user; waiting for resume")
        time.sleep(_POLL_INTERVAL)
    if was_paused and log is not None:
        log("Resumed by user")
    return was_paused
