from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from trendforge.acquisition.normalize import normalize_tiktok_item
from trendforge.acquisition.persist import to_discovered_video
from trendforge.config import load_discovery_config
from trendforge.discovery.apify import ApifyClient
from trendforge.discovery.pipeline import (
    apply_signals,
    record_observation,
    refresh_candidate_metrics,
    upsert_discovered_video,
)
from trendforge.discovery.provider import DiscoveredVideo
from trendforge.models import ContentCandidate, DiscoveryRun, utcnow


def _apify_tiktok_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_discovery_config()
    apify = cfg.get("apify") if isinstance(cfg.get("apify"), dict) else {}
    tiktok = apify.get("tiktok") if isinstance(apify.get("tiktok"), dict) else {}
    return {
        "enabled": bool(apify.get("enabled", False)),
        "actor_id": str(tiktok.get("actor_id") or "clockworks~tiktok-scraper"),
        "hashtags": [str(x).lstrip("#") for x in (tiktok.get("hashtags") or ["pov", "comedy", "storytime"])],
        "results_per_hashtag": max(1, int(tiktok.get("results_per_hashtag") or 8)),
        "observe_top_n": max(1, int(tiktok.get("observe_top_n") or 20)),
        "max_short_seconds": float(tiktok.get("max_short_seconds") or 60),
        "max_charge_usd": float(tiktok.get("max_charge_usd") or 0.5),
        "timeout_secs": int(tiktok.get("timeout_secs") or 180),
        "promote": bool(tiktok.get("promote", False)),
        "keep_english_only": bool(tiktok.get("keep_english_only", False)),
    }


def _pick(item: dict[str, Any], *keys: str) -> Any:
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


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def tiktok_video_id(url: str | None, item: dict[str, Any] | None = None) -> str:
    if item:
        vid = _pick(item, "id", "videoMeta.id")
        if vid:
            return str(vid)
    if not url:
        return ""
    path = urlparse(url).path.rstrip("/")
    if "/video/" in path:
        return path.split("/video/")[-1].split("?")[0]
    return path.rsplit("/", 1)[-1]


def item_to_video(
    item: dict[str, Any],
    *,
    source_query: str | None = None,
    search_rank: int | None = None,
    max_short_seconds: float = 60,
) -> DiscoveredVideo | None:
    normalized = normalize_tiktok_item(
        item,
        source_query=source_query,
        search_rank=search_rank,
        max_short_seconds=max_short_seconds,
    )
    if normalized is None:
        return None
    return to_discovered_video(
        normalized,
        provenance={"apify": True, "text_language": normalized.language_signal},
    )


def _source_query(item: dict[str, Any]) -> str:
    query = _pick(item, "searchQuery")
    if query:
        return str(query)
    tags = item.get("hashtags")
    if isinstance(tags, list) and tags:
        first = tags[0]
        if isinstance(first, dict) and first.get("name"):
            return str(first["name"])
        if isinstance(first, str):
            return first
    return "tiktok"


def _base_actor_input() -> dict[str, Any]:
    return {
        "excludePinnedPosts": False,
        "scrapeRelatedVideos": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadCovers": False,
        "shouldDownloadMusicCovers": False,
        "shouldDownloadSlideshowImages": False,
        "shouldDownloadSubtitles": False,
        "shouldDownloadVideos": False,
        "commentsPerPost": 0,
        "topLevelCommentsPerPost": 0,
        "maxRepliesPerComment": 0,
        "maxFollowersPerProfile": 0,
        "maxFollowingPerProfile": 0,
        "proxyCountryCode": "None",
    }


