"""Switch between Gmail (Lilith account) logins and track one full Gmail run.

Flow (each step is detected from the screen, so a missed click or a menu that
falls back to an earlier step is recovered by re-detecting the current state):

``Top-Left Governor Portrait`` → ``Cài đặt`` → ``Tài khoản`` →
``Trung Tâm Người Dùng`` → ``Chuyển tài khoản`` → account popup →
dropdown (scrolled all the way to the bottom) → bottom Gmail → ``Đăng nhập``
→ game load.

The dropdown lists logged-in Gmail accounts by most-recent login, so always
picking the bottom (least-recently used) account cycles through every Gmail
exactly once. The Gmail that was active when the run started ends up at the
bottom last; switching back to it completes the run.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from rok_automation.automation.account_switcher import AccountSwitcher
from rok_automation.game.console import safe_print
from rok_automation.game.interface import GameInterface
from rok_automation.vision.account_label import read_text_detections
from rok_automation.vision.screen import ScreenDetector, load_color_frame, load_gray_frame


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MENU_CLOSE_TEMPLATE = _PROJECT_ROOT / "templates/daily_reward/vip_close_x.png"
DEFAULT_GMAIL_STATE_PATH = _PROJECT_ROOT / "state/gmail_rotation.json"
DEFAULT_SIGNED_IN_ACCOUNTS_PATH = _PROJECT_ROOT / "state/signed_in_gmail_accounts.json"
GMAIL_PROGRESS_UPDATED = "[GMAIL_PROGRESS_UPDATED]"

_SWITCH_TIMEOUT = 120.0
_LOGIN_UI_TIMEOUT = 60.0
_STATE_CHANGE_TIMEOUT = 3.0
_MAX_ACTION_ATTEMPTS = 5
_MAX_DROPDOWN_SCROLLS = 10
_MAX_IDENTITY_READS = 8
_ACTION_RETRY_DELAY = 0.20
_SCROLL_SETTLE_DELAY = 0.35
# A reading must repeat this many times (once) before the entry click
# is trusted, but the switch proceeds after a few attempts if the list
# keeps jittering, so it can never hang.
_MAX_STABILITY_WAITS = 3
_LOGIN_RECLICK_DELAY = 4.0
_USER_CENTER_LINGER = 6.0
# Lilith's account web UI is white/grey; RoK screens are saturated. Measured
# on reference screenshots: Lilith UI >= 0.97 low-saturation, RoK <= 0.12.
_LILITH_LOW_SAT_RATIO = 0.80
_LILITH_BRIGHT_RATIO = 0.60

# Normalized label bounds of the settings-menu "Tài khoản" button, per frame
# size, so later switches verify one small crop instead of OCR-ing the menu.
_ACCOUNT_BUTTON_CACHE: dict[tuple[int, int], tuple[float, float, float, float]] = {}
_USER_CENTER_SWITCH_CACHE: dict[tuple[int, int], Box] = {}
_POPUP_LOGIN_CACHE: dict[tuple[int, int], Box] = {}
_POPUP_FIELD_CACHE: dict[tuple[int, int], Box] = {}

_EMAIL_PATTERN = re.compile(r"([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})")
_UID_PATTERN = re.compile(r"u[il1|]d\W*(\d{4,})")
_ACCOUNT_ID_PATTERN = re.compile(r"idtaikho[a-z]*\W*(\d{4,})")

Box = tuple[int, int, int, int]
Detection = tuple[str, Box, float]


# --------------------------------------------------------------------------- #
# Identity & persistent run state
# --------------------------------------------------------------------------- #


def email_key(email: str | None) -> str | None:
    """Normalize an OCR'd email for comparison (case/space-insensitive)."""

    if not email:
        return None
    compact = "".join(email.casefold().split())
    match = _EMAIL_PATTERN.search(compact)
    if match is None:
        return None
    local, _, domain = match.group(1).partition("@")
    # OCR occasionally mangles the shared domain ("gmai1.com"); only the
    # local part distinguishes Gmail accounts from each other.
    if "gm" in domain or "mail" in domain:
        domain = "gmail.com"
    return f"{local}@{domain}"


@dataclass(frozen=True)
class GmailIdentity:
    """One Lilith/Gmail login, identified by email and/or numeric UID."""

    email: str | None = None
    uid: str | None = None

    def label(self) -> str:
        if self.email and self.uid:
            return f"{self.email} (UID {self.uid})"
        return self.email or (f"UID {self.uid}" if self.uid else "unknown Gmail")

    def matches(self, other: GmailIdentity | None) -> bool:
        """Same account if the UID or the normalized email is identical.

        Exact comparison only: farm accounts are often numbered
        (``farm06``/``farm07``), so fuzzy matching would merge them.
        """

        if other is None:
            return False
        if self.uid and other.uid and self.uid == other.uid:
            return True
        mine, theirs = email_key(self.email), email_key(other.email)
        return bool(mine and theirs and mine == theirs)

    def to_dict(self) -> dict[str, str | None]:
        return {"email": self.email, "uid": self.uid}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> GmailIdentity | None:
        if not data:
            return None
        identity = cls(email=data.get("email"), uid=data.get("uid"))
        return identity if identity.email or identity.uid else None


@dataclass(frozen=True)
class SignedInGmailAccount:
    """A Gmail account shown in the UI's persistent signed-in list."""

    identity: GmailIdentity
    verified: bool = False


