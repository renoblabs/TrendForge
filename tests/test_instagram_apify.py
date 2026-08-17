from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from trendforge.db import get_session_factory, init_db
from trendforge.discovery.gather import run_gathering_jobs
from trendforge.discovery.instagram import (
    item_to_video,
    run_instagram_discovery,
    run_instagram_observation,
)
from trendforge.models import ContentCandidate, DiscoveryRun


@pytest.fixture()
def db(tmp_path: Path) -> Session:
    path = tmp_path / "ig.db"
    init_db(path)
    session = get_session_factory(path)()
    yield session
    session.close()


REEL = {
    "id": "3760479727136600867",
    "type": "Video",
    "shortCode": "DQv6GNRCPMj",
    "caption": "garden bird #birdsofinstagram",
    "hashtags": ["birdsofinstagram", "gardenbirds"],
    "url": "https://www.instagram.com/p/DQv6GNRCPMj/",
    "commentsCount": 23,
    "displayUrl": "https://example.com/cover.jpg",
    "videoPlayCount": 2419,
    "igPlayCount": 2419,
    "likesCount": -1,
    "timestamp": "2025-11-07T08:39:32.000Z",
    "ownerUsername": "theresenybu",
    "ownerId": "241146166",
    "productType": "clips",
    "videoDuration": 30,
    "inputUrl": "https://www.instagram.com/explore/tags/pov",
    "reshareCount": 4,
}


class FakeApify:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def run_actor(self, actor_id, run_input, **kwargs):
        self.calls.append({"actor_id": actor_id, "input": run_input, "kwargs": kwargs})
        return list(self.items)


def test_item_to_video_reel():
    video = item_to_video(REEL, source_query="pov")
    assert video is not None
    assert video.platform == "instagram"
    assert video.external_id == "DQv6GNRCPMj"
    assert video.views == 2419
    assert video.likes is None  # hidden likes are -1
    assert video.duration == 30
    assert video.is_short is True
    assert video.channel_name == "theresenybu"


def test_item_skips_image_posts():
    assert (
        item_to_video(
            {
                "shortCode": "abc",
                "url": "https://www.instagram.com/p/abc/",
                "productType": "feed",
                "type": "Image",
                "likesCount": 10,
            }
        )
        is None
    )


def test_discover_upserts_instagram(db: Session):
    client = FakeApify([REEL])
    run = run_instagram_discovery(
        db,
        client=client,
        cfg={"apify": {"enabled": True, "instagram": {"enabled": True, "hashtags": ["pov"]}}},
    )
    assert run.kind == "discover_instagram"
    assert run.new_candidates == 1
    row = db.query(ContentCandidate).one()
    assert row.platform == "instagram"
    assert row.views == 2419
    assert client.calls[0]["input"]["resultsType"] == "reels"


def test_observe_refreshes_by_url(db: Session):
    run_instagram_discovery(
        db,
        client=FakeApify([REEL]),
        cfg={"apify": {"enabled": True, "instagram": {"enabled": True}}},
    )
    later = dict(REEL)
    later["videoPlayCount"] = 5000
    later["igPlayCount"] = 5000
    client = FakeApify([later])
    run = run_instagram_observation(
        db,
        client=client,
        cfg={"apify": {"enabled": True, "instagram": {"enabled": True, "observe_top_n": 5}}},
    )
    assert run.observations_written == 1
    row = db.query(ContentCandidate).one()
    assert row.views == 5000
    assert "directUrls" in client.calls[0]["input"]


def test_gather_instagram_without_youtube(db: Session, monkeypatch):
    monkeypatch.setattr(
        "trendforge.discovery.gather.get_settings",
        lambda: type("S", (), {"has_youtube": False, "has_apify": True})(),
    )
    ran = run_gathering_jobs(
        db,
        jobs=["discover_instagram"],
        force=True,
        apify_client=FakeApify([REEL]),
        cfg={
            "apify": {"enabled": True, "instagram": {"enabled": True}},
            "schedule": {"enabled": True},
        },
    )
    assert [name for name, _ in ran] == ["discover_instagram"]
    assert db.query(DiscoveryRun).filter_by(kind="discover_instagram").count() == 1
