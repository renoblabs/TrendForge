from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.db import get_session_factory, init_db
from trendforge.discovery.gather import run_gathering_jobs
from trendforge.discovery.tiktok import item_to_video, run_tiktok_discovery, run_tiktok_observation
from trendforge.models import ContentCandidate, DiscoveryRun


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "tiktok.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


NESTED = {
    "id": "7543693751290481942",
    "text": "ootd #foruyou",
    "textLanguage": "en",
    "createTimeISO": "2025-08-28T17:44:35.000Z",
    "webVideoUrl": "https://www.tiktok.com/@gretalynnhihi/video/7543693751290481942",
    "playCount": 145900,
    "diggCount": 23400,
    "commentCount": 46,
    "shareCount": 145,
    "authorMeta": {"id": "6733984297591636998", "name": "gretalynnhihi", "fans": 51200},
    "videoMeta": {"duration": 15, "coverUrl": "https://example.com/cover.jpg"},
    "hashtags": [{"name": "foruyou"}],
    "searchQuery": "ootd",
}


class FakeApify:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def run_actor(self, actor_id, run_input, **kwargs):
        self.calls.append({"actor_id": actor_id, "input": run_input, "kwargs": kwargs})
        return list(self.items)


def test_item_to_video_nested():
    video = item_to_video(NESTED, source_query="ootd")
    assert video is not None
    assert video.platform == "tiktok"
    assert video.external_id == "7543693751290481942"
    assert video.views == 145900
    assert video.duration == 15
    assert video.is_short is True
    assert video.channel_name == "gretalynnhihi"


def test_item_to_video_flat_keys():
    video = item_to_video(
        {
            "id": "1",
            "webVideoUrl": "https://www.tiktok.com/@x/video/1",
            "playCount": 10,
            "diggCount": 2,
            "authorMeta.name": "x",
            "videoMeta.duration": 9,
            "createTimeISO": "2026-08-16T12:00:00.000Z",
            "text": "hi",
        }
    )
    assert video is not None
    assert video.channel_name == "x"
    assert video.duration == 9


def test_discover_upserts_tiktok(db: Session):
    client = FakeApify([NESTED])
    run = run_tiktok_discovery(db, client=client, cfg={"apify": {"enabled": True, "tiktok": {"hashtags": ["pov"]}}})
    assert run.kind == "discover_tiktok"
    assert run.new_candidates == 1
    row = db.query(ContentCandidate).one()
    assert row.platform == "tiktok"
    assert row.views == 145900
    assert row.discovery_score is not None
    assert client.calls[0]["kwargs"].get("max_total_charge_usd") == 0.5 or "maxTotalChargeUsd" in str(client.calls[0]["kwargs"])


def test_observe_refreshes_by_url(db: Session):
    run_tiktok_discovery(db, client=FakeApify([NESTED]), cfg={"apify": {"enabled": True}})
    later = dict(NESTED)
    later["playCount"] = 200000
    client = FakeApify([later])
    run = run_tiktok_observation(db, client=client, cfg={"apify": {"enabled": True, "tiktok": {"observe_top_n": 5}}})
    assert run.observations_written == 1
    row = db.query(ContentCandidate).one()
    assert row.views == 200000
    assert "postURLs" in client.calls[0]["input"]


def test_gather_tiktok_without_youtube(db: Session, monkeypatch):
    monkeypatch.setattr(
        "trendforge.discovery.gather.get_settings",
        lambda: type("S", (), {"has_youtube": False, "has_apify": True})(),
    )
    ran = run_gathering_jobs(
        db,
        jobs=["discover_tiktok"],
        force=True,
        apify_client=FakeApify([NESTED]),
        cfg={"apify": {"enabled": True}, "schedule": {"enabled": True}},
    )
    assert [name for name, _ in ran] == ["discover_tiktok"]
    assert db.query(DiscoveryRun).filter_by(kind="discover_tiktok").count() == 1
