"""Desktop GUI for Rise of Kingdoms Farming Automation.

Provides controls to launch automation routines, configure options (including
future/planned feature toggles), trigger test actions, and monitor live log
output in an integrated console.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Any

from rok_automation.automation.controller import DEFAULT_SESSION_SUMMARY_PATH
from rok_automation.automation.gmail_switcher import (
    GMAIL_PROGRESS_UPDATED,
    DEFAULT_GMAIL_STATE_PATH,
    DEFAULT_SIGNED_IN_ACCOUNTS_PATH,
    GmailAccountRegistry,
    GmailIdentity,
    GmailRotation,
)
from rok_automation.game.display import enable_dpi_awareness
from rok_automation.game.emulators import families, get_family, select_family
from rok_automation.game.flags import (
    DEFAULT_DAILY_REWARD_FLAG_PATH,
    set_city_resources,
    set_daily_rewards,
    set_forge_material,
)
from rok_automation.game.pause import clear_paused, set_paused
from rok_automation.game.process import run_hidden
from rok_automation.logic.resource_selector import (
    Resource,
    SearchTarget,
    calculate_farmed_resources,
    format_resource_amount,
    resource_node_yield,
)


def hide_console_window() -> None:
    """Hide the Windows console window if one is attached to this process."""
    if os.environ.get("ROK_SHOW_CONSOLE") == "1":
        return
    if sys.platform == "win32":
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                # SW_HIDE = 0
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass


def _get_python_cli_executable() -> str:
    """Return path to python.exe (rather than pythonw.exe) for worker subprocesses."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        candidate = exe[:-9] + "python.exe"
        if os.path.isfile(candidate):
            return candidate
    return exe


class ModernColors:
    """Dark palette theme colors for the UI."""

    BG_DARK = "#121316"
    BG_CARD = "#1c1e24"
    BG_CARD_LIGHT = "#252830"
    BG_BORDER = "#2f333d"
    BG_CONSOLE = "#0e0f12"

    TEXT_PRIMARY = "#f3f4f6"
    TEXT_SECONDARY = "#9ca3af"
    TEXT_MUTED = "#6b7280"

    ACCENT_GREEN = "#10b981"
    ACCENT_GREEN_HOVER = "#059669"
    ACCENT_RED = "#ef4444"
    ACCENT_RED_HOVER = "#dc2626"
    ACCENT_BLUE = "#3b82f6"
    ACCENT_BLUE_HOVER = "#2563eb"
    ACCENT_AMBER = "#f59e0b"
    ACCENT_PURPLE = "#8b5cf6"

    # Console syntax coloring
    LOG_NORMAL = "#e5e7eb"
    LOG_SUCCESS = "#34d399"
    LOG_ERROR = "#f87171"
    LOG_WARNING = "#fbbf24"
    LOG_INFO = "#60a5fa"
    LOG_TIMESTAMP = "#6b7280"
    LOG_PLANNED = "#c084fc"


def _emulator_label(key: str) -> str:
    """Return the display label of an emulator family."""

    try:
        family = get_family(key)
    except Exception:
        return key
    return family.label or family.key


def _default_emulator_key() -> str:
    """Return the key of the emulator family that is installed."""

    try:
        family, _ = select_family()
    except Exception:
        return "mumu"
    return family.key


