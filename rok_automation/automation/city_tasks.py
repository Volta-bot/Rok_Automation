"""Collect city resources and manage one forge production queue.

City buildings sometimes hide their ready icons. Zoom out slightly once before
scanning and stop if that gesture has changed the view to the world map.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from rok_automation.game.adb import AdbError
from rok_automation.game.console import safe_print
from rok_automation.game.interface import GameInterface
from rok_automation.vision.screen import ScreenDetector, ScreenState, load_gray_frame


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CITY_TEMPLATES = _PROJECT_ROOT / "templates" / "city"

_RESOURCE_TEMPLATES = {
    resource: tuple(_CITY_TEMPLATES / f"resource_{resource.lower()}_{suffix}.png"
                    for suffix in ("icon", "white"))
    for resource in ("Food", "Wood", "Stone", "Gold")
}
_FORGE_READY_TEMPLATE = _CITY_TEMPLATES / "forge_ready_icon.png"
_FORGE_BUILDING_TEMPLATES = (
    _CITY_TEMPLATES / "forge_building.png",
    _CITY_TEMPLATES / "forge_building_alternate.png",
)
_FORGE_MATERIALS_BUTTON_TEMPLATE = _CITY_TEMPLATES / "forge_materials_button.png"
_FORGE_MATERIALS_CLOSE_TEMPLATE = _CITY_TEMPLATES / "forge_materials_close.png"
_MATERIAL_TEMPLATES = {
    "leather": _CITY_TEMPLATES / "material_leather.png",
    "iron": _CITY_TEMPLATES / "material_iron.png",
    "wood": _CITY_TEMPLATES / "material_wood.png",
    "bone": _CITY_TEMPLATES / "material_bone.png",
}

_MENU_WAIT_SECONDS = 1.0
# Fractions of the canonical frame that avoid the top bar, side controls and
# bottom chat overlay while retaining the visible city buildings.
_RESOURCE_SCAN_AREA = (0.072, 0.143, 0.978, 0.892)


class CityTaskRunner:
    """Perform the enabled city actions once after a farming pass."""

    def __init__(
        self,
        game: GameInterface,
        detector: ScreenDetector,
        *,
        log: Callable[[str], None] = safe_print,
    ) -> None:
        self.game = game
        self.detector = detector
        self.log = log

    def run(
        self,
        *,
        collect_resources: bool,
        forge_material: str | None,
    ) -> None:
        """Collect each visible resource type and optionally refill the forge.

        Claim a completed forge output if present, then open the forge and
        select the requested material regardless of output readiness.
        """

        material = (forge_material or "").casefold()
        if material not in _MATERIAL_TEMPLATES:
            material = ""
        if not collect_resources and not material:
            return

        remaining_resources = set(_RESOURCE_TEMPLATES) if collect_resources else set()
        forge_pending = bool(material)
        screenshot = self.game.screenshot()
        if not self._is_still_in_city(screenshot):
            self._log("City view changed; skipping city tasks")
            return
        self._log("Zooming out slightly to reveal city icons")
        try:
            self.game.zoom_out_city()
        except AdbError as exc:
            self._log(f"Could not zoom out ({exc}); scanning the current city view")
        self.game.wait(0.50)
        screenshot = self.game.screenshot()
        if not self._is_still_in_city(screenshot):
            self._log("Zoom left the city; skipping city tasks")
            return
        if remaining_resources:
            self._collect_visible_resources(remaining_resources)
        if forge_pending and self._claim_and_start_forge(material):
            forge_pending = False

        if remaining_resources:
            self._log(
                "No ready city icon found for " + ", ".join(sorted(remaining_resources))
            )
        if forge_pending:
            self._log("Forge task could not be started; skipped")

    def _scan_resource_markers(
        self, frame, resources: set[str],
    ) -> list[tuple[str, tuple[int, int, int, int]]]:
        """Find all visible ready markers in one screenshot."""
        frame_height, frame_width = frame.shape[:2]
        left, top, right, bottom = (
            round(value * dimension)
            for value, dimension in zip(
                _RESOURCE_SCAN_AREA,
                (frame_width, frame_height, frame_width, frame_height),
            )
        )
        area = frame[top:bottom, left:right]
        markers: list[tuple[str, tuple[int, int, int, int]]] = []
        for resource, templates in _RESOURCE_TEMPLATES.items():
            if resource not in resources:
                continue
            search_area = area.copy()
            for _ in range(24):
                hits = [
                    hit for template in templates
                    if (hit := self.detector._locate_in_frame(
                        search_area, template, confidence_threshold=0.82,
                        context_key=f"city-resource-{resource}", maximum_scale=1.35,
                    )) is not None
                ]
                hit = max(hits, key=lambda candidate: candidate.confidence, default=None)
                if hit is None:
                    break
                x, y, width, height = hit.bounds
                bounds = (left + x, top + y, width, height)
                cx, cy = bounds[0] + width / 2, bounds[1] + height / 2
                # Both templates, or different resource templates, can match
                # the same bubble. Keep only one click for each position.
                if not any(
                    abs(cx - (bx + bw / 2)) < max(width, bw) * 0.7
                    and abs(cy - (by + bh / 2)) < max(height, bh) * 0.7
                    for _, (bx, by, bw, bh) in markers
                ):
                    markers.append((resource, bounds))
                pad = max(8, round(max(width, height) * 0.5))
                search_area[max(0, y - pad):y + height + pad,
                            max(0, x - pad):x + width + pad] = 0
        return markers

    def _collect_visible_resources(self, remaining_resources: set[str]) -> None:
        resources = set(remaining_resources)
        for pass_number in (1, 2):
            screenshot = self.game.screenshot()
            frame = load_gray_frame(screenshot)
            if frame is None:
                self._log("Could not read the city view; skipping resource collection")
                return
            markers = self._scan_resource_markers(frame, resources)
            if pass_number == 2:
                self._log(f"Double-check found {len(markers)} remaining resource marker(s)")
            for resource, bounds in markers:
                self.game.click(bounds, screenshot)
                remaining_resources.discard(resource)
            if markers:
                self._log(f"Clicked {len(markers)} resource marker(s) on pass {pass_number}")
                self.game.wait(0.50)
            elif pass_number == 1:
                # A second image can reveal icons obscured by animation.
                self.game.wait(0.50)

    def _claim_and_start_forge(self, material: str) -> bool:
        """Claim any completed output and select the requested material."""

        screenshot = self.game.screenshot()
        forge_hits = [
            hit for template in _FORGE_BUILDING_TEMPLATES
            if (hit := self.detector.locate_template(
                screenshot, template, confidence_threshold=0.70,
                context_key="forge-building",
            )) is not None
        ]
        forge = max(forge_hits, key=lambda hit: hit.confidence, default=None)
        if forge is None:
            self._log("Forge building was not visible; skipped")
            return False
        frame = load_gray_frame(screenshot)
        if frame is None:
            self._log("Could not read the city view; skipped forge")
            return False
        x, y, width, height = forge.bounds
        frame_height, frame_width = frame.shape[:2]
        left = max(0, x - width // 3)
        top = max(0, y - height)
        right = min(frame_width, x + width + width // 3)
        bottom = min(frame_height, y + height // 2)
        ready = self.detector._locate_in_frame(
            frame[top:bottom, left:right],
            _FORGE_READY_TEMPLATE,
            confidence_threshold=0.80,
            context_key="forge-ready-near-building",
            maximum_scale=1.35,
        )
        if ready is not None:
            rx, ry, rw, rh = ready.bounds
            self.game.click((left + rx, top + ry, rw, rh), screenshot)
            self.game.wait(0.45)
            self._log("Claimed completed forge output")
        else:
            self._log("No completed forge output found; opening forge anyway")

        # The output disappears when claimed, so retain the building's
        # position from the same frame and open it after claiming.
        screenshot = self.game.screenshot()
        self.game.click(forge.bounds, screenshot)
        self._log("Opened forge building")
        self.game.wait(_MENU_WAIT_SECONDS)

        screenshot = self.game.screenshot()
        materials_button = self.detector.locate_template(
            screenshot,
            _FORGE_MATERIALS_BUTTON_TEMPLATE,
            confidence_threshold=0.64,
            context_key="forge-materials-button",
        )
        if materials_button is None:
            self._log("Forge material button did not appear; skipped")
            return True
        self.game.click(materials_button.bounds, screenshot)
        self.game.wait(_MENU_WAIT_SECONDS)

        screenshot = self.game.screenshot()
        material_hit = self.detector.locate_template(
            screenshot,
            _MATERIAL_TEMPLATES[material],
            confidence_threshold=0.64,
            context_key=f"forge-material-{material}",
        )
        if material_hit is None:
            self._log(f"Could not find the {material} material card; skipped")
            return True
        for selection in range(5):
            self.game.click(material_hit.bounds, screenshot)
            if selection < 4:
                self.game.wait(0.08)
        self.game.wait(0.50)
        self._dismiss_material_menu()
        self._log(f"Selected forge material five times: {material.title()}")
        return True

    def _dismiss_material_menu(self) -> None:
        """Close the material dialog when choosing a card leaves it open."""

        screenshot = self.game.screenshot()
        close = self.detector.locate_template(
            screenshot,
            _FORGE_MATERIALS_CLOSE_TEMPLATE,
            confidence_threshold=0.64,
            context_key="forge-materials-close",
        )
        if close is None:
            return
        self.game.click(close.bounds, screenshot)
        self.game.wait(0.35)

    def _is_still_in_city(self, screenshot: Path) -> bool:
        detection = self.detector.detect(
            screenshot,
            read_account=False,
            candidate_states={ScreenState.CITY, ScreenState.WORLD_MAP},
            context_key="city-tasks",
        )
        return detection.state is ScreenState.CITY

    def _log(self, message: str) -> None:
        self.log(f"[City Tasks] {message}")
