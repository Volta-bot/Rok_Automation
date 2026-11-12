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
- **Total Resources Farmed Calculation:** Tracks every dispatched army's resource type and node level (Lv8–Lv5), calculating yields according to official node capacities (Food/Wood: Lv8 1.575M, Lv7 1.418M, Lv6 1.26M, Lv5 945k; Stone: Lv8 1.181M, Lv7 1.063M, Lv6 945k, Lv5 709k; Gold: Lv8 525k, Lv7 472.5k, Lv6 420k, Lv5 315k) and logging a detailed per-resource and grand total summary upon completing the run.

### 2. Multi-Account Character Switching (`Nhân vật`)
Once all 5 army slots are marching (or all resource levels are exhausted), the automation switches to the next starred character:

`Top-Left Governor Portrait` → `Cài đặt (Settings)` → `Nhân vật (Characters)` → `Starred Character List` → `Confirm Login (Có)` → `Game Load`

- **State-Driven Menu Navigation:** Recovers automatically if any menu click misses or falls back to an earlier screen.
- **Positional Starred-Account Traversal:**
  - Detects the active account via its bright green checkmark badge (`selected_account_check.png` + HSV green verification).
  - Supports 12-account starred lists split across a top 6 page (`1/12..6/12`) and bottom 6 page (`7/12..12/12`).
  - If the green checkmark is not in the first 6 accounts upon opening `Nhân vật`, it scrolls down immediately to scan the last 6 accounts.
  - If started mid-list, it jumps to the nearest list end (`1/12` or `12/12`) and traverses sequentially in one clean pass (`top-to-bottom` or `bottom-to-top`), skipping the already-farmed starting account.

### 3. Multi-Gmail Account Switching (`Tài khoản / Trung Tâm Người Dùng`)
After farming all RoK characters in the current Gmail account, the automation automatically cycles to the next Gmail account until every logged-in Gmail account has been farmed:

`Governor Portrait` → `Cài đặt` → `Tài khoản` → `Trung Tâm Người Dùng` → `Chuyển tài khoản` → `Expand Dropdown` → `Scroll to Bottom` → `Select Bottom Account` → `Đăng nhập` → `Game Load`

- **Bottom-Most LRU Rotation:** Lilith's account switcher lists logged-in Gmail accounts by recency. By scrolling all the way to the bottom and selecting the least-recently used account, the automation cycles through every Gmail account in sequence.
- **Run Completion Tracking:** Remembers the starting Gmail account and every account farmed (`state/gmail_rotation.json`). When all other accounts have been farmed and the rotation returns to the starting account, the run is complete.
- **Fault-Tolerant & Resumable:** Progress is written atomically after every switch; if interrupted, restarting `--farm` resumes from the last active Gmail account without refarming previously cleared accounts.
- **In-Game Modal Recovery:** Automatically detects and confirms in-game dialogs ("Xác nhận", "Đồng ý", "Có") during game reload.

### 4. Daily Reward Collection (VIP & Campaign)
When enabled, daily rewards are claimed first before any resource farming begins:
1. **VIP Rewards:**
   - Detects the VIP badge/text under the governor portrait across any VIP level.
   - Opens the VIP menu and claims the wooden chest above (daily VIP points) and dismisses the "Thưởng điểm hàng ngày" popup.
   - Claims the metal chest below via the green "Nhận" button (free daily VIP chest) and dismisses the "Xin chúc mừng" popup.
   - Closes the VIP menu via the top-right 'X' button.
2. **Campaign (Chiến dịch / Chinh phạt) Golden Chest:**
   - Expands the bottom-right menu (3-dots button) if needed and clicks "Chiến dịch".
   - Selects "Chinh phạt" and clicks the yellow golden chest on the campaign map to claim it.
   - Confirms and dismisses the reward popup via "Xác nhận".
   - Presses the `<` back button on the top-left twice at the same position to close Chinh phạt and Chiến dịch menus, cleanly returning to City view to begin farming.
