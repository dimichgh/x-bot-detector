from datetime import datetime, timedelta, timezone

from conftest import AS_OF

from xbotdetect.analysis import AnalysisConfig, analyze
from xbotdetect.analysis.account import account_signals
from xbotdetect.analysis.behavior import max_quiet_gap_hours, timeline_metrics
from xbotdetect.analysis.scoring import Signal, combine, level
from xbotdetect.analysis.temporal import creation_peak, follower_map_bands
from xbotdetect.models import About, Account, Post

UTC = timezone.utc


def keys(signals):
    return {s.key for s in signals if s.contribution > 0}


def test_combine_is_noisy_or():
    a = Signal("a", "A", 1.0, 0.4, "")
    b = Signal("b", "B", 0.5, 0.4, "")
    assert combine([]) == 0
    assert abs(combine([a]) - 0.4) < 1e-9
    assert abs(combine([a, b]) - (1 - 0.6 * 0.8)) < 1e-9
    assert (
        level(0.1) == "low"
        and level(0.35) == "moderate"
        and level(0.55) == "high"
        and level(0.8) == "very high"
    )


def test_young_account_with_big_audience():
    acc = Account(
        id="1",
        handle="NewsGuy",
        created_at=AS_OF - timedelta(days=250),
        followers=7600,
        following=1700,
        tweets=10_000,
        description="news",
        avatar_url="https://pbs.twimg.com/profile_images/1/a.jpg",
        banner_url="x",
        source="fxtwitter",
    )
    sig = account_signals(acc, AS_OF, AnalysisConfig())
    assert {"young_account", "rapid_follower_growth"} <= keys(sig)
    assert "hyperactive" not in keys(sig)  # 40/day is the threshold's floor
    assert 0.3 <= combine(sig) < 0.5  # metadata alone stays "moderate"


def test_hyperactive_amplifier_with_origin_mismatch():
    acc = Account(
        id="2",
        handle="Elena",
        created_at=AS_OF - timedelta(days=527),
        followers=2500,
        following=3700,
        tweets=106_000,
        location="Россия.",
        description="quote",
        avatar_url="https://pbs.twimg.com/a.jpg",
        banner_url="b",
        source="fxtwitter",
        about=About(
            based_in="Netherlands",
            source="Russian Federation Android App",
            username_changes=1,
            username_last_changed_at=AS_OF - timedelta(days=527) + timedelta(minutes=1),
        ),
    )
    sig = account_signals(acc, AS_OF, AnalysisConfig())
    k = keys(sig)
    assert {"hyperactive", "origin_mismatch", "mass_following"} <= k
    assert "username_changes" not in k  # renamed one minute after sign-up: normal


def test_bulk_signup_profile():
    acc = Account(
        id="3",
        handle="Maria48201937",
        created_at=AS_OF - timedelta(days=40),
        followers=3000,
        following=4990,
        tweets=12,
        avatar_url="https://abs.twimg.com/sticky/default_profile_images/default_profile_normal.png",
        source="fxtwitter",
        about=About(
            username_changes=3, username_last_changed_at=AS_OF - timedelta(days=5), location_accurate=False
        ),
    )
    k = keys(account_signals(acc, AS_OF, AnalysisConfig()))
    assert {
        "autogen_handle",
        "profile_incomplete",
        "dormant_audience",
        "follow_limit",
        "username_changes",
        "location_inaccurate",
    } <= k


def test_creation_peak_and_bands():
    base = datetime(2015, 1, 1, tzinfo=UTC)
    organic = [base + timedelta(days=37 * i) for i in range(120)]  # spread over ~12 years
    batch = [datetime(2025, 6, 1, tzinfo=UTC) + timedelta(hours=3 * i) for i in range(60)]
    dates = organic[:50] + batch + organic[50:]  # oldest follow first
    p = creation_peak(dates, 30)
    assert p.count >= 60 and p.share >= 60 / 180 and p.start.year == 2025  # + any organic date in that window
    bands = follower_map_bands(dates, AS_OF)
    assert bands and bands[0].size >= 55
    assert 45 <= bands[0].rank_start <= 55 and not bands[0].fresh_signups
    assert follower_map_bands(organic, AS_OF) == []


