"""Per-account metadata signals: age vs. growth, follow ratios, activity, profile completeness, origin."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..models import Account
from ..util import days_between, fmt_date, fmt_int, log_ramp, ramp
from . import countries
from .config import AnalysisConfig
from .scoring import Signal

_AUTOGEN_HANDLE = re.compile(r"^[A-Za-z]+_?[A-Za-z]*\d{5,}$|\d{8,}$")
_FOLLOW_LIMIT = (4900, 5100)  # X caps following at 5,000 until you have enough followers


def account_metrics(acc: Account, as_of: datetime) -> dict[str, Any]:
    age = days_between(acc.created_at, as_of)
    eff = max(age or 0.0, 1.0)
    m: dict[str, Any] = {"age_days": round(age, 1) if age is not None else None}
    if acc.followers is not None:
        m["followers_per_day"] = round(acc.followers / eff, 2)
    if acc.tweets is not None:
        m["tweets_per_day"] = round(acc.tweets / eff, 2)
    if acc.likes is not None:
        m["likes_per_day"] = round(acc.likes / eff, 2)
    if acc.followers is not None and acc.following is not None:
        m["following_to_followers"] = round(acc.following / max(acc.followers, 1), 3)
    return m


def _origin_signals(acc: Account) -> list[Signal]:
    out: list[Signal] = []
    about = acc.about
    if about is None:
        return out
    based = countries.source_country(about.based_in)
    signup = countries.source_country(about.source)
    claimed = countries.resolve(acc.location)
    parts: list[str] = []
    score = 0.0
    if based and claimed and not (based & claimed):
        score = 1.0
        parts.append(f"profile location {acc.location!r} but X says based in {about.based_in}")
    if based and signup and not (based & signup):
        score = max(score, 0.6)
        parts.append(f"signed up via {about.source!r} but based in {about.based_in}")
    elif not based and signup and claimed and not (signup & claimed):
        score = max(score, 0.5)
        parts.append(f"profile location {acc.location!r} but signed up via {about.source!r}")
    if score:
        out.append(Signal("origin_mismatch", "Country mismatch", score, 0.3, "; ".join(parts), "origin"))
    if about.location_accurate is False:
        out.append(
            Signal(
                "location_inaccurate",
                "VPN/proxy flagged",
                1.0,
                0.15,
                "X notes the shown country may be inaccurate (connection via VPN/proxy)",
                "origin",
            )
        )
    n = about.username_changes or 0
    if n:
        changed = about.username_last_changed_at
        just_after_signup = (
            n == 1
            and changed is not None
            and acc.created_at is not None
            and abs((changed - acc.created_at).total_seconds()) < 3 * 86400
        )
        if not just_after_signup:
            when = f" (last {fmt_date(changed)})" if changed else ""
            out.append(
                Signal(
                    "username_changes",
                    "Handle changed",
                    ramp(n, 0, 3),
                    0.25,
                    f"handle changed {n}x{when}; bought/repurposed accounts are often renamed",
                    "origin",
                )
            )
    return out


def account_signals(acc: Account, as_of: datetime, cfg: AnalysisConfig) -> list[Signal]:
    sig: list[Signal] = []
    age = days_between(acc.created_at, as_of)
    eff = max(age or 0.0, 1.0)

    if age is not None:
        s = ramp(age, 730, 90)
        if acc.created_at and acc.created_at >= cfg.recent_since:
            s = max(s, 0.5)
        if s > 0:
            sig.append(
                Signal(
                    "young_account",
                    "New account",
                    s,
                    0.2,
                    f"created {fmt_date(acc.created_at)} ({age:.0f} days ago)",
                )
            )

    fol, fing, tw = acc.followers, acc.following, acc.tweets
    if age is not None and fol is not None and fol >= 300 and age < 1095:
        fpd = fol / eff
        s = log_ramp(fpd, 3, 100)
        if s > 0:
            sig.append(
                Signal(
                    "rapid_follower_growth",
                    "Fast follower growth",
                    s,
                    0.35,
                    f"{fmt_int(fol)} followers in {age:.0f} days (~{fpd:.0f}/day)",
                )
            )

    if fol is not None and fing is not None:
        ratio = fing / max(fol, 1)
        if fing >= 500 and ratio >= 1.2:
            sig.append(
                Signal(
                    "mass_following",
                    "Follows far more than followed",
                    log_ramp(ratio, 1.2, 6.0),
                    0.25,
                    f"follows {fmt_int(fing)} vs {fmt_int(fol)} followers ({ratio:.2f}x)",
                )
            )
        if _FOLLOW_LIMIT[0] <= fing <= _FOLLOW_LIMIT[1] and fol < 5000:
            sig.append(
                Signal(
                    "follow_limit",
                    "At the 5,000-follow cap",
                    0.7,
                    0.2,
                    f"following {fmt_int(fing)} - pinned at X's follow limit, typical of follow-for-follow",
                )
            )
        if fing >= 1000 and fol >= 1000 and 0.85 <= ratio <= 1.15:
            sig.append(
                Signal(
                    "follow_back_symmetry",
                    "1:1 follow-back shape",
                    0.5,
                    0.2,
                    f"{fmt_int(fol)} followers / {fmt_int(fing)} following - near 1:1, typical of follow-back rings",
                )
            )

    if tw is not None and age is not None and age >= 14:
        tpd = tw / eff
        s = log_ramp(tpd, 40, 200)
        if s > 0:
            sig.append(
                Signal(
                    "hyperactive",
                    "Inhuman posting volume",
                    s,
                    0.4,
                    f"{fmt_int(tw)} posts in {age:.0f} days (~{tpd:.0f}/day)",
                    "behavior",
                )
            )
    if tw is not None and fol is not None and fol >= 1000 and tw < 50:
        sig.append(
            Signal(
                "dormant_audience",
                "Big audience, almost no posts",
                ramp(tw, 50, 0),
                0.35,
                f"{fmt_int(fol)} followers but only {fmt_int(tw)} posts - audience likely bought",
            )
        )
    if acc.likes is not None and age is not None and age >= 14:
        lpd = acc.likes / eff
        s = log_ramp(lpd, 300, 1500)
        if s > 0:
            sig.append(
                Signal(
                    "like_farming",
                    "Extreme like volume",
                    s,
                    0.15,
                    f"{fmt_int(acc.likes)} likes (~{lpd:.0f}/day)",
                    "behavior",
                )
            )

    parts: list[str] = []
    s = 0.0
    if acc.default_avatar:
        s += 0.6
        parts.append("default avatar")
    if not (acc.description or "").strip():
        s += 0.3
        parts.append("empty bio")
    if acc.source in ("fxtwitter", "twscrape") and not acc.banner_url:
        s += 0.15
        parts.append("no banner")
    if s:
        sig.append(Signal("profile_incomplete", "Bare profile", min(s, 1.0), 0.25, ", ".join(parts)))

    if _AUTOGEN_HANDLE.search(acc.handle or ""):
        sig.append(
            Signal(
                "autogen_handle",
                "Auto-generated handle",
                1.0,
                0.15,
                f"@{acc.handle} looks like X's default name+digits handle (bulk sign-ups rarely rename)",
            )
        )

    if acc.verified and age is not None and age < 180 and acc.verified_type not in ("business", "government"):
        sig.append(
            Signal(
                "paid_new",
                "Paid check on new account",
                0.5,
                0.15,
                "paid verification on an account under 6 months old (buys reply/ranking boost)",
            )
        )

    sig.extend(_origin_signals(acc))
    return sig
