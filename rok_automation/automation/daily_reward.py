"""Claim daily VIP points, VIP free chest, and Campaign golden chest."""

from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from rok_automation.game.console import safe_print
from rok_automation.game.interface import GameInterface
from rok_automation.vision.account_label import read_text_detections
from rok_automation.vision.screen import (
    ScreenDetector,
    ScreenState,
    load_color_frame,
    load_gray_frame,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DAILY_REWARD_TEMPLATES = _PROJECT_ROOT / "templates/daily_reward"
_NAVIGATION_TEMPLATES = _PROJECT_ROOT / "templates/navigation"

# Static normalized coordinates (x, y, w, h) for standard 16:9 resolutions.
# Used as fast lookups and reliable fallbacks.
NORM_VIP_BUTTON = (0.075, 0.065, 0.075, 0.045)
# The daily-VIP-points reward is claimed with the upper white "Nhận" button,
# below the chest artwork. Targeting the artwork can land beside the close X.
NORM_WOODEN_NHAN = (0.820, 0.225, 0.140, 0.055)
NORM_GREEN_NHAN = (0.771, 0.463, 0.080, 0.056)
NORM_VIP_CLOSE_X = (0.940, 0.015, 0.045, 0.055)
NORM_POPUP_DISMISS = (0.500, 0.920, 0.010, 0.010)

NORM_MENU_DOTS = (0.925, 0.900, 0.055, 0.080)
NORM_CHIEN_DICH = (0.535, 0.880, 0.065, 0.090)
NORM_CHINH_PHAT = (0.050, 0.150, 0.185, 0.350)
NORM_GOLDEN_CHEST = (0.050, 0.250, 0.085, 0.140)
NORM_XAC_NHAN = (0.394, 0.779, 0.229, 0.136)
NORM_BACK_BUTTON = (0.010, 0.015, 0.045, 0.055)

# Session-wide cache: (frame_width, frame_height, key) -> (x, y, w, h)
# Ensures static button locations are scanned once and reused across all accounts.
_PINNED_COORDINATES: dict[tuple[int, int, str], tuple[int, int, int, int]] = {}

# Countdown timer pattern (e.g. "09:23:34" or "10:38") indicating collected rewards.
_TIMER_REGEX = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_VIP_REWARD_CLAIM_DELAY = 2.0


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

    def _log(self, message: str) -> None:
        self.log(f"[Daily Reward] {message}")

    def _capture(self) -> tuple[Path, Any, tuple[int, int]]:
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
        """Execute the complete daily reward sequence:

        1. Return to City view if on World Map.
        2. Claim VIP rewards (Wooden chest daily points + Metal chest free VIP gift).
        3. Claim Campaign rewards (Chiến dịch -> Chinh phạt -> Golden chest -> Xác nhận).
        4. Return to City view.
        """
        self._log("Starting daily reward routine (VIP & Campaign)...")
        try:
            self.ensure_city_view()
            vip_ok = self.claim_vip_rewards()
            campaign_ok = self.claim_campaign_rewards()
            if vip_ok and campaign_ok:
                self._log("Daily rewards collected successfully.")
                return True
            elif vip_ok or campaign_ok:
                self._log("Daily rewards partially collected.")
                return True
            else:
                self._log("Could not collect daily rewards; proceeding.")
                return False
        except Exception as exc:
            self._log(f"Encountered unexpected error during daily rewards: {exc}")
            self._recover_to_city()
            return False

    def ensure_city_view(self, max_attempts: int = 5) -> bool:
        """Ensure game is in City view before navigating menus."""
        for attempt in range(max_attempts):
            screenshot, frame, (w, h) = self._capture()
            detection = self.detector.detect(screenshot)
            if detection.state is ScreenState.CITY:
                return True
            if detection.state is ScreenState.WORLD_MAP:
                self._log("Currently on World Map; returning to City view...")
                city_btn = (
                    0,
                    round(0.74 * h),
                    round(0.16 * w),
                    round(0.26 * h),
                )
                self.game.click(city_btn, screenshot)
                self.game.wait(1.0)
                continue

            # In some open menu or popup: try clicking top-left back button or margin
            self._log(f"Screen is {detection.state.value}; pressing Back to reach City...")
            back_btn = self._get_cached_bounds((w, h), "back_button", NORM_BACK_BUTTON)
            self.game.click(back_btn, screenshot)
            self.game.wait(0.5)

        return False

    def _recover_to_city(self) -> None:
        """Emergency recovery: close any open dialogs/menus back to City."""
        self._log("Attempting recovery back to City view...")
        for _ in range(3):
            try:
                screenshot, frame, (w, h) = self._capture()
                detection = self.detector.detect(screenshot)
                if detection.state is ScreenState.CITY:
                    return
                # Tap top-left back button
                back_btn = self._get_cached_bounds((w, h), "back_button", NORM_BACK_BUTTON)
                self.game.click(back_btn, screenshot)
                self.game.wait(0.5)
            except Exception:
                break

    # -------------------------------------------------------------------------
    # STEP 1: VIP Rewards
    # -------------------------------------------------------------------------

    def claim_vip_rewards(self) -> bool:
        """Claim daily VIP points (wooden chest) and daily VIP gift (metal chest)."""
        self._log("Step 1/2: Checking VIP daily rewards...")
        screenshot, frame, size = self._capture()
        w, h = size

        # 1. Locate and click VIP button
        vip_bounds = self._locate_vip_button(screenshot, frame, size)
        self._log(f"Clicking VIP icon at {vip_bounds}...")
        self.game.click(vip_bounds, screenshot)
        self.game.wait(0.5)

        # 2. Wait for VIP menu to open
        menu_open = self._wait_for_condition(
            condition=lambda f: self._is_vip_menu(f),
            timeout=3.5,
            retry_action=lambda: self.game.click(vip_bounds, self.game.screenshot()),
        )
        if not menu_open:
            self._log("VIP menu did not open after retries; skipping VIP rewards.")
            return False

        # 3. Claim Wooden Chest (above) - Daily Points (or skip if already collected)
        screenshot, frame, size = self._capture()
        if self._is_wooden_chest_collected(frame, screenshot):
            self._log("VIP daily points (wooden chest) already collected today. Skipping.")
        else:
            self._claim_wooden_chest(size)

        # 4. Claim Metal Chest (below) - Daily Free Gift (or skip if already collected)
        screenshot, frame, size = self._capture()
        if self._is_metal_chest_collected(frame, screenshot):
            self._log("VIP daily free gift (metal chest) already collected today. Skipping.")
        else:
            self._claim_metal_chest(size)

        # 5. Close VIP menu via 'X' on top right
        self._close_vip_menu(size)
        return True

    def _locate_vip_button(
        self,
        screenshot: Path,
        frame: Any,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        """Locate VIP icon/text under governor avatar, with caching & fallback."""
        w, h = size
        cached = _PINNED_COORDINATES.get((w, h, "vip_button"))
        if cached is not None:
            return cached

        # Try OCR in top-left region
        roi = (0, 0, int(w * 0.35), int(h * 0.20))
        try:
            dets = read_text_detections(frame, roi)
            for text, box, _score in dets:
                norm = strip_accents_and_normalize(text)
                if "vip" in norm:
                    self._pin_bounds(size, "vip_button", box)
                    return box
        except Exception:
            pass

        # Try template matching VIP shield
        shield_tpl = _DAILY_REWARD_TEMPLATES / "vip_shield.png"
        if shield_tpl.is_file():
            hit = self.detector.locate_template(
                screenshot,
                shield_tpl,
                confidence_threshold=0.65,
            )
            if hit is not None:
                self._pin_bounds(size, "vip_button", hit.bounds)
                return hit.bounds

        # Fallback to normalized position
        fallback = self._get_cached_bounds(size, "vip_button", NORM_VIP_BUTTON)
        self._pin_bounds(size, "vip_button", fallback)
        return fallback

    def _is_vip_menu(self, frame: Any) -> bool:
        """Check if VIP menu is currently visible."""
        h, w = frame.shape[:2]
        roi = (0, 0, w, int(h * 0.35))
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, _score in dets:
                norm = strip_accents_and_normalize(text)
                if norm == "vip" or "docquyen" in norm or "mienphi" in norm:
                    return True
        except Exception:
            pass
        return False

    def _is_wooden_chest_collected(self, frame: Any, screenshot: Path) -> bool:
        """Check if VIP wooden chest (daily points) has already been collected."""
        h, w = frame.shape[:2]
        roi = (int(0.60 * w), 0, int(0.40 * w), int(0.35 * h))
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, _score in dets:
                if _TIMER_REGEX.search(text):
                    return True
        except Exception:
            pass

        tpl = _DAILY_REWARD_TEMPLATES / "wooden_chest_collected.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.75)
            if hit is not None:
                return True
        return False

    def _claim_wooden_chest(self, size: tuple[int, int]) -> None:
        """Click wooden chest above to claim daily points, and dismiss popup."""
        self._log("Claiming VIP daily points (wooden chest above)...")
        screenshot, frame, _ = self._capture()

        # Claim through the white upper 'Nhận' button, not the chest artwork.
        nhan_bounds = self._locate_wooden_nhan(frame, size)
        self.game.click(nhan_bounds, screenshot)
        self.game.wait(_VIP_REWARD_CLAIM_DELAY)

        # Check for 'Thưởng điểm hàng ngày' popup
        _, popup_frame, _ = self._capture()
        if self._is_vip_points_popup(popup_frame):
            self._log("Daily VIP points popup appeared. Dismissing popup...")
            self._dismiss_vip_reward_popup(size)
            self.game.wait(0.3)
        else:
            self._log("VIP daily points already claimed today or no popup shown.")

    def _dismiss_vip_reward_popup(self, size: tuple[int, int]) -> None:
        """Dismiss a VIP reward popup at a fixed point."""

        w, h = size
        dismiss_pt = (
            round(NORM_POPUP_DISMISS[0] * w),
            round(NORM_POPUP_DISMISS[1] * h),
            1,
            1,
        )
        screenshot = self.game.screenshot()
        self.game.click(dismiss_pt, screenshot)

    def _locate_wooden_nhan(
        self,
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
        roi = (int(w * 0.78), int(h * 0.18), int(w * 0.20), int(h * 0.14))
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
                if any(k in norm for k in ("thuongdiem", "thungdim", "diemhomnay", "dimhomnay", "hangngay")):
                    return True
        except Exception:
            pass
        return False

    def _is_metal_chest_collected(self, frame: Any, screenshot: Path) -> bool:
        """Check if VIP metal chest (daily free gift) has already been collected."""
        h, w = frame.shape[:2]
        roi = (int(0.40 * w), int(0.35 * h), int(0.60 * w), int(0.60 * h))
        dets: list[tuple[str, tuple[int, int, int, int], float]] = []
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, _score in dets:
                if _TIMER_REGEX.search(text):
                    return True
        except Exception:
            pass

        tpl = _DAILY_REWARD_TEMPLATES / "metal_chest_collected.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.75)
            if hit is not None:
                return True

        # Check absence of green 'Nhận' button and text
        tpl_nhan = _DAILY_REWARD_TEMPLATES / "green_nhan.png"
        hit_nhan = None
        if tpl_nhan.is_file():
            hit_nhan = self.detector.locate_template(screenshot, tpl_nhan, confidence_threshold=0.70)
        has_nhan_text = False
        for text, _b, _s in dets:
            if "nhan" in strip_accents_and_normalize(text):
                has_nhan_text = True
                break
        if hit_nhan is None and not has_nhan_text:
            return True

        return False

    def _claim_metal_chest(self, size: tuple[int, int]) -> None:
        """Click green 'Nhận' button on metal chest below, and dismiss popup."""
        self._log("Claiming VIP daily free chest (metal chest below)...")
        screenshot, frame, _ = self._capture()

        nhan_bounds = self._locate_green_nhan(screenshot, frame, size)
        self.game.click(nhan_bounds, screenshot)
        self.game.wait(_VIP_REWARD_CLAIM_DELAY)

        # Check for 'Xin chúc mừng' popup
        _, popup_frame, _ = self._capture()
        if self._is_vip_chest_popup(popup_frame):
            self._log("VIP daily gift popup appeared ('Xin chúc mừng'). Dismissing...")
            self._dismiss_vip_reward_popup(size)
            self.game.wait(0.3)
        else:
            self._log("VIP daily free chest already claimed today or no popup shown.")

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
        roi = (int(w * 0.70), int(h * 0.40), int(w * 0.25), int(h * 0.18))
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

    def _close_vip_menu(self, size: tuple[int, int]) -> None:
        """Click top-right 'X' button to close VIP menu and return to City."""
        w, h = size
        self._log("Closing VIP menu (clicking X on top-right)...")
        screenshot, frame, _ = self._capture()

        x_bounds = self._locate_vip_close_x(screenshot, size)
        self.game.click(x_bounds, screenshot)
        self.game.wait(0.5)

        # Verify VIP menu closed
        closed = self._wait_for_condition(
            condition=lambda f: not self._is_vip_menu(f),
            timeout=2.5,
            retry_action=lambda: self.game.click(x_bounds, self.game.screenshot()),
        )
        if not closed:
            self._log("Warning: VIP menu did not close immediately; proceeding.")

    def _locate_vip_close_x(
        self,
        screenshot: Path,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        w, h = size
        cached = _PINNED_COORDINATES.get((w, h, "vip_close_x"))
        if cached is not None:
            return cached

        tpl = _DAILY_REWARD_TEMPLATES / "vip_close_x.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.75)
            if hit is not None:
                self._pin_bounds(size, "vip_close_x", hit.bounds)
                return hit.bounds

        fallback = self._get_cached_bounds(size, "vip_close_x", NORM_VIP_CLOSE_X)
        self._pin_bounds(size, "vip_close_x", fallback)
        return fallback

    # -------------------------------------------------------------------------
    # STEP 2: Campaign (Chiến Dịch / Chinh Phạt) Rewards
    # -------------------------------------------------------------------------

    def claim_campaign_rewards(self) -> bool:
        """Claim daily campaign golden chest in Chinh Phạt."""
        self._log("Step 2/2: Checking Campaign daily rewards...")
        screenshot, frame, size = self._capture()
        w, h = size

        # 1. Ensure bottom menu is open (expand if needed)
        self._open_bottom_menu_if_needed(size)

        # 2. Click 'Chiến dịch' button
        self._open_chien_dich_menu(size)

        # 3. Click 'Chinh phạt' card
        self._open_chinh_phat_screen(size)

        # 4. Check if golden chest is already claimed, or click to claim
        screenshot, frame, size = self._capture()
        if self._is_chinh_phat_chest_collected(frame, screenshot):
            self._log("Chinh phạt daily golden chest already collected today. Skipping claim.")
        else:
            self._claim_chinh_phat_chest(size)

        # 5. Press '<' back button twice at the same position to return to City
        self._close_campaign_menus(size)
        return True

    def _open_bottom_menu_if_needed(self, size: tuple[int, int]) -> None:
        """Open bottom-right 3-dots menu if Chiến dịch button is not visible."""
        w, h = size
        screenshot, frame, _ = self._capture()

        # Check if 'Chiến dịch' is already visible
        roi = (int(w * 0.50), int(h * 0.82), int(w * 0.16), int(h * 0.18))
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, _score in dets:
                norm = strip_accents_and_normalize(text)
                if any(k in norm for k in ("chiendich", "chindich", "dich")):
                    self._log("Bottom menu is already open.")
                    return
        except Exception:
            pass

        # Click 3-dots menu button on bottom-right
        self._log("Opening bottom menu (clicking 3 dots at bottom-right)...")
        dots_bounds = self._locate_menu_dots(screenshot, size)
        self.game.click(dots_bounds, screenshot)
        self.game.wait(0.5)

    def _locate_menu_dots(
        self,
        screenshot: Path,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        w, h = size
        cached = _PINNED_COORDINATES.get((w, h, "menu_dots"))
        if cached is not None:
            return cached

        tpl = _DAILY_REWARD_TEMPLATES / "menu_dots.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.75)
            if hit is not None:
                self._pin_bounds(size, "menu_dots", hit.bounds)
                return hit.bounds

        fallback = self._get_cached_bounds(size, "menu_dots", NORM_MENU_DOTS)
        self._pin_bounds(size, "menu_dots", fallback)
        return fallback

    def _open_chien_dich_menu(self, size: tuple[int, int]) -> None:
        """Click 'Chiến dịch' button and wait for menu to load."""
        w, h = size
        self._log("Clicking 'Chiến dịch' button...")
        screenshot, frame, _ = self._capture()

        cd_bounds = self._locate_chien_dich(screenshot, frame, size)
        self.game.click(cd_bounds, screenshot)
        self.game.wait(0.5)

        opened = self._wait_for_condition(
            condition=lambda f: self._is_chien_dich_menu(f),
            timeout=3.5,
            retry_action=lambda: self.game.click(cd_bounds, self.game.screenshot()),
        )
        if not opened:
            self._log("Warning: Chiến dịch menu may not have opened; proceeding anyway.")

    def _locate_chien_dich(
        self,
        screenshot: Path,
        frame: Any,
        size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        w, h = size
        cached = _PINNED_COORDINATES.get((w, h, "chien_dich"))
        if cached is not None:
            return cached

        # Try OCR
        roi = (int(w * 0.50), int(h * 0.82), int(w * 0.16), int(h * 0.18))
        try:
            dets = read_text_detections(frame, roi)
            for text, box, _score in dets:
                norm = strip_accents_and_normalize(text)
                if any(k in norm for k in ("chiendich", "chindich", "dich")):
                    # Box is text; circular swords button is right above it
                    swords_box = (
                        box[0],
                        max(0, box[1] - round(0.06 * h)),
                        box[2],
                        round(0.06 * h),
                    )
                    self._pin_bounds(size, "chien_dich", swords_box)
                    return swords_box
        except Exception:
            pass

        tpl = _DAILY_REWARD_TEMPLATES / "chien_dich.png"
        if tpl.is_file():
            hit = self.detector.locate_template(screenshot, tpl, confidence_threshold=0.70)
            if hit is not None:
                self._pin_bounds(size, "chien_dich", hit.bounds)
                return hit.bounds

        fallback = self._get_cached_bounds(size, "chien_dich", NORM_CHIEN_DICH)
        self._pin_bounds(size, "chien_dich", fallback)
        return fallback

    def _is_chien_dich_menu(self, frame: Any) -> bool:
        """Check if Chiến dịch screen is visible."""
        h, w = frame.shape[:2]
        roi = (0, 0, w, int(h * 0.50))
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, _score in dets:
                norm = strip_accents_and_normalize(text)
                if any(k in norm for k in ("chiendich", "chindich", "chindch", "chinhphat", "chinhpht")):
                    return True
        except Exception:
            pass
        return False

    def _open_chinh_phat_screen(self, size: tuple[int, int]) -> None:
        """Click 'Chinh phạt' card and wait for map screen."""
        w, h = size
        self._log("Clicking 'Chinh phạt' card...")
        screenshot, frame, _ = self._capture()

        cp_bounds = self._locate_chinh_phat(screenshot, frame, size)
        self.game.click(cp_bounds, screenshot)
        self.game.wait(0.6)

        opened = self._wait_for_condition(
            condition=lambda f: self._is_chinh_phat_screen(f),
            timeout=3.5,
            retry_action=lambda: self.game.click(cp_bounds, self.game.screenshot()),
        )
        if not opened:
            self._log("Warning: Chinh phạt screen may not have opened; proceeding anyway.")

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

    def _is_chinh_phat_screen(self, frame: Any) -> bool:
        """Check if Chinh phạt map screen is visible."""
        h, w = frame.shape[:2]
        roi = (int(w * 0.30), 0, int(w * 0.40), int(h * 0.15))
        try:
            dets = read_text_detections(frame, roi)
            for text, _box, _score in dets:
                norm = strip_accents_and_normalize(text)
                if "chinhphat" in norm or "phat" in norm:
                    return True
        except Exception:
            pass
        return False

    def _is_chinh_phat_chest_collected(self, frame: Any, screenshot: Path) -> bool:
        """Check if Chinh phạt campaign daily chest has already been collected."""
        # 1. If uncollected golden glowing chest is detected, it is definitely not collected.
        tpl_gold = _DAILY_REWARD_TEMPLATES / "golden_chest.png"
        if tpl_gold.is_file():
            hit_gold = self.detector.locate_template(screenshot, tpl_gold, confidence_threshold=0.70)
            if hit_gold is not None:
                return False

        # 2. Check for collected (open grey chest) template.
        tpl_collected = _DAILY_REWARD_TEMPLATES / "chinh_phat_chest_collected.png"
        if tpl_collected.is_file():
            hit_col = self.detector.locate_template(screenshot, tpl_collected, confidence_threshold=0.68)
            if hit_col is not None:
                return True

        # 3. Check color saturation in the chest dial ROI.
        h, w = frame.shape[:2]
        rx = int(0.04 * w)
        ry = int(0.22 * h)
        rw = int(0.12 * w)
        rh = int(0.20 * h)
        crop = frame[ry : ry + rh, rx : rx + rw]
        if crop.size > 0:
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            sat_mean = float(np.mean(hsv[:, :, 1]))
            # Glowing golden chest has sat > 70; open grey chest has sat < 40
            if sat_mean < 45.0:
                return True

        return False

    def _claim_chinh_phat_chest(self, size: tuple[int, int]) -> None:
        """Click yellow golden chest and confirm with 'Xác nhận'."""
        w, h = size
        self._log("Claiming Chinh phạt daily golden chest...")
        screenshot, frame, _ = self._capture()

        chest_bounds = self._locate_golden_chest(screenshot, size)
        self.game.click(chest_bounds, screenshot)
        self.game.wait(0.6)

        # Check for 'XÁC NHẬN' reward popup
        popup_path, popup_frame, _ = self._capture()
        xac_nhan_bounds = self._detect_xac_nhan(popup_path, popup_frame, size)
        if xac_nhan_bounds is not None:
            self._log("Chinh phạt reward popup appeared. Clicking 'Xác nhận'...")
            self.game.click(xac_nhan_bounds, popup_path)
            self.game.wait(0.4)
        else:
            self._log("Chinh phạt golden chest already claimed today or no reward popup shown.")

    def _locate_golden_chest(
        self,
        screenshot: Path,
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
                if "xacnhan" in norm or "xac" in norm:
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

    def _close_campaign_menus(self, size: tuple[int, int]) -> None:
        """Press '<' back button twice to leave the campaign screens."""
        w, h = size
        self._log("Closing Chinh phạt and Chiến dịch menus (clicking Back button twice)...")
        screenshot, frame, _ = self._capture()

        back_bounds = self._locate_back_button(screenshot, size)

        # First press: closes Chinh phạt -> returns to Chiến dịch
        self._log("Pressing Back button (1/2) to exit Chinh phạt...")
        self.game.click(back_bounds, screenshot)
        self.game.wait(0.8)

        # Second press at the same position: closes Chiến dịch -> returns to City
        self._log("Pressing Back button (2/2) to exit Chiến dịch...")
        self.game.click(back_bounds, self.game.screenshot())
        self.game.wait(1.0)

        self._log("Campaign menus closed.")

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
        condition: Callable[[Any], bool],
        timeout: float = 3.0,
        interval: float = 0.25,
        retry_action: Callable[[], None] | None = None,
    ) -> bool:
        """Poll condition with periodic retry until True or timeout."""
        deadline = time.monotonic() + timeout
        retry_deadline = time.monotonic() + (timeout / 2.0)
        retried = False

        while time.monotonic() < deadline:
            try:
                _p, frame, _s = self._capture()
                if condition(frame):
                    return True
            except Exception:
                pass

            if not retried and retry_action and time.monotonic() > retry_deadline:
                retried = True
                try:
                    retry_action()
                except Exception:
                    pass

            self.game.wait(interval)

        return False
