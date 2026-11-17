import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from rok_automation.vision.button_positions import ButtonPositionCache
from rok_automation.automation.alliance_resource import AllianceResourceFarmer
from rok_automation.vision.screen import ScreenDetector, _POSITION_CACHE, _load_gray_template


class ButtonPositionTests(unittest.TestCase):
    def setUp(self):
        self.cache = ButtonPositionCache()
        self.frame = np.zeros((240, 400, 3), dtype=np.uint8)
        self.icon = np.random.default_rng(2).integers(0, 255, (30, 40, 3), dtype=np.uint8)
        self.frame[80:110, 200:240] = self.icon

    def test_position_is_verified_and_small_movement_updates_position(self):
        self.cache.remember(self.frame, 'button', (200, 80, 40, 30))
        self.assertEqual(self.cache.locate(self.frame, 'button'), (200, 80, 40, 30))
        moved = np.zeros_like(self.frame)
        moved[84:114, 205:245] = self.icon
        self.assertEqual(self.cache.locate(moved, 'button'), (205, 84, 40, 30))

    def test_missing_changed_or_resized_button_requires_new_scan(self):
        self.cache.remember(self.frame, 'button', (200, 80, 40, 30))
        self.assertIsNone(self.cache.locate(np.zeros_like(self.frame), 'button'))
        self.assertFalse(self.cache.entries)
        self.cache.remember(self.frame, 'button', (200, 80, 40, 30))
        self.assertIsNone(self.cache.locate(np.zeros((480, 800, 3), np.uint8), 'button'))

    def test_different_buttons_and_contexts_do_not_share_position(self):
        self.cache.remember(self.frame, ('button', 'menu'), (200, 80, 40, 30))
        self.assertIsNone(self.cache.locate(self.frame, ('button', 'other menu')))

    def test_flat_colour_patch_is_not_a_button_identity(self):
        self.frame[80:110, 200:240] = (220, 150, 5)
        self.cache.remember(self.frame, 'button', (200, 80, 40, 30))
        self.assertIsNone(self.cache.locate(self.frame, 'button'))

    def runner(self):
        runner = AllianceResourceFarmer(MagicMock(), ScreenDetector(), state_path=None, log=lambda _: None)
        runner.button_positions = self.cache
        runner.frame = self.frame
        return runner

    def test_capture_skips_ocr_until_text_is_needed_and_reads_once_per_frame(self):
        runner = self.runner()
        runner.game.screenshot.return_value = 'frame'
        with patch('rok_automation.automation.alliance_resource.load_color_frame', return_value=self.frame), \
             patch('rok_automation.automation.alliance_resource.read_text_detections', return_value=[]) as ocr:
            runner._capture()
            ocr.assert_not_called()
            self.assertEqual(runner.words, [])
            self.assertEqual(runner.words, [])
            self.assertEqual(ocr.call_count, 1)
            runner._capture()
            self.assertEqual(runner.words, [])
            self.assertEqual(ocr.call_count, 2)

    def test_static_text_reuses_position_without_ocr_and_rescans_when_stale(self):
        runner = self.runner()
        words = [('Nhận', (200, 80, 40, 30), .99)]
        with patch('rok_automation.automation.alliance_resource.read_text_detections', return_value=words) as ocr:
            self.assertEqual(runner._text('nhan'), (200, 80, 40, 30))
            runner._words = None
            self.assertEqual(runner._text('nhan'), (200, 80, 40, 30))
            self.assertEqual(ocr.call_count, 1)
            runner.frame = np.zeros_like(self.frame)
            ocr.return_value = []
            self.assertIsNone(runner._text('nhan'))
            self.assertEqual(ocr.call_count, 2)

    def test_preset_reuse_avoids_multiscale_scan_and_rejects_preset_two(self):
        fixture = Path(__file__).parent / 'fixtures' / 'alliance_deployment_inset.png'
        runner = self.runner()
        runner.frame = cv2.imread(str(fixture))
        first = runner._match_preset_icon()
        self.assertIsNotNone(first)
        with patch('rok_automation.automation.alliance_resource.cv2.imread', side_effect=AssertionError('unnecessary rescan')):
            self.assertEqual(runner._match_preset_icon(), first)
        x, y, w, h = first
        two = cv2.imread(str(Path(__file__).resolve().parents[1] / 'templates' / 'alliance' / 'preset_2_button.png'))
        runner.frame[y:y+h, x:x+w] = cv2.resize(two, (w, h), interpolation=cv2.INTER_AREA)
        self.assertIsNone(self.cache.locate(runner.frame, 'troop_preset_1'))

    def test_normal_farming_checks_exact_position_without_neighbourhood_scan(self):
        template = Path(__file__).resolve().parents[1] / 'templates' / 'gathering' / 'gather_button_alliance.png'
        frame = cv2.imread(str(Path(__file__).parent / 'fixtures' / 'alliance_gather_popup.png'), cv2.IMREAD_GRAYSCALE)
        _load_gray_template(template)
        key = (720, 1280, template.name, None)
        previous = _POSITION_CACHE.get(key)
        _POSITION_CACHE[key] = (853, 446, 205, 77, 100)
        try:
            with patch.object(ScreenDetector, '_match_at_scale', wraps=ScreenDetector._match_at_scale) as match:
                hit = ScreenDetector._locate_in_frame(frame, template, confidence_threshold=.78)
            self.assertEqual(hit.bounds, (853, 446, 205, 77))
            self.assertEqual(match.call_count, 1)
            self.assertEqual(match.call_args.args[0].shape, (77, 205))
        finally:
            if previous is None:
                _POSITION_CACHE.pop(key, None)
            else:
                _POSITION_CACHE[key] = previous