3. **Session Position Pinning:** Remembers static button locations across accounts to avoid redundant rescanning.
4. **Resilient Error Recovery:** Detects already-claimed chests, dismisses stuck popups, and recovers safely back to the City view without hanging.

### 5. Resolution-Independent Detection
- **Canonical Capture Size:** Every screenshot is rescaled to a fixed **1280x720** frame before any detection runs. The UI templates are authored against one resolution, so normalising the capture keeps template matching, OCR regions and the pinned-position cache **identical no matter how large or small the LDPlayer window is**.
  - Without this, the matcher's scale ceiling (`maximum_scale = 1.35`) meant any capture wider than ~1728px could not match the templates at all — the score collapsed from `0.99` to `0.65` on a maximised window.
  - Measured across window widths from 800px to 1920px on real game frames, the mean template score spread fell from **0.117 to 0.014** (30 of 31 template/frame pairs improved; the remaining one was unchanged at 0.013 → 0.016).
- **Aspect-Correct Crops:** A window whose aspect is not 16:9 is centre-cropped rather than stretched, so the game content is never distorted.
- **Bidirectional Interpolation:** Shrinking uses area averaging (best detail retention); growing uses **bilinear** rather than cubic/lanczos, because those ring around sharp edges and the ringing made the OCR read of the small `Cấp: N` label fail at some window sizes.
- **Correct Click Mapping:** Clicks and drags are mapped back through the same crop and scale the capture used, so a click always lands on the same game pixel regardless of the window size.

### 6. Capture Backends (Desktop / ADB / Native)
The automation can read the game through three interchangeable backends, selected with `--backend` or in the GUI's *Capture Backend* dropdown. Each one falls back to the next slower backend automatically, so a run never dies because the preferred one is missing.

| Backend | Frames come from | Cost per frame | Window may be hidden? |
| :--- | :--- | :--- | :--- |
| `native` | The emulator's own renderer | **≈ 6–100 ms** | **Yes** |
| `adb` | `adb exec-out screencap` | ≈ 250–380 ms | Yes |
| `desktop` | The visible window (`mss`) | ≈ 21 ms | No |

- **Native Capture (fastest, recommended):** Many emulators can hand out their own rendered framebuffer. LDPlayer ships `ldopengl64.dll` next to `ldconsole.exe`; its `CreateScreenShotInstance` entry point returns a COM-style object whose second vtable slot copies the emulator's *rendered framebuffer* into memory. Reading that buffer avoids a full frame transfer entirely and works while the emulator window is minimised, hidden or covered.
  - The emulator instance, its PID and its resolution are read from `ldconsole.exe list2`, so the captured frame and the input coordinates always describe the same emulator.
  - The library is located automatically through the uninstall registry (`DisplayIcon`) and the usual install folders; set `ROK_LDPLAYER_DIR` to override it. It needs LDPlayer 9.0.78 or newer.
  - Frames arrive bottom-up and are flipped into image order before any detection runs.
- **ADB Capture:** Frames are pulled from the emulator over ADB and input travels through the Android input pipeline, so the host mouse is never moved.
  - Raw captures are used rather than PNG (`250 ms` versus `316 ms` per frame) and are normalised to the same canonical `1280x720` size as the desktop path, so every template and OCR region behaves identically.
- **Desktop Capture:** Reads the visible render child window from the screen. Fastest to set up, but the window must stay visible and uncovered. The window is matched by title *and* by having a child that renders the game, so a browser tab mentioning the emulator is never picked up by mistake.
- **Input:** All backends deliver taps and drags through the Android input pipeline (`input tap` / `input swipe`), which never steals the host mouse. Drags are stretched with the distance they cover so the game reads them as a drag rather than a flick or a stray tap.
- **Adaptive Screen-Wait Budgets:** The controller measures the real capture latency at startup and scales every screen-wait budget to match, keeping the number of poll attempts per transition constant. Without this a slow capture would give the controller only one or two samples of a transition and it would time out before the screen had finished changing.
- **Setup for the background backends:** In the emulator's settings, enable **ADB debugging** with a **local connection** and restart it. Leave *remote connection* off — it exposes the ADB port to the whole network, and ADB grants full control of the device.

