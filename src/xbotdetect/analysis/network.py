"""Follow-graph analysis: seed neighbourhoods, dense mutual-follow clusters, seed-pair overlap."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from itertools import combinations
from typing import Any

import networkx as nx

from ..dataset import Dataset
from ..util import days_between, fmt_date, iso, ramp
from .account import ORG_TYPES
from .behavior import normalize_text
from .config import AnalysisConfig
from .scoring import Signal, combine, level
from .temporal import (
    Band,
    Burst,
    Peak,
    burst_excess,
    creation_bursts,
    creation_peak,
    follower_map_bands,
    monthly_histogram,
    recent_share,
)


@dataclass
class Neighborhood:
    seed_id: str
    relation: str  # "followers" | "following"
    sampled: int
    total: int | None
    recent_share: float  # share of "young" accounts (see AnalysisConfig.young_days)
    peak: Peak
    aged_peak: Peak  # excluding fresh sign-ups (possible X onboarding)
    peak90: Peak
    bursts: list[Burst]
    burst_excess: float  # share of the sample in non-fresh creation bursts, beyond background
    bands: list[Band]
    band_share: float  # share of sample inside non-fresh bands
    botlike_share: float
    median_age_days: float | None
    monthly: dict[str, int]
    # (rank oldest-first, creation date) for plotting the follower map.
    points: list[tuple[int, datetime]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_id": self.seed_id,
            "relation": self.relation,
            "sampled": self.sampled,
            "total": self.total,
            "recent_share": round(self.recent_share, 4),
            "peak": self.peak.to_dict(),
            "aged_peak": self.aged_peak.to_dict(),
            "peak90": self.peak90.to_dict(),
            "bursts": [b.to_dict() for b in self.bursts],
            "burst_excess": round(self.burst_excess, 4),
            "bands": [b.to_dict() for b in self.bands],
            "band_share": round(self.band_share, 4),
            "botlike_share": round(self.botlike_share, 4),
            "median_age_days": self.median_age_days,
            "monthly": self.monthly,
        }


def bursts_for(dates: list[datetime | None], as_of: datetime, cfg: AnalysisConfig) -> list[Burst]:
    return creation_bursts(
        dates,
        as_of,
        window_days=cfg.burst_window_days,
        background_days=cfg.burst_background_days,
        alpha=cfg.burst_alpha,
        fresh_days=cfg.fresh_signup_days,
    )


def neighborhood(
    ds: Dataset,
    seed_id: str,
    relation: str,
    meta_scores: dict[str, float],
    cfg: AnalysisConfig,
) -> Neighborhood | None:
    ids = (ds.followers if relation == "followers" else ds.following).get(seed_id)
    if not ids:
        return None
    accs = [ds.accounts.get(i) for i in ids]
    dates_newest_first = [a.created_at if a else None for a in accs]
    dates = [d for d in dates_newest_first if d is not None]
    as_of = ds.as_of
    fresh_cut = as_of.timestamp() - cfg.fresh_signup_days * 86400
    aged = [d for d in dates if d.timestamp() < fresh_cut]
    oldest_first = list(reversed(dates_newest_first))
    bands = follower_map_bands(
        oldest_first,
        as_of,
        window_days=cfg.creation_window_days,
        min_fraction=cfg.band_min_fraction,
        min_lift=cfg.band_min_lift,
        fresh_days=cfg.fresh_signup_days,
        alpha=cfg.burst_alpha,
    )
    bursts = bursts_for(dates, as_of, cfg)
    n_known = max(len(dates), 1)
    scored = [meta_scores[i] for i in ids if i in meta_scores]
    seed = ds.accounts.get(seed_id)
    ages = [days_between(d, as_of) for d in dates]
    return Neighborhood(
        seed_id=seed_id,
        relation=relation,
        sampled=len(ids),
        total=(seed.followers if relation == "followers" else seed.following) if seed else None,
        recent_share=recent_share(dates, cfg.recent_since),
        peak=creation_peak(dates, cfg.creation_window_days),
        aged_peak=creation_peak(aged, cfg.creation_window_days),
        peak90=creation_peak(dates, 90),
        bursts=bursts,
        burst_excess=burst_excess(bursts),
        bands=bands,
        band_share=sum(b.size for b in bands if not b.fresh_signups) / n_known,
        botlike_share=(sum(s >= cfg.botlike_threshold for s in scored) / len(scored)) if scored else 0.0,
        median_age_days=round(statistics.median(ages), 1) if ages else None,
        monthly=monthly_histogram(dates),
        points=[(r, d) for r, d in enumerate(oldest_first) if d is not None],
    )


def mutual_share(ds: Dataset, seed_id: str) -> float | None:
    """Share of the (sampled) following list that follows back."""
    fol, fing = ds.followers.get(seed_id), ds.following.get(seed_id)
    if not fol or not fing:
        return None
    return len(set(fol) & set(fing)) / len(fing)


def burst_text(b: Burst) -> str:
    return (
        f"{b.count} accounts created {fmt_date(b.start)} - {fmt_date(b.end)} vs ~{b.expected:.1f} expected "
        f"from the surrounding months (p={b.p_value:.0e})"
    )


def burst_strength(nb: Neighborhood) -> float:
    hard = [b for b in nb.bursts if not b.fresh_signups]
    if not hard:
        return 0.0
    # Magnitude (share of the sample) with a floor for any large, unambiguous batch.
    return max(ramp(nb.burst_excess, 0.01, 0.10), 0.35 if max(b.count for b in hard) >= 20 else 0.0)


def seed_network_signals(
    followers: Neighborhood | None, following: Neighborhood | None, mutual: float | None, cfg: AnalysisConfig
) -> list[Signal]:
    sig: list[Signal] = []
    if followers and followers.sampled >= 50:
        nb = followers
        s = burst_strength(nb)
        if s > 0:
            hard = [b for b in nb.bursts if not b.fresh_signups]
            sig.append(
                Signal(
                    "follower_creation_burst",
                    "Followers created in batches",
                    s,
                    0.4,
                    f"{len(hard)} creation burst(s) among {nb.sampled} sampled followers, "
                    f"{nb.burst_excess:.1%} of them beyond background; largest: {burst_text(hard[0])}",
                    "network",
                )
            )
        # The newest followers of any account skew young (new users follow a lot), so the share only
        # says something about the audience when the sample covers a good part of it.
        coverage = nb.sampled / nb.total if nb.total else 0.0
        s = ramp(nb.recent_share, 0.5, 0.85) * ramp(coverage, 0.2, 0.6)
        if s > 0:
            sig.append(
                Signal(
                    "followers_mostly_new",
                    "Followers mostly young accounts",
                    s,
                    0.2,
                    f"{nb.recent_share:.0%} of sampled followers are {cfg.young_label} "
                    f"(sample covers {coverage:.0%} of followers)",
                    "network",
                )
            )
        hard_bands = [b for b in nb.bands if not b.fresh_signups]
        if hard_bands:
            s = ramp(nb.band_share, 0.05, 0.3)
            if s > 0:
                b = hard_bands[0]
                sig.append(
                    Signal(
                        "follower_map_bands",
                        "Follower-map bands",
                        s,
                        0.35,
                        f"{len(hard_bands)} band(s) of consecutive followers created in the same window; largest: "
                        f"{b.size} accounts created {fmt_date(b.created_start)} - {fmt_date(b.created_end)} "
                        f"followed back-to-back (p={b.p_value:.0e}; {nb.band_share:.0%} of sample)",
                        "network",
                    )
                )
        s = ramp(nb.botlike_share, 0.15, 0.45)
        if s > 0:
            sig.append(
                Signal(
                    "botlike_followers",
                    "Bot-like followers",
                    s,
                    0.3,
                    f"{nb.botlike_share:.0%} of sampled followers score as bot-like on metadata alone",
                    "network",
                )
            )
    if following and following.sampled >= 50:
        # Accounts following 100k+ (legacy auto-follow-back) don't choose whom they follow.
        s = burst_strength(following) * ramp(following.total or 0, 100_000, 20_000)
        if s > 0:
            hard = [b for b in following.bursts if not b.fresh_signups]
            sig.append(
                Signal(
                    "following_creation_burst",
                    "Follows batch-created accounts",
                    s,
                    0.3,
                    f"{following.burst_excess:.1%} of the accounts it follows sit in creation bursts; largest: "
                    f"{burst_text(hard[0])}",
                    "network",
                )
            )
    if mutual is not None and following and following.sampled >= 30:
        s = ramp(mutual, 0.5, 0.9)
        if s > 0:
            sig.append(
                Signal(
                    "high_reciprocity",
                    "Follow-back ring",
                    s,
                    0.15,
                    f"{mutual:.0%} of sampled followed accounts follow back",
                    "network",
                )
            )
    return sig


def build_follow_graph(ds: Dataset) -> nx.DiGraph:
    g = nx.DiGraph()
    for sid in ds.seed_ids:
        for f in ds.followers.get(sid, []):
            g.add_edge(f, sid)
        for t in ds.following.get(sid, []):
            g.add_edge(sid, t)
    for cid in ds.expanded_ids:
        for t in ds.following.get(cid, []):
            g.add_edge(cid, t)
    return g


def amplification_graph(ds: Dataset) -> nx.DiGraph:
    """A -> B when A reposted/quoted B in the collected timelines (weight = count)."""
    g = nx.DiGraph()
    for owner, posts in ds.timelines.items():
        for p in posts:
            if p.kind in ("repost", "quote") and p.target_id and p.target_id != owner:
                w = g.get_edge_data(owner, p.target_id, {}).get("weight", 0)
                g.add_edge(owner, p.target_id, weight=w + 1)
    return g


@dataclass
class Cluster:
    """A dense group of accounts. ``kind`` is "neighbours" (expanded accounts around the seeds;
    seed edges are excluded from its metrics because expansion selects seed-connected accounts)
    or "seed-group" (the analysed handles themselves)."""

    id: int
    kind: str
    members: list[str]
    # seed id -> {"mutual": n, "follows": n, "followed_by": n} ties to members
    seed_ties: dict[str, dict[str, int]]
    attached_seeds: list[str]
    density: float
    reciprocity: float
    peak30: Peak
    peak90: Peak
    recent_share: float
    mean_member_score: float
    following_jaccard: float | None
    shared_targets: list[tuple[str, int]]
    name_dup_share: float | None
    bio_dup_share: float | None
    co_amplification: float | None  # mean pairwise Jaccard of reposted posts (needs member timelines)
    verified_org_share: float
    signals: list[Signal]
    score: float
    level: str

    @property
    def size(self) -> int:
        return len(self.members)

    def attachment(self, seed_id: str) -> float:
        """0-1: how embedded a seed is in this cluster (mutual ties count double)."""
        if self.kind == "seed-group":
            return 1.0 if seed_id in self.members else 0.0
        t = self.seed_ties.get(seed_id)
        if not t or not self.size:
            return 0.0
        return ramp((2 * t["mutual"] + t["follows"] + t["followed_by"]) / (2 * self.size), 0.15, 0.6)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(
            size=self.size,
            peak30=self.peak30.to_dict(),
            peak90=self.peak90.to_dict(),
            signals=[s.to_dict() for s in self.signals],
            score=round(self.score, 4),
            density=round(self.density, 4),
            reciprocity=round(self.reciprocity, 4),
            recent_share=round(self.recent_share, 4),
            mean_member_score=round(self.mean_member_score, 4),
            following_jaccard=None if self.following_jaccard is None else round(self.following_jaccard, 4),
            name_dup_share=None if self.name_dup_share is None else round(self.name_dup_share, 4),
            bio_dup_share=None if self.bio_dup_share is None else round(self.bio_dup_share, 4),
            co_amplification=None if self.co_amplification is None else round(self.co_amplification, 4),
        )
        return d


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def mean_pairwise_jaccard(sets: list[set[str]], max_pairs: int = 5000) -> float | None:
    sets = [s for s in sets if s]
    if len(sets) < 2:
        return None
    vals = []
    for a, b in combinations(sets, 2):
        vals.append(_jaccard(a, b))
        if len(vals) >= max_pairs:
            break
    return statistics.fmean(vals)


@dataclass
class PoolBaseline:
    """Creation dates of every expanded neighbour. Expansion is biased toward new, bot-like
    accounts, so a cluster's composition only counts where it stands out from this pool."""

    dates: list[datetime]
    recent_share: float

    def share_in(self, start: datetime | None, days: float) -> float:
        if start is None or not self.dates:
            return 0.0
        end = start + timedelta(days=days)
        return sum(start <= d <= end for d in self.dates) / len(self.dates)


