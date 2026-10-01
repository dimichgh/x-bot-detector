"""Small helpers: time parsing, Snowflake IDs, handle normalisation, score ramps."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# X/Twitter Snowflake epoch (2010-11-04T01:42:54.657Z) in milliseconds.
SNOWFLAKE_EPOCH_MS = 1288834974657
# User IDs below this are pre-Snowflake sequential IDs and carry no timestamp.
_MIN_SNOWFLAKE_ID = 10**14


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def parse_iso(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return parse_twitter_date(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_twitter_date(value: str | None) -> datetime | None:
    """Parse X's legacy ``Thu Jan 22 10:23:48 +0000 2026`` format (and ISO as a fallback)."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except (TypeError, ValueError):
        pass
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and value[:4].isdigit():
        return parse_iso(value)
    return None


def snowflake_time(id_: str | int | None) -> datetime | None:
    """Creation time encoded in a Snowflake ID (accounts created after ~2013, every tweet)."""
    try:
        n = int(id_)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if n < _MIN_SNOWFLAKE_ID:
        return None
    return datetime.fromtimestamp(((n >> 22) + SNOWFLAKE_EPOCH_MS) / 1000, tz=timezone.utc)


_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_URL_HANDLE_RE = re.compile(r"(?:x|twitter)\.com/(?:#!/)?@?([A-Za-z0-9_]{1,15})(?:[/?#]|$)", re.I)
_TWEET_RE = re.compile(r"(?:x|twitter)\.com/[^/]+/status(?:es)?/(\d+)", re.I)


def normalize_handle(value: str) -> str:
    """Accept ``@handle``, ``handle`` or a profile URL; return the bare handle."""
    v = value.strip()
    m = _URL_HANDLE_RE.search(v)
    if m:
        v = m.group(1)
    v = v.lstrip("@").strip()
    if not _HANDLE_RE.match(v):
        raise ValueError(f"not a valid X handle: {value!r}")
    return v


def parse_tweet_id(value: str) -> str:
    v = value.strip()
    m = _TWEET_RE.search(v)
    if m:
        return m.group(1)
    if v.isdigit():
        return v
    raise ValueError(f"not a tweet URL or ID: {value!r}")


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def ramp(x: float | None, lo: float, hi: float) -> float:
    """0 at ``lo`` (or below), 1 at ``hi`` (or above), linear in between. Works for lo > hi too."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0.0
    if hi == lo:
        return 1.0 if x >= hi else 0.0
    return clamp((x - lo) / (hi - lo))


def log_ramp(x: float | None, lo: float, hi: float) -> float:
    """Like :func:`ramp` but on a log scale; ``lo``/``hi`` must be > 0."""
    if x is None or x <= 0:
        return 0.0
    return ramp(math.log(x), math.log(lo), math.log(hi))


def days_between(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400.0


def fmt_int(n: int | float | None) -> str:
    return "?" if n is None else f"{int(round(n)):,}"


def fmt_date(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "?"
