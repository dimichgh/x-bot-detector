"""Replay a saved :class:`~xbotdetect.dataset.Dataset` (``--save-dataset``) without network access."""

from __future__ import annotations

from ..dataset import Dataset
from ..models import About, Account
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
)


class OfflineSource(DataSource):
    name = "offline"
    capabilities = frozenset({PROFILE, ABOUT, FOLLOWERS, FOLLOWING, TIMELINE, TWEET, REPOSTERS, REPLIES})

    def __init__(self, dataset: Dataset):
        self.ds = dataset

    def _ids_to_accounts(self, ids: list[str], limit: int) -> list[Account]:
        return [self.ds.accounts[i] for i in ids[:limit] if i in self.ds.accounts]

    async def get_profile(self, handle: str) -> Account:
        acc = self.ds.by_handle(handle)
        if acc is None:
            raise NotFound(f"@{handle}: not in dataset")
        return acc

    async def get_about(self, account: Account) -> About | None:
        acc = self.ds.accounts.get(account.id)
        return acc.about if acc else None

    async def get_followers(self, account: Account, limit: int) -> list[Account]:
        if account.id not in self.ds.followers:
            raise NotFound(f"@{account.handle}: followers not in dataset")
        return self._ids_to_accounts(self.ds.followers[account.id], limit)

    async def get_following(self, account: Account, limit: int) -> list[Account]:
        if account.id not in self.ds.following:
            raise NotFound(f"@{account.handle}: following not in dataset")
        return self._ids_to_accounts(self.ds.following[account.id], limit)

    async def get_timeline(self, account: Account, limit: int) -> PostBatch:
        posts = self.ds.timelines.get(account.id)
        if posts is None:
            raise NotFound(f"@{account.handle}: timeline not in dataset")
        return PostBatch(posts[:limit], [])

    async def get_tweet(self, tweet_id: str) -> PostBatch:
        for posts in self.ds.timelines.values():
            for p in posts:
                if p.id == tweet_id:
                    return PostBatch([p], [])
        raise NotFound(f"tweet {tweet_id}: not in dataset")

    async def get_reposters(self, tweet_id: str, limit: int) -> list[Account]:
        eng = self.ds.engagements.get(tweet_id)
        if eng is None:
            raise NotFound(f"tweet {tweet_id}: engagement not in dataset")
        return self._ids_to_accounts(eng.reposter_ids, limit)

    async def get_replies(self, tweet_id: str, limit: int) -> PostBatch:
        eng = self.ds.engagements.get(tweet_id)
        if eng is None:
            raise NotFound(f"tweet {tweet_id}: engagement not in dataset")
        accounts = [self.ds.accounts[p.author_id] for p in eng.replies if p.author_id in self.ds.accounts]
        return PostBatch(eng.replies[:limit], accounts)
