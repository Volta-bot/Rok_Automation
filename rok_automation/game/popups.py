"""Recognize and dismiss known accidental game menus."""

from pathlib import Path
from typing import Callable

from rok_automation.game.interface import GameInterface
from rok_automation.vision.screen import ScreenDetector, TemplateHit, load_gray_frame

_NAVIGATION = Path(__file__).resolve().parents[2] / "templates" / "navigation"
_BACK_BUTTON = _NAVIGATION.parent / "daily_reward" / "back_button.png"
_UNKNOWN_MENU_CONTROLS = (
    (_BACK_BUTTON, "Back arrow", 0.82),
    (_NAVIGATION / "unknown_menu_close_gold.png", "X button", 0.88),
    (_NAVIGATION / "unknown_menu_close_light.png", "X button", 0.88),
)


def locate_extended_view(
    detector: ScreenDetector, screenshot: Path,
) -> TemplateHit | None:
    """Recognize the view selector and return its toggle without clicking."""
    icon = detector.locate_template(
        screenshot, _NAVIGATION / "extended_view_mountains.png",
        confidence_threshold=0.88,
    )
    if not isinstance(icon, TemplateHit):
        return None
    for name in ("world_city_button.png", "city_map_button.png"):
        button = detector.locate_template(
            screenshot, _NAVIGATION / name, confidence_threshold=0.68,
        )
        if not isinstance(button, TemplateHit):
            continue
        x, y, width, height = button.bounds
        ix, iy, iw, ih = icon.bounds
        # The mountains selector sits above and to the right of the toggle.
        if not (x + width / 2 < ix + iw / 2 < x + 3 * width
                and y - 3 * height < iy + ih / 2 < y):
            continue
        return button
    return None


def close_extended_view(
    game: GameInterface, detector: ScreenDetector, screenshot: Path,
    *, log: Callable[[str], None],
) -> bool:
    """Collapse the long-press view selector using its map/city button."""
    button = locate_extended_view(detector, screenshot)
    if button is None:
        return False
    log("Closing extended view popup with the map/city button")
    game.click(button.bounds, screenshot)
    game.wait(0.5)
    return True


def back_out_of_unknown_menu(
    game: GameInterface, detector: ScreenDetector, screenshot: Path,
    *, log: Callable[[str], None],
) -> bool:
    """Tap a matched Back arrow or corner X; never guess coordinates."""
    frame = load_gray_frame(screenshot)
    if frame is None:
        return False
    frame_height, frame_width = frame.shape[:2]
    for template, label, confidence in _UNKNOWN_MENU_CONTROLS:
        hit = detector.locate_template(
            screenshot, template, confidence_threshold=confidence,
        )
        if not isinstance(hit, TemplateHit):
            continue
        x, y, width, height = hit.bounds
        if template == _BACK_BUTTON:
            in_corner = x + width <= frame_width * 0.10
        else:
            in_corner = (x + width <= frame_width * 0.16
                         or x >= frame_width * 0.80)
        if not (0 <= x and 0 <= y and in_corner
                and x + width <= frame_width
                and y + height <= frame_height * 0.12):
            continue
        log(f"Unrecognized menu: tapping the {label} and detecting again")
        game.click(hit.bounds, screenshot)
        game.wait(0.5)
        return True
    return False


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


def close_mailbox(
    game: GameInterface, detector: ScreenDetector, screenshot: Path,
    *, log: Callable[[str], None],
) -> bool:
    """Recognize the mailbox tabs and close it using its top-right X."""
    title = detector.locate_template(
        screenshot, _NAVIGATION / "mailbox_tabs.png", confidence_threshold=0.86,
    )
    if not isinstance(title, TemplateHit):
        return False
    close = detector.locate_template(
        screenshot, _NAVIGATION / "mailbox_close.png", confidence_threshold=0.88,
    )
    if not isinstance(close, TemplateHit):
        log("Mailbox detected, but its close button was not found")
        return False
    log("Mailbox detected: tapping the top-right X and detecting again")
    game.click(close.bounds, screenshot)
    game.wait(0.5)
    return True
