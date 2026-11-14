"""Command-line entry point for emulator detection and screenshot capture."""

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from rok_automation.automation.controller import FarmingController
from rok_automation.automation.gmail_switcher import DEFAULT_GMAIL_STATE_PATH
from rok_automation.game.emulators import families
from rok_automation.game.frame import CANONICAL_CAPTURE_SIZE
from rok_automation.game.native import create_native_interface
from rok_automation.game.pause import clear_paused
from rok_automation.vision.screen import ScreenDetector
from rok_automation.logic.resource_selector import (
    RESOURCE_LEVELS,
    Resource,
    format_resource_amount,
)

_RESOURCE_DISPLAY_NAMES = {
    Resource.GOLD: "Gold (Vàng)",
    Resource.STONE: "Stone (Đá)",
    Resource.FOOD: "Food (Ngô)",
    Resource.WOOD: "Wood (Gỗ)",
}


def _run_stockpile_check(game: Any) -> int:
    """Capture the current screen and report stockpiles + farming choice.

    Mirrors the controller's fallback: a resource keyword in the account
    name wins, otherwise the lowest top-bar stockpile is farmed, and the
    weighted-random pick is only used when nothing could be read.
    """

    from rok_automation.logic.resource_selector import (
        lowest_stockpile_resource,
        random_preferred_resource,
    )
    from rok_automation.vision.resource_bar import read_resource_bar
    from rok_automation.vision.screen import ScreenDetector, load_color_frame

    try:
        screenshot_path = game.screenshot()
    except (OSError, RuntimeError) as exc:
        print(f"Screenshot failed: {exc}")
        return 1
    print(f"Screenshot saved: {screenshot_path}")

    frame = load_color_frame(screenshot_path)
    if frame is None:
        print(f"Could not read the captured frame: {screenshot_path}")
        return 1

    try:
        stockpiles = read_resource_bar(frame)
    except RuntimeError as exc:
        print(f"Stockpile OCR failed: {exc}")
        stockpiles = {}

    print("Stockpiles (top bar):")
    for resource in Resource:
        amount = stockpiles.get(resource)
        if amount is None:
            print(f"  {resource.value}: not readable")
        else:
            print(f"  {resource.value}: {format_resource_amount(amount)}")

    keyword_resource: Resource | None = None
    account_name: str | None = None
    try:
        detection = ScreenDetector().detect(screenshot_path)
        if detection.state.value == "world_map":
            account_name = detection.account_name
            keyword_resource = detection.preferred_resource
    except (OSError, RuntimeError, ValueError):
        pass

    if account_name:
        print(f"Account: {account_name}")
    if keyword_resource is not None:
        print(
            f"Would farm: {keyword_resource.value} — resource keyword in "
            "the account name"
        )
        return 0

    chosen = lowest_stockpile_resource(stockpiles)
    if chosen is not None:
        print(
            f"Would farm: {chosen.value} — lowest top-bar stockpile "
            f"({format_resource_amount(stockpiles[chosen] or 0)})"
        )
    else:
        fallback = random_preferred_resource()
        print(
            f"Would farm: {fallback.value} — stockpiles unreadable, "
            "random choice (Gold weighted higher)"
        )
    return 0


