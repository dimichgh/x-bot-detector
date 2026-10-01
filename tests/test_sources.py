import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from xbotdetect.analysis import countries
from xbotdetect.cookies import CookieError, load_cookies, parse_cookie_text
from xbotdetect.dataset import Dataset
from xbotdetect.models import Account, Engagement, Post
from xbotdetect.sources.base import NotFound
from xbotdetect.sources.bird import account_from_bird, post_from_bird
from xbotdetect.sources.cache import Cache, CachedSource
from xbotdetect.sources.fxtwitter import FxTwitterSource, about_from_fx, account_from_fx, post_from_fx
from xbotdetect.sources.twscrape_source import account_from_tws, post_from_tws
from xbotdetect.util import normalize_handle, parse_tweet_id, snowflake_time

UTC = timezone.utc


def fx_user(i: int, handle: str, joined: str = "Thu Jan 22 10:23:48 +0000 2026", **kw):
    u = {
        "screen_name": handle,
        "id": str(i),
        "followers": 100 + i,
        "following": 50,
        "likes": 10,
        "media_count": 1,
        "statuses": 500,
        "name": handle.title(),
        "description": "hi",
        "location": "Berlin, Germany",
        "banner_url": "https://pbs.twimg.com/profile_banners/x",
        "avatar_url": "https://pbs.twimg.com/profile_images/y.jpg",
        "joined": joined,
        "protected": False,
        "website": {"url": "http://example.com"},
        "verification": {"verified": True, "type": "individual"},
    }
    u.update(kw)
    return u


def test_util_parsing():
    assert normalize_handle("@Foo_1") == "Foo_1"
    assert normalize_handle("https://x.com/Foo_1/status/123") == "Foo_1"
    assert normalize_handle("twitter.com/bar") == "bar"
    with pytest.raises(ValueError):
        normalize_handle("not a handle!")
    assert parse_tweet_id("https://x.com/a/status/2105400661227516264?s=20") == "2105400661227516264"
    assert snowflake_time("2014282700832612352").date().isoformat() == "2026-01-22"
    assert snowflake_time("12345") is None


def test_countries():
    assert countries.resolve("Россия.") == {"RU"}
    assert countries.resolve("Berlin, Deutschland") == {"DE"}
    assert countries.resolve("🇺🇦 Kyiv") == {"UA"}
    assert countries.resolve("somewhere over the rainbow") == set()
    assert countries.source_country("Russian Federation Android App") == {"RU"}
    assert countries.source_country("Germany Android App") == {"DE"}
    assert countries.source_country("United States App Store") == {"US"}
    assert countries.source_country("Netherlands") == {"NL"}


def test_fx_mapping():
    a = account_from_fx(fx_user(2014282700832612352, "MartinX"))
    assert a.handle == "MartinX" and a.created_at == datetime(2026, 1, 22, 10, 23, 48, tzinfo=UTC)
    assert a.tweets == 500 and a.verified and a.url == "http://example.com" and not a.default_avatar
    ab = about_from_fx(
        {
            "based_in": "Germany",
            "source": "Germany Android App",
            "location_accurate": True,
            "username_changes": {"count": 2, "last_changed_at": "2026-01-22T10:24:54.686Z"},
        }
    )
    assert ab.based_in == "Germany" and ab.username_changes == 2 and ab.username_last_changed_at.year == 2026

    owner = Account(id="1", handle="owner")
    repost = {
        "id": "99",
        "text": "x",
        "author": fx_user(5, "orig"),
        "created_timestamp": 1790789966,
        "reposted_by": {"id": "1", "screen_name": "owner"},
    }
    p, emb = post_from_fx(repost, owner)
    assert p.kind == "repost" and p.author_id == "1" and p.target_id == "5" and not p.time_is_action
    assert [e.handle for e in emb] == ["orig"]
    reply = {
        "id": "100",
        "text": "@a hi",
        "author": fx_user(1, "owner"),
        "created_at": "Wed Sep 30 17:39:26 +0000 2026",
        "replying_to": {"screen_name": "a", "status": "42"},
    }
    p, _ = post_from_fx(reply, owner)
    assert p.kind == "reply" and p.target_handle == "a" and p.target_post_id == "42" and p.time_is_action


