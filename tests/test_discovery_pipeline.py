from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.db import get_session_factory, init_db
from trendforge.discovery.pipeline import (
    promote_top_candidates,
    run_discovery,
    run_observation_refresh,
    upsert_discovered_video,
)
from trendforge.discovery.provider import DiscoveredVideo, QuotaExhaustedError, SearchPage
from trendforge.discovery.youtube import YouTubeDiscoveryProvider
from trendforge.models import AnalysisStatus, CandidateObservation, ContentCandidate


NOW = datetime(2026, 8, 13, 18, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "disc.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


def _video(
    vid: str,
    *,
    views: int,
    query: str = "AI",
    channel: str = "UC1",
    hours_ago: float = 6,
    likes: int | None = 10,
    comments: int | None = 2,
    rank: int = 1,
) -> DiscoveredVideo:
    return DiscoveredVideo(
        external_id=vid,
        url=f"https://www.youtube.com/shorts/{vid}",
        title=f"Title {vid}",
        description="demo #shorts",
        channel_id=channel,
        channel_name=f"Chan {channel}",
        published_at=NOW - timedelta(hours=hours_ago),
        duration=42.0,
        views=views,
        likes=likes,
        comments=comments,
        is_short=True,
        source_query=query,
        search_rank=rank,
        raw={"id": vid, "views": views},
    )


def test_duplicate_candidate_handling(db: Session):
    a, created = upsert_discovered_video(db, _video("abc", views=100, query="AI"))
    db.commit()
    b, created2 = upsert_discovered_video(db, _video("abc", views=250, query="comedy"))
    db.commit()
    assert created is True
    assert created2 is False
    assert a.id == b.id
    assert db.query(ContentCandidate).count() == 1
    assert set(b.source_queries) == {"AI", "comedy"}
    assert db.query(CandidateObservation).filter_by(candidate_id=b.id).count() == 2
    assert b.views == 250


def test_observation_creation_and_velocity(db: Session):
    row, _ = upsert_discovered_video(
        db, _video("v1", views=120_000, hours_ago=8, query="funny")
    )
    db.commit()
    assert row.views_per_hour is not None
    assert row.age_hours is not None
    later = _video("v1", views=240_000, hours_ago=8, query="funny")
    later.raw = {"n": 2}
    upsert_discovered_video(db, later)
    db.commit()
    db.refresh(row)
    assert row.views == 240_000
    assert db.query(CandidateObservation).count() == 2


def test_promotion_top_n(db: Session):
    cfg = {
        "promote_top_n": 2,
        "promote_min_score": None,
        "min_creator_videos": 3,
        "queries": [],
        "analysis": {"high_signal_only": False},
    }
    for i, views in enumerate([10, 500_000, 200_000, 50], start=1):
        upsert_discovered_video(
            db, _video(f"p{i}", views=views, hours_ago=4, likes=1000, comments=50)
        )
    db.commit()
    n = promote_top_candidates(db, cfg=cfg, force_stub_analysis=True, analyze=True)
    assert n == 2
    promoted = db.query(ContentCandidate).filter(ContentCandidate.promoted_at.isnot(None)).all()
    assert len(promoted) == 2
    for row in promoted:
        assert row.analysis_status == AnalysisStatus.ANALYZED
        assert row.format_id is not None


class FakeProvider:
    name = "youtube"

    def __init__(self, pages: dict[str, SearchPage], videos: list[dict], channels: dict):
        self.pages = pages
        self.videos = videos
        self.channels = channels
        self.search_calls = 0
        self.search_queries: list[str] = []
        self.search_max_results: list[int] = []
        self.search_orders: list[str] = []
        self.search_region_codes: list[str | None] = []
        self.search_relevance_languages: list[str | None] = []
        self.video_id_requests: list[list[str]] = []

    def search(
        self,
        query,
        *,
        published_after,
        page_token=None,
        max_results=25,
        region_code=None,
        relevance_language=None,
        order="date",
    ):
        self.search_calls += 1
        self.search_queries.append(query)
        self.search_max_results.append(max_results)
        self.search_orders.append(order)
        self.search_region_codes.append(region_code)
        self.search_relevance_languages.append(relevance_language)
        page = self.pages.get((query, region_code), self.pages.get(query))
        if page is None:
            page = SearchPage(video_ids=[])
        return SearchPage(
            video_ids=page.video_ids[:max_results],
            next_page_token=page.next_page_token,
            raw=page.raw,
        )

    def get_videos(self, video_ids):
        self.video_id_requests.append(list(video_ids))
        wanted = set(video_ids)
        return [v for v in self.videos if v.get("id") in wanted]

    def get_channels(self, channel_ids):
        return {k: v for k, v in self.channels.items() if k in set(channel_ids)}


def _yt_item(
    vid: str,
    duration: str,
    views: str,
    channel="UC9",
    default_language=None,
    default_audio_language=None,
):
    snippet = {
        "title": vid,
        "description": "x",
        "channelId": channel,
        "channelTitle": "DemoChan",
        "publishedAt": (NOW - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "categoryId": "23",
        "thumbnails": {"default": {"url": "http://example/t.jpg"}},
    }
    if default_language is not None:
        snippet["defaultLanguage"] = default_language
    if default_audio_language is not None:
        snippet["defaultAudioLanguage"] = default_audio_language
    return {
        "id": vid,
        "snippet": snippet,
        "contentDetails": {"duration": duration},
        "statistics": {"viewCount": views, "likeCount": "10", "commentCount": "1"},
    }


def test_run_discovery_filters_non_shorts_and_dedupes_across_queries(db: Session):
    videos = [
        _yt_item("short1", "PT40S", "90000"),
        _yt_item("long1", "PT5M", "9000000"),
        _yt_item("short1", "PT40S", "90000"),
    ]
    provider = FakeProvider(
        pages={
            "AI": SearchPage(video_ids=["short1", "long1"]),
            "comedy": SearchPage(video_ids=["short1"]),
        },
        videos=videos,
        channels={"UC9": {"id": "UC9", "statistics": {"subscriberCount": "1000"}}},
    )
    cfg = {
        "queries": ["AI", "comedy"],
        "published_window": "last_24_hours",
        "windows": {"last_24_hours": 24},
        "max_results_per_query": 25,
        "max_pages_per_query": 1,
        "max_short_seconds": 60,
        "promote_top_n": 0,
        "min_creator_videos": 3,
        "region_code": None,
    }
    run = run_discovery(
        db, provider=provider, promote=False, cfg=cfg, now=NOW
    )
    assert run.candidates_found == 1
    assert run.new_candidates == 1
    assert db.query(ContentCandidate).count() == 1
    row = db.query(ContentCandidate).one()
    assert row.external_id == "short1"
    assert row.is_short is True
    assert "AI" in row.source_queries
    assert "comedy" in row.source_queries
    assert row.analysis_status == AnalysisStatus.SKIPPED


def test_observe_updates_same_candidate(db: Session):
    row, _ = upsert_discovered_video(db, _video("obs1", views=1000, hours_ago=3))
    db.commit()
    provider = FakeProvider(
        pages={},
        videos=[_yt_item("obs1", "PT30S", "5000", channel="UC1")],
        channels={"UC1": {"id": "UC1", "statistics": {"subscriberCount": "99"}}},
    )
    cfg = {"observe_top_n": 10, "max_short_seconds": 60, "min_creator_videos": 3}
    run = run_observation_refresh(db, provider=provider, cfg=cfg)
    db.refresh(row)
    assert run.observations_written == 1
    assert row.views == 5000
    assert db.query(CandidateObservation).filter_by(candidate_id=row.id).count() == 2


class QuotaProvider(YouTubeDiscoveryProvider):
    def __init__(self):
        super().__init__(api_key="fake")

    def search(self, *args, **kwargs):
        raise QuotaExhaustedError("quotaExceeded")

    def get_videos(self, video_ids):
        return []

    def get_channels(self, channel_ids):
        return {}


def test_quota_error_is_recorded(db: Session):
    cfg = {
        "queries": ["AI"],
        "published_window": "last_24_hours",
        "windows": {"last_24_hours": 24},
        "max_results_per_query": 5,
        "max_pages_per_query": 1,
        "max_short_seconds": 60,
        "promote_top_n": 0,
        "min_creator_videos": 3,
    }
    run = run_discovery(db, provider=QuotaProvider(), promote=False, cfg=cfg, now=NOW)
    assert run.api_errors
    assert run.api_errors[0]["type"] == "quota"
    assert run.candidates_found == 0


def test_pagination_collects_multiple_pages(db: Session):
    class PagingProvider:
        name = "youtube"

        def search(
            self,
            query,
            *,
            published_after,
            page_token=None,
            max_results=25,
            region_code=None,
            relevance_language=None,
            order="date",
        ):
            if page_token is None:
                return SearchPage(video_ids=["a"], next_page_token="p2", raw={"page": 1})
            return SearchPage(video_ids=["b"], next_page_token=None, raw={"page": 2})

        def get_videos(self, video_ids):
            return [_yt_item(i, "PT20S", "1000") for i in video_ids]

        def get_channels(self, channel_ids):
            return {}

    cfg = {
        "queries": ["AI"],
        "published_window": "last_24_hours",
        "windows": {"last_24_hours": 24},
        "max_results_per_query": 25,
        "max_pages_per_query": 2,
        "max_short_seconds": 60,
        "promote_top_n": 0,
        "min_creator_videos": 3,
    }
    run = run_discovery(db, provider=PagingProvider(), promote=False, cfg=cfg, now=NOW)
    ids = {c.external_id for c in db.query(ContentCandidate).all()}
    assert ids == {"a", "b"}
    assert run.candidates_found == 2


def test_discovery_limit_caps_collection_and_stats(db: Session):
    ids = [f"s{i}" for i in range(1, 8)]
    videos = [_yt_item(i, "PT20S", "1000") for i in ids]
    provider = FakeProvider(
        pages={
            "AI": SearchPage(video_ids=ids[:5]),
            "comedy": SearchPage(video_ids=ids[5:]),
        },
        videos=videos,
        channels={},
    )
    cfg = {
        "queries": ["AI", "comedy"],
        "published_window": "last_24_hours",
        "windows": {"last_24_hours": 24},
        "max_results_per_query": 25,
        "max_pages_per_query": 1,
        "max_short_seconds": 60,
        "promote_top_n": 0,
        "min_creator_videos": 3,
        "region_code": None,
    }
    run = run_discovery(db, provider=provider, promote=False, cfg=cfg, now=NOW, limit=3)
    stored = db.query(ContentCandidate).all()
    assert len(stored) == 3
    assert run.candidates_found == 3
    assert run.new_candidates == 3
    assert run.observations_written == 3
    assert provider.search_queries == ["AI"]
    assert provider.search_max_results == [3]
    assert run.queries_used == ["AI"]
    assert provider.video_id_requests
    assert len(provider.video_id_requests[0]) == 3


def test_discovery_without_limit_still_walks_all_queries(db: Session):
    provider = FakeProvider(
        pages={
            "AI": SearchPage(video_ids=["a"]),
            "comedy": SearchPage(video_ids=["b"]),
        },
        videos=[_yt_item("a", "PT20S", "1"), _yt_item("b", "PT20S", "1")],
        channels={},
    )
    cfg = {
        "queries": ["AI", "comedy"],
        "published_window": "last_24_hours",
        "windows": {"last_24_hours": 24},
        "max_results_per_query": 25,
        "max_pages_per_query": 1,
        "max_short_seconds": 60,
        "promote_top_n": 0,
        "min_creator_videos": 3,
        "region_code": None,
    }
    run = run_discovery(db, provider=provider, promote=False, cfg=cfg, now=NOW)
    assert provider.search_queries == ["AI", "comedy"]
    assert run.candidates_found == 2
    assert db.query(ContentCandidate).count() == 2


def test_broad_mode_skips_topic_queries_and_uses_viewcount(db: Session):
    provider = FakeProvider(
        pages={"": SearchPage(video_ids=["b1", "b2", "b3"])},
        videos=[_yt_item("b1", "PT20S", "9000"), _yt_item("b2", "PT20S", "8000"), _yt_item("b3", "PT20S", "7000")],
        channels={},
    )
    cfg = {
        "queries": ["AI", "comedy"],
        "published_window": "last_48_hours",
        "windows": {"last_24_hours": 24, "last_48_hours": 48},
        "max_results_per_query": 25,
        "max_pages_per_query": 1,
        "max_short_seconds": 60,
        "promote_top_n": 0,
        "min_creator_videos": 3,
        "region_code": None,
        "broad": {
            "search_terms": [""],
            "order": "viewCount",
            "published_window": "last_24_hours",
            "max_results": 25,
            "max_pages": 1,
        },
    }
    run = run_discovery(db, provider=provider, promote=False, cfg=cfg, now=NOW, mode="broad")
    assert provider.search_queries == [""]
    assert provider.search_orders == ["viewCount"]
    assert "AI" not in provider.search_queries
    assert run.kind == "discover_broad"
    assert run.queries_used == ["broad"]
    assert run.candidates_found == 3
    row = db.query(ContentCandidate).filter_by(external_id="b1").one()
    assert "broad" in (row.source_queries or [])


NA_PROFILES = {
    "north_america_english": {"language": "en", "regions": ["US", "CA"]},
    "global": {"language": None, "regions": []},
}


def _profile_cfg():
    cfg = {
        "queries": ["AI", "comedy"],
        "published_window": "last_48_hours",
        "windows": {"last_24_hours": 24, "last_48_hours": 48},
        "max_results_per_query": 25,
        "max_pages_per_query": 1,
        "max_short_seconds": 60,
        "promote_top_n": 0,
        "min_creator_videos": 3,
        "region_code": None,
        "default_profile": "north_america_english",
        "profiles": NA_PROFILES,
        "broad": {
            "search_terms": [""],
            "order": "viewCount",
            "published_window": "last_24_hours",
            "max_results": 25,
            "max_pages": 1,
            "profile": "north_america_english",
            "unconstrained_query": "#shorts",
        },
    }
    return cfg


def test_topic_mode_ignores_default_profile(db: Session):
    provider = FakeProvider(
        pages={"AI": SearchPage(video_ids=["a"]), "comedy": SearchPage(video_ids=["b"])},
        videos=[_yt_item("a", "PT20S", "1"), _yt_item("b", "PT20S", "1")],
        channels={},
    )
    run = run_discovery(db, provider=provider, promote=False, cfg=_profile_cfg(), now=NOW, mode="topics")
    assert provider.search_queries == ["AI", "comedy"]
    assert provider.search_region_codes == [None, None]
    assert provider.search_relevance_languages == [None, None]
    assert run.kind == "discover"
    notes = json.loads(run.notes)
    assert notes["discovery_profile"] is None


def test_broad_default_profile_uses_us_ca_and_english(db: Session):
    provider = FakeProvider(
        pages={"#shorts": SearchPage(video_ids=["n1", "n2"])},
        videos=[
            _yt_item("n1", "PT20S", "100", default_language="en"),
            _yt_item("n2", "PT20S", "90", default_audio_language="en-CA"),
        ],
        channels={},
    )
    run = run_discovery(db, provider=provider, promote=False, cfg=_profile_cfg(), now=NOW, mode="broad")
    assert provider.search_queries == ["#shorts", "#shorts"]
    assert provider.search_region_codes == ["US", "CA"]
    assert provider.search_relevance_languages == ["en", "en"]
    assert provider.search_orders == ["viewCount", "viewCount"]
    notes = json.loads(run.notes)
    assert notes["discovery_profile"] == "north_america_english"
    row = db.query(ContentCandidate).filter_by(external_id="n1").one()
    assert row.raw_metadata["discovery_profile"] == "north_america_english"
    assert row.raw_metadata["language_signal"] == "en"
    assert row.raw_metadata["region_signal"] == "US"
    assert row.raw_metadata["language_evidence"]["defaultLanguage"] == "en"


def test_explicit_global_profile_skips_region_and_language_bias(db: Session):
    provider = FakeProvider(
        pages={"#shorts": SearchPage(video_ids=["g1"])},
        videos=[_yt_item("g1", "PT20S", "10")],
        channels={},
    )
    run = run_discovery(
        db,
        provider=provider,
        promote=False,
        cfg=_profile_cfg(),
        now=NOW,
        mode="broad",
        profile_name="global",
    )
    assert provider.search_region_codes == [None]
    assert provider.search_relevance_languages == [None]
    notes = json.loads(run.notes)
    assert notes["discovery_profile"] == "global"


def test_english_kept_non_english_filtered_unknown_kept(db: Session):
    provider = FakeProvider(
        pages={"#shorts": SearchPage(video_ids=["en1", "ja1", "unk1"])},
        videos=[
            _yt_item("en1", "PT20S", "10", default_language="en-US"),
            _yt_item("ja1", "PT20S", "99", default_audio_language="ja"),
            _yt_item("unk1", "PT20S", "11"),
        ],
        channels={},
    )
    run = run_discovery(db, provider=provider, promote=False, cfg=_profile_cfg(), now=NOW, mode="broad")
    ids = {c.external_id for c in db.query(ContentCandidate).all()}
    assert ids == {"en1", "unk1"}
    notes = json.loads(run.notes)
    assert notes["language_filtered"] == 1
    unk = db.query(ContentCandidate).filter_by(external_id="unk1").one()
    assert unk.raw_metadata["language_signal"] == "unknown"
    en = db.query(ContentCandidate).filter_by(external_id="en1").one()
    assert en.raw_metadata["language_signal"] == "en"


def test_empty_us_falls_through_to_ca(db: Session):
    provider = FakeProvider(
        pages={
            ("#shorts", "US"): SearchPage(video_ids=[]),
            ("#shorts", "CA"): SearchPage(video_ids=["ca1"]),
        },
        videos=[_yt_item("ca1", "PT20S", "50", default_language="en")],
        channels={},
    )
    run = run_discovery(db, provider=provider, promote=False, cfg=_profile_cfg(), now=NOW, mode="broad")
    assert provider.search_region_codes == ["US", "CA"]
    notes = json.loads(run.notes)
    assert notes["search_results_by_region"]["US"] == 0
    assert notes["search_results_by_region"]["CA"] == 1
    assert run.candidates_found == 1
    row = db.query(ContentCandidate).filter_by(external_id="ca1").one()
    assert row.raw_metadata["region_signal"] == "CA"


def test_successful_us_and_ca_searches(db: Session):
    provider = FakeProvider(
        pages={
            ("#shorts", "US"): SearchPage(video_ids=["us1"]),
            ("#shorts", "CA"): SearchPage(video_ids=["ca1"]),
        },
        videos=[
            _yt_item("us1", "PT20S", "50", default_language="en-US"),
            _yt_item("ca1", "PT20S", "40", default_language="en-CA"),
        ],
        channels={},
    )
    run = run_discovery(db, provider=provider, promote=False, cfg=_profile_cfg(), now=NOW, mode="broad")
    notes = json.loads(run.notes)
    assert notes["search_results_by_region"]["US"] == 1
    assert notes["search_results_by_region"]["CA"] == 1
    assert {c.external_id for c in db.query(ContentCandidate).all()} == {"us1", "ca1"}
    assert notes["search_results"] == 2


def test_no_result_search_is_explicit(db: Session):
    provider = FakeProvider(pages={"#shorts": SearchPage(video_ids=[])}, videos=[], channels={})
    run = run_discovery(db, provider=provider, promote=False, cfg=_profile_cfg(), now=NOW, mode="broad")
    notes = json.loads(run.notes)
    assert notes["search_results"] == 0
    assert run.candidates_found == 0
    from trendforge.discovery.pipeline import format_discovery_diagnostics

    text = format_discovery_diagnostics(run)
    assert "Search returned 0 candidates with profile north_america_english." in text
    assert "search_results=0" in text


def test_shorts_filter_rejects_long_keeps_short(db: Session):
    provider = FakeProvider(
        pages={"#shorts": SearchPage(video_ids=["ok", "long"])},
        videos=[_yt_item("ok", "PT20S", "10"), _yt_item("long", "PT5M", "99")],
        channels={},
    )
    run = run_discovery(db, provider=provider, promote=False, cfg=_profile_cfg(), now=NOW, mode="broad")
    notes = json.loads(run.notes)
    assert notes["shorts_eligible"] == 1
    assert notes["shorts_rejected"] == 1
    assert {c.external_id for c in db.query(ContentCandidate).all()} == {"ok"}


def test_unconstrained_query_is_sent_instead_of_omitting_q(db: Session):
    provider = FakeProvider(
        pages={"#shorts": SearchPage(video_ids=["s1"])},
        videos=[_yt_item("s1", "PT20S", "10")],
        channels={},
    )
    run = run_discovery(db, provider=provider, promote=False, cfg=_profile_cfg(), now=NOW, mode="broad")
    assert provider.search_queries == ["#shorts", "#shorts"]
    notes = json.loads(run.notes)
    assert notes["search_q"] == ["#shorts"]
    assert run.queries_used == ["broad"]