def cluster_signals(
    density: float,
    reciprocity: float,
    peak30: Peak,
    peak90: Peak,
    recent: float,
    mean_score: float,
    jac: float | None,
    cfg: AnalysisConfig,
    baseline: PoolBaseline | None = None,
    gate_composition: bool = True,
    coordination: list[Signal] | None = None,
) -> list[Signal]:
    """Structure (density, mutuality, shared follows) is the evidence; composition (created
    together, new, bot-like) only counts in proportion to how much structure there is."""
    sig = []
    structure = [ramp(density, 0.08, 0.4), ramp(reciprocity, 0.3, 0.8)]
    if structure[0]:
        sig.append(
            Signal(
                "density",
                "Dense follow mesh",
                structure[0],
                0.35,
                f"{density:.0%} of possible follow edges present",
                "cluster",
            )
        )
    if structure[1]:
        sig.append(
            Signal(
                "reciprocity",
                "Mutual follows",
                structure[1],
                0.2,
                f"{reciprocity:.0%} of linked pairs follow each other",
                "cluster",
            )
        )
    if jac is not None:
        s = ramp(jac, 0.05, 0.25)
        structure.append(s)
        if s:
            sig.append(
                Signal(
                    "following_overlap",
                    "Follow the same accounts",
                    s,
                    0.3,
                    f"mean pairwise Jaccard of followed accounts {jac:.2f}",
                    "cluster",
                )
            )
    sig.extend(coordination or [])
    gate = min(1.0, max(structure) / 0.5) if gate_composition else 1.0
    gate_note = f" (scaled x{gate:.2f}: weak follow structure)" if gate < 1 else ""

    s = max(ramp(peak30.share, 0.25, 0.6), ramp(peak90.share, 0.45, 0.85))
    lift_note = ""
    if s and baseline is not None:
        base = max(baseline.share_in(peak30.start, cfg.creation_window_days), 1 / max(len(baseline.dates), 1))
        lift = peak30.share / base
        s *= ramp(lift, 1.5, 4.0)
        lift_note = f", {lift:.1f}x the expanded pool's rate"
    if s * gate:
        sig.append(
            Signal(
                "creation_cohort",
                "Created together",
                s * gate,
                0.35,
                f"{peak30.share:.0%} created within {cfg.creation_window_days} days ({fmt_date(peak30.start)} - "
                f"{fmt_date(peak30.end)}){lift_note}, {peak90.share:.0%} within 90 days{gate_note}",
                "cluster",
            )
        )
    s = ramp(recent, 0.5, 0.9)
    if baseline is not None:
        s *= ramp(recent - baseline.recent_share, 0.0, 0.3)
    if s * gate:
        sig.append(
            Signal(
                "recent_cohort",
                "Young accounts",
                s * gate,
                0.2,
                f"{recent:.0%} {cfg.young_label}{gate_note}",
                "cluster",
            )
        )
    s = ramp(mean_score, 0.25, 0.55) * gate
    if s:
        sig.append(
            Signal(
                "member_scores",
                "Bot-like members",
                s,
                0.3,
                f"mean member metadata score {mean_score:.2f}{gate_note}",
                "cluster",
            )
        )
    return sig


