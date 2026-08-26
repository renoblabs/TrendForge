from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from trendforge.acquisition.config import (
    apply_acquisition_profile,
    build_actor_input,
    clamp_limit,
    load_acquisition_config,
    resolve_actor_id,
    source_config,
)
from trendforge.config import load_research_config

SNAPSHOT_VERSION = "cohort-config-v1"
DEFAULT_DISPLAY_TIMEZONE = "America/Toronto"
NORMALIZATION_VERSIONS = {
    "tiktok": "tiktok-normalization-v1",
    "instagram": "instagram-normalization-v1",
}

# Actor input is frozen because it controls sampling. Only known non-credential
# fields are admitted, even if a provider config later grows unrelated settings.
ACTOR_INPUT_ALLOWLIST = {
    "commentsPerPost",
    "content_type",
    "country_code",
    "datePosted",
    "deduplicateAcrossKeywords",
    "excludePinnedPosts",
    "hashtags",
    "instagramUsernames",
    "keywords",
    "limit",
    "maxFollowersPerProfile",
    "maxFollowingPerProfile",
    "maxItemsPerKeyword",
    "maxRepliesPerComment",
    "maxTotalResults",
    "postsPerProfile",
    "proxyCountryCode",
    "region",
    "resultsLimit",
    "resultsPerPage",
    "resultsType",
    "scrapeRelatedVideos",
    "searchQueries",
    "searchSection",
    "searchType",
    "shouldDownloadAvatars",
    "shouldDownloadCovers",
    "shouldDownloadMusicCovers",
    "shouldDownloadSlideshowImages",
    "shouldDownloadSubtitles",
    "shouldDownloadVideos",
    "sort",
    "sort_by",
    "topLevelCommentsPerPost",
    "videoSearchDateFilter",
    "videoSearchSorting",
}
SECRET_KEY_PARTS = ("authorization", "credential", "password", "secret", "token", "api_key", "apikey")


def ensure_utc(value: datetime) -> datetime:
    """Normalize SQLite-naive datetimes and aware datetimes to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_secret_key(key: Any) -> bool:
    text = str(key).strip().lower().replace("-", "_")
    return any(part in text for part in SECRET_KEY_PARTS)


def _secret_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _secret_safe(item)
            for key, item in value.items()
            if not _is_secret_key(key)
        }
    if isinstance(value, list):
        return [_secret_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_secret_safe(item) for item in value]
    return value


def _safe_actor_input(actor_input: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _secret_safe(deepcopy(actor_input[key]))
        for key in sorted(ACTOR_INPUT_ALLOWLIST)
        if key in actor_input and not _is_secret_key(key)
    }


def build_config_snapshot(
    source: str,
    profile: str,
    limit: int,
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve and freeze only acquisition fields that affect cohort sampling."""
    resolved = deepcopy(load_acquisition_config(cfg))
    source_name = str(source).strip().lower()
    resolved = apply_acquisition_profile(resolved, source_name, profile)
    source_cfg = source_config(resolved, source_name)
    capped_limit = clamp_limit(source_cfg, limit)
    actor_input = build_actor_input(source_cfg, capped_limit)
    audience = resolved.get("audience") if isinstance(resolved.get("audience"), dict) else {}
    queries = (
        source_cfg.get("_queries")
        or actor_input.get("searchQueries")
        or actor_input.get("keywords")
        or actor_input.get("hashtags")
        or []
    )
    creators = (
        source_cfg.get("_creators")
        or actor_input.get("instagramUsernames")
        or []
    )
    snapshot = {
        "snapshot_version": SNAPSHOT_VERSION,
        "source": source_name,
        "provider": str(source_cfg.get("provider") or "apify"),
        "profile": str(source_cfg.get("_profile") or profile),
        "role": source_cfg.get("role"),
        "actor_id": resolve_actor_id(resolved, source_cfg),
        "queries": list(queries) if isinstance(queries, list) else [],
        "creators": list(creators) if isinstance(creators, list) else [],
        "ordering": actor_input.get("videoSearchSorting")
        or actor_input.get("sort")
        or actor_input.get("sort_by"),
        "freshness_window": source_cfg.get("_window")
        or actor_input.get("videoSearchDateFilter")
        or actor_input.get("datePosted"),
        "language_rules": {
            "language": audience.get("language"),
            "regions": list(audience.get("regions") or []),
            "keep_unknown_language": bool(audience.get("keep_unknown_language", True)),
            "keep_english_only": bool(source_cfg.get("keep_english_only", False)),
        },
        "duration_rules": {
            "max_short_seconds": float(source_cfg.get("max_short_seconds") or 60),
        },
        "result_limit": capped_limit,
        "actor_input": _safe_actor_input(actor_input),
        "normalization_version": NORMALIZATION_VERSIONS.get(source_name),
    }
    return _secret_safe(snapshot)