class ROKAutomationApp:
    """Main Application Window for ROKauto."""

    def __init__(
        self,
        root: tk.Tk,
        session_summary_path: Path | None = DEFAULT_SESSION_SUMMARY_PATH,
        display_scale: float = 1.0,
        daily_reward_flag_path: Path | None = DEFAULT_DAILY_REWARD_FLAG_PATH,
        gmail_state_path: Path | None = DEFAULT_GMAIL_STATE_PATH,
        signed_in_accounts_path: Path | None = DEFAULT_SIGNED_IN_ACCOUNTS_PATH,
    ) -> None:
        hide_console_window()
        self.root = root
        self.session_summary_path = session_summary_path
        self.display_scale = display_scale
        self.daily_reward_flag_path = daily_reward_flag_path
        self.gmail_state_path = gmail_state_path
        self.signed_in_accounts = GmailAccountRegistry(signed_in_accounts_path)
        self.root.title("Rise of Kingdoms Automation — ROKauto")
        # Wide enough for the whole layout: the two option cards side by
        # side need about 1160px, and the button row needs the rest.
        # The size is given in physical pixels, so it is multiplied by the
        # display scale to keep the same amount of room on a scaled screen.
        width = round(1200 * display_scale)
        height = round(820 * display_scale)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(round(880 * display_scale), round(640 * display_scale))
        self.root.configure(bg=ModernColors.BG_DARK)

        # Process & Thread state
        self._current_process: subprocess.Popen[str] | None = None
        self._worker_thread: threading.Thread | None = None
        self._output_queue: queue.Queue[str | None] = queue.Queue()
        self._is_running = False
        self._is_paused = False
        self._session_targets: list[SearchTarget] = []

        # Options state
        self.opt_farm = tk.BooleanVar(value=True)
        self.opt_switch_gmail = tk.BooleanVar(value=True)
        self.gmail_account_entry = tk.StringVar()
        # Which emulator family to drive.
        self.opt_emulator = tk.StringVar(value=_default_emulator_key())

        # Additional automation options.
        self.opt_daily_reward = tk.BooleanVar(value=False)
        self.opt_collect_city_resources = tk.BooleanVar(value=False)
        self.opt_auto_forge = tk.BooleanVar(value=False)
        self.opt_forge_material = tk.StringVar(value="Leather")
        self.opt_alliance = tk.BooleanVar(value=False)
        self.opt_train_troops = tk.BooleanVar(value=False)

        # Console controls
        self.opt_autoscroll = tk.BooleanVar(value=True)

        self._setup_styles()
        self._build_ui()
        self._poll_output_queue()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<F5>", lambda event: self.start_automation())
        self.root.bind("<Escape>", lambda event: self.stop_automation())

        # Check emulator status initially
        self.root.after(200, self.check_emulator_connection)

    def _setup_styles(self) -> None:
        """Configure ttk styles with a clean dark theme."""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Frame styles
        style.configure("Dark.TFrame", background=ModernColors.BG_DARK)
        style.configure("Card.TFrame", background=ModernColors.BG_CARD)

        # Label styles
        style.configure(
            "Title.TLabel",
            background=ModernColors.BG_DARK,
            foreground=ModernColors.TEXT_PRIMARY,
            font=("Segoe UI", 14, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=ModernColors.BG_DARK,
            foreground=ModernColors.TEXT_MUTED,
            font=("Segoe UI", 9),
        )
        style.configure(
            "CardTitle.TLabel",
            background=ModernColors.BG_CARD,
            foreground=ModernColors.TEXT_PRIMARY,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "CardDesc.TLabel",
            background=ModernColors.BG_CARD,
            foreground=ModernColors.TEXT_MUTED,
            font=("Segoe UI", 9),
        )

        # Checkbutton styles
        style.configure(
            "Dark.TCheckbutton",
            background=ModernColors.BG_CARD,
            foreground=ModernColors.TEXT_PRIMARY,
            font=("Segoe UI", 9),
        )
        style.map(
            "Dark.TCheckbutton",
            background=[("active", ModernColors.BG_CARD)],
            foreground=[("active", ModernColors.TEXT_PRIMARY)],
        )

        # Combobox styles (the emulator picker)
        style.configure(
            "Dark.TCombobox",
            background=ModernColors.BG_CARD_LIGHT,
            fieldbackground=ModernColors.BG_CARD_LIGHT,
            foreground=ModernColors.TEXT_PRIMARY,
            arrowcolor=ModernColors.TEXT_PRIMARY,
            bordercolor=ModernColors.BG_BORDER,
            font=("Segoe UI", 9),
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", ModernColors.BG_CARD_LIGHT)],
            background=[("readonly", ModernColors.BG_CARD_LIGHT)],
            foreground=[("readonly", ModernColors.TEXT_PRIMARY)],
        )

    def _build_ui(self) -> None:
        """Construct the UI layout."""
        # Top Container
        top_frame = tk.Frame(self.root, bg=ModernColors.BG_DARK, padx=16, pady=12)
        top_frame.pack(fill=tk.X)

        self._build_header(top_frame)

        # Middle Container: Options & Action Panels
        middle_frame = tk.Frame(self.root, bg=ModernColors.BG_DARK, padx=16, pady=4)
        middle_frame.pack(fill=tk.X)

        # Left Card: Main Execution Controls & Core Options
        left_card = tk.Frame(
            middle_frame,
            bg=ModernColors.BG_CARD,
            highlightthickness=1,
            highlightbackground=ModernColors.BG_BORDER,
            padx=14,
            pady=12,
        )
        left_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        self._build_execution_card(left_card)

        # Right Card: Additional & Planned Feature Options
        right_card = tk.Frame(
            middle_frame,
            bg=ModernColors.BG_CARD,
            highlightthickness=1,
            highlightbackground=ModernColors.BG_BORDER,
            padx=14,
            pady=12,
        )
        right_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        self._build_planned_options_card(right_card)

        # Bottom Container: Console Output Area
        console_frame = tk.Frame(self.root, bg=ModernColors.BG_DARK, padx=16, pady=8)
        console_frame.pack(fill=tk.BOTH, expand=True)
        self._build_console(console_frame)

    def _build_header(self, parent: tk.Widget) -> None:
        """Build top application header with title and emulator status."""
        title_box = tk.Frame(parent, bg=ModernColors.BG_DARK)
        title_box.pack(side=tk.LEFT, fill=tk.Y)

        title_lbl = tk.Label(
            title_box,
            text="Rise of Kingdoms Automation",
            font=("Segoe UI", 15, "bold"),
            bg=ModernColors.BG_DARK,
            fg=ModernColors.TEXT_PRIMARY,
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            title_box,
            text="Autonomous multi-account farming & rotation for LDPlayer 9",
            font=("Segoe UI", 9),
            bg=ModernColors.BG_DARK,
            fg=ModernColors.TEXT_MUTED,
        )
        sub_lbl.pack(anchor="w")

        # Status badge & emulator indicator on right
        status_box = tk.Frame(parent, bg=ModernColors.BG_DARK)
        status_box.pack(side=tk.RIGHT, fill=tk.Y)

        self.emulator_status_badge = tk.Label(
            status_box,
            text="Emulator: Checking...",
            font=("Segoe UI", 9, "bold"),
            bg=ModernColors.BG_CARD_LIGHT,
            fg=ModernColors.TEXT_SECONDARY,
            padx=10,
            pady=4,
            relief=tk.FLAT,
        )
        self.emulator_status_badge.pack(side=tk.LEFT, padx=(0, 6))

        check_btn = tk.Button(
            status_box,
            text="↻ Refresh",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_SECONDARY,
            activebackground=ModernColors.BG_BORDER,
            activeforeground=ModernColors.TEXT_PRIMARY,
            bd=1,
            relief=tk.SOLID,
            cursor="hand2",
            command=self.check_emulator_connection,
            padx=8,
            pady=2,
        )
        check_btn.pack(side=tk.LEFT)

    def _build_execution_card(self, parent: tk.Widget) -> None:
        """Build the main execution buttons and core configuration options."""
        header_row = tk.Frame(parent, bg=ModernColors.BG_CARD)
        header_row.pack(fill=tk.X, pady=(0, 8))

        lbl = tk.Label(
            header_row,
            text="Execution Controls",
            font=("Segoe UI", 11, "bold"),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_PRIMARY,
        )
        lbl.pack(side=tk.LEFT)

        self.lbl_run_state = tk.Label(
            header_row,
            text="● Idle",
            font=("Segoe UI", 9, "bold"),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_MUTED,
        )
        self.lbl_run_state.pack(side=tk.RIGHT)

        self.lbl_session_farmed = tk.Label(
            header_row,
            text="",
            font=("Segoe UI", 9, "bold"),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.ACCENT_GREEN,
        )
        self.lbl_session_farmed.pack(side=tk.RIGHT, padx=(0, 10))

        # Big Action Buttons Row (Start / Pause / Stop).
        # A grid with uniform columns keeps the three buttons the same
        # width and the same gaps, whatever the window size.
        btn_box = tk.Frame(parent, bg=ModernColors.BG_CARD)
        btn_box.pack(fill=tk.X, pady=(0, 10))
        self.btn_box = btn_box
        # START keeps the most room, PAUSE and STOP share the rest evenly.
        btn_box.columnconfigure(0, weight=2)
        btn_box.columnconfigure(1, weight=1, uniform="action_button")
        btn_box.columnconfigure(2, weight=1, uniform="action_button")

        self.btn_start = tk.Button(
            btn_box,
            text="▶  START AUTOMATION",
            font=("Segoe UI", 11, "bold"),
            bg=ModernColors.ACCENT_GREEN,
            fg="#ffffff",
            activebackground=ModernColors.ACCENT_GREEN_HOVER,
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            cursor="hand2",
            padx=16,
            pady=8,
            command=self.start_automation,
        )
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=5)

        self.btn_pause = tk.Button(
            btn_box,
            text="⏸  PAUSE",
            font=("Segoe UI", 10, "bold"),
            bg=ModernColors.ACCENT_BLUE,
            fg="#ffffff",
            activebackground=ModernColors.ACCENT_BLUE_HOVER,
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            cursor="hand2",
            state=tk.DISABLED,
            padx=10,
            pady=8,
            command=self.toggle_pause,
        )
        self.btn_pause.grid(row=0, column=1, sticky="ew", padx=5)

        self.btn_stop = tk.Button(
            btn_box,
            text="⏹  STOP",
            font=("Segoe UI", 10, "bold"),
            bg=ModernColors.ACCENT_RED,
            fg="#ffffff",
            activebackground=ModernColors.ACCENT_RED_HOVER,
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            cursor="hand2",
            state=tk.DISABLED,
            padx=10,
            pady=8,
            command=self.stop_automation,
        )
        self.btn_stop.grid(row=0, column=2, sticky="ew", padx=5)

        scope_box = tk.Frame(parent, bg=ModernColors.BG_CARD)
        scope_box.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            scope_box,
            text="Quick Farm Scope:",
            font=("Segoe UI", 8, "bold"),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=(0, 6))

        self.btn_farm_current_gmail = tk.Button(
            scope_box,
            text="Farm This Gmail Only",
            font=("Segoe UI", 8, "bold"),
            bg=ModernColors.BG_CARD_LIGHT,
            fg=ModernColors.TEXT_PRIMARY,
            activebackground=ModernColors.BG_BORDER,
            activeforeground=ModernColors.TEXT_PRIMARY,
            bd=1,
            relief=tk.SOLID,
            cursor="hand2",
            command=lambda: self.start_automation(scope="gmail"),
            padx=7,
            pady=3,
        )
        self.btn_farm_current_gmail.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_farm_current_rok = tk.Button(
            scope_box,
            text="Farm This RoK Account Only",
            font=("Segoe UI", 8, "bold"),
            bg=ModernColors.BG_CARD_LIGHT,
            fg=ModernColors.TEXT_PRIMARY,
            activebackground=ModernColors.BG_BORDER,
            activeforeground=ModernColors.TEXT_PRIMARY,
            bd=1,
            relief=tk.SOLID,
            cursor="hand2",
            command=lambda: self.start_automation(scope="rok"),
            padx=7,
            pady=3,
        )
        self.btn_farm_current_rok.pack(side=tk.LEFT)

        # Core Options Section
        opts_lbl = tk.Label(
            parent,
            text="Core Farming Options",
            font=("Segoe UI", 9, "bold"),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.ACCENT_BLUE,
        )
        opts_lbl.pack(anchor="w", pady=(2, 4))

        cb_farm = tk.Checkbutton(
            parent,
            text="Auto Farm Resources (Find nodes Lv8→Lv5 & dispatch 5 armies)",
            variable=self.opt_farm,
            font=("Segoe UI", 9),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_PRIMARY,
            selectcolor=ModernColors.BG_DARK,
            activebackground=ModernColors.BG_CARD,
            activeforeground=ModernColors.TEXT_PRIMARY,
            anchor="w",
        )
        cb_farm.pack(fill=tk.X, pady=1)

        cb_switch_gmail = tk.Checkbutton(
            parent,
            text="Switch Gmail Accounts (Rotate across all signed-in Gmails)",
            variable=self.opt_switch_gmail,
            font=("Segoe UI", 9),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_PRIMARY,
            selectcolor=ModernColors.BG_DARK,
            activebackground=ModernColors.BG_CARD,
            activeforeground=ModernColors.TEXT_PRIMARY,
            anchor="w",
        )
        cb_switch_gmail.pack(fill=tk.X, pady=1)

        self._build_signed_in_accounts_panel(parent)

        lbl_emulator = tk.Label(
            parent,
            text="Emulator",
            font=("Segoe UI", 9),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_MUTED,
        )
        lbl_emulator.pack(anchor="w", pady=(6, 1))

        self.emulator_display = tk.StringVar(value=_emulator_label(self.opt_emulator.get()))
        combo_emulator = ttk.Combobox(
            parent,
            textvariable=self.emulator_display,
            state="readonly",
            values=[_emulator_label(family.key) for family in families()],
            style="Dark.TCombobox",
            font=("Segoe UI", 9),
        )
        combo_emulator.pack(fill=tk.X, pady=1)
        combo_emulator.bind("<<ComboboxSelected>>", self._on_emulator_changed)

        # Quick Utility Diagnostics Tools
        tools_lbl = tk.Label(
            parent,
            text="Quick Tools & Diagnostics",
            font=("Segoe UI", 9, "bold"),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_MUTED,
        )
        tools_lbl.pack(anchor="w", pady=(8, 4))

        tools_box = tk.Frame(parent, bg=ModernColors.BG_CARD)
        tools_box.pack(fill=tk.X)

        btn_screen = tk.Button(
            tools_box,
            text="📸 Screenshot",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD_LIGHT,
            fg=ModernColors.TEXT_PRIMARY,
            activebackground=ModernColors.BG_BORDER,
            activeforeground=ModernColors.TEXT_PRIMARY,
            bd=1,
            relief=tk.SOLID,
            cursor="hand2",
            command=self.run_screenshot_test,
            padx=8,
            pady=3,
        )
        btn_screen.pack(side=tk.LEFT, padx=(0, 4))

        btn_detect = tk.Button(
            tools_box,
            text="🔍 Detect Screen",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD_LIGHT,
            fg=ModernColors.TEXT_PRIMARY,
            activebackground=ModernColors.BG_BORDER,
            activeforeground=ModernColors.TEXT_PRIMARY,
            bd=1,
            relief=tk.SOLID,
            cursor="hand2",
            command=self.run_detect_screen_test,
            padx=8,
            pady=3,
        )
        btn_detect.pack(side=tk.LEFT, padx=(0, 4))

        btn_stock = tk.Button(
            tools_box,
            text="💰 Check Stockpiles",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD_LIGHT,
            fg=ModernColors.TEXT_PRIMARY,
            activebackground=ModernColors.BG_BORDER,
            activeforeground=ModernColors.TEXT_PRIMARY,
            bd=1,
            relief=tk.SOLID,
            cursor="hand2",
            command=self.run_check_stockpiles,
            padx=8,
            pady=3,
        )
        btn_stock.pack(side=tk.LEFT, padx=(4, 0))

    def _build_signed_in_accounts_panel(self, parent: tk.Widget) -> None:
        """Build the persistent Gmail account list and progress controls."""

        panel = tk.Frame(
            parent,
            bg=ModernColors.BG_CARD_LIGHT,
            highlightthickness=1,
            highlightbackground=ModernColors.BG_BORDER,
            padx=8,
            pady=7,
        )
        panel.pack(fill=tk.X, pady=(8, 6))

        header = tk.Frame(panel, bg=ModernColors.BG_CARD_LIGHT)
        header.pack(fill=tk.X, pady=(0, 5))
        tk.Label(
            header,
            text="Signed-in accounts",
            font=("Segoe UI", 9, "bold"),
            bg=ModernColors.BG_CARD_LIGHT,
            fg=ModernColors.TEXT_PRIMARY,
        ).pack(side=tk.LEFT)
        self.btn_reset_gmail_progress = tk.Button(
            header,
            text="Reset Gmail Run Progress",
            font=("Segoe UI", 8, "bold"),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.ACCENT_AMBER,
            activebackground=ModernColors.BG_BORDER,
            activeforeground=ModernColors.ACCENT_AMBER,
            bd=1,
            relief=tk.SOLID,
            cursor="hand2",
            command=self.reset_gmail_run_progress,
            padx=6,
            pady=2,
        )
        self.btn_reset_gmail_progress.pack(side=tk.RIGHT)

        add_row = tk.Frame(panel, bg=ModernColors.BG_CARD_LIGHT)
        add_row.pack(fill=tk.X, pady=(0, 6))
        self.entry_gmail_account = tk.Entry(
            add_row,
            textvariable=self.gmail_account_entry,
            font=("Segoe UI", 9),
            bg=ModernColors.BG_DARK,
            fg=ModernColors.TEXT_PRIMARY,
            insertbackground=ModernColors.TEXT_PRIMARY,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=ModernColors.BG_BORDER,
            highlightcolor=ModernColors.ACCENT_BLUE,
        )
        self.entry_gmail_account.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry_gmail_account.bind("<Return>", self._add_signed_in_account)
        self.btn_add_gmail_account = tk.Button(
            add_row,
            text="Add",
            font=("Segoe UI", 8, "bold"),
            bg=ModernColors.ACCENT_BLUE,
            fg="#ffffff",
            activebackground=ModernColors.ACCENT_BLUE_HOVER,
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._add_signed_in_account,
            padx=10,
            pady=3,
        )
        self.btn_add_gmail_account.pack(side=tk.LEFT, padx=(5, 0))

        list_box = tk.Frame(panel, bg=ModernColors.BG_CARD_LIGHT)
        list_box.pack(fill=tk.X)
        self.signed_in_accounts_canvas = tk.Canvas(
            list_box,
            height=112,
            bg=ModernColors.BG_CARD_LIGHT,
            highlightthickness=0,
            bd=0,
        )
        scrollbar = tk.Scrollbar(
            list_box,
            orient=tk.VERTICAL,
            command=self.signed_in_accounts_canvas.yview,
        )
        self.signed_in_accounts_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.signed_in_accounts_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.signed_in_accounts_rows = tk.Frame(
            self.signed_in_accounts_canvas,
            bg=ModernColors.BG_CARD_LIGHT,
        )
        self._signed_in_accounts_window = self.signed_in_accounts_canvas.create_window(
            (0, 0),
            anchor="nw",
            window=self.signed_in_accounts_rows,
        )
        self.signed_in_accounts_rows.bind(
            "<Configure>", self._resize_signed_in_accounts_scroll_region
        )
        self.signed_in_accounts_canvas.bind(
            "<Configure>", self._resize_signed_in_accounts_rows
        )
        self.refresh_signed_in_accounts()

    def _resize_signed_in_accounts_scroll_region(self, _event: object = None) -> None:
        self.signed_in_accounts_canvas.configure(
            scrollregion=self.signed_in_accounts_canvas.bbox("all")
        )

    def _resize_signed_in_accounts_rows(self, event: tk.Event[tk.Misc]) -> None:
        self.signed_in_accounts_canvas.itemconfigure(
            self._signed_in_accounts_window,
            width=event.width,
        )

    def _add_signed_in_account(self, _event: object = None) -> None:
        """Save a manually supplied Gmail address as an unverified account."""

        value = self.gmail_account_entry.get()
        try:
            account = self.signed_in_accounts.add_manual(value)
        except OSError as exc:
            self.log_error(f"Could not save the signed-in Gmail list: {exc}")
            return
        if account is None:
            self.log_warning("Enter a Gmail address before adding it.")
            return
        self.gmail_account_entry.set("")
        self.refresh_signed_in_accounts()
        self.log_info(
            f"Added {account.identity.label()} as unknown until verified in-game."
        )

    def _remove_signed_in_account(self, identity: GmailIdentity) -> None:
        try:
            removed = self.signed_in_accounts.remove(identity)
        except OSError as exc:
            self.log_error(f"Could not update the signed-in Gmail list: {exc}")
            return
        if removed:
            self.refresh_signed_in_accounts()
            self.log_info(f"Removed {identity.label()} from the signed-in list.")

    def reset_gmail_run_progress(self) -> None:
        """Clear saved Gmail completion status while retaining the account list."""

        if self._is_running:
            self.log_warning("Stop the automation before resetting Gmail progress.")
            return
        try:
            GmailRotation.reset_progress(self.gmail_state_path)
        except OSError as exc:
            self.log_error(f"Could not reset Gmail run progress: {exc}")
            return
        self.refresh_signed_in_accounts()
        self.log_info("Gmail run progress reset. All signed-in accounts are pending.")

    def _toggle_gmail_progress(self, identity: GmailIdentity) -> None:
        """Toggle the saved completion mark while automation is stopped."""
        if self._is_running:
            return
        try:
            rotation = GmailRotation.load(self.gmail_state_path, include_completed=True)
            completed = not rotation.is_farmed(identity)
            rotation.set_manual_farmed(identity, completed)
        except OSError as exc:
            self.log_error(f"Could not update Gmail account progress: {exc}")
            return
        self.refresh_signed_in_accounts()
        self.log_info(f"Marked {identity.label()} as {'farmed' if completed else 'pending'}.")

    def refresh_signed_in_accounts(self) -> None:
        """Render saved accounts with their verification and run-progress state."""

        for child in self.signed_in_accounts_rows.winfo_children():
            child.destroy()
        try:
            accounts = self.signed_in_accounts.load()
            farmed = GmailRotation.farmed_from_path(self.gmail_state_path)
        except OSError as exc:
            self.log_error(f"Could not read Gmail account progress: {exc}")
            accounts, farmed = [], []

        if not accounts:
            tk.Label(
                self.signed_in_accounts_rows,
                text="(empty)",
                font=("Segoe UI", 9, "italic"),
                bg=ModernColors.BG_CARD_LIGHT,
                fg=ModernColors.TEXT_MUTED,
                anchor="w",
            ).pack(fill=tk.X, padx=3, pady=4)
            self._resize_signed_in_accounts_scroll_region()
            return

        for account in accounts:
            row = tk.Frame(
                self.signed_in_accounts_rows,
                bg=ModernColors.BG_CARD_LIGHT,
            )
            row.pack(fill=tk.X, pady=1)
            completed = any(account.identity.matches(identity) for identity in farmed)
            tk.Button(
                row,
                text="✓" if completed else "✕",
                font=("Segoe UI", 10, "bold"),
                bg=ModernColors.BG_CARD_LIGHT,
                fg=(
                    ModernColors.ACCENT_GREEN
                    if completed
                    else ModernColors.ACCENT_RED
                ),
                width=2,
                bd=0,
                relief=tk.FLAT,
                activebackground=ModernColors.BG_CARD_LIGHT,
                cursor="hand2",
                state=tk.DISABLED if self._is_running else tk.NORMAL,
                disabledforeground=(ModernColors.ACCENT_GREEN if completed else ModernColors.ACCENT_RED),
                command=lambda identity=account.identity: self._toggle_gmail_progress(identity),
            ).pack(side=tk.LEFT)
            tk.Label(
                row,
                text=account.identity.label(),
                font=("Segoe UI", 8),
                bg=ModernColors.BG_CARD_LIGHT,
                fg=ModernColors.TEXT_PRIMARY,
                anchor="w",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)
            tk.Label(
                row,
                text="verified" if account.verified else "unknown",
                font=("Segoe UI", 7, "bold"),
                bg=("#064e3b" if account.verified else ModernColors.BG_BORDER),
                fg=(
                    ModernColors.ACCENT_GREEN
                    if account.verified
                    else ModernColors.TEXT_SECONDARY
                ),
                padx=4,
                pady=1,
            ).pack(side=tk.LEFT, padx=(4, 4))
            tk.Button(
                row,
                text="Remove",
                font=("Segoe UI", 7),
                bg=ModernColors.BG_CARD,
                fg=ModernColors.TEXT_SECONDARY,
                activebackground=ModernColors.BG_BORDER,
                activeforeground=ModernColors.TEXT_PRIMARY,
                bd=1,
                relief=tk.SOLID,
                cursor="hand2",
                command=lambda identity=account.identity: self._remove_signed_in_account(
                    identity
                ),
                padx=4,
                pady=1,
                state=(tk.DISABLED if self._is_running else tk.NORMAL),
            ).pack(side=tk.RIGHT)
        self._resize_signed_in_accounts_scroll_region()

    def _build_planned_options_card(self, parent: tk.Widget) -> None:
        """Build the section for temporary & planned feature option toggles."""
        header_row = tk.Frame(parent, bg=ModernColors.BG_CARD)
        header_row.pack(fill=tk.X, pady=(0, 4))

        lbl = tk.Label(
            header_row,
            text="Additional Automation Options",
            font=("Segoe UI", 11, "bold"),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_PRIMARY,
        )
        lbl.pack(side=tk.LEFT)

        badge = tk.Label(
            header_row,
            text="Optional",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD_LIGHT,
            fg=ModernColors.ACCENT_PURPLE,
            padx=6,
            pady=1,
        )
        badge.pack(side=tk.RIGHT)

        desc_lbl = tk.Label(
            parent,
            text="Configure optional routines to run for each RoK account:",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_MUTED,
            anchor="w",
            justify=tk.LEFT,
        )
        desc_lbl.pack(fill=tk.X, pady=(0, 6))

        optional_features = [
            (
                "Collect Daily Rewards",
                self.opt_daily_reward,
                "Claim daily login gifts, quest rewards, and daily VIP points & free chest",
                True,
            ),
            (
                "Collect City Resources",
                self.opt_collect_city_resources,
                "Collect one ready icon for Food, Wood, Stone, and Gold after a small zoom out",
                True,
            ),
            (
                "Alliance Help & Tech",
                self.opt_alliance,
                "Auto-click alliance help hand and donate to recommended technology",
                False,
            ),
            (
                "Auto Train Troops",
                self.opt_train_troops,
                "Queue troop training in Infantry, Cavalry, Archery, and Siege camps",
                False,
            ),
        ]

        for title, var, tooltip, is_ready in optional_features:
            row = tk.Frame(parent, bg=ModernColors.BG_CARD)
            row.pack(fill=tk.X, pady=1)

            cb = tk.Checkbutton(
                row,
                text=title,
                variable=var,
                font=("Segoe UI", 9),
                bg=ModernColors.BG_CARD,
                fg=ModernColors.TEXT_PRIMARY,
                selectcolor=ModernColors.BG_DARK,
                activebackground=ModernColors.BG_CARD,
                activeforeground=ModernColors.TEXT_PRIMARY,
                command=lambda t=title, v=var: self._on_planned_option_toggled(t, v),
                anchor="w",
            )
            cb.pack(side=tk.LEFT)

            tag = tk.Label(
                row,
                text="[Ready]" if is_ready else "[Planned]",
                font=("Segoe UI", 8),
                bg=ModernColors.BG_CARD,
                fg=ModernColors.ACCENT_GREEN if is_ready else ModernColors.ACCENT_PURPLE,
            )
            tag.pack(side=tk.LEFT, padx=4)

        forge_row = tk.Frame(parent, bg=ModernColors.BG_CARD)
        forge_row.pack(fill=tk.X, pady=(5, 1))
        tk.Checkbutton(
            forge_row,
            text="Auto Forge Material",
            variable=self.opt_auto_forge,
            font=("Segoe UI", 9),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_PRIMARY,
            selectcolor=ModernColors.BG_DARK,
            activebackground=ModernColors.BG_CARD,
            activeforeground=ModernColors.TEXT_PRIMARY,
            command=self._on_auto_forge_toggled,
            anchor="w",
        ).pack(side=tk.LEFT)
        tk.Label(
            forge_row,
            text="[Ready]",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.ACCENT_GREEN,
        ).pack(side=tk.LEFT, padx=4)

        forge_material_row = tk.Frame(parent, bg=ModernColors.BG_CARD)
        forge_material_row.pack(fill=tk.X, pady=(0, 3))
        tk.Label(
            forge_material_row,
            text="Forge material",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_MUTED,
        ).pack(side=tk.LEFT)
        self.combo_forge_material = ttk.Combobox(
            forge_material_row,
            textvariable=self.opt_forge_material,
            values=("Leather", "Iron", "Wood", "Bone"),
            state="readonly",
            width=13,
            style="Dark.TCombobox",
            font=("Segoe UI", 8),
        )
        self.combo_forge_material.pack(side=tk.LEFT, padx=(8, 0))
        self.combo_forge_material.bind(
            "<<ComboboxSelected>>",
            self._on_forge_material_changed,
        )

        # Temporary Quick Action Buttons (for testing upcoming modules)
        action_lbl = tk.Label(
            parent,
            text="Test Action Triggers (Future Modules)",
            font=("Segoe UI", 9, "bold"),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_MUTED,
        )
        action_lbl.pack(anchor="w", pady=(8, 4))

        quick_actions_box = tk.Frame(parent, bg=ModernColors.BG_CARD)
        quick_actions_box.pack(fill=tk.X)

        test_buttons = [
            ("🎁 Daily Rewards", "Collect Daily Rewards (incl. Daily VIP Chest)"),
            ("🤝 Alliance Help", "Alliance Help"),
            ("⚔️ Train Troops", "Auto Train Troops"),
        ]

        for btn_text, action_name in test_buttons:
            btn = tk.Button(
                quick_actions_box,
                text=btn_text,
                font=("Segoe UI", 8),
                bg=ModernColors.BG_CARD_LIGHT,
                fg=ModernColors.TEXT_PRIMARY,
                activebackground=ModernColors.BG_BORDER,
                activeforeground=ModernColors.TEXT_PRIMARY,
                bd=1,
                relief=tk.SOLID,
                cursor="hand2",
                command=lambda name=action_name: self.trigger_planned_feature_test(name),
                padx=6,
                pady=3,
            )
            btn.pack(side=tk.LEFT, padx=(0, 4), pady=2)

    def _build_console(self, parent: tk.Widget) -> None:
        """Build the bottom console area showing live application output."""
        header_bar = tk.Frame(parent, bg=ModernColors.BG_DARK)
        header_bar.pack(fill=tk.X, pady=(0, 4))

        console_title = tk.Label(
            header_bar,
            text="Output Console",
            font=("Segoe UI", 10, "bold"),
            bg=ModernColors.BG_DARK,
            fg=ModernColors.TEXT_PRIMARY,
        )
        console_title.pack(side=tk.LEFT)

        self.console_line_count = tk.Label(
            header_bar,
            text="(Ready)",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_DARK,
            fg=ModernColors.TEXT_MUTED,
        )
        self.console_line_count.pack(side=tk.LEFT, padx=6)

        # Right-side console toolbar buttons
        btn_copy = tk.Button(
            header_bar,
            text="📋 Copy Log",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_SECONDARY,
            activebackground=ModernColors.BG_BORDER,
            activeforeground=ModernColors.TEXT_PRIMARY,
            bd=1,
            relief=tk.SOLID,
            cursor="hand2",
            command=self.copy_console_log,
            padx=6,
            pady=1,
        )
        btn_copy.pack(side=tk.RIGHT, padx=(4, 0))

        btn_clear = tk.Button(
            header_bar,
            text="🗑 Clear",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_SECONDARY,
            activebackground=ModernColors.BG_BORDER,
            activeforeground=ModernColors.TEXT_PRIMARY,
            bd=1,
            relief=tk.SOLID,
            cursor="hand2",
            command=self.clear_console,
            padx=6,
            pady=1,
        )
        btn_clear.pack(side=tk.RIGHT, padx=(4, 0))

        cb_scroll = tk.Checkbutton(
            header_bar,
            text="Auto-scroll",
            variable=self.opt_autoscroll,
            font=("Segoe UI", 8),
            bg=ModernColors.BG_DARK,
            fg=ModernColors.TEXT_SECONDARY,
            selectcolor=ModernColors.BG_CARD,
            activebackground=ModernColors.BG_DARK,
            activeforeground=ModernColors.TEXT_PRIMARY,
        )
        cb_scroll.pack(side=tk.RIGHT, padx=(4, 6))

        # Console Text Box with Scrollbar
        console_container = tk.Frame(
            parent,
            bg=ModernColors.BG_CONSOLE,
            highlightthickness=1,
            highlightbackground=ModernColors.BG_BORDER,
        )
        console_container.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(console_container, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        console_font = self._pick_console_font()

        self.txt_console = tk.Text(
            console_container,
            bg=ModernColors.BG_CONSOLE,
            fg=ModernColors.LOG_NORMAL,
            insertbackground=ModernColors.TEXT_PRIMARY,
            font=console_font,
            wrap=tk.WORD,
            yscrollcommand=scrollbar.set,
            padx=10,
            pady=8,
            bd=0,
            relief=tk.FLAT,
        )
        self.txt_console.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.txt_console.yview)

        # Configure color highlight tags for the console
        self.txt_console.tag_configure("normal", foreground=ModernColors.LOG_NORMAL)
        self.txt_console.tag_configure(
            "timestamp", foreground=ModernColors.LOG_TIMESTAMP
        )
        self.txt_console.tag_configure(
            "success", foreground=ModernColors.LOG_SUCCESS, font=(console_font[0], console_font[1], "bold")
        )
        self.txt_console.tag_configure(
            "error", foreground=ModernColors.LOG_ERROR, font=(console_font[0], console_font[1], "bold")
        )
        self.txt_console.tag_configure(
            "warning", foreground=ModernColors.LOG_WARNING
        )
        self.txt_console.tag_configure("info", foreground=ModernColors.LOG_INFO)
        self.txt_console.tag_configure(
            "planned", foreground=ModernColors.LOG_PLANNED
        )
        self.txt_console.tag_configure(
            "banner", foreground=ModernColors.ACCENT_GREEN, font=(console_font[0], console_font[1], "bold")
        )

        self._print_welcome_banner()

    def _pick_console_font(self) -> tuple[str, int]:
        """Return available monospace font for console."""
        available = tkfont.families(self.root)
        for preferred in ("Cascadia Code", "Consolas", "Courier New"):
            if preferred in available:
                return (preferred, 10)
        return ("Courier", 10)

    def _print_welcome_banner(self) -> None:
        """Print initial instructions and system greeting."""
        banner = (
            "========================================================================\n"
            "  ROKauto — Rise of Kingdoms Automation Control Center\n"
            "========================================================================\n"
        )
        self.log_raw(banner, tag="banner")
        self.log_info("System ready. Ensure LDPlayer 9 is running with Rise of Kingdoms open.")
        self.log_info("Select options above and press [START AUTOMATION] or press F5 to begin.")

    def log_raw(self, message: str, tag: str | None = None) -> None:
        """Append raw line to console text widget."""
        self.txt_console.insert(tk.END, message, tag if tag else "normal")
        if self.opt_autoscroll.get():
            self.txt_console.see(tk.END)

    def log(self, message: str, tag: str | None = None) -> None:
        """Format and append a log entry with timestamp."""
        timestamp = datetime.now().strftime("[%H:%M:%S] ")
        self.txt_console.insert(tk.END, timestamp, "timestamp")

        # Automatic tag detection if not explicitly given
        if not tag:
            lower = message.lower()
            if any(k in lower for k in ("error", "failed", "exception", "traceback")):
                tag = "error"
            elif any(k in lower for k in ("connected", "success", "completed", "saved")):
                tag = "success"
            elif any(k in lower for k in ("warning", "stopped safely", "contested", "skip")):
                tag = "warning"
            elif any(k in lower for k in ("[planned", "[notice", "future", "upcoming")):
                tag = "planned"
            elif any(k in lower for k in ("emulator:", "screen:", "account:", "starting")):
                tag = "info"
            else:
                tag = "normal"

        self.txt_console.insert(tk.END, message + "\n", tag)
        if self.opt_autoscroll.get():
            self.txt_console.see(tk.END)

    def log_info(self, message: str) -> None:
        self.log(f"[INFO] {message}", tag="info")

    def log_success(self, message: str) -> None:
        self.log(f"[SUCCESS] {message}", tag="success")

    def log_warning(self, message: str) -> None:
        self.log(f"[WARNING] {message}", tag="warning")

    def log_error(self, message: str) -> None:
        self.log(f"[ERROR] {message}", tag="error")

    def clear_console(self) -> None:
        """Clear all console text."""
        self.txt_console.delete("1.0", tk.END)
        self.log_info("Console cleared.")

    def copy_console_log(self) -> None:
        """Copy all console text to Windows clipboard."""
        content = self.txt_console.get("1.0", tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.log_info("Full console output copied to clipboard.")

    def selected_emulator(self) -> str:
        """Return the emulator family key behind the displayed label."""

        display = self.emulator_display.get()
        for family in families():
            if _emulator_label(family.key) == display:
                return family.key
        return self.opt_emulator.get()

    def _on_emulator_changed(self, _event: object = None) -> None:
        """Explain the selected emulator and check that it is installed."""

        key = self.selected_emulator()
        self.opt_emulator.set(key)
        try:
            family = get_family(key)
        except Exception as exc:
            self.log_error(f"Unknown emulator: {exc}")
            return

        if family.is_installed():
            self.log_success(f"Emulator ready: {family.label} ({', '.join(str(f) for f in family.install_folders())})")
        else:
            self.log_warning(
                f"{family.label} does not look installed on this machine. "
                "Install it, or pick another emulator."
            )

        self._check_adb_ready()

    def _check_adb_ready(self) -> None:
        """Report whether the emulator accepts input over ADB."""

        self.log_info("MuMu uses ADB for screenshots and input." if self.selected_emulator() == "mumu" else "LDPlayer uses native capture and ADB input.")

        try:
            from rok_automation.game.adb import find_adb_device, find_adb_executable

            family = get_family(self.selected_emulator())
            adb_path = find_adb_executable(family)
            preferred = family.preferred_serials()
            if family.key == "mumu":
                if not preferred:
                    raise RuntimeError("No running MuMu instance was found")
                from rok_automation.game.adb import _run_global
                _run_global(adb_path, ["connect", preferred[0]], timeout=25)
            device = find_adb_device(adb_path, preferred=preferred)
            if family.key == "mumu" and device not in preferred:
                raise RuntimeError("MuMu is not connected to ADB")
        except Exception as exc:
            self.log_error(
                f"Input over ADB is not available: {exc}. Enable 'ADB "
                "debugging' with a local connection in the emulator settings "
                "and restart the emulator."
            )
            return
        self.log_success(f"ADB ready: {device} via {adb_path}")

    def check_emulator_connection(self) -> bool:
        """Check if the emulator is reachable and update the status badge.

        Frames come from the emulator's own renderer, so the emulator
        counts as connected even when its window is minimised or covered.
        The ADB connection is checked too, because the input still travels
        through the Android input pipeline.
        """

        try:
            from rok_automation.game.adb import (
                find_adb_device,
                find_adb_executable,
            )

            family = get_family(self.selected_emulator())
            adb_path = find_adb_executable(family)
            preferred = family.preferred_serials()
            if family.key == "mumu":
                if not preferred:
                    raise RuntimeError("No running MuMu instance was found")
                from rok_automation.game.adb import _run_global
                _run_global(adb_path, ["connect", preferred[0]], timeout=25)
            device = find_adb_device(adb_path, preferred=preferred)
            if family.key == "mumu" and device not in preferred:
                raise RuntimeError("MuMu is not connected to ADB")
        except Exception:
            self.emulator_status_badge.config(
                text="No emulator found",
                fg=ModernColors.ACCENT_RED,
                bg="#450a0a",
            )
            return False
        self.emulator_status_badge.config(
            text=f"Emulator: Connected ({device})",
            fg=ModernColors.ACCENT_GREEN,
            bg="#064e3b",
        )
        return True

    def _on_planned_option_toggled(self, title: str, var: tk.BooleanVar) -> None:
        """Handle toggling of optional / future features."""
        state_str = "ENABLED" if var.get() else "DISABLED"
        if title == "Collect Daily Rewards":
            # The automation runs in its own process, so the option is
            # handed over through a flag file that is read before every
            # account. Switching it on therefore takes effect from the
            # next account, even in the middle of a run.
            enabled = bool(var.get())
            try:
                set_daily_rewards(enabled, self.daily_reward_flag_path)
            except OSError as exc:
                self.log_error(f"Could not update the daily reward option: {exc}")
                return
            self.log(
                f"[Option] {title} set to {state_str}. "
                + (
                    "Rewards will be claimed from the next account "
                    "(daily VIP points, VIP free chest and the Campaign "
                    "golden chest)."
                    if enabled
                    else "No more rewards will be claimed."
                ),
                tag="info" if enabled else "warning",
            )
            return

        if title == "Collect City Resources":
            enabled = bool(var.get())
            try:
                set_city_resources(enabled)
            except OSError as exc:
                self.log_error(f"Could not update city resource collection: {exc}")
                return
            self.log(
                f"[Option] {title} set to {state_str}. "
                + (
                    "Ready city icons will be collected from the next account."
                    if enabled
                    else "No more city resources will be collected."
                ),
                tag="info" if enabled else "warning",
            )
            return

        self.log(
            f"[Option] {title} set to {state_str}. "
            f"(Note: Feature module is prepared for a future update and will be executed when implemented)",
            tag="planned",
        )

    def _on_auto_forge_toggled(self) -> None:
        """Persist the selected forge material for the worker process."""

        enabled = bool(self.opt_auto_forge.get())
        try:
            set_forge_material(self.opt_forge_material.get() if enabled else None)
        except OSError as exc:
            self.log_error(f"Could not update the forge option: {exc}")
            return
        state = "ENABLED" if enabled else "DISABLED"
        detail = (
            f"Next ready forge will produce {self.opt_forge_material.get()}."
            if enabled
            else "No more forge production will be started."
        )
        self.log(f"[Option] Auto Forge Material set to {state}. {detail}")

    def _on_forge_material_changed(self, _event: object = None) -> None:
        """Apply a changed material immediately when automatic forging is on."""

        if not self.opt_auto_forge.get():
            return
        try:
            set_forge_material(self.opt_forge_material.get())
        except OSError as exc:
            self.log_error(f"Could not select forge material: {exc}")
            return
        self.log_info(
            f"Forge material changed to {self.opt_forge_material.get()}; "
            "it takes effect from the next account."
        )

    def trigger_planned_feature_test(self, feature_name: str) -> None:
        """Handler for the temporary buttons to test/preview planned actions."""
        if "daily reward" in feature_name.lower():
            if self._is_running:
                self.log_warning("Cannot run test while automation is active.")
                return
            cmd = [_get_python_cli_executable(), "-u", "-m", "rok_automation", "--daily-reward-only"]
            self.log(
                "Executing Daily Rewards collection test (VIP & Campaign; planned feature active)...",
                tag="planned",
            )
            self._launch_utility_command(cmd)
            return

        self.log(
            f"[Action Trigger] Testing '{feature_name}': "
            f"Module is planned and currently in design. Stub action simulated successfully.",
            tag="planned",
        )

    def start_automation(self, scope: str = "all") -> None:
        """Compile selected options and launch the automation worker subprocess."""
        if self._is_running:
            self.log_warning("Automation is already running.")
            return

        if scope not in {"all", "gmail", "rok"}:
            raise ValueError(f"Unsupported farm scope: {scope}")

        self._session_targets.clear()
        self.lbl_session_farmed.config(text="")
        if self.session_summary_path and self.session_summary_path.is_file():
            try:
                self.session_summary_path.unlink()
            except OSError:
                pass

        # Check options
        farm_enabled = self.opt_farm.get() or scope != "all"
        if not farm_enabled:
            if self.opt_daily_reward.get():
                self.log_info(
                    "Farming is unchecked; running Daily Rewards collection only."
                )
            else:
                confirm = messagebox.askyesno(
                    "ROKauto",
                    "Farming option is unchecked. Do you want to run screen diagnostic only?",
                )
                if not confirm:
                    return

        if self.opt_daily_reward.get():
            self.log_info(
                "Daily Rewards active: VIP points, free VIP chest, and Campaign golden chest will be claimed."
            )

        if scope == "gmail":
            self.log_info("Farming all RoK accounts in the current Gmail only.")
        elif scope == "rok":
            self.log_info("Farming the current RoK account only.")

        # Check other planned options and notify user in log
        planned_enabled = []
        if self.opt_alliance.get():
            planned_enabled.append("Alliance Help & Tech")
        if self.opt_train_troops.get():
            planned_enabled.append("Auto Train Troops")

        if planned_enabled:
            self.log(
                f"[Planned Features Enabled] The following upcoming features are active in config: "
                f"{', '.join(planned_enabled)}. (These modules will be executed as their handlers are deployed).",
                tag="planned",
            )

        cmd = [_get_python_cli_executable(), "-u", "-m", "rok_automation"]
        cmd.extend(["--emulator", self.selected_emulator()])
        if self.gmail_state_path is not None:
            cmd.extend(["--gmail-state", str(self.gmail_state_path)])
        if farm_enabled:
            cmd.append("--farm")
            # The reward option is handed over through the flag file, so
            # it can be switched while the run is going.
            try:
                set_daily_rewards(
                    bool(self.opt_daily_reward.get()),
                    self.daily_reward_flag_path,
                )
                set_city_resources(bool(self.opt_collect_city_resources.get()))
                set_forge_material(
                    self.opt_forge_material.get()
                    if self.opt_auto_forge.get()
                    else None
                )
            except OSError as exc:
                self.log_error(f"Could not apply automation options: {exc}")
        elif self.opt_daily_reward.get():
            cmd.append("--daily-reward-only")
        if scope in {"gmail", "rok"} or not self.opt_switch_gmail.get():
            cmd.append("--no-switch-gmail")
        if scope == "rok":
            cmd.append("--current-rok-only")

        self.log_info(f"Starting automation with command: {' '.join(cmd)}")
        self._start_worker(cmd)

    def _spawn_process(self, cmd: list[str]) -> subprocess.Popen[str]:
        """Start the worker subprocess and return its handle.

        The process is created on the calling thread so the handle exists
        immediately. Creating it inside the reader thread left STOP with
        nothing to kill when the user clicked straight after START.
        """

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"

        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            env=env,
        )

    def _start_worker(self, cmd: list[str]) -> None:
        """Spawn the worker process and start streaming its output."""

        try:
            process = self._spawn_process(cmd)
        except Exception as exc:
            self.log_error(f"Failed to start the automation process: {exc}")
            self._set_running_state(False)
            return

        self._current_process = process
        self._set_running_state(True)
        self._worker_thread = threading.Thread(
            target=self._run_subprocess_worker,
            args=(process,),
            daemon=True,
        )
        self._worker_thread.start()

    def run_screenshot_test(self) -> None:
        """Run quick screenshot diagnostic command."""
        if self._is_running:
            self.log_warning("Cannot run test while automation is active.")
            return
        cmd = [
            _get_python_cli_executable(),
            "-u",
            "-m",
            "rok_automation",
            "--screenshot",
            "--emulator",
            self.selected_emulator(),
        ]
        self.log_info("Executing screenshot test...")
        self._launch_utility_command(cmd)

    def run_detect_screen_test(self) -> None:
        """Run quick screen classification diagnostic command."""
        if self._is_running:
            self.log_warning("Cannot run test while automation is active.")
            return
        cmd = [
            _get_python_cli_executable(),
            "-u",
            "-m",
            "rok_automation",
            "--detect-screen",
            "--emulator",
            self.selected_emulator(),
        ]
        self.log_info("Executing screen detection diagnostic...")
        self._launch_utility_command(cmd)

    def run_check_stockpiles(self) -> None:
        """Run the stockpile diagnostic: top-bar amounts + farming choice."""
        if self._is_running:
            self.log_warning("Cannot run test while automation is active.")
            return
        cmd = [
            _get_python_cli_executable(),
            "-u",
            "-m",
            "rok_automation",
            "--check-stockpiles",
            "--emulator",
            self.selected_emulator(),
        ]
        self.log_info("Reading the top-bar stockpiles...")
        self._launch_utility_command(cmd)

    def _launch_utility_command(self, cmd: list[str]) -> None:
        """Run a short utility command in background."""
        self._start_worker(cmd)

    def _run_subprocess_worker(self, process: subprocess.Popen[str]) -> None:
        """Background worker thread that streams a subprocess's output."""
        try:
            if process.stdout is not None:
                for line in iter(process.stdout.readline, ""):
                    if line:
                        self._output_queue.put(line.rstrip("\r\n"))
                    elif process.poll() is not None:
                        break

            process.wait()
            return_code = process.returncode

            if return_code == 0:
                self._output_queue.put("[SUCCESS] Process completed successfully.")
            else:
                self._output_queue.put(
                    f"[ERROR] Process exited with return code {return_code}."
                )

        except Exception as exc:
            self._output_queue.put(f"[ERROR] Failed to execute process: {exc}")
        finally:
            self._current_process = None
            self._output_queue.put(None)  # Sentinel for completion

    def _poll_output_queue(self) -> None:
        """Drain lines from background worker queue into the console."""
        try:
            while True:
                line = self._output_queue.get_nowait()
                if line is None:
                    # Sentinel received: process finished
                    self._set_running_state(False)
                    self.refresh_signed_in_accounts()
                    self.check_emulator_connection()
                else:
                    if GMAIL_PROGRESS_UPDATED in line:
                        self.refresh_signed_in_accounts()
                        continue
                    self.log(line)
                    # Live tracking of dispatched resource armies
                    match = re.search(
                        r"Army\s+(\d+)/5\s+dispatched\s+to\s+(Food|Wood|Stone|Gold)\s+Lv(\d+)",
                        line,
                        re.IGNORECASE,
                    )
                    if match:
                        res_name = match.group(2).capitalize()
                        lvl = int(match.group(3))
                        try:
                            target = SearchTarget(
                                resource=Resource(res_name), level=lvl
                            )
                            self._session_targets.append(target)
                            totals = calculate_farmed_resources(
                                self._session_targets
                            )
                            grand_total = sum(totals.values())
                            self.lbl_session_farmed.config(
                                text=(
                                    f"Farmed: {format_resource_amount(grand_total)} "
                                    f"({len(self._session_targets)} armies)"
                                )
                            )
                        except Exception:
                            pass
        except queue.Empty:
            pass

        self.root.after(50, self._poll_output_queue)

    def toggle_pause(self) -> None:
        """Pause the running automation, or resume it when already paused."""

        if not self._is_running:
            return

        if self._is_paused:
            clear_paused()
            self._is_paused = False
            self.log_info("Resuming automation.")
            self.lbl_run_state.config(
                text="● Running...", fg=ModernColors.ACCENT_GREEN
            )
            self.btn_pause.config(
                text="⏸  PAUSE",
                bg=ModernColors.ACCENT_BLUE,
                activebackground=ModernColors.ACCENT_BLUE_HOVER,
            )
        else:
            set_paused()
            self._is_paused = True
            self.log_warning("Pausing automation — the game will be left untouched.")
            self.lbl_run_state.config(
                text="● Paused", fg=ModernColors.ACCENT_AMBER
            )
            self.btn_pause.config(
                text="▶  PLAY",
                bg=ModernColors.ACCENT_AMBER,
                activebackground=ModernColors.ACCENT_AMBER,
            )

    def stop_automation(self) -> None:
        """Stop the running automation process and show the resources farmed."""

        proc = self._current_process
        if not self._is_running and proc is None:
            return

        self.log_warning("Stopping automation requested by user (force stop)...")
        self.lbl_run_state.config(text="● Stopping...", fg=ModernColors.ACCENT_AMBER)

        # A paused run is blocked in its wait loop; make sure the pause flag
        # is cleared so nothing is left half-paused for the next run.
        if self._is_paused:
            clear_paused()
            self._is_paused = False

        self._terminate_process(proc)

        self.log_info("Automation stopped.")
        self._set_running_state(False)

        # Show how much resources were farmed before force stop!
        self._display_farmed_resources_summary(stopped_early=True)

    def _terminate_process(self, proc: subprocess.Popen[str] | None) -> None:
        """Terminate the worker process and wait until it is really gone.

        ``taskkill /T`` handles helper processes, but it can silently fail
        (for example while the process is still starting up), so the exit
        is confirmed and a direct kill is used as a fallback. This is what
        made STOP occasionally need several presses.
        """

        if proc is None:
            return

        if proc.poll() is not None:
            return

        if sys.platform == "win32":
            try:
                run_hidden(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                    timeout=20,
                )
            except Exception as exc:
                self.log_warning(f"taskkill could not run ({exc}); killing directly")
        else:
            try:
                proc.terminate()
            except Exception as exc:
                self.log_warning(f"terminate failed ({exc}); killing directly")

        # Confirm the process really exited before reporting "stopped".
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

        try:
            proc.kill()
        except Exception:
            pass

        try:
            proc.wait(timeout=5)
        except Exception:
            self.log_error("The automation process could not be terminated.")

    def _display_farmed_resources_summary(self, *, stopped_early: bool = True) -> None:
        """Display full breakdown of all resources farmed up to this moment."""
        targets = list(self._session_targets)

        # Check if disk session_summary.json has recorded targets
        if self.session_summary_path and self.session_summary_path.is_file():
            try:
                data = json.loads(
                    self.session_summary_path.read_text(encoding="utf-8")
                )
                disk_targets = [
                    SearchTarget(resource=Resource(t["resource"]), level=t["level"])
                    for t in data.get("targets", [])
                    if "resource" in t and "level" in t
                ]
                if len(disk_targets) >= len(targets):
                    targets = disk_targets
            except Exception:
                pass

        if not targets:
            self.log_info(
                "Force stop completed: No resource nodes were gathered before stopping."
            )
            return

        totals = calculate_farmed_resources(targets)
        grand_total = sum(totals.values())
        army_counts = {
            r: sum(1 for t in targets if t.resource == r)
            for r in Resource
        }

        resource_labels = {
            Resource.FOOD: "Food (Ngô)",
            Resource.WOOD: "Wood (Gỗ)",
            Resource.STONE: "Stone (Đá)",
            Resource.GOLD: "Gold (Vàng)",
        }

        title = (
            "TOTAL RESOURCES FARMED BEFORE FORCE STOP"
            if stopped_early
            else "TOTAL RESOURCES FARMED THIS RUN"
        )

        self.log_raw("\n")
        self.log_raw("=" * 66 + "\n", tag="warning")
        self.log_raw(f"  {title}\n", tag="warning")
        self.log_raw("=" * 66 + "\n", tag="warning")
        for resource in (Resource.FOOD, Resource.WOOD, Resource.STONE, Resource.GOLD):
            amount = totals[resource]
            armies = army_counts[resource]
            formatted = format_resource_amount(amount)
            self.log_raw(
                f"  • {resource_labels[resource]:<14}: {formatted:<18} "
                f"across {armies} arm{'y' if armies == 1 else 'ies'}\n",
                tag="normal",
            )
        self.log_raw("-" * 66 + "\n", tag="warning")
        self.log_raw(
            f"  GRAND TOTAL    : {format_resource_amount(grand_total)} "
            f"gathered across {len(targets)} armies\n",
            tag="success",
        )
        if stopped_early:
            self.log_raw(
                f"  Status         : Force stopped by user after {len(targets)} armies\n",
                tag="warning",
            )
        self.log_raw("=" * 66 + "\n\n", tag="warning")

        self.lbl_session_farmed.config(
            text=f"Farmed: {format_resource_amount(grand_total)} ({len(targets)} armies) [Stopped]"
        )

    def _set_running_state(self, is_running: bool) -> None:
        """Toggle UI controls based on active running state."""
        self._is_running = is_running
        if is_running:
            self.btn_start.config(state=tk.DISABLED, bg=ModernColors.BG_CARD_LIGHT)
            self.btn_farm_current_gmail.config(state=tk.DISABLED)
            self.btn_farm_current_rok.config(state=tk.DISABLED)
            self.btn_reset_gmail_progress.config(state=tk.DISABLED)
            self.btn_add_gmail_account.config(state=tk.DISABLED)
            self.entry_gmail_account.config(state=tk.DISABLED)
            self.btn_stop.config(state=tk.NORMAL, bg=ModernColors.ACCENT_RED)
            self.btn_pause.config(state=tk.NORMAL)
            self.lbl_run_state.config(
                text="● Running...", fg=ModernColors.ACCENT_GREEN
            )
            self.console_line_count.config(text="(Active)", fg=ModernColors.ACCENT_GREEN)
        else:
            self.btn_start.config(state=tk.NORMAL, bg=ModernColors.ACCENT_GREEN)
            self.btn_farm_current_gmail.config(state=tk.NORMAL)
            self.btn_farm_current_rok.config(state=tk.NORMAL)
            self.btn_reset_gmail_progress.config(state=tk.NORMAL)
            self.btn_add_gmail_account.config(state=tk.NORMAL)
            self.entry_gmail_account.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED, bg=ModernColors.BG_CARD_LIGHT)
            self.btn_pause.config(state=tk.DISABLED)
            # Reset the pause button so the next run starts unpaused.
            self._is_paused = False
            self.btn_pause.config(
                text="⏸  PAUSE",
                bg=ModernColors.ACCENT_BLUE,
                activebackground=ModernColors.ACCENT_BLUE_HOVER,
            )
            self.lbl_run_state.config(text="● Idle", fg=ModernColors.TEXT_MUTED)
            self.console_line_count.config(text="(Ready)", fg=ModernColors.TEXT_MUTED)
        self.refresh_signed_in_accounts()

    def on_close(self) -> None:
        """Handle application close event."""
        if self._is_running and self._current_process is not None:
            if messagebox.askyesno(
                "Exit ROKauto",
                "Automation is currently active. Do you want to stop it and exit?",
            ):
                self.stop_automation()
                self.root.destroy()
        else:
            self.root.destroy()


def launch_gui(hide_console: bool = True) -> int:
    """Entry point to launch the graphical user interface."""
    if hide_console:
        hide_console_window()
    # The toolkit has to know about the display scaling before the first
    # window is created, otherwise Windows stretches the whole interface.
    scale = enable_dpi_awareness()
    root = tk.Tk()
    app = ROKAutomationApp(root, display_scale=scale)
    root.mainloop()
    return 0
