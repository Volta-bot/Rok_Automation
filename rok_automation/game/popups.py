"""Recognize and dismiss known accidental game menus."""

from pathlib import Path
from typing import Callable

from rok_automation.game.interface import GameInterface
from rok_automation.vision.screen import (
    ScreenDetection, ScreenDetector, ScreenState, TemplateHit, load_gray_frame,
    VIEW_TOGGLE_MIN_CONFIDENCE, VIEW_TOGGLE_TEMPLATES, VIP_REWARD_POPUPS,
)

_NAVIGATION = Path(__file__).resolve().parents[2] / "templates" / "navigation"
_BACK_BUTTON = _NAVIGATION.parent / "daily_reward" / "back_button.png"
_CENTERED_PANEL_CLOSE = _NAVIGATION.parent / "daily_reward" / "vip_close_x.png"
_UNKNOWN_MENU_CONTROLS = (
    (_BACK_BUTTON, "Back arrow", 0.82),
    (_NAVIGATION / "unknown_menu_close_gold.png", "X button", 0.88),
    (_NAVIGATION / "unknown_menu_close_light.png", "X button", 0.88),
    (_CENTERED_PANEL_CLOSE, "X button", 0.90),
)


def locate_view_toggle(
    detector: ScreenDetector, screenshot: Path,
) -> tuple[Path, TemplateHit] | None:
    """Find the fortress or map button, in that order, in either view."""
    for template in VIEW_TOGGLE_TEMPLATES:
        hit = detector.locate_template(
            screenshot, template, confidence_threshold=VIEW_TOGGLE_MIN_CONFIDENCE,
        )
        if isinstance(hit, TemplateHit) and hit.confidence >= VIEW_TOGGLE_MIN_CONFIDENCE:
            return template, hit
    return None


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
    for template in VIEW_TOGGLE_TEMPLATES:
        button = detector.locate_template(
            screenshot, template, confidence_threshold=VIEW_TOGGLE_MIN_CONFIDENCE,
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


def locate_unknown_menu_control(
    detector: ScreenDetector, screenshot: Path, *, close_only: bool = False,
) -> tuple[str, TemplateHit] | None:
    """Find a verified Back arrow or X on an unrecognized screen."""
    frame = load_gray_frame(screenshot)
    if frame is None:
        return None
    frame_height, frame_width = frame.shape[:2]
    for template, label, confidence in _UNKNOWN_MENU_CONTROLS:
        if close_only and template == _BACK_BUTTON:
            continue
        hit = detector.locate_template(
            screenshot, template, confidence_threshold=confidence,
        )
        if not isinstance(hit, TemplateHit):
            continue
        x, y, width, height = hit.bounds
        bottom_limit = 0.12
        if template == _BACK_BUTTON:
            in_corner = x + width <= frame_width * 0.10
        elif template == _CENTERED_PANEL_CLOSE:
            # City building info and VIP use the same inset header and X.
            in_corner = (x >= frame_width * 0.78 and x + width <= frame_width * 0.96
                         and y >= frame_height * 0.06)
            bottom_limit = 0.18
        else:
            in_corner = (x + width <= frame_width * 0.16
                         or x >= frame_width * 0.80)
        if not (0 <= x and 0 <= y and in_corner
                and x + width <= frame_width
                and y + height <= frame_height * bottom_limit):
            continue
        return label, hit
    return None


def back_out_of_unknown_menu(
    game: GameInterface, detector: ScreenDetector, screenshot: Path,
    *, log: Callable[[str], None],
) -> bool:
    """Tap a matched Back arrow or X, including the inset panel header."""
    control = locate_unknown_menu_control(detector, screenshot)
    if control is None:
        return False
    label, hit = control
    log(f"Unrecognized menu: tapping the {label} and detecting again")
    game.click(hit.bounds, screenshot)
    game.wait(0.5)
    return True


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


def close_vip_reward(
    game: GameInterface, screenshot: Path, detection: ScreenDetection,
    *, log: Callable[[str], None], settle_delay: float = 0.5,
) -> bool:
    """Dismiss a VIP reward by tapping its banner."""
    if detection.state not in VIP_REWARD_POPUPS or detection.bounds is None:
        return False
    chest = "metal" if detection.state is ScreenState.VIP_METAL_CHEST_REWARD else "wooden"
    log(f"VIP {chest} chest reward detected. Tapping to dismiss...")
    game.click(detection.bounds, screenshot)
    game.wait(settle_delay)
    return True


def close_chat(
    game: GameInterface, screenshot: Path, detection: ScreenDetection,
    *, log: Callable[[str], None],
) -> None:
    """Dismiss a recognized chat panel using its collapse chevron."""
    if detection.state is ScreenState.CHAT and detection.bounds is not None:
        log("Closing accidentally opened chat panel with the < button")
        game.click(detection.bounds, screenshot)
        game.wait(0.5)