STRUCTURE_KEYS = {"density": 0.35, "reciprocity": 0.2, "following_overlap": 0.3}
COMPOSITION_KEYS = {
    "creation_cohort": 0.35,
    "recent_cohort": 0.2,
    "member_scores": 0.3,
    "templated_profiles": 0.3,
    "co_amplification": 0.35,
}
# Normalise composition against the signals that are always measurable; templating and
# co-amplification only add on top (they need names/bios or member timelines).
CORE_COMPOSITION = {k: COMPOSITION_KEYS[k] for k in ("creation_cohort", "recent_cohort", "member_scores")}


def _max_combined(weights: dict[str, float]) -> float:
    return 1.0 - math.prod(1.0 - w for w in weights.values())


def cluster_score(sig: list[Signal], kind: str, org_share: float = 0.0) -> float:
    """Neighbour clusters need both structure (a dense follow mesh) and anomalous composition
    (created together, bot-like, templated, co-amplifying): dense real communities such as an
    agency's accounts or a friend group are structure without anomaly. The analysed seed group
    has no selection bias, so any evidence counts there."""
    if kind != "neighbours":
        return combine(sig)
    structure = combine([s for s in sig if s.key in STRUCTURE_KEYS]) / _max_combined(STRUCTURE_KEYS)
    composition = combine([s for s in sig if s.key in COMPOSITION_KEYS]) / _max_combined(CORE_COMPOSITION)
    composition *= 1.0 - org_share  # identity-verified organisations are not farms
    return min(1.0, structure) * (0.25 + 0.75 * min(1.0, composition))


