"""Normalised data model shared by every data source and analysis step."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any

from .util import iso, parse_iso


@dataclass
class About:
    """X's "About this account" panel (country of origin, handle history)."""

    based_in: str | None = None
    # Signup client + region, e.g. "Germany Android App" / "Russian Federation App Store".
    source: str | None = None
    location_accurate: bool | None = None
    created_country_accurate: bool | None = None
    username_changes: int | None = None
    username_last_changed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["username_last_changed_at"] = iso(self.username_last_changed_at)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> About | None:
        if not d:
            return None
        d = dict(d)
        d["username_last_changed_at"] = parse_iso(d.get("username_last_changed_at"))
        return cls(**{k: d.get(k) for k in (f.name for f in fields(cls))})


@dataclass
class Account:
    id: str
    handle: str
    name: str = ""
    created_at: datetime | None = None
    followers: int | None = None
    following: int | None = None
    tweets: int | None = None
    likes: int | None = None
    media: int | None = None
    listed: int | None = None
    description: str = ""
    location: str = ""
    url: str | None = None
    avatar_url: str | None = None
    banner_url: str | None = None
    verified: bool | None = None
    verified_type: str | None = None
    protected: bool | None = None
    about: About | None = None
    # Which backend produced this record (fxtwitter, twscrape, bird, ...).
    source: str = ""

    @property
    def default_avatar(self) -> bool:
        return bool(self.avatar_url) and "default_profile_images" in (self.avatar_url or "")

    def merge(self, other: Account) -> Account:
        """Fill fields that are missing here with values from ``other`` (same account)."""
        for f in fields(self):
            mine = getattr(self, f.name)
            theirs = getattr(other, f.name)
            if (mine is None or mine == "") and theirs not in (None, ""):
                setattr(self, f.name, theirs)
        return self

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = iso(self.created_at)
        d["about"] = self.about.to_dict() if self.about else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Account:
        d = dict(d)
        d["created_at"] = parse_iso(d.get("created_at"))
        d["about"] = About.from_dict(d.get("about"))
        return cls(**{k: d[k] for k in (f.name for f in fields(cls)) if k in d})


@dataclass
class Post:
    """One timeline item. ``kind`` is post | repost | reply | quote."""

    id: str
    author_id: str
    author_handle: str
    kind: str = "post"
    created_at: datetime | None = None
    # False when ``created_at`` is the *original* tweet's time rather than when this
    # account acted (FxTwitter reports reposts that way).
    time_is_action: bool = True
    text: str = ""
    lang: str | None = None
    # For repost/quote: the original author; for reply: the account replied to.
    target_id: str | None = None
    target_handle: str | None = None
    target_post_id: str | None = None
    likes: int | None = None
    reposts: int | None = None
    replies: int | None = None
    views: int | None = None
    client: str | None = None  # posting client label when the backend exposes it

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = iso(self.created_at)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Post:
        d = dict(d)
        d["created_at"] = parse_iso(d.get("created_at"))
        return cls(**{k: d[k] for k in (f.name for f in fields(cls)) if k in d})


@dataclass
class Engagement:
    """Who engaged with one tweet: reposters and repliers."""

    tweet_id: str
    author_id: str | None = None
    created_at: datetime | None = None
    reposter_ids: list[str] = field(default_factory=list)
    replies: list[Post] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tweet_id": self.tweet_id,
            "author_id": self.author_id,
            "created_at": iso(self.created_at),
            "reposter_ids": list(self.reposter_ids),
            "replies": [p.to_dict() for p in self.replies],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Engagement:
        return cls(
            tweet_id=d["tweet_id"],
            author_id=d.get("author_id"),
            created_at=parse_iso(d.get("created_at")),
            reposter_ids=list(d.get("reposter_ids") or []),
            replies=[Post.from_dict(p) for p in d.get("replies") or []],
        )
