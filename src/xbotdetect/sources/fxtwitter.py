"""FxTwitter public API (https://api.fxtwitter.com): free, no X login needed.

Provides profiles, "About this account", followers/following lists, profile timelines,
reposters and reply threads. It proxies X's own endpoints, so list order matches X
(newest follower first).
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import __version__
from ..models import About, Account, Post
from ..util import parse_iso, parse_twitter_date, snowflake_time
from .base import (
    ABOUT,
    FOLLOWERS,
    FOLLOWING,
    PROFILE,
    REPLIES,
    REPOSTERS,
    TIMELINE,
    TWEET,
    DataSource,
    NotFound,
    PostBatch,
    SourceError,
    log,
)

DEFAULT_BASE_URL = "https://api.fxtwitter.com/2"
USER_AGENT = f"x-bot-detector/{__version__} (+https://github.com/dimichgh/x-bot-detector)"


def account_from_fx(u: dict[str, Any]) -> Account:
    ver = u.get("verification") or {}
    website = u.get("website") or {}
    uid = str(u.get("id") or "")
    return Account(
        id=uid,
        handle=u.get("screen_name") or "",
        name=u.get("name") or "",
        created_at=parse_twitter_date(u.get("joined")) or snowflake_time(uid),
        followers=u.get("followers"),
        following=u.get("following"),
        tweets=u.get("statuses", u.get("tweets")),
        likes=u.get("likes"),
        media=u.get("media_count"),
        description=u.get("description") or "",
        location=u.get("location") or "",
        url=website.get("url") if isinstance(website, dict) else None,
        avatar_url=u.get("avatar_url"),
        banner_url=u.get("banner_url"),
        verified=ver.get("verified"),
        verified_type=ver.get("type"),
        protected=u.get("protected"),
        source="fxtwitter",
    )


def about_from_fx(a: dict[str, Any] | None) -> About | None:
    if not a:
        return None
    changes = a.get("username_changes") or {}
    return About(
        based_in=a.get("based_in"),
        source=a.get("source"),
        location_accurate=a.get("location_accurate"),
        created_country_accurate=a.get("created_country_accurate"),
        username_changes=changes.get("count"),
        username_last_changed_at=parse_iso(changes.get("last_changed_at")),
    )


def _status_time(st: dict[str, Any]):
    ts = st.get("created_timestamp")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return parse_twitter_date(st.get("created_at")) or snowflake_time(st.get("id"))


def post_from_fx(st: dict[str, Any], owner: Account | None = None) -> tuple[Post, list[Account]]:
    """Map an FxTwitter status. ``owner`` is the account whose timeline this came from."""
    author = account_from_fx(st.get("author") or {})
    embedded = [author] if author.id else []
    reposted_by = st.get("reposted_by") or None
    replying_to = st.get("replying_to") or None
    quote = st.get("quote") or None
    common = dict(
        text=st.get("text") or "",
        lang=st.get("lang"),
        likes=st.get("likes"),
        reposts=st.get("reposts"),
        replies=st.get("replies"),
        views=st.get("views"),
    )
    is_repost = bool(reposted_by) or (owner is not None and bool(author.id) and author.id != owner.id)
    if is_repost and owner is not None:
        post = Post(
            id=str(st.get("id")),
            author_id=owner.id,
            author_handle=owner.handle,
            kind="repost",
            created_at=_status_time(st),
            time_is_action=False,  # FxTwitter gives the original tweet's time, not the repost's
            target_id=author.id,
            target_handle=author.handle,
            target_post_id=str(st.get("id")),
            **common,
        )
        return post, embedded

    post = Post(
        id=str(st.get("id")),
        author_id=author.id,
        author_handle=author.handle,
        created_at=_status_time(st),
        **common,
    )
    if replying_to:
        post.kind = "reply"
        post.target_handle = replying_to.get("screen_name")
        post.target_post_id = replying_to.get("status")
    elif quote:
        q_author = account_from_fx(quote.get("author") or {})
        post.kind = "quote"
        post.target_id = q_author.id or None
        post.target_handle = q_author.handle or None
        post.target_post_id = str(quote.get("id")) if quote.get("id") else None
        if q_author.id:
            embedded.append(q_author)
    return post, embedded


class FxTwitterSource(DataSource):
    name = "fxtwitter"
    capabilities = frozenset({PROFILE, ABOUT, FOLLOWERS, FOLLOWING, TIMELINE, TWEET, REPOSTERS, REPLIES})

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        concurrency: int = 4,
        timeout: float = 30.0,
        retries: int = 4,
        page_delay: float = 0.25,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.page_delay = page_delay
        self._sem = asyncio.Semaphore(concurrency)
        self._client = client
        self._own_client = client is None
        self._timeout = timeout

    async def open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
            )

    async def close(self) -> None:
        if self._client is not None and self._own_client:
            await self._client.aclose()
            self._client = None

    async def _get(
        self, path: str, params: dict[str, Any] | None = None, retry_404: int = 0
    ) -> dict[str, Any]:
        """GET with retries on 429/5xx/network errors. ``retry_404`` retries that many 404s
        first, because FxTwitter's list endpoints occasionally 404 transiently."""
        if self._client is None:
            await self.open()
        assert self._client is not None
        url = f"{self.base_url}/{path.lstrip('/')}"
        delay = 2.0
        last_err: str = ""
        for attempt in range(self.retries + 1):
            try:
                async with self._sem:
                    resp = await self._client.get(url, params=params or None)
            except httpx.HTTPError as e:
                last_err = f"{type(e).__name__}: {e}"
            else:
                data: Any = None
                if resp.status_code < 500 and resp.status_code != 429:
                    try:
                        data = resp.json()
                    except ValueError:
                        data = None
                code = resp.status_code
                if isinstance(data, dict) and isinstance(data.get("code"), int):
                    code = max(code, data["code"]) if code != 200 else data["code"]
                if code == 404 and retry_404 > 0:
                    retry_404 -= 1
                    last_err = "HTTP 404"
                elif code == 404:
                    raise NotFound(f"{path}: not found (deleted, suspended or protected?)")
                elif code in (401, 403):
                    raise NotFound(f"{path}: not accessible (HTTP {code})")
                elif code == 429 or code >= 500:
                    last_err = f"HTTP {code}"
                    retry_after = resp.headers.get("retry-after")
                    if retry_after and retry_after.isdigit():
                        delay = max(delay, min(float(retry_after), 120.0))
                elif code >= 400:
                    raise SourceError(f"{path}: API error {code}")
                elif not isinstance(data, dict):
                    raise SourceError(f"{path}: invalid JSON response")
                else:
                    return data
            if attempt < self.retries:
                wait = delay * (1 + random.random() * 0.25)
                log.info("fxtwitter %s: %s, retrying in %.1fs", path, last_err, wait)
                await asyncio.sleep(wait)
                delay *= 2
        raise SourceError(f"{path}: giving up after {self.retries + 1} attempts ({last_err})")

    async def _paged(self, path: str, key: str, limit: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        cursor: str | None = None
        while len(out) < limit:
            try:
                data = await self._get(path, {"cursor": cursor} if cursor else None, retry_404=2)
            except NotFound:
                if cursor is None:
                    raise
                break  # some endpoints 404 past the last page instead of returning an empty one
            items = data.get(key) or []
            fresh = [it for it in items if str(it.get("id")) not in seen]
            for it in fresh:
                seen.add(str(it.get("id")))
            out.extend(fresh)
            next_cursor = (data.get("cursor") or {}).get("bottom")
            if not fresh or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
            if self.page_delay:
                await asyncio.sleep(self.page_delay)
        return out[:limit]

    async def get_profile(self, handle: str) -> Account:
        data = await self._get(f"profile/{handle}")
        user = data.get("user")
        if not user:
            raise NotFound(f"@{handle}: no such user")
        return account_from_fx(user)

    async def get_about(self, account: Account) -> About | None:
        data = await self._get(f"profile/{account.handle}/about")
        return about_from_fx(data.get("about_account"))

    async def get_followers(self, account: Account, limit: int) -> list[Account]:
        rows = await self._paged(f"profile/{account.handle}/followers", "results", limit)
        return [account_from_fx(u) for u in rows if u.get("id")]

    async def get_following(self, account: Account, limit: int) -> list[Account]:
        rows = await self._paged(f"profile/{account.handle}/following", "results", limit)
        return [account_from_fx(u) for u in rows if u.get("id")]

    async def get_timeline(self, account: Account, limit: int) -> PostBatch:
        rows = await self._paged(f"profile/{account.handle}/statuses", "results", limit)
        batch = PostBatch()
        for st in rows:
            post, accounts = post_from_fx(st, owner=account)
            batch.posts.append(post)
            batch.accounts.extend(accounts)
        return batch

    async def get_tweet(self, tweet_id: str) -> PostBatch:
        data = await self._get(f"status/{tweet_id}")
        st = data.get("status")
        if not st:
            raise NotFound(f"tweet {tweet_id}: not found")
        post, accounts = post_from_fx(st)
        return PostBatch([post], accounts)

    async def get_reposters(self, tweet_id: str, limit: int) -> list[Account]:
        rows = await self._paged(f"status/{tweet_id}/reposts", "results", limit)
        return [account_from_fx(u) for u in rows if u.get("id")]

    async def get_replies(self, tweet_id: str, limit: int) -> PostBatch:
        rows = await self._paged(f"conversation/{tweet_id}", "replies", limit)
        batch = PostBatch()
        for st in rows:
            post, accounts = post_from_fx(st)
            batch.posts.append(post)
            batch.accounts.extend(accounts)
        return batch
