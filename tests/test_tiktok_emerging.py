from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.acquisition.config import apply_acquisition_mode, build_actor_input
from trendforge.acquisition.errors import AcquisitionError
from trendforge.acquisition.normalize import normalize_tiktok_item
from trendforge.acquisition.provider import ActorRunResult
from trendforge.acquisition.sampling import age_hours, summarize_population
from trendforge.acquisition.service import run_acquisition
from trendforge.config import load_data_sources_config
from trendforge.db import get_session_factory, init_db
from trendforge.models import AnalysisStatus, CandidateObservation, ContentCandidate

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 8, 16, 22, 0, tzinfo=timezone.utc)
RECENT = (NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z")

CFG = {
    "apify": {
        "enabled": True,
        "actors": {
            "tiktok_discovery": "clockworks/tiktok-scraper",
            "tiktok_emerging": "coregent/tiktok-keyword-search-scraper",
            "instagram_reels": "test/ig-actor",
            "youtube_shorts": None,
        },
    },
    "experiments": {
        "tiktok_emerging_breakout": {
            "enabled": True,
            "actor_key": "tiktok_emerging",
            "window": "24h",
            "regions": ["US", "CA"],
            "first_run_queries": ["POV", "skit", "comedy", "storytime", "plot twist"],
            "default_limit": 40,
            "max_limit": 75,
            "timeout_secs": 300,
            "max_charge_usd": 0.5,
            "max_short_seconds": 60,
            "limit_input_keys": ["maxItemsPerKeyword"],
            "total_limit_key": "maxTotalResults",
            "actor_input": {
                "searchType": "video",
                "sort": "latest",
                "region": "US",
                "datePosted": "last24Hours",
                "deduplicateAcrossKeywords": True,
            },
        }
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
            "limit_input_keys": ["resultsLimit"],
            "actor_input": {"hashtags": ["pov"], "resultsLimit": 25},
        },
    },
}


def _items() -> list[dict]:
    raw = json.loads((FIXTURES / "tiktok_keyword_search.json").read_text(encoding="utf-8"))
    for item in raw:
        if item.get("createTimeISO") == "REPLACE_RECENT":
            item["createTimeISO"] = RECENT
    return raw


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setattr("trendforge.acquisition.service.DATA_DIR", tmp_path)
    path = tmp_path / "emerging.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


class FakeApify:
    def __init__(self, items, cost=0.04):
        self.items = items
        self.calls = []

    def run_actor_result(self, actor_id, run_input, **kwargs):
        self.calls.append({"actor_id": actor_id, "input": run_input, "kwargs": kwargs})
        return ActorRunResult(
            actor_id=actor_id,
            items=list(self.items),
            run_id="em-run",
            dataset_id="em-ds",
            status="SUCCEEDED",
            actual_cost=0.04,
            currency="USD",
        )


def test_selected_actor_is_configurable_not_hardcoded():
    cfg = load_data_sources_config()
    assert cfg["apify"]["actors"]["tiktok_emerging"] == "coregent/tiktok-keyword-search-scraper"
    tiktok_py = Path(__file__).resolve().parents[1] / "src/trendforge/acquisition/tiktok.py"
    assert "coregent" not in tiktok_py.read_text(encoding="utf-8")
    actor_input = cfg["experiments"]["tiktok_emerging_breakout"]["actor_input"]
    assert "includeCaption" not in actor_input
    assert actor_input["sort"] == "latest"
    assert actor_input["datePosted"] == "last24Hours"
    assert actor_input["region"] == "US"


def test_normalize_keyword_search_fields():
    item = normalize_tiktok_item(_items()[0], max_short_seconds=60)
    assert item is not None
    assert item.external_id == "em1"
    assert item.url.endswith("/video/em1")
    assert item.views == 840
    assert item.likes == 90
    assert item.shares == 2
    assert item.saves == 7
    assert item.creator == "smallacct"
    assert item.creator_followers == 1200
    assert item.audio == "original sound"
    assert item.language_signal == "en"
    assert item.region_signal == "US"
    assert item.audience_relevance == "region_sampled"
    assert item.source_query == "POV"
    assert item.search_rank == 1
    assert item.is_short is True
    hours = age_hours(item.published_at, now=NOW)
    assert hours is not None and hours < 24


