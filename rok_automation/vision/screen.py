"""Read-only game screen classification using supplied UI templates."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import cv2
from rok_automation.logic.resource_selector import RESOURCE_LEVELS, Resource
from rok_automation.vision.account_label import (
    read_account_label,
    read_army_count,
    read_text_region,
)
from rok_automation.vision.green_route import detect_green_incoming_route


class ScreenState(str, Enum):
    UNKNOWN = "unknown"
    CITY = "city"
    WORLD_MAP = "world_map"
    RESOURCE_SEARCH = "resource_search"
    RESOURCE_FOUND = "resource_found"
    NO_RESULT = "no_result"
    NEW_TROOPS_PROMPT = "new_troops_prompt"
    TROOP_COMPOSITION = "troop_composition"


@dataclass(frozen=True)
class ScreenDetection:
    state: ScreenState
    confidence: float
    evidence: str
    bounds: tuple[int, int, int, int] | None = None
    resource_level: int | None = None
    account_name: str | None = None
    preferred_resource: Resource | None = None
    green_route_detected: bool | None = None
    green_route_confidence: float | None = None
    green_route_evidence: str | None = None
    armies_out: int | None = None
    resource_coordinates: tuple[int, int] | None = None


@dataclass(frozen=True)
class TemplateHit:
    confidence: float
    bounds: tuple[int, int, int, int]


@dataclass(frozen=True)
class _Template:
    state: ScreenState
    name: str
    path: Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = (
    _Template(
        ScreenState.CITY,
        "city map button",
        _PROJECT_ROOT / "templates/navigation/city_map_button.png",
    ),
    _Template(
        ScreenState.WORLD_MAP,
        "world map city button",
        _PROJECT_ROOT / "templates/navigation/world_city_button.png",
    ),
    _Template(
        ScreenState.WORLD_MAP,
        "world map search button",
        _PROJECT_ROOT / "templates/navigation/world_search_button.png",
    ),
    _Template(
        ScreenState.RESOURCE_SEARCH,
        "resource search button",
        _PROJECT_ROOT / "templates/search/submit_button.png",
    ),
    _Template(
        ScreenState.RESOURCE_FOUND,
        "gather button",
        _PROJECT_ROOT / "templates/gathering/gather_button.png",
    ),
    _Template(
        ScreenState.NEW_TROOPS_PROMPT,
        "new troops button",
        _PROJECT_ROOT / "templates/gathering/new_troops_button.png",
    ),
    _Template(
        ScreenState.TROOP_COMPOSITION,
        "march button",
        _PROJECT_ROOT / "templates/gathering/march_button.png",
    ),
)
_TEMPLATE_CACHE: dict[Path, Any] = {}
# (frame size, template identity) -> scale step (percent) of a confident match.
_SCALE_CACHE: dict[tuple[int, ...], int] = {}


def _load_gray_template(path: Path) -> Any:
    cached = _TEMPLATE_CACHE.get(path)
    if cached is not None:
        return cached
    image_template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image_template is None:
        raise FileNotFoundError(f"Missing UI template: {path}")
    _TEMPLATE_CACHE[path] = image_template
    return image_template


class ScreenDetector:
    """Classify a screenshot from stable UI buttons, returning UNKNOWN on doubt."""

    def __init__(self, confidence_threshold: float = 0.72) -> None:
        if not 0.0 < confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self.confidence_threshold = confidence_threshold

    def detect(
        self,
        screenshot_path: str | Path,
        *,
        expected_resource_level: int | None = None,
        read_account: bool = True,
        candidate_states: set[ScreenState] | None = None,
    ) -> ScreenDetection:
        """Classify an image and attach the active level to a found result.

        The farming controller supplies ``expected_resource_level`` from its
        runtime context. The search interface is set to that level before the
        search, so this avoids OCR of the result label.
        """

        if (
            expected_resource_level is not None
            and expected_resource_level not in RESOURCE_LEVELS
        ):
            raise ValueError(
                f"expected_resource_level must be one of {RESOURCE_LEVELS}"
            )

        image_path = Path(screenshot_path)
        frame = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {image_path}")

        best = ScreenDetection(ScreenState.UNKNOWN, 0.0, "no UI template matched")
        templates = (
            _TEMPLATES
            if candidate_states is None
            else tuple(
                template for template in _TEMPLATES
                if template.state in candidate_states
            )
        )
        for template in templates:
            image_template = _load_gray_template(template.path)

            score, bounds = self._best_scale_match(frame, image_template)
            if score > best.confidence:
                height, width = self._scaled_size(image_template, bounds[2])
                best = ScreenDetection(
                    template.state,
                    score,
                    template.name,
                    (bounds[0], bounds[1], width, height),
                )
                if score >= 0.92 and len(templates) == 1:
                    break

        if best.confidence < self.confidence_threshold:
            return ScreenDetection(
                ScreenState.UNKNOWN,
                best.confidence,
                f"best candidate was {best.evidence}",
                best.bounds,
            )
        if best.state is ScreenState.RESOURCE_SEARCH and best.bounds is not None:
            level = self._read_search_level(frame, best.bounds)
            evidence = best.evidence
            if level is not None:
                evidence = f"{evidence}; OCR read Cấp: {level}"
            return ScreenDetection(
                best.state,
                best.confidence,
                evidence,
                best.bounds,
                level,
            )
        if best.state is ScreenState.RESOURCE_FOUND:
            color_frame = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if color_frame is None:
                raise FileNotFoundError(f"Could not read screenshot: {image_path}")
            green_route = detect_green_incoming_route(color_frame)
            resource_coordinates = ScreenDetector._read_resource_coordinates(
                color_frame
            )
            evidence = best.evidence
            if green_route.detected:
                evidence = f"{evidence}; green incoming route"
            if resource_coordinates is not None:
                evidence = (
                    f"{evidence}; resource coordinates "
                    f"X:{resource_coordinates[0]} Y:{resource_coordinates[1]}"
                )
            return ScreenDetection(
                best.state,
                best.confidence,
                evidence,
                best.bounds,
                expected_resource_level,
                green_route_detected=green_route.detected,
                green_route_confidence=green_route.confidence,
                green_route_evidence=green_route.evidence,
                resource_coordinates=resource_coordinates,
            )
        if best.state is ScreenState.WORLD_MAP and read_account:
            color_frame = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if color_frame is None:
                raise FileNotFoundError(f"Could not read screenshot: {image_path}")
            account_name, preferred_resource = read_account_label(color_frame)
            armies_out = read_army_count(color_frame)
            evidence = best.evidence
            if account_name is not None:
                evidence = f"{evidence}; account label OCR"
            if armies_out is not None:
                evidence = f"{evidence}; march counter OCR"
            else:
                # The right-side march list and its x/5 label disappear when
                # no armies are marching. The user confirmed that this means 0.
                armies_out = 0
                evidence = f"{evidence}; no march counter visible, assuming 0/5"
            return ScreenDetection(
                best.state,
                best.confidence,
                evidence,
                best.bounds,
                account_name=account_name,
                preferred_resource=preferred_resource,
                armies_out=armies_out,
            )
        return best

    @staticmethod
    def _read_resource_coordinates(
        frame: Any,
    ) -> tuple[int, int] | None:
        """Read X/Y from the resource-info panel, excluding map HUD coords."""

        frame_height, frame_width = frame.shape[:2]
        lines = read_text_region(
            frame,
            (
                round(frame_width * 0.50),
                round(frame_height * 0.17),
                round(frame_width * 0.47),
                round(frame_height * 0.59),
            ),
            upscale=2.0,
        )
        text = " ".join(lines).replace("Ｘ", "X").replace("Ｙ", "Y")
        match = re.search(
            r"\bX\s*[:：]?\s*(\d{1,5})\D{0,12}\bY\s*[:：]?\s*(\d{1,5})\b",
            text,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None
        return int(match.group(1)), int(match.group(2))

    @classmethod
    def locate_template(
        cls,
        screenshot_path: str | Path,
        template_path: str | Path,
        *,
        confidence_threshold: float = 0.68,
    ) -> TemplateHit | None:
        """Locate a supplied visual control in a screenshot for safe clicking."""

        frame = cv2.imread(str(screenshot_path), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {screenshot_path}")
        image_template = _load_gray_template(Path(template_path))

        score, match = cls._best_scale_match(
            frame,
            image_template,
            minimum_scale=0.45,
            maximum_scale=1.35,
        )
        if score < confidence_threshold:
            return None
        height, width = cls._scaled_size(image_template, match[2])
        return TemplateHit(score, (match[0], match[1], width, height))

    @staticmethod
    def _read_search_level(
        frame: Any,
        submit_bounds: tuple[int, int, int, int],
    ) -> int | None:
        """OCR the visible ``Cấp: N`` label beside the resource slider."""

        x, y, width, height = submit_bounds
        # The label is right-aligned just above the slider, about one
        # submit-button height above the button itself. Crop it tightly so
        # OCR cannot borrow a number from the slider or another UI element.
        label_left = round(x + width * 0.25)
        label_right = round(x + width * 2.0)
        label_top = round(y - height * 1.30)
        label_bottom = round(y - height * 0.55)
        lines = read_text_region(
            frame,
            (
                label_left,
                label_top,
                label_right - label_left,
                label_bottom - label_top,
            ),
            upscale=3.0,
        )
        match = ScreenDetector._parse_level_text(lines)
        if match is None:
            # Accommodate slight panel/layout shifts while still requiring
            # OCR to read the Cấp label adjacent to the digit.
            lines = read_text_region(
                frame,
                (
                    max(0, round(x - width * 0.15)),
                    max(0, round(y - height * 2.9)),
                    round(width * 1.9),
                    round(height * 2.55),
                ),
                upscale=2.0,
            )
            match = ScreenDetector._parse_level_text(lines)
        if match is None:
            return None
        return int(match.group(1))

    @staticmethod
    def _parse_level_text(lines: tuple[str, ...]) -> re.Match[str] | None:
        decomposed = unicodedata.normalize("NFD", " ".join(lines).casefold())
        folded = "".join(
            character
            for character in decomposed
            if not unicodedata.category(character).startswith("M")
        ).replace("đ", "d")
        # RapidOCR occasionally drops the small accented "á" and returns
        # "Cp: 8"; accept that label variant while still requiring the C/p
        # prefix next to the level digit.
        return re.search(r"\bc(?:a)?p\s*[:：]?\s*([5-8])\b", folded)

    @staticmethod
    def _scaled_size(template: Any, scale: float) -> tuple[int, int]:
        width = max(1, round(template.shape[1] * scale))
        height = max(1, round(template.shape[0] * scale))
        return height, width

    @classmethod
    def _best_scale_match(
        cls,
        frame: Any,
        template: Any,
        *,
        minimum_scale: float = 0.45,
        maximum_scale: float = 1.10,
    ) -> tuple[float, tuple[int, int, float]]:
        best_score = -1.0
        best_match = (0, 0, 1.0)
        scale_start = round(minimum_scale * 100)
        scale_end = round(maximum_scale * 100)
        cache_key = cls._scale_cache_key(frame, template)
        cached_step = _SCALE_CACHE.get(cache_key)
        if cached_step is not None and scale_start <= cached_step <= scale_end:
            # The emulator resolution is fixed during a run, so once a control
            # has been matched confidently its on-screen scale is known. Only
            # check that scale and its neighbours instead of all ~20 scales;
            # a missing control then fails fast instead of taking ~0.4s.
            for scale_step in (cached_step, cached_step - 5, cached_step + 5):
                if not scale_start <= scale_step <= scale_end:
                    continue
                score, location = cls._match_at_scale(frame, template, scale_step)
                if score > best_score:
                    best_score = score
                    best_match = (location[0], location[1], scale_step / 100.0)
                if scale_step == cached_step and score >= 0.90:
                    break
            if best_score >= 0.90:
                _SCALE_CACHE[cache_key] = round(best_match[2] * 100)
            return max(0.0, best_score), best_match

        all_steps = range(scale_start, scale_end + 1, 5)
        # Check scales closest to 1.0 first so standard-resolution hits
        # short-circuit immediately without scanning all ~20 scales.
        primary_steps = tuple(
            step for step in (100, 95, 105, 90, 110)
            if scale_start <= step <= scale_end
        )
        remaining_steps = tuple(
            step for step in all_steps if step not in primary_steps
        )
        for index, scale_step in enumerate((*primary_steps, *remaining_steps)):
            scale = scale_step / 100.0
            width = max(1, round(template.shape[1] * scale))
            height = max(1, round(template.shape[0] * scale))
            if width > frame.shape[1] or height > frame.shape[0]:
                continue

            max_score, max_location = cls._match_at_scale(frame, template, scale_step)
            if max_score > best_score:
                best_score = max_score
                best_match = (max_location[0], max_location[1], scale)
            if index + 1 == len(primary_steps) and best_score >= 0.90:
                break

        if best_score >= 0.90:
            _SCALE_CACHE[cache_key] = round(best_match[2] * 100)
        return max(0.0, best_score), best_match

    @staticmethod
    def _scale_cache_key(frame: Any, template: Any) -> tuple[int, ...]:
        sample = template[:: max(1, template.shape[0] // 16), :: max(1, template.shape[1] // 16)]
        return (
            frame.shape[0],
            frame.shape[1],
            template.shape[0],
            template.shape[1],
            int(sample.sum()),
        )

    @staticmethod
    def _match_at_scale(
        frame: Any,
        template: Any,
        scale_step: int,
    ) -> tuple[float, tuple[int, int]]:
        scale = scale_step / 100.0
        width = max(1, round(template.shape[1] * scale))
        height = max(1, round(template.shape[0] * scale))
        if width > frame.shape[1] or height > frame.shape[0]:
            return -1.0, (0, 0)
        resized = (
            template
            if scale_step == 100
            else cv2.resize(
                template,
                (width, height),
                interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC,
            )
        )
        result = cv2.matchTemplate(frame, resized, cv2.TM_CCOEFF_NORMED)
        _min_score, max_score, _min_location, max_location = cv2.minMaxLoc(result)
        return float(max_score), (int(max_location[0]), int(max_location[1]))
