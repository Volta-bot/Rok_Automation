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
from rok_automation.game.popups import close_chat
from rok_automation.game.screen_retry import MIN_SCREEN_SCANS
from rok_automation.vision.screen import ScreenDetector, ScreenState, TemplateHit, load_gray_frame


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CITY_TEMPLATES = _PROJECT_ROOT / "templates" / "city"

_RESOURCE_TEMPLATES = {
    resource: tuple(_CITY_TEMPLATES / f"resource_{resource.lower()}_{suffix}.png"
                    for suffix in ("icon", "white"))
    for resource in ("Food", "Wood", "Stone", "Gold")
}
_FORGE_ICON_TEMPLATE = _CITY_TEMPLATES / "forge_icon.png"
_FORGE_MATERIALS_BUTTON_TEMPLATE = _CITY_TEMPLATES / "forge_materials_button.png"
_FORGE_MATERIALS_CLOSE_TEMPLATE = _CITY_TEMPLATES / "forge_materials_close.png"
_MATERIAL_TEMPLATES = {
    "leather": _CITY_TEMPLATES / "material_leather.png",
    "iron": _CITY_TEMPLATES / "material_iron.png",
    "wood": _CITY_TEMPLATES / "material_wood.png",
    "bone": _CITY_TEMPLATES / "material_bone.png",
}

