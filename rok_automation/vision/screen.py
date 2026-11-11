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
# (frame_h, frame_w, template_name, context_key) -> (x, y, w, h, scale_step)
_POSITION_CACHE: dict[tuple[int, int, str, Any], tuple[int, int, int, int, int]] = {}
# In-memory decoded screenshot cache: Path -> (mtime_ns, size, bgr_frame, gray_frame)
_FRAME_CACHE: dict[Path, tuple[int, int, Any, Any]] = {}

# Normalized (left, top, right, bottom) search regions for static UI controls.
_TEMPLATE_ROIS: dict[str, tuple[float, float, float, float]] = {
    "world_search_button.png": (0.00, 0.56, 0.16, 0.88),
    "city_map_button.png": (0.00, 0.74, 0.16, 1.00),
    "world_city_button.png": (0.00, 0.74, 0.16, 1.00),
    "gather_button.png": (0.45, 0.44, 0.95, 0.86),
    "new_troops_button.png": (0.55, 0.05, 0.98, 0.70),
    "march_button.png": (0.50, 0.64, 0.98, 0.98),
    "resource_food.png": (0.25, 0.72, 0.46, 0.99),
    "resource_wood.png": (0.40, 0.72, 0.60, 0.99),
    "resource_stone.png": (0.55, 0.72, 0.75, 0.99),
    "resource_gold.png": (0.70, 0.72, 0.90, 0.99),
    "submit_button.png": (0.12, 0.25, 0.98, 0.82),
    "level_plus.png": (0.12, 0.18, 0.98, 0.72),
    "level_minus.png": (0.12, 0.18, 0.98, 0.72),
    "settings_button.png": (0.65, 0.60, 1.0, 1.0),
    "character_menu_button.png": (0.10, 0.18, 0.90, 0.86),
    "vip_shield.png": (0.00, 0.00, 0.25, 0.18),
    "wooden_chest.png": (0.80, 0.05, 0.98, 0.25),
    "green_nhan.png": (0.70, 0.40, 0.95, 0.60),
    "vip_close_x.png": (0.90, 0.00, 1.00, 0.12),
    "menu_dots.png": (0.88, 0.85, 1.00, 1.00),
    "chien_dich.png": (0.50, 0.82, 0.65, 1.00),
    "chinh_phat_card.png": (0.02, 0.10, 0.30, 0.55),
    "golden_chest.png": (0.02, 0.20, 0.18, 0.45),
    "chinh_phat_chest_collected.png": (0.02, 0.20, 0.18, 0.45),
    "wooden_chest_collected.png": (0.65, 0.05, 0.98, 0.28),
    "metal_chest_collected.png": (0.55, 0.35, 0.95, 0.75),
    "xac_nhan.png": (0.35, 0.70, 0.65, 0.95),
    "back_button.png": (0.00, 0.00, 0.10, 0.12),
}
# Search-popup controls sit directly above whichever resource icon is selected.
_RESOURCE_DEPENDENT_TEMPLATES = frozenset({
    "submit_button.png",
    "level_plus.png",
    "level_minus.png",
})
_RESOURCE_ICON_NAMES: dict[Resource, str] = {
    Resource.GOLD: "resource_gold.png",
    Resource.STONE: "resource_stone.png",
    Resource.FOOD: "resource_food.png",
    Resource.WOOD: "resource_wood.png",
}
# Look-alike cyan buttons whose inner text strip disambiguates them without OCR.
_BLUE_TEXT_TEMPLATES = frozenset({
    "gather_button.png",
    "new_troops_button.png",
    "submit_button.png",
})


def cache_captured_frame(path: str | Path, bgr_frame: Any) -> None:
    """Store a freshly captured BGR frame in memory to avoid disk re-reads."""

    resolved = Path(path).resolve()
    try:
        stat = resolved.stat()
        mtime_ns, size = stat.st_mtime_ns, stat.st_size
    except OSError:
        mtime_ns, size = -1, -1
    gray_frame = (
        cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
        if bgr_frame.ndim == 3
        else bgr_frame
    )
    if len(_FRAME_CACHE) >= 6:
        _FRAME_CACHE.clear()
    _FRAME_CACHE[resolved] = (mtime_ns, size, bgr_frame, gray_frame)


