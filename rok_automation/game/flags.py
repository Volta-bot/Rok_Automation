"""Runtime flags the interface can change while a run is in progress.

The automation runs in its own process, so the interface cannot simply
hand it a new setting. Instead both sides agree on a small file: the
interface writes it, the automation reads it before every account.

That is what makes an option such as *Collect Daily Rewards* work while
a run is going: switching it on takes effect from the next account.
"""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The file the interface writes and the automation reads.
DEFAULT_DAILY_REWARD_FLAG_PATH = _PROJECT_ROOT / "state" / "daily_reward.flag"


def _resolve(path: Path | None) -> Path:
    return path or DEFAULT_DAILY_REWARD_FLAG_PATH


def daily_rewards_enabled(path: Path | None = None) -> bool:
    """Whether daily rewards should be claimed for the next account."""

    try:
        return _resolve(path).is_file()
    except OSError:
        return False


def set_daily_rewards(enabled: bool, path: Path | None = None) -> Path:
    """Turn the daily reward option on or off for the running automation."""

    destination = _resolve(path)
    if enabled:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("enabled", encoding="utf-8")
    else:
        clear_daily_rewards(destination)
    return destination


def clear_daily_rewards(path: Path | None = None) -> None:
    """Remove the daily reward flag."""

    destination = _resolve(path)
    try:
        destination.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