def test_quiet_gap_and_timeline_metrics():
    assert max_quiet_gap_hours([]) == 24
    assert max_quiet_gap_hours(list(range(24))) == 0
    assert max_quiet_gap_hours([22, 23, 0, 1, 9]) == 12  # 10..21
    owner = Account(id="1", handle="me")
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    posts = [
        Post(
            id=str(i),
            author_id="1",
            author_handle="me",
            created_at=t0 + timedelta(hours=i),
            text="same text here for all",
        )
        for i in range(48)
    ]
    posts += [
        Post(
            id=f"r{i}",
            author_id="1",
            author_handle="me",
            kind="repost",
            target_id="9",
            target_handle="other",
            time_is_action=False,
        )
        for i in range(10)
    ]
    m = timeline_metrics(owner, posts)
    assert m["max_quiet_gap_hours"] == 0 and m["interval_cv"] == 0.0
    assert m["duplicate_share"] == 1.0 and m["top_amplified"] == [("other", 10)]


def test_farm_world_detected(farm_world):
    r = analyze(farm_world)
    seed = r.seeds[0]
    k = keys(seed.signals)
    assert "follower_map_bands" in k
    assert "in_suspicious_cluster" in k
    assert "origin_mismatch" in k
    assert seed.level in ("high", "very high")
    farm_clusters = [c for c in r.clusters if c.kind == "neighbours" and c.score >= 0.5]
    assert farm_clusters, [(c.size, c.score) for c in r.clusters]
    top = farm_clusters[0]
    assert all(int(m) >= 50_000 for m in top.members)  # only farm accounts
    assert top.density > 0.5 and top.peak30.share == 1.0
    assert top.size >= 25  # the 30 expanded farm accounts stay one cluster
    assert top.seed_ties["1"]["mutual"] >= 0.8 * top.size
    farm_scores = [r.accounts[m].score for m in top.members]
    assert min(farm_scores) >= 0.7


def test_clean_world_stays_low(clean_world):
    r = analyze(clean_world)
    seed = r.seeds[0]
    k = keys(seed.signals)
    assert "follower_map_bands" not in k and "in_suspicious_cluster" not in k
    assert not [c for c in r.clusters if c.score >= 0.5]
    assert seed.score < 0.7  # only its own metadata (young, fast growth, origin mismatch)


def test_report_serialises(farm_world, tmp_path):
    import json

    from xbotdetect.report import render_text, write_reports

    r = analyze(farm_world)
    text = render_text(r)
    assert "@TargetAccount" in text and "FOLLOW CLUSTERS" in text
    paths = write_reports(r, tmp_path, ["html", "md", "json", "csv", "gexf", "graphml"])
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)
    html = (tmp_path / "report.html").read_text()
    assert html.count("<svg") >= 3 and "prefers-color-scheme:dark" in html
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["seeds"][0]["handle"] == "TargetAccount" and data["clusters"]


def test_burst_statistics():
    import random

    from xbotdetect.analysis.temporal import binom_sf, creation_bursts, poisson_sf

    assert abs(poisson_sf(5, 0.5) - 1.7212e-4) < 1e-7
    assert abs(binom_sf(2, 10, 0.5) - (1 - 11 / 1024)) < 1e-9
    rng = random.Random(3)
    organic = [datetime(2009, 1, 1, tzinfo=UTC) + timedelta(days=rng.uniform(0, 6200)) for _ in range(2000)]
    # A smooth platform-wide sign-up wave is not a farm.
    organic += [datetime(2022, 11, 1, tzinfo=UTC) + timedelta(days=rng.uniform(0, 120)) for _ in range(300)]
    assert creation_bursts(organic, AS_OF) == []
    farm = [datetime(2016, 3, 3, tzinfo=UTC) + timedelta(hours=rng.uniform(0, 96)) for _ in range(40)]
    bursts = creation_bursts(organic + farm, AS_OF)
    assert len(bursts) == 1 and bursts[0].start.year == 2016 and bursts[0].count >= 40
    assert bursts[0].p_value < 1e-20 and not bursts[0].fresh_signups


