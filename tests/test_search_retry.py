import unittest
from unittest.mock import MagicMock, patch

from rok_automation.automation.controller import FarmingController, FarmingError
from rok_automation.game.interface import TimingConfig
from rok_automation.vision.screen import ScreenDetection, ScreenState, TemplateHit


class SearchRetryTests(unittest.TestCase):
    def run_wait(self, *, capture_latency=0.021, map_changed=True, result_at=None, menu_visible=True):
        game, detector = MagicMock(), MagicMock()
        game.timing = TimingConfig(capture_latency=capture_latency)
        clock = [0.0]
        def capture():
            clock[0] += 0.1
            return 'frame'
        game.screenshot.side_effect = capture
        game.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        def locate(frame, path, **kwargs):
            if path.name == 'gather_button.png':
                return TemplateHit(0.95, (1, 2, 30, 20)) if result_at is not None and clock[0] >= result_at else None
            return TemplateHit(0.9, (1, 2, 30, 20)) if menu_visible else None
        detector.locate_template.side_effect = locate
        detector.detect.return_value = ScreenDetection(ScreenState.RESOURCE_FOUND, 0.95, 'found')
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
