"""Everything collected for one run, serialisable to JSON so analyses can be replayed offline."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Account, Engagement, Post
from .util import iso, parse_iso, utcnow

FORMAT_VERSION = 1


@dataclass
class Dataset:
    as_of: datetime = field(default_factory=utcnow)
    seed_ids: list[str] = field(default_factory=list)
    accounts: dict[str, Account] = field(default_factory=dict)
    # X order: most recent first.
    followers: dict[str, list[str]] = field(default_factory=dict)
    following: dict[str, list[str]] = field(default_factory=dict)
    timelines: dict[str, list[Post]] = field(default_factory=dict)
    engagements: dict[str, Engagement] = field(default_factory=dict)
    # Accounts whose following list was fetched during expansion (2nd degree).
    expanded_ids: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def add_account(self, acc: Account) -> Account:
        if not acc.id:
            return acc
        have = self.accounts.get(acc.id)
        if have is None:
            self.accounts[acc.id] = acc
            return acc
        # Prefer the richer record but keep any fields only the other one has.
        if _richness(acc) > _richness(have):
            self.accounts[acc.id] = acc.merge(have)
        else:
            have.merge(acc)
        return self.accounts[acc.id]

    def by_handle(self, handle: str) -> Account | None:
        h = handle.lower()
        for acc in self.accounts.values():
            if acc.handle.lower() == h:
                return acc
        return None

    @property
    def seeds(self) -> list[Account]:
        return [self.accounts[i] for i in self.seed_ids if i in self.accounts]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_VERSION,
            "as_of": iso(self.as_of),
            "seed_ids": self.seed_ids,
            "accounts": {k: v.to_dict() for k, v in self.accounts.items()},
            "followers": self.followers,
            "following": self.following,
            "timelines": {k: [p.to_dict() for p in v] for k, v in self.timelines.items()},
            "engagements": {k: v.to_dict() for k, v in self.engagements.items()},
            "expanded_ids": self.expanded_ids,
            "meta": self.meta,
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Dataset:
        return cls(
            as_of=parse_iso(d.get("as_of")) or utcnow(),
            seed_ids=list(d.get("seed_ids") or []),
            accounts={k: Account.from_dict(v) for k, v in (d.get("accounts") or {}).items()},
            followers={k: list(v) for k, v in (d.get("followers") or {}).items()},
            following={k: list(v) for k, v in (d.get("following") or {}).items()},
            timelines={k: [Post.from_dict(p) for p in v] for k, v in (d.get("timelines") or {}).items()},
            engagements={k: Engagement.from_dict(v) for k, v in (d.get("engagements") or {}).items()},
            expanded_ids=list(d.get("expanded_ids") or []),
            meta=dict(d.get("meta") or {}),
            errors=list(d.get("errors") or []),
        )

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
        if path.suffix == ".gz":
            path.write_bytes(gzip.compress(raw.encode()))
        else:
            path.write_text(raw)
        return path

    @classmethod
    def load(cls, path: str | Path) -> Dataset:
        path = Path(path)
        raw = gzip.decompress(path.read_bytes()).decode() if path.suffix == ".gz" else path.read_text()
        return cls.from_dict(json.loads(raw))


def _richness(acc: Account) -> int:
    return sum(
        v not in (None, "")
        for v in (
            acc.created_at,
            acc.followers,
            acc.following,
            acc.tweets,
            acc.likes,
            acc.location,
            acc.about,
        )
    )
