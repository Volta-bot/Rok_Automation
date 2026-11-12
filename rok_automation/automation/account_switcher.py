"""Navigate the in-game profile menus and select each linked RoK character."""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from rok_automation.game.interface import GameInterface
from rok_automation.vision.account_label import read_text_detections
from rok_automation.vision.screen import (
    ScreenDetector,
    ScreenState,
    load_color_frame,
    load_gray_frame,
)


_OPEN_MENU_TIMEOUT = 15.0
_ACCOUNT_LOAD_TIMEOUT = 180.0
_MAX_SCROLLS = 32
_MAX_ACTION_ATTEMPTS = 5
_ACTION_RETRY_DELAY = 0.20
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS_BUTTON = _PROJECT_ROOT / "templates/profile/settings_button.png"
_CHARACTER_MENU_BUTTON = (
    _PROJECT_ROOT / "templates/profile/character_menu_button.png"
)
_CHARACTER_LIST_TITLE = (
    _PROJECT_ROOT / "templates/profile/character_list_title.png"
)
_STARRED_ACCOUNTS_HEADER = (
    _PROJECT_ROOT / "templates/profile/starred_accounts_header.png"
)
_REGULAR_ACCOUNTS_HEADER = (
    _PROJECT_ROOT / "templates/profile/regular_accounts_header.png"
)
_LOGIN_CONFIRMATION_TITLE = (
    _PROJECT_ROOT / "templates/profile/login_confirmation_title.png"
)
_CONFIRM_LOGIN_YES = (
    _PROJECT_ROOT / "templates/profile/confirm_login_yes.png"
)
_SELECTED_ACCOUNT_CHECK = (
    _PROJECT_ROOT / "templates/profile/selected_account_check.png"
)


def account_key(name: str) -> str:
    """Normalize OCR accents and punctuation while preserving name casing."""

    name = re.sub(r"\[[^]]*\]", " ", name)
    decomposed = unicodedata.normalize("NFD", name)
    folded = "".join(
        char for char in decomposed
        if not unicodedata.category(char).startswith("M")
    ).replace("đ", "d").replace("Đ", "D")
    return "".join(char for char in folded if char.isalnum())


def _casefolded_account_key(name: str) -> str:
    return account_key(name).casefold()


@dataclass
class AccountTraversal:
    """Position-based cursor for one ordered pass through starred accounts."""

    direction: int | None = None
    current_index: int | None = None
    total_accounts: int | None = None
    skipped_index: int | None = None
    anchored_to_top: bool = False

    def next_target_index(self) -> int | None:
        """Return the next 0-based account index to farm, or None if finished."""

        if self.direction not in {-1, 1} or self.current_index is None:
            return None
        candidate = self.current_index + self.direction
        if self.skipped_index is not None and candidate == self.skipped_index:
            candidate += self.direction
        if candidate < 0:
            return -1 if not self.anchored_to_top else None
        if self.total_accounts is not None and candidate >= self.total_accounts:
            return None
        return candidate

    def has_more_accounts(self) -> bool:
        """Return False only when traversal is initialized and at the final end."""

        if self.direction not in {-1, 1} or self.current_index is None:
            return True
        return self.next_target_index() is not None


