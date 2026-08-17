from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol


@dataclass
class NormalizedItem:
    platform: str
    external_id: str
    url: str
    creator: Optional[str] = None
    creator_id: Optional[str] = None
    creator_followers: Optional[int] = None
    profile_url: Optional[str] = None
    published_at: Optional[datetime] = None
    title_or_caption: Optional[str] = None
    hashtags: list[str] = field(default_factory=list)
    audio: Optional[str] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    saves: Optional[int] = None
    duration: Optional[float] = None
    thumbnail_url: Optional[str] = None
    language_signal: str = "unknown"
    language_evidence: Optional[dict[str, Any]] = None
    region_signal: Optional[str] = None
    audience_relevance: Optional[str] = None
    source_query: Optional[str] = None
    search_rank: Optional[int] = None
    is_short: bool = True


@dataclass
class ActorRunResult:
    actor_id: str
    items: list[dict[str, Any]]
    run_id: Optional[str] = None
    dataset_id: Optional[str] = None
    status: str = "SUCCEEDED"
    actual_cost: Optional[float] = None
    estimated_cost: Optional[float] = None
    currency: Optional[str] = None
    raw_run: dict[str, Any] = field(default_factory=dict)


class AcquisitionProvider(Protocol):
    source: str
    provider_name: str

    def discover(self, config: dict[str, Any]) -> ActorRunResult: ...

    def normalize(self, raw_item: dict[str, Any], **kwargs: Any) -> NormalizedItem | None: ...
