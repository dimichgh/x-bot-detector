"""Tunable analysis parameters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..util import iso


def _default_recent_since() -> datetime:
    return datetime(2024, 10, 1, tzinfo=timezone.utc)


@dataclass
class AnalysisConfig:
    # Accounts created on/after this date count as part of the "new wave" (late 2024 - 2026).
    recent_since: datetime = field(default_factory=_default_recent_since)
    # Width of the creation-date window used for clustering (days).
    creation_window_days: int = 30
    # A metadata score at/above this marks an account as "bot-like" in neighbourhood shares.
    botlike_threshold: float = 0.5
    # Accounts created within this many days of the analysis date are treated as "fresh sign-ups":
    # X onboarding pushes them to follow accounts, so they are discounted in follower spikes.
    fresh_signup_days: int = 60
    # Follower-map band detection.
    band_min_fraction: float = 0.6
    band_min_lift: float = 2.0
    min_cluster_size: int = 3
    # Cluster score at/above which members get the "in suspicious cluster" signal.
    cluster_flag_threshold: float = 0.5
    louvain_resolution: float = 1.0
    seed: int = 42

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["recent_since"] = iso(self.recent_since)
        return d
