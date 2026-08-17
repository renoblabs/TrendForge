from __future__ import annotations

import httpx
import pytest

from trendforge.acquisition.apify import ApifyClient, extract_cost
from trendforge.acquisition.errors import AcquisitionError, MissingTokenError


def _client(handler) -> ApifyClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.apify.com/v2")
    return ApifyClient(token="test-token", base_url="https://api.apify.com/v2", client=http)


def test_extract_cost_only_when_numeric():
    assert extract_cost({"usageTotalUsd": 0.12}) == (0.12, "USD")
    assert extract_cost({"status": "SUCCEEDED"}) == (None, None)
    assert extract_cost(None) == (None, None)


def test_successful_actor_run_and_dataset():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/runs"):
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": "run-1",
                        "status": "SUCCEEDED",
                        "defaultDatasetId": "ds-1",
                        "usageTotalUsd": 0.07,
                    }
                },
            )
        if request.url.path.endswith("/datasets/ds-1/items"):
            return httpx.Response(200, json=[{"id": "a"}, {"id": "b"}])
        return httpx.Response(404, json={"error": request.url.path})

    result = _client(handler).run_actor_result("clockworks/tiktok-scraper", {"hashtags": ["pov"]}, max_items=10)
    assert result.status == "SUCCEEDED"
    assert result.run_id == "run-1"
    assert result.dataset_id == "ds-1"
    assert result.actual_cost == 0.07
    assert result.currency == "USD"
    assert len(result.items) == 2


def test_dataset_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                201,
                json={"data": {"id": "r", "status": "SUCCEEDED", "defaultDatasetId": "ds"}},
            )
        offset = int(request.url.params.get("offset") or 0)
        if offset == 0:
            return httpx.Response(200, json=[{"id": "1"}, {"id": "2"}])
        if offset == 2:
            return httpx.Response(200, json=[{"id": "3"}])
        return httpx.Response(200, json=[])

    client = _client(handler)
    items = client.fetch_dataset_items("ds", max_items=5, page_size=2)
    assert [i["id"] for i in items] == ["1", "2", "3"]


def test_empty_dataset():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                201,
                json={"data": {"id": "r", "status": "SUCCEEDED", "defaultDatasetId": "ds"}},
            )
        return httpx.Response(200, json=[])

    result = _client(handler).run_actor_result("x", {}, max_items=10)
    assert result.items == []
    assert result.status == "SUCCEEDED"


def test_actor_failure_does_not_raise_on_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"data": {"id": "r", "status": "FAILED", "defaultDatasetId": None}},
        )

    result = _client(handler).run_actor_result("x", {})
    assert result.status == "FAILED"
    assert result.items == []
    with pytest.raises(AcquisitionError, match="FAILED"):
        _client(handler).run_actor("x", {})


def test_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(AcquisitionError, match="500"):
        _client(handler).run_actor_result("x", {})


def test_missing_token():
    client = ApifyClient(token="", client=httpx.Client())
    with pytest.raises(MissingTokenError, match="APIFY_API_TOKEN"):
        client.run_actor_result("x", {})