def _trigrams(s: str) -> set[str]:
    s = f"  {s} "
    return {s[i : i + 3] for i in range(len(s) - 2)}


def near_duplicate_share(
    texts: list[str], threshold: float = 0.7, min_len: int = 3, max_pairs: int = 5000
) -> float | None:
    """Share of text pairs that are near-identical (character-trigram Jaccard >= threshold)."""
    items = [normalize_text(t) for t in texts]
    grams = [_trigrams(t) for t in items if len(t) >= min_len]
    if len(grams) < 3:
        return None
    pairs = dup = 0
    for a, b in combinations(grams, 2):
        pairs += 1
        if len(a & b) / len(a | b) >= threshold:
            dup += 1
        if pairs >= max_pairs:
            break
    return dup / pairs


def co_amplification(ds: Dataset, members: list[str]) -> tuple[float, int] | None:
    sets = [
        {p.target_post_id for p in ds.timelines[m] if p.kind == "repost" and p.target_post_id}
        for m in members
        if m in ds.timelines
    ]
    sets = [x for x in sets if x]
    if len(sets) < 3:
        return None
    jac = mean_pairwise_jaccard(sets)
    return (jac, len(sets)) if jac is not None else None


def coordination_signals(ds: Dataset, members: list[str]) -> tuple[list[Signal], dict[str, Any]]:
    accs = [ds.accounts[m] for m in members if m in ds.accounts]
    names = near_duplicate_share([a.name for a in accs], threshold=0.8)
    bios = near_duplicate_share([a.description for a in accs if a.description], threshold=0.6, min_len=12)
    coamp = co_amplification(ds, members)
    sig: list[Signal] = []
    s = max(ramp(names or 0, 0.15, 0.5), ramp(bios or 0, 0.1, 0.4))
    if s:
        sig.append(
            Signal(
                "templated_profiles",
                "Templated profiles",
                s,
                0.3,
                f"near-identical display names in {names or 0:.0%} and bios in {bios or 0:.0%} of member pairs",
                "cluster",
            )
        )
    if coamp:
        s = ramp(coamp[0], 0.03, 0.2)
        if s:
            sig.append(
                Signal(
                    "co_amplification",
                    "Repost the same posts",
                    s,
                    0.35,
                    f"mean pairwise overlap of reposted posts {coamp[0]:.2f} across {coamp[1]} member timelines",
                    "cluster",
                )
            )
    return sig, {
        "name_dup_share": names,
        "bio_dup_share": bios,
        "co_amplification": coamp[0] if coamp else None,
    }


