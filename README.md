# ROKauto

ROKauto is a Windows desktop automation tool for **Rise of Kingdoms** on **MuMu Player**. It uses screen recognition and ADB to farm resources across characters and signed-in Gmail accounts, with optional daily and city tasks. The game UI templates are designed for the **Vietnamese** in-game language.

## Features

- **State-driven farming:** Detects the current screen, navigates between the city, map, search, resource node and troop screens, and recovers from missed actions or unexpected dialogs. It reads the march counter and fills available slots, up to five armies.
- **Menu rescans:** When an expected screen is missing, allow an initial scan plus at least five fresh rescans before giving up, even if capture/OCR has exhausted the timeout. This applies to farming, daily rewards, account/Gmail navigation, alliance navigation and forge menus. Rescans do not mean repeating a click. Recognized accidental mailboxes are closed before retrying the interrupted resource search.
- **Resource selection:** Recognizes Vietnamese and English resource keywords in governor names. Without a keyword it selects the lowest readable top-bar stockpile; if none are readable, it uses a weighted random choice favoring gold.
- **Node search:** Searches levels 8 down to 5, falls back to other resources, avoids green incoming routes and duplicate coordinates, and remembers recently farmed nodes across cycles for 30 minutes. Run summaries estimate yields from dispatched resource types and levels.
- **Character and Gmail rotation:** Traverses starred characters and switches through signed-in Gmail accounts. Rotation progress is saved and can be resumed after interruption. A successfully completed full Gmail run automatically resets its run and character progress for the next run; stopped or failed runs retain their progress. The GUI can maintain a signed-in account list, mark accounts complete, remove entries and reset run progress.
- **Daily rewards:** Optionally claims available VIP points, the free VIP chest and the campaign chest, handling already-claimed rewards and common dialogs.
- **City tasks:** The GUI can collect visible ready Food, Wood, Stone and Gold icons after a small zoom out. It can also claim a ready forge output and select Leather, Iron, Wood or Bone for production, whether or not an output was ready. Enabled city tasks run after farming each account, or immediately on the current account when farming is disabled.
- **Background operation:** Screenshots use MuMu's native renderer when available, checked against an ADB screenshot at startup; capture falls back to ADB if necessary. Taps use MuMu's local ADB connection, so the host mouse is not moved and the emulator can be covered. Captures are normalized to 1280×720, with corresponding coordinate mapping for input. Capture latency is measured to scale timeouts, and ADB input retries after reconnection.
- **Farming capture and recognition:** Uses the capture, search-result waiting and troop-dispatch flow from checkpoint `39fbf4e`. Every capture writes a canonical PNG and every new army reads a fresh screen. Search levels are read from the active resource slider and verified after each adjustment.
- **Control center:** Start, pause and stop a run, choose the account scope and optional tasks, view logs and resource totals, and run screenshot, screen-detection and stockpile diagnostics. Alliance help/technology and troop training are marked as planned, not implemented.

## Requirements and setup

- Windows, Python 3.11 or later, and MuMu Player with Rise of Kingdoms installed and signed in.
- Set the game's language to Vietnamese. Use a landscape 16:9 emulator display; the project normalizes screenshots to 1280×720.
- Enable local ADB debugging in MuMu and restart the instance if necessary. MuMu's bundled ADB is discovered automatically; alternatively put `adb` on PATH or set `ROK_ADB_PATH` to its executable.

From the project directory:

```powershell
python -m pip install -e .
```

Start MuMu and open the game, then launch `run_gui.vbs` or `run_gui.bat`. Select a MuMu instance in the GUI before running a task. Instance names are read from MuMu; Refresh rediscovers instances. Each instance has its own saved account list and progress. The previous list and progress remain with instance #0. Open another GUI window to run a different instance; an instance can only have one active GUI worker at a time. Accounts are added manually or learned during rotation, not automatically read from Android on selection. `run.bat` offers a menu for the GUI or CLI farming.

Click **Help** at the far left below the title, or press **F1**, to open the in-app guide. It covers setup, run controls, account rotation, optional tasks, diagnostics, and less obvious actions such as the Gmail-only cycle and the idle Skip tests.

## Portable Windows release

Extract the release ZIP completely, then run `ROKauto.exe`. Python, OCR models,
and dependencies are included. Keep `ROKauto-worker.exe` and `_internal/` alongside
the launcher. MuMu and the game still need the setup described above.

