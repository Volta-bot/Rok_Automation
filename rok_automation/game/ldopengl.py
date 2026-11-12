"""Native LDPlayer capture through the emulator's own OpenGL surface.

LDPlayer ships ``ldopengl64.dll`` next to ``ldconsole.exe``. The DLL's
``CreateScreenShotInstance`` entry point returns a COM-style object whose
second vtable slot copies the emulator's rendered framebuffer into
memory. Reading that buffer costs a fraction of a frame transfer over
ADB, and it keeps working when the emulator window is minimised, hidden
or covered by another window.

The emulator instance and the DLL are located through ``ldconsole.exe``,
which also reports the resolution the framebuffer is laid out at, so the
captured frame and the input coordinates always agree.

This module was written for this project against the DLL's published
entry points; it is not derived from another project's source.
"""

from __future__ import annotations

import ctypes
import os
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np

from rok_automation.game.adb import AdbInput
from rok_automation.game.interface import TimingConfig
from rok_automation.game.ldplayer import normalize_capture
from rok_automation.vision.screen import cache_captured_frame


class LDOpenGLError(RuntimeError):
    """Raised when the native capture backend cannot be used."""


@dataclass(frozen=True)
class LDPlayerInstance:
    """One LDPlayer instance as reported by ``ldconsole.exe list2``."""

    index: int
    name: str
    top_window: int
    bind_window: int
    running: bool
    player_pid: int
    vbox_pid: int
    width: int
    height: int
    dpi: int

    @property
    def is_usable(self) -> bool:
        """Whether this instance is booted and can be captured."""

        return self.running and self.player_pid > 0 and self.width > 0 and self.height > 0


def parse_instance_list(output: str) -> list[LDPlayerInstance]:
    """Parse the CSV rows printed by ``ldconsole.exe list2``.

    Each row looks like::

        0,LDPlayer,4523898,4327240,1,23400,2860,1920,1080,280

    The instance name may contain commas, so the name is taken from the
    middle of the row and the numeric fields from both ends.
    """

    instances: list[LDPlayerInstance] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(",")
        if len(fields) < 10:
            continue
        try:
            instance = LDPlayerInstance(
                index=int(fields[0]),
                name=",".join(fields[1:-8]).strip(),
                top_window=int(fields[-8]),
                bind_window=int(fields[-7]),
                running=bool(int(fields[-6])),
                player_pid=int(fields[-5]),
                vbox_pid=int(fields[-4]),
                width=int(fields[-3]),
                height=int(fields[-2]),
                dpi=int(fields[-1]),
            )
        except ValueError:
            continue
        instances.append(instance)
    return instances


def _registry_install_dirs() -> list[Path]:
    """Return LDPlayer install folders recorded in the uninstall registry."""

    if sys.platform != "win32":
        return []

    import winreg

    directories: list[Path] = []
    pattern = re.compile(r"([A-Za-z]:\\[^,\"]*?dnplayer\.exe)", re.IGNORECASE)
    hives = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, key_path in hives:
        try:
            root = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        try:
            with root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, index)[0]) as entry:
                            icon = str(winreg.QueryValueEx(entry, "DisplayIcon")[0])
                    except OSError:
                        continue
                    match = pattern.search(icon)
                    if match:
                        directories.append(Path(match.group(1)).parent)
        except OSError:
            continue
    return directories


def _candidate_install_dirs() -> list[Path]:
    """Return the usual places an LDPlayer installation lives."""

    directories: list[Path] = []

    override = os.environ.get("ROK_LDPLAYER_DIR")
    if override:
        directories.append(Path(override))

    directories.extend(_registry_install_dirs())

    for drive in "CDEFGH":
        root = Path(f"{drive}:/LDPlayer")
        if root.is_dir():
            directories.append(root)
            try:
                directories.extend(sorted(p for p in root.iterdir() if p.is_dir()))
            except OSError:
                continue
        for name in ("LDPlayer9", "LDPlayer14"):
            directories.append(Path(f"{drive}:/{name}"))
        directories.append(Path(f"{drive}:/Program Files/LDPlayer"))
        directories.append(Path(f"{drive}:/Program Files (x86)/LDPlayer"))

    return directories


def find_ldplayer_console() -> Path:
    """Locate ``ldconsole.exe`` and return its folder.

    The folder also holds ``ldopengl64.dll``, which the native capture
    backend needs.
    """

    seen: set[str] = set()
    for directory in _candidate_install_dirs():
        key = str(directory).casefold()
        if key in seen:
            continue
        seen.add(key)
        console = directory / "ldconsole.exe"
        if console.is_file():
            return directory

    raise LDOpenGLError(
        "Could not find ldconsole.exe. Set ROK_LDPLAYER_DIR to the LDPlayer "
        "installation folder."
    )


