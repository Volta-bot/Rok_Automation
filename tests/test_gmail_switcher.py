"""Unit tests for multi-Gmail account switching and rotation tracking."""

import tempfile
import unittest
from pathlib import Path

import cv2

from rok_automation.automation.account_switcher import AccountSwitcher
from rok_automation.automation.gmail_switcher import (
    GmailIdentity,
    GmailRotation,
    GmailSwitcher,
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


class TestGmailRotation(unittest.TestCase):
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
