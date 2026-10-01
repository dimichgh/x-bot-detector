"""Authenticated backend via `twscrape <https://github.com/vladkens/twscrape>`_ using your X session.

Give it the cookies of a logged-in x.com browser session (``auth_token`` + ``ct0``), see
:mod:`xbotdetect.cookies`. twscrape keeps GraphQL query IDs / transaction IDs current and
waits out per-account rate limits. Its anonymous usage telemetry is switched off here
unless you explicitly set ``TWS_TELEMETRY``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..cookies import cookie_header
from ..models import About, Account, Post
from ..util import snowflake_time
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
)

DEFAULT_DB = Path(os.environ.get("XBOT_HOME", "~/.xbotdetect")).expanduser() / "twscrape-accounts.db"
SESSION_USERNAME = "xbot_session"


def account_from_tws(u: Any) -> Account:
    links = getattr(u, "descriptionLinks", None) or []
    uid = str(u.id)
    return Account(
        id=uid,
        handle=u.username,
        name=u.displayname or "",
        created_at=u.created or snowflake_time(uid),
        followers=u.followersCount,
        following=u.friendsCount,
        tweets=u.statusesCount,
        likes=u.favouritesCount,
        media=u.mediaCount,
        listed=u.listedCount,
        description=u.rawDescription or "",
        location=u.location or "",
        url=links[-1].url if links else None,
        avatar_url=u.profileImageUrl,
        banner_url=u.profileBannerUrl,
        verified=bool(u.verified or u.blue),
        verified_type=u.blueType or ("legacy" if u.verified else None),
        protected=u.protected,
        source="twscrape",
    )


def about_from_tws(a: Any) -> About | None:
    if a is None:
        return None
    changed = a.username_last_changed_at
    return About(
        based_in=a.account_based_in,
        source=a.source,
        location_accurate=a.location_accurate,
        username_changes=a.username_changes,
        username_last_changed_at=datetime.fromtimestamp(changed / 1000, tz=timezone.utc) if changed else None,
    )


def _explain(e: Exception) -> str:
    if type(e).__name__ == "NoAccountError":
        return (
            "no usable X session (cookies expired or logged out, or every account is rate-limited; "
            "refresh auth_token/ct0 or retry later)"
        )
    return f"{type(e).__name__}: {e}"


def post_from_tws(t: Any) -> tuple[Post, list[Account]]:
    author = account_from_tws(t.user)
    embedded = [author]
    post = Post(
        id=str(t.id),
        author_id=author.id,
        author_handle=author.handle,
        created_at=t.date,
        text=t.rawContent or "",
        lang=t.lang,
        likes=t.likeCount,
        reposts=t.retweetCount,
        replies=t.replyCount,
        views=t.viewCount,
        client=t.sourceLabel,
    )
    if t.retweetedTweet is not None:
        orig = account_from_tws(t.retweetedTweet.user)
        embedded.append(orig)
        post.kind = "repost"
        post.target_id, post.target_handle = orig.id, orig.handle
        post.target_post_id = str(t.retweetedTweet.id)
    elif t.inReplyToTweetId:
        post.kind = "reply"
        ref = t.inReplyToUser
        post.target_id = str(ref.id) if ref else None
        post.target_handle = ref.username if ref else t.inReplyToScreenName
        post.target_post_id = str(t.inReplyToTweetId)
    elif t.quotedTweet is not None:
        q = account_from_tws(t.quotedTweet.user)
        embedded.append(q)
        post.kind = "quote"
        post.target_id, post.target_handle = q.id, q.handle
        post.target_post_id = str(t.quotedTweet.id)
    return post, embedded


class TwscrapeSource(DataSource):
    name = "twscrape"
    capabilities = frozenset({PROFILE, ABOUT, FOLLOWERS, FOLLOWING, TIMELINE, TWEET, REPOSTERS, REPLIES})

    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        db_path: str | os.PathLike[str] | None = None,
        proxy: str | None = None,
    ):
        self.cookies = cookies
        self.db_path = Path(db_path).expanduser() if db_path else DEFAULT_DB
        self.proxy = proxy
        self.api: Any = None

    async def open(self) -> None:
        if self.api is not None:
            return
        os.environ.setdefault("TWS_TELEMETRY", "0")
        os.environ.setdefault("TWS_LOG_LEVEL", "WARNING")
        try:
            from twscrape import API
            from twscrape.logger import set_log_level
        except ImportError as e:
            raise SourceError("twscrape is not installed: pip install 'x-bot-detector[twscrape]'") from e
        set_log_level(os.environ["TWS_LOG_LEVEL"].upper())  # type: ignore[arg-type]
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.api = API(str(self.db_path), proxy=self.proxy, raise_when_no_account=True)
        if self.cookies:
            await self.api.pool.add_account_cookies(SESSION_USERNAME, cookie_header(self.cookies))
        if not await self.api.pool.get_all():
            raise SourceError(
                "twscrape has no X account: pass your session cookies (--cookies / --cookies-from-browser / "
                "X_AUTH_TOKEN + X_CT0) or add accounts with the `twscrape` CLI"
            )

    async def _collect(self, agen: Any, limit: int, mapper) -> list:
        out = []
        try:
            async for item in agen:
                out.append(mapper(item))
                if len(out) >= limit:
                    break
        except Exception as e:  # twscrape surfaces HTTP/account errors as plain exceptions
            if out:
                return out
            raise SourceError(f"twscrape: {_explain(e)}") from e
        return out

    async def _user(self, call):
        try:
            return await call
        except Exception as e:
            raise SourceError(f"twscrape: {_explain(e)}") from e

    async def get_profile(self, handle: str) -> Account:
        await self.open()
        u = await self._user(self.api.user_by_login(handle))
        if u is None:
            raise NotFound(f"@{handle}: no such user")
        return account_from_tws(u)

    async def get_about(self, account: Account) -> About | None:
        await self.open()
        return about_from_tws(await self._user(self.api.user_about(account.handle)))

    async def get_followers(self, account: Account, limit: int) -> list[Account]:
        await self.open()
        return await self._collect(self.api.followers(int(account.id), limit=limit), limit, account_from_tws)

    async def get_following(self, account: Account, limit: int) -> list[Account]:
        await self.open()
        return await self._collect(self.api.following(int(account.id), limit=limit), limit, account_from_tws)

    async def _posts(self, agen: Any, limit: int) -> PostBatch:
        pairs = await self._collect(agen, limit, post_from_tws)
        batch = PostBatch()
        for post, accounts in pairs:
            batch.posts.append(post)
            batch.accounts.extend(accounts)
        return batch

    async def get_timeline(self, account: Account, limit: int) -> PostBatch:
        await self.open()
        return await self._posts(self.api.user_tweets(int(account.id), limit=limit), limit)

    async def get_tweet(self, tweet_id: str) -> PostBatch:
        await self.open()
        t = await self._user(self.api.tweet_details(int(tweet_id)))
        if t is None:
            raise NotFound(f"tweet {tweet_id}: not found")
        post, accounts = post_from_tws(t)
        return PostBatch([post], accounts)

    async def get_reposters(self, tweet_id: str, limit: int) -> list[Account]:
        await self.open()
        return await self._collect(self.api.retweeters(int(tweet_id), limit=limit), limit, account_from_tws)

    async def get_replies(self, tweet_id: str, limit: int) -> PostBatch:
        await self.open()
        return await self._posts(self.api.tweet_replies(int(tweet_id), limit=limit), limit)
