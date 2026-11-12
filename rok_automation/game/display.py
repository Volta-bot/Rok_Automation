"""Display scaling support for the graphical interface.

Windows scales a window that is not DPI-aware by rendering it at 96 DPI
and stretching the result, which makes the whole interface look soft and
slow to redraw on a scaled display. The fix is to declare the process
per-monitor DPI aware *before* the toolkit creates the first window, and
to size the window in the same units the layout was designed in.

The helpers here are deliberately dependency free and safe to call on
other platforms, where they simply do nothing.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


def enable_dpi_awareness() -> float:
    """Make the process per-monitor DPI aware and return the scale factor.

    Returns the ratio between the real display DPI and the 96 DPI that
    the layout was designed against, so the window can be given the
    physical size that matches the space the widgets need.
    """

    if sys.platform != "win32":
        return 1.0

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the documented -4 handle.
    try:
        set_awareness = user32.SetProcessDpiAwarenessContext
        set_awareness.argtypes = (ctypes.c_void_p,)
        set_awareness.restype = wintypes.BOOL
        set_awareness(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass

    return display_scale()


def display_scale() -> float:
    """Return the ratio between the display DPI and 96 DPI."""

    if sys.platform != "win32":
        return 1.0

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        screen_dc = user32.GetDC(None)
        try:
            dpi = gdi32.GetDeviceCaps(screen_dc, 88)  # LOGPIXELSX
        finally:
            user32.ReleaseDC(None, screen_dc)
    except (AttributeError, OSError):
        return 1.0

    if not dpi or dpi <= 0:
        return 1.0
    return dpi / 96.0
