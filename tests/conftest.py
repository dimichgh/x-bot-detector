"""Synthetic X neighbourhoods with a known follow farm, so detectors can be checked against ground truth."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from xbotdetect.dataset import Dataset
from xbotdetect.models import About, Account, Post

AS_OF = datetime(2026, 10, 1, tzinfo=timezone.utc)
UTC = timezone.utc


def organic(rng: random.Random, i: int) -> Account:
    created = datetime(2009, 1, 1, tzinfo=UTC) + timedelta(
        days=rng.uniform(0, (AS_OF - datetime(2009, 1, 1, tzinfo=UTC)).days - 90)
    )
    followers = int(rng.lognormvariate(5.5, 1.3))
    return Account(
        id=str(10_000 + i),
        handle=f"person_{i}",
        name=f"Person {i}",
        created_at=created,
        followers=followers,
        following=int(rng.lognormvariate(5.8, 0.8)),
        tweets=int(rng.lognormvariate(7, 1.2)),
        likes=int(rng.lognormvariate(7.5, 1.2)),
        description="I write about things",
        location="Berlin",
        avatar_url="https://pbs.twimg.com/profile_images/1/a.jpg",
        banner_url="https://pbs.twimg.com/profile_banners/1/1",
        source="fxtwitter",
    )


FARM_START = datetime(2025, 6, 1, tzinfo=UTC)


def farm_account(rng: random.Random, i: int, start: datetime = FARM_START) -> Account:
    created = start + timedelta(days=rng.uniform(0, 10))
    return Account(
        id=str(50_000 + i),
        handle=f"Anna{rng.randint(10_000_000, 99_999_999)}",
        name="Anna",
        created_at=created,
        followers=rng.randint(2500, 4000),
        following=rng.randint(2800, 4200),
        tweets=rng.randint(40_000, 90_000),
        likes=rng.randint(1000, 5000),
        description="",
        avatar_url="https://abs.twimg.com/sticky/default_profile_images/default_profile_normal.png",
        source="fxtwitter",
    )


def build_world(with_farm: bool = True, seed: int = 7, farm_start: datetime = FARM_START) -> Dataset:
    rng = random.Random(seed)
    ds = Dataset(as_of=AS_OF)
    target = Account(
        id="1",
        handle="TargetAccount",
        name="Target",
        created_at=datetime(2026, 1, 15, tzinfo=UTC),
        followers=8000,
        following=1500,
        tweets=9000,
        likes=3000,
        description="News and commentary",
        location="Россия",
        avatar_url="https://pbs.twimg.com/profile_images/2/b.jpg",
        banner_url="https://pbs.twimg.com/profile_banners/2/2",
        about=About(
            based_in="Netherlands",
            source="Russian Federation Android App",
            username_changes=1,
            username_last_changed_at=datetime(2026, 1, 15, 0, 5, tzinfo=UTC),
        ),
        source="fxtwitter",
    )
    ds.add_account(target)
    ds.seed_ids.append(target.id)

    people = [organic(rng, i) for i in range(320)]
    for a in people:
        ds.add_account(a)
    farm = [farm_account(rng, i, farm_start) for i in range(80)] if with_farm else []
    for a in farm:
        ds.add_account(a)

    # Followers newest-first; the farm followed in one contiguous batch.
    fol = [a.id for a in people[:300]]
    rng.shuffle(fol)
    if farm:
        fol = fol[:120] + [a.id for a in farm] + fol[120:]
    ds.followers[target.id] = fol
    following = [a.id for a in people[200:320]] + [a.id for a in farm[:40]]
    ds.following[target.id] = following

    # 2nd degree: farm accounts follow each other densely; people follow sparsely.
    expanded = farm[:30] + people[200:215]
    for a in expanded:
        if a in farm:
            mates = [b.id for b in farm if b.id != a.id and rng.random() < 0.7]
            ds.following[a.id] = mates + [target.id] + [p.id for p in rng.sample(people, 20)]
        else:
            ds.following[a.id] = [p.id for p in rng.sample(people, 40) if p.id != a.id]
        ds.expanded_ids.append(a.id)

    t0 = AS_OF - timedelta(days=3)
    posts = []
    for k in range(60):
        posts.append(
            Post(
                id=str(900_000 + k),
                author_id=target.id,
                author_handle=target.handle,
                kind="repost" if k % 4 else "post",
                created_at=t0 + timedelta(minutes=37 * k),
                time_is_action=not bool(k % 4),
                text=f"post number {k} about the news of the day",
                target_id=farm[k % len(farm)].id if (k % 4 and farm) else None,
                target_handle=farm[k % len(farm)].handle if (k % 4 and farm) else None,
                target_post_id=str(800_000 + k) if k % 4 else None,
            )
        )
    ds.timelines[target.id] = posts
    ds.meta = {"source": "synthetic", "options": {"followers": 400, "following": 200}}
    return ds


@pytest.fixture
def farm_world() -> Dataset:
    return build_world(with_farm=True)


@pytest.fixture
def clean_world() -> Dataset:
    return build_world(with_farm=False)


@pytest.fixture
def aged_farm_world() -> Dataset:
    """Same farm, but registered in 2016: bought aged accounts dodge new-account heuristics."""
    return build_world(with_farm=True, farm_start=datetime(2016, 3, 1, tzinfo=UTC))
