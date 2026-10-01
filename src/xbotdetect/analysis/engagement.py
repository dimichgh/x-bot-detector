"""Engagement-set analysis: who reposted / replied to one post, and how fast."""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..dataset import Dataset
from ..models import Engagement
from ..util import days_between, fmt_date, ramp
from .behavior import normalize_text
from .config import AnalysisConfig
from .scoring import Signal, combine, level
from .temporal import Peak, creation_peak, monthly_histogram, recent_share


@dataclass
class EngagementResult:
    tweet_id: str
    author_id: str | None
    engagers: int
    reposters: int
    repliers: int
    recent_share: float
    peak: Peak
    botlike_share: float
    median_age_days: float | None
    median_reply_delay_min: float | None
    replies_within_5min: float | None
    duplicate_reply_share: float | None
    monthly: dict[str, int]
    top_suspicious: list[str]
    signals: list[Signal]
    score: float
    level: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tweet_id": self.tweet_id,
            "author_id": self.author_id,
            "engagers": self.engagers,
            "reposters": self.reposters,
            "repliers": self.repliers,
            "recent_share": round(self.recent_share, 4),
            "peak": self.peak.to_dict(),
            "botlike_share": round(self.botlike_share, 4),
            "median_age_days": self.median_age_days,
            "median_reply_delay_min": self.median_reply_delay_min,
            "replies_within_5min": self.replies_within_5min,
            "duplicate_reply_share": self.duplicate_reply_share,
            "monthly": self.monthly,
            "top_suspicious": self.top_suspicious,
            "signals": [s.to_dict() for s in self.signals],
            "score": round(self.score, 4),
            "level": self.level,
        }


def analyze_engagement(
    ds: Dataset, eng: Engagement, meta_scores: dict[str, float], cfg: AnalysisConfig
) -> EngagementResult:
    repliers = [p.author_id for p in eng.replies if p.author_id and p.author_id != eng.author_id]
    ids = list(dict.fromkeys([*eng.reposter_ids, *repliers]))
    accs = [ds.accounts[i] for i in ids if i in ds.accounts]
    dates = [a.created_at for a in accs if a.created_at]
    peak = creation_peak(dates, cfg.creation_window_days)
    rec = recent_share(dates, cfg.recent_since)
    scored = [(i, meta_scores[i]) for i in ids if i in meta_scores]
    botlike = sum(s >= cfg.botlike_threshold for _, s in scored) / len(scored) if scored else 0.0
    ages = [days_between(d, ds.as_of) for d in dates]

    delays = []
    if eng.created_at:
        delays = [
            (p.created_at - eng.created_at).total_seconds() / 60
            for p in eng.replies
            if p.created_at and p.author_id != eng.author_id
        ]
        delays = [d for d in delays if d >= 0]
    texts = [normalize_text(p.text) for p in eng.replies if p.author_id != eng.author_id]
    texts = [t for t in texts if len(t) >= 8]
    dup = None
    if len(texts) >= 5:
        c = Counter(texts)
        dup = sum(v for v in c.values() if v > 1) / len(texts)

    sig: list[Signal] = []
    if len(dates) >= 20:
        s = ramp(peak.share, 0.2, 0.45)
        if s:
            sig.append(
                Signal(
                    "engager_creation_spike",
                    "Engagers created together",
                    s,
                    0.4,
                    f"{peak.share:.0%} of {len(dates)} engaging accounts were created within "
                    f"{cfg.creation_window_days} days ({fmt_date(peak.start)} - {fmt_date(peak.end)}); >40% is highly anomalous",
                    "engagement",
                )
            )
        s = ramp(rec, 0.5, 0.85)
        if s:
            sig.append(
                Signal(
                    "engagers_new",
                    "Engagers mostly new",
                    s,
                    0.25,
                    f"{rec:.0%} of engagers created after {fmt_date(cfg.recent_since)}",
                    "engagement",
                )
            )
        s = ramp(botlike, 0.15, 0.45)
        if s:
            sig.append(
                Signal(
                    "engagers_botlike",
                    "Bot-like engagers",
                    s,
                    0.3,
                    f"{botlike:.0%} of engagers score as bot-like",
                    "engagement",
                )
            )
    within5 = None
    if len(delays) >= 10:
        within5 = sum(d <= 5 for d in delays) / len(delays)
        s = ramp(within5, 0.3, 0.7)
        if s:
            sig.append(
                Signal(
                    "reply_velocity",
                    "Instant replies",
                    s,
                    0.2,
                    f"{within5:.0%} of replies arrived within 5 minutes of the post",
                    "engagement",
                )
            )
    if dup is not None:
        s = ramp(dup, 0.1, 0.4)
        if s:
            sig.append(
                Signal(
                    "duplicate_replies",
                    "Copy-paste replies",
                    s,
                    0.25,
                    f"{dup:.0%} of replies are duplicates",
                    "engagement",
                )
            )

    score = combine(sig)
    top = [i for i, s in sorted(scored, key=lambda x: x[1], reverse=True)[:20] if s >= cfg.botlike_threshold]
    return EngagementResult(
        tweet_id=eng.tweet_id,
        author_id=eng.author_id,
        engagers=len(ids),
        reposters=len(eng.reposter_ids),
        repliers=len(set(repliers)),
        recent_share=rec,
        peak=peak,
        botlike_share=botlike,
        median_age_days=round(statistics.median(ages), 1) if ages else None,
        median_reply_delay_min=round(statistics.median(delays), 1) if delays else None,
        replies_within_5min=None if within5 is None else round(within5, 3),
        duplicate_reply_share=None if dup is None else round(dup, 3),
        monthly=monthly_histogram(dates),
        top_suspicious=top,
        signals=sig,
        score=score,
        level=level(score),
    )
