import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import cv2

from rok_automation.automation.alliance_resource import (
    AllianceResourceFarmer, MIN_REMAINING, preset_error, remaining_resources,
)
from rok_automation.automation.controller import FarmingController
from rok_automation.logic.resource_selector import Resource
from rok_automation.vision.screen import ScreenDetection, ScreenState


class AllianceResourceTests(unittest.TestCase):
    def runner(self, state_path=None):
        runner = AllianceResourceFarmer(MagicMock(), MagicMock(), log=lambda _: None, state_path=state_path)
        runner.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        runner.shot = 'frame'
        runner.words = []
        return runner

    def test_remaining_amount_requires_label_and_strict_threshold(self):
        for text, expected in [('Còn: 66.397.797', 66397797), ('Còn: 50,000,000', MIN_REMAINING),
                               ('Kho Dự Trữ 66.351.255', 66351255), ('Lính thu gom: 12', None),
                               ('Còn: unreadable', None)]:
            self.assertEqual(remaining_resources(text), expected)

    def test_error_text_including_actual_screenshot_ocr(self):
        self.assertTrue(preset_error('Không thể tải trang bị này; chỉ huy chính không ở trong thành phố'))
        self.assertTrue(preset_error('Không th ti thanh trang bnày; chi huy chính không trong thành phố.'))
        self.assertFalse(preset_error('Quân mới Hành Quân'))

    def test_navigation_accepts_actual_screenshot_ocr_labels(self):
        runner = self.runner()
        runner.words = [('Lãnh th', (760, 464, 86, 27), 1)]
        self.assertIsNotNone(runner._text('lanh tho', 'lanh th', area=(.4, .45, .85, .75)))
        runner.words = [('Chm đ gia nhp', (243, 354, 192, 31), 1)]
        self.assertIsNotNone(runner._text('cham de gia nhap', 'chm d gia nhp'))

    def test_runtime_flag_can_enable_and_disable_feature(self):
        from rok_automation.game.flags import alliance_resource_enabled, set_alliance_resource

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'alliance_resource.flag'
            self.assertFalse(alliance_resource_enabled(path))
            set_alliance_resource(True, path)
            self.assertTrue(alliance_resource_enabled(path))
            set_alliance_resource(False, path)
            self.assertFalse(alliance_resource_enabled(path))

    def test_alliance_tag_must_match_current_account_not_chat_or_other_city(self):
        runner = self.runner()
        runner.words = [('[CHAT]Other', (180, 650, 200, 20), .99),
                        ('[WRONG]Other', (520, 400, 200, 20), .99),
                        ('[93DE]Vàng 6 mỏLM', (540, 420, 200, 20), .95)]
        self.assertEqual(runner._tag('Vàng 6 mỏLM'), '93DE')
        self.assertIsNone(runner._tag('unreadable'))

    def test_card_resources_are_associated_with_their_coordinates(self):
        runner = self.runner()
        runner.words = [('X:893 Y:773', (190, 490, 100, 20), .99),
                        ('Độ bền: 100%', (170, 540, 140, 20), .99),
                        ('X:894 Y:768', (710, 550, 100, 20), .99),
                        ('Đang thu gom', (710, 570, 100, 20), .99),
                        ('Còn: 66.397.797', (690, 598, 140, 20), .99)]
        node, bounds = runner._node()
        self.assertEqual(node['remaining'], 66397797)
        self.assertEqual(node['coordinates'], [894, 768])
        runner.words[3] = ('Xây dựng', (710, 570, 100, 20), .99)
        self.assertTrue(runner._node()[0]['building'])

    def setup_flow(self, runner, *, remaining=66397797, building=False):
        runner._capture = MagicMock()
        runner._tag = MagicMock(return_value='93DE')
        runner._press_text = MagicMock(return_value=True)
        runner._press_once = MagicMock(return_value=True)
        runner._text = MagicMock(return_value=(10, 10, 20, 20))
        runner._node = MagicMock(return_value=({'coordinates': [894, 768], 'remaining': remaining,
                                               'building': building}, (10, 10, 20, 20)))
        runner._template = MagicMock(return_value=(1200, 250, 30, 30))
        runner._match_preset_icon = MagicMock(return_value=(1200, 250, 30, 30))
        runner._close_to_world = MagicMock()
        runner._click = MagicMock()
        runner.detector.detect.return_value = ScreenDetection(ScreenState.WORLD_MAP, 1, 'map')
        runner._verified_action = MagicMock(return_value=True)
        runner._load_preset = MagicMock(side_effect=lambda: not preset_error(' '.join(t for t, _, _ in runner.words)))
        runner._world_visible = lambda: runner.detector.detect.return_value.state is ScreenState.WORLD_MAP

    def test_threshold_construction_and_error_never_march_and_always_close(self):
        for remaining, building, error in [(50000000, False, False), (49999999, False, False),
                                            (66000000, True, False), (66000000, False, True)]:
            runner = self.runner()
            self.setup_flow(runner, remaining=remaining, building=building)
            if error:
                runner.words = [('Không thể tải trang bị này', (0, 0, 100, 20), 1)]
            self.assertEqual(runner.run('name'), 0)
            self.assertFalse(runner.march_attempted)
            runner._close_to_world.assert_called_once()

    def test_exactly_one_march_attempt_even_when_confirmation_times_out(self):
        for confirmed in (True, False):
            runner = self.runner()
            self.setup_flow(runner)
            if not confirmed:
                runner.detector.detect.return_value = ScreenDetection(ScreenState.UNKNOWN, 0, 'slow')
            self.assertEqual(runner.run('name'), int(confirmed))
            self.assertTrue(runner.march_attempted)
            # Navigation/preset verification is mocked here; final dispatch is not.
            self.assertEqual(runner._click.call_count, 1)

    def test_disk_cache_is_reused_only_after_verifying_current_alliance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'alliance_nodes.json'
            runner = self.runner(path)
            runner._save('93DE', {'coordinates': [894, 768], 'remaining': 49000000, 'building': False})
            restored = self.runner(path)
            self.setup_flow(restored)
            self.assertEqual(restored.run('another account'), 0)
            restored._tag.assert_called_once_with('another account')
            restored._press_text.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['93DE']['remaining'], 49000000)

    def test_unexpected_read_failure_skips_feature(self):
        runner = self.runner()
        runner._capture = MagicMock(side_effect=RuntimeError('OCR failed'))
        runner._close_to_world = MagicMock()
        self.assertEqual(runner.run('name'), 0)
        runner._close_to_world.assert_called_once()

    def test_read_failure_logs_start_reason_and_return_to_normal_farming(self):
        logs = []
        runner = AllianceResourceFarmer(MagicMock(), MagicMock(), log=logs.append, state_path=None)
        runner._capture = MagicMock(side_effect=RuntimeError('OCR failed'))
        runner._close_to_world = MagicMock()
        self.assertEqual(runner.run('current character'), 0)
        self.assertTrue(all(line.startswith('[Alliance]') for line in logs))
        self.assertIn('Starting alliance resource check for current character', logs[0])
        self.assertTrue(any('OCR failed' in line for line in logs))
        self.assertIn('continuing normal farming', logs[-1])

    def test_missing_navigation_button_is_logged(self):
        runner = self.runner()
        runner.detector.locate_template.return_value = None
        logs = []
        runner.log = logs.append
        runner._capture = MagicMock()
        self.assertFalse(runner._press_text('lien minh'))
        self.assertEqual(logs[0], 'Looking for lien minh')
        self.assertIn('Could not find lien minh', logs[-1])

    def test_alliance_button_uses_shield_instead_of_merged_menu_label(self):
        runner = self.runner()
        runner.words = [('Đạo Cụ Liên Minh', (790, 690, 200, 25), .99)]
        runner._capture = MagicMock()
        runner._alliance_menu_button = MagicMock(return_value=(909, 650, 38, 40))
        self.assertTrue(runner._press_text('lien minh'))
        runner.game.click.assert_called_once_with((909, 650, 38, 40), 'frame')

    def test_alliance_shield_matches_real_menu_at_different_resolutions(self):
        fixture = Path(__file__).parent / 'fixtures' / 'alliance_world_menu.png'
        original = cv2.imread(str(fixture))
        for width, height in ((1280, 720), (1907, 1077)):
            with self.subTest(size=(width, height)):
                runner = self.runner()
                runner.frame = cv2.resize(original, (width, height))
                runner.words = [('Đạo Cụ Liên Minh', (790, 690, 200, 25), .99)]
                bounds = runner._alliance_menu_button()
                self.assertIsNotNone(bounds)
                x, y, w, h = bounds
                self.assertLess(abs((x + w / 2) / width - .727), .01)
                self.assertLess(abs((y + h / 2) / height - .935), .01)
                self.assertGreater(w, width * .02)

    def test_tiny_unrelated_shield_match_is_not_accepted(self):
        runner = self.runner()
        template = cv2.imread(str(Path(__file__).resolve().parents[1] / 'templates' / 'alliance' / 'menu_shield.png'))
        runner.frame[665:679, 1087:1100] = cv2.resize(template, (13, 14))
        self.assertIsNone(runner._alliance_menu_button())

    def test_alliance_label_fallback_rejects_merged_labels(self):
        runner = self.runner()
        runner.words = [('Đạo Cụ Liên Minh', (790, 690, 200, 25), .99)]
        self.assertIsNone(runner._alliance_label_icon_bounds())
        runner.words = [('Liên Minh', (883, 692, 90, 24), .99)]
        bounds = runner._alliance_label_icon_bounds()
        self.assertEqual(bounds[0] + bounds[2] / 2, 928)
        self.assertLess(bounds[1], 692)

    def test_territory_click_targets_red_flag(self):
        runner = self.runner()
        runner._capture = MagicMock()
        runner._template = MagicMock(return_value=(800, 380, 40, 50))
        self.assertTrue(runner._press_text('lanh tho', 'lanh th'))
        runner._template.assert_called_once_with('territory_flag')
        runner.game.click.assert_called_once_with((800, 380, 40, 50), 'frame')

    def test_preset_one_waits_for_delayed_control(self):
        runner = self.runner()
        runner._capture = MagicMock()
        runner._match_preset_icon = MagicMock(side_effect=[None, (1200, 250, 30, 30)])
        with patch('rok_automation.automation.alliance_resource.read_text_detections', return_value=[]):
            self.assertEqual(runner._preset_one_bounds(), (1200, 250, 30, 30))
        self.assertEqual(runner._capture.call_count, 2)

    def test_new_troops_uses_existing_blue_button_template(self):
        runner = self.runner()
        runner._capture = MagicMock()
        runner.detector.locate_template.return_value = MagicMock(bounds=(900, 100, 200, 80))
        self.assertTrue(runner._press_text('quan moi', 'quan mi'))
        runner.game.click.assert_called_once_with((900, 100, 200, 80), 'frame')
        template = runner.detector.locate_template.call_args.args[1]
        self.assertEqual(template.name, 'new_troops_button.png')

    def test_new_troops_uses_shared_screen_reader_and_ignores_instruction_text(self):
        runner = self.runner()
        runner.frame = np.zeros((1077, 1913, 3), dtype=np.uint8)
        runner.detector.locate_template.return_value = None
        runner.words = [('Tập hợp quân mới từ', (1388, 77, 245, 37), .99),
                        ('thành phố của bạn', (1389, 111, 222, 35), .99),
                        ('Quân mi', (1397, 179, 228, 73), .95)]
        self.assertIsNone(runner._new_troops_bounds())
        runner.dispatch_reader = MagicMock()
        runner.dispatch_reader.read.return_value = ScreenDetection(
            ScreenState.NEW_TROOPS_PROMPT, .99, 'new_troops_button', bounds=(1350, 154, 325, 130))
        self.assertEqual(runner._new_troops_bounds(), (1350, 154, 325, 130))
        runner.dispatch_reader.read.assert_called_once_with('frame', expected_states=(ScreenState.NEW_TROOPS_PROMPT,))

    def test_normal_and_alliance_detect_same_deployment_button_in_saved_capture(self):
        from rok_automation.vision.screen import ScreenDetector

        fixture = Path(__file__).parent / 'fixtures' / 'alliance_deployment_inset.png'
        detector = ScreenDetector()
        controller = FarmingController(MagicMock(), detector=detector, log=lambda _: None)
        detection, ambiguous = controller._classify_dispatch_screen(
            fixture, expected_states=(ScreenState.TROOP_COMPOSITION,))
        runner = AllianceResourceFarmer(MagicMock(), detector, state_path=None)
        runner.shot = fixture
        self.assertEqual(detection.state, ScreenState.TROOP_COMPOSITION)
        self.assertFalse(ambiguous)
        self.assertEqual(runner._dispatch_bounds(ScreenState.TROOP_COMPOSITION), detection.bounds)
        self.assertIsNone(runner._dispatch_bounds(ScreenState.NEW_TROOPS_PROMPT))

    def test_alliance_gather_variant_is_shared_and_clicks_visible_button(self):
        from rok_automation.vision.screen import ScreenDetector

        fixture = Path(__file__).parent / 'fixtures' / 'alliance_gather_popup.png'
        detector = ScreenDetector()
        controller = FarmingController(MagicMock(), detector=detector, log=lambda _: None)
        detection, ambiguous = controller._classify_dispatch_screen(
            fixture, expected_states=(ScreenState.RESOURCE_FOUND,))
        self.assertEqual(detection.state, ScreenState.RESOURCE_FOUND)
        self.assertFalse(ambiguous)
        self.assertGreater(detection.confidence, .95)
        runner = AllianceResourceFarmer(MagicMock(), detector, state_path=None, log=lambda _: None)
        runner.shot = fixture
        runner.frame = cv2.imread(str(fixture))
        runner._capture = MagicMock()
        self.assertFalse(runner._world_visible())
        # An already open popup must stop the node-selection retry loop.
        select_node = MagicMock()
        self.assertTrue(runner._verified_action('select node', select_node,
                                               lambda: bool(runner._dispatch_bounds(ScreenState.RESOURCE_FOUND))))
        select_node.assert_not_called()
        # The next action clicks the actual shared-detector button bounds.
        self.assertTrue(runner._press_dispatch(ScreenState.RESOURCE_FOUND,
                                              expected=lambda: runner.game.click.called))
        runner.game.click.assert_called_once_with(detection.bounds, fixture)

    def test_ambiguous_shared_button_requires_ocr_confirmation(self):
        from rok_automation.automation.dispatch import DispatchScreenReader

        reader = DispatchScreenReader(MagicMock())
        detection = ScreenDetection(ScreenState.NEW_TROOPS_PROMPT, .8, 'similar buttons', bounds=(1, 2, 3, 4))
        with patch.object(reader, 'classify', return_value=(detection, True)), \
             patch.object(reader, 'read_button_state', return_value=None):
            self.assertEqual(reader.read('frame').state, ScreenState.UNKNOWN)

    def test_preset_fallback_selects_one_and_rejects_two(self):
        runner = self.runner()
        runner._capture = MagicMock()
        runner._template = MagicMock(return_value=None)
        words = [('2', (1200, 310, 20, 25), .99), ('1', (1200, 250, 20, 25), .99)]
        with patch('rok_automation.automation.alliance_resource.read_text_detections', return_value=words):
            self.assertEqual(runner._preset_one_bounds(), (1206, 258, 8, 8))

    def test_visual_preset_match_distinguishes_one_from_two_at_different_sizes(self):
        templates = Path(__file__).resolve().parents[1] / 'templates' / 'alliance'
        one = cv2.imread(str(templates / 'preset_1_button.png'))
        two = cv2.imread(str(templates / 'preset_2_button.png'))
        for width, height in ((35, 35), (35, 30), (42, 42)):
            with self.subTest(size=(width, height)):
                runner = self.runner()
                runner.frame[300:300 + height, 1200:1200 + width] = cv2.resize(two, (width, height), interpolation=cv2.INTER_AREA)
                self.assertIsNone(runner._match_preset_icon())
                runner.frame[220:220 + height, 1200:1200 + width] = cv2.resize(one, (width, height), interpolation=cv2.INTER_AREA)
                bounds = runner._match_preset_icon()
                self.assertIsNotNone(bounds)
                self.assertLess(abs(bounds[0] - 1200), 3)
                self.assertLess(abs(bounds[1] - 220), 3)

    def test_preset_detected_in_actual_inset_game_capture(self):
        runner = self.runner()
        fixture = Path(__file__).parent / 'fixtures' / 'alliance_deployment_inset.png'
        runner.frame = cv2.imread(str(fixture))
        self.assertIsNotNone(runner.frame)
        bounds = runner._match_preset_icon()
        self.assertIsNotNone(bounds)
        x, y, w, h = bounds
        self.assertLess(abs(x + w / 2 - 1104), 3)
        self.assertLess(abs(y + h / 2 - 261), 3)
        # Retain preset #2 while hiding #1: the wider search must not select #2.
        runner.frame[y-3:y+h+3, x-3:x+w+3] = (70, 55, 0)
        self.assertIsNone(runner._match_preset_icon())

    def test_action_retries_missed_clicks_until_fifth_attempt_is_verified(self):
        runner = self.runner()
        runner._capture = MagicMock()
        find = MagicMock(return_value=(100, 100, 20, 20))
        self.assertTrue(runner._verified_action('open menu', find,
                                               lambda: runner.game.click.call_count == 5))
        self.assertEqual(runner.game.click.call_count, 5)
        self.assertEqual(find.call_count, 5)

    def test_action_does_not_succeed_just_because_it_clicked(self):
        runner = self.runner()
        runner._capture = MagicMock()
        self.assertFalse(runner._verified_action('open menu', lambda: (100, 100, 20, 20), lambda: False))
        self.assertEqual(runner.game.click.call_count, 5)

    def test_missing_button_is_searched_five_times(self):
        runner = self.runner()
        runner._capture = MagicMock()
        find = MagicMock(return_value=None)
        self.assertFalse(runner._verified_action('open menu', find, lambda: False))
        self.assertEqual(find.call_count, 5)
        runner.game.click.assert_not_called()

    def test_delayed_transition_is_polled_before_clicking_again(self):
        runner = self.runner()
        runner._capture = MagicMock()
        verified = MagicMock(side_effect=[False, False, True])
        self.assertTrue(runner._verified_action('open menu', lambda: (100, 100, 20, 20), verified))
        self.assertEqual(runner.game.click.call_count, 1)
        self.assertEqual(runner._capture.call_count, 3)

    def test_already_open_menu_is_verified_without_clicking(self):
        runner = self.runner()
        runner._capture = MagicMock()
        self.assertTrue(runner._verified_action('open menu', lambda: (100, 100, 20, 20), lambda: True))
        runner.game.click.assert_not_called()

    def test_preset_must_change_commanders_or_troop_settings(self):
        for changed in (True, False):
            with self.subTest(changed=changed):
                runner = self.runner()
                runner._capture = MagicMock()
                runner._preset_one_bounds = MagicMock(return_value=(1200, 250, 30, 30))
                runner._text = MagicMock(return_value=(800, 600, 200, 60))
                runner._deployment_signature = MagicMock(side_effect=[('old',), ('new',)] if changed else None,
                                                         return_value=('old',))
                self.assertEqual(runner._load_preset(), changed)
                self.assertEqual(runner.game.click.call_count, 1 if changed else 5)

    def test_preset_error_aborts_verification_without_marching(self):
        runner = self.runner()
        runner._capture = MagicMock()
        runner.words = [('Không thể tải trang bị này', (0, 0, 100, 20), .99)]
        with self.assertRaisesRegex(RuntimeError, 'outside the city'):
            runner._load_preset()
        runner.game.click.assert_not_called()

    def test_claim_and_resource_header_click_only_once(self):
        for label in ('nhan', 'trung tam tai nguyen'):
            with self.subTest(label=label):
                runner = self.runner()
                runner._capture = MagicMock()
                runner._text = MagicMock(return_value=(100, 100, 20, 20))
                self.assertTrue(runner._press_once(label))
                runner.game.click.assert_called_once()
                runner._text.assert_called_once()

    def test_missing_single_click_button_is_not_retried(self):
        runner = self.runner()
        runner._capture = MagicMock()
        runner._text = MagicMock(return_value=None)
        self.assertFalse(runner._press_once('nhan'))
        runner._text.assert_called_once()
        runner.game.click.assert_not_called()

    def test_missing_coordinates_scroll_back_up_reopen_then_scan_down_again(self):
        runner = self.runner()
        runner._capture = MagicMock()
        events = []
        scans = 0
        reopened = False

        def scroll(*, up):
            events.append('up' if up else 'down')

        def header(*args, **kwargs):
            return (100, 100, 200, 30) if 'up' in events else None

        def click(bounds):
            nonlocal reopened
            reopened = True
            events.append('open')

        def node(preferred):
            nonlocal scans
            scans += 1
            if reopened and events[-1] == 'down':
                return {'coordinates': [894, 768], 'remaining': 66000000}, (100, 300, 100, 20)
            return None, None

        runner._scroll_territory = scroll
        runner._text = header
        runner._click = click
        runner._node = node
        result, bounds = runner._find_alliance_node()
        self.assertEqual(result['coordinates'], [894, 768])
        self.assertEqual(events, ['down'] * 5 + ['up', 'open', 'down'])
        self.assertEqual(events.count('open'), 1)

    def test_territory_fallback_accepts_truncated_label_but_rejects_left_panel_count(self):
        runner = self.runner()
        runner.frame = np.zeros((1035, 1545, 3), dtype=np.uint8)
        runner.words = [('Lãnh th: 187', (81, 798, 188, 36), .98),
                        ('Lãnh th', (984, 662, 126, 37), .99)]
        bounds = runner._territory_label_icon_bounds()
        self.assertEqual(bounds[0] + bounds[2] / 2, 1047)
        self.assertLess(bounds[1], 662)


