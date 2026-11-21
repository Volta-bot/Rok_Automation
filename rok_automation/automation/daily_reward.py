"""Claim daily VIP points, VIP free chest, and Campaign golden chest."""

from __future__ import annotations

import re
import time
import unicodedata
from functools import partial
from pathlib import Path
from typing import Any, Callable

from rok_automation.game.console import safe_print
from rok_automation.game.interface import GameInterface
from rok_automation.game.popups import back_out_of_unknown_menu, close_vip_reward, locate_view_toggle
from rok_automation.game.screen_retry import MIN_SCREEN_SCANS, screen_scan_attempts
from rok_automation.vision.account_label import read_text_detections
from rok_automation.vision.screen import (
    ScreenDetection,
    ScreenDetector,
    ScreenState,
    VIP_REWARD_POPUPS,
    load_color_frame,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DAILY_REWARD_TEMPLATES = _PROJECT_ROOT / "templates/daily_reward"

# Static normalized coordinates (x, y, w, h) for standard 16:9 resolutions.
# Used as fast lookups and reliable fallbacks.
# The daily-VIP-points reward is claimed with the upper white "Nhận" button,
# below the chest artwork. Targeting the artwork can land beside the close X.
NORM_WOODEN_NHAN = (0.750, 0.257, 0.095, 0.050)
NORM_GREEN_NHAN = (0.688, 0.438, 0.092, 0.056)
NORM_VIP_CLOSE_X = (0.840, 0.090, 0.030, 0.050)
NORM_POPUP_DISMISS = (0.495, 0.915, 0.010, 0.010)

NORM_CHIEN_DICH = (0.54, 0.895, 0.060, 0.091)
NORM_CHINH_PHAT = (0.050, 0.150, 0.185, 0.350)
NORM_GOLDEN_CHEST = (0.050, 0.250, 0.085, 0.140)
NORM_BACK_BUTTON = (0.010, 0.036, 0.045, 0.055)

# Session-wide cache: (frame_width, frame_height, key) -> (x, y, w, h)
# Ensures static button locations are scanned once and reused across all accounts.
_PINNED_COORDINATES: dict[tuple[int, int, str], tuple[int, int, int, int]] = {}

# Countdown timer pattern (e.g. "09:23:34" or "10:38") indicating collected rewards.
_TIMER_REGEX = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_MENU_SETTLE_DELAY = 0.45
_VIP_CHEST_CLAIM_DELAY = 2.0
_CAMPAIGN_CHEST_CLAIM_DELAY = 1.0
_CAMPAIGN_SCREENS = frozenset({ScreenState.CHIEN_DICH, ScreenState.CHINH_PHAT})
_Bounds = tuple[int, int, int, int]
_Capture = tuple[Path, Any, tuple[int, int]]
_ScreenCheck = Callable[[Path, Any, tuple[int, int]], bool]
_RewardButton = Callable[[Path, Any, tuple[int, int]], _Bounds]
_MenuButton = Callable[[Path, Any, tuple[int, int]], _Bounds | None]


def strip_accents_and_normalize(text: str) -> str:
    """Normalize Vietnamese accents and punctuation for robust OCR matching."""
    decomposed = unicodedata.normalize("NFD", text)
    folded = "".join(
        char for char in decomposed
        if not unicodedata.category(char).startswith("M")
    ).replace("đ", "d").replace("Đ", "D")
    return "".join(char for char in folded if char.isalnum()).casefold()


class DailyRewardClaimer:
    """Automates claiming daily VIP points/chest and Campaign golden chest."""

    def __init__(
        self,
        game: GameInterface,
        detector: ScreenDetector | None = None,
        *,
        log: Callable[[str], None] = safe_print,
    ) -> None:
        self.game = game
        self.detector = detector or ScreenDetector()
        self.log = log
        self._confirmed_rewards: set[str] | None = None

    def _log(self, message: str) -> None:
        self.log(f"[Daily Reward] {message}")

    def _click(
        self, bounds: _Bounds, screenshot: Path, *, settle_delay: float = _MENU_SETTLE_DELAY,
    ) -> None:
        self.game.click(bounds, screenshot)
        self.game.wait(settle_delay)

    def _capture(self) -> _Capture:
        """Capture screenshot and return (path, color_frame, (width, height))."""
        path = self.game.screenshot()
        frame = load_color_frame(path)
        if frame is None:
            raise FileNotFoundError(f"Failed to capture screenshot: {path}")
        h, w = frame.shape[:2]
        return path, frame, (w, h)

    def _get_cached_bounds(
        self,
        size: tuple[int, int],
        key: str,
        norm_fallback: tuple[float, float, float, float],
    ) -> tuple[int, int, int, int]:
        """Return cached bounds if available, else calculate from normalized fallback."""
        w, h = size
        cached = _PINNED_COORDINATES.get((w, h, key))
        if cached is not None:
            return cached
        bounds = (
            round(norm_fallback[0] * w),
            round(norm_fallback[1] * h),
            round(norm_fallback[2] * w),
            round(norm_fallback[3] * h),
        )
        return bounds

    def _pin_bounds(
        self,
        size: tuple[int, int],
        key: str,
        bounds: tuple[int, int, int, int],
    ) -> None:
        """Cache static button bounds for subsequent accounts."""
        w, h = size
        _PINNED_COORDINATES[(w, h, key)] = bounds

    # -------------------------------------------------------------------------
    # Flow Controller
    # -------------------------------------------------------------------------

    def claim_all(self) -> bool:
        """Claim VIP and Campaign rewards with verification inside each menu."""
        self._log("Starting daily reward routine (VIP & Campaign)...")
        self._confirmed_rewards = set()
        try:
            initial_capture = self._capture()
            state = self.detector.detect(
                initial_capture[0], candidate_states=_CAMPAIGN_SCREENS, read_account=False,
            ).state
            steps = [("VIP", self.claim_vip_rewards), ("Campaign", self.claim_campaign_rewards)]
            if state in _CAMPAIGN_SCREENS:
                steps.reverse()

            unconfirmed = []
            for label, claim in steps:
                for attempt in range(2):
                    capture = initial_capture
                    initial_capture = None
                    try:
                        if claim(initial_capture=capture):
                            break
                    except (OSError, RuntimeError, ValueError) as exc:
                        self._log(f"{label} rewards interrupted: {exc}")
                    if attempt == 0:
                        self._log(f"Rescanning the current screen to resume {label} rewards...")
                else:
                    unconfirmed.append(label)

            if not unconfirmed:
                self._log("Daily rewards collected successfully.")
                return True
            self._log(f"Could not verify all daily rewards in {' and '.join(unconfirmed)}.")
        except Exception as exc:
            self._log(f"Encountered unexpected error during daily rewards: {exc}")
        finally:
            self._confirmed_rewards = None
        self._recover_to_city()
        return False

    def _claim_visible_reward(
        self,
        label: str,
        key: str,
        *,
        is_menu: _ScreenCheck,
        is_collected: Callable[[Any, Path], bool],
        locate_button: _RewardButton,
        dismiss_popup: _ScreenCheck,
    ) -> bool:
        """Check the reward screen and retry a missed claim in the same menu."""
        if self._confirmed_rewards is not None and key in self._confirmed_rewards:
            return True
        clicked = False
        popup_seen = False

        def observe(screenshot: Path, frame: Any, size: tuple[int, int]) -> tuple[bool, bool]:
            nonlocal popup_seen
            if dismiss_popup(screenshot, frame, size):
                popup_seen = True
                return False, False
            menu_open = is_menu(screenshot, frame, size)
            return menu_open, menu_open and (popup_seen or is_collected(frame, screenshot))

        def is_verified(screenshot: Path, frame: Any, size: tuple[int, int]) -> bool:
            return observe(screenshot, frame, size)[1]

        def verified() -> bool:
            if self._confirmed_rewards is not None:
                self._confirmed_rewards.add(key)
            self._log(f"{label} collected." if clicked or popup_seen else f"{label} already collected.")
            return True

        for _attempt in range(2):
            screenshot, frame, size = self._capture()
            menu_open, collected = observe(screenshot, frame, size)
            if collected:
                return verified()
            if menu_open:
                if clicked:
                    _PINNED_COORDINATES.pop((*size, key), None)
                    self._log(f"Retrying {label} in the open menu...")
                else:
                    self._log(f"Clicking {label}...")
                self._click(
                    locate_button(screenshot, frame, size), screenshot,
                    settle_delay=_CAMPAIGN_CHEST_CLAIM_DELAY,
                )
                clicked = True
            if self._wait_for_condition(is_verified) or (
                popup_seen and self._wait_for_condition(is_verified)
            ):
                return verified()
            if popup_seen:
                break

        if clicked and not popup_seen:
            _PINNED_COORDINATES.pop((*size, key), None)
        self._log(f"Could not confirm collection of {label}.")
        return False

    def _open_reward_menu(
        self,
        label: str,
        key: str,
        locate_button: _MenuButton,
        is_open: _ScreenCheck,
        at_source: _ScreenCheck | None = None,
        recover_source: _ScreenCheck | None = None,
        attempts: int = 2,
        initial_capture: _Capture | None = None,
    ) -> bool:
        for attempt in range(attempts):
            screenshot, frame, size = initial_capture if initial_capture is not None else self._capture()
            initial_capture = None
            if is_open(screenshot, frame, size):
                return True
            source_ready = at_source is None or at_source(screenshot, frame, size)
            if recover_source is not None and (attempt or not source_ready):
                if recover_source(screenshot, frame, size):
                    screenshot, frame, size = self._capture()
                    if is_open(screenshot, frame, size):
                        return True
                    source_ready = at_source is None or at_source(screenshot, frame, size)
            if not source_ready:
                self.game.wait(0.25)
                continue
            if attempt:
                _PINNED_COORDINATES.pop((*size, key), None)
            bounds = locate_button(screenshot, frame, size)
            if bounds is None:
                self.game.wait(0.25)
                continue
            self._log(f"Clicking {label}...")
            self._click(bounds, screenshot)
            if self._wait_for_condition(is_open, timeout=3.5):
                return True
        return False

    def ensure_city_view(self, max_attempts: int = 5, *, verified_back_only: bool = False) -> bool:
        """Ensure game is in City view before navigating menus."""
        max_attempts = max(max_attempts, MIN_SCREEN_SCANS - 1)
        presses = 0
        for attempt in range(max_attempts + 1):
            screenshot, frame, (w, h) = self._capture()
            detection = self.detector.detect(screenshot, read_account=False, resource_details=frozenset())
            if detection.state is ScreenState.CITY:
                self._log(f"City view confirmed after {presses} navigation press(es).")
                return True
            if attempt == max_attempts:
                break
            if close_vip_reward(
                self.game, screenshot, detection, log=self._log, settle_delay=_MENU_SETTLE_DELAY,
            ):
                presses += 1
                continue
            if detection.state is ScreenState.WORLD_MAP:
                toggle = locate_view_toggle(self.detector, screenshot)
                if toggle is not None:
                    template, hit = toggle
                    self._log(f"Returning to City with {template.stem} (match={hit.confidence:.3f})")
                    self._click(hit.bounds, screenshot)
                    presses += 1
                else:
                    self._log("World Map detected; waiting for a fortress or map button...")
                    self.game.wait(1.0)
                continue

            from rok_automation.game.popups import close_chat, close_scout_manager, close_mailbox
            if detection.state is ScreenState.CHAT:
                close_chat(self.game, screenshot, detection, log=self._log)
                self.game.wait(_MENU_SETTLE_DELAY)
                presses += 1
                continue
            if detection.state is ScreenState.MAILBOX:
                if close_mailbox(self.game, self.detector, screenshot, log=self._log):
                    presses += 1
                continue
            campaign_menu = detection.state in _CAMPAIGN_SCREENS
            if not campaign_menu and close_scout_manager(self.game, self.detector, screenshot, log=self._log):
                presses += 1
                continue
            confirm = self._detect_xac_nhan(screenshot, frame, (w, h))
            navigation_presses = 1
            if confirm is not None:
                self._log("Confirmation dialog is still open; confirming...")
                self._click(confirm, screenshot)
            elif campaign_menu:
                x, y, width, height = self._locate_back_button(screenshot, (w, h))
                back = (x + width // 2, y + height // 2)
                if detection.state is ScreenState.CHINH_PHAT:
                    self._log("Chinh phạt detected. Pressing Back twice at the same position...")
                    self.game.tap(*back)
                    self.game.wait(_MENU_SETTLE_DELAY)
                    navigation_presses = 2
                else:
                    self._log("Chiến dịch detected. Pressing Back to reach City...")
                self.game.tap(*back)
                self.game.wait(_MENU_SETTLE_DELAY)
            elif self._is_vip_points_popup(frame) or self._is_vip_chest_popup(frame):
                self._log("Reward popup is still open; dismissing it...")
                self._dismiss_vip_reward_popup(screenshot, (w, h))
            elif detection.state is ScreenState.VIP:
                self._log("VIP menu is still open; pressing X...")
                self._click(self._locate_vip_close_x(screenshot, (w, h)), screenshot)
            else:
                closed = verified_back_only and self._close_player_profile(screenshot)
                if not closed and (verified_back_only or detection.state is ScreenState.UNKNOWN):
                    closed = back_out_of_unknown_menu(self.game, self.detector, screenshot, log=self._log)
                if not closed:
                    if verified_back_only:
                        self.game.wait(0.4)
                        continue
                    self._log(f"Screen is {detection.state.value}; pressing Back to reach City...")
                    self._click(self._locate_back_button(screenshot, (w, h)), screenshot)
            presses += navigation_presses

        self._log(f"Could not confirm City view after {presses} navigation press(es).")
        return False

    def _recover_to_city(self) -> None:
        """Emergency recovery: close any open dialogs/menus back to City."""
        self._log("Attempting recovery back to City view...")
        try:
            self.ensure_city_view(verified_back_only=True)
        except Exception as exc:
            self._log(f"Recovery failed: {exc}")

    # -------------------------------------------------------------------------
    # STEP 1: VIP Rewards
    # -------------------------------------------------------------------------

    def claim_vip_rewards(self, *, initial_capture: _Capture | None = None) -> bool:
        """Claim daily VIP points (wooden chest) and daily VIP gift (metal chest)."""
        self._log("Step 1/2: Checking VIP daily rewards...")

        entry: tuple[_Capture, ScreenDetection] | None = None
        entry_state = ScreenState.UNKNOWN

        def vip_reward_entry(screenshot: Path, frame: Any, size: tuple[int, int]) -> bool:
            nonlocal entry, entry_state
            detection = self.detector.detect(
                screenshot, candidate_states=VIP_REWARD_POPUPS | {ScreenState.CITY, ScreenState.VIP},
                read_account=False,
            )
            entry_state = detection.state
            if entry_state is not ScreenState.VIP and entry_state not in VIP_REWARD_POPUPS:
                return False
            entry = (screenshot, frame, size), detection
            return True

        menu_open = self._open_reward_menu(
            "VIP icon", "vip_button", self._locate_vip_button,
            vip_reward_entry,
            lambda *_capture: entry_state is ScreenState.CITY,
            recover_source=self._recover_vip_entry,
            attempts=3,
            initial_capture=initial_capture,
        )
        if not menu_open or entry is None:
            self._log("VIP menu did not open after retries; skipping VIP rewards.")
            if not self.ensure_city_view(verified_back_only=True):
                raise RuntimeError("Could not return to City after retrying VIP rewards")
            return False

        return self._claim_vip_chests(*entry)

    def _claim_vip_chests(self, capture: _Capture, detection: ScreenDetection) -> bool:
        """Tap both chests from the same VIP frame, then verify or recover."""
        wooden = ScreenState.VIP_WOODEN_CHEST_REWARD
        metal = ScreenState.VIP_METAL_CHEST_REWARD
        rewards = {
            wooden: ("VIP daily points (wooden chest)", "wooden_nhan",
                     self._is_wooden_chest_collected, self._locate_wooden_nhan),
            metal: ("VIP daily free gift (metal chest)", "green_nhan",
                    self._is_metal_chest_collected, self._locate_green_nhan),
        }
        confirmed = self._confirmed_rewards if self._confirmed_rewards is not None else set()
        seen: set[ScreenState] = set()
        retried: set[ScreenState] = set()
        metal_target: tuple[_Bounds, tuple[int, int]] | None = None
        metal_resumed = False

        for scan in range(MIN_SCREEN_SCANS + 1):
            screenshot, frame, size = capture
            state = detection.state
            if state is ScreenState.VIP:
                for reward, (label, key, is_collected, _) in rewards.items():
                    if key not in confirmed and (reward in seen or is_collected(frame, screenshot)):
                        confirmed.add(key)
                        message = "collection confirmed" if scan else "already collected"
                        self._log(f"{label} {message}.")
                remaining = [reward for reward, (_, key, _, _) in rewards.items() if key not in confirmed]
                if not remaining:
                    self._close_vip_menu(screenshot, size)
                    return True
                if scan == MIN_SCREEN_SCANS:
                    break

                # Locate both buttons before the first tap changes the screen.
                targets = {}
                for reward in remaining:
                    if reward in retried:
                        continue
                    _, key, _, locate = rewards[reward]
                    if scan:
                        _PINNED_COORDINATES.pop((*size, key), None)
                        retried.add(reward)
                    targets[reward] = locate(screenshot, frame, size)
                if metal in targets:
                    metal_target = targets[metal], size
                for reward, bounds in targets.items():
                    self._tap_vip_chest_pair(rewards[reward][0], bounds, screenshot)
                if not targets:
                    self.game.wait(0.25)
            elif scan < MIN_SCREEN_SCANS:
                if state is ScreenState.UNKNOWN:
                    if self._is_vip_points_popup(frame):
                        state = wooden
                    elif self._is_vip_chest_popup(frame):
                        state = metal
                if state in VIP_REWARD_POPUPS:
                    seen.add(state)
                    if not close_vip_reward(
                        self.game, screenshot, detection, log=self._log, settle_delay=_MENU_SETTLE_DELAY,
                    ):
                        self._log(f"Dismissing the {rewards[state][0]} overlay...")
                        self._dismiss_vip_reward_popup(screenshot, size)
                    if (state is wooden and metal_target is not None and metal_target[1] == size
                            and metal not in seen and rewards[metal][1] not in confirmed and not metal_resumed):
                        self._tap_vip_chest_pair(rewards[metal][0], metal_target[0], screenshot)
                        metal_resumed = True
                else:
                    self.game.wait(0.25)
            if scan < MIN_SCREEN_SCANS:
                capture = self._capture()
                detection = self.detector.detect(
                    capture[0], candidate_states=VIP_REWARD_POPUPS | {ScreenState.VIP}, read_account=False,
                )

        missing = [label for label, key, _, _ in rewards.values() if key not in confirmed]
        self._log(f"Could not confirm collection of {' and '.join(missing)}.")
        return False

    def _tap_vip_chest_pair(self, label: str, bounds: _Bounds, screenshot: Path) -> None:
        self._log(f"Tapping {label} twice...")
        self._click(bounds, screenshot, settle_delay=_VIP_CHEST_CLAIM_DELAY)
        self._click(bounds, screenshot)

    def _locate_vip_button(
        self,
        screenshot: Path,
        frame: Any,
        size: tuple[int, int],
    ) -> _Bounds | None:
        """Find the VIP shield or label in the badge area beside the portrait."""
        w, h = size
        hit = self.detector.locate_template(
            screenshot, _DAILY_REWARD_TEMPLATES / "vip_shield.png",
            confidence_threshold=0.76, refine_scale=True,
        )
        if hit is not None:
            return hit.bounds

        # Start to the right of the player portrait and above its UTC label.
        roi = (round(w * .077), round(h * .058), round(w * .074), round(h * .056))
        try:
            for text, box, score in read_text_detections(frame, roi):
                norm = strip_accents_and_normalize(text)
                x, y, width, height = box
                if (
                    score >= .70 and re.fullmatch(r"vip\d{0,2}", norm)
                    and .085 * w <= x + width / 2 <= .15 * w
                    and .058 * h <= y + height / 2 <= .114 * h
                ):
                    return box
        except Exception:
            pass

        return None

    def _recover_vip_entry(self, screenshot: Path, frame: Any, size: tuple[int, int]) -> bool:
        if self._close_player_profile(screenshot):
            return True
        if self._is_screen(screenshot, frame, size, state=ScreenState.CITY):
            return False
        self._log("Recovering from an unexpected screen before retrying VIP...")
        return self.ensure_city_view(verified_back_only=True)

    def _close_player_profile(self, screenshot: Path) -> bool:
        settings = self.detector.locate_template(
            screenshot, _PROJECT_ROOT / "templates/profile/settings_button.png",
            confidence_threshold=.80,
        )
        if settings is None:
            return False
        close = self.detector.locate_template(
            screenshot, _DAILY_REWARD_TEMPLATES / "vip_close_x.png",
            confidence_threshold=.75,
        )
        if close is None:
            return back_out_of_unknown_menu(self.game, self.detector, screenshot, log=self._log)
        self._log("Player profile opened while entering VIP. Closing it and retrying...")
        self._click(close.bounds, screenshot)
        return True

    def _is_screen(
        self, screenshot: Path, _frame: Any, _size: tuple[int, int], *, state: ScreenState,
    ) -> bool:
        return self.detector.detect(
            screenshot, candidate_states={state}, read_account=False,
        ).state is state

    def _is_wooden_chest_collected(self, frame: Any, screenshot: Path) -> bool:
        """Check if VIP wooden chest (daily points) has already been collected."""
        tpl = _DAILY_REWARD_TEMPLATES / "wooden_chest_collected.png"
        if self.detector.locate_template(screenshot, tpl, confidence_threshold=0.75) is not None:
            return True

        h, w = frame.shape[:2]
        roi = (int(0.72 * w), int(0.23 * h), int(0.15 * w), int(0.09 * h))
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, _score in dets:
                if _TIMER_REGEX.search(text):
                    return True
        except Exception:
            pass

        return False

    def _dismiss_vip_reward_popup(self, screenshot: Path, size: tuple[int, int]) -> None:
        """Dismiss a VIP reward popup at a fixed point."""

        w, h = size
        dismiss_pt = (
            round(NORM_POPUP_DISMISS[0] * w),
            round(NORM_POPUP_DISMISS[1] * h),
            round(NORM_POPUP_DISMISS[2] * w),
            round(NORM_POPUP_DISMISS[3] * h),
        )
        self._click(dismiss_pt, screenshot)

    def _locate_wooden_nhan(
        self,
        _screenshot: Path,
        frame: Any,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        """Locate the white 'Nhận' button for the daily VIP points reward."""

        w, h = size
        cached = _PINNED_COORDINATES.get((w, h, "wooden_nhan"))
        if cached is not None:
            return cached

        # Limit OCR to the upper reward row so it cannot select the lower
        # green 'Nhận' button for the metal chest.
        roi = (int(w * 0.73), int(h * 0.22), int(w * 0.16), int(h * 0.12))
        try:
            for text, bounds, _score in read_text_detections(frame, roi):
                if "nhan" in strip_accents_and_normalize(text):
                    self._pin_bounds(size, "wooden_nhan", bounds)
                    return bounds
        except Exception:
            pass

        # Do not pin an unverified fallback: a later OCR pass can still
        # calibrate the button position for this resolution.
        return self._get_cached_bounds(size, "wooden_nhan", NORM_WOODEN_NHAN)

    def _is_vip_points_popup(self, frame: Any) -> bool:
        """Check if 'Thưởng điểm hàng ngày' popup is active."""
        h, w = frame.shape[:2]
        roi = (int(w * 0.25), int(h * 0.10), int(w * 0.50), int(h* 0.35))
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, _score in dets:
                norm = strip_accents_and_normalize(text)
                # "Điểm hôm nay" and "Miễn phí mỗi ngày" belong to the
                # normal VIP page, not to a reward overlay.
                if any(k in norm for k in ("thuongdiem", "thungdim")):
                    return True
        except Exception:
            pass
        return False

    def _is_metal_chest_collected(self, frame: Any, screenshot: Path) -> bool:
        """Check if VIP metal chest (daily free gift) has already been collected."""
        tpl = _DAILY_REWARD_TEMPLATES / "metal_chest_collected.png"
        if self.detector.locate_template(screenshot, tpl, confidence_threshold=0.75) is not None:
            return True

        h, w = frame.shape[:2]
        roi = (int(0.66 * w), int(0.42 * h), int(0.16 * w), int(0.12 * h))
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, _score in dets:
                if _TIMER_REGEX.search(text):
                    return True
        except Exception:
            pass

        return False

    def _locate_green_nhan(
        self,
        screenshot: Path,
        frame: Any,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        w, h = size
        cached = _PINNED_COORDINATES.get((w, h, "green_nhan"))
        if cached is not None:
            return cached

        # Try OCR for 'Nhận' in metal chest area
        roi = (int(w * 0.66), int(h * 0.40), int(w * 0.22), int(h * 0.17))
        try:
            dets = read_text_detections(frame, roi)
            for text, box, _score in dets:
                norm = strip_accents_and_normalize(text)
                if "nhan" in norm:
                    self._pin_bounds(size, "green_nhan", box)
                    return box
        except Exception:
            pass

        # Try template matching
        tpl = _DAILY_REWARD_TEMPLATES / "green_nhan.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.70)
            if hit is not None:
                x, y, width, height = hit.bounds
                if 0.66 * w <= x + width / 2 <= 0.88 * w and 0.40 * h <= y + height / 2 <= 0.57 * h:
                    self._pin_bounds(size, "green_nhan", hit.bounds)
                    return hit.bounds

        fallback = self._get_cached_bounds(size, "green_nhan", NORM_GREEN_NHAN)
        self._pin_bounds(size, "green_nhan", fallback)
        return fallback

    def _is_vip_chest_popup(self, frame: Any) -> bool:
        """Check if 'Xin chúc mừng' reward popup is active."""
        h, w = frame.shape[:2]
        roi = (int(w * 0.20), 0, int(w * 0.60), int(h * 0.25))
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, _score in dets:
                norm = strip_accents_and_normalize(text)
                if any(k in norm for k in ("chucmung", "xinchucmung")):
                    return True
        except Exception:
            pass
        return False

    def _close_vip_menu(
        self, screenshot: Path | None = None, size: tuple[int, int] | None = None,
    ) -> None:
        """Close the VIP menu once the chest checks finish."""
        self._log("Closing VIP menu and checking for City view...")
        if screenshot is not None and size is not None:
            self._click(self._locate_vip_close_x(screenshot, size), screenshot)
        if not self.ensure_city_view():
            raise RuntimeError("VIP menu did not return to City")

    def _locate_vip_close_x(
        self,
        screenshot: Path,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        tpl = _DAILY_REWARD_TEMPLATES / "vip_close_x.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.75)
            if hit is not None:
                self._pin_bounds(size, "vip_close_x", hit.bounds)
                return hit.bounds

        # Recovery may have been triggered by a stale close position.
        w, h = size
        return (round(w * NORM_VIP_CLOSE_X[0]), round(h * NORM_VIP_CLOSE_X[1]),
                round(w * NORM_VIP_CLOSE_X[2]), round(h * NORM_VIP_CLOSE_X[3]))

    # -------------------------------------------------------------------------
    # STEP 2: Campaign (Chiến Dịch / Chinh Phạt) Rewards
    # -------------------------------------------------------------------------

    def claim_campaign_rewards(self, *, initial_capture: _Capture | None = None) -> bool:
        """Claim daily campaign golden chest in Chinh Phạt."""
        self._log("Step 2/2: Checking Campaign daily rewards...")
        screenshot, _, _ = initial_capture if initial_capture is not None else self._capture()
        state = self.detector.detect(
            screenshot, candidate_states=_CAMPAIGN_SCREENS | {ScreenState.CITY}, read_account=False,
        ).state
        if state not in _CAMPAIGN_SCREENS:
            if state is not ScreenState.CITY and not self.ensure_city_view(verified_back_only=True):
                raise RuntimeError("Could not reach City to open Campaign")
            self._open_bottom_menu_if_needed()
            self._open_chien_dich_menu()
        if state is not ScreenState.CHINH_PHAT:
            self._open_chinh_phat_screen()

        collected = self._claim_visible_reward(
            "Chinh phạt daily golden chest", "golden_chest",
            is_menu=partial(self._is_screen, state=ScreenState.CHINH_PHAT),
            is_collected=self._is_chinh_phat_chest_collected,
            locate_button=self._locate_golden_chest,
            dismiss_popup=self._confirm_campaign_popup,
        )
        if collected or self._confirmed_rewards is None:
            self._close_campaign_menus()
        return collected

    def _open_bottom_menu_if_needed(self) -> None:
        """Allow the bottom row to settle before deciding whether to toggle it."""
        expanded = False
        for scan in range(MIN_SCREEN_SCANS):
            screenshot, frame, size = self._capture()
            if self._campaign_button_visible(screenshot, frame, size):
                self._log("Bottom menu is already open.")
                return
            expanded = expanded or self.detector.locate_template(
                screenshot, _PROJECT_ROOT / "templates/alliance/menu_shield.png",
                confidence_threshold=.88, refine_scale=True,
            ) is not None
            if scan + 1 < MIN_SCREEN_SCANS:
                self.game.wait(.20)
        if expanded:
            raise RuntimeError("Bottom menu is already open, but the Chiến dịch button could not be verified")

        dots = self.detector.locate_template(
            screenshot, _DAILY_REWARD_TEMPLATES / "menu_dots_glyph.png",
            confidence_threshold=.90, minimum_scale=.60, refine_scale=True,
        )
        if dots is None:
            dots = self.detector.locate_template(
                screenshot, _DAILY_REWARD_TEMPLATES / "menu_dots.png",
                confidence_threshold=.82, refine_scale=True,
            )
        if dots is None:
            raise RuntimeError("Could not locate the bottom-menu toggle")
        self._log("Opening bottom menu (clicking 3 dots at bottom-right)...")
        self._click(dots.bounds, screenshot)
        if not self._wait_for_condition(self._campaign_button_visible):
            raise RuntimeError("Chiến dịch button did not appear after expanding the bottom menu")

    def _campaign_button_visible(self, screenshot: Path, frame: Any, size: tuple[int, int]) -> bool:
        return self._find_chien_dich(screenshot, frame, size) is not None

    def _open_chien_dich_menu(self) -> None:
        """Click 'Chiến dịch' button and wait for menu to load."""
        opened = self._open_reward_menu(
            "'Chiến dịch' button", "chien_dich", self._find_chien_dich,
            partial(self._is_screen, state=ScreenState.CHIEN_DICH),
        )
        if not opened:
            raise RuntimeError("Chiến dịch menu did not open after rescanning")

    def _find_chien_dich(self, screenshot: Path, frame: Any, size: tuple[int, int]) -> _Bounds | None:
        hit = self.detector.locate_template(
            screenshot, _DAILY_REWARD_TEMPLATES / "chien_dich_swords.png",
            confidence_threshold=.86, refine_scale=True,
        )
        if hit is not None:
            return hit.bounds

        w, h = size
        roi = (round(w * .523), round(h * .955), round(w * .087), round(h * .045))
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, score in dets:
                norm = strip_accents_and_normalize(text)
                if score >= .70 and norm in ("chiendich", "chindich", "chindch"):
                    x, y, width, height = NORM_CHIEN_DICH
                    return (round(x * w), round(y * h), round(width * w), round(height * h))
        except Exception:
            pass

        return None

    def _open_chinh_phat_screen(self) -> None:
        """Click 'Chinh phạt' card and wait for map screen."""
        opened = self._open_reward_menu(
            "'Chinh phạt' card", "chinh_phat", self._locate_chinh_phat,
            partial(self._is_screen, state=ScreenState.CHINH_PHAT),
            partial(self._is_screen, state=ScreenState.CHIEN_DICH),
        )
        if not opened:
            raise RuntimeError("Chinh phạt screen did not open after rescanning")

    def _locate_chinh_phat(
        self,
        screenshot: Path,
        frame: Any,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        w, h = size
        cached = _PINNED_COORDINATES.get((w, h, "chinh_phat"))
        if cached is not None:
            return cached

        # Try OCR
        roi = (0, int(h * 0.10), int(w * 0.28), int(h * 0.45))
        try:
            dets = read_text_detections(frame, roi)
            for text, box, _score in dets:
                norm = strip_accents_and_normalize(text)
                if any(k in norm for k in ("chinhphat", "chinhpht")):
                    self._pin_bounds(size, "chinh_phat", box)
                    return box
        except Exception:
            pass

        tpl = _DAILY_REWARD_TEMPLATES / "chinh_phat_card.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.70)
            if hit is not None:
                self._pin_bounds(size, "chinh_phat", hit.bounds)
                return hit.bounds

        fallback = self._get_cached_bounds(size, "chinh_phat", NORM_CHINH_PHAT)
        self._pin_bounds(size, "chinh_phat", fallback)
        return fallback

    def _is_chinh_phat_chest_collected(self, _frame: Any, screenshot: Path) -> bool:
        """Check if Chinh phạt campaign daily chest has already been collected."""
        # 1. If uncollected golden glowing chest is detected, it is definitely not collected.
        tpl_gold = _DAILY_REWARD_TEMPLATES / "golden_chest.png"
        if tpl_gold.is_file():
            hit_gold = self.detector.locate_template(screenshot, tpl_gold, confidence_threshold=0.70)
            if hit_gold is not None:
                return False

        return self.detector.locate_template(
            screenshot, _DAILY_REWARD_TEMPLATES / "chinh_phat_chest_collected.png",
            confidence_threshold=0.78,
        ) is not None

    def _confirm_campaign_popup(self, screenshot: Path, frame: Any, size: tuple[int, int]) -> bool:
        bounds = self._detect_xac_nhan(screenshot, frame, size)
        if bounds is None:
            return False
        self._log("Chinh phạt reward popup appeared. Clicking 'Xác nhận'...")
        self._click(bounds, screenshot)
        return True

    def _locate_golden_chest(
        self,
        screenshot: Path,
        _frame: Any,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        w, h = size
        cached = _PINNED_COORDINATES.get((w, h, "golden_chest"))
        if cached is not None:
            return cached

        tpl = _DAILY_REWARD_TEMPLATES / "golden_chest.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.70)
            if hit is not None:
                self._pin_bounds(size, "golden_chest", hit.bounds)
                return hit.bounds

        fallback = self._get_cached_bounds(size, "golden_chest", NORM_GOLDEN_CHEST)
        self._pin_bounds(size, "golden_chest", fallback)
        return fallback

    def _detect_xac_nhan(
        self,
        screenshot: Path,
        frame: Any,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int] | None:
        """Find 'XÁC NHẬN' button on reward popup if present."""
        w, h = size
        roi = (int(w * 0.30), int(h * 0.70), int(w * 0.40), int(h * 0.28))
        try:
            dets = read_text_detections(frame, roi)
            for text, box, _score in dets:
                norm = strip_accents_and_normalize(text)
                if "xacnhan" in norm:
                    self._pin_bounds(size, "xac_nhan", box)
                    return box
        except Exception:
            pass

        tpl = _DAILY_REWARD_TEMPLATES / "xac_nhan.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.75)
            if hit is not None:
                self._pin_bounds(size, "xac_nhan", hit.bounds)
                return hit.bounds

        return None

    def _close_campaign_menus(self) -> None:
        """Return from Campaign and verify City after the Back presses."""
        self._log("Closing campaign menus and checking for City view...")
        if not self.ensure_city_view():
            raise RuntimeError("Campaign menus did not return to City")

    def _locate_back_button(
        self,
        screenshot: Path,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        w, h = size
        cached = _PINNED_COORDINATES.get((w, h, "back_button"))
        if cached is not None:
            return cached

        tpl = _DAILY_REWARD_TEMPLATES / "back_button.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.75)
            if hit is not None:
                self._pin_bounds(size, "back_button", hit.bounds)
                return hit.bounds

        fallback = self._get_cached_bounds(size, "back_button", NORM_BACK_BUTTON)
        self._pin_bounds(size, "back_button", fallback)
        return fallback

    # -------------------------------------------------------------------------
    # Polling & Wait Utilities
    # -------------------------------------------------------------------------

    def _wait_for_condition(
        self,
        condition: _ScreenCheck,
        timeout: float = 3.0,
        interval: float = 0.25,
    ) -> bool:
        """Poll fresh frames until success, allowing at least five rescans."""
        from rok_automation.game.popups import close_mailbox

        deadline = time.monotonic() + timeout

        for _scan in screen_scan_attempts(deadline, clock=time.monotonic):
            try:
                screenshot, frame, size = self._capture()
            except OSError:
                pass
            else:
                if condition(screenshot, frame, size):
                    return True
                close_mailbox(self.game, self.detector, screenshot, log=self._log)

            self.game.wait(interval)

        return False
