"""Finite states for the one-pass farming controller."""

from enum import Enum


class FarmState(str, Enum):
    START = "start"
    DETECT_ACCOUNT = "detect_account"
    OPEN_WORLD_MAP = "open_world_map"
    OPEN_SEARCH = "open_search"
    SELECT_TARGET = "select_target"
    SEARCH_RESOURCE = "search_resource"
    PROCESS_RESULT = "process_result"
    SEND_TROOPS = "send_troops"
    VERIFY_GATHERING = "verify_gathering"
    SWITCH_GMAIL = "switch_gmail"
    COMPLETE = "complete"
    STOPPED = "stopped"
