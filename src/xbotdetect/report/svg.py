"""Dependency-free inline SVG charts for the HTML report (colours come from CSS custom properties)."""

from __future__ import annotations

import html
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import networkx as nx

from ..util import fmt_date, fmt_int

if TYPE_CHECKING:
    from ..analysis import Report
    from ..analysis.network import Neighborhood


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _year_ticks(lo: datetime, hi: datetime, max_ticks: int = 8) -> list[datetime]:
    years = list(range(lo.year, hi.year + 2))
    step = max(1, math.ceil(len(years) / max_ticks))
    return [
        datetime(y, 1, 1, tzinfo=timezone.utc)
        for y in years[::step]
        if lo <= datetime(y, 1, 1, tzinfo=timezone.utc) <= hi
    ]


def follower_map(
    nb: Neighborhood, as_of: datetime, recent_since: datetime, width: int = 760, height: int = 300
) -> str:
    """Creation date (y) vs follow order (x, oldest follow on the left)."""
    pts = nb.points
    if len(pts) < 10:
        return ""
    ml, mr, mt, mb = 52, 12, 14, 34
    pw, ph = width - ml - mr, height - mt - mb
    n = max(p[0] for p in pts) + 1
    lo = min(d for _, d in pts)
    lo = datetime(lo.year, 1, 1, tzinfo=timezone.utc)
    hi = as_of
    span = (hi - lo).total_seconds() or 1.0

    def x(rank: float) -> float:
        return ml + (rank + 0.5) / n * pw

    def y(d: datetime) -> float:
        return mt + ph - (d - lo).total_seconds() / span * ph

    out = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Follower map: account creation date by follow order for {nb.sampled} sampled {nb.relation}">'
    ]
    for t in _year_ticks(lo, hi):
        yy = y(t)
        out.append(f'<line class="grid" x1="{ml}" x2="{ml + pw}" y1="{yy:.1f}" y2="{yy:.1f}"/>')
        out.append(f'<text class="tick" x="{ml - 8}" y="{yy + 4:.1f}" text-anchor="end">{t.year}</text>')
    rs = y(recent_since)
    ref_label = ""
    if mt <= rs <= mt + ph:
        out.append(f'<line class="ref" x1="{ml}" x2="{ml + pw}" y1="{rs:.1f}" y2="{rs:.1f}"/>')
        ref_label = (
            f'<text class="tick halo" x="{ml + pw}" y="{rs - 5:.1f}" text-anchor="end">'
            f"new wave ({fmt_date(recent_since)})</text>"
        )
    for b in nb.bands:
        bx0, bx1 = x(b.rank_start - 0.5), x(b.rank_end + 0.5)
        by0, by1 = y(b.created_end), y(b.created_start)
        cls = "band fresh" if b.fresh_signups else "band"
        tip = (
            f"Band: {b.size} consecutive {nb.relation} created {fmt_date(b.created_start)} - {fmt_date(b.created_end)}"
            f" ({b.density:.0%} of the run, {b.lift:.1f}x the base rate)"
            + (" - fresh sign-ups, may be X onboarding" if b.fresh_signups else "")
        )
        out.append(
            f'<rect class="{cls}" x="{bx0:.1f}" y="{by0 - 3:.1f}" width="{max(bx1 - bx0, 2):.1f}" '
            f'height="{max(by1 - by0, 2) + 6:.1f}" rx="3"><title>{esc(tip)}</title></rect>'
        )
    for rank, d in pts:
        cls = "pt new" if d >= recent_since else "pt"
        out.append(
            f'<circle class="{cls}" cx="{x(rank):.1f}" cy="{y(d):.1f}" r="2.6">'
            f"<title>#{rank + 1} by follow order - created {fmt_date(d)}</title></circle>"
        )
    out.append(ref_label)  # after the dots so it stays readable
    out.append(f'<line class="axis" x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}"/>')
    out.append(f'<text class="tick" x="{ml}" y="{height - 10}">oldest follow in sample</text>')
    out.append(
        f'<text class="tick" x="{ml + pw}" y="{height - 10}" text-anchor="end">most recent follow</text>'
    )
    out.append("</svg>")
    return "".join(out)


