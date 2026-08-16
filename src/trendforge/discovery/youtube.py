from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from trendforge.config import get_settings
from trendforge.discovery.provider import (
    DiscoveryError,
    QuotaExhaustedError,
    SearchPage,
)


class YouTubeDiscoveryProvider:
    name = "youtube"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ):
        settings = get_settings()
        self.api_key = (api_key if api_key is not None else settings.youtube_api_key).strip()
        self.base_url = (base_url or settings.youtube_api_base_url).rstrip("/")
        self._client = client

    def _client_ctx(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=30.0)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise DiscoveryError("YOUTUBE_API_KEY is not set.")
        query = {**params, "key": self.api_key}
        url = f"{self.base_url}/{path.lstrip('/')}"
        owns_client = self._client is None
        client = self._client_ctx()
        try:
            resp = client.get(url, params=query)
        finally:
            if owns_client:
                client.close()

        if resp.status_code in {403, 429}:
            body = _safe_json(resp)
            reason = _error_reason(body)
            if reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"} or resp.status_code == 429:
                raise QuotaExhaustedError(
                    f"YouTube API quota/rate limit ({reason or resp.status_code}): {resp.text[:500]}"
                )
            raise DiscoveryError(f"YouTube API {resp.status_code}: {resp.text[:500]}")
        if resp.status_code >= 400:
            raise DiscoveryError(f"YouTube API {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    def search(
        self,
        query: str,
        *,
        published_after: datetime,
        page_token: str | None = None,
        max_results: int = 25,
        region_code: str | None = None,
        relevance_language: str | None = None,
        order: str = "date",
    ) -> SearchPage:
        if published_after.tzinfo is None:
            published_after = published_after.replace(tzinfo=timezone.utc)
        params: dict[str, Any] = {
            "part": "snippet",
            "type": "video",
            "videoDuration": "short",
            "order": order or "date",
            "maxResults": min(50, max(1, max_results)),
            "publishedAfter": published_after.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        if region_code:
            params["regionCode"] = region_code
        if relevance_language:
            params["relevanceLanguage"] = relevance_language
        data = self._get("search", params)
        ids = [
            item["id"]["videoId"]
            for item in data.get("items") or []
            if item.get("id", {}).get("videoId")
        ]
        return SearchPage(
            video_ids=ids,
            next_page_token=data.get("nextPageToken"),
            raw=data,
        )

    def get_videos(self, video_ids: list[str]) -> list[dict[str, Any]]:
        if not video_ids:
            return []
        items: list[dict[str, Any]] = []
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i : i + 50]
            data = self._get(
                "videos",
                {
                    "part": "snippet,contentDetails,statistics",
                    "id": ",".join(chunk),
                    "maxResults": 50,
                },
            )
            items.extend(data.get("items") or [])
        return items

    def get_channels(self, channel_ids: list[str]) -> dict[str, dict[str, Any]]:
        unique = [cid for cid in dict.fromkeys(channel_ids) if cid]
        out: dict[str, dict[str, Any]] = {}
        if not unique:
            return out
        for i in range(0, len(unique), 50):
            chunk = unique[i : i + 50]
            data = self._get(
                "channels",
                {
                    "part": "snippet,statistics",
                    "id": ",".join(chunk),
                    "maxResults": 50,
                },
            )
            for item in data.get("items") or []:
                cid = item.get("id")
                if cid:
                    out[cid] = item
        return out


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _error_reason(body: dict[str, Any]) -> str | None:
    errors = (body.get("error") or {}).get("errors") or []
    if errors and isinstance(errors[0], dict):
        return errors[0].get("reason")
    return (body.get("error") or {}).get("status")