def run_tiktok_discovery(
    db: Session,
    *,
    client: ApifyClient | None = None,
    cfg: dict[str, Any] | None = None,
) -> DiscoveryRun:
    cfg = cfg or load_discovery_config()
    tt = _apify_tiktok_cfg(cfg)
    client = client or ApifyClient()
    run = DiscoveryRun(
        kind="discover_tiktok",
        started_at=utcnow(),
        queries_used=list(tt["hashtags"]),
        api_errors=[],
    )
    db.add(run)
    db.flush()
    errors: list[dict[str, Any]] = []
    found = 0
    created_n = 0
    dupes = 0
    obs = 0
    try:
        per = int(tt["results_per_hashtag"])
        actor_input = {
            **_base_actor_input(),
            "hashtags": tt["hashtags"],
            "resultsPerPage": per,
        }
        cap = per * max(1, len(tt["hashtags"]))
        items = client.run_actor(
            tt["actor_id"],
            actor_input,
            timeout_secs=tt["timeout_secs"],
            max_total_charge_usd=tt["max_charge_usd"],
            max_items=cap,
        )
        rank = 0
        for item in items:
            lang = str(_pick(item, "textLanguage") or "").lower()
            if tt["keep_english_only"] and lang and not lang.startswith("en"):
                continue
            video = item_to_video(
                item,
                source_query=_source_query(item),
                search_rank=rank,
                max_short_seconds=tt["max_short_seconds"],
            )
            if video is None or not video.is_short:
                continue
            rank += 1
            _, created = upsert_discovered_video(db, video, cfg=cfg)
            found += 1
            obs += 1
            if created:
                created_n += 1
            else:
                dupes += 1
    except Exception as exc:
        errors.append({"type": "apify", "message": str(exc)})
    run.candidates_found = found
    run.new_candidates = created_n
    run.duplicates = dupes
    run.observations_written = obs
    run.api_errors = errors
    run.completed_at = utcnow()
    db.commit()
    return run


def select_tiktok_observe_candidates(
    db: Session,
    *,
    limit: int,
    profile: str | None = None,
) -> list[ContentCandidate]:
    rows = (
        db.query(ContentCandidate)
        .filter(ContentCandidate.platform == "tiktok", ContentCandidate.url.isnot(None))
        .order_by(ContentCandidate.discovered_at.asc(), ContentCandidate.id.asc())
        .all()
        if profile
        else (
            db.query(ContentCandidate)
            .filter(ContentCandidate.platform == "tiktok", ContentCandidate.url.isnot(None))
            .order_by(ContentCandidate.discovery_score.desc(), ContentCandidate.id.asc())
            .limit(limit)
            .all()
        )
    )
    if not profile:
        return rows
    matched = [
        row
        for row in rows
        if profile in (row.acquisition_profiles or [])
        or (row.raw_metadata or {}).get("acquisition_profile") == profile
    ]
    return matched[:limit]


def run_tiktok_observation(
    db: Session,
    *,
    client: ApifyClient | None = None,
    cfg: dict[str, Any] | None = None,
    limit: int | None = None,
    profile: str | None = None,
) -> DiscoveryRun:
    cfg = cfg or load_discovery_config()
    tt = _apify_tiktok_cfg(cfg)
    client = client or ApifyClient()
    run = DiscoveryRun(kind="observe_tiktok", started_at=utcnow(), queries_used=[], api_errors=[])
    db.add(run)
    db.flush()
    errors: list[dict[str, Any]] = []
    cap = int(limit) if limit is not None else int(tt["observe_top_n"])
    rows = select_tiktok_observe_candidates(db, limit=cap, profile=profile)
    urls = [r.url for r in rows if r.url]
    obs = 0
    if urls:
        try:
            items = client.run_actor(
                tt["actor_id"],
                {**_base_actor_input(), "postURLs": urls, "resultsPerPage": 1},
                timeout_secs=tt["timeout_secs"],
                max_total_charge_usd=tt["max_charge_usd"],
                max_items=len(urls),
            )
            by_id = {}
            for item in items:
                video = item_to_video(item, max_short_seconds=tt["max_short_seconds"])
                if video:
                    by_id[video.external_id] = video
                    by_id[video.url] = video

            for row in rows:
                video = by_id.get(row.external_id or "") or by_id.get(row.url or "")
                if not video:
                    continue
                refresh_candidate_metrics(row, video)
                if video.shares is not None:
                    row.shares = video.shares
                record_observation(db, row, video, source="tiktok_observe")
                apply_signals(db, row, cfg)
                obs += 1
        except Exception as exc:
            errors.append({"type": "apify", "message": str(exc)})
    run.candidates_found = len(rows)
    run.observations_written = obs
    run.api_errors = errors
    run.completed_at = utcnow()
    db.commit()
    return run
