"""Tests for the daily reward collection feature (VIP & Campaign)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from rok_automation.automation.controller import FarmingController
from rok_automation.automation.daily_reward import (
    DailyRewardClaimer,
    strip_accents_and_normalize,
    _PINNED_COORDINATES,
)
from rok_automation.automation.states import FarmState
from rok_automation.game.interface import GameInterface, TimingConfig
from rok_automation.vision.screen import ScreenDetection, ScreenDetector, ScreenState

_TEST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent
_ARTIFACTS_DIR = _PROJECT_ROOT / "artifacts"


class FakeGame:
    """Mock GameInterface that logs clicks and returns test frames."""

    def __init__(self, frames: list[Path] | None = None) -> None:
        self.timing = TimingConfig()
        self.clicks: list[tuple[int, int, int, int]] = []
        self._frames = list(frames or [])
        self._frame_idx = 0
        self._default_frame = _ARTIFACTS_DIR / "test_960.png"

    def screenshot(self) -> Path:
        if self._frame_idx < len(self._frames):
            frame = self._frames[self._frame_idx]
            self._frame_idx += 1
            return frame
        return self._default_frame

    def click(self, bounds: tuple[int, int, int, int], screenshot_path: str | Path) -> None:
        self.clicks.append(bounds)

    def drag(self, start: tuple[int, int], end: tuple[int, int], screenshot_path: str | Path) -> None:
        pass

    def wait(self, seconds: float) -> None:
        pass


class TestDailyReward(unittest.TestCase):
    """Unit tests for daily reward collection logic and vision components."""

    def setUp(self) -> None:
        _PINNED_COORDINATES.clear()

    def test_strip_accents_and_normalize(self) -> None:
        self.assertEqual(strip_accents_and_normalize("XÁC NHẬN"), "xacnhan")
        self.assertEqual(strip_accents_and_normalize("XÁC NHÂN"), "xacnhan")
        self.assertEqual(strip_accents_and_normalize("Thưởng điểm hàng ngày"), "thuongdiemhangngay")
        self.assertEqual(strip_accents_and_normalize("Thưng đim hàng ngày"), "thungdimhangngay")
        self.assertEqual(strip_accents_and_normalize("XIN CHÚC MỪNG"), "xinchucmung")
        self.assertEqual(strip_accents_and_normalize("Chiến dịch"), "chiendich")
        self.assertEqual(strip_accents_and_normalize("Chin dch"), "chindch")
        self.assertEqual(strip_accents_and_normalize("Chinh phạt"), "chinhphat")
        self.assertEqual(strip_accents_and_normalize("Chinh pht"), "chinhpht")
        self.assertEqual(strip_accents_and_normalize("VIP 16"), "vip16")

    def test_pinned_coordinates_cache(self) -> None:
        """Verify static coordinates are cached across calls."""
        game = FakeGame()
        claimer = DailyRewardClaimer(game)

        # First call calculates and pins
        b1 = claimer._get_cached_bounds((1000, 500), "vip_button", (0.1, 0.2, 0.3, 0.4))
        self.assertEqual(b1, (100, 100, 300, 200))
        claimer._pin_bounds((1000, 500), "vip_button", (105, 105, 290, 195))

        # Subsequent call gets pinned coordinate directly
        b2 = claimer._get_cached_bounds((1000, 500), "vip_button", (0.1, 0.2, 0.3, 0.4))
        self.assertEqual(b2, (105, 105, 290, 195))

    def test_vip_detection_on_test_images(self) -> None:
        """Verify VIP button is accurately recognized on real game frames."""
        claimer = DailyRewardClaimer(FakeGame())
        for img_name in ("test_960.png", "test_1080.png"):
            p = _ARTIFACTS_DIR / img_name
            if not p.is_file():
                continue
            frame = cv2.imread(str(p))
            h, w = frame.shape[:2]
            bounds = claimer._locate_vip_button(p, frame, (w, h))
            # Center of VIP button should be in top-left region: x ~ 8-15%, y ~ 6-12%
            cx = (bounds[0] + bounds[2] / 2) / w
            cy = (bounds[1] + bounds[3] / 2) / h
            self.assertGreater(cx, 0.05)
            self.assertLess(cx, 0.20)
            self.assertGreater(cy, 0.04)
            self.assertLess(cy, 0.15)

    def test_popup_recognition_helpers(self) -> None:
        """Verify detection of various reward popup texts."""
        claimer = DailyRewardClaimer(FakeGame())

        # Create dummy frame with "Thưởng điểm hàng ngày" text via mock
        with patch("rok_automation.automation.daily_reward.read_text_detections") as mock_dets:
            mock_dets.return_value = [("Thưởng điểm hàng ngày", (300, 100, 200, 50), 0.95)]
            dummy = np.zeros((600, 1000, 3), dtype=np.uint8)
            self.assertTrue(claimer._is_vip_points_popup(dummy))

        with patch("rok_automation.automation.daily_reward.read_text_detections") as mock_dets:
            mock_dets.return_value = [("XIN CHÚC MỪNG", (300, 100, 200, 50), 0.95)]
            dummy = np.zeros((600, 1000, 3), dtype=np.uint8)
            self.assertTrue(claimer._is_vip_chest_popup(dummy))

        with patch("rok_automation.automation.daily_reward.read_text_detections") as mock_dets:
            mock_dets.return_value = [("XÁC NHẬN", (400, 500, 200, 60), 0.95)]
            dummy = np.zeros((600, 1000, 3), dtype=np.uint8)
            bounds = claimer._detect_xac_nhan(Path("dummy.png"), dummy, (1000, 600))
            self.assertIsNotNone(bounds)
            self.assertEqual(bounds, (400, 500, 200, 60))

    def test_simulated_claim_all_flow(self) -> None:
        """Verify full claim_all sequence executes all steps and clicks."""
        game = FakeGame()
        detector = MagicMock(spec=ScreenDetector)
        detector.detect.return_value = ScreenDetection(
            state=ScreenState.CITY,
            confidence=0.95,
            evidence="city map button",
        )
        detector.locate_template.return_value = None

        claimer = DailyRewardClaimer(game, detector=detector)

        # Mock sub-steps to simulate smooth successful progression
        with patch.object(claimer, "_is_vip_menu", side_effect=[True, False]), \
             patch.object(claimer, "_is_wooden_chest_collected", return_value=False), \
             patch.object(claimer, "_is_metal_chest_collected", return_value=False), \
             patch.object(claimer, "_is_chinh_phat_chest_collected", return_value=False), \
             patch.object(claimer, "_is_vip_points_popup", return_value=True), \
             patch.object(claimer, "_is_vip_chest_popup", return_value=True), \
             patch.object(claimer, "_is_chien_dich_menu", return_value=True), \
             patch.object(claimer, "_is_chinh_phat_screen", return_value=True), \
             patch.object(claimer, "_detect_xac_nhan", return_value=(400, 500, 200, 80)):

            success = claimer.claim_all()
            self.assertTrue(success)
            # Check that clicks were made for each required button:
            # 1. VIP button
            # 2. Wooden chest
            # 3. Dismiss popup 1
            # 4. Green Nhận
            # 5. Dismiss popup 2
            # 6. VIP Close X
            # 7. Menu dots
            # 8. Chiến dịch
            # 9. Chinh phạt
            # 10. Golden chest
            # 11. Xác nhận
            # 12. Back button (1/2)
            # 13. Back button (2/2)
            self.assertGreaterEqual(len(game.clicks), 10)

    def test_error_recovery_graceful_exit(self) -> None:
        """Verify unexpected errors do not crash caller and recovery is invoked."""
        game = FakeGame()
        claimer = DailyRewardClaimer(game)

        with patch.object(claimer, "claim_vip_rewards", side_effect=RuntimeError("Simulated menu error")), \
             patch.object(claimer, "_recover_to_city") as mock_recover:
            result = claimer.claim_all()
            self.assertFalse(result)
            mock_recover.assert_called_once()

    def test_controller_daily_rewards_flag_triggers_claimer(self) -> None:
        """Verify FarmingController invokes DailyRewardClaimer when daily_rewards is True."""
        mock_game = MagicMock()
        # The controller reads the captured frame, so the mock has to hand
        # back a real image instead of a placeholder.
        mock_game.screenshot.return_value = _ARTIFACTS_DIR / "test_960.png"
        mock_detector = MagicMock()
        mock_detector.locate_template.return_value = None
        mock_detector.detect.return_value = ScreenDetection(
            state=ScreenState.CITY,
            confidence=0.95,
            evidence="city map button",
        )

        controller = FarmingController(
            mock_game,
            detector=mock_detector,
            daily_rewards=True,
            switch_gmail=False,
        )

        with patch("rok_automation.automation.daily_reward.DailyRewardClaimer.claim_all") as mock_claim:
            mock_claim.return_value = True
            # Simulate _run_one_account up to the world map transition
            with patch.object(controller, "_farm_one_army", return_value=True):
                # When detector returns WORLD_MAP with 5 armies out, it completes immediately
                mock_detector.detect.side_effect = [
                    ScreenDetection(state=ScreenState.CITY, confidence=0.95, evidence="city"),
                    ScreenDetection(state=ScreenState.CITY, confidence=0.95, evidence="city"),
                    ScreenDetection(
                        state=ScreenState.WORLD_MAP,
                        confidence=0.95,
                        evidence="map",
                        armies_out=5,
                        account_name="TestAcc",
                    ),
                    ScreenDetection(
                        state=ScreenState.WORLD_MAP,
                        confidence=0.95,
                        evidence="map",
                        armies_out=5,
                        account_name="TestAcc",
                    ),
                ]
                with patch.object(controller, "_press_and_wait"):
                    context = controller._run_one_account()
                    mock_claim.assert_called_once()
                    self.assertIn(FarmState.CLAIM_DAILY_REWARD, [FarmState.CLAIM_DAILY_REWARD])

    def test_skip_already_collected_rewards(self) -> None:
        """Verify already collected rewards are skipped cleanly without attempting claim clicks."""
        game = FakeGame()
        detector = MagicMock(spec=ScreenDetector)
        detector.detect.return_value = ScreenDetection(
            state=ScreenState.CITY,
            confidence=0.95,
            evidence="city map button",
        )
        detector.locate_template.return_value = None

        claimer = DailyRewardClaimer(game, detector=detector)

        with patch.object(claimer, "_is_vip_menu", side_effect=[True, False]), \
             patch.object(claimer, "_is_wooden_chest_collected", return_value=True), \
             patch.object(claimer, "_is_metal_chest_collected", return_value=True), \
             patch.object(claimer, "_is_chinh_phat_chest_collected", return_value=True), \
             patch.object(claimer, "_is_chien_dich_menu", return_value=True), \
             patch.object(claimer, "_is_chinh_phat_screen", return_value=True), \
             patch.object(claimer, "_claim_wooden_chest") as mock_claim_wood, \
             patch.object(claimer, "_claim_metal_chest") as mock_claim_metal, \
             patch.object(claimer, "_claim_chinh_phat_chest") as mock_claim_cp:

            success = claimer.claim_all()
            self.assertTrue(success)
            # Verify claiming functions were NOT called because rewards were already collected
            mock_claim_wood.assert_not_called()
            mock_claim_metal.assert_not_called()
            mock_claim_cp.assert_not_called()

            # Navigation clicks still occurred:
            # 1. VIP button
            # 2. VIP close X
            # 3. Menu dots
            # 4. Chiến dịch
            # 5. Chinh phạt
            # 6. Back button 1
            # 7. Back button 2
            self.assertGreaterEqual(len(game.clicks), 5)
            self.assertLess(len(game.clicks), 10)

    def test_is_wooden_chest_collected_timer(self) -> None:
        """Verify countdown timer text triggers wooden chest collected status."""
        claimer = DailyRewardClaimer(FakeGame())
        dummy = np.zeros((600, 1000, 3), dtype=np.uint8)
        frame_path = _ARTIFACTS_DIR / "test_960.png"

        # With countdown timer present
        with patch("rok_automation.automation.daily_reward.read_text_detections") as mock_dets:
            mock_dets.return_value = [("09:23:34", (700, 100, 100, 30), 0.95)]
            self.assertTrue(claimer._is_wooden_chest_collected(dummy, frame_path))

        # Without timer and template not matching
        with patch("rok_automation.automation.daily_reward.read_text_detections") as mock_dets, \
             patch.object(claimer.detector, "locate_template", return_value=None):
            mock_dets.return_value = [("VIP 16", (700, 100, 100, 30), 0.95)]
            self.assertFalse(claimer._is_wooden_chest_collected(dummy, frame_path))

    def test_is_metal_chest_collected_timer_and_no_nhan(self) -> None:
        """Verify metal chest detects timer or absence of green Nhận button."""
        claimer = DailyRewardClaimer(FakeGame())
        dummy = np.zeros((600, 1000, 3), dtype=np.uint8)
        frame_path = _ARTIFACTS_DIR / "test_960.png"

        # 1. With timer present -> True
        with patch("rok_automation.automation.daily_reward.read_text_detections") as mock_dets:
            mock_dets.return_value = [("09:23:34", (700, 400, 100, 30), 0.95)]
            self.assertTrue(claimer._is_metal_chest_collected(dummy, frame_path))

        # 2. Uncollected with green Nhận detected -> False
        with patch("rok_automation.automation.daily_reward.read_text_detections") as mock_dets, \
             patch.object(claimer.detector, "locate_template", side_effect=lambda s, tpl, **kw: MagicMock() if "green_nhan" in str(tpl) else None):
            mock_dets.return_value = [("NHẬN", (750, 450, 80, 40), 0.95)]
            self.assertFalse(claimer._is_metal_chest_collected(dummy, frame_path))

    def test_is_chinh_phat_chest_collected_saturation_and_template(self) -> None:
        """Verify Chinh Phạt chest uses golden chest template and desaturation check."""
        claimer = DailyRewardClaimer(FakeGame())
        h, w = 600, 1000
        frame_path = _ARTIFACTS_DIR / "test_960.png"

        # 1. Uncollected: golden chest matches
        with patch.object(claimer.detector, "locate_template", side_effect=lambda s, tpl, **kw: MagicMock() if "golden_chest" in str(tpl) else None):
            dummy = np.zeros((h, w, 3), dtype=np.uint8)
            self.assertFalse(claimer._is_chinh_phat_chest_collected(dummy, frame_path))

        # 2. Collected: golden chest does not match, chest area is desaturated grey (sat < 45)
        with patch.object(claimer.detector, "locate_template", return_value=None):
            # Grey frame (B=100, G=100, R=100 has saturation 0)
            grey_frame = np.full((h, w, 3), 100, dtype=np.uint8)
            self.assertTrue(claimer._is_chinh_phat_chest_collected(grey_frame, frame_path))

            # Vibrant golden frame (B=0, G=215, R=255 has saturation 255)
            gold_frame = np.zeros((h, w, 3), dtype=np.uint8)
            gold_frame[:, :] = (0, 215, 255)
            self.assertFalse(claimer._is_chinh_phat_chest_collected(gold_frame, frame_path))


if __name__ == "__main__":
    unittest.main()
