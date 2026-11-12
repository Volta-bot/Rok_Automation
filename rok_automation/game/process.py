"""Launching helper programs without flashing a console window.

The GUI runs on ``pythonw``, and the worker it starts runs without a
console of its own. Any console program that worker launches — ``adb``
and the emulator's own tools — would therefore open a console window
that appears and disappears again, which looks like a flashing command
prompt and steals the focus of the window the user is looking at.

Every helper program is therefore started with ``CREATE_NO_WINDOW``, so
it runs in the background without ever touching the screen.
"""

from __future__ import annotations

import subprocess
import sys


def hidden_creation_flags() -> int:
    """Return the flag that keeps a child program from opening a window."""

    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_hidden(command, **kwargs) -> subprocess.CompletedProcess:
    """Run a command without letting it open a console window."""

    kwargs.setdefault("creationflags", hidden_creation_flags())
    return subprocess.run(command, **kwargs)


def popen_hidden(command, **kwargs) -> subprocess.Popen:
    """Start a command without letting it open a console window."""

    kwargs.setdefault("creationflags", hidden_creation_flags())
    return subprocess.Popen(command, **kwargs)
