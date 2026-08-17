from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from trendforge.acquisition.provider import NormalizedItem
from trendforge.discovery.profiles import normalize_language_code
from trendforge.discovery.shorts import parse_iso8601_duration


def pick(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
        cur: Any = item
        ok = True
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def as_int(value: Any, *, allow_negative: bool = False) -> int | None:
    try:
        if value is None or value == "":
            return None
        n = int(value)
        if not allow_negative and n < 0:
            return None
        return n
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    return None


def language_signal_from_code(value: Any) -> str:
    code = normalize_language_code(value)
    if not code:
        return "unknown"
    if code == "en":
        return "en"
    return "non_en"


def _hashtags(raw: Any, caption: str | None = None) -> list[str]:
    tags: list[str] = []
    if isinstance(raw, list):
        for tag in raw:
            if isinstance(tag, dict) and tag.get("name"):
                tags.append(str(tag["name"]).lstrip("#").lower())
            elif isinstance(tag, str) and tag.strip():
                tags.append(tag.lstrip("#").lower())
    if not tags and caption:
        for token in caption.split():
            if token.startswith("#") and len(token) > 1:
                tags.append(token.lstrip("#").lower().rstrip(".,!?"))
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def tiktok_video_id(url: str | None, item: dict[str, Any] | None = None) -> str:
    if item:
        vid = pick(item, "videoId", "id", "videoMeta.id")
        if vid:
            return str(vid)
    if not url:
        return ""
    path = urlparse(url).path.rstrip("/")
    if "/video/" in path:
        return path.split("/video/")[-1].split("?")[0]
    return path.rsplit("/", 1)[-1]


def instagram_shortcode(url: str | None, item: dict[str, Any] | None = None) -> str:
    if item:
        code = pick(item, "shortCode", "shortcode", "code")
        if code:
            return str(code)
        vid = pick(item, "id")
        if vid:
            return str(vid)
    if not url:
        return ""
    path = urlparse(url).path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    for i, part in enumerate(parts):
        if part in {"reel", "p", "tv", "reels"} and i + 1 < len(parts):
            return parts[i + 1]
    return parts[-1] if parts else ""


def _instagram_url(item: dict[str, Any], shortcode: str) -> str:
    url = str(pick(item, "url", "inputUrl") or "")
    if "/reel/" in url or "/p/" in url or "/tv/" in url:
        return url.split("?")[0]
    product = str(pick(item, "productType") or "").lower()
    kind = "reel" if product in {"clips", "reel", "reels"} else "p"
    return f"https://www.instagram.com/{kind}/{shortcode}/"


def _region_signal(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text or text in {"NONE", "NULL"}:
        return None
    return text[:8]


def _audience_relevance(region: str | None) -> str:
    if region in {"US", "CA"}:
        return "region_sampled"
    return "unknown"


def _tiktok_source_query(item: dict[str, Any], fallback: str | None = None) -> str | None:
    query = pick(item, "keyword", "searchQuery", "normalizedKeyword")
    if query:
        return str(query)
    tags = item.get("hashtags")
    if isinstance(tags, list) and tags:
        first = tags[0]
        if isinstance(first, dict) and first.get("name"):
            return str(first["name"])
        if isinstance(first, str):
            return first
    return fallback


def _instagram_source_query(item: dict[str, Any], fallback: str | None = None) -> str | None:
    input_url = str(pick(item, "inputUrl") or "")
    if "/tags/" in input_url:
        return input_url.rstrip("/").split("/tags/")[-1].split("/")[0].lstrip("#")
    if "/explore/tags/" in input_url:
        return input_url.rstrip("/").split("/")[-1].lstrip("#")
    tags = item.get("hashtags")
    if isinstance(tags, list) and tags:
        first = tags[0]
        if isinstance(first, str):
            return first.lstrip("#")
    return fallback


def normalize_tiktok_item(
    item: dict[str, Any],
    *,
    source_query: str | None = None,
    search_rank: int | None = None,
    max_short_seconds: float = 60,
) -> NormalizedItem | None:
    url = str(pick(item, "webVideoUrl", "videoUrl", "shareUrl", "url") or "")
    vid = tiktok_video_id(url, item)
    if not vid or not url:
        return None
    duration = as_float(pick(item, "videoMeta.duration", "duration"))
    if duration is None:
        iso = pick(item, "videoMeta.durationISO")
        duration = parse_iso8601_duration(str(iso)) if iso else None
    text = str(pick(item, "text", "desc", "caption") or "") or None
    author = (
        str(
            pick(
                item,
                "authorMeta.name",
                "authorUniqueId",
                "authorMeta.nickName",
                "authorNickname",
                "author",
            )
            or ""
        )
        or None
    )
    author_id = str(pick(item, "authorMeta.id", "authorId") or "") or None
    profile = str(pick(item, "authorMeta.profileUrl", "authorUrl") or "") or None
    if not profile and author:
        profile = f"https://www.tiktok.com/@{author.lstrip('@')}"
    audio = (
        str(
            pick(item, "musicMeta.musicName", "musicMeta.songName", "musicName", "musicTitle")
            or ""
        )
        or None
    )
    lang_raw = pick(item, "textLanguage", "language", "captionLanguage")
    lang = language_signal_from_code(lang_raw)
    published_at = parse_timestamp(pick(item, "createTimeISO", "createTime", "publishedAt"))
    region = _region_signal(pick(item, "region", "location", "region_signal"))
    rank = search_rank if search_rank is not None else as_int(pick(item, "searchRank"))
    return NormalizedItem(
        platform="tiktok",
        external_id=vid,
        url=url.split("?")[0],
        creator=author,
        creator_id=author_id,
        creator_followers=as_int(
            pick(item, "authorMeta.fans", "authorMeta.followers", "authorFollowerCount")
        ),
        profile_url=profile,
        published_at=published_at,
        title_or_caption=text,
        hashtags=_hashtags(pick(item, "hashtags"), text),
        audio=audio,
        views=as_int(pick(item, "playCount", "play_count", "views")),
        likes=as_int(pick(item, "diggCount", "likeCount", "likes")),
        comments=as_int(pick(item, "commentCount", "comments")),
        shares=as_int(pick(item, "shareCount", "shares")),
        saves=as_int(pick(item, "collectCount", "saves")),
        duration=duration,
        thumbnail_url=str(pick(item, "videoMeta.coverUrl", "coverUrl") or "") or None,
        language_signal=lang,
        language_evidence={"textLanguage": lang_raw} if lang_raw not in (None, "") else None,
        region_signal=region,
        audience_relevance=_audience_relevance(region),
        source_query=source_query or _tiktok_source_query(item),
        search_rank=rank,
        is_short=duration is None or duration <= max_short_seconds,
    )


def normalize_tiktok_fresh_item(
    item: dict[str, Any],
    *,
    source_query: str | None = None,
    search_rank: int | None = None,
    max_short_seconds: float = 60,
) -> NormalizedItem | None:
    """clockworks search (LATEST + PAST_24_HOURS). Same video schema as the hashtag Actor."""
    if item.get("errorCode") or item.get("_warning"):
        return None
    normalized = normalize_tiktok_item(
        item,
        source_query=source_query or pick(item, "searchQuery", "input"),
        search_rank=search_rank,
        max_short_seconds=max_short_seconds,
    )
    return normalized


def normalize_tiktok_trending_item(
    item: dict[str, Any],
    *,
    source_query: str | None = None,
    search_rank: int | None = None,
    max_short_seconds: float = 60,
) -> NormalizedItem | None:
    """xtracto Explore-ranked videos. Nested TikTok API objects, not hashtag rows."""
    if item.get("errorCode") or item.get("_warning") or item.get("_warningDetail"):
        return None
    vid = str(pick(item, "id", "videoId") or "")
    author = str(pick(item, "author.uniqueId", "author.nickname") or "") or None
    url = str(pick(item, "videoUrl", "shareUrl", "url") or "")
    if not vid:
        return None
    if not url:
        if not author:
            return None
        url = f"https://www.tiktok.com/@{author.lstrip('@')}/video/{vid}"
    duration = as_float(pick(item, "video.duration", "duration"))
    text = str(pick(item, "desc", "text", "caption") or "") or None
    author_id = str(pick(item, "author.id") or "") or None
    profile = f"https://www.tiktok.com/@{author.lstrip('@')}" if author else None
    audio = str(pick(item, "music.title", "music.authorName") or "") or None
    lang_raw = pick(item, "textLanguage", "language")
    country = pick(item, "_input.country_code", "region")
    region = _region_signal(country)
    challenges = pick(item, "challenges") or []
    rank = search_rank if search_rank is not None else as_int(pick(item, "_rank"))
    return NormalizedItem(
        platform="tiktok",
        external_id=vid,
        url=url.split("?")[0],
        creator=author,
        creator_id=author_id,
        creator_followers=as_int(
            pick(
                item,
                "authorStats.followerCount",
                "author.followerCount",
                "author.fans",
            )
        ),
        profile_url=profile,
        published_at=parse_timestamp(pick(item, "createTime", "createTimeISO")),
        title_or_caption=text,
        hashtags=_hashtags(challenges, text),
        audio=audio,
        views=as_int(pick(item, "stats.playCount", "stats.play_count", "playCount")),
        likes=as_int(pick(item, "stats.diggCount", "stats.likeCount", "diggCount")),
        comments=as_int(pick(item, "stats.commentCount", "commentCount")),
        shares=as_int(pick(item, "stats.shareCount", "shareCount")),
        saves=as_int(pick(item, "stats.collectCount", "collectCount")),
        duration=duration,
        thumbnail_url=str(pick(item, "video.cover", "video.originCover") or "") or None,
        language_signal=language_signal_from_code(lang_raw),
        language_evidence={"textLanguage": lang_raw} if lang_raw not in (None, "") else None,
        region_signal=region,
        audience_relevance=_audience_relevance(region),
        source_query=source_query or "trending",
        search_rank=rank,
        is_short=duration is None or duration <= max_short_seconds,
    )


def normalize_instagram_item(
    item: dict[str, Any],
    *,
    source_query: str | None = None,
    search_rank: int | None = None,
    max_short_seconds: float = 60,
) -> NormalizedItem | None:
    shortcode = instagram_shortcode(pick(item, "url"), item)
    if not shortcode:
        return None
    product = str(pick(item, "productType", "type") or "").lower()
    if product in {"feed", "carousel_container", "image", "sidecar"}:
        return None
    if product not in {"clips", "reel", "reels", "video", ""} and item.get("type") not in {
        "Video",
        "GraphVideo",
        None,
    }:
        if not pick(item, "videoUrl", "videoPlayCount", "igPlayCount", "videoDuration"):
            return None
    url = _instagram_url(item, shortcode)
    caption = str(pick(item, "caption") or "") or None
    owner = str(pick(item, "ownerUsername", "owner.username") or "") or None
    owner_id = str(pick(item, "ownerId", "owner.id") or "") or None
    profile = str(pick(item, "ownerProfileUrl") or "") or None
    if not profile and owner:
        profile = f"https://www.instagram.com/{owner.lstrip('@')}/"
    audio = str(
        pick(
            item,
            "musicInfo.song_name",
            "musicInfo.audio_title",
            "clipsMusicAttributionInfo.songName",
            "audio",
        )
        or ""
    ) or None
    duration = as_float(pick(item, "videoDuration", "duration"))
    lang = language_signal_from_code(pick(item, "language", "captionLanguage"))
    return NormalizedItem(
        platform="instagram",
        external_id=shortcode,
        url=url,
        creator=owner,
        creator_id=owner_id,
        creator_followers=as_int(pick(item, "ownerFollowersCount", "owner.followersCount")),
        profile_url=profile,
        published_at=parse_timestamp(pick(item, "timestamp", "takenAt", "publishedAt")),
        title_or_caption=caption,
        hashtags=_hashtags(pick(item, "hashtags"), caption),
        audio=audio,
        views=as_int(pick(item, "videoPlayCount", "igPlayCount", "videoViewCount", "playCount")),
        likes=as_int(pick(item, "likesCount", "likes")),
        comments=as_int(pick(item, "commentsCount", "comments")),
        shares=as_int(pick(item, "reshareCount", "sharesCount", "shares")),
        saves=as_int(pick(item, "savesCount", "saves")),
        duration=duration,
        thumbnail_url=str(pick(item, "displayUrl") or "") or None,
        language_signal=lang,
        source_query=source_query or _instagram_source_query(item),
        search_rank=search_rank,
        is_short=duration is None or duration <= max_short_seconds,
    )


def normalize_instagram_creator_reel(
    item: dict[str, Any],
    *,
    source_query: str | None = None,
    search_rank: int | None = None,
    max_short_seconds: float = 90,
) -> NormalizedItem | None:
    """Profile Reels tab items from instagram-scraper/instagram-profile-reels-scraper."""
    if item.get("errorCode") or item.get("_warning"):
        return None
    if item.get("is_video") is False:
        return None
    shortcode = instagram_shortcode(pick(item, "reel_url", "url"), item)
    if not shortcode:
        return None
    url = str(pick(item, "reel_url", "url") or "") or f"https://www.instagram.com/reel/{shortcode}/"
    caption = str(pick(item, "caption") or "") or None
    owner = str(pick(item, "owner.username", "ownerUsername") or "") or None
    owner_id = str(pick(item, "owner.id", "ownerId") or "") or None
    profile = None
    if owner:
        profile = f"https://www.instagram.com/{owner.lstrip('@')}/"
    audio = str(
        pick(
            item,
            "clips_music_attribution_info.song_name",
            "clipsMusicAttributionInfo.songName",
            "audio",
        )
        or ""
    ) or None
    duration = as_float(pick(item, "video_duration", "videoDuration", "duration"))
    followers = as_int(
        pick(
            item,
            "owner.followers",
            "owner.edge_followed_by.count",
            "ownerFollowersCount",
        )
    )
    lang = language_signal_from_code(pick(item, "language", "captionLanguage"))
    creator_query = owner.lstrip("@") if owner else None
    return NormalizedItem(
        platform="instagram",
        external_id=shortcode,
        url=url.split("?")[0],
        creator=owner,
        creator_id=owner_id,
        creator_followers=followers,
        profile_url=profile,
        published_at=parse_timestamp(pick(item, "taken_at", "timestamp", "takenAt")),
        title_or_caption=caption,
        hashtags=_hashtags(pick(item, "hashtags"), caption),
        audio=audio,
        views=as_int(pick(item, "play_count", "view_count", "videoPlayCount", "igPlayCount")),
        likes=as_int(pick(item, "like_count", "likesCount")),
        comments=as_int(pick(item, "comment_count", "commentsCount")),
        shares=as_int(pick(item, "share_count", "reshareCount", "shares")),
        saves=as_int(pick(item, "save_count", "savesCount", "saves")),
        duration=duration,
        thumbnail_url=str(pick(item, "image", "displayUrl") or "") or None,
        language_signal=lang,
        source_query=source_query or creator_query or _instagram_source_query(item),
        search_rank=search_rank,
        is_short=duration is None or duration <= max_short_seconds,
    )

