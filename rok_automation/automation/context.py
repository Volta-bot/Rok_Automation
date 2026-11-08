"""Transient data shared throughout one farming run."""

from dataclasses import dataclass

from rok_automation.automation.states import FarmState
from rok_automation.logic.resource_selector import Resource, SearchTarget


@dataclass
class FarmContext:
    account_name: str | None = None
    preferred_resource: Resource | None = None
    current_target: SearchTarget | None = None
    current_state: FarmState = FarmState.START
    starting_armies_out: int | None = None
    armies_dispatched: int = 0
    last_error: str | None = None
