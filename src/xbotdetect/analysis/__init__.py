"""Turn a collected :class:`~xbotdetect.dataset.Dataset` into a scored :class:`Report`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from ..dataset import Dataset
from ..util import iso
from .account import account_metrics, account_signals
from .behavior import behavior_signals, timeline_metrics
from .config import AnalysisConfig
from .engagement import EngagementResult, analyze_engagement
from .network import (
    Cluster,
    Neighborhood,
    PairOverlap,
    amplification_graph,
    build_follow_graph,
    detect_clusters,
    group_creation_summary,
    mutual_share,
    neighborhood,
    pair_overlaps,
    seed_network_signals,
)
from .scoring import Signal, active, combine, level

__all__ = ["AnalysisConfig", "AccountResult", "Report", "analyze"]

CAVEATS = [
    "Scores are suspicion indicators, not proof of automation. Fast-growing political or news accounts, "
    "follow-back communities and genuine new users can trip individual signals.",
    "Follower/following lists are samples (most recent first); densities and overlaps are lower bounds.",
    "Brand-new accounts naturally follow popular accounts during X onboarding; creation spikes made only of "
    "very fresh sign-ups are discounted.",
    "Network structure (who follows whom, created when) is harder to fake than text; weigh cluster findings "
    "above single-account metadata.",
]


@dataclass
class AccountResult:
    id: str
    handle: str
    roles: list[str]
    score: float
    level: str
    signals: list[Signal]
    metrics: dict[str, Any]
    meta_score: float  # metadata (+behaviour) only, before network context
    cluster_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "handle": self.handle,
            "roles": self.roles,
            "score": round(self.score, 4),
            "level": self.level,
            "meta_score": round(self.meta_score, 4),
            "cluster_id": self.cluster_id,
            "signals": [s.to_dict() for s in active(self.signals)],
            "metrics": self.metrics,
        }


@dataclass
class Report:
    dataset: Dataset
    config: AnalysisConfig
    accounts: dict[str, AccountResult]
    neighborhoods: dict[str, dict[str, Neighborhood]]
    behavior: dict[str, dict[str, Any]]
    clusters: list[Cluster]
    pairs: list[PairOverlap]
    group: dict[str, Any] | None
    engagements: list[EngagementResult]
    follow_graph: nx.DiGraph
    amp_graph: nx.DiGraph
    caveats: list[str] = field(default_factory=lambda: list(CAVEATS))

    @property
    def seeds(self) -> list[AccountResult]:
        return [self.accounts[i] for i in self.dataset.seed_ids if i in self.accounts]

    def handle(self, account_id: str) -> str:
        acc = self.dataset.accounts.get(account_id)
        return acc.handle if acc else account_id

    def ranked(
        self, role: str | None = None, exclude_seeds: bool = True, direct_only: bool = True
    ) -> list[AccountResult]:
        """Accounts by score. ``direct_only`` skips accounts only seen 2nd-hand (followed by an
        expanded neighbour) so the list stays about the seeds' own neighbourhood."""
        indirect = {"2nd-degree", "other"}
        rows = [
            r
            for r in self.accounts.values()
            if (role is None or role in r.roles)
            and not (exclude_seeds and "seed" in r.roles)
            and not (direct_only and set(r.roles) <= indirect)
        ]
        return sorted(rows, key=lambda r: r.score, reverse=True)

    def to_dict(self, include_all_accounts: bool = True) -> dict[str, Any]:
        ds = self.dataset
        accounts = self.accounts.values() if include_all_accounts else self.seeds
        return {
            "as_of": iso(ds.as_of),
            "config": self.config.to_dict(),
            "collection": ds.meta,
            "seeds": [
                {**self.accounts[i].to_dict(), "profile": ds.accounts[i].to_dict()}
                for i in ds.seed_ids
                if i in self.accounts
            ],
            "neighborhoods": {
                sid: {rel: nb.to_dict() for rel, nb in rels.items()}
                for sid, rels in self.neighborhoods.items()
            },
            "behavior": self.behavior,
            "clusters": [c.to_dict() for c in self.clusters],
            "pairs": [p.to_dict() for p in self.pairs],
            "group": self.group,
            "engagements": [e.to_dict() for e in self.engagements],
            "accounts": [
                {**r.to_dict(), "profile": ds.accounts[r.id].to_dict()}
                for r in sorted(accounts, key=lambda r: r.score, reverse=True)
            ],
            "caveats": self.caveats,
            "errors": ds.errors,
        }


def _roles(ds: Dataset) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}

    def add(i: str, r: str) -> None:
        lst = roles.setdefault(i, [])
        if r not in lst:
            lst.append(r)

    for sid in ds.seed_ids:
        add(sid, "seed")
        for i in ds.followers.get(sid, []):
            add(i, "follower")
        for i in ds.following.get(sid, []):
            add(i, "following")
        for p in ds.timelines.get(sid, []):
            if p.target_id and p.kind in ("repost", "quote"):
                add(p.target_id, "amplified")
    for cid in ds.expanded_ids:
        add(cid, "expanded")
    for cid in ds.expanded_ids:
        for i in ds.following.get(cid, []):
            if i not in roles:
                add(i, "2nd-degree")
    for eng in ds.engagements.values():
        for i in eng.reposter_ids:
            add(i, "engager")
        for p in eng.replies:
            add(p.author_id, "engager")
    for i in ds.accounts:
        roles.setdefault(i, ["other"])
    return roles