---

### 7. Emulator Support
The capture and input layer is emulator-agnostic: the farming loop only ever talks to the `GameInterface` protocol (`screenshot`, `click`, `drag`, `wait`), so switching emulators changes nothing in the automation logic, the templates or the OCR regions.

Each emulator family is one small module in `rok_automation/game/emulators/` that answers four questions:

```python
@register
class LDPlayerFamily(EmulatorFamily):
    key = "ldplayer"                       # used on the command line
    label = "LDPlayer"                     # shown in the GUI
    window_terms = ("ldplayer", "雷电")      # how to find its window
    window_classes = ("LDPlayerMainFrame",) # ... and its main-frame class
    adb_locations = ("adb.exe",)           # its bundled adb
    registry_markers = ("dnplayer.exe",)   # how to find its install folder
    supports_native_capture = True         # can it read its own framebuffer?

    def discover(self) -> list[EmulatorInstance]: ...
    def open_capture(self, instance) -> FrameSource: ...
```

**Adding an emulator is therefore a single new file** — no change to the automation, the vision layer or the CLI.

| Family | Status | Native capture | Instance discovery |
| :--- | :--- | :--- | :--- |
| `ldplayer` | Supported | `ldopengl64.dll` | `ldconsole.exe list2` |
| `mumu` | Supported (ADB + desktop) | *awaiting verification* | `MuMuManager.exe adb -v <index>` |

- **Choosing an emulator:** `--emulator ldplayer` on the command line, the *Emulator* dropdown in the GUI, or the `ROK_EMULATOR` environment variable. The choice is remembered in `state/emulator.json`.
- **MuMu Player:** MuMu 12's native capture library (`external_renderer_ipc.dll`) has to be checked against the installed version before it can be trusted, so MuMu currently uses the ADB backend. Everything else — discovery, the bundled adb, the window match and the serial — already works.

### 8. Surviving a Flaky Emulator
Emulators sometimes stop answering ADB until the connection is re-established. The symptoms are a capture that suddenly takes tens of seconds, or a device that shows up as `offline`.

