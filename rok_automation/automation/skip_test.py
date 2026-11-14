"""Standalone account-skip diagnostics using the regular rotation state."""
from pathlib import Path

from rok_automation.automation.account_switcher import AccountSwitcher, AccountTraversal
from rok_automation.automation.gmail_switcher import (
    GMAIL_PROGRESS_UPDATED, GmailAccountRegistry, GmailRotation, GmailSwitcher,
)
from rok_automation.vision.screen import ScreenDetector


def run_skip_test(game, kind: str, state_path: Path) -> None:
    detector = ScreenDetector()
    rotation = GmailRotation.load(state_path)
    switcher = GmailSwitcher(game, detector, signed_in_accounts=GmailAccountRegistry())
    # RoK skip tests reuse the last observed or successfully selected Gmail.
    # A Gmail skip always rechecks before marking an account as complete.
    current = rotation.current if kind == "rok" else None
    if current is None:
        current = switcher.switch_to_next(rotation, observe_only=True).identity
        if current is None:
            raise RuntimeError("Could not identify the current Gmail account")
        rotation.current = current
        rotation.save()
    else:
        print(f"Using remembered Gmail: {current.label()}")
    if kind == "gmail":
        switcher._register_current(rotation, current)
        result = switcher.switch_to_next(rotation)
        rotation.current = result.identity
        rotation.save()
        print(f"Skipped Gmail; selected {result.identity.label()}")
        return
    if kind != "rok":
        raise ValueError("Unknown skip type")

    saved = rotation.get_character_progress(current) or {}
    traversal = AccountTraversal()
    if saved.get("skip_test") and isinstance(saved.get("selected_index"), int):
        traversal.current_index = saved["selected_index"]
        traversal.total_accounts = saved.get("total")
        traversal.direction = saved.get("direction")
        traversal.skipped_index = saved.get("skipped_index")
        traversal.anchored_to_top = True

    def record(index, total, direction):
        rotation.set_character_progress(current, index, total, direction)
        entry = rotation.get_character_progress(current)
        entry.update(skip_test=True, skipped_index=traversal.skipped_index)
        rotation.save()
        print(GMAIL_PROGRESS_UPDATED)

    if traversal.current_index is not None:
        record(traversal.current_index, traversal.total_accounts, traversal.direction)
        if not traversal.has_more_accounts():
            switcher._register_current(rotation, current)
            print("No more RoK accounts; Gmail marked complete")
            return

    name = AccountSwitcher(game, detector).switch_to_next(traversal, on_initialized=record)
    if name is None:
        switcher._register_current(rotation, current)
        print("No more RoK accounts; Gmail marked complete")
        return
    # Keep the completed position on display; the selected one is still pending.
    entry = rotation.get_character_progress(current)
    if entry is not None:
        entry.update(skip_test=True, selected_index=traversal.current_index,
                     skipped_index=traversal.skipped_index)
        rotation.save()
    print(f"Skipped RoK account; selected {name}")
