"""Self-contained HTML report (no external assets; light + dark mode)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..analysis.scoring import active
from ..util import fmt_date, fmt_int
from . import svg
from .svg import esc
from .text import seed_ties_text

if TYPE_CHECKING:
    from ..analysis import AccountResult, Report

LEVEL_CLASS = {"low": "good", "moderate": "warning", "high": "serious", "very high": "critical"}

CSS = """
:root{color-scheme:light;
--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;
--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s1-wash:rgba(42,120,214,.10);
--s2-wash:rgba(235,104,52,.12);--track:#e9e8e3;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;
--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s1-wash:rgba(57,135,229,.14);
--s2-wash:rgba(217,89,38,.16);--track:#2c2c2a}}
:root[data-theme="dark"]{color-scheme:dark;
--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s1-wash:rgba(57,135,229,.14);
--s2-wash:rgba(217,89,38,.16);--track:#2c2c2a}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:40px 0 12px}h3{font-size:16px;margin:20px 0 8px}
a{color:var(--s1)}.sub{color:var(--ink-2);margin:0}.muted{color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin:16px 0}
.row{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
.score{font-size:40px;font-weight:600;line-height:1}
.badge{display:inline-flex;align-items:center;gap:6px;font-weight:600;font-size:13px;padding:2px 10px;border-radius:999px;border:1px solid var(--border)}
.badge i{width:9px;height:9px;border-radius:50%;display:inline-block}
.good i{background:var(--good)}.warning i{background:var(--warning)}.serious i{background:var(--serious)}.critical i{background:var(--critical)}
.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px 16px;margin:16px 0}
.facts div{font-size:13px;color:var(--ink-2)}.facts b{display:block;color:var(--ink);font-size:15px;font-weight:600}
.sig{display:grid;grid-template-columns:minmax(150px,220px) 90px 1fr;gap:4px 12px;align-items:center;margin:6px 0;font-size:14px}
.meter{height:8px;background:var(--track);border-radius:4px;overflow:hidden}.meter span{display:block;height:100%;background:var(--s1);border-radius:4px}
.sig small{color:var(--ink-2)}
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--grid);stroke-width:1}.chart .axis{stroke:var(--axis);stroke-width:1}
.chart .ref{stroke:var(--muted);stroke-width:1}
.chart .tick{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.chart .halo{paint-order:stroke;stroke:var(--surface);stroke-width:4px;stroke-linejoin:round;fill:var(--ink-2)}
.chart .pt{fill:var(--s1);opacity:.75}.chart .pt.new{fill:var(--s2)}.chart .pt:hover{opacity:1;r:5}
.chart .band{fill:var(--s2-wash);stroke:var(--s2);stroke-width:1}.chart .burst{fill:var(--s2-wash)}.chart .burst.fresh{fill:var(--s1-wash)}.chart .band.fresh{fill:var(--s1-wash);stroke:var(--muted)}
.chart .bar{fill:var(--s1)}.chart .bar.new{fill:var(--s2)}.chart .bar:hover{opacity:.8}
.net .edge{stroke:var(--axis);stroke-width:1}.net .edge.mutual{stroke:var(--ink-2);stroke-width:2}.net .edge.seedlink{stroke:var(--grid);stroke-width:1}
.net .node{stroke:var(--surface);stroke-width:2}.net .node.seed{stroke:var(--ink);stroke-width:2.5}
.net .s1{fill:var(--s1)}.net .s2{fill:var(--s2)}.net .s3{fill:var(--s3)}.net .other{fill:var(--ink-2)}.net .none{fill:var(--muted)}
.net .nlabel{fill:var(--ink-2);font-size:11px}.net .nlabel.seed{fill:var(--ink);font-weight:600;font-size:12px}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--ink-2);margin:8px 0}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:10px;height:10px;border-radius:50%;display:inline-block}.sw.s1{background:var(--s1)}.sw.s2{background:var(--s2)}
.sw.s3{background:var(--s3)}.sw.other{background:var(--ink-2)}.sw.none{background:var(--muted)}
.ln{width:18px;height:0;border-top:1px solid var(--axis);display:inline-block}.ln.mutual{border-top:2px solid var(--ink-2)}.ln.seedlink{border-top-color:var(--grid)}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--ink-2);font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}
ul.plain{padding-left:18px;margin:6px 0}code{font-size:12px}
details summary{cursor:pointer;color:var(--ink-2)}
@media (max-width:640px){.sig{grid-template-columns:1fr 70px}.sig small{grid-column:1/-1}}
"""


def _badge(level: str) -> str:
    return f'<span class="badge {LEVEL_CLASS.get(level, "good")}"><i></i>{esc(level)}</span>'


def _h(report: Report, account_id: str, link: bool = True) -> str:
    h = report.handle(account_id)
    return f'<a href="https://x.com/{esc(h)}" rel="noopener">@{esc(h)}</a>' if link else f"@{esc(h)}"


def _signals(res: AccountResult | None, signals=None) -> str:
    sigs = active(signals if signals is not None else res.signals)
    if not sigs:
        return '<p class="muted">No signals fired.</p>'
    rows = []
    for s in sigs:
        rows.append(
            f'<div class="sig"><b>{esc(s.label)}</b><div class="meter" title="contribution {s.contribution:.2f} of max {s.weight:.2f}">'
            f'<span style="width:{min(s.contribution / 0.45, 1) * 100:.0f}%"></span></div><small>{esc(s.detail)}</small></div>'
        )
    return "".join(rows)


def _seed_card(report: Report, res: AccountResult) -> str:
    ds = report.dataset
    acc = ds.accounts[res.id]
    m = res.metrics
    about = acc.about
    facts = [
        ("Created", f"{fmt_date(acc.created_at)}"),
        ("Age", f"{m['age_days']:.0f} days" if m.get("age_days") is not None else "?"),
        ("Followers", fmt_int(acc.followers)),
        ("Following", fmt_int(acc.following)),
        ("Posts", fmt_int(acc.tweets)),
        ("Posts / day", f"{m['tweets_per_day']:.1f}" if "tweets_per_day" in m else "?"),
        ("Followers / day", f"{m['followers_per_day']:.1f}" if "followers_per_day" in m else "?"),
        ("Profile location", acc.location or "-"),
    ]
    if about:
        facts += [("X: based in", about.based_in or "-"), ("X: signup source", about.source or "-")]
        if about.username_changes is not None:
            facts.append(("Handle changes", str(about.username_changes)))
    if acc.verified:
        facts.append(("Verified", acc.verified_type or "yes"))
    if "mutual_share_sampled" in m:
        facts.append(("Follow-back (sample)", f"{m['mutual_share_sampled']:.0%}"))
    out = [
        f'<section class="card" id="seed-{esc(acc.handle)}"><div class="row"><h3 style="margin:0">{_h(report, res.id)}'
        f' <span class="muted">{esc(acc.name)}</span></h3>{_badge(res.level)}<span class="score">{res.score * 100:.0f}</span>'
        f'<span class="muted">/ 100 suspicion{f" · cluster #{res.cluster_id}" if res.cluster_id else ""}</span></div>'
    ]
    if acc.description:
        out.append(f'<p class="sub">{esc(acc.description)}</p>')
    out.append(
        '<div class="facts">' + "".join(f"<div>{esc(k)}<b>{esc(v)}</b></div>" for k, v in facts) + "</div>"
    )
    out.append("<h3>Signals</h3>" + _signals(res))

    for rel, nb in report.neighborhoods.get(res.id, {}).items():
        out.append(
            f"<h3>{'Followers' if rel == 'followers' else 'Accounts it follows'}: {fmt_int(nb.sampled)} sampled"
            f"{f' of {fmt_int(nb.total)}' if nb.total else ''}</h3>"
        )
        cfg = report.config
        hard_bursts = [b for b in nb.bursts if not b.fresh_signups]
        stats = [
            f"{nb.recent_share:.0%} {cfg.young_label} (created after {fmt_date(cfg.recent_since)})",
            f"{len(hard_bursts)} creation burst(s) (any year), {nb.burst_excess:.1%} of the sample beyond background"
            + (
                "; largest: "
                + f"{hard_bursts[0].count} created {fmt_date(hard_bursts[0].start)} - {fmt_date(hard_bursts[0].end)} "
                + f"vs ~{hard_bursts[0].expected:.1f} expected (p={hard_bursts[0].p_value:.0e})"
                if hard_bursts
                else ""
            ),
            f"densest {report.config.creation_window_days}-day creation window: {nb.peak.share:.0%} "
            f"({fmt_date(nb.peak.start)} - {fmt_date(nb.peak.end)}); excluding fresh sign-ups: {nb.aged_peak.share:.0%}",
            f"{nb.botlike_share:.0%} bot-like on metadata",
            f"median account age {nb.median_age_days:.0f} days" if nb.median_age_days is not None else "",
            f"{len(nb.bands)} follower-map band(s)"
            + (f", {nb.band_share:.0%} of sample" if nb.bands else ""),
        ]
        out.append('<ul class="plain">' + "".join(f"<li>{esc(s)}</li>" for s in stats if s) + "</ul>")
        if rel == "followers":
            chart = svg.follower_map(nb, ds.as_of, cfg.recent_since, young_label=cfg.young_label)
            if chart:
                out.append(
                    '<div class="legend"><span><i class="sw s1"></i>Older follower</span>'
                    f'<span><i class="sw s2"></i>Young follower ({esc(cfg.young_label)})</span>'
                    "<span>Boxes = bands of consecutive followers created together</span>"
                    "<span>Horizontal stripes = creation bursts</span></div>" + chart
                )
        out.append(
            svg.creation_histogram(
                nb.monthly, report.config.recent_since, ds.as_of, label=f"Creation month of sampled {rel}"
            )
        )

    b = report.behavior.get(res.id)
    if b:
        out.append("<h3>Recent timeline</h3><ul class='plain'>")
        kinds = ", ".join(f"{v} {k}s" for k, v in b.get("kinds", {}).items())
        out.append(f"<li>{b['items']} items: {esc(kinds)}</li>")
        if b.get("timed_items"):
            out.append(
                f"<li>Own activity in {b.get('active_hours')}/24 UTC hours; longest quiet stretch "
                f"{b.get('max_quiet_gap_hours')}h over {b.get('span_days')} days</li>"
            )
        if b.get("top_amplified"):
            amp = ", ".join(f"@{esc(h)} ({n})" for h, n in b["top_amplified"][:8])
            out.append(f"<li>Most amplified: {amp}</li>")
        if b.get("languages"):
            out.append(
                f"<li>Languages: {esc(', '.join(f'{k} ({v})' for k, v in b['languages'].items()))}</li>"
            )
        out.append("</ul>")
    out.append("</section>")
    return "".join(out)


def _clusters(report: Report) -> str:
    if not report.clusters:
        if report.dataset.expanded_ids:
            return '<p class="muted">No mutual-follow clusters of 3+ accounts found among the expanded neighbours.</p>'
        return '<p class="muted">No 2nd-degree expansion was run, so no follow clusters could be measured (use <code>--expand</code>).</p>'
    rows = []
    for c in report.clusters:
        members = " ".join(_h(report, m) for m in c.members[:40]) + (" ..." if c.size > 40 else "")
        targets = ", ".join(f"{_h(report, t)} ({n})" for t, n in c.shared_targets[:8]) or "-"
        sigs = "; ".join(f"{s.label}: {s.detail}" for s in active(c.signals))
        ties = seed_ties_text(report, c)
        kind = "seed group" if c.kind == "seed-group" else "neighbours"
        rows.append(
            f"<tr><td>#{c.id}<br><span class='muted'>{kind}</span></td><td>{_badge(c.level)} {c.score * 100:.0f}</td>"
            f"<td class='num'>{c.size}</td>"
            f"<td class='num'>{c.density:.0%}</td><td class='num'>{c.reciprocity:.0%}</td>"
            f"<td class='num'>{c.peak30.share:.0%}</td><td class='num'>{c.recent_share:.0%}</td>"
            f"<td>{members}<details><summary>why</summary>{esc(sigs) or '-'}<br>Jointly followed: {targets}"
            f"{'<br>Seed ties: ' + esc(ties) if ties else ''}</details></td></tr>"
        )
    return (
        '<div class="tbl"><table><thead><tr><th>Cluster</th><th>Score</th><th>Size</th><th>Density</th><th>Mutual</th>'
        f"<th>Created within {report.config.creation_window_days}d</th><th>Young</th><th>Members</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _pairs(report: Report) -> str:
    if not report.pairs:
        return ""
    rows = []

    def yn(v: bool | None) -> str:
        return "yes" if v else ("not seen" if v is False else "?")

    for p in report.pairs:
        rows.append(
            f"<tr><td>{_h(report, p.a)} / {_h(report, p.b)}</td>"
            f"<td class='num'>{'-' if p.follower_jaccard is None else f'{p.follower_jaccard:.3f}'} ({p.shared_followers})</td>"
            f"<td class='num'>{'-' if p.following_jaccard is None else f'{p.following_jaccard:.3f}'} ({p.shared_following})</td>"
            f"<td>{yn(p.a_follows_b)} / {yn(p.b_follows_a)}</td>"
            f"<td class='num'>{'-' if p.created_days_apart is None else f'{p.created_days_apart:.0f}'}</td>"
            f"<td class='num'>{p.shared_amplified_accounts} / {p.shared_reposted_posts}</td></tr>"
        )
    g = report.group
    head = ""
    if g:
        pk = g["peak"]
        head = (
            f"<p>{g['seeds']} seeds created {g['earliest'][:10]} - {g['latest'][:10]}; {g['recent_share']:.0%} {report.config.young_label}"
            + (
                f"; {pk['count']} of them created within {report.config.creation_window_days} days of each other"
                if pk["count"] >= 2
                else ""
            )
            + ".</p>"
        )
    return (
        "<h2>Seed overlap</h2>"
        + head
        + '<div class="tbl"><table><thead><tr><th>Pair</th><th>Follower Jaccard (shared)</th><th>Following Jaccard (shared)</th>'
        "<th>Follows (a→b / b→a)</th><th>Created days apart</th><th>Shared amplified accounts / reposted posts</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _neighbours(report: Report, limit: int = 50) -> str:
    rows = []
    for r in report.ranked()[:limit]:
        if r.score < 0.3:
            break
        acc = report.dataset.accounts[r.id]
        sig = "; ".join(s.label for s in active(r.signals)[:4])
        rows.append(
            f"<tr><td>{_h(report, r.id)}</td><td>{_badge(r.level)} {r.score * 100:.0f}</td><td>{esc(', '.join(r.roles))}</td>"
            f"<td>{fmt_date(acc.created_at)}</td><td class='num'>{fmt_int(acc.followers)}</td><td class='num'>{fmt_int(acc.following)}</td>"
            f"<td class='num'>{r.metrics.get('tweets_per_day', '?')}</td><td>{'#' + str(r.cluster_id) if r.cluster_id else ''}</td><td>{esc(sig)}</td></tr>"
        )
    if not rows:
        return '<p class="muted">No neighbouring account scored moderate or above.</p>'
    return (
        '<div class="tbl"><table><thead><tr><th>Account</th><th>Score</th><th>Seen as</th><th>Created</th><th>Followers</th>'
        "<th>Following</th><th>Posts/day</th><th>Cluster</th><th>Top signals</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _engagements(report: Report) -> str:
    if not report.engagements:
        return ""
    out = ["<h2>Engagement analysis</h2>"]
    for e in report.engagements:
        out.append(
            f'<section class="card"><div class="row"><h3 style="margin:0">Post <a href="https://x.com/i/status/{esc(e.tweet_id)}">'
            f"{esc(e.tweet_id)}</a></h3>{_badge(e.level)}<span class='score'>{e.score * 100:.0f}</span></div>"
            f"<p class='sub'>{e.engagers} engaging accounts ({e.reposters} reposters, {e.repliers} repliers); "
            f"{e.recent_share:.0%} {report.config.young_label}; densest {report.config.creation_window_days}-day creation window {e.peak.share:.0%}; "
            f"median reply delay {e.median_reply_delay_min if e.median_reply_delay_min is not None else '?'} min</p>"
            + _signals(None, e.signals)
            + svg.creation_histogram(
                e.monthly, report.config.recent_since, report.dataset.as_of, label="Engager creation month"
            )
            + "</section>"
        )
    return "".join(out)


def render_html(report: Report) -> str:
    ds = report.dataset
    seeds = report.seeds
    if seeds:
        title = (
            "Bot-cluster report: "
            + ", ".join(f"@{s.handle}" for s in seeds[:4])
            + (" ..." if len(seeds) > 4 else "")
        )
    else:
        title = "Engagement report: " + ", ".join(f"post {e.tweet_id}" for e in report.engagements[:3])
    opts = ds.meta.get("options")
    sampling = f"source {esc(ds.meta.get('source', '?'))} · {len(ds.accounts):,} accounts seen"
    if opts:
        sampling = (
            f"source {esc(ds.meta.get('source', '?'))} · up to {opts.get('followers', '?')} followers / "
            f"{opts.get('following', '?')} following per seed · {len(ds.expanded_ids)} neighbours expanded · "
            f"{len(ds.accounts):,} accounts seen"
        )
    summary = "".join(
        f"<li>{_h(report, s.id)} {_badge(s.level)} <b>{s.score * 100:.0f}</b> - "
        f"{esc('; '.join(x.label for x in active(s.signals)[:4]) or 'no signals')}</li>"
        for s in seeds
    )
    flagged = [c for c in report.clusters if c.score >= report.config.cluster_flag_threshold]
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Bot-cluster report</title><style>{CSS}</style></head><body><main>",
        f"<h1>{esc(title)}</h1><p class='sub'>As of {fmt_date(ds.as_of)} · {sampling}</p>",
    ]
    if seeds:
        parts.append(
            f"<section class='card'><h3 style='margin-top:0'>Summary</h3><ul class='plain'>{summary}</ul>"
            f"<p class='sub'>{len(flagged)} suspicious follow cluster(s) of {len(report.clusters)} found.</p></section>"
        )
    if seeds:
        parts.append("<h2>Seed accounts</h2>")
        parts.extend(_seed_card(report, s) for s in seeds)
        net = svg.network(report)
        parts.append("<h2>Follow network & clusters</h2>")
        if net:
            parts.append(f"<div class='card'>{net}</div>")
        parts.append(_clusters(report))
    parts.append(_pairs(report))
    parts.append(_engagements(report))
    parts.append("<h2>Most suspicious neighbouring accounts</h2>" + _neighbours(report))
    parts.append(
        "<h2>How to read this</h2><div class='card'><p>Each signal has a strength (0-1) and a maximum weight; they combine as "
        "<code>1 - Π(1 - strength × weight)</code>, so several weak signals stack while no single one dominates. "
        "Levels: low &lt; 30 ≤ moderate &lt; 50 ≤ high &lt; 70 ≤ very high.</p><ul class='plain'>"
        + "".join(f"<li>{esc(c)}</li>" for c in report.caveats)
        + "</ul>"
        + (
            f"<details><summary>{len(ds.errors)} collection warning(s)</summary><ul class='plain'>"
            + "".join(f"<li>{esc(e)}</li>" for e in ds.errors)
            + "</ul></details>"
            if ds.errors
            else ""
        )
        + "</div></main></body></html>"
    )
    return "".join(parts)
