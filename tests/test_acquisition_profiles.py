from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.acquisition.config import apply_acquisition_profile, build_actor_input
from trendforge.acquisition.errors import AcquisitionError
from trendforge.acquisition.normalize import (
    normalize_instagram_creator_reel,
    normalize_tiktok_fresh_item,
    normalize_tiktok_trending_item,
)
from trendforge.acquisition.provider import ActorRunResult
from trendforge.acquisition.sampling import compare_profile_rows, summarize_population
from trendforge.acquisition.service import run_acquisition
from trendforge.config import load_data_sources_config
from trendforge.db import get_session_factory, init_db
from trendforge.models import AcquisitionRun, AnalysisStatus, ContentCandidate

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 8, 16, 22, 0, tzinfo=timezone.utc)
RECENT = (NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z")

CFG = {
    "apify": {
        "enabled": True,
        "actors": {
            "tiktok_discovery": "clockworks/tiktok-scraper",
            "tiktok_trending": "xtracto/tiktok-trending-scraper",
            "tiktok_fresh_search": "clockworks/tiktok-scraper",
            "instagram_reels": "apify/instagram-hashtag-scraper",
            "instagram_creator_reels": "instagram-scraper/instagram-profile-reels-scraper",
            "tiktok": {
                "trending": {"actor_id": "xtracto/tiktok-trending-scraper", "enabled": True},
                "fresh_search": {"actor_id": "clockworks/tiktok-scraper", "enabled": True},
            },
            "instagram": {
                "creator_reels": {
                    "actor_id": "instagram-scraper/instagram-profile-reels-scraper",
                    "enabled": True,
                }
            },
        },
    },
    "profiles": {
        "tiktok_trending": {
            "source": "tiktok",
            "role": "trending",
            "actor_key": "tiktok_trending",
            "enabled": True,
            "total_limit_key": "limit",
            "default_limit": 25,
            "max_limit": 50,
            "max_short_seconds": 60,
            "actor_input": {"content_type": "video", "country_code": "US", "limit": 25},
        },
        "tiktok_fresh_search": {
            "source": "tiktok",
            "role": "fresh_search",
            "actor_key": "tiktok_fresh_search",
            "enabled": True,
            "queries": ["AI", "comedy", "POV"],
            "limit_input_keys": ["resultsPerPage"],
            "default_limit": 25,
            "max_limit": 50,
            "max_short_seconds": 60,
            "actor_input": {
                "searchSection": "/video",
                "videoSearchSorting": "LATEST",
                "videoSearchDateFilter": "PAST_24_HOURS",
                "resultsPerPage": 25,
                "shouldDownloadVideos": False,
            },
        },
        "instagram_creator_reels": {
            "source": "instagram",
            "role": "creator_reels",
            "actor_key": "instagram_creator_reels",
            "enabled": True,
            "creators": ["saturdaynightlive", "dudeperfect"],
            "creator_input_key": "instagramUsernames",
            "limit_input_keys": ["postsPerProfile"],
            "min_per_query": 5,
            "default_limit": 25,
            "max_limit": 50,
            "max_short_seconds": 90,
            "actor_input": {"postsPerProfile": 12},
        },
        "tiktok_hashtag": {
            "source": "tiktok",
            "role": "hashtag",
            "actor_key": "tiktok_discovery",
            "enabled": True,
            "status": "deprecated_for_discovery",
        },
    },
    "sources": {
        "tiktok": {
            "enabled": True,
            "provider": "apify",
            "actor_key": "tiktok_discovery",
            "default_limit": 25,
            "max_limit": 100,
            "max_short_seconds": 60,
            "limit_input_keys": ["resultsPerPage"],
            "actor_input": {"hashtags": ["pov"], "resultsPerPage": 25},
        },
        "instagram": {
            "enabled": True,
            "provider": "apify",
            "actor_key": "instagram_reels",
            "default_limit": 25,
            "max_limit": 100,
            "max_short_seconds": 60,
            "limit_input_keys": ["resultsLimit"],
            "actor_input": {"hashtags": ["pov"], "resultsLimit": 25},
        },
    },
}


def _load(name: str) -> list[dict]:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    for item in raw:
        if item.get("createTimeISO") == "REPLACE_RECENT":
            item["createTimeISO"] = RECENT
        if item.get("taken_at") == "REPLACE_RECENT":
            item["taken_at"] = RECENT
    return raw


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setattr("trendforge.acquisition.service.DATA_DIR", tmp_path)
    path = tmp_path / "profiles.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


class FakeApify:
    def __init__(self, items, cost=0.05, status="SUCCEEDED"):
        self.items = items
        self.calls = []
        self.cost = cost
        self.status = status

    def run_actor_result(self, actor_id, run_input, **kwargs):
        self.calls.append({"actor_id": actor_id, "input": run_input, "kwargs": kwargs})
        return ActorRunResult(
            actor_id=actor_id,
            items=list(self.items),
            run_id="p-run",
            dataset_id="p-ds",
            status=self.status,
            actual_cost=self.cost,
            currency="USD" if self.cost is not None else None,
        )


def test_actors_are_configurable_not_hardcoded():
    cfg = load_data_sources_config()
    assert cfg["apify"]["actors"]["tiktok"]["trending"]["actor_id"] == "xtracto/tiktok-trending-scraper"
    assert cfg["apify"]["actors"]["tiktok"]["fresh_search"]["actor_id"] == "clockworks/tiktok-scraper"
    assert (
        cfg["apify"]["actors"]["instagram"]["creator_reels"]["actor_id"]
        == "instagram-scraper/instagram-profile-reels-scraper"
    )
    tiktok_py = Path(__file__).resolve().parents[1] / "src/trendforge/acquisition/tiktok.py"
    ig_py = Path(__file__).resolve().parents[1] / "src/trendforge/acquisition/instagram.py"
    text = tiktok_py.read_text(encoding="utf-8") + ig_py.read_text(encoding="utf-8")
    assert "xtracto/" not in text
    assert "instagram-scraper/" not in text


def test_trending_actor_input_uses_schema_fields():
    cfg = apply_acquisition_profile(CFG, "tiktok", "tiktok_trending")
    payload = build_actor_input(cfg["sources"]["tiktok"], 25)
    assert payload["content_type"] == "video"
    assert payload["country_code"] == "US"
    assert payload["limit"] == 25
    assert "hashtags" not in payload
    assert "resultsPerPage" not in payload
    live = apply_acquisition_profile(load_data_sources_config(), "tiktok", "tiktok_trending")
    live_payload = build_actor_input(live["sources"]["tiktok"], 25)
    assert live_payload["content_type"] == "video"
    assert live_payload["country_code"] == "US"
    assert live_payload["limit"] == 25
    assert "resultsPerPage" not in live_payload
    assert "hashtags" not in live_payload


def test_fresh_search_actor_input_latest_and_24h():
    cfg = apply_acquisition_profile(CFG, "tiktok", "tiktok_fresh_search")
    payload = build_actor_input(cfg["sources"]["tiktok"], 25)
    assert payload["searchSection"] == "/video"
    assert payload["videoSearchSorting"] == "LATEST"
    assert payload["videoSearchDateFilter"] == "PAST_24_HOURS"
    assert payload["searchQueries"] == ["AI", "comedy", "POV"]
    assert payload["resultsPerPage"] == 8
    assert "hashtags" not in payload


def test_live_fresh_search_query_mix_drops_story():
    live = apply_acquisition_profile(load_data_sources_config(), "tiktok", "tiktok_fresh_search")
    payload = build_actor_input(live["sources"]["tiktok"], 25)
    queries = [str(q) for q in payload["searchQueries"]]
    assert "story" not in {q.lower() for q in queries}
    assert "animal" in queries
    assert "funny" in queries
    assert payload["videoSearchSorting"] == "LATEST"
    assert payload["videoSearchDateFilter"] == "PAST_24_HOURS"
    assert len(queries) <= 10


def test_instagram_creator_input():
    cfg = apply_acquisition_profile(CFG, "tiktok", "tiktok_trending")
    with pytest.raises(AcquisitionError, match="instagram"):
        apply_acquisition_profile(CFG, "instagram", "tiktok_trending")
    cfg = apply_acquisition_profile(CFG, "instagram", "instagram_creator_reels")
    payload = build_actor_input(cfg["sources"]["instagram"], 25)
    assert payload["instagramUsernames"] == ["saturdaynightlive", "dudeperfect"]
    assert payload["postsPerProfile"] >= 5


def test_empty_creators_rejected():
    cfg = json.loads(json.dumps(CFG))
    cfg["profiles"]["instagram_creator_reels"]["creators"] = []
    with pytest.raises(AcquisitionError, match="creators list"):
        apply_acquisition_profile(cfg, "instagram", "instagram_creator_reels")


def test_live_instagram_creators_are_configured():
    live = load_data_sources_config()
    creators = live["profiles"]["instagram_creator_reels"]["creators"]
    assert 5 <= len(creators) <= 10
    assert "saturdaynightlive" not in creators
    assert "thetryguys" not in creators
    assert "zachking" in creators
    assert "" not in creators
    assert all(str(c).strip() for c in creators)
    ig_py = Path(__file__).resolve().parents[1] / "src/trendforge/acquisition/instagram.py"
    text = ig_py.read_text(encoding="utf-8")
    for handle in creators:
        assert handle not in text


def test_normalize_trending_metrics_and_region():
    item = normalize_tiktok_trending_item(_load("tiktok_trending.json")[0], max_short_seconds=60)
    assert item is not None
    assert item.external_id == "trend1"
    assert item.views == 12000
    assert item.likes == 900
    assert item.shares == 12
    assert item.saves == 30
    assert item.creator == "smalltrend"
    assert item.creator_followers == 8400
    assert item.audio == "original sound - smalltrend"
    assert item.region_signal == "US"
    assert item.source_query == "trending"
    assert item.search_rank == 1
    assert item.is_short is True


def test_normalize_trending_old_long_language_and_warning():
    items = _load("tiktok_trending.json")
    old = normalize_tiktok_trending_item(items[1])
    long = normalize_tiktok_trending_item(items[2])
    es = normalize_tiktok_trending_item(items[3])
    warn = normalize_tiktok_trending_item(items[4])
    from trendforge.acquisition.sampling import age_hours

    assert old is not None and age_hours(old.published_at, now=NOW) > 24
    assert long is not None and long.is_short is False
    assert es is not None and es.language_signal == "non_en"
    assert warn is None
    unknown = normalize_tiktok_trending_item(
        {
            "id": "trend-un",
            "desc": "emoji only",
            "textLanguage": "un",
            "author": {"uniqueId": "unk"},
            "stats": {"playCount": 100},
            "video": {"duration": 12},
            "_input": {"country_code": "US"},
        }
    )
    assert unknown is not None
    assert unknown.language_signal == "unknown"


def test_normalize_fresh_search_query():
    item = normalize_tiktok_fresh_item(_load("tiktok_fresh_search.json")[0])
    assert item is not None
    assert item.source_query == "comedy"
    assert item.language_signal == "en"
    assert item.shares == 1


def test_normalize_creator_reel_followers_and_timestamp():
    item = normalize_instagram_creator_reel(_load("instagram_creator_reels.json")[0])
    assert item is not None
    assert item.external_id == "DVj4je6ktQW"
    assert item.creator == "saturdaynightlive"
    assert item.creator_followers == 45000000
    assert item.views == 9200
    assert item.likes == 400
    assert item.comments == 12
    assert item.shares is None
    assert item.published_at is not None
    assert item.audio == "Original audio"
    photo = normalize_instagram_creator_reel(_load("instagram_creator_reels.json")[2])
    assert photo is None


def test_trending_run_dedupe_and_cost(db: Session):
    items = _load("tiktok_trending.json")
    first = items[0]
    second = dict(first)
    second["_rank"] = 9
    client = FakeApify([first, second], cost=0.02)
    run = run_acquisition(
        db, "tiktok", profile="tiktok_trending", limit=25, client=client, cfg=CFG
    )
    assert run.profile == "tiktok_trending"
    assert run.actor_id == "xtracto/tiktok-trending-scraper"
    assert client.calls[0]["input"]["country_code"] == "US"
    assert run.items_new == 1
    assert run.items_duplicate == 1
    assert run.actual_cost == 0.02
    row = db.query(ContentCandidate).one()
    assert row.analysis_status == AnalysisStatus.SKIPPED
    assert "tiktok_trending" in (row.acquisition_profiles or [])
    assert (row.raw_metadata or {}).get("profiles_that_found_candidate") == ["tiktok_trending"]
    quality = (run.run_metadata_json or {})["sampling_quality"]
    assert quality["recent_rate"] is not None
    assert (run.run_metadata_json or {})["population"]["discovery_yield"] == 0.5


def test_fresh_search_run(db: Session):
    client = FakeApify(_load("tiktok_fresh_search.json"), cost=0.09)
    run = run_acquisition(
        db, "tiktok", profile="tiktok_fresh_search", limit=25, client=client, cfg=CFG
    )
    assert run.actor_id == "clockworks/tiktok-scraper"
    assert client.calls[0]["input"]["videoSearchSorting"] == "LATEST"
    assert client.calls[0]["input"]["videoSearchDateFilter"] == "PAST_24_HOURS"
    assert run.items_new == 2
    ids = {c.external_id for c in db.query(ContentCandidate).all()}
    assert ids == {"fresh1", "fresh-old"}
    qy = (run.run_metadata_json or {}).get("query_yield") or {}
    assert qy["comedy"]["new"] == 1
    assert qy["POV"]["new"] == 1
    row = db.query(ContentCandidate).filter_by(external_id="fresh1").one()
    assert (row.raw_metadata or {}).get("queries_that_found_candidate") == ["comedy"]


def test_cross_profile_provenance(db: Session):
    trend = _load("tiktok_trending.json")[0]
    trend["id"] = "shared1"
    trend["author"] = {"uniqueId": "shared"}
    fresh = {
        "id": "shared1",
        "webVideoUrl": "https://www.tiktok.com/@shared/video/shared1",
        "text": "same video",
        "createTimeISO": RECENT,
        "playCount": 99,
        "authorMeta": {"name": "shared"},
        "videoMeta": {"duration": 10},
        "searchQuery": "POV",
    }
    run_acquisition(db, "tiktok", profile="tiktok_trending", client=FakeApify([trend]), cfg=CFG)
    run = run_acquisition(
        db, "tiktok", profile="tiktok_fresh_search", client=FakeApify([fresh]), cfg=CFG
    )
    assert db.query(ContentCandidate).count() == 1
    assert run.items_duplicate == 1
    row = db.query(ContentCandidate).one()
    found = row.acquisition_profiles or []
    assert "tiktok_trending" in found
    assert "tiktok_fresh_search" in found
    queries = (row.raw_metadata or {}).get("queries_that_found_candidate") or []
    assert "POV" in queries
    assert "trending" in queries


def test_instagram_creator_run(db: Session):
    run = run_acquisition(
        db,
        "instagram",
        profile="instagram_creator_reels",
        client=FakeApify(_load("instagram_creator_reels.json"), cost=0.0),
        cfg=CFG,
    )
    assert run.profile == "instagram_creator_reels"
    assert run.items_new == 2
    assert run.items_rejected == 1
    row = db.query(ContentCandidate).filter_by(external_id="DVj4je6ktQW").one()
    assert row.channel_subscriber_count == 45000000
    history = (row.raw_metadata or {}).get("creator_history")
    assert history is not None
    assert history["historical_content_count"] == 1
    assert history["creator_lift"] is None
    assert history["creator_baseline_views"] is None


def test_empty_dataset(db: Session):
    run = run_acquisition(
        db, "tiktok", profile="tiktok_trending", client=FakeApify([]), cfg=CFG
    )
    assert run.status == "succeeded"
    assert run.items_found == 0
    assert (run.run_metadata_json or {})["population"]["discovery_yield"] is None


def test_malformed_item_rejected(db: Session):
    run = run_acquisition(
        db,
        "tiktok",
        profile="tiktok_trending",
        client=FakeApify([{"desc": "no id"}]),
        cfg=CFG,
    )
    assert run.items_rejected == 1
    assert db.query(ContentCandidate).count() == 0


def test_compare_profile_rows_uses_latest_succeeded():
    older = AcquisitionRun(
        source="tiktok",
        provider="apify",
        actor_id="clockworks/tiktok-scraper",
        profile="tiktok_hashtag",
        status="succeeded",
        items_found=24,
        items_new=0,
        items_duplicate=9,
        items_rejected=15,
        actual_cost=0.0898,
        currency="USD",
        started_at=NOW - timedelta(hours=2),
        run_metadata_json={
            "sampling_quality": {"recent_rate": 0.04, "english_rate": 0.83},
            "population": {"discovery_yield": 0.0, "high_signal_candidates": 0, "high_signal_yield": 0.0},
        },
    )
    newer = AcquisitionRun(
        source="tiktok",
        provider="apify",
        actor_id="xtracto/tiktok-trending-scraper",
        profile="tiktok_trending",
        status="succeeded",
        items_found=10,
        items_new=4,
        items_duplicate=1,
        items_rejected=5,
        actual_cost=0.02,
        currency="USD",
        started_at=NOW,
        run_metadata_json={
            "profile_label": "TikTok Trending",
            "sampling_quality": {"recent_rate": 0.8, "english_rate": 0.0, "unknown_language_rate": 1.0},
            "population": {"discovery_yield": 0.8, "high_signal_candidates": 0, "high_signal_yield": 0.0},
        },
    )
    rows = compare_profile_rows([older, newer])
    by_name = {r["profile"]: r for r in rows}
    assert by_name["tiktok_hashtag"]["new"] == 0
    assert by_name["tiktok_trending"]["raw"] == 10
    assert by_name["tiktok_trending"]["cost"] == 0.02


def test_query_provenance_dedupes_across_search_terms(db: Session):
    first = _load("tiktok_fresh_search.json")[0]
    second = dict(first)
    second["searchQuery"] = "animal"
    run = run_acquisition(
        db,
        "tiktok",
        profile="tiktok_fresh_search",
        client=FakeApify([first, second]),
        cfg=CFG,
    )
    assert run.items_new == 1
    assert run.items_duplicate == 1
    row = db.query(ContentCandidate).one()
    found = (row.raw_metadata or {}).get("queries_that_found_candidate") or []
    assert "comedy" in found
    assert "animal" in found
    assert db.query(ContentCandidate).count() == 1


def test_query_yield_and_recentness(db: Session):
    from trendforge.acquisition.sampling import summarize_query_yield

    summary = summarize_query_yield(
        [
            {"query": "animal", "outcome": "new"},
            {"query": "animal", "outcome": "rejected_not_short"},
            {"query": "POV", "outcome": "new"},
            {"query": None, "outcome": "rejected_unnormalizable"},
        ]
    )
    assert summary["animal"]["raw"] == 2
    assert summary["animal"]["new"] == 1
    assert summary["animal"]["new_rate"] == 0.5
    assert summary["POV"]["new_rate"] == 1.0
    assert summary["(none)"]["rejected"] == 1


def test_creator_baseline_lift_after_batch(db: Session):
    items = []
    for i, views in enumerate([100, 200, 300, 800], start=1):
        items.append(
            {
                "shortcode": f"baseReel{i}",
                "is_video": True,
                "video_duration": 10,
                "play_count": views,
                "like_count": 1,
                "comment_count": 0,
                "taken_at": RECENT,
                "reel_url": f"https://www.instagram.com/reel/baseReel{i}/",
                "owner": {"id": "ownB", "username": "baselineguy", "followers": 1000},
            }
        )
    run = run_acquisition(
        db,
        "instagram",
        profile="instagram_creator_reels",
        client=FakeApify(items),
        cfg=CFG,
    )
    assert run.items_new == 4
    rows = db.query(ContentCandidate).all()
    lifts = []
    for row in rows:
        hist = (row.raw_metadata or {}).get("creator_history") or {}
        assert hist.get("creator_baseline_views") is not None
        assert hist.get("baseline_sample_size") == 3
        lifts.append(hist.get("creator_lift"))
        if row.views == 800:
            assert hist["creator_lift"] == 4.0
    assert all(v is not None for v in lifts)
    stats = (run.run_metadata_json or {}).get("creator_baselines") or {}
    assert stats["creators_processed"] == 1
    assert stats["creators_with_baseline"] == 1
    assert stats["reels_with_creator_lift"] == 4
    assert stats["median_creator_lift"] is not None


def test_creator_baseline_null_rules(db: Session):
    insufficient = [
        {
            "shortcode": "onlyOne",
            "is_video": True,
            "video_duration": 9,
            "play_count": 50,
            "taken_at": RECENT,
            "reel_url": "https://www.instagram.com/reel/onlyOne/",
            "owner": {"id": "ownC", "username": "thin", "followers": 10},
        },
        {
            "shortcode": "onlyTwo",
            "is_video": True,
            "video_duration": 9,
            "play_count": 80,
            "taken_at": RECENT,
            "reel_url": "https://www.instagram.com/reel/onlyTwo/",
            "owner": {"id": "ownC", "username": "thin", "followers": 10},
        },
    ]
    run_acquisition(
        db, "instagram", profile="instagram_creator_reels", client=FakeApify(insufficient), cfg=CFG
    )
    for row in db.query(ContentCandidate).all():
        hist = (row.raw_metadata or {}).get("creator_history") or {}
        assert hist.get("creator_baseline_views") is None
        assert hist.get("creator_lift") is None

    zeros = [
        {
            "shortcode": f"zero{i}",
            "is_video": True,
            "video_duration": 8,
            "play_count": 0,
            "taken_at": RECENT,
            "reel_url": f"https://www.instagram.com/reel/zero{i}/",
            "owner": {"id": "ownZ", "username": "zeroed", "followers": 9},
        }
        for i in range(4)
    ]
    zeros[-1]["play_count"] = 40
    zeros[-1]["shortcode"] = "zeroHit"
    zeros[-1]["reel_url"] = "https://www.instagram.com/reel/zeroHit/"
    run_acquisition(
        db, "instagram", profile="instagram_creator_reels", client=FakeApify(zeros), cfg=CFG
    )
    hit = db.query(ContentCandidate).filter_by(external_id="zeroHit").one()
    hist = (hit.raw_metadata or {}).get("creator_history") or {}
    assert hist.get("creator_baseline_views") == 0 or hist.get("creator_lift") is None
    assert hist.get("creator_lift") is None

    missing = [
        {
            "shortcode": f"miss{i}",
            "is_video": True,
            "video_duration": 8,
            "play_count": 120 if i < 3 else None,
            "taken_at": RECENT,
            "reel_url": f"https://www.instagram.com/reel/miss{i}/",
            "owner": {"id": "ownM", "username": "missingviews", "followers": 9},
        }
        for i in range(4)
    ]
    del missing[-1]["play_count"]
    run_acquisition(
        db, "instagram", profile="instagram_creator_reels", client=FakeApify(missing), cfg=CFG
    )
    miss = db.query(ContentCandidate).filter_by(external_id="miss3").one()
    hist = (miss.raw_metadata or {}).get("creator_history") or {}
    assert miss.views is None
    assert hist.get("creator_lift") is None


def test_compare_profile_rows_includes_creator_metrics():
    ig = AcquisitionRun(
        source="instagram",
        provider="apify",
        actor_id="instagram-scraper/instagram-profile-reels-scraper",
        profile="instagram_creator_reels",
        status="succeeded",
        items_found=16,
        items_new=13,
        items_duplicate=0,
        items_rejected=3,
        actual_cost=0.018,
        currency="USD",
        started_at=NOW,
        run_metadata_json={
            "profile_label": "Instagram Creator Reels",
            "sampling_quality": {"recent_rate": 0.0, "english_rate": 0.0, "unknown_language_rate": 1.0},
            "population": {
                "discovery_yield": 1.0,
                "recentness_yield": 0.0,
                "high_signal_candidates": 1,
                "creator_baselines": {
                    "creators_processed": 2,
                    "creators_with_baseline": 2,
                    "reels_with_creator_lift": 13,
                    "median_creator_lift": 1.4,
                },
            },
        },
    )
    rows = compare_profile_rows([ig])
    assert rows[0]["creators_processed"] == 2
    assert rows[0]["median_creator_lift"] == 1.4
    assert rows[0]["recentness_yield"] == 0.0


def test_observe_selects_fresh_search_profile(db: Session):
    from trendforge.discovery.tiktok import select_tiktok_observe_candidates

    keep = ContentCandidate(
        platform="tiktok",
        url="https://www.tiktok.com/@a/video/keep1",
        external_id="keep1",
        acquisition_profiles=["tiktok_fresh_search"],
        analysis_status=AnalysisStatus.SKIPPED,
    )
    skip = ContentCandidate(
        platform="tiktok",
        url="https://www.tiktok.com/@b/video/skip1",
        external_id="skip1",
        acquisition_profiles=["tiktok_trending"],
        analysis_status=AnalysisStatus.SKIPPED,
    )
    db.add_all([keep, skip])
    db.flush()
    rows = select_tiktok_observe_candidates(db, limit=10, profile="tiktok_fresh_search")
    assert [row.external_id for row in rows] == ["keep1"]
