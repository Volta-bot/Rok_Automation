"""Console output that survives a console with a limited code page.

The automation logs Vietnamese labels such as "Ngô" and "Gỗ". A console
running on a legacy code page cannot encode those characters, and a
plain ``print`` would raise in the middle of a run and abort the routine
that was logging.
"""

from __future__ import annotations

import sys


def safe_print(message: str) -> None:
    """Print a line, replacing any character the console cannot encode."""

    try:
        print(message)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        fallback = message.encode(encoding, "replace").decode(encoding, "replace")
        try:
            print(fallback)
        except UnicodeEncodeError:
            print(message.encode("ascii", "replace").decode("ascii"))
