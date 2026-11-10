# Rise of Kingdoms Farming Automation (`rok-automation`)

A fast, state-driven Windows desktop automation tool for **Rise of Kingdoms** running inside **LDPlayer 9**. It automatically detects the active governor account, infers its preferred resource from its name, dispatches all available gathering armies while avoiding contested nodes, and cycles through every starred character linked to the signed-in account.

---

## Key Features

### 1. State-Driven Farming Loop with Automatic Self-Recovery
Instead of relying on blind delays or fixed coordinate clicks, the automation continuously classifies the current game screen and executes the action for that exact state:

`City View` → **(Map Button)** → `World Map` → **(Search Button)** → `Resource Search Menu` → **(Select Resource & Level → Tìm kiếm)** → `Resource Node Found` → **(Thu thập)** → `New Troops (Quân mới)` → **(Hành quân)** → `World Map`

- **Self-Healing Recovery:** If any click misses, lags, or accidentally closes a popup back to the World Map (or an earlier step), the controller detects the actual screen it landed on and resumes the flow from that state automatically.
- **Smart Resource Selection:** Reads the governor name on the World Map (or from the character selection card), strips the `[Guild]` tag, and maps Vietnamese/English keywords (`Vàng`/`Gold`, `Đá`/`Stone`, `Ngô`/`Food`, `Gỗ`/`Wood`, including OCR variants like `G6`, `Ng0`) to the preferred resource. If an account name has no resource keyword, it selects a weighted-random resource (**40% Gold**, **20% Stone**, **20% Food**, **20% Wood**).
- **Dynamic Level Fallback (Lv8 → Lv5):** Searches from Level 8 down to Level 5 for the preferred resource first, then shuffles the remaining resource types (Lv8 → Lv5). When a level has no available nodes or is contested, it remembers the highest viable level for that resource for the rest of the account's run.
- **Contested Node & Duplicate Avoidance:**
  - **Green Incoming Route Detection:** Analyzes HSV color masks and line geometry to detect incoming green dotted march lines from other players and skips contested nodes.
  - **Coordinate Deduplication:** Reads the node's `X: ... Y: ...` coordinates from the resource popup and skips nodes already visited during the current account's session.
  - **Map-Change Detection:** Compares map margin strips before and after pressing `Tìm kiếm` to detect when no node was found (`Không tìm thấy...`) without waiting for toast animations to fade.
- **Active March Slot Detection:** Reads the `x/5` march counter on the right side of the World Map (treating an absent counter as `0/5`) and only dispatches armies for the remaining free slots.

### 2. Multi-Account Character Switching (`Nhân vật`)
Once all 5 army slots are marching (or all resource levels are exhausted), the automation switches to the next starred character:

`Top-Left Governor Portrait` → `Cài đặt (Settings)` → `Nhân vật (Characters)` → `Starred Character List` → `Confirm Login (Có)` → `Game Load`

- **State-Driven Menu Navigation:** Recovers automatically if any menu click misses or falls back to an earlier screen.
- **Positional Starred-Account Traversal:**
  - Detects the active account via its bright green checkmark badge (`selected_account_check.png` + HSV green verification).
  - Supports 12-account starred lists split across a top 6 page (`1/12..6/12`) and bottom 6 page (`7/12..12/12`).
  - If the green checkmark is not in the first 6 accounts upon opening `Nhân vật`, it scrolls down immediately to scan the last 6 accounts.
  - If started mid-list, it jumps to the nearest list end (`1/12` or `12/12`) and traverses sequentially in one clean pass (`top-to-bottom` or `bottom-to-top`), skipping the already-farmed starting account.

### 3. High-Speed Vision Optimizations
- **Static Region-of-Interest (ROI) Cropping:** Restricts template matching for static HUD buttons (`city_map_button`, `world_search_button`, `gather_button`, `new_troops_button`, `march_button`, `resource_*`, `settings_button`, `character_menu_button`) to their exact normalized screen regions.
- **Pinned Position & Scale Caching:** Remembers the exact pixel bounds and template scale (`_POSITION_CACHE` and `_SCALE_CACHE`) after the first match, turning subsequent button lookups into a `< 2ms` single-scale verification pass.
- **Per-Resource Search Popup Caching:** Because the `Tìm kiếm`, `+`, and `-` popup box shifts horizontally depending on which resource icon (`Food`, `Wood`, `Stone`, `Gold`) is selected, the detector caches button positions and `Cấp: N` levels **per resource**. Subsequent armies searching the same resource and level skip OCR entirely (`0ms`) and click `Tìm kiếm` immediately.
- **Sub-Millisecond Blue-Button Text Verification:** Distinguishes similar cyan buttons (`Tìm kiếm`, `Thu thập`, `Quân mới`, `Hành quân`) in `~0.05ms` by correlating their inner text strip (`[22%:78%, 14%:86%]`), reserving OCR only for genuinely ambiguous frames.
- **In-Memory Frame Caching:** Captured screenshots are cached in memory (`BGR` and `Grayscale`) during `mss` grab so downstream detectors and OCR readers never re-decode PNG files from disk.

---

## Technology Stack

