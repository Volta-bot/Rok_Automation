"""Choosing how the automation talks to the emulator.

Three capture backends are available, in increasing order of speed:

``desktop``
    Captures the visible emulator window from the Windows desktop. No
    emulator setup is needed, but the window must stay visible and
    uncovered.

``adb``
    Captures the emulator's framebuffer over ADB and sends input
    through the Android input pipeline. The window may be minimised or
    covered, but every frame costs a full transfer over ADB.

``native``
    Captures the emulator's rendered framebuffer through the emulator's
    own capture library, which is roughly an order of magnitude faster
    than the ADB path, and sends input over ADB as well.

Which emulator is used is decided by the emulator family, which is
looked up in :mod:`rok_automation.game.emulators`. Each backend falls
back to the next slower one when it is unavailable, so a run never dies
just because the preferred backend is missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from rok_automation.game.emulators import (
    EmulatorError,
    EmulatorFamily,
    select_family,
    select_instance,
)
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


def _desktop_interface(
    family: EmulatorFamily,
    *,
    timing: TimingConfig,
    log: Callable[[str], None] | None,
) -> GameInterface:
    from rok_automation.game.desktop import capture_emulator_window, find_emulator_window
    from rok_automation.game.interface import DesktopInterface

    window = find_emulator_window(family.window_terms, family.window_classes)
    if window is None:
        raise EmulatorError(
            f"No visible {family.label or family.key} window was found. Start "
            "the emulator, or switch to a background backend."
        )
    if log is not None:
        log(f"Desktop capture attached to {window.title} (handle={window.handle})")
    return DesktopInterface(
        window,
        screenshot_path=Path("artifacts/emulator_desktop.png"),
        timing=timing,
        log=log,
    )


def _adb_interface(
    family: EmulatorFamily,
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
        emulator=family,
        screenshot_path=Path("artifacts/emulator_adb.png"),
        timing=timing,
        log=log,
    )
    if log is not None:
        log(f"ADB capture attached to {interface.serial}")
    return interface


def _native_interface(
    family: EmulatorFamily,
    *,
    index: int | None,
    adb_path: str | None,
    serial: str | None,
    timing: TimingConfig,
    log: Callable[[str], None] | None,
) -> GameInterface:
    from rok_automation.game.native import NativeInterface

    instances = family.discover()
    instance = select_instance(instances, index, family=family.label or family.key)
    source = family.open_capture(instance)

    if log is not None:
        log(
            f"Native capture attached to {family.label} '{instance.name}' "
            f"(index {instance.index}, {instance.width}x{instance.height})"
        )

    return NativeInterface(
        source=source,
        instance=instance,
        adb_path=adb_path,
        serial=serial,
        screenshot_path=Path("artifacts/emulator_native.png"),
        timing=timing,
        log=log,
    )


def create_game_interface(
    backend: str = BACKEND_NATIVE,
    *,
    emulator: str | None = None,
    index: int | None = None,
    adb_path: str | None = None,
    serial: str | None = None,
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
    family, saved_index = select_family(emulator)
    if index is None:
        index = saved_index

    attempts: list[tuple[str, Callable[[], GameInterface]]] = []
    if backend == BACKEND_NATIVE:
        attempts.append(
            (
                BACKEND_NATIVE,
                lambda: _native_interface(
                    family,
                    index=index,
                    adb_path=adb_path,
                    serial=serial,
                    timing=timing,
                    log=log,
                ),
            )
        )
        attempts.append(
            (
                BACKEND_ADB,
                lambda: _adb_interface(
                    family,
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
                    family,
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
            lambda: _desktop_interface(family, timing=timing, log=log),
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
            log(f"Using the '{name}' backend with {family.label or family.key}.")
        return interface

    raise RuntimeError(f"No capture backend could be started: {last_error}")
