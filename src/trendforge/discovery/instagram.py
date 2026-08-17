from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from trendforge.acquisition.normalize import normalize_instagram_item
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


def _apify_instagram_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_discovery_config()
    apify = cfg.get("apify") if isinstance(cfg.get("apify"), dict) else {}
    ig = apify.get("instagram") if isinstance(apify.get("instagram"), dict) else {}
    return {
        "enabled": bool(apify.get("enabled", False)) and bool(ig.get("enabled", False)),
        "discover_actor_id": str(ig.get("discover_actor_id") or "apify~instagram-hashtag-scraper"),
        "observe_actor_id": str(ig.get("observe_actor_id") or "apify~instagram-scraper"),
        "hashtags": [str(x).lstrip("#") for x in (ig.get("hashtags") or ["pov", "comedy", "reels"])],
        "results_per_hashtag": max(1, int(ig.get("results_per_hashtag") or 8)),
        "observe_top_n": max(1, int(ig.get("observe_top_n") or 20)),
        "max_short_seconds": float(ig.get("max_short_seconds") or 60),
        "max_charge_usd": float(ig.get("max_charge_usd") or 0.5),
        "timeout_secs": int(ig.get("timeout_secs") or 180),
        "discover_every_minutes": max(1, int(ig.get("discover_every_minutes") or 1440)),
        "observe_every_minutes": max(1, int(ig.get("observe_every_minutes") or 720)),
        "promote": bool(ig.get("promote", False)),
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
        n = int(value)
        # Instagram scrapers use -1 when likes are hidden
        if n < 0:
            return None
        return n
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def instagram_shortcode(url: str | None, item: dict[str, Any] | None = None) -> str:
    if item:
        code = _pick(item, "shortCode", "shortcode", "code")
        if code:
            return str(code)
        vid = _pick(item, "id")
        if vid:
            return str(vid)
    if not url:
        return ""
    path = urlparse(url).path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    # /reel/CODE or /p/CODE or /tv/CODE
    for i, part in enumerate(parts):
        if part in {"reel", "p", "tv", "reels"} and i + 1 < len(parts):
            return parts[i + 1]
    return parts[-1] if parts else ""


def _canonical_url(item: dict[str, Any], shortcode: str) -> str:
    url = str(_pick(item, "url", "inputUrl") or "")
    if "/reel/" in url or "/p/" in url or "/tv/" in url:
        return url.split("?")[0]
    product = str(_pick(item, "productType") or "").lower()
    kind = "reel" if product in {"clips", "reel", "reels"} else "p"
    return f"https://www.instagram.com/{kind}/{shortcode}/"


def item_to_video(
    item: dict[str, Any],
    *,
    source_query: str | None = None,
    search_rank: int | None = None,
    max_short_seconds: float = 60,
) -> DiscoveredVideo | None:
    normalized = normalize_instagram_item(
        item,
        source_query=source_query,
        search_rank=search_rank,
        max_short_seconds=max_short_seconds,
    )
    if normalized is None:
        return None
    product = str(_pick(item, "productType", "type") or "").lower()
    return to_discovered_video(
        normalized,
        provenance={"apify": True, "product_type": product or None},
    )


def _source_query(item: dict[str, Any], fallback: str = "instagram") -> str:
    input_url = str(_pick(item, "inputUrl") or "")
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


def run_instagram_discovery(
    db: Session,
    *,
    client: ApifyClient | None = None,
    cfg: dict[str, Any] | None = None,
) -> DiscoveryRun:
    cfg = cfg or load_discovery_config()
    ig = _apify_instagram_cfg(cfg)
    client = client or ApifyClient()
    run = DiscoveryRun(
        kind="discover_instagram",
        started_at=utcnow(),
        queries_used=list(ig["hashtags"]),
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
        per = int(ig["results_per_hashtag"])
        actor_input = {
            "hashtags": ig["hashtags"],
            "resultsType": "reels",
            "resultsLimit": per,
        }
        cap = per * max(1, len(ig["hashtags"]))
        items = client.run_actor(
            ig["discover_actor_id"],
            actor_input,
            timeout_secs=ig["timeout_secs"],
            max_total_charge_usd=ig["max_charge_usd"],
            max_items=cap,
        )
        rank = 0
        for item in items:
            video = item_to_video(
                item,
                source_query=_source_query(item, ig["hashtags"][0] if ig["hashtags"] else "instagram"),
                search_rank=rank,
                max_short_seconds=ig["max_short_seconds"],
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


def run_instagram_observation(
    db: Session,
    *,
    client: ApifyClient | None = None,
    cfg: dict[str, Any] | None = None,
) -> DiscoveryRun:
    cfg = cfg or load_discovery_config()
    ig = _apify_instagram_cfg(cfg)
    client = client or ApifyClient()
    run = DiscoveryRun(kind="observe_instagram", started_at=utcnow(), queries_used=[], api_errors=[])
    db.add(run)
    db.flush()
    errors: list[dict[str, Any]] = []
    rows = (
        db.query(ContentCandidate)
        .filter(ContentCandidate.platform == "instagram", ContentCandidate.url.isnot(None))
        .order_by(ContentCandidate.discovery_score.desc(), ContentCandidate.id.asc())
        .limit(int(ig["observe_top_n"]))
        .all()
    )
    urls = [r.url for r in rows if r.url]
    obs = 0
    if urls:
        try:
            items = client.run_actor(
                ig["observe_actor_id"],
                {
                    "directUrls": urls,
                    "resultsType": "posts",
                    "resultsLimit": max(1, len(urls)),
                },
                timeout_secs=ig["timeout_secs"],
                max_total_charge_usd=ig["max_charge_usd"],
                max_items=len(urls),
            )
            by_id: dict[str, DiscoveredVideo] = {}
            for item in items:
                video = item_to_video(item, max_short_seconds=ig["max_short_seconds"])
                if video:
                    by_id[video.external_id] = video
                    by_id[video.url] = video
                    by_id[video.url.rstrip("/") + "/"] = video
            for row in rows:
                video = by_id.get(row.external_id or "") or by_id.get(row.url or "")
                if not video and row.url:
                    video = by_id.get(row.url.rstrip("/")) or by_id.get(row.url.rstrip("/") + "/")
                if not video:
                    continue
                refresh_candidate_metrics(row, video)
                if video.shares is not None:
                    row.shares = video.shares
                record_observation(db, row, video, source="instagram_observe")
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
