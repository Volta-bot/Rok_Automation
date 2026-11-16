"""Shared recognition for normal and alliance gathering/deployment screens."""

from dataclasses import replace
from pathlib import Path
from typing import Any
import unicodedata

from rok_automation.logic.resource_selector import Resource
from rok_automation.vision.account_label import read_text_region
from rok_automation.vision.screen import (
    ScreenDetection, ScreenDetector, ScreenState, load_color_frame, load_gray_frame,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SEARCH_TEMPLATES = _PROJECT_ROOT / "templates/search"
_GATHERING_TEMPLATES = _PROJECT_ROOT / "templates/gathering"
_NAVIGATION_TEMPLATES = _PROJECT_ROOT / "templates/navigation"
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


class DispatchScreenReader:
    def __init__(self, detector, resource=None):
        self.detector = detector
        self._selected_resource = resource

    def _locate_button(self, screenshot, state):
        names = [_DISPATCH_BUTTONS[state]]
        if state is ScreenState.RESOURCE_FOUND:
            names.append("gather_button_alliance")
        for name in names:
            hit = self.detector.locate_template(
                screenshot, _DISPATCH_TEMPLATE_DIRS[state] / f"{name}.png",
                confidence_threshold=_DISPATCH_BUTTON_CONFIDENCE,
                context_key=self._selected_resource,
            )
            if hit is not None:
                return hit
        return None

    def read(self, screenshot, *, expected_states=None):
        detection, ambiguous = self.classify(screenshot, expected_states=expected_states)
        if ambiguous:
            state = self.read_button_state(screenshot, detection.bounds)
            if state is None:
                return ScreenDetection(ScreenState.UNKNOWN, 0, "ambiguous gather button")
            return replace(detection, state=state)
        return detection

    def classify(
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
                    hit = self._locate_button(screenshot, exp_state)
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
            hit = self._locate_button(screenshot, state)
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
            candidate_states={ScreenState.WORLD_MAP, ScreenState.CITY, ScreenState.MAILBOX},
        )
        if world.state in {ScreenState.WORLD_MAP, ScreenState.CITY, ScreenState.MAILBOX}:
            return world, False
        return ScreenDetection(ScreenState.UNKNOWN, 0.0, "no gather-flow button"), False

    def read_button_state(
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