# Try the roof, then slightly deeper and shallower points on the building.
_FORGE_TAP_OFFSETS = (1.0, 1.4, 0.65)
_MENU_WAIT_SECONDS = 1.0
# Fractions of the canonical frame that avoid the top bar, side controls and
# bottom chat overlay while retaining the visible city buildings.
_RESOURCE_SCAN_AREA = (0.072, 0.143, 0.978, 0.892)
# Keep the lower-left chat feed and its interaction area out of resource clicks.
_CHAT_HUD_RIGHT = 0.45
_CHAT_HUD_TOP = 0.84
# The right commander portrait strip can resemble small material cards.
_FORGE_MATERIAL_SCAN_AREA = (0.072, 0.143, 0.90, 0.892)


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
        screenshot, _ = self._capture_without_chat()
        if screenshot is None:
            return
        if not self._is_still_in_city(screenshot):
            self._log("City view changed; skipping city tasks")
            return
        self._log("Zooming out slightly to reveal city icons")
        try:
            self.game.zoom_out_city()
        except AdbError as exc:
            self._log(f"Could not zoom out ({exc}); scanning the current city view")
        self.game.wait(0.50)
        screenshot, _ = self._capture_without_chat()
        if screenshot is None:
            return
        if not self._is_still_in_city(screenshot):
            self._log("Zoom left the city; skipping city tasks")
            return
        if remaining_resources and not self._collect_visible_resources(remaining_resources):
            return
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
        chat_right = round(frame_width * _CHAT_HUD_RIGHT) - left
        chat_top = round(frame_height * _CHAT_HUD_TOP) - top
        markers: list[tuple[str, tuple[int, int, int, int]]] = []
        for resource, templates in _RESOURCE_TEMPLATES.items():
            if resource not in resources:
                continue
            search_area = area.copy()
            search_area[chat_top:, :chat_right] = 0
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
                overlaps_chat = x < chat_right and y + height > chat_top
                if not overlaps_chat and not any(
                    abs(cx - (bx + bw / 2)) < max(width, bw) * 0.7
                    and abs(cy - (by + bh / 2)) < max(height, bh) * 0.7
                    for _, (bx, by, bw, bh) in markers
                ):
                    markers.append((resource, bounds))
                pad = max(8, round(max(width, height) * 0.5))
                search_area[max(0, y - pad):y + height + pad,
                            max(0, x - pad):x + width + pad] = 0
        return markers

    def _capture_without_chat(self) -> tuple[Path | None, bool]:
        for attempt in range(4):
            screenshot = self.game.screenshot()
            detection = self.detector.detect(
                screenshot, read_account=False, candidate_states={ScreenState.CHAT},
            )
            if detection.state is not ScreenState.CHAT:
                return screenshot, attempt > 0
            if attempt < 3:
                close_chat(self.game, screenshot, detection, log=self._log)
        self._log("Chat panel stayed open; stopping city tasks")
        return None, True

    def _collect_visible_resources(self, remaining_resources: set[str]) -> bool:
        resources = set(remaining_resources)
        screenshot, _ = self._capture_without_chat()
        # A chat interruption on the double-check gets one fresh scan.
        for pass_number in (1, 2, 3):
            if screenshot is None:
                return False
            frame = load_gray_frame(screenshot)
            if frame is None:
                self._log("Could not read the city view; skipping resource collection")
                return True
            markers = self._scan_resource_markers(frame, resources)
            if pass_number == 2:
                self._log(f"Double-check found {len(markers)} remaining resource marker(s)")
            for resource, bounds in markers:
                self.game.click(bounds, screenshot)
                remaining_resources.discard(resource)
            if markers:
                self._log(f"Clicked {len(markers)} resource marker(s) on pass {pass_number}")
            elif pass_number >= 2:
                break
            self.game.wait(0.50)
            screenshot, recovered_chat = self._capture_without_chat()
            if screenshot is None:
                return False
            if pass_number >= 2 and not recovered_chat:
                break
        return True

    def _locate_forge_material(self, frame, template: Path, kind: str):
        """Find a material inside an overhead bubble, excluding recipe labels."""
        frame_height, frame_width = frame.shape[:2]
        left, top, right, bottom = (
            round(value * dimension)
            for value, dimension in zip(
                _FORGE_MATERIAL_SCAN_AREA,
                (frame_width, frame_height, frame_width, frame_height),
            )
        )
        area = frame[top:bottom, left:right].copy()
        for _ in range(8):
            hit = self.detector._locate_in_frame(
                area, template, confidence_threshold=0.80,
                context_key=f"forge-overhead-{kind}",
                minimum_scale=0.15, refine_scale=True,
            )
            if hit is None:
                return None
            x, y, width, height = hit.bounds
            # Menu templates contain the material card, not the surrounding
            # white speech bubble. Recipe labels have no white base below them.
            pad = round(width * 0.25)
            base = area[y + height:y + height + round(width * 0.6),
                        max(0, x - pad):x + width + pad]
            # Stacked reward cards obscure more of the white bubble base.
            if base.size and (base >= 200).mean() >= 0.15:
                return TemplateHit(hit.confidence, (left + x, top + y, width, height))
            area[y:y + height, x:x + width] = 0
        return None

    def _find_forge_marker(self, screenshot: Path, frame) -> tuple[str, TemplateHit] | None:
        # Output can be any material, independent of the next requested recipe.
        idle = self.detector.locate_template(
            screenshot, _FORGE_ICON_TEMPLATE, confidence_threshold=0.72,
            context_key="forge-overhead-idle", minimum_scale=0.15, refine_scale=True,
        )
        if idle is not None:
            return "idle", idle
        hits = [
            (kind, hit) for kind, template in _MATERIAL_TEMPLATES.items()
            if (hit := self._locate_forge_material(frame, template, kind)) is not None
        ]
        return max(hits, key=lambda candidate: candidate[1].confidence, default=None)

    def _open_forge(self) -> tuple[Path, TemplateHit] | None:
        screenshot = self.game.screenshot()
        taps = 0
        for attempt in range(1, len(_FORGE_TAP_OFFSETS) + 1):
            if attempt > 1:
                self.game.wait(0.35)
                screenshot, _ = self._capture_without_chat()
                if screenshot is None or not self._is_still_in_city(screenshot):
                    self._log("City view unavailable; stopping forge retries")
                    return None

            frame = load_gray_frame(screenshot)
            if frame is None:
                self._log(f"Forge attempt {attempt}: could not read the city view")
                continue
            marker = self._find_forge_marker(screenshot, frame)
            if marker is None:
                self._log(f"Forge attempt {attempt}: no forge marker visible")
                continue

            kind, icon = marker
            x, y, width, height = icon.bounds
            frame_height, frame_width = frame.shape[:2]
            click_x = round(x + width / 2)
            target_height = height if kind == "idle" else max(width, height)
            click_y = round(y + target_height * (1 + _FORGE_TAP_OFFSETS[taps]))
            if not (0 <= click_x < frame_width and 0 <= click_y < frame_height):
                self._log(f"Forge attempt {attempt}: target is outside the city view")
                continue

            forge_bounds = (click_x, click_y, 1, 1)
            self.game.click(forge_bounds, screenshot)
            taps += 1
            if kind != "idle":
                # The game needs time to clear a claimed reward before selecting the building.
                self.game.wait(0.25)
                self.game.click(forge_bounds, screenshot)
            self.game.wait(_MENU_WAIT_SECONDS)
            screenshot, button = self._wait_for_menu_control(
                _FORGE_MATERIALS_BUTTON_TEMPLATE, context_key="forge-materials-button",
            )
            if button is not None:
                self._log("Opened forge building")
                return screenshot, button
            self._log(f"Forge attempt {attempt}: Materials button missing after {kind} marker tap at ({click_x}, {click_y})")
        return None

    def _claim_and_start_forge(self, material: str) -> bool:
        """Claim any completed output and select the requested material."""
        opened = self._open_forge()
        if opened is None:
            return False
        screenshot, materials_button = opened
        self.game.click(materials_button.bounds, screenshot)
        self.game.wait(_MENU_WAIT_SECONDS)

        screenshot, material_hit = self._wait_for_menu_control(
            _MATERIAL_TEMPLATES[material],
            context_key=f"forge-material-{material}",
        )
        if material_hit is None:
            self._log(f"Could not find the {material} material card; skipped")
            self._dismiss_material_menu()
            return False
        for selection in range(5):
            self.game.click(material_hit.bounds, screenshot)
            if selection < 4:
                self.game.wait(0.08)
        self.game.wait(0.50)
        self._dismiss_material_menu()
        self._log(f"Selected forge material five times: {material.title()}")
        return True

    def _wait_for_menu_control(self, template: Path, *, context_key: str):
        for _scan in range(MIN_SCREEN_SCANS):
            screenshot = self.game.screenshot()
            hit = self.detector.locate_template(
                screenshot, template, confidence_threshold=0.64, context_key=context_key,
            )
            if hit is not None:
                return screenshot, hit
            self.game.wait(0.25)
        return screenshot, None

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
