"""Read-only game screen classification using supplied UI templates."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import AbstractSet, Any

import cv2
import numpy as np
from rok_automation.performance import timed
from rok_automation.logic.resource_selector import MAX_RESOURCE_LEVEL, RESOURCE_LEVELS, Resource
from rok_automation.vision.account_label import (
    read_account_label,
    read_army_count,
    read_text_region,
    read_text_detections,
)
from rok_automation.vision.green_route import detect_green_incoming_route


class ScreenState(str, Enum):
    UNKNOWN = "unknown"
    MAILBOX = "mailbox"
    CHAT = "chat"
    CITY = "city"
    WORLD_MAP = "world_map"
    VIP = "vip"
    VIP_METAL_CHEST_REWARD = "vip_metal_chest_reward"
    VIP_WOODEN_CHEST_REWARD = "vip_wooden_chest_reward"
    CHIEN_DICH = "chien_dich"
    CHINH_PHAT = "chinh_phat"
    RESOURCE_SEARCH = "resource_search"
    RESOURCE_FOUND = "resource_found"
    NO_RESULT = "no_result"
    NEW_TROOPS_PROMPT = "new_troops_prompt"
    TROOP_COMPOSITION = "troop_composition"


_REWARD_MENUS = frozenset({ScreenState.VIP, ScreenState.CHIEN_DICH, ScreenState.CHINH_PHAT})
_DISPATCH_BUTTONS = (
    ScreenState.RESOURCE_FOUND,
    ScreenState.NEW_TROOPS_PROMPT,
    ScreenState.TROOP_COMPOSITION,
    ScreenState.RESOURCE_SEARCH,
)
_HUD_STATES = (ScreenState.MAILBOX, ScreenState.CITY, ScreenState.WORLD_MAP)
VIP_REWARD_POPUPS = frozenset({ScreenState.VIP_METAL_CHEST_REWARD, ScreenState.VIP_WOODEN_CHEST_REWARD})
_VIP_REWARD_GUARDED_STATES = VIP_REWARD_POPUPS | {ScreenState.VIP, ScreenState.CITY}
FARMING_SCREEN_STATES = frozenset((
    *_DISPATCH_BUTTONS, *_HUD_STATES, ScreenState.CHAT,
)) | VIP_REWARD_POPUPS
_SCREEN_STATES = FARMING_SCREEN_STATES | _REWARD_MENUS
_RESOURCE_DETAIL_STATES = frozenset({ScreenState.RESOURCE_SEARCH, ScreenState.RESOURCE_FOUND})
_BUTTON_LABELS = {
    ScreenState.RESOURCE_FOUND: ("thuthap", "thuth"),
    ScreenState.NEW_TROOPS_PROMPT: ("quanmoi", "quanmi", "quanm"),
    ScreenState.TROOP_COMPOSITION: ("hanhquan", "hanhq"),
    ScreenState.RESOURCE_SEARCH: ("timkiem", "timk"),
}
_DISPATCH_CONFIDENCE = 0.78
_STRONG_BUTTON_CONFIDENCE = 0.85
_DISPATCH_AMBIGUITY_MARGIN = 0.12
_SEARCH_TAB_GEOMETRY = {
    # Tab center and submit template scale at the canonical 1280px width.
    # The Barbarian panel has a wider footer than the resource panels.
    "barbarian": (0.21, 0.85),
    Resource.FOOD: (0.35, 0.65),
    Resource.WOOD: (0.50, 0.65),
    Resource.STONE: (0.65, 0.65),
    Resource.GOLD: (0.80, 0.65),
}
_LOWEST_SLIDER_LEVEL = 1


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
    search_slider: SearchSlider | None = None


@dataclass(frozen=True)
class TemplateHit:
    confidence: float
    bounds: tuple[int, int, int, int]


@dataclass(frozen=True)
class SearchSlider:
    level: int
    minus: TemplateHit
    plus: TemplateHit


@dataclass(frozen=True)
class _Template:
    state: ScreenState
    name: str
    path: Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_NAVIGATION = _PROJECT_ROOT / "templates/navigation"
_SEARCH = _PROJECT_ROOT / "templates/search"
_CHAT_CLOSE_TEMPLATE = _NAVIGATION / "chat_close_chevron.png"
_ALLIANCE_GATHER_TEMPLATE = _PROJECT_ROOT / "templates/gathering/gather_button_alliance.png"
_CAMPAIGN_CARD_TEMPLATE = _PROJECT_ROOT / "templates/daily_reward/chinh_phat_card.png"
_VIP_REWARD_TEMPLATES = _PROJECT_ROOT / "templates/daily_reward"
_VIP_REWARD_BANNERS = (
    (ScreenState.VIP_METAL_CHEST_REWARD, "vip_metal_reward", "Xin chúc mừng", .15, .25),
    (ScreenState.VIP_WOODEN_CHEST_REWARD, "vip_wooden_reward", "Thưởng điểm hàng ngày", .19, .31),
)
WORLD_SEARCH_MIN_CONFIDENCE = 0.78
VIEW_TOGGLE_TEMPLATES = (_NAVIGATION / "world_city_button.png", _NAVIGATION / "city_map_button.png")
VIEW_TOGGLE_MIN_CONFIDENCE = 0.78
_TEMPLATES = (
    _Template(
        ScreenState.MAILBOX,
        "mailbox tabs",
        _PROJECT_ROOT / "templates/navigation/mailbox_tabs.png",
    ),
    _Template(
        ScreenState.CITY,
        "city build button",
        _NAVIGATION / "city_build_button.png",
    ),
    _Template(
        ScreenState.WORLD_MAP,
        "world map search button",
        _NAVIGATION / "world_search_button.png",
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
_TEMPLATE_BY_STATE = {template.state: template for template in _TEMPLATES}
_TEMPLATE_CACHE: dict[Path, Any] = {}
# (frame size, template identity) -> scale step (percent) of a confident match.
_SCALE_CACHE: dict[tuple[int, ...], int] = {}
# (frame_h, frame_w, template_name, context_key) -> (x, y, w, h, scale_step)
_POSITION_CACHE: dict[tuple[int, int, str, Any], tuple[int, int, int, int, int]] = {}
# A None file timestamp marks a live frame that has not been written to disk.
_FRAME_CACHE: dict[Path, tuple[int | None, int, Any, Any]] = {}


# Normalized (left, top, right, bottom) search regions for static UI controls.
_TEMPLATE_ROIS: dict[str, tuple[float, float, float, float]] = {
    "territory_flag.png": (0.40, 0.35, 0.90, 0.75),
    "close.png": (0.75, 0.00, 1.00, 0.20),
    "mailbox_tabs.png": (0.00, 0.00, 0.14, 0.10),
    "mailbox_close.png": (0.88, 0.00, 1.00, 0.12),
    "chat_close_chevron.png": (0.53, 0.44, 0.60, 0.56),
    "unknown_menu_close_gold.png": (0.00, 0.00, 1.00, 0.12),
    "unknown_menu_close_light.png": (0.00, 0.00, 1.00, 0.12),
    "scout_manager_title.png": (0.20, 0.00, 0.80, 0.20),
    "scout_manager_close.png": (0.80, 0.00, 1.00, 0.18),
    "world_search_button.png": (0.00, 0.56, 0.16, 0.88),
    "city_build_button.png": (0.00, 0.56, 0.16, 0.88),
    "extended_view_mountains.png": (0.00, 0.45, 0.25, 0.85),
    "city_map_button.png": (0.00, 0.74, 0.16, 1.00),
    "world_city_button.png": (0.00, 0.74, 0.16, 1.00),
    "gather_button.png": (0.45, 0.44, 0.95, 0.86),
    "gather_button_alliance.png": (0.45, 0.44, 0.95, 0.86),
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
    "vip_shield.png": (0.071, 0.043, 0.125, 0.125),
    "wooden_chest.png": (0.80, 0.05, 0.98, 0.25),
    "green_nhan.png": (0.70, 0.40, 0.95, 0.60),
    "vip_close_x.png": (0.78, 0.00, 0.96, 0.22),
    "menu_dots.png": (0.88, 0.85, 1.00, 1.00),
    "menu_dots_glyph.png": (0.92, 0.89, 0.997, 0.985),
    "chien_dich.png": (0.50, 0.82, 0.65, 1.00),
    "chien_dich_swords.png": (0.53, 0.89, 0.60, 0.981),
    "menu_shield.png": (0.68, 0.88, 0.78, 1.00),
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
    "gather_button_alliance.png",
    "new_troops_button.png",
    "submit_button.png",
})


def _trim_file_frames() -> None:
    # Live handles must remain readable even when diagnostics load other files.
    # One live entry is overwritten on every capture by each connected interface.
    file_paths = [path for path, entry in _FRAME_CACHE.items() if entry[0] is not None]
    for path in file_paths[:-5]:
        del _FRAME_CACHE[path]


def cache_captured_frame(
    path: str | Path, bgr_frame: Any, *, memory_only: bool = False,
) -> None:
    """Store a freshly captured BGR frame in memory to avoid disk re-reads."""

    resolved = Path(path).resolve()
    if memory_only:
        mtime_ns, size = None, 0
    else:
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
    _trim_file_frames()
    _FRAME_CACHE[resolved] = (mtime_ns, size, bgr_frame, gray_frame)


def load_color_frame(path: str | Path) -> Any:
    """Load a BGR screenshot, serving from memory when unchanged on disk."""

    resolved = Path(path).resolve()
    cached = _FRAME_CACHE.get(resolved)
    if cached is not None and cached[0] is None:
        return cached[2]
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
    _trim_file_frames()
    _FRAME_CACHE[resolved] = (mtime_ns, size, bgr, gray)
    return bgr


def load_gray_frame(path: str | Path) -> Any:
    """Load a grayscale screenshot, serving from memory when unchanged on disk."""

    resolved = Path(path).resolve()
    cached = _FRAME_CACHE.get(resolved)
    if cached is not None and cached[0] is None:
        return cached[3]
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
    _trim_file_frames()
    _FRAME_CACHE[resolved] = (mtime_ns, size, bgr, gray)
    return gray


def save_frame(path: str | Path, destination: str | Path | None = None) -> Path:
    """Explicitly export a live or file-backed frame for diagnostics."""
    frame = load_color_frame(path)
    if frame is None:
        raise FileNotFoundError(f"Could not read screenshot: {path}")
    output = Path(destination if destination is not None else path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), frame):
        raise OSError(f"Could not save screenshot: {output}")
    # The exported path is file-backed and must notice later edits on disk.
    cache_captured_frame(output, frame)
    return output


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

    @timed("detection")
    def detect(
        self,
        screenshot_path: str | Path,
        *,
        expected_resource_level: int | None = None,
        read_account: bool = True,
        candidate_states: AbstractSet[ScreenState] | None = None,
        expected_states: tuple[ScreenState, ...] = (),
        context_key: Any = None,
        resource_details: AbstractSet[ScreenState] = _RESOURCE_DETAIL_STATES,
    ) -> ScreenDetection:
        """Classify one screenshot against the requested screens.

        Expected states are checked first for strong matches during transitions.
        Resource details and account reads can be limited for frequent polls.
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

        best = self._classify_screen(
            image_path, frame,
            _SCREEN_STATES if candidate_states is None else candidate_states,
            expected_states, context_key,
        )
        if best.state is ScreenState.RESOURCE_SEARCH and best.bounds is not None:
            slider = best.search_slider if best.state in resource_details else None
            active_res = self.identify_active_search_resource(best.bounds, frame.shape[1])
            evidence = best.evidence
            if isinstance(active_res, Resource):
                evidence = f"{evidence}; active resource: {active_res.value}"
            elif active_res:
                evidence = f"{evidence}; active tab: {active_res}"
            if slider is not None:
                evidence = f"{evidence}; slider at Cấp: {slider.level}"
            return replace(
                best,
                evidence=evidence,
                resource_level=slider.level if slider is not None else None,
                preferred_resource=active_res if isinstance(active_res, Resource) else None,
                search_slider=slider,
            )
        if best.state is ScreenState.RESOURCE_FOUND and best.state in resource_details:
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

    def _classify_screen(
        self,
        image_path: Path,
        frame: Any,
        candidates: AbstractSet[ScreenState],
        expected_states: tuple[ScreenState, ...],
        context_key: Any,
    ) -> ScreenDetection:
        if candidates & _VIP_REWARD_GUARDED_STATES:
            popup = self._match_vip_reward(image_path, frame)
            if popup is not None:
                if popup.state in candidates:
                    return popup
                return ScreenDetection(ScreenState.UNKNOWN, 0.0, popup.evidence)

        if candidates & _REWARD_MENUS:
            menu = self._match_reward_menu(image_path, frame, candidates)
            if menu is not None:
                return menu

        checked: dict[ScreenState, ScreenDetection | None] = {}

        def match(state: ScreenState) -> ScreenDetection | None:
            if state not in checked:
                checked[state] = (
                    self._match_search_panel(image_path, frame, context_key)
                    if state is ScreenState.RESOURCE_SEARCH
                    else self._match_screen_button(frame, state, context_key)
                )
            return checked[state]

        for state in expected_states:
            if state in candidates and state in _DISPATCH_BUTTONS:
                hit = match(state)
                if hit is not None and hit.confidence >= _STRONG_BUTTON_CONFIDENCE:
                    return hit

        if ScreenState.CHAT in candidates:
            chat = self._locate_in_frame(
                frame, _CHAT_CLOSE_TEMPLATE,
                confidence_threshold=max(0.88, self.confidence_threshold),
                minimum_scale=0.55, maximum_scale=1.65,
            )
            if chat is not None:
                return ScreenDetection(
                    ScreenState.CHAT, chat.confidence, "chat panel close chevron", chat.bounds,
                )

        # An open panel can leave the City or World Map HUD visible behind it.
        buttons = []
        for state in _DISPATCH_BUTTONS:
            if state not in candidates:
                continue
            hit = match(state)
            if hit is not None:
                if hit.confidence >= _STRONG_BUTTON_CONFIDENCE:
                    return hit
                buttons.append(hit)
        if buttons:
            buttons.sort(key=lambda hit: hit.confidence, reverse=True)
            first = buttons[0]
            if len(buttons) == 1 or first.confidence - buttons[1].confidence >= _DISPATCH_AMBIGUITY_MARGIN:
                return first
            state = self._read_button_label(image_path, first.bounds, candidates)
            if state is ScreenState.RESOURCE_SEARCH:
                search = match(state)
                if search is not None:
                    return search
            elif state is not None:
                return ScreenDetection(state, first.confidence, "dispatch button label OCR", first.bounds)
            return ScreenDetection(ScreenState.UNKNOWN, 0.0, "ambiguous button label could not be verified")

        hud = (match(state) for state in _HUD_STATES if state in candidates)
        return max(
            (hit for hit in hud if hit is not None),
            key=lambda hit: hit.confidence,
            default=ScreenDetection(ScreenState.UNKNOWN, 0.0, "no screen matched"),
        )

    def _match_screen_button(
        self, frame: Any, state: ScreenState, context_key: Any,
    ) -> ScreenDetection | None:
        template = _TEMPLATE_BY_STATE[state]
        confidence = self.confidence_threshold
        if state in _DISPATCH_BUTTONS:
            confidence = max(confidence, _DISPATCH_CONFIDENCE)
        hit = self._locate_in_frame(
            frame, template.path, confidence_threshold=confidence, context_key=context_key,
        )
        if hit is None and state is ScreenState.RESOURCE_FOUND:
            hit = self._locate_in_frame(
                frame, _ALLIANCE_GATHER_TEMPLATE, confidence_threshold=confidence,
            )
        if hit is not None:
            return ScreenDetection(state, hit.confidence, template.name, hit.bounds)
        return None

    def _match_search_panel(
        self, image_path: Path, frame: Any, context_key: Any,
    ) -> ScreenDetection | None:
        """Locate Search and both level controls in one resource panel."""
        height, width = frame.shape[:2]
        template = _load_gray_template(_SEARCH / "submit_button.png")
        # The popup is anchored over the selected bottom-row card.
        tabs = sorted(_SEARCH_TAB_GEOMETRY, key=lambda tab: tab != context_key)
        for tab in tabs:
            center, canonical_scale = _SEARCH_TAB_GEOMETRY[tab]
            center *= width
            submit = self._match_in_area(
                frame, template,
                (round(center - width * .10), round(height * .59),
                 round(center + width * .10), round(height * .77)),
                width * canonical_scale / 1280, max(self.confidence_threshold, .78),
            )
            if submit is None or not self._verify_blue_button_text(frame, template, submit.bounds):
                continue
            x, y, w, h = submit.bounds
            controls = []
            for name, offset in (("level_minus.png", -.92), ("level_plus.png", .92)):
                center_x = x + w / 2 + offset * w
                center_y = y - .50 * h
                button = self._match_in_area(
                    frame, _load_gray_template(_SEARCH / name),
                    (round(center_x - .23 * w), round(center_y - .33 * h),
                     round(center_x + .23 * w), round(center_y + .33 * h)),
                    w / template.shape[1], .82,
                )
                if button is None:
                    break
                controls.append(button)
            if len(controls) != 2:
                continue

            minus, plus = controls
            color = load_color_frame(image_path) if tab != "barbarian" else None
            slider = self._read_slider(color, minus, plus) if color is not None else None
            return ScreenDetection(
                ScreenState.RESOURCE_SEARCH, submit.confidence, "resource search panel",
                submit.bounds, search_slider=slider,
            )
        return None

    @classmethod
    def _match_in_area(
        cls, frame: Any, template: Any, area: tuple[int, int, int, int],
        scale: float, minimum_confidence: float,
    ) -> TemplateHit | None:
        left, top, right, bottom = area
        left, top = max(0, left), max(0, top)
        score, (x, y, matched_scale) = cls._best_scale_match(
            frame[top:bottom, left:right], template,
            minimum_scale=scale * .87, maximum_scale=scale * 1.15,
        )
        if score < minimum_confidence:
            return None
        height, width = cls._scaled_size(template, matched_scale)
        return TemplateHit(score, (left + x, top + y, width, height))

    @staticmethod
    def _read_slider(frame: Any, minus: TemplateHit, plus: TemplateHit) -> SearchSlider | None:
        mx, my, mw, mh = minus.bounds
        px, py, pw, ph = plus.bounds
        if abs((my + mh / 2) - (py + ph / 2)) > mh * .18 or px <= mx + mw:
            return None
        left = mx + mw
        top = max(0, min(my, py) - round(mh * .12))
        bottom = max(my + mh, py + ph) + round(mh * .12)
        track = frame[top:bottom, left:px]
        if not track.size:
            return None
        hsv = cv2.cvtColor(track, cv2.COLOR_BGR2HSV)
        blue = cv2.inRange(hsv, (80, 38, 135), (114, 255, 255))
        _, _, stats, centers = cv2.connectedComponentsWithStats(blue)
        handles = [
            cx + left
            for (_, _, width, height, area), (cx, cy) in zip(stats[1:], centers[1:])
            if mw * .25 <= width <= mw * .95
            and mh * .65 <= height <= mh * 1.25
            and area >= width * height * .5
            and abs(cy + top - (my + mh / 2)) <= mh * .18
        ]
        if len(handles) != 1:
            return None
        # The first and last detents are slightly inboard of the +/- buttons.
        minus_center = mx + mw / 2
        span = px + pw / 2 - minus_center
        position = (handles[0] - minus_center) / span
        last_detent = MAX_RESOURCE_LEVEL - _LOWEST_SLIDER_LEVEL
        detent = (position - .112) / .776 * last_detent
        index = round(detent)
        if not 0 <= index <= last_detent or abs(detent - index) > .25:
            return None
        return SearchSlider(_LOWEST_SLIDER_LEVEL + index, minus, plus)

    @staticmethod
    def _read_button_label(
        image_path: Path,
        bounds: tuple[int, int, int, int] | None,
        candidates: AbstractSet[ScreenState],
    ) -> ScreenState | None:
        if bounds is None:
            return None
        frame = load_color_frame(image_path)
        if frame is None:
            return None
        text = unicodedata.normalize(
            "NFD", " ".join(read_text_region(frame, bounds, upscale=1.5)).casefold(),
        )
        folded = "".join(char for char in text if char.isalnum()).replace("đ", "d")
        for state, keywords in _BUTTON_LABELS.items():
            if state in candidates and any(keyword in folded for keyword in keywords):
                return state
        return None

    def _match_vip_reward(self, image_path: Path, gray_frame: Any) -> ScreenDetection | None:
        frame = load_color_frame(image_path)
        if frame is None:
            return None
        h, w = gray_frame.shape[:2]
        sample = frame[round(h * .17):round(h * .30):4, round(w * .40):round(w * .60):4]
        if not sample.size:
            return None
        gold = cv2.inRange(sample, (0, 120, 210), (155, 220, 255))
        if cv2.countNonZero(gold) < gold.size * .35:
            return None

        scale = min(w / 1280, h / 720)
        for state, template, title, top, bottom in _VIP_REWARD_BANNERS:
            banner = self._match_in_area(
                gray_frame, _load_gray_template(_VIP_REWARD_TEMPLATES / f"{template}_banner.png"),
                (round(w * .34), round(h * top), round(w * .66), round(h * bottom)), scale, .85,
            )
            if banner is None:
                continue
            # Other rewards use gold banners too. Verify the dimmed VIP title.
            header = self._match_in_area(
                gray_frame, _load_gray_template(_VIP_REWARD_TEMPLATES / f"{template}_header.png"),
                (round(w * .46), round(h * .08), round(w * .54), round(h * .15)), scale, .87,
            )
            if header is not None:
                return ScreenDetection(
                    state, min(banner.confidence, header.confidence),
                    f"{title} banner over the VIP menu", banner.bounds,
                )
        return None

    def _match_reward_menu(
        self, image_path: Path, gray_frame: Any, candidates: AbstractSet[ScreenState],
    ) -> ScreenDetection | None:
        frame = load_color_frame(image_path)
        if frame is None:
            raise FileNotFoundError(f"Could not read screenshot: {image_path}")
        h, w = frame.shape[:2]
        # Menu titles occupy the top center; HUD VIP badges and campaign
        # cards elsewhere on the screen must not count as menu titles.
        roi = (int(w * 0.30), 0, int(w * 0.40), int(h * 0.18))
        titles = {
            "vip": (ScreenState.VIP, "VIP"),
            "chiendich": (ScreenState.CHIEN_DICH, "Chiến dịch"),
            "chindich": (ScreenState.CHIEN_DICH, "Chiến dịch"),
            "chindch": (ScreenState.CHIEN_DICH, "Chiến dịch"),
            "chinhphat": (ScreenState.CHINH_PHAT, "Chinh phạt"),
            "chinhpht": (ScreenState.CHINH_PHAT, "Chinh phạt"),
        }
        for text, bounds, confidence in read_text_detections(frame, roi):
            normalized = unicodedata.normalize("NFD", text.casefold())
            normalized = "".join(c for c in normalized if c.isalnum()).replace("đ", "d")
            if normalized in titles and confidence >= 0.70:
                state, label = titles[normalized]
                if state in candidates:
                    return ScreenDetection(state, confidence, f"{label} menu title OCR", bounds)
                return None
        # The Chinh phạt card belongs to the Chiến dịch selection menu.
        if ScreenState.CHIEN_DICH not in candidates:
            return None
        hit = self._locate_in_frame(
            gray_frame, _CAMPAIGN_CARD_TEMPLATE,
            confidence_threshold=0.88,
        )
        if hit is not None:
            return ScreenDetection(
                ScreenState.CHIEN_DICH, hit.confidence, "Chiến dịch: Chinh phạt card", hit.bounds,
            )
        return None

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

    @staticmethod
    def invalidate_position(template_path: str | Path, *, context_key: Any = None) -> None:
        """Forget a stale position for one control and resource across frame sizes."""
        name = Path(template_path).name
        for key in list(_POSITION_CACHE):
            if key[2] == name and key[3] == context_key:
                del _POSITION_CACHE[key]

    @classmethod
    @timed("detection")
    def locate_template(
        cls,
        screenshot_path: str | Path,
        template_path: str | Path,
        *,
        confidence_threshold: float = 0.68,
        context_key: Any = None,
        fast_only: bool = False,
        minimum_scale: float = 0.35,
        refine_scale: bool = False,
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
            fast_only=fast_only,
            minimum_scale=minimum_scale,
            refine_scale=refine_scale,
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
        fast_only: bool = False,
        minimum_scale: float = 0.35,
        refine_scale: bool = False,
    ) -> TemplateHit | None:
        """Fast ROI + pinned-position template matcher for static UI buttons."""

        image_template = _load_gray_template(template_path)
        template_name = template_path.name
        if template_name == "world_search_button.png":
            confidence_threshold = max(confidence_threshold, WORLD_SEARCH_MIN_CONFIDENCE)
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
            # Truly static controls can be checked at the exact learned
            # position without even searching the small neighbourhood.
            exact = frame[py:py+ph, px:px+pw]
            if exact.shape[:2] == (ph, pw):
                score, _ = cls._match_at_scale(exact, image_template, p_step)
                if score >= max(confidence_threshold, 0.72) and (
                    template_name not in _BLUE_TEXT_TEMPLATES
                    or cls._verify_blue_button_text(frame, image_template, (px, py, pw, ph))
                ):
                    return TemplateHit(score, (px, py, pw, ph))
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
                _POSITION_CACHE[(frame_h, frame_w, template_name, cand_ctx)] = (hit_x, hit_y, pw, ph, p_step)
                return TemplateHit(score, (hit_x, hit_y, pw, ph))

        if fast_only:
            return None

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
            minimum_scale=minimum_scale,
            maximum_scale=maximum_scale,
            frame_key_shape=(frame_h, frame_w),
        )
        if refine_scale:
            # At small scales, a lookalike can win the coarse scan while the
            # real icon falls between steps. Always check these scales as well.
            coarse_step = round(match[2] * 100)
            minimum_step = round(minimum_scale * 100)
            maximum_step = round(maximum_scale * 100)
            small_steps = range(minimum_step, min(maximum_step, 25) + 1)
            nearby_steps = range(max(minimum_step, coarse_step - 4),
                                 min(maximum_step, coarse_step + 4) + 1)
            for step in sorted(set(small_steps).union(nearby_steps) - {coarse_step}):
                refined_score, location = cls._match_at_scale(roi_frame, image_template, step)
                if refined_score > score:
                    score = refined_score
                    match = (location[0], location[1], step / 100.0)
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
                minimum_scale=minimum_scale,
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
