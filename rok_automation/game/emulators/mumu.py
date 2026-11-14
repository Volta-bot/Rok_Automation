"""The MuMu Player emulator family.

MuMu 12 ships ``MuMuManager.exe``, which reports the ADB port of every
instance, and ``external_renderer_ipc.dll``, which can hand out the
emulator's rendered framebuffer the same way LDPlayer's capture library
does.

Only the parts that could be verified on a real installation are
implemented here. The native capture needs MuMu 12 (>= 3.8.13) and has to
be checked against the installed DLL before it is trusted, so
:meth:`MuMuFamily.open_capture` refuses rather than guessing.
"""

from __future__ import annotations

import json
import re
import socket
import sys
import subprocess
from pathlib import Path

from rok_automation.game.emulators import (
    EmulatorError,
    EmulatorFamily,
    EmulatorInstance,
    install_dirs_from_registry,
    register,
)
from rok_automation.game.process import run_hidden

# The folders MuMu 12 is commonly installed in.
_INSTALL_LOCATIONS = (
    r"C:\Program Files\Netease\MuMuPlayer",
    r"C:\Program Files (x86)\Netease\MuMuPlayer",
    r"D:\Program Files\Netease\MuMuPlayer",
    r"E:\Program Files\Netease\MuMuPlayer",
    r"C:\Program Files\Netease\MuMuPlayer-12.0",
    r"C:\Program Files (x86)\Netease\MuMuPlayer-12.0",
    r"D:\Program Files\Netease\MuMuPlayer-12.0",
    r"D:\Program Files (x86)\Netease\MuMuPlayer-12.0",
    r"E:\Program Files\Netease\MuMuPlayer-12.0",
    r"E:\Program Files (x86)\Netease\MuMuPlayer-12.0",
)

# The folders that hold the ADB client, relative to the install folder.
_ADB_LOCATIONS = (
    "nx_main/adb.exe",
    "nx_device/15.0/shell/adb.exe",
    "shell/adb.exe",
    "shell/adb_server.exe",
    "nx_main/shell/adb.exe",
    "nx_device/12.0/shell/adb.exe",
)


@register
class MuMuFamily(EmulatorFamily):
    """MuMu Player 12."""

    key = "mumu"
    label = "MuMu Player"
    install_locations = _INSTALL_LOCATIONS
    adb_locations = _ADB_LOCATIONS
    registry_markers = ("mumuplayer.exe", "nemumain.exe", "nemulauncher.exe")
    # The MuMu capture library has to be verified against the installed
    # version before it can be used.
    supports_native_capture = False

    def install_folders(self) -> list[Path]:
        folders = super().install_folders()
        for directory in install_dirs_from_registry(self.registry_markers):
            if directory not in folders:
                folders.append(directory)
        if sys.platform == "win32":
            import winreg

            for hive, key_path in (
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ):
                try:
                    with winreg.OpenKey(hive, key_path) as root:
                        for index in range(winreg.QueryInfoKey(root)[0]):
                            try:
                                with winreg.OpenKey(root, winreg.EnumKey(root, index)[0]) as entry:
                                    name = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                                    if "mumuplayer" not in name.lower():
                                        continue
                                    folder = Path(str(winreg.QueryValueEx(entry, "InstallLocation")[0]))
                                    if folder.is_dir() and folder not in folders:
                                        folders.append(folder)
                            except OSError:
                                continue
                except OSError:
                    continue
        return folders

    # -- discovery ------------------------------------------------------ #

    def manager_path(self) -> Path:
        """Return the folder that holds ``MuMuManager.exe``."""

        for folder in self.install_folders():
            for relative in ("nx_main/MuMuManager.exe", "shell/MuMuManager.exe", "MuMuManager.exe"):
                candidate = folder / relative
                if candidate.is_file():
                    return candidate.parent
        raise EmulatorError("Could not find MuMuManager.exe.")

    def adb_port(self, index: int) -> int | None:
        """Ask MuMuManager which ADB port an instance listens on."""

        manager = self.manager_path() / "MuMuManager.exe"
        try:
            result = run_hidden(
                [str(manager), "adb", "-v", str(index)],
                capture_output=True,
                timeout=20,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise EmulatorError(f"MuMuManager.exe did not answer: {exc}") from exc

        output = result.stdout.decode("utf-8", "replace").strip()
        if not output:
            output = result.stderr.decode("utf-8", "replace").strip()

        for token in output.replace(":", " ").replace(",", " ").split():
            if token.isdigit():
                port = int(token)
                if 1024 < port < 65536:
                    return port
        return None

    def discover(self) -> list[EmulatorInstance]:
        """Return the MuMu instances and whether they are running."""

        instances: list[EmulatorInstance] = []
        # Current MuMu releases record each instance's actual ADB port here.
        for folder in self.install_folders():
            for config in (folder / "vms").glob("*/configs/vm_config.json"):
                match = re.search(r"-(\d+)$", config.parent.parent.name)
                if not match:
                    continue
                try:
                    data = json.loads(config.read_text(encoding="utf-8-sig"))
                    port = int(data["vm"]["nat"]["port_forward"]["adb"]["host_port"])
                    if not 1024 < port < 65536:
                        continue
                except (OSError, ValueError, TypeError, KeyError):
                    continue
                index = int(match.group(1))
                instances.append(EmulatorInstance(
                    index=index, name=f"MuMu Player {index}",
                    running=self._port_is_open(port), details={"adb_port": str(port)},
                ))
        if instances:
            return sorted(instances, key=lambda item: item.index)

        for index in range(8):
            try:
                port = self.adb_port(index)
            except EmulatorError:
                if not instances:
                    raise
                break
            if port is None:
                continue
            running = self._port_is_open(port)
            instances.append(
                EmulatorInstance(
                    index=index,
                    name=f"MuMu Player {index}",
                    running=running,
                    pid=-1,
                    width=0,
                    height=0,
                    details={"adb_port": str(port)},
                )
            )
        if not instances:
            raise EmulatorError("MuMuManager.exe reported no emulator instances")
        return instances

    @staticmethod
    def _port_is_open(port: int, timeout: float = 0.4) -> bool:
        """Check whether the emulator is listening on its ADB port."""

        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return True
        except OSError:
            return False

    def preferred_serials(self) -> list[str]:
        """MuMu appears in adb devices as a loopback address."""

        serials: list[str] = []
        try:
            instances = self.discover()
        except EmulatorError:
            return serials
        for instance in instances:
            port = instance.details.get("adb_port")
            if port and instance.running:
                serials.append(f"127.0.0.1:{port}")
        return serials

    # -- native capture ------------------------------------------------- #

    def capture_library(self) -> Path:
        """Return the path of the native capture library."""

        for folder in self.install_folders():
            for relative in (
                "shell/sdk/external_renderer_ipc.dll",
                "nx_device/12.0/shell/sdk/external_renderer_ipc.dll",
                "nx_main/sdk/external_renderer_ipc.dll",
            ):
                candidate = folder / relative
                if candidate.is_file():
                    return candidate
        raise EmulatorError(
            "Could not find external_renderer_ipc.dll. MuMu native capture "
            "needs MuMu 12 version 3.8.13 or newer."
        )

    def open_capture(self, instance: EmulatorInstance):
        """Open a native capture of the MuMu framebuffer.

        The MuMu capture library has to be verified against the installed
        version before it can be used, so this refuses for now.
        """

        raise EmulatorError(
            "Native capture for MuMu Player is not verified yet. Use "
            "LDPlayer, or verify the MuMu capture library first."
        )
