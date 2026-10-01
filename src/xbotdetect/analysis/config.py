"""Tunable analysis parameters.

Nothing here is tied to particular years: "young" is relative to the analysis date
(``young_days``), and creation-date clustering looks for statistically unusual bursts
wherever they fall in time. ``campaign_since`` optionally pins a known campaign window.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from ..util import iso


@dataclass
class AnalysisConfig:
    # Accounts younger than this count as "young" (relative to the analysis date).
    young_days: int = 730
    # Optional fixed start of a known campaign window (e.g. 2024-10-01). When set it replaces the
    # relative cut-off and accounts created inside the window get a stronger "new account" signal.
    campaign_since: datetime | None = None
    # Resolved cut-off date (campaign_since, or as_of - young_days); filled by resolve().
    recent_since: datetime | None = None
    # Width of the creation-date clustering window (days) for peaks, bands and cluster cohorts.
    creation_window_days: int = 30
    # Creation-burst detection: window width, local background span and significance level
    # (Bonferroni-corrected across all windows in the sample).
    burst_window_days: int = 7
    burst_background_days: int = 180
    burst_alpha: float = 1e-3
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

    def resolve(self, as_of: datetime) -> AnalysisConfig:
        """Return a copy with ``recent_since`` fixed for this analysis date."""
        since = self.campaign_since or (as_of - timedelta(days=self.young_days))
        return replace(self, recent_since=since)

    @property
    def young_label(self) -> str:
        if self.campaign_since:
            return f"created since {self.campaign_since:%Y-%m-%d}"
        return (
            f"younger than {self.young_days / 365:.0f}y"
            if self.young_days % 365 == 0
            else (f"younger than {self.young_days} days")
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["recent_since"] = iso(self.recent_since)
        d["campaign_since"] = iso(self.campaign_since)
        return d
