import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rok_automation.__main__ import main
from rok_automation.automation.gmail_cycle import cycle_gmail_accounts
from rok_automation.automation.gmail_switcher import GmailIdentity, GmailSwitcher, GmailSwitchResult
from rok_automation.ui.app import ROKAutomationApp


class GmailCycleTests(unittest.TestCase):
    def test_cycle_returns_to_start_without_loading_farm_progress(self):
        logs = []
        first = GmailIdentity(email='first@example.com')
        second = GmailIdentity(email='second@example.com')
        rotations = []
        switcher = GmailSwitcher(MagicMock(), MagicMock(), log=logs.append)
        def step(rotation, *, visit_only):
            self.assertTrue(visit_only)
            self.assertIsNone(rotation.path)
            identity = first if not rotations else second
            switcher._record_rotation_current(rotation, identity, visit_only=True)
            rotations.append(rotation)
            return GmailSwitchResult(identity=second if len(rotations) == 1 else first,
                                     run_complete=len(rotations) == 2)
        with patch('rok_automation.automation.gmail_cycle.GmailSwitcher') as factory, \
             patch('rok_automation.automation.gmail_cycle.GmailRotation.load') as load, \
             patch.object(switcher, '_register_current') as farmed:
            factory.return_value.switch_to_next.side_effect = step
            cycle_gmail_accounts(MagicMock(), log=logs.append)
        load.assert_not_called()
        farmed.assert_not_called()
        self.assertIs(rotations[0], rotations[1])
        self.assertEqual(len(rotations[0].farmed), 2)
        self.assertIn('Gmail cycle complete', logs[-1])
        self.assertFalse(any('Finished all RoK' in line for line in logs))

    def test_cli_cycle_never_constructs_farming_controller(self):
        game = MagicMock()
        with patch.object(sys, 'argv', ['rok', '--cycle-gmail-only']), \
             patch('rok_automation.__main__._build_game', return_value=game), \
             patch('rok_automation.__main__.clear_paused'), \
             patch('rok_automation.__main__.FarmingController') as farm, \
             patch('rok_automation.automation.gmail_cycle.cycle_gmail_accounts') as cycle:
            self.assertEqual(main(), 0)
        cycle.assert_called_once_with(game)
        farm.assert_not_called()

    def make_app(self, farm, switch, chores=False):
        app = ROKAutomationApp.__new__(ROKAutomationApp)
        app._is_running = False
        for name, value in {'opt_farm': farm, 'opt_switch_gmail': switch,
                            'opt_alliance_resource': False,
                            'opt_collect_city_resources': chores, 'opt_auto_forge': chores,
                            'opt_daily_reward': chores, 'opt_alliance': False,
                            'opt_train_troops': False, 'opt_forge_material': 'Iron'}.items():
            setattr(app, name, SimpleNamespace(get=lambda value=value: value))
        app._require_instance = MagicMock(return_value=True)
        app._acquire_instance_lock = MagicMock(return_value=True)
        app._state_path = MagicMock(side_effect=lambda name: Path('unused') / name)
        app.selected_emulator = MagicMock(return_value='mumu')
        app.gmail_state_path = None
        app.session_summary_path = None
        app.daily_reward_flag_path = Path('unused/daily.flag')
        app._session_targets = []
        app.log = MagicMock()
        app.log_info = MagicMock()
        app._start_worker = MagicMock()
        return app

    def test_checkbox_combinations_and_plan_without_any_dialog(self):
        for farm, switch, expected in ((False, False, None), (False, True, '--cycle-gmail-only'),
                                       (True, False, '--farm'), (True, True, '--farm')):
            with self.subTest(farm=farm, switch=switch):
                app = self.make_app(farm, switch)
                events = []
                app.log_info.side_effect = lambda message: events.append(message)
                app._start_worker.side_effect = lambda cmd: events.append('START')
                with patch('rok_automation.ui.app.messagebox.askyesno') as dialog, \
                     patch('rok_automation.ui.app.clear_skip'), \
                     patch('rok_automation.ui.app.set_daily_rewards'), \
                     patch('rok_automation.ui.app.set_city_resources'), \
                     patch('rok_automation.ui.app.set_alliance_resource'), \
                     patch('rok_automation.ui.app.set_forge_material'):
                    app.start_automation()
                dialog.assert_not_called()
                self.assertTrue(any('Auto Farm Resources:' in call.args[0] for call in app.log.call_args_list))
                self.assertTrue(any(line.startswith('Plan:') for line in events))
                if expected is None:
                    app._start_worker.assert_not_called()
                    app._acquire_instance_lock.assert_not_called()
                else:
                    cmd = app._start_worker.call_args.args[0]
                    self.assertIn(expected, cmd)
                    self.assertEqual(events[-1], 'START')
                    if not farm:
                        self.assertNotIn('--farm', cmd)

    def test_cycle_only_excludes_reward_and_city_actions(self):
        app = self.make_app(False, True, chores=True)
        with patch('rok_automation.ui.app.clear_skip'), \
             patch('rok_automation.ui.app.messagebox.askyesno') as dialog:
            app.start_automation()
        cmd = app._start_worker.call_args.args[0]
        self.assertIn('--cycle-gmail-only', cmd)
        for flag in ('--farm', '--city-tasks-only', '--daily-reward-only'):
            self.assertNotIn(flag, cmd)
        dialog.assert_not_called()
