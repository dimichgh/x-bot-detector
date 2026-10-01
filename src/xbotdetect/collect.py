"""Fetch everything the analysis needs: seed profiles, follow lists, timelines, then expand
into the most suspicious neighbours (2nd degree) to measure mutual-follow density."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, TypeVar

from .analysis.account import account_signals
from .analysis.config import AnalysisConfig
from .analysis.network import bursts_for
from .analysis.scoring import combine
from .dataset import Dataset
from .models import Account, Engagement
from .sources.base import (
    ABOUT,
    FOLLOWERS,
    FOLLOWING,
    TIMELINE,
    DataSource,
    NotFound,
    SourceError,
    Unsupported,
)
from .util import iso, utcnow

log = logging.getLogger("xbotdetect")
T = TypeVar("T")


@dataclass
class CollectOptions:
    followers: int = 1000  # followers to sample per seed (newest first)
    following: int = 1000  # followed accounts to sample per seed
    timeline: int = 100  # recent timeline items per seed (0 = skip behaviour analysis)
    about: bool = True  # fetch "About this account" (country, handle changes) for seeds
    expand: int = 30  # neighbours whose own following list is fetched (0 = no 2nd-degree graph)
    expand_following: int = 400  # following entries per expanded neighbour
    expand_about: bool = True
    expand_timeline: int = 20  # one timeline page per expanded neighbour (co-amplification)
    expand_scope: str = "all"  # all: any age (aged farms too); recent: only young accounts
    min_candidate_followers: int = 200
    concurrency: int = 4

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _bounded(sem: asyncio.Semaphore, coro: Awaitable[T]) -> T:
    async with sem:
        return await coro


class Collector:
    def __init__(
        self, source: DataSource, opts: CollectOptions, cfg: AnalysisConfig, ds: Dataset | None = None
    ):
        self.src = source
        self.opts = opts
        self.cfg = cfg
        self.ds = ds or Dataset()
        self.sem = asyncio.Semaphore(max(1, opts.concurrency))
        self._unsupported: set[str] = set()

    def _err(self, msg: str) -> None:
        log.warning(msg)
        self.ds.errors.append(msg)

    async def _try(self, what: str, capability: str, fn: Callable[[], Awaitable[T]]) -> T | None:
        if not self.src.supports(capability):
            if capability not in self._unsupported:
                self._unsupported.add(capability)
                self._err(f"source {self.src.name} cannot fetch {capability}; skipped")
            return None
        try:
            return await _bounded(self.sem, fn())
        except NotFound as e:
            self._err(f"{what}: {e}")
        except Unsupported as e:
            if capability not in self._unsupported:
                self._unsupported.add(capability)
                self._err(f"{what}: {e}")
        except SourceError as e:
            self._err(f"{what}: {e}")
        return None

    async def seeds(self, handles: list[str]) -> list[Account]:
        async def one(h: str) -> Account | None:
            try:
                return await _bounded(self.sem, self.src.get_profile(h))
            except (NotFound, SourceError) as e:
                msg = str(e)
                self._err(msg if h.lower() in msg.lower() else f"@{h}: {msg}")
                return None

        found = [a for a in await asyncio.gather(*(one(h) for h in handles)) if a]
        for acc in found:
            stored = self.ds.add_account(acc)
            if stored.id not in self.ds.seed_ids:
                self.ds.seed_ids.append(stored.id)
        log.info("resolved %d/%d seed accounts", len(found), len(handles))
        return [self.ds.accounts[a.id] for a in found]

    async def about(self, accounts: list[Account]) -> None:
        async def one(acc: Account) -> None:
            if acc.about is not None:
                return
            about = await self._try(f"@{acc.handle} about", ABOUT, lambda: self.src.get_about(acc))
            if about is not None:
                self.ds.accounts[acc.id].about = about

        await asyncio.gather(*(one(a) for a in accounts))

    async def lists(self, acc: Account, followers: int, following: int, timeline: int) -> None:
        tasks = []
        if followers > 0:
            tasks.append(self._followers(acc, followers))
        if following > 0:
            tasks.append(self._following(acc, following))
        if timeline > 0:
            tasks.append(self._timeline(acc, timeline))
        await asyncio.gather(*tasks)

    async def _followers(self, acc: Account, n: int) -> None:
        rows = await self._try(f"@{acc.handle} followers", FOLLOWERS, lambda: self.src.get_followers(acc, n))
        if rows is not None:
            self.ds.followers[acc.id] = [self.ds.add_account(a).id for a in rows]
            log.info("@%s: %d followers sampled", acc.handle, len(rows))

    async def _following(self, acc: Account, n: int) -> None:
        rows = await self._try(f"@{acc.handle} following", FOLLOWING, lambda: self.src.get_following(acc, n))
        if rows is not None:
            self.ds.following[acc.id] = [self.ds.add_account(a).id for a in rows]
            log.info("@%s: %d followed accounts sampled", acc.handle, len(rows))

    async def _timeline(self, acc: Account, n: int) -> None:
        batch = await self._try(f"@{acc.handle} timeline", TIMELINE, lambda: self.src.get_timeline(acc, n))
        if batch is not None:
            for a in batch.accounts:
                self.ds.add_account(a)
            self.ds.timelines[acc.id] = batch.posts
            log.info("@%s: %d timeline items", acc.handle, len(batch.posts))

    def _burst_windows(self) -> list[tuple[float, float]]:
        """Creation bursts (any year) among the seeds' followers and followed accounts."""
        windows: list[tuple[float, float]] = []
        for lists in (self.ds.followers, self.ds.following):
            for sid in self.ds.seed_ids:
                dates = [self.ds.accounts[i].created_at for i in lists.get(sid, []) if i in self.ds.accounts]
                for b in bursts_for(dates, self.ds.as_of, self.cfg):
                    if not b.fresh_signups:
                        windows.append((b.start.timestamp(), b.end.timestamp()))
        return windows

    def candidates(self, k: int) -> list[Account]:
        """Rank seed neighbours for 2nd-degree expansion: inside a creation burst, mutual with a
        seed, shared between seeds, bot-like on metadata, sizeable, young."""
        seeds = set(self.ds.seed_ids)
        seen_in: dict[str, int] = {}
        mutual: set[str] = set()
        for sid in self.ds.seed_ids:
            fol = set(self.ds.followers.get(sid, []))
            fing = set(self.ds.following.get(sid, []))
            mutual |= fol & fing
            for i in fol | fing:
                seen_in[i] = seen_in.get(i, 0) + 1
        windows = self._burst_windows()
        priority: dict[str, float] = {}
        for aid, n_seeds in seen_in.items():
            if aid in seeds or aid in self.ds.expanded_ids:
                continue
            acc = self.ds.accounts.get(aid)
            if acc is None or acc.protected or (acc.following is not None and acc.following == 0):
                continue
            recent = bool(acc.created_at and acc.created_at >= self.cfg.recent_since)
            if self.opts.expand_scope == "recent" and not recent:
                continue
            if (acc.followers or 0) < self.opts.min_candidate_followers:
                continue
            meta = combine(account_signals(acc, self.ds.as_of, self.cfg))
            ts = acc.created_at.timestamp() if acc.created_at else None
            in_burst = ts is not None and any(a <= ts <= b for a, b in windows)
            priority[aid] = (
                meta
                + (0.35 if aid in mutual else 0.0)
                + (0.35 if in_burst else 0.0)
                + 0.2 * min(n_seeds - 1, 3)
                + (0.1 if recent else 0.0)
                + (0.1 if (acc.followers or 0) >= 1000 else 0.0)
            )
        # Round-robin over seeds so one big neighbourhood doesn't take the whole budget.
        queues = []
        for sid in self.ds.seed_ids:
            hood = set(self.ds.followers.get(sid, [])) | set(self.ds.following.get(sid, []))
            queues.append(sorted((a for a in hood if a in priority), key=lambda a: priority[a], reverse=True))
        picked: list[str] = []
        while len(picked) < k and any(queues):
            for q in queues:
                while q and q[0] in picked:
                    q.pop(0)
                if q and len(picked) < k:
                    picked.append(q.pop(0))
        return [self.ds.accounts[aid] for aid in picked]

    async def expand(self) -> None:
        o = self.opts
        if o.expand <= 0:
            return
        if not self.src.supports(FOLLOWING):
            self._err(f"source {self.src.name} cannot fetch following lists; 2nd-degree expansion skipped")
            return
        cands = self.candidates(o.expand)
        log.info("expanding %d neighbour accounts (their following lists)", len(cands))
        tasks = [self.lists(a, 0, o.expand_following, o.expand_timeline) for a in cands]
        if o.expand_about:
            tasks.append(self.about(cands))
        await asyncio.gather(*tasks)
        self.ds.expanded_ids.extend(a.id for a in cands if a.id in self.ds.following)


