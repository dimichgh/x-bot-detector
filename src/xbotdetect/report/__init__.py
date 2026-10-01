"""Report writers: terminal text, Markdown, HTML, JSON, CSV and Gephi graph exports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from ..analysis.scoring import active
from ..util import iso
from .html import render_html
from .text import render_markdown, render_text

if TYPE_CHECKING:
    from ..analysis import Report

FORMATS = ("html", "md", "json", "csv", "gexf", "graphml")

__all__ = ["FORMATS", "render_text", "render_markdown", "render_html", "write_reports", "export_graph"]


def export_graph(report: Report) -> nx.DiGraph:
    """Follow + amplification graph with node attributes, ready for Gephi."""
    ds = report.dataset
    g = nx.DiGraph()
    for u, v in report.follow_graph.edges():
        g.add_edge(u, v, kind="follows", weight=1)
    for u, v, d in report.amp_graph.edges(data=True):
        if g.has_edge(u, v):
            g[u][v]["amplifies"] = d.get("weight", 1)
        else:
            g.add_edge(u, v, kind="amplifies", weight=d.get("weight", 1))
    for n in g.nodes():
        acc = ds.accounts.get(n)
        res = report.accounts.get(n)
        attrs = {
            "label": acc.handle if acc else n,
            "created": iso(acc.created_at) if acc and acc.created_at else "",
            "created_year": acc.created_at.year if acc and acc.created_at else 0,
            "followers": (acc.followers or 0) if acc else 0,
            "following": (acc.following or 0) if acc else 0,
            "score": round(res.score, 4) if res else 0.0,
            "meta_score": round(res.meta_score, 4) if res else 0.0,
            "level": res.level if res else "",
            "roles": ",".join(res.roles) if res else "",
            "cluster": (res.cluster_id or 0) if res else 0,
            "seed": n in ds.seed_ids,
        }
        g.nodes[n].update(attrs)
    return g


def _csv(report: Report, path: Path) -> None:
    ds = report.dataset
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "id",
                "handle",
                "score",
                "level",
                "meta_score",
                "roles",
                "cluster",
                "created_at",
                "followers",
                "following",
                "tweets",
                "followers_per_day",
                "tweets_per_day",
                "based_in",
                "location",
                "top_signals",
            ]
        )
        for r in sorted(report.accounts.values(), key=lambda r: r.score, reverse=True):
            acc = ds.accounts[r.id]
            w.writerow(
                [
                    r.id,
                    acc.handle,
                    round(r.score, 4),
                    r.level,
                    round(r.meta_score, 4),
                    "|".join(r.roles),
                    r.cluster_id or "",
                    iso(acc.created_at) or "",
                    acc.followers,
                    acc.following,
                    acc.tweets,
                    r.metrics.get("followers_per_day"),
                    r.metrics.get("tweets_per_day"),
                    acc.about.based_in if acc.about else "",
                    acc.location,
                    "; ".join(s.label for s in active(r.signals)[:5]),
                ]
            )


def write_reports(
    report: Report, out_dir: str | Path, formats: list[str], stem: str = "report"
) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fmt in formats:
        if fmt == "html":
            p = out / f"{stem}.html"
            p.write_text(render_html(report))
        elif fmt == "md":
            p = out / f"{stem}.md"
            p.write_text(render_markdown(report))
        elif fmt == "json":
            p = out / f"{stem}.json"
            p.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=1, default=str))
        elif fmt == "csv":
            p = out / f"{stem}-accounts.csv"
            _csv(report, p)
        elif fmt == "gexf":
            p = out / f"{stem}-graph.gexf"
            nx.write_gexf(export_graph(report), p)
        elif fmt == "graphml":
            p = out / f"{stem}-graph.graphml"
            nx.write_graphml(export_graph(report), p)
        else:
            raise ValueError(f"unknown report format {fmt!r}; choose from {', '.join(FORMATS)}")
        written.append(p)
    return written
