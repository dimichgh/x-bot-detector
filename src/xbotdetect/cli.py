"""Command line: ``xbot analyze @handle ...``, ``xbot engagement <post-url>``, ``xbot report dataset.json``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .analysis import AnalysisConfig, analyze
from .collect import CollectOptions, collect, collect_engagement
from .cookies import CookieError
from .dataset import Dataset
from .report import FORMATS, render_text, write_reports
from .sources import SOURCE_NAMES, SourceError, SourceOptions, build_source
from .util import normalize_handle, parse_iso, parse_tweet_id, utcnow

log = logging.getLogger("xbotdetect")

EPILOG = """
data sources (--source, comma-separated, tried in order):
  fxtwitter   free public API (api.fxtwitter.com), no login. Default.
  twscrape    your logged-in X session via cookies (--cookies / --cookies-from-browser / X_AUTH_TOKEN+X_CT0).
  bird        the `bird` CLI used by OpenClaw (npm i -g @steipete/bird); reads your browser's X session itself.
  offline     replay a dataset saved with --save-dataset (needs --dataset).

examples:
  xbot analyze @MartinKuban @OxaraElena
  xbot analyze -f handles.txt --followers 2000 --expand 60 --out reports/
  xbot analyze @someone --source twscrape --cookies-from-browser chrome
  xbot profile @a @b @c                      # quick metadata-only scoring
  xbot engagement https://x.com/user/status/123
  xbot report reports/dataset.json.gz --recent-since 2025-01-01
