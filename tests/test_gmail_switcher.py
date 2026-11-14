"""Unit tests for multi-Gmail account switching and rotation tracking."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from rok_automation.automation.account_switcher import AccountSwitcher
from rok_automation.automation.gmail_switcher import (
    GmailIdentity,
    GmailAccountRegistry,
    GMAIL_PROGRESS_UPDATED,
    GmailRotation,
    GmailSwitcher,
    _LilithScreen,
    _DropdownEntry,
    email_key,
)
from rok_automation.vision.screen import ScreenDetector


class DummyGame:
    timing = type("Timing", (), {"poll_interval": 0.05, "click_delay": 0.02})()

    def __init__(self) -> None:
        self.clicks: list[tuple[int, int, int, int]] = []
        self.drags: list[tuple[tuple[int, int], tuple[int, int]]] = []

    def screenshot(self) -> Path:
        return Path("dummy.png")

    def click(self, bounds, screenshot_path) -> None:
        self.clicks.append(bounds)

    def drag(self, start, end, screenshot_path) -> None:
        self.drags.append((start, end))

    def wait(self, seconds: float) -> None:
        pass


class TestGmailIdentity(unittest.TestCase):
    def test_email_normalization(self):
        self.assertEqual(email_key("FarmDuy06@gmail.com"), "farmduy06@gmail.com")
        self.assertEqual(email_key("  sduy6132 @ gmail.com  "), "sduy6132@gmail.com")
        self.assertEqual(email_key("renyasettty@gmai1.com"), "renyasettty@gmail.com")
        self.assertIsNone(email_key("not-an-email"))

    def test_identity_matching(self):
        a1 = GmailIdentity(email="farmduy06@gmail.com", uid="51486")
        a2 = GmailIdentity(email="farmduy06@gmail.com", uid=None)
        a3 = GmailIdentity(email=None, uid="51486")
        b = GmailIdentity(email="farmduy07@gmail.com", uid="51487")

        self.assertTrue(a1.matches(a2))
        self.assertTrue(a1.matches(a3))
        self.assertFalse(a1.matches(b))
        self.assertFalse(a2.matches(b))


class TestRedesignedUserCenter(unittest.TestCase):
    def test_reads_current_identity_and_red_switch_button(self):
        frame = np.full((720, 1280, 3), 245, dtype=np.uint8)
        cv2.rectangle(frame, (309, 428), (550, 492), (70, 65, 250), -1)
        detections = (
            ("Trung Tâm Người Dùng", (55, 20, 200, 25), 0.99),
            ("example@gmail.com", (110, 75, 220, 25), 0.99),
            ("ID tài khoản:32990", (110, 120, 150, 20), 0.99),
        )
        switcher = GmailSwitcher(DummyGame(), ScreenDetector(), log=lambda _: None)
        with patch("rok_automation.automation.gmail_switcher.read_text_detections", return_value=detections):
            screen = switcher._read_user_center(frame)
        self.assertEqual(screen.current, GmailIdentity(email="example@gmail.com", uid="32990"))
        x, y, width, height = screen.switch_link
        self.assertTrue(x <= 430 <= x + width and y <= 460 <= y + height)
        x, y, width, height = screen.user_center_close
        self.assertTrue(x <= 42 <= x + width and y <= 30 <= y + height)

    def test_does_not_treat_account_number_as_switch_button(self):
        frame = np.full((720, 1280, 3), 245, dtype=np.uint8)
        self.assertIsNone(GmailSwitcher._find_user_center_switch_button(frame))

    def test_switch_closes_character_list_before_clicking_account(self):
        game = DummyGame()
        switcher = GmailSwitcher(game, ScreenDetector(), log=lambda _: None)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        switcher.rok_menus._detect_menu_state = MagicMock(side_effect=[
            ("character_list", None, 1.0), ("settings_menu", (20, 20, 30, 30), 1.0),
        ])
        close = (1062, 68, 48, 40)
        account = (300, 200, 40, 30)
        with patch.object(switcher, "_capture", return_value=(Path("screen.png"), frame)), \
             patch.object(switcher, "_read_lilith_screen", return_value=None), \
             patch.object(switcher, "_find_game_menu_close", return_value=close), \
             patch.object(switcher, "_find_account_button", return_value=account), \
             patch.object(switcher, "_wait_for_state_change", side_effect=[None, RuntimeError("done")]) as wait:
            with self.assertRaisesRegex(RuntimeError, "done"):
                switcher.switch_to_next(GmailRotation())
        self.assertEqual(game.clicks, [close, account])
        self.assertEqual(wait.call_args_list[0].args, ("character_list",))

    def test_character_list_close_is_bounded_when_x_is_missing(self):
        game = DummyGame()
        switcher = GmailSwitcher(game, ScreenDetector(), log=lambda _: None)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        switcher.rok_menus._detect_menu_state = MagicMock(return_value=("character_list", None, 1.0))
        with patch.object(switcher, "_capture", return_value=(Path("screen.png"), frame)), \
             patch.object(switcher, "_read_lilith_screen", return_value=None), \
             patch.object(switcher, "_find_game_menu_close", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Character list close button"):
                switcher.switch_to_next(GmailRotation())
        self.assertEqual(game.clicks, [])

    def test_close_sequence_returns_to_map(self):
        game = DummyGame()
        game.timing = MagicMock()
        game.timing.scaled = lambda seconds: seconds
        detector = MagicMock()
        detector.locate_template.return_value.bounds = (1200, 10, 35, 35)
        switcher = GmailSwitcher(game, detector, log=lambda _: None)
        back = (24, 10, 38, 40)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        captures = [(Path("settings.png"), frame), (Path("profile.png"), frame),
                    (Path("map.png"), frame)]
        switcher.rok_menus._detect_menu_state = MagicMock(side_effect=[
            ("settings_menu", None, 1.0), ("governor_profile", None, 1.0),
            ("map_or_city", None, 1.0),
        ])
        with patch.object(switcher, "_find_game_menu_close", return_value=(1200, 10, 35, 35)), \
                patch.object(switcher, "_capture", side_effect=captures), \
                patch.object(switcher, "_read_lilith_screen", return_value=None):
            switcher._close_lilith_ui(Path("center.png"),
                                      _LilithScreen(state="user_center", user_center_close=back))
        self.assertEqual(game.clicks, [back, (1200, 10, 35, 35), (1200, 10, 35, 35)])

    def test_close_retries_and_stops_if_menu_does_not_close(self):
        game = DummyGame()
        game.timing = MagicMock()
        game.timing.scaled = lambda seconds: seconds
        detector = MagicMock()
        detector.locate_template.return_value.bounds = (1200, 10, 35, 35)
        switcher = GmailSwitcher(game, detector, log=lambda _: None)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        switcher.rok_menus._detect_menu_state = MagicMock(return_value=("settings_menu", None, 1.0))
        with patch.object(switcher, "_find_game_menu_close", return_value=(1200, 10, 35, 35)), \
                patch.object(switcher, "_capture", return_value=(Path("settings.png"), frame)), \
                patch.object(switcher, "_read_lilith_screen", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "settings_menu close button did not respond"):
                switcher._close_lilith_ui(Path("settings.png"), None)
        self.assertEqual(len(game.clicks), 5)

    def test_finds_close_x_on_centered_panel(self):
        template = cv2.imread(str(Path(__file__).resolve().parents[1] / "templates/daily_reward/vip_close_x.png"))
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        height, width = template.shape[:2]
        frame[35:35 + height, 1087:1087 + width] = template
        bounds = GmailSwitcher._find_game_menu_close(frame)
        self.assertIsNotNone(bounds)
        x, y, w, h = bounds
        self.assertTrue(x <= 1087 + width // 2 <= x + w)
        self.assertTrue(y <= 35 + height // 2 <= y + h)


class TestRedesignedGmailDropdown(unittest.TestCase):
    def test_dropdown_accepts_account_id_rows(self):
        frame = np.full((712, 1275, 3), 245, dtype=np.uint8)
        detections = (
            ("Chuyển tài khoản", (558, 218, 159, 23), 0.99),
            ("first@gmail.com", (475, 302, 200, 25), 0.99),
            ("first@gmail.com", (475, 374, 200, 21), 0.99),
            ("ID tài khon: 1744392", (476, 406, 155, 18), 0.99),
            ("second@gmail.com", (475, 455, 189, 23), 0.99),
            ("ID tài khoản: 51486", (476, 489, 138, 18), 0.99),
            ("third@gmail.com", (474, 538, 192, 23), 0.99),
        )
        switcher = GmailSwitcher(DummyGame(), ScreenDetector(), log=lambda _: None)
        with patch("rok_automation.automation.gmail_switcher.read_text_detections", return_value=detections):
            screen = switcher._read_popup(frame)
        self.assertEqual(screen.state, "dropdown_open")
        self.assertEqual([entry.uid for entry in screen.entries], ["1744392", "51486", None])
        self.assertEqual(screen.field_email, "first@gmail.com")

    def test_swipe_starts_and_ends_inside_dropdown_rows(self):
        game = DummyGame()
        switcher = GmailSwitcher(game, ScreenDetector(), log=lambda _: None)
        entries = [
            _DropdownEntry("first@gmail.com", "1744392", (475, 374, 200, 21)),
            _DropdownEntry("second@gmail.com", "51486", (475, 455, 189, 23)),
            _DropdownEntry("third@gmail.com", None, (474, 538, 192, 23)),
        ]
        switcher._scroll_dropdown(Path("frame.png"), np.zeros((712, 1275, 3)), entries)
        start, end = game.drags[0]
        self.assertTrue(340 < end[1] < start[1] < 590)
        self.assertGreater(start[1] - end[1], 180)

    def test_swipe_distance_does_not_shrink_with_fewer_visible_rows(self):
        game = DummyGame()
        switcher = GmailSwitcher(game, ScreenDetector(), log=lambda _: None)
        frame = np.zeros((712, 1275, 3))
        rows = [_DropdownEntry("one@gmail.com", None, (475, 374, 200, 21)),
                _DropdownEntry("two@gmail.com", None, (475, 455, 200, 21)),
                _DropdownEntry("three@gmail.com", None, (475, 538, 200, 21))]
        switcher._scroll_dropdown(Path("frame.png"), frame, rows)
        switcher._scroll_dropdown(Path("frame.png"), frame, rows[:1])
        self.assertEqual(game.drags[0], game.drags[1])


class TestGmailRotation(unittest.TestCase):
    def test_manual_marks_preserve_current_and_can_be_reversed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rotation.json"
            current = GmailIdentity(email="current@gmail.com")
            marked = GmailIdentity(email="marked@gmail.com")
            rotation = GmailRotation(first=current, current=current, path=path)
            rotation.set_manual_farmed(marked, True)
            saved = GmailRotation.load(path)
            self.assertEqual(saved.current, current)
            self.assertEqual(saved.first, current)
            self.assertTrue(saved.is_farmed(marked))
            saved.set_manual_farmed(marked, False)
            self.assertFalse(GmailRotation.load(path).is_farmed(marked))

    def test_editing_completed_run_preserves_other_marks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rotation.json"
            a, b = GmailIdentity(email="a@gmail.com"), GmailIdentity(email="b@gmail.com")
            rotation = GmailRotation(first=a, current=a, farmed=[a, b], completed=True, path=path)
            rotation.set_manual_farmed(a, False)
            saved = GmailRotation.load(path)
            self.assertIsNone(saved.first)
            self.assertFalse(saved.is_farmed(a))
            self.assertTrue(saved.is_farmed(b))

    def test_finishing_account_saves_verified_entry_before_notification(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = GmailAccountRegistry(Path(tmpdir) / "accounts.json")
            rotation = GmailRotation(path=Path(tmpdir) / "rotation.json")
            identity = GmailIdentity(email="new@gmail.com", uid="123")
            notifications = []

            def log(message):
                if GMAIL_PROGRESS_UPDATED in message:
                    notifications.append((registry.load(), GmailRotation.farmed_from_path(rotation.path)))

            switcher = GmailSwitcher(DummyGame(), ScreenDetector(), log=log, signed_in_accounts=registry)
            switcher._register_current(rotation, identity)
            self.assertEqual(len(notifications), 1)
            self.assertTrue(notifications[0][0][0].verified)
            self.assertEqual(notifications[0][1], [identity])

    def test_rotation_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            rotation = GmailRotation.load(state_file)
            self.assertFalse(rotation.is_resumed)
            self.assertIsNone(rotation.first)
            self.assertEqual(len(rotation.farmed), 0)

            acc1 = GmailIdentity(email="acc1@gmail.com", uid="111")
            acc2 = GmailIdentity(email="acc2@gmail.com", uid="222")
            acc3 = GmailIdentity(email="acc3@gmail.com", uid="333")

            rotation.mark_farmed(acc1)
            self.assertEqual(rotation.first, acc1)
            self.assertEqual(len(rotation.farmed), 1)
            self.assertTrue(rotation.is_farmed(acc1))
            self.assertFalse(rotation.is_farmed(acc2))

            # Reload from disk
            reloaded = GmailRotation.load(state_file)
            self.assertTrue(reloaded.is_resumed)
            self.assertEqual(reloaded.first, acc1)
            self.assertEqual(len(reloaded.farmed), 1)

            # Mark acc2 farmed
            reloaded.mark_farmed(acc2)
            self.assertEqual(len(reloaded.farmed), 2)
            self.assertEqual(reloaded.current, acc2)


class TestTargetSelection(unittest.TestCase):
    def setUp(self):
        self.game = DummyGame()
        self.switcher = GmailSwitcher(self.game, ScreenDetector(), log=lambda m: None)

    def test_single_account_stops(self):
        acc1 = GmailIdentity(email="acc1@gmail.com", uid="111")
        rotation = GmailRotation(first=acc1, farmed=[acc1], current=acc1)
        entries = []
        target = self.switcher._choose_target(rotation, acc1, entries)
        self.assertIsNone(target)

    def test_three_account_rotation(self):
        acc1 = GmailIdentity(email="acc1@gmail.com", uid="111")
        acc2 = GmailIdentity(email="acc2@gmail.com", uid="222")
        acc3 = GmailIdentity(email="acc3@gmail.com", uid="333")

        # Step 1: Start on acc1, farmed acc1. Dropdown has acc2 and acc3.
        # acc3 is at the bottom (y=300), acc2 is at y=200.
        rotation = GmailRotation(first=acc1, farmed=[acc1], current=acc1)
        entries = [
            _DropdownEntry("acc2@gmail.com", "222", (100, 200, 100, 30)),
            _DropdownEntry("acc3@gmail.com", "333", (100, 300, 100, 30)),
        ]
        chosen = self.switcher._choose_target(rotation, acc1, entries)
        self.assertIsNotNone(chosen)
        entry, returning_to_first = chosen
        self.assertEqual(entry.email, "acc3@gmail.com")
        self.assertFalse(returning_to_first)

        # Step 2: Now on acc3, farmed acc3. Dropdown has acc1 and acc2.
        # acc2 is at the bottom (y=300), acc1 is at y=200.
        rotation.mark_farmed(acc3)
        entries = [
            _DropdownEntry("acc1@gmail.com", "111", (100, 200, 100, 30)),
            _DropdownEntry("acc2@gmail.com", "222", (100, 300, 100, 30)),
        ]
        chosen = self.switcher._choose_target(rotation, acc3, entries)
        self.assertIsNotNone(chosen)
        entry, returning_to_first = chosen
        self.assertEqual(entry.email, "acc2@gmail.com")
        self.assertFalse(returning_to_first)

        # Step 3: Now on acc2, farmed acc2. All other accounts (acc3, acc2) are farmed!
        # Dropdown has acc3 (y=200) and acc1 (y=300).
        rotation.mark_farmed(acc2)
        entries = [
            _DropdownEntry("acc3@gmail.com", "333", (100, 200, 100, 30)),
            _DropdownEntry("acc1@gmail.com", "111", (100, 300, 100, 30)),
        ]
        chosen = self.switcher._choose_target(rotation, acc2, entries)
        self.assertIsNotNone(chosen)
        entry, returning_to_first = chosen
        self.assertEqual(entry.email, "acc1@gmail.com")
        self.assertTrue(returning_to_first)  # Run complete!


class TestUploadedScreenshots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = DummyGame()
        cls.detector = ScreenDetector()
        cls.switcher = GmailSwitcher(cls.game, cls.detector, log=lambda m: None)
        cls.base_dir = Path(
            r"C:\Users\SON_PC\.gemini\antigravity\brain\674f8559-46fd-427e-8436-9d5b2540895b\.user_uploaded"
        )

    def test_governor_profile_screen(self):
        p = self.base_dir / "media_1794386400760_9a1c7077.jpg"
        if not p.exists():
            self.skipTest("Image not present")
        frame = cv2.imread(str(p))
        state, bounds, score = self.switcher.rok_menus._detect_menu_state(p, frame)
        self.assertEqual(state, "governor_profile")
        self.assertIsNotNone(bounds)

    def test_user_center_screen(self):
        p = self.base_dir / "media_1794386413503_b3caa0d0.png"
        if not p.exists():
            self.skipTest("Image not present")
        frame = cv2.imread(str(p))
        screen = self.switcher._read_lilith_screen(frame)
        self.assertIsNotNone(screen)
        self.assertEqual(screen.state, "user_center")
        self.assertIsNotNone(screen.current)
        self.assertEqual(screen.current.email, "hayday2103pl@gmail.com")
        self.assertEqual(screen.current.uid, "798030")
        self.assertIsNotNone(screen.switch_link)

    def test_switch_popup_screen(self):
        p = self.base_dir / "media_1794386430375_1050bf76.png"
        if not p.exists():
            self.skipTest("Image not present")
        frame = cv2.imread(str(p))
        screen = self.switcher._read_lilith_screen(frame)
        self.assertIsNotNone(screen)
        self.assertEqual(screen.state, "switch_popup")
        self.assertEqual(screen.field_email, "farmduy06@gmail.com")
        self.assertIsNotNone(screen.login_button)

    def test_dropdown_open_screen(self):
        p = self.base_dir / "media_1794386621057_3c3ff0ca.png"
        if not p.exists():
            self.skipTest("Image not present")
        frame = cv2.imread(str(p))
        screen = self.switcher._read_lilith_screen(frame)
        self.assertIsNotNone(screen)
        self.assertEqual(screen.state, "dropdown_open")
        self.assertEqual(screen.field_email, "farmduy06@gmail.com")
        self.assertGreaterEqual(len(screen.entries), 2)
        bottom_entry = screen.entries[-1]
        self.assertEqual(bottom_entry.email, "renyasettty@gmail.com")
        self.assertEqual(bottom_entry.uid, "32990")


class TestEntryClickBounds(unittest.TestCase):
    """The click on a dropdown entry lands in the middle of its row."""

    def setUp(self):
        self.switcher = GmailSwitcher(DummyGame(), ScreenDetector(), log=lambda m: None)

    def test_the_click_is_lowered_half_a_row(self):
        # Rows 56px apart, e-mail text 22px tall at the top of each row.
        entries = [
            _DropdownEntry("a@gmail.com", None, (100, 350, 180, 22)),
            _DropdownEntry("b@gmail.com", None, (100, 406, 180, 22)),
            _DropdownEntry("c@gmail.com", None, (100, 462, 180, 22)),
        ]
        bounds = self.switcher._entry_click_bounds(entries[-1], entries)
        self.assertEqual(bounds, (100, 462, 180, 56))
        # The click point (centre) must be clearly below the text centre.
        self.assertGreater(
            bounds[1] + bounds[3] // 2,
            entries[-1].bounds[1] + entries[-1].bounds[3] // 2,
        )

    def test_the_lowered_click_stays_inside_the_row(self):
        entries = [
            _DropdownEntry("a@gmail.com", None, (100, 350, 180, 22)),
            _DropdownEntry("b@gmail.com", None, (100, 406, 180, 22)),
            _DropdownEntry("c@gmail.com", None, (100, 462, 180, 22)),
        ]
        for entry in entries:
            bounds = self.switcher._entry_click_bounds(entry, entries)
            centre_y = bounds[1] + bounds[3] // 2
            # The click must stay inside this row: above the next row's
            # e-mail text and below this row's text centre.
            self.assertGreater(centre_y, entry.bounds[1])
            index = entries.index(entry)
            if index + 1 < len(entries):
                self.assertLess(centre_y, entries[index + 1].bounds[1])

    def test_a_wrapped_two_line_email_is_not_lowered_too_far(self):
        # A wrapped e-mail is taller than the others; the row pitch does
        # not change, so the click still lands mid-row.
        entries = [
            _DropdownEntry("a@gmail.com", None, (100, 350, 180, 22)),
            _DropdownEntry("wrapped.long.name@gmail.com", None, (100, 406, 180, 44)),
        ]
        bounds = self.switcher._entry_click_bounds(entries[-1], entries)
        self.assertEqual(bounds, (100, 406, 180, 56))
        centre_y = bounds[1] + bounds[3] // 2
        self.assertEqual(centre_y, 434)

    def test_without_measurable_pitch_the_bounds_are_kept(self):
        single = [_DropdownEntry("a@gmail.com", None, (100, 350, 180, 22))]
        self.assertEqual(
            self.switcher._entry_click_bounds(single[0], single),
            single[0].bounds,
        )


if __name__ == "__main__":
    unittest.main()
