from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from trendforge.acquisition.provider import NormalizedItem
from trendforge.discovery.pipeline import (
    apply_signals,
    find_candidate,
    merge_sampling_raw,
    merge_source_queries,
    record_observation,
)
from trendforge.discovery.profiles import keep_language_candidate
from trendforge.discovery.provider import DiscoveredVideo
from trendforge.discovery.signals import creator_baseline_views, creator_lift
from trendforge.models import AcquisitionRun, AnalysisStatus, ContentCandidate, utcnow

MIN_CREATOR_BASELINE_VIDEOS = 3


def to_discovered_video(item: NormalizedItem, *, provenance: dict[str, Any] | None = None) -> DiscoveredVideo:
    title = item.title_or_caption
    return DiscoveredVideo(
        external_id=item.external_id,
        url=item.url,
        title=(title[:180] if title else None),
        description=title,
        channel_id=item.creator_id,
        channel_name=item.creator,
        published_at=item.published_at,
        duration=item.duration,
        views=item.views,
        likes=item.likes,
        comments=item.comments,
        shares=item.shares,
        thumbnail_url=item.thumbnail_url,
        hashtags=list(item.hashtags),
        channel_subscriber_count=item.creator_followers,
        is_short=item.is_short,
        source_query=item.source_query,
        search_rank=item.search_rank,
        platform=item.platform,
        raw=dict(provenance or {}),
    )


def _refresh_present(candidate: ContentCandidate, video: DiscoveredVideo) -> None:
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


def _merge_profile_list(existing: list | None, profile: str | None) -> list[str]:
    names = [str(x) for x in (existing or []) if x]
    if profile and profile not in names:
        names.append(profile)
    return names


def _creator_history(
    db: Session,
    candidate: ContentCandidate,
    *,
    min_videos: int = MIN_CREATOR_BASELINE_VIDEOS,
) -> dict[str, Any] | None:
    """Measurement-only creator baseline. Not written into discovery_score."""
    if not candidate.channel_id and not candidate.channel_name:
        return None
    q = db.query(ContentCandidate).filter(ContentCandidate.platform == candidate.platform)
    if candidate.channel_id:
        q = q.filter(ContentCandidate.channel_id == candidate.channel_id)
    else:
        q = q.filter(ContentCandidate.channel_name == candidate.channel_name)
    others = [row for row in q.all() if row.id != candidate.id]
    present = [row.views for row in others if row.views is not None]
    now = utcnow()
    recent_views = []
    for row in others:
        if row.views is None or row.published_at is None:
            continue
        published = row.published_at
        if published.tzinfo is None:
            from datetime import timezone

            published = published.replace(tzinfo=timezone.utc)
        age = (now - published).total_seconds() / 3600.0
        if age <= 24 * 30:
            recent_views.append(row.views)
    median_all = creator_baseline_views(present, min_videos=1) if present else None
    lift_baseline = creator_baseline_views(present, min_videos=min_videos)
    recent_median = creator_baseline_views(recent_views, min_videos=1) if recent_views else None
    return {
        "creator_id": candidate.channel_id,
        "creator_name": candidate.channel_name or candidate.creator,
        "historical_content_count": len(others),
        "baseline_sample_size": len(present),
        "historical_views": sum(present) if present else None,
        "historical_median_views": None if median_all is None else round(median_all, 2),
        "historical_recent_median_views": None if recent_median is None else round(recent_median, 2),
        "creator_baseline_views": None if lift_baseline is None else round(lift_baseline, 2),
        "creator_lift": creator_lift(candidate.views, lift_baseline),
        "min_videos_for_baseline": min_videos,
        "definition": (
            "creator_baseline_views is the median of other same-creator items already stored "
            f"(need {min_videos}+ with views). creator_lift = current_views / baseline. "
            "Null when history, baseline, or current views are insufficient. Not part of discovery_score."
        ),
    }


def apply_creator_history(db: Session, candidate: ContentCandidate) -> dict[str, Any] | None:
    history = _creator_history(db, candidate)
    if not history:
        return None
    meta = dict(candidate.raw_metadata or {})
    meta["creator_history"] = history
    candidate.raw_metadata = meta
    return history


def refresh_creator_baselines(db: Session, candidate_ids: list[int]) -> None:
    """Recompute measurement baselines after a batch so early rows see later siblings."""
    seen: set[int] = set()
    for cid in candidate_ids:
        if cid in seen:
            continue
        seen.add(cid)
        row = db.get(ContentCandidate, cid)
        if row is None:
            continue
        apply_creator_history(db, row)
    db.flush()


def persist_normalized_item(
    db: Session,
    item: NormalizedItem,
    run: AcquisitionRun,
    *,
    cfg: dict[str, Any] | None = None,
    write_observation: bool = True,
) -> tuple[ContentCandidate | None, str]:
    """Return (candidate, outcome) where outcome is new|duplicate|rejected_*."""
    if not item.external_id or not item.url:
        return None, "rejected_unnormalizable"
    if not keep_language_candidate(item.language_signal or "unknown"):
        return None, "rejected_language"
    if item.is_short is False:
        return None, "rejected_not_short"

    profile_name = getattr(run, "profile", None) or (run.run_metadata_json or {}).get("profile")
    provenance = {
        "provider": run.provider,
        "actor_id": run.actor_id,
        "acquisition_run_id": run.id,
        "source": item.platform,
        "source_query": item.source_query,
        "source_actor": run.actor_id,
        "audio": item.audio,
        "language_signal": item.language_signal,
        "language_evidence": item.language_evidence,
        "region_signal": item.region_signal,
        "audience_relevance": item.audience_relevance,
        "profile_url": item.profile_url,
        "saves": item.saves,
        "region_meaning": "search sampling bias, not creator location",
        "acquisition_profile": profile_name,
        "profiles_that_found_candidate": [profile_name] if profile_name else [],
        "queries_that_found_candidate": [item.source_query] if item.source_query else [],
    }
    video = to_discovered_video(item, provenance=provenance)
    existing = find_candidate(
        db,
        external_id=video.external_id,
        url=video.url,
        platform=video.platform,
    )
    created = existing is None
    if existing is None:
        existing = ContentCandidate(
            platform=video.platform,
            url=video.url,
            external_id=video.external_id,
            analysis_status=AnalysisStatus.SKIPPED,
            data_origin="live",
            acquisition_run_id=run.id,
            acquisition_provider=run.provider,
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
    existing.acquisition_run_id = run.id
    existing.acquisition_provider = run.provider
    existing.acquisition_profiles = _merge_profile_list(existing.acquisition_profiles, profile_name)
    _refresh_present(existing, video)
    existing.raw_metadata = merge_sampling_raw(existing.raw_metadata, provenance)
    apply_creator_history(db, existing)
    db.flush()
    if write_observation:
        record_observation(db, existing, video, source=item.platform)
        apply_signals(db, existing, cfg)
    return existing, ("new" if created else "duplicate")