- **Bounded captures:** An ADB capture that has not returned within `25 s` is treated as stale instead of blocking the run for minutes.
- **Automatic reconnection:** A stale capture or tap re-establishes the connection (`adb connect 127.0.0.1:<port>`, derived from the emulator's serial) and is retried once, so a hiccup does not end the run.
- **Native capture avoids the problem:** The native backend reads frames straight from the renderer, so a stalled ADB connection cannot slow the detection down. Only the input commands still travel over ADB.
- **Restarting the emulator:** When the emulator itself has stopped responding, `ldconsole.exe launch --index 0` starts it again; `MuMuManager.exe api -v 0 launch_player` does the same for MuMu.

---

### 7. High-Speed Vision Optimizations
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
| **Native Capture** | `ctypes` + LDPlayer's `ldopengl64.dll` | Reads the emulator's rendered framebuffer directly, for the fastest background capture |
| **Emulator Discovery** | `ldconsole.exe list2` | Reports the emulator instances, their PIDs and their resolution |
| **Computer Vision** | OpenCV (`opencv-python`) & `numpy` | Multi-scale normalized cross-correlation (`TM_CCOEFF_NORMED`), HSV color segmentation (green march routes & active account checkmarks), and map-motion diffing |
| **OCR Engine** | `rapidocr` + `onnxruntime` | Offline Vietnamese & English text recognition for governor names, `x/5` army counters, `Cấp: N` search levels, and `X/Y` node coordinates |

---

## Project Structure

```text
ROKauto/
├── rok_automation/
│   ├── __main__.py                 # CLI entry point (--gui, --farm, --backend, --emulator, --detect-screen, --screenshot)
│   ├── ui/                         # Graphical User Interface (Control Center & Live Console)
│   │   ├── app.py                  # Tkinter UI application, process worker, and live console stream
│   │   └── __init__.py
│   ├── automation/                 # Emulator-agnostic farming logic
│   │   ├── controller.py           # State-driven farming loop, search level caching, & army dispatch
│   │   ├── account_switcher.py     # State-driven profile/settings/character switcher & list scroller
│   │   ├── gmail_switcher.py       # Multi-Gmail account switcher & LRU rotation tracker
│   │   ├── daily_reward.py         # Daily VIP & Campaign reward claimer
│   │   ├── context.py              # Per-account runtime context (armies out, target, errors)
│   │   └── states.py               # Farming state enum definitions
│   ├── game/                       # Capture & input layer (the emulator-aware part)
│   │   ├── backends.py             # Backend selection & automatic fallback chain
│   │   ├── emulators/              # One module per supported emulator family
│   │   │   ├── __init__.py         # The registry, instance model & selection
│   │   │   ├── ldplayer.py         # LDPlayer: ldconsole discovery + ldopengl64 capture
│   │   │   └── mumu.py             # MuMu Player: MuMuManager discovery + bundled adb
│   │   ├── desktop.py              # Generic window discovery, screen capture & mouse input
│   │   ├── native.py               # Native frame source + ADB input, combined
│   │   ├── adb.py                  # ADB capture + Android input pipeline delivery
│   │   ├── frame.py                # Canonical 1280x720 frame geometry
│   │   ├── interface.py            # GameInterface abstraction & timing configuration
│   │   ├── console.py              # Console output that survives a limited code page
│   │   └── pause.py                # Cooperative pause support
│   ├── logic/
│   │   └── resource_selector.py    # Vietnamese/English resource keyword parser & Lv8-Lv5 search planner
│   └── vision/
│       ├── screen.py               # ROI/pinned template matcher, screen classifier, & level reader
│       ├── account_label.py        # RapidOCR wrapper for governor labels & x/5 march counter
│       └── green_route.py          # HSV + morphological detector for incoming green march routes
├── templates/                      # UI reference templates (navigation, search, gathering, profile)
├── run.bat                         # Interactive Windows CLI launcher
├── run_gui.bat                     # One-click Graphical User Interface launcher (auto-closes console)
├── run_gui.vbs                     # Completely silent GUI launcher (no console window)
└── pyproject.toml                  # Package metadata and dependencies
```

---

## Installation & Usage

### Prerequisites
- **OS:** Windows 10 / 11
- **Emulator:** LDPlayer 9 or newer, or MuMu Player 12 (with Rise of Kingdoms set to **Tiếng Việt** in-game language)
- **Python:** Python 3.11 or newer
- **Optional:** ADB, for background operation (*Settings* ➔ *ADB debugging* ➔ *Enable local connection*)
- **Optional:** native capture needs the emulator's own capture library (LDPlayer 9.0.78 or newer)

---

### Recommended Emulator & Display Settings

> [!IMPORTANT]
> **Keep the LDPlayer window reasonably large.**
> The detector normalises every capture to a fixed 1280x720 frame, so **detection no longer breaks when the window is resized** — a maximised window works as well as a small one. However, a *very small* window still loses real detail: the emulator downsamples the game's 1920x1080 render to fit, and the tiny text (`Cấp: 8`, the `x/5` counter) becomes unreadable no matter how it is scaled afterwards.
>
> **Sweet spot:** anything from roughly **1100px wide upward**. Below that, expect occasional OCR misses on the smallest labels.

> [!NOTE]
> This only applies to the `desktop` backend. The `native` and `adb` backends read the emulator's own 1920x1080 render, so they always see the full-resolution frame and the window may be minimised or covered.

1. **Window Size (flexible):**
   - Any window size works for *detection*, because captures are normalised to 1280x720 before matching.
   - With the `desktop` backend, keep the LDPlayer window visible and unminimized during automation (do not overlap other windows over it) — the capture reads the visible pixels.
   - If you resize the window mid-run, the next capture simply picks up the new size.

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

### Graphical User Interface (`run_gui.vbs` / `run_gui.bat`)
Launch the ROKauto Control Center GUI cleanly without any background command prompt window:
- **`run_gui.vbs` (Recommended):** Completely silent launch with zero console window.
- **`run_gui.bat`:** Standard launcher that launches detached `pythonw` and closes its command prompt automatically.
- **CLI / Terminal:** `python -m rok_automation --gui` (automatically hides attached console; pass `--show-console` to keep it).

The graphical user interface features:
- **One-Click Start & Stop:** Launch automation with `[START AUTOMATION]` (or press `F5`) and stop it safely anytime with `[STOP]` (or press `Esc`). Stopping reliably terminates the worker on the first press.
- **Pause & Resume:** `[PAUSE]` freezes the run between two actions — the game is left untouched and the button turns into `[PLAY]` to continue exactly where it stopped.
- **Focused Farming Buttons:** Run every starred RoK character in the current Gmail only, or farm the current RoK character only, without switching Gmail accounts.
- **Core Automation Options:** Easily toggle automatic resource farming, multi-Gmail switching, and rotation resetting.
- **Capture Backend Dropdown:** Choose *Native* (fastest, reads the emulator's own framebuffer), *ADB* (background, slower frames) or *Desktop* (captures the visible window). The chosen backend is used for the run and for the quick diagnostic tools.
- **Emulator Dropdown:** Choose which emulator family to drive (LDPlayer, MuMu Player). The choice is remembered between runs.
- **Modular & Planned Feature Options:** Toggle or test upcoming modules such as *Collect Daily Rewards* (claims daily gifts, quest chests, and VIP points & free chest), *Alliance Help & Tech*, and *Auto Train Troops*.
- **Integrated Live Output Console:** Monospace console stream underneath with real-time log coloring, auto-scroll, log copying, and clearing.
- **Diagnostic Quick Tools:** Instant one-click buttons for LDPlayer screen capture, screen classification, and window hierarchy diagnostics.

### Interactive CLI Launcher (`run.bat`)
Double-click `run.bat` or run it from a terminal:

```powershell
.\run.bat
```
Press `y` when prompted to start farming across all starred accounts.

### CLI Commands

- **Run full multi-account farming automation across all Gmail logins:**
  ```powershell
  python -m rok_automation --farm
  ```
- **Claim daily rewards (VIP & Campaign) before farming each account:**
  ```powershell
  python -m rok_automation --farm --daily-reward
  ```
- **Claim daily rewards on the current account only and exit:**
  ```powershell
  python -m rok_automation --daily-reward-only
  ```
- **Farm characters in the current Gmail account only (disable Gmail switching):**
  ```powershell
  python -m rok_automation --farm --no-switch-gmail
  ```
- **Farm the current RoK character only (disable character and Gmail switching):**
  ```powershell
  python -m rok_automation --farm --no-switch-gmail --current-rok-only
  ```
- **Reset saved Gmail rotation progress and start a fresh run:**
  ```powershell
  python -m rok_automation --farm --reset-gmail-run
  ```
- **Farm in the background (the emulator may be minimised or covered):**
  ```powershell
  python -m rok_automation --farm --backend native
  ```
  `native` reads the emulator's own framebuffer and is the fastest option; `--backend adb` pulls frames over ADB instead, and `--backend desktop` captures the visible window. All three fall back to the next slower one automatically. Add `--adb-serial emulator-5554` to pick a specific device, or `--emulator-index 0` to pick a specific emulator instance.
- **Choose the emulator (LDPlayer or MuMu Player):**
  ```powershell
  python -m rok_automation --farm --emulator ldplayer
  python -m rok_automation --farm --emulator mumu
  ```
  With no `--emulator`, the installed family is used. The choice is remembered in `state/emulator.json`, and `ROK_EMULATOR` overrides it.
- **Keep the old ADB-only behaviour:**
  ```powershell
  python -m rok_automation --farm --adb
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

> **Note:** The `desktop` backend reads the visible `RenderWindow`, so keep LDPlayer unminimized and visible while it is selected. The `native` and `adb` backends read the emulator itself and work with the window minimised, hidden or covered.
