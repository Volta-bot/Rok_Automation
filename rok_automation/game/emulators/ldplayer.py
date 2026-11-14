"""The LDPlayer emulator family.

LDPlayer ships ``ldopengl64.dll`` next to ``ldconsole.exe``. The DLL's
``CreateScreenShotInstance`` entry point returns a COM-style object whose
second vtable slot copies the emulator's rendered framebuffer into
memory. Reading that buffer costs a fraction of a frame transfer over
ADB, and it keeps working when the emulator window is minimised, hidden
or covered by another window.

This module was written for this project against the DLL's published
entry points; it is not derived from another project's source.
"""

from __future__ import annotations

import ctypes
import subprocess
from pathlib import Path

import cv2
import numpy as np

from rok_automation.game.emulators import (
    EmulatorError,
    EmulatorFamily,
    EmulatorInstance,
    install_dirs_from_registry,
    register,
)
from rok_automation.game.process import run_hidden

# The folders LDPlayer is commonly installed in.
_INSTALL_LOCATIONS = (
    r"C:\LDPlayer\LDPlayer14",
    r"C:\LDPlayer\LDPlayer9",
    r"C:\LDPlayer",
    r"D:\LDPlayer\LDPlayer14",
    r"D:\LDPlayer\LDPlayer9",
    r"D:\LDPlayer",
    r"E:\LDPlayer\LDPlayer14",
    r"E:\LDPlayer\LDPlayer9",
    r"E:\LDPlayer",
    r"C:\Program Files\LDPlayer",
    r"C:\Program Files (x86)\LDPlayer",
)


def parse_instance_list(output: str) -> list[EmulatorInstance]:
    """Parse the CSV rows printed by ``ldconsole.exe list2``.

    Each row looks like::

        0,LDPlayer,4523898,4327240,1,23400,2860,1920,1080,280

    The instance name may contain commas, so the name is taken from the
    middle of the row and the numeric fields from both ends.
    """

    instances: list[EmulatorInstance] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(",")
        if len(fields) < 10:
            continue
        try:
            instances.append(
                EmulatorInstance(
                    index=int(fields[0]),
                    name=",".join(fields[1:-8]).strip(),
                    running=bool(int(fields[-6])),
                    pid=int(fields[-5]),
                    width=int(fields[-3]),
                    height=int(fields[-2]),
                    details={
                        "top_window": fields[-8],
                        "bind_window": fields[-7],
                        "vbox_pid": fields[-4],
                        "dpi": fields[-1],
                    },
                )
            )
        except ValueError:
            continue
    return instances


def to_image_order(frame: np.ndarray) -> np.ndarray:
    """Turn a raw framebuffer image into normal top-down image order.

    The renderer hands back rows bottom-up, so the frame has to be
    flipped before anything can match it against a template.
    """

    return np.ascontiguousarray(cv2.flip(frame, 0))


class LDOpenGLCapture:
    """Read frames straight out of the emulator's rendered framebuffer."""

    def __init__(self, instance: EmulatorInstance, dll_path: Path) -> None:
        self.instance = instance
        self._width = instance.width
        self._height = instance.height

        if not dll_path.is_file():
            raise EmulatorError(f"The LDPlayer capture library is missing: {dll_path}")

        try:
            library = ctypes.WinDLL(str(dll_path))
        except OSError as exc:
            raise EmulatorError(
                f"Could not load {dll_path}: {exc}. Native capture needs "
                "LDPlayer 9.0.78 or newer."
            ) from exc

        create_instance = library.CreateScreenShotInstance
        create_instance.restype = ctypes.c_void_p
        create_instance.argtypes = (ctypes.c_int, ctypes.c_int)

        handle = create_instance(instance.index, instance.pid)
        if not handle:
            raise EmulatorError(
                "LDPlayer refused to create a capture instance. Restart the emulator."
            )
        self._handle = ctypes.c_void_p(handle)

        # The object follows the usual COM layout: the second vtable slot
        # copies the framebuffer out of the renderer.
        vtable = ctypes.cast(
            self._handle, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        capture = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)
        self._capture = ctypes.cast(vtable[1], capture)

    def capture(self) -> np.ndarray:
        """Return the current frame as an upright BGR image."""

        pointer = self._capture(self._handle)
        if not pointer:
            raise EmulatorError("The LDPlayer framebuffer returned no image")

        size = self._height * self._width * 3
        buffer = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ubyte * size)).contents
        frame = np.ctypeslib.as_array(buffer).reshape((self._height, self._width, 3))
        # Copy out of the renderer's memory before it can be reused.
        return to_image_order(frame.copy())

    def close(self) -> None:
        """Drop the capture instance.

        The DLL's release slot is deliberately left alone: its signature is
        not documented, and the instance lives for the whole run anyway.
        """

        self._handle = None
        self._capture = None


@register
class LDPlayerFamily(EmulatorFamily):
    """LDPlayer, read through its own OpenGL renderer."""

    key = "ldplayer"
    label = "LDPlayer"
    install_locations = _INSTALL_LOCATIONS
    adb_locations = ("adb.exe", "adb_server.exe")
    registry_markers = ("dnplayer.exe", "ldplayer.exe")
    supports_native_capture = True

    def install_folders(self) -> list[Path]:
        folders = super().install_folders()
        for directory in install_dirs_from_registry(self.registry_markers):
            if directory not in folders:
                folders.append(directory)
        # The registry points at the emulator executable, but the capture
        # library lives next to ldconsole.exe in the same folder.
        return folders

    # -- discovery ------------------------------------------------------ #

    def console_path(self) -> Path:
        """Return the folder that holds ``ldconsole.exe``."""

        for folder in self.install_folders():
            if (folder / "ldconsole.exe").is_file():
                return folder
        raise EmulatorError(
            "Could not find ldconsole.exe. Set ROK_LDPLAYER_DIR to the "
            "LDPlayer installation folder."
        )

    def list_instances(self) -> list[EmulatorInstance]:
        """Ask ``ldconsole.exe`` which emulator instances exist."""

        folder = self.console_path()
        try:
            result = run_hidden(
                [str(folder / "ldconsole.exe"), "list2"],
                capture_output=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired as exc:
            raise EmulatorError("ldconsole.exe did not answer in time") from exc
        except OSError as exc:
            raise EmulatorError(f"Could not run ldconsole.exe: {exc}") from exc

        output = result.stdout.decode("utf-8", "replace")
        if not output.strip():
            output = result.stdout.decode("gbk", "replace")

        instances = parse_instance_list(output)
        if not instances:
            raise EmulatorError("ldconsole.exe reported no emulator instances")
        return instances

    def discover(self) -> list[EmulatorInstance]:
        return self.list_instances()

    # -- native capture ------------------------------------------------- #

    def capture_library(self) -> Path:
        """Return the path of the capture library."""

        folder = self.console_path()
        for name in ("ldopengl64.dll", "ldopengl32.dll"):
            candidate = folder / name
            if candidate.is_file():
                return candidate
        raise EmulatorError(f"No LDPlayer capture library in {folder}")

    def open_capture(self, instance: EmulatorInstance) -> LDOpenGLCapture:
        return LDOpenGLCapture(instance, self.capture_library())