class AccountSwitcher:
    """Open the character list and move by the active card's position."""

    def __init__(
        self,
        game: GameInterface,
        detector: ScreenDetector,
        *,
        log=print,
    ) -> None:
        self.game = game
        self.detector = detector
        self.log = log

    def switch_to_next(self, traversal: AccountTraversal) -> str | None:
        """Select the next starred card using 6-account top/bottom pages."""

        self._open_character_list()
        screenshot, frame, top_down_cards, active_position = (
            self._read_page_and_active_position(traversal, max_attempts=1)
        )
        total = traversal.total_accounts
        page_size = len(top_down_cards)
        at_top = self._starred_accounts_header_bounds(frame) is not None
        at_bottom = self._regular_accounts_header_bounds(frame) is not None

        if traversal.direction is None:
            if active_position is not None and (at_top or not at_bottom):
                # Green tick is visible in the first 6 accounts (1/12 .. 6/12).
                start_index = active_position
                if start_index == 0:
                    traversal.direction = 1
                    traversal.current_index = 0
                    target_index = traversal.next_target_index()
                    self._log(
                        f"Starting positional account traversal at 1"
                        f"/{total if total is not None else '?'}; "
                        "direction: top-to-bottom, left-to-right"
                    )
                elif (
                    total is not None
                    and total <= page_size
                    and start_index == total - 1
                ):
                    traversal.direction = -1
                    traversal.current_index = start_index
                    target_index = traversal.next_target_index()
                    self._log(
                        f"Starting positional account traversal at "
                        f"{start_index + 1}/{total}; "
                        "direction: bottom-to-top, right-to-left"
                    )
                else:
                    # Started at 2/12 .. 6/12: nearest end is 1/12 (index 0),
                    # which is already on this top screen—no scroll needed!
                    traversal.skipped_index = start_index
                    traversal.direction = 1
                    target_index = 0
                    self._log(
                        f"Started mid-list at {start_index + 1}"
                        f"/{total if total is not None else '?'}; "
                        f"moving to nearest end (1/{total if total is not None else '?'}) "
                        "and farming top-to-bottom, left-to-right"
                    )
            else:
                # Green tick is not in the first 6 accounts (or list opened at
                # the bottom): scroll ALL the way to the bottom to see 7/12..12/12.
                if active_position is None:
                    screenshot, frame, top_down_cards = (
                        self._scroll_all_the_way(
                            traversal,
                            toward_list_end=True,
                            until_check_gone=False,
                            initial=(screenshot, frame, top_down_cards),
                        )
                    )
                    (
                        screenshot,
                        frame,
                        top_down_cards,
                        active_position,
                    ) = self._read_page_and_active_position(
                        traversal,
                        initial=(screenshot, frame, top_down_cards),
                    )
                    total = traversal.total_accounts
                    page_size = len(top_down_cards)
                    if active_position is None:
                        raise RuntimeError(
                            "Could not locate the green check on any starred account"
                        )
                    at_top = False
                    at_bottom = True

                bottom_start = (
                    max(0, total - page_size)
                    if total is not None
                    else page_size
                )
                start_index = bottom_start + active_position
                if active_position == page_size - 1:
                    traversal.direction = -1
                    traversal.current_index = start_index
                    target_index = traversal.next_target_index()
                    self._log(
                        f"Starting positional account traversal at "
                        f"{start_index + 1}"
                        f"/{total if total is not None else '?'}; "
                        "direction: bottom-to-top, right-to-left"
                    )
                else:
                    # Started at 7/12 .. 11/12: nearest end is 12/12, which is
                    # already at the end of this bottom screen—no scroll needed!
                    traversal.skipped_index = start_index
                    traversal.direction = -1
                    target_index = bottom_start + page_size - 1
                    self._log(
                        f"Started mid-list at {start_index + 1}"
                        f"/{total if total is not None else '?'}; "
                        f"moving to nearest end ({target_index + 1}"
                        f"/{total if total is not None else '?'}) "
                        "and farming bottom-to-top, right-to-left"
                    )
        else:
            # Subsequent switches: if the green tick is visible on the top 6
            # page (1/12 .. 6/12), sync current_index directly to active_position.
            if active_position is not None and (at_top or not at_bottom):
                if active_position != traversal.skipped_index:
                    traversal.current_index = active_position
            elif active_position is not None and at_bottom and total is not None:
                bottom_idx = max(0, total - page_size) + active_position
                if bottom_idx != traversal.skipped_index:
                    traversal.current_index = bottom_idx
            elif active_position is None and (at_top or not at_bottom):
                # Green tick is not in the first 6 accounts: scroll down
                # immediately and scan for the green tick in the last 6 accounts.
                screenshot, frame, top_down_cards = self._scroll_all_the_way(
                    traversal,
                    toward_list_end=True,
                    until_check_gone=False,
                    initial=(screenshot, frame, top_down_cards),
                )
                (
                    screenshot,
                    frame,
                    top_down_cards,
                    active_position,
                ) = self._read_page_and_active_position(
                    traversal,
                    initial=(screenshot, frame, top_down_cards),
                )
                total = traversal.total_accounts
                page_size = len(top_down_cards)
                at_top = False
                at_bottom = True
                if active_position is not None and total is not None:
                    bottom_idx = max(0, total - page_size) + active_position
                    if bottom_idx != traversal.skipped_index:
                        traversal.current_index = bottom_idx
            target_index = traversal.next_target_index()

        if target_index is None:
            self._log(
                "Reached the end of the starred accounts list; "
                "no more accounts to farm"
            )
            return None

        # Rule 1: If target_index is in the first 6 accounts (0..5, i.e. 1/12..6/12):
        # - For 1/12 -> 5/12, if the green tick is already seen at the top, do NOT
        #   scroll at all; just select the next account immediately.
        # - If we are coming from the bottom (7/12 -> 6/12) and the list is still
        #   at the bottom (green tick on 7/12 visible or not at top), scroll ALL
        #   the way to the top until the green tick is gone.
        if target_index < page_size:
            if not at_top and (at_bottom or active_position is not None):
                screenshot, frame, top_down_cards = self._scroll_all_the_way(
                    traversal,
                    toward_list_end=False,
                    until_check_gone=(active_position is not None),
                    initial=(screenshot, frame, top_down_cards),
                )
            card_offset = target_index
        else:
            # Rule 2 & 3: For 6/12 -> 7/12 (or any target in 7/12 .. 12/12),
            # scroll ALL the way to the bottom until the green tick on 1/12..6/12
            # is no longer visible, then select from the bottom 6 accounts.
            if at_top or not at_bottom:
                screenshot, frame, top_down_cards = self._scroll_all_the_way(
                    traversal,
                    toward_list_end=True,
                    until_check_gone=(
                        active_position is not None and at_top
                    ),
                    initial=(screenshot, frame, top_down_cards),
                )
                total = traversal.total_accounts
                page_size = len(top_down_cards)

            bottom_start = (
                max(0, total - page_size)
                if total is not None
                else page_size
            )
            card_offset = target_index - bottom_start

        if not (0 <= card_offset < len(top_down_cards)):
            raise RuntimeError(
                f"Target account {target_index + 1} is out of range for the "
                f"visible {len(top_down_cards)} cards"
            )

        name, bounds = top_down_cards[card_offset]
        self._log(
            f"Switching to starred account "
            f"{target_index + 1}"
            f"/{total if total is not None else '?'}: {name}"
        )
        self._select_card_and_confirm_login(
            traversal,
            target_index=target_index,
            card_offset=card_offset,
            initial_screenshot=screenshot,
            initial_bounds=bounds,
        )
        self._wait_for_game(name)
        traversal.current_index = target_index
        return name

    def _read_page_and_active_position(
        self,
        traversal: AccountTraversal,
        *,
        initial: (
            tuple[Path, Any, list[tuple[str, tuple[int, int, int, int]]]]
            | None
        ) = None,
        max_attempts: int = _MAX_ACTION_ATTEMPTS,
    ) -> tuple[
        Path,
        Any,
        list[tuple[str, tuple[int, int, int, int]]],
        int | None,
    ]:
        """Locate the green tick on the current page."""

        screenshot, frame, top_down_cards = (
            initial
            if initial is not None
            else self._read_visible_page(traversal)
        )
        for attempt in range(max(1, max_attempts)):
            active_bounds = self._selected_account_bounds(
                screenshot,
                frame,
                top_down_cards,
            )
            active_position = (
                self._card_index(top_down_cards, active_bounds)
                if active_bounds is not None
                else None
            )
            if active_position is not None:
                return screenshot, frame, top_down_cards, active_position
            if attempt + 1 < max_attempts:
                self.game.wait(_ACTION_RETRY_DELAY)
                screenshot, frame, top_down_cards = self._read_visible_page(
                    traversal
                )
        return screenshot, frame, top_down_cards, None

    def _read_visible_page(
        self,
        traversal: AccountTraversal,
    ) -> tuple[Path, Any, list[tuple[str, tuple[int, int, int, int]]]]:
        screenshot, frame = self._capture()
        cards = self._read_account_cards(frame)
        starred_account_total = self._read_starred_account_total(frame)
        if starred_account_total is not None:
            traversal.total_accounts = starred_account_total

        for _retry in range(_MAX_ACTION_ATTEMPTS - 1):
            if cards:
                break
            self.game.wait(_ACTION_RETRY_DELAY)
            screenshot, frame = self._capture()
            cards = self._read_account_cards(frame)
            starred_account_total = self._read_starred_account_total(frame)
            if starred_account_total is not None:
                traversal.total_accounts = starred_account_total
        if not cards:
            raise RuntimeError(
                "Could not read any visible starred accounts after retries"
            )
        traversal.anchored_to_top = True
        top_down_cards = self._order_account_cards(
            cards,
            frame_height=frame.shape[0],
            direction=1,
        )
        return screenshot, frame, top_down_cards

    def _scroll_all_the_way(
        self,
        traversal: AccountTraversal,
        *,
        toward_list_end: bool,
        until_check_gone: bool,
        initial: (
            tuple[Path, Any, list[tuple[str, tuple[int, int, int, int]]]]
            | None
        ) = None,
    ) -> tuple[Path, Any, list[tuple[str, tuple[int, int, int, int]]]]:
        """Scroll all the way to the bottom or top until settled."""

        screenshot, frame, top_down_cards = (
            initial
            if initial is not None
            else self._read_visible_page(traversal)
        )
        for _ in range(8):
            at_top = self._starred_accounts_header_bounds(frame) is not None
            at_bottom = self._regular_accounts_header_bounds(frame) is not None
            active_bounds = self._selected_account_bounds(
                screenshot,
                frame,
                top_down_cards,
            )
            if toward_list_end and at_bottom and (
                not until_check_gone or active_bounds is None
            ):
                break
            if not toward_list_end and at_top and (
                not until_check_gone or active_bounds is None
            ):
                break

            height, width = frame.shape[:2]
            start_y, end_y = (
                (0.76, 0.24) if toward_list_end else (0.24, 0.76)
            )
            before_keys = [
                _casefolded_account_key(name) for name, _ in top_down_cards
            ]
            self.game.drag(
                (round(width * 0.51), round(height * start_y)),
                (round(width * 0.51), round(height * end_y)),
                screenshot,
            )
            self.game.wait(0.35)
            screenshot, frame, top_down_cards = self._read_visible_page(
                traversal
            )
            after_keys = [
                _casefolded_account_key(name) for name, _ in top_down_cards
            ]
            if after_keys == before_keys:
                break

        return screenshot, frame, top_down_cards

    def _open_character_list(self) -> None:
        """Drive Top-Left Portrait → Cài đặt → Nhân vật → Character List by state.

        Every iteration detects which menu/screen is currently showing and
        presses the button for *that* screen. If a click misses or a menu closes
        back to an earlier screen, the loop re-detects the current state and
        continues from there.
        """

        deadline = time.monotonic() + _OPEN_MENU_TIMEOUT * 2
        clicks = {
            "portrait": 0,
            "settings": 0,
            "characters": 0,
        }
        last_state = "unknown"

        while time.monotonic() < deadline:
            screenshot, frame = self._capture()
            state, bounds, score = self._detect_menu_state(screenshot, frame)
            last_state = state

            if state == "character_list":
                self.game.wait(0.20)
                return

            if state == "settings_menu" and bounds is not None:
                clicks["characters"] += 1
                if clicks["characters"] > _MAX_ACTION_ATTEMPTS:
                    raise RuntimeError(
                        f"Nhân vật button did not open character list after "
                        f"{_MAX_ACTION_ATTEMPTS} clicks"
                    )
                retry_note = (
                    f" (retry {clicks['characters'] - 1})"
                    if clicks["characters"] > 1
                    else ""
                )
                self._log(
                    f"Clicking characters button (match={score:.3f}){retry_note}"
                )
                self.game.click(bounds, screenshot)
                self._wait_for_menu_state_change("settings_menu")
                continue

            if state == "governor_profile" and bounds is not None:
                clicks["settings"] += 1
                if clicks["settings"] > _MAX_ACTION_ATTEMPTS:
                    raise RuntimeError(
                        f"Cài đặt button did not open settings after "
                        f"{_MAX_ACTION_ATTEMPTS} clicks"
                    )
                retry_note = (
                    f" (retry {clicks['settings'] - 1})"
                    if clicks["settings"] > 1
                    else ""
                )
                if score > 0.0:
                    self._log(
                        f"Clicking settings button (match={score:.3f}){retry_note}"
                    )
                else:
                    self._log(
                        f"Clicking 'Cài đặt' label on profile screen{retry_note}"
                    )
                self.game.click(bounds, screenshot)
                self._wait_for_menu_state_change("governor_profile")
                continue

            if state == "map_or_city" and bounds is not None:
                clicks["portrait"] += 1
                if clicks["portrait"] > _MAX_ACTION_ATTEMPTS:
                    raise RuntimeError(
                        f"Governor portrait did not open profile after "
                        f"{_MAX_ACTION_ATTEMPTS} clicks"
                    )
                retry_note = (
                    f" (retry {clicks['portrait'] - 1})"
                    if clicks["portrait"] > 1
                    else ""
                )
                if clicks["portrait"] > 1:
                    self._log(
                        f"Clicking top-left governor icon{retry_note}"
                    )
                self.game.click(bounds, screenshot)
                self._wait_for_menu_state_change("map_or_city")
                continue

            self.game.wait(_ACTION_RETRY_DELAY)

        raise RuntimeError(
            f"Timed out opening the character selection screen; "
            f"last detected state was {last_state}"
        )

    def _detect_menu_state(
        self,
        screenshot: Path,
        frame: Any,
    ) -> tuple[str, tuple[int, int, int, int] | None, float]:
        """Classify which step of the account-switcher menu chain is on screen.

        Checks from the deepest modal back to the base map/city screen so an
        open modal always takes priority over background UI.
        """

        login_yes = self._find_login_confirmation_yes(frame)
        if login_yes is not None:
            bounds, _title_score, yes_score = login_yes
            return "login_confirmation", bounds, yes_score

        if (
            self._starred_accounts_header_bounds(frame) is not None
            or self._regular_accounts_header_bounds(frame) is not None
        ):
            return "character_list", None, 1.0

        char_hit = self.detector.locate_template(
            screenshot,
            _CHARACTER_MENU_BUTTON,
            confidence_threshold=0.70,
        )
        if char_hit is not None:
            return "settings_menu", char_hit.bounds, char_hit.confidence

        settings_hit = self.detector.locate_template(
            screenshot,
            _SETTINGS_BUTTON,
            confidence_threshold=0.70,
        )
        if settings_hit is not None:
            return "governor_profile", settings_hit.bounds, settings_hit.confidence

        height, width = frame.shape[:2]
        detections = read_text_detections(
            frame,
            (
                round(width * 0.70),
                round(height * 0.65),
                round(width * 0.30),
                height - round(height * 0.65),
            ),
            upscale=2.0,
        )
        for text, bounds, _score in detections:
            folded = _fold(text)
            if any(k in folded for k in ("cai dat", "cai dt", "cai d")):
                return "governor_profile", bounds, 0.0

        detection = self.detector.detect(
            screenshot,
            read_account=False,
            candidate_states={ScreenState.CITY, ScreenState.WORLD_MAP},
        )
        if detection.state in {ScreenState.CITY, ScreenState.WORLD_MAP}:
            portrait_bounds = (
                round(width * 0.005),
                round(height * 0.002),
                round(width * 0.075),
                round(height * 0.12),
            )
            return "map_or_city", portrait_bounds, detection.confidence

        return "unknown", None, 0.0

    def _wait_for_menu_state_change(self, previous_state: str) -> None:
        """Wait briefly until the screen advances past ``previous_state``."""

        self.game.wait(_ACTION_RETRY_DELAY)
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            screenshot, frame = self._capture()
            state, _bounds, _score = self._detect_menu_state(screenshot, frame)
            if state != previous_state and state != "unknown":
                return
            self.game.wait(self.game.timing.poll_interval)

    def _select_card_and_confirm_login(
        self,
        traversal: AccountTraversal,
        *,
        target_index: int,
        card_offset: int,
        initial_screenshot: Path,
        initial_bounds: tuple[int, int, int, int],
    ) -> None:
        """Click the target character card and confirm login ('Có') by state.

        If clicking the character card misses and the character selection screen
        remains open, re-click the card. If the menu falls back to an earlier
        screen, re-open the character list and continue.
        """

        self.game.click(initial_bounds, initial_screenshot)
        card_clicks = 1
        yes_clicks = 0
        confirmed_login = False
        deadline = time.monotonic() + 20.0

        while time.monotonic() < deadline:
            self.game.wait(_ACTION_RETRY_DELAY)
            screenshot, frame = self._capture()
            state, bounds, _score = self._detect_menu_state(screenshot, frame)

            if state == "login_confirmation" and bounds is not None:
                login_yes = self._find_login_confirmation_yes(frame)
                if login_yes is not None:
                    yes_bounds, title_score, yes_score = login_yes
                    yes_clicks += 1
                    if yes_clicks > _MAX_ACTION_ATTEMPTS:
                        raise RuntimeError(
                            f"Login confirmation 'Có' button did not respond "
                            f"after {_MAX_ACTION_ATTEMPTS} clicks"
                        )
                    self._log(
                        f"Login confirmation dialog detected "
                        f"(title={title_score:.3f}, yes={yes_score:.3f}); "
                        "clicking Có and waiting for game load"
                    )
                    self.game.click(yes_bounds, screenshot)
                    confirmed_login = True
                    self._wait_for_menu_state_change("login_confirmation")
                    return

            if confirmed_login and state != "login_confirmation":
                return

            if state == "character_list":
                if card_clicks >= _MAX_ACTION_ATTEMPTS:
                    raise RuntimeError(
                        "Timed out waiting for the account-login confirmation dialog; "
                        f"character card did not open dialog after {card_clicks} clicks"
                    )
                cards = self._order_account_cards(
                    self._read_account_cards(frame),
                    frame_height=frame.shape[0],
                    direction=1,
                )
                if 0 <= card_offset < len(cards):
                    _card_name, card_bounds = cards[card_offset]
                    card_clicks += 1
                    self._log(
                        f"Character list still open; clicking account card again "
                        f"(retry {card_clicks - 1})"
                    )
                    self.game.click(card_bounds, screenshot)
                    continue

            if state in {"settings_menu", "governor_profile", "map_or_city"}:
                if confirmed_login:
                    return
                self._log(
                    f"Menu fell back to {state} before login confirmation; "
                    "re-opening character list"
                )
                self._open_character_list()
                if target_index >= 6:
                    self._scroll_all_the_way(
                        traversal,
                        toward_list_end=True,
                        until_check_gone=False,
                    )
                continue

        if not confirmed_login:
            raise RuntimeError(
                "Timed out waiting for the account-login confirmation dialog; "
                "no unverified button was clicked"
            )

    @staticmethod
    def _find_login_confirmation_yes(
        frame: Any,
    ) -> tuple[tuple[int, int, int, int], float, float] | None:
        """Return (yes_bounds, title_score, yes_score) if the login modal is open."""

        title_template = cv2.imread(
            str(_LOGIN_CONFIRMATION_TITLE),
            cv2.IMREAD_GRAYSCALE,
        )
        yes_template = cv2.imread(
            str(_CONFIRM_LOGIN_YES),
            cv2.IMREAD_GRAYSCALE,
        )
        if title_template is None or yes_template is None:
            raise RuntimeError("Missing login confirmation templates")

        frame_height, frame_width = frame.shape[:2]
        gray = (
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if frame.ndim == 3
            else frame
        )
        title_left = round(frame_width * 0.28)
        title_top = round(frame_height * 0.12)
        title_crop = gray[
            title_top : round(frame_height * 0.35),
            title_left : round(frame_width * 0.72),
        ]
        yes_left = round(frame_width * 0.48)
        yes_top = round(frame_height * 0.58)
        yes_crop = gray[
            yes_top : round(frame_height * 0.82),
            yes_left : round(frame_width * 0.80),
        ]
        title_score, _ = ScreenDetector._best_scale_match(
            title_crop,
            title_template,
            minimum_scale=0.75,
            maximum_scale=1.20,
        )
        yes_score, yes_match = ScreenDetector._best_scale_match(
            yes_crop,
            yes_template,
            minimum_scale=0.75,
            maximum_scale=1.20,
        )
        if title_score >= 0.80 and yes_score >= 0.78:
            yes_height, yes_width = ScreenDetector._scaled_size(
                yes_template,
                yes_match[2],
            )
            button_bounds = (
                yes_left + yes_match[0],
                yes_top + yes_match[1],
                yes_width,
                yes_height,
            )
            return button_bounds, title_score, yes_score
        return None

    @staticmethod
    def _find_dialog_confirmation(
        frame: Any,
    ) -> tuple[int, int, int, int] | None:
        """Find generic in-game modal confirmation buttons (Xác nhận, Đồng ý, Có, OK)."""

        height, width = frame.shape[:2]
        detections = read_text_detections(
            frame,
            (
                round(width * 0.30),
                round(height * 0.45),
                round(width * 0.40),
                round(height * 0.35),
            ),
            upscale=1.8,
        )
        for text, bounds, score in detections:
            if score < 0.35:
                continue
            compact = "".join(char for char in _fold(text) if char.isalnum())
            if compact in {"xacnhan", "xacnhn", "dongy", "dngy", "co", "ok", "tieptuc"}:
                return bounds
        return None

    def _wait_for_text(self, keyword: str) -> None:
        deadline = time.monotonic() + _OPEN_MENU_TIMEOUT
        while time.monotonic() < deadline:
            screenshot, frame = self._capture()
            detections = read_text_detections(
                frame,
                (0, 0, frame.shape[1], frame.shape[0]),
                upscale=1.8,
            )
            if any(keyword in _fold(text) for text, _box, _score in detections):
                return
            self.game.wait(self.game.timing.poll_interval)
        raise RuntimeError(f"Timed out waiting for menu text {keyword!r}")

    def _read_account_cards(
        self,
        frame: Any,
    ) -> list[tuple[str, tuple[int, int, int, int]]]:
        height, width = frame.shape[:2]
        min_card_y = round(height * 0.17)
        starred_bounds = self._starred_accounts_header_bounds(frame)
        if starred_bounds is not None:
            min_card_y = max(
                min_card_y,
                starred_bounds[1] + round(starred_bounds[3] * 0.75),
            )

        # Keep card names inside the unclipped viewport so the card and its
        # green checkmark (roughly 0.08 * height below the name) are fully visible.
        max_card_y = round(height * 0.79)
        regular_bounds = self._regular_accounts_header_bounds(frame)
        if regular_bounds is not None:
            max_card_y = min(
                max_card_y,
                regular_bounds[1] - round(height * 0.02),
            )
        if max_card_y <= min_card_y:
            return []

        detections = read_text_detections(
            frame,
            (
                round(width * 0.10),
                round(height * 0.15),
                round(width * 0.82),
                round(height * 0.73),
            ),
            upscale=3.0,
        )

        # Every character card has a kingdom (#1234 ...) and power
        # (Sức mạnh: ...) line below its governor name. Anchor card detection
        # to those sublines so labels on other screens are never mistaken for
        # character cards.
        card_subline_anchors: list[tuple[int, int]] = []
        for text, bounds, confidence in detections:
            if confidence < 0.25:
                continue
            folded = _fold(text)
            stripped = text.strip()
            if (
                stripped.startswith("#")
                or "vuong" in folded
                or "vung" in folded
                or "quoc" in folded
                or "quc" in folded
                or "suc manh" in folded
                or "sc mnh" in folded
            ):
                card_subline_anchors.append((bounds[0], bounds[1]))

        cards = []
        for text, bounds, confidence in detections:
            stripped = text.strip()
            folded = _fold(stripped)
            x, y, _box_width, _box_height = bounds
            if confidence < 0.35 or len(account_key(stripped)) < 3:
                continue
            if not (min_card_y <= y <= max_card_y):
                continue

            normalized_x = x / width
            in_left_col = 0.18 <= normalized_x <= 0.38
            in_right_col = 0.53 <= normalized_x <= 0.74
            if not (in_left_col or in_right_col):
                continue

            if (
                stripped.startswith("#")
                or re.fullmatch(r"[\W_\d]+", stripped)
                or re.search(r"\d+\.\d{3}", stripped)
            ):
                continue
            if any(
                phrase in folded
                for phrase in (
                    "nhan vat",
                    "nhn vt",
                    "ngoi sao",
                    "thuong",
                    "thung",
                    "tao nhan vat",
                    "to nhn vt",
                    "cai dat chung",
                    "cai dat chien dau",
                    "cai dat",
                    "tim thong doc",
                    "vuong quoc",
                    "vung quoc",
                    "quc ",
                    "suc manh",
                    "sc mnh",
                    "manh:",
                    "mnh:",
                    "thong bao",
                    "tai khoan",
                    "ho tro khach hang",
                    "huong dan choi",
                    "ngon ngu",
                    "cong dong",
                    "diem hanh vi",
                    "bieu tuong cam xuc",
                )
            ):
                continue

            above_card_subline = any(
                abs(anchor_x - x) <= width * 0.10
                and height * 0.015 <= (anchor_y - y) <= height * 0.14
                for anchor_x, anchor_y in card_subline_anchors
            )
            if not above_card_subline:
                continue

            # Click the OCR text itself, which lies inside the corresponding
            # account card, rather than guessing a fixed row/column position.
            cards.append((stripped, bounds))

        cards.sort(key=lambda item: (item[1][1], item[1][0]))
        deduplicated: list[tuple[str, tuple[int, int, int, int]]] = []
        for name, bounds in cards:
            x, y, box_width, box_height = bounds
            center_x = x + box_width / 2
            center_y = y + box_height / 2
            same_card = any(
                abs(center_x - (prior_x + prior_width / 2)) <= width * 0.15
                and abs(center_y - (prior_y + prior_height / 2))
                <= height * 0.07
                for _prior_name, (prior_x, prior_y, prior_width, prior_height)
                in deduplicated
            )
            if not same_card:
                # Keep separate cards even when their names are identical.
                deduplicated.append((name, bounds))
        return deduplicated

    def _read_starred_account_total(self, frame: Any) -> int | None:
        """Read the count at the right side of the visible starred-account header."""

        header_bounds = self._starred_accounts_header_bounds(frame)
        if header_bounds is None:
            return None

        height, width = frame.shape[:2]
        _hx, hy, _hw, hh = header_bounds
        detections = read_text_detections(
            frame,
            (
                round(width * 0.72),
                round(height * 0.16),
                round(width * 0.25),
                round(height * 0.18),
            ),
            upscale=2.5,
        )
        for text, bounds, confidence in detections:
            # OCR sometimes groups the chevron with the numeral (for example,
            # "3^"), so accept a standalone one- or two-digit number inside
            # the vertical band of the matched starred-accounts header.
            count = re.search(r"(?<!\d)(\d{1,2})(?!\d)", text)
            if count is None or confidence < 0.25:
                continue
            _x, y, _box_width, _box_height = bounds
            if hy <= y <= hy + hh:
                return int(count.group(1))
        return None

    def _selected_account_bounds(
        self,
        screenshot: Path,
        frame: Any,
        cards: list[tuple[str, tuple[int, int, int, int]]],
    ) -> tuple[int, int, int, int] | None:
        """Find the card with the green check marking the active character."""

        if not cards:
            return None
        height, width = frame.shape[:2]

        # Primary detector: inspect each detected card's bottom-left portrait
        # region for the bright green checkmark badge. This avoids full-screen
        # grayscale template false positives and works across all resolutions
        # and custom avatar frames.
        if frame.ndim == 3:
            best_ratio = 0.0
            best_bounds: tuple[int, int, int, int] | None = None
            for _name, bounds in cards:
                x, y, _box_width, _box_height = bounds
                rx1 = max(0, x - round(width * 0.145))
                rx2 = max(0, x - round(width * 0.055))
                ry1 = max(0, y + round(height * 0.025))
                ry2 = min(height, y + round(height * 0.125))
                roi = frame[ry1:ry2, rx1:rx2]
                if roi.size == 0:
                    continue
                hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                green_mask = cv2.inRange(
                    hsv,
                    np.array((35, 90, 90), dtype=np.uint8),
                    np.array((88, 255, 255), dtype=np.uint8),
                )
                ratio = float(np.mean(green_mask > 0))
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_bounds = bounds
            if best_ratio >= 0.045 and best_bounds is not None:
                return best_bounds

        gray = (
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if frame.ndim == 3
            else frame
        )
        template = cv2.imread(
            str(_SELECTED_ACCOUNT_CHECK),
            cv2.IMREAD_GRAYSCALE,
        )
        if gray is None or template is None:
            return None

        score, (check_x, check_y, scale) = ScreenDetector._best_scale_match(
            gray,
            template,
            minimum_scale=0.55,
            maximum_scale=1.25,
        )
        if score < 0.70:
            return None

        check_height, check_width = ScreenDetector._scaled_size(template, scale)
        if frame.ndim == 3:
            patch = frame[
                check_y : check_y + check_height,
                check_x : check_x + check_width,
            ]
            if patch.size == 0:
                return None
            hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            green_mask = cv2.inRange(
                hsv,
                np.array((35, 90, 90), dtype=np.uint8),
                np.array((88, 255, 255), dtype=np.uint8),
            )
            if float(np.mean(green_mask > 0)) < 0.15:
                return None

        check_center_x = check_x + check_width / 2
        check_center_y = check_y + check_height / 2
        candidates = []
        for _name, bounds in cards:
            x, y, _box_width, box_height = bounds
            # The green check sits on the bottom-left of its card's portrait,
            # so the card's name text is always to the right of `check_center_x`
            # and slightly above `check_center_y`.
            horizontal_offset = x - check_center_x
            vertical_offset = check_center_y - (y + box_height / 2)
            if not (width * 0.03 <= horizontal_offset <= width * 0.22):
                continue
            if not (height * 0.02 <= vertical_offset <= height * 0.12):
                continue
            distance = (
                (vertical_offset / height) ** 2
                + (horizontal_offset / width) ** 2
            )
            candidates.append((distance, bounds))
        if not candidates:
            return None
        _distance, bounds = min(candidates)
        return bounds

    @staticmethod
    def _card_index(
        cards: list[tuple[str, tuple[int, int, int, int]]],
        bounds: tuple[int, int, int, int],
    ) -> int | None:
        return next(
            (index for index, (_name, card_bounds) in enumerate(cards)
             if card_bounds == bounds),
            None,
        )

    @staticmethod
    def _order_account_cards(
        cards: list[tuple[str, tuple[int, int, int, int]]],
        *,
        frame_height: int,
        direction: int,
    ) -> list[tuple[str, tuple[int, int, int, int]]]:
        """Order cards row-major, optionally reversing for bottom-up traversal."""

        rows: list[list[tuple[str, tuple[int, int, int, int]]]] = []
        row_centers: list[float] = []
        row_tolerance = frame_height * 0.065
        for card in sorted(cards, key=lambda item: item[1][1]):
            _name, (_x, y, _width, height) = card
            center_y = y + height / 2
            if not rows or abs(center_y - row_centers[-1]) > row_tolerance:
                rows.append([card])
                row_centers.append(center_y)
            else:
                rows[-1].append(card)
                row_centers[-1] = sum(
                    bounds[1] + bounds[3] / 2 for _label, bounds in rows[-1]
                ) / len(rows[-1])

        for row in rows:
            row.sort(key=lambda item: item[1][0])

        ordered = [card for row in rows for card in row]
        if direction < 0:
            ordered.reverse()
        return ordered

    def _scroll_account_list(
        self,
        screenshot: Path,
        frame: Any,
        *,
        toward_list_end: bool,
    ) -> int:
        height, width = frame.shape[:2]
        before_cards = self._order_account_cards(
            self._read_account_cards(frame),
            frame_height=height,
            direction=1,
        )
        # The cards are arranged in rows about 19% of the frame high. Move
        # roughly one row per gesture so the next viewport overlaps the current one.
        start_y, end_y = (
            (0.68, 0.55) if toward_list_end else (0.55, 0.68)
        )
        self.game.drag(
            (round(width * 0.51), round(height * start_y)),
            (round(width * 0.51), round(height * end_y)),
            screenshot,
        )
        self.game.wait(0.35)
        _new_screenshot, after_frame = self._capture()
        after_cards = self._order_account_cards(
            self._read_account_cards(after_frame),
            frame_height=height,
            direction=1,
        )
        if not before_cards or not after_cards:
            return 0

        pairs: list[tuple[int, int]] = []
        for before_idx, (before_name, before_bounds) in enumerate(before_cards):
            before_key = _casefolded_account_key(before_name)
            before_y = before_bounds[1] + before_bounds[3] / 2
            best_pair: tuple[float, int, int] | None = None
            for after_idx, (after_name, after_bounds) in enumerate(after_cards):
                if (before_idx % 2) != (after_idx % 2):
                    continue
                if _casefolded_account_key(after_name) != before_key:
                    continue
                after_y = after_bounds[1] + after_bounds[3] / 2
                dy = round(before_y - after_y)
                slot_shift = before_idx - after_idx
                # Prefer matching the card instance whose vertical movement
                # matches the slot shift (each row is ~0.188 * height).
                expected_dy = (slot_shift / 2.0) * (height * 0.188)
                err = abs(dy - expected_dy)
                if best_pair is None or err < best_pair[0]:
                    best_pair = (err, slot_shift, dy)
            if best_pair is not None:
                pairs.append((best_pair[1], best_pair[2]))

        if pairs:
            median_slot_shift = round(
                float(np.median([slot for slot, _dy in pairs]))
            )
            median_dy = float(np.median([dy for _slot, dy in pairs]))
            if median_slot_shift == 0 and abs(median_dy) < height * 0.06:
                return 0
            if median_slot_shift != 0 and (
                (median_slot_shift > 0) == toward_list_end
            ):
                return median_slot_shift
            if abs(median_dy) >= height * 0.06 and (
                (median_dy > 0) == toward_list_end
            ):
                rows = max(1, round(abs(median_dy) / (height * 0.185)))
                return 2 * rows if toward_list_end else -2 * rows
            return 0

        before_keys = [_casefolded_account_key(name) for name, _ in before_cards]
        after_keys = [_casefolded_account_key(name) for name, _ in after_cards]
        if before_keys != after_keys:
            return len(before_cards) if toward_list_end else -len(before_cards)
        return 0

    @staticmethod
    def _list_viewport_gray(frame: Any) -> Any:
        height, width = frame.shape[:2]
        crop = frame[
            round(height * 0.18) : round(height * 0.85),
            round(width * 0.13) : round(width * 0.85),
        ]
        if crop.ndim == 3:
            crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(crop, (5, 5), 0)

    def _visible_account_signature(
        self,
        frame: Any,
    ) -> tuple[tuple[str, int, int], ...]:
        """Return normalized card keys and coarse positions to verify scrolling."""

        height, width = frame.shape[:2]
        return tuple(
            (
                _casefolded_account_key(name),
                round(bounds[0] / width * 20),
                round(bounds[1] / height * 20),
            )
            for name, bounds in self._read_account_cards(frame)
        )

    def _wait_for_game(self, expected_name: str) -> None:
        deadline = time.monotonic() + _ACCOUNT_LOAD_TIMEOUT
        next_progress_log = time.monotonic() + 15.0
        reported_character_list = False
        while time.monotonic() < deadline:
            screenshot, frame = self._capture()
            login_yes = self._find_login_confirmation_yes(frame)
            if login_yes is not None:
                yes_bounds, _title_score, _yes_score = login_yes
                self._log("Login confirmation dialog still open; clicking Có again")
                self.game.click(yes_bounds, screenshot)
                self.game.wait(0.40)
                continue

            dialog_confirm = self._find_dialog_confirmation(frame)
            if dialog_confirm is not None:
                self._log("In-game confirmation dialog detected; clicking confirm")
                self.game.click(dialog_confirm, screenshot)
                self.game.wait(0.50)
                continue

            if self._character_list_is_open(screenshot):
                if not reported_character_list:
                    self._log(
                        "Account selected, but the character list is still open; "
                        "waiting for it to close before farming"
                    )
                    reported_character_list = True
                self.game.wait(max(0.20, self.game.timing.poll_interval))
                continue

            detection = self.detector.detect(
                screenshot,
                read_account=False,
                candidate_states={ScreenState.CITY, ScreenState.WORLD_MAP},
            )
            if detection.state in {ScreenState.CITY, ScreenState.WORLD_MAP}:
                self._log(f"RoK {detection.state.value} loaded for {expected_name}")
                return
            if time.monotonic() >= next_progress_log:
                self._log(f"Still waiting for {expected_name} to load")
                next_progress_log = time.monotonic() + 15.0
            self.game.wait(max(0.20, self.game.timing.poll_interval))
        raise RuntimeError(
            f"Timed out waiting for {expected_name!r} to load; "
            "stopping rather than farming an unverified account"
        )

    @staticmethod
    def _starred_accounts_header_bounds(
        frame: Any,
    ) -> tuple[int, int, int, int] | None:
        """Locate the starred-accounts bar if visible in the upper viewport."""

        gray = (
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if frame.ndim == 3
            else frame
        )
        template = cv2.imread(
            str(_STARRED_ACCOUNTS_HEADER),
            cv2.IMREAD_GRAYSCALE,
        )
        if gray is None or template is None:
            raise RuntimeError("Could not read the starred-accounts header images")
        height, width = gray.shape[:2]
        left, right = round(width * 0.08), round(width * 0.75)
        top, bottom = round(height * 0.12), round(height * 0.36)
        score, match = ScreenDetector._best_scale_match(
            gray[top:bottom, left:right],
            template,
            minimum_scale=0.35,
            maximum_scale=1.15,
        )
        if score < 0.76:
            return None
        scaled_height, scaled_width = ScreenDetector._scaled_size(
            template,
            match[2],
        )
        return (
            left + match[0],
            top + match[1],
            scaled_width,
            scaled_height,
        )

    @staticmethod
    def _regular_accounts_header_bounds(
        frame: Any,
    ) -> tuple[int, int, int, int] | None:
        """Locate the regular-accounts bar if visible in the viewport."""

        gray = (
            cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if frame.ndim == 3
            else frame
        )
        template = cv2.imread(
            str(_REGULAR_ACCOUNTS_HEADER),
            cv2.IMREAD_GRAYSCALE,
        )
        if gray is None or template is None:
            raise RuntimeError("Could not read the regular-accounts header images")
        height, width = gray.shape[:2]
        left, right = round(width * 0.08), round(width * 0.75)
        top, bottom = round(height * 0.25), round(height * 0.98)
        score, match = ScreenDetector._best_scale_match(
            gray[top:bottom, left:right],
            template,
            minimum_scale=0.35,
            maximum_scale=1.15,
        )
        if score < 0.76:
            return None
        scaled_height, scaled_width = ScreenDetector._scaled_size(
            template,
            match[2],
        )
        return (
            left + match[0],
            top + match[1],
            scaled_width,
            scaled_height,
        )

    @classmethod
    def _character_list_is_open(cls, screenshot: Path) -> bool:
        """Check the distinctive starred-accounts bar in its screen region."""

        frame = load_gray_frame(screenshot)
        if frame is None:
            raise RuntimeError("Could not read the starred-accounts header images")
        return cls._starred_accounts_header_bounds(frame) is not None

    @classmethod
    def _regular_accounts_section_is_visible(cls, screenshot: Path) -> bool:
        """Use the next section header to confirm the starred list has ended."""

        frame = load_gray_frame(screenshot)
        if frame is None:
            raise RuntimeError("Could not read the regular-accounts header images")
        return cls._regular_accounts_header_bounds(frame) is not None

    def _capture(self) -> tuple[Path, Any]:
        screenshot = self.game.screenshot()
        frame = load_color_frame(screenshot)
        if frame is None:
            raise RuntimeError(f"Could not read screenshot: {screenshot}")
        return screenshot, frame

    def _log(self, message: str) -> None:
        self.log(message)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(
        char for char in decomposed
        if not unicodedata.category(char).startswith("M")
    ).replace("đ", "d")
