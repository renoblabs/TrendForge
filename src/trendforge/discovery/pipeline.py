from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from trendforge.analysis.client import MissingAPIKeyError
from trendforge.analysis.origin import is_seed_record
from trendforge.analysis.prompt import PROMPT_VERSION
from trendforge.analysis.selection import (
    analysis_config,
    is_high_signal,
    is_stub_analysis,
    needs_live_reanalysis,
    rank_for_analysis,
)
from trendforge.config import get_settings, load_discovery_config, load_discovery_weights
from trendforge.discovery.profiles import (
    classify_language,
    keep_language_candidate,
    relevance_language,
    resolve_active_profile,
    sampling_metadata,
    search_regions,
)
from trendforge.discovery.provider import (
    DiscoveryError,
    DiscoveredVideo,
    QuotaExhaustedError,
)
from trendforge.discovery.shorts import is_youtube_short, parse_iso8601_duration
from trendforge.discovery.signals import ObservationPoint, compute_signals
from trendforge.discovery.youtube import YouTubeDiscoveryProvider
from trendforge.models import (
    AnalysisStatus,
    CandidateObservation,
    ContentCandidate,
    DiscoveryRun,
    utcnow,
)
from trendforge.services import analyze_candidate


HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")


BROAD_SOURCE = "broad"


def published_after_from_config(
    cfg: dict[str, Any], now: datetime | None = None, window_key: str | None = None
) -> datetime:
    now = now or datetime.now(timezone.utc)
    window = window_key or cfg.get("published_window") or "last_48_hours"
    hours = int((cfg.get("windows") or {}).get(window, 48))
    return now - timedelta(hours=hours)


def resolve_search_plan(
    cfg: dict[str, Any],
    *,
    mode: str = "topics",
    now: datetime | None = None,
) -> tuple[list[str], str, datetime, str]:
    """Return (queries, order, published_after, run_kind)."""
    if mode == "broad":
        broad = cfg.get("broad") or {}
        terms = list(broad.get("search_terms") or [""])
        if not terms:
            terms = [""]
        order = str(broad.get("order") or "viewCount")
        window = broad.get("published_window") or cfg.get("published_window")
        placeholder = str(broad.get("unconstrained_query") or "").strip()
        resolved: list[str] = []
        for term in terms:
            text = "" if term is None else str(term)
            resolved.append(text if text.strip() else placeholder)
        if not resolved:
            resolved = [placeholder]
        return resolved, order, published_after_from_config(cfg, now=now, window_key=window), "discover_broad"
    queries = list(cfg.get("queries") or [])
    return queries, "date", published_after_from_config(cfg, now=now), "discover"


def youtube_shorts_url(video_id: str) -> str:
    return f"https://www.youtube.com/shorts/{video_id}"


def extract_hashtags(*texts: str | None) -> list[str]:
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in HASHTAG_RE.findall(text):
            tag = match.lower()
            if tag not in found:
                found.append(tag)
    return found


def parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def video_to_discovered(
    video: dict[str, Any],
    *,
    source_query: str | None,
    search_rank: int | None,
    channel: dict[str, Any] | None,
    max_short_seconds: float,
) -> DiscoveredVideo:
    snippet = video.get("snippet") or {}
    stats = video.get("statistics") or {}
    details = video.get("contentDetails") or {}
    thumbs = snippet.get("thumbnails") or {}
    thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
    vid = video.get("id") or ""
    duration = parse_iso8601_duration(details.get("duration"))
    channel_stats = (channel or {}).get("statistics") or {}
    title = snippet.get("title")
    description = snippet.get("description")
    tags = snippet.get("tags") or []
    tag_blob = " ".join(f"#{t}" for t in tags if isinstance(t, str))
    return DiscoveredVideo(
        external_id=vid,
        url=youtube_shorts_url(vid),
        title=title,
        description=description,
        channel_id=snippet.get("channelId"),
        channel_name=snippet.get("channelTitle"),
        published_at=parse_published_at(snippet.get("publishedAt")),
        duration=duration,
        views=_int_or_none(stats.get("viewCount")),
        likes=_int_or_none(stats.get("likeCount")),
        comments=_int_or_none(stats.get("commentCount")),
        favorite_count=_int_or_none(stats.get("favoriteCount")),
        thumbnail_url=thumb,
        hashtags=extract_hashtags(title, description, tag_blob),
        category=snippet.get("categoryId"),
        channel_subscriber_count=_int_or_none(channel_stats.get("subscriberCount")),
        is_short=is_youtube_short(video, max_seconds=max_short_seconds),
        source_query=source_query,
        search_rank=search_rank,
        raw={"video": video, "channel": channel},
    )


