"""Finite-state controller for searching and dispatching five resource armies."""

from __future__ import annotations

import json
import random
import time
import unicodedata
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2

from rok_automation.automation.context import FarmContext
from rok_automation.automation.account_switcher import (
    AccountSwitcher,
    AccountTraversal,
)
from rok_automation.automation.gmail_switcher import (
    DEFAULT_GMAIL_STATE_PATH,
    GmailRotation,
    GmailSwitcher,
)
from rok_automation.automation.states import FarmState
from rok_automation.game.interface import GameInterface
from rok_automation.logic.resource_selector import (
    RESOURCE_LEVELS,
    Resource,
    SearchTarget,
    account_name_without_guild,
    calculate_farmed_resources,
    format_resource_amount,
    random_preferred_resource,
    resource_for_account,
    resource_node_yield,
    search_plan,
)
from rok_automation.vision.account_label import read_text_region
from rok_automation.vision.screen import (
    ScreenDetection,
    ScreenDetector,
    ScreenState,
    load_color_frame,
    load_gray_frame,
)


class FarmingError(RuntimeError):
    """Raised when the current screen cannot be handled safely."""


DEFAULT_SESSION_SUMMARY_PATH = Path("state/session_summary.json")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SEARCH_TEMPLATES = _PROJECT_ROOT / "templates/search"
_GATHERING_TEMPLATES = _PROJECT_ROOT / "templates/gathering"
_NAVIGATION_TEMPLATES = _PROJECT_ROOT / "templates/navigation"
_SEARCH_SETTLE_SECONDS = 0.85
_SEARCH_SUBMIT_DELAY = 0.04
_SEARCH_MENU_READY_TIMEOUT = 2.5
_CONTROL_READY_TIMEOUT = 2.5
_LEVEL_REPEAT_PAUSE = 0.08
_LEVEL_SETTLE_DELAY = 0.12
_MAX_ACTION_ATTEMPTS = 5
_ACTION_RETRY_DELAY = 0.20
_UNCHANGED_SEARCH_FRAMES = 3
_RESOURCE_BUTTONS = {
    Resource.GOLD: _SEARCH_TEMPLATES / "resource_gold.png",
    Resource.STONE: _SEARCH_TEMPLATES / "resource_stone.png",
    Resource.FOOD: _SEARCH_TEMPLATES / "resource_food.png",
    Resource.WOOD: _SEARCH_TEMPLATES / "resource_wood.png",
}
# Gather flow: each screen is recognised by its button, and the button that
# belongs to the recognised screen is the one pressed next.
_DISPATCH_BUTTONS = {
    ScreenState.RESOURCE_FOUND: "gather_button",
    ScreenState.NEW_TROOPS_PROMPT: "new_troops_button",
    ScreenState.TROOP_COMPOSITION: "march_button",
    ScreenState.RESOURCE_SEARCH: "submit_button",
}
_DISPATCH_TEMPLATE_DIRS = {
    ScreenState.RESOURCE_FOUND: _GATHERING_TEMPLATES,
    ScreenState.NEW_TROOPS_PROMPT: _GATHERING_TEMPLATES,
    ScreenState.TROOP_COMPOSITION: _GATHERING_TEMPLATES,
    ScreenState.RESOURCE_SEARCH: _SEARCH_TEMPLATES,
}
# Diacritic-free, space-free OCR labels of each button (including RapidOCR's
# occasional dropped-vowel variants such as "Quân mi" -> "quanmi").
_DISPATCH_BUTTON_TEXT = {
    ScreenState.RESOURCE_FOUND: ("thuthap", "thuth"),
    ScreenState.NEW_TROOPS_PROMPT: ("quanmoi", "quanmi", "quanm"),
    ScreenState.TROOP_COMPOSITION: ("hanhquan", "hanhq"),
    ScreenState.RESOURCE_SEARCH: ("timkiem", "timk"),
}
_DISPATCH_BUTTON_CONFIDENCE = 0.78
_DISPATCH_AMBIGUITY_MARGIN = 0.12
_DISPATCH_TIMEOUT = 20.0
_STEP_CHANGE_TIMEOUT = 0.70
_STEP_SETTLE_DELAY = 0.05
_FALLBACK_CONFIRM_SECONDS = 0.45
_NEXT_DISPATCH_STATE: dict[ScreenState, ScreenState] = {
    ScreenState.RESOURCE_FOUND: ScreenState.NEW_TROOPS_PROMPT,
    ScreenState.NEW_TROOPS_PROMPT: ScreenState.TROOP_COMPOSITION,
    ScreenState.TROOP_COMPOSITION: ScreenState.WORLD_MAP,
}
# Recovery: how many failed steps / unreadable screens one army may hit
# before the run stops safely.
_MAX_RECOVERY_ATTEMPTS = 5
_MAX_UNKNOWN_READS = 8
_UNKNOWN_SCREEN_WAIT = 0.5
_MAX_ARMY_STEPS = 80
_SCREEN_READ_TIMEOUT = 3.0
_CITY_TO_MAP_TIMEOUT = 6.0
_DIRECT_MAP_SETTLE_DELAY = 1.0
# The City map button is fixed at the lower-left corner of the game view.
_NORM_CITY_MAP_BUTTON = (0.008, 0.825, 0.085, 0.165)


