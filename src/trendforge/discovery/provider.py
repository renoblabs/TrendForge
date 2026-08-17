from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol


class DiscoveryError(RuntimeError):
    pass


class QuotaExhaustedError(DiscoveryError):
    pass


@dataclass
class DiscoveredVideo:
    external_id: str
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    published_at: Optional[datetime] = None
    duration: Optional[float] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    favorite_count: Optional[int] = None
    thumbnail_url: Optional[str] = None
    hashtags: list[str] = field(default_factory=list)
    category: Optional[str] = None
    channel_subscriber_count: Optional[int] = None
    is_short: bool = False
    source_query: Optional[str] = None
    search_rank: Optional[int] = None
    platform: str = "youtube"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchPage:
    video_ids: list[str]
    next_page_token: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


class DiscoveryProvider(Protocol):
    name: str

    def search(
        self,
        query: str,
        *,
        published_after: datetime,
        page_token: str | None = None,
        max_results: int = 25,
    ) -> SearchPage: ...

    def get_videos(self, video_ids: list[str]) -> list[dict[str, Any]]: ...

    def get_channels(self, channel_ids: list[str]) -> dict[str, dict[str, Any]]: ...
