"""Attempt one alliance gathering march, then leave normal farming to the caller."""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import uuid
from pathlib import Path

import cv2

from rok_automation.automation.dispatch import DispatchScreenReader
from rok_automation.vision.account_label import read_text_detections
from rok_automation.vision.button_positions import STATIC_BUTTON_POSITIONS
from rok_automation.game.console import format_log
from rok_automation.storage import write_json
from rok_automation.game.screen_retry import MIN_SCREEN_SCANS
from rok_automation.vision.screen import ScreenState, load_color_frame

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLIANCE_STATE_PATH = Path(os.environ.get("ROK_STATE_DIR", _ROOT / "state")) / "alliance_nodes.json"
_TEMPLATES = _ROOT / "templates" / "alliance"
MIN_REMAINING = 50_000_000
ACTION_ATTEMPTS = 5
_ALLIANCE_CHECK_SESSION = uuid.uuid4().hex


class _AllianceHasNoNode(Exception):
    """The resource-center header explicitly reports zero built nodes."""


def normalized(text):
    text = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)))


def remaining_resources(text):
    """Parse only a labelled remaining amount; troop counts are not stockpiles."""
    match = re.search(r"(?:c[oò]n|kho\s*d[uự]\s*tr[uữ])\s*:?\s*([\d][\d.,\s]*)", text, re.I)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def preset_error(text):
    text = normalized(text)
    return ("khongthetai" in text or "khongotrongthanhpho" in text
            or "chihuychinhkhong" in text and "thanhpho" in text)


