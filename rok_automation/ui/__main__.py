"""Direct CLI runner for ROKauto User Interface."""

import sys
from rok_automation.ui.app import launch_gui

if __name__ == "__main__":
    sys.exit(launch_gui())
