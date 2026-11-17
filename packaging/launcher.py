"""Shared entry point for the windowed launcher and console worker."""

import os
import sys
from pathlib import Path


def main() -> int:
    from rok_automation.runtime import data_root

    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ROK_STATE_DIR", str(root / "state"))
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding="utf-8", errors="replace", write_through=True, line_buffering=True)

    if sys.argv[1:] == ["--self-test"]:
        import json
        import socket
        import tkinter as tk
        from unittest.mock import patch
        import cv2
        import numpy as np
        from rok_automation.vision.account_label import _get_ocr_engine
        from rok_automation.vision.screen import _PROJECT_ROOT
        from rok_automation.ui.app import ROKAutomationApp

        # Prove that OCR can initialize and infer without downloading anything.
        with patch.object(socket.socket, "connect", side_effect=RuntimeError("Offline self-test")):
            engine = _get_ocr_engine()
            frame = np.full((120, 480, 3), 255, dtype=np.uint8)
            cv2.putText(frame, "123456", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
            result = engine(frame)
            if not result.txts:
                raise RuntimeError("OCR self-test recognized no text")
        templates = list((_PROJECT_ROOT / "templates").rglob("*.png"))
        if not templates or any(cv2.imread(str(path)) is None for path in templates):
            raise RuntimeError("Missing or unreadable templates")
        window = tk.Tk()
        window.withdraw()
        app = ROKAutomationApp(window)
        window.update_idletasks()
        from rok_automation.runtime import worker_command
        process = app._spawn_process([*worker_command(), "--help"])
        output, _ = process.communicate(timeout=30)
        if process.returncode != 0 or "--farm" not in output:
            raise RuntimeError("GUI could not launch its bundled worker")
        window.destroy()
        print(json.dumps({"ok": True, "templates": len(templates), "ocr": list(result.txts)}))
        return 0

    if not sys.argv[1:] and Path(sys.executable).stem.lower() != "rokauto-worker":
        sys.argv.append("--gui")
    from rok_automation.__main__ import main as run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
