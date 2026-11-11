"""Command-line entry point for emulator detection and screenshot capture."""

import argparse
import sys
from pathlib import Path

from rok_automation.automation.controller import FarmingController
from rok_automation.game.ldplayer import (
    capture_ldplayer_window,
    describe_window_tree,
    find_ldplayer_window,
)
from rok_automation.game.interface import LDPlayerInterface
from rok_automation.vision.screen import ScreenDetector
from rok_automation.logic.resource_selector import RESOURCE_LEVELS, Resource

_RESOURCE_DISPLAY_NAMES = {
    Resource.GOLD: "Gold (Vàng)",
    Resource.STONE: "Stone (Đá)",
    Resource.FOOD: "Food (Ngô)",
    Resource.WOOD: "Wood (Gỗ)",
}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Rise of Kingdoms emulator tools")
    parser.add_argument(
        "--screenshot",
        nargs="?",
        const=Path("artifacts/ldplayer.png"),
        type=Path,
        metavar="PATH",
        help="capture the visible LDPlayer game area (default: artifacts/ldplayer.png)",
    )
    parser.add_argument(
        "--diagnose-window",
        action="store_true",
        help="list the LDPlayer window and child-window bounds",
    )
    parser.add_argument(
        "--detect-screen",
        action="store_true",
        help="capture LDPlayer and classify the visible game screen",
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
        "--reset-gmail-run",
        action="store_true",
        help="clear saved Gmail rotation progress and start a fresh full run",
    )
    parser.add_argument(
        "--gmail-state",
        type=Path,
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

    window = find_ldplayer_window()
    if window is None:
        print("Emulator: Disconnected (no matching visible LDPlayer window found)")
        return 1

    print(f"Emulator: Connected — {window.title} (handle={window.handle})")

    if args.diagnose_window:
        for description in describe_window_tree(window):
            print(description)

    if args.screenshot is not None or args.detect_screen:
        screenshot_path = args.screenshot or Path("artifacts/ldplayer.png")
        try:
            output_path = capture_ldplayer_window(window, screenshot_path)
        except (OSError, RuntimeError) as exc:
            print(f"Screenshot failed: {exc}")
            return 1
        print(f"Screenshot saved: {output_path}")

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
        claimer = DailyRewardClaimer(LDPlayerInterface(window))
        success = claimer.claim_all()
        return 0 if success else 1

    if args.farm:
        try:
            controller = FarmingController(
                LDPlayerInterface(window),
                daily_rewards=args.daily_reward,
                switch_gmail=not args.no_switch_gmail,
                reset_gmail_run=args.reset_gmail_run,
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
