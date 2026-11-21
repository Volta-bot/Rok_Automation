import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2

from rok_automation.automation.controller import FarmingController, FarmingError
from rok_automation.game.interface import TimingConfig
from rok_automation.logic.resource_selector import RESOURCE_LEVELS, Resource
from rok_automation.vision.screen import (
    VIEW_TOGGLE_TEMPLATES, ScreenDetection, ScreenDetector, ScreenState, TemplateHit,
    cache_captured_frame, load_color_frame,
)


class SearchRetryTests(unittest.TestCase):
    def test_barbarian_panel_opens_search_and_switches_to_gold(self):
        screenshot = Path(__file__).parent / "fixtures" / "barbarian_search.png"
        game = MagicMock(timing=TimingConfig())
        game.screenshot.return_value = screenshot
        detector = ScreenDetector()
        controller = FarmingController(game, detector=detector, log=lambda _: None)
        controller.context.preferred_resource = Resource.GOLD

        _, opened = controller._wait_for_dispatch_change(
            ScreenState.WORLD_MAP, expected_states=(ScreenState.RESOURCE_SEARCH,),
        )
        self.assertIs(opened.state, ScreenState.RESOURCE_SEARCH)
        self.assertIn("active tab: barbarian", opened.evidence)
        self.assertIsNone(opened.preferred_resource)
        self.assertIsNone(opened.search_slider)
        game.screenshot.assert_called_once()

        gold = ScreenDetection(
            ScreenState.RESOURCE_SEARCH, .95, "gold panel", (946, 455, 153, 72),
            resource_level=8, preferred_resource=Resource.GOLD,
        )
        result = ScreenDetection(
            ScreenState.RESOURCE_FOUND, .95, "gather", resource_coordinates=(100, 200),
        )
        with patch.object(controller, "_set_search_level", return_value=(screenshot, gold)) as adjust, \
             patch.object(controller, "_wait_for_search_result", return_value=(screenshot, result)), \
             patch.object(controller, "_dispatch_army", return_value=True), \
             patch.object(controller, "_remember_farmed_node"):
            self.assertTrue(controller._farm_one_army(1, dict.fromkeys(Resource, max(RESOURCE_LEVELS)), set()))

        adjust.assert_called_once_with(8, initial_screenshot=None)
        self.assertEqual(game.click.call_count, 2)
        x, y, width, height = game.click.call_args_list[0].args[0]
        self.assertTrue(970 < x + width / 2 < 1075 and 585 < y + height / 2 < 690)
        self.assertEqual(game.click.call_args_list[1].args, (gold.bounds, screenshot))

    def run_wait(self, *, capture_latency=0.021, map_changed=True, result_at=None, menu_visible=True):
        game, detector = MagicMock(), MagicMock()
        game.timing = TimingConfig(capture_latency=capture_latency)
        clock = [0.0]
        def capture():
            clock[0] += 0.1
            return 'frame'
        game.screenshot.side_effect = capture
        game.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        def detect(frame, **kwargs):
            if result_at is not None and clock[0] >= result_at:
                return ScreenDetection(ScreenState.RESOURCE_FOUND, 0.95, 'found')
            if menu_visible:
                return ScreenDetection(ScreenState.RESOURCE_SEARCH, 0.9, 'search', (1, 2, 30, 20))
            return ScreenDetection(ScreenState.UNKNOWN, 0.0, 'loading')
        detector.detect.side_effect = detect
        controller = FarmingController(game, detector=detector, log=lambda _: None)
        with patch('rok_automation.automation.controller.time.monotonic', side_effect=lambda: clock[0]), \
             patch.object(controller, '_map_view_changed', return_value=map_changed):
            try:
                return controller._wait_for_search_result(object()), clock[0]
            except FarmingError as error:
                return error, clock[0]

    def test_open_search_preserves_checkpoint_transition_timeout(self):
        result, elapsed = self.run_wait()
        self.assertIsInstance(result, FarmingError)
        self.assertIn('Timed out waiting', str(result))
        self.assertGreaterEqual(elapsed, 12)

    def test_unchanged_map_keeps_existing_no_result_handling(self):
        result, elapsed = self.run_wait(map_changed=False)
        self.assertEqual(result[1].state, ScreenState.NO_RESULT)
        self.assertLess(elapsed, 2)

    def test_result_arriving_before_retry_is_processed(self):
        result, elapsed = self.run_wait(result_at=1.5)
        self.assertEqual(result[1].state, ScreenState.RESOURCE_FOUND)
        self.assertLess(elapsed, 2)

    def test_loading_without_search_button_keeps_full_transition_budget(self):
        result, elapsed = self.run_wait(menu_visible=False)
        self.assertIsInstance(result, FarmingError)
        self.assertIn('Timed out waiting', str(result))
        self.assertGreaterEqual(elapsed, 12)

    def test_city_build_icon_does_not_match_world_search(self):
        detector = ScreenDetector()
        fixtures = Path(__file__).parent / "fixtures"
        template = fixtures.parents[1] / "templates/navigation/world_search_button.png"
        self.assertIsNone(detector.locate_template(
            fixtures / "forge_bone_stacked.png", template, confidence_threshold=0.60,
        ))
        self.assertIsNotNone(detector.locate_template(
            fixtures / "alliance_gather_popup.png", template, confidence_threshold=0.78,
        ))

    def test_search_tap_rejects_weak_match_and_strong_match_on_city(self):
        shot = Path("frame.png")
        world = ScreenDetection(ScreenState.WORLD_MAP, 0.90, "previous world frame")
        city = ScreenDetection(ScreenState.CITY, 0.95, "City map button")
        for score in (0.615, 0.91):
            with self.subTest(score=score):
                game, detector = MagicMock(timing=TimingConfig()), MagicMock()
                detector.detect.return_value = (
                    ScreenDetection(ScreenState.WORLD_MAP, score, "weak search", (30, 520, 45, 45))
                    if score < 0.78 else city
                )
                controller = FarmingController(game, detector=detector, log=lambda _: None)
                with patch.object(controller, "_recover_search_mailbox"), \
                     patch("rok_automation.game.popups.close_extended_view", return_value=False):
                    if score < 0.78:
                        with self.assertRaisesRegex(FarmingError, "Could not locate world_search_button"):
                            controller._press_and_wait(
                                shot, world, Path("world_search_button.png"), confidence_threshold=0.60,
                            )
                    else:
                        self.assertEqual(controller._press_and_wait(
                            shot, world, Path("world_search_button.png"), confidence_threshold=0.78,
                        ), (shot, city))
                game.click.assert_not_called()

    def test_city_to_world_retries_after_first_tap_does_not_change_screen(self):
        first, retry, landed = map(Path, ("city.png", "city-again.png", "world.png"))
        city = ScreenDetection(ScreenState.CITY, 0.99, "city map button")
        world = ScreenDetection(ScreenState.WORLD_MAP, 0.99, "world map city button")
        controller = FarmingController(MagicMock(), detector=MagicMock(), log=lambda _: None)
        with patch.object(controller, "_read_screen", side_effect=[(first, city), (retry, city)]), \
             patch.object(controller, "_press_and_wait", side_effect=[None, (landed, world)]) as press:
            self.assertEqual(controller._open_world_map_directly(), (landed, world))
        self.assertEqual([call.args[0] for call in press.call_args_list], [first, retry])
        self.assertTrue(all(call.args[2].name == "city_map_button.png" for call in press.call_args_list))

    def test_view_detection_uses_build_and_search_with_either_toggle_icon(self):
        fixtures = Path(__file__).parent / "fixtures"
        detector = ScreenDetector()
        states = {ScreenState.CITY, ScreenState.WORLD_MAP}
        for source, state, swap in (
            ("forge_bone_stacked.png", ScreenState.CITY, VIEW_TOGGLE_TEMPLATES),
            ("alliance_gather_popup.png", ScreenState.WORLD_MAP, VIEW_TOGGLE_TEMPLATES[::-1]),
        ):
            with self.subTest(state=state):
                original = fixtures / source
                frame = load_color_frame(original).copy()
                replacement, existing = swap
                hit = detector.locate_template(original, existing)
                self.assertIsNotNone(hit)
                x, y, w, h = hit.bounds
                frame[y:y+h, x:x+w] = cv2.resize(cv2.imread(str(replacement)), (w, h))
                shot = Path(f"test-swapped-{state.value}.png")
                cache_captured_frame(shot, frame, memory_only=True)

                for path in (original, shot):
                    self.assertEqual(detector.detect(
                        path, read_account=False, candidate_states=states,
                    ).state, state)

    def test_world_entry_taps_fortress_first_and_falls_back_to_map(self):
        shot, landed = Path("city.png"), Path("world.png")
        city = ScreenDetection(ScreenState.CITY, 0.95, "build")
        world = ScreenDetection(ScreenState.WORLD_MAP, 0.95, "search")
        fortress = TemplateHit(0.84, (10, 600, 90, 90))
        map_button = TemplateHit(0.99, (14, 604, 90, 90))
        for hits, expected in (([fortress, map_button], fortress), ([None, map_button], map_button)):
            with self.subTest(expected=expected):
                game, detector = MagicMock(timing=TimingConfig()), MagicMock()
                detector.locate_template.side_effect = hits
                controller = FarmingController(game, detector=detector, log=lambda _: None)
                with patch.object(controller, "_read_screen", return_value=(shot, city)), \
                     patch.object(controller, "_wait_for_dispatch_change", return_value=(landed, world)):
                    self.assertEqual(controller._open_world_map_directly(), (landed, world))
                game.click.assert_called_once_with(expected.bounds, shot)
                expected_templates = VIEW_TOGGLE_TEMPLATES[:detector.locate_template.call_count]
                self.assertEqual(tuple(c.args[1] for c in detector.locate_template.call_args_list), expected_templates)
