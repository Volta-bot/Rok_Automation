"""Cooperative skip requests shared between the GUI and farming worker."""
from pathlib import Path

DEFAULT_SKIP_PATH = Path(__file__).resolve().parents[2] / "state" / "skip_account.flag"


def request_skip(kind: str, path: Path = DEFAULT_SKIP_PATH) -> None:
    if kind not in {"rok", "gmail"}:
        raise ValueError("Unknown account skip type")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(kind, encoding="utf-8")


def clear_skip(path: Path = DEFAULT_SKIP_PATH) -> None:
    path.unlink(missing_ok=True)


def consume_skip(path: Path = DEFAULT_SKIP_PATH) -> str | None:
    try:
        kind = path.read_text(encoding="utf-8").strip()
        clear_skip(path)
    except FileNotFoundError:
        return None
    return kind if kind in {"rok", "gmail"} else None
