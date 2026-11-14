# ROKauto

ROKauto is a Windows desktop automation tool for **Rise of Kingdoms** on **MuMu Player**. It uses screen recognition and ADB to farm resources across characters and signed-in Gmail accounts, with optional daily and city tasks. The game UI templates are designed for the **Vietnamese** in-game language.

## Features

- **State-driven farming:** Detects the current screen, navigates between the city, map, search, resource node and troop screens, and recovers from missed actions or unexpected dialogs. It reads the march counter and fills available slots, up to five armies.
- **Resource selection:** Recognizes Vietnamese and English resource keywords in governor names. Without a keyword it selects the lowest readable top-bar stockpile; if none are readable, it uses a weighted random choice favoring gold.
- **Node search:** Searches levels 8 down to 5, falls back to other resources, avoids green incoming routes and duplicate coordinates, and remembers recently farmed nodes across cycles for 30 minutes. Run summaries estimate yields from dispatched resource types and levels.
- **Character and Gmail rotation:** Traverses starred characters and switches through signed-in Gmail accounts. Rotation progress is saved and can be resumed after interruption. The GUI can maintain a signed-in account list, mark accounts complete, remove entries and reset run progress.
- **Daily rewards:** Optionally claims available VIP points, the free VIP chest and the campaign chest, handling already-claimed rewards and common dialogs.
- **City tasks:** The GUI can collect visible ready Food, Wood, Stone and Gold icons after a small zoom out. It can also claim a ready forge output and select Leather, Iron, Wood or Bone for production, whether or not an output was ready. Enabled city tasks run after farming each account, or immediately on the current account when farming is disabled.
- **Background operation:** Screenshots use MuMu's native renderer when available, checked against an ADB screenshot at startup; capture falls back to ADB if necessary. Taps use MuMu's local ADB connection, so the host mouse is not moved and the emulator can be covered. Captures are normalized to 1280×720, with corresponding coordinate mapping for input. Capture latency is measured to scale timeouts, and ADB input retries after reconnection.
- **Control center:** Start, pause and stop a run, choose the account scope and optional tasks, view logs and resource totals, and run screenshot, screen-detection and stockpile diagnostics. Alliance help/technology and troop training are marked as planned, not implemented.

## Requirements and setup

- Windows, Python 3.11 or later, and MuMu Player with Rise of Kingdoms installed and signed in.
- Set the game's language to Vietnamese. Use a landscape 16:9 emulator display; the project normalizes screenshots to 1280×720.
- Enable local ADB debugging in MuMu and restart the instance if necessary. MuMu's bundled ADB is discovered automatically; alternatively put `adb` on PATH or set `ROK_ADB_PATH` to its executable.

From the project directory:

```powershell
python -m pip install -e .
```

Start MuMu and open the game, then launch `run_gui.vbs` or `run_gui.bat`. `run.bat` offers a menu for the GUI or CLI farming. The GUI also provides connection status and a refresh button.

## Command line

```powershell
# Farm all available characters and signed-in Gmail accounts
python -m rok_automation --farm

# Limit the run to the current Gmail or current RoK character
python -m rok_automation --farm --no-switch-gmail
python -m rok_automation --farm --current-rok-only

# Claim daily rewards before farming, or claim them on the current account only
python -m rok_automation --farm --daily-reward
python -m rok_automation --daily-reward-only

# Run city tasks previously enabled in the GUI on the current account
python -m rok_automation --city-tasks-only

# Diagnostics
python -m rok_automation --screenshot artifacts/mumu.png
python -m rok_automation --detect-screen
python -m rok_automation --check-stockpiles

# Select a MuMu instance or specify an ADB client/device
python -m rok_automation --farm --emulator-index 0
python -m rok_automation --farm --adb-path C:\path\to\adb.exe --adb-serial 127.0.0.1:16384
python -m rok_automation --help
```

`--emulator mumu` can be supplied explicitly. `ROK_EMULATOR` selects the registered emulator family; when set, `ROK_EMULATOR_INDEX` can also select an instance. An explicit CLI selection takes precedence over environment and saved settings. `--gmail-state PATH` changes the rotation progress file, and `--search-level` supplies a known resource level to screen detection. `--gui --show-console` launches the GUI with its console visible.

## Runtime files

The `state/` directory stores emulator selection, Gmail rotation and account records, feature flags, and recently farmed nodes. `artifacts/` holds screenshots and run output. Both directories are ignored by Git. The GUI's **Reset Gmail Run Progress** control resets rotation progress; use it when starting a fresh rotation. Treat account records and screenshots as private.

## Development

The `rok_automation/automation` package contains the farming controller, account switching, daily rewards and city tasks; `game` contains ADB, capture and emulator discovery; `vision` handles screen and text recognition; `logic` contains resource selection and yield calculations; and `ui` contains the desktop control center. Templates are in `templates/`.

```powershell
python -m unittest discover -s tests
```

Most tests use mocks or synthetic images and do not require a running emulator. Actual game operation still depends on MuMu, ADB, the game UI and matching templates.