class FarmingController:
    """Coordinate game input, screen recognition, and resource search policy."""

    def __init__(
        self,
        game: GameInterface,
        *,
        detector: ScreenDetector | None = None,
        max_armies: int = 5,
        rng: random.Random | None = None,
        log=print,
        daily_rewards: bool = False,
        switch_gmail: bool = True,
        switch_rok_accounts: bool = True,
        gmail_state_path: Path | None = DEFAULT_GMAIL_STATE_PATH,
        reset_gmail_run: bool = False,
        session_summary_path: Path | None = DEFAULT_SESSION_SUMMARY_PATH,
    ) -> None:
        if not 1 <= max_armies <= 5:
            raise ValueError("max_armies must be between 1 and 5")
        self.game = game
        self.detector = detector or ScreenDetector()
        self.max_armies = max_armies
        self.rng = rng or random.Random()
        self.log = log
        self.daily_rewards = daily_rewards
        self.switch_gmail = switch_gmail
        self.switch_rok_accounts = switch_rok_accounts
        self.gmail_state_path = gmail_state_path
        self.reset_gmail_run = reset_gmail_run
        self.session_summary_path = (
            session_summary_path or DEFAULT_SESSION_SUMMARY_PATH
        )
        self.context = FarmContext()
        self._selected_resource: Resource | None = None
        self._resource_levels: dict[Resource, int] = {}
        self.session_targets: list[SearchTarget] = []

    # Screen-wait budgets scale with how slow a capture is, so the ADB
    # backend (about 350 ms per frame) still gets the same number of
    # samples of a transition as the desktop backend (about 21 ms).
    def _budget(self, seconds: float) -> float:
        return self.game.timing.scaled(seconds)

    def run(self) -> FarmContext:
        """Farm the configured RoK characters across the configured Gmail scope.

        Characters of the current Gmail are farmed first; then the Gmail
        switcher logs into the bottom-most Gmail in the account popup and the
        cycle repeats until the rotation returns to the run's first Gmail.
        """

        rotation: GmailRotation | None = None
        if self.switch_gmail:
            rotation = GmailRotation.load(
                self.gmail_state_path,
                reset=self.reset_gmail_run,
            )
            if rotation.is_resumed:
                self._log(
                    f"Resuming Gmail run started from {rotation.first.label()}; "
                    f"already farmed: "
                    + ", ".join(identity.label() for identity in rotation.farmed)
                )
            else:
                self._log("Starting a new Gmail run")

        total_dispatched = 0
        total_characters = 0
        gmail_accounts = 0
        self._save_session_summary(status="running")
        try:
            while True:
                context, characters, dispatched = self._farm_linked_characters()
                total_dispatched += dispatched
                total_characters += characters
                if context.last_error:
                    context.armies_dispatched = total_dispatched
                    context.dispatched_targets = list(self.session_targets)
                    self._save_session_summary(status="error")
                    if self.session_targets:
                        self._log_resource_summary(
                            total_characters=total_characters,
                            gmail_accounts=gmail_accounts or 1,
                        )
                    return context
                gmail_accounts += 1

                if rotation is None:
                    context.armies_dispatched = total_dispatched
                    context.dispatched_targets = list(self.session_targets)
                    self._save_session_summary(status="complete")
                    self._transition(
                        FarmState.COMPLETE,
                        f"Finished {characters} RoK account(s) linked to this Gmail account",
                    )
                    self._log_resource_summary(
                        total_characters=total_characters,
                        gmail_accounts=1,
                    )
                    return context

                self._transition(
                    FarmState.SWITCH_GMAIL,
                    f"Finished {characters} RoK account(s) in this Gmail; "
                    "switching Gmail account",
                )
                result = GmailSwitcher(
                    self.game,
                    self.detector,
                    log=self._log,
                ).switch_to_next(rotation)
                if result.run_complete:
                    context.armies_dispatched = total_dispatched
                    context.dispatched_targets = list(self.session_targets)
                    context.last_error = None
                    self._save_session_summary(status="complete")
                    self._transition(
                        FarmState.COMPLETE,
                        f"Gmail run complete: {len(rotation.farmed)} Gmail account(s) "
                        f"farmed in total; {total_characters} RoK account(s) and "
                        f"{total_dispatched} armies this session",
                    )
                    self._log_resource_summary(
                        total_characters=total_characters,
                        gmail_accounts=len(rotation.farmed),
                    )
                    return context
                self._log(
                    f"Switched to Gmail {result.identity.label()}; "
                    f"farming its RoK accounts ({gmail_accounts + 1} Gmail "
                    "account(s) this session)"
                )
        except KeyboardInterrupt:
            self._log("Force stop requested by user (KeyboardInterrupt).")
            self.context.armies_dispatched = total_dispatched
            self.context.dispatched_targets = list(self.session_targets)
            self.context.last_error = "Force stopped by user"
            self._save_session_summary(status="stopped_by_user")
            if self.session_targets:
                self._log_resource_summary(
                    total_characters=total_characters,
                    gmail_accounts=gmail_accounts or 1,
                    stopped_early=True,
                )
            else:
                self._log("No resource armies were dispatched before force stop.")
            return self.context

    def _farm_linked_characters(self) -> tuple[FarmContext, int, int]:
        """Farm each RoK character in the current Gmail, then stop at the end.

        Returns the last context, the number of characters farmed, and the
        number of armies dispatched.
        """

        if not self.switch_rok_accounts:
            self.context = FarmContext()
            context = self._run_one_account()
            total_dispatched = context.armies_dispatched
            exhausted_resources = (
                context.last_error
                == "No available resource location found in the search plan"
            )
            if context.last_error and not exhausted_resources:
                return context, 0, total_dispatched
            if exhausted_resources:
                self._log(
                    f"No resource location was available for {context.account_name}; "
                    "finishing the current RoK account"
                )
                context.last_error = None
            self._log(f"Finished farming {context.account_name}; current RoK account only")
            return context, 1, total_dispatched

        traversal = AccountTraversal()
        farmed_accounts = 0
        total_dispatched = 0
        expected_account_name: str | None = None
        while True:
            self.context = FarmContext()
            context = self._run_one_account(
                expected_account_name=expected_account_name,
            )
            total_dispatched += context.armies_dispatched
            exhausted_resources = (
                context.last_error
                == "No available resource location found in the search plan"
            )
            if context.last_error and not exhausted_resources:
                return context, farmed_accounts, total_dispatched
            if exhausted_resources:
                self._log(
                    f"No resource location was available for {context.account_name}; "
                    "moving to the next RoK account"
                )
                context.last_error = None

            farmed_accounts += 1
            self._log(
                f"Finished farming {context.account_name}; "
                f"{farmed_accounts} account(s) farmed this run"
            )

            if not traversal.has_more_accounts():
                context.last_error = None
                return context, farmed_accounts, total_dispatched

            next_name = AccountSwitcher(
                self.game,
                self.detector,
                log=self._log,
            ).switch_to_next(traversal)
            if next_name is None:
                context.last_error = None
                return context, farmed_accounts, total_dispatched
            expected_account_name = next_name
            self._log(f"Selected {next_name}; starting its farming run")

    def _open_world_map_directly(self) -> tuple[Path, ScreenDetection]:
        """Press the City map button without classifying the current view first."""

        screenshot = self.game.screenshot()
        frame = load_gray_frame(screenshot)
        if frame is None:
            raise FarmingError("Could not read the game frame before opening the world map")
        height, width = frame.shape[:2]
        map_bounds = (
            round(_NORM_CITY_MAP_BUTTON[0] * width),
            round(_NORM_CITY_MAP_BUTTON[1] * height),
            round(_NORM_CITY_MAP_BUTTON[2] * width),
            round(_NORM_CITY_MAP_BUTTON[3] * height),
        )
        self._transition(FarmState.OPEN_WORLD_MAP, "Opening the world map")
        self._log("Pressing the City map button directly")
        self.game.click(map_bounds, screenshot)
        self.game.wait(_DIRECT_MAP_SETTLE_DELAY)

        screenshot, detection = self._read_screen()
        for _attempt in range(_MAX_ACTION_ATTEMPTS - 1):
            if detection.state is not ScreenState.UNKNOWN:
                break
            self.game.wait(_ACTION_RETRY_DELAY)
            screenshot, detection = self._read_screen()
        return screenshot, detection

    def _run_one_account(
        self,
        *,
        expected_account_name: str | None = None,
    ) -> FarmContext:
        self._transition(FarmState.DETECT_ACCOUNT)
        if self.daily_rewards:
            self._transition(
                FarmState.CLAIM_DAILY_REWARD,
                "Claiming daily VIP and campaign rewards",
            )
            from rok_automation.automation.daily_reward import DailyRewardClaimer

            claimer = DailyRewardClaimer(
                self.game,
                self.detector,
                log=self._log,
            )
            claimer.claim_all()
            self.game.wait(1.5)
            screenshot, detection = self._open_world_map_directly()
        elif expected_account_name is None:
            screenshot, detection = self._open_world_map_directly()
        else:
            screenshot, detection = self._read_screen()
            for _attempt in range(_MAX_ACTION_ATTEMPTS - 1):
                if detection.state is not ScreenState.UNKNOWN:
                    break
                self.game.wait(_ACTION_RETRY_DELAY)
                screenshot, detection = self._read_screen()

        if detection.state is ScreenState.CITY:
            for _attempt in range(_MAX_ACTION_ATTEMPTS):
                self._transition(FarmState.OPEN_WORLD_MAP, "Opening the world map")
                self._press_and_wait(
                    screenshot,
                    detection,
                    _NAVIGATION_TEMPLATES / "city_map_button.png",
                    confidence_threshold=0.68,
                    change_timeout=self._budget(_CITY_TO_MAP_TIMEOUT),
                )
                screenshot, detection = self._read_screen()
                if detection.state is not ScreenState.CITY:
                    break
                self.game.wait(_ACTION_RETRY_DELAY)

        world_flow_states = {
            ScreenState.WORLD_MAP,
            ScreenState.RESOURCE_SEARCH,
            ScreenState.RESOURCE_FOUND,
            ScreenState.NEW_TROOPS_PROMPT,
            ScreenState.TROOP_COMPOSITION,
        }
        if detection.state not in world_flow_states:
            raise FarmingError(
                f"Start farming from the city or world map; detected {detection.state.value}"
            )

        world_detection = self.detector.detect(
            screenshot,
            candidate_states={ScreenState.WORLD_MAP},
        )
        for _attempt in range(_MAX_ACTION_ATTEMPTS - 1):
            if (
                world_detection.state is ScreenState.WORLD_MAP
                and world_detection.armies_out is not None
            ):
                break
            if detection.state is not ScreenState.WORLD_MAP:
                break
            self.game.wait(_ACTION_RETRY_DELAY)
            screenshot = self.game.screenshot()
            world_detection = self.detector.detect(
                screenshot,
                candidate_states={ScreenState.WORLD_MAP},
            )

        if world_detection.state is ScreenState.WORLD_MAP:
            self.context.account_name = world_detection.account_name
            self.context.preferred_resource = world_detection.preferred_resource
            armies_out = world_detection.armies_out
        else:
            # Started mid-flow (e.g. search menu, gather prompt, or march screen
            # already open): still read the account label/army count if visible,
            # and default armies_out to 0 so _farm_one_army continues from the
            # current screen state.
            color_frame = cv2.imread(str(screenshot), cv2.IMREAD_COLOR)
            if color_frame is not None:
                from rok_automation.vision.account_label import (
                    read_account_label,
                    read_army_count,
                )

                account_name, preferred_resource = read_account_label(color_frame)
                self.context.account_name = account_name
                self.context.preferred_resource = preferred_resource
                armies_out = read_army_count(color_frame)
                if armies_out is None:
                    armies_out = 0
            else:
                armies_out = 0
        if expected_account_name:
            if not self.context.account_name:
                self.context.account_name = (
                    account_name_without_guild(expected_account_name)
                    or expected_account_name
                )
            if self.context.preferred_resource is None:
                self.context.preferred_resource = resource_for_account(
                    expected_account_name
                )
        if not self.context.account_name:
            self.context.account_name = "current account"
        if self.context.preferred_resource is None:
            self.context.preferred_resource = random_preferred_resource(
                self.rng
            )
            self._log(
                f"No resource keyword in {self.context.account_name!r}; "
                f"randomly selected {self.context.preferred_resource.value} "
                "(Gold weighted higher)"
            )
        if armies_out is None:
            raise FarmingError(
                "Could not read the world-map troop counter (x/5); "
                "not dispatching without knowing the available army slots"
            )
        self.context.starting_armies_out = armies_out
        dispatch_limit = min(
            self.max_armies,
            5 - self.context.starting_armies_out,
        )

        self._log(
            f"Account: {self.context.account_name}; preferred resource: "
            f"{self.context.preferred_resource.value}"
        )
        self._log(
            f"Armies already marching: {self.context.starting_armies_out}/5; "
            f"available slots: {5 - self.context.starting_armies_out}"
        )
        if dispatch_limit == 0:
            self._transition(
                FarmState.COMPLETE,
                "All 5 army slots are already in use; nothing to dispatch",
            )
            return self.context

        # The search dialog retains its resource choice after it is closed.
        # Remember that choice for this run and only click a different type.
        self._selected_resource = None
        self._resource_levels = {}
        # A failed level stays unavailable for later armies in this run.
        highest_candidate_level = {
            resource: RESOURCE_LEVELS[0] for resource in Resource
        }
        seen_resource_coordinates: set[tuple[int, int]] = set()

        for army_number in range(1, dispatch_limit + 1):
            total_army_number = self.context.starting_armies_out + army_number
            dispatched = self._farm_one_army(
                total_army_number,
                highest_candidate_level,
                seen_resource_coordinates,
            )
            if not dispatched:
                self.context.last_error = (
                    "No available resource location found in the search plan"
                )
                self._transition(
                    FarmState.STOPPED,
                    "No available resource location found for any supported "
                    "resource at levels 8–5",
                )
                return self.context

        acc_total = self.context.grand_total_farmed
        yield_str = f" (+{acc_total:,} resources)" if acc_total > 0 else ""
        self._transition(
            FarmState.COMPLETE,
            f"Dispatched {self.context.armies_dispatched} new armies{yield_str}; "
            f"started with {self.context.starting_armies_out}/5 already marching",
        )
        return self.context

    def _farm_one_army(
        self,
        army_number: int,
        highest_candidate_level: dict[Resource, int],
        seen_resource_coordinates: set[tuple[int, int]],
    ) -> bool:
        """Send one army by following the farming flow from whatever screen is up.

        Flow: city → (map button) → world map → (search button) → search menu
        → (resource, level, Tìm kiếm) → resource found → (Thu thập) →
        Quân mới → Hành quân → world map.

        Every iteration first identifies the current screen and then performs
        only the step that belongs to that screen. If a step fails (missed
        click, timeout, unexpected screen), the next iteration re-identifies
        the screen and continues from there instead of stopping.

        Returns ``True`` when the army was dispatched and ``False`` when no
        target in the search plan had an available node.
        """

        target_list: list[SearchTarget] = list(search_plan(
            self.context.preferred_resource,
            rng=self.rng,
        ))
        target_index = 0
        pending_result: ScreenDetection | None = None
        pending_screen: tuple[Path, ScreenDetection] | None = None
        recoveries = 0
        unknown_reads = 0
        last_error = "none"

        def current_target() -> SearchTarget:
            return target_list[min(target_index, len(target_list) - 1)]

        def skip_unavailable_targets() -> None:
            nonlocal target_index
            while (
                target_index < len(target_list)
                and target_list[target_index].level
                > highest_candidate_level[target_list[target_index].resource]
            ):
                target_index += 1

        for _step in range(_MAX_ARMY_STEPS):
            if recoveries > _MAX_RECOVERY_ATTEMPTS:
                raise FarmingError(
                    f"Could not recover the farming flow for army {army_number}/5 "
                    f"after {_MAX_RECOVERY_ATTEMPTS} attempts; last error: {last_error}"
                )
            try:
                if pending_result is not None:
                    # A search just opened a result; handle it directly.
                    screenshot, screen = None, pending_result
                elif pending_screen is not None:
                    screenshot, screen = pending_screen
                    pending_screen = None
                else:
                    screenshot, screen = self._read_screen()
                state = screen.state

                if state is ScreenState.UNKNOWN:
                    unknown_reads += 1
                    if unknown_reads > _MAX_UNKNOWN_READS:
                        raise FarmingError(
                            "Could not recognise the current screen "
                            f"({screen.evidence}); stopping safely"
                        )
                    self._log("Waiting for a recognisable screen…")
                    self.game.wait(_UNKNOWN_SCREEN_WAIT)
                    continue
                unknown_reads = 0

                if state is ScreenState.CITY:
                    self._transition(FarmState.OPEN_WORLD_MAP, "Opening the world map")
                    pending_screen = self._press_and_wait(
                        screenshot,
                        screen,
                        _NAVIGATION_TEMPLATES / "city_map_button.png",
                        confidence_threshold=0.68,
                        change_timeout=self._budget(_CITY_TO_MAP_TIMEOUT),
                    )
                    continue

                if state is ScreenState.WORLD_MAP:
                    self._transition(FarmState.OPEN_SEARCH, "Opening resource search")
                    pending_screen = self._press_and_wait(
                        screenshot,
                        screen,
                        _NAVIGATION_TEMPLATES / "world_search_button.png",
                        confidence_threshold=0.65,
                        change_timeout=1.5,
                    )
                    continue

                if state is ScreenState.RESOURCE_SEARCH:
                    # Guard: If gather_button (THU THẬP) is already visible on screen
                    # (e.g. from an earlier search pan), handle it as RESOURCE_FOUND immediately.
                    if screenshot is not None:
                        gather_hit = self.detector.locate_template(
                            screenshot,
                            _GATHERING_TEMPLATES / "gather_button.png",
                            confidence_threshold=0.75,
                        )
                        if gather_hit is not None:
                            pending_result = self.detector.detect(
                                screenshot,
                                read_account=False,
                                candidate_states={ScreenState.RESOURCE_FOUND},
                            )
                            continue

                    skip_unavailable_targets()
                    if target_index >= len(target_list):
                        return False
                    target = target_list[target_index]
                    self.context.current_target = target
                    self._transition(
                        FarmState.SELECT_TARGET,
                        f"Army {army_number}/5: selecting "
                        f"{target.resource.value} Lv{target.level}",
                    )
                    # Determine which resource tab is currently active on screen.
                    active_resource = screen.preferred_resource
                    if active_resource is None and screen.bounds is not None and screenshot is not None:
                        frame = load_gray_frame(screenshot)
                        if frame is not None:
                            res = ScreenDetector.identify_active_search_resource(
                                screen.bounds, frame.shape[1]
                            )
                            if isinstance(res, Resource):
                                active_resource = res

                    if active_resource == target.resource:
                        # Desired resource is already selected and dialog is open.
                        # Do NOT click the icon again, as that toggles the box closed.
                        self._selected_resource = target.resource
                    else:
                        # Dialog is on a different tab (or closed); click to switch/open.
                        self._click_template(
                            _RESOURCE_BUTTONS[target.resource],
                            expected_state=ScreenState.RESOURCE_SEARCH,
                            confidence_threshold=0.62,
                            initial_screenshot=screenshot,
                        )
                        self._selected_resource = target.resource
                        screenshot = None
                    screenshot = self._set_search_level(
                        target.level,
                        initial_screenshot=screenshot,
                    )

                    self._transition(
                        FarmState.SEARCH_RESOURCE,
                        f"Searching {target.resource.value} Lv{target.level}",
                    )
                    if screenshot is None:
                        self.game.wait(_SEARCH_SUBMIT_DELAY)
                    before_search_path = self._click_template(
                        _SEARCH_TEMPLATES / "submit_button.png",
                        expected_state=ScreenState.RESOURCE_SEARCH,
                        initial_screenshot=screenshot,
                    )
                    before_search = load_color_frame(before_search_path)
                    if before_search is None:
                        raise FarmingError("Could not read the pre-search screenshot")
                    _result_path, result = self._wait_for_search_result(before_search)
                    self._transition(FarmState.PROCESS_RESULT)

                    if result.state is ScreenState.NO_RESULT:
                        highest_candidate_level[target.resource] = min(
                            highest_candidate_level[target.resource],
                            target.level - 1,
                        )
                        self._log(
                            f"No {target.resource.value} Lv{target.level} found; "
                            f"remembering Lv{highest_candidate_level[target.resource]} "
                            "as the highest level to try next"
                        )
                        target_index += 1
                        continue
                    pending_result = result
                    continue

                if state is ScreenState.RESOURCE_FOUND:
                    target = current_target()
                    if pending_result is not None:
                        result = pending_result
                        pending_result = None
                    else:
                        # Reached the result panel through recovery: re-read
                        # its coordinates and route before deciding.
                        self._log("Resource panel is open; continuing from Thu thập")
                        result = self.detector.detect(
                            screenshot,
                            read_account=False,
                            candidate_states={ScreenState.RESOURCE_FOUND},
                        )
                    coordinates = result.resource_coordinates
                    if coordinates is not None:
                        self._log(
                            f"Resource location: X:{coordinates[0]} Y:{coordinates[1]}"
                        )
                    duplicate_coordinates = next(
                        (
                            prior
                            for prior in seen_resource_coordinates
                            if coordinates is not None
                            and max(
                                abs(coordinates[0] - prior[0]),
                                abs(coordinates[1] - prior[1]),
                            ) <= 2
                        ),
                        None,
                    )
                    skip_reason: str | None = None
                    if duplicate_coordinates is not None:
                        # Search can return the same node after another army
                        # has already been assigned there, even when its
                        # route is not visible in this particular frame.
                        skip_reason = (
                            f"Already handled nearby resource at "
                            f"X:{coordinates[0]} Y:{coordinates[1]} "
                            f"(previously X:{duplicate_coordinates[0]} "
                            f"Y:{duplicate_coordinates[1]})"
                        )
                    elif result.green_route_detected:
                        if coordinates is not None:
                            seen_resource_coordinates.add(coordinates)
                        skip_reason = (
                            "Green incoming march route at this location "
                            f"(confidence={result.green_route_confidence or 0.0:.3f})"
                        )
                    if skip_reason is not None:
                        highest_candidate_level[target.resource] = min(
                            highest_candidate_level[target.resource],
                            target.level - 1,
                        )
                        self._log(
                            f"{skip_reason}; skipping {target.resource.value} "
                            f"Lv{target.level} and remembering a maximum level of "
                            f"{highest_candidate_level[target.resource]}"
                        )
                        target_index += 1
                        self._transition(FarmState.OPEN_SEARCH, "Opening resource search")
                        fresh_path = self.game.screenshot()
                        pending_screen = self._press_and_wait(
                            fresh_path,
                            result,
                            _NAVIGATION_TEMPLATES / "world_search_button.png",
                            confidence_threshold=0.65,
                            change_timeout=1.5,
                        )
                        continue

                    if self._dispatch_army(target, army_number):
                        if coordinates is not None:
                            seen_resource_coordinates.add(coordinates)
                        return True
                    recoveries += 1
                    last_error = "gather flow fell back before Hành quân"
                    continue

                if state in {
                    ScreenState.NEW_TROOPS_PROMPT,
                    ScreenState.TROOP_COMPOSITION,
                }:
                    self._log(
                        f"On the {state.value} screen; continuing the gather flow"
                    )
                    if self._dispatch_army(current_target(), army_number):
                        return True
                    recoveries += 1
                    last_error = "gather flow fell back before Hành quân"
                    continue

                raise FarmingError(f"No farming step for screen {state.value}")
            except FarmingError as error:
                pending_result = None
                pending_screen = None
                recoveries += 1
                last_error = str(error)
                # The resource choice may or may not have been applied.
                self._selected_resource = None
                self._resource_levels.clear()
                self._log(
                    f"Step failed ({error}); checking which screen is showing "
                    f"and continuing from there ({recoveries}/{_MAX_RECOVERY_ATTEMPTS})"
                )
                self.game.wait(_ACTION_RETRY_DELAY)

        raise FarmingError(
            f"Army {army_number}/5 did not finish within {_MAX_ARMY_STEPS} steps; "
            f"last error: {last_error}"
        )

    def _read_screen(
        self,
        *,
        expected_states: tuple[ScreenState, ...] | None = None,
    ) -> tuple[Path, ScreenDetection]:
        """Identify the current farming screen using fast ROI/pinned checks."""

        deadline = time.monotonic() + self._budget(_SCREEN_READ_TIMEOUT)
        previous: ScreenDetection | None = None
        screenshot = self.game.screenshot()
        detection = ScreenDetection(ScreenState.UNKNOWN, 0.0, "no capture yet")
        while True:
            detection, ambiguous = self._classify_dispatch_screen(
                screenshot,
                expected_states=expected_states,
            )
            if (
                detection.state is not ScreenState.UNKNOWN
                and not ambiguous
                and detection.confidence >= 0.80
            ):
                return screenshot, detection
            stable = (
                previous is not None
                and previous.state is detection.state
                and self._bounds_close(previous.bounds, detection.bounds)
            )
            previous = detection
            if stable and detection.state is not ScreenState.UNKNOWN:
                if not ambiguous:
                    return screenshot, detection
                ocr_state = self._read_button_state(screenshot, detection.bounds)
                if ocr_state is not None:
                    return screenshot, replace(detection, state=ocr_state)
                return screenshot, detection
            if time.monotonic() >= deadline:
                return screenshot, detection
            self.game.wait(self.game.timing.poll_interval)
            screenshot = self.game.screenshot()

    def _press_and_wait(
        self,
        screenshot: Path | None,
        screen: ScreenDetection,
        template_path: Path,
        *,
        confidence_threshold: float,
        change_timeout: float | None = None,
    ) -> tuple[Path, ScreenDetection] | None:
        """Click a navigation button and wait until the screen moves on."""

        if change_timeout is None:
            change_timeout = self._budget(_STEP_CHANGE_TIMEOUT)
        if screenshot is None:
            screenshot = self.game.screenshot()
        hit = self.detector.locate_template(
            screenshot,
            template_path,
            confidence_threshold=confidence_threshold,
            context_key=self._selected_resource,
        )
        if hit is None:
            raise FarmingError(
                f"Could not locate {template_path.name} on the "
                f"{screen.state.value} screen"
            )
        self._log(f"Clicking {template_path.stem} (match={hit.confidence:.3f})")
        self.game.click(hit.bounds, screenshot)
        next_expected = (
            (ScreenState.RESOURCE_SEARCH,)
            if template_path.name == "world_search_button.png"
            else (ScreenState.WORLD_MAP,)
            if template_path.name == "city_map_button.png"
            else None
        )
        changed = self._wait_for_dispatch_change(
            screen.state,
            hit.bounds,
            timeout=change_timeout,
            expected_states=next_expected,
        )
        if changed is not None:
            next_screenshot, next_detection, ambiguous = changed
            if not ambiguous and next_detection.confidence >= 0.80:
                return next_screenshot, next_detection
        return None

    def _set_search_level(
        self,
        desired_level: int,
        *,
        initial_screenshot: Path | None = None,
    ) -> Path | None:
        if desired_level not in RESOURCE_LEVELS:
            raise FarmingError(f"Unsupported resource level: {desired_level}")

        resource = self._selected_resource
        cached_level = (
            self._resource_levels.get(resource)
            if resource is not None
            else None
        )
        if cached_level == desired_level:
            return initial_screenshot

        if cached_level is not None and resource is not None:
            direction = "plus" if cached_level < desired_level else "minus"
            click_count = abs(desired_level - cached_level)
            self._log(f"Changing search level {cached_level} → {desired_level}")
            self._click_template(
                _SEARCH_TEMPLATES / f"level_{direction}.png",
                expected_state=ScreenState.RESOURCE_SEARCH,
                confidence_threshold=0.60,
                repeat=click_count,
                initial_screenshot=initial_screenshot,
            )
            self._resource_levels[resource] = desired_level
            return None

        screenshot_hint = initial_screenshot
        for _attempt in range(_MAX_ACTION_ATTEMPTS):
            screenshot, detection = self._wait_for_search_menu_level(
                initial_screenshot=screenshot_hint
            )
            screenshot_hint = None
            current_level = detection.resource_level
            if current_level is None:
                raise FarmingError("Could not read the level shown in the search menu")
            if resource is not None:
                self._resource_levels[resource] = current_level
            if current_level == desired_level:
                return screenshot

            direction = "plus" if current_level < desired_level else "minus"
            click_count = abs(desired_level - current_level)
            self._log(f"Changing search level {current_level} → {desired_level}")
            self._click_template(
                _SEARCH_TEMPLATES / f"level_{direction}.png",
                expected_state=ScreenState.RESOURCE_SEARCH,
                confidence_threshold=0.60,
                repeat=click_count,
                initial_screenshot=screenshot,
            )
            self.game.wait(_LEVEL_SETTLE_DELAY)

            # Check up to 5 times with a small delay so the slider/label update
            # is detected before deciding another click is needed.
            for _verify in range(_MAX_ACTION_ATTEMPTS):
                verify_screenshot, verify_detection = self._wait_for_search_menu_level()
                if verify_detection.resource_level is not None and resource is not None:
                    self._resource_levels[resource] = verify_detection.resource_level
                if verify_detection.resource_level == desired_level:
                    return verify_screenshot
                self.game.wait(_ACTION_RETRY_DELAY)

        final_screenshot, final_detection = self._wait_for_search_menu_level()
        if final_detection.resource_level != desired_level:
            raise FarmingError(
                f"Could not set search level {desired_level}; "
                f"menu shows {final_detection.resource_level}"
            )
        if resource is not None:
            self._resource_levels[resource] = desired_level
        return final_screenshot

    def _wait_for_search_menu_level(
        self,
        *,
        initial_screenshot: Path | None = None,
    ) -> tuple[Path, ScreenDetection]:
        """Wait briefly for the resource menu to finish opening or updating."""

        deadline = time.monotonic() + self._budget(_SEARCH_MENU_READY_TIMEOUT)
        last_state = ScreenState.UNKNOWN
        attempts = 0
        next_screenshot = initial_screenshot
        while time.monotonic() < deadline or attempts < _MAX_ACTION_ATTEMPTS:
            attempts += 1
            screenshot = (
                next_screenshot
                if next_screenshot is not None
                else self.game.screenshot()
            )
            next_screenshot = None
            detection = self.detector.detect(
                screenshot,
                read_account=False,
                candidate_states={ScreenState.RESOURCE_SEARCH},
                context_key=self._selected_resource,
            )
            last_state = detection.state
            if (
                detection.state is ScreenState.RESOURCE_SEARCH
                and detection.resource_level is not None
            ):
                return screenshot, detection
            if detection.state not in {
                ScreenState.UNKNOWN,
                ScreenState.RESOURCE_SEARCH,
            }:
                self._require_state(detection, ScreenState.RESOURCE_SEARCH)
            self.game.wait(_ACTION_RETRY_DELAY)

        if last_state is ScreenState.RESOURCE_SEARCH:
            raise FarmingError("Could not read the level shown in the search menu")
        raise FarmingError(
            "Search menu did not become ready; "
            f"last detected screen was {last_state.value}"
        )

    def _dispatch_army(
        self,
        target: SearchTarget,
        army_number: int,
    ) -> bool:
        """Drive Thu thập → Quân mới → Hành quân by re-reading the screen.

        Uses fast ROI/pinned-position checks and checks the expected next step
        in the chain first while preserving full fallback recovery if a click
        misses and lands on another screen.
        """

        self._transition(
            FarmState.SEND_TROOPS,
            f"Found {target.resource.value} Lv{target.level}; opening gather",
        )
        deadline = time.monotonic() + self._budget(_DISPATCH_TIMEOUT)
        clicks = {state: 0 for state in _DISPATCH_BUTTONS}
        marched = False
        fallback_since: float | None = None
        last_state = ScreenState.UNKNOWN
        expected_next: tuple[ScreenState, ...] = (ScreenState.RESOURCE_FOUND,)
        pending_frame: tuple[Path, ScreenDetection, bool] | None = None

        while time.monotonic() < deadline:
            if pending_frame is not None:
                screenshot, detection, ambiguous = pending_frame
                pending_frame = None
            else:
                screenshot = self.game.screenshot()
                detection, ambiguous = self._classify_dispatch_screen(
                    screenshot,
                    expected_states=expected_next,
                )
            last_state = detection.state

            if detection.state is ScreenState.UNKNOWN:
                fallback_since = None
                self.game.wait(self.game.timing.poll_interval)
                continue

            state = detection.state
            if ambiguous:
                ocr_state = self._read_button_state(screenshot, detection.bounds)
                if ocr_state is not None:
                    state = ocr_state

            if state in {
                ScreenState.CITY,
                ScreenState.WORLD_MAP,
                ScreenState.RESOURCE_SEARCH,
            }:
                if marched and state is ScreenState.WORLD_MAP:
                    self.context.armies_dispatched += 1
                    self.context.dispatched_targets.append(target)
                    self.session_targets.append(target)
                    self._save_session_summary(status="running")
                    node_yield = resource_node_yield(target.resource, target.level)
                    self._log(
                        f"Army {army_number}/5 dispatched to "
                        f"{target.resource.value} Lv{target.level} "
                        f"(+{node_yield:,} resources)"
                    )
                    return True
                if not marched:
                    if fallback_since is None:
                        fallback_since = time.monotonic()
                    elif time.monotonic() - fallback_since >= self._budget(
                        _FALLBACK_CONFIRM_SECONDS
                    ):
                        expected = (
                            "Quân mới / Hành quân"
                            if any(clicks.values())
                            else "Thu thập"
                        )
                        self._log(
                            f"Gather flow failed: expected the {expected} screen "
                            f"but landed on {state.value}; continuing from {state.value}"
                        )
                        return False
                self.game.wait(self.game.timing.poll_interval)
                continue
            fallback_since = None

            clicks[state] += 1
            template_name = _DISPATCH_BUTTONS[state]
            if clicks[state] > _MAX_ACTION_ATTEMPTS:
                raise FarmingError(
                    f"{template_name} did not respond after "
                    f"{_MAX_ACTION_ATTEMPTS} clicks"
                )
            if state is ScreenState.TROOP_COMPOSITION and not marched:
                self._transition(FarmState.VERIFY_GATHERING, "Checking the march screen")
            retry_note = f" (retry {clicks[state] - 1})" if clicks[state] > 1 else ""
            self._log(
                f"Clicking {template_name} (match={detection.confidence:.3f})"
                f"{retry_note}"
            )
            self.game.click(detection.bounds, screenshot)
            if state is ScreenState.TROOP_COMPOSITION:
                marched = True
            next_state = _NEXT_DISPATCH_STATE.get(state)
            expected_next = (next_state,) if next_state is not None else ()
            pending_frame = self._wait_for_dispatch_change(
                detection.state,
                detection.bounds,
                expected_states=expected_next or None,
            )

        raise FarmingError(
            f"Timed out sending troops to {target.resource.value} "
            f"Lv{target.level}; last detected screen was {last_state.value}"
        )

    def _classify_dispatch_screen(
        self,
        screenshot: Path,
        *,
        expected_states: tuple[ScreenState, ...] | None = None,
    ) -> tuple[ScreenDetection, bool]:
        """Identify which gather-flow screen is showing.

        Checks ``expected_states`` first so the common happy path only checks
        a single pinned ROI (< 2ms). Falls back to scanning the remaining
        buttons and map HUD if the expected state isn't on screen.
        """

        if expected_states:
            for exp_state in expected_states:
                if exp_state in _DISPATCH_BUTTONS:
                    template_name = _DISPATCH_BUTTONS[exp_state]
                    hit = self.detector.locate_template(
                        screenshot,
                        _DISPATCH_TEMPLATE_DIRS[exp_state] / f"{template_name}.png",
                        confidence_threshold=_DISPATCH_BUTTON_CONFIDENCE,
                        context_key=self._selected_resource,
                    )
                    if hit is not None:
                        active_res = None
                        if exp_state is ScreenState.RESOURCE_SEARCH:
                            frame = load_gray_frame(screenshot)
                            if frame is not None:
                                res = ScreenDetector.identify_active_search_resource(
                                    hit.bounds, frame.shape[1]
                                )
                                if isinstance(res, Resource):
                                    active_res = res
                        return (
                            ScreenDetection(
                                exp_state,
                                hit.confidence,
                                template_name,
                                hit.bounds,
                                preferred_resource=active_res,
                            ),
                            False,
                        )
                elif exp_state is ScreenState.WORLD_MAP:
                    march_hit = self.detector.locate_template(
                        screenshot,
                        _GATHERING_TEMPLATES / "march_button.png",
                        confidence_threshold=_DISPATCH_BUTTON_CONFIDENCE,
                    )
                    if march_hit is None:
                        world_hit = self.detector.locate_template(
                            screenshot,
                            _NAVIGATION_TEMPLATES / "world_search_button.png",
                            confidence_threshold=0.75,
                        )
                        if world_hit is not None:
                            return (
                                ScreenDetection(
                                    ScreenState.WORLD_MAP,
                                    world_hit.confidence,
                                    "world map search button",
                                    world_hit.bounds,
                                ),
                                False,
                            )

        hits: list[tuple[float, ScreenState, Any, Any]] = []
        for state, template_name in _DISPATCH_BUTTONS.items():
            hit = self.detector.locate_template(
                screenshot,
                _DISPATCH_TEMPLATE_DIRS[state] / f"{template_name}.png",
                confidence_threshold=_DISPATCH_BUTTON_CONFIDENCE,
                context_key=self._selected_resource,
            )
            if hit is not None:
                active_res = None
                if state is ScreenState.RESOURCE_SEARCH:
                    frame = load_gray_frame(screenshot)
                    if frame is not None:
                        res = ScreenDetector.identify_active_search_resource(
                            hit.bounds, frame.shape[1]
                        )
                        if isinstance(res, Resource):
                            active_res = res
                hits.append((hit.confidence, state, hit, active_res))
                if hit.confidence >= 0.85:
                    # Inner text is already verified in locate_template for
                    # blue buttons, so a strong hit is unambiguous.
                    return (
                        ScreenDetection(
                            state,
                            hit.confidence,
                            template_name,
                            hit.bounds,
                            preferred_resource=active_res,
                        ),
                        False,
                    )
        hits.sort(key=lambda item: item[0], reverse=True)
        if hits:
            score, state, hit, active_res = hits[0]
            runner_up = hits[1] if len(hits) > 1 else None
            ambiguous = (
                runner_up is not None
                and score - runner_up[0] < _DISPATCH_AMBIGUITY_MARGIN
            )
            return (
                ScreenDetection(
                    state,
                    score,
                    _DISPATCH_BUTTONS[state],
                    hit.bounds,
                    preferred_resource=active_res,
                ),
                ambiguous,
            )

        world = self.detector.detect(
            screenshot,
            read_account=False,
            candidate_states={ScreenState.WORLD_MAP, ScreenState.CITY},
        )
        if world.state in {ScreenState.WORLD_MAP, ScreenState.CITY}:
            return world, False
        return ScreenDetection(ScreenState.UNKNOWN, 0.0, "no gather-flow button"), False

    def _read_button_state(
        self,
        screenshot: Path,
        bounds: tuple[int, int, int, int] | None,
    ) -> ScreenState | None:
        """OCR a blue button's label to tell Thu thập / Quân mới / Tìm kiếm apart."""

        if bounds is None:
            return None
        frame = load_color_frame(screenshot)
        if frame is None:
            return None
        decomposed = unicodedata.normalize(
            "NFD", " ".join(read_text_region(frame, bounds, upscale=1.5)).casefold()
        )
        folded = "".join(
            character
            for character in decomposed
            if not unicodedata.category(character).startswith("M")
            and not character.isspace()
        ).replace("đ", "d")
        for state, keywords in _DISPATCH_BUTTON_TEXT.items():
            if any(keyword in folded for keyword in keywords):
                return state
        return None

    def _wait_for_dispatch_change(
        self,
        clicked_state: ScreenState,
        clicked_bounds: tuple[int, int, int, int] | None,
        *,
        timeout: float | None = None,
        expected_states: tuple[ScreenState, ...] | None = None,
    ) -> tuple[Path, ScreenDetection, bool] | None:
        """Wait until the screen advances past ``clicked_state`` and return it."""

        if timeout is None:
            timeout = self._budget(_STEP_CHANGE_TIMEOUT)
        self.game.wait(_STEP_SETTLE_DELAY)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            screenshot = self.game.screenshot()
            detection, ambiguous = self._classify_dispatch_screen(
                screenshot,
                expected_states=expected_states,
            )
            if detection.state is not ScreenState.UNKNOWN:
                if expected_states and detection.state in expected_states:
                    return screenshot, detection, ambiguous
                if detection.state is not clicked_state:
                    return screenshot, detection, ambiguous
            self.game.wait(self.game.timing.poll_interval)
        return None

    @staticmethod
    def _bounds_close(
        first: tuple[int, int, int, int] | None,
        second: tuple[int, int, int, int] | None,
        tolerance: int = 12,
    ) -> bool:
        if first is None or second is None:
            return first is second
        return (
            abs((first[0] + first[2] / 2) - (second[0] + second[2] / 2)) <= tolerance
            and abs((first[1] + first[3] / 2) - (second[1] + second[3] / 2)) <= tolerance
        )

    def _click_template(
        self,
        template_path: Path,
        *,
        expected_state: ScreenState,
        confidence_threshold: float = 0.68,
        repeat: int = 1,
        initial_screenshot: Path | None = None,
    ) -> Path:
        if repeat < 1:
            raise ValueError("repeat must be at least 1")
        # A menu can briefly classify as UNKNOWN while its animation settles.
        # Retry that transient state, but never click from a different known
        # screen. Revalidate both state and target before every attempt.
        deadline = time.monotonic() + self._budget(_CONTROL_READY_TIMEOUT)
        last_state = ScreenState.UNKNOWN
        attempts = 0
        next_screenshot = initial_screenshot
        while time.monotonic() < deadline or attempts < _MAX_ACTION_ATTEMPTS:
            attempts += 1
            screenshot = (
                next_screenshot
                if next_screenshot is not None
                else self.game.screenshot()
            )
            next_screenshot = None
            hit = self.detector.locate_template(
                screenshot,
                template_path,
                confidence_threshold=confidence_threshold,
                context_key=self._selected_resource,
            )
            if hit is not None:
                last_state = expected_state
                self._log(
                    f"Clicking {template_path.stem}"
                    f"{f' {repeat} times' if repeat > 1 else ''} "
                    f"(match={hit.confidence:.3f})"
                )
                for click_index in range(repeat):
                    self.game.click(hit.bounds, screenshot)
                    if click_index + 1 < repeat:
                        self.game.wait(_LEVEL_REPEAT_PAUSE)
                return screenshot

            detection = self.detector.detect(
                screenshot,
                read_account=False,
                candidate_states={expected_state},
                context_key=self._selected_resource,
            )
            last_state = detection.state
            if (
                detection.state is not expected_state
                and detection.state is not ScreenState.UNKNOWN
                and attempts >= _MAX_ACTION_ATTEMPTS
            ):
                self._require_state(detection, expected_state)

            self.game.wait(_ACTION_RETRY_DELAY)

        raise FarmingError(
            f"Could not confirm {expected_state.value} and locate "
            f"{template_path.name}; last detected screen was {last_state.value}"
        )

    def _wait_for_search_result(
        self,
        before_search: Any,
    ) -> tuple[Path, ScreenDetection]:
        """Wait for a resource result or infer no-result from an unchanged map.

        The search menu and toast cover the coordinate text, so compare stable
        map strips around those overlays. A result opens the gather panel; an
        unsuccessful search leaves the menu and underlying map in place.
        """

        deadline = time.monotonic() + self._budget(self.game.timing.transition_timeout)
        started = time.monotonic()
        unchanged_frames = 0
        last_detection: ScreenDetection | None = None

        while time.monotonic() < deadline:
            screenshot = self.game.screenshot()
            gather_button = self.detector.locate_template(
                screenshot,
                _GATHERING_TEMPLATES / "gather_button.png",
                confidence_threshold=0.68,
            )
            submit_button = (
                None
                if gather_button is not None and gather_button.confidence >= 0.80
                else self.detector.locate_template(
                    screenshot,
                    _SEARCH_TEMPLATES / "submit_button.png",
                    confidence_threshold=0.68,
                    context_key=self._selected_resource,
                )
            )
            if gather_button is not None and (
                submit_button is None
                or gather_button.confidence > submit_button.confidence
            ):
                detection = self.detector.detect(
                    screenshot,
                    read_account=False,
                    candidate_states={ScreenState.RESOURCE_FOUND},
                )
                last_detection = detection
                if detection.state is ScreenState.RESOURCE_FOUND:
                    return screenshot, detection

            if submit_button is not None and (
                gather_button is None
                or submit_button.confidence >= gather_button.confidence
            ):
                # The blue search button can visually resemble the blue gather
                # button. Compare their match scores so a real gather button
                # wins when the result panel has opened.
                last_detection = ScreenDetection(
                    ScreenState.RESOURCE_SEARCH,
                    submit_button.confidence,
                    "search menu remains open",
                    submit_button.bounds,
                )
                if self._map_view_changed(before_search, screenshot):
                    unchanged_frames = 0
                else:
                    unchanged_frames += 1

                if (
                    time.monotonic() - started >= self._budget(_SEARCH_SETTLE_SECONDS)
                    and unchanged_frames >= _UNCHANGED_SEARCH_FRAMES
                ):
                    return screenshot, ScreenDetection(
                        ScreenState.NO_RESULT,
                        1.0,
                        "Map view unchanged after search; no resource location found",
                        submit_button.bounds,
                    )
            else:
                unchanged_frames = 0

            self.game.wait(self.game.timing.poll_interval)

        observed = last_detection.state.value if last_detection else "no screen"
        raise FarmingError(
            "Timed out waiting for a resource result or a stable unchanged map; "
            f"last detected screen was {observed}"
        )

    @staticmethod
    def _map_view_changed(
        before: Any,
        after_path: str | Path,
    ) -> bool:
        """Compare map-only strips, ignoring the search dialog and toast."""

        after = load_color_frame(after_path)
        if after is None or before.shape[:2] != after.shape[:2]:
            return True

        height, width = before.shape[:2]
        regions = (
            (0.04, 0.11, 0.96, 0.36),  # map above the centered dialog/toast
            (0.02, 0.39, 0.15, 0.79),  # left map margin
            (0.86, 0.39, 0.98, 0.79),  # right map margin
        )
        changed_fractions: list[float] = []
        for left, top, right, bottom in regions:
            x1, x2 = round(width * left), round(width * right)
            y1, y2 = round(height * top), round(height * bottom)
            before_region = before[y1:y2, x1:x2]
            after_region = after[y1:y2, x1:x2]
            target_width = max(1, round(before_region.shape[1] * 0.5))
            target_height = max(1, round(before_region.shape[0] * 0.5))
            before_small = cv2.resize(
                cv2.cvtColor(before_region, cv2.COLOR_BGR2GRAY),
                (target_width, target_height),
                interpolation=cv2.INTER_AREA,
            )
            after_small = cv2.resize(
                cv2.cvtColor(after_region, cv2.COLOR_BGR2GRAY),
                (target_width, target_height),
                interpolation=cv2.INTER_AREA,
            )
            before_small = cv2.GaussianBlur(before_small, (5, 5), 0)
            after_small = cv2.GaussianBlur(after_small, (5, 5), 0)
            difference = cv2.absdiff(before_small, after_small)
            changed_fractions.append(float((difference > 24).mean()))

        # Camera movement changes substantial portions of multiple map strips;
        # small unit and ambient animations should remain below these bounds.
        return max(changed_fractions) >= 0.12 or sum(changed_fractions) >= 0.24

    def _wait_for_states(
        self,
        expected_states: set[ScreenState],
    ) -> tuple[Path, ScreenDetection]:
        timeout = self._budget(self.game.timing.transition_timeout)
        deadline = time.monotonic() + timeout
        last_detection: ScreenDetection | None = None
        while time.monotonic() < deadline:
            screenshot = self.game.screenshot()
            detection = self.detector.detect(
                screenshot,
                read_account=False,
                candidate_states=expected_states,
            )
            last_detection = detection
            if detection.state in expected_states:
                return screenshot, detection
            self.game.wait(self.game.timing.poll_interval)

        observed = last_detection.state.value if last_detection else "no screen"
        expected = ", ".join(state.value for state in expected_states)
        raise FarmingError(
            f"Timed out waiting for {expected}; last detected screen was {observed}"
        )

    @staticmethod
    def _require_state(
        detection: ScreenDetection,
        expected_state: ScreenState,
    ) -> None:
        if detection.state is not expected_state:
            raise FarmingError(
                f"Expected {expected_state.value} before clicking, "
                f"found {detection.state.value}"
            )

    def _transition(self, state: FarmState, message: str | None = None) -> None:
        self.context.current_state = state
        self._log(message or f"State: {state.value}")

    def _save_session_summary(self, *, status: str = "running") -> None:
        """Write current session yield to a JSON file for live tracking & force stop recovery."""
        if self.session_summary_path is None:
            return
        try:
            self.session_summary_path.parent.mkdir(parents=True, exist_ok=True)
            totals = calculate_farmed_resources(self.session_targets)
            grand_total = sum(totals.values())
            data = {
                "status": status,
                "timestamp": datetime.now().isoformat(),
                "armies_dispatched": len(self.session_targets),
                "targets": [
                    {
                        "resource": t.resource.value,
                        "level": t.level,
                        "yield": resource_node_yield(t.resource, t.level),
                    }
                    for t in self.session_targets
                ],
                "totals": {r.value: totals[r] for r in Resource},
                "grand_total": grand_total,
                "grand_total_formatted": format_resource_amount(grand_total),
            }
            temp_path = self.session_summary_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp_path.replace(self.session_summary_path)
        except Exception:
            pass

    def _log_resource_summary(
        self,
        *,
        total_characters: int,
        gmail_accounts: int,
        stopped_early: bool = False,
    ) -> None:
        """Log a formatted breakdown and grand total of all resources gathered."""
        totals = calculate_farmed_resources(self.session_targets)
        grand_total = sum(totals.values())
        army_counts = {
            r: sum(1 for t in self.session_targets if t.resource == r)
            for r in Resource
        }

        resource_labels = {
            Resource.FOOD: "Food (Ngô)",
            Resource.WOOD: "Wood (Gỗ)",
            Resource.STONE: "Stone (Đá)",
            Resource.GOLD: "Gold (Vàng)",
        }

        header_title = (
            "TOTAL RESOURCES FARMED BEFORE FORCE STOP"
            if stopped_early
            else "TOTAL RESOURCES FARMED THIS RUN"
        )

        self._log("=" * 62)
        self._log(header_title)
        self._log("=" * 62)
        for resource in (Resource.FOOD, Resource.WOOD, Resource.STONE, Resource.GOLD):
            amount = totals[resource]
            armies = army_counts[resource]
            formatted = format_resource_amount(amount)
            self._log(
                f"  • {resource_labels[resource]:<14}: {formatted:<18} "
                f"across {armies} arm{'y' if armies == 1 else 'ies'}"
            )
        self._log("-" * 62)
        self._log(
            f"  GRAND TOTAL    : {format_resource_amount(grand_total)} "
            f"gathered across {len(self.session_targets)} armies"
        )
        if stopped_early:
            self._log(
                f"  Status         : Force stopped by user after {total_characters} "
                f"RoK character(s)"
            )
        else:
            self._log(
                f"  Accounts cleared: {total_characters} RoK character(s) "
                f"across {gmail_accounts} Gmail account(s)"
            )
        self._log("=" * 62)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log(f"[{timestamp}] {message}")
