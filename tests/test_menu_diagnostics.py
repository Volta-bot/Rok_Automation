import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from rok_automation.vision.screen import ScreenDetector, ScreenState, TemplateHit


class MenuDiagnosticTests(unittest.TestCase):
    def test_real_vip_menu(self):
        path = Path(__file__).parent / "fixtures/vip_menu_collected.png"
        result = ScreenDetector().detect(path, include_menus=True)
        self.assertEqual(result.state, ScreenState.VIP)

    def test_menu_titles_and_unrelated_text(self):
        cases = (("VIP", ScreenState.VIP),
                 ("Chiến dịch", ScreenState.CHIEN_DICH),
                 ("Chinh phạt", ScreenState.CHINH_PHAT),
                 ("Chinh pht", ScreenState.CHINH_PHAT),
                 ("VIP 10", None), ("Miễn phí", None), ("phạt", None))
        frame = np.zeros((720, 1280, 3), np.uint8)
        for text, expected in cases:
            with self.subTest(text=text), \
                 patch("rok_automation.vision.screen.load_color_frame", return_value=frame), \
                 patch("rok_automation.vision.screen.read_text_detections",
                       return_value=[(text, (550, 20, 180, 40), 0.98)]) as ocr, \
                 patch.object(ScreenDetector, "locate_template", return_value=None):
                result = ScreenDetector()._detect_reward_menu(Path("frame"))
                self.assertEqual(result.state if result else None, expected)
                self.assertEqual(ocr.call_args.args[1], (384, 0, 512, 129))

    def test_campaign_card_means_campaign_selection_not_conquest_map(self):
        with patch("rok_automation.vision.screen.load_color_frame",
                   return_value=np.zeros((720, 1280, 3), np.uint8)), \
             patch("rok_automation.vision.screen.read_text_detections", return_value=[]), \
             patch.object(ScreenDetector, "locate_template",
                          return_value=TemplateHit(0.95, (25, 80, 250, 300))):
            result = ScreenDetector()._detect_reward_menu(Path("frame"))
        self.assertEqual(result.state, ScreenState.CHIEN_DICH)

    def test_farming_detection_does_not_enable_diagnostic_menu_states(self):
        path = Path(__file__).parent / "fixtures/vip_menu_collected.png"
        with patch.object(ScreenDetector, "_detect_reward_menu") as menu:
            ScreenDetector().detect(path, read_account=False)
        menu.assert_not_called()
