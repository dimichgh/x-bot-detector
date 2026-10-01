"""Creation-date clustering and "follower map" band detection.

A follower map plots each follower's account-creation date against the order in which it
followed (X returns followers newest-first). Organic audiences scatter; purchased or farmed
followers show up as dense horizontal *bands*: long runs of consecutive followers whose
accounts were all created within the same few weeks.
"""

from __future__ import annotations

import bisect
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from ..util import iso


@dataclass
class Peak:
    share: float
    count: int
    total: int
    start: datetime | None
    end: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "start": iso(self.start), "end": iso(self.end), "share": round(self.share, 4)}


def creation_peak(dates: list[datetime | None], window_days: float) -> Peak:
    """Largest share of ``dates`` falling inside any window of ``window_days``."""
    ts = sorted(d for d in dates if d is not None)
    n = len(ts)
    if n == 0:
        return Peak(0.0, 0, 0, None, None)
    w = timedelta(days=window_days)
    best, bi, bj = 0, 0, 0
    i = 0
    for j in range(n):
        while ts[j] - ts[i] > w:
            i += 1
        if j - i + 1 > best:
            best, bi, bj = j - i + 1, i, j
    return Peak(best / n, best, n, ts[bi], ts[bj])


def recent_share(dates: list[datetime | None], since: datetime) -> float:
    known = [d for d in dates if d is not None]
    return sum(d >= since for d in known) / len(known) if known else 0.0


def monthly_histogram(dates: list[datetime | None]) -> dict[str, int]:
    c = Counter(d.strftime("%Y-%m") for d in dates if d is not None)
    return dict(sorted(c.items()))


@dataclass
class Band:
    rank_start: int  # follow-order index, 0 = oldest follower in the sample
    rank_end: int
    size: int  # members of the run created inside the dense window
    share_of_sample: float
    density: float  # size / run length
    lift: float  # density vs. how common that creation window is in the whole sample
    created_start: datetime
    created_end: datetime
    fresh_signups: bool  # window ends near the analysis date (could be X onboarding)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(
            created_start=iso(self.created_start),
            created_end=iso(self.created_end),
            share_of_sample=round(self.share_of_sample, 4),
            density=round(self.density, 3),
            lift=round(self.lift, 2),
        )
        return d


def _densest(ts: list[datetime], window: timedelta) -> tuple[int, datetime, datetime]:
    s = sorted(ts)
    best, bi, bj, i = 0, 0, 0, 0
    for j in range(len(s)):
        while s[j] - s[i] > window:
            i += 1
        if j - i + 1 > best:
            best, bi, bj = j - i + 1, i, j
    return best, s[bi], s[bj]


def follower_map_bands(
    dates_oldest_first: list[datetime | None],
    as_of: datetime,
    window_days: float = 30,
    min_fraction: float = 0.6,
    min_lift: float = 2.0,
    fresh_days: float = 60,
    run_length: int | None = None,
) -> list[Band]:
    """Find runs of consecutive followers whose creation dates are tightly clustered."""
    pts = [(i, d) for i, d in enumerate(dates_oldest_first) if d is not None]
    n = len(pts)
    if n < 40:
        return []
    w = run_length or max(20, min(100, n // 20))
    step = max(1, w // 4)
    window = timedelta(days=window_days)
    all_ts = sorted(d for _, d in pts)

    def global_share(start: datetime, end: datetime) -> float:
        lo = bisect.bisect_left(all_ts, start)
        hi = bisect.bisect_right(all_ts, end)
        return (hi - lo) / n

    flagged: list[tuple[int, int]] = []
    for s in range(0, n - w + 1, step):
        chunk = [d for _, d in pts[s : s + w]]
        cnt, a, b = _densest(chunk, window)
        frac = cnt / w
        if frac < min_fraction:
            continue
        lift = frac / max(global_share(a, a + window), 1e-9)
        if lift >= min_lift:
            flagged.append((s, s + w - 1))

    merged: list[list[int]] = []
    for a, b in flagged:
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])

    bands: list[Band] = []
    for a, b in merged:
        run = pts[a : b + 1]
        cnt, start, end = _densest([d for _, d in run], window)
        gs = global_share(start, start + window)
        bands.append(
            Band(
                rank_start=run[0][0],
                rank_end=run[-1][0],
                size=cnt,
                share_of_sample=cnt / n,
                density=cnt / len(run),
                lift=(cnt / len(run)) / max(gs, 1e-9),
                created_start=start,
                created_end=end,
                fresh_signups=(as_of - end).days <= fresh_days,
            )
        )
    bands.sort(key=lambda bd: bd.size, reverse=True)
    return bands