def test_old_timestamp_and_long_and_language():
    items = _items()
    old = normalize_tiktok_item(items[1])
    long = normalize_tiktok_item(items[2])
    es = normalize_tiktok_item(items[3])
    unknown = normalize_tiktok_item(items[4])
    assert old is not None and age_hours(old.published_at, now=NOW) > 24
    assert long is not None and long.is_short is False
    assert es is not None and es.language_signal == "non_en"
    assert unknown is not None and unknown.language_signal == "unknown"
    assert unknown.shares is None


def test_sampling_quality_rates():
    items = [normalize_tiktok_item(x) for x in _items()]
    items = [i for i in items if i is not None]
    summary = summarize_population(
        items, now=NOW, new_count=2, duplicate_count=1, rejected_count=2, raw_count=5
    )
    quality = summary["sampling_quality"]
    assert summary["english"] == 2
    assert summary["unknown_language"] == 2
    assert summary["non_english"] == 1
    assert quality["short_rate"] == 0.8
    assert quality["recent_rate"] == 0.8
    assert quality["unknown_language_rate"] == 0.4
    assert quality["north_america_signal_rate"] == 1.0
    assert quality["new_candidate_rate"] == 0.4


def test_emerging_mode_uses_search_actor_and_preserves_raw(db: Session):
    client = FakeApify(_items())
    run = run_acquisition(
        db, "tiktok", mode="emerging", limit=40, client=client, cfg=CFG
    )
    assert run.status == "succeeded"
    assert run.actor_id == "coregent/tiktok-keyword-search-scraper"
    assert client.calls[0]["input"]["sort"] == "latest"
    assert client.calls[0]["input"]["datePosted"] == "last24Hours"
    assert client.calls[0]["input"]["region"] == "US"
    assert client.calls[0]["input"]["keywords"] == ["POV", "skit", "comedy", "storytime", "plot twist"]
    assert client.calls[0]["input"]["maxTotalResults"] == 40
    assert run.items_new == 3
    assert run.items_rejected == 2
    ids = {c.external_id for c in db.query(ContentCandidate).all()}
    assert ids == {"em1", "em-old", "em-unknown-lang"}
    row = db.query(ContentCandidate).filter_by(external_id="em1").one()
    assert row.analysis_status == AnalysisStatus.SKIPPED
    assert (row.raw_metadata or {}).get("source_query") == "POV"
    assert (row.raw_metadata or {}).get("region_signal") == "US"
    assert db.query(CandidateObservation).count() == 3
    raw_path = Path((run.run_metadata_json or {})["raw_dataset_path"])
    assert raw_path.exists()
    quality = (run.run_metadata_json or {})["sampling_quality"]
    assert quality["recent_rate"] is not None
    assert run.actual_cost == 0.04
    reasons = (run.run_metadata_json or {})["reject_reasons"]
    assert reasons.get("rejected_not_short") == 1
    assert reasons.get("rejected_language") == 1
    assert (run.run_metadata_json or {}).get("rejected_sample")


def test_emerging_dedupe_same_video_two_queries(db: Session):
    first = _items()[0]
    second = dict(first)
    second["keyword"] = "comedy"
    second["searchRank"] = 9
    run = run_acquisition(
        db, "tiktok", mode="emerging", client=FakeApify([first, second]), cfg=CFG
    )
    assert db.query(ContentCandidate).count() == 1
    assert run.items_new == 1
    assert run.items_duplicate == 1
    row = db.query(ContentCandidate).one()
    assert "POV" in (row.source_queries or [])
    assert "comedy" in (row.source_queries or [])


def test_instagram_emerging_rejected():
    with pytest.raises(AcquisitionError, match="TikTok-only"):
        apply_acquisition_mode(CFG, "instagram", "emerging")


def test_build_input_sets_per_keyword_and_total():
    cfg = apply_acquisition_mode(CFG, "tiktok", "emerging")
    source = cfg["sources"]["tiktok"]
    payload = build_actor_input(source, 40)
    assert payload["maxItemsPerKeyword"] == 8
    assert payload["maxTotalResults"] == 40
    assert payload["sort"] == "latest"
