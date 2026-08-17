from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from trendforge.acquisition.apify import ApifyClient
from trendforge.acquisition.config import (
    apply_acquisition_mode,
    apply_acquisition_profile,
    build_actor_input,
    clamp_limit,
    load_acquisition_config,
    resolve_actor_id,
    source_config,
)
from trendforge.acquisition.errors import AcquisitionError, MissingTokenError
from trendforge.acquisition.instagram import ApifyInstagramProvider
from trendforge.acquisition.persist import persist_normalized_item, refresh_creator_baselines
from trendforge.acquisition.provider import ActorRunResult
from trendforge.acquisition.tiktok import ApifyTikTokProvider
from trendforge.acquisition.sampling import (
    RECENT_HOURS,
    age_hours,
    attach_high_signal_yield,
    summarize_creator_baselines,
    summarize_population,
    summarize_query_yield,
    views_per_hour,
)
from trendforge.analysis.selection import is_high_signal
from trendforge.config import DATA_DIR, get_settings, load_discovery_config
from trendforge.models import AcquisitionRun, ContentCandidate, utcnow

PROVIDERS = {
    "tiktok": ApifyTikTokProvider,
    "instagram": ApifyInstagramProvider,
}


def quality_sample(item, *, now=None) -> dict[str, Any]:
    published = item.published_at.isoformat() if item.published_at else None
    hours = age_hours(item.published_at, now=now)
    return {
        "platform": item.platform,
        "external_id": item.external_id,
        "url": item.url,
        "title": (item.title_or_caption or "")[:180],
        "views": item.views,
        "likes": item.likes,
        "comments": item.comments,
        "shares": item.shares,
        "creator": item.creator,
        "creator_followers": item.creator_followers,
        "published_at": published,
        "age_hours": hours,
        "views_per_hour": views_per_hour(item.views, hours),
        "language_signal": item.language_signal,
        "region_signal": item.region_signal,
        "audience_relevance": item.audience_relevance,
        "duration": item.duration,
        "audio": item.audio,
        "source_query": item.source_query,
        "is_short": item.is_short,
    }


def _raw_dir(run_id: int) -> Path:
    path = DATA_DIR / "acquisition" / str(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_raw(run_id: int, items: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    folder = _raw_dir(run_id)
    dataset_path = folder / "dataset.json"
    meta_path = folder / "run.json"
    dataset_path.write_text(json.dumps(items, ensure_ascii=False, default=str), encoding="utf-8")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, default=str), encoding="utf-8")
    return str(dataset_path)


def get_provider(source: str, client: ApifyClient | None = None):
    cls = PROVIDERS.get(source)
    if cls is None:
        raise AcquisitionError(
            f"Unsupported acquisition source {source!r}. Use tiktok or instagram. "
            "YouTube remains on the official API."
        )
    return cls(client=client)


def _previous_source_run(
    db: Session,
    *,
    source: str,
    actor_id: str | None,
    exclude_id: int,
) -> dict[str, Any] | None:
    q = (
        db.query(AcquisitionRun)
        .filter(
            AcquisitionRun.source == source,
            AcquisitionRun.status == "succeeded",
            AcquisitionRun.id != exclude_id,
        )
        .order_by(AcquisitionRun.started_at.desc())
    )
    if actor_id:
        q = q.filter(AcquisitionRun.actor_id != actor_id)
    prev = q.first()
    if prev is None:
        return None
    meta = prev.run_metadata_json or {}
    return {
        "id": prev.id,
        "actor_id": prev.actor_id,
        "items_found": prev.items_found,
        "items_new": prev.items_new,
        "items_duplicate": prev.items_duplicate,
        "items_rejected": prev.items_rejected,
        "actual_cost": prev.actual_cost,
        "sampling_quality": (meta.get("population") or {}).get("sampling_quality")
        or meta.get("sampling_quality"),
        "median_views": (meta.get("population") or {}).get("median_views"),
        "median_age_hours": (meta.get("population") or {}).get("median_age_hours"),
    }