def canonical_config_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(
        _secret_safe(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def config_fingerprint(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_config_json(snapshot).encode("utf-8")).hexdigest()


def snapshot_with_fingerprint(
    source: str,
    profile: str,
    limit: int,
    *,
    cfg: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    snapshot = build_config_snapshot(source, profile, limit, cfg=cfg)
    return snapshot, config_fingerprint(snapshot)


def composite_config_snapshot(run_snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    """Freeze an honest multi-run snapshot; it cannot equal one live profile."""
    entries = []
    for index, row in enumerate(run_snapshots):
        raw = deepcopy(row)
        if isinstance(raw.get("snapshot"), dict):
            snapshot = _secret_safe(raw.pop("snapshot"))
            run_id = raw.pop("run_id", None)
        else:
            snapshot = _secret_safe(raw)
            run_id = snapshot.pop("run_id", None)
        entries.append(
            {
                "run_id": run_id,
                "fingerprint": config_fingerprint(snapshot),
                "snapshot": snapshot,
                "order": index,
            }
        )
    entries.sort(
        key=lambda row: (
            row["run_id"] is None,
            row["run_id"] if row["run_id"] is not None else row["order"],
        )
    )
    for row in entries:
        row.pop("order", None)
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "composite": True,
        "comparison_status": "NOT_COMPARABLE",
        "source_runs": entries,
    }


def config_drift_status(
    stored_snapshot: dict[str, Any],
    stored_fingerprint: str | None = None,
    *,
    cfg: dict[str, Any] | None = None,
) -> str:
    if stored_snapshot.get("composite"):
        return "NOT_COMPARABLE"
    try:
        current = build_config_snapshot(
            str(stored_snapshot["source"]),
            str(stored_snapshot["profile"]),
            int(stored_snapshot["result_limit"]),
            cfg=cfg,
        )
    except (KeyError, TypeError, ValueError):
        return "UNKNOWN"
    expected = stored_fingerprint or config_fingerprint(stored_snapshot)
    return "MATCH" if config_fingerprint(current) == expected else "DRIFT"


def config_drift(
    stored_snapshot: dict[str, Any],
    stored_fingerprint: str | None = None,
    *,
    cfg: dict[str, Any] | None = None,
) -> bool | None:
    status = config_drift_status(
        stored_snapshot,
        stored_fingerprint,
        cfg=cfg,
    )
    if status == "MATCH":
        return False
    if status == "DRIFT":
        return True
    return None


def timing_defaults(profile: str, cfg: dict[str, Any] | None = None) -> dict[str, float]:
    research = cfg or load_research_config()
    groups = research.get("cohort_timing_defaults")
    groups = groups if isinstance(groups, dict) else {}
    default = groups.get("default") if isinstance(groups.get("default"), dict) else {}
    selected = groups.get(profile) if isinstance(groups.get(profile), dict) else {}
    values = {**default, **selected}
    return {
        "t1_window_start_hours": float(values.get("t1_window_start_hours", 3.0)),
        "t1_target_hours": float(values.get("t1_target_hours", 3.5)),
        "t1_window_end_hours": float(values.get("t1_window_end_hours", 4.0)),
        "t2_target_hours": float(values.get("t2_target_hours", 168.0)),
    }


def cohort_targets(
    t0_at: datetime,
    profile: str,
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, datetime]:
    t0 = ensure_utc(t0_at)
    timing = timing_defaults(profile, cfg=cfg)
    return {
        "target_t1_window_start": t0 + timedelta(hours=timing["t1_window_start_hours"]),
        "target_t1_at": t0 + timedelta(hours=timing["t1_target_hours"]),
        "target_t1_window_end": t0 + timedelta(hours=timing["t1_window_end_hours"]),
        "target_t2_at": t0 + timedelta(hours=timing["t2_target_hours"]),
    }


def format_toronto(
    value: datetime | None,
    fmt: str = "%Y-%m-%d %H:%M:%S %Z",
    *,
    cfg: dict[str, Any] | None = None,
) -> str:
    if value is None:
        return "—"
    research = cfg or load_research_config()
    zone_name = str(research.get("display_timezone") or DEFAULT_DISPLAY_TIMEZONE)
    return ensure_utc(value).astimezone(ZoneInfo(zone_name)).strftime(fmt)