def _build_game(args):
    """Open the emulator connection, or report why it failed."""

    try:
        interface = create_native_interface(
            emulator=args.emulator,
            index=args.emulator_index,
            adb_path=args.adb_path,
            serial=args.adb_serial,
            log=lambda message: print(f"[{message}]"),
        )
    except Exception as exc:  # noqa: BLE001 - reported to the user
        print(f"Emulator: Disconnected ({exc})")
        return None

    # Remember the emulator that was asked for, so the next run can use it
    # without repeating the flag.
    if args.emulator:
        try:
            from rok_automation.game.emulators import save_selection

            save_selection(args.emulator, args.emulator_index)
        except OSError:
            pass

    return interface


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Rise of Kingdoms emulator tools")
    parser.add_argument(
        "--screenshot",
        nargs="?",
        const=Path("artifacts/mumu.png"),
        type=Path,
        metavar="PATH",
        help="capture the MuMu game screen over ADB",
    )
    parser.add_argument(
        "--detect-screen",
        action="store_true",
        help="capture MuMu and classify the visible game screen",
    )
    parser.add_argument(
        "--check-stockpiles",
        action="store_true",
        help=(
            "capture the game and report the top-bar stockpiles plus the "
            "resource the automation would choose to farm"
        ),
    )
    parser.add_argument(
        "--farm",
        action="store_true",
        help=(
            "farm each linked RoK character across all logged-in Gmail accounts, "
            "dispatching up to five armies per character"
        ),
    )
    parser.add_argument(
        "--daily-reward",
        action="store_true",
        help="claim daily VIP and campaign rewards before farming each account",
    )
    parser.add_argument(
        "--daily-reward-only",
        action="store_true",
        help="claim daily VIP and campaign rewards on the current account and exit",
    )
    parser.add_argument(
        "--no-switch-gmail",
        action="store_true",
        help="farm characters in the current Gmail only (do not switch Gmail accounts)",
    )
    parser.add_argument(
        "--current-rok-only",
        action="store_true",
        help="farm the currently active RoK character only (do not switch characters)",
    )
    parser.add_argument(
        "--gmail-state",
        type=Path,
        default=DEFAULT_GMAIL_STATE_PATH,
        metavar="PATH",
        help="custom path for Gmail rotation progress JSON",
    )
    parser.add_argument(
        "--search-level",
        type=int,
        choices=RESOURCE_LEVELS,
        help="known active level from controller context for a found resource",
    )
    parser.add_argument(
        "--emulator",
        metavar="NAME",
        help=(
            "emulator family to drive: "
            + ", ".join(family.key for family in families())
            + " (default: mumu)"
        ),
    )
    parser.add_argument(
        "--emulator-index",
        type=int,
        metavar="N",
        help="emulator instance index to drive (default: the running one)",
    )
    parser.add_argument(
        "--adb-serial",
        metavar="SERIAL",
        help="ADB device serial to use (default: the connected emulator)",
    )
    parser.add_argument(
        "--adb-path",
        metavar="PATH",
        help="path to the adb executable (default: MuMu's bundled adb)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="launch the graphical user interface (ROKauto Control Center)",
    )
    parser.add_argument(
        "--show-console",
        action="store_true",
        help="keep the terminal console visible when launching GUI",
    )
    args = parser.parse_args()

    if args.gui:
        from rok_automation.ui.app import launch_gui
        return launch_gui(hide_console=not args.show_console)

    game = _build_game(args)
    if game is None:
        return 1

    if args.check_stockpiles:
        return _run_stockpile_check(game)

    if args.screenshot is not None or args.detect_screen:
        screenshot_path = args.screenshot or Path("artifacts/mumu.png")
        try:
            output_path = game.screenshot()
        except (OSError, RuntimeError) as exc:
            print(f"Screenshot failed: {exc}")
            return 1
        if args.screenshot is not None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.resolve() != screenshot_path.resolve():
                shutil.copyfile(output_path, screenshot_path)
            output_path = screenshot_path
        print(f"Screenshot saved: {output_path}")
        print(
            "  (normalised to "
            f"{CANONICAL_CAPTURE_SIZE[0]}x{CANONICAL_CAPTURE_SIZE[1]} "
            "for resolution-independent detection)"
        )

        if args.detect_screen:
            try:
                detection = ScreenDetector().detect(
                    output_path,
                    expected_resource_level=args.search_level,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"Screen detection failed: {exc}")
                return 1
            level = ""
            if detection.state.value in {"resource_search", "resource_found"}:
                level_value = (
                    str(detection.resource_level)
                    if detection.resource_level is not None
                    else "unknown"
                )
                level = f", level={level_value}"
            route = ""
            if detection.green_route_detected is not None:
                route_state = (
                    "detected" if detection.green_route_detected else "not_detected"
                )
                route_confidence = (
                    detection.green_route_confidence
                    if detection.green_route_confidence is not None
                    else 0.0
                )
                route = (
                    f", green_route={route_state}"
                    f" (confidence={route_confidence:.3f})"
                )
            coordinates = ""
            if detection.resource_coordinates is not None:
                x, y = detection.resource_coordinates
                coordinates = f", resource_coordinates=({x},{y})"
            print(
                f"Screen: {detection.state.value}{level}{route}{coordinates} "
                f"(confidence={detection.confidence:.3f}, evidence={detection.evidence})"
            )
            if detection.state.value == "world_map":
                account_name = detection.account_name or "not recognized"
                resource_name = _RESOURCE_DISPLAY_NAMES.get(
                    detection.preferred_resource,
                    "unknown (no recognized resource keyword)",
                )
                print(
                    f"Account: {account_name}; preferred resource: {resource_name}"
                )
                if detection.armies_out is not None:
                    print(f"Armies already marching: {detection.armies_out}/5")
                else:
                    print("Armies already marching: not recognized")

    if args.daily_reward_only or (args.daily_reward and not args.farm):
        from rok_automation.automation.daily_reward import DailyRewardClaimer
        claimer = DailyRewardClaimer(game)
        success = claimer.claim_all()
        return 0 if success else 1

    if args.farm:
        # A stale pause flag from an earlier run must not freeze this one.
        clear_paused()
        try:
            controller = FarmingController(
                game,
                daily_rewards=args.daily_reward,
                switch_gmail=not args.no_switch_gmail,
                switch_rok_accounts=not args.current_rok_only,
                gmail_state_path=args.gmail_state,
            )
            context = controller.run()
        except KeyboardInterrupt:
            print("\n[!] Force stop requested by user.")
            return 1
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Farming stopped safely: {exc}")
            return 1
        if context.last_error:
            print(
                f"Farming stopped after {context.armies_dispatched} armies: "
                f"{context.last_error}"
            )
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
