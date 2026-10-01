"""SQLite response cache wrapped around any data source, so re-runs don't re-fetch."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..models import About, Account, Post
from .base import DataSource, PostBatch


class Cache:
    def __init__(self, path: str | Path, ttl_seconds: float):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds
        self.db = sqlite3.connect(str(self.path))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, value TEXT NOT NULL)"
        )
        self.db.commit()

    def get(self, key: str) -> Any | None:
        row = self.db.execute("SELECT fetched_at, value FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None or time.time() - row[0] > self.ttl:
            return None
        return json.loads(row[1])

    def put(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cache (key, fetched_at, value) VALUES (?, ?, ?)",
            (key, time.time(), json.dumps(value, ensure_ascii=False)),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()


def _batch_to_json(b: PostBatch) -> dict[str, Any]:
    return {"posts": [p.to_dict() for p in b.posts], "accounts": [a.to_dict() for a in b.accounts]}


def _batch_from_json(d: dict[str, Any]) -> PostBatch:
    return PostBatch(
        [Post.from_dict(p) for p in d.get("posts", [])], [Account.from_dict(a) for a in d.get("accounts", [])]
    )


class CachedSource(DataSource):
    """Caches results per (source, method, key). A cached list fetched with a larger
    limit (or that reached the end of the list) also serves smaller requests."""

    def __init__(self, inner: DataSource, cache: Cache):
        self.inner = inner
        self.cache = cache
        self.name = inner.name
        self.capabilities = inner.capabilities
        self.hits = 0
        self.misses = 0

    async def open(self) -> None:
        await self.inner.open()

    async def close(self) -> None:
        await self.inner.close()
        self.cache.close()

    def _key(self, method: str, key: str) -> str:
        return f"{self.inner.name}:{method}:{key}"

    async def _single(self, method: str, key: str, call, to_json, from_json):
        k = self._key(method, key)
        hit = self.cache.get(k)
        if hit is not None:
            self.hits += 1
            return from_json(hit["value"])
        self.misses += 1
        value = await call()  # NotFound is deliberately not cached: FxTwitter 404s can be transient
        self.cache.put(k, {"value": to_json(value)})
        return value

    async def _listing(self, method: str, key: str, limit: int, call, to_json, from_json, size):
        k = self._key(method, key)
        hit = self.cache.get(k)
        if hit is not None and (hit["limit"] >= limit or hit["complete"]):
            self.hits += 1
            return from_json(hit["value"], limit)
        self.misses += 1
        value = await call()
        self.cache.put(k, {"limit": limit, "complete": size(value) < limit, "value": to_json(value)})
        return value

    async def get_profile(self, handle: str) -> Account:
        return await self._single(
            "profile",
            handle.lower(),
            lambda: self.inner.get_profile(handle),
            lambda a: a.to_dict(),
            Account.from_dict,
        )

    async def get_about(self, account: Account) -> About | None:
        return await self._single(
            "about",
            account.id,
            lambda: self.inner.get_about(account),
            lambda a: a.to_dict() if a else None,
            About.from_dict,
        )

    def _accounts(self, method: str, account: Account, limit: int, fn):
        return self._listing(
            method,
            account.id,
            limit,
            lambda: fn(account, limit),
            lambda xs: [a.to_dict() for a in xs],
            lambda d, n: [Account.from_dict(a) for a in d[:n]],
            len,
        )

    async def get_followers(self, account: Account, limit: int) -> list[Account]:
        return await self._accounts("followers", account, limit, self.inner.get_followers)

    async def get_following(self, account: Account, limit: int) -> list[Account]:
        return await self._accounts("following", account, limit, self.inner.get_following)

    def _batch_slice(self, d: dict[str, Any], n: int) -> PostBatch:
        b = _batch_from_json(d)
        b.posts = b.posts[:n]
        return b

    async def get_timeline(self, account: Account, limit: int) -> PostBatch:
        return await self._listing(
            "timeline",
            account.id,
            limit,
            lambda: self.inner.get_timeline(account, limit),
            _batch_to_json,
            self._batch_slice,
            lambda b: len(b.posts),
        )

    async def get_tweet(self, tweet_id: str) -> PostBatch:
        return await self._single(
            "tweet", tweet_id, lambda: self.inner.get_tweet(tweet_id), _batch_to_json, _batch_from_json
        )

    async def get_reposters(self, tweet_id: str, limit: int) -> list[Account]:
        return await self._listing(
            "reposters",
            tweet_id,
            limit,
            lambda: self.inner.get_reposters(tweet_id, limit),
            lambda xs: [a.to_dict() for a in xs],
            lambda d, n: [Account.from_dict(a) for a in d[:n]],
            len,
        )

    async def get_replies(self, tweet_id: str, limit: int) -> PostBatch:
        return await self._listing(
            "replies",
            tweet_id,
            limit,
            lambda: self.inner.get_replies(tweet_id, limit),
            _batch_to_json,
            self._batch_slice,
            lambda b: len(b.posts),
        )
