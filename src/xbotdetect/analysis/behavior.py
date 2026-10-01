"""Timeline behaviour: amplification share, round-the-clock posting, metronomic cadence, templated text."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import datetime
from typing import Any

from ..models import Account, Post
from ..util import days_between, ramp
from .scoring import Signal

_URL = re.compile(r"https?://\S+")
_MENTION = re.compile(r"@\w+")
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)

# First-party clients; anything else (schedulers, scripts, bots) counts as third-party.
OFFICIAL_CLIENTS = {
    "twitter web app",
    "twitter for iphone",
    "twitter for android",
    "twitter for ipad",
    "twitter for mac",
    "x web app",
    "x for iphone",
    "x for android",
    "tweetdeck",
    "tweetdeck web app",
    "twitter media studio",
}


def normalize_text(text: str) -> str:
    t = _URL.sub(" ", text or "")
    t = _MENTION.sub(" ", t)
    return _NON_WORD.sub(" ", t).strip().lower()


def max_quiet_gap_hours(hours: list[int]) -> int:
    """Longest run of consecutive hours-of-day (circular) with no activity."""
    seen = set(hours)
    if not seen:
        return 24
    best = run = 0
    for h in list(range(24)) * 2:
        run = 0 if h in seen else run + 1
        best = max(best, run)
    return min(best, 24)


def timeline_metrics(owner: Account, posts: list[Post]) -> dict[str, Any]:
    kinds = Counter(p.kind for p in posts)
    n = len(posts)
    m: dict[str, Any] = {
        "items": n,
        "kinds": dict(kinds),
        "repost_share": round(kinds.get("repost", 0) / n, 3) if n else None,
    }
    amp = [p for p in posts if p.kind in ("repost", "quote") and p.target_handle]
    targets = Counter(p.target_handle for p in amp if p.target_id != owner.id)
    m["top_amplified"] = targets.most_common(10)
    m["self_reposts"] = sum(p.target_id == owner.id for p in amp)
    m["languages"] = dict(Counter(p.lang for p in posts if p.lang).most_common(5))

    timed = sorted((p.created_at for p in posts if p.time_is_action and p.created_at), reverse=False)
    m["timed_items"] = len(timed)
    if timed:
        hours = [t.hour for t in timed]
        m["hour_histogram_utc"] = [hours.count(h) for h in range(24)]
        m["active_hours"] = len(set(hours))
        m["max_quiet_gap_hours"] = max_quiet_gap_hours(hours)
        m["span_days"] = round((timed[-1] - timed[0]).total_seconds() / 86400, 2)
        if len(timed) >= 10 and m["span_days"] >= 1.0:
            m["recent_posts_per_day"] = round(len(timed) / m["span_days"], 2)
        gaps = [(b - a).total_seconds() for a, b in zip(timed, timed[1:], strict=False)]
        gaps = [g for g in gaps if g > 0]
        if len(gaps) >= 2:
            mean = statistics.fmean(gaps)
            m["interval_cv"] = round(statistics.pstdev(gaps) / mean, 3) if mean else None
            m["median_interval_min"] = round(statistics.median(gaps) / 60, 1)

    own = [normalize_text(p.text) for p in posts if p.kind != "repost"]
    own = [t for t in own if len(t) >= 20]
    m["own_texts"] = len(own)
    if own:
        c = Counter(own)
        dup = sum(v for v in c.values() if v > 1)
        m["duplicate_share"] = round(dup / len(own), 3)

    clients = [p.client for p in posts if p.client and p.kind != "repost"]
    if clients:
        third = [c for c in clients if c.lower() not in OFFICIAL_CLIENTS]
        m["clients"] = dict(Counter(clients).most_common(5))
        m["third_party_client_share"] = round(len(third) / len(clients), 3)
    return m


def behavior_signals(
    m: dict[str, Any], acc: Account | None = None, as_of: datetime | None = None
) -> list[Signal]:
    sig: list[Signal] = []
    recent = m.get("recent_posts_per_day")
    age = days_between(acc.created_at, as_of) if acc and as_of else None
    if recent and age and age >= 1095 and acc and acc.tweets is not None:
        lifetime = acc.tweets / age
        ratio = recent / max(lifetime, 0.01)
        s = ramp(ratio, 10, 50) if recent >= 5 else 0.0
        if s > 0:
            sig.append(
                Signal(
                    "reactivated",
                    "Dormant account reactivated",
                    s,
                    0.3,
                    f"posting ~{recent:.0f}/day recently vs ~{lifetime:.1f}/day over its {age / 365:.0f}-year life "
                    f"({ratio:.0f}x); aged accounts are often bought and repurposed",
                    "behavior",
                )
            )
    n = m.get("items", 0)
    if n >= 15 and m.get("repost_share") is not None:
        share = m["repost_share"]
        s = ramp(share, 0.6, 0.95)
        if s > 0:
            sig.append(
                Signal(
                    "amplifier",
                    "Mostly reposts",
                    s,
                    0.3,
                    f"{m['kinds'].get('repost', 0)} of {n} recent timeline items are reposts ({share:.0%})",
                    "behavior",
                )
            )
    timed = m.get("timed_items", 0)
    if timed >= 30 and m.get("span_days", 0) >= 1.0:
        gap = m["max_quiet_gap_hours"]
        s = ramp(gap, 5, 1)
        if s > 0:
            sig.append(
                Signal(
                    "round_the_clock",
                    "No sleep gap",
                    s,
                    0.25,
                    f"active in {m['active_hours']}/24 UTC hours; longest quiet stretch {gap}h over {m['span_days']:.1f} days",
                    "behavior",
                )
            )
    cv = m.get("interval_cv")
    if cv is not None and timed >= 20:
        s = ramp(cv, 0.6, 0.2)
        if s > 0:
            sig.append(
                Signal(
                    "metronomic",
                    "Clockwork cadence",
                    s,
                    0.2,
                    f"posting intervals unusually regular (CV {cv:.2f}, median {m.get('median_interval_min')} min); humans are bursty",
                    "behavior",
                )
            )
    if m.get("own_texts", 0) >= 10 and m.get("duplicate_share") is not None:
        s = ramp(m["duplicate_share"], 0.15, 0.5)
        if s > 0:
            sig.append(
                Signal(
                    "duplicate_posts",
                    "Templated posts",
                    s,
                    0.2,
                    f"{m['duplicate_share']:.0%} of own posts are near-identical copies",
                    "behavior",
                )
            )
    share = m.get("third_party_client_share")
    if share is not None:
        s = ramp(share, 0.3, 0.8)
        if s > 0:
            top = ", ".join(m.get("clients", {}))
            sig.append(
                Signal(
                    "automation_client",
                    "Posts via automation client",
                    s,
                    0.2,
                    f"{share:.0%} of posts from non-X clients ({top})",
                    "behavior",
                )
            )
    return sig
