"""Visit signed-in Gmail accounts without farming characters."""

from rok_automation.automation.gmail_switcher import GmailRotation, GmailSwitcher
from rok_automation.game.console import safe_print, format_log
from rok_automation.vision.screen import ScreenDetector


def cycle_gmail_accounts(game, *, log=safe_print) -> None:
    # Keep visit history separate from persisted farming progress.
    rotation = GmailRotation()
    switcher = GmailSwitcher(game, ScreenDetector(), log=log)
    log(format_log("Starting Gmail-only cycle; visiting signed-in accounts and returning to the starting Gmail", "Gmail"))
    while True:
        result = switcher.switch_to_next(rotation, visit_only=True)
        if result.run_complete:
            log(format_log(f"Gmail cycle complete; visited {len(rotation.farmed)} account(s). No armies dispatched", "Gmail"))
            return