class GmailAccountRegistry:
    """Persist accounts supplied manually or observed in Lilith's account UI.

    A manual entry stays in this list even when it cannot be found in the
    game.  Once the game exposes the same identity, it is upgraded to a
    verified entry and any UID read from the game is retained.
    """

    def __init__(
        self,
        path: Path | None = DEFAULT_SIGNED_IN_ACCOUNTS_PATH,
    ) -> None:
        self.path = path

    def load(self) -> list[SignedInGmailAccount]:
        if self.path is None or not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        raw_accounts = data.get("accounts", []) if isinstance(data, dict) else []
        if not isinstance(raw_accounts, list):
            return []
        accounts: list[SignedInGmailAccount] = []
        for raw in raw_accounts:
            if not isinstance(raw, dict):
                continue
            identity = GmailIdentity.from_dict(raw)
            if identity is None:
                continue
            accounts.append(
                SignedInGmailAccount(
                    identity=identity,
                    verified=bool(raw.get("verified", False)),
                )
            )
        return accounts

    def add_manual(self, value: str) -> SignedInGmailAccount | None:
        """Add a user-entered address as unknown until the game verifies it."""

        address = value.strip()
        if not address:
            return None
        return self._upsert(GmailIdentity(email=address), verified=False)

    def verify(self, identity: GmailIdentity) -> SignedInGmailAccount | None:
        """Record an account positively observed in the game."""

        if not identity.email and not identity.uid:
            return None
        return self._upsert(identity, verified=True)

    def place(self, identity: GmailIdentity, position: int) -> None:
        """Place an observed account at a zero-based position, shifting other entries."""
        if not identity.email and not identity.uid:
            return
        self.verify(identity)
        accounts = self.load()
        for index, account in enumerate(accounts):
            if self._matches(account.identity, identity):
                accounts.insert(max(0, min(position, len(accounts) - 1)), accounts.pop(index))
                self._save(accounts)
                return

    def verify_many(self, identities: list[GmailIdentity]) -> None:
        for identity in identities:
            try:
                self.verify(identity)
            except OSError:
                # The farming rotation remains authoritative if the optional
                # UI registry cannot be written at this instant.
                continue

    def remove(self, identity: GmailIdentity) -> bool:
        accounts = self.load()
        kept = [
            account
            for account in accounts
            if not self._matches(account.identity, identity)
        ]
        if len(kept) == len(accounts):
            return False
        self._save(kept)
        return True

    def _upsert(
        self,
        identity: GmailIdentity,
        *,
        verified: bool,
    ) -> SignedInGmailAccount:
        accounts = self.load()
        for index, account in enumerate(accounts):
            if not self._matches(account.identity, identity):
                continue
            merged = GmailIdentity(
                # Prefer the game's reading when it verifies an entry: it
                # corrects harmless manual typos such as gmai1.com.
                email=(
                    identity.email
                    if verified and identity.email
                    else account.identity.email
                )
                or identity.email,
                uid=identity.uid or account.identity.uid,
            )
            updated = SignedInGmailAccount(
                identity=merged,
                verified=account.verified or verified,
            )
            accounts[index] = updated
            self._save(accounts)
            return updated

        created = SignedInGmailAccount(identity=identity, verified=verified)
        accounts.append(created)
        self._save(accounts)
        return created

    @staticmethod
    def _matches(left: GmailIdentity, right: GmailIdentity) -> bool:
        if left.matches(right):
            return True
        # Keep manually entered malformed addresses from being duplicated.
        return bool(
            left.email
            and right.email
            and left.email.strip().casefold() == right.email.strip().casefold()
        )

    def _save(self, accounts: list[SignedInGmailAccount]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "accounts": [
                {
                    **account.identity.to_dict(),
                    "verified": account.verified,
                }
                for account in accounts
            ],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


@dataclass
class GmailRotation:
    """Remembers which Gmail started the run and which have been farmed.

    Persisted to JSON after every change so a crash or restart resumes the
    same run instead of farming Gmail accounts twice.
    """

    first: GmailIdentity | None = None
    farmed: list[GmailIdentity] = field(default_factory=list)
    current: GmailIdentity | None = None
    completed: bool = False
    switches: int = 0
    character_progress: list[dict] = field(default_factory=list)
    started_at: str | None = None
    path: Path | None = None

    @classmethod
    def load(
        cls,
        path: Path | None = DEFAULT_GMAIL_STATE_PATH,
        *,
        include_completed: bool = False,
    ) -> GmailRotation:
        """Resume an unfinished run, or start a new one."""

        if path is None or not path.exists():
            return cls._new(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls._new(path)
        if data.get("completed") and not include_completed:
            return cls._new(path)
        rotation = cls(
            first=GmailIdentity.from_dict(data.get("first")),
            farmed=[
                identity
                for identity in (
                    GmailIdentity.from_dict(item)
                    for item in data.get("farmed", [])
                )
                if identity is not None
            ],
            current=GmailIdentity.from_dict(data.get("current")),
            completed=bool(data.get("completed")),
            switches=int(data.get("switches", 0)),
            character_progress=data.get("character_progress", []) if isinstance(data.get("character_progress", []), list) else [],
            started_at=data.get("started_at"),
            path=path,
        )
        return rotation

    @classmethod
    def _new(cls, path: Path | None) -> GmailRotation:
        return cls(
            started_at=datetime.now().isoformat(timespec="seconds"),
            path=path,
        )

    @classmethod
    def farmed_from_path(cls, path: Path | None) -> list[GmailIdentity]:
        """Read progress for display, including a completed run's final state."""

        if path is None or not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        return [
            identity
            for identity in (
                GmailIdentity.from_dict(item) for item in data.get("farmed", [])
            )
            if identity is not None
        ]

    @classmethod
    def reset_progress(cls, path: Path | None = DEFAULT_GMAIL_STATE_PATH) -> None:
        """Replace the saved Gmail run with an empty, fresh rotation."""

        cls._new(path).save()

    @property
    def is_resumed(self) -> bool:
        return self.first is not None

    def is_farmed(self, identity: GmailIdentity) -> bool:
        return any(identity.matches(farmed) for farmed in self.farmed)

    def mark_farmed(self, identity: GmailIdentity) -> None:
        """Record a Gmail whose RoK characters have all been farmed."""

        if self.first is None:
            self.first = identity
        for index, farmed in enumerate(self.farmed):
            if identity.matches(farmed):
                # Fill in any field (email/UID) that an earlier read missed.
                self.farmed[index] = GmailIdentity(
                    email=farmed.email or identity.email,
                    uid=farmed.uid or identity.uid,
                )
                break
        else:
            self.farmed.append(identity)
        self.current = identity
        self.save()

    def set_manual_farmed(self, identity: GmailIdentity, completed: bool) -> None:
        """Change a display/skip mark without claiming this is the current login."""
        if self.completed:
            self.completed = False
            self.first = None
            self.current = None
            self.switches = 0
        self.farmed = [item for item in self.farmed if not item.matches(identity)]
        if completed:
            self.farmed.append(identity)
        self.save()

    def get_character_progress(self, identity: GmailIdentity) -> dict | None:
        for entry in self.character_progress:
            if not isinstance(entry, dict):
                continue
            saved = GmailIdentity.from_dict(entry.get("identity"))
            if saved and saved.matches(identity):
                return entry
        return None

    def set_character_progress(self, identity: GmailIdentity, index: int,
                               total: int | None, direction: int) -> None:
        self.character_progress = [
            entry for entry in self.character_progress
            if not (isinstance(entry, dict)
                    and (saved := GmailIdentity.from_dict(entry.get("identity")))
                    and saved.matches(identity))
        ]
        self.character_progress.append({"identity": identity.to_dict(),
                                        "index": index, "total": total,
                                        "direction": direction})
        self.save()

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "started_at": self.started_at,
            "first": self.first.to_dict() if self.first else None,
            "farmed": [identity.to_dict() for identity in self.farmed],
            "current": self.current.to_dict() if self.current else None,
            "completed": self.completed,
            "switches": self.switches,
            "character_progress": self.character_progress,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


@dataclass(frozen=True)
class GmailSwitchResult:
    """Outcome of one Gmail switch attempt."""

    identity: GmailIdentity | None
    run_complete: bool


# --------------------------------------------------------------------------- #
# Screen model for Lilith's account UI
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _DropdownEntry:
    email: str
    uid: str | None
    bounds: Box

    @property
    def identity(self) -> GmailIdentity:
        return GmailIdentity(email=self.email, uid=self.uid)

    @property
    def center_y(self) -> float:
        return self.bounds[1] + self.bounds[3] / 2


@dataclass
class _LilithScreen:
    """What was read from one frame of the Lilith account UI."""

    state: str  # user_center | switch_popup | dropdown_open | lilith_unknown
    switch_link: Box | None = None  # "Chuyển tài khoản" in Trung Tâm Người Dùng
    user_center_close: Box | None = None
    current: GmailIdentity | None = None
    popup_close: Box | None = None
    field_email: str | None = None
    field_bounds: Box | None = None
    login_button: Box | None = None
    confirm_button: Box | None = None
    entries: list[_DropdownEntry] = field(default_factory=list)


class GmailSwitcher:
    """Drive the Lilith account UI to log into the bottom-most Gmail."""

    def __init__(
        self,
        game: GameInterface,
        detector: ScreenDetector,
        *,
        log=safe_print,
        signed_in_accounts: GmailAccountRegistry | None = None,
    ) -> None:
        self.game = game
        self.detector = detector
        self.log = log
        self.signed_in_accounts = signed_in_accounts
        # Reuse the RoK menu-chain detection (portrait → Cài đặt → settings)
        # and the game-load wait from the character switcher.
        self.rok_menus = AccountSwitcher(game, detector, log=log)

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def switch_to_next(self, rotation: GmailRotation, *, record_only: bool = False,
                       observe_only: bool = False) -> GmailSwitchResult:
        """Record the current Gmail as farmed and log into the next one.

        Returns ``run_complete=True`` when every Gmail has been farmed: either
        the bottom account is the run's first Gmail (we switch back to it so
        the next run starts from the same place) or only one Gmail exists.
        """

        deadline = time.monotonic() + _SWITCH_TIMEOUT
        clicks = {
            "portrait": 0,
            "settings": 0,
            "account": 0,
            "character_list": 0,
            "switch_link": 0,
            "dropdown": 0,
            "entry": 0,
            "login": 0,
            "confirm": 0,
        }
        current: GmailIdentity | None = None
        identity_reads = 0
        target: GmailIdentity | None = None
        returning_to_first = False
        scroll_settled = False
        scrolls = 0
        # Identical consecutive readings seen since the last change; a
        # reading must repeat once before the click is trusted, because
        # the list can still be gliding right after a drag.
        stable_reads = 0
        stability_waits = 0
        last_signature: tuple[str, ...] | None = None
        last_state = "unknown"

        while time.monotonic() < deadline:
            screenshot, frame = self._capture()
            lilith = self._read_lilith_screen(frame)

            if lilith is None:
                state, bounds, score = self.rok_menus._detect_menu_state(
                    screenshot,
                    frame,
                )
                last_state = state
                if state == "character_list":
                    # The character traversal leaves this modal open when the
                    # final starred character has been checked. Close it before
                    # trying to open Tài khoản in the settings panel behind it.
                    self._bump(clicks, "character_list", "Character list close button")
                    close_bounds = self._find_game_menu_close(frame)
                    if close_bounds is None:
                        self._log("Could not find the character list close button; retrying")
                        self.game.wait(_ACTION_RETRY_DELAY)
                        continue
                    self._log("Closing character list to switch Gmail accounts")
                    self.game.click(close_bounds, screenshot)
                    self._wait_for_state_change("character_list")
                    continue
                if state == "settings_menu":
                    self._bump(clicks, "account", "Tài khoản button")
                    account_bounds = self._find_account_button(
                        frame,
                        character_button=bounds,
                        use_label=clicks["account"] % 2 == 0,
                    )
                    if account_bounds is None:
                        self._log("Could not find 'Tài khoản' in settings yet; retrying")
                        self.game.wait(_ACTION_RETRY_DELAY)
                        continue
                    self._log(
                        "Clicking 'Tài khoản' in settings"
                        + self._retry_note(clicks["account"])
                    )
                    self.game.click(account_bounds, screenshot)
                    self._wait_for_state_change("settings_menu")
                    continue
                if state == "governor_profile" and bounds is not None:
                    self._bump(clicks, "settings", "Cài đặt button")
                    self._log(
                        f"Clicking settings button (match={score:.3f})"
                        + self._retry_note(clicks["settings"])
                    )
                    self.game.click(bounds, screenshot)
                    self._wait_for_state_change("governor_profile")
                    continue
                if state == "map_or_city" and bounds is not None:
                    self._bump(clicks, "portrait", "Governor portrait")
                    self._log(
                        "Opening governor profile to switch Gmail account"
                        + self._retry_note(clicks["portrait"])
                    )
                    self.game.click(bounds, screenshot)
                    self._wait_for_state_change("map_or_city")
                    continue
                self.game.wait(_ACTION_RETRY_DELAY)
                continue

            last_state = lilith.state

            if current is None and lilith.state in {"switch_popup", "dropdown_open"}:
                # Started with the popup already open: the user center behind
                # the dimmed overlay still shows the current Gmail.
                background = self._read_user_center(frame)
                if background is not None and background.current is not None:
                    current = background.current
                    if not observe_only:
                        self._register_current(rotation, current)
                    if record_only or observe_only:
                        self._close_lilith_ui(screenshot, lilith)
                        return GmailSwitchResult(identity=current, run_complete=False)
                else:
                    identity_reads += 1
                    if identity_reads >= _MAX_IDENTITY_READS:
                        raise RuntimeError(
                            "Could not read the current Gmail behind the "
                            "'Chuyển tài khoản' popup"
                        )
                    self.game.wait(_ACTION_RETRY_DELAY)
                    continue

            if lilith.state == "user_center":
                if current is None:
                    if lilith.current is not None and lilith.current.email:
                        current = lilith.current
                    else:
                        identity_reads += 1
                        if identity_reads < _MAX_IDENTITY_READS:
                            # The web view may still be rendering the email row.
                            self.game.wait(_ACTION_RETRY_DELAY * 2)
                            continue
                        if lilith.current is None:
                            raise RuntimeError(
                                "Could not read the current Gmail address or UID "
                                "in Trung Tâm Người Dùng"
                            )
                        current = lilith.current
                    if not observe_only:
                        self._register_current(rotation, current)
                    if record_only or observe_only:
                        self._close_lilith_ui(screenshot, lilith)
                        return GmailSwitchResult(identity=current, run_complete=False)

                self._record_verified(current)
                if lilith.switch_link is None:
                    self.game.wait(_ACTION_RETRY_DELAY)
                    continue
                self._bump(clicks, "switch_link", "'Chuyển tài khoản' link")
                self._log(
                    "Clicking 'Chuyển tài khoản'"
                    + self._retry_note(clicks["switch_link"])
                )
                self.game.click(lilith.switch_link, screenshot)
                self._wait_for_state_change("user_center")
                continue

            if lilith.state == "dropdown_open":
                self._record_verified_entries(lilith.entries)
                if not scroll_settled:
                    signature = tuple(
                        (email_key(entry.email) or entry.email, round(entry.center_y / 15))
                        for entry in lilith.entries
                    )
                    unchanged = (
                        last_signature is not None and signature == last_signature
                    )
                    at_bottom = (
                        unchanged or scrolls >= _MAX_DROPDOWN_SCROLLS
                    )
                    if not at_bottom:
                        last_signature = signature
                        stable_reads = 0
                        stability_waits = 0
                        scrolls += 1
                        self._scroll_dropdown(screenshot, frame, lilith.entries)
                        continue
                    if not unchanged or stable_reads == 0:
                        # The list keeps gliding or bouncing for a moment
                        # after a drag, so a single reading can be out of
                        # date by half a row. Only trust one that repeats
                        # after the settle delay; this costs one capture,
                        # not another scroll. If the reading never
                        # repeats, continue with the latest one after a
                        # few attempts rather than hanging the switch.
                        # stability_waits is deliberately NOT reset here:
                        # a list whose rows appear/disappear between OCR
                        # reads never repeats, and resetting the cap made
                        # the switch loop until the timeout.
                        if not unchanged:
                            stable_reads = 0
                        stable_reads += 1
                        if stability_waits < _MAX_STABILITY_WAITS:
                            stability_waits += 1
                            self.game.wait(_SCROLL_SETTLE_DELAY)
                            continue
                    scroll_settled = True
                    self._log(
                        "Gmail dropdown is at the bottom; visible accounts: "
                        + ", ".join(entry.email for entry in lilith.entries)
                    )

                if target is None:
                    choice = self._choose_target(rotation, current, lilith.entries)
                    if choice is None:
                        self._log(
                            "No other Gmail account to switch to; "
                            "every Gmail account in this run has been farmed"
                        )
                        self._close_lilith_ui(screenshot, lilith)
                        rotation.completed = True
                        rotation.save()
                        return GmailSwitchResult(identity=current, run_complete=True)
                    entry, returning_to_first = choice
                    target = entry.identity
                    if returning_to_first:
                        self._log(
                            f"Bottom Gmail is the run's first account "
                            f"({target.label()}); switching back to finish the run"
                        )
                    else:
                        self._log(f"Selecting bottom Gmail account: {target.label()}")

                entry = self._entry_for(target, lilith.entries)
                if entry is None:
                    # The list moved (e.g. a drag was read as a tap); rescan.
                    self._log("Target Gmail not visible in dropdown; re-scrolling")
                    scroll_settled = False
                    last_signature = None
                    scrolls = 0
                    stable_reads = 0
                    stability_waits = 0
                    continue
                self._bump(clicks, "entry", "Gmail dropdown entry")
                self.game.click(
                    self._entry_click_bounds(entry, lilith.entries),
                    screenshot,
                )
                self._wait_for_state_change("dropdown_open")
                continue

            if lilith.state == "switch_popup":
                if (
                    target is not None
                    and lilith.login_button is not None
                    and self._field_matches(lilith.field_email, target)
                ):
                    self._bump(clicks, "login", "Đăng nhập button")
                    self._log(
                        f"Logging into {target.label()}"
                        + self._retry_note(clicks["login"])
                    )
                    self.game.click(lilith.login_button, screenshot)
                    self._wait_for_login(target)
                    rotation.current = target
                    rotation.switches += 1
                    if returning_to_first:
                        rotation.completed = True
                    rotation.save()
                    return GmailSwitchResult(
                        identity=target,
                        run_complete=returning_to_first,
                    )

                if lilith.field_bounds is None:
                    self.game.wait(_ACTION_RETRY_DELAY)
                    continue
                if target is not None:
                    self._log(
                        f"Popup shows {lilith.field_email or 'no account'} instead "
                        f"of {target.label()}; reopening the dropdown"
                    )
                self._bump(clicks, "dropdown", "Gmail dropdown")
                self.game.click(lilith.field_bounds, screenshot)
                scroll_settled = False
                last_signature = None
                scrolls = 0
                stable_reads = 0
                stability_waits = 0
                self._wait_for_state_change("switch_popup")
                continue

            # lilith_unknown: web view still loading or an unexpected dialog.
            if lilith.confirm_button is not None:
                self._bump(clicks, "confirm", "confirmation button")
                self._log("Confirming Lilith account dialog")
                self.game.click(lilith.confirm_button, screenshot)
                self._wait_for_state_change("lilith_unknown")
                continue
            self.game.wait(_ACTION_RETRY_DELAY)

        raise RuntimeError(
            "Timed out switching Gmail account; "
            f"last detected state was {last_state}"
        )

    # ------------------------------------------------------------------ #
    # Decisions
    # ------------------------------------------------------------------ #

    def _register_current(
        self,
        rotation: GmailRotation,
        current: GmailIdentity,
    ) -> None:
        first_time = rotation.first is None
        self._record_verified(current)
        rotation.mark_farmed(current)
        self._log(GMAIL_PROGRESS_UPDATED)
        if first_time:
            self._log(
                f"Gmail run started from {current.label()}; this account "
                "completes the run when the rotation returns to it"
            )
        else:
            self._log(
                f"Finished all RoK accounts in Gmail {current.label()}; "
                f"{len(rotation.farmed)} Gmail account(s) farmed this run"
            )

    def _record_verified(self, identity: GmailIdentity | None) -> None:
        """Save a Gmail identity positively observed in the game."""

        if self.signed_in_accounts is None or identity is None:
            return
        try:
            self.signed_in_accounts.verify(identity)
        except OSError as exc:
            self._log(f"Could not save the signed-in Gmail list: {exc}")

    def _record_verified_entries(self, entries: list[_DropdownEntry]) -> None:
        for entry in entries:
            self._record_verified(entry.identity)

    def _choose_target(
        self,
        rotation: GmailRotation,
        current: GmailIdentity | None,
        entries: list[_DropdownEntry],
    ) -> tuple[_DropdownEntry, bool] | None:
        """Pick the bottom-most Gmail; returns (entry, returning_to_first).

        The account list is ordered by recent login, so always visit the
        least-recently used account even when it was already farmed.
        """

        for entry in sorted(entries, key=lambda item: item.center_y, reverse=True):
            identity = entry.identity
            if identity.matches(current):
                continue
            return entry, bool(rotation.first and identity.matches(rotation.first))

        return None

    @staticmethod
    def _entry_for(
        target: GmailIdentity,
        entries: list[_DropdownEntry],
    ) -> _DropdownEntry | None:
        return next(
            (entry for entry in entries if entry.identity.matches(target)),
            None,
        )

    @staticmethod
    def _entry_click_bounds(
        entry: _DropdownEntry,
        entries: list[_DropdownEntry],
    ) -> Box:
        """Lower the click into the middle of the account row.

        The dropdown's rows keep a constant pitch: the e-mail text sits
        at the top of its row and the row card extends a full pitch
        below it. Clicking the centre of the text alone can sit high
        enough that the tap is read by the row above, so the click is
        taken from the middle of the row instead. The pitch comes from
        the detected rows themselves, so it follows the dropdown
        wherever it is drawn on screen.
        """

        x, y, width, height = entry.bounds
        tops = sorted(item.bounds[1] for item in entries)
        pitch = min(
            (below - above for above, below in zip(tops, tops[1:]) if below > above),
            default=0,
        )
        if pitch <= height:
            return entry.bounds
        return (x, y, width, pitch)

    @staticmethod
    def _field_matches(field_email: str | None, target: GmailIdentity) -> bool:
        if target.email is None:
            return False
        return email_key(field_email) == email_key(target.email)

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def _scroll_dropdown(
        self,
        screenshot: Path,
        frame: Any,
        entries: list[_DropdownEntry],
    ) -> None:
        """Drag the dropdown list upward to reveal accounts further down."""

        height, width = frame.shape[:2]
        center_x = (round(sum(entry.bounds[0] + entry.bounds[2] / 2 for entry in entries)
                          / len(entries)) if entries else round(width * 0.50))
        # Use the dropdown's viewport rather than the number of OCR rows:
        # partially clipped or unreadable rows should not shorten later swipes.
        field = _POPUP_FIELD_CACHE.get((height, width))
        top = (field[1] + field[3] + round(height * 0.05)
               if field is not None else round(height * 0.52))
        end_y = min(top, round(height * 0.68))
        start_y = min(round(height * 0.86),
                      max(round(height * 0.79), end_y + round(height * 0.20)))

        self.game.drag((center_x, start_y), (center_x, end_y), screenshot)
        self.game.wait(_SCROLL_SETTLE_DELAY)

    def _wait_for_login(self, target: GmailIdentity) -> None:
        """Wait for the Lilith UI to close, then for RoK to reach the map."""

        deadline = time.monotonic() + _LOGIN_UI_TIMEOUT
        last_login_click = time.monotonic()
        user_center_since: float | None = None
        extra_clicks = 0
        while time.monotonic() < deadline:
            screenshot, frame = self._capture()
            lilith = self._read_lilith_screen(frame)
            if lilith is None:
                state, _, _ = self.rok_menus._detect_menu_state(screenshot, frame)
                if state in {"settings_menu", "governor_profile"}:
                    self._close_lilith_ui(screenshot, None)
                break
            now = time.monotonic()
            if lilith.state == "switch_popup" and lilith.login_button is not None:
                user_center_since = None
                if now - last_login_click >= _LOGIN_RECLICK_DELAY:
                    extra_clicks += 1
                    if extra_clicks > _MAX_ACTION_ATTEMPTS:
                        raise RuntimeError(
                            "'Đăng nhập' did not switch the Gmail account after "
                            f"{_MAX_ACTION_ATTEMPTS} clicks"
                        )
                    self._log("Account popup still open; clicking 'Đăng nhập' again")
                    self.game.click(lilith.login_button, screenshot)
                    last_login_click = now
            elif lilith.confirm_button is not None:
                self._log("Confirming Lilith account dialog")
                self.game.click(lilith.confirm_button, screenshot)
                last_login_click = now
            elif lilith.state == "user_center":
                # Some SDK builds switch in place and leave the user center
                # open on the new account; close it so the game can reload.
                user_center_since = user_center_since or now
                if now - user_center_since >= _USER_CENTER_LINGER:
                    if lilith.current is not None and not lilith.current.matches(
                        target
                    ):
                        raise RuntimeError(
                            f"Gmail login did not switch: user center still shows "
                            f"{lilith.current.label()} instead of {target.label()}"
                        )
                    if lilith.user_center_close is not None:
                        self._log("Closing Trung Tâm Người Dùng after Gmail switch")
                        self._close_lilith_ui(screenshot, lilith)
                        break
            self.game.wait(max(0.25, self.game.timing.poll_interval))
        else:
            raise RuntimeError(
                f"Timed out waiting for the Gmail login to {target.label()} "
                "to close the account screen"
            )

        self._log(f"Gmail {target.label()} accepted; waiting for RoK to load")
        self.rok_menus._wait_for_game(target.email or target.label())

    def _close_lilith_ui(self, screenshot: Path, lilith: _LilithScreen | None) -> None:
        """Close account center, settings and profile before returning to play."""

        deadline = time.monotonic() + self.game.timing.scaled(30.0)
        clicks = {"popup": 0, "user_center": 0, "settings_menu": 0, "governor_profile": 0}
        frame = None
        last_state = "unknown"
        while time.monotonic() < deadline:
            if frame is None and lilith is None:
                screenshot, frame = self._capture()
                lilith = self._read_lilith_screen(frame)
            if lilith is not None:
                last_state = lilith.state
                if lilith.state in {"switch_popup", "dropdown_open"}:
                    key, bounds = "popup", lilith.popup_close
                elif lilith.state == "user_center":
                    key, bounds = "user_center", lilith.user_center_close
                else:
                    key, bounds = None, None
            else:
                last_state, _, _ = self.rok_menus._detect_menu_state(screenshot, frame)
                if last_state == "map_or_city":
                    return
                if last_state in {"settings_menu", "governor_profile"}:
                    key = last_state
                    bounds = self._find_game_menu_close(frame)
                else:
                    key, bounds = None, None

            if key is not None and bounds is not None:
                self._bump(clicks, key, f"{key} close button")
                self._log(f"Closing {key.replace('_', ' ')}")
                self.game.click(bounds, screenshot)
            self.game.wait(0.6)
            screenshot, frame = self._capture()
            lilith = self._read_lilith_screen(frame)

        raise RuntimeError(f"Could not return to the city or map; last screen: {last_state}")

    @staticmethod
    def _find_game_menu_close(frame: Any) -> Box | None:
        """Find the X on a centered settings or governor panel."""
        height, width = frame.shape[:2]
        template = load_gray_frame(_MENU_CLOSE_TEMPLATE)
        if template is None:
            raise RuntimeError("Could not load the menu close-button template")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        left, right = round(width * 0.65), round(width * 0.99)
        bottom = round(height * 0.20)
        region = gray[:bottom, left:right]
        score, (x, y, scale) = ScreenDetector._best_scale_match(
            region, template, maximum_scale=1.35
        )
        if score < 0.75:
            return None
        return (left + x, y, round(template.shape[1] * scale),
                round(template.shape[0] * scale))

    def _wait_for_state_change(self, previous_state: str) -> None:
        """Wait briefly until the screen advances past ``previous_state``."""

        self.game.wait(_ACTION_RETRY_DELAY)
        deadline = time.monotonic() + self.game.timing.scaled(_STATE_CHANGE_TIMEOUT)
        while time.monotonic() < deadline:
            screenshot, frame = self._capture()
            lilith = self._read_lilith_screen(frame)
            if lilith is not None:
                state = lilith.state
            else:
                state, _bounds, _score = self.rok_menus._detect_menu_state(
                    screenshot,
                    frame,
                )
            if state != previous_state and state != "unknown":
                return
            self.game.wait(self.game.timing.poll_interval)

    # ------------------------------------------------------------------ #
    # Vision
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_lilith_ui(frame: Any) -> bool:
        """Cheap colour gate before any OCR: Lilith's web UI is white/grey."""

        small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
        if small.ndim == 2:
            return False
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        low_saturation = float(np.mean(hsv[..., 1] < 28))
        bright = float(np.mean(hsv[..., 2] > 120))
        return (
            low_saturation >= _LILITH_LOW_SAT_RATIO
            and bright >= _LILITH_BRIGHT_RATIO
        )

    def _read_lilith_screen(self, frame: Any) -> _LilithScreen | None:
        """Classify the Lilith account UI, or return None for RoK screens."""

        if not self._is_lilith_ui(frame):
            return None

        popup = self._read_popup(frame)
        if popup is not None:
            return popup
        screen = self._read_user_center(frame)
        if screen is not None:
            return screen
        unknown = _LilithScreen(state="lilith_unknown")
        unknown.confirm_button = self._find_confirm_button(frame)
        return unknown

    @classmethod
    def _find_red_login_button(cls, frame: Any) -> Box | None:
        """Sub-millisecond HSV color detection for the red 'Đăng nhập' button."""

        height, width = frame.shape[:2]
        cached = _POPUP_LOGIN_CACHE.get((height, width))
        if cached is not None:
            cx, cy, cw, ch = cached
            patch = frame[cy : cy + ch, cx : cx + cw]
            if patch.size > 0:
                hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
                mask = cv2.inRange(hsv, (0, 120, 140), (12, 255, 255)) | cv2.inRange(
                    hsv, (170, 120, 140), (180, 255, 255)
                )
                if float(np.mean(mask > 0)) > 0.40:
                    return cached

        rx0, ry0 = round(width * 0.32), round(height * 0.46)
        rx1, ry1 = round(width * 0.68), round(height * 0.62)
        crop = frame[ry0:ry1, rx0:rx1]
        if crop.size == 0:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 120, 140), (12, 255, 255)) | cv2.inRange(
            hsv, (170, 120, 140), (180, 255, 255)
        )
        if float(np.mean(mask > 0)) < 0.10:
            return None
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            bx, by, bw, bh = cv2.boundingRect(c)
            if bw > width * 0.15 and bh > height * 0.03:
                hit = (rx0 + bx, ry0 + by, bw, bh)
                _POPUP_LOGIN_CACHE[(height, width)] = hit
                return hit
        return None

    def _read_user_center(self, frame: Any) -> _LilithScreen | None:
        """Read Trung Tâm Người Dùng: title, UID, email, 'Chuyển tài khoản'.

        Also works on the dimmed background behind the account popup.
        """

        height, width = frame.shape[:2]
        # Targeted static ROI: Left user panel (title, UID, switch button, email)
        crop_region = (0, 0, round(width * 0.52), round(height * 0.35))
        detections = read_text_detections(
            frame,
            crop_region,
            upscale=2.0,
        )
        title = next(
            (
                bounds
                for text, bounds, _score in detections
                if "trungtam" in _compact(text)
                and bounds[1] < height * 0.12
            ),
            None,
        )
        if title is None:
            return None

        screen = _LilithScreen(state="user_center")
        new_switch = self._find_user_center_switch_button(frame)
        emails: list[tuple[int, str]] = []
        uids: list[tuple[int, str]] = []
        for text, bounds, score in detections:
            compact = _compact(text)
            if (
                screen.switch_link is None
                and compact.startswith("chuy")
                and "taikh" in compact
                and bounds[1] < height * 0.25
            ):
                screen.switch_link = bounds
                _USER_CENTER_SWITCH_CACHE[(height, width)] = bounds
            match = _EMAIL_PATTERN.search(_email_text(text))
            if match and score >= 0.5:
                emails.append((bounds[1], match.group(1)))
                continue
            match = _UID_PATTERN.search(_fold(text).replace(" ", ""))
            if not match:
                match = _ACCOUNT_ID_PATTERN.search(_fold(text).replace(" ", ""))
            if match:
                uids.append((bounds[1], match.group(1)))
        if new_switch is not None:
            screen.switch_link = new_switch
        # Fallback to cached switch link if OCR missed the button this frame.
        if screen.switch_link is None:
            screen.switch_link = _USER_CENTER_SWITCH_CACHE.get((height, width))

        # The account's own rows are the topmost; popup rows sit lower.
        email = min(emails)[1] if emails else None
        uid = min(uids)[1] if uids else None
        if email or uid:
            screen.current = GmailIdentity(email=email, uid=uid)
        screen.user_center_close = self._find_close_button(
            frame,
            (round(width * 0.90), 0, width - round(width * 0.90), round(height * 0.10)),
        )
        if new_switch is not None:
            # The redesigned center uses a back arrow before the title,
            # rather than the old close button in the top-right corner.
            screen.user_center_close = (
                round(width * 0.018), round(height * 0.015),
                round(width * 0.03), round(height * 0.055),
            )
        return screen

    @staticmethod
    def _find_user_center_switch_button(frame: Any) -> Box | None:
        """Find the red switch button in the redesigned left account panel."""
        height, width = frame.shape[:2]
        left, top = round(width * 0.20), round(height * 0.50)
        right, bottom = round(width * 0.45), round(height * 0.75)
        crop = frame[top:bottom, left:right]
        if crop.size == 0:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 120, 140), (12, 255, 255)) | cv2.inRange(
            hsv, (170, 120, 140), (180, 255, 255)
        )
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True):
            x, y, w, h = cv2.boundingRect(contour)
            if w >= width * 0.12 and h >= height * 0.04:
                return (left + x, top + y, w, h)
        return None

    def _read_popup(self, frame: Any) -> _LilithScreen | None:
        """Read the centered 'Chuyển tài khoản' popup and its dropdown."""

        height, width = frame.shape[:2]
        left, top = round(width * 0.20), round(height * 0.18)
        detections = read_text_detections(
            frame,
            (left, top, round(width * 0.60), round(height * 0.72)),
            upscale=2.0,
        )
        # The popup title is horizontally centred; the user-center link with
        # the same text sits left of centre, so prefer the most centred hit.
        title_candidates = [
            bounds
            for text, bounds, _score in detections
            if _compact(text).startswith("chuy")
            and "taikh" in _compact(text)
            and abs(bounds[0] + bounds[2] / 2 - width / 2) <= width * 0.07
        ]
        if not title_candidates:
            return None
        title = min(
            title_candidates,
            key=lambda bounds: abs(bounds[0] + bounds[2] / 2 - width / 2),
        )

        below = [item for item in detections if item[1][1] > title[1] + title[3] * 0.5]
        # Wrapped rows must be joined before matching, or the wrapped
        # account vanishes from the list and the dropdown reading
        # alternates between frames (see _merge_wrapped_email_lines).
        below = _merge_wrapped_email_lines(below)
        emails: list[tuple[str, Box]] = []
        uids: list[tuple[str, Box]] = []
        login_button: Box | None = self._find_red_login_button(frame)
        close_button: Box | None = None
        for text, bounds, _score in detections:
            if (
                close_button is None
                and text.strip() in {"×", "x", "X", "✕", "十", "+", "*"}
                and bounds[0] > title[0] + title[2]
                and abs(bounds[1] - title[1]) <= height * 0.06
            ):
                close_button = bounds
        for text, bounds, score in below:
            compact = _compact(text)
            match = _EMAIL_PATTERN.search(_email_text(text))
            if match and score >= 0.5:
                emails.append((match.group(1), bounds))
                continue
            uid_match = _UID_PATTERN.search(_fold(text).replace(" ", ""))
            if not uid_match:
                uid_match = _ACCOUNT_ID_PATTERN.search(_fold(text).replace(" ", ""))
            if uid_match:
                uids.append((uid_match.group(1), bounds))
                continue
            if (
                login_button is None
                and compact.startswith("dang")
                and "nh" in compact
                and len(compact) <= 9
                and not any(char.isdigit() for char in compact)
            ):
                login_button = bounds

        emails.sort(key=lambda item: item[1][1])
        field_bottom = (
            emails[0][1][1] + emails[0][1][3]
            if emails
            else title[1] + title[3]
        )
        list_uids = [item for item in uids if item[1][1] >= field_bottom]
        # A real popup always shows its 'Đăng nhập' button or, when the
        # dropdown covers that button, UID rows of the listed accounts.
        if login_button is None and not list_uids:
            return None

        screen = _LilithScreen(state="switch_popup", popup_close=close_button)
        if emails:
            # The dropdown field is the first email under the popup title.
            screen.field_email, screen.field_bounds = emails[0]
            _POPUP_FIELD_CACHE[(height, width)] = emails[0][1]
        elif (height, width) in _POPUP_FIELD_CACHE:
            screen.field_bounds = _POPUP_FIELD_CACHE[(height, width)]
        elif login_button is not None:
            # Empty field: tap the band between the title and 'Đăng nhập'.
            title_bottom = title[1] + title[3]
            band = max(1, login_button[1] - title_bottom)
            screen.field_bounds = (
                title[0],
                title_bottom + round(band * 0.25),
                title[2],
                max(1, round(band * 0.5)),
            )
        screen.login_button = login_button

        if list_uids:
            screen.state = "dropdown_open"
            for email, bounds in emails[1:]:
                email_height = max(1, bounds[3])
                uid = next(
                    (
                        uid_text
                        for uid_text, uid_bounds in list_uids
                        if 0 < uid_bounds[1] - bounds[1] <= email_height * 2.2
                        and abs(uid_bounds[0] - bounds[0]) <= width * 0.05
                    ),
                    None,
                )
                screen.entries.append(
                    _DropdownEntry(email=email, uid=uid, bounds=bounds)
                )
            if not screen.entries:
                # Only partially-clipped rows are visible; treat as scrolling.
                screen.state = "lilith_unknown"
        return screen

    def _find_account_button(
        self,
        frame: Any,
        *,
        character_button: Box | None,
        use_label: bool,
    ) -> Box | None:
        """Locate the settings-menu 'Tài khoản' button (icon above its label)."""

        height, width = frame.shape[:2]
        label: Box | None = None
        cached = _ACCOUNT_BUTTON_CACHE.get((height, width))
        if cached is not None:
            nx, ny, nw, nh = cached
            # Verify the pinned label with one small OCR crop.
            crop = (
                round((nx - nw * 0.5) * width),
                round((ny - nh * 1.0) * height),
                round(nw * 2.0 * width),
                round(nh * 3.0 * height),
            )
            for text, bounds, _score in read_text_detections(frame, crop, upscale=2.5):
                if _is_account_label(text):
                    label = bounds
                    break
        if label is None:
            detections = read_text_detections(
                frame,
                (
                    round(width * 0.04),
                    round(height * 0.10),
                    round(width * 0.92),
                    round(height * 0.87),
                ),
                upscale=1.6,
            )
            label = next(
                (bounds for text, bounds, _score in detections if _is_account_label(text)),
                None,
            )
        if label is None:
            return None
        x, y, label_width, label_height = label
        _ACCOUNT_BUTTON_CACHE[(height, width)] = (
            x / width,
            y / height,
            label_width / width,
            label_height / height,
        )
        if use_label:
            return label
        # The icon sits above its label; the 'Nhân vật' template (icon +
        # label) gives the icon-to-label distance at the current scale.
        offset = (
            character_button[3] * 0.44
            if character_button is not None
            else label_height * 2.5
        )
        size = max(label_height * 2, round(offset * 0.6))
        center_x = x + label_width / 2
        center_y = max(size / 2, y + label_height / 2 - offset)
        return (
            round(center_x - size / 2),
            round(center_y - size / 2),
            round(size),
            round(size),
        )

    @staticmethod
    def _find_confirm_button(frame: Any) -> Box | None:
        """Generic Lilith confirmation dialog button (Xác nhận / Đồng ý)."""

        height, width = frame.shape[:2]
        for text, bounds, _score in read_text_detections(
            frame,
            (
                round(width * 0.25),
                round(height * 0.30),
                round(width * 0.50),
                round(height * 0.50),
            ),
            upscale=1.8,
        ):
            compact = _compact(text)
            if compact in {"xacnhan", "xacnhn", "dongy", "dngy", "tieptuc", "tiptuc"}:
                return bounds
        return None

    @staticmethod
    def _find_close_button(frame: Any, region: Box) -> Box | None:
        for text, bounds, _score in read_text_detections(frame, region, upscale=2.0):
            if text.strip() in {"×", "x", "X", "✕", "十", "+", "*"}:
                return bounds
        # Fallback to the canonical top-right corner inside the region.
        rx, ry, rw, rh = region
        if rw >= 20 and rh >= 20:
            return (rx + rw - round(rw * 0.4), ry + round(rh * 0.1), round(rw * 0.3), round(rh * 0.6))
        return None

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _bump(clicks: dict[str, int], key: str, description: str) -> None:
        clicks[key] += 1
        if clicks[key] > _MAX_ACTION_ATTEMPTS:
            raise RuntimeError(
                f"{description} did not respond after {_MAX_ACTION_ATTEMPTS} clicks"
            )

    @staticmethod
    def _retry_note(count: int) -> str:
        return f" (retry {count - 1})" if count > 1 else ""

    def _capture(self) -> tuple[Path, Any]:
        screenshot = self.game.screenshot()
        frame = load_color_frame(screenshot)
        if frame is None:
            raise RuntimeError(f"Could not read screenshot: {screenshot}")
        return screenshot, frame

    def _log(self, message: str) -> None:
        self.log(message)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(
        char for char in decomposed
        if not unicodedata.category(char).startswith("M")
    ).replace("đ", "d")


