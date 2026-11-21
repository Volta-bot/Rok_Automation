"""Tests for the daily reward collection feature (VIP & Campaign)."""

from __future__ import annotations

import unittest
from itertools import count
from pathlib import Path
from unittest.mock import MagicMock, call, patch

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
from rok_automation.vision.screen import ScreenDetection, ScreenDetector, ScreenState, TemplateHit

_TEST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent
_ARTIFACTS_DIR = _PROJECT_ROOT / "artifacts"


class FakeGame:
    """Mock GameInterface that logs clicks and returns test frames."""

    def __init__(self) -> None:
        self.timing = TimingConfig()
        self.clicks: list[tuple[int, int, int, int]] = []
        self.taps: list[tuple[int, int]] = []
        self.waits: list[float] = []
        self._default_frame = _ARTIFACTS_DIR / "test_960.png"

    def screenshot(self) -> Path:
        return self._default_frame

    def click(self, bounds: tuple[int, int, int, int], screenshot_path: str | Path) -> None:
        self.clicks.append(bounds)

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))

    def drag(self, start: tuple[int, int], end: tuple[int, int], screenshot_path: str | Path) -> None:
        pass

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)


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

    def test_vip_target_ignores_cached_portrait_bounds(self) -> None:
        screenshot = _TEST_DIR / "fixtures/reward_city_menu_open.png"
        frame = cv2.imread(str(screenshot))
        size = (1280, 720)
        claimer = DailyRewardClaimer(FakeGame())
        claimer._pin_bounds(size, "vip_button", (5, 3, 80, 87))
        x, y, w, h = claimer._locate_vip_button(screenshot, frame, size)
        self.assertTrue(95 < x + w / 2 < 155 and 45 < y + h / 2 < 80)

    def test_vip_reopens_after_accidentally_entering_profile(self) -> None:
        game = MagicMock()
        detector = MagicMock(spec=ScreenDetector)
        claimer = DailyRewardClaimer(game, detector, log=lambda _: None)
        size = (1280, 720)
        vip_button = (100, 42, 30, 40)
        close_button = (1070, 64, 34, 34)
        state = "city"
        clicks = []

        def click(bounds, shot):
            nonlocal state
            clicks.append((shot.name, bounds))
            state = {("city", 1): "profile", ("profile", 2): "city", ("city", 3): "vip"}[
                shot.name, len(clicks)
            ]

        def locate(shot, template, **kwargs):
            if shot.name == "city" and template.name == "vip_shield.png":
                return TemplateHit(.97, vip_button)
            if shot.name == "profile" and template.name == "settings_button.png":
                return TemplateHit(.99, (1000, 520, 100, 80))
            if shot.name == "profile" and template.name == "vip_close_x.png":
                return TemplateHit(.96, close_button)
            return None

        game.click.side_effect = click
        detector.locate_template.side_effect = locate
        states = {"city": ScreenState.CITY, "vip": ScreenState.VIP, "profile": ScreenState.UNKNOWN}
        detector.detect.side_effect = lambda shot, **_: ScreenDetection(states[shot.name], 1, shot.name)
        with patch.object(claimer, "_capture", side_effect=lambda: (Path(state), object(), size)), \
             patch.object(claimer, "_claim_vip_chests", return_value=True) as claim, \
             patch.object(claimer, "_close_vip_menu"), \
             patch("rok_automation.automation.daily_reward.time.monotonic", side_effect=count(step=10)):
            self.assertTrue(claimer.claim_vip_rewards())
        self.assertEqual(clicks, [
            ("city", vip_button), ("profile", close_button), ("city", vip_button),
        ])
        claim.assert_called_once()

    def test_vip_recovers_from_unexpected_menus_before_each_retry(self) -> None:
        game = MagicMock()
        detector = MagicMock(spec=ScreenDetector)
        claimer = DailyRewardClaimer(game, detector, log=lambda _: None)
        size = (1280, 720)
        frame = np.zeros((720, 1280, 3), np.uint8)
        badge = (100, 42, 30, 40)
        back = (15, 12, 56, 56)
        state = "city"
        badge_taps = 0
        clicks = []

        def click(bounds, screenshot):
            nonlocal state, badge_taps
            self.assertEqual(screenshot, Path(state))
            clicks.append((state, bounds))
            if state == "city":
                badge_taps += 1
                state = "vip" if badge_taps == 3 else "event"
            elif state == "event":
                state = "city"

        def close_event(_game, _detector, screenshot, *, log):
            if screenshot != Path("event"):
                return False
            game.click(back, screenshot)
            return True

        game.click.side_effect = click
        detector.locate_template.return_value = None
        states = {"city": ScreenState.CITY, "vip": ScreenState.VIP, "event": ScreenState.UNKNOWN}
        detector.detect.side_effect = lambda shot, **_: ScreenDetection(states[shot.name], 1, shot.name)
        with patch.object(claimer, "_capture", side_effect=lambda: (Path(state), frame, size)), \
             patch.object(claimer, "_locate_vip_button", return_value=badge), \
             patch.object(claimer, "_detect_xac_nhan", return_value=None), \
             patch.object(claimer, "_is_vip_points_popup", return_value=False), \
             patch.object(claimer, "_is_vip_chest_popup", return_value=False), \
             patch.object(claimer, "_claim_vip_chests", return_value=True) as claim, \
             patch.object(claimer, "_close_vip_menu"), \
             patch("rok_automation.automation.daily_reward.back_out_of_unknown_menu", side_effect=close_event), \
             patch("rok_automation.automation.daily_reward.time.monotonic", side_effect=count(step=10)):
            self.assertTrue(claimer.claim_vip_rewards())
        self.assertEqual(clicks, [
            ("city", badge), ("event", back), ("city", badge), ("event", back), ("city", badge),
        ])
        claim.assert_called_once()

    def test_bottom_menu_remains_open_if_campaign_is_delayed_or_hidden(self) -> None:
        screenshot = _TEST_DIR / "fixtures/reward_city_menu_open.png"
        frame = cv2.imread(str(screenshot))
        size = (1280, 720)
        for delayed in (True, False):
            with self.subTest(delayed=delayed):
                game = FakeGame()
                claimer = DailyRewardClaimer(game, log=lambda _: None)
                bounds = claimer._find_chien_dich(screenshot, frame, size)
                self.assertIsNotNone(bounds)
                readings = [None, bounds] if delayed else [None] * 6
                with patch.object(claimer, "_capture", return_value=(screenshot, frame, size)), \
                     patch.object(claimer, "_find_chien_dich", side_effect=readings):
                    if delayed:
                        claimer._open_bottom_menu_if_needed()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "Bottom menu is already open"):
                            claimer._open_bottom_menu_if_needed()
                self.assertEqual(game.clicks, [])

    def test_closed_bottom_menu_is_toggled_once_then_campaign_is_seen(self) -> None:
        opened = _TEST_DIR / "fixtures/reward_city_menu_open.png"
        size = (1280, 720)
        for filename in ("forge_bone_stacked.png", "reward_city_menu_closed_badge.png"):
            with self.subTest(frame=filename):
                closed = _TEST_DIR / "fixtures" / filename
                game = FakeGame()
                claimer = DailyRewardClaimer(game, log=lambda _: None)
                frames = [(closed, cv2.imread(str(closed)), size)] * 6
                frames.append((opened, cv2.imread(str(opened)), size))
                with patch.object(claimer, "_capture", side_effect=frames):
                    claimer._open_bottom_menu_if_needed()
                self.assertEqual(len(game.clicks), 1)
                x, y, w, h = game.clicks[0]
                self.assertTrue(1205 < x + w / 2 < 1255 and 648 < y + h / 2 < 690)

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

    def test_collected_vip_menu_is_not_a_reward_popup_and_x_is_inside_header(self) -> None:
        path = _TEST_DIR / "fixtures/vip_menu_collected.png"
        frame = cv2.imread(str(path))
        self.assertIsNotNone(frame)
        claimer = DailyRewardClaimer(FakeGame())
        self.assertFalse(claimer._is_vip_points_popup(frame))
        self.assertFalse(claimer._is_vip_chest_popup(frame))
        self.assertEqual(claimer.detector.detect(path).state, ScreenState.VIP)
        # A stale coordinate from a previous failed attempt must be ignored.
        claimer._pin_bounds((1280, 720), "vip_close_x", (1203, 11, 58, 40))
        x, y, w, h = claimer._locate_vip_close_x(path, (1280, 720))
        self.assertLess(abs(x + w / 2 - 1092), 15)
        self.assertLess(abs(y + h / 2 - 82), 15)

    def test_open_vip_reward_overlays_are_verified_without_claiming_again(self) -> None:
        fixtures = _TEST_DIR / "fixtures"
        vip = fixtures / "vip_menu_collected.png"
        city = fixtures / "forge_bone_stacked.png"
        detector = ScreenDetector()
        for chest, expected_state in (
            ("wooden", ScreenState.VIP_WOODEN_CHEST_REWARD),
            ("metal", ScreenState.VIP_METAL_CHEST_REWARD),
        ):
            with self.subTest(chest=chest):
                popup = fixtures / f"vip_{chest}_reward.png"
                detection = detector.detect(popup)
                self.assertIs(detection.state, expected_state)
                self.assertIs(detector.detect(popup, candidate_states={ScreenState.CITY}).state, ScreenState.UNKNOWN)

                game = MagicMock(timing=TimingConfig())
                claimer = DailyRewardClaimer(game, detector, log=lambda _: None)
                # Simulate the first dismissal being ignored by the game.
                game.screenshot.side_effect = [popup, popup, vip, city]
                with patch.object(claimer, "_is_wooden_chest_collected", return_value=chest != "wooden"), \
                     patch.object(claimer, "_is_metal_chest_collected", return_value=chest != "metal"):
                    self.assertTrue(claimer.claim_vip_rewards())
                self.assertEqual(game.click.call_count, 3)
                self.assertEqual(game.click.call_args_list[:2], [call(detection.bounds, popup)] * 2)
                self.assertEqual(game.click.call_args_list[2].args[1], vip)
                for index, action in enumerate(game.method_calls):
                    if action[0] == "click":
                        self.assertEqual(game.method_calls[index + 1], call.wait(0.45))

    def test_vip_daily_points_label_is_not_a_popup_title(self) -> None:
        claimer = DailyRewardClaimer(FakeGame())
        frame = np.zeros((720, 1280, 3), np.uint8)
        for label in ("Điểm hôm nay: 200", "Miễn phí mỗi ngày", "Phần thưởng hàng ngày"):
            with self.subTest(label=label), patch(
                "rok_automation.automation.daily_reward.read_text_detections",
                return_value=[(label, (300, 100, 200, 50), 0.95)],
            ):
                self.assertFalse(claimer._is_vip_points_popup(frame))

    def test_recovery_on_real_vip_frame_clicks_x_then_confirms_city(self) -> None:
        path = _TEST_DIR / "fixtures/vip_menu_collected.png"
        frame = cv2.imread(str(path))
        game = FakeGame()
        detector = ScreenDetector()
        claimer = DailyRewardClaimer(game, detector, log=lambda _: None)
        with patch.object(claimer, "_capture", return_value=(path, frame, (1280, 720))), \
             patch.object(detector, "detect", side_effect=[
                 ScreenDetection(ScreenState.VIP, 1, "VIP"),
                 ScreenDetection(ScreenState.CITY, 1, "city"),
             ]):
            self.assertTrue(claimer.ensure_city_view())
        self.assertEqual(len(game.clicks), 1)
        x, y, w, h = game.clicks[0]
        self.assertLess(abs(x + w / 2 - 1092), 15)
        self.assertLess(abs(y + h / 2 - 82), 15)

    def test_world_to_city_taps_fortress_first_and_falls_back_to_map(self) -> None:
        screenshot = Path("frame.png")
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "search")
        city = ScreenDetection(ScreenState.CITY, 0.95, "build")
        fortress = TemplateHit(0.85, (10, 600, 90, 90))
        map_button = TemplateHit(0.98, (14, 604, 90, 90))
        for hits, expected in (([fortress, map_button], fortress), ([None, map_button], map_button)):
            with self.subTest(expected=expected):
                game = FakeGame()
                detector = MagicMock(spec=ScreenDetector)
                detector.detect.side_effect = [world, city]
                detector.locate_template.side_effect = hits
                claimer = DailyRewardClaimer(game, detector, log=lambda _: None)
                with patch.object(claimer, "_capture", return_value=(screenshot, frame, (1280, 720))):
                    self.assertTrue(claimer.ensure_city_view())
                self.assertEqual(game.clicks, [expected.bounds])

    def test_vip_taps_both_pairs_before_rescanning_and_resumes_from_either_popup(self) -> None:
        size = (1280, 720)
        vip = (100, 40, 80, 35)
        wooden = (960, 185, 120, 36)
        metal = (880, 315, 118, 40)
        banner = (450, 120, 380, 55)
        close = (1076, 68, 30, 30)
        screens = {
            "city": ScreenState.CITY, "vip": ScreenState.VIP, "collected": ScreenState.VIP,
            "wood_popup": ScreenState.VIP_WOODEN_CHEST_REWARD,
            "metal_popup": ScreenState.VIP_METAL_CHEST_REWARD,
        }
        for interruption, recovery_taps, recovery_delays in (
            (None, [], []),
            ("wood_popup", [banner, metal, metal], [0.45, 2.0, 0.45]),
            ("metal_popup", [banner], [0.45]),
        ):
            with self.subTest(interruption=interruption):
                game = MagicMock()
                detector = MagicMock(spec=ScreenDetector)
                claimer = DailyRewardClaimer(game, detector, log=lambda _: None)
                frames = ["city", "vip", *([interruption] if interruption else []), "collected", "city"]
                captures = iter(frames)
                events = []

                def capture():
                    frame = next(captures)
                    events.append(frame)
                    return Path(frame), frame, size

                game.click.side_effect = lambda bounds, _: events.append(bounds)
                detector.detect.side_effect = lambda shot, **_: ScreenDetection(
                    screens[shot.name], 1, shot.name, banner,
                )
                with patch.object(claimer, "_capture", side_effect=capture), \
                     patch.object(claimer, "_locate_vip_button", return_value=vip), \
                     patch.object(claimer, "_locate_wooden_nhan", return_value=wooden), \
                     patch.object(claimer, "_locate_green_nhan", return_value=metal), \
                     patch.object(claimer, "_locate_vip_close_x", return_value=close), \
                     patch.object(claimer, "_is_wooden_chest_collected", side_effect=lambda frame, _: frame == "collected"), \
                     patch.object(claimer, "_is_metal_chest_collected", side_effect=lambda frame, _: frame == "collected"):
                    self.assertTrue(claimer.claim_vip_rewards())
                recovery = [interruption, *recovery_taps] if interruption else []
                self.assertEqual(events, [
                    "city", vip, "vip", wooden, wooden, metal, metal, *recovery, "collected", close, "city",
                ])
                self.assertEqual(detector.detect.call_count, len(frames))
                delays = [0.45, 2.0, 0.45, 2.0, 0.45, *recovery_delays, 0.45]
                self.assertEqual(game.wait.call_args_list, [call(delay) for delay in delays])

    def test_already_claimed_vip_chests_are_skipped_on_entry(self) -> None:
        fixtures = _TEST_DIR / "fixtures"
        vip = fixtures / "vip_menu_collected.png"
        city = fixtures / "forge_bone_stacked.png"
        game = MagicMock(timing=TimingConfig())
        game.screenshot.side_effect = [vip, city]
        claimer = DailyRewardClaimer(game, log=lambda _: None)
        with patch.object(claimer, "_locate_wooden_nhan") as wooden, \
             patch.object(claimer, "_locate_green_nhan") as metal:
            self.assertTrue(claimer.claim_vip_rewards())
        wooden.assert_not_called()
        metal.assert_not_called()
        self.assertEqual(game.screenshot.call_count, 2)
        game.click.assert_called_once()
        game.wait.assert_called_once_with(0.45)

    def test_campaign_retries_chest_in_place_and_waits_for_confirm_popup(self) -> None:
        game = MagicMock()
        detector = MagicMock(spec=ScreenDetector)
        claimer = DailyRewardClaimer(game, detector, log=lambda _: None)
        size = (1280, 720)
        campaign = (700, 610, 60, 70)
        conquest = (80, 150, 200, 240)
        chest = (70, 170, 100, 80)
        stale = (220, 150, 80, 50)
        confirm = (530, 570, 220, 70)
        claimer._pin_bounds(size, "golden_chest", stale)
        screen = "city"
        pending_scans = 0
        clicks = []

        def capture():
            nonlocal screen, pending_scans
            if screen == "loading_reward":
                pending_scans += 1
                if pending_scans == 6:
                    screen = "reward_popup"
            return Path(screen), screen, size

        def click(bounds, shot):
            nonlocal screen
            self.assertEqual(shot, Path(screen))
            clicks.append(bounds)
            screen = {
                ("city", campaign): "campaign",
                ("campaign", conquest): "conquest",
                ("conquest", stale): "conquest",
                ("conquest", chest): "loading_reward",
                ("reward_popup", confirm): "conquest",
            }[(screen, bounds)]

        game.click.side_effect = click
        detector.locate_template.return_value = TemplateHit(0.95, chest)
        menu_states = {
            "city": ScreenState.CITY, "campaign": ScreenState.CHIEN_DICH,
            "conquest": ScreenState.CHINH_PHAT,
        }
        detector.detect.side_effect = lambda shot, **_options: ScreenDetection(
            menu_states.get(shot.name, ScreenState.UNKNOWN), 1, shot.name,
        )
        with patch.object(claimer, "_capture", side_effect=capture), \
             patch.object(claimer, "_find_chien_dich", side_effect=lambda _s, f, _d: campaign if f == "city" else None), \
             patch.object(claimer, "_locate_chinh_phat", return_value=conquest), \
             patch.object(claimer, "_is_chinh_phat_chest_collected", return_value=False), \
             patch.object(claimer, "_detect_xac_nhan", side_effect=lambda _s, f, _d: confirm if f == "reward_popup" else None), \
             patch.object(claimer, "_close_campaign_menus") as close_menu, \
             patch("rok_automation.automation.daily_reward.time.monotonic", side_effect=count(step=10)), \
             patch("rok_automation.game.popups.close_mailbox", return_value=False):
            self.assertTrue(claimer.claim_campaign_rewards())
        self.assertEqual(clicks, [campaign, conquest, stale, chest, confirm])
        self.assertEqual(pending_scans, 6)
        self.assertEqual(_PINNED_COORDINATES[(*size, "golden_chest")], chest)
        close_menu.assert_called_once_with()
        for index, action in enumerate(game.method_calls):
            if action[0] == "click":
                delay = 1.0 if action.args[0] in (stale, chest) else 0.45
                self.assertEqual(game.method_calls[index + 1], call.wait(delay))

    def test_campaign_back_taps_twice_before_scanning_for_city(self) -> None:
        for fallback in (False, True):
            with self.subTest(second_tap_missed=fallback):
                game = MagicMock()
                detector = MagicMock(spec=ScreenDetector)
                states = [ScreenState.CHINH_PHAT, ScreenState.CITY]
                if fallback:
                    states.insert(1, ScreenState.CHIEN_DICH)
                frames = iter(Path(state.value) for state in states)
                detector.detect.side_effect = [ScreenDetection(state, 1, state.value) for state in states]
                claimer = DailyRewardClaimer(game, detector, log=lambda _: None)
                tap_counts = []

                def capture():
                    tap_counts.append(game.tap.call_count)
                    return next(frames), object(), (1280, 720)

                with patch.object(claimer, "_capture", side_effect=capture), \
                     patch.object(claimer, "_detect_xac_nhan", return_value=None), \
                     patch.object(claimer, "_locate_back_button", return_value=(20, 15, 40, 50)):
                    claimer._close_campaign_menus()

                self.assertEqual(tap_counts, [0, 2, 3] if fallback else [0, 2])
                self.assertEqual(game.tap.call_args_list, [call(40, 40)] * (3 if fallback else 2))
                self.assertEqual(game.wait.call_args_list, [call(0.45)] * (3 if fallback else 2))
                self.assertEqual(detector.detect.call_args_list, [
                    call(Path(state.value), read_account=False, resource_details=frozenset()) for state in states
                ])

    def test_error_recovery_graceful_exit(self) -> None:
        """Verify unexpected errors do not crash caller and recovery is invoked."""
        game = FakeGame()
        claimer = DailyRewardClaimer(game)

        with patch.object(claimer, "claim_vip_rewards", side_effect=RuntimeError("Simulated menu error")) as vip, \
             patch.object(claimer, "claim_campaign_rewards", return_value=True) as campaign, \
             patch.object(claimer, "_recover_to_city") as mock_recover:
            result = claimer.claim_all()
            self.assertFalse(result)
            self.assertEqual(vip.call_count, 2)
            campaign.assert_called_once_with(initial_capture=None)
            mock_recover.assert_called_once()

    def test_reward_recovery_resumes_open_conquest_before_vip(self) -> None:
        claimer = DailyRewardClaimer(FakeGame(), MagicMock(spec=ScreenDetector), log=lambda _: None)
        claimer.detector.detect.return_value = ScreenDetection(ScreenState.CHINH_PHAT, 1, "conquest")
        with patch.object(claimer, "_open_bottom_menu_if_needed") as bottom, \
             patch.object(claimer, "_open_chien_dich_menu") as campaign, \
             patch.object(claimer, "_open_chinh_phat_screen") as conquest, \
             patch.object(claimer, "_claim_visible_reward", side_effect=[False, True]) as claim, \
             patch.object(claimer, "_close_campaign_menus") as close, \
             patch.object(claimer, "claim_vip_rewards", return_value=True) as vip:
            steps = MagicMock()
            steps.attach_mock(claim, "claim")
            steps.attach_mock(vip, "vip")
            self.assertTrue(claimer.claim_all())
        self.assertEqual([step[0] for step in steps.mock_calls], ["claim", "claim", "vip"])
        bottom.assert_not_called()
        campaign.assert_not_called()
        conquest.assert_not_called()
        close.assert_called_once_with()

    def test_campaign_retries_back_until_city_is_detected(self) -> None:
        game = MagicMock()
        detector = MagicMock()
        detector.detect.side_effect = [
            ScreenDetection(ScreenState.UNKNOWN, 0, "menu"),
            ScreenDetection(ScreenState.UNKNOWN, 0, "missed press"),
            ScreenDetection(ScreenState.UNKNOWN, 0, "another menu"),
            ScreenDetection(ScreenState.CITY, 1, "city"),
        ]
        claimer = DailyRewardClaimer(game, detector, log=lambda _: None)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        with patch.object(claimer, "_capture", return_value=(Path("frame.png"), frame, (1280, 720))), \
             patch.object(claimer, "_detect_xac_nhan", return_value=None), \
             patch.object(claimer, "_is_vip_points_popup", return_value=False), \
             patch.object(claimer, "_is_vip_chest_popup", return_value=False), \
             patch("rok_automation.game.popups.close_scout_manager", return_value=False):
            claimer._close_campaign_menus()
        self.assertEqual(game.click.call_count, 3)
        self.assertEqual(detector.detect.call_count, 4)

    def test_vip_close_retries_x_and_fails_if_city_never_appears(self) -> None:
        game = MagicMock()
        detector = MagicMock()
        detector.detect.return_value = ScreenDetection(ScreenState.VIP, 1, "vip")
        claimer = DailyRewardClaimer(game, detector, log=lambda _: None)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        with patch.object(claimer, "_capture", return_value=(Path("frame.png"), frame, (1280, 720))), \
             patch.object(claimer, "_locate_vip_close_x", return_value=(1, 2, 3, 4)), \
             patch.object(claimer, "_detect_xac_nhan", return_value=None), \
             patch.object(claimer, "_is_vip_points_popup", return_value=False), \
             patch.object(claimer, "_is_vip_chest_popup", return_value=False), \
             patch("rok_automation.game.popups.close_scout_manager", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "VIP menu"):
                claimer._close_vip_menu()
        self.assertEqual(game.click.call_count, 5)
        self.assertEqual(detector.detect.call_count, 6)

    @patch("rok_automation.automation.controller.city_resources_enabled", return_value=False)
    @patch("rok_automation.automation.controller.forge_material_to_produce", return_value=None)
    def test_controller_daily_rewards_flag_triggers_claimer(self, _forge, _resources) -> None:
        """Verify FarmingController invokes DailyRewardClaimer when daily_rewards is True."""
        mock_game = MagicMock()
        events = []
        # The controller reads the captured frame, so the mock has to hand
        # back a real image instead of a placeholder.
        mock_game.screenshot.side_effect = lambda: events.append("capture") or _ARTIFACTS_DIR / "test_960.png"
        mock_game.wait.side_effect = lambda delay: events.append(("wait", delay))
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
            mock_claim.side_effect = lambda: events.append("claimed rewards") or True
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
                with patch.object(controller, "_press_and_wait", return_value=None) as press:
                    context = controller._run_one_account()
                    mock_claim.assert_called_once()
                    self.assertEqual(events[:3], ["claimed rewards", ("wait", 0.60), "capture"])
                    self.assertEqual(press.call_count, 2)
                    self.assertEqual(context.current_state, FarmState.COMPLETE)

    def test_skips_campaign_reward_already_collected(self) -> None:
        game = FakeGame()
        detector = MagicMock(spec=ScreenDetector)
        detector.detect.return_value = ScreenDetection(
            state=ScreenState.CITY,
            confidence=0.95,
            evidence="city map button",
        )
        detector.locate_template.return_value = None

        claimer = DailyRewardClaimer(game, detector=detector)

        with patch.object(claimer, "_is_screen", return_value=True), \
             patch.object(claimer, "_open_reward_menu", return_value=True), \
             patch.object(claimer, "_is_chinh_phat_chest_collected", return_value=True), \
             patch.object(claimer, "_campaign_button_visible", return_value=True), \
             patch.object(claimer, "_confirm_campaign_popup", return_value=False), \
             patch.object(claimer, "_locate_golden_chest") as golden:
            self.assertTrue(claimer.claim_campaign_rewards())
        golden.assert_not_called()

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

    def test_is_metal_chest_collected_requires_positive_evidence(self) -> None:
        claimer = DailyRewardClaimer(FakeGame())
        dummy = np.zeros((600, 1000, 3), dtype=np.uint8)
        frame_path = _ARTIFACTS_DIR / "test_960.png"

        # 1. With timer present -> True
        with patch("rok_automation.automation.daily_reward.read_text_detections") as mock_dets:
            mock_dets.return_value = [("09:23:34", (700, 400, 100, 30), 0.95)]
            self.assertTrue(claimer._is_metal_chest_collected(dummy, frame_path))

        with patch("rok_automation.automation.daily_reward.read_text_detections") as mock_dets, \
             patch.object(claimer.detector, "locate_template", return_value=None):
            mock_dets.return_value = [("NHẬN", (750, 450, 80, 40), 0.95)]
            self.assertFalse(claimer._is_metal_chest_collected(dummy, frame_path))
            mock_dets.return_value = []
            self.assertFalse(claimer._is_metal_chest_collected(dummy, frame_path))

    def test_is_chinh_phat_chest_collected_requires_open_chest(self) -> None:
        claimer = DailyRewardClaimer(FakeGame())
        h, w = 600, 1000
        frame_path = _ARTIFACTS_DIR / "test_960.png"

        dummy = np.full((h, w, 3), 100, dtype=np.uint8)
        with patch.object(claimer.detector, "locate_template", return_value=MagicMock()):
            self.assertFalse(claimer._is_chinh_phat_chest_collected(dummy, frame_path))
        with patch.object(claimer.detector, "locate_template", side_effect=[None, MagicMock()]):
            self.assertTrue(claimer._is_chinh_phat_chest_collected(dummy, frame_path))
        with patch.object(claimer.detector, "locate_template", return_value=None):
            self.assertFalse(claimer._is_chinh_phat_chest_collected(dummy, frame_path))


if __name__ == "__main__":
    unittest.main()