class AllianceResourceFarmer:
    def __init__(self, game, detector, *, log=print, state_path=DEFAULT_ALLIANCE_STATE_PATH):
        self.game, self.detector = game, detector
        self.dispatch_reader = DispatchScreenReader(detector)
        self.log = lambda message: log(format_log(message, "Alliance"))
        self.state_path = state_path
        self.march_attempted = False
        self._words = None
        self.button_positions = STATIC_BUTTON_POSITIONS
        self.nodes = {}
        try:
            data = json.loads(state_path.read_text(encoding="utf-8")) if state_path else {}
            self.nodes = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            pass

    def _capture(self):
        self.shot = self.game.screenshot()
        self.frame = load_color_frame(self.shot)
        if self.frame is None:
            raise RuntimeError("unreadable alliance screen")
        self._words = None

    @property
    def words(self):
        if self._words is None:
            h, w = self.frame.shape[:2]
            self._words = read_text_detections(self.frame, (0, 0, w, h), upscale=1.5)
        return self._words

    @words.setter
    def words(self, value):
        self._words = value

    def _click(self, bounds):
        self.game.click(bounds, self.shot)
        self.game.wait(0.35)

    def _text(self, *labels, area=None):
        stable = normalized(labels[0]) in {
            "lienminh", "lanhtho", "hanhquan", "chamdegianhap",
            "thuthap", "thunhap", "quanmoi", "nhan",
        }
        key = ('label', tuple(normalized(label) for label in labels), area)
        if stable:
            cached = self.button_positions.locate(self.frame, key)
            if cached:
                return cached
        for text, bounds, score in self.words:
            if score < 0.45:
                continue
            x, y, w, h = bounds
            if area:
                fh, fw = self.frame.shape[:2]
                if not (area[0] <= (x + w / 2) / fw <= area[2] and area[1] <= (y + h / 2) / fh <= area[3]):
                    continue
            if any(normalized(label) in normalized(text) for label in labels):
                if stable:
                    self.button_positions.remember(self.frame, key, bounds)
                return bounds
        return None

    def _verified_action(self, label, find, succeeded):
        """Re-read before and after each click; never advance on a click alone."""
        for attempt in range(1, ACTION_ATTEMPTS + 1):
            self._capture()
            if succeeded():
                self.log(f"Verified {label}")
                return True
            self.log(f"{label}: attempt {attempt}/{ACTION_ATTEMPTS}")
            bounds = find()
            if bounds:
                self._click(bounds)
                # Allow a delayed transition before issuing another click.
                for _ in range(MIN_SCREEN_SCANS):
                    self._capture()
                    if succeeded():
                        self.log(f"Verified {label}")
                        return True
                    self.game.wait(.3)
            else:
                self.log(f"Button for {label} not found; retrying")
                self.game.wait(.3)
        self.log(f"Could not verify {label} after {ACTION_ATTEMPTS} attempts; skipping alliance farming")
        return False

    def _text_button(self, labels, area):
        if normalized(labels[0]) == "lienminh":
            return self._alliance_menu_button()
        if normalized(labels[0]) == "lanhtho":
            return self._template("territory_flag") or self._territory_label_icon_bounds()
        if normalized(labels[0]) == "quanmoi":
            return self._new_troops_bounds()
        return self._text(*labels, area=area)

    def _press_once(self, label, *, area=None, already_done=None):
        """One click only for claim/section toggles; never repeat a toggle."""
        bounds = None
        for _scan in range(MIN_SCREEN_SCANS):
            self._capture()
            if already_done is not None and already_done():
                self.log(f"{label}: already completed/open; no click needed")
                return True
            bounds = self._text(label, area=area)
            if bounds:
                break
            self.game.wait(.3)
        if not bounds:
            self.log(f"{label}: button not found; not retrying this click")
            return False
        self.log(f"Clicking {label} once")
        self._click(bounds)
        self._capture()
        return True

    def _scroll_territory(self, *, up):
        h, w = self.frame.shape[:2]
        low, high = (int(w * .78), int(h * .8)), (int(w * .78), int(h * .4))
        self.game.drag(high if up else low, low if up else high, self.shot)
        self.game.wait(.3)
        self._capture()

    def _reopen_resource_section(self):
        self.log("Coordinates not found: scrolling back up to the alliance resource-center section")
        self._capture()
        for attempt in range(ACTION_ATTEMPTS + 1):
            self._check_resource_center_count()
            bounds = self._text("trung tam tai nguyen")
            if bounds:
                self.log("Reopening Trung Tâm Tài Nguyên Liên Minh with one click")
                self._click(bounds)
                self._capture()
                return True
            if attempt < ACTION_ATTEMPTS:
                self._scroll_territory(up=True)
        self.log("Could not find the alliance resource-center header after scrolling back up")
        return False

    def _find_alliance_node(self, preferred_coordinates=None):
        for cycle in range(ACTION_ATTEMPTS):
            self.log(f"Scanning alliance node coordinates: pass {cycle + 1}/{ACTION_ATTEMPTS}")
            self._capture()
            for scroll in range(ACTION_ATTEMPTS + 1):
                self._check_resource_center_count()
                node, bounds = self._node(preferred_coordinates)
                if node:
                    self.log("Verified alliance resource node coordinates and remaining resources")
                    return node, bounds
                if scroll < ACTION_ATTEMPTS:
                    self._scroll_territory(up=False)
            if cycle < ACTION_ATTEMPTS - 1 and not self._reopen_resource_section():
                break
        return None, None

    def _resource_center_count(self):
        # Associate the counter with its own header, not a fortress/flag
        # counter elsewhere in the territory menu.
        for text, (x, y, w, h), score in self.words:
            if score < .65 or 'trungtamtainguyen' not in normalized(text):
                continue
            for count_text, (cx, cy, cw, ch), count_score in self.words:
                if count_score < .7 or cx + cw < x + w:
                    continue
                if abs((cy + ch / 2) - (y + h / 2)) > max(h, ch):
                    continue
                match = re.search(r'(?<!\d)([0-4Oo])\s*[/／]\s*4(?!\d)', count_text)
                if match:
                    return int(match[1]) if match[1].isdigit() else 0
        return None

    def _check_resource_center_count(self):
        if self._resource_center_count() == 0:
            raise _AllianceHasNoNode()

    def _press_text(self, *labels, area=None, expected=None):
        self.log(f"Looking for {labels[0]}")
        if expected is not None:
            return self._verified_action(labels[0], lambda: self._text_button(labels, area), expected)
        for _ in range(ACTION_ATTEMPTS):
            self._capture()
            if normalized(labels[0]) == "lienminh":
                # OCR can merge adjacent bottom-menu labels into one box.
                # Its centre lands on Items instead of the Alliance shield.
                bounds = self._alliance_menu_button()
            elif normalized(labels[0]) == "lanhtho":
                bounds = self._template("territory_flag")
                if not bounds:
                    bounds = self._territory_label_icon_bounds()
            elif normalized(labels[0]) == "quanmoi":
                bounds = self._new_troops_bounds()
            else:
                bounds = self._text(*labels, area=area)
            if bounds:
                self.log(f"Clicking {labels[0]}")
                self._click(bounds)
                return True
            self.game.wait(0.2)
        self.log(f"Could not find {labels[0]}; continuing without this action")
        return False

    def _new_troops_bounds(self):
        return self._dispatch_bounds(ScreenState.NEW_TROOPS_PROMPT)

    def _dispatch_bounds(self, state):
        detection = self.dispatch_reader.read(self.shot, expected_states=(state,))
        return detection.bounds if detection.state is state else None

    def _press_dispatch(self, state, expected):
        return self._verified_action(state.value, lambda: self._dispatch_bounds(state), expected)

    def _alliance_label_icon_bounds(self):
        fh, fw = self.frame.shape[:2]
        for text, (x, y, w, h), score in self.words:
            if (normalized(text) == "lienminh" and score >= .5
                    and .4 < (x + w / 2) / fw < .85 and y > fh * .88
                    and w < fw * .14):
                return (round(x + w / 2) - 5, round(y - fh * .055) - 5, 10, 10)
        return None

    def _alliance_menu_button(self):
        cached = self.button_positions.locate(self.frame, 'alliance_shield')
        if cached:
            self.log(f"Using remembered Liên Minh button at {cached}")
            return cached
        # Search the actual bottom menu at realistic icon sizes, rather than
        # accepting a tiny global match on another control.
        template = cv2.imread(str(_TEMPLATES / 'menu_shield.png'))
        fh, fw = self.frame.shape[:2]
        left, top = round(fw * .45), round(fh * .82)
        roi = self.frame[top:fh, left:round(fw * .9)]
        best_score, best_bounds = -1, None
        if template is not None:
            for step in range(65, 201, 3):
                scale = step / 100
                w, h = round(template.shape[1] * scale), round(template.shape[0] * scale)
                if w > roi.shape[1] or h > roi.shape[0]:
                    continue
                resized = cv2.resize(template, (w, h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
                _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(roi, resized, cv2.TM_CCOEFF_NORMED))
                if score > best_score:
                    best_score = score
                    best_bounds = (left + point[0], top + point[1], w, h)
        self.log(f"Liên Minh shield match: {best_score:.3f} at {best_bounds} (frame {fw}x{fh})")
        if best_score >= .85:
            self.button_positions.remember(self.frame, 'alliance_shield', best_bounds)
            return best_bounds
        return self._alliance_label_icon_bounds()

    def _territory_label_icon_bounds(self):
        fh, fw = self.frame.shape[:2]
        for text, (x, y, w, h), score in self.words:
            if (normalized(text) in {"lanhtho", "lanhth"} and score >= .5
                    and .4 < (x + w / 2) / fw < .9
                    and .45 < (y + h / 2) / fh < .8 and w < fw * .16):
                # The label sits below the red flag. Click the icon centre,
                # not the similarly named territory count in the left panel.
                return (round(x + w / 2) - 5, round(y - fh * .075) - 5, 10, 10)
        return None

    def _preset_one_bounds(self, attempts=ACTION_ATTEMPTS):
        # The small preset buttons may appear after the deployment screen's
        # other controls. Wait for them and use enlarged OCR if the template
        # misses a scale or highlight variation.
        for attempt in range(attempts):
            self._capture()
            bounds = self._match_preset_icon()
            if bounds:
                self.log(f"Found troop preset #1 at {bounds}")
                return bounds
            fh, fw = self.frame.shape[:2]
            region = (round(fw * .78), round(fh * .24), round(fw * .21), round(fh * .28))
            words = read_text_detections(self.frame, region, upscale=4)
            for text, (x, y, w, h), score in words:
                if text.strip() == "1" and score >= .7:
                    # Click the button's centre, using a small box so input
                    # randomisation cannot reach the adjacent preset.
                    return (round(x + w / 2) - 4, round(y + h / 2) - 4, 8, 8)
            if attempt < attempts - 1:
                self.log("Waiting for troop preset #1 on the right side")
                self.game.wait(.35)
        return None

    def _world_visible(self):
        # Candidate-only world detection can mistake an overlay for the map.
        # Inspect gather/deployment buttons before the map HUD: the Search
        # button remains visible behind the alliance resource popup.
        detection = self.dispatch_reader.read(self.shot)
        return detection.state is ScreenState.WORLD_MAP and not self._template("close")

    def _territory_visible(self):
        return bool(self._text("lanh tho", "lanh th", area=(.2, 0, .85, .14))
                    or self._text("trung tam tai nguyen"))

    def _resource_cards_visible(self):
        return self._node()[0] is not None or bool(self._text(
            "kho thoc", "lo go", "bai da", "quang goc", "qung goc", area=(.1, .3, .9, .9)))

    def _claim_ready(self):
        bounds = self._text("nhan", area=(.65, .1, .9, .27))
        if not bounds:
            return False
        x, y, w, h = bounds
        hsv = cv2.cvtColor(self.frame[y:y+h, x:x+w], cv2.COLOR_BGR2HSV)
        return bool(((hsv[:, :, 0] > 80) & (hsv[:, :, 0] < 115)
                     & (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 100)).mean() > .2)

    def _deployment_signature(self):
        fh, fw = self.frame.shape[:2]
        return tuple(normalized(text) for text, (x, y, w, h), score in self.words
                     if score >= .5 and ((x < fw * .4 and fh * .14 < y < fh * .4)
                                         or (fw * .5 < x < fw * .9 and fh * .14 < y < fh * .78)))

    def _load_preset(self):
        before = None

        def find():
            nonlocal before
            bounds = self._preset_one_bounds(attempts=1)
            if bounds:
                before = self._deployment_signature()
            return bounds

        def loaded():
            if preset_error(" ".join(t for t, _, _ in self.words)):
                raise RuntimeError("preset commander is outside the city")
            return (before is not None and self._deployment_signature() != before
                    and bool(self._dispatch_bounds(ScreenState.TROOP_COMPOSITION)))

        return self._verified_action("load troop preset #1 (verify commanders/troops changed)", find, loaded)

    def _match_preset_icon(self):
        cached = self.button_positions.locate(self.frame, 'troop_preset_1')
        if cached:
            self.log(f"Using remembered preset #1 button at {cached}")
            return cached
        """Match inside the preset column so unrelated global hits cannot win."""
        template = cv2.imread(str(_TEMPLATES / "preset_1_button.png"))
        other = cv2.imread(str(_TEMPLATES / "preset_2_button.png"))
        if template is None:
            return None
        fh, fw = self.frame.shape[:2]
        # Deployment dialogs can be inset (the preset column is then around
        # 86% of the capture width), even though cropped user screenshots
        # make the buttons look flush with the right edge.
        left, top = round(fw * .78), round(fh * .24)
        roi = self.frame[top:round(fh * .52), left:round(fw * .99)]
        best_score, best_bounds = -1, None
        for step in range(40, 161, 2):
            scale = step / 100
            for aspect in (.85, .925, 1, 1.075, 1.15):
                w, h = round(template.shape[1] * scale), round(template.shape[0] * scale * aspect)
                if min(w, h) < 12 or w > roi.shape[1] or h > roi.shape[0]:
                    continue
                resized = cv2.resize(template, (w, h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
                scores = cv2.matchTemplate(roi, resized, cv2.TM_CCOEFF_NORMED)
                _, score, _, point = cv2.minMaxLoc(scores)
                if score > best_score:
                    best_score = score
                    best_bounds = (left + point[0], top + point[1], w, h)
        self.log(f"Preset #1 visual match: {best_score:.3f} at {best_bounds} (frame {fw}x{fh})")
        if best_bounds and other is not None:
            x, y, w, h = best_bounds
            patch = self.frame[y:y + h, x:x + w]
            alternate = cv2.resize(other, (w, h), interpolation=cv2.INTER_AREA if w < other.shape[1] else cv2.INTER_LINEAR)
            other_score = float(cv2.matchTemplate(patch, alternate, cv2.TM_CCOEFF_NORMED)[0, 0])
            if best_score - other_score < .02:
                self.log(f"Rejecting preset match: resembles preset #2 ({other_score:.3f})")
                return None
        if best_score >= .88:
            self.button_positions.remember(self.frame, 'troop_preset_1', best_bounds)
            return best_bounds
        return None

    def _template(self, name):
        match = self.detector.locate_template(self.shot, _TEMPLATES / (name + ".png"), confidence_threshold=0.78)
        if not match:
            return None
        x, y, w, h = match.bounds
        fh, fw = self.frame.shape[:2]
        if name == "close" and not (x > fw * .75 and y < fh * .2):
            return None
        if name.startswith("preset_1") and not (x > fw * .78 and fh * .24 < y < fh * .55):
            return None
        if name == "menu_shield" and not (fw * .4 < x < fw * .85 and y > fh * .8):
            return None
        if name == "territory_flag" and not (fw * .4 < x < fw * .9 and fh * .35 < y < fh * .75):
            return None
        return match.bounds

    def _save(self, tag, node):
        self.nodes[tag] = dict(node, checked_at=time.time())
        if self.state_path:
            try:
                write_json(self.state_path, self.nodes)
            except OSError as exc:
                self.log(f"Could not save alliance node information: {exc}")

    def _tag(self, account_name):
        # Read the current city's tag, never one from chat or another city.
        candidates = []
        for text, bounds, score in self.words:
            x, y, w, h = bounds
            fh, fw = self.frame.shape[:2]
            if not (0.25 < (x + w / 2) / fw < 0.75 and 0.3 < (y + h / 2) / fh < 0.7):
                continue
            match = re.search(r"\[([^\[\]]{1,12})\]", text)
            name = normalized(text.split("]", 1)[-1])
            if match and score >= 0.5 and name and name == normalized(account_name or ""):
                candidates.append(match.group(1).upper())
        return candidates[0] if len(set(candidates)) == 1 else None

    def _node(self, preferred_coordinates=None):
        """Associate remaining resources and status with a single coordinate card."""
        found = []
        for text, bounds, score in self.words:
            match = re.search(r"X\s*:\s*(\d+)\s*Y\s*:\s*(\d+)", text, re.I)
            if not match or score < 0.5:
                continue
            x, y, w, h = bounds
            fh, fw = self.frame.shape[:2]
            # Cards run vertically below their coordinate link.
            related = [t for t, (bx, by, bw, bh), _ in self.words
                       if abs((bx + bw / 2) - (x + w / 2)) < fw * .09
                       and y - h <= by <= y + fh * .10]
            joined = " ".join(related)
            building = "xaydung" in normalized(joined)
            remaining = remaining_resources(joined)
            if building or remaining is not None:
                found.append(({"coordinates": [int(match[1]), int(match[2])], "remaining": remaining,
                               "building": building}, bounds))
        return next((item for item in found if item[0]["coordinates"] == preferred_coordinates),
                    found[0] if found else (None, None))

    def _close_to_world(self):
        def close_button():
            bounds = self._template("close")
            if bounds:
                return bounds
            if self._text("thu thap", "thu nhap", area=(.55, .4, .95, .8)) or self._new_troops_bounds():
                h, w = self.frame.shape[:2]
                return (int(w * .3), int(h * .35), 8, 8)
            return None
        self._verified_action("close menus and return to World View", close_button, self._world_visible)

    def run(self, account_name):
        """Return 1 only for a confirmed march; never retry the march button."""
        self.log(f"Starting alliance resource check for {account_name}; reading current alliance tag")
        try:
            return self._run(account_name)
        except _AllianceHasNoNode:
            tag = self.current_alliance_tag
            self._save(tag, {'node_count': 0, 'available': False, 'coordinates': None,
                             'remaining': 0, 'building': False,
                             'checked_session': _ALLIANCE_CHECK_SESSION})
            self.log(f"Alliance [{tag}] resource center shows 0/4: no node; skipping all matching accounts this run")
            return 0
        except (OSError, RuntimeError, ValueError, cv2.error) as exc:
            self.log(f"Alliance node skipped: {exc}")
            return 0
        finally:
            try:
                self._close_to_world()
            except (OSError, RuntimeError, ValueError, cv2.error) as exc:
                self.log(f"Could not close alliance menus: {exc}")
            self.log("Alliance resource step finished; continuing normal farming")

    def _run(self, account_name):
        self._capture()
        tag = self._tag(account_name)
        if not tag:
            self.log("Alliance node skipped: current account's alliance tag is unreadable")
            return 0
        self.log(f"Current account belongs to alliance [{tag}]")
        self.current_alliance_tag = tag
        cached = self.nodes.get(tag, {})
        if isinstance(cached, dict) and cached:
            if cached.get('node_count') == 0 and cached.get('checked_session') == _ALLIANCE_CHECK_SESSION:
                self.log(f"Alliance [{tag}] has no resource node (0/4); skipping this account")
                return 0
            self.log(f"Remembered alliance [{tag}] node: {cached.get('coordinates')}, remaining {cached.get('remaining')}")
            if cached.get('node_count') != 0 and time.time() - cached.get("checked_at", 0) < 120 and (
                cached.get("building") or isinstance(cached.get("remaining"), int) and cached["remaining"] <= MIN_REMAINING
            ):
                self.log("Alliance node skipped: recently checked node is unavailable")
                return 0

        def menu_toggle():
            h, w = self.frame.shape[:2]
            return (int(w * .945), int(h * .91), int(w * .025), int(h * .04))
        if not self._verified_action("open bottom-right menu", menu_toggle,
                                     lambda: bool(self._alliance_menu_button())):
            return 0
        if not self._press_text("lien minh", area=(.4, .83, .85, 1),
                                expected=lambda: bool(self._template("territory_flag") or self._territory_label_icon_bounds())):
            return 0
        if not self._press_text("lanh tho", "lanh th", area=(.4, .45, .85, .75), expected=self._territory_visible):
            return 0
        self._check_resource_center_count()
        self._press_once("nhan", area=(.65, .1, .9, .27), already_done=lambda: not self._claim_ready())
        self._press_once("trung tam tai nguyen", already_done=self._resource_cards_visible)
        self._check_resource_center_count()
        node, bounds = self._find_alliance_node(cached.get("coordinates") if isinstance(cached, dict) else None)
        if not node:
            self.log("Alliance node skipped: remaining resources or node coordinates unreadable")
            return 0
        self._save(tag, node)
        self.log(f"Alliance [{tag}] node {node['coordinates']}: remaining {node['remaining']}, under construction: {node['building']}")
        if node["building"] or node["remaining"] is None or node["remaining"] <= MIN_REMAINING:
            self.log(f"Alliance [{tag}] node unavailable or at/below 50 million; continuing normal farming")
            return 0
        self.log("Opening alliance node coordinates")
        if not self._verified_action("open alliance node coordinates", lambda: self._node(node['coordinates'])[1], self._world_visible):
            return 0
        def node_center():
            h, w = self.frame.shape[:2]
            return (int(w * .49), int(h * .43), int(w * .02), int(h * .04)) if self._world_visible() else None
        if not self._verified_action("select alliance resource node", node_center,
                                     lambda: bool(self._dispatch_bounds(ScreenState.RESOURCE_FOUND))):
            return 0
        join_visible = lambda: bool(self._text("cham de gia nhap", "chm d gia nhp"))
        if not self._press_dispatch(ScreenState.RESOURCE_FOUND, expected=join_visible):
            return 0
        if not self._press_text("cham de gia nhap", "chm d gia nhp", expected=lambda: bool(self._new_troops_bounds())):
            return 0
        if not self._press_dispatch(ScreenState.NEW_TROOPS_PROMPT,
                                    expected=lambda: bool(self._dispatch_bounds(ScreenState.TROOP_COMPOSITION))):
            return 0
        if not self._load_preset():
            return 0
        march = self._dispatch_bounds(ScreenState.TROOP_COMPOSITION)
        if not march:
            self.log("Alliance node skipped: March button not found after loading preset #1")
            return 0
        self.log("Sending exactly one alliance gathering march")
        self.march_attempted = True
        self._click(march)  # Exactly one attempt, even if confirmation is slow.
        for attempt in range(ACTION_ATTEMPTS):
            self._capture()
            if self._world_visible():
                self.log(f"Sent one alliance gathering march for [{tag}]")
                return 1
            self.log(f"Checking march result: {attempt + 1}/{ACTION_ATTEMPTS}; not sending another march")
            self.game.wait(.3)
        self.log("Alliance march confirmation unavailable; not retrying")
        return 0