def test_young_is_relative_and_campaign_window_optional():
    acc = Account(
        id="9",
        handle="x",
        created_at=AS_OF - timedelta(days=500),
        followers=10,
        following=10,
        description="d",
        source="fxtwitter",
        banner_url="b",
    )
    rel = AnalysisConfig().resolve(AS_OF)
    assert rel.recent_since == AS_OF - timedelta(days=730)
    s_rel = next(s for s in account_signals(acc, AS_OF, rel) if s.key == "young_account").score
    camp = AnalysisConfig(campaign_since=datetime(2024, 10, 1, tzinfo=UTC)).resolve(AS_OF)
    s_camp = next(s for s in account_signals(acc, AS_OF, camp) if s.key == "young_account").score
    assert s_rel < 0.5 <= s_camp and camp.recent_since.year == 2024


def test_aged_account_renamed_and_reactivated():
    from xbotdetect.analysis.behavior import behavior_signals

    acc = Account(
        id="7",
        handle="SomeName",
        created_at=datetime(2014, 5, 1, tzinfo=UTC),
        followers=900,
        following=800,
        tweets=2500,
        description="d",
        source="fxtwitter",
        banner_url="b",
        about=About(username_changes=2, username_last_changed_at=AS_OF - timedelta(days=60)),
    )
    sig = {s.key: s for s in account_signals(acc, AS_OF, AnalysisConfig().resolve(AS_OF))}
    assert sig["username_changes"].score >= 0.7 and "12-year-old" in sig["username_changes"].detail
    t0 = AS_OF - timedelta(days=2)
    posts = [
        Post(
            id=str(i),
            author_id="7",
            author_handle="SomeName",
            created_at=t0 + timedelta(minutes=50 * i),
            text=f"post {i} with some words",
        )
        for i in range(60)
    ]
    m = timeline_metrics(acc, posts)
    k = keys(behavior_signals(m, acc, AS_OF))
    assert "reactivated" in k  # ~29/day now vs ~0.6/day over 12 years


def test_aged_farm_detected_without_any_year_assumption(aged_farm_world):
    r = analyze(aged_farm_world)
    seed = r.seeds[0]
    k = keys(seed.signals)
    assert {"follower_creation_burst", "follower_map_bands", "in_suspicious_cluster"} <= k
    bursts = r.neighborhoods["1"]["followers"].bursts
    assert all(b.start.year == 2016 for b in bursts)
    assert sum(b.count for b in bursts) >= 75  # 80 accounts over 10 days -> adjacent 7-day bursts
    top = max((c for c in r.clusters if c.kind == "neighbours"), key=lambda c: c.score)
    assert top.score >= 0.5 and all(int(m) >= 50_000 for m in top.members)
    assert any(s.key == "templated_profiles" for s in top.signals)  # every farm account is named "Anna"


async def test_expansion_reaches_aged_farm(tmp_path, aged_farm_world):
    from xbotdetect.collect import CollectOptions, collect
    from xbotdetect.sources.offline import OfflineSource

    src = OfflineSource(aged_farm_world)
    opts = CollectOptions(followers=400, following=200, timeline=0, expand=20, expand_timeline=0)
    ds = await collect(src, ["TargetAccount"], opts)
    farm_expanded = [i for i in ds.expanded_ids if int(i) >= 50_000]
    assert len(farm_expanded) >= 15  # burst members are prioritised even though they are 10 years old
    old = await collect(
        src, ["TargetAccount"], CollectOptions(**{**opts.to_dict(), "expand_scope": "recent"})
    )
    assert not [i for i in old.expanded_ids if int(i) >= 50_000]  # the old date-gated behaviour misses them


def test_celebrity_and_verified_org_discounts():
    cfg = AnalysisConfig().resolve(AS_OF)
    star = Account(
        id="11",
        handle="FamousCEO",
        created_at=AS_OF - timedelta(days=97),
        followers=1_100_000,
        following=50,
        tweets=19,
        description="CEO",
        source="fxtwitter",
        banner_url="b",
    )
    k = keys(account_signals(star, AS_OF, cfg))
    assert "rapid_follower_growth" not in k and "dormant_audience" not in k  # beyond follow-farm scale
    gov = Account(
        id="12",
        handle="AgencyHead",
        created_at=AS_OF - timedelta(days=100),
        followers=50_000,
        following=100,
        tweets=30,
        description="",
        source="fxtwitter",
        verified=True,
        verified_type="government",
    )
    plain = Account(**{**gov.__dict__, "id": "13", "verified_type": None, "verified": False})
    assert combine(account_signals(gov, AS_OF, cfg)) < 0.5 * combine(account_signals(plain, AS_OF, cfg))