def list_instances(console_dir: Path) -> list[LDPlayerInstance]:
    """Ask ``ldconsole.exe`` which emulator instances exist."""

    console = console_dir / "ldconsole.exe"
    if not console.is_file():
        raise LDOpenGLError(f"ldconsole.exe is missing from {console_dir}")

    try:
        result = subprocess.run(
            [str(console), "list2"],
            capture_output=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise LDOpenGLError("ldconsole.exe did not answer in time") from exc
    except OSError as exc:
        raise LDOpenGLError(f"Could not run ldconsole.exe: {exc}") from exc

    output = result.stdout.decode("utf-8", "replace")
    if not output.strip():
        output = result.stdout.decode("gbk", "replace")

    instances = parse_instance_list(output)
    if not instances:
        raise LDOpenGLError("ldconsole.exe reported no emulator instances")
    return instances


def select_instance(
    instances: Iterable[LDPlayerInstance],
    index: int | None = None,
) -> LDPlayerInstance:
    """Pick the emulator instance to drive.

    Without an explicit index the first booted instance is used, which
    matches the single-instance setups the automation is written for.
    """

    candidates = list(instances)
    if index is not None:
        for instance in candidates:
            if instance.index == index:
                if not instance.is_usable:
                    raise LDOpenGLError(
                        f"LDPlayer instance {index} is not running; start it first."
                    )
                return instance
        raise LDOpenGLError(f"No LDPlayer instance with index {index}")

    for instance in candidates:
        if instance.is_usable:
            return instance

    raise LDOpenGLError(
        "No running LDPlayer instance was found. Start the emulator and try again."
    )


def to_image_order(frame: np.ndarray) -> np.ndarray:
    """Turn a raw framebuffer image into normal top-down image order.

    The renderer hands back rows bottom-up, so the frame has to be
    flipped before anything can match it against a template.
    """

    return np.ascontiguousarray(cv2.flip(frame, 0))


class LDOpenGLCapture:
    """Read frames straight out of the emulator's rendered framebuffer."""

    def __init__(self, instance: LDPlayerInstance, dll_path: Path) -> None:
        self.instance = instance
        self._width = instance.width
        self._height = instance.height

        if not dll_path.is_file():
            raise LDOpenGLError(f"The LDPlayer capture library is missing: {dll_path}")

        try:
            library = ctypes.WinDLL(str(dll_path))
        except OSError as exc:
            raise LDOpenGLError(
                f"Could not load {dll_path}: {exc}. Native capture needs "
                "LDPlayer 9.0.78 or newer."
            ) from exc

        create_instance = library.CreateScreenShotInstance
        create_instance.restype = ctypes.c_void_p
        create_instance.argtypes = (ctypes.c_int, ctypes.c_int)

        handle = create_instance(instance.index, instance.player_pid)
        if not handle:
            raise LDOpenGLError(
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
            raise LDOpenGLError("The LDPlayer framebuffer returned no image")

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


class LdOpenGlInterface(AdbInput):
    """Capture frames natively; deliver input over ADB.

    Frames come from the emulator's own renderer, so detection is never
    confused by an overlapping window and no frame ever has to travel
    over ADB. Input still travels through the Android input pipeline,
    which keeps the host mouse untouched.
    """

    def __init__(
        self,
        *,
        instance: LDPlayerInstance,
        dll_path: Path,
        adb_path: str | None = None,
        serial: str | None = None,
        screenshot_path: Path = Path("artifacts/ldplayer_native.png"),
        timing: TimingConfig = TimingConfig(),
        pause_flag_path: Path | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.capture = LDOpenGLCapture(instance, dll_path)
        super().__init__(
            adb_path=adb_path,
            serial=serial,
            timing=timing,
            pause_flag_path=pause_flag_path,
            log=log,
        )
        self.screenshot_path = screenshot_path
        self.instance = instance

        # The framebuffer is laid out at the resolution LDPlayer reports,
        # while input is delivered in the guest's display space. They are
        # normally the same, but a mismatch would shift every click.
        guest_width, guest_height = self._guest_size
        if (guest_width, guest_height) != (instance.width, instance.height):
            message = (
                f"LDPlayer renders at {instance.width}x{instance.height} but the "
                f"guest display is {guest_width}x{guest_height}; clicks are "
                "mapped through the guest display size."
            )
            if log is not None:
                log(message)

        self._measure_latency()

    def _measure_latency(self) -> None:
        """Time a few captures so the timeouts match the real speed."""

        samples = []
        for _ in range(3):
            started = time.perf_counter()
            self.capture.capture()
            samples.append(time.perf_counter() - started)
        latency = statistics.median(samples)
        self.timing = replace(self.timing, capture_latency=latency)
        if self.log is not None:
            self.log(
                f"Native capture latency {latency * 1000:.0f} ms; "
                f"screen timeouts scaled {self.timeout_scale:.1f}x"
            )

    def screenshot(self) -> Path:
        """Capture the emulator and store a canonical 1280x720 frame."""

        frame = self.capture.capture()
        normalized = normalize_capture(frame)
        destination = self.screenshot_path.expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved = destination.resolve()
        cv2.imwrite(str(resolved), normalized)
        cache_captured_frame(resolved, normalized)
        return resolved


def build_native_interface(
    *,
    adb_path: str | None = None,
    serial: str | None = None,
    instance_index: int | None = None,
    console_dir: Path | None = None,
    screenshot_path: Path = Path("artifacts/ldplayer_native.png"),
    timing: TimingConfig = TimingConfig(),
    pause_flag_path: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> LdOpenGlInterface:
    """Create a native-capture interface for a running LDPlayer."""

    folder = console_dir or find_ldplayer_console()
    instances = list_instances(folder)
    instance = select_instance(instances, instance_index)

    dll_path = folder / "ldopengl64.dll"
    if not dll_path.is_file():
        dll_path = folder / "ldopengl32.dll"

    if log is not None:
        log(
            f"Native capture attached to LDPlayer '{instance.name}' "
            f"(index {instance.index}, {instance.width}x{instance.height})"
        )

    return LdOpenGlInterface(
        instance=instance,
        dll_path=dll_path,
        adb_path=adb_path,
        serial=serial,
        screenshot_path=screenshot_path,
        timing=timing,
        pause_flag_path=pause_flag_path,
        log=log,
    )