def load_color_frame(path: str | Path) -> Any:
    """Load a BGR screenshot, serving from memory when unchanged on disk."""

    resolved = Path(path).resolve()
    try:
        stat = resolved.stat()
        mtime_ns, size = stat.st_mtime_ns, stat.st_size
    except OSError:
        mtime_ns, size = -2, -2
    cached = _FRAME_CACHE.get(resolved)
    if cached is not None and cached[0] == mtime_ns and cached[1] == size:
        return cached[2]
    bgr = cv2.imread(str(resolved), cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if len(_FRAME_CACHE) >= 6:
        _FRAME_CACHE.clear()
    _FRAME_CACHE[resolved] = (mtime_ns, size, bgr, gray)
    return bgr


def load_gray_frame(path: str | Path) -> Any:
    """Load a grayscale screenshot, serving from memory when unchanged on disk."""

    resolved = Path(path).resolve()
    try:
        stat = resolved.stat()
        mtime_ns, size = stat.st_mtime_ns, stat.st_size
    except OSError:
        mtime_ns, size = -2, -2
    cached = _FRAME_CACHE.get(resolved)
    if cached is not None and cached[0] == mtime_ns and cached[1] == size:
        return cached[3]
    bgr = cv2.imread(str(resolved), cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if len(_FRAME_CACHE) >= 6:
        _FRAME_CACHE.clear()
    _FRAME_CACHE[resolved] = (mtime_ns, size, bgr, gray)
    return gray


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
        context_key: Any = None,
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
        frame = load_gray_frame(image_path)
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
            hit = self._locate_in_frame(
                frame,
                template.path,
                confidence_threshold=0.0,
                context_key=context_key,
                maximum_scale=1.35,
            )
            if hit is not None and hit.confidence > best.confidence:
                best = ScreenDetection(
                    template.state,
                    hit.confidence,
                    template.name,
                    hit.bounds,
                )
                if hit.confidence >= 0.90 and len(templates) == 1:
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
            active_res = self.identify_active_search_resource(best.bounds, frame.shape[1])
            evidence = best.evidence
            if isinstance(active_res, Resource):
                evidence = f"{evidence}; active resource: {active_res.value}"
            elif active_res:
                evidence = f"{evidence}; active tab: {active_res}"
            if level is not None:
                evidence = f"{evidence}; OCR read Cấp: {level}"
            return ScreenDetection(
                best.state,
                best.confidence,
                evidence,
                best.bounds,
                level,
                preferred_resource=active_res if isinstance(active_res, Resource) else None,
            )
        if best.state is ScreenState.RESOURCE_FOUND:
            color_frame = load_color_frame(image_path)
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
            color_frame = load_color_frame(image_path)
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
        context_key: Any = None,
    ) -> TemplateHit | None:
        """Locate a supplied visual control in a screenshot for safe clicking."""

        frame = load_gray_frame(screenshot_path)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {screenshot_path}")
        return cls._locate_in_frame(
            frame,
            Path(template_path),
            confidence_threshold=confidence_threshold,
            context_key=context_key,
            maximum_scale=1.35,
        )

    @classmethod
    def _locate_in_frame(
        cls,
        frame: Any,
        template_path: Path,
        *,
        confidence_threshold: float,
        context_key: Any = None,
        maximum_scale: float = 1.35,
    ) -> TemplateHit | None:
        """Fast ROI + pinned-position template matcher for static UI buttons."""

        image_template = _load_gray_template(template_path)
        template_name = template_path.name
        frame_h, frame_w = frame.shape[:2]
        is_resource_dep = template_name in _RESOURCE_DEPENDENT_TEMPLATES
        effective_context = context_key if is_resource_dep else None

        # 1. Fast path: check pinned (x, y, w, h, scale_step) if already known.
        candidate_contexts: tuple[Any, ...] = (
            (effective_context,)
            if (not is_resource_dep or effective_context is not None)
            else (*Resource, None)
        )
        for cand_ctx in candidate_contexts:
            pinned = _POSITION_CACHE.get(
                (frame_h, frame_w, template_name, cand_ctx)
            )
            if pinned is None:
                continue
            px, py, pw, ph, p_step = pinned
            pad = 24
            x0 = max(0, px - pad)
            y0 = max(0, py - pad)
            x1 = min(frame_w, px + pw + pad)
            y1 = min(frame_h, py + ph + pad)
            crop = frame[y0:y1, x0:x1]
            score, loc = cls._match_at_scale(crop, image_template, p_step)
            if score >= max(confidence_threshold, 0.72):
                hit_x, hit_y = x0 + loc[0], y0 + loc[1]
                if template_name in _BLUE_TEXT_TEMPLATES and not cls._verify_blue_button_text(
                    frame, image_template, (hit_x, hit_y, pw, ph)
                ):
                    continue
                return TemplateHit(score, (hit_x, hit_y, pw, ph))

        # 2. ROI scan: crop to the control's static region (narrowed above the
        # selected resource icon for Tìm kiếm / + / - when that icon's position
        # is already cached).
        roi = _TEMPLATE_ROIS.get(template_name)
        narrowed_roi = False
        if roi is not None:
            left_f, top_f, right_f, bottom_f = roi
            if is_resource_dep and isinstance(effective_context, Resource):
                icon_name = _RESOURCE_ICON_NAMES.get(effective_context)
                icon_pinned = (
                    _POSITION_CACHE.get((frame_h, frame_w, icon_name, None))
                    if icon_name is not None
                    else None
                )
                if icon_pinned is not None:
                    ix, _iy, iw, _ih, _istep = icon_pinned
                    center_x = (ix + iw / 2) / frame_w
                    left_f = max(left_f, center_x - 0.22)
                    right_f = min(right_f, center_x + 0.22)
                    narrowed_roi = True
            rx0 = max(0, round(frame_w * left_f))
            ry0 = max(0, round(frame_h * top_f))
            rx1 = min(frame_w, round(frame_w * right_f))
            ry1 = min(frame_h, round(frame_h * bottom_f))
        else:
            rx0, ry0, rx1, ry1 = 0, 0, frame_w, frame_h

        roi_frame = frame[ry0:ry1, rx0:rx1]
        score, match = cls._best_scale_match(
            roi_frame,
            image_template,
            minimum_scale=0.35,
            maximum_scale=maximum_scale,
            frame_key_shape=(frame_h, frame_w),
        )
        height, width = cls._scaled_size(image_template, match[2])
        hit_bounds = (rx0 + match[0], ry0 + match[1], width, height)

        if score >= confidence_threshold and template_name in _BLUE_TEXT_TEMPLATES:
            if not cls._verify_blue_button_text(frame, image_template, hit_bounds):
                score = 0.0

        if score < confidence_threshold and narrowed_roi and roi is not None:
            left_f, top_f, right_f, bottom_f = roi
            rx0 = max(0, round(frame_w * left_f))
            ry0 = max(0, round(frame_h * top_f))
            rx1 = min(frame_w, round(frame_w * right_f))
            ry1 = min(frame_h, round(frame_h * bottom_f))
            roi_frame = frame[ry0:ry1, rx0:rx1]
            score, match = cls._best_scale_match(
                roi_frame,
                image_template,
                minimum_scale=0.35,
                maximum_scale=maximum_scale,
                frame_key_shape=(frame_h, frame_w),
            )
            height, width = cls._scaled_size(image_template, match[2])
            hit_bounds = (rx0 + match[0], ry0 + match[1], width, height)
            if score >= confidence_threshold and template_name in _BLUE_TEXT_TEMPLATES:
                if not cls._verify_blue_button_text(frame, image_template, hit_bounds):
                    score = 0.0

        if score < confidence_threshold:
            return None

        if score >= 0.78 and template_name in _TEMPLATE_ROIS:
            scale_step = round(match[2] * 100)
            _POSITION_CACHE[
                (frame_h, frame_w, template_name, effective_context)
            ] = (hit_bounds[0], hit_bounds[1], width, height, scale_step)
        return TemplateHit(score, hit_bounds)

    @staticmethod
    def _verify_blue_button_text(
        frame: Any,
        template: Any,
        bounds: tuple[int, int, int, int],
    ) -> bool:
        """Verify the inner label strip of a cyan button in ~0.05ms without OCR."""

        x, y, width, height = bounds
        if width <= 8 or height <= 8:
            return False
        patch = frame[
            max(0, y) : min(frame.shape[0], y + height),
            max(0, x) : min(frame.shape[1], x + width),
        ]
        if patch.size == 0:
            return False
        tpl_h, tpl_w = template.shape[:2]
        resized_patch = cv2.resize(
            patch,
            (tpl_w, tpl_h),
            interpolation=cv2.INTER_AREA if width < tpl_w else cv2.INTER_LINEAR,
        )
        y0, y1 = int(tpl_h * 0.22), int(tpl_h * 0.78)
        x0, x1 = int(tpl_w * 0.14), int(tpl_w * 0.86)
        inner_tpl = template[y0:y1, x0:x1]
        search_patch = resized_patch[
            max(0, y0 - 4) : min(tpl_h, y1 + 4),
            max(0, x0 - 4) : min(tpl_w, x1 + 4),
        ]
        if (
            search_patch.shape[0] < inner_tpl.shape[0]
            or search_patch.shape[1] < inner_tpl.shape[1]
        ):
            return False
        res = cv2.matchTemplate(search_patch, inner_tpl, cv2.TM_CCOEFF_NORMED)
        return float(res.max()) >= 0.56

    @staticmethod
    def identify_active_search_resource(
        submit_bounds: tuple[int, int, int, int],
        frame_width: int,
    ) -> Resource | str:
        """Identify which resource tab the search dialog is currently anchored above."""

        center_x = (submit_bounds[0] + submit_bounds[2] / 2) / frame_width
        if center_x < 0.285:
            return "barbarian"
        elif center_x < 0.428:
            return Resource.FOOD
        elif center_x < 0.575:
            return Resource.WOOD
        elif center_x < 0.725:
            return Resource.STONE
        else:
            return Resource.GOLD

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
        # RapidOCR occasionally drops or misreads the small accented "ấ" in
        # "Cấp: 8" as "Cp: 8" or "CEp.8"; accept those variants while still
        # requiring the C/p prefix next to the level digit.
        return re.search(r"\bc(?:[aei])?p\s*[:：.]?\s*([5-8])\b", folded)

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
        frame_key_shape: tuple[int, int] | None = None,
    ) -> tuple[float, tuple[int, int, float]]:
        best_score = -1.0
        best_match = (0, 0, 1.0)
        scale_start = round(minimum_scale * 100)
        scale_end = round(maximum_scale * 100)
        cache_key = cls._scale_cache_key(
            frame,
            template,
            frame_key_shape=frame_key_shape,
        )
        cached_step = _SCALE_CACHE.get(cache_key)
        checked_steps: set[int] = set()
        if cached_step is not None and scale_start <= cached_step <= scale_end:
            for scale_step in (cached_step, cached_step - 5, cached_step + 5):
                if not scale_start <= scale_step <= scale_end:
                    continue
                checked_steps.add(scale_step)
                score, location = cls._match_at_scale(frame, template, scale_step)
                if score > best_score:
                    best_score = score
                    best_match = (location[0], location[1], scale_step / 100.0)
                if scale_step == cached_step and score >= 0.85:
                    break
            if best_score >= 0.78:
                _SCALE_CACHE[cache_key] = round(best_match[2] * 100)
                return max(0.0, best_score), best_match

        all_steps = range(scale_start, scale_end + 1, 5)
        # Check scales closest to 1.0 (and 0.65/0.85 for popups) first.
        primary_steps = tuple(
            step for step in (100, 95, 105, 90, 110, 65, 85, 70, 60)
            if scale_start <= step <= scale_end and step not in checked_steps
        )
        remaining_steps = tuple(
            step
            for step in all_steps
            if step not in primary_steps and step not in checked_steps
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
            if index + 1 == len(primary_steps) and best_score >= 0.88:
                break

        if best_score >= 0.85:
            _SCALE_CACHE[cache_key] = round(best_match[2] * 100)
        return max(0.0, best_score), best_match

    @staticmethod
    def _scale_cache_key(
        frame: Any,
        template: Any,
        *,
        frame_key_shape: tuple[int, int] | None = None,
    ) -> tuple[int, ...]:
        fh, fw = (
            frame_key_shape
            if frame_key_shape is not None
            else (frame.shape[0], frame.shape[1])
        )
        sample = template[:: max(1, template.shape[0] // 16), :: max(1, template.shape[1] // 16)]
        return (
            fh,
            fw,
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
