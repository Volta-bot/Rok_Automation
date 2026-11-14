"""Collect city resources and manage one forge production queue.

City buildings sometimes hide their ready icons. Zoom out slightly once before
scanning and stop if that gesture has changed the view to the world map.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from rok_automation.game.console import safe_print
from rok_automation.game.interface import GameInterface
from rok_automation.vision.screen import ScreenDetector, ScreenState


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CITY_TEMPLATES = _PROJECT_ROOT / "templates" / "city"

_RESOURCE_TEMPLATES = {
    resource: tuple(_CITY_TEMPLATES / f"resource_{resource.lower()}_{suffix}.png"
                    for suffix in ("icon", "white"))
    for resource in ("Food", "Wood", "Stone", "Gold")
}
_FORGE_READY_TEMPLATE = _CITY_TEMPLATES / "forge_ready_icon.png"
_FORGE_BUILDING_TEMPLATE = _CITY_TEMPLATES / "forge_building.png"
_FORGE_MATERIALS_BUTTON_TEMPLATE = _CITY_TEMPLATES / "forge_materials_button.png"
_FORGE_MATERIALS_CLOSE_TEMPLATE = _CITY_TEMPLATES / "forge_materials_close.png"
_MATERIAL_TEMPLATES = {
    "leather": _CITY_TEMPLATES / "material_leather.png",
    "iron": _CITY_TEMPLATES / "material_iron.png",
    "wood": _CITY_TEMPLATES / "material_wood.png",
    "bone": _CITY_TEMPLATES / "material_bone.png",
}

_MENU_WAIT_SECONDS = 1.0


class CityTaskRunner:
    """Perform the enabled city actions once before a farming pass."""

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

        A forge task starts only after its ready-output icon is found and
        claimed. If the output is not ready, there is no safe action to take,
        so the forge is skipped for this account.
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
        self.game.zoom_out_city()
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
            self._log("Forge output was not ready or the forge was not visible; skipped")

    def _collect_visible_resources(self, remaining_resources: set[str]) -> None:
        for resource, templates in _RESOURCE_TEMPLATES.items():
            if resource not in remaining_resources:
                continue
            screenshot = self.game.screenshot()
            hits = [
                hit for template in templates
                if (hit := self.detector.locate_template(
                    screenshot,
                    template,
                    confidence_threshold=0.64,
                    context_key=f"city-resource-{resource}",
                )) is not None
            ]
            hit = max(hits, key=lambda candidate: candidate.confidence, default=None)
            if hit is None:
                continue
            self.game.click(hit.bounds, screenshot)
            self.game.wait(0.25)
            remaining_resources.remove(resource)
            self._log(f"Collected ready {resource.lower()} buildings")

    def _claim_and_start_forge(self, material: str) -> bool:
        """Claim a completed forge output, then select the next material."""

        screenshot = self.game.screenshot()
        ready = self.detector.locate_template(
            screenshot,
            _FORGE_READY_TEMPLATE,
            confidence_threshold=0.64,
            context_key="forge-ready",
        )
        if ready is None:
            return False

        self.game.click(ready.bounds, screenshot)
        self.game.wait(0.45)
        self._log("Claimed completed forge output")

        screenshot = self.game.screenshot()
        forge = self.detector.locate_template(
            screenshot,
            _FORGE_BUILDING_TEMPLATE,
            confidence_threshold=0.62,
            context_key="forge-building",
        )
        if forge is None:
            self._log("Forge building was not visible after claiming output; skipped")
            return True
        self.game.click(forge.bounds, screenshot)
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
        self.game.click(material_hit.bounds, screenshot)
        self.game.wait(0.50)
        self._dismiss_material_menu()
        self._log(f"Started forge production: {material.title()}")
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
