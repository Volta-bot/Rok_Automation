"""Read MuMu's renderer through its external renderer IPC library."""

from __future__ import annotations

import ctypes
from pathlib import Path

import cv2
import numpy as np

from rok_automation.game.emulators import EmulatorError


class MuMuFrameSource:
    """Capture the selected MuMu display as upright BGR frames."""

    def __init__(self, install_root: Path, library_path: Path, index: int) -> None:
        try:
            self._library = ctypes.CDLL(str(library_path))
            connect = self._library.nemu_connect
            connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
            connect.restype = ctypes.c_int
            self._disconnect = self._library.nemu_disconnect
            self._disconnect.argtypes = [ctypes.c_int]
            self._disconnect.restype = None
            self._capture = self._library.nemu_capture_display
            self._capture.argtypes = [
                ctypes.c_int, ctypes.c_uint, ctypes.c_int,
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_ubyte),
            ]
            self._capture.restype = ctypes.c_int
            self._handle = connect(str(install_root), index)
        except (OSError, AttributeError) as exc:
            raise EmulatorError(f"Could not load MuMu renderer: {exc}") from exc
        if self._handle <= 0:
            raise EmulatorError("MuMu renderer connection failed")

    def capture(self) -> np.ndarray:
        width, height = ctypes.c_int(), ctypes.c_int()
        result = self._capture(self._handle, 0, 0, ctypes.byref(width), ctypes.byref(height), None)
        if result != 0 or not (0 < width.value <= 8192 and 0 < height.value <= 8192):
            raise EmulatorError("MuMu renderer could not read display dimensions")
        size = width.value * height.value * 4
        pixels = (ctypes.c_ubyte * size)()
        expected = (width.value, height.value)
        result = self._capture(self._handle, 0, size, ctypes.byref(width), ctypes.byref(height), pixels)
        if result != 0 or (width.value, height.value) != expected:
            raise EmulatorError("MuMu renderer could not capture the display")
        rgba = np.ctypeslib.as_array(pixels).reshape((height.value, width.value, 4))
        return cv2.cvtColor(cv2.flip(rgba, 0), cv2.COLOR_RGBA2BGR)

    def close(self) -> None:
        if self._handle:
            self._disconnect(self._handle)
            self._handle = 0