def test_cluster_needs_structure_and_anomaly():
    from xbotdetect.analysis.network import cluster_score

    dense = [Signal("density", "", 1.0, 0.35, ""), Signal("reciprocity", "", 1.0, 0.2, "")]
    anomalous = [Signal("creation_cohort", "", 1.0, 0.35, ""), Signal("member_scores", "", 1.0, 0.3, "")]
    real_community = cluster_score(dense, "neighbours")
    farm = cluster_score(dense + anomalous, "neighbours")
    assert real_community < 0.3 < 0.5 < farm  # 0.5 = cluster_flag_threshold
    assert cluster_score(anomalous, "neighbours") == 0  # no follow structure, no cluster
    assert cluster_score(dense + anomalous, "neighbours", org_share=1.0) == real_community  # verified orgs
    assert cluster_score(anomalous, "seed-group") > 0.5  # user-chosen group: composition alone counts


def test_young_followers_ignored_when_sample_is_a_sliver(farm_world):
    from xbotdetect.analysis.network import seed_network_signals

    r = analyze(farm_world)
    nb = r.neighborhoods["1"]["followers"]
    nb.recent_share = 0.95
    nb.total = 240_000_000  # 400 newest followers of a mega account = the last few minutes
    assert "followers_mostly_new" not in keys(seed_network_signals(nb, None, None, r.config))
    nb.total = 450
    assert "followers_mostly_new" in keys(seed_network_signals(nb, None, None, r.config))


def _origin(location, source, based, blocked=None, accurate=None):
    acc = Account(
        id="20",
        handle="someone",
        created_at=AS_OF - timedelta(days=2000),
        followers=300,
        following=300,
        tweets=3000,
        description="d",
        location=location,
        source="fxtwitter",
        banner_url="b",
        about=About(based_in=based, source=source, location_accurate=accurate),
    )
    cfg = AnalysisConfig() if blocked is None else AnalysisConfig(x_blocked_countries=frozenset(blocked))
    return {s.key: s for s in account_signals(acc, AS_OF, cfg.resolve(AS_OF))}


def test_vpn_from_country_where_x_is_blocked_is_not_a_mismatch():
    # Russian user, Russian app store, connecting via a Dutch VPN because X is blocked in Russia.
    sig = _origin("Москва", "Russian Federation Android App", "Netherlands")
    assert sig["origin_mismatch"].score <= 0.1 and "blocked in Russia" in sig["origin_mismatch"].detail
    vpn = _origin("Москва", "Russian Federation Android App", "Netherlands", accurate=False)
    assert vpn["location_inaccurate"].score <= 0.2  # VPN expected there
    # Without the blocked-country list the same pattern is a moderate mismatch.
    assert (
        _origin("Москва", "Russian Federation Android App", "Netherlands", blocked=[])[
            "origin_mismatch"
        ].score
        == 0.4
    )


def test_persona_mismatch_vs_relocation():
    # Claims Germany, registered via the Russian app store and X places it in Russia: persona mismatch.
    assert (
        _origin("Berlin, Germany", "Russian Federation Android App", "Russian Federation")[
            "origin_mismatch"
        ].score
        == 1.0
    )
    # Claims Germany and X places it in Germany, but it signed up in Russia: fits an emigrant.
    assert (
        _origin("Berlin, Germany", "Russian Federation App Store", "Germany")["origin_mismatch"].score == 0.3
    )
    # Ordinary country, only "based in" differs: travel, relocation or VPN.
    assert _origin("Texas", "United States App Store", "Nigeria")["origin_mismatch"].score == 0.4
    assert "origin_mismatch" not in _origin("Berlin", "Germany Android App", "Germany")
    # Claims Russia but US app store + US "based in": common circumvention in a blocked country.
    assert (
        _origin("Российская Федерация", "United States Android App", "United States")["origin_mismatch"].score
        == 0.3
    )
