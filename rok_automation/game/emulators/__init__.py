"""The registry of emulator families the automation can drive.

Every emulator family is a small module in this package that subclasses
:class:`EmulatorFamily` and registers itself. A family only has to answer
three questions:

* where its bundled ``adb`` lives,
* which instances are running, and
* how to open a native capture of the emulator's own framebuffer.

Everything else — capture, input, detection, the farming loop — is shared
and stays emulator-agnostic.

Adding a new emulator is therefore a single new file::

    # rok_automation/game/emulators/bluestacks.py
    from rok_automation.game.emulators import EmulatorFamily, register

    @register
    class BlueStacksFamily(EmulatorFamily):
        key = "bluestacks"
        label = "BlueStacks"
        ...
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Where the chosen emulator family is remembered between runs.
DEFAULT_EMULATOR_STATE_PATH = _PROJECT_ROOT / "state" / "emulator.json"


class EmulatorError(RuntimeError):
    """Raised when an emulator family cannot be used."""


@dataclass(frozen=True)
class EmulatorInstance:
    """One running (or stopped) instance of an emulator."""

    index: int
    name: str
    running: bool = False
    pid: int = -1
    width: int = 0
    height: int = 0
    # Anything else the family wants to carry along, such as the ADB port.
    details: dict[str, str] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        """Whether this instance is booted and can be captured."""

        return self.running and self.pid > 0 and self.width > 0 and self.height > 0


@runtime_checkable
class FrameSource(Protocol):
    """A source of game frames in canonical screen order."""

    def capture(self): ...


class EmulatorFamily:
    """Base class describing one family of emulator."""

    # Short identifier used on the command line and in the state file.
    key: str = ""
    # Human readable name.
    label: str = ""
    # Folders the emulator may be installed in. An entry may contain a "*"
    # wildcard for a version folder.
    install_locations: tuple[str, ...] = ()
    # adb.exe paths relative to the install folder.
    adb_locations: tuple[str, ...] = ()
    # Executable names that mark an install folder in the registry.
    registry_markers: tuple[str, ...] = ()
    # Whether this family can read the emulator's own framebuffer.
    supports_native_capture: bool = False

    def install_folders(self) -> list[Path]:
        """Return the folders this emulator is installed in."""

        folders: list[Path] = []
        for pattern in self.install_locations:
            candidates: list[Path] = []
            if "*" in pattern:
                path = Path(pattern)
                candidates = sorted(path.parent.glob(path.name))
            else:
                candidates = [Path(pattern)]
            for candidate in candidates:
                if candidate.is_dir() and candidate not in folders:
                    folders.append(candidate)
        return folders

    def is_installed(self) -> bool:
        """Whether this emulator family is present on this machine."""

        return bool(self.install_folders())

    def adb_executables(self) -> list[Path]:
        """Return the adb clients this emulator ships with."""

        found: list[Path] = []
        for folder in self.install_folders():
            for relative in self.adb_locations:
                candidate = folder / relative
                if candidate.is_file() and candidate not in found:
                    found.append(candidate)
        return found

    def preferred_serials(self) -> list[str]:
        """ADB serials this emulator is likely to appear as.

        When more than one emulator is connected, this picks the right one.
        """

        return []

    def discover(self) -> list[EmulatorInstance]:
        """Return the instances this emulator knows about."""

        raise EmulatorError(f"'{self.key}' cannot list its instances")

    def open_capture(self, instance: EmulatorInstance) -> FrameSource:
        """Open a native capture of the emulator's own framebuffer.

        Raises :class:`EmulatorError` when the family has no native capture
        or when it is not available on this machine.
        """

        raise EmulatorError(f"'{self.key}' has no native capture")

    @property
    def has_native_capture(self) -> bool:
        """Whether this family can read the emulator's own framebuffer."""

        return bool(self.supports_native_capture)


def select_instance(
    instances: list[EmulatorInstance],
    index: int | None = None,
    *,
    family: str = "emulator",
) -> EmulatorInstance:
    """Pick the emulator instance to drive.

    Without an explicit index the first booted instance is used, which
    matches the single-instance setups the automation is written for.
    """

    if index is not None:
        for instance in instances:
            if instance.index == index:
                if not instance.is_usable:
                    raise EmulatorError(
                        f"{family} instance {index} is not running; start it first."
                    )
                return instance
        raise EmulatorError(f"No {family} instance with index {index}")

    for instance in instances:
        if instance.is_usable:
            return instance

    raise EmulatorError(
        f"No running {family} instance was found. Start the emulator and try again."
    )


