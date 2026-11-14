"""Lightweight, per-account timings for the farming worker."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from time import perf_counter


@dataclass
class PerformanceStats:
    totals: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    # Each active operation tracks time spent in measured children so that
    # detection does not also charge OCR time to itself.
    _children: list[float] = field(default_factory=list)

    def record(self, name: str, seconds: float = 0.0) -> None:
        self.totals[name] = self.totals.get(name, 0.0) + seconds
        self.counts[name] = self.counts.get(name, 0) + 1

    def snapshot(self) -> dict:
        return {
            name: {
                "calls": count,
                "total_ms": round(self.totals[name] * 1000, 2),
                "mean_ms": round(self.totals[name] * 1000 / count, 2),
            }
            for name, count in self.counts.items()
        }


_ACTIVE: ContextVar[PerformanceStats | None] = ContextVar("farm_performance", default=None)


@contextmanager
def collect_performance(stats: PerformanceStats):
    token = _ACTIVE.set(stats)
    try:
        yield stats
    finally:
        _ACTIVE.reset(token)


def record_event(name: str, seconds: float = 0.0) -> None:
    stats = _ACTIVE.get()
    if stats is not None:
        stats.record(name, seconds)


def timed(name: str):
    """Measure exclusive operation time only while a collector is active."""
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            stats = _ACTIVE.get()
            if stats is None:
                return function(*args, **kwargs)
            stats._children.append(0.0)
            started = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = perf_counter() - started
                children = stats._children.pop()
                if stats._children:
                    stats._children[-1] += elapsed
                stats.record(name, max(0.0, elapsed - children))
        return measured
    return decorate