def _compact(text: str) -> str:
    """Accent-free, lowercase, letters/digits only (OCR-variant tolerant)."""

    return "".join(char for char in _fold(text) if char.isalnum())


def _email_text(text: str) -> str:
    return "".join(_fold(text).split())


def _merge_wrapped_email_lines(
    detections: list[tuple[str, Box, float]],
) -> list[tuple[str, Box, float]]:
    """Join the OCR lines of an e-mail row that wrapped onto two lines.

    The dropdown draws long addresses on two lines (``...@gmail`` over
    ``.com``), and no single line then matches the e-mail pattern, so
    that account silently disappears from the reading. A line holding
    the left part of an address (an ``@`` with no full domain) is merged
    with the short line directly below it when they form a valid address.
    """

    merged: list[tuple[str, Box, float]] = []
    skip_next = False
    ordered = sorted(detections, key=lambda item: (item[1][1], item[1][0]))
    for index, (text, bounds, score) in enumerate(ordered):
        if skip_next:
            skip_next = False
            continue
        candidate = _email_text(text)
        if (
            "@" in candidate
            and _EMAIL_PATTERN.search(candidate) is None
            and index + 1 < len(ordered)
        ):
            next_text, next_bounds, next_score = ordered[index + 1]
            fragment = _email_text(next_text)
            gap = next_bounds[1] - (bounds[1] + bounds[3])
            overlaps_x = (
                next_bounds[0] < bounds[0] + bounds[2]
                and bounds[0] < next_bounds[0] + next_bounds[2]
            )
            if (
                0 <= gap <= max(1, bounds[3])
                and overlaps_x
                and fragment
                and "@" not in fragment
                and len(fragment) <= 6
            ):
                joined = candidate + fragment
                if _EMAIL_PATTERN.search(joined):
                    x1 = min(bounds[0], next_bounds[0])
                    y1 = min(bounds[1], next_bounds[1])
                    x2 = max(
                        bounds[0] + bounds[2],
                        next_bounds[0] + next_bounds[2],
                    )
                    y2 = max(
                        bounds[1] + bounds[3],
                        next_bounds[1] + next_bounds[3],
                    )
                    merged.append(
                        (joined, (x1, y1, x2 - x1, y2 - y1), min(score, next_score))
                    )
                    skip_next = True
                    continue
        merged.append((text, bounds, score))
    return merged


def _is_account_label(text: str) -> bool:
    compact = _compact(text)
    return compact.startswith("taikh") and len(compact) <= 9
