"""Console output that survives a console with a limited code page.

The automation logs Vietnamese labels such as "Ngô" and "Gỗ". A console
running on a legacy code page cannot encode those characters, and a
plain ``print`` would raise in the middle of a run and abort the routine
that was logging.
"""

from __future__ import annotations

import sys
import re


TAG_COLORS = {
    "Farm": "#74C7EC", "Account": "#CBA6F7", "Gmail": "#F5C2E7",
    "Search": "#89DCEB", "Troops": "#A6E3A1", "City": "#FAB387",
    "Rewards": "#F9E2AF", "Timing": "#94A3B8", "System": "#89B4FA",
    "Info": "#89B4FA", "Success": "#A6E3A1", "Warning": "#F9E2AF",
    "Error": "#F38BA8", "Option": "#CBA6F7", "Planned": "#94A3B8",
    "Capture": "#89DCEB",
}
_ALIASES = {tag.casefold(): tag for tag in TAG_COLORS}
_ALIASES.update({"city tasks": "City", "daily reward": "Rewards", "notice": "Info"})
_TIMESTAMP = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*")


def split_log(message: str, default_tag: str = "Info") -> tuple[str, str]:
    """Normalize legacy prefixes without mistaking bracketed account names for tags."""
    body = str(message).strip()
    while _TIMESTAMP.match(body):
        body = _TIMESTAMP.sub("", body, count=1)
    matched = re.match(r"^\[([^\]]+)\]\s*(.*)$", body, re.DOTALL)
    if matched and matched[1].casefold() in _ALIASES:
        return _ALIASES[matched[1].casefold()], matched[2]
    return _ALIASES.get(default_tag.casefold(), default_tag), body


def format_log(message: str, default_tag: str = "Info") -> str:
    # Internal progress markers are consumed by the GUI, never user-facing logs.
    if str(message).strip() == "[GMAIL_PROGRESS_UPDATED]":
        return str(message).strip()
    tag, body = split_log(message, default_tag)
    return f"[{tag}] {body}"


def safe_print(message: str) -> None:
    """Print a line, replacing any character the console cannot encode."""

    message = format_log(message)
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        fallback = message.encode(encoding, "replace").decode(encoding, "replace")
        try:
            print(fallback)
        except UnicodeEncodeError:
            print(message.encode("ascii", "replace").decode("ascii"))