SAMPLING_RAW_KEYS = (
    "discovery_profile",
    "acquisition_profile",
    "profiles_that_found_candidate",
    "queries_that_found_candidate",
    "language_signal",
    "region_signal",
    "language_evidence",
    "profile_regions",
    "region_meaning",
    "provider",
    "actor_id",
    "audio",
    "profile_url",
    "saves",
    "source_actor",
    "source",
    "audience_relevance",
    "creator_history",
)


def _union_str_list(*groups: Any) -> list[str]:
    seen: list[str] = []
    for value in groups:
        items: list[str] = []
        if isinstance(value, list):
            items = [str(x) for x in value if x]
        elif value:
            items = [str(value)]
        for name in items:
            if name not in seen:
                seen.append(name)
    return seen


def merge_sampling_raw(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(incoming or {})
    prev = existing or {}
    skip = {"profiles_that_found_candidate", "queries_that_found_candidate"}
    for key in SAMPLING_RAW_KEYS:
        if key in skip:
            continue
        if merged.get(key) in (None, [], {}) and prev.get(key) not in (None, [], {}):
            merged[key] = prev[key]
    profiles = _union_str_list(
        prev.get("profiles_that_found_candidate"),
        merged.get("profiles_that_found_candidate"),
    )
    if profiles:
        merged["profiles_that_found_candidate"] = profiles
    queries = _union_str_list(
        prev.get("queries_that_found_candidate"),
        merged.get("queries_that_found_candidate"),
    )
    if queries:
        merged["queries_that_found_candidate"] = queries
    return merged


def find_candidate(
    db: Session, *, external_id: str, url: str, platform: str = "youtube"
) -> ContentCandidate | None:
    row = (
        db.query(ContentCandidate)
        .filter(
            ContentCandidate.platform == platform,
            ContentCandidate.external_id == external_id,
        )
        .one_or_none()
    )
    if row:
        return row
    return db.query(ContentCandidate).filter_by(url=url).one_or_none()


def merge_source_queries(existing: list | None, query: str | None) -> list[str]:
    queries = [str(q) for q in (existing or [])]
    if query and query not in queries:
        queries.append(query)
    return queries


def record_observation(
    db: Session,
    candidate: ContentCandidate,
    video: DiscoveredVideo,
    *,
    source: str = "youtube",
    observed_at: datetime | None = None,
) -> CandidateObservation:
    obs = CandidateObservation(
        candidate_id=candidate.id,
        observed_at=observed_at or utcnow(),
        view_count=video.views,
        like_count=video.likes,
        comment_count=video.comments,
        favorite_count=video.favorite_count,
        channel_subscriber_count=video.channel_subscriber_count,
        rank=video.search_rank,
        source=source,
        raw_metadata=video.raw,
    )
    db.add(obs)
    db.flush()
    return obs


def refresh_candidate_metrics(candidate: ContentCandidate, video: DiscoveredVideo) -> None:
    if video.views is not None:
        candidate.views = video.views
    if video.likes is not None:
        candidate.likes = video.likes
    if video.comments is not None:
        candidate.comments = video.comments
    if video.shares is not None:
        candidate.shares = video.shares
    if video.favorite_count is not None:
        candidate.favorite_count = video.favorite_count
    if video.channel_subscriber_count is not None:
        candidate.channel_subscriber_count = video.channel_subscriber_count
    if video.title:
        candidate.title = video.title
    if video.description is not None:
        candidate.description = video.description
    if video.thumbnail_url:
        candidate.thumbnail_url = video.thumbnail_url
    if video.channel_name:
        candidate.channel_name = video.channel_name
        candidate.creator = video.channel_name
    if video.duration is not None:
        candidate.duration = video.duration
    candidate.is_short = video.is_short
    candidate.raw_metadata = merge_sampling_raw(candidate.raw_metadata, video.raw)


def apply_signals(db: Session, candidate: ContentCandidate, cfg: dict[str, Any] | None = None) -> None:
    cfg = cfg or load_discovery_config()
    weights = load_discovery_weights()
    observations = (
        db.query(CandidateObservation)
        .filter_by(candidate_id=candidate.id)
        .order_by(CandidateObservation.observed_at.asc())
        .all()
    )
    points = [
        ObservationPoint(
            observed_at=o.observed_at,
            view_count=o.view_count,
            like_count=o.like_count,
            comment_count=o.comment_count,
        )
        for o in observations
    ]
    others = (
        db.query(ContentCandidate.views)
        .filter(
            ContentCandidate.platform == (candidate.platform or "youtube"),
            ContentCandidate.channel_id == candidate.channel_id,
            ContentCandidate.id != candidate.id,
            ContentCandidate.channel_id.isnot(None),
        )
        .all()
    )
    other_views = [row[0] for row in others]
    signals = compute_signals(
        observations=points,
        published_at=candidate.published_at,
        views=candidate.views,
        likes=candidate.likes,
        comments=candidate.comments,
        other_channel_views=other_views,
        min_creator_videos=int(cfg.get("min_creator_videos", 3)),
        weights_config=weights,
    )
    candidate.age_hours = signals.age_hours
    candidate.views_per_hour = signals.views_per_hour
    candidate.acceleration = signals.acceleration
    candidate.like_rate = signals.like_rate
    candidate.comment_rate = signals.comment_rate
    candidate.creator_baseline = signals.creator_baseline
    candidate.creator_baseline_estimated = signals.creator_baseline_estimated
    candidate.creator_lift = signals.creator_lift
    candidate.discovery_score = signals.discovery_score
    candidate.discovery_breakdown = signals.breakdown
    candidate.discovery_labels = signals.labels


def upsert_discovered_video(
    db: Session,
    video: DiscoveredVideo,
    *,
    cfg: dict[str, Any] | None = None,
) -> tuple[ContentCandidate, bool]:
    """Return (candidate, created). Dedupes on youtube external_id / url."""
    existing = find_candidate(
        db, external_id=video.external_id, url=video.url, platform=video.platform or "youtube"
    )
    created = existing is None
    if existing is None:
        existing = ContentCandidate(
            platform=video.platform or "youtube",
            url=video.url,
            external_id=video.external_id,
            analysis_status=AnalysisStatus.SKIPPED,
            data_origin="live",
        )
        db.add(existing)
        db.flush()
    queries = merge_source_queries(existing.source_queries, video.source_query)
    existing.source_queries = queries
    existing.source_query = existing.source_query or video.source_query
    existing.external_id = video.external_id
    existing.channel_id = video.channel_id or existing.channel_id
    existing.channel_name = video.channel_name or existing.channel_name
    existing.creator = video.channel_name or existing.creator
    existing.published_at = video.published_at or existing.published_at
    existing.hashtags = video.hashtags or existing.hashtags
    existing.category = video.category or existing.category
    refresh_candidate_metrics(existing, video)
    db.flush()
    record_observation(db, existing, video)
    apply_signals(db, existing, cfg)
    return existing, created


def collect_search_ids(
    provider: YouTubeDiscoveryProvider,
    query: str,
    published_after: datetime,
    *,
    max_results: int,
    max_pages: int,
    region_code: str | None,
    errors: list[dict[str, Any]],
    order: str = "date",
    relevance_language: str | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    ids: list[str] = []
    raw_pages: list[dict[str, Any]] = []
    token = None
    for _ in range(max(1, max_pages)):
        try:
            page = provider.search(
                query,
                published_after=published_after,
                page_token=token,
                max_results=max_results,
                region_code=region_code,
                relevance_language=relevance_language,
                order=order,
            )
        except QuotaExhaustedError as exc:
            errors.append({"query": query, "type": "quota", "message": str(exc)})
            raise
        except DiscoveryError as exc:
            errors.append({"query": query, "type": "api", "message": str(exc)})
            break
        raw_pages.append(page.raw)
        ids.extend(page.video_ids)
        token = page.next_page_token
        if not token:
            break
    # preserve order, drop dupes within this query
    seen: set[str] = set()
    ordered: list[str] = []
    for vid in ids:
        if vid not in seen:
            seen.add(vid)
            ordered.append(vid)
    return ordered, raw_pages


def format_discovery_diagnostics(run: DiscoveryRun) -> str:
    notes: dict[str, Any] = {}
    if run.notes:
        try:
            parsed = json.loads(run.notes)
            if isinstance(parsed, dict):
                notes = parsed
        except json.JSONDecodeError:
            notes = {}
    regions = notes.get("regions") or []
    if isinstance(regions, list):
        region_text = ",".join(str(r) for r in regions) or "(none)"
    else:
        region_text = str(regions)
    q_terms = notes.get("search_q") or []
    q_text = ",".join(str(q) for q in q_terms) if isinstance(q_terms, list) else str(q_terms)
    lines = [
        (
            f"profile={notes.get('discovery_profile') or '(none)'} "
            f"regions={region_text} relevanceLanguage={notes.get('relevanceLanguage') or '(none)'} "
            f"videoDuration={notes.get('videoDuration')} publishedAfter={notes.get('publishedAfter')} "
            f"order={notes.get('order')} q={q_text or '(omitted)'}"
        ),
        (
            f"search_results={notes.get('search_results', 0)} "
            f"video_details={notes.get('video_details', 0)} "
            f"duration_candidates={notes.get('duration_candidates', 0)} "
            f"shorts_eligible={notes.get('shorts_eligible', 0)} "
            f"shorts_rejected={notes.get('shorts_rejected', 0)} "
            f"language_kept={notes.get('language_kept', 0)} "
            f"language_dropped={notes.get('language_dropped', notes.get('language_filtered', 0))} "
            f"duplicates={notes.get('duplicates', run.duplicates)} "
            f"persisted={notes.get('persisted', run.candidates_found)}"
        ),
    ]
    by_region = notes.get("search_results_by_region") or {}
    if by_region:
        region_bits = " ".join(f"{k}={v}" for k, v in by_region.items())
        lines.append(f"search_by_region {region_bits}")
    if int(notes.get("search_results") or 0) == 0:
        profile = notes.get("discovery_profile") or "current settings"
        lines.append(f"Search returned 0 candidates with profile {profile}.")
    return "\n".join(lines)


def run_discovery(
    db: Session,
    *,
    provider: YouTubeDiscoveryProvider | None = None,
    promote: bool = True,
    force_stub_analysis: bool = True,
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
    limit: int | None = None,
    mode: str = "topics",
    profile_name: str | None = None,
) -> DiscoveryRun:
    cfg = cfg or load_discovery_config()
    provider = provider or YouTubeDiscoveryProvider()
    queries, order, published_after, run_kind = resolve_search_plan(cfg, mode=mode, now=now)
    profile = resolve_active_profile(cfg, mode=mode, profile_name=profile_name)
    regions = search_regions(profile, cfg.get("region_code"))
    lang_param = relevance_language(profile)
    run = DiscoveryRun(
        kind=run_kind,
        started_at=utcnow(),
        queries_used=list(queries),
        api_errors=[],
    )
    db.add(run)
    db.flush()

    errors: list[dict[str, Any]] = []
    max_short = float(cfg.get("max_short_seconds", 60))
    found_ids: list[str] = []
    query_ranks: dict[str, dict[str, Any]] = {}
    max_per_query = int(cfg.get("max_results_per_query", 25))
    if mode == "broad":
        broad = cfg.get("broad") or {}
        max_per_query = int(broad.get("max_results") or max_per_query)
        max_pages = int(broad.get("max_pages") or cfg.get("max_pages_per_query", 1))
    else:
        max_pages = int(cfg.get("max_pages_per_query", 1))
    queries_ran: list[str] = []
    search_by_region: dict[str, int] = {}
    placeholder = str((cfg.get("broad") or {}).get("unconstrained_query") or "").strip()

    try:
        for query in queries:
            for region in regions:
                unique_so_far = list(dict.fromkeys(found_ids))
                if limit is not None and len(unique_so_far) >= limit:
                    break
                remaining = None if limit is None else limit - len(unique_so_far)
                page_size = max_per_query if remaining is None else min(max_per_query, remaining)
                if page_size <= 0:
                    break
                if mode == "broad" and (not query or query == placeholder):
                    label = BROAD_SOURCE
                else:
                    label = query if query else BROAD_SOURCE
                if label not in queries_ran:
                    queries_ran.append(label)
                ids, _pages = collect_search_ids(
                    provider,
                    query,
                    published_after,
                    max_results=page_size,
                    max_pages=max_pages,
                    region_code=region,
                    errors=errors,
                    order=order,
                    relevance_language=lang_param,
                )
                region_key = region or "none"
                search_by_region[region_key] = search_by_region.get(region_key, 0) + len(ids)
                for idx, vid in enumerate(ids, start=1):
                    found_ids.append(vid)
                    info = query_ranks.setdefault(
                        vid, {"queries": [], "rank": idx, "region": region}
                    )
                    if label not in info["queries"]:
                        info["queries"].append(label)
                    info["rank"] = min(info["rank"], idx)
                    if info.get("region") is None and region:
                        info["region"] = region
            unique_so_far = list(dict.fromkeys(found_ids))
            if limit is not None and len(unique_so_far) >= limit:
                break
        unique_ids = list(dict.fromkeys(found_ids))
        if limit is not None:
            unique_ids = unique_ids[:limit]
        videos = provider.get_videos(unique_ids) if unique_ids else []
        channel_ids = [
            (v.get("snippet") or {}).get("channelId") for v in videos if (v.get("snippet") or {}).get("channelId")
        ]
        channels = provider.get_channels(channel_ids)
    except QuotaExhaustedError as exc:
        errors.append({"type": "quota", "message": str(exc)})
        videos = []
        channels = {}
        unique_ids = []

    new_count = 0
    dup_count = 0
    obs_count = 0
    language_filtered = 0
    language_kept = 0
    duration_candidates = 0
    shorts_eligible = 0
    shorts_rejected = 0
    seen_run: set[str] = set()

    for video in videos:
        if limit is not None and len(seen_run) >= limit:
            break
        vid = video.get("id")
        if not vid:
            continue
        duration_candidates += 1
        if not is_youtube_short(video, max_seconds=max_short):
            shorts_rejected += 1
            continue
        shorts_eligible += 1
        snippet = video.get("snippet") or {}
        meta = query_ranks.get(vid, {"queries": [], "rank": None, "region": None})
        source_queries = meta.get("queries") or [None]
        language_signal = "unknown"
        if profile and profile.language:
            language_signal = classify_language(snippet, profile.language)
            if not keep_language_candidate(language_signal):
                language_filtered += 1
                continue
        language_kept += 1
        discovered = video_to_discovered(
            video,
            source_query=source_queries[0],
            search_rank=meta.get("rank"),
            channel=channels.get(snippet.get("channelId")),
            max_short_seconds=max_short,
        )
        region_signal = meta.get("region")
        if not region_signal and profile and profile.regions:
            region_signal = ",".join(profile.regions)
        discovered.raw.update(
            sampling_metadata(
                profile=profile,
                language_signal=language_signal,
                region_signal=region_signal,
                snippet=snippet,
            )
        )
        candidate, created = upsert_discovered_video(db, discovered, cfg=cfg)
        obs_count += 1
        if created:
            new_count += 1
        else:
            dup_count += 1
        for extra_q in source_queries[1:]:
            candidate.source_queries = merge_source_queries(candidate.source_queries, extra_q)
        seen_run.add(vid)

    promoted = 0
    if promote:
        promoted = promote_top_candidates(
            db, cfg=cfg, force_stub_analysis=force_stub_analysis
        )

    run.candidates_found = len(seen_run)
    run.new_candidates = new_count
    run.duplicates = dup_count
    run.promoted_candidates = promoted
    run.observations_written = obs_count
    run.queries_used = queries_ran
    run.api_errors = errors or None
    run.notes = json.dumps(
        {
            "discovery_profile": profile.name if profile else None,
            "regions": [r for r in regions if r],
            "relevanceLanguage": lang_param,
            "videoDuration": "short",
            "publishedAfter": published_after.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "order": order,
            "search_q": [q for q in queries if q],
            "search_results": len(unique_ids),
            "search_results_by_region": search_by_region,
            "video_details": len(videos),
            "duration_candidates": duration_candidates,
            "shorts_eligible": shorts_eligible,
            "shorts_rejected": shorts_rejected,
            "language_kept": language_kept,
            "language_dropped": language_filtered,
            "language_filtered": language_filtered,
            "duplicates": dup_count,
            "persisted": len(seen_run),
        }
    )
    run.completed_at = utcnow()
    db.commit()
    db.refresh(run)
    return run


def run_observation_refresh(
    db: Session,
    *,
    provider: YouTubeDiscoveryProvider | None = None,
    cfg: dict[str, Any] | None = None,
) -> DiscoveryRun:
    cfg = cfg or load_discovery_config()
    provider = provider or YouTubeDiscoveryProvider()
    run = DiscoveryRun(kind="observe", started_at=utcnow(), queries_used=[], api_errors=[])
    db.add(run)
    db.flush()
    errors: list[dict[str, Any]] = []

    q = (
        db.query(ContentCandidate)
        .filter(ContentCandidate.platform == "youtube", ContentCandidate.external_id.isnot(None))
        .order_by(ContentCandidate.discovery_score.desc(), ContentCandidate.id.asc())
    )
    limit = int(cfg.get("observe_top_n", 80))
    rows = q.limit(limit).all()
    ids = [r.external_id for r in rows if r.external_id]
    try:
        videos = provider.get_videos(ids)
        channel_ids = [
            (v.get("snippet") or {}).get("channelId") for v in videos if (v.get("snippet") or {}).get("channelId")
        ]
        channels = provider.get_channels(channel_ids)
    except (QuotaExhaustedError, DiscoveryError) as exc:
        kind = "quota" if isinstance(exc, QuotaExhaustedError) else "api"
        errors.append({"type": kind, "message": str(exc)})
        videos = []
        channels = {}

    by_id = {v.get("id"): v for v in videos if v.get("id")}
    obs_count = 0
    max_short = float(cfg.get("max_short_seconds", 60))
    for row in rows:
        video = by_id.get(row.external_id)
        if not video:
            continue
        discovered = video_to_discovered(
            video,
            source_query=row.source_query,
            search_rank=None,
            channel=channels.get((video.get("snippet") or {}).get("channelId")),
            max_short_seconds=max_short,
        )
        refresh_candidate_metrics(row, discovered)
        record_observation(db, row, discovered, source="youtube_observe")
        apply_signals(db, row, cfg)
        obs_count += 1

    run.candidates_found = len(rows)
    run.observations_written = obs_count
    run.api_errors = errors or None
    run.completed_at = utcnow()
    db.commit()
    db.refresh(run)
    return run


def promote_top_candidates(
    db: Session,
    *,
    cfg: dict[str, Any] | None = None,
    force_stub_analysis: bool = True,
    analyze: bool = True,
) -> int:
    cfg = cfg or load_discovery_config()
    conf = analysis_config(cfg)
    top_n = int(cfg.get("promote_top_n", 25))
    min_score = cfg.get("promote_min_score")
    q = (
        db.query(ContentCandidate)
        .filter(
            ContentCandidate.platform == "youtube",
            ContentCandidate.promoted_at.is_(None),
            ContentCandidate.is_short.is_(True),
        )
        .order_by(ContentCandidate.discovery_score.desc(), ContentCandidate.id.asc())
    )
    if min_score is not None:
        q = q.filter(ContentCandidate.discovery_score >= float(min_score))
    rows = [row for row in q.all() if not is_seed_record(row)]
    if conf.get("high_signal_only", True):
        rows = [row for row in rows if is_high_signal(row, cfg)]
    cap = int(conf.get("max_analyze_per_run") or top_n)
    rows = rows[: min(top_n, cap)]
    count = 0
    for row in rows:
        row.promoted_at = utcnow()
        if row.analysis_status == AnalysisStatus.SKIPPED:
            row.analysis_status = AnalysisStatus.PENDING
        count += 1
        if analyze and row.analysis_status in {AnalysisStatus.PENDING, AnalysisStatus.FAILED}:
            try:
                analyze_candidate(db, row, force_stub=force_stub_analysis)
            except Exception:
                continue
    db.commit()
    return count


@dataclass
class HighSignalAnalysisStats:
    eligible: int = 0
    analyzed: int = 0
    skipped: int = 0
    promoted: int = 0
    stub_refreshed: int = 0
    live_appended: int = 0
    prompt_version: str = PROMPT_VERSION
    skipped_reason: str | None = None


def list_live_high_signal_candidates(
    db: Session,
    cfg: dict[str, Any] | None = None,
) -> list[ContentCandidate]:
    cfg = cfg or load_discovery_config()
    rows = (
        db.query(ContentCandidate)
        .filter(
            ContentCandidate.platform == "youtube",
            ContentCandidate.is_short.is_(True),
        )
        .all()
    )
    live = [row for row in rows if not is_seed_record(row) and is_high_signal(row, cfg)]
    return rank_for_analysis(live)


def run_high_signal_analysis(
    db: Session,
    *,
    live: bool = False,
    cfg: dict[str, Any] | None = None,
) -> HighSignalAnalysisStats:
    """Promote unpromoted high-signal rows, then analyze/re-analyze with the current prompt.

    Live mode appends a new analysis when the current result is stub or an older prompt.
    It does not mutate historical analysis_history entries.
    """
    cfg = cfg or load_discovery_config()
    conf = analysis_config(cfg)
    cap = int(conf.get("max_analyze_per_run") or cfg.get("promote_top_n", 25))
    stats = HighSignalAnalysisStats()

    stats.promoted = promote_top_candidates(
        db,
        cfg=cfg,
        force_stub_analysis=not live,
        analyze=False,
    )

    eligible = list_live_high_signal_candidates(db, cfg)
    stats.eligible = len(eligible)
    pool = [row for row in eligible if row.promoted_at is not None]
    if live:
        queue = [row for row in pool if needs_live_reanalysis(row)]
    else:
        queue = [
            row
            for row in pool
            if row.analysis_status in {AnalysisStatus.PENDING, AnalysisStatus.FAILED}
        ]
    queue = rank_for_analysis(queue)
    overflow = max(0, len(queue) - cap)
    queue = queue[:cap]
    stats.skipped = overflow

    settings = get_settings()
    if live and not settings.has_openrouter:
        stats.skipped += len(queue)
        stats.skipped_reason = "OPENROUTER_API_KEY is not set; live re-analysis skipped"
        db.commit()
        return stats

    for row in queue:
        was_stub = is_stub_analysis(row)
        try:
            analyze_candidate(
                db,
                row,
                force_stub=not live,
                require_live=live,
            )
        except MissingAPIKeyError:
            stats.skipped += 1
            stats.skipped_reason = "OPENROUTER_API_KEY is not set; live re-analysis skipped"
            break
        except Exception:
            stats.skipped += 1
            continue
        stats.analyzed += 1
        if live:
            stats.live_appended += 1
            if was_stub:
                stats.stub_refreshed += 1
    db.commit()
    return stats

