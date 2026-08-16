from __future__ import annotations

import httpx
import pytest

from trendforge.discovery.provider import DiscoveryError, QuotaExhaustedError
from trendforge.discovery.youtube import YouTubeDiscoveryProvider


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "quota" in url or request.url.params.get("q") == "quota":
        return httpx.Response(
            403,
            json={"error": {"errors": [{"reason": "quotaExceeded"}], "code": 403}},
        )
    if "/search" in url:
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": {"videoId": "vid1"}},
                    {"id": {"videoId": "vid2"}},
                ],
                "nextPageToken": "abc",
            },
        )
    if "/videos" in url:
        return httpx.Response(
            200,
            json={"items": [{"id": "vid1", "statistics": {"viewCount": "10"}}]},
        )
    if "/channels" in url:
        return httpx.Response(
            200,
            json={"items": [{"id": "UC1", "statistics": {"subscriberCount": "9"}}]},
        )
    return httpx.Response(404, json={"error": {"message": "not found"}})


def test_search_videos_channels_and_quota():
    transport = httpx.MockTransport(_handler)
    client = httpx.Client(transport=transport)
    provider = YouTubeDiscoveryProvider(
        api_key="test-key",
        base_url="https://www.googleapis.com/youtube/v3",
        client=client,
    )
    from datetime import datetime, timezone

    page = provider.search(
        "funny",
        published_after=datetime(2026, 8, 12, tzinfo=timezone.utc),
        max_results=10,
    )
    assert page.video_ids == ["vid1", "vid2"]
    assert page.next_page_token == "abc"
    videos = provider.get_videos(["vid1"])
    assert videos[0]["id"] == "vid1"
    channels = provider.get_channels(["UC1"])
    assert "UC1" in channels

    with pytest.raises(QuotaExhaustedError):
        provider.search(
            "quota",
            published_after=datetime(2026, 8, 12, tzinfo=timezone.utc),
        )


def test_missing_key():
    provider = YouTubeDiscoveryProvider(api_key="")
    from datetime import datetime, timezone

    with pytest.raises(DiscoveryError):
        provider.search("x", published_after=datetime.now(timezone.utc))


def test_broad_search_omits_q_and_orders_by_viewcount():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["q"] = request.url.params.get("q", "")
        captured["order"] = request.url.params.get("order", "")
        captured["has_q"] = "q" in request.url.params
        return httpx.Response(
            200,
            json={"items": [{"id": {"videoId": "vid1"}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = YouTubeDiscoveryProvider(
        api_key="test-key",
        base_url="https://www.googleapis.com/youtube/v3",
        client=client,
    )
    from datetime import datetime, timezone

    page = provider.search(
        "",
        published_after=datetime(2026, 8, 12, tzinfo=timezone.utc),
        order="viewCount",
    )
    assert page.video_ids == ["vid1"]
    assert captured["has_q"] is False
    assert captured["order"] == "viewCount"


def test_search_passes_region_and_relevance_language():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["regionCode"] = request.url.params.get("regionCode", "")
        captured["relevanceLanguage"] = request.url.params.get("relevanceLanguage", "")
        captured["has_q"] = "q" in request.url.params
        return httpx.Response(200, json={"items": [{"id": {"videoId": "vid1"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = YouTubeDiscoveryProvider(
        api_key="test-key",
        base_url="https://www.googleapis.com/youtube/v3",
        client=client,
    )
    from datetime import datetime, timezone

    page = provider.search(
        "",
        published_after=datetime(2026, 8, 12, tzinfo=timezone.utc),
        order="viewCount",
        region_code="US",
        relevance_language="en",
    )
    assert page.video_ids == ["vid1"]
    assert captured["regionCode"] == "US"
    assert captured["relevanceLanguage"] == "en"
    assert captured["has_q"] is False


def test_generic_api_error():
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    provider = YouTubeDiscoveryProvider(api_key="k", client=client)
    from datetime import datetime, timezone

    with pytest.raises(DiscoveryError):
        provider.search("x", published_after=datetime.now(timezone.utc))
