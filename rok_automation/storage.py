"""Crash-safe replacement of small runtime state files."""

import json
import os
import tempfile
import time
from pathlib import Path


def write_json(path: Path, payload, *, indent: int = 2) -> None:
    """Publish a complete save, retaining the old file if writing fails.

    Unique staging names prevent independent saves from sharing a temporary
    file. This protects file integrity, not concurrent read/modify/write edits.
    """
    encoded = json.dumps(payload, ensure_ascii=False, indent=indent, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.2)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