def install_dirs_from_registry(markers: tuple[str, ...]) -> list[Path]:
    """Find install folders recorded in the Windows uninstall registry.

    Emulators register their executable as the ``DisplayIcon`` of an
    uninstall entry, which is the most reliable way to find an installation
    that lives outside the usual folders.
    """

    if sys.platform != "win32" or not markers:
        return []

    import re
    import winreg

    pattern = re.compile(
        r"([A-Za-z]:\\[^,\"]*?(?:" + "|".join(re.escape(m) for m in markers) + r"))",
        re.IGNORECASE,
    )
    directories: list[Path] = []
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
                        directory = Path(match.group(1)).parent
                        if directory not in directories:
                            directories.append(directory)
        except OSError:
            continue
    return directories


_FAMILIES: dict[str, EmulatorFamily] = {}


def register(family):
    """Register an emulator family so it can be selected by key.

    Usable as a decorator on a class, in which case the class is
    instantiated once and that instance is kept.
    """

    if isinstance(family, type):
        family = family()
    if not family.key:
        raise EmulatorError("An emulator family needs a key")
    _FAMILIES[family.key] = family
    return family


def families() -> list[EmulatorFamily]:
    """Return every registered emulator family."""

    _load_builtin_families()
    return sorted(_FAMILIES.values(), key=lambda family: family.key)


def get_family(key: str) -> EmulatorFamily:
    """Return the registered family with this key."""

    _load_builtin_families()
    try:
        return _FAMILIES[key]
    except KeyError:
        known = ", ".join(sorted(_FAMILIES)) or "none"
        raise EmulatorError(f"Unknown emulator '{key}'. Known emulators: {known}") from None


_builtin_loaded = False


def _load_builtin_families() -> None:
    """Import the built-in family modules once."""

    global _builtin_loaded
    if _builtin_loaded:
        return
    _builtin_loaded = True

    from rok_automation.game.emulators import mumu  # noqa: F401


def save_selection(key: str, index: int | None = None, path: Path | None = None) -> Path:
    """Remember which emulator family to use between runs."""

    destination = path or DEFAULT_EMULATOR_STATE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"family": key}
    if index is not None:
        payload["index"] = index
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return destination


def load_selection(path: Path | None = None) -> tuple[str | None, int | None]:
    """Read the remembered emulator family and instance index."""

    source = path or DEFAULT_EMULATOR_STATE_PATH
    if not source.is_file():
        return None, None
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    family = payload.get("family")
    index = payload.get("index")
    if not isinstance(family, str) or not family:
        return None, None
    if not isinstance(index, int):
        index = None
    return family, index


def selection_from_environment() -> tuple[str | None, int | None]:
    """Read the emulator selection from the environment.

    ``ROK_EMULATOR`` names the family and ``ROK_EMULATOR_INDEX`` the
    instance, which is handy for scheduled runs.
    """

    family = os.environ.get("ROK_EMULATOR") or None
    raw_index = os.environ.get("ROK_EMULATOR_INDEX")
    index = None
    if raw_index:
        try:
            index = int(raw_index)
        except ValueError:
            index = None
    return family, index


def select_family(
    preferred: str | None = None,
    *,
    state_path: Path | None = None,
) -> tuple[EmulatorFamily, int | None]:
    """Work out which emulator family to drive.

    The order is: the explicit request, then the environment, then the
    remembered selection, then MuMu Player by default.
    """

    if preferred:
        family = get_family(preferred)
        # A remembered instance index only applies to the family it was
        # remembered for.
        saved_family, saved_index = load_selection(state_path)
        index = saved_index if saved_family == preferred else None
        return family, index

    env_family, env_index = selection_from_environment()
    if env_family:
        return get_family(env_family), env_index

    saved_family, saved_index = load_selection(state_path)
    if saved_family and saved_family in {family.key for family in families()}:
        return get_family(saved_family), saved_index

    return get_family("mumu"), None
