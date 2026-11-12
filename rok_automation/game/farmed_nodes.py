"""Remember which resource nodes were farmed recently.

The world search can return the same node again after the army that
marched to it has arrived: the node is still on the map and its march
route is no longer visible. A fresh farming cycle used to have no
memory of earlier cycles at all, so the very node that was just emptied
was farmed again.

The nodes farmed by an account are therefore remembered with
timestamps in a small file that survives restarts and account
rotation. A node is only skipped while it is recent; afterwards it is
probably depleted anyway, so the search will not return it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_FARMED_NODES_PATH = _PROJECT_ROOT / "state" / "farmed_nodes.json"

# How long a farmed node stays remembered. A march needs roughly this
# long to strip its load from a node, so re-farming earlier than this
# wastes a march slot on an already-gathering node.
DEFAULT_MEMORY_WINDOW_SECONDS = 30 * 60.0


def _resolve(path: Path | None) -> Path:
    return path or DEFAULT_FARMED_NODES_PATH


def _key(coordinates: tuple[int, int]) -> str:
    return f"{coordinates[0]},{coordinates[1]}"


def _parse_key(key: str) -> tuple[int, int] | None:
    try:
        x, y = key.split(",", 1)
        return int(x), int(y)
    except (ValueError, AttributeError):
        return None


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _live_entries(
    entries: dict,
    *,
    window: float,
    now: float,
) -> dict[str, float]:
    live: dict[str, float] = {}
    for key, stamp in entries.items():
        if not isinstance(stamp, (int, float)):
            continue
        if now - stamp > window:
            continue
        live[key] = float(stamp)
    return live


def load_recent_farmed_nodes(
    account: str,
    *,
    window: float = DEFAULT_MEMORY_WINDOW_SECONDS,
    now: float | None = None,
    path: Path | None = None,
) -> dict[tuple[int, int], float]:
    """Map the nodes this account farmed within the window to their times."""

    current = time.time() if now is None else now
    data = _read(_resolve(path))
    entries = data.get(account)
    if not isinstance(entries, dict):
        return {}
    recent: dict[tuple[int, int], float] = {}
    for key, stamp in _live_entries(entries, window=window, now=current).items():
        coordinates = _parse_key(key)
        if coordinates is not None:
            recent[coordinates] = stamp
    return recent


def remember_farmed_node(
    account: str,
    coordinates: tuple[int, int],
    *,
    window: float = DEFAULT_MEMORY_WINDOW_SECONDS,
    now: float | None = None,
    path: Path | None = None,
) -> None:
    """Record that this account just farmed the node at these coordinates."""

    current = time.time() if now is None else now
    destination = _resolve(path)
    data = _read(destination)
    entries = data.get(account)
    if not isinstance(entries, dict):
        entries = {}
    entries = _live_entries(entries, window=window, now=current)
    entries[_key(coordinates)] = current
    data[account] = entries
    _write(data, destination)