def _measure(
    ds: Dataset,
    g: nx.DiGraph,
    members: list[str],
    kind: str,
    meta_scores: dict[str, float],
    cfg: AnalysisConfig,
    baseline: PoolBaseline | None = None,
) -> Cluster:
    mem = sorted(members)
    n = len(mem)
    mset = set(mem)
    sub = g.subgraph(mem)
    edges = sum(1 for u, v in sub.edges() if u != v)
    density = edges / (n * (n - 1)) if n > 1 else 0.0
    linked = mutual = 0
    for a, b in combinations(mem, 2):
        ab, ba = sub.has_edge(a, b), sub.has_edge(b, a)
        if ab or ba:
            linked += 1
            mutual += ab and ba
    recip = mutual / linked if linked else 0.0
    dates = [ds.accounts[m].created_at for m in mem if m in ds.accounts]
    p30 = creation_peak(dates, cfg.creation_window_days)
    p90 = creation_peak(dates, 90)
    rec = recent_share(dates, cfg.recent_since)
    mean_score = statistics.fmean(meta_scores.get(m, 0.0) for m in mem)
    follow_sets = [set(ds.following[m]) for m in mem if m in ds.following]
    jac = mean_pairwise_jaccard(follow_sets)
    targets: Counter[str] = Counter()
    for s in follow_sets:
        targets.update(t for t in s if t not in mset)
    need = max(2, math.ceil(0.5 * n))
    shared = [(t, c) for t, c in targets.most_common(15) if c >= need]
    ties: dict[str, dict[str, int]] = {}
    if kind == "neighbours":
        for sid in ds.seed_ids:
            fol = sum(g.has_edge(sid, m) for m in mem)
            by = sum(g.has_edge(m, sid) for m in mem)
            mut = sum(g.has_edge(sid, m) and g.has_edge(m, sid) for m in mem)
            if fol or by:
                ties[sid] = {"mutual": mut, "follows": fol - mut, "followed_by": by - mut}
    coord, coord_metrics = coordination_signals(ds, mem)
    sig = cluster_signals(
        density,
        recip,
        p30,
        p90,
        rec,
        mean_score,
        jac,
        cfg,
        baseline,
        gate_composition=kind == "neighbours",
        coordination=coord,
    )
    orgs = [ds.accounts[m] for m in mem if m in ds.accounts and ds.accounts[m].verified_type in ORG_TYPES]
    org_share = len(orgs) / n
    # Shares over 3-5 accounts are coarse evidence; full weight from 6 members up.
    score = cluster_score(sig, kind, org_share) * min(1.0, (n - 1) / 5)
    c = Cluster(
        id=0,
        kind=kind,
        members=mem,
        seed_ties=ties,
        attached_seeds=[],
        density=density,
        reciprocity=recip,
        peak30=p30,
        peak90=p90,
        recent_share=rec,
        mean_member_score=mean_score,
        following_jaccard=jac,
        **coord_metrics,
        verified_org_share=org_share,
        shared_targets=shared,
        signals=sig,
        score=score,
        level=level(score),
    )
    c.attached_seeds = [s for s in ds.seed_ids if c.attachment(s) > 0] if kind == "neighbours" else list(mem)
    return c