"""


def _date(s: str) -> datetime:
    dt = parse_iso(s)
    if dt is None:
        raise argparse.ArgumentTypeError(f"not a date: {s!r} (use YYYY-MM-DD)")
    return dt


def _add_source_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("data source")
    g.add_argument(
        "--source", default="fxtwitter", help=f"one or more of {', '.join(SOURCE_NAMES)} (default: fxtwitter)"
    )
    g.add_argument("--cookies", help='X session cookies, e.g. "auth_token=...; ct0=..." (twscrape/bird)')
    g.add_argument(
        "--cookies-file", help="file with cookies (Cookie header, JSON export or Netscape cookies.txt)"
    )
    g.add_argument(
        "--cookies-from-browser", metavar="BROWSER", help="read x.com cookies from chrome/firefox/safari/..."
    )
    g.add_argument(
        "--twscrape-db", help="twscrape accounts database (default ~/.xbotdetect/twscrape-accounts.db)"
    )
    g.add_argument("--proxy", help="HTTP proxy for twscrape")
    g.add_argument("--dataset", help="dataset file for --source offline")
    g.add_argument("--concurrency", type=int, default=4, help="parallel requests (default 4)")
    g.add_argument("--no-cache", action="store_true", help="don't read/write the local response cache")
    g.add_argument(
        "--cache-ttl", type=float, default=24.0, metavar="HOURS", help="cache lifetime (default 24h)"
    )


def _add_analysis_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("analysis")
    g.add_argument(
        "--recent-since",
        type=_date,
        default=datetime(2024, 10, 1, tzinfo=timezone.utc),
        help="accounts created on/after this date count as the new wave (default 2024-10-01)",
    )
    g.add_argument("--window-days", type=int, default=30, help="creation-date clustering window (default 30)")
    g.add_argument("--as-of", type=_date, help="analysis date (default: collection time)")


def _add_output_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("output")
    g.add_argument("--out", help="directory for report files (default ./xbot-reports/<date>-<handles>)")
    g.add_argument(
        "--format",
        default="html,md,json",
        help=f"comma-separated: {', '.join(FORMATS)} (default html,md,json)",
    )
    g.add_argument("--no-files", action="store_true", help="only print the terminal summary")
    g.add_argument(
        "--save-dataset",
        nargs="?",
        const="",
        metavar="PATH",
        help="also save raw collected data (default <out>/dataset.json.gz) for offline re-analysis",
    )
    g.add_argument(
        "--json", action="store_true", help="print the JSON report to stdout instead of the text summary"
    )
    g.add_argument("-q", "--quiet", action="store_true")
    g.add_argument("-v", "--verbose", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="xbot",
        description="Detect coordinated follow-farm / bot clusters on X (Twitter).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    a = sub.add_parser(
        "analyze",
        help="full analysis of one or more handles (metadata + network + behaviour)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    a.add_argument("handles", nargs="*", help="@handles or profile URLs")
    a.add_argument("-f", "--file", help="file with one handle per line")
    c = a.add_argument_group("collection")
    c.add_argument("--followers", type=int, default=1000, help="followers to sample per seed (default 1000)")
    c.add_argument(
        "--following", type=int, default=1000, help="followed accounts to sample per seed (default 1000)"
    )
    c.add_argument(
        "--timeline", type=int, default=100, help="recent timeline items per seed (default 100, 0=skip)"
    )
    c.add_argument(
        "--expand",
        type=int,
        default=30,
        help="neighbours whose following lists are fetched to measure cluster density (default 30, 0=off)",
    )
    c.add_argument(
        "--expand-following",
        type=int,
        default=400,
        help="following entries per expanded neighbour (default 400)",
    )
    c.add_argument(
        "--expand-timeline", type=int, default=0, help="timeline items per expanded neighbour (default 0)"
    )
    c.add_argument(
        "--expand-scope",
        choices=("recent", "all"),
        default="recent",
        help="expand only new-wave neighbours (default) or any",
    )
    c.add_argument("--min-candidate-followers", type=int, default=200)
    c.add_argument("--no-about", action="store_true", help="skip 'About this account' lookups")
    _add_source_args(a)
    _add_analysis_args(a)
    _add_output_args(a)

    pr = sub.add_parser("profile", help="quick metadata + timeline scoring, no follower graphs")
    pr.add_argument("handles", nargs="*")
    pr.add_argument("-f", "--file")
    pr.add_argument("--timeline", type=int, default=40)
    pr.add_argument("--no-about", action="store_true")
    _add_source_args(pr)
    _add_analysis_args(pr)
    _add_output_args(pr)

    e = sub.add_parser(
        "engagement",
        help="analyse who reposted/replied to posts (creation-date concentration, reply velocity)",
    )
    e.add_argument("posts", nargs="+", help="post URLs or IDs")
    e.add_argument("--reposts", type=int, default=500, help="reposters to fetch per post (default 500)")
    e.add_argument("--replies", type=int, default=300, help="replies to fetch per post (default 300)")
    _add_source_args(e)
    _add_analysis_args(e)
    _add_output_args(e)

    r = sub.add_parser("report", help="re-analyse a saved dataset (no network)")
    r.add_argument("dataset_file")
    _add_analysis_args(r)
    _add_output_args(r)
    return ap


def _handles(args: argparse.Namespace) -> list[str]:
    raw = list(args.handles or [])
    if getattr(args, "file", None):
        for line in Path(args.file).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            raw.extend(x for x in re.split(r"[\s,]+", line) if x)
    out: list[str] = []
    for h in raw:
        n = normalize_handle(h)
        if n.lower() not in {x.lower() for x in out}:
            out.append(n)
    if not out:
        raise SystemExit("error: give at least one handle (or -f FILE)")
    return out


def _source(args: argparse.Namespace):
    return build_source(
        args.source,
        SourceOptions(
            cookies=args.cookies,
            cookies_file=args.cookies_file,
            cookies_from_browser=args.cookies_from_browser,
            twscrape_db=args.twscrape_db,
            proxy=args.proxy,
            dataset=args.dataset,
            concurrency=args.concurrency,
            cache=not args.no_cache,
            cache_ttl_hours=args.cache_ttl,
        ),
    )


def _cfg(args: argparse.Namespace) -> AnalysisConfig:
    return AnalysisConfig(recent_since=args.recent_since, creation_window_days=args.window_days)


def _out_dir(args: argparse.Namespace, names: list[str]) -> Path:
    if args.out:
        return Path(args.out)
    slug = "-".join(n.lower() for n in names[:3]) + (f"-and-{len(names) - 3}-more" if len(names) > 3 else "")
    return Path("xbot-reports") / f"{utcnow():%Y%m%d-%H%M}-{slug[:80]}"


def _finish(args: argparse.Namespace, ds: Dataset, names: list[str]) -> int:
    if args.as_of:
        ds.as_of = args.as_of
    report = analyze(ds, _cfg(args))
    if args.json:
        import json

        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=1, default=str))
    else:
        print(render_text(report))
    if not args.no_files:
        out = _out_dir(args, names)
        formats = [f.strip() for f in args.format.split(",") if f.strip()]
        written = write_reports(report, out, formats)
        if args.save_dataset is not None:
            written.append(ds.save(args.save_dataset or out / "dataset.json.gz"))
        print("\nwrote: " + ", ".join(str(p) for p in written), file=sys.stderr)
    elif args.save_dataset:
        ds.save(args.save_dataset)
    return 0


async def _run(args: argparse.Namespace) -> int:
    if args.command == "report":
        ds = Dataset.load(args.dataset_file)
        names = [a.handle for a in ds.seeds] or [Path(args.dataset_file).stem]
        return _finish(args, ds, names)

    src = _source(args)
    async with src:
        if args.command == "engagement":
            ids = [parse_tweet_id(p) for p in args.posts]
            ds = await collect_engagement(src, ids, reposts=args.reposts, replies=args.replies)
            return _finish(args, ds, [f"post-{i}" for i in ids])
        handles = _handles(args)
        if args.command == "profile":
            opts = CollectOptions(
                followers=0,
                following=0,
                timeline=args.timeline,
                expand=0,
                about=not args.no_about,
                concurrency=args.concurrency,
            )
        else:
            opts = CollectOptions(
                followers=args.followers,
                following=args.following,
                timeline=args.timeline,
                about=not args.no_about,
                expand=args.expand,
                expand_following=args.expand_following,
                expand_timeline=args.expand_timeline,
                expand_scope=args.expand_scope,
                min_candidate_followers=args.min_candidate_followers,
                concurrency=args.concurrency,
            )
        ds = await collect(src, handles, opts, _cfg(args))
        if not ds.seed_ids:
            print(
                "error: none of the handles could be resolved:\n  " + "\n  ".join(ds.errors), file=sys.stderr
            )
            return 2
        return _finish(args, ds, handles)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = logging.WARNING if args.quiet else logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname).1s %(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    try:
        return asyncio.run(_run(args))
    except (SourceError, CookieError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
