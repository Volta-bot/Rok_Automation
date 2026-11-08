# Rise of Kingdoms Automation

An incremental Windows desktop automation project for Rise of Kingdoms running in LDPlayer 9.

## Current capability

The project can detect a visible LDPlayer window, capture the game-render child area to PNG, recognize the account and resource, classify supplied UI states, and read the level shown in the search panel. On the world map it reads the current `x/5` march counter; if the counter is not visible, it treats that as `0/5`. On a found resource, it checks for an incoming green dotted route and reads the resource's X/Y coordinates; during one account's run, it skips occupied routes and coordinates it has already handled. When it encounters an occupied location at a level, it remembers to try lower levels for the rest of that account's run. The optional `--farm` mode uses a guarded state machine to search resources and dispatch only into army slots that are free. After each account, it opens the profile → settings → character screens and advances from the green-check card by position, top-left to bottom-right or in reverse depending on the starting position. It scrolls the character list one row at a time when needed. Gmail switching is not part of this flow. It stops if a required screen or control cannot be recognized.

## Observed farming flow

1. Start in the city; click the map button at the lower left to move to the world map.
2. Read the city label below the player's city, ignore text inside square brackets (the guild name), and map `Vàng`/`Gold`, `Đá`/`Stone`, `Ngô`/`Food`, or `Gỗ`/`Wood` to its resource. A number may directly follow the keyword, as in `Gỗ8`.
3. Click the lower-left magnifying-glass button and choose a resource type. Current resource levels descend from 8 to 5; the account's preferred resource is tried before randomly ordered alternatives.
4. On a failed search, the camera stays put and the game displays a message beginning `Không tìm thấy` and ending `hãy thử lại sau`.
5. On success, the camera moves to the resource. The observed action sequence is `THU THẬP` → `Quân mới` → `HÀNH QUÂN`.

The farming controller tries the preferred resource at levels 8 through 5, then randomly orders the other resource types and tries levels 8 through 5 for each. A failed search lowers the level; a result with a detected green incoming route is skipped before trying the next target; an apparently clear result proceeds through gather, new troops, and march. Blue and white incoming routes are not detected yet. A run stops if no supported resource location is found or a screen transition cannot be verified.

## Run

From the repository root, use Python 3.11 or newer. Install the project and its screen-capture and Vietnamese OCR dependencies:

```powershell
python -m pip install -e .
```

Check emulator detection:

```powershell
python -m rok_automation
```

The command prints the detected window title and handle, or reports that no matching window was found. Window matching currently looks for `LDPlayer` or `雷电` in the title.

Capture LDPlayer's game-render area to `artifacts/ldplayer.png`:

```powershell
python -m rok_automation --screenshot
```

You can supply another output path with `--screenshot path\to\capture.png`. This uses ordinary desktop capture, so the game area must be visible, unminimized, and unobscured while capturing.

Capture and classify the visible game screen:

```powershell
python -m pip install -e .
python -m rok_automation --detect-screen --search-level 8
```

The detector reports `unknown` if no supplied template matches confidently. In the resource-search panel it reads the visible `Cấp: N` label and reports that level; `--search-level` remains available for a controller to attach its known active level to a found-resource panel. On the world map it prints the account name without its bracketed guild tag and the preferred resource mapped from Vietnamese or English resource keywords.

On a found-resource screen, `--detect-screen` also reports `green_route=detected` or `green_route=not_detected` with a confidence score. The current route check covers the green march route only; it does not yet recognize blue or white routes. On the world map, it reports the `x/5` march count used to limit new dispatches.

To farm the current RoK character and then move through the other linked characters under the same signed-in Gmail account, run:

```powershell
python -m rok_automation --farm
```

Keep LDPlayer visible and unobscured. The command may click the game, dispatch up to five armies per character, and move through starred RoK characters in positional order until it reaches the end of the list. It does not switch Gmail accounts. It stops if it cannot verify a screen transition or locate the active account card.

To inspect the detected LDPlayer window and its child-window bounds:

```powershell
python -m rok_automation --diagnose-window
```

## Planned development

1. Emulator window detection (implemented)
2. Screenshot capture (implemented)
3. Basic mouse interaction (implemented for matched controls)
4. GUI skeleton
5. No-result detection by comparing the map before and after search (implemented)
6. Resource-search workflow and screen transitions (implemented for farming)
7. Teleport and gathering workflow
8. Daily rewards, recovery, and logging refinement

Visual recognition will use screenshots from the actual game. No game coordinates or image templates are assumed in advance.