Packaged builds save settings, account progress, and screenshots under
`%LOCALAPPDATA%\ROKauto`; updating the extracted application preserves that data.
Source runs retain their existing project-local data paths. Personal state and
screenshots are never copied into the release.

To build on Windows x64 with Python 3.11 or newer:

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -e . pyinstaller
.\.venv-build\Scripts\python.exe packaging\build_portable.py
```

The build initializes OCR to cache the required models (network may be needed
on the build machine), bundles the application, and checks offline OCR, templates,
Tk, and worker startup before producing `dist/ROKauto-windows-x64.zip` and its
SHA-256 file. Publish the ZIP and checksum as GitHub Release assets. Keep
generated executables, bundled dependencies, and build environments out of Git.
Validate actual emulator operation on a Windows machine without Python before
publishing a release. `ROKauto-worker.exe --help` exposes the existing CLI.

## Command line usage

```powershell
# Farm all available characters and signed-in Gmail accounts
python -m rok_automation --farm

# Limit the run to the current Gmail or current RoK character
python -m rok_automation --farm --no-switch-gmail
python -m rok_automation --farm --current-rok-only

# Cycle signed-in Gmail accounts once without farming, then return to the start
python -m rok_automation --cycle-gmail-only

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

Screenshot recovery reconnects to the selected emulator and tries a fresh capture once. Input is retried only when ADB confirms the device was unavailable before delivery. If a march cannot be confirmed, farming stops and leaves saved account progress available for resuming. Damaged Gmail progress is reported and preserved until you restore it or explicitly reset it in the GUI.

Static button positions are learned during each worker session and reused across characters. Each reused position is visually checked in a small local region; changed or missing buttons trigger normal detection, and different frame sizes use separate cache entries. Alliance screenshots run OCR only when a step needs text. Position caches reset when the worker restarts.

Enable **Farm Alliance Resource Node** under **Core Farming Options** to attempt one preset #1 march after daily rewards and before normal gathering. The world-map troop counter must show **0/5**. The node must have more than 50 million resources and must not be under construction. A missing commander, unreadable node, or unavailable node skips this step. Normal gathering continues from the world map using the character's previously identified resource preference. The alliance march uses one of the five available march slots.

The CLI equivalent is `python -m rok_automation --farm --alliance-resource`. Alliance tags, node coordinates, remaining resources, construction status, and check times are saved in `state/alliance_nodes.json` (or the selected instance's state directory). Matching alliances reuse this information; eligible nodes are checked again before dispatch, while unavailable results are reused for up to two minutes.

A **0/4** counter beside the alliance resource-center header means no node exists. This result is saved by alliance tag and skips alliance gathering for all remaining matching accounts in that worker run, without coordinate searches or scroll retries. A new worker run checks again so newly built nodes can be detected.

The `state/` directory stores emulator selection, Gmail rotation and account records, feature flags, and recently farmed nodes. `artifacts/` holds screenshots and run output. Both directories are ignored by Git. The GUI's **Reset Gmail Run Progress** control resets rotation progress; use it when starting a fresh rotation. Treat account records and screenshots as private.

Screenshot, screen-detection and stockpile diagnostics export PNG files. Each connection uses its own folder under `artifacts/<emulator>-<index>/<session>/`, so concurrent instances and diagnostics cannot overwrite each other's frames. Routine farming updates `emulator_native.png` there on every capture; a farming failure also exports `farming_error.png` in the same folder. An explicit `--screenshot PATH` still writes to the requested path.

Logs report elapsed seconds per successful army dispatch and per account, along with capture, detection, OCR, ADB input, wait and retry statistics. Completed account measurements are also stored under `performance` in `state/session_summary.json`. Component timings exclude nested measured work (for example, OCR is not counted twice inside detection); dispatch and account durations are overall wall-clock measurements, including pauses. Compare dispatch times and retry counts across similar runs when tuning performance.

## Development

The `rok_automation/automation` package contains the farming controller, account switching, daily rewards and city tasks; `game` contains ADB, capture and emulator discovery; `vision` handles screen and text recognition; `logic` contains resource selection and yield calculations; and `ui` contains the desktop control center. Templates are in `templates/`.

```powershell
python -m unittest discover -s tests
```

Most tests use mocks or synthetic images and do not require a running emulator. Actual game operation still depends on MuMu, ADB, the game UI and matching templates.

