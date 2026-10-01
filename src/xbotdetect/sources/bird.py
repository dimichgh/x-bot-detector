"""Backend that shells out to the `bird` CLI (``npm i -g @steipete/bird``), as used by OpenClaw.

``bird`` authenticates with your browser's X session automatically (Safari/Chrome/Firefox
cookie stores) or with ``AUTH_TOKEN``/``CT0``. It has no full-profile lookup, so combine it
with another source for profiles, e.g. ``--source bird,fxtwitter`` (the CLI does this for you).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
from typing import Any

from ..models import About, Account, Post
from ..util import parse_twitter_date, snowflake_time
from .base import ABOUT, FOLLOWERS, FOLLOWING, TIMELINE, DataSource, NotFound, PostBatch, SourceError

PAGE_SIZE = 50
_RT_RE = re.compile(r"^RT @([A-Za-z0-9_]{1,15}):")


def account_from_bird(u: dict[str, Any]) -> Account:
    uid = str(u.get("id") or "")
    return Account(
        id=uid,
        handle=u.get("username") or "",
        name=u.get("name") or "",
        created_at=parse_twitter_date(u.get("createdAt")) or snowflake_time(uid),
        followers=u.get("followersCount"),
        following=u.get("followingCount"),
        description=u.get("description") or "",
        avatar_url=u.get("profileImageUrl"),
        verified=u.get("isBlueVerified"),
        source="bird",
    )


def post_from_bird(t: dict[str, Any], owner: Account) -> Post:
    text = t.get("text") or ""
    post = Post(
        id=str(t.get("id")),
        author_id=str(t.get("authorId") or owner.id),
        author_handle=(t.get("author") or {}).get("username") or owner.handle,
        created_at=parse_twitter_date(t.get("createdAt")) or snowflake_time(t.get("id")),
        text=text,
        likes=t.get("likeCount"),
        reposts=t.get("retweetCount"),
        replies=t.get("replyCount"),
    )
    m = _RT_RE.match(text)
    if m or post.author_id != owner.id:
        post.kind = "repost"
        post.target_handle = m.group(1) if m else post.author_handle
        post.target_id = None if m else post.author_id
        post.author_id, post.author_handle = owner.id, owner.handle
        post.time_is_action = bool(m)  # a native "RT @" item carries the repost's own timestamp
    elif t.get("inReplyToStatusId"):
        post.kind = "reply"
        post.target_post_id = str(t["inReplyToStatusId"])
    elif t.get("quotedTweet"):
        q = t["quotedTweet"]
        post.kind = "quote"
        post.target_post_id = str(q.get("id")) if q.get("id") else None
        post.target_handle = (q.get("author") or {}).get("username")
        post.target_id = str(q["authorId"]) if q.get("authorId") else None
    return post


class BirdSource(DataSource):
    name = "bird"
    capabilities = frozenset({ABOUT, FOLLOWERS, FOLLOWING, TIMELINE})

    def __init__(
        self,
        executable: str = "bird",
        cookie_source: str | None = None,
        cookies: dict[str, str] | None = None,
        timeout: float = 600.0,
    ):
        self.executable = executable
        self.cookie_source = cookie_source
        self.cookies = cookies
        self.timeout = timeout

    async def open(self) -> None:
        if shutil.which(self.executable) is None:
            raise SourceError(f"`{self.executable}` not found on PATH; install with: npm i -g @steipete/bird")

    async def _run(self, *args: str) -> Any:
        cmd = [self.executable, *args, "--json", "--plain"]
        if self.cookie_source:
            cmd += ["--cookie-source", self.cookie_source]
        env = dict(os.environ)
        if self.cookies:  # pass secrets via env, never argv
            env["AUTH_TOKEN"], env["CT0"] = self.cookies["auth_token"], self.cookies["ct0"]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError as e:
            proc.kill()
            raise SourceError(f"bird {args[0]} timed out") from e
        if proc.returncode != 0:
            msg = err.decode(errors="replace").strip().splitlines()
            text = msg[-1] if msg else f"exit code {proc.returncode}"
            if re.search(r"not found|does not exist|suspended", text, re.I):
                raise NotFound(f"bird {args[0]}: {text}")
            raise SourceError(f"bird {args[0]}: {text}")
        try:
            return json.loads(out.decode() or "null")
        except json.JSONDecodeError as e:
            raise SourceError(f"bird {args[0]}: unexpected output ({e})") from e

    async def _users(self, command: str, account: Account, limit: int) -> list[Account]:
        pages = max(1, math.ceil(limit / PAGE_SIZE))
        data = await self._run(
            command, "--user", account.id, "-n", str(PAGE_SIZE), "--all", "--max-pages", str(pages)
        )
        rows = data.get("users", []) if isinstance(data, dict) else data or []
        return [account_from_bird(u) for u in rows if u.get("id")][:limit]

    async def get_followers(self, account: Account, limit: int) -> list[Account]:
        return await self._users("followers", account, limit)

    async def get_following(self, account: Account, limit: int) -> list[Account]:
        return await self._users("following", account, limit)

    async def get_about(self, account: Account) -> About | None:
        a = await self._run("about", f"@{account.handle}")
        if not isinstance(a, dict):
            return None
        return About(
            based_in=a.get("accountBasedIn"),
            source=a.get("source"),
            location_accurate=a.get("locationAccurate"),
            created_country_accurate=a.get("createdCountryAccurate"),
        )

    async def get_timeline(self, account: Account, limit: int) -> PostBatch:
        data = await self._run("user-tweets", f"@{account.handle}", "-n", str(limit))
        rows = data.get("tweets", []) if isinstance(data, dict) else data or []
        return PostBatch([post_from_bird(t, account) for t in rows if t.get("id")][:limit], [])