def _directed_density(g: nx.DiGraph, a: set[str], b: set[str] | None = None) -> float:
    if b is None:
        n = len(a)
        return sum(1 for u, v in g.subgraph(a).edges() if u != v) / (n * (n - 1)) if n > 1 else 0.0
    cross = sum(g.has_edge(u, v) + g.has_edge(v, u) for u in a for v in b)
    return cross / (2 * len(a) * len(b))


def _merge_dense(g: nx.DiGraph, comms: list[set[str]]) -> list[set[str]]:
    """Louvain can split one near-clique into pieces; re-join communities whose mutual
    linkage is as dense as their insides."""
    comms = [c for c in comms if c]
    merged = True
    while merged and len(comms) > 1:
        merged = False
        dens = [_directed_density(g, c) for c in comms]
        for i in range(len(comms)):
            for j in range(i + 1, len(comms)):
                inner = min(dens[i], dens[j])
                if inner > 0 and _directed_density(g, comms[i], comms[j]) >= 0.6 * inner:
                    comms[i] |= comms.pop(j)
                    merged = True
                    break
            if merged:
                break
    return comms


def _prune_periphery(h: nx.Graph, members: set[str], frac: float = 0.3) -> set[str]:
    """Drop members only loosely attached to the community (e.g. accounts the group follows)."""
    if len(members) < 4:
        return members
    deg = {m: sum(d.get("weight", 1) for n, d in h[m].items() if n in members) for m in members}
    cut = frac * statistics.median(deg.values())
    return {m for m in members if deg[m] >= cut}


