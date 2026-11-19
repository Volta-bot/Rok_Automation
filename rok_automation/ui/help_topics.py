"""User-facing guide for the desktop controls and their secondary actions."""

HELP_TOPICS = (
    (
        "Setup",
        (
            (
                "Connect to MuMu Player",
                "Open Rise of Kingdoms in a running MuMu instance with local ADB debugging enabled. "
                "Set the game language to Vietnamese and use a landscape 16:9 display.",
            ),
            (
                "Emulator, instance and Refresh",
                "Select the emulator and the named instance you want to control. Refresh rediscovers "
                "instances and checks the selected connection. If the instance is unavailable, "
                "ROKauto retries the connection automatically.",
            ),
            (
                "Multiple instances",
                "Each instance has its own saved account list and progress. Open a separate ROKauto "
                "window for each instance you want to run simultaneously. Each instance supports one "
                "active worker.",
            ),
            (
                "Background operation",
                "ROKauto captures the emulator in the background, using ADB as a fallback. It sends "
                "taps through ADB, so you can cover the emulator window and keep using your mouse.",
            ),
        ),
    ),
    (
        "Run Controls",
        (
            (
                "Start Automation (F5)",
                "Starts the enabled routines. With Auto Farm Resources enabled, it farms the current "
                "Gmail's starred RoK characters. Enable Switch Gmail Accounts to rotate through the "
                "other signed-in Gmails too.",
            ),
            (
                "Gmail-only cycle",
                "Uncheck Auto Farm Resources, leave Switch Gmail Accounts checked and press Start. "
                "ROKauto cycles through the signed-in Gmails once and returns to the starting Gmail. "
                "This mode skips farming, character switching, rewards and city tasks.",
            ),
            (
                "Farm This Gmail Only / Farm This RoK Account Only",
                "The Gmail button farms within the current Gmail, including its starred RoK characters. "
                "The RoK button limits farming to the current character. Both buttons override "
                "Switch Gmail Accounts. With farming off, either button runs enabled rewards and "
                "city tasks on the current character.",
            ),
            (
                "Pause / Play and Stop (Esc)",
                "Pause holds the worker at a safe checkpoint and changes the button to Play. "
                "Stop ends the worker and prints estimated farming totals in the Output Console. "
                "Saved rotation progress is available when you restart after a stop or failure.",
            ),
            (
                "Help (F1)",
                "Opens this guide in a separate window while the main controls remain available. "
                "Press Esc while the guide has focus to close it.",
            ),
        ),
    ),
    (
        "Farming",
        (
            (
                "Auto Farm Resources",
                "Fills available march slots, up to five. Searches from level 8 down to level 5 "
                "and falls back to other resource types. It avoids nodes with green incoming "
                "routes and remembers recently dispatched coordinates for about 30 minutes.",
            ),
            (
                "How ROKauto picks a resource",
                "A Vietnamese or English resource keyword in the governor name sets the preference. "
                "Otherwise ROKauto chooses the lowest readable top-bar stockpile. If none can be "
                "read, it chooses randomly with extra weight for Gold.",
            ),
            (
                "Farm Alliance Resource Node",
                "Only attempts to farm the alliance node when no troops are out (0/5 on the "
                "world-map march counter). The character must start in World View and the node "
                "must be usable with more than 50 million resources remaining. This sends one "
                "march with preset #1 before regular gathering and uses one march slot. "
                "You can toggle this option during a run for the next character.",
            ),
            (
                "Farmed total",
                "The badge accumulates dispatch counts and estimated yields across runs in this window. "
                "Yields are calculated from the resource and node level when a march is dispatched.",
            ),
        ),
    ),
    (
        "Accounts",
        (
            (
                "Signed-in accounts",
                "Expand the panel to see the selected instance's saved Gmail list. Enter an address "
                "and click Add or press Enter. Added accounts show as unknown until verified in-game. "
                "Rotation can also learn accounts it encounters. Remove deletes the saved entry "
                "from this list while leaving the Android sign-in intact.",
            ),
            (
                "Position, arrows and ✓/✕",
                "Each row shows the character's saved position, traversal direction (↓ or ↑), "
                "and how many characters have been farmed. While idle, click ✕ to mark a Gmail "
                "complete for the rotation or click ✓ to make it pending again.",
            ),
            (
                "Reset Gmail Run Progress",
                "While idle, reset the saved Gmail and character progress to start a fresh rotation. "
                "The account list stays saved. A successfully completed full Gmail farming run "
                "resets rotation progress automatically.",
            ),
            (
                "Skip RoK account / Skip Gmail account",
                "During farming, requests a switch at the next safe checkpoint if that kind of "
                "switching is enabled. A paused run resumes to process the skip. While idle, "
                "these buttons run a live skip test that can switch accounts and update saved "
                "rotation progress, including Gmail completion marks.",
            ),
        ),
    ),
    (
        "Optional Tasks",
        (
            (
                "Collect Daily Rewards",
                "Claims available VIP points, the free VIP chest and the Campaign golden chest "
                "before farming. Changes during a run apply from the next character.",
            ),
            (
                "Collect City Resources",
                "After farming, zooms out slightly and collects ready Food, Wood, Stone and Gold "
                "production icons in the city. Changes during a run apply from the next character.",
            ),
            (
                "Auto Forge Material",
                "After farming, claims finished forge output if available and starts the selected "
                "Leather, Iron, Wood or Bone production. The material choice is remembered even "
                "when the checkbox is off. Changes during a run apply from the next character.",
            ),
            (
                "Run optional tasks on the current character",
                "Turn off Auto Farm Resources and Switch Gmail Accounts, select the tasks you want "
                "and press Start. For a single task, use the Daily Rewards, City Resources or Forge "
                "Material test button while idle. These buttons perform real in-game actions on "
                "the current character even when the corresponding checkbox is off.",
            ),
            (
                "Planned options",
                "Alliance Help & Tech and Auto Train Troops are placeholders. Their test buttons "
                "write a simulated action to the console.",
            ),
        ),
    ),
    (
        "Diagnostics",
        (
            (
                "Screenshot",
                "Captures the selected emulator screen and saves a PNG. The Output Console shows "
                "where the capture was saved.",
            ),
            (
                "Detect Screen",
                "Captures the screen and reports what ROKauto recognizes, such as the city, world map "
                "or a resource search screen.",
            ),
            (
                "Check Stockpiles",
                "Reads the visible Food, Wood, Stone and Gold amounts and reports the stockpile-based "
                "farming choice. Use it with the game's resource bar visible.",
            ),
            (
                "Output Console",
                "Shows progress, diagnostic results, warnings and errors. Copy Log copies the entire "
                "visible log to the clipboard. Clear empties the console display. Turn Auto-scroll "
                "off to read older lines while new output arrives.",
            ),
        ),
    ),
)