class AllianceWorkflowTests(unittest.TestCase):
    def test_alliance_precedes_normal_farming_and_reserves_one_slot(self):
        game, detector = MagicMock(), MagicMock()
        before = ScreenDetection(ScreenState.WORLD_MAP, 1, 'map', armies_out=3,
                                 account_name='Vàng 6 mỏLM', preferred_resource=Resource.GOLD)
        after = ScreenDetection(ScreenState.WORLD_MAP, 1, 'node', armies_out=4)
        controller = FarmingController(game, detector=detector, alliance_resource=True, log=lambda _: None,
                                       daily_rewards_flag_path=None)
        events = []
        runner = MagicMock()
        runner.run.side_effect = lambda name: events.append(('alliance', name)) or 1
        detector.detect.side_effect = [before, after]
        rewards = MagicMock()
        rewards.claim_all.side_effect = lambda: events.append(('rewards', None))
        rewards.ensure_city_view.return_value = True
        with patch.object(controller, '_should_claim_daily_rewards', return_value=True), \
             patch('rok_automation.automation.daily_reward.DailyRewardClaimer', return_value=rewards), \
             patch.object(controller, '_open_world_map_directly', return_value=('frame', before)), \
             patch.object(controller, '_farm_one_army', side_effect=lambda *args: events.append(('normal', args[0])) or True), \
             patch.object(controller, '_run_enabled_city_tasks'), \
             patch('rok_automation.automation.controller.load_recent_farmed_nodes', return_value=[]), \
             patch('rok_automation.automation.alliance_resource.AllianceResourceFarmer', return_value=runner):
            controller._farm_account()
        self.assertEqual(events, [('rewards', None), ('alliance', 'Vàng 6 mỏLM'), ('normal', 5)])
        self.assertEqual(controller.context.armies_dispatched, 1)
        self.assertEqual(controller.context.starting_armies_out, 3)
        self.assertEqual(controller.context.preferred_resource, Resource.GOLD)
        self.assertEqual(controller.context.account_name, 'Vàng 6 mỏLM')


if __name__ == '__main__':
    unittest.main()