def detect_clusters(
    ds: Dataset, g: nx.DiGraph, meta_scores: dict[str, float], cfg: AnalysisConfig
) -> list[Cluster]:
    """Louvain communities among expanded neighbours (seeds excluded), plus the seed group itself
    when 3+ handles were analysed together."""
    seeds = set(ds.seed_ids)
    core = sorted(set(ds.expanded_ids) - seeds)
    h = nx.Graph()
    h.add_nodes_from(core)
    for u, v in sorted(g.subgraph(core).edges()):
        if u == v:
            continue
        w = h.get_edge_data(u, v, {}).get("weight", 0)
        h.add_edge(u, v, weight=w + 1)
    h.remove_nodes_from([n for n in list(h.nodes()) if h.degree(n) == 0])
    clusters: list[Cluster] = []
    pool_dates = sorted(
        ds.accounts[i].created_at for i in core if i in ds.accounts and ds.accounts[i].created_at
    )
    baseline = PoolBaseline(pool_dates, recent_share(pool_dates, cfg.recent_since)) if pool_dates else None
    if h.number_of_edges():
        comms = nx.community.louvain_communities(
            h, weight="weight", resolution=cfg.louvain_resolution, seed=cfg.seed
        )
        for members in _merge_dense(g, [set(c) for c in comms]):
            members = _prune_periphery(h, members)
            if len(members) >= cfg.min_cluster_size:
                clusters.append(_measure(ds, g, list(members), "neighbours", meta_scores, cfg, baseline))
    observed_seeds = [s for s in ds.seed_ids if s in ds.following or s in ds.followers]
    if len(observed_seeds) >= max(3, cfg.min_cluster_size):
        clusters.append(_measure(ds, g, observed_seeds, "seed-group", meta_scores, cfg))
    clusters.sort(key=lambda c: (c.score, c.size), reverse=True)
    for i, c in enumerate(clusters, 1):
        c.id = i
    return clusters


@dataclass
class PairOverlap:
    a: str
    b: str
    follower_jaccard: float | None
    following_jaccard: float | None
    shared_followers: int
    shared_following: int
    a_follows_b: bool | None
    b_follows_a: bool | None
    created_days_apart: float | None
    shared_amplified_accounts: int
    shared_reposted_posts: int
    a_amplifies_b: int
    b_amplifies_a: int

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("follower_jaccard", "following_jaccard", "created_days_apart"):
            if d[k] is not None:
                d[k] = round(d[k], 4)
        return d


def _follows(ds: Dataset, x: str, y: str) -> bool | None:
    known = None
    if x in ds.following:
        if y in ds.following[x]:
            return True
        known = False
    if y in ds.followers:
        if x in ds.followers[y]:
            return True
        known = False
    return known


def pair_overlaps(ds: Dataset) -> list[PairOverlap]:
    out = []
    amp_targets = {
        s: Counter(
            p.target_id for p in ds.timelines.get(s, []) if p.kind in ("repost", "quote") and p.target_id
        )
        for s in ds.seed_ids
    }
    reposted = {
        s: {p.target_post_id for p in ds.timelines.get(s, []) if p.kind == "repost" and p.target_post_id}
        for s in ds.seed_ids
    }
    for a, b in combinations(ds.seed_ids, 2):
        fa, fb = set(ds.followers.get(a, [])), set(ds.followers.get(b, []))
        ga, gb = set(ds.following.get(a, [])), set(ds.following.get(b, []))
        aa, ab = ds.accounts.get(a), ds.accounts.get(b)
        apart = None
        if aa and ab and aa.created_at and ab.created_at:
            apart = abs((aa.created_at - ab.created_at).total_seconds()) / 86400
        out.append(
            PairOverlap(
                a=a,
                b=b,
                follower_jaccard=_jaccard(fa, fb) if fa and fb else None,
                following_jaccard=_jaccard(ga, gb) if ga and gb else None,
                shared_followers=len(fa & fb),
                shared_following=len(ga & gb),
                a_follows_b=_follows(ds, a, b),
                b_follows_a=_follows(ds, b, a),
                created_days_apart=apart,
                shared_amplified_accounts=len(set(amp_targets[a]) & set(amp_targets[b])),
                shared_reposted_posts=len(reposted[a] & reposted[b]),
                a_amplifies_b=amp_targets[a].get(b, 0),
                b_amplifies_a=amp_targets[b].get(a, 0),
            )
        )
    return out


def group_creation_summary(ds: Dataset, cfg: AnalysisConfig) -> dict[str, Any] | None:
    dates = [a.created_at for a in ds.seeds if a.created_at]
    if len(dates) < 2:
        return None
    p = creation_peak(dates, cfg.creation_window_days)
    return {
        "seeds": len(dates),
        "earliest": iso(min(dates)),
        "latest": iso(max(dates)),
        "recent_share": round(recent_share(dates, cfg.recent_since), 3),
        "peak": p.to_dict(),
    }