| Component | Technology | Purpose |
| :--- | :--- | :--- |
| **Language** | Python 3.11+ | Core application logic, state machines, and CLI |
| **Window & Input API** | Win32 API (`ctypes` / `user32.dll`) | Per-monitor DPI awareness (`DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2`), `LDPlayer` & `RenderWindow` handle discovery, and hardware-level cursor click/drag synthesis |
| **Screen Capture** | `mss` | Ultra-fast desktop region capture of LDPlayer's child `RenderWindow` |
| **Computer Vision** | OpenCV (`opencv-python`) & `numpy` | Multi-scale normalized cross-correlation (`TM_CCOEFF_NORMED`), HSV color segmentation (green march routes & active account checkmarks), and map-motion diffing |
| **OCR Engine** | `rapidocr` + `onnxruntime` | Offline Vietnamese & English text recognition for governor names, `x/5` army counters, `Cấp: N` search levels, and `X/Y` node coordinates |

---

## Project Structure

```text
ROKauto/
├── rok_automation/
│   ├── __main__.py                 # CLI entry point (--farm, --detect-screen, --screenshot, --diagnose-window)
│   ├── automation/
│   │   ├── controller.py           # State-driven farming loop, search level caching, & army dispatch
│   │   ├── account_switcher.py     # State-driven profile/settings/character switcher & list scroller
│   │   ├── context.py              # Per-account runtime context (armies out, target, errors)
│   │   └── states.py               # Farming state enum definitions
│   ├── game/
│   │   ├── ldplayer.py             # Win32 LDPlayer window discovery, MSS capture, click & drag
│   │   └── interface.py            # GameInterface abstraction & timing configuration
│   ├── logic/
│   │   └── resource_selector.py    # Vietnamese/English resource keyword parser & Lv8-Lv5 search planner
│   └── vision/
│       ├── screen.py               # ROI/pinned template matcher, screen classifier, & level reader
│       ├── account_label.py        # RapidOCR wrapper for governor labels & x/5 march counter
│       └── green_route.py          # HSV + morphological detector for incoming green march routes
├── templates/                      # UI reference templates (navigation, search, gathering, profile)
├── run.bat                         # One-click interactive Windows launcher
└── pyproject.toml                  # Package metadata and dependencies
```

---

## Installation & Usage

### Prerequisites
- **OS:** Windows 10 / 11
- **Emulator:** LDPlayer 9 (with Rise of Kingdoms set to **Tiếng Việt** in-game language)
- **Python:** Python 3.11 or newer

---

### Recommended Emulator & Display Settings

> [!IMPORTANT]
> **MAKE THE LDPLAYER SCREEN AS BIG AS POSSIBLE!**
> Computer vision template matching and text OCR (reading account names, army count `x/5`, and search levels `Cấp: 8`) require high pixel sharpness. **Do not run LDPlayer in a tiny window or low resolution.** If the screen is shrunk, text and buttons become blurry and detection will fail.

1. **Maximize the Window:**
   - Always maximize or expand the LDPlayer window on your monitor so UI elements are as large and clear as possible.
   - Keep the LDPlayer window visible and unminimized during automation (do not overlap other windows over it).

2. **LDPlayer Emulator Resolution Settings:**
   - In LDPlayer, click the **Settings** (gear icon) on the right toolbar ➔ **Advanced**:
     - Mode: **Tablet (Landscape)**
     - Resolution: **`1600x900 (dpi 240)`** or **`1920x1080 (dpi 280)`** *(minimum: `1280x720 (dpi 240)`)*
     - **Avoid** Phone/Mobile portrait resolutions (e.g. `720x1280`) or low resolutions (`960x540`).
   - Save and restart LDPlayer.

3. **Windows Display Scaling:**
   - Windows Settings ➔ **System** ➔ **Display** ➔ Set **Scale** to **100%** to prevent Windows from blurring the game render window.

4. **Rise of Kingdoms In-Game Settings:**
   - **Language:** Must be set to **Tiếng Việt** (*Avatar ➔ Cài đặt ➔ Ngôn ngữ*).
   - **Graphics Quality:** Set to **Trung bình (Medium)** or **Cao (High)** for crisp button edges and fonts.

---

### Setup
Clone the repository and install the package in editable mode:

```powershell
git clone https://github.com/Volta-bot/Rok_Automation.git
cd Rok_Automation
python -m pip install -e .
```

### Quick Start (`run.bat`)
Double-click `run.bat` or run it from a terminal:

```powershell
.\run.bat
```
Press `y` when prompted to start farming across all starred accounts.

### CLI Commands

- **Run full multi-account farming automation:**
  ```powershell
  python -m rok_automation --farm
  ```
- **Detect and classify the current LDPlayer screen (read-only):**
  ```powershell
  python -m rok_automation --detect-screen
  ```
- **Capture a screenshot of LDPlayer's render area:**
  ```powershell
  python -m rok_automation --screenshot artifacts/ldplayer.png
  ```
- **Inspect LDPlayer window handles and child bounds:**
  ```powershell
  python -m rok_automation --diagnose-window
  ```

> **Note:** Because capture and input operate directly on LDPlayer's visible `RenderWindow`, keep LDPlayer unminimized and visible on screen while the automation is running.
