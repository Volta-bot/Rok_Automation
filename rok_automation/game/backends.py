"""Choosing how the automation talks to the emulator.

Three backends are available, in increasing order of speed:

``desktop``
    Captures the visible LDPlayer window from the Windows desktop. No
    emulator setup is needed, but the window must stay visible and
    uncovered.

``adb``
    Captures the emulator's framebuffer over ADB and sends input
    through the Android input pipeline. The window may be minimised or
    covered, but every frame costs a full transfer over ADB.

``native``
    Captures the emulator's rendered framebuffer through LDPlayer's own
    ``ldopengl64.dll``, which is roughly an order of magnitude faster
    than the ADB path, and sends input over ADB as well.

Each backend falls back to the next slower one when it is unavailable,
so a run never dies just because the preferred backend is missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from rok_automation.game.interface import GameInterface, TimingConfig

BACKEND_DESKTOP = "desktop"
BACKEND_ADB = "adb"
BACKEND_NATIVE = "native"

BACKEND_CHOICES = (BACKEND_DESKTOP, BACKEND_ADB, BACKEND_NATIVE)

BACKEND_LABELS = {
    BACKEND_DESKTOP: "Desktop window (emulator must stay visible)",
    BACKEND_ADB: "ADB capture (background, slower frames)",
    BACKEND_NATIVE: "Native capture (fastest frames, background)",
}

_SCREENSHOT_PATHS = {
    BACKEND_DESKTOP: Path("artifacts/ldplayer.png"),
    BACKEND_ADB: Path("artifacts/ldplayer_adb.png"),
    BACKEND_NATIVE: Path("artifacts/ldplayer_native.png"),
}


def _desktop_interface(
    *,
    timing: TimingConfig,
    log: Callable[[str], None] | None,
) -> GameInterface:
    from rok_automation.game.interface import LDPlayerInterface
    from rok_automation.game.ldplayer import find_ldplayer_window

    window = find_ldplayer_window()
    if window is None:
        raise RuntimeError(
            "No visible LDPlayer window was found. Start the emulator, or "
            "switch to a background backend."
        )
    if log is not None:
        log(f"Desktop capture attached to {window.title} (handle={window.handle})")
    return LDPlayerInterface(
        window,
        screenshot_path=_SCREENSHOT_PATHS[BACKEND_DESKTOP],
        timing=timing,
        log=log,
    )


def _adb_interface(
    *,
    adb_path: str | None,
    serial: str | None,
    timing: TimingConfig,
    log: Callable[[str], None] | None,
) -> GameInterface:
    from rok_automation.game.adb import AdbInterface

    interface = AdbInterface(
        adb_path=adb_path,
        serial=serial,
        screenshot_path=_SCREENSHOT_PATHS[BACKEND_ADB],
        timing=timing,
        log=log,
    )
    if log is not None:
        log(f"ADB capture attached to {interface.serial}")
    return interface


def _native_interface(
    *,
    adb_path: str | None,
    serial: str | None,
    instance_index: int | None,
    timing: TimingConfig,
    log: Callable[[str], None] | None,
) -> GameInterface:
    from rok_automation.game.ldopengl import build_native_interface

    return build_native_interface(
        adb_path=adb_path,
        serial=serial,
        instance_index=instance_index,
        screenshot_path=_SCREENSHOT_PATHS[BACKEND_NATIVE],
        timing=timing,
        log=log,
    )


def create_game_interface(
    backend: str = BACKEND_NATIVE,
    *,
    adb_path: str | None = None,
    serial: str | None = None,
    instance_index: int | None = None,
    timing: TimingConfig | None = None,
    pause_flag_path: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> GameInterface:
    """Create the requested backend, falling back when it is unavailable."""

    if backend not in BACKEND_CHOICES:
        raise ValueError(
            f"Unknown backend '{backend}'. Choose one of: {', '.join(BACKEND_CHOICES)}"
        )

    timing = timing or TimingConfig()

    attempts: list[tuple[str, Callable[[], GameInterface]]] = []
    if backend == BACKEND_NATIVE:
        attempts.append(
            (
                BACKEND_NATIVE,
                lambda: _native_interface(
                    adb_path=adb_path,
                    serial=serial,
                    instance_index=instance_index,
                    timing=timing,
                    log=log,
                ),
            )
        )
        attempts.append(
            (
                BACKEND_ADB,
                lambda: _adb_interface(
                    adb_path=adb_path,
                    serial=serial,
                    timing=timing,
                    log=log,
                ),
            )
        )
    elif backend == BACKEND_ADB:
        attempts.append(
            (
                BACKEND_ADB,
                lambda: _adb_interface(
                    adb_path=adb_path,
                    serial=serial,
                    timing=timing,
                    log=log,
                ),
            )
        )
    attempts.append(
        (
            BACKEND_DESKTOP,
            lambda: _desktop_interface(timing=timing, log=log),
        )
    )

    last_error: Exception | None = None
    for name, build in attempts:
        try:
            interface = build()
        except Exception as exc:  # noqa: BLE001 - fall back to the next backend
            last_error = exc
            if log is not None:
                log(f"Backend '{name}' is unavailable ({exc}); trying the next one.")
            continue
        if log is not None:
            log(f"Using the '{name}' backend.")
        return interface

    raise RuntimeError(f"No capture backend could be started: {last_error}")
