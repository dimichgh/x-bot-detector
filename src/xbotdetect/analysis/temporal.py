"""Creation-date clustering: statistical creation bursts and "follower map" bands.

Both detectors are time-agnostic. They compare a group of accounts against its *own*
local background, so a farm of accounts registered in 2016 is caught as readily as one
registered last month, and smooth platform-wide sign-up waves are not mistaken for farms.

* **Creation bursts**: a short window (default 7 days) holding far more accounts than the
  surrounding months predict, tested with a Poisson tail probability and Bonferroni-corrected
  across every window in the sample. Farms register accounts in batches over days; organic
  audiences and platform growth waves are spread over months.
* **Follower-map bands**: a follower map plots each follower's creation date against the
  order in which it followed (X returns followers newest-first). Purchased or farmed followers
  show up as long runs of consecutive followers all created in the same window, tested with
  a binomial tail probability against the sample's overall creation-date distribution.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from ..util import iso


def _logsumexp(a: float, b: float) -> float:
    if a == -math.inf:
        return b
    if b == -math.inf:
        return a
    m = max(a, b)
    return m + math.log(math.exp(a - m) + math.exp(b - m))


def poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    if k < lam:  # tail is near 1; compute via the complement for accuracy
        cdf = -math.inf
        for i in range(k):
            cdf = _logsumexp(cdf, -lam + i * math.log(lam) - math.lgamma(i + 1))
        return max(0.0, 1.0 - math.exp(cdf))
    total = -math.inf
    i = k
    while True:
        term = -lam + i * math.log(lam) - math.lgamma(i + 1)
        total = _logsumexp(total, term)
        if term < total - 40 or i > k + 100_000:
            break
        i += 1
    return min(1.0, math.exp(total))


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    if k <= 0:
        return 1.0
    if k > n or p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    lp, lq = math.log(p), math.log1p(-p)
    total = -math.inf
    for i in range(k, n + 1):
        term = math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq
        total = _logsumexp(total, term)
        if term < total - 40 and i > n * p:
            break
    return min(1.0, math.exp(total))


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


def recent_share(dates: list[datetime | None], since: datetime | None) -> float:
    known = [d for d in dates if d is not None]
    if not known or since is None:
        return 0.0
    return sum(d >= since for d in known) / len(known)


def monthly_histogram(dates: list[datetime | None]) -> dict[str, int]:
    c = Counter(d.strftime("%Y-%m") for d in dates if d is not None)
    return dict(sorted(c.items()))


@dataclass
class Burst:
    """A short window with far more account creations than its local background predicts."""

    start: datetime
    end: datetime
    count: int
    expected: float
    p_value: float
    excess_share: float  # (count - expected) / sample size
    fresh_signups: bool  # ends near the analysis date (could be X onboarding)

    @property
    def ratio(self) -> float:
        return self.count / max(self.expected, 1e-9)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(
            start=iso(self.start),
            end=iso(self.end),
            expected=round(self.expected, 2),
            p_value=float(f"{self.p_value:.3g}"),
            excess_share=round(self.excess_share, 4),
            ratio=round(self.ratio, 1),
        )
        return d


def creation_bursts(
    dates: list[datetime | None],
    as_of: datetime,
    window_days: float = 7,
    background_days: float = 180,
    alpha: float = 1e-3,
    min_count: int = 5,
    fresh_days: float = 60,
) -> list[Burst]:
    """Non-overlapping creation bursts, most significant first."""
    ts = sorted(d for d in dates if d is not None)
    n = len(ts)
    if n < 30:
        return []
    secs = [t.timestamp() for t in ts]
    w = window_days * 86400
    half_bg = background_days * 86400 / 2
    horizon = as_of.timestamp()
    n_windows = max(1.0, (secs[-1] - secs[0]) / w)
    threshold = alpha / n_windows
    need = max(min_count, math.ceil(0.01 * n))
    found: list[tuple[float, int, int, float]] = []
    j = 0
    for i in range(n):
        j = max(j, i)
        while j < n and secs[j] <= secs[i] + w:
            j += 1
        c = j - i
        if c < need:
            continue
        lo = bisect.bisect_left(secs, secs[i] - half_bg)
        hi = bisect.bisect_right(secs, secs[i] + w + half_bg)
        background = (hi - lo) - c
        span = min(secs[i] + w + half_bg, horizon) - max(secs[i] - half_bg, secs[0])
        bg_span = max(span - w, w)
        lam = max(background / bg_span * w, 0.5)
        p = poisson_sf(c, lam)
        if p < threshold:
            found.append((p, i, j - 1, lam))
    found.sort()
    taken: list[tuple[int, int]] = []
    bursts: list[Burst] = []
    for p, a, b, lam in found:
        if any(not (b < x or a > y) for x, y in taken):
            continue
        taken.append((a, b))
        c = b - a + 1
        bursts.append(
            Burst(
                start=ts[a],
                end=ts[b],
                count=c,
                expected=lam,
                p_value=p,
                excess_share=(c - lam) / n,
                fresh_signups=(as_of - ts[b]).days <= fresh_days,
            )
        )
    bursts.sort(key=lambda bu: bu.excess_share, reverse=True)
    return bursts


def burst_excess(bursts: list[Burst], include_fresh: bool = False) -> float:
    return sum(b.excess_share for b in bursts if include_fresh or not b.fresh_signups)


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
    p_value: float = 1.0  # binomial tail vs. the sample's base rate for that creation window

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(
            created_start=iso(self.created_start),
            created_end=iso(self.created_end),
            share_of_sample=round(self.share_of_sample, 4),
            density=round(self.density, 3),
            lift=round(self.lift, 2),
            p_value=float(f"{self.p_value:.3g}"),
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
    alpha: float = 1e-3,
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
    starts = range(0, n - w + 1, step)
    threshold = alpha / max(len(starts), 1)

    def global_share(start: datetime, end: datetime) -> float:
        lo = bisect.bisect_left(all_ts, start)
        hi = bisect.bisect_right(all_ts, end)
        return max((hi - lo) / n, 1 / n)

    flagged: list[tuple[int, int]] = []
    for s in starts:
        chunk = [d for _, d in pts[s : s + w]]
        cnt, a, _ = _densest(chunk, window)
        frac = cnt / w
        if frac < min_fraction:
            continue
        base = global_share(a, a + window)
        if frac / base >= min_lift and binom_sf(cnt, w, base) < threshold:
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
                lift=(cnt / len(run)) / gs,
                created_start=start,
                created_end=end,
                fresh_signups=(as_of - end).days <= fresh_days,
                p_value=binom_sf(cnt, len(run), gs),
            )
        )
    bands.sort(key=lambda bd: bd.size, reverse=True)
    return bands