def _mock_fx(pages: dict[str, dict], flaky_404: set[str] | None = None):
    calls: list[str] = []
    flaky = set(flaky_404 or ())

    def handler(req: httpx.Request) -> httpx.Response:
        key = req.url.path + (("?cursor=" + req.url.params["cursor"]) if "cursor" in req.url.params else "")
        calls.append(key)
        if key in flaky:
            flaky.discard(key)
            return httpx.Response(404, json={"code": 404, "results": []})
        if key not in pages:
            return httpx.Response(404, json={"code": 404, "message": "Not found"})
        return httpx.Response(200, json=pages[key])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_fx_source_pagination_and_retry(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    pages = {
        "/2/profile/someone": {"code": 200, "user": fx_user(7, "someone")},
        "/2/profile/someone/followers": {
            "code": 200,
            "results": [fx_user(i, f"f{i}") for i in range(3)],
            "cursor": {"bottom": "c1"},
        },
        "/2/profile/someone/followers?cursor=c1": {
            "code": 200,
            "results": [fx_user(i, f"f{i}") for i in range(3, 5)],
            "cursor": {"bottom": "c2"},
        },
        "/2/profile/someone/followers?cursor=c2": {"code": 200, "results": [], "cursor": {"bottom": None}},
    }
    client, calls = _mock_fx(pages, flaky_404={"/2/profile/someone/followers?cursor=c1"})
    src = FxTwitterSource(base_url="https://api.fxtwitter.com/2", client=client, page_delay=0)
    acc = await src.get_profile("someone")
    fol = await src.get_followers(acc, 10)
    assert [a.handle for a in fol] == ["f0", "f1", "f2", "f3", "f4"]
    assert calls.count("/2/profile/someone/followers?cursor=c1") == 2  # transient 404 retried
    assert len(await src.get_followers(acc, 2)) == 2
    with pytest.raises(NotFound):
        await src.get_profile("missing")


async def _no_sleep(*_a, **_k):
    return None


async def test_cached_source(tmp_path):
    class Fake:
        name = "fake"
        capabilities = frozenset({"profile", "followers"})
        calls = 0

        async def open(self):
            pass

        async def close(self):
            pass

        def supports(self, c):
            return c in self.capabilities

        async def get_followers(self, account, limit):
            Fake.calls += 1
            return [Account(id=str(i), handle=f"u{i}") for i in range(min(limit, 7))]

    src = CachedSource(Fake(), Cache(tmp_path / "c.sqlite3", 3600))
    me = Account(id="1", handle="me")
    assert len(await src.get_followers(me, 5)) == 5 and Fake.calls == 1
    assert len(await src.get_followers(me, 3)) == 3 and Fake.calls == 1  # served from the larger cached list
    assert len(await src.get_followers(me, 20)) == 7 and Fake.calls == 2  # needed more
    assert len(await src.get_followers(me, 50)) == 7 and Fake.calls == 2  # list known to be complete


def test_cookies(tmp_path):
    assert parse_cookie_text("auth_token=abc; ct0=def; lang=en")["ct0"] == "def"
    assert parse_cookie_text(
        json.dumps([{"name": "auth_token", "value": "a"}, {"name": "ct0", "value": "b"}])
    ) == {"auth_token": "a", "ct0": "b"}
    netscape = ".x.com\tTRUE\t/\tTRUE\t0\tauth_token\tAAA\n.x.com\tTRUE\t/\tTRUE\t0\tct0\tBBB\n"
    f = tmp_path / "cookies.txt"
    f.write_text("# Netscape HTTP Cookie File\n" + netscape)
    assert load_cookies(cookies_file=str(f)) == {"auth_token": "AAA", "ct0": "BBB"}
    assert load_cookies(env={"AUTH_TOKEN": "t", "CT0": "c"}) == {"auth_token": "t", "ct0": "c"}
    assert load_cookies(env={}) is None
    with pytest.raises(CookieError):
        load_cookies(cookies="auth_token=only")


def test_bird_and_twscrape_mapping():
    b = account_from_bird(
        {
            "id": "2014282700832612352",
            "username": "x",
            "followersCount": 5,
            "followingCount": 6,
            "isBlueVerified": True,
            "createdAt": "Thu Jan 22 10:23:48 +0000 2026",
        }
    )
    assert b.followers == 5 and b.created_at.year == 2026
    owner = Account(id="1", handle="me")
    p = post_from_bird(
        {
            "id": "2105400661227516264",
            "text": "RT @other: hello",
            "authorId": "1",
            "author": {"username": "me"},
            "createdAt": "Wed Sep 30 17:39:26 +0000 2026",
        },
        owner,
    )
    assert p.kind == "repost" and p.target_handle == "other" and p.time_is_action

    def user(i, name):
        return SimpleNamespace(
            id=i,
            username=name,
            displayname=name,
            created=datetime(2025, 1, 1, tzinfo=UTC),
            followersCount=1,
            friendsCount=2,
            statusesCount=3,
            favouritesCount=4,
            mediaCount=0,
            listedCount=0,
            rawDescription="",
            location="",
            profileImageUrl="u",
            profileBannerUrl=None,
            protected=False,
            verified=False,
            blue=True,
            blueType=None,
            descriptionLinks=[],
        )

    t = SimpleNamespace(
        id=5,
        user=user(1, "me"),
        date=datetime(2026, 9, 1, tzinfo=UTC),
        rawContent="RT",
        lang="en",
        likeCount=0,
        retweetCount=0,
        replyCount=0,
        viewCount=None,
        sourceLabel="Buffer",
        retweetedTweet=SimpleNamespace(id=4, user=user(2, "orig")),
        inReplyToTweetId=None,
        quotedTweet=None,
    )
    post, emb = post_from_tws(t)
    assert (
        post.kind == "repost" and post.target_handle == "orig" and post.client == "Buffer" and len(emb) == 2
    )
    assert account_from_tws(user(1, "me")).verified is True


def test_dataset_roundtrip(tmp_path, farm_world):
    farm_world.engagements["5"] = Engagement(
        "5",
        "1",
        datetime(2026, 9, 1, tzinfo=UTC),
        ["10001"],
        [Post(id="6", author_id="10002", author_handle="p", kind="reply")],
    )
    for name in ("d.json", "d.json.gz"):
        p = farm_world.save(tmp_path / name)
        back = Dataset.load(p)
        assert back.seed_ids == farm_world.seed_ids and len(back.accounts) == len(farm_world.accounts)
        assert back.accounts["1"].about.based_in == "Netherlands"
        assert back.followers == farm_world.followers and back.engagements["5"].replies[0].kind == "reply"
