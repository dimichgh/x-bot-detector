"""Signals and how they combine.

Each signal has a strength ``score`` in [0, 1] and a ``weight`` (its maximum contribution).
They combine with a noisy-OR, ``1 - prod(1 - score * weight)``, so several weak signals
stack into a strong one while no single weak signal can dominate.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

LEVELS = ((0.7, "very high"), (0.5, "high"), (0.3, "moderate"), (0.0, "low"))


@dataclass
class Signal:
    key: str
    label: str
    score: float
    weight: float
    detail: str
    category: str = "metadata"  # metadata | origin | behavior | network | cluster | engagement

    @property
    def contribution(self) -> float:
        return self.score * self.weight

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["contribution"] = round(self.contribution, 4)
        d["score"] = round(self.score, 4)
        return d


def combine(signals: list[Signal]) -> float:
    return 1.0 - math.prod(1.0 - min(max(s.contribution, 0.0), 0.999) for s in signals)


def level(score: float) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "low"


def active(signals: list[Signal], min_contribution: float = 0.01) -> list[Signal]:
    """Signals that actually fired, strongest first."""
    return sorted(
        (s for s in signals if s.contribution >= min_contribution), key=lambda s: s.contribution, reverse=True
    )
