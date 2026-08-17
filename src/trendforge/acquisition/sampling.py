from __future__ import annotations

from datetime import datetime
from typing import Any

from trendforge.acquisition.provider import NormalizedItem
from trendforge.models import utcnow

RECENT_HOURS = 24.0
NA_REGIONS = {"US", "CA"}


def age_hours(published_at: datetime | None, *, now: datetime | None = None) -> float | None:
    if published_at is None:
        return None
    current = now or utcnow()
    published = published_at
    if published.tzinfo is None:
        from datetime import timezone

        published = published.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        from datetime import timezone

        current = current.replace(tzinfo=timezone.utc)
    hours = (current - published).total_seconds() / 3600.0
    if hours < 0:
        return 0.0
    return round(hours, 3)


def views_per_hour(views: int | None, hours: float | None) -> float | None:
    if views is None or hours is None or hours <= 0:
        return None
    return round(views / hours, 2)


def median(values: list[float | int | None]) -> float | None:
    nums = sorted(float(v) for v in values if v is not None)
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return nums[mid]
    return (nums[mid - 1] + nums[mid]) / 2.0


def _rate(numer: int, denom: int) -> float | None:
    if denom <= 0:
        return None
    return round(numer / denom, 4)


def summarize_population(
    items: list[NormalizedItem],
    *,
    now: datetime | None = None,
    new_count: int = 0,
    duplicate_count: int = 0,
    rejected_count: int = 0,
    raw_count: int = 0,
) -> dict[str, Any]:
    """Acquisition-quality stats. Not discovery/opportunity scores."""
    now = now or utcnow()
    ages: list[float] = []
    vph: list[float] = []
    views: list[int] = []
    english = unknown_lang = non_en = 0
    shorts = 0
    na = 0
    followers_present = 0
    shares_present = 0
    likes_present = 0
    comments_present = 0
    timestamps = 0
    recent = 0
    for item in items:
        hours = age_hours(item.published_at, now=now)
        if hours is not None:
            ages.append(hours)
            timestamps += 1
            if hours <= RECENT_HOURS:
                recent += 1
        rate = views_per_hour(item.views, hours)
        if rate is not None:
            vph.append(rate)
        if item.views is not None:
            views.append(item.views)
        if item.is_short:
            shorts += 1
        if item.language_signal == "en":
            english += 1
        elif item.language_signal == "non_en":
            non_en += 1
        else:
            unknown_lang += 1
        if (item.region_signal or "").upper() in NA_REGIONS:
            na += 1
        if item.creator_followers is not None:
            followers_present += 1
        if item.shares is not None:
            shares_present += 1
        if item.likes is not None:
            likes_present += 1
        if item.comments is not None:
            comments_present += 1

    kept = new_count + duplicate_count
    n = len(items)
    return {
        "raw_items": raw_count,
        "normalized": n,
        "shorts_eligible": shorts,
        "english": english,
        "unknown_language": unknown_lang,
        "non_english": non_en,
        "new_candidates": new_count,
        "duplicates": duplicate_count,
        "rejected": rejected_count,
        "median_views": median(views),
        "median_age_hours": median(ages),
        "median_views_per_hour": median(vph),
        "creator_followers_completeness": _rate(followers_present, n),
        "shares_completeness": _rate(shares_present, n),
        "likes_completeness": _rate(likes_present, n),
        "comments_completeness": _rate(comments_present, n),
        "timestamp_completeness": _rate(timestamps, n),
        "sampling_quality": {
            "recent_rate": _rate(recent, timestamps),
            "short_rate": _rate(shorts, n),
            "english_rate": _rate(english, n),
            "unknown_language_rate": _rate(unknown_lang, n),
            "north_america_signal_rate": _rate(na, n),
            "new_candidate_rate": _rate(new_count, raw_count or (kept + rejected_count)),
            "duplicate_rate": _rate(duplicate_count, kept),
        },
        "discovery_yield": _rate(new_count, kept),
        "recentness_yield": _rate(recent, kept) if kept else _rate(recent, timestamps),
        "high_signal_yield": None,
    }


