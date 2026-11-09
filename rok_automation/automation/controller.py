"""Finite-state controller for searching and dispatching five resource armies."""

from __future__ import annotations

import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import cv2

from rok_automation.automation.context import FarmContext
from rok_automation.automation.account_switcher import (
    AccountSwitcher,
    AccountTraversal,
)
from rok_automation.automation.states import FarmState
from rok_automation.game.interface import GameInterface
from rok_automation.logic.resource_selector import (
    RESOURCE_LEVELS,
    Resource,
    SearchTarget,
    account_name_without_guild,
    random_preferred_resource,
    resource_for_account,
    search_plan,
)
from rok_automation.vision.screen import ScreenDetection, ScreenDetector, ScreenState


class FarmingError(RuntimeError):
    """Raised when the current screen cannot be handled safely."""


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SEARCH_TEMPLATES = _PROJECT_ROOT / "templates/search"
_GATHERING_TEMPLATES = _PROJECT_ROOT / "templates/gathering"
_NAVIGATION_TEMPLATES = _PROJECT_ROOT / "templates/navigation"
_SEARCH_SETTLE_SECONDS = 0.35
_SEARCH_SUBMIT_DELAY = 0.12
_SEARCH_MENU_READY_TIMEOUT = 2.5
_CONTROL_READY_TIMEOUT = 2.5
_LEVEL_REPEAT_PAUSE = 0.12
_LEVEL_SETTLE_DELAY = 0.18
_MAX_ACTION_ATTEMPTS = 5
_ACTION_RETRY_DELAY = 0.20
_UNCHANGED_SEARCH_FRAMES = 2
_RESOURCE_BUTTONS = {
    Resource.GOLD: _SEARCH_TEMPLATES / "resource_gold.png",
    Resource.STONE: _SEARCH_TEMPLATES / "resource_stone.png",
    Resource.FOOD: _SEARCH_TEMPLATES / "resource_food.png",
    Resource.WOOD: _SEARCH_TEMPLATES / "resource_wood.png",
}


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
    ) -> None:
        if not 1 <= max_armies <= 5:
            raise ValueError("max_armies must be between 1 and 5")
        self.game = game
        self.detector = detector or ScreenDetector()
        self.max_armies = max_armies
        self.rng = rng or random.Random()
        self.log = log
        self.context = FarmContext()

    def run(self) -> FarmContext:
        """Farm each linked RoK character once, then stop at the list end."""

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
                context.armies_dispatched = total_dispatched
                return context
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
                context.armies_dispatched = total_dispatched
                context.last_error = None
                self._transition(
                    FarmState.COMPLETE,
                    f"Finished {farmed_accounts} RoK account(s) linked to this Gmail account",
                )
                return context

            next_name = AccountSwitcher(
                self.game,
                self.detector,
                log=self._log,
            ).switch_to_next(traversal)
            if next_name is None:
                context.armies_dispatched = total_dispatched
                context.last_error = None
                self._transition(
                    FarmState.COMPLETE,
                    f"Finished {farmed_accounts} RoK account(s) linked to this Gmail account",
                )
                return context
            expected_account_name = next_name
            self._log(f"Selected {next_name}; starting its farming run")

    def _run_one_account(
        self,
        *,
        expected_account_name: str | None = None,
    ) -> FarmContext:
        self._transition(FarmState.DETECT_ACCOUNT)
        screenshot = self.game.screenshot()
        detection = self.detector.detect(screenshot)
        for _attempt in range(_MAX_ACTION_ATTEMPTS - 1):
            if detection.state in {ScreenState.CITY, ScreenState.WORLD_MAP}:
                break
            self.game.wait(_ACTION_RETRY_DELAY)
            screenshot = self.game.screenshot()
            detection = self.detector.detect(screenshot)

        if detection.state is ScreenState.CITY:
            self._transition(FarmState.OPEN_WORLD_MAP, "Opening the world map")
            self._click_template(
                _NAVIGATION_TEMPLATES / "city_map_button.png",
                expected_state=ScreenState.CITY,
            )
            screenshot, _detection = self._wait_for_states(
                {ScreenState.WORLD_MAP}
            )
            detection = self.detector.detect(
                screenshot,
                candidate_states={ScreenState.WORLD_MAP},
            )
        elif detection.state is not ScreenState.WORLD_MAP:
            raise FarmingError(
                f"Start farming from the city or world map; detected {detection.state.value}"
            )

        for _attempt in range(_MAX_ACTION_ATTEMPTS - 1):
            if (
                detection.state is ScreenState.WORLD_MAP
                and detection.armies_out is not None
            ):
                break
            self.game.wait(_ACTION_RETRY_DELAY)
            screenshot = self.game.screenshot()
            detection = self.detector.detect(
                screenshot,
                candidate_states={ScreenState.WORLD_MAP},
            )

        if detection.state is not ScreenState.WORLD_MAP:
            raise FarmingError("Could not reach the world map")

        self.context.account_name = detection.account_name
        self.context.preferred_resource = detection.preferred_resource
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
        if detection.armies_out is None:
            raise FarmingError(
                "Could not read the world-map troop counter (x/5); "
                "not dispatching without knowing the available army slots"
            )
        self.context.starting_armies_out = detection.armies_out
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
        selected_resource: Resource | None = None
        # A failed level stays unavailable for later armies in this run.
        highest_candidate_level = {
            resource: RESOURCE_LEVELS[0] for resource in Resource
        }
        seen_resource_coordinates: set[tuple[int, int]] = set()

        for army_number in range(1, dispatch_limit + 1):
            total_army_number = self.context.starting_armies_out + army_number
            self._open_search_if_needed()
            dispatched = False
            targets: Iterable[SearchTarget] = search_plan(
                self.context.preferred_resource,
                rng=self.rng,
            )

            for target in targets:
                if target.level > highest_candidate_level[target.resource]:
                    continue

                self.context.current_target = target
                self._transition(
                    FarmState.SELECT_TARGET,
                    f"Army {total_army_number}/5: selecting "
                    f"{target.resource.value} Lv{target.level}",
                )
                if selected_resource is not target.resource:
                    self._click_template(
                        _RESOURCE_BUTTONS[target.resource],
                        expected_state=ScreenState.RESOURCE_SEARCH,
                        confidence_threshold=0.62,
                    )
                    selected_resource = target.resource
                self._set_search_level(target.level)

                self._transition(
                    FarmState.SEARCH_RESOURCE,
                    f"Searching {target.resource.value} Lv{target.level}",
                )
                # Let the search panel finish applying a newly selected level
                # before submitting the search.
                self.game.wait(_SEARCH_SUBMIT_DELAY)
                before_search_path = self._click_template(
                    _SEARCH_TEMPLATES / "submit_button.png",
                    expected_state=ScreenState.RESOURCE_SEARCH,
                )
                before_search = cv2.imread(
                    str(before_search_path), cv2.IMREAD_COLOR
                )
                if before_search is None:
                    raise FarmingError("Could not read the pre-search screenshot")
                result_path, result = self._wait_for_search_result(before_search)
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
                    continue

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
                if duplicate_coordinates is not None:
                    # Search can return the same node after another army has
                    # already been assigned there, even when its route is not
                    # visible in this particular frame.
                    highest_candidate_level[target.resource] = min(
                        highest_candidate_level[target.resource],
                        target.level - 1,
                    )
                    self._log(
                        f"Already handled nearby resource at "
                        f"X:{coordinates[0]} Y:{coordinates[1]} "
                        f"(previously X:{duplicate_coordinates[0]} "
                        f"Y:{duplicate_coordinates[1]}); skipping it and remembering "
                        f"{target.resource.value} up to Lv"
                        f"{highest_candidate_level[target.resource]}"
                    )
                    self._open_search_if_needed()
                    continue

                if result.green_route_detected:
                    if coordinates is not None:
                        seen_resource_coordinates.add(coordinates)
                    highest_candidate_level[target.resource] = min(
                        highest_candidate_level[target.resource],
                        target.level - 1,
                    )
                    self._log(
                        f"Green incoming march route at this location; skipping "
                        f"{target.resource.value} Lv{target.level} "
                        f"(confidence={result.green_route_confidence or 0.0:.3f}); "
                        f"remembering a maximum level of "
                        f"{highest_candidate_level[target.resource]}"
                    )
                    self._open_search_if_needed()
                    continue

                self._dispatch_army(
                    target,
                    total_army_number,
                    result_path=result_path,
                    result_detection=result,
                )
                if coordinates is not None:
                    seen_resource_coordinates.add(coordinates)
                dispatched = True
                break

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

        self._transition(
            FarmState.COMPLETE,
            f"Dispatched {self.context.armies_dispatched} new armies; "
            f"started with {self.context.starting_armies_out}/5 already marching",
        )
        return self.context

    def _open_search_if_needed(self) -> None:
        detection = ScreenDetection(ScreenState.UNKNOWN, 0.0, "uninitialized")
        for _attempt in range(_MAX_ACTION_ATTEMPTS):
            screenshot = self.game.screenshot()
            detection = self.detector.detect(
                screenshot,
                read_account=False,
                candidate_states={
                    ScreenState.WORLD_MAP,
                    ScreenState.RESOURCE_SEARCH,
                    ScreenState.RESOURCE_FOUND,
                },
            )
            if detection.state is ScreenState.RESOURCE_SEARCH:
                return
            if detection.state in {
                ScreenState.WORLD_MAP,
                ScreenState.RESOURCE_FOUND,
            }:
                break
            self.game.wait(_ACTION_RETRY_DELAY)

        if detection.state not in {ScreenState.WORLD_MAP, ScreenState.RESOURCE_FOUND}:
            raise FarmingError(
                f"Expected world map, resource result, or search menu; "
                f"found {detection.state.value}"
            )

        self._transition(FarmState.OPEN_SEARCH, "Opening resource search")
        self._click_template(
            _NAVIGATION_TEMPLATES / "world_search_button.png",
            expected_state=detection.state,
            # The template is cropped to the button face only (no map
            # background or red notification dot), so real hits score ~0.9+
            # on any terrain while other UI stays near 0.6.
            confidence_threshold=0.75,
        )
        self._wait_for_states(
            {ScreenState.RESOURCE_SEARCH}
        )

    def _set_search_level(self, desired_level: int) -> None:
        if desired_level not in RESOURCE_LEVELS:
            raise FarmingError(f"Unsupported resource level: {desired_level}")

        for _attempt in range(_MAX_ACTION_ATTEMPTS):
            _screenshot, detection = self._wait_for_search_menu_level()
            current_level = detection.resource_level
            if current_level is None:
                raise FarmingError("Could not read the level shown in the search menu")
            if current_level == desired_level:
                return

            direction = "plus" if current_level < desired_level else "minus"
            click_count = abs(desired_level - current_level)
            self._log(f"Changing search level {current_level} → {desired_level}")
            self._click_template(
                _SEARCH_TEMPLATES / f"level_{direction}.png",
                expected_state=ScreenState.RESOURCE_SEARCH,
                confidence_threshold=0.60,
                repeat=click_count,
            )
            self.game.wait(_LEVEL_SETTLE_DELAY)

            # Check up to 5 times with a small delay so the slider/label update
            # is detected before deciding another click is needed.
            for _verify in range(_MAX_ACTION_ATTEMPTS):
                _screenshot, verify_detection = self._wait_for_search_menu_level()
                if verify_detection.resource_level == desired_level:
                    return
                self.game.wait(_ACTION_RETRY_DELAY)

        _screenshot, final_detection = self._wait_for_search_menu_level()
        if final_detection.resource_level != desired_level:
            raise FarmingError(
                f"Could not set search level {desired_level}; "
                f"menu shows {final_detection.resource_level}"
            )

    def _wait_for_search_menu_level(self) -> tuple[Path, ScreenDetection]:
        """Wait briefly for the resource menu to finish opening or updating."""

        deadline = time.monotonic() + _SEARCH_MENU_READY_TIMEOUT
        last_state = ScreenState.UNKNOWN
        attempts = 0
        while time.monotonic() < deadline or attempts < _MAX_ACTION_ATTEMPTS:
            attempts += 1
            screenshot = self.game.screenshot()
            detection = self.detector.detect(
                screenshot,
                read_account=False,
                candidate_states={ScreenState.RESOURCE_SEARCH},
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
        *,
        result_path: Path | None = None,
        result_detection: ScreenDetection | None = None,
    ) -> None:
        self._transition(
            FarmState.SEND_TROOPS,
            f"Found {target.resource.value} Lv{target.level}; opening gather",
        )
        if (
            result_path is not None
            and result_detection is not None
            and result_detection.state is ScreenState.RESOURCE_FOUND
            and result_detection.bounds is not None
        ):
            self._log("Clicking gather_button (already verified)")
            self.game.click(result_detection.bounds, result_path)
        else:
            self._click_template(
                _GATHERING_TEMPLATES / "gather_button.png",
                expected_state=ScreenState.RESOURCE_FOUND,
            )
        self._wait_for_states({ScreenState.NEW_TROOPS_PROMPT})

        self._click_template(
            _GATHERING_TEMPLATES / "new_troops_button.png",
            expected_state=ScreenState.NEW_TROOPS_PROMPT,
        )
        self._wait_for_states({ScreenState.TROOP_COMPOSITION})

        self._transition(FarmState.VERIFY_GATHERING, "Checking the march screen")
        self._click_template(
            _GATHERING_TEMPLATES / "march_button.png",
            expected_state=ScreenState.TROOP_COMPOSITION,
        )
        self._wait_for_states({ScreenState.WORLD_MAP})
        self.context.armies_dispatched += 1
        self._log(
            f"Army {army_number}/5 dispatched to "
            f"{target.resource.value} Lv{target.level}"
        )

    def _click_template(
        self,
        template_path: Path,
        *,
        expected_state: ScreenState,
        confidence_threshold: float = 0.68,
        repeat: int = 1,
    ) -> Path:
        if repeat < 1:
            raise ValueError("repeat must be at least 1")
        # A menu can briefly classify as UNKNOWN while its animation settles.
        # Retry that transient state, but never click from a different known
        # screen. Revalidate both state and target before every attempt.
        deadline = time.monotonic() + _CONTROL_READY_TIMEOUT
        last_state = ScreenState.UNKNOWN
        attempts = 0
        while time.monotonic() < deadline or attempts < _MAX_ACTION_ATTEMPTS:
            attempts += 1
            screenshot = self.game.screenshot()
            detection = self.detector.detect(
                screenshot,
                read_account=False,
                candidate_states={expected_state},
            )
            last_state = detection.state
            if detection.state is expected_state:
                hit = self.detector.locate_template(
                    screenshot,
                    template_path,
                    confidence_threshold=confidence_threshold,
                )
                if hit is not None:
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
            elif (
                detection.state is not ScreenState.UNKNOWN
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

        deadline = time.monotonic() + self.game.timing.transition_timeout
        started = time.monotonic()
        unchanged_frames = 0
        last_detection: ScreenDetection | None = None

        while time.monotonic() < deadline:
            screenshot = self.game.screenshot()
            submit_button = self.detector.locate_template(
                screenshot,
                _SEARCH_TEMPLATES / "submit_button.png",
                confidence_threshold=0.68,
            )
            gather_button = self.detector.locate_template(
                screenshot,
                _GATHERING_TEMPLATES / "gather_button.png",
                confidence_threshold=0.68,
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
                    time.monotonic() - started >= _SEARCH_SETTLE_SECONDS
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

        after = cv2.imread(str(after_path), cv2.IMREAD_COLOR)
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
        timeout = self.game.timing.transition_timeout
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

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log(f"[{timestamp}] {message}")
