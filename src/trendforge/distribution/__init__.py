from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol


@dataclass
class ChannelInfo:
    id: str
    platform: str
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PostRequest:
    content: str
    platform: str
    channel_id: str
    media_urls: list[str] = field(default_factory=list)
    scheduled_at: Optional[datetime] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PostResult:
    provider_post_id: str
    status: str
    published_url: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


class DistributionProvider(Protocol):
    name: str

    def list_channels(self) -> list[ChannelInfo]: ...

    def create_post(self, request: PostRequest) -> PostResult: ...

    def schedule_post(self, request: PostRequest) -> PostResult: ...

    def publish_post(self, request: PostRequest) -> PostResult: ...

    def get_status(self, provider_post_id: str) -> PostResult: ...

    def get_published_url(self, provider_post_id: str) -> Optional[str]: ...

    def cancel(self, provider_post_id: str) -> PostResult: ...


class PostizProvider:
    """Maps to Postiz public API. Phase 1: interface only — no live calls."""

    name = "postiz"

    def __init__(self, api_key: str = "", base_url: str = "https://api.postiz.com/public/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _require_live(self) -> None:
        raise NotImplementedError(
            "PostizProvider is stubbed in Phase 1. "
            "Wire OPENROUTER/POSTIZ keys and implement HTTP calls in Phase 2. "
            f"Would call {self.base_url}/posts"
        )

    def list_channels(self) -> list[ChannelInfo]:
        self._require_live()
        return []

    def create_post(self, request: PostRequest) -> PostResult:
        self._require_live()
        return PostResult(provider_post_id="", status="draft")

    def schedule_post(self, request: PostRequest) -> PostResult:
        self._require_live()
        return PostResult(provider_post_id="", status="scheduled")

    def publish_post(self, request: PostRequest) -> PostResult:
        self._require_live()
        return PostResult(provider_post_id="", status="published")

    def get_status(self, provider_post_id: str) -> PostResult:
        self._require_live()
        return PostResult(provider_post_id=provider_post_id, status="unknown")

    def get_published_url(self, provider_post_id: str) -> Optional[str]:
        self._require_live()
        return None

    def cancel(self, provider_post_id: str) -> PostResult:
        self._require_live()
        return PostResult(provider_post_id=provider_post_id, status="cancelled")