def summarize_query_yield(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """new / raw per source_query. Acquisition-quality only."""
    if not events:
        return None
    buckets: dict[str, dict[str, Any]] = {}
    for event in events:
        query = str(event.get("query") or "").strip() or "(none)"
        row = buckets.setdefault(
            query,
            {"query": query, "raw": 0, "new": 0, "duplicates": 0, "rejected": 0, "new_rate": None},
        )
        row["raw"] += 1
        outcome = str(event.get("outcome") or "")
        if outcome == "new":
            row["new"] += 1
        elif outcome == "duplicate":
            row["duplicates"] += 1
        else:
            row["rejected"] += 1
    for row in buckets.values():
        row["new_rate"] = _rate(int(row["new"]), int(row["raw"]))
    return buckets


def summarize_creator_baselines(candidates: list) -> dict[str, Any]:
    """Creator-relative measurement stats. Not a discovery score."""
    creators: set[str] = set()
    with_baseline: set[str] = set()
    lifts: list[float] = []
    for candidate in candidates:
        key = str(candidate.channel_id or candidate.channel_name or "").strip()
        if key:
            creators.add(key)
        history = (candidate.raw_metadata or {}).get("creator_history") or {}
        baseline = history.get("creator_baseline_views")
        if key and baseline is not None:
            with_baseline.add(key)
        lift = history.get("creator_lift")
        if lift is not None:
            lifts.append(float(lift))
    return {
        "creators_processed": len(creators) if creators else 0,
        "creators_with_baseline": len(with_baseline),
        "reels_with_creator_lift": len(lifts),
        "median_creator_lift": median(lifts),
        "min_videos_for_baseline": 3,
        "definition": (
            "creator_baseline_views = median views of other same-creator items already stored "
            "(need 3+ with views). creator_lift = current_views / baseline. "
            "Null when history, baseline, or current views are insufficient. Not part of discovery_score."
        ),
    }


def attach_high_signal_yield(population: dict[str, Any], high_signal_count: int | None) -> dict[str, Any]:
    kept = int(population.get("new_candidates") or 0) + int(population.get("duplicates") or 0)
    if high_signal_count is None:
        population["high_signal_candidates"] = None
        population["high_signal_yield"] = None
        return population
    population["high_signal_candidates"] = high_signal_count
    population["high_signal_yield"] = _rate(high_signal_count, kept)
    return population


def infer_run_profile(run) -> str:
    if getattr(run, "profile", None):
        return str(run.profile)
    meta = run.run_metadata_json or {}
    if meta.get("profile"):
        return str(meta["profile"])
    if meta.get("mode") in {"emerging", "tiktok_emerging"} or meta.get("experiment") == "tiktok_emerging_breakout":
        return "tiktok_emerging"
    actor_input = meta.get("actor_input") or {}
    actor = run.actor_id or ""
    if run.source == "tiktok":
        if "xtracto" in actor or actor_input.get("content_type") == "video":
            return "tiktok_trending"
        if actor_input.get("searchQueries") or actor_input.get("videoSearchDateFilter") == "PAST_24_HOURS":
            return "tiktok_fresh_search"
        if actor_input.get("keywords") or actor_input.get("datePosted") == "last24Hours":
            return "tiktok_emerging"
        return "tiktok_hashtag"
    if run.source == "instagram":
        if actor_input.get("instagramUsernames") or "profile-reels" in actor or "creator" in actor:
            return "instagram_creator_reels"
        return "instagram_hashtag"
    return str(run.source)


def compare_profile_rows(runs: list) -> list[dict[str, Any]]:
    """Latest succeeded run per profile. Acquisition-quality comparison, not a predictor."""
    latest: dict[str, Any] = {}
    ordered = sorted(
        [r for r in runs if getattr(r, "status", None) == "succeeded"],
        key=lambda row: row.started_at or 0,
        reverse=True,
    )
    for run in ordered:
        name = infer_run_profile(run)
        if name in latest:
            continue
        meta = run.run_metadata_json or {}
        quality = meta.get("sampling_quality") or (meta.get("population") or {}).get("sampling_quality") or {}
        population = meta.get("population") or {}
        persisted = (run.items_new or 0) + (run.items_duplicate or 0)
        cost = run.actual_cost
        useful = run.items_new or 0
        cost_per_useful = None
        if cost is not None and useful > 0:
            cost_per_useful = round(float(cost) / useful, 6)
        latest[name] = {
            "profile": name,
            "label": meta.get("profile_label") or name,
            "run_id": run.id,
            "raw": run.items_found,
            "new": run.items_new,
            "duplicates": run.items_duplicate,
            "rejected": run.items_rejected,
            "recent_rate": quality.get("recent_rate"),
            "english_rate": quality.get("english_rate"),
            "unknown_language_rate": quality.get("unknown_language_rate"),
            "high_signal": population.get("high_signal_candidates"),
            "high_signal_yield": population.get("high_signal_yield"),
            "discovery_yield": population.get("discovery_yield"),
            "recentness_yield": population.get("recentness_yield"),
            "query_yield": meta.get("query_yield"),
            "creators_processed": (population.get("creator_baselines") or {}).get("creators_processed"),
            "creators_with_baseline": (population.get("creator_baselines") or {}).get("creators_with_baseline"),
            "reels_with_creator_lift": (population.get("creator_baselines") or {}).get("reels_with_creator_lift"),
            "median_creator_lift": (population.get("creator_baselines") or {}).get("median_creator_lift"),
            "cost": cost,
            "currency": run.currency,
            "cost_per_useful": cost_per_useful,
            "persisted": persisted,
        }
    preferred = [
        "tiktok_hashtag",
        "tiktok_trending",
        "tiktok_fresh_search",
        "tiktok_emerging",
        "instagram_hashtag",
        "instagram_creator_reels",
    ]
    rows = [latest[k] for k in preferred if k in latest]
    rows.extend(v for k, v in latest.items() if k not in preferred)
    return rows

