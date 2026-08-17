from __future__ import annotations

import json
from pathlib import Path

from trendforge.acquisition.normalize import normalize_instagram_item, normalize_tiktok_item

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_tiktok_normalization_from_fixture():
    item = normalize_tiktok_item(_load("tiktok_apify.json")[0], source_query="ootd")
    assert item is not None
    assert item.platform == "tiktok"
    assert item.external_id == "7543693751290481942"
    assert item.views == 145900
    assert item.likes == 23400
    assert item.comments == 46
    assert item.shares == 145
    assert item.saves == 800
    assert item.duration == 15
    assert item.creator == "gretalynnhihi"
    assert item.creator_id == "6733984297591636998"
    assert item.creator_followers == 51200
    assert item.profile_url.endswith("gretalynnhihi")
    assert item.audio == "original sound"
    assert item.published_at is not None
    assert item.language_signal == "en"
    assert "foruyou" in item.hashtags


def test_tiktok_missing_metrics_stay_none():
    item = normalize_tiktok_item(_load("tiktok_apify.json")[1])
    assert item is not None
    assert item.views is None
    assert item.likes is None
    assert item.comments is None
    assert item.shares is None
    assert item.published_at is not None


def test_tiktok_rejects_missing_id_and_url():
    assert normalize_tiktok_item({"text": "no identity"}) is None


def test_tiktok_unix_and_iso_timestamps():
    iso = normalize_tiktok_item(
        {
            "id": "1",
            "webVideoUrl": "https://www.tiktok.com/@x/video/1",
            "createTimeISO": "2026-08-16T12:00:00.000Z",
        }
    )
    unix = normalize_tiktok_item(
        {
            "id": "2",
            "webVideoUrl": "https://www.tiktok.com/@x/video/2",
            "createTime": 1750000000,
        }
    )
    assert iso is not None and iso.published_at is not None
    assert unix is not None and unix.published_at is not None
    assert iso.published_at.year == 2026


def test_tiktok_zero_is_not_unknown():
    item = normalize_tiktok_item(
        {
            "id": "3",
            "webVideoUrl": "https://www.tiktok.com/@x/video/3",
            "playCount": 0,
            "shareCount": 0,
        }
    )
    assert item is not None
    assert item.views == 0
    assert item.shares == 0


def test_instagram_normalization_from_fixture():
    item = normalize_instagram_item(_load("instagram_apify.json")[0], source_query="pov")
    assert item is not None
    assert item.platform == "instagram"
    assert item.external_id == "DQv6GNRCPMj"
    assert item.views == 2419
    assert item.likes is None
    assert item.comments == 23
    assert item.shares == 4
    assert item.creator == "theresenybu"
    assert item.creator_id == "241146166"
    assert item.duration == 30
    assert item.language_signal == "unknown"
    assert item.source_query == "pov"


def test_instagram_missing_metrics_stay_none():
    item = normalize_instagram_item(_load("instagram_apify.json")[1])
    assert item is not None
    assert item.views is None
    assert item.likes is None
    assert item.comments is None
    assert item.shares is None
    assert item.saves is None


def test_instagram_skips_image_posts():
    assert normalize_instagram_item(_load("instagram_apify.json")[2]) is None