def run_acquisition(
    db: Session,
    source: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    mode: str | None = None,
    profile: str | None = None,
    client: ApifyClient | None = None,
    cfg: dict[str, Any] | None = None,
) -> AcquisitionRun:
    cfg = deepcopy(load_acquisition_config(cfg))
    source = source.strip().lower()
    if profile:
        cfg = apply_acquisition_profile(cfg, source, profile)
    else:
        cfg = apply_acquisition_mode(cfg, source, mode)
    source_cfg = source_config(cfg, source)
    capped = clamp_limit(source_cfg, limit)
    source_cfg["_limit"] = capped
    sources = cfg.setdefault("sources", {})
    if isinstance(sources, dict):
        sources[source] = source_cfg

    profile_name = source_cfg.get("_profile")
    if not profile_name:
        profile_name = f"{source}_hashtag" if source in {"tiktok", "instagram"} else source
    actor_id = resolve_actor_id(cfg, source_cfg)
    actor_input = build_actor_input(source_cfg, capped)
    provider_name = str(source_cfg.get("provider") or "apify")
    experiment = source_cfg.get("_experiment")
    search_region = (
        str(
            actor_input.get("country_code")
            or actor_input.get("region")
            or actor_input.get("location")
            or ""
        )
        .strip()
        .upper()
        or None
    )

    run = AcquisitionRun(
        source=source,
        provider=provider_name,
        actor_id=actor_id,
        profile=profile_name,
        started_at=utcnow(),
        status="running",
        errors=[],
        run_metadata_json={
            "limit": capped,
            "profile": profile_name,
            "profile_label": source_cfg.get("_profile_label") or profile_name,
            "profile_status": source_cfg.get("_profile_status"),
            "mode": source_cfg.get("_mode") or (mode or "default"),
            "experiment": experiment,
            "queries": source_cfg.get("_queries") or actor_input.get("keywords") or actor_input.get("searchQueries") or actor_input.get("hashtags"),
            "creators": source_cfg.get("_creators") or actor_input.get("instagramUsernames"),
            "window": source_cfg.get("_window")
            or actor_input.get("datePosted")
            or actor_input.get("videoSearchDateFilter")
            or actor_input.get("dateRange"),
            "regions": source_cfg.get("_regions") or ([search_region] if search_region else None),
            "audience_regions": source_cfg.get("_audience_regions"),
            "actor_input": actor_input,
            "dry_run": dry_run,
            "max_charge_usd": source_cfg.get("max_charge_usd"),
        },
    )
    db.add(run)
    db.flush()

    if dry_run:
        run.status = "dry_run"
        run.completed_at = utcnow()
        run.run_metadata_json = {
            **(run.run_metadata_json or {}),
            "note": "No Apify call. Inspect actor_input before spending credits.",
        }
        db.commit()
        return run

    settings = get_settings()
    if provider_name == "apify" and client is None and not settings.has_apify:
        run.status = "config_error"
        run.errors = [
            {
                "type": "config",
                "message": "APIFY_API_TOKEN is not set. YouTube discovery is unchanged.",
            }
        ]
        run.completed_at = utcnow()
        db.commit()
        raise MissingTokenError(
            "APIFY_API_TOKEN is not set. Add it to .env to run Apify acquisition. "
            "YouTube discovery continues without it."
        )

    provider = get_provider(source, client=client)
    try:
        result: ActorRunResult = provider.discover(cfg)
    except MissingTokenError:
        run.status = "config_error"
        run.errors = [{"type": "config", "message": "APIFY_API_TOKEN is not set."}]
        run.completed_at = utcnow()
        db.commit()
        raise
    except AcquisitionError as exc:
        run.status = "failed"
        run.errors = [{"type": "apify", "message": str(exc)}]
        run.completed_at = utcnow()
        db.commit()
        raise

    run.apify_run_id = result.run_id
    run.apify_dataset_id = result.dataset_id
    run.actual_cost = result.actual_cost
    run.estimated_cost = result.estimated_cost
    run.currency = result.currency
    raw_path = _write_raw(
        run.id,
        result.items,
        {
            "apify_run_id": result.run_id,
            "apify_dataset_id": result.dataset_id,
            "actor_id": result.actor_id,
            "status": result.status,
            "actual_cost": result.actual_cost,
            "currency": result.currency,
        },
    )
    meta = dict(run.run_metadata_json or {})
    meta["raw_dataset_path"] = raw_path
    meta["apify_status"] = result.status
    run.run_metadata_json = meta

    if result.status != "SUCCEEDED":
        run.status = "failed"
        run.items_found = len(result.items)
        run.errors = [
            {
                "type": "actor",
                "message": f"Apify actor ended with status {result.status}",
            }
        ]
        run.completed_at = utcnow()
        db.commit()
        raise AcquisitionError(f"Apify actor {result.actor_id} ended with status {result.status}")

    found = len(result.items)
    new_n = 0
    dupes = 0
    rejected = 0
    reject_reasons: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    rejected_sample: list[dict[str, Any]] = []
    normalized_items = []
    kept_ids: list[int] = []
    query_events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    rank = 0
    now = utcnow()
    max_short = float(source_cfg.get("max_short_seconds") or 60)
    for raw in result.items:
        item = provider.normalize(
            raw,
            search_rank=rank,
            max_short_seconds=max_short,
            profile=profile_name,
        )
        if item is None:
            rejected += 1
            reject_reasons["rejected_unnormalizable"] = reject_reasons.get("rejected_unnormalizable", 0) + 1
            query_events.append(
                {
                    "query": raw.get("searchQuery") or raw.get("keyword") or raw.get("input"),
                    "outcome": "rejected_unnormalizable",
                }
            )
            continue
        if not item.region_signal and search_region:
            item.region_signal = search_region
            item.audience_relevance = (
                "region_sampled" if search_region in {"US", "CA"} else "unknown"
            )
        normalized_items.append(item)
        key = (item.platform, item.external_id)
        write_obs = key not in seen
        seen.add(key)
        candidate, outcome = persist_normalized_item(
            db,
            item,
            run,
            cfg=None,
            write_observation=write_obs,
        )
        if outcome.startswith("rejected") or candidate is None:
            rejected += 1
            reason = outcome if outcome.startswith("rejected") else "rejected"
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
            if len(rejected_sample) < 12:
                rejected_sample.append({**quality_sample(item, now=now), "reject_reason": reason})
            query_events.append({"query": item.source_query, "outcome": reason})
            continue
        rank += 1
        if outcome == "new":
            new_n += 1
        else:
            dupes += 1
        query_events.append({"query": item.source_query, "outcome": outcome})
        if candidate is not None and candidate.id is not None:
            kept_ids.append(candidate.id)
        samples.append(quality_sample(item, now=now))

    samples.sort(
        key=lambda row: (
            row.get("age_hours") is None,
            row.get("age_hours") if row.get("age_hours") is not None else 0,
            row.get("views") is None,
            -(row.get("views") or 0),
        )
    )
    refresh_creator_baselines(db, kept_ids)
    population = summarize_population(
        normalized_items,
        now=now,
        new_count=new_n,
        duplicate_count=dupes,
        rejected_count=rejected,
        raw_count=found,
    )
    high_signal_count = 0
    try:
        disc_cfg = load_discovery_config()
    except Exception:
        disc_cfg = {}
    for cid in kept_ids:
        row = db.get(ContentCandidate, cid)
        if row is not None and is_high_signal(row, disc_cfg):
            high_signal_count += 1
    attach_high_signal_yield(population, high_signal_count)
    kept = new_n + dupes
    recent_kept = sum(
        1
        for row in samples
        if row.get("age_hours") is not None and row.get("age_hours") <= RECENT_HOURS
    )
    if kept:
        population["recentness_yield"] = round(recent_kept / kept, 4)
    else:
        population["recentness_yield"] = None
    kept_rows = [db.get(ContentCandidate, cid) for cid in kept_ids]
    kept_rows = [row for row in kept_rows if row is not None]
    creator_baselines = summarize_creator_baselines(kept_rows)
    population["creator_baselines"] = creator_baselines
    query_yield = summarize_query_yield(query_events)
    run.items_found = found
    run.items_new = new_n
    run.items_duplicate = dupes
    run.items_rejected = rejected
    run.status = "succeeded"
    run.completed_at = utcnow()
    meta = dict(run.run_metadata_json or {})
    meta["items_normalized"] = new_n + dupes
    meta["items_persisted"] = new_n + dupes
    meta["quality_sample"] = samples[:15]
    meta["rejected_sample"] = rejected_sample
    meta["reject_reasons"] = reject_reasons
    meta["population"] = population
    meta["sampling_quality"] = population.get("sampling_quality")
    meta["query_yield"] = query_yield
    meta["creator_baselines"] = creator_baselines
    meta["comparison"] = _previous_source_run(
        db, source=source, actor_id=run.actor_id, exclude_id=run.id
    )
    run.run_metadata_json = meta
    db.commit()
    return run
