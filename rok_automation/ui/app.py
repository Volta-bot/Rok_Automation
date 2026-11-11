"""Desktop GUI for Rise of Kingdoms Farming Automation.

Provides controls to launch automation routines, configure options (including
future/planned feature toggles), trigger test actions, and monitor live log
output in an integrated console.
"""

from __future__ import annotations

import os
import queue
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

from rok_automation.game.ldplayer import find_ldplayer_window


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


class ROKAutomationApp:
    """Main Application Window for ROKauto."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Rise of Kingdoms Automation — ROKauto")
        self.root.geometry("880x720")
        self.root.minsize(760, 560)
        self.root.configure(bg=ModernColors.BG_DARK)

        # Process & Thread state
        self._current_process: subprocess.Popen[str] | None = None
        self._worker_thread: threading.Thread | None = None
        self._output_queue: queue.Queue[str | None] = queue.Queue()
        self._is_running = False

        # Options state
        self.opt_farm = tk.BooleanVar(value=True)
        self.opt_switch_gmail = tk.BooleanVar(value=True)
        self.opt_reset_gmail = tk.BooleanVar(value=False)

        # Temporary / Planned options (not yet implemented)
        self.opt_daily_reward = tk.BooleanVar(value=False)
        self.opt_vip_chest = tk.BooleanVar(value=False)
        self.opt_alliance = tk.BooleanVar(value=False)
        self.opt_tavern = tk.BooleanVar(value=False)
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

        # Big Action Buttons Row (Start / Stop)
        btn_box = tk.Frame(parent, bg=ModernColors.BG_CARD)
        btn_box.pack(fill=tk.X, pady=(0, 10))

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
        self.btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.btn_stop = tk.Button(
            btn_box,
            text="⏹  STOP",
            font=("Segoe UI", 11, "bold"),
            bg=ModernColors.ACCENT_RED,
            fg="#ffffff",
            activebackground=ModernColors.ACCENT_RED_HOVER,
            activeforeground="#ffffff",
            bd=0,
            relief=tk.FLAT,
            cursor="hand2",
            state=tk.DISABLED,
            padx=16,
            pady=8,
            command=self.stop_automation,
        )
        self.btn_stop.pack(side=tk.RIGHT, fill=tk.X, expand=False)

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

        cb_reset = tk.Checkbutton(
            parent,
            text="Reset Gmail Run Progress (Start fresh rotation from first Gmail)",
            variable=self.opt_reset_gmail,
            font=("Segoe UI", 9),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_PRIMARY,
            selectcolor=ModernColors.BG_DARK,
            activebackground=ModernColors.BG_CARD,
            activeforeground=ModernColors.TEXT_PRIMARY,
            anchor="w",
        )
        cb_reset.pack(fill=tk.X, pady=1)

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

        btn_diag = tk.Button(
            tools_box,
            text="📋 Diagnose Window",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD_LIGHT,
            fg=ModernColors.TEXT_PRIMARY,
            activebackground=ModernColors.BG_BORDER,
            activeforeground=ModernColors.TEXT_PRIMARY,
            bd=1,
            relief=tk.SOLID,
            cursor="hand2",
            command=self.run_diagnose_window,
            padx=8,
            pady=3,
        )
        btn_diag.pack(side=tk.LEFT)

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
            text="Modular / Future",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD_LIGHT,
            fg=ModernColors.ACCENT_PURPLE,
            padx=6,
            pady=1,
        )
        badge.pack(side=tk.RIGHT)

        desc_lbl = tk.Label(
            parent,
            text="Enable upcoming features (temporary placeholders for future routines):",
            font=("Segoe UI", 8),
            bg=ModernColors.BG_CARD,
            fg=ModernColors.TEXT_MUTED,
            anchor="w",
            justify=tk.LEFT,
        )
        desc_lbl.pack(fill=tk.X, pady=(0, 6))

        # List of upcoming option toggles
        planned_options = [
            (
                "Collect Daily Rewards",
                self.opt_daily_reward,
                "Claim daily VIP points, login gifts, and daily quest chests",
            ),
            (
                "Collect VIP Free Chest",
                self.opt_vip_chest,
                "Claim free daily VIP chest & exclusive governor points",
            ),
            (
                "Alliance Help & Tech",
                self.opt_alliance,
                "Auto-click alliance help hand and donate to recommended technology",
            ),
            (
                "Tavern Free Chests",
                self.opt_tavern,
                "Open available free Silver and Gold recruitment chests in Tavern",
            ),
            (
                "Auto Train Troops",
                self.opt_train_troops,
                "Queue troop training in Infantry, Cavalry, Archery, and Siege camps",
            ),
        ]

        for title, var, tooltip in planned_options:
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
                text="[Planned]",
                font=("Segoe UI", 8),
                bg=ModernColors.BG_CARD,
                fg=ModernColors.ACCENT_PURPLE,
            )
            tag.pack(side=tk.LEFT, padx=4)

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
            ("🎁 Daily Reward", "Collect Daily Reward"),
            ("⭐ VIP Chest", "Collect VIP Chest"),
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

    def check_emulator_connection(self) -> bool:
        """Check if LDPlayer window is available and update status badge."""
        window = find_ldplayer_window()
        if window is not None:
            self.emulator_status_badge.config(
                text=f"Emulator: Connected ({window.title})",
                fg=ModernColors.ACCENT_GREEN,
                bg="#064e3b",
            )
            return True
        else:
            self.emulator_status_badge.config(
                text="Emulator: Disconnected (No LDPlayer found)",
                fg=ModernColors.ACCENT_RED,
                bg="#450a0a",
            )
            return False

    def _on_planned_option_toggled(self, title: str, var: tk.BooleanVar) -> None:
        """Handle toggling of planned / future option."""
        state_str = "ENABLED" if var.get() else "DISABLED"
        self.log(
            f"[Option] {title} set to {state_str}. "
            f"(Note: Feature module is prepared for a future update and will be executed when implemented)",
            tag="planned",
        )

    def trigger_planned_feature_test(self, feature_name: str) -> None:
        """Handler for the temporary buttons to test/preview planned actions."""
        self.log(
            f"[Action Trigger] Testing '{feature_name}': "
            f"Module is planned and currently in design. Stub action simulated successfully.",
            tag="planned",
        )

    def start_automation(self) -> None:
        """Compile selected options and launch the automation worker subprocess."""
        if self._is_running:
            self.log_warning("Automation is already running.")
            return

        # Check options
        if not self.opt_farm.get():
            # If user unchecked farm, let them know or ask
            confirm = messagebox.askyesno(
                "ROKauto",
                "Farming option is unchecked. Do you want to run screen diagnostic only?",
            )
            if not confirm:
                return

        # Check planned options and notify user in log
        planned_enabled = []
        if self.opt_daily_reward.get():
            planned_enabled.append("Collect Daily Rewards")
        if self.opt_vip_chest.get():
            planned_enabled.append("Collect VIP Chest")
        if self.opt_alliance.get():
            planned_enabled.append("Alliance Help & Tech")
        if self.opt_tavern.get():
            planned_enabled.append("Tavern Free Chests")
        if self.opt_train_troops.get():
            planned_enabled.append("Auto Train Troops")

        if planned_enabled:
            self.log(
                f"[Planned Features Enabled] The following upcoming features are active in config: "
                f"{', '.join(planned_enabled)}. (These modules will be executed as their handlers are deployed).",
                tag="planned",
            )

        cmd = [sys.executable, "-u", "-m", "rok_automation"]
        if self.opt_farm.get():
            cmd.append("--farm")
        if not self.opt_switch_gmail.get():
            cmd.append("--no-switch-gmail")
        if self.opt_reset_gmail.get():
            cmd.append("--reset-gmail-run")

        self.log_info(f"Starting automation with command: {' '.join(cmd)}")
        self._set_running_state(True)

        self._worker_thread = threading.Thread(
            target=self._run_subprocess_worker,
            args=(cmd,),
            daemon=True,
        )
        self._worker_thread.start()

    def run_screenshot_test(self) -> None:
        """Run quick screenshot diagnostic command."""
        if self._is_running:
            self.log_warning("Cannot run test while automation is active.")
            return
        cmd = [sys.executable, "-u", "-m", "rok_automation", "--screenshot"]
        self.log_info("Executing screenshot test...")
        self._launch_utility_command(cmd)

    def run_detect_screen_test(self) -> None:
        """Run quick screen classification diagnostic command."""
        if self._is_running:
            self.log_warning("Cannot run test while automation is active.")
            return
        cmd = [sys.executable, "-u", "-m", "rok_automation", "--detect-screen"]
        self.log_info("Executing screen detection diagnostic...")
        self._launch_utility_command(cmd)

    def run_diagnose_window(self) -> None:
        """Run window hierarchy diagnostic command."""
        if self._is_running:
            self.log_warning("Cannot run test while automation is active.")
            return
        cmd = [sys.executable, "-u", "-m", "rok_automation", "--diagnose-window"]
        self.log_info("Diagnosing LDPlayer window structure...")
        self._launch_utility_command(cmd)

    def _launch_utility_command(self, cmd: list[str]) -> None:
        """Run a short utility command in background."""
        self._set_running_state(True)
        self._worker_thread = threading.Thread(
            target=self._run_subprocess_worker,
            args=(cmd,),
            daemon=True,
        )
        self._worker_thread.start()

    def _run_subprocess_worker(self, cmd: list[str]) -> None:
        """Background worker thread that runs a subprocess and streams output."""
        try:
            # Set UTF-8 encoding environment for clean subprocess unicode handling
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONUTF8"] = "1"

            # On Windows, hide command window if desired
            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            self._current_process = subprocess.Popen(
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

            if self._current_process.stdout is not None:
                for line in iter(self._current_process.stdout.readline, ""):
                    if line:
                        self._output_queue.put(line.rstrip("\r\n"))
                    elif self._current_process.poll() is not None:
                        break

            self._current_process.wait()
            return_code = self._current_process.returncode

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
                    self.check_emulator_connection()
                else:
                    self.log(line)
        except queue.Empty:
            pass

        self.root.after(50, self._poll_output_queue)

    def stop_automation(self) -> None:
        """Gracefully stop or terminate the running automation process."""
        if not self._is_running or self._current_process is None:
            return

        self.log_warning("Stopping automation requested by user...")
        self.lbl_run_state.config(text="● Stopping...", fg=ModernColors.ACCENT_AMBER)

        proc = self._current_process
        if proc is not None:
            try:
                if sys.platform == "win32":
                    # Force kill process tree cleanly on Windows
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        check=False,
                    )
                else:
                    proc.terminate()
            except Exception as exc:
                self.log_error(f"Error terminating process: {exc}")

        self.log_info("Automation stopped.")
        self._set_running_state(False)

    def _set_running_state(self, is_running: bool) -> None:
        """Toggle UI controls based on active running state."""
        self._is_running = is_running
        if is_running:
            self.btn_start.config(state=tk.DISABLED, bg=ModernColors.BG_CARD_LIGHT)
            self.btn_stop.config(state=tk.NORMAL, bg=ModernColors.ACCENT_RED)
            self.lbl_run_state.config(
                text="● Running...", fg=ModernColors.ACCENT_GREEN
            )
            self.console_line_count.config(text="(Active)", fg=ModernColors.ACCENT_GREEN)
        else:
            self.btn_start.config(state=tk.NORMAL, bg=ModernColors.ACCENT_GREEN)
            self.btn_stop.config(state=tk.DISABLED, bg=ModernColors.BG_CARD_LIGHT)
            self.lbl_run_state.config(text="● Idle", fg=ModernColors.TEXT_MUTED)
            self.console_line_count.config(text="(Ready)", fg=ModernColors.TEXT_MUTED)

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


def launch_gui() -> int:
    """Entry point to launch the graphical user interface."""
    root = tk.Tk()
    app = ROKAutomationApp(root)
    root.mainloop()
    return 0