def analyze(ds: Dataset, cfg: AnalysisConfig | None = None) -> Report:
    cfg = cfg or AnalysisConfig()
    as_of = ds.as_of

    # 1. Metadata + behaviour signals for every account we have a record of.
    base: dict[str, list[Signal]] = {}
    behavior: dict[str, dict[str, Any]] = {}
    for aid, acc in ds.accounts.items():
        sig = account_signals(acc, as_of, cfg)
        posts = ds.timelines.get(aid)
        if posts:
            m = timeline_metrics(acc, posts)
            behavior[aid] = m
            sig += behavior_signals(m)
        base[aid] = sig
    meta_scores = {aid: combine(sig) for aid, sig in base.items()}

    # 2. Seed neighbourhoods (creation-date clustering, follower-map bands).
    neighborhoods: dict[str, dict[str, Neighborhood]] = {}
    seed_sig: dict[str, list[Signal]] = {}
    for sid in ds.seed_ids:
        rels = {}
        for rel in ("followers", "following"):
            nb = neighborhood(ds, sid, rel, meta_scores, cfg)
            if nb:
                rels[rel] = nb
        neighborhoods[sid] = rels
        seed_sig[sid] = seed_network_signals(
            rels.get("followers"), rels.get("following"), mutual_share(ds, sid), cfg
        )

    # 3. Graph clusters among expanded neighbours (+ the seed group itself).
    g = build_follow_graph(ds)
    clusters = detect_clusters(ds, g, meta_scores, cfg)
    membership: dict[str, Cluster] = {}
    for c in clusters:
        if c.kind == "neighbours":
            for m in c.members:
                membership[m] = c
    seed_cluster: dict[str, tuple[Cluster, Signal | None]] = {}
    for sid in ds.seed_ids:
        best: tuple[float, Cluster] | None = None
        for c in clusters:
            a = c.attachment(sid)
            if a > 0 and (best is None or a * c.score > best[0]):
                best = (a * c.score, c)
        if best is None:
            continue
        c = best[1]
        sig = None
        if c.score >= cfg.cluster_flag_threshold:
            if c.kind == "seed-group":
                detail = f"the analysed handles form cluster #{c.id} ({c.size} accounts, score {c.score:.2f})"
            else:
                t = c.seed_ties[sid]
                detail = (
                    f"tied to {t['mutual']} mutual / {t['follows']} followed / {t['followed_by']} following members "
                    f"of cluster #{c.id} ({c.size} accounts, score {c.score:.2f})"
                )
            sig = Signal(
                "in_suspicious_cluster", "Tied into suspicious cluster", best[0], 0.45, detail, "cluster"
            )
        seed_cluster[sid] = (c, sig)

    # 4. Final per-account scores.
    roles = _roles(ds)
    results: dict[str, AccountResult] = {}
    for aid, acc in ds.accounts.items():
        sig = list(base.get(aid, [])) + seed_sig.get(aid, [])
        c = membership.get(aid)
        if aid in seed_cluster:
            tied, extra = seed_cluster[aid]
            c = tied if extra else None  # only label a seed with a cluster when the tie counted
            if extra:
                sig.append(extra)
        elif c is not None and c.score >= cfg.cluster_flag_threshold:
            sig.append(
                Signal(
                    "in_suspicious_cluster",
                    "In suspicious cluster",
                    c.score,
                    0.45,
                    f"member of cluster #{c.id} ({c.size} accounts, cluster score {c.score:.2f})",
                    "cluster",
                )
            )
        score = combine(sig)
        results[aid] = AccountResult(
            id=aid,
            handle=acc.handle,
            roles=roles.get(aid, ["other"]),
            score=score,
            level=level(score),
            signals=sig,
            metrics=account_metrics(acc, as_of),
            meta_score=meta_scores.get(aid, 0.0),
            cluster_id=c.id if c else None,
        )
    for sid in ds.seed_ids:
        if sid in results:
            m = mutual_share(ds, sid)
            if m is not None:
                results[sid].metrics["mutual_share_sampled"] = round(m, 3)

    engagements = [analyze_engagement(ds, e, meta_scores, cfg) for e in ds.engagements.values()]
    return Report(
        dataset=ds,
        config=cfg,
        accounts=results,
        neighborhoods=neighborhoods,
        behavior=behavior,
        clusters=clusters,
        pairs=pair_overlaps(ds),
        group=group_creation_summary(ds, cfg),
        engagements=engagements,
        follow_graph=g,
        amp_graph=amplification_graph(ds),
    )
