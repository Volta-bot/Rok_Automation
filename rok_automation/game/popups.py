"""Recognize and dismiss known accidental game menus."""

from pathlib import Path
from typing import Callable

from rok_automation.game.interface import GameInterface
from rok_automation.vision.screen import ScreenDetector, TemplateHit

_NAVIGATION = Path(__file__).resolve().parents[2] / "templates" / "navigation"


def close_scout_manager(
    game: GameInterface, detector: ScreenDetector, screenshot: Path,
    *, log: Callable[[str], None],
) -> bool:
    """Close the scout menu only after recognizing both its title and X."""
    title = detector.locate_template(
        screenshot, _NAVIGATION / "scout_manager_title.png", confidence_threshold=0.86,
    )
    if not isinstance(title, TemplateHit):
        return False
    close = detector.locate_template(
        screenshot, _NAVIGATION / "scout_manager_close.png", confidence_threshold=0.82,
    )
    if not isinstance(close, TemplateHit):
        log("Scout management is open, but its close button was not found")
        return False
    log("Closing accidentally opened scout management menu")
    game.click(close.bounds, screenshot)
    game.wait(0.5)
    return True
