"""Plain-text (terminal) and Markdown summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..analysis.scoring import active
from ..util import fmt_date, fmt_int

if TYPE_CHECKING:
    from ..analysis import AccountResult, Report


def _facts(report: Report, r: AccountResult) -> str:
    acc = report.dataset.accounts[r.id]
    m = r.metrics
    bits = [
        f"created {fmt_date(acc.created_at)}",
        f"{fmt_int(acc.followers)} followers / {fmt_int(acc.following)} following",
        f"{fmt_int(acc.tweets)} posts"
        + (f" ({m['tweets_per_day']:.0f}/day)" if "tweets_per_day" in m else ""),
    ]
    if acc.about and acc.about.based_in:
        bits.append(f"based in {acc.about.based_in}")
    return " · ".join(bits)


def _nb_line(report: Report, sid: str, rel: str) -> str | None:
    nb = report.neighborhoods.get(sid, {}).get(rel)
    if not nb:
        return None
    cfg = report.config
    total = f" of {fmt_int(nb.total)}" if nb.total else ""
    return (
        f"{rel} ({nb.sampled}{total} sampled): {nb.recent_share:.0%} {cfg.young_label}, "
        f"{len([b for b in nb.bursts if not b.fresh_signups])} creation burst(s) holding {nb.burst_excess:.1%} beyond "
        f"background, {len(nb.bands)} follower-map band(s), {nb.botlike_share:.0%} bot-like"
    )


def render_text(report: Report, top: int = 15) -> str:
    ds = report.dataset
    L: list[str] = []
    L.append(f"X bot-cluster analysis · as of {fmt_date(ds.as_of)} · source {ds.meta.get('source', '?')}")
    L.append("")
    if report.seeds:
        L.append("SEEDS")
    for r in report.seeds:
        clus = f"  [cluster #{r.cluster_id}]" if r.cluster_id else ""
        L.append(f"  @{r.handle:<20} {r.score * 100:5.0f}  {r.level.upper()}{clus}")
        L.append(f"      {_facts(report, r)}")
        for s in active(r.signals):
            L.append(f"      +{s.contribution:.2f} {s.label}: {s.detail}")
        for rel in ("followers", "following"):
            line = _nb_line(report, r.id, rel)
            if line:
                L.append(f"      · {line}")
        b = report.behavior.get(r.id)
        if b and b.get("top_amplified"):
            L.append("      · amplifies: " + ", ".join(f"@{h} ({n})" for h, n in b["top_amplified"][:5]))
    L.append("")
    if not report.seeds:
        pass
    elif report.clusters:
        L.append("FOLLOW CLUSTERS (density measured among neighbours, excluding seed edges)")
        for c in report.clusters:
            kind = "seed group" if c.kind == "seed-group" else "neighbours"
            L.append(
                f"  #{c.id} {c.score * 100:3.0f} {c.level:<9} {kind}: {c.size} accounts · density {c.density:.0%} · "
                f"mutual {c.reciprocity:.0%} · {c.peak30.share:.0%} created within {report.config.creation_window_days}d "
                f"({fmt_date(c.peak30.start)}..{fmt_date(c.peak30.end)}) · {c.recent_share:.0%} young"
            )
            L.append(
                "      members: "
                + " ".join(f"@{report.handle(m)}" for m in c.members[:25])
                + (" ..." if c.size > 25 else "")
            )
            ties = seed_ties_text(report, c)
            if ties:
                L.append("      seed ties: " + ties)
            if c.shared_targets:
                L.append(
                    "      jointly follow: "
                    + ", ".join(f"@{report.handle(t)} ({n})" for t, n in c.shared_targets[:6])
                )
    elif ds.expanded_ids:
        L.append("FOLLOW CLUSTERS: none of 3+ accounts among expanded neighbours")
    else:
        L.append("FOLLOW CLUSTERS: not measured (no 2nd-degree expansion; use --expand N)")
    if report.informative_pairs:
        L.append("")
        L.append("SEED OVERLAP")
        for p in report.informative_pairs:
            fj = "-" if p.follower_jaccard is None else f"{p.follower_jaccard:.3f}"
            gj = "-" if p.following_jaccard is None else f"{p.following_jaccard:.3f}"
            apart = "?" if p.created_days_apart is None else f"{p.created_days_apart:.0f}"
            L.append(
                f"  @{report.handle(p.a)} / @{report.handle(p.b)}: followers J={fj} ({p.shared_followers} shared), "
                f"following J={gj} ({p.shared_following} shared), follows {_yn(p.a_follows_b)}/{_yn(p.b_follows_a)}, "
                f"created {apart} days apart, {p.shared_reposted_posts} reposted posts in common"
            )
    for e in report.engagements:
        L.append("")
        L.append(f"ENGAGEMENT on post {e.tweet_id}: {e.score * 100:.0f} {e.level.upper()}")
        L.append(
            f"  {e.engagers} engagers ({e.reposters} reposters, {e.repliers} repliers) · {e.recent_share:.0%} young · "
            f"densest creation window {e.peak.share:.0%} · {e.botlike_share:.0%} bot-like"
        )
        for s in active(e.signals):
            L.append(f"      +{s.contribution:.2f} {s.label}: {s.detail}")
    ranked = [r for r in report.ranked() if r.score >= 0.3][:top]
    if ranked:
        L.append("")
        L.append("MOST SUSPICIOUS NEIGHBOURS")
        for r in ranked:
            sig = "; ".join(s.label for s in active(r.signals)[:3])
            clus = f" #{r.cluster_id}" if r.cluster_id else ""
            L.append(
                f"  @{r.handle:<20} {r.score * 100:3.0f} {r.level:<9}{clus:<4} [{', '.join(r.roles)}] {sig}"
            )
    if ds.errors:
        L.append("")
        L.append(f"{len(ds.errors)} collection warning(s); see the JSON/HTML report.")
    L.append("")
    L.append("Scores are suspicion indicators, not proof. Review context before drawing conclusions.")
    return "\n".join(L)


def seed_ties_text(report: Report, c) -> str:
    return "; ".join(
        f"@{report.handle(sid)} mutual with {t['mutual']}, follows {t['follows']}, followed by {t['followed_by']} of {c.size}"
        for sid, t in c.seed_ties.items()
    )


def _yn(v: bool | None) -> str:
    return "yes" if v else ("no" if v is False else "?")


def render_markdown(report: Report, top: int = 40) -> str:
    ds = report.dataset
    cfg = report.config
    L: list[str] = []
    L.append("# Bot-cluster report: " + ", ".join(f"@{s.handle}" for s in report.seeds))
    L.append("")
    opts = ds.meta.get("options", {})
    L.append(
        f"As of {fmt_date(ds.as_of)} · source `{ds.meta.get('source', '?')}` · up to {opts.get('followers', '?')} followers / "
        f"{opts.get('following', '?')} following per seed · {len(ds.expanded_ids)} neighbours expanded · {len(ds.accounts):,} accounts seen"
    )
    L.append("")
    L.append("## Seeds")
    for r in report.seeds:
        L.append("")
        L.append(
            f"### @{r.handle} - {r.score * 100:.0f}/100 ({r.level})"
            + (f", cluster #{r.cluster_id}" if r.cluster_id else "")
        )
        L.append("")
        L.append(_facts(report, r))
        L.append("")
        for s in active(r.signals):
            L.append(f"- **{s.label}** (+{s.contribution:.2f}): {s.detail}")
        for rel in ("followers", "following"):
            line = _nb_line(report, r.id, rel)
            if line:
                L.append(f"- _{line}_")
    L.append("")
    L.append("## Follow clusters")
    L.append("")
    if report.clusters:
        L.append(
            f"| # | Score | Kind | Size | Density | Mutual | Created within {cfg.creation_window_days}d | Young "
            "| Seed ties | Members |"
        )
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for c in report.clusters:
            mem = " ".join(f"@{report.handle(m)}" for m in c.members[:20]) + (" ..." if c.size > 20 else "")
            L.append(
                f"| {c.id} | {c.score * 100:.0f} ({c.level}) | {c.kind} | {c.size} | {c.density:.0%} | {c.reciprocity:.0%} | "
                f"{c.peak30.share:.0%} | {c.recent_share:.0%} | {seed_ties_text(report, c) or '-'} | {mem} |"
            )
    else:
        L.append("_No clusters measured or found._")
    if report.informative_pairs:
        L.append("")
        L.append("## Seed overlap")
        L.append("")
        L.append(
            "| Pair | Follower J (shared) | Following J (shared) | Follows a→b / b→a | Created days apart |"
        )
        L.append("|---|---|---|---|---|")
        for p in report.informative_pairs:
            fj = "-" if p.follower_jaccard is None else f"{p.follower_jaccard:.3f}"
            gj = "-" if p.following_jaccard is None else f"{p.following_jaccard:.3f}"
            apart = "-" if p.created_days_apart is None else f"{p.created_days_apart:.0f}"
            L.append(
                f"| @{report.handle(p.a)} / @{report.handle(p.b)} | {fj} ({p.shared_followers}) | {gj} ({p.shared_following}) | "
                f"{_yn(p.a_follows_b)} / {_yn(p.b_follows_a)} | {apart} |"
            )
    for e in report.engagements:
        L.append("")
        L.append(f"## Engagement on post {e.tweet_id}: {e.score * 100:.0f} ({e.level})")
        L.append("")
        for s in active(e.signals):
            L.append(f"- **{s.label}**: {s.detail}")
    ranked = [r for r in report.ranked() if r.score >= 0.3][:top]
    if ranked:
        L.append("")
        L.append("## Most suspicious neighbours")
        L.append("")
        L.append("| Account | Score | Seen as | Created | Followers | Following | Cluster | Top signals |")
        L.append("|---|---|---|---|---|---|---|---|")
        for r in ranked:
            acc = ds.accounts[r.id]
            sig = "; ".join(s.label for s in active(r.signals)[:3])
            L.append(
                f"| @{r.handle} | {r.score * 100:.0f} ({r.level}) | {', '.join(r.roles)} | {fmt_date(acc.created_at)} | "
                f"{fmt_int(acc.followers)} | {fmt_int(acc.following)} | {r.cluster_id or ''} | {sig} |"
            )
    L.append("")
    L.append("## Caveats")
    L.append("")
    L.extend(f"- {c}" for c in report.caveats)
    return "\n".join(L) + "\n"