def creation_histogram(
    monthly: dict[str, int],
    recent_since: datetime,
    as_of: datetime,
    width: int = 760,
    height: int = 180,
    label: str = "",
) -> str:
    if not monthly:
        return ""
    first = min(monthly)
    y0, m0 = int(first[:4]), int(first[5:7])
    months: list[str] = []
    yy, mm = y0, m0
    end = (as_of.year, as_of.month)
    while (yy, mm) <= end:
        months.append(f"{yy:04d}-{mm:02d}")
        mm += 1
        if mm == 13:
            yy, mm = yy + 1, 1
    ml, mr, mt, mb = 52, 12, 10, 26
    pw, ph = width - ml - mr, height - mt - mb
    top = max(monthly.values())
    slot = pw / len(months)
    bw = max(1.0, min(24.0, slot - 2))
    out = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label or "Accounts created per month")}">'
    ]
    for frac in (0.5, 1.0):
        gy = mt + ph - frac * ph
        out.append(f'<line class="grid" x1="{ml}" x2="{ml + pw}" y1="{gy:.1f}" y2="{gy:.1f}"/>')
        out.append(
            f'<text class="tick" x="{ml - 8}" y="{gy + 4:.1f}" text-anchor="end">{fmt_int(top * frac)}</text>'
        )
    rs_key = recent_since.strftime("%Y-%m")
    for i, key in enumerate(months):
        v = monthly.get(key, 0)
        if key == rs_key:
            rx = ml + i * slot
            out.append(f'<line class="ref" x1="{rx:.1f}" x2="{rx:.1f}" y1="{mt}" y2="{mt + ph}"/>')
        if not v:
            continue
        h = max(v / top * ph, 1.5)
        bx = ml + i * slot + (slot - bw) / 2
        by = mt + ph - h
        r = min(4.0, bw / 2, h)
        cls = "bar new" if key >= rs_key else "bar"
        # Rounded data-end, square at the baseline.
        path = (
            f"M{bx:.1f},{mt + ph:.1f} V{by + r:.1f} Q{bx:.1f},{by:.1f} {bx + r:.1f},{by:.1f} "
            f"H{bx + bw - r:.1f} Q{bx + bw:.1f},{by:.1f} {bx + bw:.1f},{by + r:.1f} V{mt + ph:.1f} Z"
        )
        out.append(f'<path class="{cls}" d="{path}"><title>{key}: {v} accounts created</title></path>')
    out.append(f'<line class="axis" x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}"/>')
    for i, key in enumerate(months):
        if key.endswith("-01") and (int(key[:4]) % max(1, math.ceil(len(months) / 12 / 8)) == 0):
            out.append(
                f'<text class="tick" x="{ml + i * slot:.1f}" y="{height - 8}" text-anchor="middle">{key[:4]}</text>'
            )
    out.append("</svg>")
    return "".join(out)


