"""Data-source interface. Every backend maps its native objects onto :mod:`xbotdetect.models`."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..models import About, Account, Post

log = logging.getLogger("xbotdetect")

PROFILE = "profile"
ABOUT = "about"
FOLLOWERS = "followers"
FOLLOWING = "following"
TIMELINE = "timeline"
REPOSTERS = "reposters"
REPLIES = "replies"
TWEET = "tweet"

ALL_CAPABILITIES = frozenset({PROFILE, ABOUT, FOLLOWERS, FOLLOWING, TIMELINE, REPOSTERS, REPLIES, TWEET})


class SourceError(Exception):
    """A backend failed in a way that another backend might not."""


class NotFound(SourceError):
    """Account/tweet does not exist, is suspended, or is protected."""


class Unsupported(SourceError):
    """The backend cannot provide this kind of data."""


@dataclass
class PostBatch:
    """Posts plus any account records embedded in them (repost/quote authors, repliers)."""

    posts: list[Post] = field(default_factory=list)
    accounts: list[Account] = field(default_factory=list)


class DataSource:
    name: str = "base"
    capabilities: frozenset[str] = frozenset()

    async def open(self) -> None:  # noqa: B027 - optional hook
        pass

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass

    async def __aenter__(self) -> DataSource:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    async def get_profile(self, handle: str) -> Account:
        raise Unsupported(f"{self.name}: profile lookup")

    async def get_about(self, account: Account) -> About | None:
        raise Unsupported(f"{self.name}: about-account")

    async def get_followers(self, account: Account, limit: int) -> list[Account]:
        """Followers in X's order: most recent follower first."""
        raise Unsupported(f"{self.name}: followers")

    async def get_following(self, account: Account, limit: int) -> list[Account]:
        """Accounts followed, most recently followed first."""
        raise Unsupported(f"{self.name}: following")

    async def get_timeline(self, account: Account, limit: int) -> PostBatch:
        raise Unsupported(f"{self.name}: timeline")

    async def get_tweet(self, tweet_id: str) -> PostBatch:
        raise Unsupported(f"{self.name}: tweet lookup")

    async def get_reposters(self, tweet_id: str, limit: int) -> list[Account]:
        raise Unsupported(f"{self.name}: reposters")

    async def get_replies(self, tweet_id: str, limit: int) -> PostBatch:
        raise Unsupported(f"{self.name}: replies")


class CompositeSource(DataSource):
    """Route each request to the first backend that supports it; fall back on backend errors."""

    def __init__(self, sources: list[DataSource]):
        if not sources:
            raise ValueError("at least one data source is required")
        self.sources = sources
        self.name = "+".join(s.name for s in sources)
        self.capabilities = frozenset().union(*(s.capabilities for s in sources))

    async def open(self) -> None:
        for s in self.sources:
            await s.open()

    async def close(self) -> None:
        for s in self.sources:
            await s.close()

    async def _call(self, capability: str, method: str, *args):
        errors: list[str] = []
        for s in self.sources:
            if not s.supports(capability):
                continue
            try:
                return await getattr(s, method)(*args)
            except NotFound:
                raise
            except Unsupported as e:
                errors.append(str(e))
            except SourceError as e:
                log.warning("%s failed for %s: %s; trying next source", s.name, method, e)
                errors.append(f"{s.name}: {e}")
        raise Unsupported("; ".join(errors) or f"no configured source provides {capability}")

    async def get_profile(self, handle):
        return await self._call(PROFILE, "get_profile", handle)

    async def get_about(self, account):
        return await self._call(ABOUT, "get_about", account)

    async def get_followers(self, account, limit):
        return await self._call(FOLLOWERS, "get_followers", account, limit)

    async def get_following(self, account, limit):
        return await self._call(FOLLOWING, "get_following", account, limit)

    async def get_timeline(self, account, limit):
        return await self._call(TIMELINE, "get_timeline", account, limit)

    async def get_tweet(self, tweet_id):
        return await self._call(TWEET, "get_tweet", tweet_id)

    async def get_reposters(self, tweet_id, limit):
        return await self._call(REPOSTERS, "get_reposters", tweet_id, limit)

    async def get_replies(self, tweet_id, limit):
        return await self._call(REPLIES, "get_replies", tweet_id, limit)
