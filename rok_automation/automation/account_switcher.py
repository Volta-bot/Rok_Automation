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
from rok_automation.vision.screen import ScreenDetector, ScreenState


_OPEN_MENU_TIMEOUT = 15.0
_ACCOUNT_LOAD_TIMEOUT = 180.0
_MAX_SCROLLS = 32
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CITY_MAP_BUTTON = _PROJECT_ROOT / "templates/navigation/city_map_button.png"
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
        """Select the next starred card in the chosen positional order."""

        self._open_character_list()
        initializing = traversal.direction is None
        search_offset = 0
        search_toward_end = True
        reversed_search = False
        target_index: int | None = (
            traversal.next_target_index() if not initializing else None
        )

        for _ in range(_MAX_SCROLLS * 2 + 1):
            screenshot, frame = self._capture()
            cards = self._read_account_cards(frame)
            starred_account_total = self._read_starred_account_total(frame)
            if starred_account_total is not None:
                traversal.total_accounts = starred_account_total

            # OCR can return an empty frame while the list is animating or
            # dimmed. Retry briefly before treating the view as unreadable.
            for _retry in range(2):
                if cards:
                    break
                self.game.wait(0.20)
                screenshot, frame = self._capture()
                cards = self._read_account_cards(frame)
                starred_account_total = self._read_starred_account_total(frame)
                if starred_account_total is not None:
                    traversal.total_accounts = starred_account_total
            if not cards:
                raise RuntimeError(
                    "Could not read any visible starred accounts after retries"
                )

            height = frame.shape[0]
            top_down_cards = self._order_account_cards(
                cards,
                frame_height=height,
                direction=1,
            )
            starred_header = self._starred_accounts_header_bounds(frame)
            regular_header = self._regular_accounts_header_bounds(frame)
            at_starred_top = (
                starred_header is not None
                and (top_down_cards[0][1][1] - starred_header[1]) < height * 0.22
            )
            at_starred_bottom = (
                regular_header is not None
                and (regular_header[1] - top_down_cards[-1][1][1]) < height * 0.34
            )
            total = traversal.total_accounts
            if at_starred_top or total is not None:
                traversal.anchored_to_top = True

            active_bounds = self._selected_account_bounds(
                screenshot,
                frame,
                cards,
            )
            active_position = (
                self._card_index(top_down_cards, active_bounds)
                if active_bounds is not None
                else None
            )

            anchored_index: int | None = None
            if active_position is not None:
                if at_starred_top:
                    anchored_index = active_position
                elif at_starred_bottom and total is not None:
                    anchored_index = max(
                        active_position,
                        total - len(top_down_cards) + active_position,
                    )

            if traversal.direction is None:
                if active_position is None:
                    at_boundary = (
                        (search_toward_end and at_starred_bottom)
                        or (not search_toward_end and at_starred_top)
                    )
                    if not at_boundary:
                        delta_slots = self._scroll_account_list(
                            screenshot,
                            frame,
                            toward_list_end=search_toward_end,
                        )
                        if delta_slots:
                            step = (
                                (2 if search_toward_end else -2)
                                if isinstance(delta_slots, bool)
                                else int(delta_slots)
                            )
                            search_offset = max(0, search_offset + step)
                            if total is not None:
                                search_offset = min(
                                    max(0, total - len(top_down_cards)),
                                    search_offset,
                                )
                            continue
                    if not reversed_search:
                        search_toward_end = not search_toward_end
                        reversed_search = True
                        continue
                    raise RuntimeError(
                        "Could not locate the green check on a starred account; "
                        "stopping without choosing by name"
                    )

                start_index = (
                    anchored_index
                    if anchored_index is not None
                    else search_offset + active_position
                )
                if total is not None:
                    start_index = min(total - 1, start_index)
                if start_index == 0 or (at_starred_top and active_position == 0):
                    traversal.direction = 1
                    traversal.current_index = 0
                    target_index = traversal.next_target_index()
                    self._log(
                        f"Starting positional account traversal at 1"
                        f"/{total if total is not None else '?'}; "
                        "direction: top-to-bottom, left-to-right"
                    )
                elif (
                    (total is not None and start_index >= total - 1)
                    or (
                        total is None
                        and at_starred_bottom
                        and active_position == len(top_down_cards) - 1
                    )
                ):
                    if total is not None:
                        start_index = total - 1
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
                    traversal.skipped_index = start_index
                    if total is None or start_index <= (total - 1) / 2:
                        traversal.direction = 1
                        target_index = 0
                        self._log(
                            f"Started mid-list at {start_index + 1}"
                            f"/{total if total is not None else '?'}; "
                            f"moving to nearest end (1/{total if total is not None else '?'}) "
                            "and farming top-to-bottom, left-to-right"
                        )
                    else:
                        traversal.direction = -1
                        target_index = total - 1
                        self._log(
                            f"Started mid-list at {start_index + 1}/{total}; "
                            f"moving to nearest end ({total}/{total}) "
                            "and farming bottom-to-top, right-to-left"
                        )
            elif (
                not initializing
                and anchored_index is not None
                and traversal.current_index is not None
                and traversal.current_index != anchored_index
                and anchored_index != traversal.skipped_index
            ):
                self._log(
                    f"Re-synced current account position to "
                    f"{anchored_index + 1}"
                    f"/{total if total is not None else '?'} "
                    "from the anchored list view"
                )
                traversal.current_index = anchored_index
                target_index = traversal.next_target_index()
            elif (
                not initializing
                and not traversal.anchored_to_top
                and active_position is not None
                and traversal.direction == -1
            ):
                traversal.current_index = active_position
                target_index = active_position - 1

            if target_index is None:
                self._log(
                    "Reached the end of the starred accounts list; "
                    "no more accounts to farm"
                )
                return None

            active_global = (
                traversal.current_index
                if traversal.current_index is not None
                else traversal.skipped_index
            )
            card_offset: int | None = None
            if at_starred_top:
                card_offset = target_index
            elif at_starred_bottom and total is not None:
                visible_start = max(0, total - len(top_down_cards))
                card_offset = target_index - visible_start
            elif active_position is not None and active_global is not None:
                card_offset = active_position + (target_index - active_global)

            if (
                card_offset is not None
                and active_position is not None
                and card_offset == active_position
            ):
                actual_index = (
                    anchored_index
                    if anchored_index is not None
                    else target_index
                )
                traversal.skipped_index = None
                traversal.current_index = actual_index
                target_index = traversal.next_target_index()
                if target_index is None:
                    return None
                card_offset = active_position + (target_index - actual_index)

            if card_offset is not None and 0 <= card_offset < len(top_down_cards):
                name, bounds = top_down_cards[card_offset]
                self._log(
                    f"Switching to starred account "
                    f"{target_index + 1}"
                    f"/{total if total is not None else '?'}: {name}"
                )
                self.game.click(bounds, screenshot)
                self._confirm_account_login()
                self._wait_for_game(name)
                traversal.current_index = target_index
                return name

            if card_offset is not None:
                need_scroll_toward_end = card_offset >= len(top_down_cards)
            elif at_starred_top:
                need_scroll_toward_end = True
            elif at_starred_bottom:
                need_scroll_toward_end = False
            else:
                need_scroll_toward_end = (
                    search_toward_end
                    if active_position is None and not reversed_search
                    else traversal.direction == 1
                )

            if need_scroll_toward_end and at_starred_bottom:
                self._log(
                    f"Reached the end of {total or (traversal.current_index or 0) + 1} "
                    "starred accounts"
                )
                return None
            if not need_scroll_toward_end and at_starred_top and target_index < 0:
                self._log("Reached the start of the starred accounts list")
                return None

            if self._scroll_account_list(
                screenshot,
                frame,
                toward_list_end=need_scroll_toward_end,
            ):
                search_toward_end = need_scroll_toward_end
                continue

            if not need_scroll_toward_end and not reversed_search:
                reversed_search = True
                search_toward_end = True
                continue
            if need_scroll_toward_end and not reversed_search and active_position is None:
                reversed_search = True
                search_toward_end = False
                continue

            if total is not None:
                raise RuntimeError(
                    f"Could not reveal starred account {target_index + 1} of "
                    f"{total} by scrolling; stopping rather than skipping it"
                )
            self._log(
                "No next starred card appeared after scrolling; "
                f"finished at account {(traversal.current_index or 0) + 1}"
            )
            return None

        raise RuntimeError(
            "Stopped safely after too many character-list scrolls; "
            "could not locate the target account"
        )

    def _open_character_list(self) -> None:
        screenshot, frame = self._capture()
        height, width = frame.shape[:2]
        detection = self.detector.detect(
            screenshot,
            read_account=False,
            candidate_states={ScreenState.CITY, ScreenState.WORLD_MAP},
        )
        if detection.state not in {ScreenState.CITY, ScreenState.WORLD_MAP}:
            raise RuntimeError(
                "Account switching must start from the city or world map; "
                f"found {detection.state.value}"
            )

        # The governor portrait stays anchored at the upper-left in city and
        # world-map views. Use a generous hit area around the portrait.
        self.game.click(
            (round(width * 0.005), round(height * 0.002),
             round(width * 0.075), round(height * 0.12)),
            screenshot,
        )
        self._wait_for_text("ho so thong doc")
        self._click_profile_settings()
        self._click_menu_template(_CHARACTER_MENU_BUTTON, "characters")
        self._wait_for_template(_CHARACTER_LIST_TITLE, "character list")
        self.game.wait(0.25)

    def _click_profile_settings(self) -> None:
        """Use the supplied settings artwork when its small caption OCR fails."""

        deadline = time.monotonic() + 5.0
        last_screenshot: Path | None = None
        last_frame: Any | None = None
        while time.monotonic() < deadline:
            screenshot, frame = self._capture()
            last_screenshot, last_frame = screenshot, frame
            hit = self.detector.locate_template(
                screenshot,
                _SETTINGS_BUTTON,
                confidence_threshold=0.58,
            )
            if hit is not None:
                self._log(
                    f"Clicking settings button (match={hit.confidence:.3f})"
                )
                self.game.click(hit.bounds, screenshot)
                return
            self.game.wait(self.game.timing.poll_interval)

        if last_screenshot is not None and last_frame is not None:
            height, width = last_frame.shape[:2]
            # The profile screen was confirmed immediately beforehand; this
            # fallback targets only the lower-right settings tile.
            self._log("Clicking settings tile at its profile-menu position")
            self.game.click(
                (
                    round(width * 0.79),
                    round(height * 0.75),
                    round(width * 0.15),
                    round(height * 0.20),
                ),
                last_screenshot,
            )

    def _click_text(self, keyword: str) -> None:
        deadline = time.monotonic() + _OPEN_MENU_TIMEOUT
        while time.monotonic() < deadline:
            screenshot, frame = self._capture()
            detections = read_text_detections(
                frame,
                (0, 0, frame.shape[1], frame.shape[0]),
                upscale=2.0,
            )
            for text, bounds, _score in detections:
                if keyword in _fold(text):
                    self.game.click(bounds, screenshot)
                    return
            self.game.wait(self.game.timing.poll_interval)
        raise RuntimeError(f"Could not locate the {keyword!r} menu label")

    def _click_menu_template(
        self,
        template: Path,
        label: str,
        *,
        timeout: float = _OPEN_MENU_TIMEOUT,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            screenshot, _frame = self._capture()
            hit = self.detector.locate_template(
                screenshot,
                template,
                confidence_threshold=0.58,
            )
            if hit is not None:
                self._log(
                    f"Clicking {label} button (match={hit.confidence:.3f})"
                )
                self.game.click(hit.bounds, screenshot)
                return
            self.game.wait(self.game.timing.poll_interval)
        raise RuntimeError(f"Could not locate the {label!r} menu button")

    def _wait_for_template(self, template: Path, label: str) -> None:
        deadline = time.monotonic() + _OPEN_MENU_TIMEOUT
        while time.monotonic() < deadline:
            screenshot = self.game.screenshot()
            if self.detector.locate_template(
                screenshot,
                template,
                confidence_threshold=0.58,
            ) is not None:
                return
            self.game.wait(self.game.timing.poll_interval)
        raise RuntimeError(f"Timed out waiting for the {label} screen")

    def _confirm_account_login(self) -> None:
        """Click Có once after visually anchoring the confirmation dialog."""

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

        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            screenshot, frame = self._capture()
            frame_height, frame_width = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # Search only the centered modal region at realistic scales so the
            # top NHÂN VẬT window title bar cannot be mistaken for this dialog.
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
                self._log(
                    f"Login confirmation dialog detected "
                    f"(title={title_score:.3f}, yes={yes_score:.3f}); "
                    "clicking Có and waiting for game load"
                )
                self.game.click(button_bounds, screenshot)
                return
            self.game.wait(self.game.timing.poll_interval)

        raise RuntimeError(
            "Timed out waiting for the account-login confirmation dialog; "
            "no unverified button was clicked"
        )

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
        opened_world_map = False
        reported_character_list = False
        while time.monotonic() < deadline:
            screenshot = self.game.screenshot()
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
            if detection.state is ScreenState.CITY and not opened_world_map:
                hit = self.detector.locate_template(
                    screenshot,
                    _CITY_MAP_BUTTON,
                    confidence_threshold=0.65,
                )
                if hit is not None:
                    self.game.click(hit.bounds, screenshot)
                    opened_world_map = True
                else:
                    self.game.wait(max(0.20, self.game.timing.poll_interval))
                continue
            if detection.state is ScreenState.WORLD_MAP:
                self._log(f"RoK world map loaded for {expected_name}")
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

        frame = cv2.imread(str(screenshot), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise RuntimeError("Could not read the starred-accounts header images")
        return cls._starred_accounts_header_bounds(frame) is not None

    @classmethod
    def _regular_accounts_section_is_visible(cls, screenshot: Path) -> bool:
        """Use the next section header to confirm the starred list has ended."""

        frame = cv2.imread(str(screenshot), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise RuntimeError("Could not read the regular-accounts header images")
        return cls._regular_accounts_header_bounds(frame) is not None

    def _capture(self) -> tuple[Path, Any]:
        screenshot = self.game.screenshot()
        frame = cv2.imread(str(screenshot), cv2.IMREAD_COLOR)
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
