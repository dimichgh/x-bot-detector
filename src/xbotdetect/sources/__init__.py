"""Data-source registry: ``fxtwitter`` (no login), ``twscrape`` / ``bird`` (your X session), ``offline``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..cookies import load_cookies
from ..dataset import Dataset
from .base import PROFILE, CompositeSource, DataSource, NotFound, PostBatch, SourceError, Unsupported
from .cache import Cache, CachedSource

SOURCE_NAMES = ("fxtwitter", "twscrape", "bird", "offline")
DEFAULT_CACHE = Path(os.environ.get("XBOT_HOME", "~/.xbotdetect")).expanduser() / "cache.sqlite3"

__all__ = [
    "SOURCE_NAMES",
    "SourceOptions",
    "build_source",
    "DataSource",
    "CompositeSource",
    "NotFound",
    "PostBatch",
    "SourceError",
    "Unsupported",
]


@dataclass
class SourceOptions:
    cookies: str | None = None
    cookies_file: str | None = None
    cookies_from_browser: str | None = None
    twscrape_db: str | None = None
    proxy: str | None = None
    bird_cookie_source: str | None = None
    dataset: str | None = None
    fx_base_url: str | None = None
    concurrency: int = 4
    cache: bool = True
    cache_path: str | None = None
    cache_ttl_hours: float = 24.0


def _make(name: str, opts: SourceOptions) -> DataSource:
    if name == "fxtwitter":
        from .fxtwitter import DEFAULT_BASE_URL, FxTwitterSource

        return FxTwitterSource(base_url=opts.fx_base_url or DEFAULT_BASE_URL, concurrency=opts.concurrency)
    if name == "twscrape":
        from .twscrape_source import TwscrapeSource

        cookies = load_cookies(opts.cookies, opts.cookies_file, opts.cookies_from_browser)
        return TwscrapeSource(cookies=cookies, db_path=opts.twscrape_db, proxy=opts.proxy)
    if name == "bird":
        from .bird import BirdSource

        cookies = None
        if opts.cookies or opts.cookies_file:
            cookies = load_cookies(opts.cookies, opts.cookies_file)
        return BirdSource(cookie_source=opts.bird_cookie_source or opts.cookies_from_browser, cookies=cookies)
    if name == "offline":
        from .offline import OfflineSource

        if not opts.dataset:
            raise SourceError("the offline source needs --dataset PATH")
        return OfflineSource(Dataset.load(opts.dataset))
    raise SourceError(f"unknown source {name!r}; choose from {', '.join(SOURCE_NAMES)}")


def build_source(names: str | list[str], opts: SourceOptions | None = None) -> DataSource:
    """Build a (cached, composite) source from a comma-separated list, e.g. ``"bird,fxtwitter"``."""
    opts = opts or SourceOptions()
    if isinstance(names, str):
        names = [n.strip().lower() for n in names.split(",") if n.strip()]
    sources = [_make(n, opts) for n in names]
    if not any(s.supports(PROFILE) for s in sources):
        sources.append(_make("fxtwitter", opts))  # bird alone cannot look up profiles
    if opts.cache:
        cache_path = Path(opts.cache_path).expanduser() if opts.cache_path else DEFAULT_CACHE
        sources = [
            s if s.name == "offline" else CachedSource(s, Cache(cache_path, opts.cache_ttl_hours * 3600))
            for s in sources
        ]
    return sources[0] if len(sources) == 1 else CompositeSource(sources)