def network(report: Report, width: int = 760, height: int = 520) -> str:
    """Seeds + expanded neighbours, edges = observed follows between them. Seed edges are drawn
    faintly and barely pull the layout: every expanded account is seed-connected by construction."""
    ds = report.dataset
    seeds = set(ds.seed_ids)
    core = seeds | set(ds.expanded_ids)
    g = nx.Graph()
    for u, v in sorted(report.follow_graph.subgraph(core).edges()):
        if u == v or g.has_edge(u, v):
            continue
        mutual = report.follow_graph.has_edge(v, u)
        seed_edge = u in seeds or v in seeds
        g.add_edge(
            u, v, mutual=mutual, seed=seed_edge, weight=(0.15 if seed_edge else 1.0) * (2 if mutual else 1)
        )
    if g.number_of_nodes() < 3:
        return ""
    pos = nx.spring_layout(g, seed=report.config.seed, iterations=300, weight="weight")
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    pad = 44
    sx = (width - 2 * pad) / ((max(xs) - min(xs)) or 1)
    sy = (height - 2 * pad) / ((max(ys) - min(ys)) or 1)

    def px(n: str) -> tuple[float, float]:
        return pad + (pos[n][0] - min(xs)) * sx, pad + (pos[n][1] - min(ys)) * sy

    flagged = [
        c
        for c in report.clusters
        if c.score >= report.config.cluster_flag_threshold and c.kind == "neighbours"
    ]
    slot = {c.id: i + 1 for i, c in enumerate(flagged[:3])}
    out = [
        f'<svg class="chart net" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Follow network of seeds and expanded neighbours">'
    ]
    for u, v, d in g.edges(data=True):
        (x1, y1), (x2, y2) = px(u), px(v)
        cls = "edge" + (" mutual" if d["mutual"] else "") + (" seedlink" if d["seed"] else "")
        out.append(f'<line class="{cls}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    # Draw seeds last so they sit on top; label seeds first, then the highest scores, skipping overlaps.
    order = sorted(
        g.nodes(), key=lambda n: (n in seeds, report.accounts[n].score if n in report.accounts else 0)
    )
    placed: list[tuple[float, float, float, float]] = []
    labels: list[str] = []
    for n in sorted(
        order, key=lambda n: (n not in seeds, -(report.accounts[n].score if n in report.accounts else 0))
    ):
        acc = ds.accounts.get(n)
        res = report.accounts.get(n)
        if not (n in seeds or (res and res.score >= 0.6)) or len(labels) >= 14:
            continue
        x0, y0 = px(n)
        r = 4 + 2.2 * math.log10(max((acc.followers if acc else 0) or 1, 1))
        text = f"@{acc.handle if acc else n}"
        w = 6.6 * len(text)
        box = (x0 - w / 2, y0 - r - 16, x0 + w / 2, y0 - r - 2)
        if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in placed):
            continue
        placed.append(box)
        cls = "nlabel seed" if n in seeds else "nlabel"
        labels.append(
            f'<text class="{cls}" x="{x0:.1f}" y="{y0 - r - 5:.1f}" text-anchor="middle">{esc(text)}</text>'
        )
    for n in order:
        acc = ds.accounts.get(n)
        res = report.accounts.get(n)
        x0, y0 = px(n)
        r = 4 + 2.2 * math.log10(max((acc.followers if acc else 0) or 1, 1))
        cid = res.cluster_id if res else None
        cls = f"node s{slot[cid]}" if cid in slot else ("node other" if cid else "node none")
        tip = f"@{acc.handle if acc else n}"
        if acc:
            tip += f" - created {fmt_date(acc.created_at)}, {fmt_int(acc.followers)} followers"
        if res:
            tip += f" - score {res.score * 100:.0f} ({res.level})"
            if cid:
                tip += f", cluster #{cid}"
        out.append(
            f'<circle class="{cls}{" seed" if n in seeds else ""}" cx="{x0:.1f}" cy="{y0:.1f}" r="{r:.1f}">'
            f"<title>{esc(tip)}</title></circle>"
        )
    out.extend(labels)
    out.append("</svg>")
    legend = ['<div class="legend">']
    for c in flagged[:3]:
        legend.append(f'<span><i class="sw s{slot[c.id]}"></i>Cluster #{c.id} ({c.size})</span>')
    if any(c.id not in slot for c in report.clusters if c.kind == "neighbours"):
        legend.append('<span><i class="sw other"></i>Other clusters</span>')
    legend.append('<span><i class="sw none"></i>Not in a cluster</span>')
    legend.append(
        '<span><i class="ln mutual"></i>Mutual follow</span><span><i class="ln"></i>One-way follow</span>'
        '<span><i class="ln seedlink"></i>Link to a seed</span>'
    )
    legend.append("<span>Node size = followers (log); ringed = seed; hover a node for details</span></div>")
    return "".join(out) + "".join(legend)
