from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from urllib.parse import urlparse


@dataclass
class RawCandidate:
    url: str
    platform: str = "unknown"
    creator: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    duration: Optional[float] = None
    thumbnail_url: Optional[str] = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class TrendSource(Protocol):
    name: str

    def fetch_candidates(self, query: str | None = None, limit: int = 20) -> list[RawCandidate]:
        ...


def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "tiktok.com" in host:
        return "tiktok"
    if "instagram.com" in host:
        return "instagram"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "capcut.com" in host:
        return "capcut"
    return "unknown"


class ManualSource:
    name = "manual"

    def fetch_candidates(self, query: str | None = None, limit: int = 20) -> list[RawCandidate]:
        """Parse pasted URLs (one per line) or a simple JSON list of objects/urls."""
        if not query or not query.strip():
            return []

        text = query.strip()
        candidates: list[RawCandidate] = []

        if text.startswith("["):
            import json

            payload = json.loads(text)
            for item in payload[:limit]:
                if isinstance(item, str):
                    candidates.append(
                        RawCandidate(url=item.strip(), platform=detect_platform(item.strip()))
                    )
                elif isinstance(item, dict) and item.get("url"):
                    url = str(item["url"]).strip()
                    candidates.append(
                        RawCandidate(
                            url=url,
                            platform=str(item.get("platform") or detect_platform(url)),
                            creator=item.get("creator"),
                            title=item.get("title"),
                            description=item.get("description"),
                            views=item.get("views"),
                            likes=item.get("likes"),
                            comments=item.get("comments"),
                            shares=item.get("shares"),
                            duration=item.get("duration"),
                            thumbnail_url=item.get("thumbnail_url"),
                            raw_metadata=item.get("raw_metadata") or item,
                        )
                    )
            return candidates

        for line in text.splitlines():
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            candidates.append(RawCandidate(url=url, platform=detect_platform(url)))
            if len(candidates) >= limit:
                break
        return candidates


class _UnimplementedSource:
    name = "unimplemented"
    reason = "Not implemented in Phase 1"

    def fetch_candidates(self, query: str | None = None, limit: int = 20) -> list[RawCandidate]:
        raise NotImplementedError(self.reason)


class TikTokSource(_UnimplementedSource):
    name = "tiktok"
    reason = (
        "TikTok Research API is academic-only; commercial Display/Content APIs do not "
        "provide broad trending discovery suitable for this MVP. Use ManualSource."
    )


class YouTubeSource(_UnimplementedSource):
    name = "youtube"
    reason = (
        "YouTube Data API v3 can search Shorts but quota is expensive (search=100 units). "
        "Documented for Phase 2; use ManualSource for now."
    )


class InstagramSource(_UnimplementedSource):
    name = "instagram"
    reason = (
        "Instagram Graph API is primarily for owned Business accounts, not public trend "
        "discovery. Use ManualSource."
    )


class CapCutSource(_UnimplementedSource):
    name = "capcut"
    reason = "CapCut has no public documented API for trending templates. Use ManualSource."