async def collect(
    source: DataSource,
    handles: list[str],
    opts: CollectOptions | None = None,
    cfg: AnalysisConfig | None = None,
) -> Dataset:
    opts = opts or CollectOptions()
    started = time.monotonic()
    as_of = utcnow()
    cfg = (cfg or AnalysisConfig()).resolve(as_of)
    col = Collector(source, opts, cfg, Dataset(as_of=as_of))
    seeds = await col.seeds(handles)
    if opts.about and seeds:
        await col.about(seeds)
    await asyncio.gather(*(col.lists(a, opts.followers, opts.following, opts.timeline) for a in seeds))
    await col.expand()
    col.ds.meta = {
        "source": source.name,
        "handles": handles,
        "collected_at": iso(col.ds.as_of),
        "options": opts.to_dict(),
        "seconds": round(time.monotonic() - started, 1),
        "accounts": len(col.ds.accounts),
    }
    return col.ds


async def collect_engagement(
    source: DataSource,
    tweet_ids: list[str],
    reposts: int = 500,
    replies: int = 300,
    ds: Dataset | None = None,
) -> Dataset:
    ds = ds or Dataset(as_of=utcnow())
    for tid in tweet_ids:
        eng = Engagement(tweet_id=tid)
        try:
            root = await source.get_tweet(tid)
            for a in root.accounts:
                ds.add_account(a)
            if root.posts:
                eng.author_id = root.posts[0].author_id
                eng.created_at = root.posts[0].created_at
        except (NotFound, SourceError) as e:
            ds.errors.append(f"tweet {tid}: {e}")
            log.warning("tweet %s: %s", tid, e)
        if reposts > 0:
            try:
                eng.reposter_ids = [ds.add_account(a).id for a in await source.get_reposters(tid, reposts)]
            except (NotFound, SourceError) as e:
                ds.errors.append(f"tweet {tid} reposters: {e}")
        if replies > 0:
            try:
                batch = await source.get_replies(tid, replies)
                for a in batch.accounts:
                    ds.add_account(a)
                eng.replies = [p for p in batch.posts if p.id != tid]
            except (NotFound, SourceError) as e:
                ds.errors.append(f"tweet {tid} replies: {e}")
        log.info("tweet %s: %d reposters, %d replies", tid, len(eng.reposter_ids), len(eng.replies))
        ds.engagements[tid] = eng
    ds.meta.setdefault("source", source.name)
    ds.meta["tweets"] = tweet_ids
    return ds
