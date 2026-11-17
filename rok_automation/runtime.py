"""Paths and worker commands shared by source and portable builds."""

import os
import sys
from pathlib import Path


def data_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ROKauto"
    return Path(__file__).resolve().parents[1]


def worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).with_name("ROKauto-worker.exe"))]
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        candidate = str(Path(exe).with_name("python.exe"))
        if Path(candidate).is_file():
            exe = candidate
    return [exe, "-u", "-m", "rok_automation"]
