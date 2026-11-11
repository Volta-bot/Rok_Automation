"""Transient data shared throughout one farming run."""

from dataclasses import dataclass, field

from rok_automation.automation.states import FarmState
from rok_automation.logic.resource_selector import (
    Resource,
    SearchTarget,
    calculate_farmed_resources,
)


@dataclass
class FarmContext:
    account_name: str | None = None
    preferred_resource: Resource | None = None
    current_target: SearchTarget | None = None
    current_state: FarmState = FarmState.START
    starting_armies_out: int | None = None
    armies_dispatched: int = 0
    last_error: str | None = None
    dispatched_targets: list[SearchTarget] = field(default_factory=list)

    @property
    def total_farmed_resources(self) -> dict[Resource, int]:
        return calculate_farmed_resources(self.dispatched_targets)

    @property
    def grand_total_farmed(self) -> int:
        return sum(self.total_farmed_resources.values())
